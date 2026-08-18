"""Validation for persisted and runtime Explore pivot configuration."""

from __future__ import annotations

import copy
import math
import re
from datetime import date, datetime, time
from typing import Any

from haute._graph_utils import _sanitize_func_name
from haute.errors import ConfigError

EXPLORE_PIVOT_CONFIG_VERSION = 1
PIVOT_AGGREGATIONS = frozenset(
    {"sum", "count", "average", "min", "max", "median", "distinct_count"}
)
PIVOT_SORT_DIRECTIONS = frozenset({"ascending", "descending"})
PIVOT_VALUE_SORTS = frozenset({"none", *PIVOT_SORT_DIRECTIONS})
PIVOT_COLOR_SCALES = frozenset({"none", "low_red_high_green", "low_green_high_red"})
PIVOT_DECIMAL_PLACES_MAX = 10
PIVOT_NUMBER_FORMATS = frozenset(
    {"general", "number", "percent", "currency_gbp", "currency_usd", "currency_eur"}
)
PIVOT_MEMBER_KINDS = frozenset(
    {"null", "string", "boolean", "integer", "float", "nan", "date", "datetime", "time", "decimal"}
)

_CARD_KEYS = frozenset(
    {
        "version",
        "id",
        "name",
        "enabled",
        "filters",
        "columns",
        "rows",
        "values",
        "formulas",
        "value_order",
        "options",
    }
)
_REFERENCE_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_INTEGER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_DECIMAL_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:E[+-]?[0-9]+)?\Z")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_TIME_PATTERN = re.compile(
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?\Z"
)
_MISSING = object()


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


def _copy_simple_dict(
    value: dict[Any, Any], *, context: str, index: int, scope: str
) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _is_simple_literal(item):
            raise ConfigError(
                f"Explore pivot {scope} fields must use string keys and simple literals.",
                context=context,
                index=index,
                key=repr(key),
                actual_type=type(item).__name__,
            )
        copied[key] = copy.deepcopy(item)
    return copied


def _copy_known_dict(
    value: dict[Any, Any],
    *,
    known_keys: frozenset[str],
    context: str,
    index: int,
    scope: str,
) -> dict[str, Any]:
    """Deep-copy a typed object while applying literal rules to future fields only."""

    copied: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or (key not in known_keys and not _is_simple_literal(item)):
            raise ConfigError(
                f"Explore pivot {scope} fields must use string keys and simple literals.",
                context=context,
                index=index,
                key=repr(key),
                actual_type=type(item).__name__,
            )
        copied[key] = copy.deepcopy(item)
    return copied


def _require_non_empty_string(
    value: Any,
    *,
    context: str,
    index: int,
    label: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"Explore pivot {label} must be a non-empty string.",
            context=context,
            index=index,
            actual_type=type(value).__name__,
        )
    return value


def _validate_decimal_places(
    value: Any,
    *,
    context: str,
    card_index: int,
    placement_index: int,
) -> int | None:
    """Validate the optional presentation precision for a pivot placement."""

    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= PIVOT_DECIMAL_PLACES_MAX:
        raise ConfigError(
            "Explore pivot decimal places must be an integer from 0 through "
            f"{PIVOT_DECIMAL_PLACES_MAX} or null.",
            context=context,
            index=card_index,
            placement_index=placement_index,
            actual_type=type(value).__name__,
        )
    return value


def _validate_number_format(
    placement: dict[str, Any],
    *,
    context: str,
    card_index: int,
    placement_index: int,
) -> str:
    """Validate the required persisted number format for a v1 placement."""

    if "number_format" not in placement:
        raise ConfigError(
            "Explore pivot number format is required.",
            context=context,
            index=card_index,
            placement_index=placement_index,
        )
    number_format = placement["number_format"]
    if not isinstance(number_format, str) or number_format not in PIVOT_NUMBER_FORMATS:
        raise ConfigError(
            "Explore pivot number format is unsupported.",
            context=context,
            index=card_index,
            placement_index=placement_index,
            actual_type=type(number_format).__name__,
        )
    return number_format


