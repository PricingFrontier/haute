"""Validation contract for persisted Explore pivot cards."""

from __future__ import annotations

import copy

import pytest

from haute._explore_pivots import validate_explore_pivot_state, validate_explore_pivots
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
                "reference": "claims_sum",
                "display_name": "Claims",
                "sort_rows": "none",
                "color_scale": "none",
                "color_scale_split_by": None,
                "number_format": "general",
                "decimal_places": None,
                "use_grouping": True,
            }
        ],
        "formulas": [],
        "value_order": ["value_1"],
        "options": {
            "row_grand_totals": True,
            "column_grand_totals": True,
            "sort_by": None,
        },
    }
    pivot.update(updates)
    placement_lists = [
        items
        for items in (pivot["columns"], pivot["rows"], pivot["values"], pivot["formulas"])
        if isinstance(items, list)
    ]
    for placement in [item for items in placement_lists for item in items]:
        if isinstance(placement, dict):
            placement.setdefault("number_format", "general")
            placement.setdefault("decimal_places", None)
            placement.setdefault("use_grouping", True)
    for value in pivot["values"] if isinstance(pivot["values"], list) else []:
        if isinstance(value, dict):
            value.setdefault("color_scale_split_by", None)
    if ("values" in updates or "formulas" in updates) and "value_order" not in updates:
        pivot["value_order"] = [
            *(value["id"] for value in pivot["values"] if isinstance(value, dict)),
            *(
                formula if isinstance(formula, str) else formula.get("id", "")
                for formula in (pivot["formulas"] if isinstance(pivot["formulas"], list) else [])
                if isinstance(formula, (str, dict))
            ),
        ]
    return pivot


def _formula(**updates: object) -> dict[str, object]:
    formula: dict[str, object] = {
        "id": "formula_1",
        "reference": "claim_share",
        "display_name": "Claim share",
        "expression": 'pl.col("claims").sum() / 100',
        "number_format": "general",
        "decimal_places": None,
        "use_grouping": True,
    }
    formula.update(updates)
    return formula


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


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (("value_order",), "value_order is required"),
        (("columns", 0, "number_format"), "number format is required"),
        (("columns", 0, "decimal_places"), "decimal places is required"),
        (("columns", 0, "use_grouping"), "grouping preference is required"),
        (("rows", 0, "number_format"), "number format is required"),
        (("values", 0, "color_scale_split_by"), "colour scale split is required"),
    ],
)
def test_validate_explore_pivots_rejects_missing_current_v1_fields(
    path: tuple[str | int, ...], message: str
) -> None:
    pivot = _pivot()
    target: object = pivot
    for segment in path[:-1]:
        target = target[segment]  # type: ignore[index]
    del target[path[-1]]  # type: ignore[index]

    with pytest.raises(ConfigError, match=message):
        validate_explore_pivots([pivot], context="test")


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {
                "values": [
                    {
                        "id": "value_1",
                        "field": "claims",
                        "aggregation": "sum",
                        "display_name": "Claims",
                    }
                ]
            },
            "reference",
        ),
        (
            {
                "values": [
                    {
                        "id": "value_1",
                        "field": "claims",
                        "aggregation": "sum",
                        "reference": "value_1",
                        "display_name": "Claims",
                    }
                ]
            },
            "reference",
        ),
        (
            {
                "values": [
                    {
                        "id": "value_1",
                        "field": "claims",
                        "aggregation": "sum",
                        "reference": "sum_claims",
                        "display_name": "Claims",
                    }
                ]
            },
            "reference",
        ),
        (
            {
                "formulas": [
                    {"id": "formula_1", "display_name": "Claim share", "expression": "pl.lit(1)"}
                ]
            },
            "reference",
        ),
        ({"formulas": None}, "formulas must be a list"),
    ],
)
def test_validate_explore_pivots_rejects_noncanonical_required_fields(
    updates: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_explore_pivots([_pivot(**updates)], context="test")


def test_validate_explore_pivots_accepts_double_digit_duplicate_reference_suffixes() -> None:
    values = [
        {
            "id": f"value_{position}",
            "field": "claims",
            "aggregation": "sum",
            "reference": "claims_sum" if position == 1 else f"claims_sum_{position}",
            "display_name": "Claims",
        }
        for position in range(1, 11)
    ]

    validated = validate_explore_pivots([_pivot(values=values)], context="test")

    assert validated[0]["values"][-1]["reference"] == "claims_sum_10"


def test_validate_explore_pivot_state_resolves_shared_formulas_and_rejects_unknown_ids() -> None:
    definitions = [_formula()]
    first = _pivot(formulas=["formula_1"])
    second = _pivot(id="pivot_2", name="Second", formulas=["formula_1"])

    shared, pivots = validate_explore_pivot_state(definitions, [first, second], context="test")

    assert shared[0]["id"] == "formula_1"
    assert [pivot["formulas"][0] for pivot in pivots] == ["formula_1", "formula_1"]
    with pytest.raises(ConfigError, match="unknown shared formula id"):
        validate_explore_pivot_state(definitions, [_pivot(formulas=["missing"])], context="test")


def test_validate_explore_pivot_state_rejects_inline_formula_selections() -> None:
    definition = _formula()

    with pytest.raises(ConfigError, match="formula.*id"):
        validate_explore_pivot_state([definition], [_pivot(formulas=[definition])], context="test")


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("number_format", "number format is required"),
        ("decimal_places", "decimal places is required"),
        ("use_grouping", "grouping preference is required"),
    ],
)
def test_validate_explore_pivot_state_rejects_incomplete_formula_formatting(
    field: str, message: str
) -> None:
    definition = _formula()
    del definition[field]

    with pytest.raises(ConfigError, match=message):
        validate_explore_pivot_state([definition], [_pivot(formulas=["formula_1"])], context="test")


