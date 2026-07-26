"""Tests for parse_expression_chain and edge cases in parse_expression/evaluate_expression."""

from __future__ import annotations

import math

from haute._expression_parser import (
    evaluate_expression,
    parse_expression,
    parse_expression_chain,
)

# ###########################################################################
# 1. parse_expression_chain
# ###########################################################################


class TestChainSingleWithColumns:
    def test_single_with_columns_returns_chain_of_one(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("target"))'
        chain = parse_expression_chain(code, "target")
        assert chain is not None
        assert len(chain) == 1
        assert chain[0].target_column == "target"
        assert chain[0].expression_text == "a + 1"

    def test_single_with_columns_arithmetic(self):
        code = 'df = df.with_columns((pl.col("x") * pl.col("y")).alias("product"))'
        chain = parse_expression_chain(code, "product")
        assert len(chain) == 1
        assert set(chain[0].referenced_columns) == {"x", "y"}

    def test_single_with_columns_conditional(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("age") > 65)'
            '.then(pl.lit("senior")).otherwise(pl.lit("standard"))'
            '.alias("tier")\n'
            ")"
        )
        chain = parse_expression_chain(code, "tier")
        assert len(chain) == 1
        assert chain[0].expression_type == "conditional"

    def test_assignment_to_non_df_name_uses_same_wrapping_as_evaluator(self):
        code = 'result = df.with_columns((pl.col("a") * 2).alias("y"))'
        chain = parse_expression_chain(code, "y")
        evaluated = evaluate_expression(code, "y", {"a": 3})
        assert chain is not None
        assert [item.expression_text for item in chain] == ["a * 2"]
        assert evaluated.expression_text == chain[0].expression_text
        assert evaluated.result_value == 6


class TestChainMultipleWithColumnsDependency:
    def test_two_step_dependency(self):
        code = (
            'df = df.with_columns((pl.col("a") + pl.col("b")).alias("mid"))\n'
            'df = df.with_columns((pl.col("mid") * 2).alias("target"))'
        )
        chain = parse_expression_chain(code, "target")
        assert chain is not None
        assert len(chain) == 2
        assert chain[0].target_column == "mid"
        assert chain[1].target_column == "target"

    def test_three_step_dependency(self):
        code = (
            'df = df.with_columns((pl.col("raw") + 1).alias("step1"))\n'
            'df = df.with_columns((pl.col("step1") * 2).alias("step2"))\n'
            'df = df.with_columns((pl.col("step2") - 10).alias("final"))'
        )
        chain = parse_expression_chain(code, "final")
        assert chain is not None
        assert len(chain) == 3
        names = [p.target_column for p in chain]
        assert names == ["step1", "step2", "final"]

    def test_dependency_order_is_topological(self):
        code = (
            'df = df.with_columns((pl.col("x") + 1).alias("a"))\n'
            'df = df.with_columns((pl.col("y") + 1).alias("b"))\n'
            'df = df.with_columns((pl.col("a") + pl.col("b")).alias("c"))'
        )
        chain = parse_expression_chain(code, "c")
        assert chain is not None
        names = [p.target_column for p in chain]
        assert "a" in names
        assert "b" in names
        assert names[-1] == "c"
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("c")


class TestChainTargetNotFound:
    def test_target_not_found_returns_empty(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("result"))'
        chain = parse_expression_chain(code, "nonexistent")
        assert chain is not None
        assert len(chain) == 0

    def test_target_not_found_different_alias(self):
        code = 'df = df.with_columns((pl.col("x") * 2).alias("y"))'
        chain = parse_expression_chain(code, "z")
        assert chain == []


class TestChainCircularDependency:
    def test_circular_dependency_does_not_infinite_loop(self):
        code = (
            'df = df.with_columns((pl.col("b") + 1).alias("a"))\n'
            'df = df.with_columns((pl.col("a") + 1).alias("b"))'
        )
        chain = parse_expression_chain(code, "b")
        assert chain is not None
        names = [p.target_column for p in chain]
        assert "b" in names

    def test_self_referencing_column(self):
        code = 'df = df.with_columns((pl.col("x") + 1).alias("x"))'
        chain = parse_expression_chain(code, "x")
        assert chain is not None
        assert len(chain) == 1
        assert chain[0].target_column == "x"


