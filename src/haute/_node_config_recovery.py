# ruff: noqa: E501
"""Pure, conservative recovery of persisted node configuration.

This module deliberately knows nothing about projects, source files, or execution.
It makes an editable candidate and explains every value it could not retain.
"""

from __future__ import annotations

import ast
import json
import math
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import UnionType
from typing import Any, Literal, cast, get_args, get_origin, get_type_hints

from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
from haute._config_validation import _TYPED_DICT_BY_NODE_TYPE, validate_optimiser_input_selectors
from haute._contracts import Contract
from haute._explore_charts import validate_explore_charts
from haute._explore_overview import validate_explore_overview
from haute._explore_pivots import validate_explore_pivot_state
from haute._output_assembler import OutputMappingSchemaError, validate_v2_output_mapping
from haute._polars_io_registry import (
    PolarsIoConfigError,
    validate_data_input_config,
    validate_data_output_config,
)
from haute._rating import validate_banding_config
from haute._rating_step_config import normalise_rating_step_config
from haute._recovery_schemas import RecoveryFieldChange, RecoveryIssue
from haute._types import (
    DATA_INPUT_CONFIG_TYPES,
    DATA_OUTPUT_CONFIG_TYPES,
    BandingFactor,
    NodeType,
    RatingTable,
)
from haute.errors import ConfigError
from haute.modelling._train_config import (
    TrainingConfigError,
    parse_evaluation_config,
    parse_tuning_config,
    training_objective_issue,
)


@dataclass(frozen=True)
class ConfigRecoveryResult:
    config: dict[str, Any]
    changes: list[RecoveryFieldChange]
    issues: list[RecoveryIssue]


_UNIVERSAL = {
    "instanceOf": str,
    "inputMapping": dict[str, str],
    "selected_columns": list[str],
    "column_renames": dict[str, str],
    "categorical_levels": dict[str, list[str | None]],
    "contract": Any,
}
_STRUCTURAL = {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}
_DISCRIMINANTS = {
    NodeType.DATA_INPUT: ("inputType", {"file", "database", "lakehouse", "databricks", "inline"}),
    NodeType.DATA_OUTPUT: ("outputType", {"file", "database", "lakehouse"}),
    NodeType.MODEL_SCORE: ("sourceType", {"run", "registered"}),
    NodeType.MODELLING: ("algorithm", {"catboost", "glm"}),
    NodeType.OPTIMISER: ("mode", {"online", "ratebook"}),
    NodeType.OPTIMISER_APPLY: ("sourceType", {"file", "run", "registered"}),
}


def _defaults() -> dict[str, dict[str, Any]]:
    return cast(
        dict[str, dict[str, Any]],
        json.loads(Path(__file__).with_name("node_defaults.json").read_text(encoding="utf-8")),
    )


def _typed_dicts(node_type: NodeType) -> tuple[type, ...]:
    if node_type is NodeType.DATA_INPUT:
        return tuple(DATA_INPUT_CONFIG_TYPES)
    if node_type is NodeType.DATA_OUTPUT:
        return tuple(DATA_OUTPUT_CONFIG_TYPES)
    item = _TYPED_DICT_BY_NODE_TYPE.get(node_type)
    return () if item is None else (item,)


def _hints(td: type) -> dict[str, Any]:
    # include inherited TypedDict fields; get_type_hints resolves postponed annotations.
    return get_type_hints(td)


