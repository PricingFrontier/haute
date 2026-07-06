"""Execution-differential harness: a saved standalone ``.py`` must run the
SAME function the canvas executor calls.

This is the structural gate for the shared ``apply_*_from_config`` /
``expand_scenarios_from_config`` / ``select_live_switch_input`` pattern.  For
each behavioural node type we:

1. build a graph,
2. codegen it to a real ``.py`` + JSON sidecars on disk,
3. import the generated module and drive ``pipeline.run()`` / the generated
   node body under source in ``{live, batch}``,
4. ``assert_frame_equal`` the result against the canvas executor
   (``_execute_lazy`` — the same engine ``execute_sink`` / deploy scoring
   uses) for the SAME source.

Before the fix the generated bodies were bare passthroughs (``return {first}``)
or hard-wired the ``live`` liveSwitch branch, so a standalone
``pipeline.run()`` silently no-oped or mis-routed.  These tests fail on that
regression and pass only when both sides share one code path.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import polars as pl
from polars.testing import assert_frame_equal

from haute._builders import _build_node_fn
from haute._config_io import collect_node_configs
from haute._execute_lazy import _execute_lazy
from haute._model_scorer import _scenario_ctx
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(nid: str, label: str, node_type: NodeType, config: dict) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=label, nodeType=node_type, config=config))


def _edge(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


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
    """Full-frame output for *target_id* from the canvas lazy executor."""
    outputs, _, _, _ = _execute_lazy(graph, _build_node_fn, source=source)
    return _collect(outputs[target_id])


def _executor_node_fn(node: GraphNode, source_names, source_ids, source: str):
    """Build the executor's function for a single node under *source*."""
    _, fn, _ = _build_node_fn(
        node,
        source_names=list(source_names),
        source_ids=list(source_ids),
        source=source,
    )
    return fn


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
            "steps": 5,
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
        {"steps": 7, "step_column": "scenario_index"},
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
    }
    path = tmp_path / "ratebook.json"
    path.write_text(json.dumps(artifact))
    return str(path).replace("\\", "/")


def test_optimiser_apply_run_matches_executor_batch(tmp_path):
    artifact_path = _write_ratebook_artifact(tmp_path)
    src = _const("rb", "ratebook_src", [{"name": "region", "value": "London"}])
    apply = _node(
        "apply",
        "apply_optimisation",
        NodeType.OPTIMISER_APPLY,
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "ratebook_input": "rb",
            "version_column": "__optimiser_version__",
        },
    )
    graph = PipelineGraph(nodes=[src, apply], edges=[_edge("rb", "apply")])

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "apply", source="batch")

    # The generated body genuinely applied the artifact (not a passthrough).
    assert "__optimiser_version__" in standalone.columns
    assert "optimised_factor" in standalone.columns
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
    """The generated liveSwitch body routes by the active runtime source in
    BOTH directions, matching the executor's ``_build_live_switch``."""
    graph, switch = _live_switch_graph()
    module = _write_and_import(graph, tmp_path)
    generated_fn = module.Switch

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


def test_modelling_run_matches_executor_batch(tmp_path):
    src = _const("c", "features", [{"name": "x", "value": 3}])
    train = _node("m", "train", NodeType.MODELLING, {"target": "loss", "algorithm": "catboost"})
    graph = PipelineGraph(nodes=[src, train], edges=[_edge("c", "m")])

    module = _write_and_import(graph, tmp_path)
    standalone = _collect(module.pipeline.run())
    reference = _executor_frame(graph, "m", source="batch")
    assert_frame_equal(standalone, reference)
