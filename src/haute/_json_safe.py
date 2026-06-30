"""Helpers for JSON-safe API payloads."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1
NON_FINITE_FLOAT_TYPE = "non_finite_float"
NON_FINITE_FLOAT_KEY = "__haute_type__"
NON_FINITE_FLOAT_VALUES = frozenset({"nan", "inf", "-inf"})


def non_finite_float_sentinel(value: float) -> dict[str, str]:
    """Return the JSON contract object for a non-finite float."""
    if math.isnan(value):
        token = "nan"
    elif math.isinf(value):
        token = "inf" if value > 0 else "-inf"
    else:
        raise ValueError(f"expected a non-finite float, got {value!r}")
    return {NON_FINITE_FLOAT_KEY: NON_FINITE_FLOAT_TYPE, "value": token}


def non_finite_float_token(value: Any) -> str | None:
    """Return the sentinel token for a JSON-safe non-finite float object."""
    if not isinstance(value, Mapping):
        return None
    if value.get(NON_FINITE_FLOAT_KEY) != NON_FINITE_FLOAT_TYPE:
        return None
    token = value.get("value")
    return token if token in NON_FINITE_FLOAT_VALUES else None


def to_json_safe(value: Any) -> Any:
    """Recursively convert *value* into a JSON-safe representation."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return non_finite_float_sentinel(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > MAX_SAFE_INTEGER else value
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    return str(value)


def row_to_json_safe(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one preview row into JSON-safe values."""
    return {str(key): to_json_safe(value) for key, value in row.items()}


def rows_to_json_safe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert preview rows into JSON-safe values."""
    return [row_to_json_safe(row) for row in rows]