def _field_hints(node_type: NodeType, raw: dict[str, Any]) -> dict[str, Any]:
    result = dict(_UNIVERSAL)
    variants = _typed_dicts(node_type)
    if node_type is NodeType.DATA_INPUT:
        selected = next(
            (
                td
                for td in variants
                if _hints(td).get("inputType")
                and raw.get("inputType") in get_args(_hints(td)["inputType"])
            ),
            None,
        )
        variants = (selected,) if selected else variants
    elif node_type is NodeType.DATA_OUTPUT:
        selected = next(
            (
                td
                for td in variants
                if _hints(td).get("outputType")
                and raw.get("outputType") in get_args(_hints(td)["outputType"])
            ),
            None,
        )
        variants = (selected,) if selected else variants
    for td in variants:
        result.update(_hints(td))
    # The public TypedDict deliberately leaves these sidecar rows broad for
    # backwards compatibility. Recovery needs their declared leaf shapes.
    if node_type is NodeType.BANDING:
        result["factors"] = list[BandingFactor]
    if node_type is NodeType.RATING_STEP:
        result["tables"] = list[RatingTable]
    if node_type is NodeType.CONSTANT:
        result["values"] = list[dict[str, Any]]
    if node_type is NodeType.CONSTANT:
        result["values"] = list[dict[str, Any]]
    return result


def _is_typed_dict(value: Any) -> bool:
    return (
        isinstance(value, type) and issubclass(value, dict) and hasattr(value, "__required_keys__")
    )


def _valid(value: Any, annotation: Any) -> bool:
    if annotation is Any:
        return True
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        return value in args and type(value) is type(next(x for x in args if x == value))
    if origin in (list,):
        return isinstance(value, list) and (not args or all(_valid(v, args[0]) for v in value))
    if origin in (dict,):
        return isinstance(value, dict) and (
            not args or all(_valid(k, args[0]) and _valid(v, args[1]) for k, v in value.items())
        )
    if origin is tuple:
        return isinstance(value, tuple)
    if origin in (UnionType, __import__("typing").Union):
        return any(_valid(value, item) for item in args)
    if _is_typed_dict(annotation):
        return (
            isinstance(value, dict)
            and all(
                key in value and _valid(value[key], hint)
                for key, hint in _hints(annotation).items()
                if key in annotation.__required_keys__
            )
            and all(
                key not in _hints(annotation) or _valid(item, _hints(annotation)[key])
                for key, item in value.items()
            )
        )
    if annotation is bool:
        return type(value) is bool
    if annotation is int:
        return type(value) is int
    if annotation is float:
        return type(value) in (int, float) and math.isfinite(value)
    if annotation is str:
        return type(value) is str
    return isinstance(value, annotation) if isinstance(annotation, type) else True


def _schema(annotation: Any) -> dict[str, Any]:
    origin, args = get_origin(annotation), get_args(annotation)
    if annotation is Any:
        return {}
    if annotation is type(None):
        return {"type": "null"}
    if origin is Literal:
        return {"enum": list(args)}
    if origin is list:
        return {"type": "array", "items": _schema(args[0]) if args else {}}
    if origin is dict:
        return {"type": "object", "additionalProperties": _schema(args[1]) if len(args) > 1 else {}}
    if origin in (UnionType, __import__("typing").Union):
        return {"anyOf": [_schema(item) for item in args]}
    if _is_typed_dict(annotation):
        hints = _hints(annotation)
        return {
            "type": "object",
            "properties": {key: _schema(value) for key, value in hints.items()},
            "required": sorted(annotation.__required_keys__),
        }
    return {
        "type": {str: "string", int: "integer", float: "number", bool: "boolean"}.get(
            annotation, "object"
        )
    }


def node_config_schema(node_type: NodeType) -> dict[str, Any]:
    """Expose the persisted configuration shape without turning it into a loader."""
    if node_type in _STRUCTURAL:
        return {
            "type": "object",
            "properties": {
                key: {"type": "string"} for key in ("definition_id", "file", "name", "instance_of")
            },
            "additionalProperties": True,
            "x-recovery-structural": True,
        }
    hints = _field_hints(node_type, {})
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {key: _schema(value) for key, value in hints.items()},
        "additionalProperties": False,
    }
    if node_type in (NodeType.DATA_INPUT, NodeType.DATA_OUTPUT):
        schema["x-variants"] = [_schema(td) for td in _typed_dicts(node_type)]
    discriminant = _DISCRIMINANTS.get(node_type)
    if discriminant:
        schema["x-discriminant"] = {"field": discriminant[0], "values": sorted(discriminant[1])}
    return schema


