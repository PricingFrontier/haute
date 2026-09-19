"""Traces read exactly the snapshot generations their preview read (CACHE-S09)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._node_snapshots import NodeSnapshotStore
from haute._seed_plans import ListedSeed
from haute._types import GraphNode, NodeType, PipelineGraph
from tests.test_preview_snapshot_seeding import (
    _ROWS,
    _code,
    _graph,
    _identity,
    _join_graph,
    _node,
    _parquet,
    _post_preview,
    _publish,
)


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame({"id": list(range(_ROWS)), "a": list(range(_ROWS))}).write_parquet(
        haute_scratch / "policies.parquet"
    )
    pl.DataFrame(
        {"id": list(range(_ROWS)), "d": [value / 10 for value in range(_ROWS)]}
    ).write_parquet(haute_scratch / "claims.parquet")
    return haute_scratch


@pytest.fixture()
def store(project: Path) -> NodeSnapshotStore:
    return NodeSnapshotStore(project)


@pytest.fixture()
def api(project: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from haute.executor import _preview_cache
    from haute.server import app

    _preview_cache.clear()
    yield TestClient(app)
    _preview_cache.clear()


@pytest.fixture()
def trace_builds(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Every node the trace builds, by id."""
    import haute.trace as trace_module

    built: Counter[str] = Counter()
    real = trace_module._build_node_fn

    def counting(node: GraphNode, **kwargs: Any) -> Any:
        built[node.id] += 1
        return real(node, **kwargs)

    monkeypatch.setattr(trace_module, "_build_node_fn", counting)
    return built