def _validate_grouping(
    placement: dict[str, Any],
    *,
    context: str,
    card_index: int,
    placement_index: int,
) -> bool:
    """Validate the required persisted grouping preference for a v1 placement."""

    if "use_grouping" not in placement:
        raise ConfigError(
            "Explore pivot grouping preference is required.",
            context=context,
            index=card_index,
            placement_index=placement_index,
        )
    use_grouping = placement["use_grouping"]
    if type(use_grouping) is not bool:
        raise ConfigError(
            "Explore pivot grouping must be a boolean.",
            context=context,
            index=card_index,
            placement_index=placement_index,
            actual_type=type(use_grouping).__name__,
        )
    return use_grouping


def _sanitised_reference(value: str, *, fallback: str) -> str:
    reference = _sanitize_func_name(value).lower().strip("_")
    contains_identifier_character = any(
        character.isascii() and (character.isalnum() or character == "_") for character in value
    )
    if not reference or (reference == "unnamed_node" and not contains_identifier_character):
        reference = fallback
    if reference.startswith("__haute_"):
        reference = f"{fallback}_{reference.lstrip('_')}"
    return reference


def _value_reference_stem(field: str, aggregation: str) -> str:
    aggregation_suffix = "mean" if aggregation == "average" else aggregation
    return f"{_sanitised_reference(field, fallback='value')}_{aggregation_suffix}"


def _is_canonical_value_reference(reference: str, field: str, aggregation: str) -> bool:
    stem = _value_reference_stem(field, aggregation)
    return (
        reference == stem
        or re.fullmatch(rf"{re.escape(stem)}_(?:[2-9]|[1-9][0-9]+)", reference) is not None
    )


def _is_valid_temporal_member(kind: str, value: Any) -> bool:
    if not isinstance(value, str) or value != value.strip() or not value:
        return False
    try:
        if kind == "date":
            return (
                _DATE_PATTERN.fullmatch(value) is not None and date.fromisoformat(value) is not None
            )
        if kind == "time":
            return (
                _TIME_PATTERN.fullmatch(value) is not None and time.fromisoformat(value) is not None
            )
        if kind == "datetime":
            date_value, separator, time_value = value.partition("T")
            return (
                separator == "T"
                and _DATE_PATTERN.fullmatch(date_value) is not None
                and _TIME_PATTERN.fullmatch(time_value) is not None
                and datetime.fromisoformat(value) is not None
            )
    except ValueError:
        return False
    return False


def _validate_member(
    raw: Any,
    *,
    context: str,
    card_index: int,
    filter_index: int,
    member_index: int,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(
            "Explore pivot filter members must be dicts.",
            context=context,
            index=card_index,
            filter_index=filter_index,
            member_index=member_index,
        )
    copied = _copy_known_dict(
        raw,
        known_keys=frozenset({"kind", "value"}),
        context=context,
        index=card_index,
        scope="member",
    )
    kind = copied.get("kind")
    if not isinstance(kind, str) or kind not in PIVOT_MEMBER_KINDS:
        raise ConfigError(
            "Explore pivot member has an unsupported kind.",
            context=context,
            index=card_index,
            filter_index=filter_index,
            member_index=member_index,
            kind=kind,
        )
    if "value" not in copied:
        raise ConfigError(
            "Explore pivot member requires a value.",
            context=context,
            index=card_index,
            filter_index=filter_index,
            member_index=member_index,
        )
    value = copied["value"]
    valid = False
    if kind in {"null", "nan"}:
        valid = value is None
    elif kind == "string":
        valid = isinstance(value, str)
    elif kind == "boolean":
        valid = isinstance(value, bool)
    elif kind == "integer":
        valid = isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value) is not None
    elif kind == "float":
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
        if not valid:
            raise ConfigError(
                "Explore pivot float member value must be a finite number.",
                context=context,
                index=card_index,
                filter_index=filter_index,
                member_index=member_index,
            )
    elif kind == "decimal":
        valid = isinstance(value, str) and _DECIMAL_PATTERN.fullmatch(value) is not None
    elif kind in {"date", "datetime", "time"}:
        valid = _is_valid_temporal_member(kind, value)
    if not valid:
        raise ConfigError(
            "Explore pivot member value does not match its kind.",
            context=context,
            index=card_index,
            filter_index=filter_index,
            member_index=member_index,
            kind=kind,
            actual_type=type(value).__name__,
        )
    return copied


