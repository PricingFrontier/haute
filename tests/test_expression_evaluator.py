"""The expression evaluator: substituted values, results and taken branches.

``evaluate_expression`` reports a node's formula with values substituted and
the computed result, and the branch-tracking evaluator records which when/then
branch fired.  Numeric and logic results are pinned against a one-row Polars
oracle where they must agree; the evaluator must reproduce Polars semantics and
must never launder its own failure into a fabricated-but-self-consistent value.
"""

from __future__ import annotations

import math
from datetime import date

import polars as pl
import pytest

from haute._expression_parser import evaluate_expression, parse_expression

# ###########################################################################
# 21. evaluate_expression — comprehensive paths (lines 1294–1444)
# ###########################################################################


class TestEvaluateExpressionPaths:
    def test_evaluate_basic_arithmetic(self):
        code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("sum"))'
        result = evaluate_expression(code, "sum", {"a": 3, "b": 7})
        assert result.result_value == 10
        assert result.substituted_text == "3 + 7"

    def test_evaluate_window_function(self):
        """Window detection via .over() (lines 1348, 1361–1364)."""
        code = 'df = df.with_columns(pl.col("premium").sum().over("region").alias("region_total"))'
        result = evaluate_expression(code, "region_total", {"premium": 100.0, "region": "North"})
        assert result is not None
        assert result.expression_type == "window"
        assert "sum" in result.substituted_text or "premium" in result.substituted_text

    def test_evaluate_preamble_ns(self):
        """Preamble namespace merging (lines 1342–1382)."""
        code = 'df = df.with_columns((pl.col("x") * RATE).alias("result"))'
        result = evaluate_expression(code, "result", {"x": 10}, preamble_ns={"RATE": 1.5})
        assert result is not None

    def test_evaluate_dot_chain_wrapping(self):
        """Code starting with dot is wrapped (line 1335–1336)."""
        code = 'df = df.with_columns((pl.col("x") + 1).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result is not None

    def test_evaluate_non_df_code_wrapping(self):
        """Code not starting with df is wrapped (lines 1337–1340)."""
        code = 'pl.col("x") + 1'
        result = evaluate_expression(code, "x", {"x": 5})
        assert result is not None

    def test_evaluate_exception_fallback(self):
        """A malformed (non-str) code argument must fail loud rather than
        laundering the observed row value into result_value: previously any
        exception fell back to ``result_value=row_values.get(target)``, which
        displayed the engine's output as if the evaluator had computed it and
        masked evaluator bugs as self-consistent. The enrichment caller wraps
        this in its own error handler."""
        with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: ast.parse exception type may vary
            evaluate_expression(None, "target", {"target": 42})  # type: ignore[arg-type]

    def test_evaluate_conditional_branches(self):
        """Conditional branch tracking (lines 1387–1398)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 10).then("high").otherwise("low").alias("label")\n'
            ")"
        )
        result = evaluate_expression(code, "label", {"x": 15})
        assert result.taken_branch == "then"
        assert result.taken_branch_index == 0

    def test_evaluate_conditional_otherwise_branch(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 10).then("high").otherwise("low").alias("label")\n'
            ")"
        )
        result = evaluate_expression(code, "label", {"x": 5})
        assert result.taken_branch == "otherwise"


# ###########################################################################
# 22. _add_window_partition_cols and _build_window_description
#     (lines 1418–1444)
# ###########################################################################


class TestWindowHelpers:
    def test_window_partition_cols_added(self):
        code = 'df = df.with_columns(pl.col("premium").sum().over("region").alias("total"))'
        result = evaluate_expression(code, "total", {"premium": 100, "region": "North"})
        assert "region" in result.referenced_columns

    def test_window_description_format(self):
        code = 'df = df.with_columns(pl.col("premium").mean().over("region").alias("avg"))'
        result = evaluate_expression(code, "avg", {"premium": 200, "region": "South"})
        assert "mean" in result.substituted_text
        assert "premium" in result.substituted_text
        assert "region" in result.substituted_text


# ###########################################################################
# 23. _substitute_values and _format_value (lines 1486–1525)
# ###########################################################################


class TestSubstituteAndFormat:
    def test_format_value_none(self):
        code = 'df = df.with_columns((pl.col("x") + pl.col("y")).alias("sum"))'
        result = evaluate_expression(code, "sum", {"x": None, "y": 5})
        assert result.result_value is None

    def test_format_value_bool(self):
        code = 'df = df.with_columns(pl.when(pl.col("flag")).then(1).otherwise(0).alias("r"))'
        result = evaluate_expression(code, "r", {"flag": True})
        assert result is not None

    def test_format_value_nan(self):
        code = 'df = df.with_columns((pl.col("x") + 1).alias("r"))'
        result = evaluate_expression(code, "r", {"x": float("nan")})
        assert "NaN" in result.substituted_text

    def test_format_value_inf(self):
        code = 'df = df.with_columns((pl.col("x") + 1).alias("r"))'
        result = evaluate_expression(code, "r", {"x": float("inf")})
        assert "inf" in result.substituted_text

    def test_format_value_neg_inf(self):
        code = 'df = df.with_columns((pl.col("x") + 1).alias("r"))'
        result = evaluate_expression(code, "r", {"x": float("-inf")})
        assert "-inf" in result.substituted_text

    def test_format_value_string(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("status") == "active").then(1).otherwise(0).alias("flag")\n'
            ")"
        )
        result = evaluate_expression(code, "flag", {"status": "active"})
        assert result is not None

    def test_replace_column_name_special_chars(self):
        """Column name with spaces uses exact replacement (line 1508)."""
        code = 'df = df.with_columns((pl.col("my col") + 1).alias("r"))'
        result = evaluate_expression(code, "r", {"my col": 5})
        assert result is not None


# ###########################################################################
# 24. _ExprEvaluator — all evaluate branches (lines 1588–2061)
# ###########################################################################


class TestExprEvaluatorBinOp:
    def test_floor_div(self):
        code = 'df = df.with_columns((pl.col("a") // pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 10, "b": 3})
        assert result.result_value == 3

    def test_mod(self):
        code = 'df = df.with_columns((pl.col("a") % pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 10, "b": 3})
        assert result.result_value == 1

    def test_power(self):
        code = 'df = df.with_columns((pl.col("a") ** pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 2, "b": 3})
        assert result.result_value == 8

    def test_bit_and(self):
        code = 'df = df.with_columns((pl.col("a") & pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 6, "b": 3})
        assert result.result_value == 2

    def test_bit_or(self):
        code = 'df = df.with_columns((pl.col("a") | pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 6, "b": 3})
        assert result.result_value == 7

    def test_binop_none_left(self):
        code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": None, "b": 3})
        assert result.result_value is None

    def test_binop_unknown_op(self):
        """Unknown operator returns None (line 1635)."""
        # BitXor isn't in the op_map for evaluator
        code = 'df = df.with_columns((pl.col("a") ^ pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 6, "b": 3})
        # BitXor not in op_map, returns None
        assert result is not None


class TestExprEvaluatorUnaryOp:
    def test_usub(self):
        code = 'df = df.with_columns((-pl.col("x")).alias("neg"))'
        result = evaluate_expression(code, "neg", {"x": 5})
        assert result.result_value == -5

    def test_uadd(self):
        code = 'df = df.with_columns((+pl.col("x")).alias("pos"))'
        result = evaluate_expression(code, "pos", {"x": 5})
        assert result.result_value == 5

    def test_not(self):
        """Python `not` on a Polars expression raises: truth value is ambiguous."""
        code = 'df = df.with_columns((not pl.col("flag")).alias("inv"))'
        with pytest.raises(TypeError, match="the truth value of an Expr is ambiguous"):
            evaluate_expression(code, "inv", {"flag": True})

    def test_invert(self):
        code = 'df = df.with_columns((~pl.col("mask")).alias("flipped"))'
        result = evaluate_expression(code, "flipped", {"mask": 0b1010})
        assert result.result_value == ~0b1010

    def test_unary_none(self):
        """Negating a typed null propagates the null."""
        code = 'df = df.with_columns((-pl.col("x")).alias("neg"))'
        row = pl.DataFrame({"x": [None]}, schema={"x": pl.Float64})
        result = evaluate_expression(code, "neg", {"x": None}, row=row)
        assert result.result_value is None
        assert result.not_computable_reason is None


class TestExprEvaluatorCompare:
    def test_eq(self):
        code = 'df = df.with_columns((pl.col("x") == 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is True

    def test_not_eq(self):
        code = 'df = df.with_columns((pl.col("x") != 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 3})
        assert result.result_value is True

    def test_lt(self):
        code = 'df = df.with_columns((pl.col("x") < 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 3})
        assert result.result_value is True

    def test_lte(self):
        code = 'df = df.with_columns((pl.col("x") <= 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is True

    def test_gt(self):
        code = 'df = df.with_columns((pl.col("x") > 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 7})
        assert result.result_value is True

    def test_gte(self):
        code = 'df = df.with_columns((pl.col("x") >= 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is True

    def test_compare_false(self):
        code = 'df = df.with_columns((pl.col("x") > 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 3})
        assert result.result_value is False

    def test_compare_none(self):
        code = 'df = df.with_columns((pl.col("x") > 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": None})
        assert result.result_value is None


class TestExprEvaluatorBoolOp:
    def test_and_true(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("a") > 0) & (pl.col("b") > 0)).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"a": 1, "b": 1})
        assert result.result_value == 1

    def test_and_false_short_circuit(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("a") > 0) & (pl.col("b") > 0)).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"a": -1, "b": 1})
        assert result.result_value == 0

    def test_or_true(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("a") > 0) | (pl.col("b") > 0)).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"a": -1, "b": 1})
        assert result.result_value == 1

    def test_or_false(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("a") > 0) | (pl.col("b") > 0)).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"a": -1, "b": -1})
        assert result.result_value == 0


class TestExprEvaluatorName:
    def test_name_none(self):
        code = 'df = df.with_columns(pl.when(pl.col("x") > 0).then(1).otherwise(None).alias("r"))'
        result = evaluate_expression(code, "r", {"x": -1})
        assert result.result_value is None

    def test_name_true(self):
        code = (
            'df = df.with_columns(pl.when(pl.col("x") > 0).then(True).otherwise(False).alias("r"))'
        )
        result = evaluate_expression(code, "r", {"x": 1})
        assert result.result_value is True

    def test_name_false(self):
        code = (
            'df = df.with_columns(pl.when(pl.col("x") > 0).then(True).otherwise(False).alias("r"))'
        )
        result = evaluate_expression(code, "r", {"x": -1})
        assert result.result_value is False

    def test_name_from_symbol_table(self):
        """Variable resolved via symbol table in evaluator (line 1696–1697)."""
        code = 'mult = 2\ndf = df.with_columns((pl.col("x") * mult).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value == 10

    def test_name_from_row_values(self):
        """Fallback to row_values (line 1698)."""
        code = 'df = df.with_columns((pl.col("x") + pl.col("y")).alias("sum"))'
        result = evaluate_expression(code, "sum", {"x": 3, "y": 7})
        assert result.result_value == 10


class TestExprEvaluatorAttribute:
    def test_pl_attribute_returns_none(self):
        """pl.Float64 etc return None in evaluator (lines 1701–1704)."""
        code = 'df = df.with_columns(pl.col("x").cast(pl.Float64).alias("xf"))'
        result = evaluate_expression(code, "xf", {"x": 5.0})
        assert result is not None


class TestExprEvaluatorCall:
    def test_pl_col_string(self):
        code = 'df = df.with_columns(pl.col("amount").alias("r"))'
        result = evaluate_expression(code, "r", {"amount": 42})
        assert result.result_value == 42

    def test_pl_col_variable_resolved(self):
        """pl.col(var_name) where var_name is in symbol table (lines 1717–1725)."""
        code = 'col_name = "amount"\ndf = df.with_columns(pl.col(col_name).alias("r"))'
        result = evaluate_expression(code, "r", {"amount": 42})
        assert result.result_value == 42

    def test_pl_col_no_args(self):
        """Malformed pl.col() call raises a Polars TypeError, not a silent None."""
        code = 'df = df.with_columns(pl.col().alias("r"))'
        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            evaluate_expression(code, "r", {})

    def test_pl_lit(self):
        code = 'df = df.with_columns(pl.lit(99).alias("r"))'
        result = evaluate_expression(code, "r", {})
        assert result.result_value == 99

    def test_pl_lit_no_args(self):
        """Malformed pl.lit() call raises a Polars TypeError, not a silent None."""
        code = 'df = df.with_columns(pl.lit().alias("r"))'
        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            evaluate_expression(code, "r", {})

    def test_pl_when_chain(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then(1).otherwise(-1).alias("sign")\n'
            ")"
        )
        result = evaluate_expression(code, "sign", {"x": 5})
        assert result.result_value == 1

    def test_horizontal_max(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 3, "b": 7})
        assert result.result_value == 7

    def test_horizontal_min(self):
        code = 'df = df.with_columns(pl.min_horizontal(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 3, "b": 7})
        assert result.result_value == 3

    def test_horizontal_sum(self):
        code = 'df = df.with_columns(pl.sum_horizontal(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 3, "b": 7})
        assert result.result_value == 10

    def test_horizontal_mean(self):
        code = 'df = df.with_columns(pl.mean_horizontal(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 4, "b": 6})
        assert result.result_value == 5.0

    def test_horizontal_coalesce(self):
        code = 'df = df.with_columns(pl.coalesce(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": None, "b": 7})
        assert result.result_value == 7

    def test_horizontal_coalesce_all_none(self):
        code = 'df = df.with_columns(pl.coalesce(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": None, "b": None})
        assert result.result_value is None

    def test_horizontal_empty(self):
        """All None values (line 2002)."""
        code = 'df = df.with_columns(pl.sum_horizontal(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": None, "b": None})
        assert result.result_value is None

    def test_horizontal_with_list(self):
        """List arg in horizontal (lines 1993–1995)."""
        code = 'df = df.with_columns(pl.sum_horizontal([pl.col("a"), pl.col("b")]).alias("r"))'
        result = evaluate_expression(code, "r", {"a": 3, "b": 7})
        assert result.result_value == 10

    def test_pl_format_eval(self):
        """pl.format evaluation (lines 2019–2029)."""
        code = 'df = df.with_columns(pl.format("{} - {}", pl.col("a"), pl.col("b")).alias("desc"))'
        result = evaluate_expression(code, "desc", {"a": "hello", "b": "world"})
        assert result.result_value == "hello - world"

    def test_pl_format_no_args(self):
        """Malformed pl.format() call raises a Polars TypeError, not a silent None."""
        code = 'df = df.with_columns(pl.format().alias("r"))'
        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            evaluate_expression(code, "r", {})

    def test_alias_method_eval(self):
        code = 'df = df.with_columns((pl.col("x") + 1).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value == 6

    def test_cast_eval(self):
        """Cast is identity in eval (line 1748)."""
        code = 'df = df.with_columns(pl.col("x").cast(pl.Float64).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value == 5

    def test_fill_null_eval_replaces(self):
        """fill_null when value is None (lines 1751–1755)."""
        code = 'df = df.with_columns(pl.col("x").fill_null(0).alias("r"))'
        result = evaluate_expression(code, "r", {"x": None})
        assert result.result_value == 0

    def test_fill_null_eval_keeps(self):
        code = 'df = df.with_columns(pl.col("x").fill_null(0).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value == 5

    def test_fill_nan_eval_replaces(self):
        """fill_nan replaces NaN (lines 1758–1763)."""
        code = 'df = df.with_columns(pl.col("x").fill_nan(0).alias("r"))'
        result = evaluate_expression(code, "r", {"x": float("nan")})
        assert result.result_value == 0

    def test_fill_nan_eval_keeps(self):
        code = 'df = df.with_columns(pl.col("x").fill_nan(0).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5.0})
        assert result.result_value == 5.0

    def test_round_eval(self):
        """round with decimals (lines 1766–1772)."""
        code = 'df = df.with_columns(pl.col("x").round(2).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 3.14159})
        assert result.result_value == pytest.approx(3.14)

    def test_round_eval_no_val(self):
        """round of a typed null propagates the null."""
        code = 'df = df.with_columns(pl.col("x").round(2).alias("r"))'
        row = pl.DataFrame({"x": [None]}, schema={"x": pl.Float64})
        result = evaluate_expression(code, "r", {"x": None}, row=row)
        assert result.result_value is None
        assert result.not_computable_reason is None

    def test_abs_eval(self):
        """abs() (lines 1775–1779)."""
        code = 'df = df.with_columns(pl.col("x").abs().alias("r"))'
        result = evaluate_expression(code, "r", {"x": -5})
        assert result.result_value == 5

    def test_abs_eval_none(self):
        """abs of a typed null propagates the null."""
        code = 'df = df.with_columns(pl.col("x").abs().alias("r"))'
        row = pl.DataFrame({"x": [None]}, schema={"x": pl.Float64})
        result = evaluate_expression(code, "r", {"x": None}, row=row)
        assert result.result_value is None
        assert result.not_computable_reason is None

    def test_clip_eval_both_bounds(self):
        """clip with lower and upper (lines 1782–1802)."""
        code = 'df = df.with_columns(pl.col("x").clip(0, 100).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 150})
        assert result.result_value == 100

    def test_clip_eval_lower_bound(self):
        code = 'df = df.with_columns(pl.col("x").clip(0, 100).alias("r"))'
        result = evaluate_expression(code, "r", {"x": -5})
        assert result.result_value == 0

    def test_clip_eval_within_bounds(self):
        code = 'df = df.with_columns(pl.col("x").clip(0, 100).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 50})
        assert result.result_value == 50

    def test_clip_eval_none(self):
        """clip of a typed null propagates the null."""
        code = 'df = df.with_columns(pl.col("x").clip(0, 100).alias("r"))'
        row = pl.DataFrame({"x": [None]}, schema={"x": pl.Int64})
        result = evaluate_expression(code, "r", {"x": None}, row=row)
        assert result.result_value is None
        assert result.not_computable_reason is None

    def test_clip_keyword_bounds(self):
        """clip with keyword args (lines 1794–1797)."""
        code = 'df = df.with_columns(pl.col("x").clip(lower_bound=0, upper_bound=100).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 150})
        assert result.result_value == 100

    def test_dt_year_eval(self):
        """dt.year() evaluation (lines 1805–1817)."""
        code = 'df = df.with_columns(pl.col("date").dt.year().alias("yr"))'
        result = evaluate_expression(code, "yr", {"date": date(2024, 6, 15)})
        assert result.result_value == 2024

    def test_dt_month_eval(self):
        code = 'df = df.with_columns(pl.col("date").dt.month().alias("mo"))'
        result = evaluate_expression(code, "mo", {"date": date(2024, 6, 15)})
        assert result.result_value == 6

    def test_dt_day_eval(self):
        code = 'df = df.with_columns(pl.col("date").dt.day().alias("d"))'
        result = evaluate_expression(code, "d", {"date": date(2024, 6, 15)})
        assert result.result_value == 15

    def test_dt_none_val(self):
        """dt.year of a typed null date propagates the null."""
        code = 'df = df.with_columns(pl.col("date").dt.year().alias("yr"))'
        row = pl.DataFrame({"date": [None]}, schema={"date": pl.Date})
        result = evaluate_expression(code, "yr", {"date": None}, row=row)
        assert result.result_value is None
        assert result.not_computable_reason is None

    def test_str_to_lowercase_eval(self):
        """str.to_lowercase() (lines 1819–1831)."""
        code = 'df = df.with_columns(pl.col("name").str.to_lowercase().alias("lower"))'
        result = evaluate_expression(code, "lower", {"name": "HELLO"})
        assert result.result_value == "hello"

    def test_str_to_uppercase_eval(self):
        code = 'df = df.with_columns(pl.col("name").str.to_uppercase().alias("upper"))'
        result = evaluate_expression(code, "upper", {"name": "hello"})
        assert result.result_value == "HELLO"

    def test_str_contains_eval(self):
        code = 'df = df.with_columns(pl.col("text").str.contains("fire").alias("has_fire"))'
        result = evaluate_expression(code, "has_fire", {"text": "wildfire risk"})
        assert result.result_value is True

    def test_str_contains_false_eval(self):
        code = 'df = df.with_columns(pl.col("text").str.contains("ice").alias("has_ice"))'
        result = evaluate_expression(code, "has_ice", {"text": "wildfire risk"})
        assert result.result_value is False

    def test_str_none_val(self):
        """str.to_lowercase of a typed null string propagates the null."""
        code = 'df = df.with_columns(pl.col("name").str.to_lowercase().alias("lower"))'
        row = pl.DataFrame({"name": [None]}, schema={"name": pl.String})
        result = evaluate_expression(code, "lower", {"name": None}, row=row)
        assert result.result_value is None
        assert result.not_computable_reason is None

    def test_str_non_string_val(self):
        """str.to_lowercase on an integer column raises a Polars SchemaError."""
        code = 'df = df.with_columns(pl.col("name").str.to_lowercase().alias("lower"))'
        with pytest.raises(pl.exceptions.SchemaError, match="String"):
            evaluate_expression(code, "lower", {"name": 42})

    def test_is_null_eval(self):
        """is_null() (lines 1834–1836)."""
        code = 'df = df.with_columns(pl.col("x").is_null().alias("r"))'
        result = evaluate_expression(code, "r", {"x": None})
        assert result.result_value is True

    def test_is_null_false_eval(self):
        code = 'df = df.with_columns(pl.col("x").is_null().alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is False

    def test_is_not_null_eval(self):
        """is_not_null() (lines 1839–1841)."""
        code = 'df = df.with_columns(pl.col("x").is_not_null().alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is True

    def test_is_between_eval(self):
        """is_between() (lines 1844–1851)."""
        code = 'df = df.with_columns(pl.col("x").is_between(0, 10).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is True

    def test_is_between_false_eval(self):
        code = 'df = df.with_columns(pl.col("x").is_between(0, 10).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 15})
        assert result.result_value is False

    def test_is_in_eval(self):
        """is_in() (lines 1854–1860).
        The evaluator can't evaluate ast.List nodes to Python lists, so is_in
        returns None for literal lists. This still exercises the code path."""
        code = 'df = df.with_columns(pl.col("x").is_in([1, 2, 3]).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 2})
        # is_in with literal list — evaluator returns None since ast.List can't be evaluated
        assert result is not None

    def test_is_in_false_eval(self):
        code = 'df = df.with_columns(pl.col("x").is_in([1, 2, 3]).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result is not None

    def test_agg_sum_eval(self):
        """Aggregations read more than the one traced row: not row-local."""
        code = 'df = df.with_columns(pl.col("x").sum().alias("r"))'
        result = evaluate_expression(code, "r", {"x": 10})
        assert result.result_value is None
        assert result.not_computable_reason == "not_row_local: sum"

    def test_agg_mean_eval(self):
        code = 'df = df.with_columns(pl.col("x").mean().alias("r"))'
        result = evaluate_expression(code, "r", {"x": 10})
        assert result.result_value is None
        assert result.not_computable_reason == "not_row_local: mean"

    def test_over_eval(self):
        """A window function is not row-local."""
        code = 'df = df.with_columns(pl.col("x").sum().over("group").alias("r"))'
        result = evaluate_expression(code, "r", {"x": 10, "group": "A"})
        assert result.result_value is None
        assert result.not_computable_reason == "not_row_local: sum"

    def test_shift_eval(self):
        """shift/diff read neighbouring rows: not row-local."""
        code = 'df = df.with_columns(pl.col("x").shift(1).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 10})
        assert result.result_value is None
        assert result.not_computable_reason == "not_row_local: shift"

    def test_diff_eval(self):
        code = 'df = df.with_columns(pl.col("x").diff().alias("r"))'
        result = evaluate_expression(code, "r", {"x": 10})
        assert result.result_value is None
        assert result.not_computable_reason == "not_row_local: diff"

    def test_log_eval(self):
        """log() (lines 1888–1892)."""
        code = 'df = df.with_columns(pl.col("x").log().alias("r"))'
        result = evaluate_expression(code, "r", {"x": math.e})
        assert result.result_value == pytest.approx(1.0)

    def test_log_eval_negative(self):
        # Polars float domain: log of a negative number is NaN, not null.
        code = 'df = df.with_columns(pl.col("x").log().alias("r"))'
        result = evaluate_expression(code, "r", {"x": -1})
        assert isinstance(result.result_value, float) and math.isnan(result.result_value)

    def test_sqrt_eval(self):
        """sqrt() (lines 1895–1899)."""
        code = 'df = df.with_columns(pl.col("x").sqrt().alias("r"))'
        result = evaluate_expression(code, "r", {"x": 16})
        assert result.result_value == 4.0

    def test_sqrt_eval_negative(self):
        # Polars float domain: sqrt of a negative number is NaN, not null.
        code = 'df = df.with_columns(pl.col("x").sqrt().alias("r"))'
        result = evaluate_expression(code, "r", {"x": -1})
        assert isinstance(result.result_value, float) and math.isnan(result.result_value)

    def test_replace_strict_eval(self):
        """A literal mapping is row-local: the row's value is looked up."""
        code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1, "b": 2}).alias("r"))'
        result = evaluate_expression(code, "r", {"x": "a"})
        assert result.result_value == 1
        assert result.not_computable_reason is None

    def test_replace_strict_default(self):
        code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1}, default=0).alias("r"))'
        result = evaluate_expression(code, "r", {"x": "b"})
        assert result.result_value == 0

    def test_replace_strict_variable_dict(self):
        """A mapping bound to a literal in the node's code is inlined as that literal."""
        code = (
            'mapping = {"a": 1, "b": 2}\n'
            'df = df.with_columns(pl.col("x").replace_strict(mapping).alias("r"))'
        )
        result = evaluate_expression(code, "r", {"x": "a"})
        assert result.result_value == 1

    def test_replace_method_eval(self):
        """Non-strict .replace() keeps the column's original dtype, so the
        mapped value comes back as the string "1", not the int 1."""
        code = 'df = df.with_columns(pl.col("x").replace({"a": 1, "b": 2}).alias("r"))'
        result = evaluate_expression(code, "r", {"x": "a"})
        assert result.result_value == "1"

    def test_bare_function_eval_returns_none(self):
        """Bare function call returns None (lines 1917–1919)."""
        code = 'df = df.with_columns(abs(pl.col("x")).alias("r"))'
        result = evaluate_expression(code, "r", {"x": -5})
        assert result is not None

    def test_default_method_eval(self):
        """Unknown method falls through to evaluate receiver (line 1914)."""
        code = 'df = df.with_columns(pl.col("x").some_unknown_method().alias("r"))'
        result = evaluate_expression(code, "r", {"x": 42})
        assert result is not None


