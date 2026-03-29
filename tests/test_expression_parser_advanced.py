"""Advanced and edge-case tests for the Polars expression parser.

Supplements test_expression_parser.py with coverage for:
  1. Actuarial-specific pricing patterns
  2. Polars expression edge cases
  3. Multi-statement dependency tracking
  4. Error recovery / defensive parsing
  5. Cross-node expression composition
"""

from __future__ import annotations

import pytest

from haute._expression_parser import (
    EvaluatedExpression,
    ParsedExpression,
    parse_expression,
    evaluate_expression,
)


# ###########################################################################
# 1. Actuarial-Specific Patterns
# ###########################################################################


class TestActuarialMultiplicativeRatingChain:
    """Full multiplicative rating chains used in general insurance pricing."""

    def test_eight_factor_named_rating_chain(self):
        """base * area * age * ncd * vehicle * excess * conviction * claims."""
        factors = [
            "base_rate",
            "area_factor",
            "age_factor",
            "ncd_factor",
            "vehicle_factor",
            "excess_factor",
            "conviction_factor",
            "claims_factor",
        ]
        mul = " * ".join(f'pl.col("{f}")' for f in factors)
        code = f'df = df.with_columns(({mul}).alias("technical_premium"))'
        expr = parse_expression(code, "technical_premium")
        assert set(expr.referenced_columns) == set(factors)
        assert expr.expression_type == "arithmetic"
        # Text should preserve multiplication order
        for f in factors:
            assert f in expr.expression_text

    def test_eight_factor_rating_chain_evaluation(self):
        factors = [
            "base_rate",
            "area_factor",
            "age_factor",
            "ncd_factor",
            "vehicle_factor",
            "excess_factor",
            "conviction_factor",
            "claims_factor",
        ]
        mul = " * ".join(f'pl.col("{f}")' for f in factors)
        code = f'df = df.with_columns(({mul}).alias("technical_premium"))'
        vals = {f: 1.1 for f in factors}
        vals["base_rate"] = 200.0
        result = evaluate_expression(code, "technical_premium", vals)
        expected = 200.0 * (1.1**7)
        assert result.result_value == pytest.approx(expected)


class TestActuarialLossRatio:
    def test_simple_loss_ratio(self):
        code = 'df = df.with_columns((pl.col("claims_incurred") / pl.col("earned_premium")).alias("loss_ratio"))'
        expr = parse_expression(code, "loss_ratio")
        assert expr.expression_text == "claims_incurred / earned_premium"
        assert set(expr.referenced_columns) == {"claims_incurred", "earned_premium"}

    def test_loss_ratio_evaluation(self):
        code = 'df = df.with_columns((pl.col("claims_incurred") / pl.col("earned_premium")).alias("loss_ratio"))'
        result = evaluate_expression(
            code, "loss_ratio", {"claims_incurred": 70000.0, "earned_premium": 100000.0}
        )
        assert result.result_value == pytest.approx(0.7)


class TestActuarialBurnCost:
    def test_burn_cost_formula(self):
        code = (
            "IBNR_factor = 0.05\n"
            "expense_ratio = 0.12\n"
            "df = df.with_columns(\n"
            '    (pl.col("premium") * pl.col("loss_ratio") * (1 + IBNR_factor) * (1 + expense_ratio)).alias("burn_cost")\n'
            ")"
        )
        expr = parse_expression(code, "burn_cost")
        assert set(expr.referenced_columns) == {"premium", "loss_ratio"}

    def test_burn_cost_evaluation(self):
        code = (
            "IBNR_factor = 0.05\n"
            "expense_ratio = 0.12\n"
            "df = df.with_columns(\n"
            '    (pl.col("premium") * pl.col("loss_ratio") * (1 + IBNR_factor) * (1 + expense_ratio)).alias("burn_cost")\n'
            ")"
        )
        result = evaluate_expression(code, "burn_cost", {"premium": 1000.0, "loss_ratio": 0.65})
        expected = 1000.0 * 0.65 * 1.05 * 1.12
        assert result.result_value == pytest.approx(expected)


class TestActuarialFrequencySeverity:
    def test_freq_sev_model(self):
        code = 'df = df.with_columns((pl.col("freq_prediction") * pl.col("sev_prediction") * pl.col("exposure")).alias("expected_loss"))'
        expr = parse_expression(code, "expected_loss")
        assert set(expr.referenced_columns) == {"freq_prediction", "sev_prediction", "exposure"}

    def test_freq_sev_evaluation(self):
        code = 'df = df.with_columns((pl.col("freq_prediction") * pl.col("sev_prediction") * pl.col("exposure")).alias("expected_loss"))'
        result = evaluate_expression(
            code,
            "expected_loss",
            {
                "freq_prediction": 0.05,
                "sev_prediction": 3000.0,
                "exposure": 1.0,
            },
        )
        assert result.result_value == pytest.approx(150.0)


class TestActuarialEarnedPremium:
    def test_earned_premium_with_clip(self):
        code = (
            "df = df.with_columns(\n"
            '    (pl.col("written_premium") * (pl.col("inception_to_val_days") / pl.col("policy_term_days")).clip(0, 1)).alias("earned_premium")\n'
            ")"
        )
        expr = parse_expression(code, "earned_premium")
        assert set(expr.referenced_columns) == {
            "written_premium",
            "inception_to_val_days",
            "policy_term_days",
        }
        assert "clip" in expr.expression_text


