"""Comprehensive TDD test suite for the Polars expression parser.

This parser takes a code string (from a pipeline node's config["code"]) and a
target column name, then returns a structured ParsedExpression (and optionally
an EvaluatedExpression with substituted values) that can be rendered as a
human-readable formula.

These tests define the full specification for the parser.  The implementation
does not exist yet -- every test here should be written first, then made to
pass as the parser is built out.
"""

from __future__ import annotations

import math

import pytest

# ---------------------------------------------------------------------------
# The dataclasses under test (will live in haute._expression_parser)
# ---------------------------------------------------------------------------
from haute._expression_parser import (
    evaluate_expression,
    parse_expression,
)

# ###########################################################################
# A. Simple Arithmetic
# ###########################################################################


class TestArithmeticAddition:
    """col + col, col + const, const + col."""

    def test_column_plus_constant(self):
        code = 'df = df.with_columns((pl.col("base") + 100).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr.expression_text == "base + 100"
        assert expr.expression_type == "arithmetic"
        assert expr.referenced_columns == ["base"]
        assert expr.constants == [100]

    def test_column_plus_column(self):
        code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr.expression_text == "a + b"
        assert expr.expression_type == "arithmetic"
        assert set(expr.referenced_columns) == {"a", "b"}
        assert expr.constants == []

    def test_constant_plus_column(self):
        """Operand order must be preserved: 100 + col, not col + 100."""
        code = 'df = df.with_columns((100 + pl.col("base")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr.expression_text == "100 + base"
        assert expr.referenced_columns == ["base"]
        assert expr.constants == [100]


class TestArithmeticSubtraction:
    def test_column_minus_constant(self):
        code = 'df = df.with_columns((pl.col("gross") - 50).alias("net"))'
        expr = parse_expression(code, "net")
        assert expr.expression_text == "gross - 50"
        assert expr.expression_type == "arithmetic"
        assert expr.referenced_columns == ["gross"]
        assert expr.constants == [50]

    def test_column_minus_column(self):
        code = 'df = df.with_columns((pl.col("income") - pl.col("expenses")).alias("profit"))'
        expr = parse_expression(code, "profit")
        assert expr.expression_text == "income - expenses"
        assert set(expr.referenced_columns) == {"income", "expenses"}


class TestArithmeticMultiplication:
    def test_column_times_constant(self):
        code = 'df = df.with_columns((pl.col("premium") * 0.7).alias("burn_cost"))'
        expr = parse_expression(code, "burn_cost")
        assert expr.expression_text == "premium * 0.7"
        assert expr.expression_type == "arithmetic"
        assert expr.referenced_columns == ["premium"]
        assert expr.constants == [0.7]

    def test_column_times_column(self):
        code = 'df = df.with_columns((pl.col("rate") * pl.col("exposure")).alias("earned_premium"))'
        expr = parse_expression(code, "earned_premium")
        assert expr.expression_text == "rate * exposure"
        assert set(expr.referenced_columns) == {"rate", "exposure"}

    def test_constant_times_column(self):
        code = 'df = df.with_columns((1.1 * pl.col("base")).alias("loaded"))'
        expr = parse_expression(code, "loaded")
        assert expr.expression_text == "1.1 * base"


class TestArithmeticDivision:
    def test_true_division(self):
        code = 'df = df.with_columns((pl.col("claims") / pl.col("exposure")).alias("frequency"))'
        expr = parse_expression(code, "frequency")
        assert expr.expression_text == "claims / exposure"
        assert expr.expression_type == "arithmetic"

    def test_floor_division(self):
        code = 'df = df.with_columns((pl.col("age") // 5).alias("age_band"))'
        expr = parse_expression(code, "age_band")
        assert expr.expression_text == "age // 5"
        assert expr.expression_type == "arithmetic"

    def test_modulo(self):
        code = 'df = df.with_columns((pl.col("month") % 4).alias("quarter_offset"))'
        expr = parse_expression(code, "quarter_offset")
        assert expr.expression_text == "month % 4"
        assert expr.expression_type == "arithmetic"


class TestArithmeticExponentiation:
    def test_power_operator(self):
        code = 'df = df.with_columns((pl.col("x") ** 2).alias("x_squared"))'
        expr = parse_expression(code, "x_squared")
        assert expr.expression_text == "x ** 2"
        assert expr.expression_type == "arithmetic"
        assert expr.constants == [2]


class TestUnaryNegation:
    def test_negation_of_column(self):
        code = 'df = df.with_columns((-pl.col("loss")).alias("gain"))'
        expr = parse_expression(code, "gain")
        assert expr.expression_text == "-loss"
        assert expr.expression_type == "arithmetic"
        assert expr.referenced_columns == ["loss"]

    def test_negation_in_expression(self):
        code = 'df = df.with_columns((pl.col("a") + (-pl.col("b"))).alias("result"))'
        expr = parse_expression(code, "result")
        assert "a" in expr.referenced_columns
        assert "b" in expr.referenced_columns


class TestChainedOperations:
    def test_three_factor_multiplication(self):
        """a * b * c -- common in rating: base * factor1 * factor2."""
        code = 'df = df.with_columns((pl.col("base") * pl.col("age_factor") * pl.col("region_factor")).alias("rate"))'
        expr = parse_expression(code, "rate")
        assert expr.expression_text == "base * age_factor * region_factor"
        assert set(expr.referenced_columns) == {"base", "age_factor", "region_factor"}

    def test_four_factor_multiplication(self):
        code = 'df = df.with_columns((pl.col("base") * pl.col("f1") * pl.col("f2") * pl.col("f3")).alias("rate"))'
        expr = parse_expression(code, "rate")
        assert set(expr.referenced_columns) == {"base", "f1", "f2", "f3"}

    def test_eight_factor_actuarial_rating_chain(self):
        """Actuarial multiplicative rating: base * f1 * f2 * ... * f8."""
        factors = " * ".join(f'pl.col("f{i}")' for i in range(1, 9))
        code = f'df = df.with_columns(({factors}).alias("rate"))'
        expr = parse_expression(code, "rate")
        assert len(expr.referenced_columns) == 8
        expected = " * ".join(f"f{i}" for i in range(1, 9))
        assert expr.expression_text == expected

    def test_chained_same_operator_addition(self):
        code = 'df = df.with_columns((pl.col("a") + pl.col("b") + pl.col("c") + pl.col("d")).alias("total"))'
        expr = parse_expression(code, "total")
        assert expr.expression_text == "a + b + c + d"