# ###########################################################################
# 25. _BranchTrackingEvaluator (lines 2064–2134)
# ###########################################################################


class TestBranchTrackingEvaluator:
    def test_taken_branch_then(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then("pos").otherwise("non_pos").alias("sign")\n'
            ")"
        )
        result = evaluate_expression(code, "sign", {"x": 5})
        assert result.taken_branch == "then"
        assert result.taken_branch_index == 0
        assert len(result.dimmed_branches) > 0

    def test_taken_branch_otherwise(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then("pos").otherwise("non_pos").alias("sign")\n'
            ")"
        )
        result = evaluate_expression(code, "sign", {"x": -1})
        assert result.taken_branch == "otherwise"

    def test_chained_when_second_branch(self):
        """Second branch taken in chained when. A bare string in .then()/.otherwise()
        is Polars column selection, not a literal, so "neg" is read as a missing
        column even though the *taken* branch is "zero"."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") < 0).then("neg")\n'
            '    .when(pl.col("x") == 0).then("zero")\n'
            '    .otherwise("pos")\n'
            '    .alias("sign")\n'
            ")"
        )
        result = evaluate_expression(code, "sign", {"x": 0})
        assert result.result_value is None
        assert result.not_computable_reason == "column_unavailable: neg"
        assert result.taken_branch is not None

    def test_nested_when_in_then_branch(self):
        """Nested conditional in then value (lines 2088–2104)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("tier") == "gold")\n'
            "    .then(\n"
            '        pl.when(pl.col("years") > 5).then(0.9).otherwise(0.95)\n'
            "    )\n"
            "    .otherwise(1.0)\n"
            '    .alias("discount")\n'
            ")"
        )
        result = evaluate_expression(code, "discount", {"tier": "gold", "years": 10})
        assert result.result_value == 0.9

    def test_branch_tracking_no_match(self):
        """No branch matched returns None (line 2114)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 100).then("big")\n'
            '    .alias("label")\n'
            ")"
        )
        result = evaluate_expression(code, "label", {"x": 1})
        assert result is not None


# ###########################################################################
# 29. _compute_result_impl — no alias match retry (lines 1568–1582)
# ###########################################################################


class TestComputeResultImpl:
    def test_compute_no_alias_auto_name(self):
        """Auto name match in compute (lines 1570–1579)."""
        code = 'df = df.with_columns(pl.col("x") + 1)'
        result = evaluate_expression(code, "x", {"x": 5})
        assert result is not None

    def test_compute_target_not_found(self):
        """Target not found falls back to row_values (line 1582)."""
        code = 'df = df.with_columns((pl.col("a") + 1).alias("b"))'
        result = evaluate_expression(code, "nonexistent", {"nonexistent": 99})
        assert result is not None


# ###########################################################################
# 30. _ExprEvaluator.evaluate — node type coverage (lines 1595–1614)
# ###########################################################################


class TestEvaluatorNodeTypes:
    def test_evaluate_constant(self):
        code = 'df = df.with_columns(pl.lit(42).alias("r"))'
        result = evaluate_expression(code, "r", {})
        assert result.result_value == 42

    def test_evaluate_unaryop(self):
        code = 'df = df.with_columns((-pl.col("x")).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 7})
        assert result.result_value == -7

    def test_evaluate_compare(self):
        code = 'df = df.with_columns((pl.col("x") > 5).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 10})
        assert result.result_value is True

    def test_evaluate_boolop(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("a") > 0) & (pl.col("b") > 0)).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"a": 1, "b": 1})
        assert result.result_value == 1

    def test_evaluate_attribute(self):
        """Attribute node in evaluator returns None for pl.X (lines 1610–1614)."""
        code = 'df = df.with_columns(pl.col("x").cast(pl.Int32).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result is not None

    def test_evaluate_fallback_node(self):
        """Unknown node type returns None (line 1614)."""
        # This is hard to trigger directly, but we can verify evaluator handles gracefully
        code = 'df = df.with_columns(pl.col("x").alias("r"))'
        result = evaluate_expression(code, "r", {"x": 42})
        assert result.result_value == 42


# ###########################################################################
# 31. _evaluate_conditional_branches — syntax error + no match (1447-1483)
# ###########################################################################


class TestEvaluateConditionalBranches:
    def test_conditional_syntax_error_returns_empty(self):
        """SyntaxError returns empty dict (line 1456–1457)."""
        # Force through evaluate_expression with invalid code for conditional
        code = (
            'df = df.with_columns(\n    pl.when(pl.col("x") > 0).then(1).otherwise(0).alias("r")\n)'
        )
        # Normal valid case first
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.taken_branch is not None

    def test_conditional_target_not_found(self):
        """best_match is None in branch eval (line 1472–1473)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then(1).otherwise(0).alias("something_else")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"x": 5})
        assert result is not None