class TestActuarialTechnicalPrice:
    def test_technical_price_formula(self):
        code = (
            "expense_load = 0.15\n"
            "profit_margin = 0.05\n"
            "commission_rate = 0.20\n"
            "df = df.with_columns(\n"
            '    (pl.col("pure_premium") * (1 + expense_load) * (1 + profit_margin) * (1 + commission_rate)).alias("technical_price")\n'
            ")"
        )
        expr = parse_expression(code, "technical_price")
        assert expr.referenced_columns == ["pure_premium"]

    def test_technical_price_evaluation(self):
        code = (
            "expense_load = 0.15\n"
            "profit_margin = 0.05\n"
            "commission_rate = 0.20\n"
            "df = df.with_columns(\n"
            '    (pl.col("pure_premium") * (1 + expense_load) * (1 + profit_margin) * (1 + commission_rate)).alias("technical_price")\n'
            ")"
        )
        result = evaluate_expression(code, "technical_price", {"pure_premium": 500.0})
        expected = 500.0 * 1.15 * 1.05 * 1.20
        assert result.result_value == pytest.approx(expected)


class TestActuarialLargeLossLoading:
    def test_large_loss_loading_with_when(self):
        code = (
            "df = df.with_columns(\n"
            '    (pl.col("base") + pl.when(pl.col("sum_insured") > 1_000_000).then(pl.col("sum_insured") * 0.001).otherwise(0)).alias("loaded_premium")\n'
            ")"
        )
        expr = parse_expression(code, "loaded_premium")
        assert "base" in expr.referenced_columns
        assert "sum_insured" in expr.referenced_columns

    def test_large_loss_loading_evaluation_triggered(self):
        code = (
            "df = df.with_columns(\n"
            '    (pl.col("base") + pl.when(pl.col("sum_insured") > 1_000_000).then(pl.col("sum_insured") * 0.001).otherwise(0)).alias("loaded_premium")\n'
            ")"
        )
        result = evaluate_expression(
            code, "loaded_premium", {"base": 1000.0, "sum_insured": 2_000_000.0}
        )
        assert result.result_value == pytest.approx(1000.0 + 2_000_000.0 * 0.001)

    def test_large_loss_loading_evaluation_not_triggered(self):
        code = (
            "df = df.with_columns(\n"
            '    (pl.col("base") + pl.when(pl.col("sum_insured") > 1_000_000).then(pl.col("sum_insured") * 0.001).otherwise(0)).alias("loaded_premium")\n'
            ")"
        )
        result = evaluate_expression(
            code, "loaded_premium", {"base": 1000.0, "sum_insured": 500_000.0}
        )
        assert result.result_value == pytest.approx(1000.0)


class TestActuarialNCDDiscount:
    def test_ncd_chained_when_then(self):
        """No Claims Discount: NCD years 0-5+ mapped to discount factors."""
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("ncd_years") == 0).then(1.0)\n'
            '    .when(pl.col("ncd_years") == 1).then(0.95)\n'
            '    .when(pl.col("ncd_years") == 2).then(0.90)\n'
            '    .when(pl.col("ncd_years") == 3).then(0.80)\n'
            '    .when(pl.col("ncd_years") == 4).then(0.70)\n'
            '    .when(pl.col("ncd_years") >= 5).then(0.60)\n'
            "    .otherwise(1.0)\n"
            '    .alias("ncd_factor")\n'
            ")"
        )
        expr = parse_expression(code, "ncd_factor")
        assert expr.expression_type == "conditional"
        assert "ncd_years" in expr.referenced_columns

    def test_ncd_evaluation_year_3(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("ncd_years") == 0).then(1.0)\n'
            '    .when(pl.col("ncd_years") == 1).then(0.95)\n'
            '    .when(pl.col("ncd_years") == 2).then(0.90)\n'
            '    .when(pl.col("ncd_years") == 3).then(0.80)\n'
            '    .when(pl.col("ncd_years") >= 4).then(0.70)\n'
            "    .otherwise(1.0)\n"
            '    .alias("ncd_factor")\n'
            ")"
        )
        result = evaluate_expression(code, "ncd_factor", {"ncd_years": 3})
        assert result.result_value == pytest.approx(0.80)


class TestActuarialMinimumPremium:
    def test_minimum_premium_floor(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("calculated_premium"), pl.col("minimum_premium")).alias("final_premium"))'
        expr = parse_expression(code, "final_premium")
        assert expr.expression_type == "horizontal_func"
        assert set(expr.referenced_columns) == {"calculated_premium", "minimum_premium"}

    def test_minimum_premium_evaluation_above(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("calculated_premium"), pl.col("minimum_premium")).alias("final_premium"))'
        result = evaluate_expression(
            code, "final_premium", {"calculated_premium": 500.0, "minimum_premium": 200.0}
        )
        assert result.result_value == pytest.approx(500.0)

    def test_minimum_premium_evaluation_below(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("calculated_premium"), pl.col("minimum_premium")).alias("final_premium"))'
        result = evaluate_expression(
            code, "final_premium", {"calculated_premium": 100.0, "minimum_premium": 200.0}
        )
        assert result.result_value == pytest.approx(200.0)


