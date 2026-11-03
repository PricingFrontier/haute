"""Each node type's config model is the one validation boundary for its config (PCFG-R07)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._config_io import load_node_config, palette_default_config
from haute._config_validation import (
    config_gaps,
    unrecognized_config_keys,
    validate_graph_configs,
    validate_node_config,
)
from haute._contracts import (
    _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY,
    DEPLOY_ANSWER_PER_ROW_CONFIG_KEY,
)
from haute._node_config_models import NODE_CONFIG_MODELS
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import ConfigError, NodeConfigError
from haute.executor import _build_node_fn
from haute.modelling._train_config import TrainingConfigError
from scripts.generate_node_config_reference import config_key_rows

REPO = Path(__file__).resolve().parents[1]

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}
TUNING = {
    "schema_version": 1,
    "trial_count": 5,
    "seed": 7,
    "metric": "gini",
    "search_space": {"depth": [4, 6]},
}


def _node(node_type: NodeType, config: dict[str, Any], label: str = "n") -> GraphNode:
    return GraphNode(id=label, data=NodeData(label=label, nodeType=node_type, config=config))


# ---------------------------------------------------------------------------
# The models
# ---------------------------------------------------------------------------


def test_every_node_type_has_a_config_model_describing_every_key() -> None:
    assert set(NODE_CONFIG_MODELS) == set(NodeType)
    for node_type in NodeType:
        undescribed = [path for path, _type, text in config_key_rows(node_type) if not text]
        assert undescribed == [], f"{node_type.value} keys without a description"


@pytest.mark.parametrize("node_type", list(NodeType), ids=lambda node_type: node_type.value)
def test_every_palette_default_passes_its_model(node_type: NodeType) -> None:
    """A new node's config is valid; a new Edge Join has no keys until the user picks them."""
    config = palette_default_config(node_type)
    validate_node_config(node_type, config, require_complete=False, shape_only=True)
    if node_type is NodeType.EDGE_JOIN:
        with pytest.raises(ConfigError):
            validate_node_config(node_type, config, require_complete=False)
    else:
        validate_node_config(node_type, config, require_complete=False)


def _example_pipelines() -> list[Path]:
    assistant = REPO / "src" / "haute" / "assistant" / "assets" / "examples"
    evaluation = REPO / "tests" / "assistant_eval" / "projects"
    return [
        REPO / "examples" / "reference" / "main.py",
        *sorted(
            path / "pipeline.py"
            for path in assistant.iterdir()
            if path.name != "invalid_adversarial" and (path / "pipeline.py").is_file()
        ),
        *sorted(
            path / "main.py"
            for path in evaluation.iterdir()
            if path.name not in {"broken_pricing", "data_recovery"} and (path / "main.py").is_file()
        ),
    ]


@pytest.mark.parametrize(
    "pipeline", _example_pipelines(), ids=lambda path: str(path.relative_to(REPO))
)
def test_every_bundled_example_pipeline_parses_under_the_models(pipeline: Path) -> None:
    """The parse holds every sidecar and decorator config to its node type's model."""
    from haute.parser import parse_pipeline_file
    from tests._project import use_project

    use_project(pipeline.parent)
    graph = parse_pipeline_file(pipeline)
    assert graph.nodes