def _listed(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A preview's ``seed_plan`` as a trace request carries it."""
    return [
        {
            "node_id": entry["node_id"],
            "port_label": entry["port_label"],
            "identity_digest": entry["identity_digest"],
            "generation_id": entry["generation_id"],
        }
        for entry in entries
    ]


def _trace(
    api: Any,
    graph: PipelineGraph,
    target: str,
    seed_plan: list[dict[str, Any]],
    *,
    row: dict[str, Any] | None = None,
    expect: int = 200,
    **fields: Any,
) -> dict[str, Any]:
    response = api.post(
        "/api/pipeline/trace",
        json={
            "graph": graph.model_dump(mode="json"),
            "target_node_id": target,
            "row_index": 0,
            "row_limit": 200,
            "row_values": row,
            "seed_plan": seed_plan,
            **fields,
        },
    )
    assert response.status_code == expect, response.text
    return response.json()


def _steps(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["node_id"]: step for step in trace["trace"]["steps"]}


def _omissions(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {omission["node_id"]: omission for omission in trace["trace"]["omissions"]}


def test_trace_of_seeded_preview_stops_at_join_and_omits_sources(
    project: Path, api: Any, trace_builds: Counter[str]
) -> None:
    from haute.executor import _preview_cache

    graph = _join_graph(project)
    _post_preview(api, graph, "banding")
    _preview_cache.clear()
    seeded = _post_preview(api, graph, "banding")
    assert [(entry["node_id"], entry["kind"]) for entry in seeded["seed_plan"]] == [
        ("join", "seeded")
    ]
    row = seeded["preview"][0]

    trace = _trace(api, graph, "banding", _listed(seeded["seed_plan"]), row=row)

    steps = _steps(trace)
    join = seeded["seed_plan"][0]
    assert steps["join"]["snapshot_generation_id"] == join["generation_id"]
    assert steps["banding"]["output_values"]["band"] == row["band"]
    omissions = _omissions(trace)
    assert set(omissions) == {"policies", "claims"}
    diagnostics = trace["trace"]["correlation_diagnostics"]
    for node_id in ("policies", "claims"):
        omission = omissions[node_id]
        assert omission["reason"] == "snapshot_seed"
        diagnostic = diagnostics[omission["diagnostic_index"]]
        assert diagnostic["node_id"] == node_id
        assert diagnostic["seed_node_ids"] == ["join"]
    assert not {"policies", "claims", "join"} & set(trace_builds)


def test_first_preview_then_trace_reads_the_capture_and_scans_no_source(
    project: Path, api: Any, trace_builds: Counter[str]
) -> None:
    # A shuffle recomputed would give every row other values: only reading
    # the capture reproduces the preview's row.
    graph = _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("claims", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            (
                "shuffled",
                NodeType.POLARS,
                _code("df = policies.with_columns(pl.int_range(pl.len()).shuffle().alias('r'))"),
            ),
            ("join", NodeType.POLARS, _code("df = shuffled.join(claims, on='id', how='left')")),
            (
                "banding",
                NodeType.POLARS,
                _code("df = join.with_columns((pl.col('a') * 2).alias('band'))"),
            ),
        ],
        [("policies", "shuffled"), ("shuffled", "join"), ("claims", "join"), ("join", "banding")],
    )
    first = _post_preview(api, graph, "banding")
    assert [(entry["node_id"], entry["kind"]) for entry in first["seed_plan"]] == [
        ("join", "captured")
    ]
    row = first["preview"][0]

    trace = _trace(api, graph, "banding", _listed(first["seed_plan"]), row=row)

    steps = _steps(trace)
    assert steps["banding"]["output_values"] == row
    assert steps["join"]["snapshot_generation_id"] == first["seed_plan"][0]["generation_id"]
    assert not {"policies", "claims", "shuffled", "join"} & set(trace_builds)


def _diamond(project: Path) -> PipelineGraph:
    """``A → B``, ``A → C``, ``B + C → D``."""
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') + 1).alias('a1'))")),
            ("B", NodeType.POLARS, _code("df = A.select('id', 'a1')")),
            ("C", NodeType.POLARS, _code("df = A.select('id', 'a')")),
            ("D", NodeType.POLARS, _code("df = B.join(C, on='id', how='left')")),
        ],
        [("src", "A"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")],
    )


def test_diamond_single_cached_branch_keeps_shared_ancestor_traceable(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    graph = _diamond(project)
    frame = pl.DataFrame({"id": list(range(_ROWS)), "a1": [value + 1 for value in range(_ROWS)]})
    b1 = _publish(store, graph, "B", frame)

    result = trace_result_to_dict(
        execute_trace(
            graph,
            target_node_id="D",
            row_limit=200,
            seed_plan=[ListedSeed("B", _identity(store, graph, "B").digest, b1)],
        )
    )

    steps = {step["node_id"]: step for step in result["steps"]}
    omitted = {omission["node_id"] for omission in result["omissions"]}
    # ``A`` still ran for ``C``: it is a step, not an omission.
    assert "A" in steps and "A" not in omitted
    assert steps["B"]["snapshot_generation_id"] == b1
    assert steps["C"]["snapshot_generation_id"] is None
    assert not omitted


def _refresh(
    store: NodeSnapshotStore, graph: PipelineGraph, node_id: str, frame: pl.DataFrame
) -> str:
    from haute._execution_context import ExecutionProfile
    from haute._node_snapshots import NodeSnapshotColumns

    identity = _identity(store, graph, node_id)
    artifact = store.stage_node_output(identity)
    frame.write_parquet(artifact.data_path)
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=True,
        profile=ExecutionProfile.NODE_SNAPSHOT,
        refresh=True,
    ) as publication:
        assert publication.generation is not None
        return publication.generation.generation_id


def _chain(project: Path) -> PipelineGraph:
    return _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("F", NodeType.POLARS, _code("df = policies.filter(pl.col('a') >= 0)")),
            ("G", NodeType.POLARS, _code("df = F.with_columns((pl.col('a') * 3).alias('g'))")),
        ],
        [("policies", "F"), ("F", "G")],
    )


def test_snapshot_published_between_unseeded_preview_and_trace_stays_unseeded(
    project: Path, api: Any, store: NodeSnapshotStore
) -> None:
    graph = _chain(project)
    preview = _post_preview(api, graph, "G")
    assert preview["seed_plan"] == []
    _publish(store, graph, "F", pl.DataFrame({"id": [7], "a": [7]}))

    trace = _trace(api, graph, "G", [], row=preview["preview"][0])

    steps = _steps(trace)
    assert all(step["snapshot_generation_id"] is None for step in steps.values())
    assert set(steps) == {"policies", "F", "G"}
    assert trace["trace"]["omissions"] == []