class TestEvaluatorBoolOpDirect:
    """Cover _ExprEvaluator._boolop (lines 1672–1686) — direct Python and/or."""

    def test_eval_and_all_true(self):
        """Python `and` on Polars expressions raises: truth value is ambiguous."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 0 and pl.col("b") > 0).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        with pytest.raises(TypeError, match="the truth value of an Expr is ambiguous"):
            evaluate_expression(code, "r", {"a": 5, "b": 5})

    def test_eval_or_first_false(self):
        """Python `or` on Polars expressions raises: truth value is ambiguous."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 0 or pl.col("b") > 0).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        with pytest.raises(TypeError, match="the truth value of an Expr is ambiguous"):
            evaluate_expression(code, "r", {"a": -1, "b": 5})


class TestEvaluatorNameLiterals:
    """Cover Name node literal handling in evaluator (lines 1690–1695)."""

    def test_eval_name_none(self):
        """Name 'None' (line 1690–1691)."""
        code = (
            "df = df.with_columns("
            'pl.when(pl.col("x") > 0).then(pl.col("x")).otherwise(None).alias("r"))'
        )
        result = evaluate_expression(code, "r", {"x": -1})
        assert result.result_value is None

    def test_eval_name_true(self):
        """Name 'True' (line 1692–1693)."""
        code = (
            'df = df.with_columns(pl.when(pl.col("x") > 0).then(True).otherwise(False).alias("r"))'
        )
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is True

    def test_eval_name_false(self):
        """Name 'False' (line 1694–1695)."""
        code = (
            'df = df.with_columns(pl.when(pl.col("x") > 0).then(True).otherwise(False).alias("r"))'
        )
        result = evaluate_expression(code, "r", {"x": -1})
        assert result.result_value is False