def _issue(
    path: str, code: str, message: str, severity: Literal["error", "warning"] = "error"
) -> RecoveryIssue:
    return RecoveryIssue(path=path, code=code, message=message, severity=severity)


def _pointer(path: str, segment: str | int) -> str:
    escaped = str(segment).replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}" if path else f"/{escaped}"


# Distinguishes "nothing recoverable" from a legitimately recovered ``None``.
_UNRECOVERED: Any = object()


def _short_repr(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _reconcile_value(
    value: Any,
    annotation: Any,
    default: Any,
    path: str,
    changes: list[RecoveryFieldChange],
    issues: list[RecoveryIssue],
    *,
    in_collection: bool = False,
) -> Any:
    """Recursively retain independent leaves, including dynamic map keys."""
    origin, args = get_origin(annotation), get_args(annotation)
    if _is_typed_dict(annotation) and isinstance(value, dict):
        object_result: dict[str, Any] = {}
        hints = _hints(annotation)
        for key, item in value.items():
            child_path = _pointer(path, key)
            if key not in hints:
                changes.append(
                    RecoveryFieldChange(
                        path=child_path, outcome="needs_review", reason="Unknown nested field."
                    )
                )
                issues.append(
                    _issue(child_path, "unknown_field", "Unknown nested field.", "warning")
                )
                continue
            child_default = default.get(key) if isinstance(default, dict) else None
            recovered = _reconcile_value(
                item, hints[key], child_default, child_path, changes, issues
            )
            if recovered is not _UNRECOVERED:
                object_result[key] = recovered
        return object_result
    if origin is list and isinstance(value, list):
        item_type = args[0] if args else Any
        list_result: list[Any] = []
        for index, item in enumerate(value):
            child_default = None
            if isinstance(default, list) and index < len(default):
                child_default = default[index]
            recovered = _reconcile_value(
                item,
                item_type,
                child_default,
                _pointer(path, index),
                changes,
                issues,
                in_collection=True,
            )
            if recovered is not _UNRECOVERED:
                list_result.append(recovered)
        return list_result
    if origin is dict and isinstance(value, dict):
        value_type = args[1] if len(args) > 1 else Any
        map_result: dict[str, Any] = {}
        for key, item in value.items():
            child_path = _pointer(path, key)
            if not isinstance(key, str) or not _valid(key, args[0] if args else Any):
                changes.append(
                    RecoveryFieldChange(
                        path=child_path, outcome="needs_input", reason="Invalid map key."
                    )
                )
                issues.append(_issue(child_path, "invalid_value", "Map key needs correction."))
                continue
            recovered = _reconcile_value(
                item, value_type, None, child_path, changes, issues, in_collection=True
            )
            if recovered is not _UNRECOVERED:
                map_result[key] = recovered
        return map_result
    if _valid(value, annotation):
        changes.append(
            RecoveryFieldChange(
                path=path,
                outcome="retained",
                reason="Valid under the current configuration contract.",
            )
        )
        return deepcopy(value)
    if default is not None and _valid(default, annotation):
        changes.append(
            RecoveryFieldChange(
                path=path,
                outcome="defaulted",
                reason="Invalid value replaced with the audited default.",
            )
        )
        issues.append(
            _issue(
                path,
                "invalid_value",
                "Invalid value was replaced with the audited default.",
                "warning",
            )
        )
        return deepcopy(default)
    if in_collection:
        # Never emit a null placeholder into a collection: exclude the entry
        # and keep its original value in the recovery report.
        changes.append(
            RecoveryFieldChange(
                path=path,
                outcome="removed",
                reason="Unrecoverable entry was excluded from the candidate; "
                f"original: {_short_repr(value)}",
            )
        )
        issues.append(
            _issue(
                path,
                "invalid_value",
                "Entry was excluded from the candidate; restore it explicitly if needed.",
                "warning",
            )
        )
        return _UNRECOVERED
    changes.append(
        RecoveryFieldChange(
            path=path,
            outcome="needs_input",
            reason=f"Invalid value has no safe default; original: {_short_repr(value)}",
        )
    )
    issues.append(_issue(path, "invalid_value", "Value needs explicit correction."))
    return _UNRECOVERED


def _validator_issues(
    node_type: NodeType, config: dict[str, Any], input_names: Sequence[str] | None
) -> list[RecoveryIssue]:
    try:
        if node_type is NodeType.API_INPUT and "tables" in config:
            validate_v2_schema(config)
        elif node_type is NodeType.DATA_INPUT:
            # Missing locators are completeness, not a recovery error.
            validate_data_input_config(config, require_complete=False)
        elif node_type is NodeType.DATA_OUTPUT:
            validate_data_output_config(config, require_complete=False)
        elif node_type is NodeType.BANDING:
            validate_banding_config(config)
        elif node_type is NodeType.RATING_STEP:
            normalise_rating_step_config(config)
        elif node_type is NodeType.EXPLORE:
            validate_explore_overview(config.get("overview", {}), context="recovery")
            validate_explore_pivot_state(
                config.get("pivot_formulas", []), config.get("pivots", []), context="recovery"
            )
            validate_explore_charts(config.get("charts", []), context="recovery")
        elif node_type is NodeType.OUTPUT:
            validate_v2_output_mapping(config.get("outputMapping", []))
        elif node_type is NodeType.MODELLING:
            if "evaluation" in config:
                evaluation = parse_evaluation_config(config["evaluation"])
                parse_tuning_config(
                    config.get("tuning"),
                    algorithm=str(config.get("algorithm", "")),
                    base_params=config.get("params", {}),
                    evaluation=evaluation,
                    configured_metrics=config.get("metrics", []),
                )
        elif (
            node_type in (NodeType.OPTIMISER, NodeType.OPTIMISER_APPLY) and input_names is not None
        ):
            validate_optimiser_input_selectors(
                node_type, config, input_names, node_label="recovery"
            )
    except (
        ApiInputSchemaError,
        OutputMappingSchemaError,
        PolarsIoConfigError,
        ConfigError,
        TrainingConfigError,
        ValueError,
    ) as exc:
        return [_issue("", "invalid_group", str(exc))]
    issues: list[RecoveryIssue] = []
    if node_type is NodeType.LIVE_SWITCH:
        mapping = config.get("input_scenario_map")
        if not isinstance(mapping, dict):
            issues.append(
                _issue("input_scenario_map", "required", "A live switch needs a scenario map.")
            )
        elif input_names is not None:
            for name in input_names:
                if name not in mapping:
                    issues.append(
                        _issue(
                            _pointer("/input_scenario_map", name),
                            "required",
                            "Every connected input needs a scenario mapping.",
                        )
                    )
            for name, scenario in mapping.items():
                if name not in input_names or not isinstance(scenario, str) or not scenario:
                    issues.append(
                        _issue(
                            f"input_scenario_map.{name}",
                            "invalid_reference",
                            "Mapping must use an exact connected input and a non-empty scenario.",
                        )
                    )
    if node_type is NodeType.SCENARIO_EXPANDER:
        required = ("quote_id", "column_name", "step_column")
        for field in required:
            if not isinstance(config.get(field), str) or not config[field]:
                issues.append(_issue(field, "required", f"{field} is required."))
        if any(field not in config for field in ("min_value", "max_value", "steps")):
            issues.append(_issue("", "incomplete_range", "Scenario range and steps are required."))
        elif (
            type(config["steps"]) is not int
            or config["steps"] <= 0
            or type(config["min_value"]) not in (int, float)
            or type(config["max_value"]) not in (int, float)
            or config["min_value"] > config["max_value"]
        ):
            issues.append(
                _issue(
                    "",
                    "invalid_range",
                    "Scenario range must have ordered numbers and positive integer steps.",
                )
            )
    if node_type is NodeType.CONSTANT:
        values = config.get("values")
        if not isinstance(values, list):
            issues.append(_issue("values", "required", "Constants require a values list."))
        else:
            names: set[str] = set()
            for index, value in enumerate(values):
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("name"), str)
                    or not value["name"].isidentifier()
                    or value["name"] in names
                    or "value" not in value
                ):
                    issues.append(
                        _issue(
                            f"values[{index}]",
                            "invalid_constant",
                            "Each constant needs a unique valid name and literal value.",
                        )
                    )
                elif value["name"]:
                    names.add(value["name"])
    if node_type is NodeType.POLARS and (
        not isinstance(config.get("code"), str) or not config["code"].strip()
    ):
        issues.append(_issue("code", "required", "Polars code must be non-empty."))
    elif node_type is NodeType.POLARS:
        try:
            ast.parse(config["code"])
        except SyntaxError as exc:
            issues.append(_issue("code", "invalid_syntax", exc.msg))
    if node_type is NodeType.EXTERNAL_FILE:
        if not isinstance(config.get("path"), str) or not config["path"]:
            issues.append(_issue("path", "required", "External file path is required."))
        if config.get("fileType") not in {"pickle", "json", "joblib", "catboost"}:
            issues.append(
                _issue("fileType", "invalid_value", "Select a supported external file type.")
            )
    if node_type in (NodeType.MODEL_SCORE, NodeType.OPTIMISER_APPLY):
        source = config.get("sourceType")
        source_field: str = (
            "run_id"
            if source == "run"
            else "registered_model"
            if source == "registered"
            else "artifact_path"
        )
        if source and (not isinstance(config.get(source_field), str) or not config[source_field]):
            issues.append(
                _issue(source_field, "required", f"{source_field} is required for {source}.")
            )
    if node_type is NodeType.MODELLING:
        for field in ("target", "algorithm"):
            if not isinstance(config.get(field), str) or not config[field]:
                issues.append(_issue(field, "required", f"{field} is required."))
        if config.get("algorithm") in {"catboost", "glm"}:
            objective_problem = training_objective_issue(config)
            if objective_problem:
                issues.append(_issue("", "incomplete_objective", objective_problem))
    if node_type is NodeType.EDGE_JOIN:
        how = config.get("how", "left")
        if how not in {"inner", "left", "right", "full", "semi", "anti", "cross", "asof"}:
            issues.append(_issue("how", "invalid_value", "Select a supported join kind."))
        on, left_on, right_on = config.get("on"), config.get("leftOn"), config.get("rightOn")
        if on is not None and (left_on is not None or right_on is not None):
            issues.append(
                _issue("", "invalid_group", "Use either on or the paired leftOn/rightOn keys.")
            )
        if (left_on is None) != (right_on is None):
            issues.append(
                _issue("", "invalid_group", "leftOn and rightOn must be configured together.")
            )
    if node_type is NodeType.OPTIMISER:
        if not isinstance(config.get("objective"), str) or not config["objective"]:
            issues.append(_issue("objective", "required", "Optimiser objective is required."))
        for field in ("max_iter", "candidate_steps", "frontier_steps", "max_cd_iterations"):
            if field in config and (type(config[field]) is not int or config[field] <= 0):
                issues.append(_issue(field, "invalid_value", "Control must be a positive integer."))
        for field in ("tolerance", "cd_tolerance"):
            if field in config and (type(config[field]) not in (int, float) or config[field] <= 0):
                issues.append(
                    _issue(field, "invalid_value", "Tolerance must be a positive number.")
                )
    return issues


