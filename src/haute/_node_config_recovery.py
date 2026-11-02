# ruff: noqa: E501
"""Pure, conservative recovery of persisted node configuration.

This module deliberately knows nothing about projects, source files, or execution.
It makes an editable candidate and explains every value it could not retain.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import UnionType
from typing import Any, Literal, get_args, get_origin, get_type_hints

from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
from haute._config_io import palette_default_config
from haute._config_validation import (
    _TYPED_DICT_BY_NODE_TYPE,
    FORM_GIVEN_KEYS,
    VALID_KEYS,
    validate_optimiser_input_selectors,
)
from haute._contracts import Contract
from haute._edge_join import build_edge_join_kwargs
from haute._explore_charts import validate_explore_charts
from haute._explore_overview import validate_explore_overview
from haute._explore_pivots import validate_explore_pivot_state
from haute._model_source import MODEL_SOURCE_TYPES, parse_model_source
from haute._node_apply import constant_frame, scenario_step_count, validate_external_file_config
from haute._output_assembler import OutputMappingSchemaError, validate_v2_output_mapping
from haute._polars_io_registry import (
    PolarsIoConfigError,
    validate_data_input_config,
    validate_data_output_config,
)
from haute._polars_steps import (
    DEFAULT_JOIN_ORDER,
    PolarsStepError,
    is_stepped_config,
    render_polars_steps,
    stepped_surface_for,
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
from haute.errors import ConfigError, HauteError
from haute.modelling._descriptors import DESCRIPTORS
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
    "inputMapping": dict[str, str],
    "selected_columns": list[str],
    "column_renames": dict[str, str],
    "contract": Any,
}
_STRUCTURAL = {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}
_DISCRIMINANTS = {
    NodeType.DATA_INPUT: ("inputType", {"file", "database", "lakehouse", "databricks", "inline"}),
    NodeType.DATA_OUTPUT: ("outputType", {"file", "database", "lakehouse", "databricks"}),
    NodeType.MODEL_SCORE: ("sourceType", {"run", "registered", "file"}),
    NodeType.MODELLING: ("algorithm", set(DESCRIPTORS)),
    NodeType.OPTIMISER: ("mode", {"online", "ratebook"}),
    NodeType.OPTIMISER_APPLY: ("sourceType", {"file", "run", "registered"}),
}


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
    # A workbench node is given these from the form where it runs; a config holding them,
    # as an earlier Haute's copies, has them removed as retired fields.
    for key in FORM_GIVEN_KEYS.get(node_type, frozenset()):
        result.pop(key, None)
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


_ENGINE_REFUSALS = (
    ApiInputSchemaError,
    OutputMappingSchemaError,
    PolarsIoConfigError,
    ConfigError,
    TrainingConfigError,
    ValueError,
)


def _refused_setting(node_type: NodeType, exc: BaseException) -> str | None:
    """The one setting an engine refusal names, or ``None`` for a whole-config refusal.

    A ``setting`` attribute names it, as do a ``field``/``missing_field`` context
    entry; otherwise the refusal names it when exactly one context entry is a
    setting of the node type.
    """
    setting = getattr(exc, "setting", None)
    if isinstance(setting, str):
        return setting
    if not isinstance(exc, HauteError):
        return None
    for key in ("field", "missing_field"):
        if isinstance(exc.context.get(key), str):
            return str(exc.context[key])
    named = [key for key in exc.context if key in VALID_KEYS.get(node_type, ())]
    return named[0] if len(named) == 1 else None


def _engine_issues(
    node_type: NodeType, config: Mapping[str, Any], check: Callable[[], object]
) -> list[RecoveryIssue]:
    """The engine's own refusal of *config*, with its message, at the setting it names."""
    try:
        check()
    except _ENGINE_REFUSALS as exc:
        # The wording the engine's own public error contract gives the refusal.
        message = exc.to_payload()["message"] if isinstance(exc, HauteError) else str(exc)
        setting = _refused_setting(node_type, exc)
        if setting is None:
            return [_issue("", "invalid_group", message)]
        code = "required" if config.get(setting) in (None, "") else "invalid_value"
        return [_issue(_pointer("", setting), code, message)]
    return []


