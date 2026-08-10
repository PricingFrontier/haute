"""Shared validation for persisted Explore pivot-card configuration."""

from __future__ import annotations

import math
from typing import Any

from haute.errors import ConfigError


def _is_simple_literal(value: Any) -> bool:
    """Return whether *value* can safely round-trip through code generation."""

    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_simple_literal(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_simple_literal(item) for key, item in value.items())
    return False


def validate_explore_pivots(value: Any, *, context: str) -> list[dict[str, Any]]:
    """Return validated, detached Explore pivot-card configuration."""

    if not isinstance(value, list):
        raise ConfigError(
            "Explore pivots config must be a list.",
            context=context,
            actual_type=type(value).__name__,
        )

    pivots: list[dict[str, Any]] = []
    pivot_ids: set[str] = set()
    for index, pivot in enumerate(value):
        if not isinstance(pivot, dict):
            raise ConfigError(
                "Explore pivot entries must be dicts.",
                context=context,
                index=index,
                actual_type=type(pivot).__name__,
            )
        if "id" not in pivot:
            raise ConfigError("Explore pivot requires an id.", context=context, index=index)
        pivot_id = pivot["id"]
        if not isinstance(pivot_id, str) or not pivot_id.strip():
            raise ConfigError(
                "Explore pivot id must be a non-empty string.",
                context=context,
                index=index,
                actual_type=type(pivot_id).__name__,
            )
        if pivot_id in pivot_ids:
            raise ConfigError(
                "Explore pivot has a duplicate pivot id.",
                context=context,
                index=index,
                pivot_id=pivot_id,
            )

        copied_pivot: dict[str, Any] = {}
        for key, item in pivot.items():
            if not isinstance(key, str) or (key != "id" and not _is_simple_literal(item)):
                raise ConfigError(
                    "Explore pivot fields must use string keys and simple literals.",
                    context=context,
                    index=index,
                    key=repr(key),
                    actual_type=type(item).__name__,
                )
            copied_pivot[key] = item
        pivot_ids.add(pivot_id)
        pivots.append(copied_pivot)
    return pivots
