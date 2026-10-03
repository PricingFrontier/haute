"""Banding config normalization and sidecar serialization helpers."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from haute._validation_error import ConfigSettingError

_COMPACT_RULE_TYPES = frozenset({"categorical", "breakpoints"})


def _banding_type(factor: dict[str, Any]) -> str:
    return str(factor.get("banding") or "")


def _is_json_scalar(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool | int | str):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _validate_map_value(value: Any, kind: str, key: str) -> None:
    """Refuse a compact rule map's empty or non-scalar value; *key* is configuration."""

    if value is None or value == "":
        raise ConfigSettingError(
            f"{kind} rule {key!r} must map to a non-empty value", setting="factors", values=(key,)
        )
    if not _is_json_scalar(value):
        raise ConfigSettingError(
            f"{kind} rule {key!r} must map to a JSON scalar value", setting="factors", values=(key,)
        )


def _expand_rule_map(
    banding_type: str,
    rules: dict[Any, Any],
) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    if banding_type == "categorical":
        for raw_key, value in rules.items():
            key = str(raw_key)
            if key == "":
                raise ConfigSettingError(
                    "categorical rule key must not be empty", setting="factors"
                )
            _validate_map_value(value, "categorical", key)
            expanded.append({"value": key, "assignment": value})
        return expanded
    if banding_type == "breakpoints":
        for raw_key, value in rules.items():
            key = str(raw_key)
            _validate_map_value(value, "breakpoint", key)
            expanded.append({"boundary": key, "label": value})
        return expanded
    raise ConfigSettingError(f"{banding_type} banding rules must be a list", setting="factors")


def normalise_banding_rules(
    banding_type: str,
    rules: list[dict[str, Any]] | dict[Any, Any] | None,
) -> list[dict[str, Any]]:
    """Return banding rules in the internal row-array shape."""
    if rules is None:
        return []
    if isinstance(rules, dict):
        return _expand_rule_map(str(banding_type or ""), rules)
    if isinstance(rules, list):
        return deepcopy(rules)
    raise ConfigSettingError(f"{banding_type} banding rules must be a list", setting="factors")