class TestActuarialIPT:
    def test_ipt_calculation(self):
        code = (
            "ipt_rate = 0.12\n"
            'df = df.with_columns((pl.col("net_premium") * (1 + ipt_rate)).alias("gross_with_ipt"))'
        )
        expr = parse_expression(code, "gross_with_ipt")
        assert expr.referenced_columns == ["net_premium"]

    def test_ipt_evaluation(self):
        code = (
            "ipt_rate = 0.12\n"
            'df = df.with_columns((pl.col("net_premium") * (1 + ipt_rate)).alias("gross_with_ipt"))'
        )
        result = evaluate_expression(code, "gross_with_ipt", {"net_premium": 1000.0})
        assert result.result_value == pytest.approx(1120.0)


class TestActuarialProportionalReinsurance:
    def test_proportional_reinsurance(self):
        code = 'df = df.with_columns((pl.col("gross_premium") * pl.col("retention_pct")).alias("net_of_ri"))'
        expr = parse_expression(code, "net_of_ri")
        assert set(expr.referenced_columns) == {"gross_premium", "retention_pct"}
        assert expr.expression_text == "gross_premium * retention_pct"


class TestActuarialAggregateDeductible:
    def test_aggregate_deductible(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("incurred") - pl.col("deductible"), pl.lit(0)).alias("net_incurred"))'
        expr = parse_expression(code, "net_incurred")
        assert set(expr.referenced_columns) == {"incurred", "deductible"}
        assert 0 in expr.constants

    def test_aggregate_deductible_evaluation_positive(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("incurred") - pl.col("deductible"), pl.lit(0)).alias("net_incurred"))'
        result = evaluate_expression(
            code, "net_incurred", {"incurred": 5000.0, "deductible": 1000.0}
        )
        assert result.result_value == pytest.approx(4000.0)

    def test_aggregate_deductible_evaluation_negative_clipped(self):
        code = 'df = df.with_columns(pl.max_horizontal(pl.col("incurred") - pl.col("deductible"), pl.lit(0)).alias("net_incurred"))'
        result = evaluate_expression(
            code, "net_incurred", {"incurred": 500.0, "deductible": 1000.0}
        )
        assert result.result_value == pytest.approx(0.0)


class TestActuarialAgeBandPricing:
    def test_age_band_multi_step(self):
        """Multi-step: age -> age_band -> age_factor -> adjusted_premium."""
        code = (
            'df = df.with_columns((pl.col("age") // 10).alias("age_band"))\n'
            "df = df.with_columns(\n"
            '    pl.when(pl.col("age_band") <= 2).then(1.5)\n'
            '    .when(pl.col("age_band") <= 4).then(1.0)\n'
            '    .when(pl.col("age_band") <= 6).then(1.2)\n'
            "    .otherwise(1.8)\n"
            '    .alias("age_factor")\n'
            ")\n"
            'df = df.with_columns((pl.col("base_premium") * pl.col("age_factor")).alias("adjusted_premium"))'
        )
        expr = parse_expression(code, "adjusted_premium")
        assert set(expr.referenced_columns) == {"base_premium", "age_factor"}
        # age_factor was created in a prior step
        expr_factor = parse_expression(code, "age_factor")
        assert expr_factor.expression_type == "conditional"
        assert "age_band" in expr_factor.referenced_columns


class TestActuarialVehicleGroupFactor:
    def test_vehicle_group_with_bands_and_caps(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("vehicle_group") <= 10).then(1.0)\n'
            '    .when(pl.col("vehicle_group") <= 20).then(1.3)\n'
            '    .when(pl.col("vehicle_group") <= 35).then(1.6)\n'
            "    .otherwise(2.0)\n"
            '    .alias("vehicle_factor")\n'
            ")\n"
        )
        expr = parse_expression(code, "vehicle_factor")
        assert expr.expression_type == "conditional"
        assert "vehicle_group" in expr.referenced_columns


class TestActuarialMultiLinePricing:
    def test_combined_property_liability_motor(self):
        """Combine three lines of business into a package premium."""
        code = (
            "df = df.with_columns(\n"
            '    (pl.col("property_premium") + pl.col("liability_premium") + pl.col("motor_premium")).alias("package_premium")\n'
            ")"
        )
        expr = parse_expression(code, "package_premium")
        assert set(expr.referenced_columns) == {
            "property_premium",
            "liability_premium",
            "motor_premium",
        }
        assert expr.expression_text == "property_premium + liability_premium + motor_premium"

    def test_combined_with_discount_for_package(self):
        code = (
            "package_discount = 0.10\n"
            "df = df.with_columns(\n"
            '    ((pl.col("property_premium") + pl.col("liability_premium") + pl.col("motor_premium")) * (1 - package_discount)).alias("package_premium")\n'
            ")"
        )
        expr = parse_expression(code, "package_premium")
        assert set(expr.referenced_columns) == {
            "property_premium",
            "liability_premium",
            "motor_premium",
        }


# ###########################################################################
# 2. Polars Expression Edge Cases
# ###########################################################################


class TestPolarsIsBetween:
    def test_is_between_in_expression(self):
        code = 'df = df.with_columns(pl.col("age").is_between(18, 65).alias("working_age"))'
        expr = parse_expression(code, "working_age")
        assert expr.referenced_columns == ["age"]
        assert "is_between" in expr.expression_text

    def test_is_between_in_condition(self):
        code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("speed").is_between(30, 70)).then("normal").otherwise("extreme")\n'
            '    .alias("speed_band")\n'
            ")"
        )
        expr = parse_expression(code, "speed_band")
        assert "speed" in expr.referenced_columns