def test_refresh_between_preview_and_trace_traces_the_preview_generation(
    project: Path, api: Any, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project)
    _post_preview(api, graph, "banding")
    preview = _post_preview(api, graph, "banding")
    join = preview["seed_plan"][0]
    identity = _identity(store, graph, "join")

    # Another job still leases the preview's generation while it is refreshed.
    with store.lease_generation(identity, join["generation_id"]):
        refreshed = _refresh(store, graph, "join", pl.DataFrame({"id": [1], "a": [1], "d": [0.1]}))
        assert refreshed != join["generation_id"]
        trace = _trace(
            api, graph, "banding", _listed(preview["seed_plan"]), row=preview["preview"][0]
        )

    assert _steps(trace)["join"]["snapshot_generation_id"] == join["generation_id"]


@pytest.mark.parametrize("another_lease", [True, False])
def test_clear_with_and_without_another_lease(
    project: Path, api: Any, store: NodeSnapshotStore, another_lease: bool
) -> None:
    graph = _join_graph(project)
    _post_preview(api, graph, "banding")
    preview = _post_preview(api, graph, "banding")
    join = preview["seed_plan"][0]
    identity = _identity(store, graph, "join")
    row = preview["preview"][0]

    if another_lease:
        with store.lease_generation(identity, join["generation_id"]):
            store.clear(identity)
            trace = _trace(api, graph, "banding", _listed(preview["seed_plan"]), row=row)
        assert _steps(trace)["join"]["snapshot_generation_id"] == join["generation_id"]
    else:
        store.clear(identity)
        expired = _trace(api, graph, "banding", _listed(preview["seed_plan"]), row=row, expect=409)
        assert expired["detail"]["error_code"] == "preview_seed_plan_expired"
        assert expired["detail"]["node_id"] == "join"


def test_graph_edit_between_preview_and_trace_is_409(project: Path, api: Any) -> None:
    graph = _join_graph(project)
    _post_preview(api, graph, "banding")
    preview = _post_preview(api, graph, "banding")
    edited = graph.model_copy(
        update={
            "nodes": [
                _node(
                    "join",
                    NodeType.POLARS,
                    _code("df = policies.join(claims, on='id', how='inner')"),
                )
                if node.id == "join"
                else node
                for node in graph.nodes
            ]
        }
    )

    expired = _trace(api, edited, "banding", _listed(preview["seed_plan"]), expect=409)

    assert expired["detail"]["error_code"] == "preview_seed_plan_expired"


def test_trace_of_column_projected_capture_executes_that_point(
    project: Path, api: Any, trace_builds: Counter[str]
) -> None:
    graph = _join_graph(project)
    projected = _post_preview(api, graph, "banding", requested_preview_columns=["band"])
    join = projected["seed_plan"][0]
    assert join["kind"] == "captured" and join["columns"] is not None

    trace = _trace(api, graph, "banding", _listed(projected["seed_plan"]))

    # The capture lacks columns the trace reads, so the trace computes ``join``.
    assert _steps(trace)["join"]["snapshot_generation_id"] is None
    assert trace_builds["join"] >= 1
    assert trace["trace"]["omissions"] == []


def test_trace_reuses_the_preview_entry_stored_under_its_plan(
    project: Path, api: Any, store: NodeSnapshotStore
) -> None:
    from haute.executor import _preview_cache, execute_graph
    from haute.trace import execute_trace

    graph = _join_graph(project)
    generation = _publish(
        store, graph, "join", pl.DataFrame({"id": [1, 2], "a": [3, 4], "d": [0.1, 0.2]})
    )
    # A full-materialisation preview seeded from the join, keyed by that seed.
    execute_graph(graph, target_node_id="banding", row_limit=200, shared_snapshots=True)
    assert len(_preview_cache) == 1

    result = execute_trace(
        graph,
        target_node_id="banding",
        row_limit=200,
        preview=_preview_cache,
        seed_plan=[ListedSeed("join", _identity(store, graph, "join").digest, generation)],
    )

    assert result.execution_origin == "preview_cache"