@pytest.mark.parametrize(
    ("node_type", "config", "setting"),
    [
        pytest.param(NodeType.BANDING, {"factors": [{"banding": "steps"}]}, "factors[0].banding"),
        pytest.param(
            NodeType.BANDING,
            {"factors": [{"banding": "breakpoints", "rightClosed": "yes"}]},
            "factors[0].rightClosed",
        ),
        pytest.param(
            NodeType.EDGE_JOIN,
            {"how": "left", "on": "a", "maintainOrder": None},
            "maintainOrder",
            id="null-where-not-taken",
        ),
        pytest.param(
            NodeType.OUTPUT,
            {
                "outputMapping": [
                    {
                        "source_port": "a",
                        "source_column": "b",
                        "output_path": "$[:].b",
                        "enabled": True,
                        "note": "x",
                    }
                ]
            },
            "outputMapping[0].note",
            id="undeclared-nested-key",
        ),
        pytest.param(NodeType.SCENARIO_EXPANDER, {"stepCount": 3.0}, "stepCount", id="float-int"),
        pytest.param(NodeType.MODEL_SCORE, {"sourceType": "mlflow"}, "sourceType"),
        pytest.param(NodeType.OPTIMISER_APPLY, {"sourceType": "artifact"}, "sourceType"),
        pytest.param(NodeType.MODELLING, {"mlflow_destination": "local"}, "mlflow_destination"),
        pytest.param(
            NodeType.DATA_INPUT, {"inputType": "file", "format": "csv", "path": 3}, "path"
        ),
        pytest.param(NodeType.DATA_INPUT, {"path": "x.csv"}, "inputType", id="no-branch"),
        pytest.param(NodeType.CONSTANT, {"values": [{"name": 1, "value": "x"}]}, "values[0].name"),
        pytest.param(
            NodeType.POLARS, {"contract": {"inputs": [1], "outputs": []}}, "contract.inputs[0]"
        ),
    ],
)
def test_a_value_its_shape_refuses_names_its_path(
    node_type: NodeType, config: dict[str, Any], setting: str
) -> None:
    with pytest.raises(NodeConfigError) as refused:
        validate_node_config(node_type, config, require_complete=False, node_label="node")
    assert refused.value.setting == setting
    assert f"'{setting}'" in str(refused.value)
    assert "'node'" in str(refused.value)


@pytest.mark.parametrize(
    "key",
    [
        "tuning",
        "loss_function",
        "regularization",
        "alpha",
        "max_iter",
        "tol",
        "robust_standard_errors",
        "theta",
        "var_power",
        "variance_power",
        "offset",
        "monotone_constraints",
        "row_limit",
    ],
)
def test_a_model_training_key_the_editor_clears_takes_null(key: str) -> None:
    validate_node_config(
        NodeType.MODELLING, {"algorithm": "glm", key: None}, require_complete=False
    )


@pytest.mark.parametrize(
    ("node_type", "config"),
    [
        (NodeType.EDGE_JOIN, {"how": "left", "on": "a", "maintainOrder": "left", "validate": None}),
        (NodeType.BANDING, {"factors": [{"banding": "categorical", "default": None}]}),
        (
            NodeType.RATING_STEP,
            {"tables": [{"factors": [], "outputColumn": "", "entries": [], "defaultValue": None}]},
        ),
    ],
)
def test_other_keys_haute_clears_take_null(node_type: NodeType, config: dict[str, Any]) -> None:
    validate_node_config(node_type, config, require_complete=False)


def test_validation_returns_the_config_unchanged() -> None:
    config = {"column_name": "value", "max_value": 2, "min_value": 1, "stepCount": 3}
    validated = validate_node_config(NodeType.SCENARIO_EXPANDER, config, require_complete=False)
    assert validated == config
    assert validated is not config
    assert list(validated) == list(config)
    assert type(validated["min_value"]) is int


def test_time_limits_are_not_config_keys() -> None:
    assert unrecognized_config_keys(NodeType.MODELLING, {"timeout": 5}) == ["timeout"]
    assert unrecognized_config_keys(NodeType.OPTIMISER, {"auto_range_timeout": 5}) == [
        "auto_range_timeout"
    ]


# ---------------------------------------------------------------------------
# The engine's rules the models call
# ---------------------------------------------------------------------------


