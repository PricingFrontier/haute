"""Seed plans: which node outputs a bounded run seeds and which it captures (CACHE-S07)."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import haute._seed_plans as seed_plans
from haute._data_points import DataPointResolver
from haute._execution_context import ExecutionProfile
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._registry import NODE_REGISTRY
from haute._seed_plans import (
    CaptureKind,
    ListedSeed,
    SeedDecision,
    SeedPlan,
    SeedPlanRequest,
    open_listed_seed_plan,
    open_resolved_seed_plan,
    open_seed_plan,
    resolve_seed_plan,
    seed_plan_fingerprint,
)
from haute._source_cache import SourceCacheGenerationMissingError, SourceCacheIdentity
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

ALL = NodeSnapshotColumns.all()
_WIDE = ["id", "a", "b", "c", "d", "a2", "c2"]

Edge = tuple[str, str] | GraphEdge


def _forbid_builder_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No builder should execute during seed plan resolution")

    for entry in NODE_REGISTRY.values():
        monkeypatch.setattr(entry, "exec", _fail)


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _parquet(path: Path) -> dict[str, Any]:
    return {"inputType": "file", "format": "parquet", "mode": "scan", "path": str(path)}


def _code(code: str) -> dict[str, Any]:
    return {"code": code}


def _graph(
    project: Path,
    nodes: list[tuple[str, NodeType, dict[str, Any]]],
    edges: list[Edge],
) -> PipelineGraph:
    return PipelineGraph(
        nodes=[_node(*spec) for spec in nodes],
        edges=[
            edge
            if isinstance(edge, GraphEdge)
            else GraphEdge(id=f"e{i}", source=edge[0], target=edge[1])
            for i, edge in enumerate(edges)
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame({"id": [1, 2, 3], "a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]}).write_parquet(
        haute_scratch / "quotes.parquet"
    )
    pl.DataFrame({"id": [1, 2, 3], "d": [0.1, 0.2, 0.3]}).write_parquet(
        haute_scratch / "claims.parquet"
    )
    return haute_scratch


@pytest.fixture()
def store(project: Path) -> NodeSnapshotStore:
    return NodeSnapshotStore(project)


def _chain(project: Path, *, costly: bool = False) -> PipelineGraph:
    """``src → A → B → C → T`` with column demands the planner knows exactly."""
    c_code = (
        "df = B.with_columns((pl.col('c') + 1).alias('c2')).sort('id')"
        if costly
        else "df = B.with_columns((pl.col('c') + 1).alias('c2'))"
    )
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("B", NodeType.POLARS, _code("df = A.filter(pl.col('a') > 0)")),
            ("C", NodeType.POLARS, _code(c_code)),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "B"), ("B", "C"), ("C", "T")],
    )


def _diamond(project: Path) -> PipelineGraph:
    """``A → B``, ``A → C``, ``B + C → D → T``."""
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("B", NodeType.POLARS, _code("df = A.select('id', 'a', 'a2')")),
            ("C", NodeType.POLARS, _code("df = A.select('id', 'b')")),
            ("D", NodeType.POLARS, _code("df = B.join(C, on='id', how='left')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "T")],
    )


def _identity(store: NodeSnapshotStore, graph: PipelineGraph, node_id: str) -> SourceCacheIdentity:
    resolver = DataPointResolver(graph, source="live", store=store)
    return resolver.node_output_slot(node_id).identity(resolver.node_output_signature(node_id))


def _publish(
    store: NodeSnapshotStore,
    graph: PipelineGraph,
    node_id: str,
    columns: list[str] | None = None,
    *,
    dependencies: dict[str, str] | None = None,
    refresh: bool = False,
) -> str:
    """Publish a generation of *node_id* holding *columns* (``None``: all) and return its id."""
    identity = _identity(store, graph, node_id)
    names = _WIDE if columns is None else columns
    artifact = store.stage_node_output(identity)
    pl.DataFrame({name: [1, 2, 3] for name in names}).write_parquet(artifact.part_path(0))
    with store.publish_node_output(
        identity,
        artifact,
        columns=ALL if columns is None else NodeSnapshotColumns.of(columns),
        dependencies=dependencies or {},
        explicit=refresh,
        profile=ExecutionProfile.TRAINING_PREP,
        refresh=refresh,
    ) as publication:
        assert publication.outcome == "published"
        assert publication.generation is not None
        return publication.generation.generation_id


def _request(
    graph: PipelineGraph,
    target: str = "T",
    *,
    required: dict[str, Any] | None = None,
    profile: ExecutionProfile = ExecutionProfile.TRAINING_PREP,
    **fields: Any,
) -> SeedPlanRequest:
    return SeedPlanRequest(
        graph=graph,
        target_node_id=target,
        source=fields.pop("source", "live"),
        profile=profile,
        required_columns_by_node=required,
        **fields,
    )


def _generation_dir(
    store: NodeSnapshotStore, identity: SourceCacheIdentity, generation: str
) -> Path:
    return store.inputs_root / identity.digest / "generations" / generation


def _corrupt(data_path: Path) -> None:
    """Replace a generation's data, under the project sandbox, with unreadable bytes."""
    if data_path.is_dir():
        from haute._chunked_writes import part_paths

        for part in part_paths(data_path):
            (data_path / part.name).write_bytes(b"corrupt")
    else:
        data_path.write_bytes(b"corrupt")


# ---------------------------------------------------------------------------
# Capture points
# ---------------------------------------------------------------------------


def test_capture_points_structural(project: Path, store: NodeSnapshotStore) -> None:
    join = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("J", NodeType.POLARS, _code("df = A.join(other, on='id', how='left')")),
            ("X", NodeType.POLARS, _code("df = J.filter(pl.col('a') > 0)")),
            ("Y", NodeType.POLARS, _code("df = X.with_columns(pl.lit(1).alias('one'))")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "J"), ("other", "J"), ("J", "X"), ("X", "Y"), ("Y", "T")],
    )
    decision = resolve_seed_plan(_request(join, required={"T": ["a", "d"]}), store=store)
    assert {node: capture.kind for node, capture in decision.captures.items()} == {
        "J": CaptureKind.MATERIALISING,
    }
    assert decision.skipped_captures == {
        "A": "slice_transparent_feeder",
        "Y": "cheap_segment",
    }

    fan_out = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("P", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("Q1", NodeType.POLARS, _code("df = P.select('id', 'a')")),
            ("Q2", NodeType.POLARS, _code("df = P.select('id', 'b')")),
            ("K", NodeType.POLARS, _code("df = Q1.join(Q2, on='id')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "P"), ("P", "Q1"), ("P", "Q2"), ("Q1", "K"), ("Q2", "K"), ("K", "T")],
    )
    decision = resolve_seed_plan(_request(fan_out), store=store)
    assert {node: capture.kind for node, capture in decision.captures.items()} == {
        "K": CaptureKind.MATERIALISING,
    }
    assert decision.skipped_captures == {
        "P": "cheap_segment",
        "Q1": "slice_transparent_feeder",
        "Q2": "slice_transparent_feeder",
    }


@pytest.mark.parametrize(
    "code", ["df = A.group_by('a').agg(pl.col('b').sum())", "df = A.sort('a')"]
)
def test_capture_points_materialising_only(
    project: Path, store: NodeSnapshotStore, code: str
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("G", NodeType.POLARS, _code(code)),
            ("X", NodeType.POLARS, _code("df = G.filter(pl.col('a') > 0)")),
            ("Y", NodeType.POLARS, _code("df = X.with_columns(pl.lit(1).alias('one'))")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "G"), ("G", "X"), ("X", "Y"), ("Y", "T")],
    )
    decision = resolve_seed_plan(_request(graph), store=store)
    assert {node: capture.kind for node, capture in decision.captures.items()} == {
        "G": CaptureKind.MATERIALISING,
    }
    assert decision.skipped_captures == {"Y": "cheap_segment"}


@pytest.mark.parametrize(("source", "captured"), [("batch", True), ("live", False)])
def test_capture_points_batch_model_score(
    project: Path, store: NodeSnapshotStore, source: str, captured: bool
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("M", NodeType.MODEL_SCORE, {}),
            ("X", NodeType.POLARS, _code("df = M.filter(pl.col('a') > 0)")),
            ("Y", NodeType.POLARS, _code("df = X.with_columns(pl.lit(1).alias('one'))")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "M"), ("M", "X"), ("X", "Y"), ("Y", "T")],
    )
    captures = resolve_seed_plan(_request(graph, source=source), store=store).captures
    assert (captures.get("M") is not None) is captured
    if captured:
        assert captures["M"].kind is CaptureKind.MODEL_SCORE


def test_capture_points_consumed_through_pass_through(
    project: Path, store: NodeSnapshotStore
) -> None:
    decision = resolve_seed_plan(_request(_chain(project)), store=store)
    assert decision.captures == {}
    assert decision.skipped_captures == {"C": "cheap_segment"}
    assert decision.pass_through_edges["T"].source == "C"

    output = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("J", NodeType.POLARS, _code("df = A.join(other, on='id', how='left')")),
            ("O", NodeType.DATA_OUTPUT, {"format": "parquet", "path": "out.parquet"}),
        ],
        [("src", "A"), ("A", "J"), ("other", "J"), ("J", "O")],
    )
    decision = resolve_seed_plan(_request(output, target="O"), store=store)
    assert {node: capture.kind for node, capture in decision.captures.items()} == {
        "J": CaptureKind.MATERIALISING,
    }
    assert decision.skipped_captures == {"A": "slice_transparent_feeder"}

    direct = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "T")],
    )
    assert resolve_seed_plan(_request(direct), store=store).captures == {}


