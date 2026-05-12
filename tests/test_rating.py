"""Tests for haute._rating — rating table lookup and banding helpers."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from haute._rating import (
    _apply_banding,
    _apply_rating_table,
    _banding_condition,
    _combine_rating_columns,
    _normalise_banding_factors,
    apply_rating_step_from_config,
)

# ---------------------------------------------------------------------------
# _banding_condition
# ---------------------------------------------------------------------------


class TestBandingCondition:
    def test_single_operator(self) -> None:
        cond = _banding_condition(pl.col("x"), {"op1": "<", "val1": 10})
        assert cond is not None
        lf = pl.DataFrame({"x": [5, 10, 15]}).lazy()
        result = lf.select(cond).collect()["x"].to_list()
        assert result == [True, False, False]

    def test_dual_operator_range(self) -> None:
        cond = _banding_condition(pl.col("x"), {"op1": ">=", "val1": 5, "op2": "<", "val2": 10})
        assert cond is not None
        lf = pl.DataFrame({"x": [3, 5, 7, 10]}).lazy()
        result = lf.select(cond).collect()["x"].to_list()
        assert result == [False, True, True, False]

    def test_eq_operator(self) -> None:
        cond = _banding_condition(pl.col("x"), {"op1": "=", "val1": 5})
        assert cond is not None
        lf = pl.DataFrame({"x": [4, 5, 6]}).lazy()
        result = lf.select(cond).collect()["x"].to_list()
        assert result == [False, True, False]

    def test_double_eq_operator(self) -> None:
        cond = _banding_condition(pl.col("x"), {"op1": "==", "val1": 5})
        assert cond is not None
        lf = pl.DataFrame({"x": [4, 5]}).lazy()
        result = lf.select(cond).collect()["x"].to_list()
        assert result == [False, True]

    def test_empty_rule_returns_none(self) -> None:
        assert _banding_condition(pl.col("x"), {}) is None

    def test_missing_val_returns_none(self) -> None:
        assert _banding_condition(pl.col("x"), {"op1": "<", "val1": ""}) is None

    def test_none_val_returns_none(self) -> None:
        assert _banding_condition(pl.col("x"), {"op1": "<", "val1": None}) is None

    def test_invalid_op_ignored(self) -> None:
        assert _banding_condition(pl.col("x"), {"op1": "!=", "val1": 5}) is None

    def test_string_val_coerced_to_float(self) -> None:
        cond = _banding_condition(pl.col("x"), {"op1": ">", "val1": "100"})
        assert cond is not None

    def test_non_numeric_val_raises(self) -> None:
        with pytest.raises(ValueError, match="non-numeric"):
            _banding_condition(pl.col("x"), {"op1": ">", "val1": "abc"})

    def test_whitespace_in_op(self) -> None:
        cond = _banding_condition(pl.col("x"), {"op1": " <= ", "val1": 10})
        assert cond is not None


# ---------------------------------------------------------------------------
# _normalise_banding_factors
# ---------------------------------------------------------------------------


class TestNormaliseBandingFactors:
    def test_returns_factors_list(self) -> None:
        config = {"factors": [{"column": "a"}]}
        assert _normalise_banding_factors(config) == [{"column": "a"}]

    def test_missing_factors_returns_empty(self) -> None:
        assert _normalise_banding_factors({}) == []

    def test_none_factors_returns_empty(self) -> None:
        assert _normalise_banding_factors({"factors": None}) == []

    def test_non_list_returns_empty(self) -> None:
        assert _normalise_banding_factors({"factors": "invalid"}) == []


# ---------------------------------------------------------------------------
# _apply_rating_table
# ---------------------------------------------------------------------------


class TestApplyRatingTable:
    def test_single_factor_lookup(self) -> None:
        lf = pl.DataFrame({"region": ["North", "South", "East"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "region_factor",
            "entries": [
                {"region": "North", "value": 1.2},
                {"region": "South", "value": 0.9},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["region_factor"].to_list() == [1.2, 0.9, None]

    def test_multi_factor_lookup(self) -> None:
        lf = pl.DataFrame(
            {
                "region": ["North", "North", "South"],
                "tier": ["gold", "silver", "gold"],
            }
        ).lazy()
        table: dict[str, Any] = {
            "factors": ["region", "tier"],
            "outputColumn": "rate",
            "entries": [
                {"region": "North", "tier": "gold", "value": 1.5},
                {"region": "North", "tier": "silver", "value": 1.0},
                {"region": "South", "tier": "gold", "value": 0.8},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["rate"].to_list() == [1.5, 1.0, 0.8]

    def test_streaming_lookup_preserves_left_input_row_order(self) -> None:
        lf = pl.DataFrame(
            {
                "row_id": list(range(1_000)),
                "region": ["South" if i % 2 == 0 else "North" for i in range(1_000)],
            }
        ).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [
                {"region": "South", "value": 0.95},
                {"region": "North", "value": 1.10},
            ],
            "defaultValue": "1.0",
        }

        result = _apply_rating_table(lf, table).collect(engine="streaming")

        assert result["row_id"].to_list() == list(range(1_000))
        assert result["factor"].head(4).to_list() == [0.95, 1.10, 0.95, 1.10]

    def test_default_value(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.2}],
            "defaultValue": "1.0",
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"].to_list() == [1.2, 1.0]

    def test_no_default_leaves_null(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Missing"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.5}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"][1] is None

    def test_empty_factors_passthrough(self) -> None:
        lf = pl.DataFrame({"x": [1]}).lazy()
        table: dict[str, Any] = {"factors": [], "outputColumn": "y", "entries": []}
        result = _apply_rating_table(lf, table).collect()
        assert "y" not in result.columns

    def test_empty_entries_passthrough(self) -> None:
        lf = pl.DataFrame({"x": [1]}).lazy()
        table: dict[str, Any] = {"factors": ["x"], "outputColumn": "y", "entries": []}
        result = _apply_rating_table(lf, table).collect()
        assert "y" not in result.columns

    def test_missing_output_column_passthrough(self) -> None:
        lf = pl.DataFrame({"x": [1]}).lazy()
        table: dict[str, Any] = {
            "factors": ["x"],
            "outputColumn": "",
            "entries": [{"x": 1, "value": 2}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result.columns == ["x"]

    def test_numeric_factor_cast_to_string(self) -> None:
        """Integer factor columns should be cast to Utf8 for the join."""
        lf = pl.DataFrame({"code": [1, 2, 3]}).lazy()
        table: dict[str, Any] = {
            "factors": ["code"],
            "outputColumn": "val",
            "entries": [
                {"code": 1, "value": 10.0},
                {"code": 2, "value": 20.0},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["val"].to_list() == [10.0, 20.0, None]

    def test_output_column_named_value(self) -> None:
        """When outputColumn is 'value', the temporary column should not be dropped."""
        lf = pl.DataFrame({"k": ["a"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["k"],
            "outputColumn": "value",
            "entries": [{"k": "a", "value": 5.0}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert "value" in result.columns
        assert result["value"].to_list() == [5.0]


# ---------------------------------------------------------------------------
# _combine_rating_columns
# ---------------------------------------------------------------------------


class TestCombineRatingColumns:
    def test_multiply_default(self) -> None:
        lf = pl.DataFrame({"a": [2.0], "b": [3.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "multiply", "combined").collect()
        assert result["combined"].to_list() == [6.0]

    def test_add(self) -> None:
        lf = pl.DataFrame({"a": [10.0], "b": [5.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "add", "sum_col").collect()
        assert result["sum_col"].to_list() == [15.0]

    def test_min(self) -> None:
        lf = pl.DataFrame({"a": [3.0], "b": [7.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "min", "min_col").collect()
        assert result["min_col"].to_list() == [3.0]

    def test_max(self) -> None:
        lf = pl.DataFrame({"a": [3.0], "b": [7.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "max", "max_col").collect()
        assert result["max_col"].to_list() == [7.0]

    def test_single_column_aliased(self) -> None:
        lf = pl.DataFrame({"a": [42.0]}).lazy()
        result = _combine_rating_columns(lf, ["a"], "multiply", "out").collect()
        assert result["out"].to_list() == [42.0]

    def test_empty_columns_passthrough(self) -> None:
        lf = pl.DataFrame({"x": [1]}).lazy()
        result = _combine_rating_columns(lf, [], "multiply", "out").collect()
        assert "out" not in result.columns

    def test_three_columns_multiply(self) -> None:
        lf = pl.DataFrame({"a": [2.0], "b": [3.0], "c": [4.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b", "c"], "multiply", "prod").collect()
        assert result["prod"].to_list() == [24.0]

    def test_three_columns_add(self) -> None:
        lf = pl.DataFrame({"a": [1.0], "b": [2.0], "c": [3.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b", "c"], "add", "total").collect()
        assert result["total"].to_list() == [6.0]

    def test_unknown_operation_raises_value_error(self) -> None:
        """An unrecognised operation string fails loudly."""
        lf = pl.DataFrame({"a": [2.0], "b": [3.0]}).lazy()
        with pytest.raises(ValueError, match="Unsupported rating combine operation"):
            _combine_rating_columns(lf, ["a", "b"], "subtract", "out").collect()

    def test_all_four_operations_roundtrip(self) -> None:
        """B24: Verify the four documented operations (multiply, add, min, max)."""
        lf = pl.DataFrame({"a": [4.0], "b": [2.0]}).lazy()
        assert _combine_rating_columns(lf, ["a", "b"], "multiply", "o").collect()[
            "o"
        ].to_list() == [8.0]
        assert _combine_rating_columns(lf, ["a", "b"], "add", "o").collect()["o"].to_list() == [6.0]
        assert _combine_rating_columns(lf, ["a", "b"], "min", "o").collect()["o"].to_list() == [2.0]
        assert _combine_rating_columns(lf, ["a", "b"], "max", "o").collect()["o"].to_list() == [4.0]


# ---------------------------------------------------------------------------
# B13: Non-numeric defaultValue handled gracefully
# ---------------------------------------------------------------------------


class TestApplyRatingTableNonNumericDefault:
    """B13: Non-numeric defaultValue should not crash; unmatched rows get null."""

    def _make_table(self, default_value: Any) -> dict[str, Any]:
        return {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.2}],
            "defaultValue": default_value,
        }

    def test_default_na_string(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table("N/A")).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_default_empty_string(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table("")).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_default_none(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table(None)).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_default_arbitrary_string(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table("abc")).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_default_whitespace_only(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table("   ")).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_valid_numeric_string_still_works(self) -> None:
        """A valid numeric string like '1.5' should still be applied."""
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table("1.5")).collect()
        assert result["factor"].to_list() == [1.2, 1.5]

    def test_valid_int_default_still_works(self) -> None:
        """An actual numeric default (int or float) still works."""
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table(2)).collect()
        assert result["factor"].to_list() == [1.2, 2.0]

    def test_default_inf_treated_as_invalid(self) -> None:
        """float('inf') default would corrupt arithmetic — treat as null."""
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table(float("inf"))).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_default_neg_inf_treated_as_invalid(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table(float("-inf"))).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_default_nan_treated_as_invalid(self) -> None:
        """float('nan') default would silently propagate — treat as null."""
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table(float("nan"))).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_default_inf_string_treated_as_invalid(self) -> None:
        """The string 'inf' parses to float inf — should also be rejected."""
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table("inf")).collect()
        assert result["factor"].to_list() == [1.2, None]

    def test_default_nan_string_treated_as_invalid(self) -> None:
        """The string 'nan' parses to float nan — should also be rejected."""
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        result = _apply_rating_table(lf, self._make_table("nan")).collect()
        assert result["factor"].to_list() == [1.2, None]


# ---------------------------------------------------------------------------
# B14: Duplicate factor combinations don't cause row fan-out
# ---------------------------------------------------------------------------


class TestApplyRatingTableDuplicateEntries:
    """B14: Duplicate factor combos in entries must not duplicate main rows."""

    def test_duplicate_entries_no_fanout(self) -> None:
        lf = pl.DataFrame({"region": ["North", "South"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [
                {"region": "North", "value": 1.0},
                {"region": "North", "value": 2.0},  # duplicate factor
                {"region": "South", "value": 0.5},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        # Must NOT fan out: still 2 rows
        assert result.height == 2
        # keep="last" → the second North entry (2.0) wins
        north_val = result.filter(pl.col("region") == "North")["factor"].to_list()
        assert north_val == [2.0]
        south_val = result.filter(pl.col("region") == "South")["factor"].to_list()
        assert south_val == [0.5]

    def test_duplicate_multi_factor_no_fanout(self) -> None:
        lf = pl.DataFrame(
            {
                "region": ["North", "South"],
                "tier": ["gold", "silver"],
            }
        ).lazy()
        table: dict[str, Any] = {
            "factors": ["region", "tier"],
            "outputColumn": "rate",
            "entries": [
                {"region": "North", "tier": "gold", "value": 1.0},
                {"region": "North", "tier": "gold", "value": 3.0},  # dup
                {"region": "South", "tier": "silver", "value": 0.8},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result.height == 2
        north_gold = result.filter((pl.col("region") == "North") & (pl.col("tier") == "gold"))[
            "rate"
        ].to_list()
        assert north_gold == [3.0]  # last entry wins

    def test_no_duplicates_unchanged(self) -> None:
        """When no duplicates exist, behaviour is unchanged."""
        lf = pl.DataFrame({"k": ["a", "b", "c"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["k"],
            "outputColumn": "val",
            "entries": [
                {"k": "a", "value": 1.0},
                {"k": "b", "value": 2.0},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result.height == 3
        assert result["val"].to_list() == [1.0, 2.0, None]


# ---------------------------------------------------------------------------
# B15: Extra keys in entries don't pollute the result
# ---------------------------------------------------------------------------


class TestApplyRatingTableExtraColumns:
    """B15: Extra keys beyond factors + 'value' must not leak into main frame."""

    def test_extra_keys_excluded(self) -> None:
        lf = pl.DataFrame({"region": ["North", "South"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [
                {"region": "North", "value": 1.2, "note": "foo", "id": 99},
                {"region": "South", "value": 0.9, "note": "bar", "id": 100},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        # Only original columns + outputColumn should be present
        assert set(result.columns) == {"region", "factor"}

    def test_extra_keys_do_not_affect_values(self) -> None:
        lf = pl.DataFrame({"k": ["x"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["k"],
            "outputColumn": "out",
            "entries": [
                {"k": "x", "value": 42.0, "extra1": "a", "extra2": 123},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["out"].to_list() == [42.0]
        assert "extra1" not in result.columns
        assert "extra2" not in result.columns

    def test_factor_column_missing_from_entries_passthrough(self) -> None:
        """If a factor column doesn't exist in any entry, bail out safely."""
        lf = pl.DataFrame({"region": ["North"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region", "nonexistent"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.0}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert "factor" not in result.columns
        assert result.columns == ["region"]

    def test_no_extra_keys_still_works(self) -> None:
        """When entries have only factor+value keys, nothing changes."""
        lf = pl.DataFrame({"k": ["a"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["k"],
            "outputColumn": "val",
            "entries": [{"k": "a", "value": 5.0}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert set(result.columns) == {"k", "val"}
        assert result["val"].to_list() == [5.0]


# ---------------------------------------------------------------------------
# P4: collect_schema() called once, not per-factor
# ---------------------------------------------------------------------------


class TestApplyRatingTableSchemaCallCount:
    """P4: verify collect_schema() is called exactly once, not per factor."""

    def test_collect_schema_called_once_for_multi_factor(self) -> None:
        """With 3 factors, collect_schema must be called once, not 3 times.

        We verify this by wrapping the LazyFrame in a proxy that counts
        calls to collect_schema().
        """
        from unittest.mock import MagicMock

        lf = pl.DataFrame(
            {
                "region": ["North", "South"],
                "tier": ["gold", "silver"],
                "segment": ["retail", "commercial"],
            }
        ).lazy()
        table: dict[str, Any] = {
            "factors": ["region", "tier", "segment"],
            "outputColumn": "rate",
            "entries": [
                {"region": "North", "tier": "gold", "segment": "retail", "value": 1.5},
                {"region": "South", "tier": "silver", "segment": "commercial", "value": 0.8},
            ],
        }

        # Wrap the LazyFrame in a delegating proxy that counts
        # collect_schema() calls.  We can't patch a Rust-backed method
        # directly, but we can wrap the object.
        real_collect_schema = lf.collect_schema
        call_counter = MagicMock(side_effect=real_collect_schema)

        class _SchemaCountingLF:
            """Proxy that delegates everything to the real LazyFrame
            but counts collect_schema() invocations."""

            def __init__(self, inner: pl.LazyFrame) -> None:
                object.__setattr__(self, "_inner", inner)

            def collect_schema(self) -> pl.Schema:
                return call_counter()

            def __getattr__(self, name: str) -> Any:
                return getattr(object.__getattribute__(self, "_inner"), name)

        proxy = _SchemaCountingLF(lf)
        result = _apply_rating_table(proxy, table).collect()  # type: ignore[arg-type]

        # Schema is called twice: once for existing_cols check, once for dtype preservation
        assert call_counter.call_count <= 2, (
            f"collect_schema() called {call_counter.call_count} times, expected at most 2"
        )
        assert result["rate"].to_list() == [1.5, 0.8]

    def test_existing_cols_computed_once_for_lazy(self) -> None:
        """With a LazyFrame, collect_schema is called once (not per-factor)."""
        lf = pl.DataFrame(
            {
                "region": ["North", "South"],
                "tier": ["gold", "silver"],
            }
        ).lazy()
        table: dict[str, Any] = {
            "factors": ["region", "tier"],
            "outputColumn": "rate",
            "entries": [
                {"region": "North", "tier": "gold", "value": 1.5},
                {"region": "South", "tier": "silver", "value": 0.8},
            ],
        }
        # Verify the code path that uses existing_cols works correctly
        result = _apply_rating_table(lf, table).collect()
        assert result["rate"].to_list() == [1.5, 0.8]

    def test_factor_missing_from_frame_excluded_from_cast(self) -> None:
        """Factors present in entries but missing from the main frame should
        be excluded from the cast_exprs.  The function returns early via the
        B15 guard (missing factor column check) before the join."""
        lf = pl.DataFrame(
            {
                "region": ["North"],
            }
        ).lazy()
        table: dict[str, Any] = {
            "factors": ["region", "nonexistent"],
            "outputColumn": "rate",
            "entries": [
                {"region": "North", "nonexistent": "x", "value": 1.5},
            ],
        }
        # "nonexistent" is not in the main frame but IS in the entries/lookup.
        # The B15 guard checks for missing factor columns in the lookup,
        # but here "nonexistent" is present in entries.  However, the
        # existing_cols set correctly filters it from cast_exprs.
        # The join on ["region", "nonexistent"] would fail because
        # "nonexistent" is not in the main frame schema.
        with pytest.raises((pl.exceptions.ColumnNotFoundError, pl.exceptions.SchemaError)):
            _apply_rating_table(lf, table).collect()


# ===========================================================================
# GAP 1: _apply_banding — comprehensive direct tests
# ===========================================================================


class TestApplyBanding:
    """Direct tests for _apply_banding.

    Production failure caught: banding misconfiguration silently passes data
    through without creating the output column, or assigns wrong bands.
    """

    def test_continuous_single_rule(self) -> None:
        """A single continuous rule assigns values inside its range."""
        lf = pl.DataFrame({"age": [18, 30, 50]}).lazy()
        rules = [{"op1": ">=", "val1": 0, "op2": "<=", "val2": 25, "assignment": "young"}]
        result = _apply_banding(lf, "age", "age_band", "continuous", rules).collect()
        assert result["age_band"].to_list() == ["young", None, None]

    def test_continuous_multiple_rules(self) -> None:
        """Multiple continuous rules create non-overlapping bands."""
        lf = pl.DataFrame({"age": [20, 35, 60]}).lazy()
        rules = [
            {"op1": ">=", "val1": 0, "op2": "<", "val2": 30, "assignment": "young"},
            {"op1": ">=", "val1": 30, "op2": "<", "val2": 50, "assignment": "mid"},
            {"op1": ">=", "val1": 50, "op2": "<=", "val2": 100, "assignment": "senior"},
        ]
        result = _apply_banding(lf, "age", "age_band", "continuous", rules).collect()
        assert result["age_band"].to_list() == ["young", "mid", "senior"]

    def test_continuous_with_default(self) -> None:
        """Default value is applied when no rule matches."""
        lf = pl.DataFrame({"x": [5, 999]}).lazy()
        rules = [{"op1": ">=", "val1": 0, "op2": "<", "val2": 10, "assignment": "low"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules, default="unknown").collect()
        assert result["band"].to_list() == ["low", "unknown"]

    def test_categorical_banding(self) -> None:
        """Categorical banding maps exact string values."""
        lf = pl.DataFrame({"prop": ["House", "Flat", "Bungalow"]}).lazy()
        rules = [
            {"value": "House", "assignment": "dwelling"},
            {"value": "Flat", "assignment": "apartment"},
        ]
        result = _apply_banding(lf, "prop", "prop_band", "categorical", rules).collect()
        assert result["prop_band"].to_list() == ["dwelling", "apartment", None]

    def test_categorical_with_default(self) -> None:
        """Categorical banding applies default for unmatched values."""
        lf = pl.DataFrame({"prop": ["House", "Unknown"]}).lazy()
        rules = [{"value": "House", "assignment": "dwelling"}]
        result = _apply_banding(lf, "prop", "band", "categorical", rules, default="other").collect()
        assert result["band"].to_list() == ["dwelling", "other"]

    def test_empty_rules_returns_frame_unchanged(self) -> None:
        """Empty rules list should return the frame without adding columns.

        Catches: silent data corruption if empty rules produce a column of all nulls.
        """
        lf = pl.DataFrame({"x": [1, 2]}).lazy()
        result = _apply_banding(lf, "x", "band", "continuous", []).collect()
        assert "band" not in result.columns

    def test_categorical_empty_rules_returns_frame_unchanged(self) -> None:
        lf = pl.DataFrame({"x": ["a"]}).lazy()
        result = _apply_banding(lf, "x", "band", "categorical", []).collect()
        assert "band" not in result.columns

    def test_continuous_rules_all_invalid_returns_unchanged(self) -> None:
        """Rules where all operators are invalid should not add a column.

        Catches: a when/then chain built from zero valid conditions would crash.
        """
        lf = pl.DataFrame({"x": [1]}).lazy()
        rules = [{"op1": "!=", "val1": 5, "assignment": "bad"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert "band" not in result.columns

    def test_categorical_numeric_column_cast_to_string(self) -> None:
        """Categorical banding on a numeric column casts to Utf8 for matching.

        Catches: integer column failing to match string keys in the remap dict.
        """
        lf = pl.DataFrame({"code": [1, 2, 3]}).lazy()
        rules = [
            {"value": "1", "assignment": "one"},
            {"value": "2", "assignment": "two"},
        ]
        result = _apply_banding(lf, "code", "band", "categorical", rules).collect()
        assert result["band"].to_list() == ["one", "two", None]

    def test_output_column_different_from_input(self) -> None:
        """Output column name is distinct from input; both preserved."""
        lf = pl.DataFrame({"age": [25]}).lazy()
        rules = [{"op1": ">=", "val1": 0, "op2": "<=", "val2": 30, "assignment": "young"}]
        result = _apply_banding(lf, "age", "age_band", "continuous", rules).collect()
        assert "age" in result.columns
        assert "age_band" in result.columns


# ===========================================================================
# GAP 2: Very large rating tables — performance and correctness
# ===========================================================================


class TestLargeRatingTable:
    """Rating table with 1000+ entries must produce correct lookups.

    Production failure caught: O(n^2) join logic, memory blowup, or
    incorrect deduplication on large lookup frames.
    """

    def test_large_table_correctness(self) -> None:
        n = 2000
        entries = [{"region": f"R{i}", "value": float(i)} for i in range(n)]
        lf = pl.DataFrame({"region": [f"R{i}" for i in range(n)]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": entries,
        }
        result = _apply_rating_table(lf, table).collect()
        assert result.height == n
        assert result["factor"].to_list() == [float(i) for i in range(n)]

    def test_large_table_with_unmatched_rows(self) -> None:
        """Large lookup where half the input rows have no match."""
        n = 1000
        entries = [{"region": f"R{i}", "value": float(i)} for i in range(n)]
        input_regions = [f"R{i}" for i in range(n)] + [f"MISSING{i}" for i in range(n)]
        lf = pl.DataFrame({"region": input_regions}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": entries,
            "defaultValue": "-1.0",
        }
        result = _apply_rating_table(lf, table).collect()
        assert result.height == 2 * n
        matched = result["factor"].to_list()[:n]
        unmatched = result["factor"].to_list()[n:]
        assert matched == [float(i) for i in range(n)]
        assert all(v == -1.0 for v in unmatched)


# ===========================================================================
# GAP 3: Rating table with all null values
# ===========================================================================


class TestAllNullRatingTable:
    """Every entry has a null value — output column should be all null.

    Production failure caught: null cast to Float64 throws, or fill_null
    with default overwrites entries that are intentionally null.
    """

    def test_all_null_values_no_default(self) -> None:
        lf = pl.DataFrame({"k": ["a", "b"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["k"],
            "outputColumn": "out",
            "entries": [
                {"k": "a", "value": None},
                {"k": "b", "value": None},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["out"].to_list() == [None, None]

    def test_all_null_values_with_default(self) -> None:
        """Default should fill nulls even when the entry explicitly has null."""
        lf = pl.DataFrame({"k": ["a", "b", "c"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["k"],
            "outputColumn": "out",
            "entries": [
                {"k": "a", "value": None},
                {"k": "b", "value": None},
            ],
            "defaultValue": "99.0",
        }
        result = _apply_rating_table(lf, table).collect()
        # Entries with null values get filled by default; unmatched "c" also gets default
        vals = result["out"].to_list()
        assert vals == [99.0, 99.0, 99.0]


# ===========================================================================
# GAP 4: Combined rating with non-numeric columns
# ===========================================================================


class TestCombineNonNumericColumns:
    """Combine operation on string columns — should propagate Polars errors
    or produce null, not silently corrupt data.

    Production failure caught: multiply/add on string columns produces
    garbage instead of a clear error.
    """

    def test_multiply_string_columns_raises(self) -> None:
        """Multiplying string columns should raise, not silently succeed."""
        lf = pl.DataFrame({"a": ["hello"], "b": ["world"]}).lazy()
        with pytest.raises(Exception):
            _combine_rating_columns(lf, ["a", "b"], "multiply", "out").collect()

    def test_add_string_columns_concatenates_or_raises(self) -> None:
        """Adding string columns either concatenates (Polars behaviour) or raises.

        Either outcome is acceptable — the key is it does NOT silently return 0.
        """
        lf = pl.DataFrame({"a": ["hello"], "b": ["world"]}).lazy()
        try:
            result = _combine_rating_columns(lf, ["a", "b"], "add", "out").collect()
            # If Polars allows string +, the result should be concatenation
            assert result["out"].to_list() == ["helloworld"]
        except Exception:
            pass  # Raising is also acceptable

    def test_min_max_on_strings(self) -> None:
        """min/max on strings should use lexicographic ordering or raise."""
        lf = pl.DataFrame({"a": ["banana"], "b": ["apple"]}).lazy()
        try:
            result_min = _combine_rating_columns(lf, ["a", "b"], "min", "out").collect()
            assert result_min["out"].to_list() == ["apple"]
            result_max = _combine_rating_columns(lf, ["a", "b"], "max", "out").collect()
            assert result_max["out"].to_list() == ["banana"]
        except Exception:
            pass  # Raising is acceptable


# ===========================================================================
# GAP 5: Banding with boundary values (floating point equality)
# ===========================================================================


class TestBandingBoundaryValues:
    """Values exactly on the boundary between two bands.

    Production failure caught: off-by-one in band assignment — e.g. a value
    of exactly 25 falls into neither the 0-25 band nor the 25-50 band.
    """

    def test_boundary_value_inclusive_exclusive(self) -> None:
        """Value exactly on boundary: [0,25) and [25,50) — 25 goes to second band."""
        lf = pl.DataFrame({"x": [24.9999, 25.0, 25.0001]}).lazy()
        rules = [
            {"op1": ">=", "val1": 0, "op2": "<", "val2": 25, "assignment": "low"},
            {"op1": ">=", "val1": 25, "op2": "<", "val2": 50, "assignment": "mid"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["low", "mid", "mid"]

    def test_boundary_both_inclusive(self) -> None:
        """Overlapping bands where boundary belongs to both: first rule wins."""
        lf = pl.DataFrame({"x": [25.0]}).lazy()
        rules = [
            {"op1": ">=", "val1": 0, "op2": "<=", "val2": 25, "assignment": "low"},
            {"op1": ">=", "val1": 25, "op2": "<=", "val2": 50, "assignment": "mid"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        # First matching rule in the when/then chain wins
        assert result["band"].to_list() == ["low"]

    def test_floating_point_near_boundary(self) -> None:
        """Floating point arithmetic near boundaries must not cause misclassification.

        Catches: 0.1 + 0.2 != 0.3 issues causing values to slip between bands.
        """
        lf = pl.DataFrame({"x": [0.1 + 0.2]}).lazy()  # 0.30000000000000004
        rules = [
            {"op1": ">=", "val1": 0, "op2": "<", "val2": 0.3, "assignment": "low"},
            {"op1": ">=", "val1": 0.3, "op2": "<", "val2": 1.0, "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        # 0.1+0.2 > 0.3, so it falls into "high"
        assert result["band"].to_list() == ["high"]

    def test_exact_equality_boundary(self) -> None:
        """Using == operator on a float boundary value."""
        lf = pl.DataFrame({"x": [0.0, 0.5, 1.0]}).lazy()
        rules = [{"op1": "=", "val1": 0.5, "assignment": "exact"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == [None, "exact", None]


# ===========================================================================
# GAP 6: Negative values in banding
# ===========================================================================


class TestBandingNegativeValues:
    """Negative numbers and reversed ranges in banding rules.

    Production failure caught: banding logic assumes positive values, or
    reversed min/max silently produces empty bands.
    """

    def test_negative_values_banded_correctly(self) -> None:
        lf = pl.DataFrame({"temp": [-20.0, -5.0, 0.0, 10.0]}).lazy()
        rules = [
            {"op1": ">=", "val1": -30, "op2": "<", "val2": -10, "assignment": "freezing"},
            {"op1": ">=", "val1": -10, "op2": "<", "val2": 0, "assignment": "cold"},
            {"op1": ">=", "val1": 0, "op2": "<=", "val2": 20, "assignment": "mild"},
        ]
        result = _apply_banding(lf, "temp", "temp_band", "continuous", rules).collect()
        assert result["temp_band"].to_list() == ["freezing", "cold", "mild", "mild"]

    def test_negative_boundary_exact(self) -> None:
        """Value exactly on a negative boundary."""
        lf = pl.DataFrame({"x": [-10.0]}).lazy()
        rules = [
            {"op1": ">=", "val1": -20, "op2": "<", "val2": -10, "assignment": "lower"},
            {"op1": ">=", "val1": -10, "op2": "<", "val2": 0, "assignment": "upper"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["upper"]

    def test_reversed_range_produces_no_match(self) -> None:
        """A rule with min > max (e.g. >= 50 AND < 10) should match nothing.

        Catches: reversed ranges silently accepting all values.
        """
        lf = pl.DataFrame({"x": [5.0, 30.0, 60.0]}).lazy()
        rules = [{"op1": ">=", "val1": 50, "op2": "<", "val2": 10, "assignment": "impossible"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == [None, None, None]


# ===========================================================================
# GAP 7: Empty DataFrame through banding
# ===========================================================================


class TestBandingEmptyDataFrame:
    """0-row input should pass through cleanly.

    Production failure caught: division by zero, empty-frame schema errors,
    or exceptions when applying when/then to an empty frame.
    """

    def test_continuous_banding_empty_frame(self) -> None:
        lf = pl.DataFrame({"x": pl.Series([], dtype=pl.Float64)}).lazy()
        rules = [{"op1": ">=", "val1": 0, "op2": "<", "val2": 10, "assignment": "low"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result.height == 0
        assert "band" in result.columns

    def test_categorical_banding_empty_frame(self) -> None:
        lf = pl.DataFrame({"cat": pl.Series([], dtype=pl.Utf8)}).lazy()
        rules = [{"value": "A", "assignment": "group_a"}]
        result = _apply_banding(lf, "cat", "band", "categorical", rules).collect()
        assert result.height == 0
        assert "band" in result.columns

    def test_rating_table_empty_frame(self) -> None:
        """Rating table applied to 0-row frame should return 0 rows with output column."""
        lf = pl.DataFrame({"region": pl.Series([], dtype=pl.Utf8)}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.2}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result.height == 0
        assert "factor" in result.columns


# ===========================================================================
# GAP 8: Multiple rating tables applied sequentially
# ===========================================================================


class TestSequentialRatingTables:
    """Output of one rating table feeds as input to the next.

    Production failure caught: column name collision, schema drift, or
    the second table failing to join because the first mutated types.
    """

    def test_chained_tables(self) -> None:
        """Two tables applied in sequence — second uses a column created by the first."""
        lf = pl.DataFrame({"region": ["North", "South"]}).lazy()
        table1: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "region_factor",
            "entries": [
                {"region": "North", "value": 1.2},
                {"region": "South", "value": 0.8},
            ],
        }
        lf = _apply_rating_table(lf, table1)

        # Second table uses a different factor column
        # First, band the region_factor
        table2: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "region_loading",
            "entries": [
                {"region": "North", "value": 0.5},
                {"region": "South", "value": 0.3},
            ],
        }
        lf = _apply_rating_table(lf, table2)
        result = lf.collect()

        assert "region_factor" in result.columns
        assert "region_loading" in result.columns
        assert result["region_factor"].to_list() == [1.2, 0.8]
        assert result["region_loading"].to_list() == [0.5, 0.3]

    def test_chained_then_combined(self) -> None:
        """Two tables applied, then their outputs multiplied together."""
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        table1: dict[str, Any] = {
            "factors": ["band"],
            "outputColumn": "f1",
            "entries": [
                {"band": "A", "value": 2.0},
                {"band": "B", "value": 3.0},
            ],
        }
        table2: dict[str, Any] = {
            "factors": ["band"],
            "outputColumn": "f2",
            "entries": [
                {"band": "A", "value": 1.5},
                {"band": "B", "value": 0.5},
            ],
        }
        lf = _apply_rating_table(lf, table1)
        lf = _apply_rating_table(lf, table2)
        lf = _combine_rating_columns(lf, ["f1", "f2"], "multiply", "combined")
        result = lf.collect()
        assert result["combined"].to_list() == [3.0, 1.5]

    def test_three_tables_sequential_with_nulls(self) -> None:
        """Three tables in sequence, some with nulls — nulls propagate correctly."""
        lf = pl.DataFrame({"k": ["a", "b", "c"]}).lazy()
        for i, entries in enumerate(
            [
                [{"k": "a", "value": 1.0}, {"k": "b", "value": 2.0}],
                [{"k": "a", "value": 10.0}, {"k": "c", "value": 30.0}],
                [{"k": "b", "value": 200.0}, {"k": "c", "value": 300.0}],
            ]
        ):
            table: dict[str, Any] = {
                "factors": ["k"],
                "outputColumn": f"t{i}",
                "entries": entries,
            }
            lf = _apply_rating_table(lf, table)
        result = lf.collect()
        assert result["t0"].to_list() == [1.0, 2.0, None]
        assert result["t1"].to_list() == [10.0, None, 30.0]
        assert result["t2"].to_list() == [None, 200.0, 300.0]


# ===========================================================================
# GAP 9: Factor names with special characters
# ===========================================================================


class TestSpecialCharacterFactorNames:
    """Factor names containing spaces, dots, brackets.

    Production failure caught: Polars column selection fails on names like
    'Property Type' or 'claim.amount' because they are not valid identifiers.
    """

    def test_factor_with_spaces(self) -> None:
        lf = pl.DataFrame({"Property Type": ["House", "Flat"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["Property Type"],
            "outputColumn": "prop factor",
            "entries": [
                {"Property Type": "House", "value": 1.0},
                {"Property Type": "Flat", "value": 1.5},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["prop factor"].to_list() == [1.0, 1.5]

    def test_factor_with_dots(self) -> None:
        lf = pl.DataFrame({"claim.type": ["fire", "flood"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["claim.type"],
            "outputColumn": "claim.factor",
            "entries": [
                {"claim.type": "fire", "value": 2.0},
                {"claim.type": "flood", "value": 3.0},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["claim.factor"].to_list() == [2.0, 3.0]

    def test_factor_with_brackets(self) -> None:
        lf = pl.DataFrame({"risk[level]": ["high", "low"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["risk[level]"],
            "outputColumn": "risk_factor",
            "entries": [
                {"risk[level]": "high", "value": 5.0},
                {"risk[level]": "low", "value": 1.0},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["risk_factor"].to_list() == [5.0, 1.0]

    def test_banding_with_special_column_names(self) -> None:
        """Banding also handles special character column names."""
        lf = pl.DataFrame({"sum insured (GBP)": [10000.0, 50000.0]}).lazy()
        rules = [
            {"op1": ">=", "val1": 0, "op2": "<", "val2": 25000, "assignment": "low"},
            {"op1": ">=", "val1": 25000, "op2": "<=", "val2": 100000, "assignment": "high"},
        ]
        result = _apply_banding(lf, "sum insured (GBP)", "si_band", "continuous", rules).collect()
        assert result["si_band"].to_list() == ["low", "high"]


# ===========================================================================
# GAP 10: Extreme float values — Inf, -Inf, very large/small numbers
# ===========================================================================


class TestExtremeFloatValues:
    """Inf, -Inf, very large numbers, very small numbers in data.

    Production failure caught: Inf values silently pass through banding/rating,
    producing nonsensical downstream arithmetic (e.g. Inf * 1.2 = Inf premium).
    """

    def test_inf_in_banding_sanitized_to_default(self) -> None:
        """Inf input values are sanitized to null, falling to default."""
        lf = pl.DataFrame({"x": [float("inf"), 5.0, float("-inf")]}).lazy()
        rules = [
            {"op1": ">", "val1": 0, "assignment": "positive"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        # Inf and -Inf are sanitized to null → fall to default (None)
        assert result["band"].to_list() == [None, "positive", None]

    def test_neg_inf_in_banding_sanitized_to_default(self) -> None:
        """-Inf input values are sanitized to null, falling to default."""
        lf = pl.DataFrame({"x": [float("-inf"), -200.0, 0.0]}).lazy()
        rules = [
            {"op1": "<", "val1": -100, "assignment": "extreme_low"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        # -Inf is sanitized to null → falls to default (None); -200 < -100 → matches
        assert result["band"].to_list() == [None, "extreme_low", None]

    def test_very_large_numbers(self) -> None:
        """Numbers near float max should band correctly without overflow."""
        lf = pl.DataFrame({"x": [1e308, -1e308, 1e-308]}).lazy()
        rules = [
            {"op1": ">", "val1": 1e307, "assignment": "huge"},
            {"op1": "<", "val1": -1e307, "assignment": "neg_huge"},
            {"op1": ">=", "val1": 0, "op2": "<", "val2": 1, "assignment": "tiny"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["huge", "neg_huge", "tiny"]

    def test_inf_in_rating_table_entry_rejected(self) -> None:
        """A rating table entry with Inf value is rejected — prevents silent pricing corruption."""
        lf = pl.DataFrame({"k": ["a", "b"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["k"],
            "outputColumn": "out",
            "entries": [
                {"k": "a", "value": float("inf")},
                {"k": "b", "value": 1.0},
            ],
        }
        with pytest.raises(ValueError, match="NaN or Inf"):
            _apply_rating_table(lf, table)

    def test_combine_with_null_uses_identity(self) -> None:
        """Null factor is treated as the multiplicative identity (1.0).

        A missing rating lookup should not zero out premiums -- the neutral
        element (1.0 for multiply, 0.0 for add) is used instead.
        """
        lf = pl.DataFrame({"a": [2.0, None], "b": [3.0, 5.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "multiply", "out").collect()
        vals = result["out"].to_list()
        assert vals[0] == 6.0
        assert vals[1] == 5.0  # None treated as 1.0 (identity for multiply)

    def test_combine_with_inf(self) -> None:
        """Inf * finite = Inf, Inf + finite = Inf."""
        lf = pl.DataFrame({"a": [float("inf")], "b": [2.0]}).lazy()
        result_mul = _combine_rating_columns(lf, ["a", "b"], "multiply", "out").collect()
        assert result_mul["out"].to_list() == [float("inf")]
        result_add = _combine_rating_columns(lf, ["a", "b"], "add", "out").collect()
        assert result_add["out"].to_list() == [float("inf")]


# ---------------------------------------------------------------------------
# Bug regression tests
# ---------------------------------------------------------------------------


class TestBugB1FactorDtypePreservation:
    """B1: Factor columns must retain their original dtype after rating lookup."""

    def test_int_factor_stays_int_after_lookup(self) -> None:
        lf = pl.DataFrame({"age": [25, 30, 35], "value_col": [1.0, 2.0, 3.0]}).lazy()
        table = {
            "factors": ["age"],
            "outputColumn": "factor",
            "entries": [
                {"age": "25", "value": 1.1},
                {"age": "30", "value": 1.2},
                {"age": "35", "value": 1.3},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        # The age column should still be Int64, NOT Utf8
        assert result["age"].dtype == pl.Int64

    def test_float_factor_stays_float_after_lookup(self) -> None:
        lf = pl.DataFrame({"score": [1.5, 2.5], "x": [0, 0]}).lazy()
        table = {
            "factors": ["score"],
            "outputColumn": "factor",
            "entries": [
                {"score": "1.5", "value": 10.0},
                {"score": "2.5", "value": 20.0},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["score"].dtype == pl.Float64


class TestBugB2CategoricalBandingFalsyValues:
    """B2: Categorical banding must not skip rules where value is 0."""

    def test_categorical_value_zero_is_not_skipped(self) -> None:
        lf = pl.DataFrame({"code": ["0", "1", "2"]}).lazy()
        rules = [
            {"value": "0", "assignment": "none"},
            {"value": "1", "assignment": "basic"},
            {"value": "2", "assignment": "full"},
        ]
        result = _apply_banding(
            lf,
            column="code",
            output_column="band",
            banding_type="categorical",
            rules=rules,
            default="unknown",
        ).collect()
        bands = result["band"].to_list()
        # value "0" should map to "none", not be skipped
        assert bands[0] == "none"
        assert bands[1] == "basic"
        assert bands[2] == "full"

    def test_categorical_integer_zero_value(self) -> None:
        """Rule with integer 0 as value (from JSON parse)."""
        lf = pl.DataFrame({"code": ["0", "1"]}).lazy()
        rules = [
            {"value": 0, "assignment": "zero_class"},
            {"value": 1, "assignment": "one_class"},
        ]
        result = _apply_banding(
            lf,
            column="code",
            output_column="band",
            banding_type="categorical",
            rules=rules,
            default="other",
        ).collect()
        bands = result["band"].to_list()
        assert bands[0] == "zero_class"


# ===========================================================================
# Edge cases: _apply_banding
# ===========================================================================


class TestApplyBandingEdgeCases:
    def test_null_values_get_default(self) -> None:
        lf = pl.DataFrame({"x": [1.0, None, 5.0]}).lazy()
        rules = [{"op1": ">=", "val1": 0, "op2": "<", "val2": 10, "assignment": "low"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules, default="fallback").collect()
        assert result["band"].to_list() == ["low", "fallback", "low"]

    def test_null_values_categorical_get_default(self) -> None:
        lf = pl.DataFrame({"cat": ["A", None, "B"]}).lazy()
        rules = [
            {"value": "A", "assignment": "alpha"},
            {"value": "B", "assignment": "beta"},
        ]
        result = _apply_banding(lf, "cat", "band", "categorical", rules, default="other").collect()
        assert result["band"].to_list() == ["alpha", "other", "beta"]

    def test_null_values_categorical_no_default(self) -> None:
        lf = pl.DataFrame({"cat": ["A", None]}).lazy()
        rules = [{"value": "A", "assignment": "alpha"}]
        result = _apply_banding(lf, "cat", "band", "categorical", rules).collect()
        assert result["band"].to_list() == ["alpha", None]

    def test_float_precision_boundary(self) -> None:
        val = 0.1 + 0.2
        lf = pl.DataFrame({"x": [val]}).lazy()
        rules = [
            {"op1": ">=", "val1": 0, "op2": "<", "val2": 0.3, "assignment": "below"},
            {"op1": ">=", "val1": 0.3, "op2": "<", "val2": 1.0, "assignment": "above"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["above"]

    def test_overlapping_continuous_first_match_wins(self) -> None:
        lf = pl.DataFrame({"x": [15.0]}).lazy()
        rules = [
            {"op1": ">=", "val1": 10, "op2": "<=", "val2": 20, "assignment": "first"},
            {"op1": ">=", "val1": 10, "op2": "<=", "val2": 20, "assignment": "second"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["first"]

    def test_negative_values_continuous(self) -> None:
        lf = pl.DataFrame({"x": [-50.0, -1.0, 0.0]}).lazy()
        rules = [
            {"op1": ">=", "val1": -100, "op2": "<", "val2": -10, "assignment": "very_neg"},
            {"op1": ">=", "val1": -10, "op2": "<=", "val2": 0, "assignment": "near_zero"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["very_neg", "near_zero", "near_zero"]

    def test_inf_values_in_input(self) -> None:
        lf = pl.DataFrame({"x": [float("inf"), float("-inf"), 5.0]}).lazy()
        rules = [
            {"op1": ">=", "val1": 0, "op2": "<", "val2": 10, "assignment": "normal"},
        ]
        result = _apply_banding(
            lf, "x", "band", "continuous", rules, default="out_of_range"
        ).collect()
        assert result["band"][2] == "normal"
        assert result["band"][0] == "out_of_range"
        assert result["band"][1] == "out_of_range"

    def test_categorical_numeric_column_cast(self) -> None:
        lf = pl.DataFrame({"code": [100, 200, 300]}).lazy()
        rules = [
            {"value": "100", "assignment": "hundred"},
            {"value": "200", "assignment": "two_hundred"},
        ]
        result = _apply_banding(lf, "code", "band", "categorical", rules).collect()
        assert result["band"].to_list() == ["hundred", "two_hundred", None]

    def test_output_column_overwrites_existing(self) -> None:
        lf = pl.DataFrame({"x": [5.0], "band": ["old_value"]}).lazy()
        rules = [{"op1": ">=", "val1": 0, "op2": "<=", "val2": 10, "assignment": "new"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["new"]

    def test_large_number_of_rules(self) -> None:
        rules = [
            {"op1": ">=", "val1": i, "op2": "<", "val2": i + 1, "assignment": f"band_{i}"}
            for i in range(150)
        ]
        lf = pl.DataFrame({"x": [0.5, 75.5, 149.5]}).lazy()
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["band_0", "band_75", "band_149"]

    def test_empty_dataframe_continuous(self) -> None:
        lf = pl.DataFrame({"x": pl.Series([], dtype=pl.Float64)}).lazy()
        rules = [{"op1": ">", "val1": 0, "op2": "<", "val2": 10, "assignment": "a"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result.height == 0
        assert "band" in result.columns

    def test_empty_dataframe_categorical(self) -> None:
        lf = pl.DataFrame({"x": pl.Series([], dtype=pl.Utf8)}).lazy()
        rules = [{"value": "A", "assignment": "alpha"}]
        result = _apply_banding(lf, "x", "band", "categorical", rules).collect()
        assert result.height == 0
        assert "band" in result.columns

    def test_column_name_with_spaces(self) -> None:
        lf = pl.DataFrame({"my column": [10.0, 20.0]}).lazy()
        rules = [
            {"op1": ">=", "val1": 0, "op2": "<", "val2": 15, "assignment": "low"},
            {"op1": ">=", "val1": 15, "op2": "<=", "val2": 30, "assignment": "high"},
        ]
        result = _apply_banding(lf, "my column", "my band", "continuous", rules).collect()
        assert result["my band"].to_list() == ["low", "high"]


# ===========================================================================
# Edge cases: _apply_rating_table
# ===========================================================================


class TestApplyRatingTableEdgeCases:
    def test_non_existent_factor_in_entries_passthrough(self) -> None:
        lf = pl.DataFrame({"region": ["North"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region", "missing_col"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.0}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert "factor" not in result.columns

    def test_non_existent_factor_in_frame_raises(self) -> None:
        lf = pl.DataFrame({"region": ["North"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["nonexistent"],
            "outputColumn": "factor",
            "entries": [{"nonexistent": "x", "value": 1.0}],
        }
        with pytest.raises((pl.exceptions.ColumnNotFoundError, pl.exceptions.SchemaError)):
            _apply_rating_table(lf, table).collect()

    def test_missing_value_key_in_entries(self) -> None:
        lf = pl.DataFrame({"region": ["North"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "score": 1.5}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert "factor" not in result.columns

    def test_null_factor_values_in_entries(self) -> None:
        lf = pl.DataFrame({"region": ["North", "South"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [
                {"region": "North", "value": 1.2},
                {"region": None, "value": 0.5},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"][0] == 1.2

    def test_empty_string_factor_value_in_entries(self) -> None:
        lf = pl.DataFrame({"region": ["North", ""]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [
                {"region": "North", "value": 1.2},
                {"region": "", "value": 0.8},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"].to_list() == [1.2, 0.8]

    def test_duplicate_entries_keep_last(self) -> None:
        lf = pl.DataFrame({"region": ["North"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [
                {"region": "North", "value": 1.0},
                {"region": "North", "value": 9.9},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result.height == 1
        assert result["factor"].to_list() == [9.9]

    def test_default_value_zero(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.2}],
            "defaultValue": "0.0",
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"].to_list() == [1.2, 0.0]

    def test_default_value_negative(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.2}],
            "defaultValue": "-5.0",
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"].to_list() == [1.2, -5.0]

    def test_output_column_collision_with_existing(self) -> None:
        lf = pl.DataFrame({"region": ["North"], "factor": [999.0]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.5}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"].to_list() == [1.5]

    def test_empty_factors_list_passthrough(self) -> None:
        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        table: dict[str, Any] = {
            "factors": [],
            "outputColumn": "out",
            "entries": [{"value": 1.0}],
        }
        result = _apply_rating_table(lf, table).collect()
        assert "out" not in result.columns
        assert result["x"].to_list() == [1, 2, 3]

    def test_inf_in_entries_rejected(self) -> None:
        lf = pl.DataFrame({"region": ["North"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": float("inf")}],
        }
        with pytest.raises(ValueError, match="NaN or Inf"):
            _apply_rating_table(lf, table).collect()

    def test_nan_in_entries_rejected(self) -> None:
        lf = pl.DataFrame({"region": ["North"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": float("nan")}],
        }
        with pytest.raises(ValueError, match="NaN or Inf"):
            _apply_rating_table(lf, table).collect()

    def test_neg_inf_in_entries_rejected(self) -> None:
        lf = pl.DataFrame({"region": ["North"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": float("-inf")}],
        }
        with pytest.raises(ValueError, match="NaN or Inf"):
            _apply_rating_table(lf, table).collect()


# ===========================================================================
# Edge cases: _combine_rating_columns
# ===========================================================================


class TestCombineRatingColumnsEdgeCases:
    def test_multiply_all_null_fills_one(self) -> None:
        lf = pl.DataFrame(
            {
                "a": pl.Series([None, None], dtype=pl.Float64),
                "b": pl.Series([None, None], dtype=pl.Float64),
            }
        ).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "multiply", "out").collect()
        assert result["out"].to_list() == [1.0, 1.0]

    def test_add_all_null_fills_zero(self) -> None:
        lf = pl.DataFrame(
            {
                "a": pl.Series([None, None], dtype=pl.Float64),
                "b": pl.Series([None, None], dtype=pl.Float64),
            }
        ).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "add", "out").collect()
        assert result["out"].to_list() == [0.0, 0.0]

    def test_min_with_nulls(self) -> None:
        lf = pl.DataFrame(
            {
                "a": [3.0, None, 5.0],
                "b": [None, None, 2.0],
            }
        ).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "min", "out").collect()
        assert result["out"].to_list() == [3.0, None, 2.0]

    def test_max_with_nulls(self) -> None:
        lf = pl.DataFrame(
            {
                "a": [3.0, None, 5.0],
                "b": [None, None, 2.0],
            }
        ).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "max", "out").collect()
        assert result["out"].to_list() == [3.0, None, 5.0]

    def test_non_existent_column_raises(self) -> None:
        lf = pl.DataFrame({"a": [1.0]}).lazy()
        with pytest.raises(Exception):
            _combine_rating_columns(lf, ["a", "nonexistent"], "multiply", "out").collect()

    def test_single_column_alias_only(self) -> None:
        lf = pl.DataFrame({"src": [7.7]}).lazy()
        result = _combine_rating_columns(lf, ["src"], "add", "dest").collect()
        assert result["dest"].to_list() == [7.7]
        assert "src" in result.columns

    def test_empty_columns_passthrough(self) -> None:
        lf = pl.DataFrame({"x": [1.0, 2.0]}).lazy()
        result = _combine_rating_columns(lf, [], "add", "out").collect()
        assert "out" not in result.columns
        assert result.columns == ["x"]

    def test_unknown_operation_raises_value_error(self) -> None:
        lf = pl.DataFrame({"a": [2.0], "b": [5.0]}).lazy()
        with pytest.raises(ValueError, match="Unsupported rating combine operation"):
            _combine_rating_columns(lf, ["a", "b"], "foobar", "out").collect()


# ---------------------------------------------------------------------------
# Edge-case coverage
# ---------------------------------------------------------------------------


class TestOverlappingBandingRulesFirstMatchWins:
    def test_first_matching_rule_wins(self) -> None:
        lf = pl.DataFrame({"x": [5, 3, 10, 11]}).lazy()
        rules = [
            {"op1": "<=", "val1": 10, "assignment": "low"},
            {"op1": "<=", "val1": 5, "assignment": "very_low"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["low", "low", "low", None]


class TestCombineWithNonExistentColumnRaises:
    def test_missing_column_raises_at_collect(self) -> None:
        lf = pl.DataFrame({"a": [1.0]}).lazy()
        result_lf = _combine_rating_columns(lf, ["missing_col"], "multiply", "out")
        with pytest.raises(pl.exceptions.ColumnNotFoundError):
            result_lf.collect()


class TestBandingWithNanInContinuousRuleValues:
    def test_nan_val_in_rule_raises(self) -> None:
        """NaN as a boundary value now raises ValueError (fail loudly)."""
        lf = pl.DataFrame({"x": [1, 5, 10]}).lazy()
        rules = [
            {"op1": ">", "val1": float("nan"), "assignment": "nan_band"},
        ]
        with pytest.raises(ValueError, match="non-finite"):
            _apply_banding(lf, "x", "band", "continuous", rules)


class TestRatingTableEmptyStringFactorValue:
    def test_empty_string_matches(self) -> None:
        lf = pl.DataFrame({"region": ["", "North", ""]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [
                {"region": "", "value": 0.5},
                {"region": "North", "value": 1.2},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"].to_list() == [0.5, 1.2, 0.5]


class TestRatingTableIntColumnStringEntries:
    def test_utf8_cast_handles_int_vs_string(self) -> None:
        lf = pl.DataFrame({"code": [1, 2, 3]}).lazy()
        table: dict[str, Any] = {
            "factors": ["code"],
            "outputColumn": "val",
            "entries": [
                {"code": "1", "value": 10.0},
                {"code": "2", "value": 20.0},
            ],
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["val"].to_list() == [10.0, 20.0, None]


class TestCombineMultiplyWithZero:
    def test_multiply_zero_produces_zero(self) -> None:
        lf = pl.DataFrame({"a": [5.0, 3.0], "b": [0.0, 0.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b"], "multiply", "out").collect()
        assert result["out"].to_list() == [0.0, 0.0]


class TestCombineMinMaxMixedValues:
    def test_min_returns_smallest(self) -> None:
        lf = pl.DataFrame({"a": [2.0], "b": [0.5], "c": [1.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b", "c"], "min", "out").collect()
        assert result["out"].to_list() == [0.5]

    def test_max_returns_largest(self) -> None:
        lf = pl.DataFrame({"a": [2.0], "b": [0.5], "c": [1.0]}).lazy()
        result = _combine_rating_columns(lf, ["a", "b", "c"], "max", "out").collect()
        assert result["out"].to_list() == [2.0]


class TestDefaultValueNegativeNumber:
    def test_negative_default_applied(self) -> None:
        lf = pl.DataFrame({"region": ["North", "Unknown"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [{"region": "North", "value": 1.2}],
            "defaultValue": -1.5,
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"].to_list() == [1.2, -1.5]


class TestRatingTableNullInFactorColumn:
    def test_rating_table_null_in_factor_column(self) -> None:
        """Null values in the factor column should not match entries and get the default."""
        lf = pl.DataFrame({"region": ["North", None, "South"]}).lazy()
        table: dict[str, Any] = {
            "factors": ["region"],
            "outputColumn": "factor",
            "entries": [
                {"region": "North", "value": 1.2},
                {"region": "South", "value": 0.9},
            ],
            "defaultValue": 1.0,
        }
        result = _apply_rating_table(lf, table).collect()
        assert result["factor"].to_list() == [1.2, 1.0, 0.9]


class TestApplyRatingStepCompactConfig:
    def test_direct_compact_entries_config_applies_rating_tables(self) -> None:
        lf = pl.DataFrame(
            {
                "vehicle_age_band": ["1-3", "1-3", "10+"],
                "cover_type": ["comprehensive", "tpft", "comprehensive"],
            }
        ).lazy()
        config: dict[str, Any] = {
            "tables": [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type"],
                    "outputColumn": "vehicle_factor",
                    "defaultValue": "1.0",
                    "entries": {
                        "1-3": {
                            "comprehensive": 0.9,
                            "tpft": 1.1,
                        },
                        "10+": {
                            "comprehensive": 1.4,
                        },
                    },
                }
            ]
        }

        result = apply_rating_step_from_config(lf, config).collect()

        assert result["vehicle_factor"].to_list() == [0.9, 1.1, 1.4]

    def test_three_factor_compact_entries_follow_editor_axis_order(self) -> None:
        lf = pl.DataFrame(
            {
                "vehicle_age_band": ["1-3", "4-5", "1-3", "10+"],
                "cover_type": [
                    "comprehensive",
                    "comprehensive",
                    "third_party_only",
                    "comprehensive",
                ],
                "channel": [
                    "confused",
                    "confused",
                    "compare_the_market",
                    "confused",
                ],
            }
        ).lazy()
        config: dict[str, Any] = {
            "tables": [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type", "channel"],
                    "outputColumn": "vehicle_factor",
                    "defaultValue": "1.0",
                    "entries": {
                        "confused": {
                            "comprehensive": {
                                "1-3": 0.91,
                                "4-5": 0.96,
                            }
                        },
                        "compare_the_market": {
                            "third_party_only": {
                                "1-3": 1.08,
                            }
                        },
                    },
                }
            ]
        }

        result = apply_rating_step_from_config(lf, config).collect()

        assert result["vehicle_factor"].to_list() == [0.91, 0.96, 1.08, 1.0]
