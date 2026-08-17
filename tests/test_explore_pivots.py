"""Validation contract for persisted Explore pivot cards."""

from __future__ import annotations

import copy

import pytest

from haute._explore_pivots import validate_explore_pivots
from haute.errors import ConfigError


def _pivot(**updates: object) -> dict[str, object]:
    pivot: dict[str, object] = {
        "version": 1,
        "id": "pivot_1",
        "name": "Claims by region",
        "enabled": True,
        "filters": [
            {
                "id": "filter_1",
                "field": "region",
                "members": [{"kind": "string", "value": "North"}],
            }
        ],
        "columns": [
            {
                "id": "column_1",
                "field": "year",
                "number_format": "general",
                "decimal_places": None,
                "use_grouping": True,
            }
        ],
        "rows": [
            {
                "id": "row_1",
                "field": "region",
                "sort": "ascending",
                "number_format": "general",
                "decimal_places": None,
                "use_grouping": True,
            }
        ],
        "values": [
            {
                "id": "value_1",
                "field": "claims",
                "aggregation": "sum",
                "display_name": "Claims",
                "sort_rows": "none",
                "color_scale": "none",
                "color_scale_split_by": None,
                "number_format": "general",
                "decimal_places": None,
                "use_grouping": True,
            }
        ],
        "options": {
            "row_grand_totals": True,
            "column_grand_totals": True,
            "sort_by": None,
        },
    }
    pivot.update(updates)
    return pivot


def test_validate_explore_pivots_rejects_versionless_cards_instead_of_migrating() -> None:
    """There is no v0 migration: every persisted card is complete version 1."""
    with pytest.raises(ConfigError, match="version must be 1"):
        validate_explore_pivots([{"id": "pivot_1"}], context="test")


def test_validate_explore_pivots_accepts_v1_and_returns_a_deep_detached_copy() -> None:
    raw = [_pivot(future={"nested": [1, 2]})]
    expected = copy.deepcopy(raw)

    validated = validate_explore_pivots(raw, context="test")

    assert validated == expected
    assert validated is not raw
    assert validated[0] is not raw[0]
    assert validated[0]["filters"] is not raw[0]["filters"]
    assert validated[0]["future"] is not raw[0]["future"]


def test_validate_explore_pivots_defaults_formatting_sort_and_colour_fields_on_older_v1() -> None:
    pivot = _pivot(
        columns=[{"id": "column_1", "field": "year"}],
        rows=[{"id": "row_1", "field": "region"}],
        values=[
            {
                "id": "value_1",
                "field": "claims",
                "aggregation": "sum",
                "display_name": "Claims",
            }
        ],
        options={"row_grand_totals": True, "column_grand_totals": True},
    )

    validated = validate_explore_pivots([pivot], context="test")[0]

    assert validated["columns"] == [
        {
            "id": "column_1",
            "field": "year",
            "number_format": "general",
            "decimal_places": None,
            "use_grouping": True,
        }
    ]
    assert validated["rows"] == [
        {
            "id": "row_1",
            "field": "region",
            "sort": "ascending",
            "number_format": "general",
            "decimal_places": None,
            "use_grouping": True,
        }
    ]
    assert validated["values"] == [
        {
            "id": "value_1",
            "field": "claims",
            "aggregation": "sum",
            "display_name": "Claims",
            "sort_rows": "none",
            "color_scale": "none",
            "color_scale_split_by": None,
            "number_format": "general",
            "decimal_places": None,
            "use_grouping": True,
        }
    ]
    assert validated["options"]["sort_by"] is None


@pytest.mark.parametrize("decimal_places", [0, 10])
def test_validate_explore_pivots_accepts_number_formats_and_decimal_place_boundaries(
    decimal_places: int,
) -> None:
    pivot = _pivot(
        columns=[
            {
                "id": "column_1",
                "field": "year",
                "number_format": "currency_gbp",
                "decimal_places": decimal_places,
                "use_grouping": False,
            }
        ],
        rows=[
            {
                "id": "row_1",
                "field": "region",
                "sort": "ascending",
                "number_format": "percent",
                "decimal_places": decimal_places,
                "use_grouping": True,
            }
        ],
        values=[
            {
                "id": "value_1",
                "field": "claims",
                "aggregation": "sum",
                "display_name": "Claims",
                "sort_rows": "none",
                "color_scale": "none",
                "number_format": "currency_eur",
                "decimal_places": decimal_places,
                "use_grouping": False,
            }
        ],
    )

    validated = validate_explore_pivots([pivot], context="test")[0]

    assert validated["columns"][0]["decimal_places"] == decimal_places
    assert validated["rows"][0]["decimal_places"] == decimal_places
    assert validated["values"][0]["decimal_places"] == decimal_places
    assert validated["columns"][0]["number_format"] == "currency_gbp"
    assert validated["columns"][0]["use_grouping"] is False
    assert validated["rows"][0]["number_format"] == "percent"
    assert validated["values"][0]["number_format"] == "currency_eur"