def _expand_banding_factor_from_sidecar(factor: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(factor)
    if "rules" in result:
        result["rules"] = normalise_banding_rules(_banding_type(result), result.get("rules"))
    return result


def expand_banding_config_from_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """Expand compact sidecar rule maps into the canonical in-memory shape."""
    result = deepcopy(config)
    factors = result.get("factors")
    if factors is None:
        return result
    if not isinstance(factors, list):
        raise ConfigSettingError("banding factors must be a list", setting="factors")

    expanded: list[dict[str, Any]] = []
    for index, factor in enumerate(factors):
        if not isinstance(factor, dict):
            raise ConfigSettingError(
                f"banding factors[{index}] must be an object", setting="factors"
            )
        expanded.append(_expand_banding_factor_from_sidecar(factor))
    result["factors"] = expanded
    return result


def normalise_banding_factors(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return banding factors in the canonical in-memory shape."""
    factors = config.get("factors")
    if factors is None:
        return []
    if not isinstance(factors, list):
        raise ConfigSettingError("banding factors must be a list", setting="factors")

    expanded_config = expand_banding_config_from_sidecar(config)
    expanded_factors = expanded_config["factors"]
    return list(expanded_factors)


def banding_factor_is_active(factor: dict[str, Any]) -> bool:
    """Return whether execution applies *factor* rather than skipping it as a draft.

    A factor missing its column, output column, or rules is a draft: the
    node passes the frame through for it. The node's column contract asks
    the same question, so it never promises a draft's output column.
    """
    return bool(factor.get("column") and factor.get("outputColumn") and factor.get("rules"))


def require_distinct_banding_outputs(factors: list[dict[str, Any]]) -> None:
    """Refuse two active factors writing one output column.

    Execution aliases each active factor's ``outputColumn`` in order, so the
    later band would silently replace the earlier one. A draft factor writes
    nothing and is left out. Factors are named by 1-based position, as the
    editor lists them, and by input column.
    """
    first_writer: dict[str, int] = {}
    for index, factor in enumerate(factors):
        if not banding_factor_is_active(factor):
            continue
        output_column = factor["outputColumn"]
        earlier = first_writer.setdefault(output_column, index)
        if earlier == index:
            continue
        raise ConfigSettingError(
            f"Banding output {output_column!r} is written by factor {earlier + 1} "
            f"({factors[earlier]['column']!r}) and factor {index + 1} "
            f"({factor['column']!r}); give each factor its own output column",
            setting="factors",
            values=(output_column, factors[earlier]["column"], factor["column"]),
        )


def _compact_rule_map(rules: dict[Any, Any], banding_type: str) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    if banding_type == "categorical":
        for raw_key, value in rules.items():
            key = str(raw_key)
            if key == "":
                raise ConfigSettingError(
                    "categorical rule key must not be empty", setting="factors"
                )
            _validate_map_value(value, "categorical", key)
            if key in compact:
                raise ConfigSettingError(
                    f"duplicate categorical rule key {key!r}", setting="factors", values=(key,)
                )
            compact[key] = value
        return compact
    if banding_type == "breakpoints":
        for raw_key, value in rules.items():
            key = str(raw_key)
            _validate_map_value(value, "breakpoint", key)
            if key in compact:
                raise ConfigSettingError(
                    f"duplicate breakpoint rule key {key!r}", setting="factors", values=(key,)
                )
            compact[key] = value
        return compact
    raise ConfigSettingError(f"{banding_type} banding rules must be a list", setting="factors")


def _compact_rule_rows(
    rules: list[Any],
    *,
    key_field: str,
    value_field: str,
    duplicate_label: str,
    allow_empty_key: bool,
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ConfigSettingError(
                f"{duplicate_label} rules[{index}] must be an object", setting="factors"
            )

        raw_key = rule.get(key_field)
        raw_value = rule.get(value_field)
        if raw_key is None:
            raise ConfigSettingError(
                f"{duplicate_label} rules[{index}] requires {key_field}", setting="factors"
            )
        if raw_key == "":
            if not allow_empty_key:
                raise ConfigSettingError(
                    f"{duplicate_label} rules[{index}] requires {key_field}", setting="factors"
                )
            key = ""
        else:
            key = str(raw_key)
        if raw_value is None or raw_value == "":
            raise ConfigSettingError(
                f"{duplicate_label} rule {key!r} requires {value_field}",
                setting="factors",
                values=(key,),
            )
        if not _is_json_scalar(raw_value):
            raise ConfigSettingError(
                f"{duplicate_label} rule {key!r} must map to a JSON scalar value",
                setting="factors",
                values=(key,),
            )
        if key in compact:
            raise ConfigSettingError(
                f"duplicate {duplicate_label} rule key {key!r}", setting="factors", values=(key,)
            )
        compact[key] = raw_value
    return compact


def _compact_banding_factor_for_sidecar(factor: dict[str, Any]) -> dict[str, Any]:
    # Factors and expanded rule rows have editor properties (_prevRules and
    # _id). A compact rules dictionary instead has user category keys, which
    # must survive even when named exactly like one of those properties.
    result = {key: deepcopy(value) for key, value in factor.items() if not key.startswith("_")}
    if isinstance(result.get("rules"), list):
        result["rules"] = [
            {key: value for key, value in rule.items() if not key.startswith("_")}
            if isinstance(rule, dict)
            else rule
            for rule in result["rules"]
        ]
    banding_type = _banding_type(result)
    if banding_type not in _COMPACT_RULE_TYPES:
        return result

    rules = result.get("rules")
    if rules is None:
        result["rules"] = {}
    elif isinstance(rules, dict):
        result["rules"] = _compact_rule_map(rules, banding_type)
    elif isinstance(rules, list):
        if banding_type == "categorical":
            result["rules"] = _compact_rule_rows(
                rules,
                key_field="value",
                value_field="assignment",
                duplicate_label="categorical",
                allow_empty_key=False,
            )
        else:
            result["rules"] = _compact_rule_rows(
                rules,
                key_field="boundary",
                value_field="label",
                duplicate_label="breakpoint",
                allow_empty_key=True,
            )
    else:
        raise ConfigSettingError(f"{banding_type} banding rules must be a list", setting="factors")
    return result


def compact_banding_config_for_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """Compact categorical and breakpoint rules for the JSON sidecar."""
    result = deepcopy(config)
    factors = result.get("factors")
    if factors is None:
        return result
    if not isinstance(factors, list):
        raise ConfigSettingError("banding factors must be a list", setting="factors")

    compacted: list[dict[str, Any]] = []
    for index, factor in enumerate(factors):
        if not isinstance(factor, dict):
            raise ConfigSettingError(
                f"banding factors[{index}] must be an object", setting="factors"
            )
        compacted.append(_compact_banding_factor_for_sidecar(factor))
    result["factors"] = compacted
    return result