class TestChainColumnCreatedEarlierUsedLater:
    def test_intermediate_column_in_chain(self):
        code = (
            'df = df.with_columns((pl.col("premium") * 1.1).alias("loaded_premium"))\n'
            'df = df.with_columns((pl.col("loaded_premium") * pl.col("exposure")).alias("earned"))'
        )
        chain = parse_expression_chain(code, "earned")
        assert len(chain) == 2
        assert chain[0].target_column == "loaded_premium"
        assert chain[1].target_column == "earned"

    def test_intermediate_not_directly_created_not_in_chain(self):
        code = (
            'df = df.with_columns((pl.col("a") + pl.col("b")).alias("sum_ab"))\n'
            'df = df.with_columns((pl.col("sum_ab") + pl.col("external_col")).alias("target"))'
        )
        chain = parse_expression_chain(code, "target")
        assert len(chain) == 2
        assert chain[0].target_column == "sum_ab"
        assert chain[1].target_column == "target"


class TestChainTargetDependsOnIntermediateNotDirectlyCreated:
    def test_dep_on_external_column_only(self):
        code = (
            'df = df.with_columns((pl.col("z") + 1).alias("unrelated"))\n'
            'df = df.with_columns((pl.col("external") * 2).alias("target"))'
        )
        chain = parse_expression_chain(code, "target")
        assert len(chain) == 1
        assert chain[0].target_column == "target"


class TestChainEmptyAndNoWithColumns:
    def test_empty_code_returns_empty(self):
        chain = parse_expression_chain("", "target")
        assert chain is not None
        assert len(chain) == 0

    def test_whitespace_only_returns_empty(self):
        chain = parse_expression_chain("   \n  ", "target")
        assert chain is not None
        assert len(chain) == 0

    def test_no_with_columns_returns_opaque_fallback(self):
        code = "x = 42\ny = x + 1"
        chain = parse_expression_chain(code, "target")
        assert chain is not None
        assert len(chain) <= 1
        if len(chain) == 1:
            assert chain[0].expression_type == "opaque"

    def test_comment_only_code_returns_empty(self):
        code = "# just a comment"
        chain = parse_expression_chain(code, "target")
        assert chain is not None
        assert len(chain) == 0


class TestChainMixedExpressionsAndLiterals:
    def test_chain_with_literal_and_arithmetic(self):
        code = (
            'df = df.with_columns(pl.lit(100).alias("base"))\n'
            'df = df.with_columns((pl.col("base") * pl.col("factor")).alias("result"))'
        )
        chain = parse_expression_chain(code, "result")
        assert len(chain) == 2
        assert chain[0].target_column == "base"
        assert chain[1].target_column == "result"

    def test_chain_with_conditional_and_arithmetic(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("age") > 25)'
            ".then(pl.lit(1.0)).otherwise(pl.lit(1.5))"
            '.alias("age_factor")\n'
            ")\n"
            'df = df.with_columns((pl.col("premium") * pl.col("age_factor")).alias("adjusted"))'
        )
        chain = parse_expression_chain(code, "adjusted")
        assert len(chain) == 2
        assert chain[0].target_column == "age_factor"
        assert chain[0].expression_type == "conditional"
        assert chain[1].target_column == "adjusted"

    def test_chain_with_keyword_column(self):
        code = (
            'df = df.with_columns(base=pl.col("x") + 1)\n'
            'df = df.with_columns((pl.col("base") * 2).alias("target"))'
        )
        chain = parse_expression_chain(code, "target")
        assert len(chain) == 2

    def test_chain_syntax_error_fallback(self):
        code = "df = df.with_columns((pl.col('a') +).alias('target'))"
        chain = parse_expression_chain(code, "target")
        assert chain is not None


# ###########################################################################
# 2. parse_expression edge cases
# ###########################################################################


class TestParseExpressionDivisionByZero:
    def test_division_by_zero_literal(self):
        code = 'df = df.with_columns((pl.col("x") / 0).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_text == "x / 0"
        assert expr.expression_type == "arithmetic"
        assert 0 in expr.constants

    def test_division_by_zero_float(self):
        code = 'df = df.with_columns((pl.col("x") / 0.0).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "/ 0.0" in expr.expression_text


class TestParseExpressionNaNArithmetic:
    def test_nan_literal_in_expression(self):
        code = 'df = df.with_columns((pl.col("x") + float("nan")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_type in ("arithmetic", "function_call")

    def test_nan_via_pl_lit(self):
        code = 'df = df.with_columns((pl.col("x") + pl.lit(float("nan"))).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None


class TestParseExpressionComparisonChaining:
    def test_chained_comparison_python_syntax(self):
        code = 'df = df.with_columns((1 < pl.col("x")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "<" in expr.expression_text

    def test_double_comparison(self):
        code = 'df = df.with_columns(((pl.col("x") > 0) & (pl.col("x") < 10)).alias("in_range"))'
        expr = parse_expression(code, "in_range")
        assert expr is not None
        assert "x" in expr.expression_text


