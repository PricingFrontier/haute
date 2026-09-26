"""Execution-differential harness: a saved standalone ``.py`` must run the
SAME function the canvas executor calls.

This is the structural gate for the shared ``apply_*_from_config`` /
``expand_scenarios_from_config`` / ``select_live_switch_input`` pattern.  A
configured node's function is a declaration (or a hook holding the user's
code); its decorator performs the node's work when the file runs on its own.
For each node type we:

1. build a graph,
2. codegen it to a real ``.py`` + JSON sidecars on disk,
3. import the generated module and drive ``pipeline.run()`` / the saved
   node under source in ``{live, batch}``,
4. ``assert_frame_equal`` the result against the canvas executor
   (``execute_lazy_graph`` — the same engine ``write_data_output`` / deploy scoring
   uses) for the SAME source.

Generated bodies were once bare passthroughs (``return {first}``) or hard-wired
the ``live`` liveSwitch branch, so a standalone ``pipeline.run()`` silently
no-oped or mis-routed.  These tests fail on that regression and pass only when
both sides share one code path.
"""

from __future__ import annotations

import importlib.util
import json
import pickle
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._builders import _build_node_fn
from haute._config_io import collect_node_configs, config_path_for_node
from haute._execution_admission import create_admitted_execution_context
from haute._mlflow_io import ScoringModel
from haute._model_scorer import _scenario_ctx
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code
from haute.execution import ExecutionProfile, execute_lazy_graph
from tests.conftest import make_ready_file_input_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(nid: str, label: str, node_type: NodeType, config: dict) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=label, nodeType=node_type, config=config))


def _edge(
    src: str,
    tgt: str,
    *,
    source_port: str | None = None,
    target_port: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=f"e_{src}_{tgt}_{source_port or 'default'}",
        source=src,
        target=tgt,
        sourceHandle=source_port,
        targetHandle=target_port,
    )


def _const(nid: str, label: str, values: list[dict]) -> GraphNode:
    return _node(nid, label, NodeType.CONSTANT, {"values": values})


def _collect(frame: object) -> pl.DataFrame:
    """Collect a LazyFrame; pass a DataFrame through."""
    return frame.collect() if hasattr(frame, "collect") else frame  # type: ignore[return-value]


