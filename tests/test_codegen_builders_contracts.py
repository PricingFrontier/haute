"""Focused contracts for codegen builder edge cases not covered elsewhere."""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._codegen_builders import _gen_live_switch, _gen_model_score, render_node_source
from haute._config_io import collect_node_configs
from haute._mlflow_io import ScoringModel
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code
from haute.errors import LiveSwitchScenarioError
from tests.conftest import compile_node_code as _compile_node_code
from tests.conftest import make_node as _n


def _live_switch_node(input_scenario_map: dict[str, str]) -> GraphNode:
    return _n(
        {
            "id": "switch",
            "data": {
                "label": "Switch",
                "nodeType": "liveSwitch",
                "config": {"input_scenario_map": input_scenario_map},
            },
        }
    )


def _write_and_import(graph: PipelineGraph, directory: Path) -> tuple[str, ModuleType]:
    """Save *graph* as a pipeline file plus sidecars under *directory* and import it."""
    name = f"gen_contract_{uuid.uuid4().hex}"
    code = graph_to_code(graph, pipeline_name=name)
    for rel_path, content in collect_node_configs(graph).items():
        sidecar = directory / rel_path
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(content, encoding="utf-8")
    py_file = directory / f"{name}.py"
    py_file.write_text(code, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, py_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return code, module


def _switch_graph(
    input_scenario_map: dict[str, str], switch_inputs: tuple[str, ...]
) -> PipelineGraph:
    """One source, one tagging transform per switch input, and the switch.

    ``score()`` seeds the single source, so the same saved file runs under
    both the batch (``run()``) and the live (``score()``) scenario.
    """
    nodes = [
        GraphNode(
            id="rows",
            data=NodeData(
                label="rows",
                nodeType=NodeType.CONSTANT,
                config={"values": [{"name": "v", "value": 1}]},
            ),
        ),
        *(
            GraphNode(
                id=name,
                data=NodeData(
                    label=name,
                    nodeType=NodeType.POLARS,
                    config={"code": f'df = rows.with_columns(branch=pl.lit("{name}"))'},
                ),
            )
            for name in switch_inputs
        ),
        _live_switch_node(input_scenario_map),
    ]
    edges = [
        *(GraphEdge(id=f"rows_{name}", source="rows", target=name) for name in switch_inputs),
        *(GraphEdge(id=f"{name}_switch", source=name, target="switch") for name in switch_inputs),
    ]
    return PipelineGraph(nodes=nodes, edges=edges)


def test_live_switch_is_a_declaration_routing_by_the_active_scenario(tmp_path: Path) -> None:
    # Branch selection is a RUNTIME concern: the decorator reads the sidecar map
    # and the active scenario, so nothing about the routing is hard-wired into
    # the generated function, which only declares the switch's inputs.
    node = _live_switch_node({"live_src": "live", "batch_src": "batch"})

    code = render_node_source(_gen_live_switch(node, ["batch_src", "live_src"]), func_name="Switch")

    assert code == (
        '@pipeline.live_switch(config="config/source_switch/Switch.json")\n'
        "def Switch(batch_src, live_src): ...\n"
    )
    _compile_node_code(code)

    graph = _switch_graph({"live_src": "live", "batch_src": "batch"}, ("batch_src", "live_src"))
    source, module = _write_and_import(graph, tmp_path)

    assert "input_scenario_map" not in source
    assert "return live_src" not in source
    batch = module.pipeline.run()
    live = module.pipeline.score(pl.DataFrame({"v": [5]}))
    assert batch.to_dicts() == [{"v": 1, "branch": "batch_src"}]
    assert live.to_dicts() == [{"v": 5, "branch": "live_src"}]


def test_live_switch_runs_with_its_declared_input_order_and_switch_name(tmp_path: Path) -> None:
    # The declaration's parameters are the inputs in edge order: the unconfigured
    # fallback takes the first declared input, and a configured mapping without
    # the active scenario raises naming the switch.
    graph = _switch_graph({}, ("shadow_src", "batch_src"))
    source, module = _write_and_import(graph, tmp_path)

    assert "def Switch(shadow_src, batch_src): ...\n" in source
    assert module.pipeline.run()["branch"].to_list() == ["shadow_src"]

    graph = _switch_graph(
        {"missing_live_src": "live", "batch_src": "batch"}, ("batch_src", "shadow_src")
    )
    _source, module = _write_and_import(graph, tmp_path)

    assert module.pipeline.run()["branch"].to_list() == ["batch_src"]
    with pytest.raises(LiveSwitchScenarioError, match="'Switch' has no input for scenario 'live'"):
        module.pipeline.score(pl.DataFrame({"v": [5]}))


def _stub_scoring_model() -> ScoringModel:
    """A CatBoost-flavoured model over a mock estimator predicting 0.25."""
    model = MagicMock()
    model.feature_names_ = ["a", "b"]
    model.predict.return_value = np.array([0.25])
    del model.predict_proba
    return ScoringModel(
        model=model,
        feature_names=["a", "b"],
        cat_feature_names=frozenset(),
        flavor="catboost",
    )


def test_model_score_registered_source_is_read_from_the_sidecar(tmp_path: Path) -> None:
    config = {
        "sourceType": "registered",
        "registered_model": "catalog.schema.pricing_model",
        "version": "7",
        "task": "regression",
        "output_column": "score",
    }
    node = _n(
        {"id": "score", "data": {"label": "Score", "nodeType": "modelScore", "config": config}}
    )

    code = render_node_source(_gen_model_score(node, ["features"]), func_name="Score")

    assert code == (
        '@pipeline.model_score(config="config/model_scoring/Score.json")\n'
        "def Score(features): ...\n"
    )
    _compile_node_code(code)

    features = GraphNode(
        id="features",
        data=NodeData(
            label="features",
            nodeType=NodeType.CONSTANT,
            config={"values": [{"name": "a", "value": 1.0}, {"name": "b", "value": 2.0}]},
        ),
    )
    graph = PipelineGraph(
        nodes=[features, node],
        edges=[GraphEdge(id="features_score", source="features", target="score")],
    )
    source, module = _write_and_import(graph, tmp_path)
    sidecar = json.loads((tmp_path / "config" / "model_scoring" / "Score.json").read_text())

    assert "catalog.schema.pricing_model" not in source
    assert sidecar["registered_model"] == "catalog.schema.pricing_model"
    with patch(
        "haute._mlflow_io.load_mlflow_model", return_value=_stub_scoring_model()
    ) as load_model:
        result = module.pipeline.run()

    assert result["score"].to_list() == [0.25]
    assert load_model.call_args.kwargs["source_type"] == "registered"
    assert load_model.call_args.kwargs["registered_model"] == "catalog.schema.pricing_model"
    assert load_model.call_args.kwargs["version"] == "7"
