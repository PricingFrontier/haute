"""Planned lazy executions seed from and capture into shared snapshots (CACHE-S07)."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import haute._execute_lazy as execute_lazy_module
import haute._source_cache as source_cache_module
from haute._data_points import DataPointResolver
from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._execution_schemas import ExecutionMetricsPayload
from haute._hashing import content_hash
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._seed_plans import SeedPlan, SeedPlanRequest, open_seed_plan
from haute._source_cache import SourceCacheIdentity
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import (
    ContractMismatchError,
    GroupByExecutionUnsupportedError,
    SnapshotPlanInputsChangedError,
)
from haute.execution import execute_lazy_graph

ALL = NodeSnapshotColumns.all()
_ROWS = 200


def test_recipe_builders_refuse_non_frame_inputs() -> None:
    def joined(*args: Any) -> None:
        raise AssertionError("recipe inspection must not execute the builder")

    joined.edge_join_roles = (0, 1)
    joined.edge_join_config = {"how": "left", "on": "id"}
    node = _node("join", NodeType.EDGE_JOIN, {})
    assert (
        execute_lazy_module._edge_join_recipe(joined, node, [pl.DataFrame({"id": [1]}), object()])
        is None
    )
    assert (
        execute_lazy_module._write_recipe(
            joined, _node("transform", NodeType.POLARS, {"code": "df = df"}), [object()]
        )
        is None
    )


def test_single_frame_api_builder_preserves_legacy_null_handle_input() -> None:
    graph = PipelineGraph(
        nodes=[_node("api", NodeType.API_INPUT, {}), _node("T", NodeType.POLARS, {})],
        edges=[GraphEdge(id="edge", source="api", target="T")],
    )

    def build(node: GraphNode, **kwargs: Any) -> Any:
        if node.id == "api":
            return node.id, lambda: pl.LazyFrame({"x": [1, 2]}), True
        return node.id, lambda frame: frame.with_columns(y=pl.col("x") + 1), False

    outputs, *_ = execute_lazy_graph(graph, build, enforce_contracts=False)
    assert outputs["T"].collect().to_dict(as_series=False) == {"x": [1, 2], "y": [2, 3]}


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _parquet(path: Path, code: str = "") -> dict[str, Any]:
    config: dict[str, Any] = {"inputType": "file", "format": "parquet", "mode": "scan"}
    config["path"] = str(path)
    if code:
        config["code"] = code
    return config


def _code(code: str) -> dict[str, Any]:
    return {"code": code}


def _graph(
    project: Path,
    nodes: list[tuple[str, NodeType, dict[str, Any]]],
    edges: list[tuple[str, str] | GraphEdge],
) -> PipelineGraph:
    return PipelineGraph(
        nodes=[_node(*spec) for spec in nodes],
        edges=[
            edge
            if isinstance(edge, GraphEdge)
            else GraphEdge(id=f"e{i}", source=edge[0], target=edge[1])
            for i, edge in enumerate(edges)
        ],
        # ``R`` is one random scalar per compiled preamble: a node reading it is
        # a node whose output differs between two computations. ``widen`` is a
        # helper the materialisation estimator cannot see through.
        preamble=(
            "import polars as pl\nimport random\nR = random.random()\n"
            "def widen(frame):\n    return frame.with_columns(pl.lit(1).alias('h'))\n"
        ),
        source_file=str(project / "main.py"),
    )


def _write_sources(project: Path) -> None:
    pl.DataFrame(
        {
            "id": list(range(_ROWS)),
            "a": list(range(_ROWS)),
            "b": [value * 2 for value in range(_ROWS)],
            "c": [value * 3 for value in range(_ROWS)],
        }
    ).write_parquet(project / "quotes.parquet")
    pl.DataFrame(
        {"id": list(range(_ROWS)), "d": [value / 10 for value in range(_ROWS)]}
    ).write_parquet(project / "claims.parquet")


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    _write_sources(haute_scratch)
    return haute_scratch


@pytest.fixture()
def store(project: Path) -> NodeSnapshotStore:
    return NodeSnapshotStore(project)


def _join_graph(
    project: Path,
    *,
    a_code: str = "df = src.with_columns((pl.col('a') * 2).alias('a2'))",
    b_code: str = "df = J.filter(pl.col('a') >= 0)",
) -> PipelineGraph:
    """``src → A → J ← other``, ``J → B → T``."""
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code(a_code)),
            ("J", NodeType.POLARS, _code("df = A.join(other, on='id', how='left')")),
            ("B", NodeType.POLARS, _code(b_code)),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "J"), ("other", "J"), ("J", "B"), ("B", "T")],
    )


@dataclass
class RunResult:
    frame: pl.DataFrame
    metrics: dict[str, Any]
    calls: Counter[str] = field(default_factory=Counter)

    @property
    def seeds(self) -> dict[str, str]:
        return {
            seed["node_id"]: seed["generation_id"] for seed in self.metrics["shared_snapshot_seeds"]
        }

    @property
    def captures(self) -> dict[str, dict[str, Any]]:
        return {capture["node_id"]: capture for capture in self.metrics["shared_snapshot_captures"]}


def _context(
    profile: ExecutionProfile, operation: str = "snapshot_seeding_test"
) -> ExecutionContext:
    """An admitted context with a fixed budget, independent of real admission."""
    memory_limit = 1024**3
    return ExecutionContext(
        operation=operation,
        profile=profile,
        memory_limit_bytes=memory_limit,
        memory_baseline_bytes=0,
        rss_limit_bytes=memory_limit,
        admission=ExecutionAdmission(
            operation=operation,
            profile=profile,
            memory_limit_bytes=memory_limit,
            rss_at_admission_bytes=0,
            rss_limit_bytes=memory_limit,
            headroom_bytes=memory_limit,
            config_key="test",
        ),
        memory_sampler=lambda: 0,
    )


def _counting_build(calls: Counter[str]) -> Callable[..., Any]:
    from haute.executor import _build_node_fn

    def build(node: GraphNode, **kwargs: Any) -> Any:
        name, fn, is_source = _build_node_fn(node, **kwargs)

        def counted(*args: Any, **fn_kwargs: Any) -> Any:
            calls[node.id] += 1
            return fn(*args, **fn_kwargs)

        if hasattr(fn, "edge_join_roles"):
            counted.edge_join_roles = fn.edge_join_roles  # type: ignore[attr-defined]
        if hasattr(fn, "edge_join_config"):
            counted.edge_join_config = fn.edge_join_config  # type: ignore[attr-defined]

        return name, counted, is_source

    return build


def _request(
    graph: PipelineGraph,
    target: str,
    *,
    required: dict[str, Any] | None,
    profile: ExecutionProfile,
    source: str = "live",
    **fields: Any,
) -> SeedPlanRequest:
    return SeedPlanRequest(
        graph=graph,
        target_node_id=target,
        source=source,
        profile=profile,
        required_columns_by_node=required,
        **fields,
    )


@contextmanager
def _planned(
    graph: PipelineGraph,
    store: NodeSnapshotStore,
    target: str = "T",
    *,
    required: dict[str, Any] | None = None,
    profile: ExecutionProfile = ExecutionProfile.TRAINING_PREP,
    context: ExecutionContext | None = None,
    source: str = "live",
    **fields: Any,
) -> Iterator[tuple[SeedPlan, ExecutionContext, Callable[[], tuple[pl.LazyFrame, Counter[str]]]]]:
    """Open a plan and yield a callable that executes the graph under it."""
    from haute.executor import _compile_preamble, _pipeline_dir

    context = context if context is not None else _context(profile)
    request = _request(graph, target, required=required, profile=profile, source=source, **fields)
    with open_seed_plan(request, store=store, execution_context=context) as plan:

        def execute() -> tuple[pl.LazyFrame, Counter[str]]:
            calls: Counter[str] = Counter()
            outputs, *_ = execute_lazy_graph(
                graph,
                _counting_build(calls),
                target_node_id=target,
                preamble_ns=_compile_preamble(
                    graph.preamble or "", pipeline_dir=_pipeline_dir(graph)
                )
                or None,
                source=source,
                enforce_contracts=True,
                required_columns_by_node=required,
                execution_context=context,
                prepare_inputs=False,
                snapshot_plan=plan,
            )
            return outputs[target], calls

        yield plan, context, execute


def _run(
    graph: PipelineGraph,
    store: NodeSnapshotStore,
    target: str = "T",
    *,
    required: dict[str, Any] | None = None,
    profile: ExecutionProfile = ExecutionProfile.TRAINING_PREP,
    context: ExecutionContext | None = None,
    source: str = "live",
    **fields: Any,
) -> RunResult:
    with _planned(
        graph,
        store,
        target,
        required=required,
        profile=profile,
        context=context,
        source=source,
        **fields,
    ) as (
        _plan,
        context,
        execute,
    ):
        output, calls = execute()
        frame = output.collect()
    return RunResult(frame, context.metrics_payload(status="completed"), calls)


def _identity(
    store: NodeSnapshotStore, graph: PipelineGraph, node_id: str, source: str = "live"
) -> SourceCacheIdentity:
    resolver = DataPointResolver(graph, source=source, store=store)
    return resolver.node_output_slot(node_id).identity(resolver.node_output_signature(node_id))


def _latest_columns(
    store: NodeSnapshotStore, graph: PipelineGraph, node_id: str
) -> NodeSnapshotColumns:
    latest = store.latest_generation(_identity(store, graph, node_id))
    assert latest is not None
    return latest.columns


def _staging_dirs(store: NodeSnapshotStore) -> list[Path]:
    return [path for path in store.inputs_root.glob("*/.staging-*") if path.is_dir()]


def _pause_at(
    monkeypatch: pytest.MonkeyPatch, node_id: str, during: Callable[[], None]
) -> list[str]:
    """Run *during* once, when a run is about to publish *node_id*'s capture."""
    paused: list[str] = []

    def fault_point(name: str, at_node: str) -> None:
        if name == "snapshot_capture_before_publish" and at_node == node_id and not paused:
            paused.append(at_node)
            during()

    monkeypatch.setattr(execute_lazy_module, "_snapshot_fault_point", fault_point)
    return paused


