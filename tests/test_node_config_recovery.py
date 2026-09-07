from __future__ import annotations

import pytest

from haute._node_config_recovery import (
    node_config_schema,
    reconcile_config,
    validate_recovery_config,
)
from haute._types import NodeType


def test_data_input_retains_valid_path_and_mode_when_argument_is_invalid() -> None:
    result = reconcile_config(
        NodeType.DATA_INPUT,
        {
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": "quotes.parquet",
            "arguments": "bad",
        },
    )
    assert result.config["path"] == "quotes.parquet"
    assert result.config["mode"] == "scan"
    assert any(change.path == "/arguments" for change in result.changes)


def test_unknown_discriminant_is_not_defaulted() -> None:
    result = reconcile_config(NodeType.DATA_INPUT, {"inputType": "oracle", "path": "x"})
    assert "inputType" not in result.config
    assert "format" not in result.config
    assert any(issue.code == "unknown_discriminant" for issue in result.issues)


def test_live_switch_default_is_current_scenario_map() -> None:
    result = reconcile_config(NodeType.LIVE_SWITCH, {}, reset=True)
    assert result.config == {"input_scenario_map": {}}


def test_validation_uses_exact_live_switch_input_names() -> None:
    issues = validate_recovery_config(
        NodeType.LIVE_SWITCH, {"input_scenario_map": {"wrong": "live"}}, input_names=["quotes"]
    )
    assert any(issue.code == "invalid_reference" for issue in issues)


def test_every_node_type_has_a_recovery_schema() -> None:
    assert {node_type.value for node_type in NodeType} == {
        node_type.value
        for node_type in NodeType
        if node_config_schema(node_type)["type"] == "object"
    }


def test_nested_factor_preserves_valid_sibling_when_one_leaf_is_bad() -> None:
    result = reconcile_config(
        NodeType.BANDING,
        {"factors": [{"banding": "continuous", "column": "age", "rightClosed": "no"}]},
    )
    assert result.config["factors"] == [{"banding": "continuous", "column": "age"}]
    assert any(change.path == "/factors/0/rightClosed" for change in result.changes)


def test_existing_database_branch_is_not_given_file_defaults() -> None:
    result = reconcile_config(
        NodeType.DATA_INPUT,
        {"inputType": "database", "format": "database", "connection": "dsn", "query": "select 1"},
    )
    assert "path" not in result.config
    assert result.config["connection"] == "dsn"


def test_structured_contract_is_accepted() -> None:
    assert not validate_recovery_config(
        NodeType.POLARS, {"code": "x = df", "contract": {"inputs": ["a"], "outputs": ["b"]}}
    )


