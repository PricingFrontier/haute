"""Validation contract for persisted Explore pivot cards."""

from __future__ import annotations

import pytest

from haute._explore_pivots import validate_explore_pivots
from haute.errors import ConfigError


def test_validate_explore_pivots_preserves_order_and_future_fields() -> None:
    pivots = [
        {
            "id": "pivot_1",
            "future_setting": {"rows": ["region"], "columns": ["year"], "limit": None},
        },
        {"id": "pivot_2"},
    ]

    assert validate_explore_pivots(pivots, context="test") == pivots


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({}, "must be a list"),
        (["pivot_1"], "entries must be dicts"),
        ([{}], "requires an id"),
        ([{"id": "   "}], "id must be a non-empty string"),
        ([{"id": "pivot_1"}, {"id": "pivot_1"}], "duplicate pivot id"),
        ([{"id": "pivot_1", "future": object()}], "simple literals"),
    ],
)
def test_validate_explore_pivots_rejects_malformed_values(value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_explore_pivots(value, context="test")


def test_validate_explore_pivots_returns_a_detached_copy() -> None:
    raw = [{"id": "pivot_1", "future": {"nested": [1, 2]}}]

    validated = validate_explore_pivots(raw, context="test")

    assert validated == raw
    assert validated is not raw
    assert validated[0] is not raw[0]
