"""Tests for HTML model card generation (haute.modelling._model_card)."""

from __future__ import annotations

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
            split_config={"strategy": "random", "test_size": 0.2},
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
    def test_no_shap_header_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "SHAP Summary" not in html

    def test_no_cv_header_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Cross-Validation" not in html

    def test_no_double_lift_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Double Lift" not in html

    def test_no_loss_curve_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Loss Curve" not in html

    def test_no_ave_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Actual vs Expected" not in html

    def test_no_lorenz_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Lorenz Curve" not in html

    def test_no_residuals_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Residuals" not in html

    def test_no_scatter_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Actual vs Predicted" not in html

    def test_no_pdp_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Partial Dependence" not in html

    def test_no_holdout_metrics_when_empty(self):
        html = generate_model_card(**_minimal_kwargs())
        assert "Holdout Metrics" not in html


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
            cv_results={
                "mean_metrics": {"rmse": 0.13},
                "std_metrics": {"rmse": 0.01},
                "n_folds": 5,
            },
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
            split_config={"strategy": "random", "test_size": 0.2},
            best_iteration=50,
        )
        html = generate_model_card(**kwargs)
        assert "Training Summary" in html
        assert "Metrics" in html
        assert "Cross-Validation" in html
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
