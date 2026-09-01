"""Stable config-error adapter for canonical Explore chart contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from haute._explore_chart_contracts import ExploreChartsConfig
from haute.errors import ConfigError


def _chart_validation_message(details: Sequence[Mapping[str, Any]]) -> str:
    """Translate Pydantic structure failures to the stable config vocabulary."""
    for detail in details:
        if detail["type"] in {"invalid-json-value", "invalid_key", "finite_number"} and not any(
            field in detail["loc"] for field in ("minimum", "maximum")
        ):
            return "Explore chart fields must use string keys and simple literals."

    for detail in details:
        if detail["type"] == "finite_number" and any(
            field in detail["loc"] for field in ("minimum", "maximum")
        ):
            return "Explore chart axis bounds must be null or finite numbers."

    detail = details[0]
    location = tuple(part for part in detail["loc"] if part not in {"int", "float"})
    error_type = detail["type"]
    field = location[-1] if location else None

    if error_type == "list_type" and not location:
        return "Explore charts config must be a list."
    if error_type == "model_type" and len(location) == 1:
        return "Explore chart entries must be dicts."
    if field == "version":
        return "Explore chart version must be 1."
    if field in {"id", "name"} and len(location) == 2:
        return f"Explore chart {field} must be a non-empty string."
    if field == "enabled" and len(location) == 2:
        return "Explore chart enabled state must be a boolean."
    if field == "pivot_id":
        if error_type == "missing":
            return "Explore chart requires a pivot_id."
        return "Explore chart pivot id must be null or a non-empty string."
    if field == "kind":
        return "Explore chart has an unsupported kind."
    if field == "orientation":
        return "Explore chart orientation must be vertical or horizontal."
    if field == "source" and "category" in location:
        return "Explore chart has an unsupported category source."
    if field == "include_grand_total":
        return "Explore chart category include_grand_total must be a boolean."
    if field == "label_rotation":
        return "Explore chart label rotation must be an integer between -90 and 90."
    if field == "mark":
        return "Explore chart has an unsupported mark."
    if field == "axis" and any(
        scope in location for scope in ("value_encodings", "series_overrides")
    ):
        return "Explore chart has an unsupported axis."
    if field == "color":
        return "Explore chart color must be null or a strict #RRGGBB hex value."
    if field == "stack_group":
        return "Explore chart stack group must be null or a non-empty string."
    if field == "stack_normalize":
        return "Explore chart stack normalize must be a boolean."
    if field == "number_format":
        return "Explore chart axis has an unsupported number format."
    if field in {"minimum", "maximum"}:
        if error_type == "missing":
            return f"Explore chart axis requires {field}."
        return "Explore chart axis bounds must be null or finite numbers."
    if field == "enabled" and "secondary" in location:
        return "Explore chart secondary axis enabled must be a boolean."
    if field == "visible" and "legend" in location:
        return "Explore chart legend visible must be a boolean."
    if field == "position" and "legend" in location:
        return "Explore chart has an unsupported legend position."
    if error_type == "missing" and isinstance(field, str):
        return f"Explore chart requires {field}."
    if error_type == "bool_type":
        return f"Explore chart {field} must be a boolean."

    message = str(detail["msg"]).removeprefix("Value error, ").rstrip(".")
    return f"Explore chart {message}."


def validate_explore_charts(value: Any, *, context: str) -> list[dict[str, Any]]:
    """Return validated, deeply detached Explore chart cards."""
    try:
        charts = ExploreChartsConfig.model_validate(value)
    except ValidationError as error:
        details = error.errors(include_url=False, include_input=False)
        detail = details[0]
        location = detail["loc"]
        index = location[0] if location and isinstance(location[0], int) else None
        raise ConfigError(
            _chart_validation_message(details),
            context=context,
            **({"index": index} if index is not None else {}),
        ) from error
    return [chart.model_dump(mode="python") for chart in charts.root]