@pytest.mark.parametrize(
    ("node_type", "config", "inputs"),
    [
        (
            NodeType.API_INPUT,
            {"tables": [{"path": "$[:]", "label": "quotes", "columns": []}]},
            None,
        ),
        (
            NodeType.DATA_INPUT,
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "x.parquet",
                "arguments": {},
            },
            None,
        ),
        (
            NodeType.DATA_INPUT,
            {
                "inputType": "database",
                "format": "database",
                "connection": "dsn",
                "query": "select 1",
            },
            None,
        ),
        (
            NodeType.DATA_INPUT,
            {"inputType": "lakehouse", "format": "delta", "mode": "scan", "path": "table"},
            None,
        ),
        (
            NodeType.DATA_INPUT,
            {"inputType": "databricks", "http_path": "http", "table": "catalog.schema.table"},
            None,
        ),
        (
            NodeType.DATA_INPUT,
            {"inputType": "inline", "format": "records", "mode": "read", "records": [{"x": 1}]},
            None,
        ),
        (
            NodeType.DATA_OUTPUT,
            {
                "outputType": "file",
                "format": "parquet",
                "mode": "sink",
                "path": "x.parquet",
                "arguments": {},
            },
            None,
        ),
        (
            NodeType.DATA_OUTPUT,
            {
                "outputType": "database",
                "format": "database",
                "connection": "dsn",
                "table": "target",
            },
            None,
        ),
        (
            NodeType.DATA_OUTPUT,
            {"outputType": "lakehouse", "format": "delta", "mode": "sink", "path": "target"},
            None,
        ),
        (NodeType.POLARS, {"code": "result = df"}, None),
        (NodeType.EDGE_JOIN, {"how": "left", "on": "id", "suffix": "_right"}, None),
        (
            NodeType.MODEL_SCORE,
            {
                "sourceType": "run",
                "run_id": "run",
                "task": "regression",
                "output_column": "prediction",
            },
            None,
        ),
        (
            NodeType.MODEL_SCORE,
            {
                "sourceType": "registered",
                "registered_model": "model",
                "version": "1",
                "task": "regression",
                "output_column": "prediction",
            },
            None,
        ),
        (
            NodeType.BANDING,
            {
                "factors": [
                    {
                        "banding": "continuous",
                        "column": "age",
                        "outputColumn": "age_band",
                        "rules": [{"op1": "<", "val1": 10, "assignment": "young"}],
                    }
                ]
            },
            None,
        ),
        (
            NodeType.RATING_STEP,
            {
                "tables": [
                    {
                        "factors": ["age_band"],
                        "outputColumn": "rate",
                        "entries": [{"age_band": "young", "value": "1.1"}],
                    }
                ]
            },
            None,
        ),
        (
            NodeType.OUTPUT,
            {
                "outputMapping": [
                    {
                        "source_port": "df",
                        "source_column": "x",
                        "output_path": "$[:].x",
                        "enabled": True,
                    }
                ],
                "outputFormat": "json",
            },
            None,
        ),
        (NodeType.EXPLORE, {}, None),
        (NodeType.EXTERNAL_FILE, {"path": "model.pkl", "fileType": "pickle"}, None),
        (NodeType.LIVE_SWITCH, {"input_scenario_map": {"df": "live"}}, ["df"]),
        (
            NodeType.MODELLING,
            {"target": "y", "algorithm": "catboost", "loss_function": "RMSE"},
            None,
        ),
        (
            NodeType.OPTIMISER,
            {"mode": "online", "objective": "premium", "data_input": "df"},
            ["df"],
        ),
        (
            NodeType.SCENARIO_EXPANDER,
            {
                "quote_id": "qid",
                "column_name": "scenario_value",
                "step_column": "scenario_index",
                "min_value": 0.0,
                "max_value": 1.0,
                "steps": 2,
            },
            None,
        ),
        (
            NodeType.OPTIMISER_APPLY,
            {"sourceType": "file", "artifact_path": "opt.json", "optimiser_mode": "online"},
            ["df"],
        ),
        (
            NodeType.OPTIMISER_APPLY,
            {"sourceType": "run", "run_id": "run", "optimiser_mode": "online"},
            ["df"],
        ),
        (
            NodeType.OPTIMISER_APPLY,
            {"sourceType": "registered", "registered_model": "model", "optimiser_mode": "online"},
            ["df"],
        ),
        (
            NodeType.CONSTANT,
            {
                "values": [
                    {"name": "zero", "value": 0},
                    {"name": "flag", "value": False},
                    {"name": "nothing", "value": None},
                ]
            },
            None,
        ),
    ],
)
def test_representative_ordinary_configs_are_valid(
    node_type: NodeType, config: dict, inputs: list[str] | None
) -> None:
    assert not validate_recovery_config(node_type, config, input_names=inputs)
    recovered = reconcile_config(node_type, {**config, "retired_setting": True})
    assert all(recovered.config[key] == value for key, value in config.items())
    assert "retired_setting" not in recovered.config
    assert any(
        change.path == "/retired_setting" and change.outcome == "removed"
        for change in recovered.changes
    )
    assert not validate_recovery_config(node_type, recovered.config, input_names=inputs)
