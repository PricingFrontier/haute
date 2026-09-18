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
from haute._seed_plans import (
    CaptureKind,
    SeedDecision,
    SeedPlan,
    SeedPlanRequest,
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


def _chain(project: Path) -> PipelineGraph:
    """``src → A → B → C → T`` with column demands the planner knows exactly."""
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("B", NodeType.POLARS, _code("df = A.filter(pl.col('a') > 0)")),
            ("C", NodeType.POLARS, _code("df = B.with_columns((pl.col('c') + 1).alias('c2'))")),
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
    pl.DataFrame({name: [1, 2, 3] for name in names}).write_parquet(artifact.data_path)
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
    captures = resolve_seed_plan(_request(join, required={"T": ["a", "d"]}), store=store).captures
    assert {node: capture.kind for node, capture in captures.items()} == {
        "A": CaptureKind.STRUCTURAL,
        "J": CaptureKind.MATERIALISING,
        "Y": CaptureKind.CONSUMED,
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
    captures = resolve_seed_plan(_request(fan_out), store=store).captures
    assert {node: capture.kind for node, capture in captures.items()} == {
        "P": CaptureKind.STRUCTURAL,
        "Q1": CaptureKind.STRUCTURAL,
        "Q2": CaptureKind.STRUCTURAL,
        "K": CaptureKind.CONSUMED,
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
    captures = resolve_seed_plan(_request(graph), store=store).captures
    assert {node: capture.kind for node, capture in captures.items()} == {
        "G": CaptureKind.MATERIALISING,
        "Y": CaptureKind.CONSUMED,
    }


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
    assert {node: capture.kind for node, capture in decision.captures.items()} == {
        "C": CaptureKind.CONSUMED
    }
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
    captures = resolve_seed_plan(_request(output, target="O"), store=store).captures
    assert {node: capture.kind for node, capture in captures.items()} == {
        "A": CaptureKind.STRUCTURAL,
        "J": CaptureKind.CONSUMED,
    }

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
    assert {node: capture.kind for node, capture in decision.captures.items()} == {
        "A": CaptureKind.CONSUMED
    }
    assert decision.executed_node_ids == {"src", "A", "T"}


def test_optimiser_second_input_selected_producer(project: Path, store: NodeSnapshotStore) -> None:
    graph = _two_input_optimiser(project, "B")
    cold = resolve_seed_plan(_request(graph, target="OPT"), store=store)
    assert {node: capture.kind for node, capture in cold.captures.items()} == {
        "B": CaptureKind.CONSUMED
    }
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
    graph = _chain(project)
    _publish(store, graph, "C", ["b"])

    decision = resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=store)

    assert decision.seeds == {}
    assert decision.captures["C"].columns == NodeSnapshotColumns.of({"a", "b"})
    assert decision.captures["C"].strict_columns == NodeSnapshotColumns.of({"a"})


@pytest.mark.parametrize(
    "profile",
    [ExecutionProfile.DEPLOY_LIVE, ExecutionProfile.DEPLOY_BATCH, ExecutionProfile.PREVIEW_EAGER],
)
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
    assert set(decision.captures) == {"A", "B", "C", "D"}


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
            ("G", NodeType.POLARS, _code("df = A.filter(pl.col('a') > 0)")),
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
            ("G", NodeType.POLARS, _code("df = A.filter(pl.col('a') > 0)")),
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


def test_corrupt_generation_fails_resolution(project: Path, store: NodeSnapshotStore) -> None:
    from haute._source_cache import SourceCacheCorruptError

    graph = _chain(project)
    generation = _publish(store, graph, "C")
    identity = _identity(store, graph, "C")
    _corrupt(_generation_dir(store, identity, generation) / "data.parquet")

    with pytest.raises(SourceCacheCorruptError):
        resolve_seed_plan(_request(graph, required={"T": ["a"]}), store=NodeSnapshotStore(project))


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
        _request(_chain(project), required={"T": ["a"]}, capture_columns_by_node={"C": ["b"]}),
        store=store,
    )
    assert decision.captures["C"].columns == NodeSnapshotColumns.of({"a", "b"})
    assert decision.captures["C"].strict_columns == NodeSnapshotColumns.of({"a"})


def test_unresolved_all_except_demand_captures_all_columns(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute.projection import AllExcept

    graph = _chain(project)
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
    assert set(decision.captures) == {"A", "B"}


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
    pl.DataFrame({"a": [1]}).write_parquet(artifact.data_path)
    plan.register_artifact(artifact)
    published = store.stage_node_output(identity)
    pl.DataFrame({"a": [1]}).write_parquet(published.data_path)
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
