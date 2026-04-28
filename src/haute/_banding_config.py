"""Banding config normalization and sidecar serialization helpers."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

_COMPACT_RULE_TYPES = frozenset({"categorical", "breakpoints"})


def _banding_type(factor: dict[str, Any]) -> str:
    return str(factor.get("banding") or "continuous")


def _is_json_scalar(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool | int | str):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _validate_map_value(value: Any, context: str) -> None:
    if value is None or value == "":
        raise ValueError(f"{context} must map to a non-empty value")
    if not _is_json_scalar(value):
        raise ValueError(f"{context} must map to a JSON scalar value")


def _expand_rule_map(
    banding_type: str,
    rules: dict[Any, Any],
) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    if banding_type == "categorical":
        for raw_key, value in rules.items():
            key = str(raw_key)
            if key == "":
                raise ValueError("categorical rule key must not be empty")
            _validate_map_value(value, f"categorical rule {key!r}")
            expanded.append({"value": key, "assignment": value})
        return expanded
    if banding_type == "breakpoints":
        for raw_key, value in rules.items():
            key = str(raw_key)
            _validate_map_value(value, f"breakpoint rule {key!r}")
            expanded.append({"boundary": key, "label": value})
        return expanded
    raise ValueError(f"{banding_type} banding rules must be a list")


def normalise_banding_rules(
    banding_type: str,
    rules: list[dict[str, Any]] | dict[Any, Any] | None,
) -> list[dict[str, Any]]:
    """Return banding rules in the internal row-array shape."""
    if rules is None:
        return []
    if isinstance(rules, dict):
        return _expand_rule_map(str(banding_type or "continuous"), rules)
    if isinstance(rules, list):
        return deepcopy(rules)
    raise ValueError(f"{banding_type} banding rules must be a list")


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
        raise ValueError("banding factors must be a list")

    expanded: list[dict[str, Any]] = []
    for index, factor in enumerate(factors):
        if not isinstance(factor, dict):
            raise ValueError(f"banding factors[{index}] must be an object")
        expanded.append(_expand_banding_factor_from_sidecar(factor))
    result["factors"] = expanded
    return result


def normalise_banding_factors(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return banding factors in the canonical in-memory shape."""
    factors = config.get("factors")
    if not isinstance(factors, list):
        return []

    expanded_config = expand_banding_config_from_sidecar(config)
    expanded_factors = expanded_config["factors"]
    return list(expanded_factors)


def _compact_rule_map(rules: dict[Any, Any], banding_type: str) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    if banding_type == "categorical":
        for raw_key, value in rules.items():
            key = str(raw_key)
            if key == "":
                raise ValueError("categorical rule key must not be empty")
            _validate_map_value(value, f"categorical rule {key!r}")
            if key in compact:
                raise ValueError(f"duplicate categorical rule key {key!r}")
            compact[key] = value
        return compact
    if banding_type == "breakpoints":
        for raw_key, value in rules.items():
            key = str(raw_key)
            _validate_map_value(value, f"breakpoint rule {key!r}")
            if key in compact:
                raise ValueError(f"duplicate breakpoint rule key {key!r}")
            compact[key] = value
        return compact
    raise ValueError(f"{banding_type} banding rules must be a list")


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
            raise ValueError(f"{duplicate_label} rules[{index}] must be an object")

        raw_key = rule.get(key_field)
        raw_value = rule.get(value_field)
        if raw_key is None:
            raise ValueError(f"{duplicate_label} rules[{index}] requires {key_field}")
        if raw_key == "":
            if not allow_empty_key:
                raise ValueError(f"{duplicate_label} rules[{index}] requires {key_field}")
            key = ""
        else:
            key = str(raw_key)
        if raw_value is None or raw_value == "":
            raise ValueError(f"{duplicate_label} rule {key!r} requires {value_field}")
        if not _is_json_scalar(raw_value):
            raise ValueError(f"{duplicate_label} rule {key!r} must map to a JSON scalar value")
        if key in compact:
            raise ValueError(f"duplicate {duplicate_label} rule key {key!r}")
        compact[key] = raw_value
    return compact


def _compact_banding_factor_for_sidecar(factor: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(factor)
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
        raise ValueError(f"{banding_type} banding rules must be a list")
    return result


def compact_banding_config_for_sidecar(config: dict[str, Any]) -> dict[str, Any]:
    """Compact categorical and breakpoint rules for the JSON sidecar."""
    result = deepcopy(config)
    factors = result.get("factors")
    if factors is None:
        return result
    if not isinstance(factors, list):
        raise ValueError("banding factors must be a list")

    compacted: list[dict[str, Any]] = []
    for index, factor in enumerate(factors):
        if not isinstance(factor, dict):
            raise ValueError(f"banding factors[{index}] must be an object")
        compacted.append(_compact_banding_factor_for_sidecar(factor))
    result["factors"] = compacted
    return result
