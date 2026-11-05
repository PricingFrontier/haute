# ruff: noqa: E501
"""Pure, conservative recovery of persisted node configuration.

This module deliberately knows nothing about projects, source files, or execution.
It makes an editable candidate and explains every value it could not retain.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType, UnionType
from typing import Any, Literal, get_args, get_origin, get_type_hints

from pydantic import BaseModel, TypeAdapter, ValidationError

from haute._config_io import palette_default_config
from haute._config_validation import CONFIG_REFUSALS, FORM_GIVEN_KEYS, VALID_KEYS
from haute._contracts import Contract
from haute._node_config_models import NODE_CONFIG_MODELS, branch_values, shape_variants
from haute._node_specs import STRUCTURAL_NODE_TYPES, node_type_table
from haute._polars_steps import DEFAULT_JOIN_ORDER
from haute._recovery_schemas import RecoveryFieldChange, RecoveryIssue
from haute._types import NodeType
from haute.errors import HauteError


@dataclass(frozen=True)
class ConfigRecoveryResult:
    config: dict[str, Any]
    changes: list[RecoveryFieldChange]
    issues: list[RecoveryIssue]


_STRUCTURAL = STRUCTURAL_NODE_TYPES


def _discriminant(node_type: NodeType) -> tuple[str, frozenset[str]] | None:
    """The key selecting the config model's branch, and the values it takes."""
    model = NODE_CONFIG_MODELS[node_type]
    if model.discriminant is None:
        return None
    return model.discriminant, branch_values(model)


def _hints(td: type) -> dict[str, Any]:
    # include inherited TypedDict fields; get_type_hints resolves postponed annotations.
    return get_type_hints(td)


def _field_hints(node_type: NodeType, raw: dict[str, Any]) -> dict[str, Any]:
    """Each top-level key the node type's config model declares, with its annotation.

    For a Data Input/Output the branch *raw*'s discriminant names, or every branch.
    """
    variants = shape_variants(NODE_CONFIG_MODELS[node_type].shape)
    discriminant = _discriminant(node_type)
    if len(variants) > 1 and discriminant is not None:
        selected = tuple(
            variant
            for variant in variants
            if raw.get(discriminant[0]) in get_args(_hints(variant).get(discriminant[0]))
        )
        variants = selected or variants
    result: dict[str, Any] = {}
    for variant in variants:
        result.update(_hints(variant))
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
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        try:
            annotation.model_validate(value)
        except ValidationError:
            return False
        return True
    if annotation is bool:
        return type(value) is bool
    if annotation is int:
        return type(value) is int
    if annotation is float:
        return type(value) in (int, float) and math.isfinite(value)
    if annotation is str:
        return type(value) is str
    return isinstance(value, annotation) if isinstance(annotation, type) else True


@functools.cache
def _adapter(annotation: Any) -> TypeAdapter[Any]:
    return TypeAdapter(annotation)


def _shape_reason(value: Any, annotation: Any) -> str:
    """Why *value* does not fit *annotation*, in the config model's own words."""
    try:
        _adapter(annotation).validate_python(value, strict=True)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        first = max(errors, key=lambda error: len(error["loc"]))
        where = "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in first["loc"]
        )
        return f"{first['msg']}{f' (at {where.lstrip(chr(46))})' if where else ''}."
    except TypeError:
        pass
    return "Value does not match its declared shape."


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
    except CONFIG_REFUSALS as exc:
        # The wording the engine's own public error contract gives the refusal.
        message = exc.to_payload()["message"] if isinstance(exc, HauteError) else str(exc)
        setting = _refused_setting(node_type, exc)
        if setting is None:
            return [_issue("", "invalid_group", message)]
        code = "required" if config.get(setting) in (None, "") else "invalid_value"
        return [_issue(_pointer("", setting), code, message)]
    return []


def gap_pointer(path: str) -> str:
    """A config path (``input_scenario_map.quotes``, ``factors[0].banding``) as a JSON pointer."""
    pointer = ""
    for part in path.replace("]", "").replace("[", ".").split("."):
        if part:
            pointer = _pointer(pointer, part)
    return pointer


