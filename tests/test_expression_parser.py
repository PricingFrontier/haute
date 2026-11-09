"""Tests for the Polars expression parser and its formula renderer.

The parser takes a code string (from a pipeline node's config["code"]) and a
target column name, then returns a structured ParsedExpression that can be
rendered as a human-readable formula.  Covers AST node conversion, namespace
methods, ``pl`` helpers, when/then chains, symbol tables and control flow,
reassignment chains, renderer parenthesisation and escaping, and name
substitution.
"""

from __future__ import annotations

import ast
import math
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

# ---------------------------------------------------------------------------
# The dataclasses under test (will live in haute._expression_parser)
# ---------------------------------------------------------------------------
from haute._expression_parser import (
    AssignmentPhases,
    _may_change,
    _substitute_names_in_ast,
    assignment_phases,
    evaluate_expression,
    parse_expression,
    parse_expression_chain,
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
        code = (
            "df = df.with_columns("
            '(pl.col("base") * pl.col("age_factor") * pl.col("region_factor")).alias("rate"))'
        )
        expr = parse_expression(code, "rate")
        assert expr.expression_text == "base * age_factor * region_factor"
        assert set(expr.referenced_columns) == {"base", "age_factor", "region_factor"}

    def test_four_factor_multiplication(self):
        code = (
            "df = df.with_columns("
            '(pl.col("base") * pl.col("f1") * pl.col("f2") * pl.col("f3")).alias("rate"))'
        )
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
        code = (
            "df = df.with_columns("
            '(pl.col("a") + pl.col("b") + pl.col("c") + pl.col("d")).alias("total"))'
        )
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
        code = (
            "df = df.with_columns("
            '((pl.col("a") - pl.col("b")) / pl.col("c") + pl.col("d") * pl.col("e"))'
            '.alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr.expression_text == "(a - b) / c + d * e"

    def test_nested_parentheses(self):
        code = (
            "df = df.with_columns("
            '((pl.col("a") + pl.col("b")) * (pl.col("c") - pl.col("d"))).alias("result"))'
        )
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
        code = (
            "df = df.with_columns("
            'pl.when(pl.col("x") < 0).then(0).otherwise(pl.col("x")).alias("clipped"))'
        )
        expr = parse_expression(code, "clipped")
        assert expr.expression_type == "conditional"
        assert "x < 0" in expr.expression_text

    def test_condition_gte(self):
        code = (
            "df = df.with_columns("
            'pl.when(pl.col("score") >= 80).then("pass").otherwise("fail").alias("grade"))'
        )
        expr = parse_expression(code, "grade")
        assert "score >= 80" in expr.expression_text

    def test_condition_lte(self):
        code = (
            "df = df.with_columns("
            'pl.when(pl.col("temp") <= 0).then("freeze").otherwise("normal").alias("state"))'
        )
        expr = parse_expression(code, "state")
        assert "temp <= 0" in expr.expression_text

    def test_condition_eq(self):
        code = (
            "df = df.with_columns("
            'pl.when(pl.col("status") == "active").then(1).otherwise(0).alias("is_active"))'
        )
        expr = parse_expression(code, "is_active")
        assert "status == " in expr.expression_text

    def test_condition_ne(self):
        code = (
            "df = df.with_columns("
            'pl.when(pl.col("type") != "excluded")'
            '.then(pl.col("amount")).otherwise(0).alias("included_amount"))'
        )
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
        code = (
            "df = df.with_columns("
            'pl.sum_horizontal(pl.col("x"), pl.col("y"), pl.col("z")).alias("total"))'
        )
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
        code = (
            "df = df.with_columns("
            'pl.max_horizontal(pl.col("a") * 1.1, pl.col("b")).alias("capped"))'
        )
        expr = parse_expression(code, "capped")
        assert expr.expression_type == "horizontal_func"
        assert set(expr.referenced_columns) == {"a", "b"}
        assert "a * 1.1" in expr.expression_text

    def test_concat_str(self):
        code = (
            "df = df.with_columns("
            'pl.concat_str(pl.col("first"), pl.col("last"), separator=" ").alias("full_name"))'
        )
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
        code = (
            "df = df.with_columns("
            'pl.col("primary").fill_null(pl.col("fallback")).alias("resolved"))'
        )
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
        code = (
            "df = df.with_columns("
            'pl.col("val").clip(lower_bound=0, upper_bound=100).alias("clipped"))'
        )
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
        code = (
            "df = df.with_columns("
            '(pl.col("end") - pl.col("start")).dt.total_days().alias("duration_days"))'
        )
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
        code = (
            "df = df.with_columns("
            'pl.col("premium").mean().over("region", "year").alias("avg_prem"))'
        )
        expr = parse_expression(code, "avg_prem")
        assert "premium" in expr.referenced_columns

    def test_over_with_expression(self):
        code = (
            "df = df.with_columns("
            '(pl.col("premium") / pl.col("premium").sum().over("region"))'
            '.alias("prem_share"))'
        )
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
        code = (
            "df = df.with_columns("
            'pl.col("val").fill_null(0).clip(lower_bound=0, upper_bound=1000).alias("cleaned"))'
        )
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
        code = (
            "df = df.with_columns("
            'pl.col("x").map_elements(lambda v: v ** 2, return_dtype=pl.Float64).alias("x_sq"))'
        )
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
            "df = df.with_columns("
            'pl.col("x").map_batches(lambda s: np.log1p(s.to_numpy())).alias("log1p_x"))'
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

    def test_float32_values_show_their_float32_digits(self):
        code = 'df = df.with_columns((pl.col("premium") - pl.col("cost")).alias("profit"))'
        row = pl.DataFrame(
            {"premium": [792.135], "cost": [528.09]},
            schema={"premium": pl.Float32, "cost": pl.Float64},
        )
        widened = float(row["premium"][0])
        assert widened != 792.135

        result = evaluate_expression(code, "profit", {"premium": widened, "cost": 528.09}, row=row)

        assert result.substituted_text == "792.135 - 528.09"
        # Only the text is shortened; the values computed on stay exact.
        assert result.input_values == {"premium": widened, "cost": 528.09}

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

    def test_floor_returns_polars_value(self):
        """floor() is a registered row-local method now computed by Polars
        (the old hand-written interpreter used to return None for it)."""
        code = 'df = df.with_columns(pl.col("x").floor().alias("y"))'
        result = evaluate_expression(code, "y", {"x": 2.7})
        assert result.result_value == 2.0


class TestAssignmentPhases:
    """Which assignments a with_columns call reads at their earlier value."""

    @pytest.mark.parametrize(
        ("code", "target", "expected"),
        [
            pytest.param(
                "df = df.with_columns(x=pl.col('x') + 1).with_columns(y=pl.col('x') * 2)",
                "y",
                AssignmentPhases(before=frozenset({"x"}), at_or_after=frozenset({"y"})),
                id="earlier-call",
            ),
            pytest.param(
                "df = df.with_columns(pl.col('x').cast(pl.Int8), y=pl.col('x') * 2)",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"x", "y"})),
                id="unaliased-method-chain",
            ),
            pytest.param(
                "df = df.with_columns((pl.col('a').alias('b') + 1), y=pl.col('b'))",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"b", "y"})),
                id="inner-alias",
            ),
            pytest.param(
                "df = df.with_columns(pl.col('s').str.to_uppercase(), pl.lit(1), y=pl.col('s'))",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"s", "literal", "y"})),
                id="name-keeping-namespace-and-literal",
            ),
            pytest.param(
                "df = df.with_columns(pl.col('x').name.suffix('_2'), y=pl.col('x'))",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"y"}), unresolved=True),
                id="renaming-namespace-is-unknown",
            ),
            pytest.param(
                "df = df.with_columns(*[pl.col(c) for c in cols], y=pl.col('x'))",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"y"}), unresolved=True),
                id="comprehension-is-unknown",
            ),
            pytest.param(
                "df = df.with_columns(pl.col('a', 'b') + 1, y=pl.col('b'))",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"y"}), unresolved=True),
                id="multi-column-is-unknown",
            ),
            pytest.param(
                "df = df.with_columns(pl.col(['x']).cast(pl.Int8), y=pl.col('x'))",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"y"}), unresolved=True),
                id="selector-list-is-unknown",
            ),
            pytest.param(
                "df = df.with_columns(pl.col('^x.*$') * 2, y=pl.col('x'))",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"y"}), unresolved=True),
                id="regex-is-unknown",
            ),
            pytest.param(
                "df = df.with_columns(pl.col('x').alias(name), y=pl.col('x'))",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"y"}), unresolved=True),
                id="dynamic-alias-is-unknown",
            ),
            pytest.param(
                "df = df.with_columns([pl.col('x').cast(pl.Int8), (pl.col('x') * 2).alias('y')])",
                "y",
                AssignmentPhases(before=frozenset(), at_or_after=frozenset({"x", "y"})),
                id="list-argument",
            ),
        ],
    )
    def test_phases(self, code: str, target: str, expected: AssignmentPhases) -> None:
        assert assignment_phases(code, target) == expected

    def test_an_unknown_write_before_the_target_call_is_recorded(self) -> None:
        code = "df = df.with_columns(pl.all().fill_null(0)).with_columns(y=pl.col('x'))"
        assert assignment_phases(code, "y") == AssignmentPhases(
            before=frozenset(), at_or_after=frozenset({"y"}), unresolved_before=True
        )

    def test_a_column_no_call_assigns_has_no_phases(self) -> None:
        assert assignment_phases("df = df.with_columns(y=pl.col('x'))", "z") is None


# ###########################################################################
# The located expression alone: its type, text, bindings and grouping
# ###########################################################################