def validate_recovery_config(
    node_type: NodeType, config: dict[str, Any], *, input_names: Sequence[str] | None = None
) -> list[RecoveryIssue]:
    """Pure validation for an editable candidate; it never probes runtime state."""
    if node_type in _STRUCTURAL:
        return [
            _issue(
                "",
                "structural_recovery_required",
                "Submodel identity and ports require structural recovery.",
            )
        ]
    if not isinstance(config, dict):
        return [_issue("", "invalid_config", "Configuration must be an object.")]
    hints = _field_hints(node_type, config)
    issues: list[RecoveryIssue] = []
    has_shape_errors = False
    for key, value in config.items():
        if key not in hints:
            issues.append(_issue(key, "unknown_field", "Unknown configuration field.", "warning"))
        elif not _valid(value, hints[key]):
            issues.append(_issue(key, "invalid_value", "Value does not match its declared shape."))
            has_shape_errors = True
    discriminant = _DISCRIMINANTS.get(node_type)
    if discriminant:
        value = config.get(discriminant[0])
        if not isinstance(value, str) or value not in discriminant[1]:
            issues.append(
                _issue(
                    discriminant[0],
                    "unknown_discriminant",
                    f"Select a supported {discriminant[0]}.",
                )
            )
    if has_shape_errors:
        return issues
    if "contract" in config:
        try:
            Contract.from_user_declared(config["contract"])
        except ValueError as exc:
            issues.append(_issue("contract", "invalid_value", str(exc)))
    issues.extend(_validator_issues(node_type, config, input_names))
    return issues


