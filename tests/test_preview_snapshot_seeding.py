"""Previews seed from and capture into shared snapshots (CACHE-S09)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from haute._data_points import DataPointResolver
from haute._execute_lazy import EagerResult, _execute_eager_core
from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotSlot, NodeSnapshotStore
from haute._seed_plans import SeedPlan, SeedPlanRequest, open_seed_plan
from haute._source_cache import SourceCacheIdentity
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

ALL = NodeSnapshotColumns.all()
_ROWS = 100


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _parquet(path: Path) -> dict[str, Any]:
    return {"inputType": "file", "format": "parquet", "mode": "scan", "path": str(path)}


def _code(code: str) -> dict[str, Any]:
    return {"code": code}


def _graph(
    project: Path,
    nodes: list[tuple[str, NodeType, dict[str, Any]]],
    edges: list[tuple[str, str]],
) -> PipelineGraph:
    return PipelineGraph(
        nodes=[_node(*spec) for spec in nodes],
        edges=[
            GraphEdge(id=f"e{i}", source=source, target=target)
            for i, (source, target) in enumerate(edges)
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
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


def _join_graph(project: Path) -> PipelineGraph:
    """``policies + claims → join → banding``."""
    return _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("claims", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("join", NodeType.POLARS, _code("df = policies.join(claims, on='id', how='left')")),
            (
                "banding",
                NodeType.POLARS,
                _code("df = join.with_columns((pl.col('a') * 2).alias('band'))"),
            ),
        ],
        [("policies", "join"), ("claims", "join"), ("join", "banding")],
    )


def _context() -> ExecutionContext:
    """An admitted preview context with a fixed budget, independent of real admission."""
    limit = 1024**3
    profile = ExecutionProfile.PREVIEW_EAGER
    return ExecutionContext(
        operation="preview_seeding_test",
        profile=profile,
        memory_limit_bytes=limit,
        memory_baseline_bytes=0,
        rss_limit_bytes=limit,
        admission=ExecutionAdmission(
            operation="preview_seeding_test",
            profile=profile,
            memory_limit_bytes=limit,
            rss_at_admission_bytes=0,
            rss_limit_bytes=limit,
            headroom_bytes=limit,
            config_key="test",
        ),
        memory_sampler=lambda: 0,
    )


@dataclass
class Preview:
    result: EagerResult
    metrics: dict[str, Any]
    built: Counter[str]
    called: Counter[str]
    plan: SeedPlan

    def rows(self, node_id: str) -> pl.DataFrame:
        frame = self.result.outputs[node_id]
        assert isinstance(frame, pl.DataFrame), (node_id, self.result.errors)
        return frame

    @property
    def seeds(self) -> dict[str, str]:
        return {
            seed["node_id"]: seed["generation_id"] for seed in self.metrics["shared_snapshot_seeds"]
        }

    @property
    def captures(self) -> dict[str, dict[str, Any]]:
        return {capture["node_id"]: capture for capture in self.metrics["shared_snapshot_captures"]}


def _counting_build(built: Counter[str], called: Counter[str]) -> Callable[..., Any]:
    from haute.executor import _build_node_fn

    def build(node: GraphNode, **kwargs: Any) -> Any:
        built[node.id] += 1
        name, fn, is_source = _build_node_fn(node, **kwargs)

        def counted(*args: Any, **fn_kwargs: Any) -> Any:
            called[node.id] += 1
            return fn(*args, **fn_kwargs)

        return name, counted, is_source

    return build


@contextmanager
def _previewing(
    graph: PipelineGraph,
    store: NodeSnapshotStore,
    target: str,
    *,
    row_limit: int | None = 3,
    source: str = "live",
    required: dict[str, list[str]] | None = None,
    preplanned: bool = False,
    materialize_all: bool = False,
) -> Iterator[Preview]:
    import haute.execution as execution_facade
    from haute.executor import _compile_preamble, _pipeline_dir

    context = _context()
    if preplanned:
        from haute._native_memory_limit import native_memory_backend_scope

        # The preview route plans its public strategy for the caller's demand
        # before executing, as execute_graph does, inside a capped worker.
        with native_memory_backend_scope("rlimit"):
            execution_facade.plan_execution_strategy(
                execution_facade.ProjectionRequest(
                    graph=graph,
                    target_node_id=target,
                    profile=context.profile,
                    required_columns_by_node=required,
                    source=source,
                ),
                execution_context=context,
            )
    request = SeedPlanRequest(
        graph=graph,
        target_node_id=target,
        source=source,
        profile=ExecutionProfile.PREVIEW_EAGER,
        required_columns_by_node=required,
    )
    with open_seed_plan(request, store=store, execution_context=context) as plan:
        built: Counter[str] = Counter()
        called: Counter[str] = Counter()
        result = _execute_eager_core(
            graph,
            _counting_build(built, called),
            target_node_id=target,
            row_limit=row_limit,
            swallow_errors=True,
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source=source,
            enforce_contracts=True,
            required_columns_by_node=required,
            materialize_node_ids=None if materialize_all else {target},
            execution_context=context,
            snapshot_plan=plan,
        )
        yield Preview(
            result=result,
            metrics=context.metrics_payload(status="completed"),
            built=built,
            called=called,
            plan=plan,
        )


def _preview(graph: PipelineGraph, store: NodeSnapshotStore, target: str, **kwargs: Any) -> Preview:
    with _previewing(graph, store, target, **kwargs) as preview:
        return preview


def _identity(
    store: NodeSnapshotStore, graph: PipelineGraph, node_id: str, source: str = "live"
) -> SourceCacheIdentity:
    resolver = DataPointResolver(graph, source=source, store=store)
    return resolver.node_output_slot(node_id).identity(resolver.node_output_signature(node_id))


def _publish(
    store: NodeSnapshotStore, graph: PipelineGraph, node_id: str, frame: pl.DataFrame
) -> str:
    identity = _identity(store, graph, node_id)
    artifact = store.stage_node_output(identity)
    frame.write_parquet(artifact.data_path)
    with store.publish_node_output(
        identity,
        artifact,
        columns=ALL,
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    ) as publication:
        assert publication.generation is not None
        return publication.generation.generation_id


def _latest(store: NodeSnapshotStore, graph: PipelineGraph, node_id: str) -> pl.DataFrame | None:
    latest = store.latest_generation(_identity(store, graph, node_id))
    return None if latest is None else pl.read_parquet(latest.generation.data_path)


# ---------------------------------------------------------------------------
# The eager engine under a plan
# ---------------------------------------------------------------------------


def test_eager_seeded_node_reads_its_generation_and_builds_nothing_above(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project)
    seeded = pl.DataFrame({"id": [1, 2, 3], "a": [7, 8, 9], "d": [0.1, 0.2, 0.3]})
    generation = _publish(store, graph, "join", seeded)

    preview = _preview(graph, store, "banding")

    assert preview.seeds == {"join": generation}
    assert preview.rows("banding")["band"].to_list() == [14, 16, 18]
    # Nothing at or above the seed is built, let alone run.
    assert set(preview.built) == {"banding"}
    assert preview.result.order == ["join", "banding"]
    assert preview.captures == {}


@pytest.fixture()
def sources_gone_after_capture(project: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Remove both sources once a capture returns, so nothing below can recompute it."""
    from haute._execute_lazy import _PlannedCaptures

    captured: list[str] = []
    capture = _PlannedCaptures.capture

    def capture_then_remove_sources(self: Any, node_id: str, *args: Any, **kwargs: Any) -> Any:
        frame = capture(self, node_id, *args, **kwargs)
        for name in ("policies.parquet", "claims.parquet"):
            (project / name).replace(project / f"{name}.gone")
        captured.append(node_id)
        return frame

    monkeypatch.setattr(_PlannedCaptures, "capture", capture_then_remove_sources)
    return captured


