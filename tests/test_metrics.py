"""Tests for diagnostic computation functions in haute.modelling._metrics."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from haute.modelling._metrics import (
    _auc,
    _gini,
    _logloss,
    _tweedie_deviance,
    compute_actual_vs_predicted,
    compute_ave_per_feature,
    compute_lorenz_curve,
    compute_metrics,
    compute_pdp,
    compute_residuals_histogram,
)

# ---------------------------------------------------------------------------
# compute_ave_per_feature — max_features=None default
# ---------------------------------------------------------------------------


class TestAveMaxFeaturesDefault:
    def test_default_processes_all_features(self):
        """With max_features=None (the new default), all features are processed."""
        n = 50
        df = pl.DataFrame({f"f{i}": np.random.RandomState(i).randn(n) for i in range(20)})
        y_true = np.random.RandomState(99).randn(n)
        y_pred = y_true + 0.1

        result = compute_ave_per_feature(df, [f"f{i}" for i in range(20)], [], y_true, y_pred)
        assert len(result) == 20

    def test_explicit_max_features_still_works(self):
        """Backward compat: passing max_features=3 still limits output."""
        n = 50
        df = pl.DataFrame({f"f{i}": np.random.randn(n) for i in range(10)})
        y_true = np.random.randn(n)
        y_pred = y_true

        result = compute_ave_per_feature(
            df,
            [f"f{i}" for i in range(10)],
            [],
            y_true,
            y_pred,
            max_features=3,
        )
        assert len(result) == 3


# ---------------------------------------------------------------------------
# compute_residuals_histogram
# ---------------------------------------------------------------------------


class TestResidualsHistogram:
    def test_basic(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([1.1, 2.2, 2.8, 4.1, 5.0])
        bins, stats = compute_residuals_histogram(y_true, y_pred, n_bins=5)

        assert len(bins) == 5
        for b in bins:
            assert "bin_center" in b
            assert "count" in b
            assert "weighted_count" in b

        assert "mean" in stats
        assert "std" in stats
        assert "skew" in stats
        assert "min" in stats
        assert "max" in stats

    def test_residuals_correct(self):
        y_true = np.array([10.0, 20.0, 30.0])
        y_pred = np.array([10.0, 20.0, 30.0])
        bins, stats = compute_residuals_histogram(y_true, y_pred, n_bins=5)
        # All residuals are 0
        assert stats["mean"] == pytest.approx(0.0)
        assert stats["std"] == pytest.approx(0.0)
        assert stats["min"] == pytest.approx(0.0)
        assert stats["max"] == pytest.approx(0.0)

    def test_weighted(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([0.0, 0.0, 0.0])  # residuals = [1, 2, 3]
        w = np.array([1.0, 1.0, 8.0])  # heavy weight on residual=3
        _, stats = compute_residuals_histogram(y_true, y_pred, weight=w, n_bins=3)
        # Weighted mean should be pulled toward 3
        assert stats["mean"] > 2.0

    def test_weighted_count_sums(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0])
        y_pred = np.array([0.0, 0.0, 0.0, 0.0])
        w = np.array([2.0, 3.0, 4.0, 5.0])
        bins, _ = compute_residuals_histogram(y_true, y_pred, weight=w, n_bins=4)
        total_wc = sum(b["weighted_count"] for b in bins)
        assert total_wc == pytest.approx(14.0)

    def test_empty_arrays(self):
        bins, stats = compute_residuals_histogram(np.array([]), np.array([]))
        assert bins == []
        assert stats == {"mean": 0.0, "std": 0.0, "skew": 0.0, "min": 0.0, "max": 0.0}

    def test_single_value(self):
        y_true = np.array([5.0])
        y_pred = np.array([3.0])
        bins, stats = compute_residuals_histogram(y_true, y_pred, n_bins=10)
        assert stats["mean"] == pytest.approx(2.0)
        assert stats["std"] == pytest.approx(0.0)
        assert stats["min"] == pytest.approx(2.0)
        assert stats["max"] == pytest.approx(2.0)

    def test_all_zero_residuals(self):
        y = np.array([1.0, 2.0, 3.0])
        bins, stats = compute_residuals_histogram(y, y, n_bins=5)
        assert stats["mean"] == pytest.approx(0.0)
        assert stats["std"] == pytest.approx(0.0)
        assert stats["skew"] == pytest.approx(0.0)

    def test_skew_positive(self):
        """Residuals skewed to the right should have positive skew."""
        rng = np.random.RandomState(42)
        # Exponential distribution has positive skew
        residuals = rng.exponential(size=1000)
        y_true = residuals
        y_pred = np.zeros(1000)
        _, stats = compute_residuals_histogram(y_true, y_pred, n_bins=50)
        assert stats["skew"] > 0

    def test_negative_skew(self):
        rng = np.random.RandomState(99)
        residuals = -rng.exponential(size=1000)
        y_true = residuals
        y_pred = np.zeros(1000)
        _, stats = compute_residuals_histogram(y_true, y_pred, n_bins=50)
        assert stats["skew"] < 0


# ---------------------------------------------------------------------------
# compute_actual_vs_predicted
# ---------------------------------------------------------------------------


class TestActualVsPredicted:
    def test_basic(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 2.1, 3.1])
        result = compute_actual_vs_predicted(y_true, y_pred)

        assert len(result) == 3
        for pt in result:
            assert "actual" in pt
            assert "predicted" in pt
            assert "weight" in pt

    def test_returns_all_when_under_max(self):
        n = 100
        y_true = np.arange(n, dtype=float)
        y_pred = y_true + 0.1
        result = compute_actual_vs_predicted(y_true, y_pred, max_points=200)
        assert len(result) == n

    def test_subsamples_when_over_max(self):
        n = 5000
        rng = np.random.RandomState(42)
        y_true = rng.randn(n)
        y_pred = y_true + rng.randn(n) * 0.1
        max_pts = 100
        result = compute_actual_vs_predicted(y_true, y_pred, max_points=max_pts)
        assert len(result) <= max_pts

    def test_weighted(self):
        y_true = np.array([1.0, 2.0])
        y_pred = np.array([1.1, 2.1])
        w = np.array([5.0, 10.0])
        result = compute_actual_vs_predicted(y_true, y_pred, weight=w)
        assert result[0]["weight"] == pytest.approx(5.0)
        assert result[1]["weight"] == pytest.approx(10.0)

    def test_empty_arrays(self):
        result = compute_actual_vs_predicted(np.array([]), np.array([]))
        assert result == []

    def test_reproducible_subsampling(self):
        """Same inputs should give same output due to fixed seed."""
        n = 5000
        rng = np.random.RandomState(123)
        y_true = rng.randn(n)
        y_pred = y_true + 0.1

        r1 = compute_actual_vs_predicted(y_true, y_pred, max_points=100)
        r2 = compute_actual_vs_predicted(y_true, y_pred, max_points=100)
        assert r1 == r2

    def test_values_rounded(self):
        y_true = np.array([1.123456789])
        y_pred = np.array([2.987654321])
        result = compute_actual_vs_predicted(y_true, y_pred)
        # Should be rounded to 6 decimal places
        assert result[0]["actual"] == round(1.123456789, 6)
        assert result[0]["predicted"] == round(2.987654321, 6)

    def test_stratified_preserves_range(self):
        """Subsampled points should span the full range of predictions."""
        n = 10000
        rng = np.random.RandomState(42)
        y_pred = rng.uniform(0, 100, n)
        y_true = y_pred + rng.randn(n)

        result = compute_actual_vs_predicted(y_true, y_pred, max_points=200)
        pred_values = [p["predicted"] for p in result]
        # Should have points near both extremes
        assert min(pred_values) < 10
        assert max(pred_values) > 90


# ---------------------------------------------------------------------------
# compute_lorenz_curve
# ---------------------------------------------------------------------------


class TestLorenzCurve:
    def test_basic(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([1.1, 2.2, 2.8, 4.1, 5.0])
        model_curve, perfect_curve = compute_lorenz_curve(y_true, y_pred)

        assert len(model_curve) > 0
        assert len(perfect_curve) > 0

    def test_endpoints_included(self):
        """Both curves must include (0,0) and (1,1)."""
        n = 100
        rng = np.random.RandomState(42)
        y_true = rng.rand(n) * 10
        y_pred = y_true + rng.randn(n) * 0.5

        model_curve, perfect_curve = compute_lorenz_curve(y_true, y_pred)

        # Start at (0, 0)
        assert model_curve[0] == {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}
        assert perfect_curve[0] == {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}

        # End at (1, 1)
        assert model_curve[-1]["cum_weight_frac"] == pytest.approx(1.0)
        assert model_curve[-1]["cum_actual_frac"] == pytest.approx(1.0)
        assert perfect_curve[-1]["cum_weight_frac"] == pytest.approx(1.0)
        assert perfect_curve[-1]["cum_actual_frac"] == pytest.approx(1.0)

    def test_monotonically_increasing(self):
        """Cumulative fractions should be non-decreasing."""
        n = 200
        rng = np.random.RandomState(42)
        y_true = rng.rand(n) * 10
        y_pred = y_true + rng.randn(n)

        model_curve, perfect_curve = compute_lorenz_curve(y_true, y_pred)

        for curve in [model_curve, perfect_curve]:
            w_fracs = [p["cum_weight_frac"] for p in curve]
            a_fracs = [p["cum_actual_frac"] for p in curve]
            for i in range(1, len(w_fracs)):
                assert w_fracs[i] >= w_fracs[i - 1]
                assert a_fracs[i] >= a_fracs[i - 1] - 1e-9  # allow float rounding

    def test_weighted(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        w = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        model_curve, _ = compute_lorenz_curve(y_true, y_pred, weight=w)
        # Should still have valid endpoints
        assert model_curve[0]["cum_weight_frac"] == 0.0
        assert model_curve[-1]["cum_weight_frac"] == pytest.approx(1.0)

    def test_empty_arrays(self):
        model_curve, perfect_curve = compute_lorenz_curve(np.array([]), np.array([]))
        assert len(model_curve) == 1
        assert model_curve[0] == {"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}

    def test_downsampling(self):
        """With many points, output should be capped at n_points."""
        n = 1000
        rng = np.random.RandomState(42)
        y_true = rng.rand(n) * 10
        y_pred = y_true + rng.randn(n) * 0.5

        model_curve, _ = compute_lorenz_curve(y_true, y_pred, n_points=50)
        assert len(model_curve) <= 50

    def test_perfect_model_curve_dominates(self):
        """The perfect curve should accumulate actual faster than model curve."""
        n = 500
        rng = np.random.RandomState(42)
        y_true = rng.rand(n) * 10
        y_pred = y_true + rng.randn(n) * 2  # noisy predictions

        model_curve, perfect_curve = compute_lorenz_curve(y_true, y_pred, n_points=20)

        # At halfway (cum_weight ~0.5), perfect should have higher cum_actual
        # Find midpoint in each curve
        def _frac_at_half(curve: list[dict]) -> float:
            for pt in curve:
                if pt["cum_weight_frac"] >= 0.45:
                    return pt["cum_actual_frac"]
            return 0.0

        perfect_mid = _frac_at_half(perfect_curve)
        model_mid = _frac_at_half(model_curve)
        assert perfect_mid >= model_mid


# ---------------------------------------------------------------------------
# compute_pdp
# ---------------------------------------------------------------------------


class _MockAlgo:
    """Mock algo that returns the mean of the specified feature column."""

    def predict(self, model: Any, df: pl.DataFrame, features: list[str]) -> np.ndarray:
        # Return the first feature column values as predictions
        return df[features[0]].to_numpy().astype(float)


class TestPdp:
    def test_basic_numeric(self):
        n = 100
        rng = np.random.RandomState(42)
        df = pl.DataFrame({"x": rng.randn(n)})
        algo = _MockAlgo()
        model = MagicMock()

        result = compute_pdp(model, algo, df, ["x"], [], n_grid=10)
        assert len(result) == 1
        assert result[0]["feature"] == "x"
        assert result[0]["type"] == "numeric"
        assert len(result[0]["grid"]) > 0

        for entry in result[0]["grid"]:
            assert "value" in entry
            assert "avg_prediction" in entry

    def test_basic_categorical(self):
        df = pl.DataFrame({"cat": ["a", "b", "c"] * 20})

        class CatAlgo:
            def predict(self, model: Any, df: pl.DataFrame, features: list[str]) -> np.ndarray:
                return np.ones(df.height)

        result = compute_pdp(MagicMock(), CatAlgo(), df, ["cat"], ["cat"], n_grid=10)
        assert len(result) == 1
        assert result[0]["feature"] == "cat"
        assert result[0]["type"] == "categorical"
        values = [e["value"] for e in result[0]["grid"]]
        assert set(values) == {"a", "b", "c"}

    def test_multiple_features(self):
        n = 50
        df = pl.DataFrame({"x": np.arange(n, dtype=float), "y": np.arange(n, dtype=float)})

        class MultiAlgo:
            def predict(self, model: Any, df: pl.DataFrame, features: list[str]) -> np.ndarray:
                return np.ones(df.height)

        result = compute_pdp(MagicMock(), MultiAlgo(), df, ["x", "y"], [], n_grid=5)
        assert len(result) == 2
        assert result[0]["feature"] == "x"
        assert result[1]["feature"] == "y"

    def test_subsamples_large_df(self):
        """DataFrames larger than max_sample should be subsampled."""
        n = 2000
        df = pl.DataFrame({"x": np.arange(n, dtype=float)})
        call_sizes: list[int] = []

        class TrackingAlgo:
            def predict(self, model: Any, df: pl.DataFrame, features: list[str]) -> np.ndarray:
                call_sizes.append(df.height)
                return np.ones(df.height)

        compute_pdp(MagicMock(), TrackingAlgo(), df, ["x"], [], n_grid=5, max_sample=500)
        # All prediction calls should use the subsampled size
        for sz in call_sizes:
            assert sz == 500

    def test_empty_df(self):
        df = pl.DataFrame({"x": pl.Series([], dtype=pl.Float64)})
        result = compute_pdp(MagicMock(), _MockAlgo(), df, ["x"], [])
        assert result == []

    def test_empty_features(self):
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0]})
        result = compute_pdp(MagicMock(), _MockAlgo(), df, [], [])
        assert result == []

    def test_feature_failure_skipped(self):
        """If a feature raises during prediction, it should be skipped."""
        n = 30
        df = pl.DataFrame(
            {
                "good": np.arange(n, dtype=float),
                "bad": np.arange(n, dtype=float),
            }
        )

        class FailOnBadAlgo:
            def predict(self, model: Any, df: pl.DataFrame, features: list[str]) -> np.ndarray:
                # The "bad" feature will have been replaced; detect via grid value
                if df["bad"][0] == df["bad"][1]:
                    raise RuntimeError("Simulated failure")
                return np.ones(df.height)

        result = compute_pdp(MagicMock(), FailOnBadAlgo(), df, ["good", "bad"], [], n_grid=5)
        # "bad" should be skipped; "good" may or may not succeed depending on
        # whether its grid replacement triggers the error. At minimum, no crash.
        assert isinstance(result, list)

    def test_categorical_caps_at_30(self):
        """Categorical features with >30 unique values should be capped at 30."""
        cats = [f"cat_{i}" for i in range(50)]
        df = pl.DataFrame({"c": cats * 2})

        class CatAlgo:
            def predict(self, model: Any, df: pl.DataFrame, features: list[str]) -> np.ndarray:
                return np.ones(df.height)

        result = compute_pdp(MagicMock(), CatAlgo(), df, ["c"], ["c"], n_grid=10)
        assert len(result[0]["grid"]) <= 30

    def test_numeric_grid_deduplication(self):
        """Constant numeric column should produce a single grid point."""
        df = pl.DataFrame({"x": [5.0] * 100})

        class ConstAlgo:
            def predict(self, model: Any, df: pl.DataFrame, features: list[str]) -> np.ndarray:
                return np.ones(df.height)

        result = compute_pdp(MagicMock(), ConstAlgo(), df, ["x"], [], n_grid=20)
        assert len(result[0]["grid"]) == 1
        assert result[0]["grid"][0]["value"] == pytest.approx(5.0)

    def test_preserves_feature_order(self):
        """Output order should match input features order."""
        n = 30
        df = pl.DataFrame(
            {
                "z": np.arange(n, dtype=float),
                "a": np.arange(n, dtype=float),
                "m": np.arange(n, dtype=float),
            }
        )

        class SimpleAlgo:
            def predict(self, model: Any, df: pl.DataFrame, features: list[str]) -> np.ndarray:
                return np.ones(df.height)

        result = compute_pdp(MagicMock(), SimpleAlgo(), df, ["z", "a", "m"], [], n_grid=5)
        assert [r["feature"] for r in result] == ["z", "a", "m"]


# ---------------------------------------------------------------------------
# compute_metrics edge cases
# ---------------------------------------------------------------------------


class TestComputeMetricsEdgeCases:
    def test_empty_arrays_return_nan_or_degenerate(self):
        y_true = np.array([])
        y_pred = np.array([])
        result = compute_metrics(y_true, y_pred, metric_names=["rmse", "mae"])
        for name in ["rmse", "mae"]:
            assert np.isnan(result[name])

    def test_single_sample(self):
        y_true = np.array([3.0])
        y_pred = np.array([2.5])
        result = compute_metrics(y_true, y_pred, metric_names=["rmse", "mae", "mse"])
        assert result["rmse"] == pytest.approx(0.5)
        assert result["mae"] == pytest.approx(0.5)
        assert result["mse"] == pytest.approx(0.25)

    def test_all_identical_predictions(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([3.0, 3.0, 3.0, 3.0, 3.0])
        result = compute_metrics(y_true, y_pred, metric_names=["rmse", "r2", "gini"])
        assert result["rmse"] > 0
        assert result["r2"] == pytest.approx(0.0)

    def test_all_identical_actuals(self):
        y_true = np.array([5.0, 5.0, 5.0, 5.0])
        y_pred = np.array([4.0, 5.0, 6.0, 7.0])
        result = compute_metrics(y_true, y_pred, metric_names=["r2"])
        assert result["r2"] == pytest.approx(0.0)

    def test_nan_values_filtered(self):
        y_true = np.array([1.0, np.nan, 3.0, 4.0])
        y_pred = np.array([1.0, 2.0, 3.0, 4.0])
        result = compute_metrics(y_true, y_pred, metric_names=["rmse"])
        assert np.isfinite(result["rmse"])
        assert result["rmse"] == pytest.approx(0.0)

    def test_inf_values_filtered(self):
        y_true = np.array([1.0, np.inf, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        result = compute_metrics(y_true, y_pred, metric_names=["rmse"])
        assert np.isfinite(result["rmse"])
        assert result["rmse"] == pytest.approx(0.0)

    def test_all_nan_returns_nan(self):
        y_true = np.array([np.nan, np.nan])
        y_pred = np.array([np.nan, np.nan])
        result = compute_metrics(y_true, y_pred, metric_names=["rmse"])
        assert np.isnan(result["rmse"])

    def test_nan_with_weights_filtered(self):
        y_true = np.array([1.0, np.nan, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        weight = np.array([1.0, 2.0, 3.0])
        result = compute_metrics(y_true, y_pred, weight=weight, metric_names=["rmse"])
        assert np.isfinite(result["rmse"])

    def test_unknown_metric_raises(self):
        y_true = np.array([1.0, 2.0])
        y_pred = np.array([1.0, 2.0])
        with pytest.raises(ValueError, match="Unknown metric"):
            compute_metrics(y_true, y_pred, metric_names=["nonexistent_metric"])

    def test_tweedie_deviance_with_variance_power(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 2.1, 3.1])
        result = compute_metrics(
            y_true, y_pred, metric_names=["tweedie_deviance"], variance_power=1.5
        )
        assert np.isfinite(result["tweedie_deviance"])


# ---------------------------------------------------------------------------
# _gini edge cases
# ---------------------------------------------------------------------------


class TestGiniEdgeCases:
    def test_empty_array(self):
        assert _gini(np.array([]), np.array([]), None) == 0.0

    def test_all_same_actuals(self):
        y_true = np.array([5.0, 5.0, 5.0, 5.0])
        y_pred = np.array([1.0, 2.0, 3.0, 4.0])
        result = _gini(y_true, y_pred, None)
        assert result == pytest.approx(1.0)

    def test_all_same_predictions(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0])
        y_pred = np.array([3.0, 3.0, 3.0, 3.0])
        result = _gini(y_true, y_pred, None)
        assert np.isfinite(result)

    def test_perfect_ranking(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        assert _gini(y_true, y_pred, None) == pytest.approx(1.0)

    def test_weighted_gini(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        weight = np.array([1.0, 1.0, 1.0, 1.0, 10.0])
        result = _gini(y_true, y_pred, weight)
        assert np.isfinite(result)
        assert result > 0

    def test_weighted_vs_unweighted_differ(self):
        y_true = np.array([0.5, 5.0, 0.2, 4.0, 1.0, 3.0, 0.1, 2.0])
        y_pred = np.array([0.3, 4.5, 0.5, 3.5, 1.5, 2.5, 0.2, 2.0])
        w = np.array([100.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        unweighted = _gini(y_true, y_pred, None)
        weighted = _gini(y_true, y_pred, w)
        assert abs(unweighted - weighted) > 0.01

    def test_single_sample(self):
        result = _gini(np.array([1.0]), np.array([2.0]), None)
        assert np.isfinite(result)


# ---------------------------------------------------------------------------
# _tweedie_deviance edge cases
# ---------------------------------------------------------------------------


class TestTweedieDevianceEdgeCases:
    def test_gamma_case(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 2.1, 3.1])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=2.0)
        assert np.isfinite(result)
        assert result >= 0

    def test_gamma_perfect_predictions(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=2.0)
        assert result == pytest.approx(0.0, abs=1e-8)

    def test_general_intermediate_p(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0])
        y_pred = np.array([1.2, 1.8, 3.2, 3.8])
        result_15 = _tweedie_deviance(y_true, y_pred, None, variance_power=1.5)
        result_13 = _tweedie_deviance(y_true, y_pred, None, variance_power=1.3)
        result_17 = _tweedie_deviance(y_true, y_pred, None, variance_power=1.7)
        assert np.isfinite(result_15)
        assert np.isfinite(result_13)
        assert np.isfinite(result_17)
        assert result_15 >= 0
        assert result_13 >= 0
        assert result_17 >= 0

    def test_zero_y_true_floored(self):
        y_true = np.array([0.0, 0.0, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=1.5)
        assert np.isfinite(result)

    def test_negative_y_true_floored(self):
        y_true = np.array([-1.0, 2.0, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=1.5)
        assert np.isfinite(result)

    def test_zero_y_pred_floored(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([0.0, 0.0, 3.0])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=1.5)
        assert np.isfinite(result)

    def test_negative_y_pred_floored(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([-1.0, 2.0, 3.0])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=1.5)
        assert np.isfinite(result)

    def test_poisson_case_delegates(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 2.1, 3.1])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=1.0)
        assert np.isfinite(result)
        assert result >= 0

    def test_weighted(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 2.1, 3.1])
        w = np.array([1.0, 2.0, 3.0])
        result = _tweedie_deviance(y_true, y_pred, w, variance_power=1.5)
        assert np.isfinite(result)

    def test_gamma_zero_y_true_floored(self):
        y_true = np.array([0.0, 2.0, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=2.0)
        assert np.isfinite(result)

    def test_gamma_zero_y_pred_floored(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([0.0, 2.0, 3.0])
        result = _tweedie_deviance(y_true, y_pred, None, variance_power=2.0)
        assert np.isfinite(result)


# ---------------------------------------------------------------------------
# _auc / _logloss edge cases
# ---------------------------------------------------------------------------


class TestAucEdgeCases:
    def test_all_same_class_zero(self):
        y_true = np.array([0, 0, 0, 0])
        y_pred = np.array([0.1, 0.2, 0.3, 0.4])
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = _auc(y_true, y_pred, None)
        assert np.isnan(result)

    def test_all_same_class_one(self):
        y_true = np.array([1, 1, 1, 1])
        y_pred = np.array([0.6, 0.7, 0.8, 0.9])
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = _auc(y_true, y_pred, None)
        assert np.isnan(result)

    def test_predictions_exactly_zero_and_one(self):
        y_true = np.array([0, 0, 1, 1])
        y_pred = np.array([0.0, 0.0, 1.0, 1.0])
        result = _auc(y_true, y_pred, None)
        assert result == pytest.approx(1.0)

    def test_perfect_separation(self):
        y_true = np.array([0, 0, 0, 1, 1, 1])
        y_pred = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
        result = _auc(y_true, y_pred, None)
        assert result == pytest.approx(1.0)


class TestLoglossEdgeCases:
    def test_all_same_class_zero_raises(self):
        y_true = np.array([0, 0, 0])
        y_pred = np.array([0.1, 0.2, 0.3])
        with pytest.raises(ValueError):
            _logloss(y_true, y_pred, None)

    def test_all_same_class_one_raises(self):
        y_true = np.array([1, 1, 1])
        y_pred = np.array([0.7, 0.8, 0.9])
        with pytest.raises(ValueError):
            _logloss(y_true, y_pred, None)

    def test_predictions_exactly_zero_and_one(self):
        y_true = np.array([0, 1])
        y_pred = np.array([1e-15, 1.0 - 1e-15])
        result = _logloss(y_true, y_pred, None)
        assert np.isfinite(result)
        assert result >= 0


# ---------------------------------------------------------------------------
# compute_lorenz_curve additional edge cases
# ---------------------------------------------------------------------------


class TestLorenzCurveEdgeCases:
    def test_all_same_actuals(self):
        y_true = np.array([3.0, 3.0, 3.0, 3.0, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        model_curve, perfect_curve = compute_lorenz_curve(y_true, y_pred)
        assert model_curve[0]["cum_weight_frac"] == 0.0
        assert model_curve[-1]["cum_weight_frac"] == pytest.approx(1.0)
        assert model_curve[-1]["cum_actual_frac"] == pytest.approx(1.0)

    def test_perfect_model(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        model_curve, perfect_curve = compute_lorenz_curve(y_true, y_pred)
        model_w = [p["cum_weight_frac"] for p in model_curve]
        model_a = [p["cum_actual_frac"] for p in model_curve]
        perfect_w = [p["cum_weight_frac"] for p in perfect_curve]
        perfect_a = [p["cum_actual_frac"] for p in perfect_curve]
        for mw, ma, pw, pa in zip(model_w, model_a, perfect_w, perfect_a):
            assert mw == pytest.approx(pw, abs=1e-4)
            assert ma == pytest.approx(pa, abs=1e-4)