def _validate_axis_placements(
    raw: Any,
    *,
    zone: str,
    context: str,
    card_index: int,
    placement_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ConfigError(
            f"Explore pivot {zone} must be a list.",
            context=context,
            index=card_index,
            actual_type=type(raw).__name__,
        )
    placements: list[dict[str, Any]] = []
    fields: set[str] = set()
    for placement_index, placement in enumerate(raw):
        if not isinstance(placement, dict):
            raise ConfigError(
                f"Explore pivot {zone} entries must be dicts.",
                context=context,
                index=card_index,
                placement_index=placement_index,
            )
        if zone == "filters":
            known_keys = frozenset({"id", "field", "members"})
        elif zone == "rows":
            known_keys = frozenset(
                {"id", "field", "sort", "decimal_places", "number_format", "use_grouping"}
            )
        else:
            known_keys = frozenset(
                {"id", "field", "decimal_places", "number_format", "use_grouping"}
            )
        copied = _copy_known_dict(
            placement,
            known_keys=known_keys,
            context=context,
            index=card_index,
            scope=f"{zone} placement",
        )
        placement_id = _require_non_empty_string(
            copied.get("id"), context=context, index=card_index, label="placement id"
        )
        field = _require_non_empty_string(
            copied.get("field"), context=context, index=card_index, label="placement field"
        )
        if placement_id in placement_ids:
            raise ConfigError(
                "Explore pivot has a duplicate placement id.",
                context=context,
                index=card_index,
                placement_id=placement_id,
            )
        if field in fields:
            raise ConfigError(
                f"Explore pivot {zone} has a duplicate field.",
                context=context,
                index=card_index,
                field=field,
            )
        placement_ids.add(placement_id)
        fields.add(field)
        copied["id"] = placement_id
        copied["field"] = field
        if zone != "filters":
            if "decimal_places" not in copied:
                raise ConfigError(
                    "Explore pivot decimal places is required.",
                    context=context,
                    index=card_index,
                    placement_index=placement_index,
                )
            decimal_places = _validate_decimal_places(
                copied["decimal_places"],
                context=context,
                card_index=card_index,
                placement_index=placement_index,
            )
            copied["decimal_places"] = decimal_places
            copied["number_format"] = _validate_number_format(
                copied,
                context=context,
                card_index=card_index,
                placement_index=placement_index,
            )
            copied["use_grouping"] = _validate_grouping(
                copied,
                context=context,
                card_index=card_index,
                placement_index=placement_index,
            )
        if zone == "filters":
            members = copied.get("members")
            if not isinstance(members, list):
                raise ConfigError(
                    "Explore pivot filter members must be a list.",
                    context=context,
                    index=card_index,
                    placement_index=placement_index,
                )
            copied["members"] = [
                _validate_member(
                    member,
                    context=context,
                    card_index=card_index,
                    filter_index=placement_index,
                    member_index=member_index,
                )
                for member_index, member in enumerate(members)
            ]
        elif zone == "rows":
            sort = copied.get("sort", "ascending")
            if not isinstance(sort, str) or sort not in PIVOT_SORT_DIRECTIONS:
                raise ConfigError(
                    "Explore pivot row has an unsupported sort direction.",
                    context=context,
                    index=card_index,
                    placement_index=placement_index,
                    sort=sort,
                )
            copied["sort"] = sort
        placements.append(copied)
    return placements


def _validate_values(
    raw: Any,
    *,
    context: str,
    card_index: int,
    placement_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ConfigError(
            "Explore pivot values must be a list.",
            context=context,
            index=card_index,
            actual_type=type(raw).__name__,
        )
    values: list[dict[str, Any]] = []
    references: set[str] = set()
    for value_index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ConfigError(
                "Explore pivot value entries must be dicts.",
                context=context,
                index=card_index,
                value_index=value_index,
            )
        copied = _copy_known_dict(
            value,
            known_keys=frozenset(
                {
                    "id",
                    "field",
                    "aggregation",
                    "reference",
                    "display_name",
                    "sort_rows",
                    "color_scale",
                    "color_scale_split_by",
                    "decimal_places",
                    "number_format",
                    "use_grouping",
                }
            ),
            context=context,
            index=card_index,
            scope="value",
        )
        placement_id = _require_non_empty_string(
            copied.get("id"), context=context, index=card_index, label="placement id"
        )
        field = _require_non_empty_string(
            copied.get("field"), context=context, index=card_index, label="placement field"
        )
        aggregation = copied.get("aggregation")
        if not isinstance(aggregation, str) or aggregation not in PIVOT_AGGREGATIONS:
            raise ConfigError(
                "Explore pivot value has an unsupported aggregation.",
                context=context,
                index=card_index,
                value_index=value_index,
                aggregation=aggregation,
            )
        reference = copied.get("reference")
        reference = _require_non_empty_string(
            reference, context=context, index=card_index, label="value reference"
        )
        if not _REFERENCE_PATTERN.fullmatch(reference) or reference.startswith("__haute_"):
            raise ConfigError(
                "Explore pivot value reference is invalid.",
                context=context,
                index=card_index,
                value_index=value_index,
                reference=reference,
            )
        if not _is_canonical_value_reference(reference, field, aggregation):
            raise ConfigError(
                "Explore pivot value reference must use its field-first aggregation alias.",
                context=context,
                index=card_index,
                value_index=value_index,
                reference=reference,
                expected_reference=_value_reference_stem(field, aggregation),
            )
        if reference in references:
            raise ConfigError(
                "Explore pivot has a duplicate value reference.",
                context=context,
                index=card_index,
                value_index=value_index,
                reference=reference,
            )
        if placement_id in placement_ids:
            raise ConfigError(
                "Explore pivot has a duplicate placement id.",
                context=context,
                index=card_index,
                placement_id=placement_id,
            )
        display_name = _require_non_empty_string(
            copied.get("display_name"),
            context=context,
            index=card_index,
            label="value display name",
        )
        sort_rows = copied.get("sort_rows", "none")
        if not isinstance(sort_rows, str) or sort_rows not in PIVOT_VALUE_SORTS:
            raise ConfigError(
                "Explore pivot value has an unsupported row sort.",
                context=context,
                index=card_index,
                value_index=value_index,
                sort_rows=sort_rows,
            )
        color_scale = copied.get("color_scale", "none")
        if not isinstance(color_scale, str) or color_scale not in PIVOT_COLOR_SCALES:
            raise ConfigError(
                "Explore pivot value has an unsupported colour scale.",
                context=context,
                index=card_index,
                value_index=value_index,
                color_scale=color_scale,
            )
        if "color_scale_split_by" not in copied:
            raise ConfigError(
                "Explore pivot colour scale split is required.",
                context=context,
                index=card_index,
                value_index=value_index,
            )
        color_scale_split_by = copied["color_scale_split_by"]
        if color_scale_split_by is not None and not isinstance(color_scale_split_by, str):
            raise ConfigError(
                "Explore pivot colour scale split must be a string or null.",
                context=context,
                index=card_index,
                value_index=value_index,
                actual_type=type(color_scale_split_by).__name__,
            )
        if "decimal_places" not in copied:
            raise ConfigError(
                "Explore pivot decimal places is required.",
                context=context,
                index=card_index,
                value_index=value_index,
            )
        decimal_places = _validate_decimal_places(
            copied["decimal_places"],
            context=context,
            card_index=card_index,
            placement_index=value_index,
        )
        number_format = _validate_number_format(
            copied,
            context=context,
            card_index=card_index,
            placement_index=value_index,
        )
        use_grouping = _validate_grouping(
            copied,
            context=context,
            card_index=card_index,
            placement_index=value_index,
        )
        placement_ids.add(placement_id)
        references.add(reference)
        copied.update(
            id=placement_id,
            field=field,
            reference=reference,
            aggregation=aggregation,
            display_name=display_name,
            sort_rows=sort_rows,
            color_scale=color_scale,
            color_scale_split_by=color_scale_split_by,
            decimal_places=decimal_places,
            number_format=number_format,
            use_grouping=use_grouping,
        )
        values.append(copied)
    return values


def _validate_formulas(
    raw: Any,
    *,
    context: str,
    card_index: int,
    placement_ids: set[str],
    references: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ConfigError(
            "Explore pivot formulas must be a list.", context=context, index=card_index
        )
    formulas: list[dict[str, Any]] = []
    for formula_index, formula in enumerate(raw):
        if not isinstance(formula, dict):
            raise ConfigError(
                "Explore pivot formula entries must be dicts.",
                context=context,
                index=card_index,
                formula_index=formula_index,
            )
        copied = _copy_known_dict(
            formula,
            known_keys=frozenset(
                {
                    "id",
                    "reference",
                    "display_name",
                    "expression",
                    "decimal_places",
                    "number_format",
                    "use_grouping",
                }
            ),
            context=context,
            index=card_index,
            scope="formula",
        )
        formula_id = _require_non_empty_string(
            copied.get("id"), context=context, index=card_index, label="formula id"
        )
        if formula_id in placement_ids:
            raise ConfigError(
                "Explore pivot has a duplicate placement id.",
                context=context,
                index=card_index,
                placement_id=formula_id,
            )
        display_name = _require_non_empty_string(
            copied.get("display_name"),
            context=context,
            index=card_index,
            label="formula display name",
        )
        reference = copied.get("reference")
        reference = _require_non_empty_string(
            reference, context=context, index=card_index, label="formula reference"
        )
        if not _REFERENCE_PATTERN.fullmatch(reference) or reference.startswith("__haute_"):
            raise ConfigError(
                "Explore pivot formula reference is invalid.",
                context=context,
                index=card_index,
                formula_index=formula_index,
                reference=reference,
            )
        if reference in references:
            raise ConfigError(
                "Explore pivot has a duplicate formula reference.",
                context=context,
                index=card_index,
                formula_index=formula_index,
                reference=reference,
            )
        expression = _require_non_empty_string(
            copied.get("expression"), context=context, index=card_index, label="formula expression"
        )
        if "decimal_places" not in copied:
            raise ConfigError(
                "Explore pivot decimal places is required.",
                context=context,
                index=card_index,
                formula_index=formula_index,
            )
        decimal_places = _validate_decimal_places(
            copied["decimal_places"],
            context=context,
            card_index=card_index,
            placement_index=formula_index,
        )
        copied.update(
            id=formula_id,
            reference=reference,
            display_name=display_name,
            expression=expression,
            decimal_places=decimal_places,
            number_format=_validate_number_format(
                copied,
                context=context,
                card_index=card_index,
                placement_index=formula_index,
            ),
            use_grouping=_validate_grouping(
                copied, context=context, card_index=card_index, placement_index=formula_index
            ),
        )
        placement_ids.add(formula_id)
        references.add(reference)
        formulas.append(copied)
    return formulas


def _validate_value_order(
    raw: Any,
    *,
    values: list[dict[str, Any]],
    formulas: list[dict[str, Any]],
    context: str,
    card_index: int,
) -> list[str]:
    """Validate the required mixed display sequence for a v1 card."""

    expected = [*(value["id"] for value in values), *(formula["id"] for formula in formulas)]
    if raw is _MISSING:
        raise ConfigError(
            "Explore pivot value_order is required.", context=context, index=card_index
        )
    if not isinstance(raw, list):
        raise ConfigError(
            "Explore pivot value_order must be a list.",
            context=context,
            index=card_index,
            actual_type=type(raw).__name__,
        )
    order: list[str] = []
    for value_index, output_id in enumerate(raw):
        if not isinstance(output_id, str) or not output_id.strip():
            raise ConfigError(
                "Explore pivot value_order must contain non-empty string ids.",
                context=context,
                index=card_index,
                value_index=value_index,
            )
        if output_id in order:
            raise ConfigError(
                "Explore pivot value_order has a duplicate id.",
                context=context,
                index=card_index,
                value_id=output_id,
            )
        order.append(output_id)
    expected_ids = set(expected)
    order_ids = set(order)
    unknown = order_ids - expected_ids
    missing = expected_ids - order_ids
    if unknown:
        raise ConfigError(
            "Explore pivot value_order contains an unknown id.",
            context=context,
            index=card_index,
            value_id=next(output_id for output_id in order if output_id in unknown),
        )
    if missing:
        raise ConfigError(
            "Explore pivot value_order is missing an output id.",
            context=context,
            index=card_index,
            value_id=next(output_id for output_id in expected if output_id in missing),
        )
    return order


def _validate_v1(raw: dict[Any, Any], *, context: str, index: int) -> dict[str, Any]:
    copied = _copy_known_dict(
        raw,
        known_keys=_CARD_KEYS,
        context=context,
        index=index,
        scope="card",
    )
    version = copied.get("version")
    if type(version) is not int or version != EXPLORE_PIVOT_CONFIG_VERSION:
        raise ConfigError(
            "Explore pivot version must be 1.", context=context, index=index, version=version
        )
    pivot_id = _require_non_empty_string(copied.get("id"), context=context, index=index, label="id")
    name = _require_non_empty_string(copied.get("name"), context=context, index=index, label="name")
    enabled = copied.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError(
            "Explore pivot enabled state must be a boolean.",
            context=context,
            index=index,
            actual_type=type(enabled).__name__,
        )

    placement_ids: set[str] = set()
    filters = _validate_axis_placements(
        copied.get("filters"),
        zone="filters",
        context=context,
        card_index=index,
        placement_ids=placement_ids,
    )
    columns = _validate_axis_placements(
        copied.get("columns"),
        zone="columns",
        context=context,
        card_index=index,
        placement_ids=placement_ids,
    )
    rows = _validate_axis_placements(
        copied.get("rows"),
        zone="rows",
        context=context,
        card_index=index,
        placement_ids=placement_ids,
    )
    values = _validate_values(
        copied.get("values"),
        context=context,
        card_index=index,
        placement_ids=placement_ids,
    )
    references = {value["reference"] for value in values}
    formulas = _validate_formulas(
        copied.get("formulas"),
        context=context,
        card_index=index,
        placement_ids=placement_ids,
        references=references,
    )
    value_order = _validate_value_order(
        copied.get("value_order", _MISSING),
        values=values,
        formulas=formulas,
        context=context,
        card_index=index,
    )
    if sum(value["sort_rows"] != "none" for value in values) > 1:
        raise ConfigError(
            "Explore pivot may have only one active Value row sort.",
            context=context,
            index=index,
        )

    split_axis_ids = {column["id"] for column in columns} | {row["id"] for row in rows}
    for value in values:
        color_scale_split_by = value["color_scale_split_by"]
        if color_scale_split_by is None:
            continue
        if color_scale_split_by not in split_axis_ids:
            raise ConfigError(
                "Explore pivot colour scale split must reference a Row or Column placement.",
                context=context,
                index=index,
                value_id=value["id"],
                color_scale_split_by=color_scale_split_by,
            )
        if value["color_scale"] == "none":
            raise ConfigError(
                "Explore pivot colour scale split requires an active colour scale.",
                context=context,
                index=index,
                value_id=value["id"],
                color_scale_split_by=color_scale_split_by,
            )

    options = copied.get("options")
    if not isinstance(options, dict):
        raise ConfigError(
            "Explore pivot options must be a dict.",
            context=context,
            index=index,
            actual_type=type(options).__name__,
        )
    copied_options = _copy_known_dict(
        options,
        known_keys=frozenset({"row_grand_totals", "column_grand_totals", "sort_by"}),
        context=context,
        index=index,
        scope="options",
    )
    for option in ("row_grand_totals", "column_grand_totals"):
        if option not in copied_options or not isinstance(copied_options[option], bool):
            raise ConfigError(
                f"Explore pivot options.{option} must be a boolean.",
                context=context,
                index=index,
            )

    active_value_ids = [value["id"] for value in values if value["sort_rows"] != "none"]
    if "sort_by" not in copied_options:
        # Older version-1 cards stored the active Value sort on the placement
        # alone. Preserve that choice when normalising them to the current
        # explicit sort-target model.
        sort_by: str | None = active_value_ids[0] if active_value_ids else None
    else:
        raw_sort_by = copied_options["sort_by"]
        if raw_sort_by is not None and not isinstance(raw_sort_by, str):
            raise ConfigError(
                "Explore pivot options.sort_by must be a string or null.",
                context=context,
                index=index,
                actual_type=type(raw_sort_by).__name__,
            )
        sort_by = raw_sort_by

    row_ids = {row["id"] for row in rows}
    value_ids = {value["id"] for value in values}
    if sort_by is not None and sort_by not in row_ids | value_ids:
        raise ConfigError(
            "Explore pivot options.sort_by must reference a Row or Value placement.",
            context=context,
            index=index,
            sort_by=sort_by,
        )
    if sort_by in value_ids:
        selected_value = next(value for value in values if value["id"] == sort_by)
        if selected_value["sort_rows"] == "none":
            raise ConfigError(
                "Explore pivot options.sort_by selected Value must have an active row sort.",
                context=context,
                index=index,
                sort_by=sort_by,
            )
    elif active_value_ids:
        raise ConfigError(
            "Explore pivot active Value row sort must match options.sort_by.",
            context=context,
            index=index,
            sort_by=sort_by,
            active_value_id=active_value_ids[0],
        )
    copied_options["sort_by"] = sort_by

    copied.update(
        version=version,
        id=pivot_id,
        name=name,
        enabled=enabled,
        filters=filters,
        columns=columns,
        rows=rows,
        values=values,
        formulas=formulas,
        value_order=value_order,
        options=copied_options,
    )
    return copied


def validate_explore_pivots(value: Any, *, context: str) -> list[dict[str, Any]]:
    """Return validated, deeply detached Explore pivot cards."""

    if not isinstance(value, list):
        raise ConfigError(
            "Explore pivots config must be a list.",
            context=context,
            actual_type=type(value).__name__,
        )

    pivots: list[dict[str, Any]] = []
    pivot_ids: set[str] = set()
    pivot_names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ConfigError(
                "Explore pivot entries must be dicts.",
                context=context,
                index=index,
                actual_type=type(raw).__name__,
            )
        pivot = _validate_v1(raw, context=context, index=index)
        pivot_id = pivot["id"]
        pivot_name_key = pivot["name"].strip().lower()
        if pivot_id in pivot_ids:
            raise ConfigError(
                "Explore pivot has a duplicate pivot id.",
                context=context,
                index=index,
                pivot_id=pivot_id,
            )
        if pivot_name_key in pivot_names:
            raise ConfigError(
                "Explore pivot has a duplicate pivot name.",
                context=context,
                index=index,
                pivot_name=pivot["name"],
            )
        pivot_ids.add(pivot_id)
        pivot_names.add(pivot_name_key)
        pivots.append(pivot)
    return pivots


def validate_explore_pivot_state(
    pivot_formulas: Any | None, pivots: Any, *, context: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate the canonical shared library and persisted formula-id selections."""

    if not isinstance(pivots, list):
        raise ConfigError(
            "Explore pivots config must be a list.",
            context=context,
            actual_type=type(pivots).__name__,
        )
    if pivot_formulas is not None and not isinstance(pivot_formulas, list):
        raise ConfigError(
            "Explore pivot formulas config must be a list.",
            context=context,
            actual_type=type(pivot_formulas).__name__,
        )

    definitions = _validate_formulas(
        list(pivot_formulas or []),
        context=context,
        card_index=-1,
        placement_ids=set(),
        references=set(),
    )
    definitions_by_id = {definition["id"]: definition for definition in definitions}

    selections: list[list[str]] = []
    for pivot_index, pivot in enumerate(pivots):
        if not isinstance(pivot, dict):
            raise ConfigError(
                "Explore pivot entries must be dicts.",
                context=context,
                index=pivot_index,
                actual_type=type(pivot).__name__,
            )
        raw_formulas = pivot.get("formulas")
        if not isinstance(raw_formulas, list):
            raise ConfigError(
                "Explore pivot formulas must be a list.", context=context, index=pivot_index
            )
        ids: list[str] = []
        for formula_index, selection in enumerate(raw_formulas):
            if not isinstance(selection, str) or not selection.strip():
                raise ConfigError(
                    "Explore pivot formula selections must contain shared formula ids.",
                    context=context,
                    index=pivot_index,
                    formula_index=formula_index,
                )
            formula_id = selection
            if formula_id in ids:
                raise ConfigError(
                    "Explore pivot has a duplicate formula selection.",
                    context=context,
                    index=pivot_index,
                    formula_id=formula_id,
                )
            ids.append(formula_id)
        selections.append(ids)

    validated_pivots: list[dict[str, Any]] = []
    pivot_ids: set[str] = set()
    pivot_names: set[str] = set()
    for pivot_index, (raw_pivot, selected_ids) in enumerate(zip(pivots, selections, strict=True)):
        base_raw = copy.deepcopy(raw_pivot)
        selected: list[dict[str, Any]] = []
        for formula_id in selected_ids:
            definition = definitions_by_id.get(formula_id)
            if definition is None:
                raise ConfigError(
                    "Explore pivot selected an unknown shared formula id.",
                    context=context,
                    index=pivot_index,
                    formula_id=formula_id,
                )
            selected.append(copy.deepcopy(definition))
        base_raw["formulas"] = selected
        pivot = _validate_v1(base_raw, context=context, index=pivot_index)
        pivot["formulas"] = list(selected_ids)

        pivot_id = pivot["id"]
        pivot_name_key = pivot["name"].strip().lower()
        if pivot_id in pivot_ids:
            raise ConfigError(
                "Explore pivot has a duplicate pivot id.",
                context=context,
                index=pivot_index,
                pivot_id=pivot_id,
            )
        if pivot_name_key in pivot_names:
            raise ConfigError(
                "Explore pivot has a duplicate pivot name.",
                context=context,
                index=pivot_index,
                pivot_name=pivot["name"],
            )
        pivot_ids.add(pivot_id)
        pivot_names.add(pivot_name_key)
        validated_pivots.append(pivot)
    return definitions, validated_pivots