class TestMixedOperatorsAndPrecedence:
    def test_add_then_multiply_natural_precedence(self):
        """a + b * c should be parsed as a + (b * c)."""
        code = 'df = df.with_columns((pl.col("a") + pl.col("b") * pl.col("c")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr.expression_text == "a + b * c"

    def test_parenthesized_override(self):
        """(a + b) * c -- parens change precedence."""
        code = 'df = df.with_columns(((pl.col("a") + pl.col("b")) * pl.col("c")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr.expression_text == "(a + b) * c"

    def test_complex_mixed_expression(self):
        """(a - b) / c + d * e."""
        code = 'df = df.with_columns(((pl.col("a") - pl.col("b")) / pl.col("c") + pl.col("d") * pl.col("e")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr.expression_text == "(a - b) / c + d * e"

    def test_nested_parentheses(self):
        code = 'df = df.with_columns(((pl.col("a") + pl.col("b")) * (pl.col("c") - pl.col("d"))).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr.expression_text == "(a + b) * (c - d)"


class TestLiteralExpression:
    def test_pl_lit_integer(self):
        code = 'df = df.with_columns(pl.lit(1).alias("one"))'
        expr = parse_expression(code, "one")
        assert expr.expression_text == "1"
        assert expr.constants == [1]
        assert expr.referenced_columns == []

    def test_pl_lit_float(self):
        code = 'df = df.with_columns(pl.lit(0.5).alias("half"))'
        expr = parse_expression(code, "half")
        assert expr.expression_text == "0.5"

    def test_pl_lit_string(self):
        code = """df = df.with_columns(pl.lit("default").alias("status"))"""
        expr = parse_expression(code, "status")
        assert expr.expression_text == '"default"'

    def test_pl_lit_none(self):
        code = 'df = df.with_columns(pl.lit(None).alias("empty"))'
        expr = parse_expression(code, "empty")
        assert expr.expression_text == "None"


# ###########################################################################
# B. when/then/otherwise Conditionals
# ###########################################################################


class TestWhenThenOtherwise:
    def test_simple_condition_gt(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("age") > 25)\n'
            '    .then(pl.col("base_rate"))\n'
            '    .otherwise(pl.col("young_rate"))\n'
            '    .alias("rate")\n'
            ")"
        )
        expr = parse_expression(code, "rate")
        assert expr.expression_type == "conditional"
        assert set(expr.referenced_columns) == {"age", "base_rate", "young_rate"}
        assert "age > 25" in expr.expression_text

    def test_simple_condition_lt(self):
        code = 'df = df.with_columns(pl.when(pl.col("x") < 0).then(0).otherwise(pl.col("x")).alias("clipped"))'
        expr = parse_expression(code, "clipped")
        assert expr.expression_type == "conditional"
        assert "x < 0" in expr.expression_text

    def test_condition_gte(self):
        code = 'df = df.with_columns(pl.when(pl.col("score") >= 80).then("pass").otherwise("fail").alias("grade"))'
        expr = parse_expression(code, "grade")
        assert "score >= 80" in expr.expression_text

    def test_condition_lte(self):
        code = 'df = df.with_columns(pl.when(pl.col("temp") <= 0).then("freeze").otherwise("normal").alias("state"))'
        expr = parse_expression(code, "state")
        assert "temp <= 0" in expr.expression_text

    def test_condition_eq(self):
        code = 'df = df.with_columns(pl.when(pl.col("status") == "active").then(1).otherwise(0).alias("is_active"))'
        expr = parse_expression(code, "is_active")
        assert "status == " in expr.expression_text

    def test_condition_ne(self):
        code = 'df = df.with_columns(pl.when(pl.col("type") != "excluded").then(pl.col("amount")).otherwise(0).alias("included_amount"))'
        expr = parse_expression(code, "included_amount")
        assert "type != " in expr.expression_text

    def test_chained_when_then(self):
        """Multiple when().then() before a final otherwise()."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("age") < 25).then("young")\n'
            '    .when(pl.col("age") < 65).then("adult")\n'
            '    .otherwise("senior")\n'
            '    .alias("age_group")\n'
            ")"
        )
        expr = parse_expression(code, "age_group")
        assert expr.expression_type == "conditional"
        assert "age < 25" in expr.expression_text
        assert "age < 65" in expr.expression_text

    def test_condition_with_and(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("age") > 18) & (pl.col("status") == "active"))\n'
            "    .then(1).otherwise(0)\n"
            '    .alias("eligible")\n'
            ")"
        )
        expr = parse_expression(code, "eligible")
        assert expr.expression_type == "conditional"
        assert "age" in expr.referenced_columns
        assert "status" in expr.referenced_columns

    def test_condition_with_or(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("type") == "A") | (pl.col("type") == "B"))\n'
            '    .then("included").otherwise("excluded")\n'
            '    .alias("bucket")\n'
            ")"
        )
        expr = parse_expression(code, "bucket")
        assert expr.expression_type == "conditional"

    def test_condition_with_not(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(~(pl.col("is_excluded")))\n'
            '    .then(pl.col("premium")).otherwise(0)\n'
            '    .alias("included_premium")\n'
            ")"
        )
        expr = parse_expression(code, "included_premium")
        assert expr.expression_type == "conditional"
        assert "is_excluded" in expr.referenced_columns

    def test_condition_is_null(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("value").is_null())\n'
            '    .then(0).otherwise(pl.col("value"))\n'
            '    .alias("value_filled")\n'
            ")"
        )
        expr = parse_expression(code, "value_filled")
        assert "is_null" in expr.expression_text.lower() or "value" in expr.referenced_columns

    def test_condition_is_not_null(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("opt").is_not_null())\n'
            '    .then(pl.col("opt")).otherwise(pl.col("default_val"))\n'
            '    .alias("resolved")\n'
            ")"
        )
        expr = parse_expression(code, "resolved")
        assert expr.expression_type == "conditional"

    def test_condition_is_in(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("region").is_in(["North", "South"]))\n'
            "    .then(1.2).otherwise(1.0)\n"
            '    .alias("region_factor")\n'
            ")"
        )
        expr = parse_expression(code, "region_factor")
        assert "region" in expr.referenced_columns

    def test_otherwise_none(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("valid") == True)\n'
            '    .then(pl.col("amount"))\n'
            "    .otherwise(None)\n"
            '    .alias("validated_amount")\n'
            ")"
        )
        expr = parse_expression(code, "validated_amount")
        assert "None" in expr.expression_text or "null" in expr.expression_text.lower()

    def test_otherwise_pl_lit_none(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("valid") == True)\n'
            '    .then(pl.col("amount"))\n'
            "    .otherwise(pl.lit(None))\n"
            '    .alias("validated_amount")\n'
            ")"
        )
        expr = parse_expression(code, "validated_amount")
        assert expr.expression_type == "conditional"

    def test_no_otherwise_implicit_null(self):
        """Missing .otherwise() means implicit null for non-matching rows."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("flag") == 1)\n'
            '    .then(pl.col("value"))\n'
            '    .alias("flagged_value")\n'
            ")"
        )
        expr = parse_expression(code, "flagged_value")
        assert expr.expression_type == "conditional"

    def test_nested_when_inside_then(self):
        """A when expression used as the then-value of another when."""
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
        expr = parse_expression(code, "discount")
        assert expr.expression_type == "conditional"
        assert len(expr.sub_expressions) > 0  # should have nested conditional

    def test_when_with_arithmetic_in_condition(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("claims") / pl.col("exposure") > 0.5)\n'
            '    .then("high").otherwise("low")\n'
            '    .alias("risk_band")\n'
            ")"
        )
        expr = parse_expression(code, "risk_band")
        assert set(expr.referenced_columns) >= {"claims", "exposure"}

    def test_when_with_arithmetic_in_then(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("type") == "A")\n'
            '    .then(pl.col("base") * 1.2)\n'
            '    .otherwise(pl.col("base") * 0.8)\n'
            '    .alias("adjusted")\n'
            ")"
        )
        expr = parse_expression(code, "adjusted")
        assert "base" in expr.referenced_columns