def _validator_issues(
    node_type: NodeType, config: dict[str, Any], input_names: Sequence[str] | None
) -> list[RecoveryIssue]:
    """The config model's refusals of *config* and what it still lacks.

    The model's ``check`` and ``check_inputs`` refusals at the setting each names;
    when they pass, every ``completeness`` gap. Every path is a JSON pointer into
    the config.
    """
    model = NODE_CONFIG_MODELS[node_type]
    issues: list[RecoveryIssue] = []
    if model.check is not None:
        check = model.check
        issues.extend(_engine_issues(node_type, config, lambda: check(config)))
    if not issues and input_names is not None and model.check_inputs is not None:
        check_inputs = model.check_inputs
        issues.extend(_engine_issues(node_type, config, lambda: check_inputs(config, input_names)))
    if issues or model.completeness is None:
        # A refused value leaves nothing for completeness to judge.
        return issues
    return [
        _issue(gap_pointer(gap.path), gap.code, gap.message)
        for gap in model.completeness(config, input_names)
    ]


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
                _issue(_pointer("", key), "invalid_value", _shape_reason(value, hints[key]))
            )
            has_shape_errors = True
    discriminant = _discriminant(node_type)
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
_PALETTE_BRANCH_FIELDS: Mapping[NodeType, tuple[str, ...]] = node_type_table(
    "palette branch fields",
    {
        NodeType.API_INPUT: (),
        NodeType.WORKBENCH_INPUT: (),
        NodeType.WORKBENCH_OUTPUT: (),
        NodeType.DATA_INPUT: ("format", "mode"),
        NodeType.DATA_OUTPUT: ("format", "mode"),
        NodeType.POLARS: (),
        NodeType.EDGE_JOIN: (),
        NodeType.MODEL_SCORE: (),
        NodeType.BANDING: (),
        NodeType.RATING_STEP: (),
        NodeType.OUTPUT: (),
        NodeType.EXPLORE: (),
        NodeType.EXTERNAL_FILE: (),
        NodeType.LIVE_SWITCH: (),
        NodeType.MODELLING: (),
        NodeType.OPTIMISER: (),
        NodeType.SCENARIO_EXPANDER: (),
        NodeType.OPTIMISER_APPLY: (),
        NodeType.CONSTANT: (),
        NodeType.SUBMODEL: (),
        NodeType.SUBMODEL_PORT: (),
    },
)


@dataclass(frozen=True)
class _DependentDefault:
    """A missing field whose default depends on the rest of the recovered config."""

    value: Callable[[Mapping[str, Any], Any], Any]
    reason: str


def _join_order_default(candidate: Mapping[str, Any], default: Any) -> Any:
    # The order a new join of this type gets, as the editor writes it.
    return DEFAULT_JOIN_ORDER.get(str(candidate.get("how")), default)


_NO_DEPENDENT_DEFAULTS: Mapping[str, _DependentDefault] = MappingProxyType({})

#: The fields of each node type whose palette default is replaced by one the
#: recovered config implies (an Edge Join's order, by its join type).
_DEPENDENT_DEFAULTS: Mapping[NodeType, Mapping[str, _DependentDefault]] = node_type_table(
    "dependent recovery defaults",
    {
        NodeType.API_INPUT: _NO_DEPENDENT_DEFAULTS,
        NodeType.WORKBENCH_INPUT: _NO_DEPENDENT_DEFAULTS,
        NodeType.WORKBENCH_OUTPUT: _NO_DEPENDENT_DEFAULTS,
        NodeType.DATA_INPUT: _NO_DEPENDENT_DEFAULTS,
        NodeType.DATA_OUTPUT: _NO_DEPENDENT_DEFAULTS,
        NodeType.POLARS: _NO_DEPENDENT_DEFAULTS,
        NodeType.EDGE_JOIN: MappingProxyType(
            {
                "maintain_order": _DependentDefault(
                    _join_order_default,
                    "Missing from the saved configuration; the order for this join type was used.",
                )
            }
        ),
        NodeType.MODEL_SCORE: _NO_DEPENDENT_DEFAULTS,
        NodeType.BANDING: _NO_DEPENDENT_DEFAULTS,
        NodeType.RATING_STEP: _NO_DEPENDENT_DEFAULTS,
        NodeType.OUTPUT: _NO_DEPENDENT_DEFAULTS,
        NodeType.EXPLORE: _NO_DEPENDENT_DEFAULTS,
        NodeType.EXTERNAL_FILE: _NO_DEPENDENT_DEFAULTS,
        NodeType.LIVE_SWITCH: _NO_DEPENDENT_DEFAULTS,
        NodeType.MODELLING: _NO_DEPENDENT_DEFAULTS,
        NodeType.OPTIMISER: _NO_DEPENDENT_DEFAULTS,
        NodeType.SCENARIO_EXPANDER: _NO_DEPENDENT_DEFAULTS,
        NodeType.OPTIMISER_APPLY: _NO_DEPENDENT_DEFAULTS,
        NodeType.CONSTANT: _NO_DEPENDENT_DEFAULTS,
        NodeType.SUBMODEL: _NO_DEPENDENT_DEFAULTS,
        NodeType.SUBMODEL_PORT: _NO_DEPENDENT_DEFAULTS,
    },
)


