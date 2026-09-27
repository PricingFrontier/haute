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


def test_trace_of_seeded_preview_reads_the_join_and_traces_above_it(
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
    # The join's row is still the snapshot's; a recompute reproduced it, so the
    # sources above it are traced rather than skipped.
    assert steps["join"]["snapshot_generation_id"] == join["generation_id"]
    assert steps["banding"]["output_values"]["band"] == row["band"]
    assert steps["policies"]["output_values"] == {"id": row["id"], "a": row["a"]}
    assert steps["claims"]["output_values"] == {"id": row["id"], "d": row["d"]}
    assert steps["join"]["input_values"]["policies.id"] == row["id"]
    assert trace["trace"]["omissions"] == []
    assert {"policies", "claims", "join"} <= set(trace_builds)


def test_first_preview_then_trace_reads_the_capture(project: Path, api: Any) -> None:
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
    # ``A`` still ran for ``C``: it is a step, not an omission. The recompute
    # above ``B`` reaches the same ``A`` row, so the two paths agree.
    assert "A" in steps and "A" not in omitted
    assert steps["B"]["snapshot_generation_id"] == b1
    assert steps["B"]["input_values"]["a1"] == steps["A"]["output_values"]["a1"]
    assert steps["C"]["snapshot_generation_id"] is None
    assert not omitted
    assert not [
        diagnostic
        for diagnostic in result["correlation_diagnostics"]
        if diagnostic["code"] == "ancestor_row_conflict"
    ]


def _refresh(
    store: NodeSnapshotStore, graph: PipelineGraph, node_id: str, frame: pl.DataFrame
) -> str:
    from haute._execution_context import ExecutionProfile
    from haute._node_snapshots import NodeSnapshotColumns

    identity = _identity(store, graph, node_id)
    artifact = store.stage_node_output(identity)
    frame.write_parquet(artifact.part_path(0))
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


def _doubling(project: Path) -> PipelineGraph:
    """``src → P`` (doubles the premium) ``→ T``."""
    pl.DataFrame({"id": [1, 2], "premium": [100, 110]}).write_parquet(project / "premiums.parquet")
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "premiums.parquet")),
            ("P", NodeType.POLARS, _code("df = src.with_columns(pl.col('premium') * 2)")),
            ("T", NodeType.POLARS, _code("df = P.with_columns(pl.lit(1).alias('x'))")),
        ],
        [("src", "P"), ("P", "T")],
    )