def test_eager_capture_is_full_data_under_a_row_limit(
    project: Path, store: NodeSnapshotStore, sources_gone_after_capture: list[str]
) -> None:
    graph = _join_graph(project)
    # Signed while the sources exist: removing them changes the signature.
    identity = _identity(store, graph, "join")

    preview = _preview(graph, store, "banding", row_limit=3)

    assert sources_gone_after_capture == ["join"]
    assert preview.captures["join"]["outcome"] == "published"
    latest = store.latest_generation(identity)
    assert latest is not None
    captured = pl.read_parquet(latest.generation.data_path)
    assert captured.height == _ROWS
    assert sorted(captured["id"].to_list()) == list(range(_ROWS))
    # The limited rows are read from what was captured.
    expected = captured.head(3).with_columns((pl.col("a") * 2).alias("band"))
    assert preview.rows("banding").equals(expected)
    assert preview.called["join"] == 1


def _counted_points(project: Path) -> PipelineGraph:
    pl.DataFrame({"x": list(range(_ROWS))}).write_parquet(project / "points.parquet")
    return _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "points.parquet")),
            ("P", NodeType.POLARS, _code("df = src.with_columns(pl.col('x').alias('x'))")),
            ("F", NodeType.POLARS, _code("df = P.filter(pl.col('x') >= 50)")),
            ("S", NodeType.POLARS, _code("df = P.select(pl.col('x').sum())")),
        ],
        [("src", "P"), ("P", "F"), ("P", "S")],
    )