@pytest.mark.parametrize("decimal_places", [-1, 11, 1.5, True, "2"])
def test_validate_explore_pivots_rejects_invalid_decimal_places(
    decimal_places: object,
) -> None:
    with pytest.raises(ConfigError, match="decimal places"):
        validate_explore_pivots(
            [
                _pivot(
                    columns=[
                        {
                            "id": "column_1",
                            "field": "year",
                            "decimal_places": decimal_places,
                        }
                    ]
                )
            ],
            context="test",
        )


@pytest.mark.parametrize("number_format", ["accounting", "currency_cad", 2, None])
def test_validate_explore_pivots_rejects_invalid_number_formats(
    number_format: object,
) -> None:
    with pytest.raises(ConfigError, match="number format"):
        validate_explore_pivots(
            [
                _pivot(
                    columns=[
                        {
                            "id": "column_1",
                            "field": "year",
                            "number_format": number_format,
                        }
                    ]
                )
            ],
            context="test",
        )


@pytest.mark.parametrize("use_grouping", [0, 1, "yes", None])
def test_validate_explore_pivots_rejects_invalid_grouping(
    use_grouping: object,
) -> None:
    with pytest.raises(ConfigError, match="grouping"):
        validate_explore_pivots(
            [
                _pivot(
                    values=[
                        {
                            "id": "value_1",
                            "field": "claims",
                            "aggregation": "sum",
                            "display_name": "Claims",
                            "use_grouping": use_grouping,
                        }
                    ]
                )
            ],
            context="test",
        )


def test_validate_explore_pivots_migrates_fixed_decimal_placements_to_number() -> None:
    validated = validate_explore_pivots(
        [
            _pivot(
                columns=[
                    {
                        "id": "column_1",
                        "field": "year",
                        "decimal_places": 2,
                    }
                ]
            )
        ],
        context="test",
    )[0]

    assert validated["columns"][0] == {
        "id": "column_1",
        "field": "year",
        "number_format": "number",
        "decimal_places": 2,
        "use_grouping": True,
    }