def _two_input_modelling(project: Path) -> PipelineGraph:
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("B", NodeType.POLARS, _code("df = other.with_columns(pl.lit(1).alias('one'))")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("other", "B"), ("A", "T"), ("B", "T")],
    )


def _two_input_optimiser(project: Path, data_input: str | None) -> PipelineGraph:
    config: dict[str, Any] = {"data_input": data_input} if data_input is not None else {}
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("B", NodeType.POLARS, _code("df = other.with_columns(pl.lit(1).alias('one'))")),
            ("OPT", NodeType.OPTIMISER, config),
        ],
        [("src", "A"), ("other", "B"), ("A", "OPT"), ("B", "OPT")],
    )


def _built_pass_through_returns(graph: PipelineGraph, node_id: str) -> str:
    """Build *node_id* the way execution does and name the parent whose frame it returns."""
    from haute._execute_lazy import PreparedExecutionRequest, _build_funcs, _prepare_execution
    from haute.executor import _build_node_fn

    prepared = _prepare_execution(PreparedExecutionRequest(graph=graph, target_node_id=node_id))
    plan = prepared.graph_plan
    funcs = _build_funcs(
        [node_id],
        plan.node_map,
        plan.id_to_name,
        prepared.all_parents,
        _build_node_fn,
        incoming_edges_by_target=prepared.incoming_edges_by_target,
        all_incoming_edges_by_target=prepared.all_incoming_edges_by_target,
        all_node_map=graph.node_map,
    )
    fn, _is_source = funcs[node_id]
    edges = prepared.incoming_edges_by_target[node_id]
    frames = [pl.LazyFrame({"parent": [edge.source]}) for edge in edges]
    returned = fn(*frames)
    return str(returned.collect()["parent"][0])


@pytest.mark.parametrize(
    ("graph_factory", "node_id"),
    [
        (_two_input_modelling, "T"),
        (lambda project: _two_input_optimiser(project, "B"), "OPT"),
        (lambda project: _two_input_optimiser(project, "A"), "OPT"),
        (
            lambda project: _graph(
                project,
                [
                    ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
                    ("A", NodeType.POLARS, _code("df = src")),
                    ("OPT", NodeType.OPTIMISER, {}),
                ],
                [("src", "A"), ("A", "OPT")],
            ),
            "OPT",
        ),
        (
            lambda project: _graph(
                project,
                [
                    ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
                    ("A", NodeType.POLARS, _code("df = src")),
                    ("O", NodeType.DATA_OUTPUT, {"format": "parquet", "path": "out.parquet"}),
                ],
                [("src", "A"), ("A", "O")],
            ),
            "O",
        ),
    ],
)
def test_pass_through_selection_matches_builders(
    project: Path, graph_factory: Any, node_id: str
) -> None:
    from haute._builders import pass_through_selected_edge
    from haute._execute_lazy import PreparedExecutionRequest, _prepare_execution

    graph = graph_factory(project)
    prepared = _prepare_execution(PreparedExecutionRequest(graph=graph, target_node_id=node_id))
    edge = pass_through_selected_edge(
        graph.node_map[node_id],
        list(prepared.incoming_edges_by_target[node_id]),
        graph.node_map,
    )
    assert edge is not None
    assert edge.source == _built_pass_through_returns(graph, node_id)


def test_two_input_pass_through_is_not_a_join(project: Path, store: NodeSnapshotStore) -> None:
    decision = resolve_seed_plan(_request(_two_input_modelling(project)), store=store)
    assert decision.captures == {}
    assert decision.skipped_captures == {"A": "cheap_segment"}
    assert decision.executed_node_ids == {"src", "A", "T"}


def test_optimiser_second_input_selected_producer(project: Path, store: NodeSnapshotStore) -> None:
    graph = _two_input_optimiser(project, "B")
    cold = resolve_seed_plan(_request(graph, target="OPT"), store=store)
    assert cold.captures == {}
    assert cold.skipped_captures == {"B": "cheap_segment"}
    assert cold.executed_node_ids == {"other", "B", "OPT"}

    generation = _publish(store, graph, "B")
    warm = resolve_seed_plan(_request(graph, target="OPT"), store=store)
    assert {node: seed.generation_id for node, seed in warm.seeds.items()} == {"B": generation}
    assert warm.captures == {}
    assert warm.executed_node_ids == {"OPT"}


def test_pass_through_selecting_second_api_port(project: Path, store: NodeSnapshotStore) -> None:
    config = {
        "path": str(project / "records.json"),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {"name": "policy_id", "path": "$[:].policy_id", "type": "int", "selected": True}
                ],
            },
            {
                "path": "$[:].drivers[:]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[:].drivers[:].driver_id",
                        "type": "int",
                        "selected": True,
                    }
                ],
            },
        ],
    }
    graph = _graph(
        project,
        [
            ("api", NodeType.API_INPUT, config),
            ("OPT", NodeType.OPTIMISER, {"data_input": "drivers"}),
        ],
        [
            GraphEdge(id="p", source="api", target="OPT", sourceHandle="policies"),
            GraphEdge(id="d", source="api", target="OPT", sourceHandle="drivers"),
        ],
    )
    decision = resolve_seed_plan(_request(graph, target="OPT"), store=store)
    assert decision.pass_through_edges["OPT"].sourceHandle == "drivers"
    assert decision.captures == {}
    assert decision.seeds == {}

    # An API input read whole (a ratebook side input fed straight from it) is
    # built for the caller but is never a node-output capture: a bundle of
    # ports is not one frame, and its tables live in the JSON cache.
    consumed = resolve_seed_plan(
        _request(graph, target="OPT", consumed_node_ids=("OPT", "api")), store=store
    )
    assert consumed.captures == {}
    assert "api" in consumed.executed_node_ids


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_walk_stops_at_first_fresh_covering_candidate(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _chain(project)
    for node_id in ("A", "B", "C"):
        _publish(store, graph, node_id)
    upstream = {_identity(store, graph, node_id).digest for node_id in ("A", "B")}
    asked: list[str] = []
    latest = store.latest_generation

    def spy(identity: SourceCacheIdentity) -> Any:
        asked.append(identity.digest)
        return latest(identity)

    monkeypatch.setattr(store, "latest_generation", spy)
    decision = resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=store)

    assert set(decision.seeds) == {"C"}
    assert decision.executed_node_ids == {"T"}
    assert decision.captures == {}
    assert not upstream & set(asked)


def test_stale_candidate_is_never_seeded(project: Path, store: NodeSnapshotStore) -> None:
    graph = _chain(project)
    a1 = _publish(store, graph, "A", refresh=True)
    _publish(store, graph, "B", dependencies={_identity(store, graph, "A").digest: a1})
    a2 = _publish(store, graph, "A", refresh=True)

    decision = resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=store)

    assert {node: seed.generation_id for node, seed in decision.seeds.items()} == {"A": a2}
    assert {"B", "C"} <= decision.executed_node_ids


def test_partial_candidate_becomes_widening_capture(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _chain(project, costly=True)
    _publish(store, graph, "C", ["b"])

    decision = resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=store)

    assert decision.seeds == {}
    assert decision.captures["C"].columns == NodeSnapshotColumns.of({"a", "b"})
    assert decision.captures["C"].strict_columns == NodeSnapshotColumns.of({"a"})


@pytest.mark.parametrize("profile", [ExecutionProfile.DEPLOY_LIVE, ExecutionProfile.DEPLOY_BATCH])
def test_profiles_without_a_class_are_rejected(
    project: Path, store: NodeSnapshotStore, profile: ExecutionProfile
) -> None:
    with pytest.raises(ValueError, match="neither reads nor writes shared snapshots"):
        resolve_seed_plan(_request(_chain(project), profile=profile), store=store)


def test_explicit_build_seeds_strictly_upstream(project: Path, store: NodeSnapshotStore) -> None:
    graph = _chain(project)
    a = _publish(store, graph, "A")
    _publish(store, graph, "B")

    decision = resolve_seed_plan(
        _request(graph, target="B", profile=ExecutionProfile.NODE_SNAPSHOT, build_node_id="B"),
        store=store,
    )

    assert {node: seed.generation_id for node, seed in decision.seeds.items()} == {"A": a}
    assert "B" not in decision.captures
    assert decision.executed_node_ids == {"B"}


def test_refresh_disables_seeding_keeps_captures(project: Path, store: NodeSnapshotStore) -> None:
    graph = _diamond(project)
    for node_id in ("A", "B", "C", "D"):
        _publish(store, graph, node_id)

    decision = resolve_seed_plan(_request(graph, refresh=True), store=store)

    assert decision.seeds == {}
    assert set(decision.captures) == {"D"}
    assert decision.skipped_captures == {
        "A": "cheap_segment",
        "B": "slice_transparent_feeder",
        "C": "slice_transparent_feeder",
    }