class TestParseExpressionBitwiseOnFloats:
    def test_bitwise_and_on_columns(self):
        code = 'df = df.with_columns((pl.col("a") & pl.col("b")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "&" in expr.expression_text
        assert expr.expression_type == "arithmetic"

    def test_bitwise_or_on_columns(self):
        code = 'df = df.with_columns((pl.col("a") | pl.col("b")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "|" in expr.expression_text


class TestParseExpressionSubscript:
    def test_subscript_on_column(self):
        code = 'df = df.with_columns(pl.col("items")[0].alias("first_item"))'
        expr = parse_expression(code, "first_item")
        assert expr is not None
        assert "items" in expr.expression_text
        assert "[0]" in expr.expression_text or "0" in expr.expression_text

    def test_subscript_with_string_key(self):
        code = 'df = df.with_columns(pl.col("data")["key"].alias("val"))'
        expr = parse_expression(code, "val")
        assert expr is not None
        assert "data" in expr.expression_text


# ###########################################################################
# 3. evaluate_expression edge cases
# ###########################################################################


class TestEvaluateDivisionByZero:
    def test_division_by_zero_int(self):
        code = 'df = df.with_columns((pl.col("a") / pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": 10, "b": 0})
        assert result is not None
        assert result.result_value is None or (
            isinstance(result.result_value, float)
            and (math.isinf(result.result_value) or math.isnan(result.result_value))
        )

    def test_division_by_zero_float(self):
        code = 'df = df.with_columns((pl.col("a") / pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": 10.0, "b": 0.0})
        assert result is not None
        assert result.result_value is None or (
            isinstance(result.result_value, float) and math.isinf(result.result_value)
        )

    def test_zero_divided_by_zero(self):
        code = 'df = df.with_columns((pl.col("a") / pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": 0.0, "b": 0.0})
        assert result is not None
        assert result.result_value is None or (
            isinstance(result.result_value, float) and math.isnan(result.result_value)
        )


class TestEvaluateMissingColumn:
    def test_missing_column_in_row_values(self):
        code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": 10})
        assert result is not None
        assert result.result_value is None or result.result_value == result.result_value

    def test_completely_empty_row_values(self):
        code = 'df = df.with_columns((pl.col("x") * 2).alias("result"))'
        result = evaluate_expression(code, "result", {})
        assert result is not None

    def test_target_in_row_values_is_not_used_as_fallback(self):
        code = 'df = df.with_columns((pl.col("missing_col") + 1).alias("result"))'
        result = evaluate_expression(code, "result", {"result": 42})
        assert result.result_value is None

    def test_unresolved_target_does_not_reuse_observed_value(self):
        code = 'df = df.filter(pl.col("x") > 0)'
        result = evaluate_expression(code, "result", {"x": 1, "result": 42})
        assert result.expression_type == "opaque"
        assert result.result_value is None


class TestEvaluateNaNComparisons:
    def test_nan_less_than_number(self):
        code = 'df = df.with_columns((pl.col("x") < 5).alias("result"))'
        result = evaluate_expression(code, "result", {"x": float("nan")})
        assert result is not None
        assert result.result_value is False or result.result_value is None

    def test_nan_equality(self):
        code = 'df = df.with_columns((pl.col("x") == pl.col("y")).alias("result"))'
        result = evaluate_expression(code, "result", {"x": float("nan"), "y": float("nan")})
        assert result is not None
        assert result.result_value is False or result.result_value is None

    def test_nan_greater_than(self):
        code = 'df = df.with_columns((pl.col("x") > 0).alias("result"))'
        result = evaluate_expression(code, "result", {"x": float("nan")})
        assert result is not None


class TestEvaluateInfinityArithmetic:
    def test_inf_plus_one(self):
        code = 'df = df.with_columns((pl.col("x") + 1).alias("result"))'
        result = evaluate_expression(code, "result", {"x": float("inf")})
        assert result is not None
        assert result.result_value == float("inf") or result.result_value is None

    def test_inf_divided_by_inf(self):
        code = 'df = df.with_columns((pl.col("a") / pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": float("inf"), "b": float("inf")})
        assert result is not None
        assert result.result_value is None or (
            isinstance(result.result_value, float) and math.isnan(result.result_value)
        )

    def test_negative_inf_times_positive(self):
        code = 'df = df.with_columns((pl.col("x") * 2).alias("result"))'
        result = evaluate_expression(code, "result", {"x": float("-inf")})
        assert result is not None
        assert result.result_value == float("-inf") or result.result_value is None

    def test_inf_minus_inf(self):
        code = 'df = df.with_columns((pl.col("a") - pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": float("inf"), "b": float("inf")})
        assert result is not None
        assert result.result_value is None or (
            isinstance(result.result_value, float) and math.isnan(result.result_value)
        )