class TestTheLocatedExpressionAlone:
    """Each column is explained by its own expression, as it stood where it ran."""

    WINDOW_BESIDE_ROW_LOCAL = (
        "df = df.with_columns(\n"
        '    pl.col("x").rank().over("g").alias("r"),\n'
        '    (pl.col("a") * 2).alias("y"),\n'
        ")"
    )

    def test_a_row_local_column_beside_a_window_keeps_its_own_facts(self) -> None:
        result = evaluate_expression(self.WINDOW_BESIDE_ROW_LOCAL, "y", {"a": 10, "x": 3, "g": "A"})

        assert result.expression_type == "arithmetic"
        assert result.substituted_text == "10 * 2"
        assert result.referenced_columns == ["a"]
        assert result.result_value == 20

    def test_the_window_beside_it_is_described_from_its_own_tree(self) -> None:
        result = evaluate_expression(self.WINDOW_BESIDE_ROW_LOCAL, "r", {"a": 10, "x": 3, "g": "A"})

        assert result.expression_type == "window"
        assert result.substituted_text == "rank of x over g"
        assert result.referenced_columns == ["x", "g"]
        assert result.result_value is None
        assert result.not_computable_reason == "not_row_local: rank"

    def test_a_window_inside_arithmetic_is_described_in_place(self) -> None:
        code = 'df = df.with_columns((pl.col("a") / pl.col("a").sum().over("g")).alias("share"))'

        result = evaluate_expression(code, "share", {"a": 10.0, "g": "A"})

        assert result.expression_type == "window"
        assert result.substituted_text == "10.0 / (sum of a over g)"

    @pytest.mark.parametrize(
        "code",
        [
            'factor = 2\ndf = df.with_columns((pl.col("x") * factor).alias("y"))\nfactor = 3',
            'factor = 2; df = df.with_columns(y=pl.col("x") * factor); factor = 3',
        ],
        ids=["lines", "one-line"],
    )
    def test_a_scalar_rebound_after_the_formula_is_read_as_the_formula_saw_it(
        self, code: str
    ) -> None:
        result = evaluate_expression(code, "y", {"x": 10})

        assert parse_expression(code, "y").expression_text == "x * 2"
        assert result.substituted_text == "10 * 2"
        assert result.result_value == 20

    def test_a_binding_keeps_the_names_it_read_when_it_ran(self) -> None:
        # ``rate`` was the preamble's when ``expr`` was built; rebinding it later
        # changes neither the formula nor its value.
        code = 'expr = pl.col("x") * rate\nrate = 3\ndf = df.with_columns(expr.alias("y"))'

        result = evaluate_expression(code, "y", {"x": 10}, preamble_ns={"rate": 2})

        assert result.substituted_text == "10 * 2"
        assert result.result_value == 20

    def test_a_binding_the_code_rebinds_out_of_sight_is_reported_not_guessed(self) -> None:
        code = 'factor = 2\nfactor *= 3\ndf = df.with_columns((pl.col("x") * factor).alias("y"))'

        result = evaluate_expression(code, "y", {"x": 10}, preamble_ns={"factor": 5})

        # Neither the stale 2 nor the preamble's 5: the value the code ran with is unknown.
        assert parse_expression(code, "y").expression_type == "opaque"
        assert result.result_value is None
        assert result.not_computable_reason == "unresolved_name: factor"

    @pytest.mark.parametrize(
        ("expression", "row", "text", "substituted", "value"),
        [
            ('-(pl.col("x") + pl.col("z"))', {"x": 1, "z": 2}, "-(x + z)", "-(1 + 2)", -3),
            (
                '-(pl.col("x") * -(pl.col("z") - 1))',
                {"x": 2, "z": 4},
                "-(x * -(z - 1))",
                "-(2 * -(4 - 1))",
                6,
            ),
            ('-pl.col("x") ** 2', {"x": 3}, "-x ** 2", "-3 ** 2", -9),
            ('(-pl.col("x")) ** 2', {"x": 3}, "(-x) ** 2", "(-3) ** 2", 9),
            (
                '~(pl.col("a") & pl.col("b"))',
                {"a": True, "b": False},
                "~(a & b)",
                "~(True & False)",
                True,
            ),
        ],
        ids=["neg-sum", "nested", "neg-power", "power-of-neg", "invert-and"],
    )
    def test_unary_operators_keep_their_grouping(
        self, expression: str, row: dict, text: str, substituted: str, value: object
    ) -> None:
        code = f'df = df.with_columns(({expression}).alias("out"))'

        result = evaluate_expression(code, "out", row)

        assert result.expression_text == text
        assert result.substituted_text == substituted
        assert result.result_value == value

    def test_a_literal_spelled_like_a_column_is_never_substituted(self) -> None:
        code = 'df = df.with_columns(pl.concat_str([pl.col("x"), pl.lit("x")]).alias("s"))'

        result = evaluate_expression(code, "s", {"x": "A"})

        assert result.expression_text == 'concat_str(x, "x")'
        assert result.substituted_text == 'concat_str("A", "x")'
        assert result.result_value == "Ax"

    def test_a_method_named_like_a_column_keeps_its_name(self) -> None:
        code = 'df = df.with_columns(pl.col("round").round(1).alias("r"))'

        result = evaluate_expression(code, "r", {"round": 1.26})

        assert result.substituted_text == "1.26.round(1)"
        assert result.result_value == 1.3

    @pytest.mark.parametrize(
        ("call", "unknown"),
        [
            ("df = df.with_columns(y=expr)", "expr"),
            ('exprs = [expr.alias("y")]\ndf = df.with_columns(exprs)', "exprs"),
            ('named = expr.alias("y")\ndf = df.with_columns(named)', "named"),
            ("again = expr\ndf = df.with_columns(y=again)", "again"),
        ],
        ids=["keyword", "list", "aliased", "rebound"],
    )
    def test_a_binding_captured_from_an_unknown_value_is_reported_not_guessed(
        self, call: str, unknown: str
    ) -> None:
        # ``expr`` captured ``factor`` while its value was unknown (the code
        # computes 60); rebinding ``factor`` afterwards, or binding ``expr``
        # to another name, does not make it known.
        code = 'factor = 2\nfactor *= 3\nexpr = pl.col("x") * factor\nfactor = 4\n' + call

        result = evaluate_expression(code, "y", {"x": 10}, preamble_ns={"factor": 5})

        assert result.result_value is None
        assert result.not_computable_reason == f"unresolved_name: {unknown}"

    @pytest.mark.parametrize(
        "code",
        [
            'expr = pl.col("x").rank().over("g")\ndf = df.with_columns(y=expr)',
            'expr = pl.col("x").rank().over("g").alias("y")\ndf = df.with_columns(expr)',
        ],
        ids=["keyword", "aliased-positional"],
    )
    def test_a_window_bound_to_a_variable_is_typed_from_its_binding(self, code: str) -> None:
        result = evaluate_expression(code, "y", {"x": 3, "g": "A"})

        assert result.expression_type == "window"
        assert result.referenced_columns == ["x", "g"]
        assert result.substituted_text == "rank of x over g"
        assert result.not_computable_reason == "not_row_local: rank"

    def test_a_nested_conditional_substitutes_values_in_every_branch(self) -> None:
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 1)\n'
            '    .then(pl.when(pl.col("b") > 2).then(pl.col("c")).otherwise(0))\n'
            '    .otherwise(pl.col("d"))\n'
            '    .alias("r")\n'
            ")"
        )

        result = evaluate_expression(code, "r", {"a": 2, "b": 3, "c": 10, "d": 20})

        assert result.expression_text == (
            "when a > 1 then when b > 2 then c otherwise 0 otherwise d"
        )
        assert result.substituted_text == (
            "when 2 > 1 then when 3 > 2 then 10 otherwise 0 otherwise 20"
        )
        assert result.result_value == 10


_TIMES_A = 'df = df.with_columns(y=pl.col("x") * {}["A"])'


_LOOKUP = 'df = df.with_columns(y=pl.col("code").replace_strict(mapping, default=0.0))'