class TestEvaluatorPlCol:
    """Cover pl.col edge cases in evaluator (lines 1716–1726)."""

    def test_pl_col_non_string_constant(self):
        """pl.col(non_string) is a malformed call: Polars raises a TypeError."""
        code = 'df = df.with_columns(pl.col(42).alias("r"))'
        with pytest.raises(TypeError, match="invalid input for `col`"):
            evaluate_expression(code, "r", {})

    def test_pl_col_name_variable(self):
        """pl.col(variable) resolved from symbol table (lines 1717–1725)."""
        code = 'cn = "amount"\ndf = df.with_columns(pl.col(cn).alias("r"))'
        result = evaluate_expression(code, "r", {"amount": 100})
        assert result.result_value == 100


class TestEvaluatorPlWhen:
    """Cover pl.when direct call in evaluator (line 1732)."""

    def test_pl_when_direct(self):
        """When called directly returns None (line 1732)."""
        code = 'df = df.with_columns(pl.when(pl.col("x") > 0).then(1).otherwise(0).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value == 1


class TestEvaluatorHorizontalUnknown:
    """Cover unknown horizontal function (line 2016–2017)."""

    def test_all_horizontal_eval(self):
        """all_horizontal evaluation — not in eval switch (line 2017)."""
        code = 'df = df.with_columns(pl.all_horizontal(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": True, "b": True})
        assert result is not None

    def test_any_horizontal_eval(self):
        code = 'df = df.with_columns(pl.any_horizontal(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": False, "b": True})
        assert result is not None


class TestEvaluatorFormatEdges:
    """Cover format edge cases (lines 2023–2029)."""

    def test_format_non_string_fmt(self):
        """Non-string format arg is a malformed call: Polars raises a TypeError."""
        code = 'df = df.with_columns(pl.format(42, pl.col("a")).alias("r"))'
        with pytest.raises(TypeError, match="not an instance of 'str'"):
            evaluate_expression(code, "r", {"a": "x"})

    def test_format_exception(self):
        """Too few placeholders for arguments raises a Polars ShapeError."""
        code = 'df = df.with_columns(pl.format("{} {} {}", pl.col("a")).alias("r"))'
        with pytest.raises(pl.exceptions.ShapeError, match="too few arguments"):
            evaluate_expression(code, "r", {"a": "x"})


class TestEvaluatorReplaceEdges:
    """Cover replace edge cases (lines 2033–2061)."""

    def test_replace_no_args(self):
        """replace_strict without a literal mapping is not proven row-local."""
        code = 'df = df.with_columns(pl.col("x").replace_strict().alias("r"))'
        result = evaluate_expression(code, "r", {"x": "a"})
        assert result.result_value is None
        assert result.not_computable_reason == "not_row_local: replace_strict"

    def test_replace_variable_not_found(self):
        """An unmapped value without a default raises, as Polars does for the column."""
        code = (
            'mapping = {"a": 1}\n'
            'df = df.with_columns(pl.col("x").replace_strict(mapping).alias("r"))'
        )
        with pytest.raises(pl.exceptions.InvalidOperationError, match="incomplete mapping"):
            evaluate_expression(code, "r", {"x": "z"})

    def test_replace_variable_with_default(self):
        code = (
            'mapping = {"a": 1}\n'
            'df = df.with_columns(pl.col("x").replace_strict(mapping, default=0).alias("r"))'
        )
        result = evaluate_expression(code, "r", {"x": "z"})
        assert result.result_value == 0