@contextmanager
def _hash_spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Path]]:
    recorded: list[Path] = []
    real_content_hash = source_cache_module.content_hash

    def recording_content_hash(path: Path) -> str:
        recorded.append(path)
        return real_content_hash(path)

    monkeypatch.setattr(source_cache_module, "content_hash", recording_content_hash)
    try:
        yield recorded
    finally:
        monkeypatch.setattr(source_cache_module, "content_hash", real_content_hash)


# ---------------------------------------------------------------------------
# Seeding and capture
# ---------------------------------------------------------------------------


def test_seeded_rerun_builds_nothing_upstream(project: Path, store: NodeSnapshotStore) -> None:
    graph = _join_graph(project)
    first = _run(graph, store, required={"T": ["a", "d"]})

    assert set(first.captures) == {"J"}
    assert {capture["outcome"] for capture in first.captures.values()} == {"published"}
    assert first.seeds == {}
    assert first.calls["src"] == 1

    second = _run(graph, store, required={"T": ["a", "d"]})

    assert set(second.seeds) == {"J"}
    assert second.captures == {}
    assert second.calls["src"] == 0 and second.calls["other"] == 0
    assert second.calls["B"] == 1
    assert_frame_equal(second.frame, first.frame)


@pytest.mark.parametrize("prewritten", [False, True])
def test_capture_without_metrics_publishes_eager_or_precomputed_data(
    project: Path, store: NodeSnapshotStore, prewritten: bool
) -> None:
    from haute._chunked_writes import part_name

    graph = _join_graph(project)
    expected = pl.DataFrame({"id": [1], "a": [2], "d": [0.1]})
    with _planned(graph, store) as (plan, _, _):
        captures = execute_lazy_module._PlannedCaptures(
            plan, graph, execution_context=None, incoming_edges_by_target={}
        )
        identity = plan.decision.captures["J"].identity
        artifact = None
        if prewritten:
            artifact = store.stage_node_output(identity, staging_token=plan.staging_token)
            expected.write_parquet(artifact.directory / part_name(0))
        result = captures.capture("J", expected, {}, artifact=artifact, prewritten=prewritten)
        assert_frame_equal(result.collect(), expected)
        latest = store.latest_generation(identity)
        assert latest is not None
        assert_frame_equal(latest.lazy_frame.collect(), expected)
        if prewritten:
            part = latest.generation.metadata.parts[0]
            assert part.digest == content_hash(latest.generation.directory / part.name)
    assert _staging_dirs(store) == []


@pytest.mark.parametrize("invalid", ["multiframe", "schema"])
def test_invalid_capture_leaves_no_staging_or_publication(
    project: Path, store: NodeSnapshotStore, invalid: str
) -> None:
    from haute._node_snapshots import NodeSnapshotMultiFrameUnsupportedError

    graph = _join_graph(project)
    with _planned(graph, store) as (plan, context, _):
        captures = execute_lazy_module._PlannedCaptures(
            plan, graph, execution_context=context, incoming_edges_by_target={}
        )
        identity = plan.decision.captures["J"].identity
        if invalid == "multiframe":
            frame = {"one": pl.LazyFrame({"id": [1]})}
            error = NodeSnapshotMultiFrameUnsupportedError
        else:
            frame = pl.LazyFrame({"id": [1]}).select("absent")
            error = pl.exceptions.ColumnNotFoundError
        with pytest.raises(error):
            captures.capture("J", frame, {})
        assert store.latest_generation(identity) is None
        assert _staging_dirs(store) == []