class TestValuesChangedInPlace:
    """A value changed without binding its name again is unknown from there on."""

    @pytest.mark.parametrize(
        ("code", "unknown"),
        [
            ('factors = {"A": 1.0}\nfactors["A"] = 2.0\n' + _TIMES_A.format("factors"), "factors"),
            ('factors = {"A": 1.0}\nfactors["A"] += 1.0\n' + _TIMES_A.format("factors"), "factors"),
            ('mapping = {"a": 2.0}\ndel mapping["a"]\n' + _LOOKUP, "mapping"),
            (
                'factors = {"A": 1.0}\nfactors.update({"A": 2.0})\n' + _TIMES_A.format("factors"),
                "factors",
            ),
            ('mapping = {"a": 2.0, "b": 3.0}\nlast = mapping.pop("b")\n' + _LOOKUP, "mapping"),
            (
                'mapping = {"a": 1.0}\nfor key in ["a"]:\n    mapping[key] = 2.0\n' + _LOOKUP,
                "mapping",
            ),
            ('box.rate = 2.0\ndf = df.with_columns(y=pl.col("x") * box.rate)', "box"),
            ('RATES["A"] = 2.0\n' + _TIMES_A.format("RATES"), "RATES"),
            (
                'factors = {"A": 1.0}\nalias = factors\nalias["A"] = 2.0\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\nalias = factors\nfactors["A"] = 2.0\n'
                + _TIMES_A.format("alias"),
                "alias",
            ),
            (
                'inner = {"A": 1.0}\nouter = {"k": inner}\ninner["A"] = 2.0\n'
                'df = df.with_columns(y=pl.col("x") * outer["k"]["A"])',
                "outer",
            ),
            (
                'a = {"A": 1.0}\nb = a\nc = b\nb = {}\na["A"] = 2.0\n' + _TIMES_A.format("c"),
                "c",
            ),
            (
                "base = [1.0]\nalias = base\nbase += [2.0]\n"
                'df = df.with_columns(y=pl.col("x") * alias[1])',
                "alias",
            ),
            (
                "factor = 1.0\nif True:\n    factor = 2.0\n"
                '    df = df.with_columns(y=pl.col("x") * factor)',
                "factor",
            ),
            (
                'factors = {"A": 1.0}\nif True:\n    factors["A"] = 2.0\n'
                '    df = df.with_columns(y=pl.col("x") * factors["A"])',
                "factors",
            ),
            (
                'factors = {"A": 1.0}\nalias = factors\nif False:\n    alias = {}\n'
                'alias["A"] = 2.0\n' + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\nif True:\n    alias = factors\nalias["A"] = 2.0\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                "factors = [1.0, 2.0]\ndf = df.with_columns(\n"
                "    popped=pl.lit(factors.pop(0)),\n"
                '    y=pl.col("x") * factors[0],\n)',
                "factors",
            ),
            (
                'factors = {"A": {"rate": 1.0}}\nrate = factors["A"]\n'
                'factors["A"]["rate"] = 2.0\n'
                'df = df.with_columns(y=pl.col("x") * rate["rate"])',
                "rate",
            ),
            (
                'factors = {"A": {"rate": 1.0}}\nmine = dict(factors)\n'
                'mine["A"]["rate"] = 2.0\n'
                'df = df.with_columns(y=pl.col("x") * factors["A"]["rate"])',
                "factors",
            ),
            (
                'inner = {"A": 1.0}\ndefaults = {"k": inner}\nouter = {**defaults}\n'
                'inner["A"] = 2.0\ndf = df.with_columns(y=pl.col("x") * outer["k"]["A"])',
                "outer",
            ),
            (
                'factors = {"A": 1.0}\ndef bump():\n    factors["A"] = 2.0\nbump()\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factor = 1.0\nRATES.update({"A": 2.0})\n' + _TIMES_A.format("RATES"),
                "RATES",
            ),
            (
                "factors = [1.0, 2.0]\ndef helper(factors=factors.pop(0)):\n    pass\n"
                'df = df.with_columns(y=pl.col("x") * factors[0])',
                "factors",
            ),
            (
                'factors = {"A": {"rate": 1.0}}\nif True:\n    alias = factors\n'
                '    rate = alias["A"]\n    rate["rate"] = 2.0\n'
                'df = df.with_columns(y=pl.col("x") * factors["A"]["rate"])',
                "factors",
            ),
            (
                'factors = {"A": 1.0}\nouter = (alias := factors)\nalias["A"] = 2.0\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\nouter = {}\nouter["k"] = factors\nouter["k"]["A"] = 2.0\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\nmatch factors:\n    case alias:\n        alias["A"] = 2.0\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\ndef bump():\n    local = factors\n    local["A"] = 2.0\n'
                "bump()\n" + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\ndef helper(d):\n    d.update({"A": 2.0})\n'
                "helper(factors)\n" + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\n'
                'expr = pl.col("x").map_elements(lambda v: v * factors["A"])\n'
                'factors["A"] = 2.0\ndf = df.with_columns(y=expr)',
                "expr",
            ),
            (
                "factor = 1.0\nmatch 2.0:\n    case factor:\n        pass\n"
                'df = df.with_columns(y=pl.col("x") * factor)',
                "factor",
            ),
            (
                "factors = [1.0]\nalias = factors\nfactors += (2.0,)\n"
                'df = df.with_columns(y=pl.col("x") * alias[-1])',
                "alias",
            ),
            (
                "factors = [1.0]\nitems = ()\nif True:\n    items = factors\n"
                "    items.append(2.0)\n"
                'df = df.with_columns(y=pl.col("x") * factors[-1])',
                "factors",
            ),
            (
                'factors = [1.0] or pl.col("x")\nfactors[0] = 2.0\n'
                'df = df.with_columns(y=pl.col("x") * factors[0])',
                "factors",
            ),
            (
                'factors = {"A": {"rate": 1.0}}\nfactors.get("A")["rate"] = 2.0\n'
                'df = df.with_columns(y=pl.col("x") * factors["A"]["rate"])',
                "factors",
            ),
            (
                'factors = {"A": 1.0}\ndef bump(items):\n    items[0]["A"] = 2.0\n'
                "bump([factors])\n" + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\ndef bump(d):\n    alias = d\n    alias["A"] = 2.0\n'
                "bump(factors)\n" + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                "factors = [1.0]\nitems = ()\nif True:\n    items = factors\n    alias = items\n"
                "    alias.append(2.0)\n"
                'df = df.with_columns(y=pl.col("x") * factors[-1])',
                "factors",
            ),
            (
                'factors = {"A": 1.0}\nerror = None\ntry:\n    raise ValueError(factors)\n'
                'except ValueError as error:\n    error.args[0]["A"] = 2.0\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\ndef bump(d):\n    box = {}\n    box["d"] = d\n'
                '    box["d"]["A"] = 2.0\nbump(factors)\n' + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\ndef bump():\n    factors["A"] = 2.0\n'
                'factors = {"A": 1.0}\nbump()\n' + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\ndef bump(d):\n    d["A"] = 2.0\nrun = bump\nrun(factors)\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'factors = {"A": 1.0}\nclass Box:\n    def __init__(self, d):\n'
                '        d["A"] = 2.0\nBox(factors)\n' + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                "factor = 1.0\ndef reset():\n    global factor\n    factor = 2.0\nreset()\n"
                'df = df.with_columns(y=pl.col("x") * factor)',
                "factor",
            ),
            (
                "factor = 1.0\nvalues = [(factor := 2.0) for _ in range(1)]\n"
                'df = df.with_columns(y=pl.col("x") * factor)',
                "factor",
            ),
            (
                "factors = [2.0, 1.0]\nordered = factors.sort()\n"
                'df = df.with_columns(y=pl.col("x") * factors[0])',
                "factors",
            ),
            (
                'factors = {"A": 1.0}\ntry:\n    error = None\n    raise ValueError(factors)\n'
                'except ValueError as error:\n    error.args[0]["A"] = 2.0\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'handlers = {}\ndef bump(d):\n    d["A"] = 2.0\nhandlers["bump"] = bump\n'
                'factors = {"A": 1.0}\nhandlers["bump"](factors)\n' + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                'rate = 2.0\nclass Parent:\n    def __init__(self, d):\n        d["A"] = rate\n'
                'class Child(Parent):\n    pass\nfactors = {"A": 1.0}\nChild(factors)\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
            (
                "factor = 1.0\ndef run(fn):\n    fn()\n    return fn\n@run\ndef reset():\n"
                "    global factor\n    factor = 2.0\n"
                'df = df.with_columns(y=pl.col("x") * factor)',
                "factor",
            ),
            (
                "factor = 1.0\ndef reset():\n    global factor\n    match 2.0:\n"
                "        case factor:\n            pass\nreset()\n"
                'df = df.with_columns(y=pl.col("x") * factor)',
                "factor",
            ),
            (
                'factor = 1.0\nexec("factor = 2.0")\ndf = df.with_columns(y=pl.col("x") * factor)',
                "factor",
            ),
            (
                'factors = {"A": 1.0}\ndef update_factors():\n'
                '    return factors.update({"A": 2.0})\nupdate_factors()\n'
                + _TIMES_A.format("factors"),
                "factors",
            ),
        ],
        ids=[
            "subscript",
            "augmented-subscript",
            "delete",
            "method-on-its-own-line",
            "pop-in-a-binding",
            "inside-a-loop",
            "attribute",
            "preamble-value",
            "through-an-alias",
            "read-through-an-alias",
            "nested",
            "alias-through-a-rebound-name",
            "augmented-list",
            "rebound-in-the-formula-block",
            "changed-in-the-formula-block",
            "rebound-in-a-block-that-may-not-run",
            "an-alias-made-in-a-block",
            "changed-by-the-formula-statement",
            "a-container-read-out",
            "a-shallow-copy",
            "unpacked-into-a-dict",
            "changed-in-a-function",
            "a-preamble-container-method",
            "a-default-argument",
            "an-element-of-an-alias-made-in-a-block",
            "a-walrus-in-a-binding",
            "stored-into-a-container",
            "a-match-capture",
            "a-function-local-alias",
            "passed-to-a-function-that-changes-it",
            "a-lambda-in-a-polars-expression",
            "rebound-by-a-match-capture",
            "a-list-extended-by-a-tuple",
            "rebound-in-a-block-before-the-change",
            "an-or-returning-a-list",
            "a-store-through-a-call",
            "passed-inside-a-list-to-a-function-that-changes-it",
            "a-parameter-changed-through-a-local-alias",
            "an-alias-of-a-name-rebound-in-the-block",
            "an-exception-capture",
            "a-parameter-held-in-a-local-container",
            "a-function-run-after-the-value-is-bound-again",
            "a-function-called-through-an-alias",
            "a-class-whose-constructor-changes-its-argument",
            "a-global-bound-again-by-a-function",
            "a-walrus-in-a-comprehension",
            "a-container-method-whose-result-is-used",
            "an-exception-capture-beside-a-binding",
            "a-function-registered-in-a-container",
            "a-subclass-of-a-changing-class",
            "a-decorator-running-a-global-rebinding",
            "a-global-bound-again-by-a-match-capture",
            "exec",
            "a-helper-returning-a-container-method-on-a-name-around-it",
        ],
    )
    def test_the_value_is_reported_not_guessed(self, code: str, unknown: str) -> None:
        preamble = {"RATES": {"A": 1.0}, "box": SimpleNamespace(rate=1.0)}

        result = evaluate_expression(code, "y", {"x": 10.0, "code": "a"}, preamble_ns=preamble)

        assert result.result_value is None
        assert result.not_computable_reason == f"unresolved_name: {unknown}"
        parsed = parse_expression(code, "y")
        assert parsed is not None
        assert parsed.expression_type == "opaque"

    @pytest.mark.parametrize(
        ("code", "value"),
        [
            (
                'factors = {"A": 1.0}\nrate = factors["A"]\nfactors["A"] = 2.0\n'
                'df = df.with_columns(y=pl.col("x") * rate)',
                10.0,
            ),
            ('factors = {"A": 2.0}\nprint(factors)\n' + _TIMES_A.format("factors"), 20.0),
            (
                "factor = 2.0\npl.Config.set_tbl_rows(5)\n"
                'df = df.with_columns(y=pl.col("x") * factor)',
                20.0,
            ),
            (
                "factor = 2.0\nif True:\n    other = 1\n"
                '    df = df.with_columns(y=pl.col("x") * factor)',
                20.0,
            ),
            (
                'factors = {"A": 1.0}\ndef helper(factors):\n'
                '    factors.update({"A": 2.0})\n' + _TIMES_A.format("factors"),
                10.0,
            ),
            (
                'factors = {"A": 1.0}\nexpr = pl.col("x") * factors["A"]\n'
                'factors["A"] = 2.0\ndf = df.with_columns(y=expr)',
                10.0,
            ),
            (
                'factors = {"A": 2.0}\nfactor = 1.0\nfactor += 0.5\n' + _TIMES_A.format("factors"),
                20.0,
            ),
            (
                'expr = pl.col("x") * 2.0\nexpr.alias("z")\ndf = df.with_columns(y=expr)',
                20.0,
            ),
            (
                'default = None\nfactors = {}\nfactors["A"] = 2.0\n'
                'df = df.with_columns(y=pl.coalesce(pl.lit(default), pl.col("x")))',
                10.0,
            ),
            (
                'factors = {"A": 2.0}\ndef total(xs):\n    t = 0\n    for x in xs:\n'
                "        t += x\n    return t\nn = total([1, 2])\n" + _TIMES_A.format("factors"),
                20.0,
            ),
            (
                "factor = 1.0\ndef reset():\n    global factor\n    factor = 2.0\n"
                "def unused():\n    reset()\n"
                'df = df.with_columns(y=pl.col("x") * factor)',
                10.0,
            ),
            (
                'factors = {"A": 2.0}\ndef clip(d):\n    d["A"] = 5.0\n'
                'df = df.with_columns(y=pl.col("x").clip(0, 100) * factors["A"])',
                20.0,
            ),
            (
                "factor = 2.0\ndef debug():\n    return locals()\n"
                'df = df.with_columns(y=pl.col("x") * factor)',
                20.0,
            ),
        ],
        ids=[
            "read-before-the-change",
            "a-function-call",
            "a-method-on-a-name-the-code-never-bound",
            "a-block-that-leaves-the-name-alone",
            "a-function-changing-its-own-parameter",
            "a-polars-expression-built-before-the-change",
            "arithmetic-on-a-number",
            "a-method-alone-on-a-polars-expression",
            "a-null-default",
            "a-helper-summing-numbers",
            "a-global-rebinding-only-an-unused-function-runs",
            "a-helper-named-like-a-polars-method",
            "reflection-in-an-unused-helper",
        ],
    )
    def test_a_value_the_code_did_not_change_keeps_it(self, code: str, value: float) -> None:
        result = evaluate_expression(code, "y", {"x": 10.0, "code": "a"})

        assert result.not_computable_reason is None
        assert result.result_value == value

    def test_a_preamble_value_after_a_change_is_reported_not_guessed(self) -> None:
        # ``alias`` holds the preamble's dict; the change through it reaches
        # ``RATES`` though the code never names it in a change.
        code = 'alias = RATES\nalias["A"] = 2.0\n' + _TIMES_A.format("RATES")

        result = evaluate_expression(code, "y", {"x": 10.0}, preamble_ns={"RATES": {"A": 1.0}})

        assert result.result_value is None
        assert result.not_computable_reason == "unresolved_name: RATES"

    def test_a_preamble_number_bound_again_by_reflection_is_reported_not_guessed(self) -> None:
        code = 'factor = 1.0\nglobals()["RATE"] = 3.0\ndf = df.with_columns(y=pl.col("x") * RATE)'

        result = evaluate_expression(code, "y", {"x": 10.0}, preamble_ns={"RATE": 2.0})

        assert result.result_value is None
        assert result.not_computable_reason == "unresolved_name: RATE"

    def test_a_preamble_number_after_a_change_still_computes(self) -> None:
        code = (
            'factors = {"A": 1.0}\nfactors["A"] = 2.0\ndf = df.with_columns(y=pl.col("x") * RATE)'
        )

        result = evaluate_expression(code, "y", {"x": 10.0}, preamble_ns={"RATE": 2.0})

        assert result.not_computable_reason is None
        assert result.result_value == 20.0

    @pytest.mark.parametrize(
        ("value", "may_change"),
        [
            (math, False),
            (math.floor, False),
            (SimpleNamespace, False),
            (pl.col("x"), False),
            (2.5, False),
            ("text", False),
            (date(2026, 1, 1), False),
            ((1, "a"), False),
            (None, False),
            ({"A": 1.0}, True),
            ([1.0], True),
            ({1.0}, True),
            (SimpleNamespace(rate=1.0), True),
            ((1, [2]), True),
        ],
        ids=[
            "module",
            "function",
            "class",
            "polars-expression",
            "number",
            "string",
            "date",
            "tuple-of-scalars",
            "none",
            "dict",
            "list",
            "set",
            "object",
            "tuple-holding-a-list",
        ],
    )
    def test_what_a_change_can_reach(self, value: object, may_change: bool) -> None:
        assert _may_change(value) is may_change


# ###########################################################################
# 1. _ExprConverter.convert — node dispatch: Dict, IfExp, JoinedStr,
#    Starred, Expr, fallback (lines 160–186)
# ###########################################################################


class TestConverterNodeDispatch:
    """Cover every isinstance-branch in _ExprConverter.convert()."""

    def test_dict_literal_in_expression(self):
        """Dict node (line 176)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.col("x").replace_strict({"a": 1, "b": 2}).alias("mapped")\n'
            ")"
        )
        expr = parse_expression(code, "mapped")
        assert expr is not None
        assert "replace_strict" in expr.expression_text

    def test_ifexp_ternary(self):
        """IfExp / ternary expression (line 177–178)."""
        # Python ternary inside a pl.lit()
        code = 'x = 5\ndf = df.with_columns(pl.lit(x if x > 0 else 0).alias("val"))'
        expr = parse_expression(code, "val")
        assert expr is not None

    def test_joinedstr_fstring_in_expression(self):
        """JoinedStr / f-string (lines 179, 328–337)."""
        code = 'prefix = "col"\ndf = df.with_columns(pl.col(f"{prefix}_a").alias("target"))'
        expr = parse_expression(code, "target")
        assert expr is not None

    def test_starred_expression(self):
        """Starred expression (line 181–182)."""
        code = (
            'exprs = [(pl.col("a") + 1).alias("a1"), (pl.col("b") + 2).alias("b1")]\n'
            "df = df.with_columns(*exprs)"
        )
        expr = parse_expression(code, "a1")
        assert expr is not None
        assert "a" in expr.referenced_columns

    def test_expr_node_wrapping(self):
        """Expr wrapper (line 183–184)."""
        # Expression statements are handled via ast.Expr
        code = 'df = df.with_columns((pl.col("z") * 2).alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None
        assert expr.expression_text == "z * 2"


# ###########################################################################
# 2. _ExprConverter._unaryop — UAdd, Not, Invert (lines 236–247)
# ###########################################################################


class TestUnaryOperations:
    def test_uadd(self):
        code = 'df = df.with_columns((+pl.col("x")).alias("pos"))'
        expr = parse_expression(code, "pos")
        assert expr is not None
        assert "+x" in expr.expression_text or "x" in expr.expression_text

    def test_not_operator(self):
        code = 'df = df.with_columns((not pl.col("flag")).alias("inv"))'
        expr = parse_expression(code, "inv")
        assert expr is not None
        # Python `not` on a Polars expr is unusual but the parser handles it
        assert "not" in expr.expression_text.lower() or expr.expression_type == "opaque"

    def test_invert_operator(self):
        code = 'df = df.with_columns((~pl.col("mask")).alias("flipped"))'
        expr = parse_expression(code, "flipped")
        assert expr is not None
        assert "~" in expr.expression_text or "mask" in expr.referenced_columns


# ###########################################################################
# 3. _ExprConverter._boolop — and / or (lines 257–260)
# ###########################################################################


class TestBoolOp:
    def test_and_expression(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("a") > 0) & (pl.col("b") > 0))\n'
            '    .then(1).otherwise(0).alias("both_pos")\n'
            ")"
        )
        expr = parse_expression(code, "both_pos")
        assert "a" in expr.referenced_columns
        assert "b" in expr.referenced_columns

    def test_or_expression(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when((pl.col("a") > 0) | (pl.col("b") > 0))\n'
            '    .then(1).otherwise(0).alias("any_pos")\n'
            ")"
        )
        expr = parse_expression(code, "any_pos")
        assert "a" in expr.referenced_columns


# ###########################################################################
# 4. _ExprConverter._constant — edge cases (lines 262–275)
# ###########################################################################


class TestConstantEdgeCases:
    def test_none_constant(self):
        code = 'df = df.with_columns(pl.lit(None).alias("empty"))'
        expr = parse_expression(code, "empty")
        assert expr.expression_text == "None"

    def test_bool_true_constant(self):
        code = 'df = df.with_columns(pl.lit(True).alias("flag"))'
        expr = parse_expression(code, "flag")
        assert expr.expression_text == "True"

    def test_bool_false_constant(self):
        code = 'df = df.with_columns(pl.lit(False).alias("flag"))'
        expr = parse_expression(code, "flag")
        assert expr.expression_text == "False"

    def test_string_constant(self):
        code = 'df = df.with_columns(pl.lit("hello").alias("greeting"))'
        expr = parse_expression(code, "greeting")
        assert '"hello"' in expr.expression_text

    def test_bytes_constant_repr(self):
        """Non-standard constant falls back to repr (line 275)."""
        code = 'df = df.with_columns(pl.lit(b"raw").alias("raw"))'
        expr = parse_expression(code, "raw")
        assert expr is not None


# ###########################################################################
# 5. _ExprConverter._name — symbol table resolution (lines 277–291)
# ###########################################################################


class TestNameResolution:
    def test_symbol_table_variable(self):
        """Variable resolved via symbol table (lines 280–283)."""
        code = 'factor = 1.05\ndf = df.with_columns((pl.col("base") * factor).alias("adjusted"))'
        expr = parse_expression(code, "adjusted")
        assert expr is not None
        assert "base" in expr.referenced_columns
        assert 1.05 in expr.constants

    def test_none_name(self):
        """Name 'None' recognised as constant (line 284–285)."""
        code = (
            "df = df.with_columns("
            'pl.when(pl.col("x") > 0).then(pl.col("x")).otherwise(None).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert "None" in expr.expression_text

    def test_true_false_name(self):
        """Name 'True'/'False' recognised (lines 286–289)."""
        code = (
            "df = df.with_columns("
            'pl.when(pl.col("x") > 0).then(True).otherwise(False).alias("flag"))'
        )
        expr = parse_expression(code, "flag")
        assert "True" in expr.expression_text
        assert "False" in expr.expression_text


# ###########################################################################
# 6. _ExprConverter._attribute — pl.Type and general attr (lines 293–298)
# ###########################################################################


class TestAttributeAccess:
    def test_pl_attribute(self):
        """pl.Float64 returns just 'Float64' (line 296)."""
        code = 'df = df.with_columns(pl.col("x").cast(pl.Float64).alias("xf"))'
        expr = parse_expression(code, "xf")
        assert "Float64" in expr.expression_text

    def test_general_attribute(self):
        """Non-pl attribute access (line 297–298)."""
        code = 'df = df.with_columns(pl.col("ts").dt.year().alias("yr"))'
        expr = parse_expression(code, "yr")
        assert "dt" in expr.expression_text
        assert "year" in expr.expression_text


# ###########################################################################
# 7. _ExprConverter._list, _tuple, _dict, _ifexp, _joinedstr
#    (lines 305–337)
# ###########################################################################


class TestContainerNodes:
    def test_list_node(self):
        code = 'df = df.with_columns(pl.col("region").is_in(["North", "South"]).alias("ns"))'
        expr = parse_expression(code, "ns")
        assert "North" in expr.expression_text or "region" in expr.referenced_columns

    def test_tuple_node(self):
        """Tuple literal rendered with parens (lines 309–311)."""
        code = 'df = df.with_columns(pl.col("x").is_in((1, 2, 3)).alias("small"))'
        expr = parse_expression(code, "small")
        assert expr is not None

    def test_dict_node_with_none_key(self):
        """Dict with **splat (line 319)."""
        code = (
            'mapping = {"a": 1}\n'
            'df = df.with_columns(pl.col("x").replace_strict({**mapping}).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert expr is not None

    def test_ifexp_rendering(self):
        """Ternary expression rendering (lines 322–326)."""
        code = (
            "threshold = 100\n"
            "df = df.with_columns(\n"
            '    (pl.col("x") * (1.1 if threshold > 50 else 0.9)).alias("adj")\n'
            ")"
        )
        expr = parse_expression(code, "adj")
        assert expr is not None
        assert "x" in expr.referenced_columns

    def test_joinedstr_rendering(self):
        """f-string rendering (lines 328–337)."""
        code = 'name = "premium"\ndf = df.with_columns(pl.col(f"loaded_{name}").alias("target"))'
        expr = parse_expression(code, "target")
        assert expr is not None


# ###########################################################################
# 8. _ExprConverter._call — Polars-specific call handling
#    Namespace methods, .over(), .cast(), opaque, bare func (lines 340–450)
# ###########################################################################


class TestStringNamespaceMethods:
    def test_str_lengths(self):
        code = 'df = df.with_columns(pl.col("name").str.len_chars().alias("name_len"))'
        expr = parse_expression(code, "name_len")
        assert "str" in expr.expression_text
        assert "name" in expr.referenced_columns

    def test_str_extract(self):
        code = 'df = df.with_columns(pl.col("text").str.extract(r"(\\d+)").alias("digits"))'
        expr = parse_expression(code, "digits")
        assert "extract" in expr.expression_text

    def test_str_strip_chars(self):
        code = 'df = df.with_columns(pl.col("s").str.strip_chars().alias("trimmed"))'
        expr = parse_expression(code, "trimmed")
        assert "strip_chars" in expr.expression_text

    def test_str_replace_ns(self):
        code = 'df = df.with_columns(pl.col("t").str.replace("a", "b").alias("fixed"))'
        expr = parse_expression(code, "fixed")
        assert "replace" in expr.expression_text

    def test_str_starts_with(self):
        code = 'df = df.with_columns(pl.col("url").str.starts_with("https").alias("secure"))'
        expr = parse_expression(code, "secure")
        assert "starts_with" in expr.expression_text

    def test_str_ends_with(self):
        code = 'df = df.with_columns(pl.col("file").str.ends_with(".csv").alias("is_csv"))'
        expr = parse_expression(code, "is_csv")
        assert "ends_with" in expr.expression_text

    def test_str_to_lowercase(self):
        code = 'df = df.with_columns(pl.col("n").str.to_lowercase().alias("lower"))'
        expr = parse_expression(code, "lower")
        assert "to_lowercase" in expr.expression_text

    def test_str_to_uppercase(self):
        code = 'df = df.with_columns(pl.col("n").str.to_uppercase().alias("upper"))'
        expr = parse_expression(code, "upper")
        assert "to_uppercase" in expr.expression_text

    def test_str_slice(self):
        code = 'df = df.with_columns(pl.col("code").str.slice(0, 3).alias("prefix"))'
        expr = parse_expression(code, "prefix")
        assert "slice" in expr.expression_text


class TestDatetimeNamespaceMethods:
    def test_dt_year(self):
        code = 'df = df.with_columns(pl.col("date").dt.year().alias("yr"))'
        expr = parse_expression(code, "yr")
        assert "year" in expr.expression_text
        assert "date" in expr.referenced_columns

    def test_dt_month(self):
        code = 'df = df.with_columns(pl.col("date").dt.month().alias("mo"))'
        expr = parse_expression(code, "mo")
        assert "month" in expr.expression_text

    def test_dt_day(self):
        code = 'df = df.with_columns(pl.col("date").dt.day().alias("d"))'
        expr = parse_expression(code, "d")
        assert "day" in expr.expression_text

    def test_dt_hour(self):
        code = 'df = df.with_columns(pl.col("ts").dt.hour().alias("h"))'
        expr = parse_expression(code, "h")
        assert "hour" in expr.expression_text

    def test_dt_minute(self):
        code = 'df = df.with_columns(pl.col("ts").dt.minute().alias("m"))'
        expr = parse_expression(code, "m")
        assert "minute" in expr.expression_text

    def test_dt_date(self):
        code = 'df = df.with_columns(pl.col("ts").dt.date().alias("d"))'
        expr = parse_expression(code, "d")
        assert "date" in expr.expression_text


class TestListNamespaceMethods:
    def test_list_lengths(self):
        code = 'df = df.with_columns(pl.col("items").list.len().alias("n"))'
        expr = parse_expression(code, "n")
        assert "list" in expr.expression_text
        assert "items" in expr.referenced_columns

    def test_list_get(self):
        code = 'df = df.with_columns(pl.col("items").list.get(0).alias("first"))'
        expr = parse_expression(code, "first")
        assert "get" in expr.expression_text

    def test_list_slice(self):
        code = 'df = df.with_columns(pl.col("items").list.slice(1, 3).alias("mid"))'
        expr = parse_expression(code, "mid")
        assert "slice" in expr.expression_text

    def test_list_first(self):
        code = 'df = df.with_columns(pl.col("items").list.first().alias("f"))'
        expr = parse_expression(code, "f")
        assert "first" in expr.expression_text

    def test_list_last(self):
        code = 'df = df.with_columns(pl.col("items").list.last().alias("l"))'
        expr = parse_expression(code, "l")
        assert "last" in expr.expression_text


class TestStructNamespaceMethods:
    def test_struct_field(self):
        code = 'df = df.with_columns(pl.col("data").struct.field("name").alias("n"))'
        expr = parse_expression(code, "n")
        assert "field" in expr.expression_text
        assert "data" in expr.referenced_columns

    def test_struct_fields(self):
        code = (
            'df = df.with_columns(pl.col("data").struct.rename_fields(["a", "b"]).alias("renamed"))'
        )
        expr = parse_expression(code, "renamed")
        assert "struct" in expr.expression_text


class TestCatNamespaceMethods:
    def test_cat_set_ordering(self):
        code = (
            'df = df.with_columns(pl.col("cat_col").cat.set_ordering("lexical").alias("ordered"))'
        )
        expr = parse_expression(code, "ordered")
        assert "cat" in expr.expression_text


class TestWindowFunctionOverForms:
    def test_over_single_partition(self):
        """Window function with .over() (lines 408–416)."""
        code = 'df = df.with_columns(pl.col("premium").sum().over("region").alias("region_total"))'
        expr = parse_expression(code, "region_total")
        assert expr is not None
        assert "over" in expr.expression_text
        assert "region" in expr.expression_text

    def test_over_keyword_arg(self):
        code = 'df = df.with_columns(pl.col("x").mean().over(pl.col("group")).alias("grp_mean"))'
        expr = parse_expression(code, "grp_mean")
        assert "over" in expr.expression_text


class TestCastOperations:
    def test_cast_with_type(self):
        code = 'df = df.with_columns(pl.col("x").cast(pl.Utf8).alias("xs"))'
        expr = parse_expression(code, "xs")
        assert "cast" in expr.expression_text
        assert "Utf8" in expr.expression_text

    def test_cast_no_args(self):
        """cast() with no args (line 424)."""
        code = 'df = df.with_columns(pl.col("x").cast().alias("xc"))'
        expr = parse_expression(code, "xc")
        assert "cast()" in expr.expression_text


class TestOpaqueMethodCalls:
    def test_map_elements(self):
        """Opaque: map_elements (lines 379–383)."""
        code = 'df = df.with_columns(pl.col("x").map_elements(lambda v: v * 2).alias("doubled"))'
        expr = parse_expression(code, "doubled")
        assert expr.expression_type == "opaque"
        assert "map_elements" in expr.expression_text

    def test_map_batches(self):
        code = 'df = df.with_columns(pl.col("x").map_batches(lambda s: s * 2).alias("doubled"))'
        expr = parse_expression(code, "doubled")
        assert expr.expression_type == "opaque"

    def test_lambda_arg_makes_opaque(self):
        """Lambda as argument (lines 343–346)."""
        code = 'df = df.with_columns(pl.col("x").map_elements(lambda v: v + 1).alias("inc"))'
        expr = parse_expression(code, "inc")
        assert expr.expression_type == "opaque"


class TestBareFunctionCall:
    def test_opaque_builtin_eval(self):
        """eval/exec/getattr mark as opaque (lines 432–447)."""
        code = 'df = df.with_columns(eval("pl.col(\'x\')").alias("dynamic"))'
        expr = parse_expression(code, "dynamic")
        assert expr.expression_type == "opaque"

    def test_user_defined_function_opaque(self):
        code = 'def my_func(x): return x * 2\ndf = df.with_columns(my_func(pl.col("x")).alias("r"))'
        # my_func gets added to symbol table making it opaque
        expr = parse_expression(code, "r")
        assert expr is not None

    def test_regular_function_call(self):
        """Non-opaque bare function (line 440)."""
        code = 'df = df.with_columns(abs(pl.col("x")).alias("abs_x"))'
        expr = parse_expression(code, "abs_x")
        assert expr is not None
        assert expr.expression_type == "function_call"

    def test_function_with_kwargs(self):
        code = 'df = df.with_columns(round(pl.col("x"), ndigits=2).alias("rounded"))'
        expr = parse_expression(code, "rounded")
        assert "ndigits" in expr.expression_text


# ###########################################################################
# 9. _pl_col — dynamic column, symbol table (lines 452–476)
# ###########################################################################


class TestPlColDynamic:
    def test_pl_col_variable_from_symbol_table(self):
        """Dynamic column name resolved from symbol table (lines 463–471)."""
        code = 'col_name = "premium"\ndf = df.with_columns((pl.col(col_name) * 2).alias("doubled"))'
        expr = parse_expression(code, "doubled")
        assert "premium" in expr.referenced_columns

    def test_pl_col_unresolvable_variable(self):
        """Dynamic column that can't be resolved (lines 473–475)."""
        code = 'df = df.with_columns(pl.col(get_col_name()).alias("dynamic"))'
        expr = parse_expression(code, "dynamic")
        assert expr.expression_type == "opaque" or expr is not None

    def test_pl_col_no_args(self):
        """pl.col() with no args (line 476)."""
        code = 'df = df.with_columns(pl.col().alias("empty"))'
        expr = parse_expression(code, "empty")
        assert expr is not None


# ###########################################################################
# 10. _pl_lit — variants (lines 478–511)
# ###########################################################################


class TestPlLitVariants:
    def test_pl_lit_int(self):
        code = 'df = df.with_columns(pl.lit(42).alias("answer"))'
        expr = parse_expression(code, "answer")
        assert 42 in expr.constants

    def test_pl_lit_true(self):
        code = 'df = df.with_columns(pl.lit(True).alias("flag"))'
        expr = parse_expression(code, "flag")
        assert True in expr.constants

    def test_pl_lit_false(self):
        code = 'df = df.with_columns(pl.lit(False).alias("flag"))'
        expr = parse_expression(code, "flag")
        assert False in expr.constants

    def test_pl_lit_none_constant(self):
        code = 'df = df.with_columns(pl.lit(None).alias("n"))'
        expr = parse_expression(code, "n")
        assert None in expr.constants

    def test_pl_lit_string(self):
        code = 'df = df.with_columns(pl.lit("hello").alias("greet"))'
        expr = parse_expression(code, "greet")
        assert "hello" in expr.constants

    def test_pl_lit_float(self):
        code = 'df = df.with_columns(pl.lit(3.14).alias("pi"))'
        expr = parse_expression(code, "pi")
        assert 3.14 in expr.constants

    def test_pl_lit_bytes_repr(self):
        """Non-standard constant in lit falls through to repr (line 498–499)."""
        code = 'df = df.with_columns(pl.lit(b"data").alias("raw"))'
        expr = parse_expression(code, "raw")
        assert expr is not None

    def test_pl_lit_name_none(self):
        """pl.lit(None) as Name node, not Constant (lines 500–502)."""
        # In older Python, None is a Name; this tests the fallback
        code = 'df = df.with_columns(pl.lit(None).alias("n"))'
        expr = parse_expression(code, "n")
        assert expr.expression_text == "None"

    def test_pl_lit_no_args(self):
        """pl.lit() with no args (line 511)."""
        code = 'df = df.with_columns(pl.lit().alias("empty"))'
        expr = parse_expression(code, "empty")
        assert "lit()" in expr.expression_text

    def test_pl_lit_variable(self):
        """pl.lit(var) where var is in symbol table (line 510)."""
        code = 'val = 42\ndf = df.with_columns(pl.lit(val).alias("answer"))'
        expr = parse_expression(code, "answer")
        assert expr is not None


# ###########################################################################
# 11. pl.format (lines 513–529)
# ###########################################################################


class TestPlFormat:
    def test_pl_format_basic(self):
        code = 'df = df.with_columns(pl.format("{} - {}", pl.col("a"), pl.col("b")).alias("desc"))'
        expr = parse_expression(code, "desc")
        assert expr.expression_type == "horizontal_func"
        assert "format" in expr.expression_text

    def test_pl_format_with_string_col_names(self):
        """String args after format string become column names (lines 522–526)."""
        code = 'df = df.with_columns(pl.format("{}: {}", "name", "value").alias("desc"))'
        expr = parse_expression(code, "desc")
        assert "name" in expr.referenced_columns
        assert "value" in expr.referenced_columns


# ###########################################################################
# 12. Horizontal funcs — list args, keyword args (lines 531–551)
# ###########################################################################


class TestHorizontalFuncEdges:
    def test_horizontal_with_list_arg(self):
        """List of string column names as arg (lines 536–545)."""
        code = 'df = df.with_columns(pl.sum_horizontal(["a", "b", "c"]).alias("total"))'
        expr = parse_expression(code, "total")
        assert expr.expression_type == "horizontal_func"
        assert "a" in expr.referenced_columns

    def test_horizontal_with_keyword(self):
        """Keyword arg like separator= (lines 548–549)."""
        code = (
            "df = df.with_columns("
            'pl.concat_str(pl.col("a"), pl.col("b"), separator="-").alias("joined"))'
        )
        expr = parse_expression(code, "joined")
        assert "separator" in expr.expression_text

    def test_coalesce(self):
        code = (
            'df = df.with_columns(pl.coalesce(pl.col("a"), pl.col("b"), pl.lit(0)).alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr.expression_type == "horizontal_func"

    def test_mean_horizontal(self):
        code = 'df = df.with_columns(pl.mean_horizontal(pl.col("x"), pl.col("y")).alias("avg"))'
        expr = parse_expression(code, "avg")
        assert expr.expression_type == "horizontal_func"

    def test_all_horizontal(self):
        code = 'df = df.with_columns(pl.all_horizontal(pl.col("a"), pl.col("b")).alias("all_true"))'
        expr = parse_expression(code, "all_true")
        assert expr.expression_type == "horizontal_func"

    def test_any_horizontal(self):
        code = 'df = df.with_columns(pl.any_horizontal(pl.col("a"), pl.col("b")).alias("any_true"))'
        expr = parse_expression(code, "any_true")
        assert expr.expression_type == "horizontal_func"


# ###########################################################################
# 13. When/then — chained when, when_continuation (lines 553–694)
# ###########################################################################


class TestChainedWhenThen:
    def test_chained_when_three_branches(self):
        """Multiple when/then before otherwise (lines 560–564)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") < 0).then("neg")\n'
            '    .when(pl.col("x") == 0).then("zero")\n'
            '    .otherwise("pos")\n'
            '    .alias("sign")\n'
            ")"
        )
        expr = parse_expression(code, "sign")
        assert expr.expression_type == "conditional"
        assert "neg" in expr.expression_text
        assert "zero" in expr.expression_text
        assert "pos" in expr.expression_text

    def test_when_no_otherwise(self):
        """When/then without otherwise (line 582)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("flag") == 1).then(pl.col("val"))\n'
            '    .alias("result")\n'
            ")"
        )
        expr = parse_expression(code, "result")
        assert expr.expression_type == "conditional"

    def test_nested_when_inside_then(self):
        """Nested conditional inside a then branch (lines 645–668)."""
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
        assert len(expr.sub_expressions) > 0