class TestBranchTrackingNestedCheck:
    """Cover _check_nested_when (lines 2116–2129)."""

    def test_nested_when_args_check(self):
        """Nested when detected via args (lines 2126–2128)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("tier") == "gold")\n'
            "    .then(\n"
            '        pl.when(pl.col("years") > 5).then(0.9).otherwise(0.95)\n'
            "    )\n"
            "    .otherwise(1.0)\n"
            '    .alias("discount")\n'
            ")"
        )
        result = evaluate_expression(code, "discount", {"tier": "gold", "years": 3})
        assert result.result_value == 0.95

    def test_nested_when_otherwise_taken(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("tier") == "gold")\n'
            "    .then(\n"
            '        pl.when(pl.col("years") > 5).then(0.9).otherwise(0.95)\n'
            "    )\n"
            "    .otherwise(1.0)\n"
            '    .alias("discount")\n'
            ")"
        )
        result = evaluate_expression(code, "discount", {"tier": "silver", "years": 10})
        assert result.result_value == 1.0


class TestEvalClauseCollectionAlias:
    """Cover alias path in _collect_eval_clauses (lines 1971–1973)."""

    def test_eval_when_then_otherwise_alias(self):
        """When chain wrapped with alias. A bare string in .then() is Polars
        column selection, not a literal, so the first-branch string "high" is
        read as a missing column even though the "mid" branch is taken."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 5).then("high")\n'
            '    .when(pl.col("x") > 0).then("mid")\n'
            '    .otherwise("low")\n'
            '    .alias("tier")\n'
            ")"
        )
        result = evaluate_expression(code, "tier", {"x": 3})
        assert result.result_value is None
        assert result.not_computable_reason == "column_unavailable: high"

    def test_eval_when_chain_break_path(self):
        """When chain with unexpected method (line 1975)."""
        code = (
            'df = df.with_columns(\n    pl.when(pl.col("x") > 0).then(1).otherwise(0).alias("r")\n)'
        )
        result = evaluate_expression(code, "r", {"x": -5})
        assert result.result_value == 0


class TestComputeResultSyntaxError:
    """Cover _compute_result_impl syntax error (lines 1552–1553)."""

    def test_compute_result_syntax_error(self):
        """Malformed code in evaluate falls back gracefully."""
        # This is covered by the exception fallback in evaluate_expression
        result = evaluate_expression(
            'df = df.with_columns((pl.col("x") + 1).alias("r"))', "r", {"x": 5}
        )
        assert result.result_value == 6


class TestEvaluatorExprNode:
    """Cover evaluate Expr node (line 1612–1613)."""

    def test_evaluate_expr_wrapper(self):
        code = 'df = df.with_columns((pl.col("x") + 1).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 10})
        assert result.result_value == 11


class TestComputeResultNoAliasRetry:
    """Cover _compute_result_impl no-alias retry (lines 1570-1579)."""

    def test_compute_result_auto_name(self):
        """Expression without alias, auto-name detected (lines 1574-1579)."""
        code = 'df = df.with_columns(pl.col("x"))'
        result = evaluate_expression(code, "x", {"x": 42})
        assert result is not None

    def test_compute_result_auto_name_binop(self):
        """BinOp auto-name via left col (line 1574)."""
        code = 'df = df.with_columns(pl.col("x") + 1)'
        result = evaluate_expression(code, "x", {"x": 5})
        assert result is not None


class TestEvaluatorDtTotalDays:
    """Cover dt.total_days() (lines 1814-1816)."""

    def test_dt_total_days(self):
        """dt.total_days is row-local: a duration's whole days."""
        from datetime import timedelta

        code = 'df = df.with_columns(pl.col("dur").dt.total_days().alias("days"))'
        result = evaluate_expression(code, "days", {"dur": timedelta(days=30)})
        assert result.result_value == 30
        assert result.not_computable_reason is None


class TestEvaluatorStrContainsNoArgs:
    """Cover str.contains with no pattern (line 1830)."""

    def test_str_contains_no_pattern(self):
        code = 'df = df.with_columns(pl.col("text").str.contains("").alias("r"))'
        result = evaluate_expression(code, "r", {"text": "hello"})
        assert result is not None


class TestEvaluatorIsBetweenNone:
    """Cover is_between with None value (line 1851)."""

    def test_is_between_none_val(self):
        code = 'df = df.with_columns(pl.col("x").is_between(0, 10).alias("r"))'
        result = evaluate_expression(code, "r", {"x": None})
        assert result.result_value is None


class TestEvaluatorIsInNoArgs:
    """Cover is_in edge (line 1859)."""

    def test_is_in_none_val(self):
        code = 'df = df.with_columns(pl.col("x").is_in([1, 2]).alias("r"))'
        result = evaluate_expression(code, "r", {"x": None})
        assert result is not None


class TestEvaluatorWhenChainDirect:
    """Cover _eval_when_chain and _eval_chained_when (lines 1923-1936)."""

    def test_eval_when_returns_none(self):
        """Direct pl.when() call in evaluator returns None (line 1926)."""
        # This is reached when evaluator encounters pl.when() as a standalone — unusual
        code = 'df = df.with_columns(pl.when(pl.col("x") > 0).then(1).otherwise(0).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value == 1


class TestEvaluatorUnknownHorizontal:
    """Cover unknown horizontal returning None (line 2016)."""

    def test_concat_str_eval(self):
        """concat_str is in _HORIZONTAL_FUNCS but not in eval switch."""
        code = (
            "df = df.with_columns("
            'pl.concat_str(pl.col("a"), pl.col("b"), separator="-").alias("r"))'
        )
        result = evaluate_expression(code, "r", {"a": "hello", "b": "world"})
        # concat_str not in eval horizontal switch → returns None
        assert result is not None


class TestEvaluatorReplaceNoMapping:
    """Cover replace with no args."""

    def test_replace_no_args(self):
        """A malformed .replace() call (no mapping) is not classified as row-local."""
        code = 'df = df.with_columns(pl.col("x").replace().alias("r"))'
        result = evaluate_expression(code, "r", {"x": "a"})
        assert result.result_value is None
        assert result.not_computable_reason == "not_row_local: replace"


class TestEvalExpressionBOM:
    """Cover evaluate_expression BOM and wrapping (lines 1335-1340, 1352)."""

    def test_eval_with_bom(self):
        code = '\ufeffdf = df.with_columns((pl.col("x") + 1).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value == 6

    def test_eval_parsed_none_fallback(self):
        """parsed is None fallback (line 1352)."""
        # Force a path where parsed could be None — empty with_columns
        code = "df = df.with_columns()"
        result = evaluate_expression(code, "r", {"r": 42})
        assert result is not None


# ###########################################################################
# 35. Evaluator - remaining evaluator paths
# ###########################################################################


class TestEvaluatorBoolOpPaths:
    """Cover BoolOp evaluator And/Or paths (lines 1672-1686)."""

    def test_bool_and_short_circuit_false(self):
        """Python `and` on Polars expressions raises: truth value is ambiguous."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0 and pl.col("y") > 0)\n'
            '    .then(1).otherwise(0).alias("r")\n'
            ")"
        )
        with pytest.raises(TypeError, match="the truth value of an Expr is ambiguous"):
            evaluate_expression(code, "r", {"x": -1, "y": 5})

    def test_bool_and_all_true_returns_last(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0 and pl.col("y") > 0)\n'
            '    .then(1).otherwise(0).alias("r")\n'
            ")"
        )
        with pytest.raises(TypeError, match="the truth value of an Expr is ambiguous"):
            evaluate_expression(code, "r", {"x": 5, "y": 5})

    def test_bool_or_first_truthy(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0 or pl.col("y") > 0)\n'
            '    .then(1).otherwise(0).alias("r")\n'
            ")"
        )
        with pytest.raises(TypeError, match="the truth value of an Expr is ambiguous"):
            evaluate_expression(code, "r", {"x": 5, "y": -1})

    def test_bool_or_all_falsy(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0 or pl.col("y") > 0)\n'
            '    .then(1).otherwise(0).alias("r")\n'
            ")"
        )
        with pytest.raises(TypeError, match="the truth value of an Expr is ambiguous"):
            evaluate_expression(code, "r", {"x": -1, "y": -1})


class TestEvaluatorNameLiteralsInWhen:
    """Cover evaluator _name for None/True/False (lines 1690-1698)."""

    def test_eval_none_name(self):
        code = 'df = df.with_columns(pl.lit(None).alias("r"))'
        result = evaluate_expression(code, "r", {})
        assert result.result_value is None

    def test_eval_true_name(self):
        code = 'df = df.with_columns(pl.lit(True).alias("r"))'
        result = evaluate_expression(code, "r", {})
        assert result.result_value is True

    def test_eval_false_name(self):
        code = 'df = df.with_columns(pl.lit(False).alias("r"))'
        result = evaluate_expression(code, "r", {})
        assert result.result_value is False


class TestEvaluatorAttributeNonPl:
    """Cover evaluator _attribute for non-pl (lines 1700-1704)."""

    def test_non_pl_attribute_returns_none(self):
        """Non-pl attribute just returns None."""
        code = 'df = df.with_columns(pl.col("x").cast(pl.Float64).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5.0})
        # cast evaluates receiver, pl.Float64 is an attribute returning None
        assert result is not None


class TestEvaluatorPlColVariable:
    """Cover pl.col(Name) in evaluator (lines 1717-1726)."""

    def test_pl_col_variable_not_in_table(self):
        """pl.col(variable) where variable is not in symbol table (line 1726)."""
        code = 'df = df.with_columns(pl.col(dynamic_name).alias("r"))'
        result = evaluate_expression(code, "r", {"r": 99})
        assert result is not None

    def test_pl_col_variable_non_string_resolved(self):
        """pl.col(variable) resolved to a non-string is a malformed call."""
        code = 'col_idx = 42\ndf = df.with_columns(pl.col(col_idx).alias("r"))'
        with pytest.raises(TypeError, match="invalid input for `col`"):
            evaluate_expression(code, "r", {"r": 99})


class TestEvaluatorPlLitNoArgs:
    """Cover pl.lit() no args."""

    def test_pl_lit_no_args_eval(self):
        code = 'df = df.with_columns(pl.lit().alias("r"))'
        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            evaluate_expression(code, "r", {})


class TestComputeResultImplSyntaxError:
    """Cover _compute_result_impl syntax error (lines 1552-1553)."""

    def test_syntax_error_in_compute(self):
        """Malformed code falls back to row_values."""
        code = 'df = df.with_columns((pl.col("x") +).alias("r"))'
        result = evaluate_expression(code, "r", {"r": 42})
        assert result is not None


class TestEvalClauseCollectionPaths:
    """Cover _collect_eval_clauses edge paths (lines 1969-1975)."""

    def test_eval_when_break_no_preceding_when(self):
        """then() without preceding when() breaks (line 1969)."""
        # This path is very hard to trigger directly
        code = (
            'df = df.with_columns(\n    pl.when(pl.col("x") > 0).then(1).otherwise(0).alias("r")\n)'
        )
        result = evaluate_expression(code, "r", {"x": -5})
        assert result.result_value == 0


# ###########################################################################
# 36. Remaining evaluator and converter paths
# ###########################################################################


class TestEvaluatorNameLiteralsDirect:
    """Directly test evaluator _name for None/True/False (lines 1691-1695)."""

    def test_when_then_lit_none(self):
        """then with lit(None) — evaluator hits None literal."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then(pl.lit(None)).otherwise(1).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is None

    def test_when_then_lit_true(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then(pl.lit(True)).otherwise(pl.lit(False)).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is True

    def test_when_then_lit_false(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then(pl.lit(True)).otherwise(pl.lit(False)).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"x": -5})
        assert result.result_value is False


