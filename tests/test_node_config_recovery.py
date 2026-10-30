from __future__ import annotations

from collections.abc import Callable

import pytest

from haute._edge_join import build_edge_join_kwargs
from haute._model_source import parse_model_source
from haute._node_apply import constant_frame, scenario_step_count, validate_external_file_config
from haute._node_config_recovery import (
    node_config_schema,
    reconcile_config,
    validate_recovery_config,
)
from haute._types import NodeType
from haute.errors import ConfigError, HauteError


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


@pytest.mark.parametrize("input_type", ["oracle", 1, True, [], {}])
def test_unknown_discriminant_is_not_defaulted(input_type: object) -> None:
    result = reconcile_config(NodeType.DATA_INPUT, {"inputType": input_type, "path": "x"})
    assert "inputType" not in result.config
    assert "format" not in result.config
    assert any(issue.code == "unknown_discriminant" for issue in result.issues)


@pytest.mark.parametrize(
    ("node_type", "config"),
    [
        (NodeType.DATA_INPUT, {"inputType": [], "inputMapping": {"x": []}}),
        (NodeType.DATA_OUTPUT, {"outputType": {}, "inputMapping": {"x": []}}),
        (NodeType.MODEL_SCORE, {"sourceType": [], "inputMapping": {"x": []}}),
        (NodeType.MODELLING, {"algorithm": {}, "inputMapping": {"x": []}}),
        (NodeType.OPTIMISER, {"mode": [], "inputMapping": {"x": []}}),
        (NodeType.OPTIMISER_APPLY, {"sourceType": {}, "inputMapping": {"x": []}}),
    ],
)
def test_shape_invalid_discriminants_do_not_reach_semantic_validators(
    node_type: NodeType, config: dict[str, object]
) -> None:
    issues = validate_recovery_config(node_type, config)
    assert any(issue.code == "invalid_value" for issue in issues)
    assert any(issue.code == "unknown_discriminant" for issue in issues)


def test_invalid_nested_shape_keeps_field_errors_and_unknown_field_warnings() -> None:
    issues = validate_recovery_config(NodeType.BANDING, {"factors": [None], "future": True})
    assert {(issue.path, issue.code, issue.severity) for issue in issues} == {
        ("/factors", "invalid_value", "error"),
        ("/future", "unknown_field", "warning"),
    }


def test_live_switch_default_is_current_scenario_map() -> None:
    result = reconcile_config(NodeType.LIVE_SWITCH, {}, reset=True)
    assert result.config == {"input_scenario_map": {}}


def test_validation_uses_exact_live_switch_input_names() -> None:
    issues = validate_recovery_config(
        NodeType.LIVE_SWITCH, {"input_scenario_map": {"wrong": "live"}}, input_names=["quotes"]
    )
    assert {(issue.path, issue.code) for issue in issues} == {
        ("/input_scenario_map/quotes", "required"),
        ("/input_scenario_map/wrong", "invalid_reference"),
    }


def _engine_refusal(check: Callable[[], object]) -> str:
    """The engine's refusal in the wording its public error contract gives it."""
    with pytest.raises((ConfigError, ValueError)) as refused:
        check()
    error = refused.value
    return error.to_payload()["message"] if isinstance(error, HauteError) else str(error)


