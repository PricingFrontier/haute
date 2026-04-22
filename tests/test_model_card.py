"""Tests for HTML model card generation (haute.modelling._model_card)."""

from __future__ import annotations

import pytest

from haute.modelling._model_card import generate_model_card
from haute.modelling._result_types import ModelCardMetadata, ModelDiagnostics


def _minimal_kwargs() -> dict:
    """Minimal required kwargs for generate_model_card."""
    return {
        "name": "test-model",
        "metrics": {"rmse": 0.1234, "gini": 0.5678},
        "params": {"iterations": 100},
        "metadata": ModelCardMetadata(
            algorithm="catboost",
            task="regression",
            train_rows=800,
            test_rows=200,
            features=["x1", "x2"],
            split_config={"strategy": "random", "validation_size": 0.2},
        ),
    }


class TestModelCardValidHtml:
    def test_contains_doctype(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "<!DOCTYPE html>" in html

    def test_contains_html_tags(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "<html" in html
        assert "<body>" in html
        assert "</html>" in html

    def test_contains_title(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "test-model" in html

    def test_xss_in_name_escaped(self):
        """Malicious model names should be escaped in HTML output."""
        kwargs = _minimal_kwargs()
        kwargs["name"] = '<script>alert("xss")</script>'
        html = generate_model_card(**kwargs)
        assert "<script>" not in html


class TestModelCardContainsMetrics:
    def test_metric_names_present(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "rmse" in html
        assert "gini" in html

    def test_metric_values_present(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "0.1234" in html
        assert "0.5678" in html


class TestModelCardOmitsEmptySections:
    @pytest.mark.parametrize(
        "header",
        [
            "SHAP Summary",
            "Cross-Validation",
            "Double Lift",
            "Loss Curve",
            "Actual vs Expected",
            "Lorenz Curve",
            "Residuals",
            "Actual vs Predicted",
            "Partial Dependence",
            "Holdout Metrics",
        ],
    )
    def test_section_omitted_when_empty(self, header):
        html = generate_model_card(**_minimal_kwargs())
        assert header not in html


class TestModelCardMinimalInput:
    def test_empty_everything_no_crash(self):
        """With only required args (metrics/params empty), still produces valid HTML."""
        html = generate_model_card(
            name="empty",
            metrics={},
            params={},
        )
        assert "<!DOCTYPE html>" in html
        assert "empty" in html


class TestModelCardAllSections:
    def test_all_section_headers_present(self):
        """When all data is provided, all section headers should appear."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            loss_history=[{"iteration": i, "train_RMSE": 1.0 / (i + 1)} for i in range(10)],
            double_lift=[
                {"decile": i + 1, "actual": 0.1 * i, "predicted": 0.11 * i, "count": 100}
                for i in range(10)
            ],
            feature_importance=[
                {"feature": "x1", "importance": 0.7},
                {"feature": "x2", "importance": 0.3},
            ],
            shap_summary=[
                {"feature": "x1", "mean_abs_shap": 0.5},
                {"feature": "x2", "mean_abs_shap": 0.2},
            ],
            feature_importance_loss=[
                {"feature": "x1", "importance": 0.6},
                {"feature": "x2", "importance": 0.4},
            ],
            ave_per_feature=[
                {
                    "feature": "x1",
                    "type": "numeric",
                    "bins": [
                        {"label": "0–5", "exposure": 100, "avg_actual": 0.5, "avg_predicted": 0.6},
                    ],
                },
            ],
            residuals_histogram=[
                {"bin_center": i, "count": 10, "weighted_count": 10.0} for i in range(5)
            ],
            residuals_stats={"mean": 0.01, "std": 0.5, "skew": 0.1, "min": -2.0, "max": 2.0},
            actual_vs_predicted=[
                {"actual": 0.5, "predicted": 0.6, "weight": 1.0},
            ],
            lorenz_curve=[
                {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0},
                {"cum_weight_frac": 1.0, "cum_actual_frac": 1.0},
            ],
            lorenz_curve_perfect=[
                {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0},
                {"cum_weight_frac": 1.0, "cum_actual_frac": 1.0},
            ],
            pdp_data=[
                {
                    "feature": "x1",
                    "type": "numeric",
                    "grid": [
                        {"value": 1, "avg_prediction": 0.5},
                        {"value": 2, "avg_prediction": 0.6},
                    ],
                },
            ],
            holdout_metrics={"rmse": 0.15, "gini": 0.55},
            diagnostics_set="validation",
        )
        kwargs["metadata"] = ModelCardMetadata(
            algorithm="catboost",
            task="regression",
            train_rows=800,
            test_rows=200,
            holdout_rows=500,
            features=["x1", "x2"],
            split_config={"strategy": "random", "validation_size": 0.2},
            best_iteration=50,
        )
        html = generate_model_card(**kwargs)
        assert "Training Summary" in html
        assert "Metrics" in html
        # Cross-Validation section was removed in Phase 2 Package 2C-5.
        assert "Cross-Validation" not in html
        assert "Double Lift" in html
        assert "Loss Curve" in html
        assert "PredictionValuesChange" in html
        assert "SHAP Summary" in html
        assert "LossFunctionChange" in html
        assert "Actual vs Expected" in html
        assert "Parameters" in html
        # New sections
        assert "Lorenz Curve" in html
        assert "Residuals" in html
        assert "Actual vs Predicted" in html
        assert "Partial Dependence" in html
        assert "Holdout Metrics" in html


class TestModelCardHoldoutAndDiagnostics:
    def test_validation_rows_label(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Validation rows" in html
        assert "Test rows" not in html

    def test_holdout_rows_shown(self):
        kwargs = _minimal_kwargs()
        kwargs["metadata"] = ModelCardMetadata(
            algorithm="catboost",
            task="regression",
            train_rows=800,
            test_rows=200,
            holdout_rows=1000,
            features=["x1", "x2"],
            split_config={"strategy": "random"},
        )
        html = generate_model_card(**kwargs)
        assert "Holdout rows" in html
        assert "1,000" in html

    def test_diagnostics_set_label(self):
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(diagnostics_set="holdout")
        html = generate_model_card(**kwargs)
        assert "Diagnostics computed on" in html
        assert "Holdout" in html

    def test_holdout_metrics_hidden_when_diagnostics_is_holdout(self):
        """When holdout IS the diagnostics set, don't show a separate holdout section."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            holdout_metrics={"rmse": 0.15},
            diagnostics_set="holdout",
        )
        html = generate_model_card(**kwargs)
        assert "Holdout Metrics" not in html

    def test_holdout_metrics_shown_when_diagnostics_is_validation(self):
        """When validation is diagnostics, holdout metrics shown separately."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            holdout_metrics={"rmse": 0.15},
            diagnostics_set="validation",
        )
        html = generate_model_card(**kwargs)
        assert "Holdout Metrics" in html


class TestModelCardEscaping:
    def test_html_special_chars_escaped(self):
        """Names with special chars should not break the HTML."""
        kwargs = _minimal_kwargs()
        kwargs["name"] = '<script>alert("xss")</script>'
        html = generate_model_card(**kwargs)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_xss_in_param_keys_escaped(self):
        """Parameter names with HTML chars should be escaped."""
        kwargs = _minimal_kwargs()
        kwargs["params"] = {"<b>bold</b>": "value", "key": "<img onerror=alert(1)>"}
        html = generate_model_card(**kwargs)
        assert "<b>" not in html
        assert "<img" not in html
        assert "&lt;b&gt;" in html

    def test_xss_in_metric_names_escaped(self):
        """Metric names with HTML should be escaped."""
        kwargs = _minimal_kwargs()
        kwargs["metrics"] = {"<script>x</script>": 0.5}
        html = generate_model_card(**kwargs)
        assert "<script>x" not in html

    def test_xss_in_algorithm_escaped(self):
        """Algorithm name should be escaped."""
        kwargs = _minimal_kwargs()
        kwargs["metadata"] = ModelCardMetadata(
            algorithm="<img src=x>",
            task="regression",
            train_rows=100,
            test_rows=50,
        )
        html = generate_model_card(**kwargs)
        assert "<img src=x>" not in html


class TestHtmlTable:
    def test_basic_table_structure(self):
        from haute.modelling._model_card import _html_table

        result = _html_table(["A", "B"], [["1", "2"], ["3", "4"]])
        assert "<table>" in result
        assert "<thead>" in result
        assert "<tbody>" in result
        assert "</table>" in result
        assert "<th" in result
        assert "<td" in result

    def test_alignment(self):
        from haute.modelling._model_card import _html_table

        result = _html_table(["Left", "Right"], [["a", "b"]], align=["left", "right"])
        assert "text-align:left" in result
        assert "text-align:right" in result

    def test_default_alignment(self):
        from haute.modelling._model_card import _html_table

        result = _html_table(["A", "B"], [["1", "2"]])
        # Default is left for all columns
        assert "text-align:left" in result

    def test_escaping_in_cells(self):
        from haute.modelling._model_card import _html_table

        result = _html_table(["<script>"], [["<b>bold</b>"]])
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        assert "<b>" not in result

    def test_empty_rows(self):
        from haute.modelling._model_card import _html_table

        result = _html_table(["A"], [])
        assert "<table>" in result
        assert "<tbody>" in result


class TestModelCardCategoricalAvE:
    def test_categorical_ave_section(self):
        """AvE with categorical type should trigger categorical chart."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            ave_per_feature=[
                {
                    "feature": "vehicle_type",
                    "type": "categorical",
                    "bins": [
                        {
                            "label": "sedan",
                            "exposure": 100,
                            "avg_actual": 0.3,
                            "avg_predicted": 0.35,
                        },
                        {"label": "suv", "exposure": 80, "avg_actual": 0.5, "avg_predicted": 0.48},
                    ],
                },
            ],
        )
        html = generate_model_card(**kwargs)
        assert "Actual vs Expected" in html
        assert "vehicle_type" in html


class TestModelCardPdpCategorical:
    def test_pdp_categorical_section(self):
        """PDP with categorical type should render bars."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            pdp_data=[
                {
                    "feature": "color",
                    "type": "categorical",
                    "grid": [
                        {"value": "red", "avg_prediction": 0.4},
                        {"value": "blue", "avg_prediction": 0.6},
                    ],
                },
            ],
        )
        html = generate_model_card(**kwargs)
        assert "Partial Dependence" in html


class TestModelCardNonFiniteMetrics:
    def test_inf_metric_shows_na(self):
        kwargs = _minimal_kwargs()
        kwargs["metrics"] = {"rmse": float("inf")}
        html = generate_model_card(**kwargs)
        assert "N/A" in html

    def test_nan_metric_shows_na(self):
        kwargs = _minimal_kwargs()
        kwargs["metrics"] = {"rmse": float("nan")}
        html = generate_model_card(**kwargs)
        assert "N/A" in html


# ``TestModelCardCVNonFinite`` deleted in Phase 2 Package 2C-5:
# ``ModelDiagnostics.cv_results`` was removed along with the CV section
# of the model card HTML.


class TestModelCardBestIteration:
    def test_best_iteration_displayed(self):
        kwargs = _minimal_kwargs()
        kwargs["metadata"] = ModelCardMetadata(
            algorithm="catboost",
            task="regression",
            train_rows=800,
            test_rows=200,
            features=["x1"],
            best_iteration=42,
        )
        html = generate_model_card(**kwargs)
        assert "Best iteration" in html
        assert "42" in html

    def test_best_iteration_absent_when_none(self):
        kwargs = _minimal_kwargs()
        html = generate_model_card(**kwargs)
        assert "Best iteration" not in html


class TestModelCardLorenzOnly:
    def test_lorenz_model_only(self):
        """Lorenz curve with only model curve, no perfect curve."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            lorenz_curve=[
                {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0},
                {"cum_weight_frac": 1.0, "cum_actual_frac": 1.0},
            ],
        )
        html = generate_model_card(**kwargs)
        assert "Lorenz Curve" in html

    def test_lorenz_perfect_only(self):
        """Lorenz curve with only perfect curve."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            lorenz_curve_perfect=[
                {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0},
                {"cum_weight_frac": 1.0, "cum_actual_frac": 1.0},
            ],
        )
        html = generate_model_card(**kwargs)
        assert "Lorenz Curve" in html


class TestModelCardResidualsSeparateStats:
    def test_residuals_with_stats_table(self):
        """Residuals with stats dict should produce a stats table."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            residuals_histogram=[
                {"bin_center": i, "count": 10, "weighted_count": 10.0} for i in range(5)
            ],
            residuals_stats={"mean": 0.01, "std": 0.5, "skew": -0.2},
        )
        html = generate_model_card(**kwargs)
        assert "Residuals" in html
        assert "Mean" in html
        assert "Std" in html

    def test_residuals_without_stats(self):
        """Residuals without stats should not show stats table."""
        kwargs = _minimal_kwargs()
        kwargs["diagnostics"] = ModelDiagnostics(
            residuals_histogram=[
                {"bin_center": i, "count": 10, "weighted_count": 10.0} for i in range(5)
            ],
        )
        html = generate_model_card(**kwargs)
        assert "Residuals" in html
        # Should not have a stats table beneath residuals
        assert "Statistic" not in html