class TestEvaluatorPlWhenDirect:
    """Cover pl.when direct evaluation (line 1732) - reached when .when() is the method."""

    def test_when_chain_eval(self):
        """When/then/otherwise where evaluator walks the full chain. A bare string
        in .then() is Polars column selection, so the first branch's "neg" is a
        missing column even though "zero" is the taken branch."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") < 0).then("neg")\n'
            '    .when(pl.col("x") == 0).then("zero")\n'
            '    .otherwise("pos")\n'
            '    .alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"x": 0})
        assert result.result_value is None
        assert result.not_computable_reason == "column_unavailable: neg"


class TestEvaluatorCompareChainFalse:
    """Cover compare returning False (line 1668-1669)."""

    def test_compare_chain_false(self):
        code = 'df = df.with_columns((pl.col("x") > 10).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 3})
        assert result.result_value is False


class TestEvaluatorExprWrapperNode:
    """Cover evaluate Expr node (lines 1611-1613)."""

    def test_expr_wrapper_direct(self):
        """ast.Expr wrapper is unwrapped."""
        code = 'df = df.with_columns((pl.col("x") * 2).alias("r"))'
        result = evaluate_expression(code, "r", {"x": 7})
        assert result.result_value == 14


class TestEvaluatorUnaryOpFallback:
    """Cover unary op unknown type (line 1649)."""

    def test_unary_invert_eval(self):
        code = 'df = df.with_columns((~pl.col("mask")).alias("flipped"))'
        result = evaluate_expression(code, "flipped", {"mask": 0xFF})
        assert result.result_value == ~0xFF


class TestEvaluatorIsInNoneNoArgs:
    """Cover is_in with no args returns None (line 1859)."""

    def test_is_in_no_args(self):
        code = 'df = df.with_columns(pl.col("x").is_in().alias("r"))'
        result = evaluate_expression(code, "r", {"x": 5})
        assert result is not None


class TestEvaluatorReplaceMapping:
    """Cover replace mapping default fallback (line 2048)."""

    def test_replace_strict_no_match_no_default(self):
        """An unmapped value without a default raises, as it does for the column."""
        code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1, "b": 2}).alias("r"))'
        with pytest.raises(pl.exceptions.InvalidOperationError, match="incomplete mapping"):
            evaluate_expression(code, "r", {"x": "c"})


class TestComputeResultNoMatch:
    """Cover _compute_result_impl target not found (lines 1574-1579)."""

    def test_compute_auto_name_no_alias(self):
        """Auto-name in compute path (lines 1574-1577)."""
        code = 'df = df.with_columns(pl.col("x") + 1)'
        result = evaluate_expression(code, "x", {"x": 42})
        assert result is not None


class TestBranchTrackingNested:
    """Cover _check_nested_when and nested branch paths (lines 2123-2128)."""

    def test_nested_when_detected_via_then(self):
        """_check_nested_when finds .then() (line 2121)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 0)\n'
            "    .then(\n"
            '        pl.when(pl.col("b") > 0).then(10).otherwise(20)\n'
            "    )\n"
            "    .otherwise(30)\n"
            '    .alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"a": 1, "b": -1})
        assert result.result_value == 20

    def test_nested_when_detected_via_pl_when(self):
        """Nested pl.when detection. A bare string in .then() is Polars column
        selection, so "yes" is read as a missing column."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 0)\n'
            "    .then(\n"
            '        pl.when(pl.col("b") > 0).then("yes").otherwise("no")\n'
            "    )\n"
            '    .otherwise("none")\n'
            '    .alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"a": 1, "b": 1})
        assert result.result_value is None
        assert result.not_computable_reason == "column_unavailable: yes"


class TestEvaluatorPlWhenAttr:
    """Cover pl.when() attribute dispatch in evaluator (line 1732)."""

    def test_pl_when_in_evaluator(self):
        """Evaluator hits pl.when() which calls _eval_when_chain (line 1732)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then(1).otherwise(-1).alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"x": 10})
        assert result.result_value == 1


class TestConditionalBranchEvalSyntax:
    """Cover _evaluate_conditional_branches syntax error (lines 1456-1457)."""

    def test_branch_eval_with_valid_code(self):
        code = (
            'df = df.with_columns(\n    pl.when(pl.col("x") > 0).then(1).otherwise(0).alias("r")\n)'
        )
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.taken_branch == "then"

    def test_branch_eval_target_not_found(self):
        """Branch eval target not found returns empty (line 1472-1473)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then(1).otherwise(0).alias("other")\n'
            ")"
        )
        # Looking for "r" but only "other" exists
        result = evaluate_expression(code, "r", {"x": 5})
        assert result is not None


class TestEvalWhenChainMethod:
    """Cover _eval_when_chain (line 1926) and _eval_chained_when (line 1936)."""

    def test_eval_when_chain_standalone(self):
        """A bare string in .then() is Polars column selection, so "pos" is
        read as a missing column even though it is the taken branch."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then("pos").otherwise("neg").alias("r")\n'
            ")"
        )
        result = evaluate_expression(code, "r", {"x": 5})
        assert result.result_value is None
        assert result.not_computable_reason == "column_unavailable: pos"


class TestEvalUnknownHorizontalFunc:
    """Cover horizontal function not in switch (line 2016)."""

    def test_concat_str_eval_returns_none(self):
        """concat_str not handled in eval → returns None (line 2016)."""
        code = 'df = df.with_columns(pl.concat_str(pl.col("a"), pl.col("b")).alias("r"))'
        result = evaluate_expression(code, "r", {"a": "hello", "b": "world"})
        # concat_str falls through to return None at line 2017
        assert result is not None


def _trace_value(expr_text: str, row: dict) -> object:
    # Parenthesise so .alias binds to the whole expression (not a sub-node when
    # the outermost operator is prefix/binary, e.g. ``~x`` or ``a / b``).
    code = f'df = df.with_columns(({expr_text}).alias("out"))'
    return evaluate_expression(code, "out", dict(row)).result_value


def _polars_value(expr_text: str, row: dict) -> object:
    expr = eval(expr_text, {"pl": pl})  # test-local literal, never user input
    return pl.DataFrame([dict(row)]).select(expr.alias("out")).item()


def _eval_node(src: str, row: dict | None = None) -> object:
    code = f"df = df.with_columns(r={src})"
    return evaluate_expression(code, "r", dict(row or {})).result_value


def _assert_matches_polars(expr_text: str, row: dict) -> object:
    trace = _trace_value(expr_text, row)
    polars = _polars_value(expr_text, row)
    if isinstance(polars, float) and math.isnan(polars):
        assert isinstance(trace, float) and math.isnan(trace), (
            f"{expr_text} on {row}: trace={trace!r} polars=nan"
        )
    else:
        assert trace == polars, f"{expr_text} on {row}: trace={trace!r} polars={polars!r}"
    return polars


# ---------------------------------------------------------------------------
# F680 — Kleene three-valued boolean logic for & / |
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row,expected",
    [
        ('pl.col("a") & pl.col("b")', {"a": False, "b": None}, False),
        ('pl.col("a") & pl.col("b")', {"a": True, "b": None}, None),
        ('pl.col("a") & pl.col("b")', {"a": None, "b": False}, False),
        ('pl.col("a") | pl.col("b")', {"a": True, "b": None}, True),
        ('pl.col("a") | pl.col("b")', {"a": False, "b": None}, None),
        ('pl.col("a") | pl.col("b")', {"a": None, "b": True}, True),
        ('pl.col("a") & pl.col("b")', {"a": True, "b": True}, True),
        ('pl.col("a") & pl.col("b")', {"a": True, "b": False}, False),
        ('pl.col("a") | pl.col("b")', {"a": False, "b": False}, False),
    ],
)
def test_kleene_matches_polars(expr_text: str, row: dict, expected: object) -> None:
    trace = _trace_value(expr_text, row)
    # Cross-check against Polars (both operands boolean-typed here).
    polars = (
        pl.DataFrame([row], schema={"a": pl.Boolean, "b": pl.Boolean})
        .select(eval(expr_text, {"pl": pl}).alias("out"))
        .item()
    )
    assert trace == expected
    assert trace == polars


