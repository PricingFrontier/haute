"""The ratebook combined-factor collar (Q17).

A ratebook solve scored every quote at a scenario-grid step inside
``[sv_min, sv_max]``; the deployed ``optimised_factor`` is clipped to the same
range so no quote deploys at a factor the solve never scored. These tests pin
the collar's parsing, its capture from the grid, and ``_apply_ratebook``'s
order: neutral fill of unseen levels, then the product, then the clamp, with
per-factor columns never clamped. Edge assertions use exact equality.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl
import pytest

from haute._builders import _apply_ratebook
from haute._ratebook_collar import (
    COMBINED_FACTOR_BOUNDS_KEY,
    CombinedFactorBoundsError,
    combined_factor_bounds_from_grid,
    parse_combined_factor_bounds,
)

_SEP = "\x1f"


def _artifact(
    tables: dict[str, list[tuple[str, float]]],
    *,
    bounds: Any,
    composite: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """A saved ratebook artifact: String factors, one table per entry of *tables*."""
    composite = composite or {}
    artifact: dict[str, Any] = {
        "version": "rb",
        "mode": "ratebook",
        "lambdas": {},
        "factor_tables": {
            name: [
                {"__factor_group__": level, "optimal_scenario_value": value}
                for level, value in entries
            ]
            for name, entries in tables.items()
        },
        "factor_dtypes": {
            name: [
                {"column": column, "dtype": {"kind": "String"}}
                for column in composite.get(name, [name])
            ]
            for name in tables
        },
    }
    if bounds is not _MISSING:
        artifact[COMBINED_FACTOR_BOUNDS_KEY] = bounds
    return artifact


_MISSING = object()


def _apply(frame: pl.DataFrame, artifact: dict[str, Any], output: str = "") -> pl.DataFrame:
    return _apply_ratebook(frame.lazy(), artifact, "", "__v__", output).collect()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ({"min": 0.9, "max": 1.1}, (0.9, 1.1)),
        ({"min": 1, "max": 2}, (1.0, 2.0)),
        ({"min": 1.0, "max": 1.0}, (1.0, 1.0)),
    ],
)
def test_valid_bounds_parse(bounds: dict[str, Any], expected: tuple[float, float]) -> None:
    assert parse_combined_factor_bounds(bounds) == expected


@pytest.mark.parametrize(
    ("bounds", "message"),
    [
        (None, "must be an object with 'min' and 'max', got NoneType"),
        ([0.9, 1.1], "must be an object with 'min' and 'max', got list"),
        ({"min": 0.9}, "exactly the keys 'min' and 'max', got ['min']"),
        ({"min": 0.9, "max": 1.1, "step": 0.1}, "got ['max', 'min', 'step']"),
        ({"min": "0.9", "max": 1.1}, "combined_factor_bounds.min must be a number, got '0.9'"),
        ({"min": 0.9, "max": True}, "combined_factor_bounds.max must be a number, got True"),
        ({"min": math.nan, "max": 1.1}, "combined_factor_bounds.min must be finite, got nan"),
        ({"min": 0.9, "max": math.inf}, "combined_factor_bounds.max must be finite, got inf"),
        ({"min": 1.1, "max": 0.9}, "min 1.1 is greater than max 0.9"),
    ],
)
def test_malformed_bounds_raise_naming_the_problem(bounds: Any, message: str) -> None:
    with pytest.raises(CombinedFactorBoundsError) as caught:
        parse_combined_factor_bounds(bounds)

    assert message in str(caught.value)


def test_bounds_come_from_the_grid_ends_as_the_solver_scored_them() -> None:
    grid = np.linspace(0.9, 1.1, 5, dtype=np.float32)

    bounds = combined_factor_bounds_from_grid(grid.tolist())

    assert bounds == {"min": float(np.float32(0.9)), "max": float(np.float32(1.1))}
    assert bounds["max"] != 1.1  # Float32 widened, not the decimal the user typed


def test_an_unsorted_grid_is_rejected_rather_than_misread() -> None:
    with pytest.raises(CombinedFactorBoundsError, match="not sorted ascending"):
        combined_factor_bounds_from_grid([1.0, 0.9, 1.1])


def test_an_empty_grid_has_no_bounds() -> None:
    with pytest.raises(CombinedFactorBoundsError, match="no scenario values"):
        combined_factor_bounds_from_grid([])


# ---------------------------------------------------------------------------
# Apply: the clamp
# ---------------------------------------------------------------------------


def test_a_product_above_the_collar_deploys_at_exactly_max() -> None:
    artifact = _artifact({"region": [("A", 1.3)]}, bounds={"min": 0.9, "max": 1.1})

    result = _apply(pl.DataFrame({"region": ["A"]}), artifact)

    assert result["optimised_factor"].to_list() == [1.1]
    assert result["region_optimised_factor"].to_list() == [1.3]


def test_a_product_below_the_collar_deploys_at_exactly_min() -> None:
    artifact = _artifact({"region": [("A", 0.5)]}, bounds={"min": 0.9, "max": 1.1})

    result = _apply(pl.DataFrame({"region": ["A"]}), artifact)

    assert result["optimised_factor"].to_list() == [0.9]


def test_products_inside_or_on_the_collar_are_unchanged_bit_for_bit() -> None:
    low, high = float(np.float32(0.9)), float(np.float32(1.1))
    inside = 1.0370000000000001
    artifact = _artifact(
        {"region": [("low", low), ("high", high), ("inside", inside)]},
        bounds={"min": low, "max": high},
    )

    result = _apply(pl.DataFrame({"region": ["low", "high", "inside"]}), artifact)

    assert result["optimised_factor"].to_list() == [low, high, inside]


def test_a_multi_factor_product_is_clamped_but_each_factor_is_not() -> None:
    artifact = _artifact(
        {
            "region": [("A", 1.08), ("B", 0.92)],
            "age": [("young", 1.07), ("old", 0.97)],
        },
        bounds={"min": 0.95, "max": 1.05},
    )
    frame = pl.DataFrame({"region": ["A", "B", "A"], "age": ["young", "old", "old"]})

    result = _apply(frame, artifact)

    assert result["region_optimised_factor"].to_list() == [1.08, 0.92, 1.08]
    assert result["age_optimised_factor"].to_list() == [1.07, 0.97, 0.97]
    # 1.1556 -> max; 0.8924 -> min; 1.0476 inside, untouched.
    assert result["optimised_factor"].to_list() == [1.05, 0.95, 1.08 * 0.97]


def test_a_composite_factor_product_is_clamped() -> None:
    artifact = _artifact(
        {"channel:age": [(f"web{_SEP}young", 1.4), (f"phone{_SEP}old", 1.02)]},
        bounds={"min": 0.8, "max": 1.2},
        composite={"channel:age": ["channel", "age"]},
    )
    frame = pl.DataFrame({"channel": ["web", "phone"], "age": ["young", "old"]})

    result = _apply(frame, artifact)

    assert result["channel:age_optimised_factor"].to_list() == [1.4, 1.02]
    assert result["optimised_factor"].to_list() == [1.2, 1.02]


def test_an_unseen_level_fills_neutral_before_the_product_is_clamped() -> None:
    artifact = _artifact(
        {"region": [("A", 1.3)], "age": [("young", 1.2)]},
        bounds={"min": 0.9, "max": 1.25},
    )
    frame = pl.DataFrame({"region": ["A", "unseen"], "age": ["unseen", "young"]})

    result = _apply(frame, artifact)

    assert result["region_optimised_factor"].to_list() == [1.3, 1.0]
    assert result["age_optimised_factor"].to_list() == [1.0, 1.2]
    # 1.3 * 1.0 -> 1.25; 1.0 * 1.2 inside.
    assert result["optimised_factor"].to_list() == [1.25, 1.2]


def test_on_a_grid_without_one_an_all_unseen_quote_deploys_at_the_nearest_end() -> None:
    low = float(np.float32(1.02))
    artifact = _artifact(
        {"region": [("A", 1.05)]},
        bounds={"min": low, "max": float(np.float32(1.1))},
    )

    result = _apply(pl.DataFrame({"region": ["unseen"]}), artifact)

    assert result["region_optimised_factor"].to_list() == [1.0]
    assert result["optimised_factor"].to_list() == [low]


def test_a_single_value_collar_deploys_every_quote_at_that_value() -> None:
    artifact = _artifact(
        {"region": [("A", 1.3), ("B", 0.7), ("C", 1.0)]},
        bounds={"min": 1.0, "max": 1.0},
    )

    result = _apply(pl.DataFrame({"region": ["A", "B", "C", "unseen"]}), artifact)

    assert result["optimised_factor"].to_list() == [1.0, 1.0, 1.0, 1.0]


def test_the_configured_output_column_carries_the_clamped_value() -> None:
    artifact = _artifact({"region": [("A", 1.3)]}, bounds={"min": 0.9, "max": 1.1})

    result = _apply(pl.DataFrame({"region": ["A"]}), artifact, "price_factor")

    assert result["price_factor"].to_list() == [1.1]
    assert "optimised_factor" not in result.columns


def test_the_clamp_is_lazy_and_keeps_the_float64_output_schema() -> None:
    artifact = _artifact({"region": [("A", 1.3)]}, bounds={"min": 0.9, "max": 1.1})

    lazy = _apply_ratebook(pl.LazyFrame({"region": ["A"]}), artifact, "", "__v__", "")

    assert isinstance(lazy, pl.LazyFrame)
    assert lazy.collect_schema()["optimised_factor"] == pl.Float64


def test_an_artifact_without_bounds_is_rejected_at_apply() -> None:
    artifact = _artifact({"region": [("A", 1.0)]}, bounds=_MISSING)

    with pytest.raises(CombinedFactorBoundsError, match="must be an object"):
        _apply(pl.DataFrame({"region": ["A"]}), artifact)


@pytest.mark.parametrize(
    "bounds",
    [
        {"min": 1.1, "max": 0.9},
        {"min": math.nan, "max": 1.1},
        {"min": "0.9", "max": 1.1},
        {"max": 1.1},
    ],
)
def test_malformed_bounds_are_rejected_at_apply(bounds: dict[str, Any]) -> None:
    artifact = _artifact({"region": [("A", 1.0)]}, bounds=bounds)

    with pytest.raises(CombinedFactorBoundsError):
        _apply(pl.DataFrame({"region": ["A"]}), artifact)
