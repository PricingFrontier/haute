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
from polars.testing import assert_frame_equal

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


def _context(profile: ExecutionProfile = ExecutionProfile.PREVIEW_EAGER) -> ExecutionContext:
    """An admitted context with a fixed budget, independent of real admission."""
    limit = 1024**3
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
    frame.write_parquet(artifact.part_path(0))
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
    return None if latest is None else latest.lazy_frame.collect()


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
    captured = latest.lazy_frame.collect()
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
    pl.DataFrame({"a": [1]}).write_parquet(artifact.part_path(0))
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
    pl.DataFrame({"id": [0], "d": [0.0]}).write_parquet(artifact.part_path(0))
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
    frame.write_parquet(artifact.part_path(0))
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


# ---------------------------------------------------------------------------
# Preview seeding, capture, cache, and response
# ---------------------------------------------------------------------------


@pytest.fixture()
def api(project: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from haute.executor import _preview_cache
    from haute.server import app

    _preview_cache.clear()
    yield TestClient(app)
    _preview_cache.clear()


@pytest.fixture()
def builds(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Every node the preview executor builds, by id."""
    import haute.executor as executor

    built: Counter[str] = Counter()
    real = executor._build_node_fn

    def counting(node: GraphNode, **kwargs: Any) -> Any:
        built[node.id] += 1
        return real(node, **kwargs)

    monkeypatch.setattr(executor, "_build_node_fn", counting)
    return built


def _post_preview(api: Any, graph: PipelineGraph, node_id: str, **fields: Any) -> dict[str, Any]:
    response = api.post(
        "/api/pipeline/preview",
        json={
            "graph": graph.model_dump(mode="json"),
            "node_id": node_id,
            "row_limit": 200,
            **fields,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok", body.get("error")
    return body


def _rows(body: dict[str, Any]) -> pl.DataFrame:
    return pl.DataFrame(body["preview"]).sort("id")


def _plan_of(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(entry["node_id"], entry["kind"]) for entry in body["seed_plan"]]


def _two_consumers(project: Path) -> PipelineGraph:
    """``policies + claims → join``, then ``join → banding`` and ``join → rated``."""
    graph = _join_graph(project)
    return graph.model_copy(
        update={
            "nodes": [
                *graph.nodes,
                _node(
                    "rated",
                    NodeType.POLARS,
                    _code("df = join.with_columns((pl.col('d') * 2).alias('r'))"),
                ),
            ],
            "edges": [*graph.edges, GraphEdge(id="r", source="join", target="rated")],
        }
    )


def test_first_preview_captures_join_and_returns_unadmitted_rows(project: Path, api: Any) -> None:
    from haute.executor import _preview_cache, execute_graph

    graph = _join_graph(project)
    unadmitted = execute_graph(
        graph, target_node_id="banding", row_limit=200, target_preview_only=True
    )
    _preview_cache.clear()

    body = _post_preview(api, graph, "banding")

    assert _plan_of(body) == [("join", "captured")]
    entry = body["seed_plan"][0]
    assert entry["node_label"] == "join"
    assert entry["columns"] is None
    assert entry["created_at"].endswith("+00:00")
    assert _rows(body).equals(pl.DataFrame(unadmitted["banding"].preview).sort("id"))


def test_second_preview_below_join_seeds_and_scans_no_source(
    project: Path, api: Any, builds: Counter[str]
) -> None:
    graph = _two_consumers(project)
    first = _post_preview(api, graph, "banding")
    builds.clear()

    second = _post_preview(api, graph, "rated")

    assert _plan_of(second) == [("join", "seeded")]
    assert second["seed_plan"][0]["generation_id"] == first["seed_plan"][0]["generation_id"]
    assert set(builds) == {"rated"}
    assert _rows(second).height == _ROWS


def test_join_target_preview_captures_and_collects_from_its_capture(
    project: Path, api: Any
) -> None:
    body = _post_preview(api, _join_graph(project), "join")

    assert _plan_of(body) == [("join", "captured")]
    assert _rows(body).height == _ROWS


def test_training_run_seeds_preview_capture(
    project: Path, api: Any, store: NodeSnapshotStore
) -> None:
    from haute.execution import execute_lazy_graph
    from haute.executor import _compile_preamble, _pipeline_dir

    graph = _join_graph(project)
    captured = _post_preview(api, graph, "banding")["seed_plan"][0]["generation_id"]

    context = _context(ExecutionProfile.TRAINING_PREP)
    request = SeedPlanRequest(
        graph=graph,
        target_node_id="banding",
        source="live",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    built: Counter[str] = Counter()
    with open_seed_plan(request, store=store, execution_context=context) as plan:
        assert {node: seed.generation_id for node, seed in plan.decision.seeds.items()} == {
            "join": captured
        }
        outputs, *_ = execute_lazy_graph(
            graph,
            _counting_build(built, Counter()),
            target_node_id="banding",
            preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
            or None,
            source="live",
            enforce_contracts=True,
            execution_context=context,
            prepare_inputs=False,
            snapshot_plan=plan,
        )
        frame = outputs["banding"].collect()
    assert frame.height == _ROWS
    assert not {"policies", "claims", "join"} & set(built)


def test_refreshed_join_misses_preview_cache(
    project: Path, api: Any, store: NodeSnapshotStore
) -> None:
    graph = _join_graph(project)
    _post_preview(api, graph, "banding")
    identity = _identity(store, graph, "join")
    artifact = store.stage_node_output(identity)
    frame = pl.DataFrame({"id": [1, 2], "a": [70, 80], "d": [0.1, 0.2]})
    frame.write_parquet(artifact.part_path(0))
    with store.publish_node_output(
        identity,
        artifact,
        columns=ALL,
        dependencies={},
        explicit=True,
        profile=ExecutionProfile.NODE_SNAPSHOT,
        refresh=True,
    ) as publication:
        assert publication.generation is not None
        refreshed = publication.generation.generation_id

    body = _post_preview(api, graph, "banding")

    assert _plan_of(body) == [("join", "seeded")]
    assert body["seed_plan"][0]["generation_id"] == refreshed
    assert _rows(body)["band"].to_list() == [140, 160]


def test_stale_join_is_not_seeded_and_is_recaptured(project: Path, api: Any) -> None:
    graph = _join_graph(project)
    first = _post_preview(api, graph, "banding")["seed_plan"][0]
    pl.DataFrame({"id": [0, 1], "a": [5, 6]}).write_parquet(project / "policies.parquet")

    body = _post_preview(api, graph, "banding")

    assert _plan_of(body) == [("join", "captured")]
    assert body["seed_plan"][0]["identity_digest"] != first["identity_digest"]
    assert _rows(body)["band"].to_list() == [10, 12]


@pytest.mark.parametrize("removal", ["clear", "evict"])
def test_capture_then_clear_or_evict_never_serves_the_cached_response(
    project: Path, api: Any, store: NodeSnapshotStore, builds: Counter[str], removal: str
) -> None:
    graph = _join_graph(project)
    j1 = _post_preview(api, graph, "banding")["seed_plan"][0]["generation_id"]
    builds.clear()
    repeat = _post_preview(api, graph, "banding")
    assert not builds, "the repeat preview is a backend cache hit"
    assert [entry["generation_id"] for entry in repeat["seed_plan"]] == [j1]

    identity = _identity(store, graph, "join")
    if removal == "clear":
        store.clear(identity)
    else:
        full = NodeSnapshotStore(project, max_generations=1)
        filler = NodeSnapshotSlot(str(project / "other.py"), "filler", "live", "bounded").identity(
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
        assert store.latest_generation(identity) is None
    builds.clear()

    after = _post_preview(api, graph, "banding")

    assert builds["join"] == 1
    assert _plan_of(after) == [("join", "captured")]
    assert after["seed_plan"][0]["generation_id"] != j1


def _grouped(project: Path) -> PipelineGraph:
    """``policies + claims → join → grouped (group-by) → shown``."""
    graph = _join_graph(project)
    return graph.model_copy(
        update={
            "nodes": [
                *graph.nodes,
                _node(
                    "grouped",
                    NodeType.POLARS,
                    _code("df = join.group_by('id').agg(pl.col('a').sum())"),
                ),
                _node(
                    "shown",
                    NodeType.POLARS,
                    _code("df = grouped.with_columns(pl.lit(1).alias('one'))"),
                ),
            ],
            "edges": [
                *graph.edges,
                GraphEdge(id="g", source="join", target="grouped"),
                GraphEdge(id="s", source="grouped", target="shown"),
            ],
        }
    )


def _preview_directly(graph: PipelineGraph, target: str) -> dict[str, Any]:
    from haute.executor import execute_graph

    return execute_graph(
        graph,
        target_node_id=target,
        row_limit=200,
        target_preview_only=True,
        include_schema_metadata=True,
        shared_snapshots=True,
    )


def test_cache_hit_corruption_propagates_without_executing(
    project: Path, api: Any, store: NodeSnapshotStore, builds: Counter[str]
) -> None:
    from haute._source_cache import SourceCacheCorruptError

    graph = _grouped(project)
    first = _post_preview(api, graph, "shown")
    assert _plan_of(first) == [("join", "captured"), ("grouped", "captured")]
    join = first["seed_plan"][0]
    # The entry is keyed by the plan a new request chooses — seeding ``grouped``
    # — and still lists ``join``, which that request never leases itself.
    identity = _identity(store, graph, "join")
    _corrupt_generation(store, identity, join["generation_id"])
    builds.clear()

    with pytest.raises(SourceCacheCorruptError):
        _preview_directly(graph, "shown")
    assert not builds


def test_cache_hit_permission_error_propagates_without_executing(
    project: Path,
    api: Any,
    store: NodeSnapshotStore,
    builds: Counter[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _grouped(project)
    join = _post_preview(api, graph, "shown")["seed_plan"][0]
    lease = NodeSnapshotStore.lease_generation

    def denied(self: NodeSnapshotStore, identity: Any, generation_id: str) -> Any:
        if generation_id == join["generation_id"]:
            raise PermissionError("the generation's metadata is not readable")
        return lease(self, identity, generation_id)

    monkeypatch.setattr(NodeSnapshotStore, "lease_generation", denied)
    builds.clear()

    with pytest.raises(PermissionError, match="not readable"):
        _preview_directly(graph, "shown")
    assert not builds


def _corrupt_generation(store: NodeSnapshotStore, identity: Any, generation_id: str) -> None:
    from haute._chunked_writes import part_paths

    gen_dir = store.inputs_root / identity.digest / "generations" / generation_id
    for part in part_paths(gen_dir):
        (gen_dir / part.name).write_bytes(b"corrupt")


def _with_extra(project: Path) -> PipelineGraph:
    """``policies + claims → join → banded``, where ``banded`` also reads ``extra``."""
    pl.DataFrame({"id": list(range(_ROWS)), "x": [1] * _ROWS}).write_parquet(
        project / "extra.parquet"
    )
    graph = _join_graph(project)
    return graph.model_copy(
        update={
            "nodes": [
                *graph.nodes,
                _node("extra", NodeType.DATA_INPUT, _parquet(project / "extra.parquet")),
                _node(
                    "banded",
                    NodeType.POLARS,
                    _code("df = pl.concat([join, extra.select('x')], how='horizontal')"),
                ),
            ],
            "edges": [
                *graph.edges,
                GraphEdge(id="b1", source="join", target="banded"),
                GraphEdge(id="b2", source="extra", target="banded"),
            ],
        }
    )


def _rewrite_extra(project: Path, value: int) -> None:
    pl.DataFrame({"id": list(range(_ROWS)), "x": [value] * _ROWS}).write_parquet(
        project / "extra.parquet"
    )


def test_input_change_after_capture_stores_nothing(
    project: Path, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._execute_lazy import _PlannedCaptures
    from haute.executor import _preview_cache

    graph = _with_extra(project)
    capture = _PlannedCaptures.capture

    def capture_then_rewrite(self: Any, node_id: str, *args: Any, **kwargs: Any) -> Any:
        frame = capture(self, node_id, *args, **kwargs)
        if node_id == "join":
            _rewrite_extra(project, 2)
        return frame

    monkeypatch.setattr(_PlannedCaptures, "capture", capture_then_rewrite)

    _post_preview(api, graph, "banded")

    # Its inputs moved while it ran: no key describes what it computed.
    assert len(_preview_cache) == 0


def test_input_change_after_the_recheck_keys_by_the_executed_identity(
    project: Path, api: Any, builds: Counter[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.executor as executor

    graph = _with_extra(project)
    resolve = executor.resolve_seed_plan
    rewritten: list[int] = []

    def rewrite_then_resolve(*args: Any, **kwargs: Any) -> Any:
        if not rewritten:
            _rewrite_extra(project, 2)
            rewritten.append(2)
        return resolve(*args, **kwargs)

    monkeypatch.setattr(executor, "resolve_seed_plan", rewrite_then_resolve)
    first = _post_preview(api, graph, "banded")
    assert pl.DataFrame(first["preview"])["x"].unique().to_list() == [1]
    assert len(executor._preview_cache) == 1
    builds.clear()

    second = _post_preview(api, graph, "banded")

    # Stored under the inputs it read, so a request reading the new ones misses.
    assert builds["banded"] == 1
    assert pl.DataFrame(second["preview"])["x"].unique().to_list() == [2]


def test_post_capture_plan_naming_an_unread_generation_stores_nothing(
    project: Path, api: Any, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.executor as executor

    graph = _join_graph(project)
    resolve = executor.resolve_seed_plan

    def refresh_then_resolve(*args: Any, **kwargs: Any) -> Any:
        # Another execution refreshes the join between this preview's capture
        # and its re-resolution: a new request would seed what it never read.
        identity = _identity(store, graph, "join")
        artifact = store.stage_node_output(identity)
        pl.DataFrame({"id": [1], "a": [9], "d": [0.9]}).write_parquet(artifact.part_path(0))
        store.publish_node_output(
            identity,
            artifact,
            columns=ALL,
            dependencies={},
            explicit=True,
            profile=ExecutionProfile.NODE_SNAPSHOT,
            refresh=True,
        ).close()
        return resolve(*args, **kwargs)

    monkeypatch.setattr(executor, "resolve_seed_plan", refresh_then_resolve)

    _post_preview(api, graph, "banding")

    assert len(executor._preview_cache) == 0


def test_partial_hit_under_captures_executes_as_a_miss(
    project: Path, api: Any, builds: Counter[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._seed_plans as seed_plans_module
    from haute.executor import _preview_cache

    # Every capture is refused for quota, so each preview plans the capture
    # again and stores under the key it computed before executing.
    full = NodeSnapshotStore(project, max_generations=1)
    filler = NodeSnapshotSlot(str(project / "other.py"), "filler", "live", "bounded").identity(
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
    monkeypatch.setattr(seed_plans_module, "_project_store", lambda: full)
    graph = _join_graph(project)
    first = _post_preview(api, graph, "banding")
    assert first["seed_plan"] == []
    assert len(_preview_cache) == 1
    ((key, entry),) = [(key, _preview_cache.get(key)) for key in list(_preview_cache._data)]
    assert entry is not None
    # A partial entry: it no longer holds the target, and it holds an output
    # an extension would carry over into what it stores.
    entry["eager_outputs"].pop("banding")
    entry["eager_outputs"]["stale"] = pl.DataFrame({"x": [1]})
    builds.clear()

    second = _post_preview(api, graph, "banding")

    assert builds["join"] == 1
    assert _rows(second).height == _ROWS
    stored = _preview_cache.get(key)
    assert stored is not None and "banding" in stored["eager_outputs"]
    # Executed as a miss: nothing of the partial entry survives.
    assert "stale" not in stored["eager_outputs"]


def test_undeclared_csv_api_input_preview_seeds_and_captures_nothing(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._execution_admission import create_admitted_execution_context
    from haute._native_memory_limit import native_memory_backend_scope
    from haute.executor import execute_graph

    pl.DataFrame({"id": list(range(_ROWS)), "a": list(range(_ROWS))}).write_csv(
        project / "policies.csv"
    )
    graph = _join_graph(project)
    graph = graph.model_copy(
        update={
            "nodes": [
                _node("policies", NodeType.API_INPUT, {"path": str(project / "policies.csv")})
                if node.id == "policies"
                else node
                for node in graph.nodes
            ],
            # An API Input's edge names its frame.
            "edges": [
                edge.model_copy(update={"sourceHandle": "policies"})
                if edge.source == "policies"
                else edge
                for edge in graph.edges
            ],
        }
    )
    context = create_admitted_execution_context(
        operation="preview_seeding_test", profile=ExecutionProfile.PREVIEW_EAGER
    )
    try:
        # The join over a CSV has no row-count estimate; the preview worker
        # runs it under its hard memory cap.
        with native_memory_backend_scope("rlimit"):
            results = execute_graph(
                graph,
                target_node_id="banding",
                row_limit=200,
                target_preview_only=True,
                execution_context=context,
                shared_snapshots=True,
            )
    finally:
        context.release_admission()

    assert results["banding"].status == "ok", results["banding"].error
    assert results["banding"].row_count == _ROWS
    assert context.preview_seed_plan == ()
    assert context.metrics_payload(status="completed")["shared_snapshot_captures"] == []
    assert store.latest_generation(_identity(store, graph, "join")) is None


def test_killed_preview_worker_leaves_no_staging(
    project: Path, api: Any, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.routes.pipeline as pipeline
    from haute._interactive_workers import InteractiveWorkerCrashedError

    graph = _join_graph(project)
    staged: list[Path] = []

    async def crashing_worker(function: Any, *args: Any, **kwargs: Any) -> Any:
        # The worker stages a capture under the route's token, then dies.
        *_rest, token = args
        identity = _identity(store, graph, "join")
        artifact = store.stage_node_output(identity, staging_token=token)
        artifact.part_path(0).write_bytes(b"partial")
        staged.append(artifact.part_path(0))
        raise InteractiveWorkerCrashedError(9)

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setattr(pipeline, "run_in_interactive_worker", crashing_worker)

    response = api.post(
        "/api/pipeline/preview",
        json={"graph": graph.model_dump(mode="json"), "node_id": "banding", "row_limit": 5},
    )

    assert response.status_code >= 500
    assert staged and not staged[0].exists()


def test_preview_inputs_lists_only_inputs_the_seeded_execution_reads(
    project: Path, api: Any, store: NodeSnapshotStore
) -> None:
    pl.DataFrame({"id": list(range(_ROWS)), "a": list(range(_ROWS))}).write_csv(
        project / "policies.csv"
    )
    graph = _join_graph(project)
    graph = graph.model_copy(
        update={
            "nodes": [
                _node(
                    "policies",
                    NodeType.DATA_INPUT,
                    {"inputType": "file", "format": "csv", "path": str(project / "policies.csv")},
                )
                if node.id == "policies"
                else node
                for node in graph.nodes
            ]
        }
    )

    def inputs() -> list[str]:
        response = api.post(
            "/api/pipeline/preview/inputs",
            json={"graph": graph.model_dump(mode="json"), "node_id": "banding"},
        )
        assert response.status_code == 200, response.text
        return response.json()["input_node_ids"]

    # ``claims`` is a direct Parquet scan: it has no snapshot to prepare.
    assert inputs() == ["policies"]
    _post_preview(api, graph, "banding")
    # The join is captured now, so the next preview reads no input at all.
    assert inputs() == []


def test_an_authored_graph_error_prepares_nothing_and_the_preview_reports_it(
    project: Path, api: Any
) -> None:
    graph = _join_graph(project)
    graph = graph.model_copy(
        update={"nodes": [*graph.nodes, _node("lonely", NodeType.EXPLORE, {})]}
    )

    inputs = api.post(
        "/api/pipeline/preview/inputs",
        json={"graph": graph.model_dump(mode="json"), "node_id": "lonely"},
    )
    assert inputs.status_code == 200, inputs.text
    assert inputs.json() == {"input_node_ids": []}

    preview = api.post(
        "/api/pipeline/preview",
        json={"graph": graph.model_dump(mode="json"), "node_id": "lonely", "row_limit": 5},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "error"
    assert "incoming edge" in preview.json()["error"]


def test_unused_unavailable_input_is_neither_listed_nor_fails_the_preview(
    project: Path, api: Any
) -> None:
    (project / "ragged.csv").write_text("id,a\n1,2\n3\n4,5,6\n", encoding="utf-8")
    graph = _join_graph(project)
    graph = graph.model_copy(
        update={
            "nodes": [
                *graph.nodes,
                _node(
                    "ragged",
                    NodeType.DATA_INPUT,
                    {"inputType": "file", "format": "csv", "path": str(project / "ragged.csv")},
                ),
                _node("elsewhere", NodeType.POLARS, _code("df = ragged.head(1)")),
            ],
            "edges": [*graph.edges, GraphEdge(id="x", source="ragged", target="elsewhere")],
        }
    )

    response = api.post(
        "/api/pipeline/preview/inputs",
        json={"graph": graph.model_dump(mode="json"), "node_id": "banding"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["input_node_ids"] == []
    assert _rows(_post_preview(api, graph, "banding")).height == _ROWS


def test_a_preview_seeding_nothing_is_keyed_like_one_without_a_plan(project: Path) -> None:
    import haute.execution as execution_facade

    graph = _join_graph(project)
    identity = execution_facade.lineage_runtime_input_identity(
        graph, target_node_id="banding", source="live"
    )

    def key(seed_plan_fingerprint: str | None, **kwargs: Any) -> str:
        return execution_facade.preview_lineage_cache_key(
            graph,
            target_node_id="banding",
            source="live",
            requested_columns=None,
            initial_column_limit=None,
            row_limit=3,
            port_label=None,
            enforce_contracts=True,
            materialisation_scope="target_only",
            seed_plan_fingerprint=seed_plan_fingerprint,
            **kwargs,
        )

    unplanned = key(None)
    assert key(None, runtime_input_identity=identity) == unplanned
    assert key("seed-plan:v1:a") not in {unplanned, key("seed-plan:v1:b")}
    assert identity.fingerprint({"k": 1}) == execution_facade.dataframe_graph_input_fingerprint(
        execution_facade._lineage_runtime_graph(
            graph, execution_facade.prepare_graph(graph, "banding", source="live")
        ),
        target_node_id=None,
        source="live",
        extra_fingerprints={"k": 1},
    )


def test_a_cached_entry_listing_a_cleared_generation_is_not_current(
    project: Path, store: NodeSnapshotStore
) -> None:
    from haute._seed_plans import ReadGeneration
    from haute.executor import _preview_entry_is_current

    graph = _join_graph(project)
    generation = _publish(store, graph, "join", pl.DataFrame({"id": [1], "a": [1], "d": [0.1]}))
    identity = _identity(store, graph, "join")
    listed = ReadGeneration(
        node_id="join",
        identity=identity,
        generation_id=generation,
        columns=ALL,
        created_at=0.0,
        kind="captured",
    )
    request = SeedPlanRequest(
        graph=graph,
        target_node_id="banding",
        source="live",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )

    with open_seed_plan(request, store=store, execution_context=_context()) as plan:
        assert _preview_entry_is_current({"seed_plan": (listed,)}, plan, graph, source="live")
    store.clear(identity)
    with open_seed_plan(request, store=store, execution_context=_context()) as plan:
        assert not _preview_entry_is_current({"seed_plan": (listed,)}, plan, graph, source="live")
        assert _preview_entry_is_current({"seed_plan": ()}, plan, graph, source="live")


def test_a_preview_seeded_below_unadmittable_work_is_still_admitted(
    project: Path, api: Any, store: NodeSnapshotStore
) -> None:
    # A sample has no row estimate: recomputing the group-by below it would be
    # refused here, where no hard worker cap bounds it. Seeded, it never runs.
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

    body = _post_preview(api, graph, "T")

    assert [(entry["node_id"], entry["generation_id"]) for entry in body["seed_plan"]] == [
        ("G", g1)
    ]
    assert body["row_count"] == 2


def _renamed_edge_join(project: Path, selected: list[str] | None) -> PipelineGraph:
    """``raw_rows`` left-joined to ``lookup_rows`` by an Edge Join that renames and selects."""
    pl.DataFrame(
        {"_id": [1, 2], "premium": [12.5, 25.0], "segment": ["North", "South"], "discard": [99, 88]}
    ).write_parquet(project / "columns.parquet")
    pl.DataFrame({"_id": [1, 2]}).write_parquet(project / "keys.parquet")
    config: dict[str, Any] = {
        "how": "left",
        "on": ["_id"],
        "column_renames": {"_id": "identifier", "segment": "region"},
    }
    if selected is not None:
        config["selected_columns"] = selected
    return PipelineGraph(
        nodes=[
            _node("raw_rows", NodeType.DATA_INPUT, _parquet(project / "columns.parquet")),
            _node("lookup_rows", NodeType.DATA_INPUT, _parquet(project / "keys.parquet")),
            _node("subject", NodeType.EDGE_JOIN, config),
        ],
        edges=[
            GraphEdge(id="e0", source="raw_rows", target="subject", targetHandle="base"),
            GraphEdge(id="e1", source="lookup_rows", target="subject", targetHandle="join"),
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )


def test_a_node_that_shapes_its_columns_is_seeded_and_reports_them_before_shaping(
    api: Any, project: Path
) -> None:
    graph = _renamed_edge_join(project, ["_id", "premium", "segment"])
    first = _post_preview(api, graph, "subject")
    assert _plan_of(first) == [("subject", "captured")]

    # Another request that misses the response cache: the capture recorded the
    # columns the node had before its own selection and renames — what its
    # Columns editor offers — so the node is read instead of computed again.
    second = _post_preview(api, graph, "subject", requested_preview_columns=["identifier"])

    assert _plan_of(second) == [("subject", "seeded")]
    # A seeded target reads the columns asked for, as any seeded preview target does.
    assert [column["name"] for column in second["columns"]] == ["identifier"]
    assert [column["name"] for column in second["available_columns"]] == [
        "_id",
        "premium",
        "segment",
        "discard",
    ]
    assert second["available_columns"] == first["available_columns"]


def test_a_preview_below_a_node_that_shapes_its_columns_seeds_it(api: Any, project: Path) -> None:
    graph = _renamed_edge_join(project, ["_id", "premium", "segment"])
    graph.nodes.append(
        _node("below", NodeType.POLARS, _code("df = subject.with_columns(pl.lit(1).alias('one'))"))
    )
    graph.edges.append(GraphEdge(id="e2", source="subject", target="below"))
    shaped = _post_preview(api, graph, "subject")

    body = _post_preview(api, graph, "below")

    # ``subject``'s fresh generation recorded its pre-shaping columns: it is read,
    # and still reports exactly the schema it had before its own shaping.
    assert ("subject", "seeded") in _plan_of(body)
    assert body["node_available_columns"]["subject"] == shaped["available_columns"]
    assert [column["name"] for column in body["node_available_columns"]["subject"]] == [
        "_id",
        "premium",
        "segment",
        "discard",
    ]


def test_a_shaping_node_generation_without_its_unshaped_columns_is_computed(
    api: Any, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._execute_lazy as execute_lazy

    graph = _renamed_edge_join(project, ["_id", "premium", "segment"])
    graph.nodes.append(
        _node("below", NodeType.POLARS, _code("df = subject.with_columns(pl.lit(1).alias('one'))"))
    )
    graph.edges.append(GraphEdge(id="e2", source="subject", target="below"))
    # A writer that does not record the pre-shaping columns (as before they were).
    with monkeypatch.context() as scoped:
        scoped.setattr(execute_lazy, "_shapes_output", lambda node: False)
        assert _plan_of(_post_preview(api, graph, "subject")) == [("subject", "captured")]

    body = _post_preview(api, graph, "below")

    # Its generation cannot say what the node offered before shaping: the node
    # is computed, and its schema is exact.
    assert ("subject", "seeded") not in _plan_of(body)
    assert [column["name"] for column in body["node_available_columns"]["subject"]] == [
        "_id",
        "premium",
        "segment",
        "discard",
    ]


def test_a_requested_column_the_node_no_longer_produces_is_refused_as_without_a_plan(
    api: Any, project: Path
) -> None:
    # The browser asks for the columns it showed last; after a column is
    # deselected that list still names it. A capture does not turn that hint
    # into demand the node must meet: the request is refused as it is when
    # nothing is seeded or captured.
    first = _post_preview(api, _renamed_edge_join(project, None), "subject")
    shown = [column["name"] for column in first["columns"]]
    assert shown == ["identifier", "premium", "region", "discard"]

    response = api.post(
        "/api/pipeline/preview",
        json={
            "graph": _renamed_edge_join(project, ["_id", "premium", "segment"]).model_dump(
                mode="json"
            ),
            "node_id": "subject",
            "row_limit": 200,
            "requested_preview_columns": shown,
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Requested preview column(s) not found on target: discard"


def test_preview_join_capture_is_chunked_into_parts_and_downstream_reads_them(
    project: Path, api: Any, store: NodeSnapshotStore
) -> None:
    n_rows = 10
    pl.DataFrame(
        {
            "_id": list(range(n_rows)),
            "premium": [float(i * 10) for i in range(n_rows)],
            "segment": ["North", "South"] * (n_rows // 2),
            "discard": [99] * n_rows,
        }
    ).write_parquet(project / "policies_task6.parquet")
    pl.DataFrame({"_id": list(range(n_rows))}).write_parquet(project / "keys_task6.parquet")

    join_config: dict[str, Any] = {
        "how": "left",
        "on": ["_id"],
        "selected_columns": ["_id", "premium", "segment"],
        "column_renames": {"_id": "identifier", "segment": "region"},
    }

    graph = PipelineGraph(
        nodes=[
            _node("raw_rows", NodeType.DATA_INPUT, _parquet(project / "policies_task6.parquet")),
            _node("lookup_rows", NodeType.DATA_INPUT, _parquet(project / "keys_task6.parquet")),
            _node("join_node", NodeType.EDGE_JOIN, join_config),
            _node(
                "banding",
                NodeType.POLARS,
                _code("df = join_node.with_columns((pl.col('premium') * 2).alias('band'))"),
            ),
        ],
        edges=[
            # Edge to JOIN handle listed FIRST
            GraphEdge(id="e0", source="lookup_rows", target="join_node", targetHandle="join"),
            # Edge to BASE handle listed SECOND
            GraphEdge(id="e1", source="raw_rows", target="join_node", targetHandle="base"),
            GraphEdge(id="e2", source="join_node", target="banding"),
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )

    first_body = _post_preview(api, graph, "banding", streaming_chunk_size=2)

    assert _plan_of(first_body) == [("join_node", "captured")]
    captures = first_body["execution_metrics"]["shared_snapshot_captures"]
    join_capture = next(c for c in captures if c["node_id"] == "join_node")
    assert join_capture["write_strategy"] == "chunked_join"
    assert join_capture["write_parts"] is not None and join_capture["write_parts"] > 1

    identity = _identity(store, graph, "join_node")
    gen = store.latest_generation(identity)
    assert gen is not None
    assert len(gen.generation.metadata.parts) > 1
    assert len(gen.generation.data_paths) > 1

    expected = (
        pl.read_parquet(project / "policies_task6.parquet")
        .join(pl.read_parquet(project / "keys_task6.parquet"), on="_id", how="left")
        .select(["_id", "premium", "segment"])
        .rename({"_id": "identifier", "segment": "region"})
        .with_columns((pl.col("premium") * 2).alias("band"))
    )
    first_rows = pl.DataFrame(first_body["preview"]).sort("identifier")
    expected_sorted = expected.sort("identifier")
    assert_frame_equal(first_rows.select(expected_sorted.columns), expected_sorted)

    from haute.executor import _preview_cache

    _preview_cache.clear()
    second_body = _post_preview(api, graph, "banding", streaming_chunk_size=2)
    assert _plan_of(second_body) == [("join_node", "seeded")]
    second_rows = pl.DataFrame(second_body["preview"]).sort("identifier")
    assert_frame_equal(second_rows.select(expected_sorted.columns), expected_sorted)


_MAP_ELEMENTS_CALLBACK_CALLS: list[Any] = []


def _record_map_elements_call(value: Any) -> Any:
    _MAP_ELEMENTS_CALLBACK_CALLS.append(value)
    return value


def test_preview_no_longer_captures_explode_or_a_limited_udf_but_still_captures_joins(
    project: Path, store: NodeSnapshotStore
) -> None:
    row_limit = 3

    # Explode node: preview captures nothing and records no skips
    explode_graph = _graph(
        project,
        [
            ("src", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            (
                "E",
                NodeType.POLARS,
                _code("df = src.with_columns(pl.lit([1, 2]).alias('k')).explode('k')"),
            ),
        ],
        [("src", "E")],
    )
    p_explode = _preview(explode_graph, store, "E", row_limit=row_limit)
    assert p_explode.captures == {}
    assert p_explode.metrics.get("shared_snapshot_capture_skips", []) == []
    assert "E" not in p_explode.result.errors
    expected_explode = (
        pl.read_parquet(project / "policies.parquet")
        .with_columns(pl.lit([1, 2]).alias("k"))
        .explode("k")
        .head(row_limit)
    )
    assert p_explode.rows("E").equals(expected_explode)

    # Map elements node: preview captures nothing and records no skips,
    # and callback runs exactly row_limit times.
    _MAP_ELEMENTS_CALLBACK_CALLS.clear()
    map_graph = PipelineGraph(
        nodes=[
            _node("src", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
            _node(
                "M",
                NodeType.POLARS,
                _code("df = src.with_columns(pl.col('a').map_elements(cb, return_dtype=pl.Int64))"),
            ),
        ],
        edges=[GraphEdge(id="e0", source="src", target="M")],
        preamble=(
            "import polars as pl\n"
            "from tests.test_preview_snapshot_seeding import _record_map_elements_call as cb\n"
        ),
        source_file=str(project / "main.py"),
    )
    p_map = _preview(map_graph, store, "M", row_limit=row_limit)
    assert p_map.captures == {}
    assert p_map.metrics.get("shared_snapshot_capture_skips", []) == []
    assert len(_MAP_ELEMENTS_CALLBACK_CALLS) == row_limit
    assert "M" not in p_map.result.errors
    expected_map = (
        pl.read_parquet(project / "policies.parquet")
        .with_columns(pl.col("a").map_elements(lambda x: x, return_dtype=pl.Int64))
        .head(row_limit)
    )
    assert p_map.rows("M").equals(expected_map)

    # Join node: preview still captures the join
    join_graph = _join_graph(project)
    p_join = _preview(join_graph, store, "banding", row_limit=row_limit)
    assert "join" in p_join.captures
    assert p_join.captures["join"]["outcome"] == "published"
