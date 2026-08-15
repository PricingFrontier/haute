"""Validation contract for persisted Explore chart cards."""

from __future__ import annotations

import copy
import math

import pytest

from haute._explore_charts import validate_explore_charts
from haute.errors import ConfigError


def _chart() -> dict[str, object]:
    return {
        "version": 1,
        "id": "chart_1",
        "name": "Claims",
        "enabled": True,
        "pivot_id": None,
        "kind": "combo",
        "orientation": "vertical",
        "category": {
            "source": "rows",
            "include_grand_total": False,
            "label_rotation": 0,
        },
        "value_encodings": [
            {
                "id": "encoding_1",
                "value_id": "value_1",
                "mark": "column",
                "axis": "primary",
                "stack_group": None,
                "stack_normalize": False,
                "color": "#AABBCC",
                "data_labels": False,
                "markers": False,
            }
        ],
        "series_overrides": [
            {
                "id": "override_1",
                "series_key": "Open",
                "mark": "line",
                "axis": "secondary",
                "stack_group": None,
                "stack_normalize": False,
                "color": None,
                "data_labels": True,
                "markers": True,
            }
        ],
        "axes": {
            "primary": {
                "title": "Count",
                "minimum": None,
                "maximum": None,
                "number_format": "integer",
            },
            "secondary": {
                "title": "",
                "minimum": 0,
                "maximum": 1,
                "number_format": "percent",
                "enabled": True,
            },
        },
        "legend": {"visible": True, "position": "bottom"},
    }


