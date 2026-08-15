"""Migration and validation for persisted Explore chart-card configuration."""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

from haute.errors import ConfigError

EXPLORE_CHART_CONFIG_VERSION = 1
_CARD_KEYS = frozenset(
    {
        "version",
        "id",
        "name",
        "enabled",
        "pivot_id",
        "kind",
        "orientation",
        "category",
        "value_encodings",
        "series_overrides",
        "axes",
        "legend",
    }
)
_COMMON_STYLE_KEYS = frozenset(
    {"mark", "axis", "stack_group", "stack_normalize", "color", "data_labels", "markers"}
)
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_NUMBER_FORMATS = frozenset(
    {"inherit", "number", "integer", "percent", "currency_gbp", "currency_usd", "currency_eur"}
)
_SERIES_MEMBER_KINDS = frozenset(
    {"null", "nan", "string", "integer", "date", "datetime", "time", "decimal", "boolean", "float"}
)


def _canonical_series_key(value: Any, *, context: str, index: int) -> str:
    value = _non_empty(value, context=context, index=index, label="series key")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ConfigError(
            "Explore chart series key must be canonical JSON.", context=context, index=index
        ) from error
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"version", "value_id", "column_path"}
        or isinstance(parsed["version"], bool)
        or not isinstance(parsed["version"], (int, float))
        or parsed["version"] != EXPLORE_CHART_CONFIG_VERSION
        or not isinstance(parsed["value_id"], str)
        or not parsed["value_id"].strip()
        or not isinstance(parsed["column_path"], list)
    ):
        raise ConfigError(
            "Explore chart series key must be a canonical version-1 identity.",
            context=context,
            index=index,
        )
    for member in parsed["column_path"]:
        if (
            not isinstance(member, dict)
            or set(member) != {"kind", "value"}
            or member.get("kind") not in _SERIES_MEMBER_KINDS
        ):
            raise ConfigError(
                "Explore chart series key has an invalid column member.",
                context=context,
                index=index,
            )
        kind, member_value = member["kind"], member["value"]
        if kind in {"null", "nan"}:
            valid = member_value is None
        elif kind == "boolean":
            valid = isinstance(member_value, bool)
        elif kind == "float":
            valid = (
                isinstance(member_value, (int, float))
                and not isinstance(member_value, bool)
                and math.isfinite(member_value)
            )
        else:
            valid = isinstance(member_value, str)
        if not valid:
            raise ConfigError(
                "Explore chart series key has an invalid column member value.",
                context=context,
                index=index,
            )
    canonical = {
        "version": EXPLORE_CHART_CONFIG_VERSION,
        "value_id": parsed["value_id"],
        "column_path": [
            {"kind": member["kind"], "value": member["value"]} for member in parsed["column_path"]
        ],
    }
    return json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))