# ###########################################################################
# C. Horizontal Functions
# ###########################################################################


class TestHorizontalFunctions:
    def test_min_horizontal_two_args(self):
        code = 'df = df.with_columns(pl.min_horizontal(pl.col("a"), pl.col("b")).alias("min_ab"))'
        expr = parse_expression(code, "min_ab")
        assert expr.expression_type == "horizontal_func"
        assert expr.expression_text == "min_horizontal(a, b)"
        assert set(expr.referenced_columns) == {"a", "b"}

    def test_max_horizontal_two_args(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("a"), pl.col("b")).alias("max_ab"))'
        expr = parse_expression(code, "max_ab")
        assert expr.expression_type == "horizontal_func"
        assert expr.expression_text == "max_horizontal(a, b)"

    def test_sum_horizontal(self):
        code = 'df = df.with_columns(pl.sum_horizontal(pl.col("x"), pl.col("y"), pl.col("z")).alias("total"))'
        expr = parse_expression(code, "total")
        assert expr.expression_type == "horizontal_func"
        assert expr.expression_text == "sum_horizontal(x, y, z)"
        assert set(expr.referenced_columns) == {"x", "y", "z"}

    def test_min_horizontal_five_args(self):
        cols = ", ".join(f'pl.col("{c}")' for c in ["a", "b", "c", "d", "e"])
        code = f'df = df.with_columns(pl.min_horizontal({cols}).alias("min_all"))'
        expr = parse_expression(code, "min_all")
        assert len(expr.referenced_columns) == 5

    def test_horizontal_with_expression_arg(self):
        """pl.max_horizontal(pl.col("a") * 1.1, pl.col("b"))."""
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("a") * 1.1, pl.col("b")).alias("capped"))'
        expr = parse_expression(code, "capped")
        assert expr.expression_type == "horizontal_func"
        assert set(expr.referenced_columns) == {"a", "b"}
        assert "a * 1.1" in expr.expression_text

    def test_concat_str(self):
        code = 'df = df.with_columns(pl.concat_str(pl.col("first"), pl.col("last"), separator=" ").alias("full_name"))'
        expr = parse_expression(code, "full_name")
        assert expr.expression_type == "horizontal_func"
        assert set(expr.referenced_columns) == {"first", "last"}

    def test_horizontal_with_literal_arg(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("val"), pl.lit(0)).alias("floored"))'
        expr = parse_expression(code, "floored")
        assert expr.expression_type == "horizontal_func"
        assert "val" in expr.referenced_columns
        assert 0 in expr.constants


# ###########################################################################
# D. Column Accessors & Methods
# ###########################################################################


class TestCastMethod:
    def test_cast_float64(self):
        code = 'df = df.with_columns(pl.col("amount").cast(pl.Float64).alias("amount_f64"))'
        expr = parse_expression(code, "amount_f64")
        assert expr.referenced_columns == ["amount"]
        assert "cast" in expr.expression_text.lower() or "Float64" in expr.expression_text

    def test_cast_int32(self):
        code = 'df = df.with_columns(pl.col("count").cast(pl.Int32).alias("count_i32"))'
        expr = parse_expression(code, "count_i32")
        assert expr.referenced_columns == ["count"]

    def test_cast_utf8(self):
        code = 'df = df.with_columns(pl.col("code").cast(pl.Utf8).alias("code_str"))'
        expr = parse_expression(code, "code_str")
        assert expr.referenced_columns == ["code"]


class TestFillNullMethod:
    def test_fill_null_with_constant(self):
        code = 'df = df.with_columns(pl.col("val").fill_null(0).alias("val_filled"))'
        expr = parse_expression(code, "val_filled")
        assert expr.referenced_columns == ["val"]
        assert "fill_null" in expr.expression_text
        assert 0 in expr.constants

    def test_fill_null_with_column(self):
        code = 'df = df.with_columns(pl.col("primary").fill_null(pl.col("fallback")).alias("resolved"))'
        expr = parse_expression(code, "resolved")
        assert set(expr.referenced_columns) == {"primary", "fallback"}

    def test_fill_null_with_strategy(self):
        code = (
            'df = df.with_columns(pl.col("val").fill_null(strategy="forward").alias("val_ffill"))'
        )
        expr = parse_expression(code, "val_ffill")
        assert expr.referenced_columns == ["val"]
        assert "forward" in expr.expression_text


class TestFillNanMethod:
    def test_fill_nan_none(self):
        code = 'df = df.with_columns(pl.col("x").fill_nan(None).alias("x_clean"))'
        expr = parse_expression(code, "x_clean")
        assert expr.referenced_columns == ["x"]
        assert "fill_nan" in expr.expression_text


class TestNumericMethods:
    def test_round(self):
        code = 'df = df.with_columns(pl.col("price").round(2).alias("price_rounded"))'
        expr = parse_expression(code, "price_rounded")
        assert expr.referenced_columns == ["price"]
        assert "round" in expr.expression_text

    def test_abs(self):
        code = 'df = df.with_columns(pl.col("change").abs().alias("abs_change"))'
        expr = parse_expression(code, "abs_change")
        assert expr.referenced_columns == ["change"]
        assert "abs" in expr.expression_text

    def test_clip_lower_bound(self):
        code = 'df = df.with_columns(pl.col("val").clip(lower_bound=0).alias("clipped"))'
        expr = parse_expression(code, "clipped")
        assert expr.referenced_columns == ["val"]
        assert "clip" in expr.expression_text

    def test_clip_both_bounds(self):
        code = 'df = df.with_columns(pl.col("val").clip(lower_bound=0, upper_bound=100).alias("clipped"))'
        expr = parse_expression(code, "clipped")
        assert expr.referenced_columns == ["val"]

    def test_log(self):
        code = 'df = df.with_columns(pl.col("x").log().alias("log_x"))'
        expr = parse_expression(code, "log_x")
        assert "log" in expr.expression_text

    def test_sqrt(self):
        code = 'df = df.with_columns(pl.col("x").sqrt().alias("sqrt_x"))'
        expr = parse_expression(code, "sqrt_x")
        assert "sqrt" in expr.expression_text