def test_an_expired_seed_plan_from_a_worker_is_a_conflict() -> None:
    from fastapi import HTTPException

    import haute.routes.pipeline as pipeline
    from haute._interactive_workers import InteractiveWorkerRemoteError
    from haute.errors import SeedPlanExpiredError

    payload = SeedPlanExpiredError(node_id="join").to_payload()
    error = InteractiveWorkerRemoteError(
        remote_type=SeedPlanExpiredError.__name__,
        remote_module=SeedPlanExpiredError.__module__,
        remote_message="expired",
        remote_traceback="",
        public_payload=payload,
    )

    with pytest.raises(HTTPException) as raised:
        pipeline._raise_interactive_remote_http_error(error, operation="pipeline_trace")

    assert raised.value.status_code == 409
    assert raised.value.detail == payload


def test_a_seeded_step_is_never_given_a_calculation(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    pl.DataFrame({"id": [1, 2], "premium": [100, 110]}).write_parquet(project / "premiums.parquet")
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "premiums.parquet")),
            ("P", NodeType.POLARS, _code("df = src.with_columns(pl.col('premium') * 2)")),
            ("T", NodeType.POLARS, _code("df = P.with_columns(pl.lit(1).alias('x'))")),
        ],
        [("src", "P"), ("P", "T")],
    )
    p1 = _publish(store, graph, "P", pl.DataFrame({"id": [1, 2], "premium": [200, 220]}))

    result = trace_result_to_dict(
        execute_trace(
            graph,
            target_node_id="T",
            column="premium",
            row_limit=200,
            seed_plan=[ListedSeed("P", _identity(store, graph, "P").digest, p1)],
        )
    )

    step = {step["node_id"]: step for step in result["steps"]}["P"]
    # Its row was read, not computed: no calculation doubles the doubled value.
    assert step["snapshot_generation_id"] == p1
    assert step["calculation"] is None
    assert step["expression"] is None
    assert result["output_value"] == 200


def test_correlation_through_the_uncached_branch_when_the_cached_one_is_ambiguous(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    pl.DataFrame({"id": list(range(20)), "a": [value % 4 for value in range(20)]}).write_parquet(
        project / "grouped.parquet"
    )
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "grouped.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns(pl.lit(1).alias('one'))")),
            ("B", NodeType.POLARS, _code("df = A.select('a').unique()")),
            ("C", NodeType.POLARS, _code("df = A.select('id', 'a')")),
            ("D", NodeType.POLARS, _code("df = C.join(B, on='a', how='left')")),
        ],
        [("src", "A"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")],
    )
    # ``B`` holds one row per group: through it, ``A``'s row is ambiguous.
    b1 = _publish(store, graph, "B", pl.DataFrame({"a": [0, 1, 2, 3]}))

    result = trace_result_to_dict(
        execute_trace(
            graph,
            target_node_id="D",
            row_limit=200,
            seed_plan=[ListedSeed("B", _identity(store, graph, "B").digest, b1)],
        )
    )

    steps = {step["node_id"]: step for step in result["steps"]}
    # Through ``C``, which identifies one row, ``A`` is traced exactly.
    assert "A" in steps
    assert steps["A"]["output_values"]["id"] == steps["D"]["output_values"]["id"]
    assert not result["omissions"]


def test_cached_work_that_could_not_be_admitted_is_still_traced_from_its_snapshot(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace

    # A sample has no row estimate: recomputing the group-by below it is refused
    # here, where no hard worker cap bounds an unestimated materialisation.
    graph = _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            (
                "sampled",
                NodeType.POLARS,
                _code("df = policies.collect().sample(fraction=1.0).lazy()"),
            ),
            ("G", NodeType.POLARS, _code("df = sampled.group_by('a').agg(pl.len().alias('n'))")),
            ("T", NodeType.POLARS, _code("df = G.with_columns(pl.lit(1).alias('x'))")),
        ],
        [("policies", "sampled"), ("sampled", "G"), ("G", "T")],
    )
    g1 = _publish(store, graph, "G", pl.DataFrame({"a": [0, 1], "n": [3, 4]}))

    result = execute_trace(
        graph,
        target_node_id="T",
        row_limit=200,
        seed_plan=[ListedSeed("G", _identity(store, graph, "G").digest, g1)],
    )

    steps = {step.node_id: step for step in result.steps}
    assert steps["G"].snapshot_generation_id == g1