def test_a_reproduced_seed_is_explained_from_its_traced_input(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    graph = _doubling(project)
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

    steps = {step["node_id"]: step for step in result["steps"]}
    # A recompute reproduced the snapshot row, so its input is traced and the
    # doubling is explained from it, not from its own output.
    assert steps["P"]["snapshot_generation_id"] == p1
    assert steps["P"]["input_values"] == {"id": 1, "premium": 100}
    assert steps["P"]["calculation"]["result_value"] == 200
    assert steps["src"]["output_values"] == {"id": 1, "premium": 100}
    assert result["output_value"] == 200


def test_a_seed_no_recompute_reproduces_is_never_given_a_calculation(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace, trace_result_to_dict

    graph = _doubling(project)
    p1 = _publish(store, graph, "P", pl.DataFrame({"id": [1, 2], "premium": [900, 990]}))

    result = trace_result_to_dict(
        execute_trace(
            graph,
            target_node_id="T",
            column="premium",
            row_limit=200,
            seed_plan=[ListedSeed("P", _identity(store, graph, "P").digest, p1)],
        )
    )

    steps = {step["node_id"]: step for step in result["steps"]}
    # Its row was read, not computed, and nothing above it holds that row: no
    # calculation doubles the doubled value, and ``src`` is not traced.
    assert steps["P"]["snapshot_generation_id"] == p1
    assert steps["P"]["calculation"] is None
    assert steps["P"]["expression"] is None
    assert result["output_value"] == 900
    assert "src" not in steps
    [omission] = result["omissions"]
    assert (omission["node_id"], omission["reason"]) == ("src", "seed_row_not_reproduced")
    diagnostic = result["correlation_diagnostics"][omission["diagnostic_index"]]
    assert diagnostic["severity"] == "info"
    assert diagnostic["seed_node_ids"] == ["P"]


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
    # Recomputing above ``G`` is refused here too, so its ancestors say so
    # rather than being traced from an unadmitted recompute.
    omissions = {omission.node_id: omission for omission in result.omissions}
    assert set(omissions) == {"policies", "sampled"}
    for omission in omissions.values():
        assert omission.reason == "seed_recompute_refused"
        diagnostic = result.correlation_diagnostics[omission.diagnostic_index]
        assert diagnostic["seed_node_ids"] == ["G"]
        assert diagnostic["reason_code"] == "materialisation_estimate_unavailable"


@pytest.mark.parametrize(
    ("premiums", "reproduced"),
    [([200, 220], True), ([900, 990], False)],
    ids=["reproduced", "not_reproduced"],
)
def test_downstream_provenance_continues_above_a_seed_only_when_reproduced(
    project: Path, store: NodeSnapshotStore, premiums: list[int], reproduced: bool
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
        pl.DataFrame({"id": [1, 2], "premium": premiums, "x": [1, 1]}),
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
    assert calculation["result_value"] == premiums[0] + 1
    source = calculation["input_sources"]["premium"]
    # The premium came from the cached ``B``, not from the executed ``A``.
    assert source["node_id"] == "B"
    assert source["result_value"] == premiums[0]
    if reproduced:
        # A recompute reproduced ``B``'s row: its doubling explains the value.
        assert source["expression_text"] == "premium * 2"
        assert "snapshot_generation_id" not in source
    else:
        # Nothing above ``B`` holds that row: provenance ends with its value.
        assert source["snapshot_generation_id"] == b1
        assert "input_sources" not in source
        assert "expression_text" not in source


@pytest.mark.parametrize(
    ("premiums", "reproduced"),
    [([200, 220], True), ([900, 990], False)],
    ids=["reproduced", "not_reproduced"],
)
def test_a_pass_through_target_borrows_a_seed_formula_only_when_reproduced(
    project: Path, store: NodeSnapshotStore, premiums: list[int], reproduced: bool
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
        pl.DataFrame({"id": [1, 2], "base": [50, 55], "premium": premiums}),
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
    assert result["output_value"] == premiums[0]
    # ``A`` creates a premium too, but not the one ``T`` reads: that came from
    # ``B``, whose doubling explains it only when a recompute reproduced it.
    if reproduced:
        assert steps["T"]["calculation"]["result_value"] == 200
        assert steps["T"]["expression"]["expression_text"] == "premium * 2"
    else:
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
    # 50 * 6 = 300, must never explain the 200 ``T`` read from ``B``'s
    # snapshot. A recompute reproduced ``B``'s row, so ``B``'s own formula does.
    assert steps["T"]["calculation"]["result_value"] == 200
    assert "* 4" in steps["T"]["expression"]["expression_text"]


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


def test_a_seed_the_recompute_holds_twice_is_ambiguous_above_it(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace

    pl.DataFrame({"id": [1, 2, 3], "a": [5, 5, 6]}).write_parquet(project / "dupes.parquet")
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "dupes.parquet")),
            ("P", NodeType.POLARS, _code("df = src.select('a')")),
            ("T", NodeType.POLARS, _code("df = P.with_columns(pl.lit(1).alias('x'))")),
        ],
        [("src", "P"), ("P", "T")],
    )
    p1 = _publish(store, graph, "P", pl.DataFrame({"a": [5, 5, 6]}))

    result = execute_trace(
        graph,
        target_node_id="T",
        row_limit=200,
        seed_plan=[ListedSeed("P", _identity(store, graph, "P").digest, p1)],
    )

    # Two recomputed rows equal the snapshot's: neither is chosen as its source.
    assert {step.node_id for step in result.steps} == {"P", "T"}
    assert [(omission.node_id, omission.reason) for omission in result.omissions] == [
        ("src", "seed_row_ambiguous")
    ]


def test_a_seed_whose_inputs_moved_is_not_traced_above(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace

    # A CSV input is snapshot-backed: preparing it for the recompute gives the
    # seed an identity other than the one its snapshot was leased under.
    claims = pl.DataFrame({"id": list(range(_ROWS)), "d": [value / 10 for value in range(_ROWS)]})
    claims.write_csv(project / "claims.csv")
    csv_input = {
        "inputType": "file",
        "format": "csv",
        "mode": "scan",
        "path": str(project / "claims.csv"),
    }
    graph = _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("claims", NodeType.DATA_INPUT, csv_input),
            ("join", NodeType.POLARS, _code("df = policies.join(claims, on='id', how='left')")),
            (
                "banding",
                NodeType.POLARS,
                _code("df = join.with_columns((pl.col('a') * 2).alias('band'))"),
            ),
        ],
        [("policies", "join"), ("claims", "join"), ("join", "banding")],
    )
    frame = pl.DataFrame(
        {
            "id": list(range(_ROWS)),
            "a": list(range(_ROWS)),
            "d": [value / 10 for value in range(_ROWS)],
        }
    )
    j1 = _publish(store, graph, "join", frame)

    result = execute_trace(
        graph,
        target_node_id="banding",
        row_limit=200,
        seed_plan=[ListedSeed("join", _identity(store, graph, "join").digest, j1)],
    )

    omissions = {omission.node_id: omission.reason for omission in result.omissions}
    assert omissions == {"policies": "seed_inputs_changed", "claims": "seed_inputs_changed"}
    assert {step.node_id for step in result.steps} == {"join", "banding"}