def test_validate_explore_pivot_state_reports_library_errors_at_the_formula_position() -> None:
    bad = _formula(id="formula_2", reference="second_share")
    del bad["number_format"]

    with pytest.raises(ConfigError, match="number format is required") as exc_info:
        validate_explore_pivot_state([_formula(), bad], [], context="test")
    assert exc_info.value.context["index"] == 1


def test_value_order_requires_current_v1_cards_and_validates_selected_formula_order() -> None:
    definition = _formula()
    values = [
        _pivot()["values"][0],
        {
            "id": "value_2",
            "field": "paid",
            "aggregation": "sum",
            "reference": "paid_sum",
            "display_name": "Paid",
        },
    ]

    missing_order = _pivot(values=values)
    del missing_order["value_order"]
    with pytest.raises(ConfigError, match="value_order is required"):
        validate_explore_pivots([missing_order], context="test")

    _, pivots = validate_explore_pivot_state(
        [definition],
        [
            _pivot(
                values=values,
                formulas=["formula_1"],
                value_order=["formula_1", "value_2", "value_1"],
            )
        ],
        context="test",
    )
    assert pivots[0]["value_order"] == ["formula_1", "value_2", "value_1"]


@pytest.mark.parametrize(
    "value_order",
    [
        ["value_1", "value_1"],
        [],
        ["value_1", "missing"],
        None,
        "value_1",
        ["value_1", ""],
    ],
)
def test_value_order_rejects_duplicate_missing_unknown_or_malformed_ids(
    value_order: object,
) -> None:
    with pytest.raises(ConfigError, match="value_order"):
        validate_explore_pivots([_pivot(value_order=value_order)], context="test")


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
                "reference": "claims_sum",
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
                            "reference": "claims_sum",
                            "display_name": "Claims",
                            "use_grouping": use_grouping,
                        }
                    ]
                )
            ],
            context="test",
        )


def test_validate_explore_pivots_rejects_fixed_decimals_without_a_number_format() -> None:
    pivot = _pivot(columns=[{"id": "column_1", "field": "year", "decimal_places": 2}])
    del pivot["columns"][0]["number_format"]

    with pytest.raises(ConfigError, match="number format is required"):
        validate_explore_pivots([pivot], context="test")


def test_validate_explore_pivots_derives_legacy_active_value_sort_target() -> None:
    pivot = _pivot(
        values=[
            {
                "id": "value_1",
                "field": "claims",
                "aggregation": "sum",
                "reference": "claims_sum",
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
                        {
                            "id": "value_1",
                            "field": "a",
                            "aggregation": "mode",
                            "reference": "a_mode",
                            "display_name": "A",
                        }
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
                            "reference": "claims_sum",
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
                            "reference": "claims_sum",
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
                            "reference": "claims_sum",
                            "display_name": "Claims",
                            "sort_rows": "ascending",
                            "color_scale": "none",
                        },
                        {
                            "id": "value_2",
                            "field": "claims",
                            "aggregation": "average",
                            "reference": "claims_mean",
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
                            "reference": "claims_sum",
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
                "reference": "claims_sum",
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
                "reference": "claims_mean",
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


def test_validate_explore_pivots_accepts_grouped_source_field_formulas() -> None:
    pivot = _pivot(
        formulas=[
            {
                "id": "formula_1",
                "reference": "claims_ratio",
                "display_name": "Claims ratio",
                "expression": 'pl.col("claims").sum() / pl.col("claims").mean()',
                "number_format": "number",
                "decimal_places": 2,
                "use_grouping": False,
            }
        ],
        values=[
            {
                "id": "value_1",
                "field": "claims",
                "aggregation": "sum",
                "reference": "claims_sum",
                "display_name": "Claims",
            },
            {
                "id": "value_2",
                "field": "claims",
                "aggregation": "average",
                "reference": "claims_mean",
                "display_name": "Average claims",
            },
        ],
    )

    validated = validate_explore_pivots([pivot], context="test")[0]

    assert validated["formulas"] == pivot["formulas"]
    assert [value["reference"] for value in validated["values"]] == [
        "claims_sum",
        "claims_mean",
    ]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {
                "values": [
                    {
                        "id": "value_1",
                        "field": "claims",
                        "aggregation": "sum",
                        "reference": "not valid",
                        "display_name": "Claims",
                    }
                ]
            },
            "reference",
        ),
        ({"formulas": {}}, "formulas must be a list"),
        ({"formulas": [{}]}, "formula id"),
        (
            {
                "formulas": [
                    {
                        "id": "formula_1",
                        "reference": "ratio",
                        "display_name": "Ratio",
                        "expression": "  ",
                    }
                ]
            },
            "formula expression",
        ),
        (
            {
                "formulas": [
                    {
                        "id": "value_1",
                        "reference": "ratio",
                        "display_name": "Ratio",
                        "expression": "pl.lit(1)",
                    }
                ]
            },
            "duplicate placement id",
        ),
        (
            {
                "formulas": [
                    {
                        "id": "formula_1",
                        "reference": "claims_sum",
                        "display_name": "Ratio",
                        "expression": "pl.lit(1)",
                    }
                ]
            },
            "duplicate formula reference",
        ),
        (
            {
                "formulas": [
                    {
                        "id": "formula_1",
                        "reference": "__haute_private",
                        "display_name": "Ratio",
                        "expression": "pl.lit(1)",
                    }
                ]
            },
            "reference",
        ),
    ],
)
def test_validate_explore_pivots_rejects_malformed_formulas(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_explore_pivots([_pivot(**updates)], context="test")


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