def test_a_bounded_run_publishes_its_capture_without_rehashing_it(
    project: Path,
    store: NodeSnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded run's captured parts publish with write-time digests without rehashing."""
    graph = _join_graph(project)
    with _hash_spy(monkeypatch) as recorded:
        run = _run(graph, store, required={"T": ["a", "d"]})

    assert run.captures["J"]["outcome"] == "published"
    assert recorded == []

    gen = store.latest_generation(_identity(store, graph, "J"))
    assert gen is not None
    assert gen.generation.metadata.parts
    for part in gen.generation.metadata.parts:
        part_path = gen.generation.directory / part.name
        assert part.digest == content_hash(part_path)


def test_disjoint_demand_publishes_one_widened_generation(
    project: Path, store: NodeSnapshotStore
) -> None:
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
    _run(graph, store, required={"T": ["a", "b"]})
    assert _latest_columns(store, graph, "J") == NodeSnapshotColumns.of({"a", "b"})

    widened = _run(graph, store, required={"T": ["c"]})

    assert widened.captures["J"]["outcome"] == "published"
    assert widened.captures["J"]["columns"] == ["a", "b", "c"]
    assert _latest_columns(store, graph, "J") == NodeSnapshotColumns.of({"a", "b", "c"})
    assert widened.frame.columns == ["c"]


def test_narrow_upstream_snapshot_widened_in_same_run(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            (
                "X",
                NodeType.POLARS,
                _code("df = src.with_columns((pl.col('c') + 1).alias('c1')).sort('id')"),
            ),
            ("J", NodeType.POLARS, _code("df = X.join(other, on='id', how='left')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "X"), ("X", "J"), ("other", "J"), ("J", "T")],
    )
    _run(graph, store, required={"T": ["a", "b"]})
    assert _latest_columns(store, graph, "J") == NodeSnapshotColumns.of({"a", "b"})
    # The join's input holds only what a run needing ``c`` reads from it.
    x_identity = _identity(store, graph, "X")
    store.clear(x_identity)
    artifact = store.stage_node_output(x_identity)
    pl.read_parquet(project / "quotes.parquet").select("id", "c").write_parquet(
        artifact.part_path(0)
    )
    store.publish_node_output(
        x_identity,
        artifact,
        columns=NodeSnapshotColumns.of({"id", "c"}),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    ).close()

    run = _run(graph, store, required={"T": ["c"]})

    assert run.seeds == {}
    assert run.calls["src"] == 1 and run.calls["other"] == 1
    assert run.captures["X"]["outcome"] == "published"
    assert _latest_columns(store, graph, "J") == NodeSnapshotColumns.of({"a", "b", "c"})
    assert _latest_columns(store, graph, "X") == ALL


def test_quota_full_sampled_join_computed_once(project: Path) -> None:
    filler = NodeSnapshotStore(project)
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            (
                "S",
                NodeType.POLARS,
                _code("df = src.with_columns(pl.int_range(pl.len()).shuffle().alias('r'))"),
            ),
            ("J", NodeType.POLARS, _code("df = S.join(other, on='id', how='left')")),
            ("T", NodeType.MODELLING, {}),
            ("P1", NodeType.POLARS, _code("df = src.select('a')")),
            ("P2", NodeType.POLARS, _code("df = src.select('b')")),
        ],
        [("src", "S"), ("S", "J"), ("other", "J"), ("J", "T"), ("src", "P1"), ("src", "P2")],
    )
    for pinned in ("P1", "P2"):
        identity = _identity(filler, graph, pinned)
        artifact = filler.stage_node_output(identity)
        pl.DataFrame({"a": [1]}).write_parquet(artifact.part_path(0))
        filler.publish_node_output(
            identity,
            artifact,
            columns=ALL,
            dependencies={},
            explicit=True,
            profile=ExecutionProfile.NODE_SNAPSHOT,
        ).close()
    full = NodeSnapshotStore(project, node_output_max_generations=2)

    with _planned(graph, full, required={"T": ["id", "r", "d"]}) as (_plan, context, execute):
        output, calls = execute()
        first, second = output.collect(), output.collect()
        assert _staging_dirs(full)
    metrics = context.metrics_payload(status="completed")

    assert_frame_equal(first, second)
    assert calls["S"] == 1
    assert {
        capture["node_id"]: capture["outcome"] for capture in metrics["shared_snapshot_captures"]
    } == {
        "S": "quota",
        "J": "quota",
    }
    assert {"code": "snapshot_capture_skipped", "node_id": "J", "reason": "quota"} in metrics[
        "warnings"
    ]
    assert full.latest_generation(_identity(full, graph, "J")) is None
    assert not _staging_dirs(full)


def test_paused_run_diamond_with_a_snapshot(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            (
                "A",
                NodeType.POLARS,
                _code("df = src.with_columns(pl.lit(R).alias('r')).sort('id')"),
            ),
            (
                "B",
                NodeType.POLARS,
                _code("df = A.select('id', pl.col('r').alias('rb')).sort('id')"),
            ),
            ("C", NodeType.POLARS, _code("df = A.select('id', pl.col('r').alias('rc'))")),
            ("D", NodeType.POLARS, _code("df = B.join(C, on='id', how='left')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "T")],
    )
    a1 = _run(graph, store, target="A").frame["r"][0]
    b_identity = _identity(store, graph, "B")
    run_two: list[str] = []

    def refresh_a_and_publish_b2() -> None:
        a_identity = _identity(store, graph, "A")
        artifact = store.stage_node_output(a_identity)
        pl.read_parquet(project / "quotes.parquet").with_columns(
            pl.lit(-1.0).alias("r")
        ).write_parquet(artifact.part_path(0))
        store.publish_node_output(
            a_identity,
            artifact,
            columns=ALL,
            dependencies={},
            explicit=True,
            profile=ExecutionProfile.NODE_SNAPSHOT,
            refresh=True,
        ).close()
        _run(graph, store, target="B")
        latest = store.latest_generation(b_identity)
        assert latest is not None
        run_two.append(latest.generation_id)

    paused = _pause_at(monkeypatch, "B", refresh_a_and_publish_b2)
    run_one = _run(graph, store)

    assert paused == ["B"]
    assert run_one.seeds.keys() == {"A"}
    assert run_one.captures["B"]["outcome"] == "superseded"
    latest_b = store.latest_generation(b_identity)
    assert latest_b is not None and latest_b.generation_id == run_two[0]
    assert run_one.frame.height == _ROWS
    assert run_one.frame["rb"].to_list() == [a1] * _ROWS
    assert run_one.frame["rc"].to_list() == [a1] * _ROWS


def test_paused_run_diamond_with_uncaptured_random_a(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            (
                "A",
                NodeType.DATA_INPUT,
                _parquet(
                    project / "quotes.parquet",
                    "df = df.with_columns(pl.lit(R).alias('r'))",
                ),
            ),
            (
                "B",
                NodeType.POLARS,
                _code("df = A.select('id', pl.col('r').alias('rb')).sort('id')"),
            ),
            ("C", NodeType.POLARS, _code("df = A.select('id', pl.col('r').alias('rc'))")),
            ("D", NodeType.POLARS, _code("df = B.join(C, on='id', how='left')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "T")],
    )
    b_identity = _identity(store, graph, "B")
    run_two: list[str] = []

    def publish_b2() -> None:
        from haute.executor import _compile_preamble

        # Run 2 computes its own A: a fresh preamble draws a fresh scalar.
        _compile_preamble.cache_clear()
        _run(graph, store, target="B")
        latest = store.latest_generation(b_identity)
        assert latest is not None
        run_two.append(latest.generation_id)

    _pause_at(monkeypatch, "B", publish_b2)
    run_one = _run(graph, store)

    assert "A" not in run_one.captures
    assert run_one.captures["B"]["outcome"] == "superseded"
    latest_b = store.latest_generation(b_identity)
    assert latest_b is not None and latest_b.generation_id == run_two[0]
    scalar = run_one.frame["rb"][0]
    assert run_one.frame["rb"].to_list() == [scalar] * _ROWS
    assert run_one.frame["rc"].to_list() == [scalar] * _ROWS
    assert latest_b.lazy_frame.collect()["rb"][0] != scalar


def test_capture_records_dependency_closure(project: Path, store: NodeSnapshotStore) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            (
                "A",
                NodeType.POLARS,
                _code("df = src.with_columns((pl.col('a') * 2).alias('a2')).sort('a')"),
            ),
            ("G", NodeType.POLARS, _code("df = A.sort('a')")),
            ("X", NodeType.POLARS, _code("df = G.filter(pl.col('a') >= 0).sort('a')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "G"), ("G", "X"), ("X", "T")],
    )
    a_run = _run(graph, store, target="A")
    a1 = a_run.captures["A"]["generation_id"]
    a_digest = _identity(store, graph, "A").digest

    run = _run(graph, store, required={"T": ["a"]})

    assert run.seeds == {"A": a1}
    g1 = run.captures["G"]["generation_id"]
    g = store.latest_generation(_identity(store, graph, "G"))
    x = store.latest_generation(_identity(store, graph, "X"))
    assert g is not None and x is not None
    assert dict(g.dependencies) == {a_digest: a1}
    assert dict(x.dependencies) == {a_digest: a1, _identity(store, graph, "G").digest: g1}

    # A seed passes on what it was itself built from: seeding G (built from
    # A1) makes X's closure name A1 even though this run never reads A.
    store.clear(_identity(store, graph, "X"))
    reseeded = _run(graph, store, required={"T": ["a"]})
    assert reseeded.seeds == {"G": g1}
    x = store.latest_generation(_identity(store, graph, "X"))
    assert x is not None
    assert dict(x.dependencies) == {a_digest: a1, _identity(store, graph, "G").digest: g1}


def test_empty_demand_seed_and_capture_keep_row_count(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project)
    cold = _run(graph, store, required={"T": []})
    warm = _run(graph, store, required={"T": []})

    assert set(warm.seeds) == {"J"}
    assert cold.frame.select(pl.len()).item() == _ROWS
    assert warm.frame.select(pl.len()).item() == _ROWS


def test_multi_input_modelling_builds_only_selected_branch(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            (
                "A",
                NodeType.POLARS,
                _code("df = src.with_columns((pl.col('a') * 2).alias('a2')).sort('a2')"),
            ),
            (
                "B",
                NodeType.POLARS,
                _code("df = other.with_columns(pl.lit(1).alias('one')).sort('one')"),
            ),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("other", "B"), ("A", "T"), ("B", "T")],
    )
    cold = _run(graph, store, required={"T": ["a2"]})

    assert cold.calls["B"] == 0 and cold.calls["other"] == 0
    assert set(cold.captures) == {"A"}
    assert "a2" in cold.frame.columns

    warm = _run(graph, store, required={"T": ["a2"]})
    assert set(warm.seeds) == {"A"}
    assert not +warm.calls
    assert_frame_equal(warm.frame, cold.frame)


def test_materialisations_a_plan_does_not_build_are_not_admitted(
    project: Path, store: NodeSnapshotStore
) -> None:
    # No admitted budget: any materialisation the run performs would be refused.
    unadmitted = ExecutionContext(operation="unadmitted", profile=ExecutionProfile.TRAINING_PREP)
    unselected_sort = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("B", NodeType.POLARS, _code("df = other.sort('d')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("other", "B"), ("A", "T"), ("B", "T")],
    )
    run = _run(unselected_sort, store, required={"T": ["a2"]}, context=unadmitted)
    assert run.calls["B"] == 0
    assert run.frame.height == _ROWS

    seeded_sort = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("S", NodeType.POLARS, _code("df = src.sort('a')")),
            ("X", NodeType.POLARS, _code("df = S.filter(pl.col('a') >= 0).sort('a')")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "S"), ("S", "X"), ("X", "T")],
    )
    _run(seeded_sort, store, required={"T": ["a"]})
    seeded = _run(
        seeded_sort,
        store,
        required={"T": ["a"]},
        context=ExecutionContext(operation="unadmitted", profile=ExecutionProfile.TRAINING_PREP),
    )
    assert set(seeded.seeds) == {"X"}
    assert not +seeded.calls


def test_a_materialisation_below_a_seed_is_estimated_from_the_seed(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = widen(src)")),
            ("S", NodeType.POLARS, _code("df = A.sort('a', descending=True)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "S"), ("S", "T")],
    )
    # Nothing can estimate the sort through the helper, and there is no hard cap
    # to run it conservatively under, so a cold run is refused.
    with pytest.raises(GroupByExecutionUnsupportedError):
        _run(graph, store, required={"T": ["a"]})

    identity = _identity(store, graph, "A")
    artifact = store.stage_node_output(identity)
    pl.read_parquet(project / "quotes.parquet").with_columns(pl.lit(1).alias("h")).write_parquet(
        artifact.part_path(0)
    )
    store.publish_node_output(
        identity,
        artifact,
        columns=ALL,
        dependencies={},
        explicit=True,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ).close()

    # With A cached, the sort is estimated from A's generation, not A's code.
    run = _run(graph, store, required={"T": ["a"]})

    assert set(run.seeds) == {"A"}
    assert run.captures["S"]["outcome"] == "published"
    assert run.frame["a"].to_list() == list(reversed(range(_ROWS)))


def _captured_source_graph(project: Path) -> PipelineGraph:
    """A Data Input with post-load code is a node-output point, captured as consumed."""
    return _graph(
        project,
        [
            (
                "src",
                NodeType.DATA_INPUT,
                _parquet(
                    project / "quotes.parquet",
                    "df = df.with_columns((pl.col('a') + 1).alias('a1')).sort('a1')",
                ),
            ),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "T")],
    )


def test_a_source_capture_waits_for_the_input_check(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _captured_source_graph(project)
    with _planned(graph, store, required={"T": ["a1"]}) as (_plan, context, execute):
        pl.DataFrame({"id": [0], "a": [1], "b": [2], "c": [3]}).write_parquet(
            project / "quotes.parquet"
        )
        with pytest.raises(SnapshotPlanInputsChangedError):
            execute()
    # Refused before anything was sunk, not merely before it was published.
    assert context.metrics_payload()["shared_snapshot_captures"] == []
    assert store.latest_generation(_identity(store, graph, "src")) is None
    assert not _staging_dirs(store)


def test_verified_source_capture_is_reused_by_its_consumer(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _captured_source_graph(project)
    first = _run(graph, store, required={"T": ["a1"]})
    second = _run(graph, store, required={"T": ["a1"]})
    assert first.captures["src"]["outcome"] == "published"
    assert set(second.seeds) == {"src"}
    assert first.frame["a1"].to_list() == list(range(1, _ROWS + 1))
    assert_frame_equal(second.frame, first.frame)


def test_planned_eager_orphan_transform_reports_missing_input_without_prebinding(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(project, [("T", NodeType.POLARS, _code("df = pl.LazyFrame({'x': [7]})"))], [])
    calls: list[str] = []

    def build(node: GraphNode, **kwargs: Any) -> Any:
        def run() -> pl.LazyFrame:
            calls.append(node.id)
            return pl.LazyFrame({"x": [7]})

        return node.id, run, False

    with _planned(graph, store, profile=ExecutionProfile.PREVIEW_EAGER) as (plan, context, _):
        result = execute_lazy_module._execute_eager_core(
            graph,
            build,
            target_node_id="T",
            execution_context=context,
            snapshot_plan=plan,
            enforce_contracts=False,
            swallow_errors=True,
        )
        assert result.outputs["T"] is None
        assert result.errors == {"T": "No input data available for node 'T'"}
    assert calls == []


def test_equivalent_cloned_passthrough_frame_retains_its_write_recipe(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._chunked_writes import WriteRecipe
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') + 1).alias('next'))")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "T")],
    )
    real_select = execute_lazy_module.select_edge_source_output
    clones: list[pl.LazyFrame] = []

    def cloned(frame: Any, edge: GraphEdge) -> Any:
        selected = real_select(frame, edge)
        if edge.target == "T":
            selected = selected.clone()
            clones.append(selected)
        return selected

    monkeypatch.setattr(execute_lazy_module, "select_edge_source_output", cloned)
    recipes: dict[str, WriteRecipe] = {}
    with _planned(graph, store) as (plan, context, _):
        outputs, *_ = execute_lazy_graph(
            graph,
            _counting_build(Counter()),
            target_node_id="T",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph)),
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
            write_recipes=recipes,
        )
        assert clones
        assert "T" in recipes and recipes["T"].fn is not None
        assert_frame_equal(recipes["T"].native().collect(), outputs["T"].collect())
        assert outputs["T"].collect()["next"].to_list() == list(range(1, _ROWS + 1))


@pytest.mark.parametrize("eager", [False, True])
def test_seeded_execution_does_not_require_a_metrics_context(
    project: Path, store: NodeSnapshotStore, eager: bool
) -> None:
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _join_graph(project)
    expected = _run(graph, store).frame
    profile = ExecutionProfile.PREVIEW_EAGER if eager else ExecutionProfile.LAZY_SINK
    request = _request(graph, "T", required=None, profile=profile)
    calls: Counter[str] = Counter()
    with open_seed_plan(request, store=store) as plan:
        kwargs = {
            "target_node_id": "T",
            "preamble_ns": _compile_preamble(
                graph.preamble or "", pipeline_dir=_pipeline_dir(graph)
            ),
            "snapshot_plan": plan,
        }
        if eager:
            result = execute_lazy_module._execute_eager_core(
                graph, _counting_build(calls), **kwargs
            )
            actual = result.outputs["T"]
        else:
            outputs, *_ = execute_lazy_graph(
                graph, _counting_build(calls), prepare_inputs=False, **kwargs
            )
            actual = outputs["T"].collect()
        assert set(plan.decision.seeds) == {"J"}
        assert calls["src"] == 0 and calls["other"] == 0
        assert_frame_equal(actual, expected)


def test_a_source_capture_whose_input_changes_before_publishing_stops_the_run(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source whose input moved mid-run stops, and publishes nothing.

    It used to keep its artifact and carry on, which is the mix the pre-run
    check exists to prevent: this run would have read a seed signed for the old
    input beside branches recomputed from the new one.
    """
    from haute.errors import SnapshotPlanInputsChangedError

    graph = _captured_source_graph(project)

    def rewrite_source() -> None:
        pl.DataFrame({"id": [0], "a": [1], "b": [2], "c": [3]}).write_parquet(
            project / "quotes.parquet"
        )

    _pause_at(monkeypatch, "src", rewrite_source)
    with pytest.raises(SnapshotPlanInputsChangedError):
        _run(graph, store, required={"T": ["a1"]})

    assert store.latest_generation(_identity(store, graph, "src")) is None


def test_pass_through_returns_the_selected_api_port(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._json_flatten import _json_cache_dir
    from haute._json_shred._cache import build_per_port_cache

    data_path = project / "records.json"
    data_path.write_text(
        json.dumps(
            [
                {"policy_id": 1, "drivers": [{"driver_id": 10}, {"driver_id": 11}]},
                {"policy_id": 2, "drivers": [{"driver_id": 12}]},
            ]
        ),
        encoding="utf-8",
    )
    config = {
        "path": str(data_path),
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
    build_per_port_cache(data_path, config, _json_cache_dir(data_path, "working"))
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

    run = _run(graph, store, target="OPT", profile=ExecutionProfile.OPTIMISER_SETUP)

    assert run.captures == {}
    assert run.frame["driver_id"].to_list() == [10, 11, 12]


# ---------------------------------------------------------------------------
# Batch Model Score
# ---------------------------------------------------------------------------


class _TenTimes:
    """A deterministic model that counts how often it is asked to score."""

    calls = 0

    def predict(self, features: Any) -> Any:
        import numpy as np

        type(self).calls += 1
        column = features["feature"] if hasattr(features, "__getitem__") else features
        return np.asarray(column, dtype="float64") * 10.0


@pytest.fixture()
def scoring_model(monkeypatch: pytest.MonkeyPatch) -> type[_TenTimes]:
    from haute import _mlflow_io

    _TenTimes.calls = 0
    monkeypatch.setattr(
        _mlflow_io,
        "load_mlflow_model",
        lambda *_args, **_kwargs: _mlflow_io.ScoringModel(
            _TenTimes(), ["feature"], flavor="pyfunc"
        ),
    )
    return _TenTimes


def _scored_graph(project: Path, **model_config: Any) -> PipelineGraph:
    """``scoring → M (batch Model Score) → T``."""
    from haute.modelling._feature_contract import build_contract, save_contract

    pl.DataFrame(
        {
            "quote_id": [f"q{index}" for index in range(1, 6)],
            "feature": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    ).write_parquet(project / "scoring.parquet")
    contract_path = project / "feature_contract.json"
    save_contract(
        build_contract(
            features=["feature"],
            feature_types={"feature": "Float64"},
            categorical_features=[],
            target_name="target",
            target_type="Float64",
            task="regression",
        ),
        contract_path,
    )
    return _graph(
        project,
        [
            ("scoring", NodeType.DATA_INPUT, _parquet(project / "scoring.parquet")),
            (
                "M",
                NodeType.MODEL_SCORE,
                {
                    "sourceType": "run",
                    "run_id": "run-1",
                    "artifact_path": "model.pyfunc",
                    "task": "regression",
                    "output_column": "prediction",
                    "feature_contract_path": str(contract_path),
                    **model_config,
                },
            ),
            ("T", NodeType.MODELLING, {}),
        ],
        [("scoring", "M"), ("M", "T")],
    )


@pytest.fixture()
def engine_sinks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every file the engine itself sinks (not the scorer's own writes)."""
    written: list[str] = []
    sink = execute_lazy_module.write_parts

    def counting(directory: Any, *args: Any, **kwargs: Any) -> Any:
        written.append(str(directory))
        return sink(directory, *args, **kwargs)

    monkeypatch.setattr(execute_lazy_module, "write_parts", counting)
    return written


def test_model_score_capture_is_the_scored_file(
    project: Path,
    store: NodeSnapshotStore,
    scoring_model: type[_TenTimes],
    engine_sinks: list[str],
) -> None:
    graph = _scored_graph(project)
    run = _run(graph, store, source="batch")

    assert run.captures["M"]["outcome"] == "published"
    assert engine_sinks == []
    assert run.frame["prediction"].to_list() == [10.0, 20.0, 30.0, 40.0, 50.0]
    latest = store.latest_generation(_identity(store, graph, "M", "batch"))
    assert latest is not None
    assert_frame_equal(latest.lazy_frame.collect(), run.frame)


def test_failed_model_score_cleans_its_staged_capture(
    project: Path,
    store: NodeSnapshotStore,
    scoring_model: type[_TenTimes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _scored_graph(project)

    def fail_prediction(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("prediction failed")

    monkeypatch.setattr(scoring_model, "predict", fail_prediction)
    with pytest.raises(RuntimeError, match="prediction failed"):
        _run(graph, store, source="batch")
    assert _staging_dirs(store) == []
    assert store.latest_generation(_identity(store, graph, "M", "batch")) is None


def test_a_scored_capture_publishes_the_scorer_s_own_digest(
    project: Path,
    store: NodeSnapshotStore,
    scoring_model: type[_TenTimes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scored node capture publishes using the scorer's write-time digest without rehashing."""
    graph = _scored_graph(project)
    with _hash_spy(monkeypatch) as recorded:
        run = _run(graph, store, source="batch")

    assert run.captures["M"]["outcome"] == "published"
    assert recorded == []

    latest = store.latest_generation(_identity(store, graph, "M", "batch"))
    assert latest is not None
    parts = latest.generation.metadata.parts
    assert len(parts) == 1
    part = parts[0]
    part_path = latest.generation.directory / part.name
    assert part.digest == content_hash(part_path)


def test_seeded_model_score_makes_zero_scoring_calls(
    project: Path, store: NodeSnapshotStore, scoring_model: type[_TenTimes]
) -> None:
    graph = _scored_graph(project)
    first = _run(graph, store, source="batch")
    calls_after_first = scoring_model.calls
    assert calls_after_first > 0

    second = _run(graph, store, source="batch")

    assert set(second.seeds) == {"M"}
    assert scoring_model.calls == calls_after_first
    assert not +second.calls
    assert_frame_equal(second.frame, first.frame)


@pytest.mark.parametrize(
    ("model_config", "expected"),
    [
        (
            {
                "code": (
                    "df = df.filter(pl.col('feature') > 2)"
                    ".with_columns((pl.col('prediction') + 1).alias('prediction'))"
                )
            },
            {"prediction": [31.0, 41.0, 51.0]},
        ),
        (
            {"column_renames": {"quote_id": "qid"}},
            {"qid": [f"q{index}" for index in range(1, 6)]},
        ),
    ],
    ids=["post_code", "rename"],
)
def test_model_score_with_its_own_post_processing_sinks_its_final_frame(
    project: Path,
    store: NodeSnapshotStore,
    scoring_model: type[_TenTimes],
    engine_sinks: list[str],
    model_config: dict[str, Any],
    expected: dict[str, list[Any]],
) -> None:
    graph = _scored_graph(project, **model_config)
    cold = _run(graph, store, source="batch")

    assert len(engine_sinks) == 1
    ((column, values),) = expected.items()
    assert cold.frame[column].to_list() == values

    warm = _run(graph, store, source="batch")
    assert set(warm.seeds) == {"M"}
    assert_frame_equal(warm.frame, cold.frame)


def test_model_score_quota_rejection_keeps_scored_file(
    project: Path,
    scoring_model: type[_TenTimes],
    engine_sinks: list[str],
) -> None:
    from haute._node_snapshots import NodeSnapshotSlot

    graph = _scored_graph(project)
    full = NodeSnapshotStore(project, node_output_max_generations=1)
    # One pinned generation of an unrelated slot fills the quota.
    filler = NodeSnapshotSlot(str(project / "other.py"), "filler", "batch", "bounded").identity(
        "filler-signature"
    )
    artifact = full.stage_node_output(filler)
    pl.DataFrame({"a": [1]}).write_parquet(artifact.part_path(0))
    full.publish_node_output(
        filler,
        artifact,
        columns=ALL,
        dependencies={},
        explicit=True,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ).close()

    with _planned(graph, full, source="batch") as (_plan, context, execute):
        output, _calls = execute()
        first, second = output.collect(), output.collect()
    metrics = context.metrics_payload(status="completed")

    assert {
        capture["node_id"]: capture["outcome"] for capture in metrics["shared_snapshot_captures"]
    } == {"M": "quota"}
    assert engine_sinks == []
    assert scoring_model.calls == 1
    assert_frame_equal(first, second)
    assert first["prediction"].to_list() == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert not _staging_dirs(full)


# ---------------------------------------------------------------------------
# Failures and guards
# ---------------------------------------------------------------------------


def test_best_effort_capture_column_unavailable_is_dropped(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project, b_code="df = J.filter(pl.col('a') >= 0).sort('a')")
    run = _run(graph, store, required={"T": ["a"]}, capture_columns_by_node={"B": ["absent"]})

    assert run.captures["B"]["columns"] == ["a"]
    assert run.frame.columns == ["a"]


def test_strict_column_missing_fails(project: Path, store: NodeSnapshotStore) -> None:
    graph = _join_graph(project)
    with pytest.raises(ContractMismatchError):
        _run(graph, store, required={"T": ["absent"]})
    assert not _staging_dirs(store)


def test_corrupt_latest_generation_fails_the_run(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.errors import SnapshotCorruptError

    graph = _join_graph(project, b_code="df = J.filter(pl.col('a') >= 0).sort('a')")
    _run(graph, store, required={"T": ["a"]})
    b_identity = _identity(store, graph, "B")

    def corrupt_b() -> None:
        latest = store.latest_generation(b_identity)
        assert latest is not None
        latest.generation.data_paths[0].write_bytes(b"corrupt")

    _pause_at(monkeypatch, "B", corrupt_b)
    with pytest.raises(SnapshotCorruptError) as raised:
        _run(graph, NodeSnapshotStore(project), required={"T": ["a", "b"]})
    # The store reports corruption against an identity; the run names the node,
    # so the message points at a cache button the user can actually press.
    assert raised.value.node_id == "B"
    assert raised.value.to_payload()["error_code"] == "snapshot_corrupt"
    assert not _staging_dirs(store)


def test_inputs_changed_before_collection_fails(project: Path, store: NodeSnapshotStore) -> None:
    graph = _join_graph(project)
    with _planned(graph, store, required={"T": ["a"]}) as (_plan, _context, execute):
        pl.DataFrame({"id": [0], "a": [1], "b": [2], "c": [3]}).write_parquet(
            project / "quotes.parquet"
        )
        with pytest.raises(SnapshotPlanInputsChangedError):
            execute()


def test_a_capture_published_before_the_inputs_moved_stays_published(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping mid-run does not unpublish what was already correct.

    A capture published before the change was computed from the inputs its
    identity signs, so it stays. Only the unfinished one is discarded, and
    nothing after it is published.
    """
    from haute.errors import SnapshotPlanInputsChangedError

    # ``sort`` makes ``A`` a capture point of its own rather than a
    # slice-transparent feeder, so ``A`` publishes before ``J`` is reached.
    graph = _join_graph(
        project, a_code="df = src.with_columns((pl.col('a') * 2).alias('a2')).sort('a')"
    )

    def rewrite_source() -> None:
        pl.DataFrame({"id": [0], "a": [1], "b": [2], "c": [3]}).write_parquet(
            project / "quotes.parquet"
        )

    # Bound before the run: the rewrite moves what a fresh signature would
    # sign, and what stays published is the identity ``A`` was computed for.
    identity_a = _identity(store, graph, "A")
    identity_j = _identity(store, graph, "J")

    _pause_at(monkeypatch, "J", rewrite_source)
    with pytest.raises(SnapshotPlanInputsChangedError):
        _run(graph, store, required={"T": ["a"]})

    assert store.latest_generation(identity_a) is not None
    assert store.latest_generation(identity_j) is None


def test_inputs_changed_before_publish_stops_the_run(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run stops rather than publishing, or reading, a mixed result."""
    from haute.errors import SnapshotPlanInputsChangedError

    graph = _join_graph(
        project, a_code="df = src.with_columns((pl.col('a') * 2).alias('a2')).sort('a')"
    )

    def rewrite_source() -> None:
        pl.DataFrame({"id": [0], "a": [1], "b": [2], "c": [3]}).write_parquet(
            project / "quotes.parquet"
        )

    _pause_at(monkeypatch, "A", rewrite_source)
    with pytest.raises(SnapshotPlanInputsChangedError):
        _run(graph, store, required={"T": ["a"]})

    assert store.latest_generation(_identity(store, graph, "A")) is None
    # The unfinished capture's staging is discarded, not left holding quota.
    assert not _staging_dirs(store)


def test_metrics_report_seeds_captures_and_warnings(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project)
    first = _run(graph, store, required={"T": ["a"]})
    second = _run(graph, store, required={"T": ["a"]})

    for run in (first, second):
        validated = ExecutionMetricsPayload.model_validate(run.metrics).model_dump(mode="json")
        for key in ("shared_snapshot_seeds", "shared_snapshot_captures", "warnings"):
            assert validated[key] == run.metrics[key]
    j_digest = _identity(store, graph, "J").digest
    capture = first.captures["J"]
    assert capture == {
        "node_id": "J",
        "identity_digest": j_digest,
        "kind": "materialising",
        "outcome": "published",
        "generation_id": capture["generation_id"],
        "columns": ["a"],
        "write_strategy": "native",
        "write_parts": 1,
        "write_chunk_rows": None,
        "write_staged_inputs": 0,
        "write_input_slices": None,
        "write_native_reason": "not_sliceable",
        "write_blocking_operator": None,
    }
    assert first.metrics["warnings"] == []
    assert first.metrics["shared_snapshot_capture_skips"] == [
        {"node_id": "A", "reason": "slice_transparent_feeder"},
        {"node_id": "B", "reason": "cheap_segment"},
    ]
    assert second.metrics["shared_snapshot_seeds"] == [
        {
            "node_id": "J",
            "identity_digest": j_digest,
            "generation_id": capture["generation_id"],
            "columns": ["a"],
        }
    ]


def test_plan_is_exclusive_with_a_cache_request(project: Path, store: NodeSnapshotStore) -> None:
    from haute.executor import _build_node_fn

    graph = _join_graph(project)
    with _planned(graph, store, required={"T": ["a"]}) as (plan, context, _execute):
        common: dict[str, Any] = {
            "target_node_id": "T",
            "enforce_contracts": True,
            "required_columns_by_node": {"T": ["a"]},
            "execution_context": context,
            "prepare_inputs": False,
            "snapshot_plan": plan,
        }
        with pytest.raises(ValueError, match="replaces the dataframe cache"):
            execute_lazy_graph(graph, _build_node_fn, dataframe_cache_request=object(), **common)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="different execution"):
            execute_lazy_graph(graph, _build_node_fn, source="batch", **common)
        with pytest.raises(ValueError, match="different execution"):
            execute_lazy_graph(
                graph,
                _build_node_fn,
                **{
                    **common,
                    "execution_context": _context(ExecutionProfile.OPTIMISER_SETUP),
                },
            )


def test_multipart_seed_estimate_counts_every_part(project: Path, store: NodeSnapshotStore) -> None:
    from haute._ram_estimate import (
        _data_input_parquet_artifact,
        _detailed_source_metadata_for_node,
    )

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = widen(src)")),
            ("S", NodeType.POLARS, _code("df = A.sort('a', descending=True)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "A"), ("A", "S"), ("S", "T")],
    )

    identity = _identity(store, graph, "A")
    artifact = store.stage_node_output(identity)
    n_parts = 3
    rows_per_part = 50
    total_rows = n_parts * rows_per_part
    for i in range(n_parts):
        pl.DataFrame(
            {
                "id": list(range(i * rows_per_part, (i + 1) * rows_per_part)),
                "a": list(range(i * rows_per_part, (i + 1) * rows_per_part)),
                "b": [v * 2 for v in range(i * rows_per_part, (i + 1) * rows_per_part)],
                "c": [v * 3 for v in range(i * rows_per_part, (i + 1) * rows_per_part)],
                "h": [1] * rows_per_part,
            }
        ).write_parquet(artifact.part_path(i))

    store.publish_node_output(
        identity,
        artifact,
        columns=ALL,
        dependencies={},
        explicit=True,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ).close()

    with _planned(graph, store, required={"T": ["a"]}) as (plan, _context, execute):
        assert set(plan.decision.seeds) == {"A"}
        est_graph = plan.estimation_graph(graph)
        node_a = next(node for node in est_graph.nodes if node.id == "A")
        assert node_a.data.nodeType == NodeType.DATA_INPUT
        assert node_a.data.config["path"].endswith("part-*.parquet")

        count, paths = _data_input_parquet_artifact(node_a.data.config)
        assert len(paths) == n_parts

        source_meta = _detailed_source_metadata_for_node(node_a)
        assert source_meta is not None
        assert source_meta.row_count == total_rows

        output, _calls = execute()
        frame = output.collect()
        assert frame.height == total_rows
        assert frame["a"].to_list() == list(reversed(range(total_rows)))


@pytest.mark.parametrize("cache_state", ["fresh", "missing", "stale", "incomplete", "other_source"])
def test_training_ram_estimates_use_only_usable_snapshots(
    project: Path,
    store: NodeSnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
    cache_state: str,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._train_service import TrainService
    from haute.routes.modelling import estimate_training
    from haute.schemas import TrainEstimateRequest
    from tests.job_store_support import seed_job

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = widen(src)")),
            ("T", NodeType.MODELLING, {"target": "a", "feature_columns": ["h"]}),
        ],
        [("src", "A"), ("A", "T")],
    )
    if cache_state != "missing":
        source = "batch" if cache_state == "other_source" else "live"
        identity = _identity(store, graph, "A", source)
        artifact = store.stage_node_output(identity)
        names = ["a"] if cache_state == "incomplete" else ["a", "h"]
        for part in range(2):
            pl.DataFrame({"a": [part * 2, part * 2 + 1], "h": [1, 1]}).select(names).write_parquet(
                artifact.part_path(part)
            )
        store.publish_node_output(
            identity,
            artifact,
            columns=NodeSnapshotColumns.of(names),
            dependencies={},
            explicit=True,
            profile=ExecutionProfile.NODE_SNAPSHOT,
        ).close()
        if cache_state == "stale":
            graph.node_map["A"].data.config["code"] = "df = widen(src).head(3)"

    def no_execution(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("RAM estimation must not execute or prepare pipeline inputs")

    monkeypatch.setattr("haute.executor._build_node_fn", no_execution)
    monkeypatch.setattr("haute._input_preparation.prepare_input_snapshots", no_execution)
    monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 1024**3)

    response = estimate_training(TrainEstimateRequest(graph=graph, node_id="T"))
    jobs = JobStore()
    seed_job(jobs, "ram", {"status": "running"})
    try:
        _warning, _limit, rows, columns = TrainService(jobs)._estimate_ram(graph, "T", None, "ram")
    finally:
        jobs.delete_job("ram")

    expected_rows = 4 if cache_state == "fresh" else None
    assert response.total_rows == rows == expected_rows
    if cache_state == "fresh":
        assert response.bytes_per_row == 2 * 8 * 3
        assert columns == 2
    else:
        assert response.bytes_per_row == columns == 0
    assert _staging_dirs(store) == []


def test_lazy_run_join_capture_is_chunked_into_parts(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._polars_utils import temporary_streaming_chunk_size

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                    "column_renames": {"a": "alpha"},
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('alpha') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e0", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e1", source="src", target="J", targetHandle="base"),
            GraphEdge(id="e2", source="J", target="B"),
            GraphEdge(id="e3", source="B", target="T"),
        ],
    )

    # _ROWS rows in chunks of 80: three parts.
    with temporary_streaming_chunk_size(80):
        run = _run(graph, store, required={"T": ["id", "alpha", "d"]})

    capture = run.captures["J"]
    assert capture["write_strategy"] == "chunked_join"
    assert capture["write_parts"] is not None and capture["write_parts"] > 1
    assert capture["outcome"] == "published"

    identity = _identity(store, graph, "J")
    gen = store.latest_generation(identity)
    assert gen is not None
    assert len(gen.generation.metadata.parts) > 1
    assert len(gen.generation.data_paths) > 1

    expected = (
        pl.read_parquet(project / "quotes.parquet")
        .join(pl.read_parquet(project / "claims.parquet"), on="id", how="left")
        .select(["id", "a", "d"])
        .rename({"a": "alpha"})
        .filter(pl.col("alpha") >= 0)
    )
    assert_frame_equal(
        run.frame.select(["id", "alpha", "d"]).sort("id"),
        expected.sort("id"),
    )


@pytest.mark.parametrize("eager", [False, True])
def test_metrics_list_skipped_capture_points(
    project: Path, store: NodeSnapshotStore, eager: bool
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("A", NodeType.POLARS, _code("df = src.select('id', 'a', 'b')")),
            ("C1", NodeType.POLARS, _code("df = A.select('id', 'a')")),
            ("C2", NodeType.POLARS, _code("df = A.select('id', 'b')")),
            ("D1", NodeType.POLARS, _code("df = C1.sort('a')")),
            ("D2", NodeType.POLARS, _code("df = C2.sort('b')")),
            ("T", NodeType.POLARS, _code("df = D1.join(D2, on='id')")),
        ],
        [
            ("src", "A"),
            ("A", "C1"),
            ("A", "C2"),
            ("C1", "D1"),
            ("C2", "D2"),
            ("D1", "T"),
            ("D2", "T"),
        ],
    )
    profile = ExecutionProfile.TRAINING_PREP
    context = _context(profile)
    if eager:
        from haute.executor import _compile_preamble, _pipeline_dir

        with _planned(graph, store, context=context, profile=profile) as (plan, _, _):
            result = execute_lazy_module._execute_eager_core(
                graph,
                _counting_build(Counter()),
                target_node_id="T",
                preamble_ns=_compile_preamble(
                    graph.preamble or "", pipeline_dir=_pipeline_dir(graph)
                ),
                execution_context=context,
                snapshot_plan=plan,
            )
            run = RunResult(result.outputs["T"], context.metrics_payload(status="completed"))
    else:
        run = _run(graph, store, context=context)

    assert run.metrics["shared_snapshot_capture_skips"] == [
        {"node_id": "A", "reason": "cheap_segment"},
    ]
    assert "A" not in run.captures

    evidence = context.worker_evidence()
    fresh = _context(ExecutionProfile.TRAINING_PREP)
    fresh.adopt_worker_evidence(evidence)
    assert (
        fresh.metrics_payload(status="completed")["shared_snapshot_capture_skips"]
        == run.metrics["shared_snapshot_capture_skips"]
    )


def test_bounded_run_over_cheap_consumed_segment_reads_it_directly_and_next_run_recomputes(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("F", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("src", "F"), ("F", "T")],
    )
    first = _run(graph, store, required={"T": ["a"]})
    assert first.captures == {}
    assert first.seeds == {}
    assert first.metrics["shared_snapshot_capture_skips"] == [
        {"node_id": "F", "reason": "cheap_segment"},
    ]
    assert first.calls["src"] == 1

    second = _run(graph, store, required={"T": ["a"]})
    assert second.seeds == {}
    assert second.captures == {}
    assert second.calls["src"] == 1
    assert_frame_equal(second.frame, first.frame)


def test_captured_chunk_local_node_reports_input_sliced_across_slices(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._polars_utils import temporary_streaming_chunk_size

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0)")),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('a') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_src", source="src", target="A"),
            GraphEdge(id="e0", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e1", source="A", target="J", targetHandle="base"),
            GraphEdge(id="e2", source="J", target="B"),
            GraphEdge(id="e3", source="B", target="T"),
        ],
    )
    with temporary_streaming_chunk_size(80):
        run = _run(graph, store, required={"T": ["id", "a", "d"], "A": ["a", "id"]})
    capture = run.captures["A"]
    assert capture["write_strategy"] == "input_sliced"
    assert capture["write_input_slices"] == 3
    assert capture["write_parts"] == 3
    assert capture["write_native_reason"] is None
    assert capture["write_blocking_operator"] is None


def test_captured_rejected_node_reports_native_with_reason_and_blocking_operator(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._polars_utils import temporary_streaming_chunk_size

    graph = _join_graph(
        project,
        a_code="df = src.filter(pl.col('a') >= 0).head(50)",
    )
    with temporary_streaming_chunk_size(80):
        run = _run(graph, store, required={"T": ["a"]})
    capture = run.captures["A"]
    assert capture["write_strategy"] == "native"
    assert capture["write_native_reason"] == "unsupported_frame_method"
    assert capture["write_blocking_operator"] == "head"
    assert capture["write_input_slices"] is None
    assert capture["write_parts"] == 1


def test_captured_two_input_node_gets_no_recipe_and_no_classifier_reason(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project)
    run = _run(graph, store, required={"T": ["a"]})
    capture = run.captures["J"]
    assert capture["write_strategy"] == "native"
    assert capture["write_native_reason"] == "not_sliceable"
    assert capture["write_blocking_operator"] is None
    assert capture["write_input_slices"] is None


def test_captured_chunk_local_node_empty_input_publishes_and_metrics_validate(
    project: Path, store: NodeSnapshotStore
) -> None:
    empty_parquet = project / "empty_quotes.parquet"
    pl.DataFrame(
        {"id": [], "a": [], "d": []},
        schema={"id": pl.Int64, "a": pl.Float64, "d": pl.Utf8},
    ).write_parquet(empty_parquet)

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(empty_parquet)),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0)")),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('a') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_src", source="src", target="A"),
            GraphEdge(id="e0", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e1", source="A", target="J", targetHandle="base"),
            GraphEdge(id="e2", source="J", target="B"),
            GraphEdge(id="e3", source="B", target="T"),
        ],
    )
    run = _run(graph, store, required={"T": ["id", "a", "d"], "A": ["a", "id"]})
    capture = run.captures["A"]
    assert capture["outcome"] == "published"
    assert capture["write_strategy"] == "input_sliced"
    assert capture["write_input_slices"] == 1
    assert capture["write_parts"] == 1
    assert capture["write_native_reason"] is None
    assert capture["write_blocking_operator"] is None

    validated = ExecutionMetricsPayload.model_validate(run.metrics)
    assert any(c.node_id == "A" for c in validated.shared_snapshot_captures)
    assert store.latest_generation(_identity(store, graph, "A")) is not None


def test_captured_instance_node_referencing_original_source_names_is_input_sliced(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._polars_utils import temporary_streaming_chunk_size

    graph = _graph(
        project,
        [
            ("src1", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("src2", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("orig", NodeType.POLARS, _code("df = src1.filter(pl.col('a') >= 0)")),
            (
                "inst",
                NodeType.POLARS,
                {"instanceOf": "orig", "code": "df = src1.filter(pl.col('a') >= 0)"},
            ),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "b"],
                },
            ),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_orig", source="src1", target="orig"),
            GraphEdge(id="e_inst", source="src2", target="inst"),
            GraphEdge(id="e_base", source="inst", target="J", targetHandle="base"),
            GraphEdge(id="e_join", source="src1", target="J", targetHandle="join"),
            GraphEdge(id="e_t", source="J", target="T"),
        ],
    )
    with temporary_streaming_chunk_size(80):
        run = _run(graph, store, required={"T": ["id", "a", "b"], "inst": ["a", "id"]})
    capture = run.captures["inst"]
    assert capture["write_strategy"] == "input_sliced"
    assert capture["write_native_reason"] is None
    assert capture["write_blocking_operator"] is None


def test_composed_passthrough_recipe_over_filtered_parent_is_input_sliced_and_equals_native(
    project: Path, tmp_path: Path, store: NodeSnapshotStore
) -> None:
    from haute._chunked_writes import WriteRecipe, part_paths, scan_parts, sliceable, write_parts
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0)")),
            ("P", NodeType.MODELLING, {}),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('a') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_src", source="src", target="A"),
            GraphEdge(id="e_ap", source="A", target="P"),
            GraphEdge(id="e_pj", source="P", target="J", targetHandle="base"),
            GraphEdge(id="e_oj", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e_jb", source="J", target="B"),
            GraphEdge(id="e_bt", source="B", target="T"),
        ],
    )
    custom_write_recipes: dict[str, WriteRecipe] = {}
    calls: Counter[str] = Counter()
    with _planned(
        graph,
        store,
        target="T",
        required={"T": ["id", "a", "d"], "P": ["a", "id"]},
    ) as (
        plan,
        context,
        _execute,
    ):
        outputs, *_ = execute_lazy_graph(
            graph,
            _counting_build(calls),
            target_node_id="T",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source="live",
            enforce_contracts=True,
            required_columns_by_node={"T": ["id", "a", "d"], "P": ["a", "id"]},
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
            write_recipes=custom_write_recipes,
            preserve_node_ids={"P"},
        )
    assert "P" in custom_write_recipes
    recipe = custom_write_recipes["P"]
    p_frame = outputs["P"]

    assert sliceable(recipe.input) is True
    assert sliceable(p_frame) is False

    target_dir = tmp_path / "passthrough_parts"
    target_dir.mkdir()
    res = write_parts(target_dir, p_frame, recipe=recipe, chunk_rows=80)
    assert res.strategy == "input_sliced"
    assert res.input_slices == 3
    assert res.chunks == 3
    assert len(res.parts) == 3
    assert res.native_reason is None
    assert res.blocking_operator is None

    written_df = scan_parts(part_paths(target_dir)).collect()
    native_df = p_frame.collect()
    assert_frame_equal(written_df, native_df)


def test_passthrough_recipe_over_rejected_parent_records_parent_rejection(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._chunked_writes import WriteRecipe
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0).head(50)")),
            ("P", NodeType.MODELLING, {}),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('a') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_src", source="src", target="A"),
            GraphEdge(id="e_ap", source="A", target="P"),
            GraphEdge(id="e_pj", source="P", target="J", targetHandle="base"),
            GraphEdge(id="e_oj", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e_jb", source="J", target="B"),
            GraphEdge(id="e_bt", source="B", target="T"),
        ],
    )
    custom_write_recipes: dict[str, WriteRecipe] = {}
    calls: Counter[str] = Counter()
    with _planned(
        graph,
        store,
        target="T",
        required={"T": ["id", "a", "d"], "P": ["a", "id"]},
    ) as (
        plan,
        context,
        _execute,
    ):
        execute_lazy_graph(
            graph,
            _counting_build(calls),
            target_node_id="T",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source="live",
            enforce_contracts=True,
            required_columns_by_node={"T": ["id", "a", "d"], "P": ["a", "id"]},
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
            write_recipes=custom_write_recipes,
        )
    assert "P" in custom_write_recipes
    recipe = custom_write_recipes["P"]
    assert recipe.fn is None
    assert recipe.reason == "unsupported_frame_method"
    assert recipe.blocking_operator == "head"
    assert recipe.reason == custom_write_recipes["A"].reason
    assert recipe.blocking_operator == custom_write_recipes["A"].blocking_operator


def test_passthrough_over_parent_with_no_recipe_gets_no_recipe_entry(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._chunked_writes import WriteRecipe
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _graph(
        project,
        [
            ("src1", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("src2", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            (
                "A",
                NodeType.POLARS,
                _code("df = src1.join(src2, on='id', how='left')"),
            ),
            ("P", NodeType.MODELLING, {}),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('a') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_s1", source="src1", target="A"),
            GraphEdge(id="e_s2", source="src2", target="A"),
            GraphEdge(id="e_ap", source="A", target="P"),
            GraphEdge(id="e_pj", source="P", target="J", targetHandle="base"),
            GraphEdge(id="e_oj", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e_jb", source="J", target="B"),
            GraphEdge(id="e_bt", source="B", target="T"),
        ],
    )
    custom_write_recipes: dict[str, WriteRecipe] = {}
    calls: Counter[str] = Counter()
    with _planned(
        graph,
        store,
        target="T",
        required={"T": ["id", "a", "d"], "P": ["a", "id"]},
    ) as (
        plan,
        context,
        _execute,
    ):
        execute_lazy_graph(
            graph,
            _counting_build(calls),
            target_node_id="T",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source="live",
            enforce_contracts=True,
            required_columns_by_node={"T": ["id", "a", "d"], "P": ["a", "id"]},
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
            write_recipes=custom_write_recipes,
        )
    assert "A" not in custom_write_recipes
    assert "P" not in custom_write_recipes


def test_passthrough_recipe_with_multiple_edges_follows_selected_edge_schema(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._chunked_writes import WriteRecipe
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0)")),
            ("C", NodeType.POLARS, _code("df = other.filter(pl.col('d') >= 0)")),
            (
                "P",
                NodeType.OPTIMISER,
                {"data_input": "C"},
            ),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "d"],
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('d') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_src", source="src", target="A"),
            GraphEdge(id="e_other", source="other", target="C"),
            GraphEdge(id="e_ap", source="A", target="P"),
            GraphEdge(id="e_cp", source="C", target="P"),
            GraphEdge(id="e_pj", source="P", target="J", targetHandle="base"),
            GraphEdge(id="e_other_j", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e_jb", source="J", target="B"),
            GraphEdge(id="e_bt", source="B", target="T"),
        ],
    )
    custom_write_recipes: dict[str, WriteRecipe] = {}
    calls: Counter[str] = Counter()
    with _planned(
        graph,
        store,
        target="T",
        required={"T": ["id", "d"], "P": ["id", "d"]},
    ) as (
        plan,
        context,
        _execute,
    ):
        execute_lazy_graph(
            graph,
            _counting_build(calls),
            target_node_id="T",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source="live",
            enforce_contracts=True,
            required_columns_by_node={"T": ["id", "d"], "P": ["id", "d"]},
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
            write_recipes=custom_write_recipes,
        )
    assert "P" in custom_write_recipes
    recipe = custom_write_recipes["P"]
    assert recipe.fn is not None
    recipe_schema = recipe.native().collect_schema()
    c_schema = custom_write_recipes["C"].native().collect_schema()
    assert recipe_schema == c_schema
    assert "d" in recipe_schema.names()
    assert "a" not in recipe_schema.names()
    assert "b" not in recipe_schema.names()


def test_link_proof_refuses_captured_parent_output_and_omits_child_recipe(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._chunked_writes import WriteRecipe
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0)")),
            ("P", NodeType.MODELLING, {}),
            (
                "J1",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            (
                "J2",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_src", source="src", target="A"),
            GraphEdge(id="e_aj1", source="A", target="J1", targetHandle="base"),
            GraphEdge(id="e_oj1", source="other", target="J1", targetHandle="join"),
            GraphEdge(id="e_ap", source="A", target="P"),
            GraphEdge(id="e_j1j2", source="J1", target="J2", targetHandle="base"),
            GraphEdge(id="e_pj2", source="P", target="J2", targetHandle="join"),
            GraphEdge(id="e_j2t", source="J2", target="T"),
        ],
    )
    custom_write_recipes: dict[str, WriteRecipe] = {}
    calls: Counter[str] = Counter()
    with _planned(
        graph,
        store,
        target="T",
        required={"T": ["id", "a", "d"], "A": ["id", "a"]},
    ) as (
        plan,
        context,
        _execute,
    ):
        assert "A" in plan.decision.captures
        assert "P" not in plan.decision.captures
        execute_lazy_graph(
            graph,
            _counting_build(calls),
            target_node_id="T",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source="live",
            enforce_contracts=True,
            required_columns_by_node={"T": ["id", "a", "d"], "A": ["id", "a"]},
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
            write_recipes=custom_write_recipes,
        )
    assert "A" in custom_write_recipes
    assert "P" not in custom_write_recipes


def test_composed_shaped_passthrough_recipe_is_input_sliced_and_preserves_shaped_columns(
    project: Path, tmp_path: Path, store: NodeSnapshotStore
) -> None:
    from haute._chunked_writes import WriteRecipe, part_paths, scan_parts, sliceable, write_parts
    from haute.executor import _compile_preamble, _pipeline_dir

    p_config = {
        "selected_columns": ["b", "id", "a"],
        "column_renames": {"b": "b_renamed"},
    }
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0)")),
            ("P", NodeType.MODELLING, p_config),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('a') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_src", source="src", target="A"),
            GraphEdge(id="e_ap", source="A", target="P"),
            GraphEdge(id="e_pj", source="P", target="J", targetHandle="base"),
            GraphEdge(id="e_oj", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e_jb", source="J", target="B"),
            GraphEdge(id="e_bt", source="B", target="T"),
        ],
    )
    custom_write_recipes: dict[str, WriteRecipe] = {}
    calls: Counter[str] = Counter()
    with _planned(
        graph,
        store,
        target="T",
        required={"T": ["id", "a", "d"], "P": ["b_renamed", "id", "a"]},
    ) as (
        plan,
        context,
        _execute,
    ):
        outputs, *_ = execute_lazy_graph(
            graph,
            _counting_build(calls),
            target_node_id="T",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source="live",
            enforce_contracts=True,
            required_columns_by_node={"T": ["id", "a", "d"], "P": ["b_renamed", "id", "a"]},
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
            write_recipes=custom_write_recipes,
            preserve_node_ids={"P"},
        )
    assert "P" in custom_write_recipes
    recipe = custom_write_recipes["P"]
    p_frame = outputs["P"]

    assert sliceable(recipe.input) is True
    assert sliceable(p_frame) is False

    target_dir = tmp_path / "shaped_passthrough_parts"
    target_dir.mkdir()
    res = write_parts(target_dir, p_frame, recipe=recipe, chunk_rows=80)
    assert res.strategy == "input_sliced"
    assert res.input_slices == 3
    assert res.chunks == 3
    assert len(res.parts) == 3
    assert res.native_reason is None
    assert res.blocking_operator is None

    written_df = scan_parts(part_paths(target_dir)).collect()
    native_df = p_frame.collect()
    assert_frame_equal(written_df, native_df)
    assert written_df.columns == ["b_renamed", "id", "a"]
    assert native_df.columns == ["b_renamed", "id", "a"]


def test_two_hop_passthrough_chain_recipe_over_filtered_parent_is_input_sliced_and_equals_native(
    project: Path, tmp_path: Path, store: NodeSnapshotStore
) -> None:
    from haute._chunked_writes import WriteRecipe, part_paths, scan_parts, sliceable, write_parts
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.filter(pl.col('a') >= 0)")),
            ("P1", NodeType.MODELLING, {}),
            ("P2", NodeType.MODELLING, {}),
            (
                "J",
                NodeType.EDGE_JOIN,
                {
                    "how": "left",
                    "on": "id",
                    "selected_columns": ["id", "a", "d"],
                },
            ),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('a') >= 0)")),
            ("T", NodeType.MODELLING, {}),
        ],
        [
            GraphEdge(id="e_src", source="src", target="A"),
            GraphEdge(id="e_ap1", source="A", target="P1"),
            GraphEdge(id="e_p1p2", source="P1", target="P2"),
            GraphEdge(id="e_p2j", source="P2", target="J", targetHandle="base"),
            GraphEdge(id="e_oj", source="other", target="J", targetHandle="join"),
            GraphEdge(id="e_jb", source="J", target="B"),
            GraphEdge(id="e_bt", source="B", target="T"),
        ],
    )
    custom_write_recipes: dict[str, WriteRecipe] = {}
    calls: Counter[str] = Counter()
    with _planned(
        graph,
        store,
        target="T",
        required={"T": ["id", "a", "d"], "P2": ["a", "id"]},
    ) as (
        plan,
        context,
        _execute,
    ):
        outputs, *_ = execute_lazy_graph(
            graph,
            _counting_build(calls),
            target_node_id="T",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source="live",
            enforce_contracts=True,
            required_columns_by_node={"T": ["id", "a", "d"], "P2": ["a", "id"]},
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
            write_recipes=custom_write_recipes,
            preserve_node_ids={"P2"},
        )
    assert "P2" in custom_write_recipes
    recipe = custom_write_recipes["P2"]
    p2_frame = outputs["P2"]

    assert sliceable(recipe.input) is True
    assert sliceable(p2_frame) is False
    # The input to the grandchild recipe is the original scan, not either pass-through's output.
    assert "SCAN" in recipe.input.explain()

    target_dir = tmp_path / "chained_passthrough_parts"
    target_dir.mkdir()
    res = write_parts(target_dir, p2_frame, recipe=recipe, chunk_rows=80)
    assert res.strategy == "input_sliced"
    assert res.input_slices == 3
    assert res.chunks == 3
    assert len(res.parts) == 3
    assert res.native_reason is None
    assert res.blocking_operator is None

    written_df = scan_parts(part_paths(target_dir)).collect()
    native_df = p2_frame.collect()
    assert_frame_equal(written_df, native_df)