def test_limit_boundary_filter_and_sum_over_a_seed(project: Path, store: NodeSnapshotStore) -> None:
    graph = _counted_points(project)
    generation = _publish(store, graph, "P", pl.DataFrame({"x": list(range(_ROWS))}))

    filtered = _preview(graph, store, "F", row_limit=3)
    summed = _preview(graph, store, "S", row_limit=3)

    assert filtered.seeds == {"P": generation}
    assert filtered.rows("F")["x"].to_list() == [50, 51, 52]
    assert summed.seeds == {"P": generation}
    assert summed.rows("S")["x"].to_list() == [4950]


def test_eager_quota_rejected_capture_continues_from_own_artifact(
    project: Path, sources_gone_after_capture: list[str]
) -> None:
    graph = _join_graph(project)
    identity = _identity(NodeSnapshotStore(project), graph, "join")
    full = NodeSnapshotStore(project, max_generations=1)
    filler = NodeSnapshotSlot(str(project / "other.py"), "filler", "live", "bounded").identity(
        "filler-signature"
    )
    artifact = full.stage_node_output(filler)
    pl.DataFrame({"a": [1]}).write_parquet(artifact.data_path)
    full.publish_node_output(
        filler,
        artifact,
        columns=ALL,
        dependencies={},
        explicit=True,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ).close()

    preview = _preview(graph, full, "banding")

    assert preview.captures["join"]["outcome"] == "quota"
    assert {"code": "snapshot_capture_skipped", "node_id": "join", "reason": "quota"} in (
        preview.metrics["warnings"]
    )
    # With both sources gone, these rows can only have come from the artifact.
    assert sources_gone_after_capture == ["join"]
    assert preview.result.errors == {}
    rows = preview.rows("banding")
    assert rows.height == 3
    assert rows["band"].to_list() == [value * 2 for value in rows["a"].to_list()]
    assert preview.called["join"] == 1
    assert full.latest_generation(identity) is None


def test_filter_and_rename_capture_nothing(project: Path, store: NodeSnapshotStore) -> None:
    graph = _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("F", NodeType.POLARS, _code("df = policies.filter(pl.col('a') > 10)")),
            ("R", NodeType.POLARS, _code("df = F.select(pl.col('a').alias('renamed'))")),
        ],
        [("policies", "F"), ("F", "R")],
    )

    preview = _preview(graph, store, "R")

    assert preview.plan.decision.captures == {}
    assert preview.captures == {}
    assert preview.rows("R")["renamed"].to_list() == [11, 12, 13]
    assert _latest(store, graph, "F") is None
    assert _latest(store, graph, "R") is None


class _TenTimes:
    """A deterministic model that scores ten times its feature."""

    def predict(self, features: Any) -> Any:
        column = features["feature"] if hasattr(features, "__getitem__") else features
        return np.asarray(column, dtype="float64") * 10.0


def test_join_below_limited_model_score_captures_the_full_scored_output(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _mlflow_io
    from haute.modelling._feature_contract import build_contract, save_contract

    monkeypatch.setattr(
        _mlflow_io,
        "load_mlflow_model",
        lambda *_args, **_kwargs: _mlflow_io.ScoringModel(
            _TenTimes(), ["feature"], flavor="pyfunc"
        ),
    )
    pl.DataFrame(
        {"quote_id": [f"q{index}" for index in range(5)], "feature": [1.0, 2.0, 3.0, 4.0, 5.0]}
    ).write_parquet(project / "scoring.parquet")
    pl.DataFrame(
        {"quote_id": [f"q{index}" for index in range(5)], "region": list("abcde")}
    ).write_parquet(project / "regions.parquet")
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
    graph = _graph(
        project,
        [
            ("scoring", NodeType.DATA_INPUT, _parquet(project / "scoring.parquet")),
            ("regions", NodeType.DATA_INPUT, _parquet(project / "regions.parquet")),
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
                },
            ),
            ("J", NodeType.POLARS, _code("df = M.join(regions, on='quote_id', how='left')")),
            ("B", NodeType.POLARS, _code("df = J.with_columns(pl.lit(1).alias('band'))")),
        ],
        [("scoring", "M"), ("M", "J"), ("regions", "J"), ("J", "B")],
    )

    preview = _preview(graph, store, "B", row_limit=2)

    assert "M" not in preview.plan.decision.captures
    assert preview.captures["J"]["outcome"] == "published"
    captured = _latest(store, graph, "J")
    assert captured is not None
    # Every row was scored, not just the two the preview shows.
    assert sorted(captured["prediction"].to_list()) == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert preview.rows("B").height == 2