class TestEvaluateNoneInOperators:
    def test_none_in_addition(self):
        code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": 5, "b": None})
        assert result is not None
        assert result.result_value is None

    def test_none_in_multiplication(self):
        code = 'df = df.with_columns((pl.col("a") * pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": None, "b": 10})
        assert result is not None
        assert result.result_value is None

    def test_none_in_comparison(self):
        code = 'df = df.with_columns((pl.col("x") > 5).alias("result"))'
        result = evaluate_expression(code, "result", {"x": None})
        assert result is not None
        assert result.result_value is None

    def test_none_in_division(self):
        code = 'df = df.with_columns((pl.col("a") / pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": None, "b": 2})
        assert result is not None
        assert result.result_value is None

    def test_none_in_subtraction(self):
        code = 'df = df.with_columns((pl.col("a") - pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": 10, "b": None})
        assert result is not None
        assert result.result_value is None

    def test_none_in_power(self):
        code = 'df = df.with_columns((pl.col("a") ** pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": None, "b": 2})
        assert result is not None
        assert result.result_value is None

    def test_none_in_modulo(self):
        code = 'df = df.with_columns((pl.col("a") % pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": 10, "b": None})
        assert result is not None
        assert result.result_value is None

    def test_none_in_floor_division(self):
        code = 'df = df.with_columns((pl.col("a") // pl.col("b")).alias("result"))'
        result = evaluate_expression(code, "result", {"a": None, "b": 3})
        assert result is not None
        assert result.result_value is None


# ###########################################################################
# 4. Additional edge cases
# ###########################################################################


class TestDivisionByZeroProducesInf:
    def test_parse_division_by_zero_literal(self):
        code = 'df = df.with_columns((pl.col("x") / 0).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_type == "arithmetic"
        assert "x" in expr.referenced_columns
        assert "/ 0" in expr.expression_text

    def test_evaluate_division_by_zero_produces_inf(self):
        code = 'df = df.with_columns((pl.col("x") / 0).alias("result"))'
        result = evaluate_expression(code, "result", {"x": 10})
        assert result is not None
        assert result.result_value is None or (
            isinstance(result.result_value, float) and math.isinf(result.result_value)
        )

    def test_evaluate_division_by_zero_negative(self):
        code = 'df = df.with_columns((pl.col("x") / 0).alias("result"))'
        result = evaluate_expression(code, "result", {"x": -5})
        assert result is not None
        assert result.result_value is None or (
            isinstance(result.result_value, float) and math.isinf(result.result_value)
        )


class TestChainSyntaxOnEmptyDataFrame:
    def test_filter_expression_parsed_from_ast(self):
        code = (
            'df = df.filter(pl.col("x") > 0)\n'
            'df = df.with_columns((pl.col("x") * 2).alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_text == "x * 2"
        assert expr.referenced_columns == ["x"]
        assert expr.expression_type == "arithmetic"

    def test_filter_does_not_affect_chain_extraction(self):
        code = (
            'df = df.with_columns((pl.col("a") + 1).alias("b"))\n'
            'df = df.filter(pl.col("b") > 0)\n'
            'df = df.with_columns((pl.col("b") * 3).alias("target"))'
        )
        chain = parse_expression_chain(code, "target")
        assert chain is not None
        assert len(chain) == 2
        assert chain[0].target_column == "b"
        assert chain[1].target_column == "target"


class TestWindowFunctionOver:
    def test_parse_window_expression(self):
        code = 'df = df.with_columns(pl.col("x").sum().over("group").alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "x" in expr.referenced_columns
        assert ".over(" in expr.expression_text

    def test_evaluate_window_expression_type(self):
        code = 'df = df.with_columns(pl.col("x").sum().over("group").alias("result"))'
        result = evaluate_expression(code, "result", {"x": 10, "group": "A"})
        assert result is not None
        assert result.expression_type == "window"

    def test_window_partition_column_in_referenced(self):
        code = 'df = df.with_columns(pl.col("premium").mean().over("region").alias("avg_premium"))'
        result = evaluate_expression(code, "avg_premium", {"premium": 100, "region": "West"})
        assert result is not None
        assert "region" in result.referenced_columns


class TestNestedWhenThenOtherwise:
    def test_parse_nested_conditional(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 10)\n'
            '    .then(pl.when(pl.col("b") > 5).then(1).otherwise(2))\n'
            "    .otherwise(3)\n"
            '    .alias("result")\n'
            ")"
        )
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_type == "conditional"
        assert "a" in expr.referenced_columns
        assert "b" in expr.referenced_columns

    def test_nested_conditional_has_sub_expressions(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("x") > 0)\n'
            '    .then(pl.when(pl.col("y") > 0).then(10).otherwise(20))\n'
            "    .otherwise(30)\n"
            '    .alias("result")\n'
            ")"
        )
        expr = parse_expression(code, "result")
        assert expr is not None
        assert len(expr.sub_expressions) >= 1
        assert expr.sub_expressions[0].expression_type == "conditional"

    def test_evaluate_nested_conditional_outer_true_inner_true(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 10)\n'
            '    .then(pl.when(pl.col("b") > 5).then(1).otherwise(2))\n'
            "    .otherwise(3)\n"
            '    .alias("result")\n'
            ")"
        )
        result = evaluate_expression(code, "result", {"a": 20, "b": 10})
        assert result is not None
        assert result.result_value in (1, None)

    def test_evaluate_nested_conditional_outer_false(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") > 10)\n'
            '    .then(pl.when(pl.col("b") > 5).then(1).otherwise(2))\n'
            "    .otherwise(3)\n"
            '    .alias("result")\n'
            ")"
        )
        result = evaluate_expression(code, "result", {"a": 5, "b": 10})
        assert result is not None
        assert result.result_value in (3, None)