def test_downstream_provenance_ends_at_the_seeded_step_with_its_value(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    pl.DataFrame({"id": [1, 2], "premium": [100, 110]}).write_parquet(project / "premiums.parquet")
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "premiums.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns(pl.lit(1).alias('x'))")),
            ("B", NodeType.POLARS, _code("df = A.with_columns(pl.col('premium') * 2)")),
            ("C", NodeType.POLARS, _code("df = A.select('id', 'x')")),
            ("D", NodeType.POLARS, _code("df = B.select('id', 'premium').join(C, on='id')")),
            (
                "E",
                NodeType.POLARS,
                _code("df = D.with_columns((pl.col('premium') + 1).alias('total'))"),
            ),
        ],
        [("src", "A"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "E")],
    )
    b1 = _publish(
        store,
        graph,
        "B",
        pl.DataFrame({"id": [1, 2], "premium": [200, 220], "x": [1, 1]}),
    )

    result = trace_result_to_dict(
        execute_trace(
            graph,
            target_node_id="E",
            column="total",
            row_limit=200,
            seed_plan=[ListedSeed("B", _identity(store, graph, "B").digest, b1)],
        )
    )

    steps = {step["node_id"]: step for step in result["steps"]}
    calculation = steps["E"]["calculation"]
    assert calculation is not None
    assert calculation["result_value"] == 201
    source = calculation["input_sources"]["premium"]
    # The premium came from the cached ``B``, not from the executed ``A``.
    assert source["node_id"] == "B"
    assert source["result_value"] == 200
    assert source["snapshot_generation_id"] == b1
    assert "input_sources" not in source