def test_migrates_v0_with_defaults_order_and_future_fields() -> None:
    raw = [{"id": "old", "enabled": False, "future": {"nested": [1, {"x": 2.0}]}}]
    assert validate_explore_charts(raw, context="test") == [
        {
            "id": "old",
            "enabled": False,
            "future": {"nested": [1, {"x": 2.0}]},
            "version": 1,
            "name": "Chart 1",
            "pivot_id": None,
            "kind": "combo",
            "orientation": "vertical",
            "category": {
                "source": "rows",
                "include_grand_total": False,
                "label_rotation": 0,
            },
            "value_encodings": [],
            "series_overrides": [],
            "axes": {
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
            "legend": {"visible": True, "position": "bottom"},
        }
    ]


def test_v0_migration_allocates_names_around_existing_v1_cards() -> None:
    existing = _chart()
    existing.update(id="configured", name="Chart 1")

    charts = validate_explore_charts(
        [
            {"id": "legacy_a", "enabled": True},
            existing,
            {"id": "legacy_b", "enabled": False},
        ],
        context="test",
    )

    assert [chart["name"] for chart in charts] == ["Chart 2", "Chart 1", "Chart 3"]


def test_full_v1_is_deeply_detached_including_nested_future_fields() -> None:
    raw = _chart()
    raw["future"] = {"nested": [{"answer": 42}]}
    raw["category"]["future"] = ["ok"]  # type: ignore[index]
    raw["value_encodings"][0]["future"] = {"style": ["ok"]}  # type: ignore[index]
    raw["axes"]["primary"]["future"] = {"axis": ["ok"]}  # type: ignore[index]
    validated = validate_explore_charts([raw], context="test")
    assert validated == [raw]
    assert validated[0] is not raw
    assert validated[0]["future"] is not raw["future"]
    assert validated[0]["category"] is not raw["category"]
    assert validated[0]["value_encodings"][0] is not raw["value_encodings"][0]
    assert validated[0]["axes"]["primary"] is not raw["axes"]["primary"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.update(version=2), "version must be 1"),
        (lambda c: c.update(id=" "), "id must be a non-empty string"),
        (lambda c: c.update(name=" "), "name must be a non-empty string"),
        (lambda c: c.update(enabled=1), "enabled state must be a boolean"),
        (lambda c: c.pop("pivot_id"), "requires a pivot_id"),
        (lambda c: c.update(pivot_id=" "), "pivot id must be null or a non-empty string"),
        (lambda c: c.update(kind="bar"), "unsupported kind"),
        (lambda c: c["category"].update(source="columns"), "unsupported category source"),  # type: ignore[index]
        (lambda c: c["category"].update(include_grand_total=1), "must be a boolean"),  # type: ignore[index]
        (lambda c: c["category"].update(label_rotation=91), "label rotation"),  # type: ignore[index]
        (lambda c: c["category"].update(label_rotation=True), "label rotation"),  # type: ignore[index]
        (lambda c: c["value_encodings"][0].update(mark="pie"), "unsupported mark"),  # type: ignore[index]
        (lambda c: c["value_encodings"][0].update(axis="tertiary"), "unsupported axis"),  # type: ignore[index]
        (lambda c: c["value_encodings"][0].update(color="#abc"), "strict #RRGGBB"),  # type: ignore[index]
        (lambda c: c["value_encodings"][0].update(color="#AABBCG"), "strict #RRGGBB"),  # type: ignore[index]
        (lambda c: c.update(orientation="diagonal"), "unsupported orientation"),
        (lambda c: c["value_encodings"][0].update(stack_normalize=1), "must be a boolean"),  # type: ignore[index]
        (
            lambda c: c["value_encodings"][0].update(stack_normalize=True),  # type: ignore[index]
            "requires a stack group",
        ),
        (lambda c: c["value_encodings"][0].update(series_key="wrong"), "identity field"),  # type: ignore[index]
        (lambda c: c["series_overrides"][0].update(value_id="wrong"), "identity field"),  # type: ignore[index]
        (lambda c: c["axes"]["primary"].update(number_format="date"), "unsupported number format"),  # type: ignore[index]
        (lambda c: c["axes"]["primary"].pop("minimum"), "requires minimum"),  # type: ignore[index]
        (lambda c: c["axes"]["primary"].pop("maximum"), "requires maximum"),  # type: ignore[index]
        (lambda c: c["axes"]["primary"].update(minimum=True), "finite number"),  # type: ignore[index]
        (lambda c: c["axes"]["primary"].update(maximum=math.inf), "finite number"),  # type: ignore[index]
        (lambda c: c["axes"]["primary"].update(minimum=2, maximum=1), "minimum must be less"),  # type: ignore[index]
        (lambda c: c["legend"].update(visible=1), "must be a boolean"),  # type: ignore[index]
        (lambda c: c["legend"].update(position="center"), "unsupported legend position"),  # type: ignore[index]
    ],
)
def test_rejects_invalid_v1_known_fields(mutate: object, message: str) -> None:
    chart = _chart()
    mutate(chart)  # type: ignore[operator]
    with pytest.raises(ConfigError, match=message):
        validate_explore_charts([chart], context="test")


@pytest.mark.parametrize(
    ("charts", "message"),
    [
        ({}, "must be a list"),
        (["chart"], "entries must be dicts"),
        ([{"enabled": True}], "requires an id"),
        ([{"id": "old", "enabled": True, "name": "no"}], "Versionless Explore chart"),
        ([{"id": "old", "enabled": True, "future": object()}], "simple literals"),
        (
            [{**_chart(), "category": {**_chart()["category"], "future": object()}}],
            "simple literals",
        ),
        ([_chart(), _chart()], "duplicate chart id"),
        ([_chart(), {**_chart(), "id": "other", "name": " claims "}], "duplicate chart name"),
    ],
)
def test_rejects_malformed_top_card_and_v0_fields(charts: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_explore_charts(charts, context="test")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda c: c["value_encodings"].append(copy.deepcopy(c["value_encodings"][0])),
            "duplicate encoding id",
        ),  # type: ignore[index]
        (
            lambda c: c["value_encodings"].append({**c["value_encodings"][0], "id": "other"}),
            "duplicate value id",
        ),  # type: ignore[index]
        (
            lambda c: c["series_overrides"].append(copy.deepcopy(c["series_overrides"][0])),
            "duplicate encoding id",
        ),  # type: ignore[index]
        (
            lambda c: c["series_overrides"].append({**c["series_overrides"][0], "id": "other"}),
            "duplicate series key",
        ),  # type: ignore[index]
    ],
)
def test_rejects_duplicate_nested_ids_and_keys(mutate: object, message: str) -> None:
    chart = _chart()
    mutate(chart)  # type: ignore[operator]
    with pytest.raises(ConfigError, match=message):
        validate_explore_charts([chart], context="test")