def _palette_is_branch_of(
    node_type: NodeType, source: dict[str, Any], defaults: dict[str, Any]
) -> bool:
    """Whether the palette default describes *source*'s branch, so it may fill its gaps."""
    discriminant = _discriminant(node_type)
    if discriminant and source.get(discriminant[0]) != defaults.get(discriminant[0]):
        return False
    return all(
        field not in source or source[field] == defaults.get(field)
        for field in _PALETTE_BRANCH_FIELDS[node_type]
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
    default_discriminant = _discriminant(node_type)
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
    discriminant = _discriminant(node_type)
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
            dependent = _DEPENDENT_DEFAULTS[node_type].get(key)
            if dependent is not None:
                default = dependent.value(candidate, default)
                reason = dependent.reason
            candidate[key] = default
            changes.append(
                RecoveryFieldChange(path=_pointer("", key), outcome="defaulted", reason=reason)
            )
    candidate = _replace_refused_values(node_type, candidate, defaults, changes, issues)
    issues.extend(validate_recovery_config(node_type, candidate))
    return ConfigRecoveryResult(candidate, changes, issues)


def _replace_refused_values(
    node_type: NodeType,
    candidate: dict[str, Any],
    defaults: Mapping[str, Any],
    changes: list[RecoveryFieldChange],
    issues: list[RecoveryIssue],
) -> dict[str, Any]:
    """Treat a value the model's engine rules refuse as an invalid value, as narrowly as
    the refusal can be isolated.

    A refused list or map keeps every entry that passes on its own and loses each that
    does not, reported ``removed`` with its original value; a refused scalar takes its
    branch's palette default where that passes (``defaulted``) and is otherwise removed
    (``needs_input``). What cannot be isolated (a rule across entries, a refusal of the
    whole config) is left in the candidate, which then fails the model, and the recover
    is refused rather than discarding valid settings beside it.
    """
    check = NODE_CONFIG_MODELS[node_type].check
    if check is None:
        return candidate

    def refusal(config: dict[str, Any]) -> Exception | None:
        try:
            check(config)
        except CONFIG_REFUSALS as exc:
            return exc
        return None

    for _ in range(len(candidate) + 1):
        exc = refusal(candidate)
        if exc is None:
            return candidate
        setting = _refused_setting(node_type, exc)
        key = None if setting is None else setting.replace("[", ".").split(".")[0]
        if key is None or key not in candidate:
            return candidate
        value = candidate[key]
        path = _pointer("", key)
        if isinstance(value, list | dict):
            kept = _isolate_refused_entries(
                node_type, key, value, candidate, refusal, path, changes, issues
            )
            if kept is None:
                return candidate
            candidate[key] = kept
            continue
        # The refusal's outcome replaces what the shape walk recorded for the value.
        changes[:] = [change for change in changes if change.path != path]
        default = defaults.get(key)
        if default is not None and default != value and not isinstance(default, list | dict):
            candidate[key] = deepcopy(default)
            changes.append(
                RecoveryFieldChange(
                    path=path,
                    outcome="defaulted",
                    reason="The value was refused; replaced with the audited default.",
                )
            )
            issues.append(
                _issue(path, "invalid_value", f"{exc} The audited default replaced it.", "warning")
            )
            continue
        candidate.pop(key)
        changes.append(
            RecoveryFieldChange(
                path=path,
                outcome="needs_input",
                reason=f"Refused value has no safe default; original: {_short_repr(value)}",
            )
        )
        issues.append(_issue(path, "invalid_value", str(exc)))
    return candidate


def _isolate_refused_entries(
    node_type: NodeType,
    key: str,
    value: list[Any] | dict[str, Any],
    candidate: dict[str, Any],
    refusal: Callable[[dict[str, Any]], Exception | None],
    path: str,
    changes: list[RecoveryFieldChange],
    issues: list[RecoveryIssue],
) -> list[Any] | dict[str, Any] | None:
    """*value* without the entries the engine refuses on their own, or ``None``.

    An entry is refused when, alone in its collection, the engine refuses it *at this
    setting*: a refusal another setting draws (an invalid sibling field) or the whole
    config draws is not the entry's, so the entry is kept. ``None`` when no entry is
    refused: the refusal is across entries and the caller cannot isolate it.
    """
    items: list[tuple[Any, Any]] = (
        list(enumerate(value)) if isinstance(value, list) else list(value.items())
    )
    refused: dict[Any, Exception] = {}
    for position, item in items:
        alone = [item] if isinstance(value, list) else {position: item}
        exc = refusal({**candidate, key: alone})
        if exc is None:
            continue
        setting = _refused_setting(node_type, exc)
        if setting is not None and setting.replace("[", ".").split(".")[0] == key:
            refused[position] = exc
    if not refused:
        return None
    for position, exc in refused.items():
        entry_path = _pointer(path, position)
        original = value[position] if isinstance(value, list) else value[position]
        changes.append(
            RecoveryFieldChange(
                path=entry_path,
                outcome="removed",
                reason="The entry was refused and excluded from the candidate; "
                f"original: {_short_repr(original)}",
            )
        )
        issues.append(_issue(entry_path, "invalid_value", str(exc), "warning"))
    if isinstance(value, list):
        return [item for position, item in enumerate(value) if position not in refused]
    return {name: item for name, item in value.items() if name not in refused}