@pytest.mark.parametrize(
    ("node_type", "config", "check", "path", "code"),
    [
        pytest.param(
            NodeType.EDGE_JOIN,
            {"how": "asof", "on": ["quote_id"], "maintainOrder": "left"},
            build_edge_join_kwargs,
            "/how",
            "invalid_value",
            id="edge-join-asof",
        ),
        pytest.param(
            NodeType.EDGE_JOIN,
            {"how": "left", "on": ["quote_id"], "validate": "1:x", "maintainOrder": "left"},
            build_edge_join_kwargs,
            "/validate",
            "invalid_value",
            id="edge-join-validate",
        ),
        pytest.param(
            NodeType.EDGE_JOIN,
            {"how": "left", "maintainOrder": "left"},
            build_edge_join_kwargs,
            "",
            "invalid_group",
            id="edge-join-no-keys",
        ),
        pytest.param(
            NodeType.SCENARIO_EXPANDER,
            {"stepCount": 0},
            scenario_step_count,
            "/stepCount",
            "invalid_value",
            id="scenario-step-count",
        ),
        pytest.param(
            NodeType.CONSTANT,
            {"values": [{"name": "rate", "value": "1"}, {"name": "rate", "value": "2"}]},
            lambda config: constant_frame(config["values"]),
            "/values",
            "invalid_value",
            id="constant-duplicate",
        ),
        pytest.param(
            NodeType.MODEL_SCORE,
            {"sourceType": "run", "run_id": ""},
            parse_model_source,
            "/run_id",
            "required",
            id="model-score-run-id",
        ),
        pytest.param(
            NodeType.EXTERNAL_FILE,
            {"path": "", "fileType": "json"},
            validate_external_file_config,
            "/path",
            "required",
            id="external-file-path",
        ),
    ],
)
def test_an_issue_is_the_engines_own_refusal_at_the_setting_it_names(
    node_type: NodeType,
    config: dict[str, object],
    check: Callable[[dict[str, object]], object],
    path: str,
    code: str,
) -> None:
    """Recovery restates no engine rule: it reports the engine's message where the
    engine says, so it can neither accept what the engine refuses nor refuse more."""
    message = _engine_refusal(lambda: check(config))
    assert [
        (issue.path, issue.code, issue.message)
        for issue in validate_recovery_config(node_type, config)
        if issue.severity == "error"
    ] == [(path, code, message)]


@pytest.mark.parametrize("name", [["rate"], 3, None])
def test_a_constant_name_that_is_not_text_is_an_issue_not_a_crash(name: object) -> None:
    """The engine refuses the name with its node-config error, which Recover reports at
    the setting; neither validation nor reconciliation raises."""
    from haute.errors import NodeConfigError

    config = {"values": [{"name": name, "value": "1"}]}
    with pytest.raises(NodeConfigError, match="is not text"):
        constant_frame(config["values"])

    issues = validate_recovery_config(NodeType.CONSTANT, config)
    assert [(issue.path, issue.code) for issue in issues if issue.severity == "error"] == [
        ("/values", "invalid_value")
    ]
    recovered = reconcile_config(NodeType.CONSTANT, config)
    assert any(issue.path == "/values" for issue in recovered.issues)


@pytest.mark.parametrize(
    ("node_type", "config"),
    [
        (NodeType.SCENARIO_EXPANDER, {"stepCount": 3}),
        (NodeType.SCENARIO_EXPANDER, {"stepCount": 3, "min_value": 1.2, "max_value": 0.8}),
        (NodeType.CONSTANT, {"values": [{"name": "Prämie 2024", "value": "1"}, {"value": "2"}]}),
        (NodeType.CONSTANT, {}),
    ],
)
def test_recovery_refuses_nothing_the_engine_runs(
    node_type: NodeType, config: dict[str, object]
) -> None:
    assert not [
        issue for issue in validate_recovery_config(node_type, config) if issue.severity == "error"
    ]