def test_a_pass_through_target_is_explained_from_the_seed_on_its_path(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    pl.DataFrame({"id": [1, 2], "base": [50, 55]}).write_parquet(project / "bases.parquet")
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "bases.parquet")),
            (
                "A",
                NodeType.POLARS,
                _code("df = src.with_columns((pl.col('base') * 2).alias('premium'))"),
            ),
            ("B", NodeType.POLARS, _code("df = A.with_columns(pl.col('premium') * 2)")),
            ("C", NodeType.POLARS, _code("df = A.select('id', 'base')")),
            ("D", NodeType.POLARS, _code("df = B.select('id', 'premium').join(C, on='id')")),
            ("T", NodeType.POLARS, _code("df = D.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "A"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "T")],
    )
    b1 = _publish(
        store,
        graph,
        "B",
        pl.DataFrame({"id": [1, 2], "base": [50, 55], "premium": [200, 220]}),
    )

    result = trace_result_to_dict(
        execute_trace(
            graph,
            target_node_id="T",
            column="premium",
            row_limit=200,
            seed_plan=[ListedSeed("B", _identity(store, graph, "B").digest, b1)],
        )
    )

    steps = {step["node_id"]: step for step in result["steps"]}
    assert result["output_value"] == 200
    # ``A`` creates a premium too, but not the one ``T`` reads: that came
    # from the snapshot of ``B``, which has no formula to show.
    assert steps["T"]["calculation"] is None
    assert steps["T"]["expression"] is None


def _colliding_join(project: Path, left: str, right: str) -> PipelineGraph:
    """``B`` and ``C`` each create a ``premium``; ``D`` joins *left* with *right*."""
    pl.DataFrame({"id": [1, 2], "base": [50, 55]}).write_parquet(project / "bases.parquet")
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "bases.parquet")),
            (
                "B",
                NodeType.POLARS,
                _code("df = src.select('id', (pl.col('base') * 4).alias('premium'))"),
            ),
            (
                "C",
                NodeType.POLARS,
                _code("df = src.select('id', (pl.col('base') * 6).alias('premium'))"),
            ),
            ("D", NodeType.POLARS, _code(f"df = {left}.join({right}, on='id')")),
            ("T", NodeType.POLARS, _code("df = D.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "B"), ("src", "C"), ("B", "D"), ("C", "D"), ("D", "T")],
    )


def test_a_pass_through_target_never_takes_the_formula_of_the_other_join_side(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    graph = _colliding_join(project, "B", "C")
    b1 = _publish(
        store,
        graph,
        "B",
        pl.DataFrame({"id": [1, 2], "premium": [200, 220]}),
    )

    result = trace_result_to_dict(
        execute_trace(
            graph,
            target_node_id="T",
            column="premium",
            row_limit=200,
            seed_plan=[ListedSeed("B", _identity(store, graph, "B").digest, b1)],
        )
    )

    steps = {step["node_id"]: step for step in result["steps"]}
    assert result["output_value"] == 200
    # ``C``'s premium reaches ``T`` only as ``premium_right``: its formula,
    # 50 * 6 = 300, must never explain the 200 ``T`` read from ``B``'s snapshot.
    assert steps["T"]["calculation"] is None
    assert steps["T"]["expression"] is None


def test_a_pass_through_target_is_explained_by_the_join_side_that_supplied_it(
    project: Path,
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    # ``C`` is the left side, and later in the graph's order than ``B``.
    graph = _colliding_join(project, "C", "B")

    result = trace_result_to_dict(
        execute_trace(graph, target_node_id="T", column="premium", row_limit=200, seed_plan=[])
    )

    calculation = {step["node_id"]: step for step in result["steps"]}["T"]["calculation"]
    assert result["output_value"] == 300
    assert calculation is not None
    assert calculation["result_value"] == 300


def test_a_pass_through_target_is_explained_by_the_last_assignment(project: Path) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    pl.DataFrame({"id": [1, 2], "base": [50, 55]}).write_parquet(project / "bases.parquet")
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "bases.parquet")),
            (
                "A",
                NodeType.POLARS,
                _code("df = src.with_columns((pl.col('base') * 2).alias('premium'))"),
            ),
            ("M", NodeType.POLARS, _code("df = A.with_columns(pl.col('premium') * 2)")),
            ("T", NodeType.POLARS, _code("df = M.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "A"), ("A", "M"), ("M", "T")],
    )

    result = trace_result_to_dict(
        execute_trace(graph, target_node_id="T", column="premium", row_limit=200, seed_plan=[])
    )

    calculation = {step["node_id"]: step for step in result["steps"]}["T"]["calculation"]
    assert result["output_value"] == 200
    # ``M`` doubled the premium ``A`` created: ``A``'s 50 * 2 = 100 would
    # explain a value ``T`` never had.
    assert calculation is not None
    assert calculation["result_value"] == 200


def test_a_pass_through_target_shows_no_formula_when_both_join_sides_hold_its_value(
    project: Path,
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    pl.DataFrame({"id": [1, 2], "base": [50, 55]}).write_parquet(project / "bases.parquet")
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "bases.parquet")),
            (
                "B",
                NodeType.POLARS,
                _code("df = src.select('id', (pl.col('base') * 4).alias('premium'))"),
            ),
            (
                "C",
                NodeType.POLARS,
                _code("df = src.select('id', (pl.col('base') * 2 + 100).alias('premium'))"),
            ),
            ("D", NodeType.POLARS, _code("df = C.join(B, on='id')")),
            ("T", NodeType.POLARS, _code("df = D.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "B"), ("src", "C"), ("B", "D"), ("C", "D"), ("D", "T")],
    )

    result = trace_result_to_dict(
        execute_trace(graph, target_node_id="T", column="premium", row_limit=200, seed_plan=[])
    )

    steps = {step["node_id"]: step for step in result["steps"]}
    assert result["output_value"] == 200
    # Both sides computed 200, so the value alone cannot say which formula
    # ``T``'s premium came from: neither is shown as if it were proven.
    assert steps["T"]["calculation"] is None
    assert steps["T"]["expression"] is None