class TestStringMethods:
    def test_str_to_lowercase(self):
        code = 'df = df.with_columns(pl.col("name").str.to_lowercase().alias("name_lower"))'
        expr = parse_expression(code, "name_lower")
        assert expr.referenced_columns == ["name"]
        assert "to_lowercase" in expr.expression_text

    def test_str_contains(self):
        code = 'df = df.with_columns(pl.col("desc").str.contains("fire").alias("has_fire"))'
        expr = parse_expression(code, "has_fire")
        assert expr.referenced_columns == ["desc"]
        assert "contains" in expr.expression_text

    def test_str_slice(self):
        code = 'df = df.with_columns(pl.col("postcode").str.slice(0, 3).alias("area_code"))'
        expr = parse_expression(code, "area_code")
        assert expr.referenced_columns == ["postcode"]
        assert "slice" in expr.expression_text

    def test_str_split(self):
        code = 'df = df.with_columns(pl.col("csv_field").str.split(",").alias("parts"))'
        expr = parse_expression(code, "parts")
        assert expr.referenced_columns == ["csv_field"]

    def test_str_replace(self):
        code = 'df = df.with_columns(pl.col("text").str.replace("old", "new").alias("text_clean"))'
        expr = parse_expression(code, "text_clean")
        assert expr.referenced_columns == ["text"]

    def test_str_lengths(self):
        code = 'df = df.with_columns(pl.col("name").str.len_chars().alias("name_len"))'
        expr = parse_expression(code, "name_len")
        assert expr.referenced_columns == ["name"]


class TestDatetimeMethods:
    def test_dt_year(self):
        code = 'df = df.with_columns(pl.col("inception_date").dt.year().alias("year"))'
        expr = parse_expression(code, "year")
        assert expr.referenced_columns == ["inception_date"]
        assert "year" in expr.expression_text

    def test_dt_month(self):
        code = 'df = df.with_columns(pl.col("inception_date").dt.month().alias("month"))'
        expr = parse_expression(code, "month")
        assert expr.referenced_columns == ["inception_date"]

    def test_dt_day(self):
        code = 'df = df.with_columns(pl.col("inception_date").dt.day().alias("day"))'
        expr = parse_expression(code, "day")
        assert expr.referenced_columns == ["inception_date"]

    def test_dt_total_days(self):
        code = 'df = df.with_columns((pl.col("end") - pl.col("start")).dt.total_days().alias("duration_days"))'
        expr = parse_expression(code, "duration_days")
        assert set(expr.referenced_columns) == {"end", "start"}


class TestListMethods:
    def test_list_len(self):
        code = 'df = df.with_columns(pl.col("items").list.len().alias("item_count"))'
        expr = parse_expression(code, "item_count")
        assert expr.referenced_columns == ["items"]

    def test_list_first(self):
        code = 'df = df.with_columns(pl.col("items").list.first().alias("first_item"))'
        expr = parse_expression(code, "first_item")
        assert expr.referenced_columns == ["items"]

    def test_list_contains(self):
        code = 'df = df.with_columns(pl.col("tags").list.contains("fire").alias("has_fire_tag"))'
        expr = parse_expression(code, "has_fire_tag")
        assert expr.referenced_columns == ["tags"]


class TestStructMethods:
    def test_struct_field(self):
        code = 'df = df.with_columns(pl.col("address").struct.field("city").alias("city"))'
        expr = parse_expression(code, "city")
        assert expr.referenced_columns == ["address"]
        assert "field" in expr.expression_text


class TestWindowFunctions:
    def test_over_single_partition(self):
        code = 'df = df.with_columns(pl.col("premium").sum().over("region").alias("region_total"))'
        expr = parse_expression(code, "region_total")
        assert expr.referenced_columns == ["premium"]
        assert "over" in expr.expression_text
        assert "region" in expr.expression_text

    def test_over_multiple_partitions(self):
        code = 'df = df.with_columns(pl.col("premium").mean().over("region", "year").alias("avg_prem"))'
        expr = parse_expression(code, "avg_prem")
        assert "premium" in expr.referenced_columns

    def test_over_with_expression(self):
        code = 'df = df.with_columns((pl.col("premium") / pl.col("premium").sum().over("region")).alias("prem_share"))'
        expr = parse_expression(code, "prem_share")
        assert "premium" in expr.referenced_columns


class TestShiftDiff:
    def test_shift(self):
        code = 'df = df.with_columns(pl.col("value").shift(1).alias("prev_value"))'
        expr = parse_expression(code, "prev_value")
        assert expr.referenced_columns == ["value"]
        assert "shift" in expr.expression_text

    def test_diff(self):
        code = 'df = df.with_columns(pl.col("value").diff().alias("value_change"))'
        expr = parse_expression(code, "value_change")
        assert expr.referenced_columns == ["value"]
        assert "diff" in expr.expression_text


class TestMethodChaining:
    def test_fill_null_then_cast(self):
        code = 'df = df.with_columns(pl.col("x").fill_null(0).cast(pl.Float64).alias("x_clean"))'
        expr = parse_expression(code, "x_clean")
        assert expr.referenced_columns == ["x"]
        assert "fill_null" in expr.expression_text
        assert "cast" in expr.expression_text.lower() or "Float64" in expr.expression_text

    def test_arithmetic_then_round(self):
        code = 'df = df.with_columns((pl.col("a") * pl.col("b")).round(2).alias("product"))'
        expr = parse_expression(code, "product")
        assert set(expr.referenced_columns) == {"a", "b"}
        assert "round" in expr.expression_text

    def test_fill_null_then_clip(self):
        code = 'df = df.with_columns(pl.col("val").fill_null(0).clip(lower_bound=0, upper_bound=1000).alias("cleaned"))'
        expr = parse_expression(code, "cleaned")
        assert expr.referenced_columns == ["val"]


# ###########################################################################
# E. .alias() Handling
# ###########################################################################


