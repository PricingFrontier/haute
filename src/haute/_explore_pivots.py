"""Migration and validation for persisted Explore pivot configuration."""

from __future__ import annotations

import copy
import math
import re
from datetime import date, datetime, time
from typing import Any

from haute.errors import ConfigError

EXPLORE_PIVOT_CONFIG_VERSION = 1
PIVOT_AGGREGATIONS = frozenset(
    {"sum", "count", "average", "min", "max", "median", "distinct_count"}
)
PIVOT_SORT_DIRECTIONS = frozenset({"ascending", "descending"})
PIVOT_VALUE_SORTS = frozenset({"none", *PIVOT_SORT_DIRECTIONS})
PIVOT_COLOR_SCALES = frozenset({"none", "low_red_high_green", "low_green_high_red"})
PIVOT_MEMBER_KINDS = frozenset(
    {"null", "string", "boolean", "integer", "float", "nan", "date", "datetime", "time", "decimal"}
)

_CARD_KEYS = frozenset(
    {"version", "id", "name", "enabled", "filters", "columns", "rows", "values", "options"}
)
_INTEGER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_DECIMAL_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:E[+-]?[0-9]+)?\Z")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_TIME_PATTERN = re.compile(
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?\Z"
)


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
            known_keys = frozenset({"id", "field", "sort"})
        else:
            known_keys = frozenset({"id", "field"})
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
                {"id", "field", "aggregation", "display_name", "sort_rows", "color_scale"}
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
        if placement_id in placement_ids:
            raise ConfigError(
                "Explore pivot has a duplicate placement id.",
                context=context,
                index=card_index,
                placement_id=placement_id,
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
        placement_ids.add(placement_id)
        copied.update(
            id=placement_id,
            field=field,
            aggregation=aggregation,
            display_name=display_name,
            sort_rows=sort_rows,
            color_scale=color_scale,
        )
        values.append(copied)
    return values


def _migrate_v0(raw: dict[Any, Any], *, context: str, index: int) -> dict[str, Any]:
    copied = _copy_known_dict(
        raw,
        known_keys=_CARD_KEYS,
        context=context,
        index=index,
        scope="card",
    )
    pivot_id = _require_non_empty_string(copied.get("id"), context=context, index=index, label="id")
    conflicting = sorted(key for key in copied if key in _CARD_KEYS and key != "id")
    if conflicting:
        raise ConfigError(
            "Versionless Explore pivot may contain only an id and future fields.",
            context=context,
            index=index,
            fields=conflicting,
        )
    copied.update(
        version=EXPLORE_PIVOT_CONFIG_VERSION,
        name=f"Pivot {index + 1}",
        enabled=True,
        filters=[],
        columns=[],
        rows=[],
        values=[],
        options={
            "row_grand_totals": True,
            "column_grand_totals": True,
            "sort_by": None,
        },
    )
    copied["id"] = pivot_id
    return copied


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
    if sum(value["sort_rows"] != "none" for value in values) > 1:
        raise ConfigError(
            "Explore pivot may have only one active Value row sort.",
            context=context,
            index=index,
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
        options=copied_options,
    )
    return copied


def validate_explore_pivots(value: Any, *, context: str) -> list[dict[str, Any]]:
    """Return migrated, validated, deeply detached Explore pivot cards."""

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
        if "id" not in raw:
            raise ConfigError("Explore pivot requires an id.", context=context, index=index)
        pivot = (
            _migrate_v0(raw, context=context, index=index)
            if "version" not in raw
            else _validate_v1(raw, context=context, index=index)
        )
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