def _write_and_import(graph: PipelineGraph, tmp_path: Path):
    """Codegen *graph* to disk (``.py`` + sidecars), import it, return the module."""
    name = f"gen_pipeline_{uuid.uuid4().hex}"
    code = graph_to_code(graph, pipeline_name=name)
    for rel_path, content in collect_node_configs(graph).items():
        cfg_file = tmp_path / rel_path
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(content)
    py_file = tmp_path / f"{name}.py"
    py_file.write_text(code)

    spec = importlib.util.spec_from_file_location(name, py_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _executor_frame(graph: PipelineGraph, target_id: str, source: str) -> pl.DataFrame:
    """Full-frame output for *target_id* from the canvas lazy executor.

    The reference runs under an admitted execution context, as every production
    surface does: a materialisation boundary (a join, a sort, a group-by) is a
    typed rejection without one, and the generated standalone module is the
    user's own process with no such gate.
    """
    context = create_admitted_execution_context(
        operation="codegen_equivalence_reference",
        profile=ExecutionProfile.LAZY_SINK,
    )
    try:
        outputs, _, _, _ = execute_lazy_graph(
            graph, _build_node_fn, source=source, execution_context=context
        )
        return _collect(outputs[target_id])
    finally:
        # Release the in-flight reservation as the production surfaces do; an
        # unreleased admission would count against every later admission in
        # the process.
        context.release_admission(preserve_primary_error=True)


def _executor_node_fn(node: GraphNode, source_names, source_ids, source: str):
    """Build the executor's function for a single node under *source*."""
    _, fn, _ = _build_node_fn(
        node,
        source_names=list(source_names),
        source_ids=list(source_ids),
        source=source,
    )
    return fn


@pytest.fixture
def isolated_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Keep generated modules and apiInput caches inside one temp project."""
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    try:
        yield tmp_path
    finally:
        set_project_root(original)


def _column(name: str, path: str, type_: str = "int") -> dict:
    return {"name": name, "path": path, "type": type_, "selected": True}


def _cached_api_graph(
    project: Path,
    *,
    records: list[dict],
    tables: list[dict],
    code: str,
    ports: list[str],
    build_cache: bool = True,
) -> PipelineGraph:
    data_path = project / "request.json"
    data_path.write_text(json.dumps(records), encoding="utf-8")
    config = {
        "path": str(data_path),
        "contract": "opaque",
        "tables": tables,
    }
    if build_cache:
        from tests.conftest import build_test_api_input_snapshots

        build_test_api_input_snapshots(data_path, config)

    api = _node("api", "Quote Input", NodeType.API_INPUT, config)
    transform = _node("transform", "Price Transform", NodeType.POLARS, {"code": code})
    return PipelineGraph(
        nodes=[api, transform],
        edges=[_edge("api", "transform", source_port=port) for port in ports],
    )


# ---------------------------------------------------------------------------
# apiInput frame identity — generated standalone execution must bind the same
# per-edge names as the canvas executor for both one- and multi-frame sources.
# ---------------------------------------------------------------------------


def test_one_frame_api_input_run_matches_executor_by_frame_label(
    isolated_project: Path,
) -> None:
    graph = _cached_api_graph(
        isolated_project,
        records=[{"quote_id": 7}, {"quote_id": 11}],
        tables=[
            {
                "path": "$[:]",
                "label": "quotes",
                "emit": True,
                "columns": [_column("quote_id", "$[:].quote_id")],
            }
        ],
        code="df = quotes.with_columns((pl.col('quote_id') * 2).alias('double_id'))",
        ports=["quotes"],
    )

    module = _write_and_import(graph, isolated_project)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "transform", source="batch")

    assert standalone["double_id"].to_list() == [14, 22]
    assert_frame_equal(standalone, reference)


def test_uncached_api_input_generated_run_matches_executor_after_snapshot_build(
    isolated_project: Path,
) -> None:
    """The standalone generated module always shreds directly (no snapshot store).

    The canvas executor, however, requires a built input snapshot. Build it
    after the standalone run to confirm both paths still agree on the data.
    """
    graph = _cached_api_graph(
        isolated_project,
        records=[{"quote_id": 7}, {"quote_id": 11}],
        tables=[
            {
                "path": "$[:]",
                "label": "quotes",
                "emit": True,
                "columns": [_column("quote_id", "$[:].quote_id")],
            }
        ],
        code="df = quotes.with_columns((pl.col('quote_id') * 2).alias('double_id'))",
        ports=["quotes"],
        build_cache=False,
    )
    module = _write_and_import(graph, isolated_project)

    standalone_direct = _collect(module.pipeline.run())

    api_config = graph.nodes[0].data.config
    data_path = Path(api_config["path"])
    from tests.conftest import build_test_api_input_snapshots

    build_test_api_input_snapshots(data_path, api_config)

    standalone_cached = _collect(module.pipeline.run())
    executor_cached = _executor_frame(graph, "transform", source="batch")
    assert_frame_equal(standalone_cached, executor_cached)
    assert_frame_equal(standalone_direct, standalone_cached)


def test_multi_frame_api_input_run_matches_executor_by_each_frame_label(
    isolated_project: Path,
) -> None:
    graph = _cached_api_graph(
        isolated_project,
        records=[
            {"policy_id": 1, "drivers": [{"driver_id": 10}, {"driver_id": 11}]},
            {"policy_id": 2, "drivers": [{"driver_id": 20}]},
        ],
        tables=[
            {
                "path": "$[:]",
                "label": "quotes",
                "emit": True,
                "columns": [_column("policy_id", "$[:].policy_id")],
            },
            {
                "path": "$[:].drivers[:]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    _column("policy_id", "$[:].policy_id"),
                    _column("driver_id", "$[:].drivers[:].driver_id"),
                ],
            },
        ],
        code=(
            "df = quotes.join(drivers, on='policy_id', how='inner')"
            ".select('policy_id', 'driver_id')"
        ),
        ports=["quotes", "drivers"],
    )

    module = _write_and_import(graph, isolated_project)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "transform", source="batch")

    assert standalone.to_dicts() == [
        {"policy_id": 1, "driver_id": 10},
        {"policy_id": 1, "driver_id": 11},
        {"policy_id": 2, "driver_id": 20},
    ]
    assert_frame_equal(standalone, reference)


# ---------------------------------------------------------------------------
# scenarioExpander — the generated body must expand, not no-op
# ---------------------------------------------------------------------------


def test_scenario_expander_run_matches_executor_batch(tmp_path):
    src = _const("c", "rows", [{"name": "q", "value": 1}])
    expander = _node(
        "exp",
        "expand_scenarios",
        NodeType.SCENARIO_EXPANDER,
        {
            "column_name": "scenario_value",
            "min_value": 0.8,
            "max_value": 1.2,
            "stepCount": 5,
            "step_column": "scenario_index",
        },
    )
    graph = PipelineGraph(nodes=[src, expander], edges=[_edge("c", "exp")])

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "exp", source="batch")

    # The generated body genuinely expanded (not a passthrough of the 1-row src).
    assert standalone.height == 5
    assert_frame_equal(standalone, reference)


def test_scenario_expander_saved_file_is_not_a_passthrough(tmp_path):
    """Regression guard for F005: a saved expander must not no-op."""
    src = _const("c", "rows", [{"name": "q", "value": 1}])
    expander = _node(
        "exp",
        "expand",
        NodeType.SCENARIO_EXPANDER,
        {"stepCount": 7, "step_column": "scenario_index"},
    )
    graph = PipelineGraph(nodes=[src, expander], edges=[_edge("c", "exp")])
    module = _write_and_import(graph, tmp_path)
    result = _collect(module.pipeline.run())
    assert result.height == 7  # 1 row × 7 steps, not a 1-row passthrough
    assert "scenario_index" in result.columns


# ---------------------------------------------------------------------------
# optimiserApply — the generated body must apply the artifact, not no-op
# ---------------------------------------------------------------------------


def _write_ratebook_artifact(tmp_path: Path) -> str:
    import json

    artifact = {
        "version": "rb_v1",
        "mode": "ratebook",
        "factor_tables": {
            "region": [
                {"__factor_group__": "London", "optimal_scenario_value": 1.05},
                {"__factor_group__": "Manchester", "optimal_scenario_value": 0.98},
            ],
        },
        # London's 1.05 lies past the collar, so both paths must clip it.
        "combined_factor_bounds": {"min": 0.99, "max": 1.02},
        "factor_dtypes": {
            "region": [{"column": "region", "dtype": {"kind": "String"}}],
        },
    }
    path = tmp_path / "ratebook.json"
    path.write_text(json.dumps(artifact))
    return str(path).replace("\\", "/")


def test_optimiser_apply_run_matches_executor_batch(tmp_path, _widen_sandbox_root):
    artifact_path = _write_ratebook_artifact(tmp_path)
    src = _const("rb", "ratebook_src", [{"name": "region", "value": "London"}])
    apply = _node(
        "apply",
        "apply_optimisation",
        NodeType.OPTIMISER_APPLY,
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "ratebook_input": "ratebook_src",
            "version_column": "__optimiser_version__",
        },
    )
    graph = PipelineGraph(nodes=[src, apply], edges=[_edge("rb", "apply")])

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "apply", source="batch")

    # The generated body genuinely applied the artifact (not a passthrough).
    assert "__optimiser_version__" in standalone.columns
    assert standalone["region_optimised_factor"].to_list() == [1.05]
    assert standalone["optimised_factor"].to_list() == [1.02]
    assert_frame_equal(standalone, reference)


# ---------------------------------------------------------------------------
# liveSwitch — the generated body must be scenario-aware in BOTH directions
# ---------------------------------------------------------------------------


def _live_switch_graph() -> tuple[PipelineGraph, GraphNode]:
    live_src = _const("live", "live_src", [{"name": "v", "value": 1}])
    batch_src = _const("batch", "batch_src", [{"name": "v", "value": 2}])
    switch = _node(
        "sw",
        "Switch",
        NodeType.LIVE_SWITCH,
        {"input_scenario_map": {"live_src": "live", "batch_src": "batch"}},
    )
    graph = PipelineGraph(
        nodes=[live_src, batch_src, switch],
        edges=[_edge("live", "sw"), _edge("batch", "sw")],
    )
    return graph, switch


def test_live_switch_batch_run_routes_batch_branch(tmp_path):
    """pipeline.run() (source=batch) must route the BATCH input — the exact
    divergence F000 reported (standalone hard-wired the live branch)."""
    graph, _ = _live_switch_graph()
    module = _write_and_import(graph, tmp_path)

    standalone = _collect(module.pipeline.run())  # run() -> _scenario_ctx="batch"
    reference = _executor_frame(graph, "sw", source="batch")

    assert standalone["v"].to_list() == [2]  # batch_src, not the live branch
    assert_frame_equal(standalone, reference)


def test_live_switch_generated_body_scenario_aware_both_directions(tmp_path):
    """The generated liveSwitch function routes by the active runtime source in
    BOTH directions, matching the executor's ``_build_live_switch``.

    The generated function is a declaration whose body never runs: the name
    its decorator returns runs the node — the decorator's routing — as a
    pipeline run does, while the registered node keeps the raw declaration.
    """
    graph, switch = _live_switch_graph()
    module = _write_and_import(graph, tmp_path)
    generated_fn = module.Switch
    registered = next(node for node in module.pipeline.nodes if node.name == "Switch")
    assert generated_fn.__wrapped__ is registered.fn

    frame_live = pl.LazyFrame({"v": [1]})
    frame_batch = pl.LazyFrame({"v": [2]})

    exec_live = _executor_node_fn(
        switch, ["live_src", "batch_src"], ["live", "batch"], source="live"
    )
    exec_batch = _executor_node_fn(
        switch, ["live_src", "batch_src"], ["live", "batch"], source="batch"
    )

    for source, expected_v in (("live", 1), ("batch", 2)):
        token = _scenario_ctx.set(source)
        try:
            standalone = _collect(generated_fn(frame_live, frame_batch))
        finally:
            _scenario_ctx.reset(token)
        executor = _collect(
            (exec_live if source == "live" else exec_batch)(frame_live, frame_batch)
        )
        assert standalone["v"].to_list() == [expected_v]
        assert_frame_equal(standalone, executor)


# ---------------------------------------------------------------------------
# modelling — a genuine passthrough stays runtime-equivalent
# ---------------------------------------------------------------------------


def test_modelling_shared_first_input_semantics_match_executor_batch(tmp_path):
    first = _const("c1", "features", [{"name": "x", "value": 3}])
    second = _const("c2", "alternate", [{"name": "x", "value": 9}])
    train = _node("m", "train", NodeType.MODELLING, {"target": "loss", "algorithm": "catboost"})
    graph = PipelineGraph(
        nodes=[first, second, train],
        edges=[_edge("c1", "m"), _edge("c2", "m")],
    )

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "m", source="batch")
    assert_frame_equal(standalone, reference)
    assert standalone["x"].to_list() == [3]


# ---------------------------------------------------------------------------
# Declarations whose decorator does the work: edgeJoin, constant, banding,
# ratingStep — each must match the executor, never pass its input through.
# ---------------------------------------------------------------------------


def test_edge_join_run_matches_executor_batch(tmp_path):
    policies = _const(
        "p", "policies", [{"name": "key", "value": 1}, {"name": "premium", "value": 100}]
    )
    regions = _const(
        "r", "regions", [{"name": "key", "value": 1}, {"name": "region", "value": "north"}]
    )
    join = _node("j", "join_regions", NodeType.EDGE_JOIN, {"how": "left", "on": ["key"]})
    graph = PipelineGraph(
        nodes=[policies, regions, join],
        edges=[_edge("p", "j", target_port="base"), _edge("r", "j", target_port="join")],
    )

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "j", source="batch")

    assert standalone.to_dicts() == [{"key": 1, "premium": 100, "region": "north"}]
    assert_frame_equal(standalone, reference)


def test_constant_run_matches_executor_batch(tmp_path):
    graph = PipelineGraph(
        nodes=[
            _const("c", "rates", [{"name": "rate", "value": 0.05}, {"name": "cap", "value": 1000}])
        ],
        edges=[],
    )

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "c", source="batch")

    assert standalone.to_dicts() == [{"rate": 0.05, "cap": 1000}]
    assert_frame_equal(standalone, reference)


def test_banding_run_matches_executor_batch(tmp_path):
    src = _const("c", "policies", [{"name": "age", "value": 30}])
    band = _node(
        "band",
        "age_bands",
        NodeType.BANDING,
        {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "age",
                    "outputColumn": "age_band",
                    "rules": [
                        {"boundary": "18", "label": "young"},
                        {"boundary": "60", "label": "adult"},
                    ],
                    "default": "senior",
                }
            ]
        },
    )
    graph = PipelineGraph(nodes=[src, band], edges=[_edge("c", "band")])

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "band", source="batch")

    # The decorator genuinely banded (not a passthrough of the source row).
    assert standalone["age_band"].to_list() == ["adult"]
    assert_frame_equal(standalone, reference)


def test_rating_step_run_matches_executor_batch(tmp_path):
    src = _const("c", "quotes", [{"name": "region", "value": "south"}])
    rate = _node(
        "rate",
        "rate",
        NodeType.RATING_STEP,
        {
            "tables": [
                {
                    "name": "Region Factor",
                    "factors": ["region"],
                    "outputColumn": "region_factor",
                    "entries": [
                        {"region": "north", "value": 0.9},
                        {"region": "south", "value": 1.1},
                    ],
                }
            ],
            "combinedOutputs": [
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 200.0}
            ],
        },
    )
    graph = PipelineGraph(nodes=[src, rate], edges=[_edge("c", "rate")])

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "rate", source="batch")

    assert standalone["region_factor"].to_list() == [1.1]
    assert standalone["premium"].to_list() == pytest.approx([220.0])
    assert_frame_equal(standalone, reference)


# ---------------------------------------------------------------------------
# modelScore — a declaration scores through its decorator; a hook receives
# the scored frame. The model is stubbed on both paths.
# ---------------------------------------------------------------------------


def _stub_scoring_model() -> ScoringModel:
    """A CatBoost-flavoured model over a mock estimator predicting 0.5 per row."""
    model = MagicMock()
    model.feature_names_ = ["a", "b"]
    model.predict.side_effect = lambda frame: np.full(len(frame), 0.5)
    del model.predict_proba
    return ScoringModel(
        model=model,
        feature_names=["a", "b"],
        cat_feature_names=frozenset(),
        flavor="catboost",
    )


@pytest.mark.parametrize(
    "code",
    ["", "df = df.with_columns(doubled=pl.col('prediction') * 2)"],
    ids=["declaration", "hook"],
)
def test_model_score_run_matches_executor_batch(tmp_path, code):
    src = _const("c", "features", [{"name": "a", "value": 1.0}, {"name": "b", "value": 2.0}])
    config = {
        "sourceType": "run",
        "run_id": "run123",
        "artifact_path": "model.cbm",
        "task": "regression",
        "output_column": "prediction",
        **({"code": code} if code else {}),
    }
    score = _node("score", "score", NodeType.MODEL_SCORE, config)
    graph = PipelineGraph(nodes=[src, score], edges=[_edge("c", "score")])

    module = _write_and_import(graph, tmp_path)
    with patch("haute._mlflow_io.load_mlflow_model", return_value=_stub_scoring_model()):
        standalone = _collect(module.pipeline.run())
        reference = _executor_frame(graph, "score", source="batch")

    assert standalone["prediction"].to_list() == [0.5]
    if code:
        assert standalone["doubled"].to_list() == [1.0]
    assert_frame_equal(standalone, reference)


# ---------------------------------------------------------------------------
# explore / modelScore hooks — code sees its input only as df, on both surfaces
# ---------------------------------------------------------------------------


def _explore_graph(code: str) -> PipelineGraph:
    rows = _const("c", "rows", [{"name": "premium", "value": 100}])
    explore = _node("ex", "inspect", NodeType.EXPLORE, {"code": code})
    return PipelineGraph(nodes=[rows, explore], edges=[_edge("c", "ex")])


def test_explore_hook_run_matches_executor_batch(tmp_path):
    graph = _explore_graph("df = df.with_columns(doubled=pl.col('premium') * 2)")

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "ex", source="batch")

    assert standalone["doubled"].to_list() == [200.0]
    assert_frame_equal(standalone, reference)


def test_explore_code_naming_its_input_fails_on_both_surfaces(tmp_path):
    """The hook receives its input as df; the input's own name is bound nowhere."""
    graph = _explore_graph("df = rows.head(1)")

    module = _write_and_import(graph, tmp_path)
    with pytest.raises(AttributeError):
        module.pipeline.run()
    with pytest.raises(NameError, match="rows"):
        _executor_frame(graph, "ex", source="batch")


def test_model_score_code_naming_its_input_fails_on_both_surfaces(tmp_path):
    """The scored frame is df; the canvas no longer also binds it under the input's name."""
    src = _const("c", "features", [{"name": "a", "value": 1.0}, {"name": "b", "value": 2.0}])
    score = _node(
        "score",
        "score",
        NodeType.MODEL_SCORE,
        {
            "sourceType": "run",
            "run_id": "run123",
            "artifact_path": "model.cbm",
            "task": "regression",
            "output_column": "prediction",
            "code": "df = features.with_columns(doubled=pl.col('prediction') * 2)",
        },
    )
    graph = PipelineGraph(nodes=[src, score], edges=[_edge("c", "score")])

    module = _write_and_import(graph, tmp_path)
    with patch("haute._mlflow_io.load_mlflow_model", return_value=_stub_scoring_model()):
        with pytest.raises(AttributeError):
            module.pipeline.run()
        with pytest.raises(NameError, match="features"):
            _executor_frame(graph, "score", source="batch")


# ---------------------------------------------------------------------------
# Hooks — a Data Input's code runs on its loaded data, an External File's on
# its inputs with the loaded object.
# ---------------------------------------------------------------------------


def test_data_input_hook_run_matches_executor_batch(isolated_project: Path) -> None:
    (isolated_project / ".git").mkdir()
    (isolated_project / "haute.toml").write_text('[project]\nname = "hooks"\n', encoding="utf-8")
    pl.DataFrame({"policy_id": [1, 2, 3]}).write_parquet(isolated_project / "policies.parquet")
    source = _node(
        "src",
        "policies",
        NodeType.DATA_INPUT,
        make_ready_file_input_config(
            "policies.parquet", code="df = df.filter(pl.col('policy_id') > 1)"
        ),
    )
    graph = PipelineGraph(nodes=[source], edges=[])

    module = _write_and_import(graph, isolated_project)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "src", source="batch")

    assert standalone["policy_id"].to_list() == [2, 3]
    assert_frame_equal(standalone, reference)