class TestAliasHandling:
    def test_explicit_alias(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("a_plus_one"))'
        expr = parse_expression(code, "a_plus_one")
        assert expr.target_column == "a_plus_one"
        assert expr.expression_text == "a + 1"

    def test_no_alias_auto_name(self):
        """When no alias is used, the column name defaults to the source column name."""
        code = 'df = df.with_columns(pl.col("a") + 1)'
        expr = parse_expression(code, "a")
        assert expr.target_column == "a"

    def test_alias_with_f_string(self):
        """Dynamic alias via f-string -- parser should handle or flag as opaque."""
        code = 'i = 3\ndf = df.with_columns((pl.col("a") * 2).alias(f"a_times_2_{i}"))'
        # f-string alias may not be resolvable statically -- parser should
        # still attempt to extract the expression if target is given
        expr = parse_expression(code, "a_times_2_3")
        # At minimum, expression_text and referenced_columns should work
        assert expr.referenced_columns == ["a"]

    def test_keyword_argument_in_with_columns(self):
        """with_columns(burn_cost=expr) is equivalent to expr.alias('burn_cost')."""
        code = 'df = df.with_columns(burn_cost=pl.col("premium") * 0.7)'
        expr = parse_expression(code, "burn_cost")
        assert expr.target_column == "burn_cost"
        assert expr.expression_text == "premium * 0.7"
        assert expr.referenced_columns == ["premium"]

    def test_multiple_keyword_arguments(self):
        code = (
            "df = df.with_columns(\n"
            '    net=pl.col("gross") - pl.col("tax"),\n'
            '    margin=pl.col("revenue") - pl.col("cost"),\n'
            ")"
        )
        expr = parse_expression(code, "net")
        assert expr.expression_text == "gross - tax"
        expr2 = parse_expression(code, "margin")
        assert expr2.expression_text == "revenue - cost"


# ###########################################################################
# F. Multi-Expression Code Blocks
# ###########################################################################


class TestMultiExpressionCodeBlocks:
    def test_single_with_columns_multiple_expressions_extract_target(self):
        code = (
            "df = df.with_columns(\n"
            '    (pl.col("a") * 2).alias("double_a"),\n'
            '    (pl.col("b") + 1).alias("b_plus_one"),\n'
            '    (pl.col("c") / 10).alias("c_tenth"),\n'
            ")"
        )
        expr = parse_expression(code, "b_plus_one")
        assert expr.expression_text == "b + 1"
        assert expr.referenced_columns == ["b"]

    def test_sequential_with_columns_dependency_tracking(self):
        """Second with_columns references a column created in the first."""
        code = (
            'df = df.with_columns((pl.col("base") * 1.1).alias("loaded"))\n'
            'df = df.with_columns((pl.col("loaded") * pl.col("factor")).alias("final_rate"))'
        )
        expr = parse_expression(code, "final_rate")
        assert expr.expression_text == "loaded * factor"
        assert set(expr.referenced_columns) == {"loaded", "factor"}

    def test_variable_assigned_then_used(self):
        code = 'expr = pl.col("a") * 2\ndf = df.with_columns(expr.alias("double_a"))'
        expr = parse_expression(code, "double_a")
        assert expr.expression_text == "a * 2"
        assert expr.referenced_columns == ["a"]

    def test_scalar_variable_used_in_expression(self):
        """A Python scalar used as a multiplier."""
        code = 'rate = 0.7\ndf = df.with_columns((pl.col("premium") * rate).alias("burn_cost"))'
        expr = parse_expression(code, "burn_cost")
        assert expr.expression_text == "premium * 0.7"
        assert expr.referenced_columns == ["premium"]
        assert 0.7 in expr.constants

    def test_expression_variable_reused_in_multiple_columns(self):
        code = (
            'base_expr = pl.col("base") * pl.col("inflation")\n'
            "df = df.with_columns(\n"
            '    (base_expr * pl.col("age_factor")).alias("rate_a"),\n'
            '    (base_expr * pl.col("region_factor")).alias("rate_b"),\n'
            ")"
        )
        expr_a = parse_expression(code, "rate_a")
        assert set(expr_a.referenced_columns) == {"base", "inflation", "age_factor"}
        expr_b = parse_expression(code, "rate_b")
        assert set(expr_b.referenced_columns) == {"base", "inflation", "region_factor"}

    def test_multiple_scalar_variables(self):
        code = (
            "load = 0.15\n"
            "tax_rate = 0.06\n"
            "df = df.with_columns(\n"
            '    (pl.col("net_premium") * (1 + load) * (1 + tax_rate)).alias("gross_premium")\n'
            ")"
        )
        expr = parse_expression(code, "gross_premium")
        assert "net_premium" in expr.referenced_columns

    def test_intermediate_column_chain(self):
        """Three sequential with_columns, each depending on the previous."""
        code = (
            'df = df.with_columns((pl.col("base") * 1.1).alias("step1"))\n'
            'df = df.with_columns((pl.col("step1") * pl.col("age_f")).alias("step2"))\n'
            'df = df.with_columns((pl.col("step2") + pl.col("expense")).alias("final"))'
        )
        expr = parse_expression(code, "final")
        assert expr.expression_text == "step2 + expense"

    def test_list_of_expressions_passed_to_with_columns(self):
        code = (
            "exprs = [\n"
            '    (pl.col("a") * 2).alias("double_a"),\n'
            '    (pl.col("b") + 1).alias("b_inc"),\n'
            "]\n"
            "df = df.with_columns(exprs)"
        )
        expr = parse_expression(code, "double_a")
        assert expr.expression_text == "a * 2"

    def test_unpacked_list_in_with_columns(self):
        code = (
            'exprs = [\n    (pl.col("a") * 2).alias("double_a"),\n]\ndf = df.with_columns(*exprs)'
        )
        expr = parse_expression(code, "double_a")
        assert expr.expression_text == "a * 2"


# ###########################################################################
# G. Complex / Opaque Patterns (graceful degradation)
# ###########################################################################


