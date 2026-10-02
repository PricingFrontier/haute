"""Caching off in the pipeline settings: previews and runs neither read nor write node data.

``specs/caching/high-level.md`` ("Caching can be turned off") and the low-level
"Caching off" rule. Each execution runs up to its target from the inputs; an
explicit build still publishes its own node; a trace still reads what its
preview listed; turning caching back on reuses what the store still holds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._execution_context import ExecutionProfile
from haute._node_snapshots import NodeSnapshotStore
from haute._pipeline_settings import update_pipeline_settings
from haute._sandbox import set_project_root
from haute._seed_plans import ListedSeed, open_listed_seed_plan, resolve_seed_plan
from haute._types import NodeType
from tests.test_data_output_seeding import _graph as _output_graph
from tests.test_data_output_seeding import _inline_worker, _result, _write
from tests.test_seed_plans import _chain, _code, _diamond, _graph, _identity, _parquet, _publish
from tests.test_seed_plans import _request as _plan_request

_ROWS = 120


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    set_project_root(haute_scratch)
    for profile in ("LAZY_SINK", "TRAINING", "NODE_SNAPSHOT"):
        monkeypatch.setenv(f"HAUTE_{profile}_MEMORY_LIMIT_MB", "1024")
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame(
        {
            "id": list(range(_ROWS)),
            "a": [float(value % 7) for value in range(_ROWS)],
            "b": [float(value % 5) for value in range(_ROWS)],
            "c": [float(value % 11) for value in range(_ROWS)],
            "y": [float(value % 3) for value in range(_ROWS)],
        }
    ).write_parquet(haute_scratch / "quotes.parquet")
    pl.DataFrame(
        {"id": list(range(_ROWS)), "d": [value / 10 for value in range(_ROWS)]}
    ).write_parquet(haute_scratch / "claims.parquet")
    return haute_scratch


@pytest.fixture()
def store(project: Path) -> NodeSnapshotStore:
    return NodeSnapshotStore(project)


def _caching(project: Path, on: bool) -> None:
    update_pipeline_settings(project, {"caching": on})


def test_a_bounded_plan_seeds_and_captures_nothing_while_caching_is_off(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _diamond(project)
    for node_id in ("A", "B", "C", "D"):
        _publish(store, graph, node_id)
    assert resolve_seed_plan(_plan_request(graph), store=store).seeds

    _caching(project, False)
    decision = resolve_seed_plan(_plan_request(graph), store=store)

    assert decision.seeds == {}
    assert decision.captures == {}
    assert decision.skipped_captures == {}
    assert decision.executed_node_ids == {"src", "A", "B", "C", "D", "T"}


def test_an_explicit_build_seeds_and_captures_nothing_upstream(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("J", NodeType.POLARS, _code("df = src.join(other, on='id', how='left')")),
            ("X", NodeType.POLARS, _code("df = J.filter(pl.col('a') > 0)")),
        ],
        [("src", "J"), ("other", "J"), ("J", "X")],
    )

    def build_x() -> Any:
        return resolve_seed_plan(
            _plan_request(
                graph, target="X", profile=ExecutionProfile.NODE_SNAPSHOT, build_node_id="X"
            ),
            store=store,
        )

    assert "J" in build_x().captures

    _caching(project, False)
    decision = build_x()
    assert decision.captures == {}
    assert decision.seeds == {}
    assert decision.executed_node_ids == {"src", "other", "J", "X"}

    # A fresh upstream generation is not read either.
    _publish(store, graph, "J")
    assert build_x().seeds == {}


def test_turning_caching_back_on_seeds_what_the_store_still_holds(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _chain(project)
    generation = _publish(store, graph, "A")

    _caching(project, False)
    assert resolve_seed_plan(_plan_request(graph, target="B"), store=store).seeds == {}

    _caching(project, True)
    seeds = resolve_seed_plan(_plan_request(graph, target="B"), store=store).seeds
    assert {node: seed.generation_id for node, seed in seeds.items()} == {"A": generation}


def test_a_trace_still_reads_the_generations_its_preview_listed(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _chain(project)
    generation = _publish(store, graph, "A")
    _caching(project, False)

    request = _plan_request(graph, target="C", profile=ExecutionProfile.PREVIEW_EAGER)
    listed = [ListedSeed("A", _identity(store, graph, "A").digest, generation)]
    with open_listed_seed_plan(request, listed, store=store) as plan:
        seeds = plan.decision.seeds

    assert {node: seed.generation_id for node, seed in seeds.items()} == {"A": generation}


def test_a_data_output_run_writes_the_same_rows_and_nothing_to_the_store(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _output_graph(project)
    _inline_worker(monkeypatch)
    _caching(project, False)

    off = _write(monkeypatch, graph)

    assert off.status_code == 200, off.body
    assert off.captures == {}
    assert off.seeds == set()
    assert NodeSnapshotStore(project).usage().total_bytes == 0
    written = _result(project)

    _caching(project, True)
    on = _write(monkeypatch, graph)
    assert on.captures == {"J": "published", "B": "published"}
    assert_frame_equal(_result(project), written, check_row_order=False)

    # Off again, the run computes from the inputs instead of reading B.
    _caching(project, False)
    again = _write(monkeypatch, graph)
    assert again.status_code == 200, again.body
    assert again.seeds == set()
    assert again.captures == {}
    assert again.calls["J"] > 0
    assert_frame_equal(_result(project), written, check_row_order=False)


def _join_graph(project: Path) -> Any:
    """``quotes + claims → join → banding``."""
    return _graph(
        project,
        [
            ("quotes", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("claims", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("join", NodeType.POLARS, _code("df = quotes.join(claims, on='id', how='left')")),
            (
                "banding",
                NodeType.POLARS,
                _code("df = join.with_columns((pl.col('a') * 2).alias('band'))"),
            ),
        ],
        [("quotes", "join"), ("claims", "join"), ("join", "banding")],
    )


def test_a_preview_runs_without_a_plan_and_writes_nothing(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    import haute.executor as executor
    from haute.executor import _preview_cache
    from haute.server import app

    # No plan at all, not merely an empty one: caching off skips the planning.
    opened: list[str] = []
    real_open = executor.open_seed_plan

    def recording_open(request: Any, **kwargs: Any) -> Any:
        opened.append(request.target_node_id)
        return real_open(request, **kwargs)

    monkeypatch.setattr(executor, "open_seed_plan", recording_open)
    client = TestClient(app)
    graph = _join_graph(project).model_dump(mode="json")

    def preview() -> dict[str, Any]:
        _preview_cache.clear()
        response = client.post(
            "/api/pipeline/preview",
            json={"graph": graph, "node_id": "banding", "row_limit": 200},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ok", body.get("error")
        return body

    _caching(project, False)
    off = preview()
    assert off["seed_plan"] == []
    assert opened == []
    assert NodeSnapshotStore(project).usage().total_bytes == 0

    _caching(project, True)
    on = preview()
    assert opened == ["banding"]
    assert [(entry["node_id"], entry["kind"]) for entry in on["seed_plan"]] == [
        ("join", "captured")
    ]
    # Row order is not part of the snapshot contract; the rows are.
    assert sorted(on["preview"], key=lambda row: row["id"]) == sorted(
        off["preview"], key=lambda row: row["id"]
    )