def test_external_file_hook_run_matches_executor_batch(isolated_project: Path) -> None:
    (isolated_project / "factor.json").write_text('{"factor": 3}', encoding="utf-8")
    source = _const("source", "rows", [{"name": "value", "value": 2}])
    external = _node(
        "external",
        "lookup",
        NodeType.EXTERNAL_FILE,
        {
            "path": "factor.json",
            "fileType": "json",
            "code": "df = df.with_columns(scaled=pl.col('value') * obj['factor'])",
        },
    )
    graph = PipelineGraph(nodes=[source, external], edges=[_edge("source", "external")])

    module = _write_and_import(graph, isolated_project)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "external", source="batch")

    assert standalone["scaled"].to_list() == [6]
    assert_frame_equal(standalone, reference)


# ---------------------------------------------------------------------------
# OUTPUT — the generated body must assemble the document, not pass through
# ---------------------------------------------------------------------------


def _output_graph() -> PipelineGraph:
    src = _const("c", "rows", [{"name": "premium", "value": 120}, {"name": "tax", "value": 12}])
    out = _node(
        "out",
        "quote_response",
        NodeType.OUTPUT,
        {
            "outputMapping": [
                {
                    "source_port": "rows",
                    "source_column": "premium",
                    "output_path": "$[:].quote.premium",
                    "enabled": True,
                },
                {
                    "source_port": "rows",
                    "source_column": "tax",
                    "output_path": "$[:].quote.tax",
                    "enabled": True,
                },
            ],
        },
    )
    return PipelineGraph(nodes=[src, out], edges=[_edge("c", "out")])