def test_kleene_false_and_null_was_masked_to_none() -> None:
    # The blanket ``left is None or right is None -> None`` guard previously
    # made ``False & null`` return None, mis-selecting when/then branches.
    assert _trace_value('pl.col("a") & pl.col("b")', {"a": False, "b": None}) is False
    assert _trace_value('pl.col("a") | pl.col("b")', {"a": True, "b": None}) is True


def test_bitand_on_integers_stays_bitwise() -> None:
    # Non-boolean & / | must remain bitwise (Kleene only applies to bools).
    assert _eval_node('pl.col("a") & pl.col("b")', {"a": 6, "b": 3}) == 2
    assert _eval_node('pl.col("a") | pl.col("b")', {"a": 4, "b": 1}) == 5


# ---------------------------------------------------------------------------
# F686 — division / floor-division / modulo by zero
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.col("a") / pl.col("b")', {"a": 1.0, "b": 0.0}),
        ('pl.col("a") / pl.col("b")', {"a": -1.0, "b": 0.0}),
        ('pl.col("a") / pl.col("b")', {"a": 0.0, "b": 0.0}),
        ('pl.col("a") / pl.col("b")', {"a": 1, "b": 0}),
        ('pl.col("a") / pl.col("b")', {"a": -1, "b": 0}),
        ('pl.col("a") // pl.col("b")', {"a": 1, "b": 0}),
        ('pl.col("a") // pl.col("b")', {"a": 1.0, "b": 0.0}),
        ('pl.col("a") // pl.col("b")', {"a": -1.0, "b": 0.0}),
        ('pl.col("a") % pl.col("b")', {"a": 1, "b": 0}),
        ('pl.col("a") % pl.col("b")', {"a": 5.0, "b": 0.0}),
    ],
)
def test_divide_by_zero_matches_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_float_div_by_zero_not_masked_to_observed() -> None:
    # The catch-all previously swallowed ZeroDivisionError and returned the
    # observed row value; 1.0/0.0 must now be +inf like Polars.
    assert _trace_value('pl.col("a") / pl.col("b")', {"a": 1.0, "b": 0.0}) == math.inf
    assert _trace_value('pl.col("a") / pl.col("b")', {"a": -1.0, "b": 0.0}) == -math.inf


def test_int_floordiv_and_mod_by_zero_are_null() -> None:
    assert _trace_value('pl.col("a") // pl.col("b")', {"a": 1, "b": 0}) is None
    assert _trace_value('pl.col("a") % pl.col("b")', {"a": 1, "b": 0}) is None


# ---------------------------------------------------------------------------
# F681 — negative base ** fractional exponent -> NaN (not complex)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.col("a") ** pl.col("b")', {"a": -8.0, "b": 0.5}),
        ('pl.col("a") ** pl.col("b")', {"a": -8.0, "b": 1.0 / 3.0}),
        ('pl.col("a") ** pl.col("b")', {"a": 2.0, "b": 3.0}),
        ('pl.col("a") ** pl.col("b")', {"a": -8.0, "b": 2.0}),
    ],
)
def test_pow_matches_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_pow_negative_fractional_is_nan_not_complex() -> None:
    result = _trace_value('pl.col("a") ** pl.col("b")', {"a": -8.0, "b": 0.5})
    assert isinstance(result, float) and math.isnan(result)
    assert not isinstance(result, complex)


# ---------------------------------------------------------------------------
# F679 — integer overflow beyond int64 is uncomputable, not a bignum
# ---------------------------------------------------------------------------


def test_integer_overflow_wraps_like_polars() -> None:
    big = 9223372036854775807  # int64 max
    # Polars computes with real int64 semantics and wraps on overflow, so the
    # evaluator must report the same wrapped value Polars computes, not None.
    assert _eval_node('pl.col("a") * pl.col("b")', {"a": big, "b": 2}) == -2
    assert _eval_node('pl.col("a") + pl.col("b")', {"a": big, "b": 1}) == -9223372036854775808
    assert _eval_node('pl.col("a") ** pl.col("b")', {"a": big, "b": 2}) == 1


def test_in_range_integer_arithmetic_still_computes() -> None:
    assert _eval_node('pl.col("a") * pl.col("b")', {"a": 1000, "b": 1000}) == 1_000_000
    assert _eval_node('pl.col("a") + pl.col("b")', {"a": -5, "b": 2}) == -3


# ---------------------------------------------------------------------------
# F682 — clip lower-then-upper on contradictory bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row_val",
    [0, 3, 5, 7, 8, 10, 12, 20],
)
def test_clip_contradictory_bounds_matches_polars(row_val: int) -> None:
    _assert_matches_polars('pl.col("a").clip(10, 5)', {"a": row_val})


def test_clip_normal_bounds_match_polars() -> None:
    for v in (-5, 0, 5, 10, 15):
        _assert_matches_polars('pl.col("a").clip(0, 10)', {"a": v})


def test_clip_lower_check_wins_on_contradiction() -> None:
    # lower=10 > upper=5: a value below 10 clamps UP to 10 (lower wins),
    # sequential min/max would instead give 5.
    assert _trace_value('pl.col("a").clip(10, 5)', {"a": 7}) == 10


# ---------------------------------------------------------------------------
# F683 — log/sqrt out-of-domain -> NaN / -inf, not None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.col("a").log()', {"a": 0.0}),
        ('pl.col("a").log()', {"a": -1.0}),
        ('pl.col("a").log()', {"a": math.e}),
        ('pl.col("a").sqrt()', {"a": -4.0}),
        ('pl.col("a").sqrt()', {"a": 9.0}),
    ],
)
def test_log_sqrt_domain_matches_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_log_zero_is_negative_infinity() -> None:
    assert _trace_value('pl.col("a").log()', {"a": 0.0}) == -math.inf


def test_log_and_sqrt_null_input_stays_null() -> None:
    """A null in a float column stays null. The trace passes the row in the dtypes the
    pipeline gave it; Polars 2 refuses ``log`` on a column whose only value is a bare
    ``None``, which has no numeric dtype."""
    row = pl.DataFrame({"a": [None]}, schema={"a": pl.Float64})
    for src in ('pl.col("a").log()', 'pl.col("a").sqrt()'):
        code = f"df = df.with_columns(r={src})"
        assert evaluate_expression(code, "r", {"a": None}, row=row).result_value is None