def test_a_seed_whose_row_is_unresolved_builds_nothing_above_it(
    project: Path, store: NodeSnapshotStore, trace_builds: Counter[str]
) -> None:
    from haute.trace import execute_trace

    # ``pipe`` hides what the code writes, so the join's row is unproven.
    banding = "df = join.pipe(lambda f: f.with_columns((pl.col('a') * 2).alias('band')))"
    graph = _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("claims", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("join", NodeType.POLARS, _code("df = policies.join(claims, on='id', how='left')")),
            ("banding", NodeType.POLARS, _code(banding)),
        ],
        [("policies", "join"), ("claims", "join"), ("join", "banding")],
    )
    j1 = _publish(store, graph, "join", pl.DataFrame({"id": [0, 1], "a": [0, 1], "d": [0.0, 0.1]}))

    result = execute_trace(
        graph,
        target_node_id="banding",
        row_limit=200,
        seed_plan=[ListedSeed("join", _identity(store, graph, "join").digest, j1)],
    )

    assert [(omission.node_id, omission.reason) for omission in result.omissions] == [
        ("join", "row_scope_unproven")
    ]
    assert not {"policies", "claims"} & set(trace_builds)


def _two_seeds(project: Path) -> PipelineGraph:
    """``P`` recomputes faithfully; ``G`` groups a sample, which is never admitted here."""
    return _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            (
                "P",
                NodeType.POLARS,
                _code("df = policies.with_columns((pl.col('a') * 2).alias('b'))"),
            ),
            (
                "sampled",
                NodeType.POLARS,
                _code("df = policies.collect().sample(fraction=1.0).lazy()"),
            ),
            ("G", NodeType.POLARS, _code("df = sampled.group_by('a').agg(pl.len().alias('n'))")),
            ("T", NodeType.POLARS, _code("df = P.join(G, on='a', how='left')")),
        ],
        [("policies", "P"), ("policies", "sampled"), ("sampled", "G"), ("P", "T"), ("G", "T")],
    )


def test_one_refused_seed_does_not_hide_what_another_seed_traces(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.trace import execute_trace

    graph = _two_seeds(project)
    rows = list(range(_ROWS))
    p1 = _publish(
        store,
        graph,
        "P",
        pl.DataFrame({"id": rows, "a": rows, "b": [value * 2 for value in rows]}),
    )
    g1 = _publish(store, graph, "G", pl.DataFrame({"a": rows, "n": [1] * _ROWS}))

    result = execute_trace(
        graph,
        target_node_id="T",
        row_limit=200,
        seed_plan=[
            ListedSeed("P", _identity(store, graph, "P").digest, p1),
            ListedSeed("G", _identity(store, graph, "G").digest, g1),
        ],
    )

    steps = {step.node_id: step for step in result.steps}
    # ``policies`` sits above both seeds: ``P``'s recompute traced it, so
    # ``G``'s refusal leaves only ``sampled`` untraced.
    assert steps["policies"].output_values["a"] == steps["T"].output_values["a"]
    assert [(omission.node_id, omission.reason) for omission in result.omissions] == [
        ("sampled", "seed_recompute_refused")
    ]


def _broken_above_seed(project: Path) -> PipelineGraph:
    """Building ``broken`` raises, so a recompute above ``S`` fails."""
    return _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            (
                "broken",
                NodeType.POLARS,
                _code("df = policies.with_columns(pl.lit(1 // 0).alias('z'))"),
            ),
            ("S", NodeType.POLARS, _code("df = broken.select('id', 'a')")),
            ("T", NodeType.POLARS, _code("df = S.with_columns(pl.lit(1).alias('x'))")),
        ],
        [("policies", "broken"), ("broken", "S"), ("S", "T")],
    )