def test_output_run_matches_executor_batch(tmp_path):
    """A saved OUTPUT node must assemble the response document standalone.

    Before the fix the generated ``@pipeline.output`` body was a bare
    ``return {first}`` passthrough, so a standalone ``pipeline.run()``
    returned the raw upstream frame instead of the assembled document.
    """
    graph = _output_graph()
    module = _write_and_import(graph, tmp_path)

    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "out", source="batch")

    # The generated body genuinely assembled (nested doc, not raw columns).
    assert "quote" in standalone.columns
    assert "premium" not in standalone.columns
    assert standalone.to_dicts() == [{"quote": {"premium": 120, "tax": 12}}]
    assert_frame_equal(standalone, reference)


def test_output_late_nested_fields_survive_full_document_schema_inference(
    isolated_project: Path,
) -> None:
    """A field appearing after Polars' default 100-row inference window is
    still part of the OUTPUT schema through generated and executor paths."""
    records = [{"id": index, "late": None, "detail": {"value": None}} for index in range(101)] + [
        {"id": 101, "late": 7, "detail": {"value": 9}}
    ]
    data_path = isolated_project / "late.json"
    data_path.write_text(json.dumps(records), encoding="utf-8")
    api = _node(
        "api",
        "rows",
        NodeType.API_INPUT,
        {
            "path": str(data_path),
            "contract": "opaque",
            "tables": [
                {
                    "path": "$[:]",
                    "label": "rows",
                    "emit": True,
                    "columns": [
                        _column("id", "$[:].id"),
                        _column("late", "$[:].late"),
                        _column("detail_value", "$[:].detail.value"),
                    ],
                }
            ],
        },
    )
    out = _node(
        "out",
        "response",
        NodeType.OUTPUT,
        {
            "outputMapping": [
                {
                    "source_port": "rows",
                    "source_column": "id",
                    "output_path": "$[:].payload.id",
                    "enabled": True,
                },
                {
                    "source_port": "rows",
                    "source_column": "late",
                    "output_path": "$[:].payload.late",
                    "enabled": True,
                },
                {
                    "source_port": "rows",
                    "source_column": "detail_value",
                    "output_path": "$[:].payload.detail.value",
                    "enabled": True,
                },
            ]
        },
    )
    graph = PipelineGraph(
        nodes=[api, out],
        edges=[_edge("api", "out", source_port="rows")],
    )

    module = _write_and_import(graph, isolated_project)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "out", source="batch")

    assert standalone.height == 102
    assert standalone.row(0, named=True) == {"payload": {"id": 0, "late": None, "detail": None}}
    assert standalone.row(-1, named=True) == {
        "payload": {"id": 101, "late": 7, "detail": {"value": 9}}
    }
    assert_frame_equal(standalone, reference)