def test_validate_explore_pivots_derives_legacy_active_value_sort_target() -> None:
    pivot = _pivot(
        values=[
            {
                "id": "value_1",
                "field": "claims",
                "aggregation": "sum",
                "display_name": "Claims",
                "sort_rows": "descending",
                "color_scale": "none",
            }
        ],
        options={"row_grand_totals": True, "column_grand_totals": True},
    )

    validated = validate_explore_pivots([pivot], context="test")[0]

    assert validated["options"]["sort_by"] == "value_1"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({}, "must be a list"),
        (["pivot_1"], "entries must be dicts"),
        ([{}], "version must be 1"),
        ([_pivot(id="   ")], "id must be a non-empty string"),
        ([_pivot(version=2)], "version must be 1"),
        ([_pivot(name="   ")], "name must be a non-empty string"),
        ([_pivot(enabled=1)], "enabled state must be a boolean"),
        ([_pivot(filters={})], "filters must be a list"),
        ([_pivot(options={"row_grand_totals": True})], "column_grand_totals"),
        (
            [_pivot(), _pivot(id="pivot_2", name=" claims BY REGION ")],
            "duplicate pivot name",
        ),
        ([_pivot(), _pivot(id="pivot_1", name="Other")], "duplicate pivot id"),
        (
            [_pivot(rows=[{"id": "same", "field": "a"}], columns=[{"id": "same", "field": "b"}])],
            "duplicate placement id",
        ),
        (
            [_pivot(rows=[{"id": "row_1", "field": "a"}, {"id": "row_2", "field": "a"}])],
            "duplicate field",
        ),
        (
            [
                _pivot(
                    values=[
                        {"id": "value_1", "field": "a", "aggregation": "mode", "display_name": "A"}
                    ]
                )
            ],
            "unsupported aggregation",
        ),
        (
            [_pivot(rows=[{"id": "row_1", "field": "region", "sort": "sideways"}])],
            "unsupported sort direction",
        ),
        (
            [
                _pivot(
                    values=[
                        {
                            "id": "value_1",
                            "field": "claims",
                            "aggregation": "sum",
                            "display_name": "Claims",
                            "sort_rows": "sideways",
                            "color_scale": "none",
                        }
                    ]
                )
            ],
            "unsupported row sort",
        ),
        (
            [
                _pivot(
                    values=[
                        {
                            "id": "value_1",
                            "field": "claims",
                            "aggregation": "sum",
                            "display_name": "Claims",
                            "sort_rows": "none",
                            "color_scale": "rainbow",
                        }
                    ]
                )
            ],
            "unsupported colour scale",
        ),
        (
            [
                _pivot(
                    values=[
                        {
                            "id": "value_1",
                            "field": "claims",
                            "aggregation": "sum",
                            "display_name": "Claims",
                            "sort_rows": "ascending",
                            "color_scale": "none",
                        },
                        {
                            "id": "value_2",
                            "field": "claims",
                            "aggregation": "average",
                            "display_name": "Average claims",
                            "sort_rows": "descending",
                            "color_scale": "none",
                        },
                    ]
                )
            ],
            "only one active Value row sort",
        ),
        (
            [_pivot(options={"row_grand_totals": True, "column_grand_totals": True, "sort_by": 1})],
            "sort_by must be a string or null",
        ),
        (
            [
                _pivot(
                    options={
                        "row_grand_totals": True,
                        "column_grand_totals": True,
                        "sort_by": "missing",
                    }
                )
            ],
            "must reference a Row or Value placement",
        ),
        (
            [
                _pivot(
                    options={
                        "row_grand_totals": True,
                        "column_grand_totals": True,
                        "sort_by": "value_1",
                    }
                )
            ],
            "selected Value must have an active row sort",
        ),
        (
            [
                _pivot(
                    values=[
                        {
                            "id": "value_1",
                            "field": "claims",
                            "aggregation": "sum",
                            "display_name": "Claims",
                            "sort_rows": "descending",
                            "color_scale": "none",
                        }
                    ],
                    options={
                        "row_grand_totals": True,
                        "column_grand_totals": True,
                        "sort_by": None,
                    },
                )
            ],
            "active Value row sort must match options.sort_by",
        ),
        (
            [
                _pivot(
                    filters=[
                        {
                            "id": "filter_1",
                            "field": "a",
                            "members": [{"kind": "float", "value": float("inf")}],
                        }
                    ]
                )
            ],
            "finite number",
        ),
        (
            [
                _pivot(
                    filters=[
                        {
                            "id": "filter_1",
                            "field": "a",
                            "members": [{"kind": "decimal", "value": "01"}],
                        }
                    ]
                )
            ],
            "does not match its kind",
        ),
        (
            [
                _pivot(
                    filters=[
                        {
                            "id": "filter_1",
                            "field": "a",
                            "members": [{"kind": "datetime", "value": "2024-01-01 12:00:00"}],
                        }
                    ]
                )
            ],
            "does not match its kind",
        ),
        ([_pivot(future=object())], "simple literals"),
    ],
)
def test_validate_explore_pivots_rejects_malformed_values(value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_explore_pivots(value, context="test")


@pytest.mark.parametrize(
    ("split_by", "color_scale", "message"),
    [
        (42, "low_red_high_green", "split.*string or null"),
        ("missing", "low_red_high_green", "split.*Row or Column"),
        ("filter_1", "low_red_high_green", "split.*Row or Column"),
        ("value_1", "low_red_high_green", "split.*Row or Column"),
        ("row_1", "none", "split.*active colour scale"),
    ],
)
def test_validate_explore_pivots_rejects_invalid_colour_scale_splits(
    split_by: object,
    color_scale: str,
    message: str,
) -> None:
    pivot = _pivot()
    value = copy.deepcopy(pivot["values"])[0]
    value["color_scale"] = color_scale
    value["color_scale_split_by"] = split_by
    pivot["values"] = [value]

    with pytest.raises(ConfigError, match=message):
        validate_explore_pivots([pivot], context="test")


def test_validate_explore_pivots_allows_repeated_value_fields_with_unique_ids() -> None:
    pivot = _pivot(
        values=[
            {
                "id": "value_1",
                "field": "claims",
                "aggregation": "sum",
                "display_name": "Claims",
                "sort_rows": "none",
                "color_scale": "low_red_high_green",
                "color_scale_split_by": "row_1",
                "number_format": "general",
                "decimal_places": None,
                "use_grouping": True,
            },
            {
                "id": "value_2",
                "field": "claims",
                "aggregation": "average",
                "display_name": "Average claims",
                "sort_rows": "descending",
                "color_scale": "low_green_high_red",
                "color_scale_split_by": "column_1",
                "number_format": "general",
                "decimal_places": None,
                "use_grouping": True,
            },
        ],
        options={
            "row_grand_totals": True,
            "column_grand_totals": True,
            "sort_by": "value_2",
        },
    )

    assert validate_explore_pivots([pivot], context="test")[0]["values"] == pivot["values"]


def test_validate_explore_pivots_accepts_canonical_typed_filter_members() -> None:
    members = [
        {"kind": "null", "value": None},
        {"kind": "string", "value": "North"},
        {"kind": "boolean", "value": True},
        {"kind": "integer", "value": "-10"},
        {"kind": "float", "value": 1.5},
        {"kind": "nan", "value": None},
        {"kind": "date", "value": "2024-02-29"},
        {"kind": "datetime", "value": "2024-02-29T12:30:00Z"},
        {"kind": "time", "value": "12:30:00.123456+01:00"},
        {"kind": "decimal", "value": "123.4500E-2"},
    ]
    pivot = _pivot(filters=[{"id": "filter_1", "field": "value", "members": members}])

    assert validate_explore_pivots([pivot], context="test")[0]["filters"][0]["members"] == members
