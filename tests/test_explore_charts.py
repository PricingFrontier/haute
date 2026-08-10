"""Validation contract for persisted Explore chart cards."""

from __future__ import annotations

import pytest

from haute._explore_charts import validate_explore_charts
from haute.errors import ConfigError


def test_validate_explore_charts_preserves_order_state_and_future_fields() -> None:
    charts = [
        {
            "id": "chart_1",
            "enabled": True,
            "future_setting": {"palette": "warm", "columns": ["premium"], "limit": None},
        },
        {"id": "chart_2", "enabled": False},
    ]

    assert validate_explore_charts(charts, context="test") == charts


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({}, "must be a list"),
        (["chart_1"], "entries must be dicts"),
        ([{"enabled": True}], "requires an id"),
        ([{"id": "   ", "enabled": True}], "id must be a non-empty string"),
        ([{"id": "chart_1"}], "requires an enabled state"),
        ([{"id": "chart_1", "enabled": 1}], "enabled state must be a boolean"),
        (
            [
                {"id": "chart_1", "enabled": True},
                {"id": "chart_1", "enabled": False},
            ],
            "duplicate chart id",
        ),
        ([{"id": "chart_1", "enabled": True, "future": object()}], "simple literals"),
    ],
)
def test_validate_explore_charts_rejects_malformed_values(value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_explore_charts(value, context="test")


def test_validate_explore_charts_returns_a_detached_copy() -> None:
    raw = [{"id": "chart_1", "enabled": True, "future": {"nested": [1, 2]}}]

    validated = validate_explore_charts(raw, context="test")

    assert validated == raw
    assert validated is not raw
    assert validated[0] is not raw[0]