# ---------------------------------------------------------------------------
# Retained input sidecars — generated bodies must read the current sidecar,
# not the config snapshot that happened to exist during code generation.
# ---------------------------------------------------------------------------


def test_generated_api_input_observes_sidecar_only_path_edit(
    isolated_project: Path,
) -> None:
    first = isolated_project / "first.parquet"
    second = isolated_project / "second.parquet"
    pl.DataFrame({"value": [1]}).write_parquet(first)
    pl.DataFrame({"value": [2]}).write_parquet(second)
    api = _node(
        "api",
        "quotes",
        NodeType.API_INPUT,
        {"path": "first.parquet", "contract": "opaque"},
    )
    graph = PipelineGraph(nodes=[api], edges=[])
    module = _write_and_import(graph, isolated_project)

    assert _collect(module.pipeline.run())["value"].to_list() == [1]

    sidecar = isolated_project / config_path_for_node(NodeType.API_INPUT, "quotes")
    sidecar.write_text(
        json.dumps({"path": "second.parquet", "contract": "opaque"}),
        encoding="utf-8",
    )
    assert _collect(module.pipeline.run())["value"].to_list() == [2]


def test_generated_and_canvas_inputs_share_project_anchor_outside_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    pipeline_dir = project / "pipelines"
    data_dir = pipeline_dir / "data"
    unrelated = tmp_path / "unrelated"
    pipeline_dir.mkdir(parents=True)
    data_dir.mkdir()
    unrelated.mkdir()
    (project / "haute.toml").write_text(
        '[project]\npipeline = "pipelines/main.py"\n',
        encoding="utf-8",
    )
    source = data_dir / "source.parquet"
    pl.DataFrame({"value": [7]}).write_parquet(source)
    graph = PipelineGraph(
        nodes=[
            _node(
                "api",
                "quotes",
                NodeType.API_INPUT,
                {"path": "data/source.parquet", "contract": "opaque"},
            )
        ],
        edges=[],
    )

    original_root = _get_project_root()
    set_project_root(project)
    try:
        module = _write_and_import(graph, pipeline_dir)
        monkeypatch.chdir(unrelated)

        generated = _collect(module.pipeline.run())
        canvas = _executor_frame(graph, "api", source="batch")
    finally:
        set_project_root(original_root)

    assert generated.to_dict(as_series=False) == {"value": [7]}
    assert_frame_equal(generated, canvas)


