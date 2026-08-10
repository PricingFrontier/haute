"""Shared validation for persisted Explore chart-card configuration."""

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


def validate_explore_charts(value: Any, *, context: str) -> list[dict[str, Any]]:
    """Return validated, detached Explore chart-card configuration.

    Chart IDs and enabled states form the stable contract. Other fields are
    retained for forwards-compatible UI round trips when they are literals
    which can be represented safely in generated Python source.
    """

    if not isinstance(value, list):
        raise ConfigError(
            "Explore charts config must be a list.",
            context=context,
            actual_type=type(value).__name__,
        )

    charts: list[dict[str, Any]] = []
    chart_ids: set[str] = set()
    for index, chart in enumerate(value):
        if not isinstance(chart, dict):
            raise ConfigError(
                "Explore chart entries must be dicts.",
                context=context,
                index=index,
                actual_type=type(chart).__name__,
            )
        if "id" not in chart:
            raise ConfigError("Explore chart requires an id.", context=context, index=index)
        chart_id = chart["id"]
        if not isinstance(chart_id, str) or not chart_id.strip():
            raise ConfigError(
                "Explore chart id must be a non-empty string.",
                context=context,
                index=index,
                actual_type=type(chart_id).__name__,
            )
        if "enabled" not in chart:
            raise ConfigError(
                "Explore chart requires an enabled state.", context=context, index=index
            )
        enabled = chart["enabled"]
        if not isinstance(enabled, bool):
            raise ConfigError(
                "Explore chart enabled state must be a boolean.",
                context=context,
                index=index,
                actual_type=type(enabled).__name__,
            )
        if chart_id in chart_ids:
            raise ConfigError(
                "Explore chart has a duplicate chart id.",
                context=context,
                index=index,
                chart_id=chart_id,
            )

        copied_chart: dict[str, Any] = {}
        for key, item in chart.items():
            if not isinstance(key, str) or (
                key not in {"id", "enabled"} and not _is_simple_literal(item)
            ):
                raise ConfigError(
                    "Explore chart fields must use string keys and simple literals.",
                    context=context,
                    index=index,
                    key=repr(key),
                    actual_type=type(item).__name__,
                )
            copied_chart[key] = item
        chart_ids.add(chart_id)
        charts.append(copied_chart)
    return charts