def test_materialises_orientation_and_stack_normalize_defaults() -> None:
    raw = _chart()
    del raw["orientation"]
    del raw["value_encodings"][0]["stack_normalize"]  # type: ignore[index]
    del raw["series_overrides"][0]["stack_normalize"]  # type: ignore[index]

    validated = validate_explore_charts([raw], context="test")

    assert validated[0]["orientation"] == "vertical"
    assert validated[0]["value_encodings"][0]["stack_normalize"] is False
    assert validated[0]["series_overrides"][0]["stack_normalize"] is False


def test_materialises_secondary_axis_enabled_and_rejects_disabled_but_used() -> None:
    raw = _chart()
    del raw["axes"]["secondary"]["enabled"]  # type: ignore[index]
    validated = validate_explore_charts([raw], context="test")
    assert validated[0]["axes"]["secondary"]["enabled"] is True

    # The fixture's series override sits on the secondary axis.
    disabled = _chart()
    disabled["axes"]["secondary"]["enabled"] = False  # type: ignore[index]
    with pytest.raises(ConfigError, match="secondary axis is disabled"):
        validate_explore_charts([disabled], context="test")

    # The same rejection covers a secondary-assigned Value encoding.
    disabled_encoding = _chart()
    disabled_encoding["axes"]["secondary"]["enabled"] = False  # type: ignore[index]
    disabled_encoding["series_overrides"][0]["axis"] = "primary"  # type: ignore[index]
    disabled_encoding["value_encodings"][0]["axis"] = "secondary"  # type: ignore[index]
    with pytest.raises(ConfigError, match="secondary axis is disabled"):
        validate_explore_charts([disabled_encoding], context="test")

    disabled_unused = _chart()
    disabled_unused["axes"]["secondary"]["enabled"] = False  # type: ignore[index]
    disabled_unused["series_overrides"][0]["axis"] = "primary"  # type: ignore[index]
    validated_disabled = validate_explore_charts([disabled_unused], context="test")
    assert validated_disabled[0]["axes"]["secondary"]["enabled"] is False

    non_boolean = _chart()
    non_boolean["axes"]["secondary"]["enabled"] = 1  # type: ignore[index]
    with pytest.raises(ConfigError, match="must be a boolean"):
        validate_explore_charts([non_boolean], context="test")


def test_accepts_horizontal_orientation_and_stacked_line_and_area() -> None:
    raw = _chart()
    raw["orientation"] = "horizontal"
    raw["value_encodings"][0].update(mark="line", stack_group="s")  # type: ignore[index]
    raw["series_overrides"][0].update(mark="area", stack_group="other")  # type: ignore[index]

    validated = validate_explore_charts([raw], context="test")

    assert validated[0]["orientation"] == "horizontal"
    assert validated[0]["value_encodings"][0]["stack_group"] == "s"
    assert validated[0]["series_overrides"][0]["stack_group"] == "other"


@pytest.mark.parametrize(
    ("encoding_update", "override_update", "message"),
    [
        (
            {"stack_group": "s", "stack_normalize": True},
            {"stack_group": "s", "stack_normalize": False},
            "must agree",
        ),
        (
            {"stack_group": "s", "axis": "primary"},
            {"stack_group": "s", "axis": "secondary"},
            "must agree",
        ),
    ],
)
def test_rejects_inconsistent_stack_groups_across_encodings_and_overrides(
    encoding_update: dict[str, object],
    override_update: dict[str, object],
    message: str,
) -> None:
    raw = _chart()
    raw["value_encodings"][0].update(encoding_update)  # type: ignore[index]
    raw["series_overrides"][0].update(override_update)  # type: ignore[index]
    with pytest.raises(ConfigError, match=message):
        validate_explore_charts([raw], context="test")