def test_generated_external_file_observes_sidecar_loader_edits_and_rejects_malformed(
    isolated_project: Path,
) -> None:
    (isolated_project / "first.json").write_text('{"factor": 2}', encoding="utf-8")
    (isolated_project / "second.pkl").write_bytes(pickle.dumps({"factor": 5}))
    source = _const("source", "rows", [{"name": "value", "value": 3}])
    external = _node(
        "external",
        "lookup",
        NodeType.EXTERNAL_FILE,
        {
            "path": "first.json",
            "fileType": "json",
            "code": "df = rows.with_columns(pl.lit(obj['factor']).alias('factor'))",
        },
    )
    graph = PipelineGraph(
        nodes=[source, external],
        edges=[_edge("source", "external")],
    )
    module = _write_and_import(graph, isolated_project)

    assert _collect(module.pipeline.run())["factor"].to_list() == [2]

    sidecar = isolated_project / config_path_for_node(NodeType.EXTERNAL_FILE, "lookup")
    sidecar.write_text(
        json.dumps({"path": "second.pkl", "fileType": "pickle"}),
        encoding="utf-8",
    )
    assert _collect(module.pipeline.run())["factor"].to_list() == [5]

    sidecar.write_text(json.dumps({"path": "second.pkl"}), encoding="utf-8")
    with pytest.raises(ValueError, match="fileType"):
        module.pipeline.run()


def test_generated_retained_input_fails_on_malformed_sidecar(
    isolated_project: Path,
) -> None:
    source = isolated_project / "source.parquet"
    pl.DataFrame({"value": [1]}).write_parquet(source)
    graph = PipelineGraph(
        nodes=[
            _node(
                "api",
                "quotes",
                NodeType.API_INPUT,
                {"path": "source.parquet", "contract": "opaque"},
            )
        ],
        edges=[],
    )
    module = _write_and_import(graph, isolated_project)
    sidecar = isolated_project / config_path_for_node(NodeType.API_INPUT, "quotes")
    sidecar.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain an object"):
        module.pipeline.run()