def _validator_issues(
    node_type: NodeType, config: dict[str, Any], input_names: Sequence[str] | None
) -> list[RecoveryIssue]:
    """The engine's refusals of *config*, from the validators the engine itself runs.

    Every path is a JSON pointer into the config. The rules written here are for
    the node types whose engine has no validator to call.
    """
    issues: list[RecoveryIssue] = []

    def engine(check: Callable[[], object]) -> None:
        issues.extend(_engine_issues(node_type, config, check))

    if node_type == NodeType.API_INPUT and "tables" in config:  # the Quote Input alone
        engine(lambda: validate_v2_schema(config))
    elif node_type is NodeType.DATA_INPUT:
        # Missing locators are completeness, not a recovery error.
        engine(lambda: validate_data_input_config(config, require_complete=False))
    elif node_type is NodeType.DATA_OUTPUT:
        engine(lambda: validate_data_output_config(config, require_complete=False))
    elif node_type is NodeType.BANDING:
        engine(lambda: validate_banding_config(config))
    elif node_type is NodeType.RATING_STEP:
        engine(lambda: normalise_rating_step_config(config))
    elif node_type is NodeType.EXPLORE:

        def explore() -> None:
            validate_explore_overview(config.get("overview", {}), context="recovery")
            validate_explore_pivot_state(
                config.get("pivot_formulas", []), config.get("pivots", []), context="recovery"
            )
            validate_explore_charts(config.get("charts", []), context="recovery")

        engine(explore)
    elif node_type is NodeType.OUTPUT:
        engine(lambda: validate_v2_output_mapping(config.get("outputMapping", [])))
    elif node_type is NodeType.MODELLING and "evaluation" in config:

        def modelling() -> None:
            evaluation = parse_evaluation_config(config["evaluation"])
            parse_tuning_config(
                config.get("tuning"),
                algorithm=str(config.get("algorithm", "")),
                base_params=config.get("params", {}),
                evaluation=evaluation,
                configured_metrics=config.get("metrics", []),
            )

        engine(modelling)
    elif node_type in (NodeType.OPTIMISER, NodeType.OPTIMISER_APPLY) and input_names is not None:
        engine(
            lambda: validate_optimiser_input_selectors(
                node_type, config, input_names, node_label="recovery"
            )
        )
    if issues:
        # A refused structure leaves nothing for the field rules below to judge.
        return issues
    if node_type is NodeType.EDGE_JOIN:
        engine(lambda: build_edge_join_kwargs(config))
    elif node_type is NodeType.SCENARIO_EXPANDER:
        engine(lambda: scenario_step_count(config))
    elif node_type is NodeType.CONSTANT:
        engine(lambda: constant_frame(config.get("values") or []))
    elif node_type is NodeType.EXTERNAL_FILE:
        engine(lambda: validate_external_file_config(config))
    elif node_type is NodeType.MODEL_SCORE and config.get("sourceType") in MODEL_SOURCE_TYPES:
        engine(lambda: parse_model_source(config))
    if node_type is NodeType.LIVE_SWITCH:
        mapping = config.get("input_scenario_map")
        if not isinstance(mapping, dict):
            issues.append(
                _issue("/input_scenario_map", "required", "A live switch needs a scenario map.")
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
                            _pointer("/input_scenario_map", name),
                            "invalid_reference",
                            "Mapping must use an exact connected input and a non-empty scenario.",
                        )
                    )
    if is_stepped_config(node_type, config):
        # The steps are rendered here: a raw sidecar candidate is not node
        # data, so no derived steps error comes with it. An ``edges`` surface's
        # references can only be checked when the caller knows the connected
        # input names.
        surface = stepped_surface_for(node_type)
        eligible: list[str] | None = (
            [] if surface.inputs == "none" else (None if input_names is None else list(input_names))
        )
        try:
            render_polars_steps(config["steps"], eligible, start=surface.start)
        except PolarsStepError as exc:
            issues.append(_issue("/steps", "incomplete", str(exc)))
    elif node_type is NodeType.POLARS and (
        not isinstance(config.get("code"), str) or not config["code"].strip()
    ):
        issues.append(_issue("/code", "required", "Polars code must be non-empty."))
    elif node_type is NodeType.POLARS:
        try:
            ast.parse(config["code"])
        except SyntaxError as exc:
            issues.append(_issue("/code", "invalid_syntax", exc.msg))
    if node_type is NodeType.OPTIMISER_APPLY:
        source = config.get("sourceType")
        source_field = (
            "run_id"
            if source == "run"
            else "registered_model"
            if source == "registered"
            else "artifact_path"
        )
        if source and (not isinstance(config.get(source_field), str) or not config[source_field]):
            issues.append(
                _issue(
                    _pointer("", source_field),
                    "required",
                    f"{source_field} is required for {source}.",
                )
            )
    if node_type is NodeType.MODELLING:
        for field in ("target", "algorithm"):
            if not isinstance(config.get(field), str) or not config[field]:
                issues.append(_issue(_pointer("", field), "required", f"{field} is required."))
        if config.get("algorithm") in DESCRIPTORS:
            objective_problem = training_objective_issue(config)
            if objective_problem:
                issues.append(_issue("", "incomplete_objective", objective_problem))
    if node_type is NodeType.OPTIMISER:
        if not isinstance(config.get("objective"), str) or not config["objective"]:
            issues.append(_issue("/objective", "required", "Optimiser objective is required."))
        for field in ("max_iter", "frontier_steps", "max_cd_iterations"):
            if field in config and (type(config[field]) is not int or config[field] <= 0):
                issues.append(
                    _issue(
                        _pointer("", field), "invalid_value", "Control must be a positive integer."
                    )
                )
        for field in ("tolerance", "cd_tolerance"):
            if field in config and (type(config[field]) not in (int, float) or config[field] <= 0):
                issues.append(
                    _issue(
                        _pointer("", field), "invalid_value", "Tolerance must be a positive number."
                    )
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
            issues.append(
                _issue(
                    _pointer("", key), "unknown_field", "Unknown configuration field.", "warning"
                )
            )
        elif not _valid(value, hints[key]):
            issues.append(
                _issue(
                    _pointer("", key), "invalid_value", "Value does not match its declared shape."
                )
            )
            has_shape_errors = True
    discriminant = _DISCRIMINANTS.get(node_type)
    if discriminant:
        value = config.get(discriminant[0])
        if not isinstance(value, str) or value not in discriminant[1]:
            issues.append(
                _issue(
                    _pointer("", discriminant[0]),
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
            issues.append(_issue("/contract", "invalid_value", str(exc)))
    issues.extend(_validator_issues(node_type, config, input_names))
    return issues


# Fields besides the discriminant that select a Data Input/Output branch: the
# palette is a parquet scan/sink, whose defaults do not fit another format or mode.
_PALETTE_BRANCH_FIELDS = {
    NodeType.DATA_INPUT: ("format", "mode"),
    NodeType.DATA_OUTPUT: ("format", "mode"),
}


def _palette_is_branch_of(
    node_type: NodeType, source: dict[str, Any], defaults: dict[str, Any]
) -> bool:
    """Whether the palette default describes *source*'s branch, so it may fill its gaps."""
    discriminant = _DISCRIMINANTS.get(node_type)
    if discriminant and source.get(discriminant[0]) != defaults.get(discriminant[0]):
        return False
    return all(
        field not in source or source[field] == defaults.get(field)
        for field in _PALETTE_BRANCH_FIELDS.get(node_type, ())
    )


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
    defaults = palette_default_config(node_type)
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
        if key == "contract":
            try:
                Contract.from_user_declared(value)
            except ValueError:
                # The annotation is generated scaffolding: the regenerated
                # decorator derives it from the settings, as for a stale one.
                changes.append(
                    RecoveryFieldChange(
                        path=path,
                        outcome="removed",
                        reason="The contract annotation could not be read; it is derived again "
                        "from the settings.",
                    )
                )
                continue
        default = defaults.get(key) if key != discriminant_field else None
        recovered = _reconcile_value(value, hints[key], default, _pointer("", key), changes, issues)
        if recovered is not _UNRECOVERED:
            candidate[key] = recovered
    if not reset and _palette_is_branch_of(node_type, source, defaults):
        # A field added or renamed since the file was saved takes the palette
        # default, as a new node would. An absent ``steps`` stays absent: it
        # means the body's code runs, and ``[]`` would discard that code.
        for key, default in defaults.items():
            if key in source or key == "steps":
                continue
            reason = "Missing from the saved configuration; the palette default was used."
            if node_type is NodeType.EDGE_JOIN and key == "maintainOrder":
                # The order a new join of this type gets, as the editor writes it.
                default = DEFAULT_JOIN_ORDER.get(str(candidate.get("how")), default)
                reason = (
                    "Missing from the saved configuration; the order for this join type was used."
                )
            candidate[key] = default
            changes.append(
                RecoveryFieldChange(path=_pointer("", key), outcome="defaulted", reason=reason)
            )
    issues.extend(validate_recovery_config(node_type, candidate))
    return ConfigRecoveryResult(candidate, changes, issues)
