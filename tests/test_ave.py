"""Tests for compute_ave_per_feature and its private helpers."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from haute.modelling._metrics import compute_ave_per_feature


class TestAveNumericBins:
    def test_basic_10_bins(self):
        """100 rows with 10 bins should produce ~10 bin entries."""
        rng = np.random.RandomState(42)
        n = 100
        df = pl.DataFrame({"feat": rng.randn(n)})
        y_true = rng.randn(n)
        y_pred = y_true + rng.randn(n) * 0.1

        result = compute_ave_per_feature(
            df,
            ["feat"],
            [],
            y_true,
            y_pred,
            n_bins=10,
        )
        assert len(result) == 1
        assert result[0]["feature"] == "feat"
        assert result[0]["type"] == "numeric"
        # Should have up to 10 bins (may be fewer if quantile edges coincide)
        assert 2 <= len(result[0]["bins"]) <= 10

    def test_bin_structure(self):
        """Each bin must have label, exposure, avg_actual, avg_predicted."""
        n = 50
        df = pl.DataFrame({"x": list(range(n))})
        y_true = np.arange(n, dtype=float)
        y_pred = y_true * 1.1

        result = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, n_bins=5)
        for b in result[0]["bins"]:
            assert "label" in b
            assert "exposure" in b
            assert "avg_actual" in b
            assert "avg_predicted" in b


class TestAveCategoricalBins:
    def test_unique_categories(self):
        """Categorical feature with distinct values."""
        cats = ["a", "b", "c", "d"]
        n = len(cats) * 10
        df = pl.DataFrame({"cat": cats * 10})
        y_true = np.random.RandomState(42).randn(n)
        y_pred = y_true + 0.1

        result = compute_ave_per_feature(
            df,
            ["cat"],
            ["cat"],
            y_true,
            y_pred,
        )
        assert len(result) == 1
        assert result[0]["type"] == "categorical"
        labels = [b["label"] for b in result[0]["bins"]]
        assert set(labels) == {"a", "b", "c", "d"}


class TestAveWithWeight:
    def test_weighted_averages_differ(self):
        """Weighted means should differ from unweighted when weights vary."""
        n = 100
        df = pl.DataFrame({"x": np.linspace(0, 1, n)})
        y_true = np.ones(n)
        y_pred = np.ones(n) * 1.1
        w_equal = np.ones(n)
        w_skewed = np.linspace(1, 10, n)

        r1 = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, w_equal, n_bins=5)
        r2 = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, w_skewed, n_bins=5)
        # Exposure totals should differ
        exp1 = sum(b["exposure"] for b in r1[0]["bins"])
        exp2 = sum(b["exposure"] for b in r2[0]["bins"])
        assert exp1 != pytest.approx(exp2)


class TestAveWithoutWeight:
    def test_uses_count(self):
        """Without weight, exposure should equal row count per bin."""
        n = 30
        df = pl.DataFrame({"x": list(range(n))})
        y_true = np.arange(n, dtype=float)
        y_pred = y_true + 1

        result = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, n_bins=3)
        total_exposure = sum(b["exposure"] for b in result[0]["bins"])
        assert total_exposure == pytest.approx(float(n))


class TestAveNanHandling:
    def test_nan_creates_missing_bin(self):
        """NaN values should create a 'Missing' bin."""
        n = 40
        vals = np.arange(n, dtype=float)
        vals[:5] = np.nan
        df = pl.DataFrame({"x": vals})
        y_true = np.ones(n)
        y_pred = np.ones(n) * 0.9

        result = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, n_bins=5)
        labels = [b["label"] for b in result[0]["bins"]]
        assert "Missing" in labels


class TestAveConstantFeature:
    def test_single_bin(self):
        """A constant feature should produce a single bin."""
        n = 50
        df = pl.DataFrame({"x": [3.14] * n})
        y_true = np.random.RandomState(42).randn(n)
        y_pred = y_true + 0.1

        result = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, n_bins=10)
        assert len(result[0]["bins"]) == 1


class TestAveFewRows:
    def test_auto_reduces_bins(self):
        """With few rows, effective bins should be reduced."""
        n = 6
        df = pl.DataFrame({"x": list(range(n))})
        y_true = np.arange(n, dtype=float)
        y_pred = y_true * 1.1

        result = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, n_bins=10)
        # With 6 rows, effective_bins = min(10, max(2, 6//3)) = min(10, 2) = 2
        assert len(result[0]["bins"]) <= 3


class TestAveManyCategories:
    def test_top_15_plus_other(self):
        """More than 15 categories should be capped at 15 + Other."""
        cats = [f"cat_{i}" for i in range(30)]
        n = len(cats) * 5
        df = pl.DataFrame({"c": (cats * 5)[:n]})
        y_true = np.random.RandomState(42).randn(n)
        y_pred = y_true

        result = compute_ave_per_feature(
            df,
            ["c"],
            ["c"],
            y_true,
            y_pred,
            max_categories=15,
        )
        labels = [b["label"] for b in result[0]["bins"]]
        assert len(labels) <= 16  # 15 + "Other"
        assert "Other" in labels


class TestAveMaxFeatures:
    def test_respects_limit(self):
        """Only process up to max_features."""
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


class TestAveCategoricalNulls:
    def test_none_values_become_missing_label(self):
        """None values in a categorical column should appear as 'Missing'."""
        df = pl.DataFrame({"c": ["a", "b", None, None, "a"]})
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([1.1, 2.1, 3.1, 4.1, 5.1])

        result = compute_ave_per_feature(df, ["c"], ["c"], y_true, y_pred)
        labels = [b["label"] for b in result[0]["bins"]]
        assert "Missing" in labels, f"Expected 'Missing' label, got {labels}"
        # __MISSING__ should NOT appear as a raw label
        assert "__MISSING__" not in labels

    def test_all_none_categorical(self):
        """Categorical column of all Nones should produce a single 'Missing' bin."""
        n = 10
        df = pl.DataFrame({"c": pl.Series([None] * n, dtype=pl.Utf8)})
        y_true = np.ones(n)
        y_pred = np.ones(n) * 0.9

        result = compute_ave_per_feature(df, ["c"], ["c"], y_true, y_pred)
        labels = [b["label"] for b in result[0]["bins"]]
        assert labels == ["Missing"]

    def test_none_values_weighted_correctly(self):
        """Missing category should have correct weighted average."""
        df = pl.DataFrame({"c": ["a", None]})
        y_true = np.array([10.0, 20.0])
        y_pred = np.array([11.0, 22.0])
        w = np.array([1.0, 3.0])

        result = compute_ave_per_feature(df, ["c"], ["c"], y_true, y_pred, w)
        bins_map = {b["label"]: b for b in result[0]["bins"]}
        missing_bin = bins_map["Missing"]
        assert missing_bin["exposure"] == pytest.approx(3.0)
        assert missing_bin["avg_actual"] == pytest.approx(20.0)
        assert missing_bin["avg_predicted"] == pytest.approx(22.0)


class TestAveNumericAllNan:
    def test_all_nan_produces_only_missing_bin(self):
        """A column of all NaN values should produce only a 'Missing' bin."""
        n = 10
        df = pl.DataFrame({"x": [float("nan")] * n})
        y_true = np.ones(n)
        y_pred = np.ones(n) * 1.5

        result = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, n_bins=5)
        bins = result[0]["bins"]
        assert len(bins) == 1
        assert bins[0]["label"] == "Missing"
        assert bins[0]["exposure"] == pytest.approx(float(n))

    def test_nan_bin_has_correct_averages(self):
        """NaN bin should compute correct weighted AvE."""
        n = 20
        vals = np.arange(n, dtype=float)
        vals[:4] = np.nan
        df = pl.DataFrame({"x": vals})
        y_true = np.arange(n, dtype=float) * 2.0
        y_pred = np.arange(n, dtype=float) * 2.5
        w = np.ones(n) * 2.0

        result = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, w, n_bins=5)
        bins_map = {b["label"]: b for b in result[0]["bins"]}
        missing = bins_map["Missing"]
        # NaN rows are indices 0-3: y_true=[0,2,4,6], y_pred=[0,2.5,5,7.5], w=[2,2,2,2]
        assert missing["exposure"] == pytest.approx(8.0)
        assert missing["avg_actual"] == pytest.approx(3.0)  # (0+2+4+6)/4
        assert missing["avg_predicted"] == pytest.approx(3.75)  # (0+2.5+5+7.5)/4


class TestAveMissingFeature:
    def test_missing_feature_in_df_skipped(self):
        """A feature not present in the DataFrame should be silently skipped."""
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0]})
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 2.1, 3.1])

        result = compute_ave_per_feature(
            df,
            ["x", "nonexistent"],
            [],
            y_true,
            y_pred,
        )
        assert len(result) == 1
        assert result[0]["feature"] == "x"


class TestAveEmpty:
    def test_empty_features(self):
        result = compute_ave_per_feature(
            pl.DataFrame({"x": [1.0]}),
            [],
            [],
            np.array([1.0]),
            np.array([1.0]),
        )
        assert result == []

    def test_empty_arrays(self):
        result = compute_ave_per_feature(
            pl.DataFrame({"x": pl.Series([], dtype=pl.Float64)}),
            ["x"],
            [],
            np.array([]),
            np.array([]),
        )
        assert result == []
