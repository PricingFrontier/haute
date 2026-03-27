"""Tests for diagnostic computation functions in haute.modelling._metrics."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from haute.modelling._metrics import (
    compute_actual_vs_predicted,
    compute_ave_per_feature,
    compute_lorenz_curve,
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