def test_model_training_checks_its_evaluation_tuning_and_device() -> None:
    with pytest.raises(TrainingConfigError, match="evaluation"):
        validate_node_config(
            NodeType.MODELLING,
            {"algorithm": "catboost", "evaluation": {"schema_version": 1, "strategy": "nope"}},
            require_complete=False,
        )
    with pytest.raises(TrainingConfigError, match="tuning"):
        validate_node_config(
            NodeType.MODELLING,
            {"algorithm": "glm", "tuning": {"garbage": True}},
            require_complete=False,
        )
    # A valid tuning with no evaluation chosen yet passes: its split rules wait for one.
    validate_node_config(
        NodeType.MODELLING, {"algorithm": "catboost", "tuning": TUNING}, require_complete=False
    )
    with pytest.raises(TrainingConfigError, match="CPU only"):
        validate_node_config(
            NodeType.MODELLING, {"algorithm": "catboost", "device": "gpu"}, require_complete=False
        )


def test_model_training_names_what_an_unfinished_config_lacks() -> None:
    gaps = {gap.path for gap in config_gaps(NodeType.MODELLING, {"algorithm": "catboost"})}
    assert {"target", "feature_columns", "evaluation"} <= gaps
    complete = {
        "algorithm": "catboost",
        "target": "y",
        "loss_function": "RMSE",
        "feature_columns": ["x"],
        "evaluation": EVALUATION,
    }
    assert config_gaps(NodeType.MODELLING, complete) == []
    with pytest.raises(NodeConfigError, match="target"):
        validate_node_config(NodeType.MODELLING, {**complete, "target": ""}, require_complete=True)


def test_a_source_switch_maps_only_what_it_routes() -> None:
    # No input mapped: the switch passes its first input through.
    validate_node_config(NodeType.LIVE_SWITCH, {}, require_complete=True, input_names=["a"])
    with pytest.raises(NodeConfigError) as refused:
        validate_node_config(
            NodeType.LIVE_SWITCH, {"input_scenario_map": {"a": ""}}, require_complete=False
        )
    assert refused.value.setting == "input_scenario_map.a"
    gaps = config_gaps(
        NodeType.LIVE_SWITCH, {"input_scenario_map": {"gone": "live"}}, input_names=["a"]
    )
    assert [(gap.path, gap.code) for gap in gaps] == [
        ("input_scenario_map.gone", "invalid_reference")
    ]


def test_an_optimiser_apply_artifact_needs_its_source_type() -> None:
    with pytest.raises(ConfigError, match="requires sourceType"):
        validate_node_config(
            NodeType.OPTIMISER_APPLY, {"artifact_path": "result.json"}, require_complete=False
        )


def test_an_incomplete_model_score_source_is_unfinished_not_malformed() -> None:
    config = {"sourceType": "run", "run_id": "", "output_column": "pred", "task": "regression"}
    validate_node_config(NodeType.MODEL_SCORE, config, require_complete=False)
    assert [gap.path for gap in config_gaps(NodeType.MODEL_SCORE, config)] == ["run_id"]


def test_an_incomplete_model_score_saves_with_its_offline_contract() -> None:
    from haute.codegen import _derive_contract_for_codegen

    node = _node(
        NodeType.MODEL_SCORE,
        {"sourceType": "run", "run_id": "", "output_column": "pred", "task": "regression"},
    )
    contract = _derive_contract_for_codegen(node)
    assert contract.inputs is None
    assert contract.outputs == frozenset({"pred"})


# ---------------------------------------------------------------------------
# The deploy scorer's private keys
# ---------------------------------------------------------------------------


def test_the_deploy_scorers_private_keys_pass_a_run_and_nothing_else() -> None:
    scored = {
        "sourceType": "file",
        "model_path": "m.cbm",
        _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY: ["a"],
    }
    validate_node_config(NodeType.MODEL_SCORE, scored, require_complete=False, persisted=False)
    with pytest.raises(ConfigError, match=_DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY):
        validate_node_config(NodeType.MODEL_SCORE, scored, require_complete=False)
    with pytest.raises(ConfigError, match="invalid internal deploy config"):
        validate_node_config(
            NodeType.MODEL_SCORE,
            {**scored, _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY: ["a", "a"]},
            require_complete=False,
            persisted=False,
        )
    answered = {"outputMapping": [], DEPLOY_ANSWER_PER_ROW_CONFIG_KEY: True}
    validate_node_config(NodeType.OUTPUT, answered, require_complete=False, persisted=False)
    with pytest.raises(ConfigError, match=DEPLOY_ANSWER_PER_ROW_CONFIG_KEY):
        validate_node_config(
            NodeType.CONSTANT,
            {"values": [], DEPLOY_ANSWER_PER_ROW_CONFIG_KEY: True},
            require_complete=False,
            persisted=False,
        )