@pytest.mark.parametrize("scenario", ["reproduced", "refused", "failed"])
def test_the_recompute_above_a_seed_leaves_the_trace_plan_in_place(
    project: Path, store: NodeSnapshotStore, scenario: str
) -> None:
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile
    from haute.trace import execute_trace

    rows = list(range(_ROWS))
    if scenario == "reproduced":
        graph, seed, target = _doubling(project), "P", "T"
        frame = pl.DataFrame({"id": [1, 2], "premium": [200, 220]})
        expected: set[str] = set()
    elif scenario == "refused":
        graph, seed, target = _two_seeds(project), "G", "T"
        frame = pl.DataFrame({"a": rows, "n": [1] * _ROWS})
        expected = {"seed_recompute_refused"}
    else:
        graph, seed, target = _broken_above_seed(project), "S", "T"
        frame = pl.DataFrame({"id": rows, "a": rows})
        expected = {"seed_recompute_failed"}
    generation = _publish(store, graph, seed, frame)
    context = create_admitted_execution_context(
        operation="trace_test", profile=ExecutionProfile.PREVIEW_EAGER
    )
    try:
        result = execute_trace(
            graph,
            target_node_id=target,
            row_limit=200,
            seed_plan=[ListedSeed(seed, _identity(store, graph, seed).digest, generation)],
            execution_context=context,
        )
        planned = set(context.projection_plan.projection_plan.needed_by_node)
    finally:
        context.release_admission()

    seed_reasons = {
        omission.reason for omission in result.omissions if omission.reason.startswith("seed_")
    }
    assert seed_reasons == expected
    # The recompute planned only the seed's lineage; the trace's own plan,
    # which reaches its target, is what the context holds afterwards.
    assert target in planned


def test_merging_a_continuation_never_overwrites_a_row_and_drops_superseded_evidence() -> None:
    from types import SimpleNamespace

    from haute.trace import _merge_seed_continuations, _SeedContinuation

    node_map = {
        node_id: SimpleNamespace(data=SimpleNamespace(label=node_id))
        for node_id in ("S", "A", "B", "C")
    }
    # The trace resolved ``A`` through an executed branch and failed on ``B``;
    # it left ``C`` unresolved for its own reason.
    cached_rows: dict = {"A": {"id": 1, "v": 3}, "B": None, "C": None}
    diagnostics = [
        {"code": "ambiguous_row_match", "node_id": "B", "severity": "warning"},
        {"code": "row_scope_unproven", "node_id": "C", "severity": "warning"},
    ]
    unresolved = {"B": ("duplicate_exact_match", 0), "C": ("row_scope_unproven", 1)}
    continuation = _SeedContinuation(
        seed_id="S",
        ancestors=("A", "B"),
        parents_of={"S": ["A", "B"], "A": [], "B": []},
        rows={"A": {"id": 1, "v": 2}, "B": {"id": 1, "w": 5}},
        positions={"A": 0, "B": 0},
    )

    proven, reported = _merge_seed_continuations(
        [continuation],
        node_map=node_map,
        cached_rows=cached_rows,
        row_positions={},
        frames={},
        row_frames={},
        diagnostics=diagnostics,
        unresolved=unresolved,
    )

    assert proven == {"S"} and reported == frozenset()
    # Two paths gave ``A`` different rows: neither is shown.
    assert cached_rows["A"] is None
    reason, index = unresolved["A"]
    assert reason == "ancestor_row_conflict"
    assert diagnostics[index]["seed_node_ids"] == ["S"]
    # ``B`` is traced above the seed; the failed attempt's evidence is gone and
    # the remaining links still point at their own node.
    assert cached_rows["B"] == {"id": 1, "w": 5}
    assert "B" not in unresolved
    assert [diagnostic["node_id"] for diagnostic in diagnostics] == ["C", "A"]
    assert diagnostics[unresolved["C"][1]]["node_id"] == "C"