class TestOpaquePatterns:
    def test_map_elements_with_lambda(self):
        code = 'df = df.with_columns(pl.col("x").map_elements(lambda v: v ** 2, return_dtype=pl.Float64).alias("x_sq"))'
        expr = parse_expression(code, "x_sq")
        assert expr.expression_type == "opaque"
        assert expr.referenced_columns == ["x"]

    def test_pipe_call(self):
        code = "df = df.pipe(some_transform)"
        expr = parse_expression(code, "result")
        assert expr.expression_type == "opaque"

    def test_external_function_call(self):
        code = 'df = df.with_columns(years_between(pl.col("start"), pl.col("end")).alias("tenure"))'
        expr = parse_expression(code, "tenure")
        assert expr.expression_type in ("function_call", "opaque")
        assert set(expr.referenced_columns) == {"start", "end"}

    def test_for_loop_generating_columns(self):
        code = (
            'for col_name in ["a", "b", "c"]:\n'
            '    df = df.with_columns((pl.col(col_name) * 2).alias(f"{col_name}_doubled"))'
        )
        expr = parse_expression(code, "a_doubled")
        # Parser may mark this opaque or may resolve iteration; at minimum no crash
        assert expr is not None

    def test_if_else_in_code(self):
        code = (
            "if use_discount:\n"
            '    df = df.with_columns((pl.col("premium") * 0.9).alias("adjusted"))\n'
            "else:\n"
            '    df = df.with_columns(pl.col("premium").alias("adjusted"))'
        )
        expr = parse_expression(code, "adjusted")
        assert expr.expression_type == "opaque"

    def test_try_except_block(self):
        code = (
            "try:\n"
            '    df = df.with_columns((pl.col("x") / pl.col("y")).alias("ratio"))\n'
            "except Exception:\n"
            '    df = df.with_columns(pl.lit(0).alias("ratio"))'
        )
        expr = parse_expression(code, "ratio")
        assert expr.expression_type == "opaque"

    def test_list_comprehension_building_expressions(self):
        code = (
            'cols = ["a", "b", "c"]\n'
            'df = df.with_columns([(pl.col(c) * 2).alias(f"{c}_x2") for c in cols])'
        )
        expr = parse_expression(code, "a_x2")
        assert expr is not None  # should not crash

    def test_nested_function_definition(self):
        code = (
            "def calc(col_name):\n"
            '    return (pl.col(col_name) ** 2).alias(f"{col_name}_sq")\n'
            "\n"
            'df = df.with_columns(calc("x"))'
        )
        expr = parse_expression(code, "x_sq")
        assert expr.expression_type == "opaque"

    def test_apply_with_numpy(self):
        code = (
            "import numpy as np\n"
            'df = df.with_columns(pl.col("x").map_batches(lambda s: np.log1p(s.to_numpy())).alias("log1p_x"))'
        )
        expr = parse_expression(code, "log1p_x")
        assert expr.expression_type == "opaque"
        assert "x" in expr.referenced_columns


# ###########################################################################
# H. Edge Cases & Error Handling
# ###########################################################################


class TestEmptyAndMinimalInput:
    def test_empty_code_string(self):
        expr = parse_expression("", "result")
        assert expr is None or expr.expression_type == "opaque"

    def test_code_with_only_comments(self):
        code = "# This is a comment\n# Another comment\n"
        expr = parse_expression(code, "result")
        assert expr is None or expr.expression_type == "opaque"

    def test_code_with_only_whitespace(self):
        code = "   \n\n  \t  \n"
        expr = parse_expression(code, "result")
        assert expr is None or expr.expression_type == "opaque"

    def test_code_with_only_imports(self):
        code = "import polars as pl\nimport numpy as np\n"
        expr = parse_expression(code, "result")
        assert expr is None or expr.expression_type == "opaque"


class TestTargetColumnNotFound:
    def test_target_not_produced(self):
        code = 'df = df.with_columns((pl.col("a") * 2).alias("double_a"))'
        expr = parse_expression(code, "nonexistent_column")
        assert expr is None or expr.expression_type == "opaque"

    def test_code_with_no_with_columns(self):
        code = 'df = df.filter(pl.col("active") == True).sort("name")'
        expr = parse_expression(code, "active")
        assert expr is None or expr.expression_type == "opaque"


class TestSyntaxErrors:
    def test_code_with_syntax_error(self):
        code = 'df = df.with_columns((pl.col("a" * 2).alias("bad"))'
        # Should not raise; should return None or opaque
        expr = parse_expression(code, "bad")
        assert expr is None or expr.expression_type == "opaque"

    def test_incomplete_expression(self):
        code = 'df = df.with_columns((pl.col("a") +).alias("bad"))'
        expr = parse_expression(code, "bad")
        assert expr is None or expr.expression_type == "opaque"

    def test_unbalanced_parentheses(self):
        code = 'df = df.with_columns((pl.col("a") * 2.alias("bad"))'
        expr = parse_expression(code, "bad")
        assert expr is None or expr.expression_type == "opaque"


class TestSpecialColumnNames:
    def test_column_name_with_spaces(self):
        code = 'df = df.with_columns((pl.col("driver age") * 2).alias("double age"))'
        expr = parse_expression(code, "double age")
        assert expr.referenced_columns == ["driver age"]

    def test_column_name_with_special_characters(self):
        code = 'df = df.with_columns((pl.col("loss_ratio_%") * 100).alias("loss_pct"))'
        expr = parse_expression(code, "loss_pct")
        assert expr.referenced_columns == ["loss_ratio_%"]

    def test_column_name_is_python_keyword(self):
        code = 'df = df.with_columns((pl.col("class") + 1).alias("class_inc"))'
        expr = parse_expression(code, "class_inc")
        assert expr.referenced_columns == ["class"]

    def test_column_name_with_dots(self):
        code = 'df = df.with_columns((pl.col("address.city") + pl.lit("!")).alias("city_bang"))'
        expr = parse_expression(code, "city_bang")
        assert expr.referenced_columns == ["address.city"]

    def test_column_name_with_unicode(self):
        code = 'df = df.with_columns((pl.col("prämie") * 1.1).alias("loaded_prämie"))'
        expr = parse_expression(code, "loaded_prämie")
        assert expr.referenced_columns == ["prämie"]


class TestLongCode:
    def test_hundred_line_code_block(self):
        """Parser should handle very long code without crashing or timing out."""
        lines = ["import polars as pl"]
        for i in range(50):
            lines.append(f'df = df.with_columns((pl.col("col_{i}") * {i + 1}).alias("out_{i}"))')
        lines.append('df = df.with_columns((pl.col("out_49") + 1).alias("final"))')
        code = "\n".join(lines)
        expr = parse_expression(code, "final")
        assert expr is not None
        assert expr.expression_text == "out_49 + 1"


