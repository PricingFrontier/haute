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
        "columns": [{"id": "column_1", "field": "year"}],
        "rows": [{"id": "row_1", "field": "region"}],
        "values": [
            {
                "id": "value_1",
                "field": "claims",
                "aggregation": "sum",
                "display_name": "Claims",
            }
        ],
        "options": {
            "row_grand_totals": True,
            "column_grand_totals": True,
        },
    }
    pivot.update(updates)
    return pivot


def test_validate_explore_pivots_migrates_v0_and_preserves_order_and_future_fields() -> None:
    raw = [
        {
            "id": "pivot_1",
            "future_setting": {"format": "accounting", "limit": None},
        },
        {"id": "pivot_2"},
    ]

    assert validate_explore_pivots(raw, context="test") == [
        {
            "id": "pivot_1",
            "future_setting": {"format": "accounting", "limit": None},
            "version": 1,
            "name": "Pivot 1",
            "enabled": True,
            "filters": [],
            "columns": [],
            "rows": [],
            "values": [],
            "options": {
                "row_grand_totals": True,
                "column_grand_totals": True,
            },
        },
        {
            "id": "pivot_2",
            "version": 1,
            "name": "Pivot 2",
            "enabled": True,
            "filters": [],
            "columns": [],
            "rows": [],
            "values": [],
            "options": {
                "row_grand_totals": True,
                "column_grand_totals": True,
            },
        },
    ]


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
    ("value", "message"),
    [
        ({}, "must be a list"),
        (["pivot_1"], "entries must be dicts"),
        ([{}], "requires an id"),
        ([{"id": "   "}], "id must be a non-empty string"),
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


def test_validate_explore_pivots_allows_repeated_value_fields_with_unique_ids() -> None:
    pivot = _pivot(
        values=[
            {"id": "value_1", "field": "claims", "aggregation": "sum", "display_name": "Claims"},
            {
                "id": "value_2",
                "field": "claims",
                "aggregation": "average",
                "display_name": "Average claims",
            },
        ]
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