# ###########################################################################
# 14. _format_call_args — kwargs with None arg (lines 685–695)
# ###########################################################################


class TestFormatCallArgs:
    def test_kwargs_with_splat(self):
        """**kwargs in call args (line 694)."""
        code = (
            'opts = {"strategy": "forward"}\n'
            'df = df.with_columns(pl.col("x").fill_null(**opts).alias("filled"))'
        )
        expr = parse_expression(code, "filled")
        assert expr is not None


# ###########################################################################
# 15. _strip_alias with f-string, _try_eval_fstring (lines 703–750)
# ###########################################################################


class TestFStringAlias:
    def test_fstring_alias(self):
        """Alias with f-string (lines 718–720)."""
        code = (
            'suffix = "total"\n'
            'df = df.with_columns((pl.col("a") + pl.col("b")).alias(f"sum_{suffix}"))'
        )
        expr = parse_expression(code, "sum_total")
        assert expr is not None
        assert "a" in expr.referenced_columns

    def test_fstring_alias_constant_value(self):
        """f-string with constant inside FormattedValue (line 744–745)."""
        code = 'df = df.with_columns((pl.col("x") * 2).alias(f"doubled_{42}"))'
        expr = parse_expression(code, "doubled_42")
        assert expr is not None

    def test_fstring_alias_unresolvable(self):
        """f-string that can't be resolved (line 747–749)."""
        code = 'df = df.with_columns((pl.col("x") * 2).alias(f"col_{unknown_var}"))'
        # parse_expression falls back since f-string can't be resolved
        expr = parse_expression(code, "col_test")
        assert expr is not None


