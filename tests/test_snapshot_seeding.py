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
from haute._data_points import DataPointResolver
from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._execution_schemas import ExecutionMetricsPayload
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._seed_plans import SeedPlan, SeedPlanRequest, open_seed_plan
from haute._source_cache import SourceCacheCorruptError, SourceCacheIdentity
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import (
    ContractMismatchError,
    GroupByExecutionUnsupportedError,
    SnapshotPlanInputsChangedError,
)
from haute.execution import execute_lazy_graph

ALL = NodeSnapshotColumns.all()
_ROWS = 200


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


def _join_graph(project: Path) -> PipelineGraph:
    """``src → A → J ← other``, ``J → B → T``."""
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "quotes.parquet")),
            ("other", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("J", NodeType.POLARS, _code("df = A.join(other, on='id', how='left')")),
            ("B", NodeType.POLARS, _code("df = J.filter(pl.col('a') >= 0)")),
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

        return name, counted, is_source

    return build


def _request(
    graph: PipelineGraph,
    target: str,
    *,
    required: dict[str, Any] | None,
    profile: ExecutionProfile,
    **fields: Any,
) -> SeedPlanRequest:
    return SeedPlanRequest(
        graph=graph,
        target_node_id=target,
        source="live",
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
    **fields: Any,
) -> Iterator[tuple[SeedPlan, ExecutionContext, Callable[[], tuple[pl.LazyFrame, Counter[str]]]]]:
    """Open a plan and yield a callable that executes the graph under it."""
    from haute.executor import _compile_preamble, _pipeline_dir

    context = context if context is not None else _context(profile)
    request = _request(graph, target, required=required, profile=profile, **fields)
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
                source="live",
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
    **fields: Any,
) -> RunResult:
    with _planned(
        graph, store, target, required=required, profile=profile, context=context, **fields
    ) as (
        _plan,
        context,
        execute,
    ):
        output, calls = execute()
        frame = output.collect()
    return RunResult(frame, context.metrics_payload(status="completed"), calls)


def _identity(store: NodeSnapshotStore, graph: PipelineGraph, node_id: str) -> SourceCacheIdentity:
    resolver = DataPointResolver(graph, source="live", store=store)
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


# ---------------------------------------------------------------------------
# Seeding and capture
# ---------------------------------------------------------------------------


def test_seeded_rerun_builds_nothing_upstream(project: Path, store: NodeSnapshotStore) -> None:
    graph = _join_graph(project)
    first = _run(graph, store, required={"T": ["a", "d"]})

    assert set(first.captures) == {"A", "J", "B"}
    assert {capture["outcome"] for capture in first.captures.values()} == {"published"}
    assert first.seeds == {}
    assert first.calls["src"] == 1

    second = _run(graph, store, required={"T": ["a", "d"]})

    assert set(second.seeds) == {"B"}
    assert second.captures == {}
    assert not +second.calls
    assert_frame_equal(second.frame, first.frame)


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
            ("X", NodeType.POLARS, _code("df = src.with_columns((pl.col('c') + 1).alias('c1'))")),
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
    pl.read_parquet(project / "quotes.parquet").select("id", "c").write_parquet(artifact.data_path)
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
        pl.DataFrame({"a": [1]}).write_parquet(artifact.data_path)
        filler.publish_node_output(
            identity,
            artifact,
            columns=ALL,
            dependencies={},
            explicit=True,
            profile=ExecutionProfile.NODE_SNAPSHOT,
        ).close()
    full = NodeSnapshotStore(project, max_generations=2)

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
                _code("df = src.with_columns(pl.lit(R).alias('r'))"),
            ),
            ("B", NodeType.POLARS, _code("df = A.select('id', pl.col('r').alias('rb'))")),
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
        ).write_parquet(artifact.data_path)
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
            ("B", NodeType.POLARS, _code("df = A.select('id', pl.col('r').alias('rb'))")),
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
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("G", NodeType.POLARS, _code("df = A.sort('a')")),
            ("X", NodeType.POLARS, _code("df = G.filter(pl.col('a') >= 0)")),
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

    assert set(warm.seeds) == {"B"}
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
            ("A", NodeType.POLARS, _code("df = src.with_columns((pl.col('a') * 2).alias('a2'))")),
            ("B", NodeType.POLARS, _code("df = other.with_columns(pl.lit(1).alias('one'))")),
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
            ("X", NodeType.POLARS, _code("df = S.filter(pl.col('a') >= 0)")),
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
        artifact.data_path
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
                    "df = df.with_columns((pl.col('a') + 1).alias('a1'))",
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


