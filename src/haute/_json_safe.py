"""Helpers for JSON-safe API payloads."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any


def to_json_safe(value: Any) -> Any:
    """Recursively convert *value* into a JSON-safe representation."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, bool)):
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