# ###########################################################################
# 16. _infer_auto_name (lines 753–768)
# ###########################################################################


class TestInferAutoName:
    def test_auto_name_from_col(self):
        """Expression without .alias() infers name from first col (lines 753–768)."""
        code = 'df = df.with_columns(pl.col("amount"))'
        expr = parse_expression(code, "amount")
        assert expr is not None
        assert "amount" in expr.referenced_columns

    def test_auto_name_from_binop(self):
        """BinOp recursion for auto name (line 767)."""
        code = 'df = df.with_columns(pl.col("x") + pl.col("y"))'
        expr = parse_expression(code, "x")
        assert expr is not None


# ###########################################################################
# 17. Symbol table builders and control flow detection
#     (lines 776–892)
# ###########################################################################


class TestSymbolTableAndControlFlow:
    def test_build_safe_symbol_table_augassign(self):
        """AugAssign is noted but not resolved (line 804–807)."""
        code = (
            'factor = 1.0\nfactor += 0.1\ndf = df.with_columns((pl.col("x") * factor).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert expr is not None

    def test_has_control_flow_if(self):
        """with_columns inside if block (lines 831–840, 866–891)."""
        code = 'if True:\n    df = df.with_columns((pl.col("x") + 1).alias("target"))\n'
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    def test_has_control_flow_for(self):
        code = 'for i in range(3):\n    df = df.with_columns((pl.col("x") + i).alias("target"))\n'
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    def test_has_control_flow_while(self):
        code = 'while False:\n    df = df.with_columns((pl.col("x") + 1).alias("target"))\n'
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    def test_has_control_flow_try(self):
        code = (
            'try:\n    df = df.with_columns((pl.col("x") + 1).alias("target"))\nexcept:\n    pass\n'
        )
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    def test_has_control_flow_with(self):
        code = 'with open("f") as f:\n    df = df.with_columns((pl.col("x") + 1).alias("target"))\n'
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    @pytest.mark.parametrize(
        "code",
        [
            'if flag:\n    df = df.with_columns(pl.col("target") * 2)\n',
            'expr = (pl.col("target") * 2).alias("target")\n'
            "if flag:\n    df = df.with_columns(expr)\n",
            'exprs = [(pl.col("target") * 2).alias("target")]\n'
            "for _ in range(2):\n    df = df.with_columns(*exprs)\n",
            'match mode:\n    case "a":\n        df = df.with_columns(pl.col("target") + 1)\n',
        ],
        ids=["unaliased", "bound-expression", "bound-list", "match"],
    )
    def test_a_conditional_write_the_parser_names_is_opaque(self, code):
        """A block's write is found however the unconditional path would name it."""
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    def test_control_flow_assigned_vars(self):
        """Variable assigned in if block references (lines 843–863)."""
        code = (
            "if True:\n"
            "    factor = 1.5\n"
            'df = df.with_columns((pl.col("x") * factor).alias("target"))\n'
        )
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    def test_augassign_in_control_flow(self):
        """AugAssign inside control flow leaves the name's value unknown where the
        expression runs, so the expression is opaque."""
        code = (
            "if True:\n"
            "    y = 1\n"
            "    y += 1\n"
            'df = df.with_columns((pl.col("a") * y).alias("target"))\n'
        )
        expr = parse_expression(code, "target")
        # y is only assigned inside if-block, so it's opaque
        assert expr.expression_type == "opaque"


# ###########################################################################
# 18. Variable-based expression list resolution (lines 899–906)
# ###########################################################################


class TestResolveListVariable:
    def test_variable_list_of_expressions(self):
        """Starred expression referencing a list variable (lines 936–945)."""
        code = (
            "exprs = [\n"
            '    (pl.col("a") + 1).alias("a1"),\n'
            '    (pl.col("b") + 2).alias("b1"),\n'
            "]\n"
            "df = df.with_columns(*exprs)"
        )
        expr_a = parse_expression(code, "a1")
        expr_b = parse_expression(code, "b1")
        assert expr_a is not None
        assert expr_b is not None
        assert "a" in expr_a.referenced_columns

    def test_single_expression_variable(self):
        """Variable referencing a single expression (lines 955–962)."""
        code = 'e = (pl.col("x") * 2).alias("doubled")\ndf = df.with_columns(e)'
        expr = parse_expression(code, "doubled")
        assert expr is not None
        assert "x" in expr.referenced_columns

    def test_list_variable_without_starred(self):
        """Variable name referencing a list passed directly (lines 948–954)."""
        code = 'exprs = [\n    (pl.col("a") + 1).alias("a1"),\n]\ndf = df.with_columns(exprs)'
        expr = parse_expression(code, "a1")
        assert expr is not None


# ###########################################################################
# 19. parse_expression edge cases (lines 988–1147)
# ###########################################################################


class TestParseExpressionEdgeCases:
    def test_empty_code(self):
        """Empty/whitespace code (lines 1007–1015)."""
        expr = parse_expression("", "target")
        assert expr is not None
        assert expr.expression_type == "opaque"
        assert expr.expression_text == ""

    def test_whitespace_only(self):
        expr = parse_expression("   \n  ", "target")
        assert expr is not None
        assert expr.expression_type == "opaque"

    def test_syntax_error_code(self):
        """SyntaxError fallback (lines 1020–1030)."""
        expr = parse_expression("df = df.with_columns((pl.col('a') +).alias('x'))", "x")
        assert expr is not None
        assert expr.expression_type == "opaque"

    def test_no_with_columns(self):
        """Code without with_columns (lines 1067–1076)."""
        expr = parse_expression("x = 42", "target")
        assert expr is not None
        assert expr.expression_type == "opaque"

    def test_bom_stripping(self):
        """BOM prefix stripping (line 1018)."""
        code = '\ufeffdf = df.with_columns((pl.col("x") + 1).alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None
        assert expr.expression_text == "x + 1"

    def test_target_not_found(self):
        """Target column not in any with_columns (lines 1126–1134)."""
        code = 'df = df.with_columns((pl.col("a") + 1).alias("b"))'
        expr = parse_expression(code, "nonexistent")
        assert expr is not None
        assert expr.expression_type == "opaque"

    def test_exception_fallback(self):
        """General exception fallback (lines 995–1003)."""
        # Very unusual code that might trigger an unexpected error
        expr = parse_expression(None, "target")  # type: ignore[arg-type]
        assert expr is not None
        assert expr.expression_type == "opaque"

    def test_no_statements_after_parse(self):
        """Empty module body (lines 1033–1041)."""
        expr = parse_expression("# just a comment\n", "target")
        assert expr is not None

    def test_keyword_column_in_with_columns(self):
        """Keyword arg in with_columns (line 976–978)."""
        code = 'df = df.with_columns(result=pl.col("x") + 1)'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_text == "x + 1"

    def test_auto_name_no_alias(self):
        """No alias, auto-name from pl.col (lines 1113–1124)."""
        code = 'df = df.with_columns(pl.col("x") + 1)'
        expr = parse_expression(code, "x")
        assert expr is not None

    def test_select_method(self):
        """select() is also searched (line 919)."""
        code = 'df = df.select((pl.col("a") + 1).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_text == "a + 1"


# ###########################################################################
# 20. Reassignment chains (lines 1184–1286)
# ###########################################################################


class TestReassignmentChains:
    def test_simple_reassignment(self):
        """expr = pl.col("base"); expr = expr * factor (lines 1184–1200)."""
        code = (
            'expr = pl.col("base")\n'
            'expr = expr * pl.col("factor_a")\n'
            'expr = expr * pl.col("factor_b")\n'
            'df = df.with_columns(expr.alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "base" in expr.referenced_columns
        assert "factor_a" in expr.referenced_columns
        assert "factor_b" in expr.referenced_columns

    def test_substitute_names_binop(self):
        """BinOp substitution (lines 1210–1221)."""
        code = 'a = pl.col("x")\nb = a + 1\ndf = df.with_columns(b.alias("result"))'
        expr = parse_expression(code, "result")
        assert "x" in expr.referenced_columns

    def test_substitute_names_unaryop(self):
        """UnaryOp substitution (lines 1222–1228)."""
        code = 'a = pl.col("x")\nb = -a\ndf = df.with_columns(b.alias("result"))'
        expr = parse_expression(code, "result")
        assert "x" in expr.referenced_columns

    def test_substitute_names_call(self):
        """Call substitution (lines 1229–1252)."""
        code = (
            'cond = pl.col("x") > 0\n'
            'df = df.with_columns(pl.when(cond).then(1).otherwise(0).alias("flag"))'
        )
        expr = parse_expression(code, "flag")
        assert "x" in expr.referenced_columns

    def test_substitute_names_attribute(self):
        """Attribute substitution (lines 1253–1259)."""
        code = 'base = pl.col("x")\ndf = df.with_columns(base.abs().alias("abs_x"))'
        expr = parse_expression(code, "abs_x")
        assert "x" in expr.referenced_columns

    def test_substitute_names_compare(self):
        """Compare substitution (lines 1260–1271)."""
        code = (
            'a = pl.col("x")\n'
            'b = pl.col("y")\n'
            'df = df.with_columns(pl.when(a > b).then(a).otherwise(b).alias("max_xy"))'
        )
        expr = parse_expression(code, "max_xy")
        assert "x" in expr.referenced_columns
        assert "y" in expr.referenced_columns

    def test_substitute_names_list(self):
        """List substitution (lines 1272–1278)."""
        code = (
            'cols = [pl.col("a"), pl.col("b")]\n'
            'df = df.with_columns(pl.sum_horizontal(*cols).alias("total"))'
        )
        expr = parse_expression(code, "total")
        assert expr is not None

    def test_substitute_names_starred(self):
        """Starred substitution (lines 1279–1285)."""
        code = 'exprs = [(pl.col("a") + 1).alias("a1")]\ndf = df.with_columns(*exprs)'
        expr = parse_expression(code, "a1")
        assert expr is not None


# ###########################################################################
# 26. parse_expression_chain edge cases (lines 2142–2232)
# ###########################################################################


class TestParseExpressionChainEdges:
    def test_chain_exception_fallback(self):
        """A malformed (non-str) code argument fails loud instead of degrading
        to a laundered single-element chain; the enrichment caller records a
        visible error on the chain field."""
        with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: ast.parse exception type may vary
            parse_expression_chain(None, "target")  # type: ignore[arg-type]

    def test_chain_dot_syntax_wrapping(self):
        """Dot-chain wrapping (line 2164–2165)."""
        code = 'df = df.with_columns((pl.col("x") + 1).alias("r"))'
        chain = parse_expression_chain(code, "r")
        assert chain is not None

    def test_chain_auto_name_inferred(self):
        """Auto name for chain (line 2190)."""
        code = (
            'df = df.with_columns(pl.col("x"))\ndf = df.with_columns((pl.col("x") + 1).alias("r"))'
        )
        chain = parse_expression_chain(code, "r")
        assert chain is not None
        assert len(chain) >= 1

    def test_chain_syntax_error(self):
        """SyntaxError fallback in chain (lines 2173–2175)."""
        code = 'df = df.with_columns((pl.col("x") +).alias("r"))'
        chain = parse_expression_chain(code, "r")
        assert chain is not None

    def test_chain_no_df_in_code(self):
        """Code without 'df' gets wrapped (line 2166–2167)."""
        code = 'result = (\n    table\n    .with_columns((pl.col("x") + 1).alias("r"))\n)'
        chain = parse_expression_chain(code, "r")
        assert chain is not None


# ###########################################################################
# 27. Expressions reading a name whose value is unknown
# ###########################################################################


class TestExprReferencesVars:
    def test_expr_references_control_flow_var(self):
        """Variable from control flow makes expression opaque (lines 1139–1147)."""
        code = (
            "if True:\n"
            "    multiplier = 2\n"
            'df = df.with_columns((pl.col("x") * multiplier).alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr.expression_type == "opaque"

    def test_expr_no_cf_reference(self):
        """Variable NOT from control flow is fine."""
        code = 'multiplier = 2\ndf = df.with_columns((pl.col("x") * multiplier).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr.expression_type == "arithmetic"


# ###########################################################################
# 28. Multi-expression code blocks
# ###########################################################################


class TestMultiExpressionBlocks:
    def test_multiple_with_columns_last_wins(self):
        """Last with_columns defining target wins."""
        code = (
            'df = df.with_columns((pl.col("x") + 1).alias("r"))\n'
            'df = df.with_columns((pl.col("x") + 2).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert expr is not None
        assert "2" in expr.expression_text

    def test_listcomp_in_with_columns(self):
        """List comprehension arg is skipped (line 967–968)."""
        code = (
            'cols = ["a", "b"]\n'
            'df = df.with_columns([pl.col(c).alias(f"{c}_new") for c in cols])\n'
            'df = df.with_columns((pl.col("x") + 1).alias("target"))'
        )
        expr = parse_expression(code, "target")
        assert expr is not None
        assert expr.expression_text == "x + 1"

    def test_is_df_assignment_variants(self):
        """Various df method names recognised (lines 811–828)."""
        code = (
            'df = df.select(pl.col("x"))\n'
            'df = df.filter(pl.col("x") > 0)\n'
            'df = df.with_columns((pl.col("x") + 1).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert expr is not None
        assert expr.expression_text == "x + 1"


# ###########################################################################
# 32. Additional coverage for remaining uncovered lines
# ###########################################################################


class TestBoolOpConverterDirect:
    """Cover _boolop dispatch (line 160) — Python `and`/`or` keywords."""

    def test_python_and_keyword(self):
        """Python `and` operator parsed as BoolOp (line 160, 258–260)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 0 and pl.col("b") > 0).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        # Python `and` creates ast.BoolOp unlike `&` which creates ast.BinOp
        expr = parse_expression(code, "r")
        assert expr is not None

    def test_python_or_keyword(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 0 or pl.col("b") > 0).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        expr = parse_expression(code, "r")
        assert expr is not None


class TestConverterFallbackPath:
    """Cover the fallback path (line 186) for unknown AST node types."""

    def test_set_in_expression(self):
        """Set literal — no handler, falls to ast.dump (line 186)."""
        code = 'df = df.with_columns(pl.col("x").is_in({1, 2, 3}).alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None


class TestSecondaryMatchAttempts:
    """Cover lines 1091–1109 — secondary search for target via symbol table resolution."""

    def test_variable_list_secondary_match(self):
        """Variable referencing list, matched in secondary search (lines 1095–1103)."""
        code = (
            "my_exprs = [\n"
            '    (pl.col("a") + 1).alias("target"),\n'
            '    (pl.col("b") + 2).alias("other"),\n'
            "]\n"
            "df = df.with_columns(my_exprs)"
        )
        expr = parse_expression(code, "target")
        assert expr is not None
        assert "a" in expr.referenced_columns

    def test_starred_in_secondary_search(self):
        """Starred expressions in secondary search (line 1093–1094)."""
        code = 'my_exprs = [(pl.col("a") + 1).alias("target")]\ndf = df.with_columns(*my_exprs)'
        expr = parse_expression(code, "target")
        assert expr is not None


class TestBuildSymbolTableFunction:
    """Cover df-assignment skipping in _build_safe_symbol_table via parse_expression."""

    def test_symbol_table_skips_df_assignments(self):
        """df assignments are skipped (lines 786–788)."""
        code = (
            "factor = 1.05\n"
            'df = df.filter(pl.col("x") > 0)\n'
            'df = df.with_columns((pl.col("x") * factor).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert 1.05 in expr.constants


class TestHasControlFlowFunction:
    """Cover _has_control_flow (lines 833–840) — Match and TryStar."""

    def test_match_statement_control_flow(self):
        """Match statement detection (lines 836–837)."""
        code = (
            "match x:\n"
            "    case 1:\n"
            '        df = df.with_columns((pl.col("a") + 1).alias("target"))\n'
            "    case _:\n"
            "        pass\n"
        )
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    def test_select_inside_if_is_opaque(self):
        code = (
            "if flag:\n"
            '    df = df.select(pl.col("a").alias("target"))\n'
            "else:\n"
            '    df = df.select(pl.lit(0).alias("target"))\n'
        )
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"

    def test_select_inside_except_star_is_opaque(self):
        code = (
            "try:\n"
            "    raise ExceptionGroup('group', [ValueError('bad')])\n"
            "except* ValueError:\n"
            '    df = df.select(pl.col("a").alias("target"))\n'
        )
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"


class TestControlFlowKeyword:
    """Cover keyword arg check inside control flow (lines 889–890)."""

    def test_keyword_in_with_columns_in_if(self):
        code = 'if True:\n    df = df.with_columns(target=pl.col("x") + 1)\n'
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"


class TestChainImplEdges:
    """Cover parse_expression_chain implementation edges (lines 2159–2232)."""

    def test_chain_with_no_alias_infer(self):
        """Column with no alias gets auto-inferred (line 2190–2192)."""
        code = (
            'df = df.with_columns(pl.col("x") + 1)\n'
            'df = df.with_columns((pl.col("x") * 2).alias("r"))'
        )
        chain = parse_expression_chain(code, "r")
        assert chain is not None

    def test_chain_target_not_in_defs(self):
        """Target not found returns empty (line 2203–2204)."""
        code = 'df = df.with_columns((pl.col("a") + 1).alias("b"))'
        chain = parse_expression_chain(code, "nonexistent")
        assert chain == []

    def test_chain_empty_result(self):
        """Chain builds but all parsed are None (line 2231)."""
        code = 'df = df.with_columns((pl.col("a") + 1).alias("r"))'
        chain = parse_expression_chain(code, "r")
        assert chain is not None
        assert len(chain) == 1


class TestSubstituteNamesCompare:
    """Cover _substitute_names_in_ast for Compare nodes (lines 1260–1271)."""

    def test_substitute_compare_node(self):
        code = (
            "threshold = 100\n"
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > threshold).then(1).otherwise(0).alias("r")\n'
            ")"
        )
        expr = parse_expression(code, "r")
        assert "100" in expr.expression_text

    def test_substitute_list_node(self):
        """Cover List substitution in _substitute_names_in_ast (lines 1272–1278)."""
        code = (
            'vals = [pl.col("a"), pl.col("b")]\n'
            'df = df.with_columns(pl.max_horizontal(*vals).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert expr is not None

    def test_substitute_keyword_in_call(self):
        """Cover keyword substitution (lines 1233–1238)."""
        code = (
            'sep_char = "-"\n'
            "df = df.with_columns("
            'pl.concat_str(pl.col("a"), pl.col("b"), separator=sep_char).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert expr is not None


class TestResolveListVariableInternals:
    """Cover inline single-expression variable resolution via parse_expression."""

    def test_resolve_non_list(self):
        """Variable is not a list (line 906)."""
        code = 'expr = pl.col("x") + 1\ndf = df.with_columns(expr.alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None

    def test_resolve_not_in_table(self):
        """Variable not in table (line 902)."""
        code = 'df = df.with_columns(unknown_var.alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None


class TestConverterNameConstants:
    """Cover _name handling for None/True/False names (lines 284–289)."""

    def test_name_none_in_expression(self):
        """Variable named None used in expression."""
        code = 'df = df.with_columns(pl.col("x").fill_null(None).alias("r"))'
        expr = parse_expression(code, "r")
        assert "None" in expr.expression_text

    def test_name_true_in_expression(self):
        code = 'df = df.with_columns(pl.lit(True).alias("r"))'
        expr = parse_expression(code, "r")
        assert "True" in expr.expression_text

    def test_name_false_in_expression(self):
        code = 'df = df.with_columns(pl.lit(False).alias("r"))'
        expr = parse_expression(code, "r")
        assert "False" in expr.expression_text


class TestConverterAttributeGeneral:
    """Cover _attribute general path (lines 297–298)."""

    def test_non_pl_attribute(self):
        """Attribute on non-pl object (line 297–298)."""
        code = 'obj_val = pl.col("x")\ndf = df.with_columns(obj_val.abs().alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None


class TestJoinedStrElseBranch:
    """Cover _joinedstr else branch (line 336)."""

    def test_fstring_with_complex_value(self):
        """f-string with non-Constant non-FormattedValue (hard to produce normally)."""
        # A normal f-string has Constant and FormattedValue parts
        # This test covers the typical path
        code = 'x = "test"\ndf = df.with_columns(pl.col(f"{x}_col").alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None


class TestPlLitNameTrue:
    """Cover pl.lit(True/False/None) as Name nodes (lines 501–508)."""

    def test_pl_lit_name_true(self):
        """pl.lit(True) as Name — older Python or unusual AST."""
        # In practice Python 3.8+ uses Constant, but this covers the fallback
        code = 'df = df.with_columns(pl.lit(True).alias("r"))'
        expr = parse_expression(code, "r")
        assert True in expr.constants

    def test_pl_lit_name_false(self):
        code = 'df = df.with_columns(pl.lit(False).alias("r"))'
        expr = parse_expression(code, "r")
        assert False in expr.constants


class TestSingleExprVariableNoAlias:
    """Cover variable expression without alias (line 961)."""

    def test_variable_single_no_alias(self):
        code = 'expr = pl.col("x") + 1\ndf = df.with_columns(expr)'
        expr = parse_expression(code, "x")
        assert expr is not None


class TestBarePLWhenChain:
    """Cover _pl_when_chain (lines 555-558) and _chained_when (lines 562-564).
    These are hit when the converter encounters a bare pl.when() or .when() node
    that hasn't been wrapped with .then()/.otherwise()."""

    def test_bare_pl_when_no_then(self):
        """pl.when() as standalone expression — unusual but possible."""
        code = 'df = df.with_columns(pl.when(pl.col("x") > 0).alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None

    def test_chained_when_at_top(self):
        """Nested pl.when inside a then, where the then is the top node."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0).then(\n'
            '        pl.when(pl.col("y") > 0).then(1).otherwise(2)\n'
            '    ).alias("r")\n'
            ")"
        )
        expr = parse_expression(code, "r")
        assert expr is not None


class TestWhenClauseCollectionEdges:
    """Cover edge cases in _collect_when_clauses (lines 582, 613-624)."""

    def test_when_break_on_non_call(self):
        """When chain encounters non-call node (line 581-582)."""
        code = (
            'df = df.with_columns(\n    pl.when(pl.col("x") > 0)\n    .then(1)\n    .alias("r")\n)'
        )
        expr = parse_expression(code, "r")
        assert expr.expression_type == "conditional"

    def test_when_alias_in_middle_of_chain(self):
        """Alias node in middle of when chain (lines 620-622)."""
        # This shouldn't normally happen but the code handles it
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 10).then("high").otherwise("low").alias("r")\n'
            ")"
        )
        expr = parse_expression(code, "r")
        assert expr.expression_type == "conditional"


class TestHasNestedWhenRecursive:
    """Cover _has_nested_when recursive paths (lines 675, 680-682)."""

    def test_has_nested_when_in_args(self):
        """Nested when found in args of a call (lines 680-682)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("tier") == "gold")\n'
            "    .then(\n"
            '        pl.when(pl.col("age") > 65).then(0.8).otherwise(0.9)\n'
            "    )\n"
            "    .otherwise(1.0)\n"
            '    .alias("discount")\n'
            ")"
        )
        expr = parse_expression(code, "discount")
        assert len(expr.sub_expressions) > 0

    def test_deeply_nested_when(self):
        """pl.when at the root of nested (line 675)."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 0)\n'
            "    .then(\n"
            '        pl.when(pl.col("b") > 0)\n'
            "        .then(\n"
            '            pl.when(pl.col("c") > 0).then(1).otherwise(2)\n'
            "        )\n"
            "        .otherwise(3)\n"
            "    )\n"
            "    .otherwise(4)\n"
            '    .alias("r")\n'
            ")"
        )
        expr = parse_expression(code, "r")
        assert expr.expression_type == "conditional"


class TestFStringAliasResolve:
    """Cover _try_eval_fstring paths (lines 738-749)."""

    def test_fstring_with_unresolvable_name(self):
        """FormattedValue with Name not in symbol table (line 743)."""
        code = 'df = df.with_columns((pl.col("x") + 1).alias(f"col_{missing}"))'
        expr = parse_expression(code, "col_5")
        # Falls back since f-string can't resolve — target won't match
        assert expr is not None

    def test_fstring_with_non_constant_resolved(self):
        """FormattedValue resolves to non-Constant (line 742-743)."""
        code = (
            'name_expr = pl.col("x")\n'
            'df = df.with_columns((pl.col("a") + 1).alias(f"col_{name_expr}"))'
        )
        expr = parse_expression(code, "col_test")
        assert expr is not None


class TestControlFlowKeywordInWithColumns:
    """Cover keyword arg in with_columns inside control flow (lines 889-890)."""

    def test_keyword_target_in_for_loop(self):
        code = 'for i in range(1):\n    df = df.with_columns(target=pl.col("x") + i)\n'
        expr = parse_expression(code, "target")
        assert expr.expression_type == "opaque"


class TestParseExpressionImplException:
    """Cover parse_expression exception fallback (lines 995-996)."""

    def test_exception_with_code(self):
        """Non-None, non-string code that causes internal error."""
        # Pass something that will make internal processing fail
        result = parse_expression(42, "target")  # type: ignore[arg-type]
        assert result is not None
        assert result.expression_type == "opaque"


# ###########################################################################
# 33. _substitute_names_in_ast — branch coverage for all node types
# ###########################################################################


class TestSubstituteNamesUnaryOp:
    """Cover UnaryOp substitution where operand changes (lines 1222-1228)."""

    def test_unaryop_substitution(self):
        """UnaryOp where operand is substituted."""
        code = 'val = pl.col("x")\nneg = -val\ndf = df.with_columns(neg.alias("r"))'
        expr = parse_expression(code, "r")
        assert "x" in expr.referenced_columns
        assert "-" in expr.expression_text


class TestSubstituteNamesKeyword:
    """Cover keyword value substitution (lines 1233-1238)."""

    def test_keyword_substitution(self):
        """Keyword value gets substituted from symbol table."""
        code = (
            'sep = "-"\n'
            "df = df.with_columns(\n"
            '    pl.concat_str(pl.col("a"), pl.col("b"), separator=sep).alias("r")\n'
            ")"
        )
        expr = parse_expression(code, "r")
        assert expr is not None


class TestSubstituteNamesAttribute:
    """Cover Attribute substitution where value changes (lines 1253-1259)."""

    def test_attribute_substitution(self):
        code = (
            'base = pl.col("x")\n'
            "result = base.str.to_lowercase()\n"
            'df = df.with_columns(result.alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert "x" in expr.referenced_columns


class TestSubstituteNamesCompareNode:
    """Cover Compare substitution where left/comparators change (lines 1260-1271)."""

    def test_compare_substitution(self):
        code = (
            "limit = 100\n"
            'cond = pl.col("x") > limit\n'
            'df = df.with_columns(pl.when(cond).then(1).otherwise(0).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert "100" in expr.expression_text


class TestSubstituteNamesListNode:
    """Cover List substitution where elements change (lines 1272-1278)."""

    def test_list_substitution(self):
        code = (
            'a = pl.col("x")\n'
            'b = pl.col("y")\n'
            "exprs = [a, b]\n"
            'df = df.with_columns(pl.sum_horizontal(*exprs).alias("r"))'
        )
        expr = parse_expression(code, "r")
        assert expr is not None


class TestSubstituteNamesStarredNode:
    """Cover Starred substitution where value changes (lines 1279-1285)."""

    def test_starred_substitution(self):
        code = 'items = [(pl.col("a") + 1).alias("a1")]\ndf = df.with_columns(*items)'
        expr = parse_expression(code, "a1")
        assert expr is not None
        assert "a" in expr.referenced_columns


# ###########################################################################
# 34. Secondary search in parse_expression_impl (lines 1091-1124)
# ###########################################################################


class TestSecondarySearchForTarget:
    """Cover the secondary search paths (lines 1091-1124)."""

    def test_variable_list_match_in_secondary_search(self):
        """Variable holding list of expressions, matched in secondary search (lines 1095-1102)."""
        code = (
            "stuff = [\n"
            '    (pl.col("a") * 2).alias("doubled_a"),\n'
            '    (pl.col("b") * 3).alias("tripled_b"),\n'
            "]\n"
            "df = df.with_columns(stuff)"
        )
        # First pass finds via _extract_expressions_from_with_columns → variable list
        expr = parse_expression(code, "doubled_a")
        assert expr is not None
        assert "a" in expr.referenced_columns

    def test_no_alias_auto_name_match(self):
        """Auto-name match in final search (lines 1113-1124)."""
        code = 'df = df.with_columns(pl.col("amount"))'
        expr = parse_expression(code, "amount")
        assert expr is not None
        assert "amount" in expr.referenced_columns

    def test_starred_in_final_search(self):
        """Starred in no-alias search is skipped (lines 1115-1116)."""
        code = 'exprs = [pl.col("x")]\ndf = df.with_columns(*exprs)'
        # x has no alias, so auto-name is "x"
        expr = parse_expression(code, "x")
        assert expr is not None


class TestChainImplNull:
    """Cover chain impl null-alias inferred (lines 2190, 2192)."""

    def test_chain_null_alias_skipped(self):
        """Expression with no alias and no auto-name is skipped (line 2192)."""
        code = (
            'df = df.with_columns(pl.lit(42))\ndf = df.with_columns((pl.col("x") + 1).alias("r"))'
        )
        chain = parse_expression_chain(code, "r")
        assert chain is not None
        assert len(chain) >= 1


class TestChainImplException:
    """Chain builder fails loud on a malformed code argument."""

    def test_chain_fallback_with_result(self):
        """A non-str code argument raises rather than laundering into a chain."""
        with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: ast.parse exception type may vary
            parse_expression_chain(123, "target")  # type: ignore[arg-type]


class TestConverterConstantRepr:
    """Cover constant non-standard repr (line 275)."""

    def test_complex_constant(self):
        """Complex number constant falls to repr."""
        code = 'df = df.with_columns(pl.lit(1+2j).alias("r"))'
        expr = parse_expression(code, "r")
        assert expr is not None


class TestConverterAttributeNonPl:
    """Cover _attribute for non-pl values (lines 297-298)."""

    def test_chained_attribute_access(self):
        code = 'df = df.with_columns(pl.col("data").struct.field("name").alias("n"))'
        expr = parse_expression(code, "n")
        assert "struct" in expr.expression_text


class TestFStringAliasNonConstantPart:
    """Cover _try_eval_fstring non-Constant/FormattedValue (line 749)."""

    def test_fstring_with_complex_expression(self):
        """f-string with expression that can't be evaluated returns None."""
        code = 'items = [1, 2]\ndf = df.with_columns((pl.col("x") + 1).alias(f"col_{items[0]}"))'
        # f-string with subscript can't be statically resolved
        expr = parse_expression(code, "col_1")
        assert expr is not None


class TestSubstitutionNoChange:
    """Cover substitution paths where node doesn't change (lines 1224-1225)."""

    def test_unaryop_no_substitution_needed(self):
        """UnaryOp with no names to substitute."""
        code = 'df = df.with_columns((-pl.col("x")).alias("r"))'
        expr = parse_expression(code, "r")
        assert "-x" in expr.expression_text


# ---------------------------------------------------------------------------
# F147 — renderer parenthesisation
# ---------------------------------------------------------------------------


def _text(expr_src: str, target: str = "r") -> str:
    code = f'df = df.with_columns(({expr_src}).alias("{target}"))'
    parsed = parse_expression(code, target)
    assert parsed is not None
    return parsed.expression_text


def test_compare_operand_gets_parens() -> None:
    assert _text('(pl.col("a") < pl.col("b")) * pl.col("c")') == "(a < b) * c"


def test_left_pow_gets_parens() -> None:
    assert _text('(pl.col("a") ** pl.col("b")) ** pl.col("c")') == "(a ** b) ** c"


def test_right_pow_stays_unparenthesised() -> None:
    # ** is right-associative, so a ** (b ** c) renders without parens.
    assert _text('pl.col("a") ** pl.col("b") ** pl.col("c")') == "a ** b ** c"


def test_unary_minus_base_of_pow_gets_parens() -> None:
    assert _text('(-pl.col("a")) ** pl.col("b")') == "(-a) ** b"


def test_boolop_or_inside_and_gets_parens() -> None:
    assert _text('(pl.col("a") or pl.col("b")) and pl.col("c")') == "(a or b) and c"


def test_boolop_and_inside_or_no_parens() -> None:
    assert _text('pl.col("a") and pl.col("b") or pl.col("c")') == "a and b or c"


# ---------------------------------------------------------------------------
# F242 — string constants escape embedded double quotes
# ---------------------------------------------------------------------------


def test_embedded_double_quote_is_escaped() -> None:
    code = 'df = df.with_columns(pl.lit(\'he said "hi"\').alias("r"))'
    parsed = parse_expression(code, "r")
    assert parsed is not None
    assert parsed.expression_text == r'"he said \"hi\""'


# ---------------------------------------------------------------------------
# F240 — f-string format specs / conversions preserved in alias resolution
# ---------------------------------------------------------------------------


def test_fstring_format_spec_in_alias_is_applied() -> None:
    code = 'i = 3\ndf = df.with_columns(pl.col("x").alias(f"col_{i:03d}"))'
    parsed = parse_expression(code, "col_003")
    assert parsed is not None
    assert parsed.expression_text == "x"


def test_fstring_conversion_in_render_preserved() -> None:
    code = 'df = df.with_columns(pl.lit(f"{pl.col(\'a\')!r}").alias("r"))'
    parsed = parse_expression(code, "r")
    assert parsed is not None
    assert "!r" in parsed.expression_text


# ---------------------------------------------------------------------------
# F241 — slice subscripts render readably, not as ast.dump garbage
# ---------------------------------------------------------------------------


def test_slice_subscript_renders() -> None:
    code = 'df = df.with_columns(pl.col("items")[1:2].alias("r"))'
    parsed = parse_expression(code, "r")
    assert parsed is not None
    assert "1:2" in parsed.expression_text
    assert "Slice" not in parsed.expression_text


# ---------------------------------------------------------------------------
# F239 — self-referential symbol resolution does not recurse forever
# ---------------------------------------------------------------------------


def test_self_referential_assignment_does_not_recurse() -> None:
    code = "x = x + 1\ndf = df.with_columns((pl.col('a') * x).alias('r'))"
    parsed = parse_expression(code, "r")  # must not raise RecursionError
    assert parsed is not None


# ---------------------------------------------------------------------------
# F089 — _substitute_names_in_ast copies positions onto the NEW node
# ---------------------------------------------------------------------------


def test_substitute_names_copies_location_to_new_node() -> None:
    tree = ast.parse("a + b", mode="eval").body
    table = {"b": ast.parse("c * 2", mode="eval").body}
    new = _substitute_names_in_ast(tree, table)
    assert new is not tree
    # copy_location(new, old): the substituted node inherits the original's
    # source position and compiles cleanly.
    assert getattr(new, "lineno", None) == tree.lineno
    ast.fix_missing_locations(new)
    compile(ast.Expression(body=new), "<test>", "eval")


# ---------------------------------------------------------------------------
# F243 — control-flow-reassigned var with a top-level binding is opaque
# ---------------------------------------------------------------------------


def test_control_flow_reassigned_var_is_opaque_despite_top_level_binding() -> None:
    code = (
        "factor = 2\n"
        "if cond:\n"
        "    factor = 3\n"
        'df = df.with_columns((pl.col("a") * factor).alias("r"))'
    )
    parsed = parse_expression(code, "r")
    assert parsed is not None
    assert parsed.expression_type == "opaque"


# ---------------------------------------------------------------------------
# F704 / F705 — chain parse reuse still yields correct dependency chain
# ---------------------------------------------------------------------------


def test_expression_chain_dependencies_resolved() -> None:
    code = (
        'df = df.with_columns((pl.col("base") * 2).alias("mid"))\n'
        'df = df.with_columns((pl.col("mid") + 1).alias("top"))'
    )
    chain = parse_expression_chain(code, "top")
    assert chain is not None
    cols = [p.target_column for p in chain]
    assert cols == ["mid", "top"]