@pytest.mark.parametrize(
    ("node_type", "config", "inputs"),
    [
        (NodeType.BANDING, {"factors": [None], "future": True}, None),
        (NodeType.EDGE_JOIN, {"how": "asof", "on": [1]}, None),
        (NodeType.LIVE_SWITCH, {"input_scenario_map": {"a/b": ""}}, ["quotes"]),
        (NodeType.LIVE_SWITCH, {}, None),
        (NodeType.SCENARIO_EXPANDER, {"stepCount": "many"}, None),
        (NodeType.CONSTANT, {"values": [{"name": "a"}, {"name": "a"}]}, None),
        (NodeType.EXTERNAL_FILE, {"fileType": "catboost"}, None),
        (NodeType.MODEL_SCORE, {"sourceType": "registered"}, None),
        (NodeType.OPTIMISER_APPLY, {"sourceType": "run"}, ["df"]),
        (NodeType.MODELLING, {"algorithm": "glm"}, None),
        (NodeType.OPTIMISER, {"max_iter": 0}, None),
        (NodeType.POLARS, {"code": "df = ("}, None),
        (NodeType.POLARS, {"contract": {"inputs": 3}}, None),
        (NodeType.DATA_INPUT, {"inputType": "oracle"}, None),
    ],
)
def test_every_issue_path_is_a_json_pointer(
    node_type: NodeType, config: dict[str, object], inputs: list[str] | None
) -> None:
    issues = validate_recovery_config(node_type, config, input_names=inputs)
    assert issues
    assert all(issue.path == "" or issue.path.startswith("/") for issue in issues), [
        issue.path for issue in issues
    ]


def test_every_node_type_has_a_recovery_schema() -> None:
    assert {node_type.value for node_type in NodeType} == {
        node_type.value
        for node_type in NodeType
        if node_config_schema(node_type)["type"] == "object"
    }