class TestMultipleDataframeVariables:
    def test_df_and_df2(self):
        code = (
            'df2 = df.with_columns((pl.col("a") * 2).alias("double_a"))\n'
            'df = df2.with_columns((pl.col("double_a") + 1).alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_text == "double_a + 1"

    def test_variable_reassignment(self):
        code = (
            'df = df.with_columns((pl.col("a") * 2).alias("step1"))\n'
            'df = df.with_columns((pl.col("step1") * 3).alias("step2"))\n'
            'df = df.with_columns((pl.col("step2") + 1).alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr.expression_text == "step2 + 1"


class TestSourceLineTracking:
    def test_source_line_single_line(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("b"))'
        expr = parse_expression(code, "b")
        assert expr.source_line == 1

    def test_source_line_multiline_code(self):
        code = '# comment\nimport polars as pl\ndf = df.with_columns((pl.col("a") + 1).alias("b"))'
        expr = parse_expression(code, "b")
        assert expr.source_line == 3

    def test_source_line_second_with_columns(self):
        code = (
            'df = df.with_columns((pl.col("a") * 2).alias("x"))\n'
            'df = df.with_columns((pl.col("x") + 1).alias("y"))'
        )
        expr = parse_expression(code, "y")
        assert expr.source_line == 2


# ###########################################################################
# I. Value Substitution (EvaluatedExpression)
# ###########################################################################


class TestEvaluatedExpressionFloats:
    def test_normal_float_substitution(self):
        code = 'df = df.with_columns((pl.col("premium") * 0.7).alias("burn_cost"))'
        result = evaluate_expression(code, "burn_cost", {"premium": 208.0})
        assert result.substituted_text == "208.0 * 0.7"
        assert result.result_value == pytest.approx(145.6)
        assert result.input_values == {"premium": 208.0}

    def test_two_column_float_substitution(self):
        code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("total"))'
        result = evaluate_expression(code, "total", {"a": 1.5, "b": 2.5})
        assert result.substituted_text == "1.5 + 2.5"
        assert result.result_value == pytest.approx(4.0)

    def test_division_float_result(self):
        code = 'df = df.with_columns((pl.col("claims") / pl.col("exposure")).alias("frequency"))'
        result = evaluate_expression(code, "frequency", {"claims": 10.0, "exposure": 3.0})
        assert result.result_value == pytest.approx(10.0 / 3.0)


class TestEvaluatedExpressionIntegers:
    def test_integer_substitution(self):
        code = 'df = df.with_columns((pl.col("count") * 2).alias("doubled"))'
        result = evaluate_expression(code, "doubled", {"count": 5})
        assert result.substituted_text == "5 * 2"
        assert result.result_value == 10

    def test_integer_floor_division(self):
        code = 'df = df.with_columns((pl.col("age") // 10).alias("decade"))'
        result = evaluate_expression(code, "decade", {"age": 37})
        assert result.result_value == 3


class TestEvaluatedExpressionStrings:
    def test_string_in_condition(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("status") == "active").then(1).otherwise(0)\n'
            '    .alias("is_active")\n'
            ")"
        )
        result = evaluate_expression(code, "is_active", {"status": "active"})
        assert result.result_value == 1
        assert result.input_values == {"status": "active"}


class TestEvaluatedExpressionNulls:
    def test_none_value_propagation(self):
        """NULL in arithmetic should propagate NULL."""
        code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("sum"))'
        result = evaluate_expression(code, "sum", {"a": 5.0, "b": None})
        assert result.result_value is None
        assert "None" in result.substituted_text or "NULL" in result.substituted_text

    def test_fill_null_with_none_input(self):
        code = 'df = df.with_columns(pl.col("val").fill_null(0).alias("val_filled"))'
        result = evaluate_expression(code, "val_filled", {"val": None})
        assert result.result_value == 0


class TestEvaluatedExpressionNaN:
    def test_nan_value_display(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("result"))'
        result = evaluate_expression(code, "result", {"a": float("nan")})
        assert math.isnan(result.result_value) or result.result_value is None
        assert "NaN" in result.substituted_text or "nan" in result.substituted_text


class TestEvaluatedExpressionInf:
    def test_positive_inf(self):
        code = 'df = df.with_columns((pl.col("x") * 2).alias("result"))'
        result = evaluate_expression(code, "result", {"x": float("inf")})
        assert result.result_value == float("inf") or result.result_value is None

    def test_negative_inf(self):
        code = 'df = df.with_columns((pl.col("x") + 1).alias("result"))'
        result = evaluate_expression(code, "result", {"x": float("-inf")})
        assert result.result_value == float("-inf") or result.result_value is None


class TestEvaluatedExpressionDates:
    def test_date_value_in_dt_year(self):
        import datetime

        code = 'df = df.with_columns(pl.col("inception").dt.year().alias("year"))'
        result = evaluate_expression(code, "year", {"inception": datetime.date(2025, 6, 15)})
        assert result.result_value == 2025


class TestEvaluatedExpressionBooleans:
    def test_boolean_in_condition(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("is_renewal")).then(0.9).otherwise(1.0)\n'
            '    .alias("renewal_factor")\n'
            ")"
        )
        result = evaluate_expression(code, "renewal_factor", {"is_renewal": True})
        assert result.result_value == pytest.approx(0.9)

    def test_boolean_false(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("is_renewal")).then(0.9).otherwise(1.0)\n'
            '    .alias("renewal_factor")\n'
            ")"
        )
        result = evaluate_expression(code, "renewal_factor", {"is_renewal": False})
        assert result.result_value == pytest.approx(1.0)


class TestEvaluatedExpressionExtremeNumbers:
    def test_very_large_number(self):
        code = 'df = df.with_columns((pl.col("x") * 2).alias("result"))'
        result = evaluate_expression(code, "result", {"x": 1e15})
        assert result.result_value == pytest.approx(2e15)

    def test_very_small_number(self):
        code = 'df = df.with_columns((pl.col("x") * 1000).alias("result"))'
        result = evaluate_expression(code, "result", {"x": 1e-10})
        assert result.result_value == pytest.approx(1e-7)

    def test_negative_value(self):
        code = 'df = df.with_columns((pl.col("x") * 2).alias("result"))'
        result = evaluate_expression(code, "result", {"x": -50.0})
        assert result.result_value == pytest.approx(-100.0)
        assert result.substituted_text == "-50.0 * 2"


class TestEvaluatedExpressionConditional:
    def test_when_then_substituted(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("age") > 25)\n'
            '    .then(pl.col("base_rate"))\n'
            '    .otherwise(pl.col("young_rate"))\n'
            '    .alias("rate")\n'
            ")"
        )
        result = evaluate_expression(
            code,
            "rate",
            {"age": 30, "base_rate": 100.0, "young_rate": 150.0},
        )
        assert result.result_value == pytest.approx(100.0)

    def test_when_then_false_branch(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("age") > 25)\n'
            '    .then(pl.col("base_rate"))\n'
            '    .otherwise(pl.col("young_rate"))\n'
            '    .alias("rate")\n'
            ")"
        )
        result = evaluate_expression(
            code,
            "rate",
            {"age": 20, "base_rate": 100.0, "young_rate": 150.0},
        )
        assert result.result_value == pytest.approx(150.0)


class TestEvaluatedExpressionHorizontal:
    def test_min_horizontal_substituted(self):
        code = 'df = df.with_columns(pl.min_horizontal(pl.col("a"), pl.col("b")).alias("min_ab"))'
        result = evaluate_expression(code, "min_ab", {"a": 10.0, "b": 5.0})
        assert result.result_value == pytest.approx(5.0)

    def test_max_horizontal_substituted(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("a"), pl.col("b")).alias("max_ab"))'
        result = evaluate_expression(code, "max_ab", {"a": 10.0, "b": 5.0})
        assert result.result_value == pytest.approx(10.0)