def test_capture_store_error_propagates_instead_of_a_node_error(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _join_graph(project)

    def denied(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("the snapshot store's metadata is not writable")

    monkeypatch.setattr(NodeSnapshotStore, "publish_node_output", denied)

    with pytest.raises(PermissionError, match="not writable"):
        _preview(graph, store, "banding")


def test_a_node_failing_while_it_is_captured_is_that_nodes_error(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("claims", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            (
                "join",
                NodeType.POLARS,
                # Fails only while rows are computed, not while planning.
                _code(
                    "df = policies.join(claims, on='id', how='left')"
                    ".with_columns((pl.col('a') - 50).cast(pl.UInt8, strict=True).alias('u'))"
                ),
            ),
            ("banding", NodeType.POLARS, _code("df = join.with_columns(pl.lit(1).alias('band'))")),
        ],
        [("policies", "join"), ("claims", "join"), ("join", "banding")],
    )

    preview = _preview(graph, store, "banding")

    assert "join" in preview.result.errors
    assert "Upstream node(s) failed" in preview.result.errors["banding"]
    assert _latest(store, graph, "join") is None


def test_pass_through_under_a_plan_reads_only_its_selected_edge(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("claims", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("X", NodeType.POLARS, _code("df = policies.with_columns(pl.lit(1).alias('x'))")),
            ("Y", NodeType.POLARS, _code("df = claims.with_columns(pl.lit(2).alias('y'))")),
            ("T", NodeType.MODELLING, {}),
        ],
        [("policies", "X"), ("claims", "Y"), ("X", "T"), ("Y", "T")],
    )

    preview = _preview(graph, store, "T")

    assert preview.rows("T").columns == ["id", "a", "x"]
    # The unselected input is neither built nor run for the pass-through.
    assert "Y" not in preview.built
    assert "claims" not in preview.built


def test_a_pass_through_selecting_its_second_input_reads_it(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _graph(
        project,
        [
            ("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            ("claims", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
            ("X", NodeType.POLARS, _code("df = policies.with_columns(pl.lit(1).alias('x'))")),
            ("Y", NodeType.POLARS, _code("df = claims.with_columns(pl.lit(2).alias('y'))")),
            ("OPT", NodeType.OPTIMISER, {"data_input": "Y"}),
        ],
        [("policies", "X"), ("claims", "Y"), ("X", "OPT"), ("Y", "OPT")],
    )

    preview = _preview(graph, store, "OPT")

    assert preview.result.errors == {}
    assert preview.rows("OPT").columns == ["id", "d", "y"]
    assert "X" not in preview.built


def test_a_capture_writes_the_columns_its_generation_keeps(
    project: Path, store: NodeSnapshotStore
) -> None:
    """A preview demanding ``a`` widens a ``join`` generation holding ``d``."""
    from haute._seed_plans import SeedPlanRequest

    graph = _join_graph(project)
    identity = _identity(store, graph, "join")
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"id": [0], "d": [0.0]}).write_parquet(artifact.data_path)
    store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.of(["id", "d"]),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    ).close()
    required = {"banding": ["band"]}
    from haute.executor import _compile_preamble, _pipeline_dir

    context = _context()
    request = SeedPlanRequest(
        graph=graph,
        target_node_id="banding",
        source="live",
        profile=ExecutionProfile.PREVIEW_EAGER,
        required_columns_by_node=required,
    )
    with open_seed_plan(request, store=store, execution_context=context) as plan:
        assert plan.decision.seeds == {}
        result = _execute_eager_core(
            graph,
            _counting_build(Counter(), Counter()),
            target_node_id="banding",
            row_limit=3,
            swallow_errors=True,
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph)),
            source="live",
            required_columns_by_node=required,
            materialize_node_ids={"banding"},
            execution_context=context,
            snapshot_plan=plan,
        )
    assert result.errors == {}
    widened = store.latest_generation(identity)
    assert widened is not None
    # The widened generation keeps ``d`` beside what this preview read.
    assert widened.columns.names is not None
    assert {"id", "a", "d"} <= widened.columns.names
    assert widened.generation.metadata.row_count == _ROWS
    # And the caller still collects only its own demand.
    target = result.outputs["banding"]
    assert isinstance(target, pl.DataFrame)
    assert target.columns == ["band"]