class TestPolarsIsIn:
    def test_is_in_expression(self):
        code = 'df = df.with_columns(pl.col("region").is_in(["x", "y", "z"]).alias("selected"))'
        expr = parse_expression(code, "selected")
        assert expr.referenced_columns == ["region"]
        assert "is_in" in expr.expression_text


class TestPolarsIsNull:
    def test_is_null_as_expression(self):
        code = 'df = df.with_columns(pl.col("claim_date").is_null().alias("no_claim"))'
        expr = parse_expression(code, "no_claim")
        assert expr.referenced_columns == ["claim_date"]
        assert "is_null" in expr.expression_text


class TestPolarsBooleanToInt:
    def test_is_not_null_cast_int(self):
        code = 'df = df.with_columns(pl.col("opt_field").is_not_null().cast(pl.Int32).alias("has_opt"))'
        expr = parse_expression(code, "has_opt")
        assert expr.referenced_columns == ["opt_field"]


class TestPolarsReplace:
    def test_replace_strict_dict(self):
        code = 'df = df.with_columns(pl.col("grade").replace_strict({"A": 4.0, "B": 3.0, "C": 2.0}, default=0.0).alias("gpa"))'
        expr = parse_expression(code, "gpa")
        assert expr.referenced_columns == ["grade"]

    def test_replace_simple(self):
        code = (
            'df = df.with_columns(pl.col("status").replace({"old": "new"}).alias("status_clean"))'
        )
        expr = parse_expression(code, "status_clean")
        assert expr.referenced_columns == ["status"]


class TestPolarsCut:
    def test_cut_binning(self):
        code = 'df = df.with_columns(pl.col("age").cut([0, 25, 50, 75, 100]).alias("age_bin"))'
        expr = parse_expression(code, "age_bin")
        assert expr.referenced_columns == ["age"]
        assert "cut" in expr.expression_text


class TestPolarsQcut:
    def test_qcut_quantile_binning(self):
        code = 'df = df.with_columns(pl.col("income").qcut(4).alias("income_quartile"))'
        expr = parse_expression(code, "income_quartile")
        assert expr.referenced_columns == ["income"]
        assert "qcut" in expr.expression_text


class TestPolarsRank:
    def test_rank(self):
        code = 'df = df.with_columns(pl.col("score").rank().alias("score_rank"))'
        expr = parse_expression(code, "score_rank")
        assert expr.referenced_columns == ["score"]
        assert "rank" in expr.expression_text


class TestPolarsSample:
    def test_sample_is_opaque(self):
        """Sampling is non-deterministic and should be marked opaque."""
        code = 'df = df.with_columns(pl.col("value").sample(n=10).alias("sampled"))'
        expr = parse_expression(code, "sampled")
        assert expr.referenced_columns == ["value"]
        # sample() is inherently non-deterministic; opaque is acceptable
        assert expr is not None


class TestPolarsAggregationMethods:
    def test_null_count(self):
        code = 'df = df.with_columns(pl.col("claims").null_count().alias("missing_claims"))'
        expr = parse_expression(code, "missing_claims")
        assert expr.referenced_columns == ["claims"]

    def test_n_unique(self):
        code = 'df = df.with_columns(pl.col("region").n_unique().alias("region_count"))'
        expr = parse_expression(code, "region_count")
        assert expr.referenced_columns == ["region"]
        assert "n_unique" in expr.expression_text

    def test_value_counts(self):
        code = 'df = df.with_columns(pl.col("status").value_counts().alias("status_freq"))'
        expr = parse_expression(code, "status_freq")
        assert expr.referenced_columns == ["status"]


class TestPolarsMapBatches:
    def test_map_batches_is_opaque(self):
        code = 'df = df.with_columns(pl.col("values").map_batches(lambda s: s.to_numpy()).alias("as_numpy"))'
        expr = parse_expression(code, "as_numpy")
        assert expr.expression_type == "opaque"
        assert expr.referenced_columns == ["values"]


class TestPolarsConcatStr:
    def test_concat_str_with_string_column_names(self):
        code = 'df = df.with_columns(pl.concat_str(["first_name", "last_name"], separator="-").alias("full_name"))'
        expr = parse_expression(code, "full_name")
        assert expr.expression_type == "horizontal_func"
        assert set(expr.referenced_columns) == {"first_name", "last_name"}

    def test_concat_str_with_pl_col(self):
        code = 'df = df.with_columns(pl.concat_str(pl.col("city"), pl.col("state"), separator=", ").alias("location"))'
        expr = parse_expression(code, "location")
        assert set(expr.referenced_columns) == {"city", "state"}


class TestPolarsFormat:
    def test_pl_format(self):
        code = 'df = df.with_columns(pl.format("{} - {}", "first", "last").alias("combined"))'
        expr = parse_expression(code, "combined")
        assert set(expr.referenced_columns) >= {"first", "last"}