# ###########################################################################
# J. Additional Polars Patterns
# ###########################################################################


class TestPolarsSpecificPatterns:
    def test_pl_col_with_regex(self):
        """pl.col("^amount_.*$") selects multiple columns -- should be opaque or handled."""
        code = 'df = df.with_columns(pl.col("^amount_.*$") * 1.1)'
        expr = parse_expression(code, "amount_gross")
        # Regex col selection is inherently ambiguous; parser should handle gracefully
        assert expr is not None

    def test_pl_all(self):
        code = "df = df.with_columns(pl.all().cast(pl.Float64))"
        expr = parse_expression(code, "some_col")
        assert expr is not None

    def test_pl_exclude(self):
        code = 'df = df.with_columns(pl.exclude("id").cast(pl.Float64))'
        expr = parse_expression(code, "amount")
        assert expr is not None

    def test_pl_col_list(self):
        """pl.col(["a", "b"]) selects multiple columns."""
        code = 'df = df.with_columns(pl.col(["a", "b"]) * 2)'
        expr = parse_expression(code, "a")
        assert expr is not None

    def test_expression_with_name_method(self):
        """Using .name.suffix() or .name.prefix() to rename."""
        code = 'df = df.with_columns(pl.col("base").name.suffix("_loaded") * 1.1)'
        expr = parse_expression(code, "base_loaded")
        assert expr is not None

    def test_select_instead_of_with_columns(self):
        """df.select() narrows columns but expressions work the same way."""
        code = 'df = df.select((pl.col("a") * 2).alias("double_a"), pl.col("b"))'
        expr = parse_expression(code, "double_a")
        assert expr.expression_text == "a * 2"


class TestComplexActuarialExpressions:
    """Realistic actuarial pricing formulas that combine many patterns."""

    def test_multiplicative_rating_with_cap(self):
        code = (
            "df = df.with_columns(\n"
            "    pl.min_horizontal(\n"
            '        pl.col("base") * pl.col("age_f") * pl.col("region_f") * pl.col("ncd_f"),\n'
            "        pl.lit(5000)\n"
            '    ).alias("capped_premium")\n'
            ")"
        )
        expr = parse_expression(code, "capped_premium")
        assert expr.expression_type == "horizontal_func"
        assert set(expr.referenced_columns) == {"base", "age_f", "region_f", "ncd_f"}

    def test_burn_cost_with_ibnr_and_expense_load(self):
        code = (
            "ibnr_factor = 1.05\n"
            "expense_load = 0.15\n"
            "df = df.with_columns(\n"
            "    (\n"
            '        (pl.col("incurred_claims") * ibnr_factor / pl.col("exposure"))\n'
            "        * (1 + expense_load)\n"
            '    ).alias("burn_cost")\n'
            ")"
        )
        expr = parse_expression(code, "burn_cost")
        assert set(expr.referenced_columns) == {"incurred_claims", "exposure"}

    def test_loss_ratio_conditional_cap(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("earned_premium") > 0)\n'
            "    .then(\n"
            "        pl.min_horizontal(\n"
            '            pl.col("incurred_claims") / pl.col("earned_premium"),\n'
            "            pl.lit(5.0)\n"
            "        )\n"
            "    )\n"
            "    .otherwise(pl.lit(None))\n"
            '    .alias("capped_lr")\n'
            ")"
        )
        expr = parse_expression(code, "capped_lr")
        assert set(expr.referenced_columns) >= {"earned_premium", "incurred_claims"}

    def test_multiline_rating_formula(self):
        """A realistic multi-factor rating with variable assignments."""
        code = (
            'base = pl.col("technical_base")\n'
            'age_f = pl.col("age_factor")\n'
            'region_f = pl.col("region_factor")\n'
            'vehicle_f = pl.col("vehicle_factor")\n'
            'ncd_f = pl.col("ncd_factor")\n'
            "commission = 0.20\n"
            "expense = 0.10\n"
            "\n"
            "net_rate = base * age_f * region_f * vehicle_f * ncd_f\n"
            "df = df.with_columns(\n"
            '    (net_rate / (1 - commission - expense)).round(2).alias("gross_premium")\n'
            ")"
        )
        expr = parse_expression(code, "gross_premium")
        expected_cols = {
            "technical_base",
            "age_factor",
            "region_factor",
            "vehicle_factor",
            "ncd_factor",
        }
        assert set(expr.referenced_columns) == expected_cols


# ###########################################################################
# K. Structural / Dataclass Contract Tests
# ###########################################################################


class TestParsedExpressionStructure:
    """Ensure the returned dataclass has the correct shape regardless of content."""

    def test_all_fields_present(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("b"))'
        expr = parse_expression(code, "b")
        assert hasattr(expr, "target_column")
        assert hasattr(expr, "expression_text")
        assert hasattr(expr, "expression_type")
        assert hasattr(expr, "referenced_columns")
        assert hasattr(expr, "constants")
        assert hasattr(expr, "sub_expressions")
        assert hasattr(expr, "source_line")

    def test_referenced_columns_is_list(self):
        code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("c"))'
        expr = parse_expression(code, "c")
        assert isinstance(expr.referenced_columns, list)

    def test_constants_is_list(self):
        code = 'df = df.with_columns((pl.col("a") * 2).alias("b"))'
        expr = parse_expression(code, "b")
        assert isinstance(expr.constants, list)

    def test_sub_expressions_is_list(self):
        code = 'df = df.with_columns((pl.col("a") * 2).alias("b"))'
        expr = parse_expression(code, "b")
        assert isinstance(expr.sub_expressions, list)

    def test_expression_type_is_valid_string(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("b"))'
        expr = parse_expression(code, "b")
        valid_types = {"arithmetic", "conditional", "horizontal_func", "function_call", "opaque"}
        assert expr.expression_type in valid_types


class TestEvaluatedExpressionStructure:
    def test_all_fields_present(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("b"))'
        result = evaluate_expression(code, "b", {"a": 5.0})
        assert hasattr(result, "substituted_text")
        assert hasattr(result, "result_value")
        assert hasattr(result, "input_values")
        # Inherited from ParsedExpression
        assert hasattr(result, "target_column")
        assert hasattr(result, "expression_text")
        assert hasattr(result, "expression_type")
        assert hasattr(result, "referenced_columns")

    def test_input_values_dict(self):
        code = 'df = df.with_columns((pl.col("a") * 2).alias("b"))'
        result = evaluate_expression(code, "b", {"a": 3.0})
        assert isinstance(result.input_values, dict)
        assert result.input_values == {"a": 3.0}