def reconcile_config(
    node_type: NodeType, raw: dict[str, Any], *, reset: bool = False
) -> ConfigRecoveryResult:
    """Recover valid independent fields and record conservative decisions."""
    changes: list[RecoveryFieldChange] = []
    issues: list[RecoveryIssue] = []
    if node_type in _STRUCTURAL:
        return ConfigRecoveryResult(
            deepcopy(raw),
            [
                RecoveryFieldChange(
                    path="",
                    outcome="blocked",
                    reason="Structural configuration is handled by the submodel recovery service.",
                )
            ],
            [
                _issue(
                    "",
                    "structural_recovery_required",
                    "Submodel identity and ports require structural recovery.",
                )
            ],
        )
    defaults = deepcopy(_defaults().get(node_type.value, {}))
    source = {} if reset else deepcopy(raw)
    default_discriminant = _DISCRIMINANTS.get(node_type)
    if (
        not reset
        and default_discriminant
        and default_discriminant[0] in source
        and source[default_discriminant[0]] != defaults.get(default_discriminant[0])
    ):
        # The palette belongs to one branch (for example file input), never use
        # it to fill a different existing provider's fields.
        defaults = {}
    candidate = defaults if reset else {}
    discriminant = _DISCRIMINANTS.get(node_type)
    discriminant_field = discriminant[0] if discriminant else None
    invalid_discriminant = bool(
        discriminant_field
        and discriminant_field in source
        and (
            not isinstance(source[discriminant_field], str)
            or source[discriminant_field] not in discriminant[1]  # type: ignore[index]
        )
    )
    hints = _field_hints(node_type, source)
    for key, value in source.items():
        path = _pointer("", key)
        if key not in hints:
            changes.append(
                RecoveryFieldChange(
                    path=path,
                    outcome="needs_review"
                    if key in {"baseInput", "joinInput", "scored_input", "factors_input"}
                    else "removed",
                    reason="Unknown or retired configuration field retained only in recovery evidence.",
                )
            )
            issues.append(
                _issue(
                    path,
                    "unknown_field",
                    "Unknown or retired configuration field was removed from the candidate.",
                    "warning",
                )
            )
            continue
        if invalid_discriminant and key == discriminant_field:
            changes.append(
                RecoveryFieldChange(
                    path=path,
                    outcome="needs_input",
                    reason="Unknown branch was not replaced with a default.",
                )
            )
            issues.append(_issue(path, "unknown_discriminant", f"Select a supported {path}."))
            continue
        default = defaults.get(key) if key != discriminant_field else None
        recovered = _reconcile_value(value, hints[key], default, _pointer("", key), changes, issues)
        if recovered is not _UNRECOVERED:
            candidate[key] = recovered
    # Absent fields stay absent: recovery never inserts a palette value for a
    # field the author omitted. Only an explicit reset seeds the full default.
    issues.extend(validate_recovery_config(node_type, candidate))
    return ConfigRecoveryResult(candidate, changes, issues)