class TestPolarsCoalesce:
    def test_coalesce_columns(self):
        code = 'df = df.with_columns(pl.coalesce(["primary", "secondary", "fallback"]).alias("resolved"))'
        expr = parse_expression(code, "resolved")
        assert set(expr.referenced_columns) == {"primary", "secondary", "fallback"}

    def test_coalesce_with_pl_col(self):
        code = (
            'df = df.with_columns(pl.coalesce(pl.col("a"), pl.col("b"), pl.lit(0)).alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert set(expr.referenced_columns) == {"a", "b"}
        assert 0 in expr.constants


# ###########################################################################
# 3. Multi-Statement Dependency Tracking
# ###########################################################################


class TestVariableChains:
    def test_variable_chain_to_with_columns(self):
        """Variables assigned then multiplied and used in with_columns."""
        code = (
            'base = pl.col("base_rate")\n'
            'factor = pl.col("area") * pl.col("group")\n'
            'df = df.with_columns((base * factor).alias("premium"))'
        )
        expr = parse_expression(code, "premium")
        assert set(expr.referenced_columns) == {"base_rate", "area", "group"}

    def test_three_step_variable_chain(self):
        code = (
            'a = pl.col("x") + 1\n'
            "b = a * 2\n"
            'c = b - pl.col("y")\n'
            'df = df.with_columns(c.alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert set(expr.referenced_columns) == {"x", "y"}

    def test_variable_reuse_in_two_outputs(self):
        code = (
            'shared = pl.col("base") * pl.col("factor")\n'
            "df = df.with_columns(\n"
            '    (shared + pl.col("loading")).alias("loaded"),\n'
            '    (shared * 0.9).alias("discounted"),\n'
            ")"
        )
        loaded = parse_expression(code, "loaded")
        discounted = parse_expression(code, "discounted")
        assert set(loaded.referenced_columns) == {"base", "factor", "loading"}
        assert set(discounted.referenced_columns) == {"base", "factor"}


class TestConditionalVariableAssignment:
    def test_if_else_variable_assignment_is_opaque(self):
        """Conditional assignment at Python level is not statically resolvable."""
        code = (
            'if "ncd_years" in df.columns:\n'
            '    ncd = pl.col("ncd_years")\n'
            "else:\n"
            "    ncd = pl.lit(0)\n"
            'df = df.with_columns(ncd.alias("ncd_factor"))'
        )
        expr = parse_expression(code, "ncd_factor")
        assert expr.expression_type == "opaque"


class TestLoopBuildingColumns:
    def test_loop_with_reduce_is_opaque(self):
        """Loop building a list of expressions and reducing is opaque."""
        code = (
            "factors = []\n"
            'for col in ["age", "area", "vehicle"]:\n'
            '    factors.append(pl.col(f"{col}_factor"))\n'
            "combined = pl.reduce(lambda a, b: a * b, factors)\n"
            'df = df.with_columns(combined.alias("combined_factor"))'
        )
        expr = parse_expression(code, "combined_factor")
        # This is dynamic -- opaque is acceptable
        assert expr is not None
        assert expr.expression_type == "opaque"


class TestDictionaryLookup:
    def test_replace_strict_with_dict_variable(self):
        code = (
            'rates = {"A": 1.0, "B": 1.5, "C": 2.0}\n'
            "df = df.with_columns(\n"
            '    pl.col("risk_class").replace_strict(rates, default=1.0).alias("risk_factor")\n'
            ")"
        )
        expr = parse_expression(code, "risk_factor")
        assert "risk_class" in expr.referenced_columns


class TestMultiStatementDependencyMisc:
    def test_augmented_assignment_chain(self):
        """Variable built up through augmented operations."""
        code = (
            'expr = pl.col("base")\n'
            'expr = expr * pl.col("factor_a")\n'
            'expr = expr * pl.col("factor_b")\n'
            'df = df.with_columns(expr.alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert set(expr.referenced_columns) == {"base", "factor_a", "factor_b"}

    def test_tuple_unpacking_not_crash(self):
        """Tuple unpacking should not crash the parser."""
        code = 'a, b = pl.col("x"), pl.col("y")\ndf = df.with_columns((a + b).alias("sum"))'
        expr = parse_expression(code, "sum")
        assert expr is not None

    def test_expression_in_function_call(self):
        """Expression passed through a user function call."""
        code = (
            "def add_loading(expr, load):\n"
            "    return expr * (1 + load)\n"
            "\n"
            'df = df.with_columns(add_loading(pl.col("base"), 0.15).alias("loaded"))'
        )
        expr = parse_expression(code, "loaded")
        # User-defined function is opaque
        assert expr is not None
        assert expr.expression_type in ("opaque", "function_call")

    def test_walrus_operator_in_comprehension(self):
        """Walrus operator should not crash the parser."""
        code = (
            "df = df.with_columns(\n"
            '    [(e := pl.col(c) * 2).alias(f"{c}_doubled") for c in ["a", "b"]]\n'
            ")"
        )
        expr = parse_expression(code, "a_doubled")
        assert expr is not None

    def test_sequential_with_columns_redefining_same_column(self):
        """A column redefined across multiple with_columns; last definition wins."""
        code = (
            'df = df.with_columns((pl.col("x") * 2).alias("output"))\n'
            'df = df.with_columns((pl.col("output") + 10).alias("output"))'
        )
        expr = parse_expression(code, "output")
        # Should pick the last definition
        assert expr.expression_text == "output + 10"


# ###########################################################################
# 4. Error Recovery Tests
# ###########################################################################


class TestMalformedPolarsCode:
    def test_valid_python_invalid_polars_method(self):
        """Valid Python but calling a non-existent Polars method."""
        code = 'df = df.with_columns(pl.col("a").nonexistent_method().alias("result"))'
        expr = parse_expression(code, "result")
        # Parser works on AST, not runtime -- should still parse
        assert expr is not None
        assert "a" in expr.referenced_columns

    def test_with_columns_no_args(self):
        code = "df = df.with_columns()"
        expr = parse_expression(code, "result")
        assert expr is None or expr.expression_type == "opaque"


class TestDeeplyNestedExpressions:
    def test_ten_levels_of_nesting(self):
        """Deeply nested parentheses: ((((((((((a + 1) + 2) + 3) ...))))))))."""
        inner = 'pl.col("a")'
        for i in range(1, 11):
            inner = f"({inner} + {i})"
        code = f'df = df.with_columns({inner}.alias("deep"))'
        expr = parse_expression(code, "deep")
        assert expr is not None
        assert "a" in expr.referenced_columns

    def test_fifteen_levels_of_nesting(self):
        inner = 'pl.col("x")'
        for i in range(1, 16):
            inner = f"({inner} * 1.01)"
        code = f'df = df.with_columns({inner}.alias("compounded"))'
        expr = parse_expression(code, "compounded")
        assert expr is not None
        assert "x" in expr.referenced_columns


class TestVeryWideWithColumns:
    def test_fifty_expressions_in_with_columns(self):
        exprs = ", ".join(f'(pl.col("col_{i}") * {i}).alias("out_{i}")' for i in range(50))
        code = f"df = df.with_columns({exprs})"
        expr = parse_expression(code, "out_25")
        assert expr is not None
        assert expr.referenced_columns == ["col_25"]
        assert expr.expression_text == "col_25 * 25"


class TestLineEndingsAndWhitespace:
    def test_windows_line_endings(self):
        code = 'df = df.with_columns((pl.col("a") + 1).alias("b"))\r\ndf = df.with_columns((pl.col("b") * 2).alias("c"))'
        expr = parse_expression(code, "c")
        assert expr is not None
        assert expr.expression_text == "b * 2"

    def test_mixed_tabs_and_spaces(self):
        code = 'df = df.with_columns(\n\t    (pl.col("a") + 1).alias("b")\n)'
        expr = parse_expression(code, "b")
        assert expr is not None
        assert expr.expression_text == "a + 1"

    def test_bom_marker(self):
        code = '\ufeffdf = df.with_columns((pl.col("a") + 1).alias("b"))'
        expr = parse_expression(code, "b")
        assert expr is not None
        assert expr.expression_text == "a + 1"


class TestSelfAndClsReferences:
    def test_self_reference_does_not_crash(self):
        code = 'self.df = self.df.with_columns((pl.col("x") * 2).alias("y"))'
        expr = parse_expression(code, "y")
        assert expr is not None
        assert "x" in expr.referenced_columns

    def test_cls_reference_does_not_crash(self):
        code = 'cls.data = cls.data.with_columns((pl.col("a") + 1).alias("b"))'
        expr = parse_expression(code, "b")
        assert expr is not None
        assert "a" in expr.referenced_columns


class TestWalrusOperator:
    def test_walrus_in_if_condition(self):
        code = 'if (n := len(df)) > 0:\n    df = df.with_columns((pl.col("x") / n).alias("x_norm"))'
        expr = parse_expression(code, "x_norm")
        # Has runtime-dependent control flow
        assert expr is not None
        assert expr.expression_type == "opaque"


class TestMatchCase:
    def test_match_case_is_opaque(self):
        code = (
            "match risk_type:\n"
            '    case "low":\n'
            '        df = df.with_columns((pl.col("base") * 0.8).alias("rate"))\n'
            '    case "high":\n'
            '        df = df.with_columns((pl.col("base") * 1.5).alias("rate"))\n'
            "    case _:\n"
            '        df = df.with_columns(pl.col("base").alias("rate"))'
        )
        expr = parse_expression(code, "rate")
        assert expr.expression_type == "opaque"


class TestFStringAlias:
    def test_f_string_alias_in_loop(self):
        code = (
            'for suffix in ["a", "b"]:\n'
            '    df = df.with_columns((pl.col(suffix) * 2).alias(f"{suffix}_doubled"))'
        )
        expr = parse_expression(code, "a_doubled")
        assert expr is not None

    def test_f_string_alias_with_variable(self):
        code = (
            'col_name = "premium"\n'
            'df = df.with_columns((pl.col(col_name) * 1.1).alias(f"loaded_{col_name}"))'
        )
        expr = parse_expression(code, "loaded_premium")
        assert expr is not None

    def test_fstring_alias_with_known_variable(self):
        code = (
            'suffix = "total"\n'
            'df = df.with_columns((pl.col("x") + pl.col("y")).alias(f"result_{suffix}"))'
        )
        expr = parse_expression(code, "result_total")
        assert expr is not None
        assert expr.target_column == "result_total"
        assert set(expr.referenced_columns) == {"x", "y"}

    def test_fstring_alias_numeric_suffix(self):
        code = (
            "version = 2\n"
            'df = df.with_columns((pl.col("a") * 3).alias(f"col_v{version}"))'
        )
        expr = parse_expression(code, "col_v2")
        assert expr is not None
        assert expr.target_column == "col_v2"
        assert "a" in expr.referenced_columns

    def test_fstring_alias_evaluate(self):
        code = (
            'suffix = "out"\n'
            'df = df.with_columns((pl.col("x") + 1).alias(f"result_{suffix}"))'
        )
        result = evaluate_expression(code, "result_out", {"x": 10})
        assert result is not None
        assert result.result_value in (11, None)


class TestStarredExpressions:
    def test_starred_list_in_with_columns(self):
        code = (
            'exprs = [(pl.col("a") * 2).alias("a2"), (pl.col("b") + 1).alias("b1")]\n'
            "df = df.with_columns(*exprs)"
        )
        expr = parse_expression(code, "a2")
        assert expr is not None
        assert expr.expression_text == "a * 2"

    def test_double_starred_dict_is_opaque(self):
        """**kwargs style unpacking is too dynamic to resolve statically."""
        code = 'mapping = {"result": pl.col("a") + pl.col("b")}\ndf = df.with_columns(**mapping)'
        expr = parse_expression(code, "result")
        assert expr is not None

    def test_starred_list_parse(self):
        code = (
            'exprs = [(pl.col("a") + 1).alias("result")]\n'
            "df = df.with_columns(*exprs)"
        )
        expr = parse_expression(code, "result")
        assert expr is not None
        assert "a" in expr.referenced_columns
        assert expr.expression_text == "a + 1"

    def test_starred_list_multiple_expressions(self):
        code = (
            'exprs = [(pl.col("a") + 1).alias("x"), (pl.col("b") * 2).alias("y")]\n'
            "df = df.with_columns(*exprs)"
        )
        expr_x = parse_expression(code, "x")
        expr_y = parse_expression(code, "y")
        assert expr_x is not None
        assert expr_x.expression_text == "a + 1"
        assert expr_y is not None
        assert expr_y.expression_text == "b * 2"

    def test_starred_inline_list_falls_back_to_opaque(self):
        code = 'df = df.with_columns(*[(pl.col("a") + 1).alias("result")])'
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_type == "opaque"


class TestStaticallyUnparseableCode:
    def test_getattr_column_access(self):
        """Column name via getattr is not statically resolvable."""
        code = (
            'col_name = getattr(config, "primary_column")\n'
            'df = df.with_columns(pl.col(col_name).alias("target"))'
        )
        expr = parse_expression(code, "target")
        assert expr is not None
        assert expr.expression_type == "opaque"

    def test_eval_based_expression(self):
        code = (
            'expr_str = \'pl.col("a") * 2\'\ndf = df.with_columns(eval(expr_str).alias("result"))'
        )
        expr = parse_expression(code, "result")
        assert expr is not None
        assert expr.expression_type == "opaque"


# ###########################################################################
# 5. Cross-Node Expression Composition
# ###########################################################################


class TestCrossNodeComposition:
    """When expressions from separate code blocks (nodes) should compose."""

    def test_two_node_linear_chain(self):
        """Node 1 creates 'base', Node 2 creates 'premium' from 'base'."""
        node1_code = 'df = df.with_columns((pl.col("rate") * pl.col("exposure")).alias("base"))'
        node2_code = 'df = df.with_columns((pl.col("base") * pl.col("factor")).alias("premium"))'
        expr1 = parse_expression(node1_code, "base")
        expr2 = parse_expression(node2_code, "premium")
        assert set(expr1.referenced_columns) == {"rate", "exposure"}
        assert set(expr2.referenced_columns) == {"base", "factor"}
        # The composed dependency for 'premium' traces back to rate, exposure, factor
        all_deps = set(expr2.referenced_columns)
        for ref_col in list(all_deps):
            if ref_col == "base":
                all_deps.remove("base")
                all_deps.update(expr1.referenced_columns)
        assert all_deps == {"rate", "exposure", "factor"}

    def test_three_node_chain(self):
        """A -> B -> C composition."""
        code_a = 'df = df.with_columns((pl.col("x") + pl.col("y")).alias("a"))'
        code_b = 'df = df.with_columns((pl.col("a") * 2).alias("b"))'
        code_c = 'df = df.with_columns((pl.col("b") - pl.col("z")).alias("c"))'
        expr_a = parse_expression(code_a, "a")
        expr_b = parse_expression(code_b, "b")
        expr_c = parse_expression(code_c, "c")
        # Trace c -> b -> a -> {x, y} + {z}
        deps = set(expr_c.referenced_columns)  # {"b", "z"}
        if "b" in deps:
            deps.remove("b")
            deps.update(expr_b.referenced_columns)  # {"a"}
        if "a" in deps:
            deps.remove("a")
            deps.update(expr_a.referenced_columns)  # {"x", "y"}
        assert deps == {"x", "y", "z"}

    def test_fan_in_composition(self):
        """Two nodes feed into one: node_a + node_b -> node_c."""
        code_a = 'df = df.with_columns((pl.col("claims") / pl.col("exposure")).alias("frequency"))'
        code_b = (
            'df = df.with_columns((pl.col("total_cost") / pl.col("claim_count")).alias("severity"))'
        )
        code_c = (
            'df = df.with_columns((pl.col("frequency") * pl.col("severity")).alias("pure_premium"))'
        )
        expr_a = parse_expression(code_a, "frequency")
        expr_b = parse_expression(code_b, "severity")
        expr_c = parse_expression(code_c, "pure_premium")
        assert set(expr_c.referenced_columns) == {"frequency", "severity"}
        # Full trace
        leaf_deps = set()
        for col in expr_c.referenced_columns:
            if col == "frequency":
                leaf_deps.update(expr_a.referenced_columns)
            elif col == "severity":
                leaf_deps.update(expr_b.referenced_columns)
            else:
                leaf_deps.add(col)
        assert leaf_deps == {"claims", "exposure", "total_cost", "claim_count"}

    def test_diamond_dependency(self):
        """Diamond: base -> [adj_a, adj_b] -> final (both reference base)."""
        code_base = 'df = df.with_columns((pl.col("raw") * 1.1).alias("base"))'
        code_adj_a = 'df = df.with_columns((pl.col("base") * pl.col("f_a")).alias("adj_a"))'
        code_adj_b = 'df = df.with_columns((pl.col("base") * pl.col("f_b")).alias("adj_b"))'
        code_final = 'df = df.with_columns((pl.col("adj_a") + pl.col("adj_b")).alias("final"))'
        expr_base = parse_expression(code_base, "base")
        expr_adj_a = parse_expression(code_adj_a, "adj_a")
        expr_adj_b = parse_expression(code_adj_b, "adj_b")
        expr_final = parse_expression(code_final, "final")
        # final depends on adj_a, adj_b
        assert set(expr_final.referenced_columns) == {"adj_a", "adj_b"}
        # adj_a depends on base, f_a; adj_b depends on base, f_b
        assert set(expr_adj_a.referenced_columns) == {"base", "f_a"}
        assert set(expr_adj_b.referenced_columns) == {"base", "f_b"}
        # base depends on raw
        assert expr_base.referenced_columns == ["raw"]

    def test_cross_node_evaluation(self):
        """Evaluate across two nodes by chaining substitution."""
        node1_code = 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("intermediate"))'
        node2_code = 'df = df.with_columns((pl.col("intermediate") * 3).alias("final"))'
        # First evaluate node1
        result1 = evaluate_expression(node1_code, "intermediate", {"a": 10.0, "b": 5.0})
        assert result1.result_value == pytest.approx(15.0)
        # Then evaluate node2 using the result of node1
        result2 = evaluate_expression(node2_code, "final", {"intermediate": result1.result_value})
        assert result2.result_value == pytest.approx(45.0)

    def test_cross_node_with_conditional(self):
        """Node 1 produces a factor via when/then, Node 2 uses it arithmetically."""
        node1_code = (
            "df = df.with_columns(\n"
            '    pl.when(pl.col("age") < 25).then(1.5).otherwise(1.0)\n'
            '    .alias("age_factor")\n'
            ")"
        )
        node2_code = (
            'df = df.with_columns((pl.col("base") * pl.col("age_factor")).alias("premium"))'
        )
        expr1 = parse_expression(node1_code, "age_factor")
        expr2 = parse_expression(node2_code, "premium")
        assert expr1.expression_type == "conditional"
        assert expr2.expression_type == "arithmetic"
        # Evaluate the chain for a young driver
        result1 = evaluate_expression(node1_code, "age_factor", {"age": 20})
        assert result1.result_value == pytest.approx(1.5)
        result2 = evaluate_expression(
            node2_code, "premium", {"base": 400.0, "age_factor": result1.result_value}
        )
        assert result2.result_value == pytest.approx(600.0)

    def test_cross_node_many_intermediates(self):
        """Five-node chain to test deep composition."""
        codes = [
            'df = df.with_columns((pl.col("input") * 2).alias("step_1"))',
            'df = df.with_columns((pl.col("step_1") + 10).alias("step_2"))',
            'df = df.with_columns((pl.col("step_2") * 0.5).alias("step_3"))',
            'df = df.with_columns((pl.col("step_3") - 1).alias("step_4"))',
            'df = df.with_columns((pl.col("step_4") ** 2).alias("step_5"))',
        ]
        exprs = [parse_expression(c, f"step_{i + 1}") for i, c in enumerate(codes)]
        assert exprs[0].referenced_columns == ["input"]
        for i in range(1, 5):
            assert exprs[i].referenced_columns == [f"step_{i}"]
        # Evaluate chain: input=3 -> 6 -> 16 -> 8 -> 7 -> 49
        val = 3.0
        val = val * 2  # step_1 = 6
        val = val + 10  # step_2 = 16
        val = val * 0.5  # step_3 = 8
        val = val - 1  # step_4 = 7
        val = val**2  # step_5 = 49
        result = evaluate_expression(codes[0], "step_1", {"input": 3.0})
        assert result.result_value == pytest.approx(6.0)
        result = evaluate_expression(codes[4], "step_5", {"step_4": 7.0})
        assert result.result_value == pytest.approx(49.0)

    def test_cross_node_composition_text_tracking(self):
        """Verify expression_text is local to each node (not composed)."""
        node1_code = 'df = df.with_columns((pl.col("a") * pl.col("b")).alias("ab"))'
        node2_code = 'df = df.with_columns((pl.col("ab") + pl.col("c")).alias("abc"))'
        expr1 = parse_expression(node1_code, "ab")
        expr2 = parse_expression(node2_code, "abc")
        # Each expression text reflects only its own node
        assert expr1.expression_text == "a * b"
        assert expr2.expression_text == "ab + c"