def test_nested_factor_preserves_valid_sibling_when_one_leaf_is_bad() -> None:
    result = reconcile_config(
        NodeType.BANDING,
        {"factors": [{"banding": "breakpoints", "column": "age", "rightClosed": "no"}]},
    )
    assert result.config["factors"] == [{"banding": "breakpoints", "column": "age"}]
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
        (
            NodeType.EDGE_JOIN,
            {"how": "left", "maintainOrder": "left_right", "on": "id", "suffix": "_right"},
            None,
        ),
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
            NodeType.MODEL_SCORE,
            {
                "sourceType": "file",
                "model_path": "models/freq.cbm",
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
                        "banding": "breakpoints",
                        "column": "age",
                        "outputColumn": "age_band",
                        "rules": [{"boundary": "10", "label": "young"}],
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
                "stepCount": 2,
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


def test_recover_preserves_valid_absence_of_optional_fields() -> None:
    # A JSON file input legitimately omits mode; recovery must not inject the
    # parquet-oriented palette default and break the config (json cannot scan).
    raw = {
        "inputType": "file",
        "format": "json",
        "path": "quotes.json",
        "arguments": {},
        "code": "",
    }
    result = reconcile_config(NodeType.DATA_INPUT, dict(raw))
    assert result.config == raw
    assert not [change for change in result.changes if change.outcome == "defaulted"]
    assert not [issue for issue in result.issues if issue.severity == "error"]


def test_recover_fills_absent_fields_from_the_palette_of_the_same_branch() -> None:
    raw = {"inputType": "file", "format": "parquet", "path": "quotes.parquet"}
    result = reconcile_config(NodeType.DATA_INPUT, dict(raw))
    # The palette is a parquet file scan, this config's branch, so what the saved
    # file lacks takes its default; an absent ``steps`` stays absent (code mode).
    assert result.config == {**raw, "mode": "scan", "arguments": {}}
    assert {change.path for change in result.changes if change.outcome == "defaulted"} == {
        "/mode",
        "/arguments",
    }


def test_recover_gives_a_field_added_since_the_save_its_palette_default() -> None:
    """A Scenario Expander saved before ``stepCount`` existed comes back runnable."""
    raw = {
        "quote_id": "qid",
        "column_name": "price",
        "step_column": "scenario_index",
        "min_value": 0.1,
        "max_value": 0.3,
    }
    result = reconcile_config(NodeType.SCENARIO_EXPANDER, dict(raw))
    assert result.config == {**raw, "stepCount": 21}
    assert ("/stepCount", "defaulted") in {(c.path, c.outcome) for c in result.changes}
    assert not [issue for issue in result.issues if issue.severity == "error"]


@pytest.mark.parametrize(
    ("how", "order"), [("left", "left_right"), ("right", "right_left"), ("semi", "left")]
)
def test_recover_gives_an_edge_join_saved_without_its_order_the_order_for_its_type(
    how: str, order: str
) -> None:
    """Polars 2 keeps a join's order only when told to, so Edge Join's order is required."""
    raw = {"how": how, "on": ["policy_id"], "suffix": "_right"}

    assert ("/maintainOrder", "required") in {
        (issue.path, issue.code) for issue in validate_recovery_config(NodeType.EDGE_JOIN, raw)
    }
    result = reconcile_config(NodeType.EDGE_JOIN, dict(raw))
    assert result.config == {**raw, "maintainOrder": order}
    assert ("/maintainOrder", "defaulted") in {(c.path, c.outcome) for c in result.changes}
    assert not [issue for issue in result.issues if issue.severity == "error"]


def test_an_unknown_edge_join_order_is_reported() -> None:
    raw = {"how": "left", "on": ["policy_id"], "maintainOrder": "sideways"}
    assert ("/maintainOrder", "invalid_value") in {
        (issue.path, issue.code) for issue in validate_recovery_config(NodeType.EDGE_JOIN, raw)
    }


@pytest.mark.parametrize(
    ("node_type", "raw"),
    [
        pytest.param(NodeType.DATA_INPUT, {"path": "quotes.parquet"}, id="no-discriminant"),
        pytest.param(
            NodeType.DATA_INPUT,
            {"inputType": "file", "format": "parquet", "mode": "read", "path": "q.parquet"},
            id="other-mode",
        ),
        pytest.param(
            NodeType.MODEL_SCORE, {"sourceType": "run", "run_id": "abc"}, id="other-provider"
        ),
    ],
)
def test_recover_never_fills_from_another_branch_palette(
    node_type: NodeType, raw: dict[str, object]
) -> None:
    result = reconcile_config(node_type, dict(raw))
    assert result.config == raw
    assert not [change for change in result.changes if change.outcome == "defaulted"]


def test_recover_missing_required_locator_is_incomplete_not_an_error() -> None:
    raw = {"inputType": "file", "format": "parquet", "mode": "read", "arguments": {}, "code": ""}
    result = reconcile_config(NodeType.DATA_INPUT, dict(raw))
    assert "path" not in result.config
    assert not [issue for issue in result.issues if issue.severity == "error"]


def test_reset_still_seeds_the_full_palette_default() -> None:
    result = reconcile_config(
        NodeType.DATA_INPUT, {"inputType": "file", "path": "keep.parquet"}, reset=True
    )
    assert result.config["format"] == "parquet"
    assert result.config["mode"] == "scan"
    assert result.config["path"] == ""


def test_unrecoverable_list_entries_are_excluded_not_null() -> None:
    first = {
        "banding": "breakpoints",
        "column": "age",
        "outputColumn": "age_band",
        "rules": [{"boundary": "30", "label": "young"}],
        "default": "other",
        "rightClosed": True,
    }
    last = {
        "banding": "categorical",
        "column": "region",
        "outputColumn": "region_band",
        "rules": {"north": "N"},
        "default": None,
        "rightClosed": False,
    }
    result = reconcile_config(NodeType.BANDING, {"factors": [first, "corrupt", last]})
    assert result.config["factors"] == [first, last]
    assert None not in result.config["factors"]
    (removed,) = [change for change in result.changes if change.path == "/factors/1"]
    assert removed.outcome == "removed"
    assert "corrupt" in removed.reason
    (issue,) = [item for item in result.issues if item.path == "/factors/1"]
    assert issue.severity == "warning"


def test_a_file_sourced_model_score_without_its_file_names_model_path() -> None:
    issues = validate_recovery_config(NodeType.MODEL_SCORE, {"sourceType": "file"})
    assert [(issue.path, issue.code) for issue in issues] == [("/model_path", "required")]


def test_a_load_file_naming_catboost_is_an_unsupported_file_type() -> None:
    issues = validate_recovery_config(
        NodeType.EXTERNAL_FILE, {"path": "model.cbm", "fileType": "catboost"}
    )
    assert [(issue.path, issue.code) for issue in issues] == [("/fileType", "invalid_value")]