def _publish_narrow(
    store: NodeSnapshotStore, graph: PipelineGraph, node_id: str, frame: pl.DataFrame
) -> SourceCacheIdentity:
    identity = _identity(store, graph, node_id)
    artifact = store.stage_node_output(identity)
    frame.write_parquet(artifact.data_path)
    store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.of(frame.columns),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
    ).close()
    return identity


def test_a_captured_target_collects_only_what_the_caller_asked_for(
    project: Path, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project)
    identity = _publish_narrow(store, graph, "join", pl.DataFrame({"id": [0], "d": [0.0]}))

    preview = _preview(graph, store, "join", required={"join": ["a"]})

    assert preview.captures["join"]["outcome"] == "published"
    assert preview.rows("join").columns == ["a"]
    widened = store.latest_generation(identity)
    assert widened is not None and widened.columns.names is not None
    assert {"id", "a", "d"} <= widened.columns.names


def test_api_ports_load_the_negotiated_demand_under_a_preplanned_strategy(
    project: Path, store: NodeSnapshotStore
) -> None:
    import json

    from haute._json_flatten import _json_cache_dir
    from haute._json_shred._cache import build_per_port_cache

    data_path = project / "records.json"
    data_path.write_text(
        json.dumps(
            [
                {"policy_id": index, "premium": index * 10, "region": f"r{index}"}
                for index in range(5)
            ]
        ),
        encoding="utf-8",
    )

    def column(name: str, kind: str) -> dict[str, Any]:
        return {"name": name, "path": f"$[:].{name}", "type": kind, "selected": True}

    config = {
        "path": str(data_path),
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    column("policy_id", "int"),
                    column("premium", "int"),
                    column("region", "str"),
                ],
            }
        ],
    }
    build_per_port_cache(data_path, config, _json_cache_dir(data_path, "working"))
    graph = PipelineGraph(
        nodes=[
            _node("api", NodeType.API_INPUT, config),
            _node("G", NodeType.POLARS, _code("df = policies.sort('premium')")),
        ],
        edges=[GraphEdge(id="p", source="api", target="G", sourceHandle="policies")],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )
    identity = _publish_narrow(
        store, graph, "G", pl.DataFrame({"policy_id": [0], "region": ["r0"]})
    )

    # The caller's strategy loads only ``premium`` from the port; the capture
    # also has to keep the columns its generation already holds.
    preview = _preview(graph, store, "G", required={"G": ["premium"]}, preplanned=True)

    assert preview.result.errors == {}
    assert preview.captures["G"]["outcome"] == "published"
    widened = store.latest_generation(identity)
    assert widened is not None and widened.columns.names is not None
    assert {"policy_id", "premium", "region"} <= widened.columns.names
    assert preview.rows("G").columns == ["premium"]


def test_an_unlimited_full_materialisation_keeps_the_negotiated_columns(
    project: Path, store: NodeSnapshotStore
) -> None:
    pl.DataFrame(
        {"id": list(range(_ROWS)), "a": list(range(_ROWS)), "d": [0.5] * _ROWS}
    ).write_parquet(project / "wide.parquet")
    graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "wide.parquet")),
            ("G", NodeType.POLARS, _code("df = src.sort('a')")),
        ],
        [("src", "G")],
    )
    identity = _publish_narrow(store, graph, "G", pl.DataFrame({"id": [0], "d": [0.0]}))

    # Every node is collected in full: ``src`` collects the caller's ``a``, but
    # ``G`` below it must still capture ``id`` and ``d`` for its generation.
    preview = _preview(
        graph, store, "G", row_limit=None, required={"G": ["a"]}, materialize_all=True
    )

    assert preview.result.errors == {}
    assert preview.captures["G"]["outcome"] == "published"
    widened = store.latest_generation(identity)
    assert widened is not None and widened.columns.names is not None
    assert {"id", "a", "d"} <= widened.columns.names
    assert widened.generation.metadata.row_count == _ROWS
    assert preview.rows("G").columns == ["a"]
