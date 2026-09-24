"""Canonical Pydantic contracts for persisted Explore chart cards."""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

EXPLORE_CHART_CONFIG_VERSION = 1

NonEmptyString = Annotated[
    str,
    Field(min_length=1, pattern=r"[\s\S]*\S[\s\S]*"),
]
HexColour = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
ChartNumber = Annotated[int | float, Field(allow_inf_nan=False)]

ChartMark = Literal["column", "line", "area"]
ChartAxis = Literal["primary", "secondary"]
ChartOrientation = Literal["vertical", "horizontal"]
ChartNumberFormat = Literal[
    "inherit",
    "number",
    "integer",
    "percent",
    "currency_gbp",
    "currency_usd",
    "currency_eur",
]

_SERIES_MEMBER_KINDS = frozenset(
    {
        "null",
        "nan",
        "string",
        "integer",
        "date",
        "datetime",
        "time",
        "decimal",
        "boolean",
        "float",
    }
)


def canonical_series_key(value: Any) -> str:
    """Return one canonical version-1 series identity or fail explicitly."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("series key must be a non-empty string")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ValueError("series key must be canonical JSON") from error
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
        raise ValueError("series key must be a canonical version-1 identity")
    for member in parsed["column_path"]:
        if (
            not isinstance(member, dict)
            or set(member) != {"kind", "value"}
            or member.get("kind") not in _SERIES_MEMBER_KINDS
        ):
            raise ValueError("series key has an invalid column member")
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
            raise ValueError("series key has an invalid column member value")
    canonical = {
        "version": EXPLORE_CHART_CONFIG_VERSION,
        "value_id": parsed["value_id"],
        "column_path": [
            {"kind": member["kind"], "value": member["value"]} for member in parsed["column_path"]
        ],
    }
    return json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))


class ChartModel(BaseModel):
    """Strict known fields only: a field the model does not declare is rejected."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class ChartStyle(ChartModel):
    mark: ChartMark
    axis: ChartAxis
    stack_group: NonEmptyString | None
    stack_normalize: bool
    color: HexColour | None
    data_labels: bool
    markers: bool

    @model_validator(mode="after")
    def _validate_stack_normalisation(self) -> ChartStyle:
        if self.stack_normalize and self.stack_group is None:
            raise ValueError("stack normalize requires a stack group")
        return self


class ChartValueEncoding(ChartStyle):
    id: NonEmptyString
    value_id: NonEmptyString


class ChartSeriesOverride(ChartStyle):
    id: NonEmptyString
    series_key: NonEmptyString

    @field_validator("series_key")
    @classmethod
    def _canonicalise_series_key(cls, value: str) -> str:
        return canonical_series_key(value)


class ChartAxisConfig(ChartModel):
    title: str
    minimum: ChartNumber | None
    maximum: ChartNumber | None
    number_format: ChartNumberFormat

    @model_validator(mode="after")
    def _validate_bounds(self) -> ChartAxisConfig:
        if self.minimum is not None and self.maximum is not None and self.minimum >= self.maximum:
            raise ValueError("axis minimum must be less than maximum")
        return self


class ChartSecondaryAxisConfig(ChartAxisConfig):
    enabled: bool


class ChartAxes(ChartModel):
    primary: ChartAxisConfig
    secondary: ChartSecondaryAxisConfig


class ChartCategory(ChartModel):
    source: Literal["rows"]
    include_grand_total: bool
    label_rotation: Annotated[int, Field(ge=-90, le=90)]


class ChartLegend(ChartModel):
    visible: bool
    position: Literal["top", "right", "bottom", "left"]


class ExploreChartConfig(ChartModel):
    version: Literal[1]
    id: NonEmptyString
    name: NonEmptyString
    enabled: bool
    pivot_id: NonEmptyString | None
    kind: Literal["combo"]
    orientation: ChartOrientation
    category: ChartCategory
    value_encodings: list[ChartValueEncoding]
    series_overrides: list[ChartSeriesOverride]
    axes: ChartAxes
    legend: ChartLegend

    @model_validator(mode="after")
    def _validate_card_relationships(self) -> ExploreChartConfig:
        encoding_ids: set[str] = set()
        value_ids: set[str] = set()
        series_keys: set[str] = set()
        for encoding in self.value_encodings:
            if encoding.id in encoding_ids:
                raise ValueError("duplicate encoding id")
            encoding_ids.add(encoding.id)
            if encoding.value_id in value_ids:
                raise ValueError("duplicate value id")
            value_ids.add(encoding.value_id)
        for override in self.series_overrides:
            if override.id in encoding_ids:
                raise ValueError("duplicate encoding id")
            encoding_ids.add(override.id)
            if override.series_key in series_keys:
                raise ValueError("duplicate series key")
            series_keys.add(override.series_key)

        stack_identities: dict[str, tuple[bool, ChartAxis]] = {}
        for style in [*self.value_encodings, *self.series_overrides]:
            group = style.stack_group
            if group is None:
                continue
            identity = (style.stack_normalize, style.axis)
            if stack_identities.setdefault(group, identity) != identity:
                raise ValueError(
                    "styles sharing a stack group must agree on stack normalize and axis"
                )

        if not self.axes.secondary.enabled and any(
            style.axis == "secondary" for style in [*self.value_encodings, *self.series_overrides]
        ):
            raise ValueError("secondary axis is disabled but a style uses it")
        return self


class ExploreChartsConfig(RootModel[list[ExploreChartConfig]]):
    """Complete ordered chart-card collection."""

    model_config = ConfigDict(strict=True)

    @model_validator(mode="after")
    def _validate_collection_identities(self) -> ExploreChartsConfig:
        ids: set[str] = set()
        names: set[str] = set()
        for chart in self.root:
            if chart.id in ids:
                raise ValueError("duplicate chart id")
            name = chart.name.strip().lower()
            if name in names:
                raise ValueError("duplicate chart name")
            ids.add(chart.id)
            names.add(name)
        return self