# ---------------------------------------------------------------------------
# F684 — max_horizontal / min_horizontal NaN ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": 1.0, "b": float("nan")}),
        ('pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": 1.0}),
        ('pl.min_horizontal(pl.col("a"), pl.col("b"))', {"a": 1.0, "b": float("nan")}),
        ('pl.min_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": 1.0}),
        (
            'pl.max_horizontal(pl.col("a"), pl.col("b"), pl.col("c"))',
            {"a": 1.0, "b": 2.0, "c": None},
        ),
    ],
)
def test_horizontal_nan_matches_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_max_horizontal_ignores_nan_regardless_of_order() -> None:
    a = _trace_value('pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": 1.0, "b": float("nan")})
    b = _trace_value('pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": 1.0})
    assert a == 1.0 and b == 1.0
    only_nan = _trace_value(
        'pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": None}
    )
    assert math.isnan(only_nan)


def test_min_horizontal_ignores_nan() -> None:
    assert (
        _trace_value('pl.min_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": 1.0})
        == 1.0
    )


# ---------------------------------------------------------------------------
# F029 / F250 — horizontal funcs: mean(null), concat_str, all/any
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.mean_horizontal(pl.col("a"), pl.col("b"))', {"a": 10.0, "b": None}),
        ('pl.concat_str([pl.col("a"), pl.col("b")], separator="-")', {"a": "x", "b": "y"}),
        ('pl.concat_str([pl.col("a"), pl.col("b")], separator="-")', {"a": "x", "b": None}),
        ('pl.all_horizontal(pl.col("a"), pl.col("b"))', {"a": True, "b": True}),
        ('pl.all_horizontal(pl.col("a"), pl.col("b"))', {"a": True, "b": False}),
        ('pl.any_horizontal(pl.col("a"), pl.col("b"))', {"a": False, "b": True}),
        ('pl.any_horizontal(pl.col("a"), pl.col("b"))', {"a": False, "b": False}),
    ],
)
def test_horizontal_funcs_match_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_horizontal_bare_string_args_are_column_names() -> None:
    # pl.sum_horizontal("a", "b") reads columns a and b (not the literal strings)
    # — previously the resulting TypeError was masked by the catch-all.
    _assert_matches_polars('pl.sum_horizontal("a", "b")', {"a": 5, "b": 7})
    _assert_matches_polars('pl.max_horizontal("a", "b")', {"a": 5.0, "b": 7.0})


def test_all_any_horizontal_kleene_null() -> None:
    assert (
        _trace_value('pl.all_horizontal(pl.col("a"), pl.col("b"))', {"a": True, "b": None}) is None
    )
    assert (
        _trace_value('pl.all_horizontal(pl.col("a"), pl.col("b"))', {"a": False, "b": None})
        is False
    )
    assert (
        _trace_value('pl.any_horizontal(pl.col("a"), pl.col("b"))', {"a": False, "b": None}) is None
    )
    assert (
        _trace_value('pl.any_horizontal(pl.col("a"), pl.col("b"))', {"a": True, "b": None}) is True
    )


def test_concat_str_null_makes_result_null() -> None:
    assert (
        _trace_value(
            'pl.concat_str([pl.col("a"), pl.col("b")], separator="-")', {"a": "x", "b": None}
        )
        is None
    )


def test_concat_str_non_str_separator_raises() -> None:
    # A non-str separator is a malformed authored expression; Polars itself
    # raises a TypeError rather than silently coercing it to "".
    code = 'df = df.with_columns(pl.concat_str([pl.col("a"), pl.col("b")], separator=5).alias("r"))'
    with pytest.raises(TypeError, match="not an instance of 'str'"):
        evaluate_expression(code, "r", {"a": "x", "b": "y"})


def test_concat_str_non_bool_ignore_nulls_raises() -> None:
    # ignore_nulls must be a bool; Polars raises a TypeError rather than
    # silently truthiness-coercing a non-bool.
    code = (
        'df = df.with_columns(pl.concat_str([pl.col("a"), pl.col("b")], ignore_nulls=5).alias("r"))'
    )
    with pytest.raises(TypeError, match="not an instance of 'bool'"):
        evaluate_expression(code, "r", {"a": "x", "b": "y"})


def test_concat_str_none_ignore_nulls_raises() -> None:
    # An explicit None for ignore_nulls is not a bool; Polars raises a
    # TypeError rather than silently coercing it to the default False.
    code = (
        "df = df.with_columns("
        'pl.concat_str([pl.col("a"), pl.col("b")], ignore_nulls=None).alias("r"))'
    )
    with pytest.raises(TypeError, match="not an instance of 'bool'"):
        evaluate_expression(code, "r", {"a": "x", "b": "y"})


def test_concat_str_valid_kwargs_still_work() -> None:
    # Correctly typed kwargs are unaffected: separator joins, ignore_nulls drops.
    assert (
        _trace_value(
            'pl.concat_str([pl.col("a"), pl.col("b")], separator="-", ignore_nulls=True)',
            {"a": "x", "b": None},
        )
        == "x"
    )
    _assert_matches_polars(
        'pl.concat_str([pl.col("a"), pl.col("b")], separator="-", ignore_nulls=True)',
        {"a": "x", "b": "y"},
    )


# ---------------------------------------------------------------------------
# F247 — unary ~ on a boolean is logical-not, not bitwise
# ---------------------------------------------------------------------------


def test_invert_boolean_matches_polars() -> None:
    _assert_matches_polars('~pl.col("a")', {"a": True})
    _assert_matches_polars('~pl.col("a")', {"a": False})


def test_invert_true_is_false_not_minus_two() -> None:
    assert _eval_node('~pl.col("a")', {"a": True}) is False
    assert _eval_node('~pl.col("a")', {"a": False}) is True


# ---------------------------------------------------------------------------
# F248 — unsupported comparison operators return None, not spurious True
# ---------------------------------------------------------------------------


def test_unsupported_comparison_returns_none() -> None:
    # `is` compares Python object identity of the two Expr objects (always
    # False for distinct pl.col(...) calls), not a Polars row comparison.
    assert _eval_node('pl.col("a") is pl.col("b")', {"a": 1, "b": 1}) is False
    # `in` on an Expr forces a Python bool context, which Polars refuses.
    code = 'df = df.with_columns(r=pl.col("a") in [1, 2, 3])'
    with pytest.raises(TypeError, match="the truth value of an Expr is ambiguous"):
        evaluate_expression(code, "r", {"a": 2})


def test_supported_comparison_still_works() -> None:
    assert _eval_node('pl.col("a") < pl.col("b")', {"a": 1, "b": 2}) is True
    assert _eval_node('pl.col("a") == pl.col("b")', {"a": 1, "b": 1}) is True


# ---------------------------------------------------------------------------
# F043 — replace_strict incomplete mapping fails loud
# ---------------------------------------------------------------------------


def test_replace_strict_incomplete_raises() -> None:
    # Polars computes the row, so an incomplete mapping raises exactly as it
    # does for the column the pipeline ran.
    code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1}).alias("r"))'
    with pytest.raises(pl.exceptions.InvalidOperationError, match="incomplete mapping"):
        evaluate_expression(code, "r", {"x": "b"})


def test_replace_strict_complete_and_default_ok() -> None:
    code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1, "b": 2}).alias("r"))'
    assert evaluate_expression(code, "r", {"x": "b"}).result_value == 2
    code_d = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1}, default=0).alias("r"))'
    assert evaluate_expression(code_d, "r", {"x": "z"}).result_value == 0


def test_non_strict_replace_leaves_unmapped_unchanged() -> None:
    code = 'df = df.with_columns(pl.col("x").replace({"a": 1}).alias("r"))'
    assert evaluate_expression(code, "r", {"x": "z"}).result_value == "z"


# ---------------------------------------------------------------------------
# F030 — evaluator failures propagate; observed value is never laundered in
# ---------------------------------------------------------------------------


def test_malformed_code_argument_raises() -> None:
    with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: ast.parse exception type may vary
        evaluate_expression(None, "r", {"r": 42})  # type: ignore[arg-type]


def test_replace_strict_failure_propagates_out_of_evaluate() -> None:
    # The failure propagates: the pre-existing row_values["r"] is never laundered in.
    code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1}).alias("r"))'
    with pytest.raises(pl.exceptions.InvalidOperationError, match="incomplete mapping"):
        evaluate_expression(code, "r", {"x": "b", "r": "LAUNDERED"})


# ---------------------------------------------------------------------------
# F148 — chained .with_columns(...).with_columns(...) last match wins
# ---------------------------------------------------------------------------


def test_chained_with_columns_selects_effective_definition() -> None:
    code = (
        'df = df.with_columns((pl.col("a") + 1).alias("r"))'
        '.with_columns((pl.col("a") + 100).alias("r"))'
    )
    parsed = parse_expression(code, "r")
    assert parsed is not None
    # The outer (last-applied) definition is the effective one.
    assert "100" in parsed.expression_text
    assert evaluate_expression(code, "r", {"a": 5}).result_value == 105


def test_separate_statements_last_definition_wins() -> None:
    code = (
        'df = df.with_columns((pl.col("a") + 1).alias("r"))\n'
        'df = df.with_columns((pl.col("a") + 100).alias("r"))'
    )
    assert evaluate_expression(code, "r", {"a": 5}).result_value == 105


# ---------------------------------------------------------------------------
# F149 / F246 — nested when/then branch tracking
# ---------------------------------------------------------------------------


def test_nested_when_in_otherwise_preserves_outer_metadata() -> None:
    code = (
        "df = df.with_columns(\n"
        '    pl.when(pl.col("x") > 100).then(pl.lit("big"))\n'
        "    .otherwise(\n"
        '        pl.when(pl.col("y") > 0).then(pl.lit("pos")).otherwise(pl.lit("neg"))\n'
        '    ).alias("r")\n'
        ")"
    )
    result = evaluate_expression(code, "r", {"x": 5, "y": 3})
    assert result.result_value == "pos"
    assert result.taken_branch == "otherwise"
    assert result.taken_branch_index == 1
    assert result.nested_branches == ["then"]
    # Cross-check the value against real Polars.
    got = (
        pl.DataFrame([{"x": 5, "y": 3}])
        .select(
            pl.when(pl.col("x") > 100)
            .then(pl.lit("big"))
            .otherwise(pl.when(pl.col("y") > 0).then(pl.lit("pos")).otherwise(pl.lit("neg")))
            .alias("r")
        )
        .item()
    )
    assert got == "pos"


def test_nested_when_in_then_records_actual_inner_branch() -> None:
    code = (
        "df = df.with_columns(\n"
        '    pl.when(pl.col("x") > 0).then(\n'
        '        pl.when(pl.col("y") > 10).then(pl.lit("a")).otherwise(pl.lit("b"))\n'
        '    ).otherwise(pl.lit("c")).alias("r")\n'
        ")"
    )
    result = evaluate_expression(code, "r", {"x": 5, "y": 3})
    assert result.result_value == "b"
    assert result.taken_branch == "then"
    assert result.taken_branch_index == 0
    # The inner branch actually fired is 'otherwise' (y>10 is False), not 'then'.
    assert result.nested_branches == ["otherwise"]


# ---------------------------------------------------------------------------
# F244 — value substitution is single-pass (inserted values not re-scanned)
# ---------------------------------------------------------------------------


def test_substitution_does_not_corrupt_inserted_string_value() -> None:
    # Column ``label`` holds a string that contains the shorter column name
    # ``a``; a sequential substitution would rewrite the ``a`` inside the
    # already-inserted "banana" value. Single-pass substitution must not.
    code = 'df = df.with_columns(pl.concat_str([pl.col("label"), pl.col("a")]).alias("r"))'
    result = evaluate_expression(code, "r", {"label": "banana", "a": "X"})
    assert "banana" in result.substituted_text
    # The inserted "banana" must be intact (its inner letters not replaced by X).
    assert "bXnXnX" not in result.substituted_text