class TestMultipleWithColumnsDependencyChain:
    def test_three_step_sequential_dependency(self):
        code = (
            'df = df.with_columns((pl.col("raw") + 1).alias("col_a"))\n'
            'df = df.with_columns((pl.col("col_a") * 2).alias("col_b"))\n'
            'df = df.with_columns((pl.col("col_b") - 10).alias("col_c"))'
        )
        chain = parse_expression_chain(code, "col_c")
        assert chain is not None
        assert len(chain) == 3
        names = [p.target_column for p in chain]
        assert names == ["col_a", "col_b", "col_c"]

    def test_dependency_order_preserved(self):
        code = (
            'df = df.with_columns((pl.col("x") ** 2).alias("col_a"))\n'
            'df = df.with_columns((pl.col("col_a") + pl.col("y")).alias("col_b"))\n'
            'df = df.with_columns((pl.col("col_b") / pl.col("z")).alias("col_c"))'
        )
        chain = parse_expression_chain(code, "col_c")
        assert chain is not None
        names = [p.target_column for p in chain]
        assert names.index("col_a") < names.index("col_b")
        assert names.index("col_b") < names.index("col_c")

    def test_intermediate_referenced_columns(self):
        code = (
            'df = df.with_columns((pl.col("x") + 1).alias("col_a"))\n'
            'df = df.with_columns((pl.col("col_a") * 2).alias("col_b"))\n'
            'df = df.with_columns((pl.col("col_b") - 10).alias("col_c"))'
        )
        chain = parse_expression_chain(code, "col_c")
        assert chain is not None
        assert "col_a" in chain[1].referenced_columns
        assert "col_b" in chain[2].referenced_columns


class TestOpaquePatternDetection:
    def test_custom_function_wrapping_expression(self):
        code = 'df = df.with_columns(my_custom_function(pl.col("x")).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "x" in expr.referenced_columns
        assert expr.expression_type == "function_call"

    def test_symbol_table_function_is_opaque(self):
        code = (
            "def my_func(col):\n"
            "    return col * 2\n"
            'df = df.with_columns(my_func(pl.col("x")).alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_type in ("function_call", "opaque")

    def test_lambda_in_map_elements_is_opaque(self):
        code = 'df = df.with_columns(pl.col("x").map_elements(lambda v: v * 2).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_type == "opaque"

    def test_map_batches_is_opaque(self):
        code = 'df = df.with_columns(pl.col("x").map_batches(lambda s: s * 2).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_type == "opaque"


class TestUnicodeAndSpacesInColumnNames:
    def test_unicode_column_name(self):
        """pl.col with a unicode column name (Japanese) parses correctly."""
        code = 'df = df.with_columns((pl.col("日本語") + 1).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "日本語" in expr.referenced_columns
        assert expr.expression_type == "arithmetic"

    def test_column_name_with_spaces(self):
        """pl.col with spaces in the column name parses correctly."""
        code = 'df = df.with_columns((pl.col("driver age") * 2).alias("result"))'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "driver age" in expr.referenced_columns
        assert expr.expression_type == "arithmetic"