# ---------------------------------------------------------------------------
# The boundaries
# ---------------------------------------------------------------------------


def test_building_a_node_holds_a_request_graphs_config_to_its_model() -> None:
    with pytest.raises(NodeConfigError) as refused:
        _build_node_fn(_node(NodeType.SCENARIO_EXPANDER, {"stepCount": "3"}))
    assert refused.value.setting == "stepCount"


def test_building_a_source_switch_refuses_an_empty_scenario_on_an_inactive_input() -> None:
    switch = _node(NodeType.LIVE_SWITCH, {"input_scenario_map": {"good": "live", "bad": ""}})
    with pytest.raises(NodeConfigError, match="empty source"):
        _build_node_fn(switch, source_names=["good", "bad"], source="live")


def test_building_a_rating_step_checks_its_tables_before_its_steps() -> None:
    from haute._validation_error import ConfigSettingError

    config = {
        "tables": [
            {"factors": ["age"], "outputColumn": "f", "entries": [{"age": "a", "value": "x"}]}
        ],
        "steps": [{}],
    }
    with pytest.raises(ConfigSettingError):
        _build_node_fn(_node(NodeType.RATING_STEP, config))


def test_a_standalone_sidecar_load_holds_it_to_its_model(tmp_path: Path) -> None:
    sidecar = tmp_path / "rates.json"
    sidecar.write_text(json.dumps({"values": [{"name": 3, "value": "1"}]}), encoding="utf-8")
    with pytest.raises(NodeConfigError) as refused:
        load_node_config("rates.json", base_dir=tmp_path, node_type=NodeType.CONSTANT)
    assert refused.value.setting == "values[0].name"


@pytest.mark.parametrize(
    "node_type",
    [NodeType.MODELLING, NodeType.DATA_OUTPUT, NodeType.EXTERNAL_FILE],
    ids=lambda node_type: node_type.value,
)
def test_a_standalone_passthrough_checks_its_sidecar(tmp_path: Path, node_type: NodeType) -> None:
    """A run that only passes the frame on still refuses a missing or malformed sidecar."""
    import polars as pl

    from haute._standalone_nodes import run_configured_node

    source = "def node(df): ...\n"
    path = tmp_path / "main.py"
    path.write_text(source, encoding="utf-8")
    namespace: dict[str, Any] = {"__file__": str(path)}
    exec(compile(source, str(path), "exec"), namespace)
    frame = pl.LazyFrame({"a": [1]})
    with pytest.raises((ConfigError, FileNotFoundError, OSError)):
        run_configured_node(
            node_type,
            "declaration",
            name="node",
            config={"config": "config/missing.json"},
            fn=namespace["node"],
            frames=[frame],
        )
    sidecar = tmp_path / "config" / "bad.json"
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps({"undeclared": True}), encoding="utf-8")
    with pytest.raises(ConfigError, match="undeclared"):
        run_configured_node(
            node_type,
            "declaration",
            name="node",
            config={"config": "config/bad.json"},
            fn=namespace["node"],
            frames=[frame],
        )


def test_a_route_answering_with_a_transformed_graph_refuses_a_config_its_shape_refuses() -> None:
    graph = PipelineGraph(nodes=[_node(NodeType.CONSTANT, {"values": [], "stray": 1})], edges=[])
    with pytest.raises(ConfigError, match="stray"):
        validate_graph_configs(graph)
    # Only the shape: the save that follows holds the graph to the whole model.
    validate_graph_configs(
        PipelineGraph(
            nodes=[_node(NodeType.EDGE_JOIN, {"how": "left", "maintainOrder": "left"})], edges=[]
        )
    )