def _is_simple_literal(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_simple_literal(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_simple_literal(item) for key, item in value.items())
    return False


def _copy_known(
    raw: dict[Any, Any], *, known: frozenset[str], context: str, index: int, scope: str
) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or (key not in known and not _is_simple_literal(item)):
            raise ConfigError(
                f"Explore chart {scope} fields must use string keys and simple literals.",
                context=context,
                index=index,
                key=repr(key),
                actual_type=type(item).__name__,
            )
        copied[key] = copy.deepcopy(item)
    return copied


def _non_empty(value: Any, *, context: str, index: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"Explore chart {label} must be a non-empty string.",
            context=context,
            index=index,
            actual_type=type(value).__name__,
        )
    return value


def _bool(value: Any, *, context: str, index: int, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(
            f"Explore chart {label} must be a boolean.",
            context=context,
            index=index,
            actual_type=type(value).__name__,
        )
    return value


def _style(
    raw: Any, *, required: frozenset[str], context: str, index: int, scope: str
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Explore chart {scope} entries must be dicts.", context=context, index=index
        )
    other_identity = "series_key" if "value_id" in required else "value_id"
    if other_identity in raw:
        raise ConfigError(
            f"Explore chart {scope} contains a misplaced identity field.",
            context=context,
            index=index,
            field=other_identity,
        )
    item = _copy_known(
        raw,
        known=_COMMON_STYLE_KEYS | required,
        context=context,
        index=index,
        scope=scope,
    )
    for key in required:
        item[key] = _non_empty(item.get(key), context=context, index=index, label=f"{scope} {key}")
    if "series_key" in required:
        item["series_key"] = _canonical_series_key(item["series_key"], context=context, index=index)
    mark = item.get("mark")
    if mark not in {"column", "line", "area"}:
        raise ConfigError(
            "Explore chart has an unsupported mark.", context=context, index=index, mark=mark
        )
    axis = item.get("axis")
    if axis not in {"primary", "secondary"}:
        raise ConfigError(
            "Explore chart has an unsupported axis.", context=context, index=index, axis=axis
        )
    stack_group = item.get("stack_group")
    if stack_group is not None and (not isinstance(stack_group, str) or not stack_group.strip()):
        raise ConfigError(
            "Explore chart stack group must be null or a non-empty string.",
            context=context,
            index=index,
        )
    stack_normalize = item["stack_normalize"] if "stack_normalize" in item else False
    if not isinstance(stack_normalize, bool):
        raise ConfigError(
            "Explore chart stack normalize must be a boolean.",
            context=context,
            index=index,
        )
    if stack_normalize and stack_group is None:
        raise ConfigError(
            "Explore chart stack normalize requires a stack group.",
            context=context,
            index=index,
        )
    color = item.get("color")
    if color is not None and (not isinstance(color, str) or _COLOR.fullmatch(color) is None):
        raise ConfigError(
            "Explore chart color must be null or a strict #RRGGBB hex value.",
            context=context,
            index=index,
        )
    item["data_labels"] = _bool(
        item.get("data_labels"), context=context, index=index, label="data labels"
    )
    item["markers"] = _bool(item.get("markers"), context=context, index=index, label="markers")
    item.update(
        mark=mark,
        axis=axis,
        stack_group=stack_group,
        stack_normalize=stack_normalize,
        color=color,
    )
    return item


def _axis(raw: Any, *, context: str, index: int, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(
            "Explore chart axes must be dicts.", context=context, index=index, axis=name
        )
    known = {"title", "minimum", "maximum", "number_format"}
    if name == "secondary":
        known.add("enabled")
    axis = _copy_known(
        raw,
        known=frozenset(known),
        context=context,
        index=index,
        scope="axis",
    )
    if name == "secondary":
        enabled = axis.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(
                "Explore chart secondary axis enabled must be a boolean.",
                context=context,
                index=index,
            )
        axis["enabled"] = enabled
    for required in ("title", "minimum", "maximum", "number_format"):
        if required not in axis:
            raise ConfigError(
                f"Explore chart axis requires {required}.",
                context=context,
                index=index,
                axis=name,
            )
    if not isinstance(axis.get("title"), str):
        raise ConfigError(
            "Explore chart axis title must be a string.", context=context, index=index, axis=name
        )
    for bound in ("minimum", "maximum"):
        value = axis.get(bound)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ConfigError(
                "Explore chart axis bounds must be null or finite numbers.",
                context=context,
                index=index,
                axis=name,
            )
    if (
        axis["minimum"] is not None
        and axis["maximum"] is not None
        and axis["minimum"] >= axis["maximum"]
    ):
        raise ConfigError(
            "Explore chart axis minimum must be less than maximum.",
            context=context,
            index=index,
            axis=name,
        )
    if axis.get("number_format") not in _NUMBER_FORMATS:
        raise ConfigError(
            "Explore chart axis has an unsupported number format.",
            context=context,
            index=index,
            axis=name,
        )
    return axis


def _migrate_v0(
    raw: dict[Any, Any], *, context: str, index: int, default_name: str
) -> dict[str, Any]:
    card = _copy_known(raw, known=_CARD_KEYS, context=context, index=index, scope="card")
    chart_id = _non_empty(card.get("id"), context=context, index=index, label="id")
    conflicting = sorted(key for key in card if key in _CARD_KEYS and key not in {"id", "enabled"})
    if conflicting:
        raise ConfigError(
            "Versionless Explore chart may contain only id, enabled, and future fields.",
            context=context,
            index=index,
            fields=conflicting,
        )
    enabled = _bool(card.get("enabled"), context=context, index=index, label="enabled state")
    card.update(
        version=1,
        id=chart_id,
        name=default_name,
        enabled=enabled,
        pivot_id=None,
        kind="combo",
        orientation="vertical",
        category={
            "source": "rows",
            "include_grand_total": False,
            "label_rotation": 0,
        },
        value_encodings=[],
        series_overrides=[],
        axes={
            "primary": {
                "title": "",
                "minimum": None,
                "maximum": None,
                "number_format": "inherit",
            },
            "secondary": {
                "title": "",
                "minimum": None,
                "maximum": None,
                "number_format": "inherit",
                "enabled": True,
            },
        },
        legend={"visible": True, "position": "bottom"},
    )
    return card


def _validate_v1(raw: dict[Any, Any], *, context: str, index: int) -> dict[str, Any]:
    card = _copy_known(raw, known=_CARD_KEYS, context=context, index=index, scope="card")
    if type(card.get("version")) is not int or card["version"] != 1:
        raise ConfigError("Explore chart version must be 1.", context=context, index=index)
    card["id"] = _non_empty(card.get("id"), context=context, index=index, label="id")
    card["name"] = _non_empty(card.get("name"), context=context, index=index, label="name")
    card["enabled"] = _bool(
        card.get("enabled"), context=context, index=index, label="enabled state"
    )
    if "pivot_id" not in card:
        raise ConfigError("Explore chart requires a pivot_id.", context=context, index=index)
    pivot_id = card["pivot_id"]
    if pivot_id is not None and (not isinstance(pivot_id, str) or not pivot_id.strip()):
        raise ConfigError(
            "Explore chart pivot id must be null or a non-empty string.",
            context=context,
            index=index,
        )
    if card.get("kind") != "combo":
        raise ConfigError("Explore chart has an unsupported kind.", context=context, index=index)
    orientation = card["orientation"] if "orientation" in card else "vertical"
    if orientation not in {"vertical", "horizontal"}:
        raise ConfigError(
            "Explore chart has an unsupported orientation.",
            context=context,
            index=index,
            orientation=orientation,
        )
    card["orientation"] = orientation
    category = card.get("category")
    if not isinstance(category, dict):
        raise ConfigError("Explore chart category must be a dict.", context=context, index=index)
    category = _copy_known(
        category,
        known=frozenset({"source", "include_grand_total", "label_rotation"}),
        context=context,
        index=index,
        scope="category",
    )
    if category.get("source") != "rows":
        raise ConfigError(
            "Explore chart has an unsupported category source.", context=context, index=index
        )
    category["include_grand_total"] = _bool(
        category.get("include_grand_total"),
        context=context,
        index=index,
        label="category include_grand_total",
    )
    rotation = category.get("label_rotation")
    if type(rotation) is not int or not -90 <= rotation <= 90:
        raise ConfigError(
            "Explore chart label rotation must be an integer between -90 and 90.",
            context=context,
            index=index,
        )
    card["category"] = category
    encoding_ids: set[str] = set()
    value_ids: set[str] = set()
    for field, required in (
        ("value_encodings", frozenset({"id", "value_id"})),
        ("series_overrides", frozenset({"id", "series_key"})),
    ):
        raw_items = card.get(field)
        if not isinstance(raw_items, list):
            raise ConfigError(
                f"Explore chart {field} must be a list.", context=context, index=index
            )
        items = []
        series_keys: set[str] = set()
        for raw_item in raw_items:
            item = _style(
                raw_item, required=required, context=context, index=index, scope=field[:-1]
            )
            if item["id"] in encoding_ids:
                raise ConfigError(
                    "Explore chart has a duplicate encoding id.", context=context, index=index
                )
            encoding_ids.add(item["id"])
            if field == "value_encodings":
                if item["value_id"] in value_ids:
                    raise ConfigError(
                        "Explore chart has a duplicate value id.", context=context, index=index
                    )
                value_ids.add(item["value_id"])
            elif item["series_key"] in series_keys:
                raise ConfigError(
                    "Explore chart has a duplicate series key.", context=context, index=index
                )
            else:
                series_keys.add(item["series_key"])
            items.append(item)
        card[field] = items
    stack_identities: dict[str, tuple[bool, str]] = {}
    for style in [*card["value_encodings"], *card["series_overrides"]]:
        group = style["stack_group"]
        if group is None:
            continue
        identity = (style["stack_normalize"], style["axis"])
        if stack_identities.setdefault(group, identity) != identity:
            raise ConfigError(
                "Explore chart styles sharing a stack group must agree on"
                " stack normalize and axis.",
                context=context,
                index=index,
                stack_group=group,
            )
    axes = card.get("axes")
    if not isinstance(axes, dict):
        raise ConfigError("Explore chart axes must be a dict.", context=context, index=index)
    axes = _copy_known(
        axes, known=frozenset({"primary", "secondary"}), context=context, index=index, scope="axes"
    )
    axes.update(
        {
            name: _axis(axes.get(name), context=context, index=index, name=name)
            for name in ("primary", "secondary")
        }
    )
    if axes["secondary"]["enabled"] is False and any(
        style["axis"] == "secondary"
        for style in [*card["value_encodings"], *card["series_overrides"]]
    ):
        raise ConfigError(
            "Explore chart secondary axis is disabled but a style uses it.",
            context=context,
            index=index,
        )
    card["axes"] = axes
    legend = card.get("legend")
    if not isinstance(legend, dict):
        raise ConfigError("Explore chart legend must be a dict.", context=context, index=index)
    legend = _copy_known(
        legend,
        known=frozenset({"visible", "position"}),
        context=context,
        index=index,
        scope="legend",
    )
    legend["visible"] = _bool(
        legend.get("visible"), context=context, index=index, label="legend visible"
    )
    if legend.get("position") not in {"top", "right", "bottom", "left"}:
        raise ConfigError(
            "Explore chart has an unsupported legend position.", context=context, index=index
        )
    card["legend"] = legend
    return card


def validate_explore_charts(value: Any, *, context: str) -> list[dict[str, Any]]:
    """Return migrated, validated, deeply detached Explore chart cards."""
    if not isinstance(value, list):
        raise ConfigError(
            "Explore charts config must be a list.",
            context=context,
            actual_type=type(value).__name__,
        )
    charts: list[dict[str, Any]] = []
    ids: set[str] = set()
    names: set[str] = set()
    allocated_names = {
        raw["name"].strip().lower()
        for raw in value
        if isinstance(raw, dict)
        and "version" in raw
        and isinstance(raw.get("name"), str)
        and raw["name"].strip()
    }
    next_name_suffix = 1
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ConfigError(
                "Explore chart entries must be dicts.",
                context=context,
                index=index,
                actual_type=type(raw).__name__,
            )
        if "id" not in raw:
            raise ConfigError("Explore chart requires an id.", context=context, index=index)
        if "version" not in raw:
            while f"chart {next_name_suffix}" in allocated_names:
                next_name_suffix += 1
            default_name = f"Chart {next_name_suffix}"
            allocated_names.add(default_name.lower())
            next_name_suffix += 1
            chart = _migrate_v0(
                raw,
                context=context,
                index=index,
                default_name=default_name,
            )
        else:
            chart = _validate_v1(raw, context=context, index=index)
        if chart["id"] in ids:
            raise ConfigError(
                "Explore chart has a duplicate chart id.",
                context=context,
                index=index,
                chart_id=chart["id"],
            )
        name = chart["name"].strip().lower()
        if name in names:
            raise ConfigError(
                "Explore chart has a duplicate chart name.",
                context=context,
                index=index,
                chart_name=chart["name"],
            )
        ids.add(chart["id"])
        names.add(name)
        charts.append(chart)
    return charts