def test_disjoint_demand_negotiates_join_columns(project: Path, store: NodeSnapshotStore) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("J", NodeType.POLARS, _code("df = src.join(other, on='id', how='left')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "J"), ("other", "J"), ("J", "T")],
    )
    _publish(store, graph, "J", ["a", "b"])

    decision = resolve_seed_plan(_request(graph, required={"T": ["c"]}), store=store)

    assert decision.seeds == {}
    capture = decision.captures["J"]
    assert capture.columns == NodeSnapshotColumns.of({"a", "b", "c"})
    assert capture.strict_columns == NodeSnapshotColumns.of({"c"})
    assert {"a", "b", "c"} <= set(decision.planning_required_columns["J"])


def test_narrow_upstream_seed_is_dropped_and_widened(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.sort('id')")),
            ("G", NodeType.POLARS, _code("df = A.filter(pl.col('a') > 0).sort('a')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "G"), ("G", "T")],
    )
    _publish(store, graph, "A", ["a"])
    _publish(store, graph, "G", ["b"])

    decision = resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=store)

    assert decision.seeds == {}
    assert decision.executed_node_ids == {"src", "A", "G", "T"}
    assert decision.captures["A"].columns == NodeSnapshotColumns.of({"a", "b"})
    assert decision.captures["G"].columns == NodeSnapshotColumns.of({"a", "b"})


def test_linear_chain_keeps_seed_after_ancestor_clear(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _chain(project)
    a1 = _publish(store, graph, "A")
    b1 = _publish(store, graph, "B", dependencies={_identity(store, graph, "A").digest: a1})
    store.clear(_identity(store, graph, "A"))

    decision = resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=store)

    assert {node: seed.generation_id for node, seed in decision.seeds.items()} == {"B": b1}


def test_recomputed_branch_seeds_recorded_ancestor(project: Path, store: NodeSnapshotStore) -> None:
    graph = _diamond(project)
    a1 = _publish(store, graph, "A")
    b1 = _publish(store, graph, "B", dependencies={_identity(store, graph, "A").digest: a1})

    decision = resolve_seed_plan(_request(graph), store=store)

    assert {node: seed.generation_id for node, seed in decision.seeds.items()} == {
        "A": a1,
        "B": b1,
    }
    assert "C" in decision.executed_node_ids


def test_recomputed_branch_with_cleared_ancestor_drops_seed(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _diamond(project)
    a1 = _publish(store, graph, "A")
    _publish(store, graph, "B", dependencies={_identity(store, graph, "A").digest: a1})
    store.clear(_identity(store, graph, "A"))

    decision = resolve_seed_plan(_request(graph), store=store)

    assert decision.seeds == {}
    assert {"src", "A", "B", "C", "D"} <= decision.executed_node_ids


def test_recorded_ancestor_not_covering_recomputed_demand_drops_seed(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _diamond(project)
    a1 = _publish(store, graph, "A", ["id"])
    _publish(store, graph, "B", dependencies={_identity(store, graph, "A").digest: a1})

    decision = resolve_seed_plan(_request(graph), store=store)

    assert decision.seeds == {}
    assert {"A", "B", "C"} <= decision.executed_node_ids


def test_conflicting_recorded_generations_drop_both_seeds(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _diamond(project)
    a_digest = _identity(store, graph, "A").digest
    a1 = _publish(store, graph, "A", refresh=True)
    _publish(store, graph, "B", dependencies={a_digest: a1})
    a2 = _publish(store, graph, "A", refresh=True)
    _publish(store, graph, "C", dependencies={a_digest: a2})
    store.clear(_identity(store, graph, "A"))
    resolution = DataPointResolver(graph, source="live", store=store)
    for node_id in ("B", "C"):
        status = store.slot_status(
            resolution.node_output_slot(node_id), resolution.node_output_signature(node_id)
        )
        assert status.state == "current"

    decision = resolve_seed_plan(_request(graph), store=store)

    assert decision.seeds == {}
    assert {"src", "A", "B", "C", "D"} <= decision.executed_node_ids


def test_ancestor_refreshed_between_reads_is_not_combined_with_its_old_descendant(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Edge order makes the walk read B before A, so A can move between the reads.
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("B", NodeType.POLARS, _code("df = A.select('id', 'a', 'a2')")),
            ("C", NodeType.POLARS, _code("df = A.select('id', 'b')")),
            ("D", NodeType.POLARS, _code("df = B.join(C, on='id', how='left')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "B"), ("A", "C"), ("C", "D"), ("B", "D"), ("D", "T")],
    )
    a1 = _publish(store, graph, "A", refresh=True)
    _publish(store, graph, "B", dependencies={_identity(store, graph, "A").digest: a1})
    b_digest = _identity(store, graph, "B").digest
    refreshed: list[str] = []
    latest = store.latest_generation

    def refresh_a_after_reading_b(identity: SourceCacheIdentity) -> Any:
        found = latest(identity)
        if identity.digest == b_digest and not refreshed:
            refreshed.append(_publish(store, graph, "A", refresh=True))
        return found

    monkeypatch.setattr(store, "latest_generation", refresh_a_after_reading_b)
    decision = resolve_seed_plan(_request(graph), store=store)

    assert {node: seed.generation_id for node, seed in decision.seeds.items()} == {
        "A": refreshed[0]
    }
    assert {"B", "C", "D"} <= decision.executed_node_ids


def test_seed_gone_stale_before_its_lease_is_reresolved(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _chain(project)
    a1 = _publish(store, graph, "A", refresh=True)
    _publish(store, graph, "B", dependencies={_identity(store, graph, "A").digest: a1})
    refreshed: list[str] = []
    resolve = seed_plans.resolve_seed_plan

    def resolve_then_refresh_ancestor(request: SeedPlanRequest, *, store: NodeSnapshotStore) -> Any:
        decision = resolve(request, store=store)
        if not refreshed:
            refreshed.append(_publish(store, graph, "A", refresh=True))
        return decision

    monkeypatch.setattr(seed_plans, "resolve_seed_plan", resolve_then_refresh_ancestor)
    with open_resolved_seed_plan(_request(graph, required={"T": ["a"]}), store=store) as plan:
        assert {node: seed.generation_id for node, seed in plan.decision.seeds.items()} == {
            "A": refreshed[0]
        }


def test_retained_seed_carries_the_negotiated_demand(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("G", NodeType.POLARS, _code("df = A.filter(pl.col('a') > 0).sort('a')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "G"), ("G", "T")],
    )
    _publish(store, graph, "A")
    _publish(store, graph, "G", ["b"])

    decision = resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=store)

    assert set(decision.seeds) == {"A"}
    assert decision.seeds["A"].demand == NodeSnapshotColumns.of({"a", "b"})
    assert decision.captures["G"].columns == NodeSnapshotColumns.of({"a", "b"})


def test_corrupt_generation_fails_resolution_naming_the_node(
    project: Path, store: NodeSnapshotStore
) -> None:
    """Resolution says which node's cache is unreadable, not just that one is.

    The store reports corruption against an identity. Every preview and run
    through the lineage failed with that text, and nothing in it told the user
    whose cache button to press.
    """
    from haute.errors import SnapshotCorruptError

    graph = _chain(project)
    generation = _publish(store, graph, "C")
    identity = _identity(store, graph, "C")
    _corrupt(_generation_dir(store, identity, generation))

    with pytest.raises(SnapshotCorruptError) as raised:
        resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=NodeSnapshotStore(project))

    assert raised.value.node_id == "C"
    payload = raised.value.to_payload()
    assert payload["error_code"] == "snapshot_corrupt"
    assert payload["node_id"] == "C"


def test_resolution_drops_across_rounds_until_stable(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("D", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("E", NodeType.POLARS, _code("df = D.select('id', 'a', 'a2')")),
            ("F", NodeType.POLARS, _code("df = D.select('id', 'b')")),
            ("G", NodeType.POLARS, _code("df = E.join(F, on='id', how='left')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "D"), ("D", "E"), ("D", "F"), ("E", "G"), ("F", "G"), ("G", "T")],
    )
    d1 = _publish(store, graph, "D")
    _publish(store, graph, "E", dependencies={_identity(store, graph, "D").digest: d1})
    store.clear(_identity(store, graph, "D"))
    rounds: list[int] = []
    negotiate = seed_plans._Resolver.negotiate

    def counting(self: Any, *args: Any, **kwargs: Any) -> Any:
        rounds.append(1)
        return negotiate(self, *args, **kwargs)

    monkeypatch.setattr(seed_plans._Resolver, "negotiate", counting)
    decision = resolve_seed_plan(_request(graph), store=store)

    assert len(rounds) == 2
    assert decision.seeds == {}
    assert decision.executed_node_ids == {"src", "D", "E", "F", "G", "T"}


def test_nodes_outside_the_lineage_are_rejected(project: Path, store: NodeSnapshotStore) -> None:
    graph = _two_input_modelling(project)
    with pytest.raises(ValueError, match="Consumed nodes are not in the target's lineage"):
        resolve_seed_plan(_request(graph, target="A", consumed_node_ids=("B",)), store=store)
    with pytest.raises(ValueError, match="explicit build's node is not in the target's lineage"):
        resolve_seed_plan(_request(graph, target="A", build_node_id="B"), store=store)


def test_capture_columns_widen_a_capture_best_effort(
    project: Path, store: NodeSnapshotStore
) -> None:
    decision = resolve_seed_plan(
        _request(
            _chain(project, costly=True),
            required={"T": ["a"]},
            capture_columns_by_node={"C": ["b"]},
        ),
        store=store,
    )
    assert decision.captures["C"].columns == NodeSnapshotColumns.of({"a", "b"})
    assert decision.captures["C"].strict_columns == NodeSnapshotColumns.of({"a"})


def test_unresolved_all_except_demand_captures_all_columns(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.projection import AllExcept

    graph = _chain(project, costly=True)
    _publish(store, graph, "C", ["a", "b"])
    decision = resolve_seed_plan(
        _request(
            graph,
            target="C",
            required={
                "C": AllExcept(required_columns=frozenset({"a"}), excluded_columns=frozenset({"b"}))
            },
        ),
        store=store,
    )
    assert decision.seeds == {}
    assert decision.captures["C"].columns == ALL


def test_consumed_side_input_is_built_but_not_through_the_pass_through(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _two_input_optimiser(project, "A")
    decision = resolve_seed_plan(
        _request(graph, target="OPT", consumed_node_ids=("A", "B")), store=store
    )
    assert decision.executed_node_ids == {"src", "other", "A", "B", "OPT"}
    assert decision.captures == {}
    assert decision.skipped_captures == {"A": "cheap_segment", "B": "cheap_segment"}


# ---------------------------------------------------------------------------
# Leases, handoff, fingerprint
# ---------------------------------------------------------------------------


def test_plan_leases_hold_through_refresh_and_clear(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _chain(project)
    c1 = _publish(store, graph, "C")
    identity = _identity(store, graph, "C")

    plan = open_resolved_seed_plan(_request(graph, required={"T": ["a"]}), store=store)
    assert {node: seed.generation_id for node, seed in plan.decision.seeds.items()} == {"C": c1}
    _publish(store, graph, "C", refresh=True)
    store.clear(identity)
    assert _generation_dir(store, identity, c1).is_dir()

    plan.close()
    assert not _generation_dir(store, identity, c1).exists()


def test_seed_retired_before_lease_reresolves_then_gives_up(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _chain(project)
    _publish(store, graph, "C")
    attempts: list[str] = []

    def retired(identity: SourceCacheIdentity, generation_id: str) -> Any:
        attempts.append(generation_id)
        raise SourceCacheGenerationMissingError("retired")

    monkeypatch.setattr(store, "lease_generation", retired)
    with pytest.raises(SourceCacheGenerationMissingError, match="kept changing"):
        open_resolved_seed_plan(_request(graph, required={"T": ["a"]}), store=store)
    assert len(attempts) == 3


def test_seed_refreshed_between_resolution_and_lease_is_reresolved(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _chain(project)
    c1 = _publish(store, graph, "C")
    identity = _identity(store, graph, "C")
    holder = store.lease_generation(identity, c1)
    holder.__enter__()  # another operation keeps c1 alive, so its lease succeeds
    refreshed: list[str] = []
    resolve = seed_plans.resolve_seed_plan

    def resolve_then_refresh(request: SeedPlanRequest, *, store: NodeSnapshotStore) -> Any:
        decision = resolve(request, store=store)
        if not refreshed:
            refreshed.append(_publish(store, graph, "C", refresh=True))
        return decision

    monkeypatch.setattr(seed_plans, "resolve_seed_plan", resolve_then_refresh)
    try:
        with open_resolved_seed_plan(_request(graph, required={"T": ["a"]}), store=store) as plan:
            assert plan.decision.seeds["C"].generation_id == refreshed[0]
    finally:
        holder.__exit__(None, None, None)


def test_handoff_round_trip(project: Path, store: NodeSnapshotStore) -> None:
    graph = _chain(project)
    c1 = _publish(store, graph, "C")
    identity = _identity(store, graph, "C")
    parent = open_resolved_seed_plan(_request(graph, required={"T": ["a"]}), store=store)
    handoff = pickle.loads(pickle.dumps(parent.handoff()))

    child = SeedPlan.adopt(handoff)
    parent.close()
    _publish(store, graph, "C", refresh=True)
    store.clear(identity)
    assert _generation_dir(store, identity, c1).is_dir()
    child.close()
    assert not _generation_dir(store, identity, c1).exists()

    with pytest.raises(SourceCacheGenerationMissingError):
        SeedPlan.adopt(handoff)


def test_plan_fingerprint(project: Path, store: NodeSnapshotStore) -> None:
    identity = SourceCacheIdentity(provider="node_output", descriptor={"node": "x"})
    other = SourceCacheIdentity(provider="node_output", descriptor={"node": "y"})

    def seed(node: str, ident: SourceCacheIdentity, generation: str) -> SeedDecision:
        return SeedDecision(node, ident, generation, ALL, ALL, {})

    g1, g2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    both = {"x": seed("x", identity, g1), "y": seed("y", other, g2)}
    assert seed_plan_fingerprint(both) == seed_plan_fingerprint(dict(reversed(both.items())))
    assert seed_plan_fingerprint(both) != seed_plan_fingerprint(
        {**both, "x": seed("x", identity, g2)}
    )
    assert seed_plan_fingerprint({}) == seed_plan_fingerprint({})
    assert seed_plan_fingerprint({}).startswith("seed-plan:v1:")

    graph = _chain(project)
    _publish(store, graph, "C")
    decision = resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=store)
    assert decision.fingerprint == seed_plan_fingerprint(decision.seeds)


def test_plan_owns_what_the_run_registers(project: Path, store: NodeSnapshotStore) -> None:
    graph = _chain(project)
    identity = _identity(store, graph, "B")
    plan = open_resolved_seed_plan(_request(graph, required={"T": ["a"]}), store=store)
    assert plan.fingerprint == plan.decision.fingerprint

    artifact = store.stage_node_output(identity)
    pl.DataFrame({"a": [1]}).write_parquet(artifact.part_path(0))
    plan.register_artifact(artifact)
    published = store.stage_node_output(identity)
    pl.DataFrame({"a": [1]}).write_parquet(published.part_path(0))
    publication = store.publish_node_output(
        identity,
        published,
        columns=NodeSnapshotColumns.of({"a"}),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    )
    assert publication.generation is not None
    generation = publication.generation.generation_id
    plan.register_publication(publication)
    plan.record_closure("B", {"a" * 64: generation})
    assert plan.dependencies_for("B") == {"a" * 64: generation}
    assert plan.dependencies_for("C") == {}

    _publish(store, graph, "B", refresh=True)
    assert _generation_dir(store, identity, generation).is_dir()
    plan.close()
    plan.close()
    assert not artifact.directory.exists()
    assert not _generation_dir(store, identity, generation).exists()


def test_close_discards_own_token_staging_only(project: Path, store: NodeSnapshotStore) -> None:
    graph = _chain(project)
    _publish(store, graph, "C")
    owner = open_resolved_seed_plan(_request(graph, required={"T": ["a"]}), store=store)
    child = SeedPlan.adopt(owner.handoff(), store)
    staged = store.stage_node_output(
        _identity(store, graph, "B"), staging_token=owner.staging_token
    )

    child.close()
    assert staged.directory.is_dir()
    owner.close()
    assert not staged.directory.exists()


def test_open_seed_plan_prepares_only_readable_inputs_first(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._input_preparation as input_preparation

    events: list[tuple[str, Any]] = []

    def prepare(order: list[str], *args: Any, **kwargs: Any) -> None:
        events.append(("prepare", list(order)))

    def resolved(
        request: SeedPlanRequest, *, store: NodeSnapshotStore, staging_token: str | None
    ) -> Any:
        assert staging_token == "a1b2c3d4e5f6"
        events.append(("resolve", store.root))
        return SeedPlan(decision=None, store=store, staging_token="t", owns_staging=False)  # type: ignore[arg-type]

    monkeypatch.setattr(input_preparation, "prepare_input_snapshots", prepare)
    monkeypatch.setattr(seed_plans, "open_resolved_seed_plan", resolved)
    monkeypatch.setattr("haute._sandbox._get_project_root", lambda: project)

    open_seed_plan(_request(_two_input_modelling(project)), staging_token="a1b2c3d4e5f6")

    assert [event for event, _ in events] == ["prepare", "resolve"]
    assert events[0][1] == ["src", "A", "T"]
    assert events[1][1] == project.resolve()


# ---------------------------------------------------------------------------
# Previews (CACHE-S09)
# ---------------------------------------------------------------------------


def _preview(graph: PipelineGraph, target: str, **fields: Any) -> SeedPlanRequest:
    return _request(graph, target, profile=ExecutionProfile.PREVIEW_EAGER, **fields)


def _joined(project: Path) -> PipelineGraph:
    """``src → A``; ``A + other → J → X → G → Y``, with ``G`` a group-by."""
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("J", NodeType.POLARS, _code("df = A.join(other, on='id', how='left')")),
            ("X", NodeType.POLARS, _code("df = J.filter(pl.col('a') > 0)")),
            ("G", NodeType.POLARS, _code("df = X.group_by('a').agg(pl.col('d').sum())")),
            ("Y", NodeType.POLARS, _code("df = G.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "A"), ("A", "J"), ("other", "J"), ("J", "X"), ("X", "G"), ("G", "Y")],
    )


def _kinds(decision: Any) -> dict[str, CaptureKind]:
    return {node: capture.kind for node, capture in decision.captures.items()}


def test_preview_capture_points_are_joins_and_materialisations(
    project: Path, store: NodeSnapshotStore
) -> None:
    decision = resolve_seed_plan(_preview(_joined(project), "Y"), store=store)
    # ``A`` feeds a join and ``Y`` is the consumed target: neither is captured.
    assert _kinds(decision) == {
        "J": CaptureKind.MATERIALISING,
        "G": CaptureKind.MATERIALISING,
    }

    # Two inputs without a materialising operation are a structural join.
    concat = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("H", NodeType.POLARS, _code("df = pl.concat([src, other], how='diagonal')")),
            ("Y", NodeType.POLARS, _code("df = H.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "H"), ("other", "H"), ("H", "Y")],
    )
    assert _kinds(resolve_seed_plan(_preview(concat, "Y"), store=store)) == {
        "H": CaptureKind.STRUCTURAL
    }


def test_preview_captures_a_join_or_group_by_target_and_a_join_feeding_a_join(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _joined(project)
    assert _kinds(resolve_seed_plan(_preview(graph, "J"), store=store)) == {
        "J": CaptureKind.MATERIALISING
    }
    assert _kinds(resolve_seed_plan(_preview(graph, "G"), store=store)) == {
        "J": CaptureKind.MATERIALISING,
        "G": CaptureKind.MATERIALISING,
    }

    chained = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("third", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("J1", NodeType.POLARS, _code("df = src.join(other, on='id', how='left')")),
            ("J2", NodeType.POLARS, _code("df = J1.join(third, on='id', how='left')")),
            ("Y", NodeType.POLARS, _code("df = J2.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "J1"), ("other", "J1"), ("J1", "J2"), ("third", "J2"), ("J2", "Y")],
    )
    assert _kinds(resolve_seed_plan(_preview(chained, "Y"), store=store)) == {
        "J1": CaptureKind.MATERIALISING,
        "J2": CaptureKind.MATERIALISING,
    }


def test_preview_never_captures_a_plain_fan_out_feeder_target_or_model_score(
    project: Path, store: NodeSnapshotStore
) -> None:
    fan_out = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("P", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("Q1", NodeType.POLARS, _code("df = P.select('id', 'a')")),
            ("Q2", NodeType.POLARS, _code("df = P.select('id', 'b')")),
            ("K", NodeType.POLARS, _code("df = Q1.join(Q2, on='id')")),
            ("Y", NodeType.POLARS, _code("df = K.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "P"), ("P", "Q1"), ("P", "Q2"), ("Q1", "K"), ("Q2", "K"), ("K", "Y")],
    )
    # A bounded run captures the fan-out ``P`` and both join feeders too.
    assert _kinds(resolve_seed_plan(_preview(fan_out, "Y"), store=store)) == {
        "K": CaptureKind.MATERIALISING
    }
    assert _kinds(resolve_seed_plan(_preview(_chain(project), "C"), store=store)) == {}

    scored = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("M", NodeType.MODEL_SCORE, {}),
            ("Y", NodeType.POLARS, _code("df = M.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "M"), ("M", "Y")],
    )
    # Under a row limit a Model Score scores row-locally: it is no capture point.
    assert _kinds(resolve_seed_plan(_preview(scored, "Y", source="batch"), store=store)) == {}


@pytest.mark.parametrize(("source", "captured"), [("batch", True), ("live", False)])
def test_preview_captures_a_batch_model_score_a_capture_below_drains(
    project: Path, store: NodeSnapshotStore, source: str, captured: bool
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("M", NodeType.MODEL_SCORE, {}),
            ("J", NodeType.POLARS, _code("df = M.join(other, on='id', how='left')")),
            ("Y", NodeType.POLARS, _code("df = J.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "M"), ("M", "J"), ("other", "J"), ("J", "Y")],
    )
    # ``J`` writes every row of ``M``: a row-local scan drained whole would
    # hold the scored frame in memory, so ``M`` writes its own scored parts.
    expected = {"J": CaptureKind.MATERIALISING}
    if captured:
        expected["M"] = CaptureKind.MODEL_SCORE
    assert _kinds(resolve_seed_plan(_preview(graph, "Y", source=source), store=store)) == expected
    # Previewing the scorer itself drains nothing: it still scores row-locally.
    assert _kinds(resolve_seed_plan(_preview(graph, "M", source=source), store=store)) == {}


def test_preview_may_seed_its_target(project: Path, store: NodeSnapshotStore) -> None:
    graph = _joined(project)
    j1 = _publish(store, graph, "J")

    decision = resolve_seed_plan(_preview(graph, "J"), store=store)

    assert {node: seed.generation_id for node, seed in decision.seeds.items()} == {"J": j1}
    assert decision.captures == {}
    assert decision.executed_node_ids == frozenset()


def _csv(path: Path) -> dict[str, Any]:
    return {"inputType": "file", "format": "csv", "path": str(path)}


def _csv_joined(project: Path) -> PipelineGraph:
    """Two snapshot-backed CSV inputs joined, then banded: ``p + c → J → B``."""
    pl.DataFrame({"id": [1, 2, 3], "a": [1, 2, 3]}).write_csv(project / "policies.csv")
    pl.DataFrame({"id": [1, 2, 3], "d": [0.1, 0.2, 0.3]}).write_csv(project / "claims.csv")
    return _graph(
        project,
        [
            ("p", NodeType.DATA_INPUT, _csv(project / "policies.csv")),
            ("c", NodeType.DATA_INPUT, _csv(project / "claims.csv")),
            ("J", NodeType.POLARS, _code("df = p.join(c, on='id', how='left')")),
            ("B", NodeType.POLARS, _code("df = J.with_columns(pl.lit(1).alias('band'))")),
        ],
        [("p", "J"), ("c", "J"), ("J", "B")],
    )


def _record_preparation(monkeypatch: pytest.MonkeyPatch, project: Path) -> list[list[str]]:
    import haute._input_preparation as input_preparation

    prepared: list[list[str]] = []
    monkeypatch.setattr(
        input_preparation,
        "prepare_input_snapshots",
        lambda order, *args, **kwargs: prepared.append(list(order)),
    )
    monkeypatch.setattr("haute._sandbox._get_project_root", lambda: project)
    return prepared


def test_preview_prepares_only_inputs_its_execution_reads(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _csv_joined(project)
    prepared = _record_preparation(monkeypatch, project)
    j1 = _publish(store, graph, "J")

    with open_seed_plan(_preview(graph, "B"), store=store) as plan:
        assert {node: seed.generation_id for node, seed in plan.decision.seeds.items()} == {"J": j1}
    # Every input sits above the seed: nothing is prepared.
    assert prepared == []


def test_stale_input_drops_seeds_below_it_before_preparation(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _csv_joined(project)
    prepared = _record_preparation(monkeypatch, project)
    _publish(store, graph, "J")
    # A rewritten source changes every signature below its input.
    pl.DataFrame({"id": [1, 2, 3, 4], "a": [1, 2, 3, 4]}).write_csv(project / "policies.csv")

    with open_seed_plan(_preview(graph, "B"), store=store) as plan:
        assert plan.decision.seeds == {}
        assert {"p", "c", "J"} <= plan.decision.executed_node_ids
        assert set(plan.decision.captures) == {"J"}
    assert prepared == [["p", "c"]]


def test_a_capture_published_while_preparing_is_seeded(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._input_preparation as input_preparation

    graph = _csv_joined(project)
    monkeypatch.setattr("haute._sandbox._get_project_root", lambda: project)
    prepared: list[list[str]] = []
    published: list[str] = []

    def prepare(order: list[str], *args: Any, **kwargs: Any) -> None:
        # Meanwhile another execution captured the join under the signatures
        # the prepared inputs produce.
        prepared.append(list(order))
        published.append(_publish(store, graph, "J"))

    monkeypatch.setattr(input_preparation, "prepare_input_snapshots", prepare)

    with open_seed_plan(_preview(graph, "B"), store=store) as plan:
        assert {node: seed.generation_id for node, seed in plan.decision.seeds.items()} == {
            "J": published[0]
        }
        assert plan.decision.executed_node_ids == frozenset({"B"})
    assert prepared == [["p", "c"]]


def test_a_moved_input_pointer_re_resolves_under_the_prepared_signatures(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._input_preparation as input_preparation
    from haute._execution_admission import create_admitted_execution_context
    from haute._native_memory_limit import native_memory_backend_scope
    from haute._source_cache import SourceCacheStore

    graph = _csv_joined(project)
    monkeypatch.setattr("haute._sandbox._get_project_root", lambda: project)
    before = _identity(store, graph, "J").digest
    real_prepare = input_preparation.prepare_input_snapshots
    prepared: list[list[str]] = []

    def prepare(order: list[str], node_map: Any, **kwargs: Any) -> Any:
        # Really build both input snapshots: their pointers move from missing
        # to a generation, and every signature below them moves with them.
        prepared.append(list(order))
        context = create_admitted_execution_context(
            operation="seed_plan_test", profile=ExecutionProfile.LAZY_SINK
        )
        try:
            with native_memory_backend_scope("rlimit"):
                return real_prepare(
                    order,
                    node_map,
                    profile=ExecutionProfile.LAZY_SINK,
                    execution_context=context,
                    base_dir=kwargs["base_dir"],
                    schema_only=False,
                    store=SourceCacheStore(project),
                )
        finally:
            context.release_admission()

    monkeypatch.setattr(input_preparation, "prepare_input_snapshots", prepare)

    with open_seed_plan(_preview(graph, "B"), store=store) as plan:
        after = _identity(store, graph, "J").digest
        assert after != before
        # The capture is written under the signature the prepared inputs give.
        assert plan.decision.captures["J"].identity.digest == after
    assert prepared == [["p", "c"]]


def test_preparation_rounds_exhausted_prepare_the_whole_lineage_once(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ("i1", "i2", "i3", "i4", "i5")
    for name in names:
        pl.DataFrame({"id": [1, 2, 3], name: [1, 2, 3]}).write_csv(project / f"{name}.csv")
    graph = _graph(
        project,
        [
            *[(name, NodeType.DATA_INPUT, _csv(project / f"{name}.csv")) for name in names],
            ("Y", NodeType.POLARS, _code("df = pl.concat([i1, i2, i3, i4, i5], how='diagonal')")),
        ],
        [(name, "Y") for name in names],
    )
    prepared = _record_preparation(monkeypatch, project)
    # Seeds keep moving: every resolution executes an input the last did not.
    executed = iter([{"i1"}, {"i2"}, {"i3"}, {"i4"}, {"i1", "i5"}])
    closed: list[frozenset[str]] = []

    class _Plan:
        def __init__(self, ids: set[str]) -> None:
            self.decision = type("Decision", (), {"executed_node_ids": frozenset(ids)})()

        def close(self) -> None:
            closed.append(self.decision.executed_node_ids)

    monkeypatch.setattr(
        seed_plans,
        "open_resolved_seed_plan",
        lambda request, *, store, staging_token: _Plan(next(executed)),
    )

    plan = open_seed_plan(_preview(graph, "Y"), store=store)

    # Three rounds of what each resolution read, then the rest of the lineage.
    assert prepared == [["i1"], ["i2"], ["i3"], ["i4", "i5"]]
    assert plan.decision.executed_node_ids == frozenset({"i1", "i5"})
    assert len(closed) == 4


def _listed(store: NodeSnapshotStore, graph: PipelineGraph, node_id: str, gen: str) -> ListedSeed:
    return ListedSeed(node_id, _identity(store, graph, node_id).digest, gen)


def test_listed_plan_leases_exactly_the_listed_generations(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _joined(project)
    j1 = _publish(store, graph, "J")
    _publish(store, graph, "G")  # current and covering, but not listed
    identity = _identity(store, graph, "J")

    with open_listed_seed_plan(
        _preview(graph, "Y"), [_listed(store, graph, "J", j1)], store=store
    ) as plan:
        assert {node: seed.generation_id for node, seed in plan.decision.seeds.items()} == {"J": j1}
        assert plan.decision.captures == {}
        # A refresh after the preview leaves its listed generation readable.
        _publish(store, graph, "J", refresh=True)
        assert plan.seed_frame("J").collect().height == 3
    assert not _generation_dir(store, identity, j1).exists()


def test_empty_listed_plan_seeds_nothing(project: Path, store: NodeSnapshotStore) -> None:
    graph = _joined(project)
    _publish(store, graph, "J")

    with open_listed_seed_plan(_preview(graph, "Y"), [], store=store) as plan:
        assert plan.decision.seeds == {}
        assert plan.decision.captures == {}


def test_listed_plan_expires_on_retired_generation_or_signature_change(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.errors import SeedPlanExpiredError

    graph = _joined(project)
    j1 = _publish(store, graph, "J")
    listed = _listed(store, graph, "J", j1)
    _publish(store, graph, "J", refresh=True)  # retires the unleased J1

    with pytest.raises(SeedPlanExpiredError) as retired:
        open_listed_seed_plan(_preview(graph, "Y"), [listed], store=store)
    assert retired.value.node_id == "J"
    assert retired.value.error_code == "preview_seed_plan_expired"

    j2 = store.latest_generation(_identity(store, graph, "J"))
    assert j2 is not None
    current = _listed(store, graph, "J", j2.generation_id)
    edited = graph.model_copy(
        update={
            "nodes": [
                _node("A", NodeType.POLARS, _code("df = src.with_columns(pl.col('a').alias('a2'))"))
                if node.id == "A"
                else node
                for node in graph.nodes
            ]
        }
    )
    with pytest.raises(SeedPlanExpiredError):
        open_listed_seed_plan(_preview(edited, "Y"), [current], store=store)
    # A point that left the target's lineage has expired too.
    with pytest.raises(SeedPlanExpiredError):
        open_listed_seed_plan(_preview(graph, "A"), [current], store=store)


def test_listed_plan_propagates_corruption(project: Path, store: NodeSnapshotStore) -> None:
    from haute._source_cache import SourceCacheCorruptError

    graph = _joined(project)
    j1 = _publish(store, graph, "J")
    _corrupt(_generation_dir(store, _identity(store, graph, "J"), j1))

    with pytest.raises(SourceCacheCorruptError):
        open_listed_seed_plan(
            _preview(graph, "Y"),
            [_listed(store, graph, "J", j1)],
            store=NodeSnapshotStore(project),
        )


def test_listed_plan_skips_a_generation_that_does_not_cover_the_demand(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _joined(project)
    narrow = _publish(store, graph, "J", ["id", "a"])
    identity = _identity(store, graph, "J")

    with open_listed_seed_plan(
        _preview(graph, "Y"), [_listed(store, graph, "J", narrow)], store=store
    ) as plan:
        assert plan.decision.seeds == {}
        assert {"src", "other", "J"} <= plan.decision.executed_node_ids
        # Still leased: the listed generation outlives a clear while the plan is open.
        store.clear(identity)
        assert _generation_dir(store, identity, narrow).is_dir()


def test_listed_plan_drops_a_seed_built_from_a_recomputed_point(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _diamond(project)
    narrow_a = _publish(store, graph, "A", ["id"])
    a_digest = _identity(store, graph, "A").digest
    b1 = _publish(store, graph, "B", dependencies={a_digest: narrow_a})

    with open_listed_seed_plan(
        _preview(graph, "D"),
        [_listed(store, graph, "A", narrow_a), _listed(store, graph, "B", b1)],
        store=store,
    ) as plan:
        # ``A`` does not cover what ``C`` reads, so the trace recomputes it, and
        # ``B1`` was built from the ``A`` the trace no longer reads.
        assert plan.decision.seeds == {}
        assert {"A", "B", "C"} <= plan.decision.executed_node_ids


def test_preview_planner_reads_instance_nodes_through_their_originals(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._seed_plans import preview_input_node_ids

    graph = _csv_joined(project)
    graph = graph.model_copy(
        update={
            "nodes": [
                *graph.nodes,
                _node("G", NodeType.POLARS, _code("df = J.group_by('a').agg(pl.col('d').sum())")),
                _node("GI", NodeType.POLARS, {"instanceOf": "G"}),
                _node("pi", NodeType.DATA_INPUT, {"instanceOf": "p"}),
                _node("K", NodeType.POLARS, _code("df = pi.join(GI, on='a', how='left')")),
            ],
            "edges": [
                *graph.edges,
                GraphEdge(id="g1", source="J", target="G"),
                GraphEdge(id="g2", source="J", target="GI"),
                GraphEdge(id="g3", source="pi", target="K"),
                GraphEdge(id="g4", source="GI", target="K"),
            ],
        }
    )
    prepared = _record_preparation(monkeypatch, project)

    # The instance runs its original's group-by, so it is a capture point.
    decision = resolve_seed_plan(_preview(graph, "K"), store=store)
    assert decision.captures["GI"].kind is CaptureKind.MATERIALISING
    # And the Data Input instance reads its original's CSV snapshot.
    listed = preview_input_node_ids(graph, "K", source="live", store=store)
    assert sorted(listed) == ["c", "p", "pi"]
    open_seed_plan(_preview(graph, "K"), store=store).close()
    assert [sorted(order) for order in prepared] == [["c", "p", "pi"]]


# ---------------------------------------------------------------------------
# Cost-gated captures
# ---------------------------------------------------------------------------


def test_cheap_segment_skips_fan_out_and_consumed_captures(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    # generation → select → fan-out to two Polars children: no capture, cheap_segment
    fan_out = _graph(
        project,
        [
            (
                "gen",
                NodeType.POLARS,
                _code("df = pl.DataFrame({'id': [1, 2, 3], 'a': [1, 2, 3], 'b': [4, 5, 6]})"),
            ),
            ("S", NodeType.POLARS, _code("df = gen.select('id', 'a')")),
            ("C1", NodeType.POLARS, _code("df = S.select('id')")),
            ("C2", NodeType.POLARS, _code("df = S.select('a')")),
            ("T", NodeType.POLARS, _code("df = C1.join(C2, on='id')")),
        ],
        [("gen", "S"), ("S", "C1"), ("S", "C2"), ("C1", "T"), ("C2", "T")],
    )
    _publish(store, fan_out, "gen")
    decision = resolve_seed_plan(_request(fan_out, target="T"), store=store)
    assert "gen" in decision.seeds
    assert "S" not in decision.captures
    assert decision.skipped_captures["S"] == "cheap_segment"

    # the same node consumed by a modelling target: no capture
    consumed = _graph(
        project,
        [
            (
                "gen",
                NodeType.POLARS,
                _code("df = pl.DataFrame({'id': [1, 2, 3], 'a': [1, 2, 3], 'b': [4, 5, 6]})"),
            ),
            ("S", NodeType.POLARS, _code("df = gen.select('id', 'a')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("gen", "S"), ("S", "T")],
    )
    d_consumed = resolve_seed_plan(_request(consumed, target="T"), store=store)
    assert "gen" in d_consumed.seeds
    assert "S" not in d_consumed.captures
    assert d_consumed.skipped_captures == {"S": "cheap_segment"}


def test_slice_transparent_feeder_is_not_captured(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    # generation → select → edge join: the select is skipped with slice_transparent_feeder;
    # the join is captured
    graph = _graph(
        project,
        [
            ("src1", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("src2", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("G", NodeType.POLARS, _code("df = src1.sort('id')")),
            ("S", NodeType.POLARS, _code("df = G.select('id', 'a')")),
            ("J", NodeType.EDGE_JOIN, {"how": "left", "on": ["id"]}),
        ],
        [
            ("src1", "G"),
            ("G", "S"),
            GraphEdge(id="e1", source="S", target="J", targetHandle="base"),
            GraphEdge(id="e2", source="src2", target="J", targetHandle="join"),
        ],
    )
    _publish(store, graph, "G")
    decision = resolve_seed_plan(_request(graph, target="J"), store=store)
    assert "G" in decision.seeds
    assert "src2" in decision.executed_node_ids
    assert {node: c.kind for node, c in decision.captures.items()} == {"J": CaptureKind.STRUCTURAL}
    assert decision.skipped_captures == {"S": "slice_transparent_feeder"}

    # the same with a select(pl.first("x")) feeder: captured STRUCTURAL
    # (a reduction is not transparent)
    reduction_graph = _graph(
        project,
        [
            ("src1", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("src2", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("S", NodeType.POLARS, _code("df = src1.select('id', pl.first('a'))")),
            ("J", NodeType.EDGE_JOIN, {"how": "left", "on": ["id"]}),
        ],
        [
            ("src1", "S"),
            GraphEdge(id="e1", source="S", target="J", targetHandle="base"),
            GraphEdge(id="e2", source="src2", target="J", targetHandle="join"),
        ],
    )
    d_red = resolve_seed_plan(_request(reduction_graph, target="J"), store=store)
    assert "src1" in d_red.executed_node_ids
    assert "src2" in d_red.executed_node_ids
    assert {node: c.kind for node, c in d_red.captures.items()} == {
        "S": CaptureKind.STRUCTURAL,
        "J": CaptureKind.STRUCTURAL,
    }
    assert d_red.skipped_captures == {}


def test_filter_feeder_is_captured_but_filter_fan_out_is_not(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    # Parquet → filter feeding a join: STRUCTURAL
    filter_join = _graph(
        project,
        [
            ("src1", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("src2", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("F", NodeType.POLARS, _code("df = src1.filter(pl.col('a') > 0)")),
            ("J", NodeType.EDGE_JOIN, {"how": "left", "on": ["id"]}),
        ],
        [
            ("src1", "F"),
            GraphEdge(id="e1", source="F", target="J", targetHandle="base"),
            GraphEdge(id="e2", source="src2", target="J", targetHandle="join"),
        ],
    )
    d1 = resolve_seed_plan(_request(filter_join, target="J"), store=store)
    assert "src1" in d1.executed_node_ids
    assert "src2" in d1.executed_node_ids
    assert d1.captures["F"].kind is CaptureKind.STRUCTURAL
    assert d1.captures["J"].kind is CaptureKind.STRUCTURAL

    # Parquet → drop_nulls feeding a join: STRUCTURAL
    drop_nulls_join = _graph(
        project,
        [
            ("src1", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("src2", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("D", NodeType.POLARS, _code("df = src1.drop_nulls('a')")),
            ("J", NodeType.EDGE_JOIN, {"how": "left", "on": ["id"]}),
        ],
        [
            ("src1", "D"),
            GraphEdge(id="e1", source="D", target="J", targetHandle="base"),
            GraphEdge(id="e2", source="src2", target="J", targetHandle="join"),
        ],
    )
    d2 = resolve_seed_plan(_request(drop_nulls_join, target="J"), store=store)
    assert "src1" in d2.executed_node_ids
    assert "src2" in d2.executed_node_ids
    assert d2.captures["D"].kind is CaptureKind.STRUCTURAL
    assert d2.captures["J"].kind is CaptureKind.STRUCTURAL

    # the same filter fanning out: skipped cheap_segment
    filter_fan = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("F", NodeType.POLARS, _code("df = src.filter(pl.col('a') > 0)")),
            ("C1", NodeType.POLARS, _code("df = F.select('id')")),
            ("C2", NodeType.POLARS, _code("df = F.select('a')")),
            ("T", NodeType.POLARS, _code("df = C1.join(C2, on='id')")),
        ],
        [("src", "F"), ("F", "C1"), ("F", "C2"), ("C1", "T"), ("C2", "T")],
    )
    d3 = resolve_seed_plan(_request(filter_fan, target="T"), store=store)
    assert "src" in d3.executed_node_ids
    assert "F" not in d3.captures
    assert d3.skipped_captures["F"] == "cheap_segment"

    # the same filter consumed: skipped cheap_segment
    filter_consumed = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("F", NodeType.POLARS, _code("df = src.filter(pl.col('a') > 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "F"), ("F", "T")],
    )
    d4 = resolve_seed_plan(_request(filter_consumed, target="T"), store=store)
    assert "src" in d4.executed_node_ids
    assert "F" not in d4.captures
    assert d4.skipped_captures["F"] == "cheap_segment"


def test_costly_segment_keeps_structural_and_consumed_captures(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    # Rating Step (not captured itself), then a select consumed by training:
    # the select is captured CONSUMED
    rating_graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("R", NodeType.RATING_STEP, {}),
            ("S", NodeType.POLARS, _code("df = R.select('id', 'a')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "R"), ("R", "S"), ("S", "T")],
    )
    d1 = resolve_seed_plan(_request(rating_graph, target="T"), store=store)
    assert "R" not in d1.captures
    assert {node: c.kind for node, c in d1.captures.items()} == {"S": CaptureKind.CONSUMED}
    assert d1.skipped_captures == {}

    # flat-file API Input → select → fan-out: STRUCTURAL
    # (a source is never captured, its segment is costly)
    api_graph = _graph(
        project,
        [
            ("api", NodeType.API_INPUT, {"path": str(project / "quotes.csv")}),
            ("S", NodeType.POLARS, _code("df = api.select('id', 'a')")),
            ("C1", NodeType.POLARS, _code("df = S.select('id')")),
            ("C2", NodeType.POLARS, _code("df = S.select('a')")),
            ("T", NodeType.POLARS, _code("df = C1.join(C2, on='id')")),
        ],
        [("api", "S"), ("S", "C1"), ("S", "C2"), ("C1", "T"), ("C2", "T")],
    )
    d2 = resolve_seed_plan(_request(api_graph, target="T"), store=store)
    assert "api" not in d2.captures
    assert d2.captures["S"].kind is CaptureKind.STRUCTURAL

    # External File with helper(df) → select → fan-out: the External File is MATERIALISING and
    # the select is skipped cheap_segment (its segment stops at that capture)
    ext_graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("ext", NodeType.EXTERNAL_FILE, _code("df = helper(src)")),
            ("S", NodeType.POLARS, _code("df = ext.select('id', 'a')")),
            ("C1", NodeType.POLARS, _code("df = S.select('id')")),
            ("C2", NodeType.POLARS, _code("df = S.select('a')")),
            ("T", NodeType.POLARS, _code("df = C1.join(C2, on='id')")),
        ],
        [("src", "ext"), ("ext", "S"), ("S", "C1"), ("S", "C2"), ("C1", "T"), ("C2", "T")],
    )
    d3 = resolve_seed_plan(_request(ext_graph, target="T"), store=store)
    assert d3.captures["ext"].kind is CaptureKind.MATERIALISING
    assert "S" not in d3.captures
    assert d3.skipped_captures["S"] == "cheap_segment"


def test_segment_stops_at_a_fresh_capture_and_at_a_seed(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    # a Data Input with group_by post-load code → select → fan-out:
    # the Data Input is MATERIALISING and the select is skipped cheap_segment
    # (its segment stops at the fresh capture)
    data_input_graph = _graph(
        project,
        [
            (
                "src",
                NodeType.DATA_INPUT,
                {
                    **_parquet(project / "quotes.parquet"),
                    "code": "df = df.group_by('id').agg(pl.col('a').sum())",
                },
            ),
            ("S", NodeType.POLARS, _code("df = src.select('id', 'a')")),
            ("C1", NodeType.POLARS, _code("df = S.select('id')")),
            ("C2", NodeType.POLARS, _code("df = S.select('a')")),
            ("T", NodeType.POLARS, _code("df = C1.join(C2, on='id')")),
        ],
        [("src", "S"), ("S", "C1"), ("S", "C2"), ("C1", "T"), ("C2", "T")],
    )
    d1 = resolve_seed_plan(_request(data_input_graph, target="T"), store=store)
    assert d1.captures["src"].kind is CaptureKind.MATERIALISING
    assert "S" not in d1.captures
    assert d1.skipped_captures["S"] == "cheap_segment"

    # the same graph with the Data Input's generation present: the Data Input is seeded and
    # the select is skipped the same way
    _publish(store, data_input_graph, "src")
    d2 = resolve_seed_plan(_request(data_input_graph, target="T"), store=store)
    assert "src" in d2.seeds
    assert "src" not in d2.captures
    assert "S" not in d2.captures
    assert d2.skipped_captures["S"] == "cheap_segment"

    # a Rating Step below a seed followed by a select fan-out: the select is STRUCTURAL
    seeded_rating_graph = _graph(
        project,
        [
            (
                "src",
                NodeType.POLARS,
                _code("df = pl.DataFrame({'id': [1, 2, 3], 'a': [1, 2, 3], 'b': [4, 5, 6]})"),
            ),
            ("R", NodeType.RATING_STEP, {}),
            ("S", NodeType.POLARS, _code("df = R.select('id', 'a')")),
            ("C1", NodeType.POLARS, _code("df = S.select('id')")),
            ("C2", NodeType.POLARS, _code("df = S.select('a')")),
            ("T", NodeType.POLARS, _code("df = C1.join(C2, on='id')")),
        ],
        [("src", "R"), ("R", "S"), ("S", "C1"), ("S", "C2"), ("C1", "T"), ("C2", "T")],
    )
    _publish(store, seeded_rating_graph, "src")
    d3 = resolve_seed_plan(_request(seeded_rating_graph, target="T"), store=store)
    assert "src" in d3.seeds
    assert "R" not in d3.captures
    assert d3.captures["S"].kind is CaptureKind.STRUCTURAL


def test_two_ports_of_one_api_input_joined_are_a_structural_capture(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="request",
                data=NodeData(label="request", nodeType=NodeType.API_INPUT, config={}),
            ),
            GraphNode(
                id="join",
                data=NodeData(
                    label="join",
                    nodeType=NodeType.EDGE_JOIN,
                    config={"how": "left", "on": ["id"]},
                ),
            ),
        ],
        edges=[
            GraphEdge(
                id="e_quotes_join",
                source="request",
                sourceHandle="quotes",
                target="join",
                targetHandle="base",
            ),
            GraphEdge(
                id="e_lookup_join",
                source="request",
                sourceHandle="lookup",
                target="join",
                targetHandle="join",
            ),
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )
    decision = resolve_seed_plan(_request(graph, target="join"), store=store)
    assert {node: capture.kind for node, capture in decision.captures.items()} == {
        "join": CaptureKind.STRUCTURAL
    }
    assert decision.skipped_captures == {}


def test_costly_code_nodes_are_captured_and_cheap_boundaries_are_not(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    costly_cases = [
        ("group_by", "df = src.group_by('id').agg(pl.col('a').sum())"),
        ("over", "df = src.with_columns(pl.col('a').sum().over('id'))"),
        ("pivot", "df = src.pivot(on='a', index='id', values='b')"),
        ("unique", "df = src.unique('id')"),
        ("join", "df = src.join(other, on='id')"),
        (
            "map_elements",
            "df = src.select(pl.col('a').map_elements(lambda x: x + 1, return_dtype=pl.Int64))",
        ),
    ]
    for _label, code in costly_cases:
        graph = _graph(
            project,
            [
                ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
                ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
                ("C", NodeType.POLARS, _code(code)),
            ],
            [("src", "C"), ("other", "C")],
        )
        decision = resolve_seed_plan(_request(graph, target="C"), store=store)
        assert decision.captures["C"].kind is CaptureKind.MATERIALISING

    cheap_cases = [
        ("explode", "df = src.explode('a')"),
        ("shift", "df = src.with_columns(pl.col('a').shift(1))"),
    ]
    for _label, code in cheap_cases:
        graph = _graph(
            project,
            [
                ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
                ("C", NodeType.POLARS, _code(code)),
            ],
            [("src", "C")],
        )
        bounded = resolve_seed_plan(_request(graph, target="C"), store=store)
        assert "C" not in bounded.captures
        preview = resolve_seed_plan(_preview(graph, "C"), store=store)
        assert "C" not in preview.captures

    edge_join_graph = _graph(
        project,
        [
            ("src1", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("src2", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("J", NodeType.EDGE_JOIN, {"how": "left", "on": ["id"]}),
        ],
        [
            GraphEdge(id="e1", source="src1", target="J", targetHandle="base"),
            GraphEdge(id="e2", source="src2", target="J", targetHandle="join"),
        ],
    )
    bounded_j = resolve_seed_plan(_request(edge_join_graph, target="J"), store=store)
    assert bounded_j.captures["J"].kind is CaptureKind.STRUCTURAL
    preview_j = resolve_seed_plan(_preview(edge_join_graph, "J"), store=store)
    assert preview_j.captures["J"].kind is CaptureKind.STRUCTURAL


def test_preview_captures_only_full_input_work(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    full_input_cases = [
        ("sort", "df = src.sort('a')"),
        ("expr_sort", "df = src.with_columns(pl.col('a').sort())"),
        ("expr_unique", "df = src.with_columns(pl.col('a').unique())"),
        ("expr_rank", "df = src.with_columns(pl.col('a').rank())"),
        ("group_by", "df = src.group_by('id').agg(pl.col('a').sum())"),
        ("pivot", "df = src.pivot(on='a', index='id', values='b')"),
        ("over", "df = src.with_columns(pl.col('a').sum().over('id'))"),
    ]
    for _label, code in full_input_cases:
        graph = _graph(
            project,
            [
                ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
                ("C", NodeType.POLARS, _code(code)),
            ],
            [("src", "C")],
        )
        prev = resolve_seed_plan(_preview(graph, "C"), store=store)
        assert prev.captures["C"].kind is CaptureKind.MATERIALISING
        assert prev.skipped_captures == {}

        bounded = resolve_seed_plan(_request(graph, target="C"), store=store)
        assert bounded.captures["C"].kind is CaptureKind.MATERIALISING

    bounded_only_cases = [
        (
            "map_elements",
            "df = src.select(pl.col('a').map_elements(lambda x: x + 1, return_dtype=pl.Int64))",
        ),
        ("pipe", "df = src.pipe(lambda d: d)"),
        ("unresolved", "df = helper(src)"),
    ]
    for _label, code in bounded_only_cases:
        graph = _graph(
            project,
            [
                ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
                ("C", NodeType.POLARS, _code(code)),
            ],
            [("src", "C")],
        )
        prev = resolve_seed_plan(_preview(graph, "C"), store=store)
        assert "C" not in prev.captures
        assert prev.skipped_captures == {}

        bounded = resolve_seed_plan(_request(graph, target="C"), store=store)
        assert bounded.captures["C"].kind is CaptureKind.MATERIALISING


def test_capture_set_is_settled_before_execution_and_claims(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_builder_execution(monkeypatch)
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.select('id', 'a')")),
            ("C1", NodeType.POLARS, _code("df = A.sort('id')")),
            ("C2", NodeType.POLARS, _code("df = A.sort('a')")),
            ("T", NodeType.POLARS, _code("df = C1.join(C2, on='id')")),
        ],
        [("src", "A"), ("A", "C1"), ("A", "C2"), ("C1", "T"), ("C2", "T")],
    )
    request = _request(graph)
    decision = resolve_seed_plan(request, store=store)
    assert decision.skipped_captures == {"A": "cheap_segment"}
    with open_resolved_seed_plan(request, store=store) as plan:
        assert plan.decision.captures == decision.captures
        assert plan.decision.skipped_captures == {"A": "cheap_segment"}
        handoff = plan.handoff()
        assert handoff.decision.captures == decision.captures
        assert handoff.decision.skipped_captures == {"A": "cheap_segment"}
        round_tripped = pickle.loads(pickle.dumps(handoff))
        assert round_tripped.decision.captures == decision.captures
        assert round_tripped.decision.skipped_captures == {"A": "cheap_segment"}
        child = SeedPlan.adopt(round_tripped, store=store)
        try:
            assert child.decision.captures == decision.captures
            assert child.decision.skipped_captures == {"A": "cheap_segment"}
        finally:
            child.close()


@pytest.mark.parametrize(
    ("between", "captured"),
    [
        ("df = M.with_columns(pl.lit(1).alias('one'))", True),
        ("df = M.filter(pl.col('a') > 0)", True),
        ("df = M.head(10)", False),
        ("df = M.slice(0, 10)", False),
        ("df = M[:10]", False),
        ("df = my_helper(M)", False),
        # Reads every row, but a bounding call anywhere is answered
        # conservatively: the scorer keeps the row-local scan it had before.
        ("df = M.sort('a').head(10)", False),
    ],
)
def test_a_row_bounding_step_between_a_model_score_and_its_capture_drains_nothing(
    project: Path, store: NodeSnapshotStore, between: str, captured: bool
) -> None:
    """Only a path that reads every row drains a scorer: a bound is pushed into its scan.

    ``head`` (or code whose calls cannot be proven) below the scorer lets Polars
    score only the rows it keeps, so capturing the scorer would score them all.
    """
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("M", NodeType.MODEL_SCORE, {}),
            ("B", NodeType.POLARS, _code(between)),
            ("S", NodeType.POLARS, _code("df = B.sort('a')")),
            ("Y", NodeType.POLARS, _code("df = S.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "M"), ("M", "B"), ("B", "S"), ("S", "Y")],
    )
    kinds = _kinds(resolve_seed_plan(_preview(graph, "Y", source="batch"), store=store))
    assert kinds.get("S") is CaptureKind.MATERIALISING
    assert (kinds.get("M") is CaptureKind.MODEL_SCORE) is captured

    # The same bound inside the capturing node's own code drains nothing either.
    bounded_capture = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("M", NodeType.MODEL_SCORE, {}),
            ("S", NodeType.POLARS, _code("df = M.head(10).sort('a')")),
            ("Y", NodeType.POLARS, _code("df = S.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "M"), ("M", "S"), ("S", "Y")],
    )
    kinds = _kinds(resolve_seed_plan(_preview(bounded_capture, "Y", source="batch"), store=store))
    assert kinds == {"S": CaptureKind.MATERIALISING}


def test_a_captured_scorer_below_drains_the_scorer_above_despite_its_post_code_bound(
    project: Path, store: NodeSnapshotStore
) -> None:
    """A captured Model Score scores its whole input before its post-code runs.

    ``M2``'s ``head`` bounds only its own output, so ``M1`` is drained through it:
    left row-local, ``M1`` would be pulled whole into ``M2``'s batched scoring.
    """
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("M1", NodeType.MODEL_SCORE, {}),
            ("M2", NodeType.MODEL_SCORE, {"code": "df = df.head(10)"}),
            ("S", NodeType.POLARS, _code("df = M2.sort('a')")),
            ("Y", NodeType.POLARS, _code("df = S.with_columns(pl.lit(1).alias('one'))")),
        ],
        [("src", "M1"), ("M1", "M2"), ("M2", "S"), ("S", "Y")],
    )
    assert _kinds(resolve_seed_plan(_preview(graph, "Y", source="batch"), store=store)) == {
        "S": CaptureKind.MATERIALISING,
        "M2": CaptureKind.MODEL_SCORE,
        "M1": CaptureKind.MODEL_SCORE,
    }