def test_a_source_capture_whose_input_changes_before_publishing_keeps_its_data(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _captured_source_graph(project)

    def rewrite_source() -> None:
        pl.DataFrame({"id": [0], "a": [1], "b": [2], "c": [3]}).write_parquet(
            project / "quotes.parquet"
        )

    _pause_at(monkeypatch, "src", rewrite_source)
    run = _run(graph, store, required={"T": ["a1"]})

    assert run.captures["src"]["outcome"] == "superseded"
    assert store.latest_generation(_identity(store, graph, "src")) is None
    assert run.frame.height == _ROWS


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
# Failures and guards
# ---------------------------------------------------------------------------


def test_best_effort_capture_column_unavailable_is_dropped(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project)
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
    graph = _join_graph(project)
    _run(graph, store, required={"T": ["a"]})
    b_identity = _identity(store, graph, "B")

    def corrupt_b() -> None:
        latest = store.latest_generation(b_identity)
        assert latest is not None
        latest.generation.data_path.write_bytes(b"corrupt")

    _pause_at(monkeypatch, "B", corrupt_b)
    with pytest.raises(SourceCacheCorruptError):
        _run(graph, NodeSnapshotStore(project), required={"T": ["a", "b"]})
    assert not _staging_dirs(store)


def test_inputs_changed_before_collection_fails(project: Path, store: NodeSnapshotStore) -> None:
    graph = _join_graph(project)
    with _planned(graph, store, required={"T": ["a"]}) as (_plan, _context, execute):
        pl.DataFrame({"id": [0], "a": [1], "b": [2], "c": [3]}).write_parquet(
            project / "quotes.parquet"
        )
        with pytest.raises(SnapshotPlanInputsChangedError):
            execute()


def test_inputs_changed_before_publish_keeps_artifact(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _join_graph(project)

    def rewrite_source() -> None:
        pl.DataFrame({"id": [0], "a": [1], "b": [2], "c": [3]}).write_parquet(
            project / "quotes.parquet"
        )

    _pause_at(monkeypatch, "A", rewrite_source)
    run = _run(graph, store, required={"T": ["a"]})

    assert run.captures["A"]["outcome"] == "superseded"
    assert {"code": "snapshot_capture_superseded", "node_id": "A", "reason": None} in run.metrics[
        "warnings"
    ]
    assert store.latest_generation(_identity(store, graph, "A")) is None
    assert run.frame.height == _ROWS


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
    b_digest = _identity(store, graph, "B").digest
    capture = first.captures["B"]
    assert capture == {
        "node_id": "B",
        "identity_digest": b_digest,
        "kind": "consumed",
        "outcome": "published",
        "generation_id": capture["generation_id"],
        "columns": ["a"],
    }
    assert first.metrics["warnings"] == []
    assert second.metrics["shared_snapshot_seeds"] == [
        {
            "node_id": "B",
            "identity_digest": b_digest,
            "generation_id": capture["generation_id"],
            "columns": ["a"],
        }
    ]


def test_plan_is_exclusive_with_checkpoint_dir_and_cache_request(
    project: Path, store: NodeSnapshotStore
) -> None:
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
        with pytest.raises(ValueError, match="replaces checkpoints"):
            execute_lazy_graph(graph, _build_node_fn, checkpoint_dir=project, **common)
        with pytest.raises(ValueError, match="replaces checkpoints"):
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
