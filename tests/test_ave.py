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
        """With few rows, a number with many values gets fewer bands: a third of its rows."""
        n = 24
        df = pl.DataFrame({"x": list(range(n))})
        y_true = np.arange(n, dtype=float)
        y_pred = y_true * 1.1

        (result,) = compute_ave_per_feature(df, ["x"], [], y_true, y_pred, n_bins=10)
        assert result["grouping"] == "equal exposure"
        assert [b["label"] for b in result["bins"]] == [
            "0–2",
            "3–5",
            "6–8",
            "9–11",
            "12–14",
            "15–17",
            "18–20",
            "21–23",
        ]


class TestAveGrouping:
    """How the x-axis is grouped: by value, by equal exposure, and in each level's order."""

    def test_a_number_with_few_values_charts_one_bin_per_value(self):
        # NCD years with 45% of rows at 9: quantile cuts would merge 8 and 9 into one bin.
        ncd = [0, 1, 2, 3, 4, 5, 6, 7, 8] * 5 + [9] * 37
        df = pl.DataFrame({"ncd": ncd, "flag": [0, 1] * 41})
        y = np.ones(len(ncd))
        ncd_result, flag_result = compute_ave_per_feature(df, ["ncd", "flag"], [], y, y, n_bins=10)
        assert ncd_result["grouping"] == flag_result["grouping"] == "each value"
        assert [b["label"] for b in ncd_result["bins"]] == [str(v) for v in range(10)]
        assert [b["label"] for b in flag_result["bins"]] == ["0", "1"]
        assert ncd_result["bins"][-1]["exposure"] == 37

    def test_bands_hold_equal_exposure_are_closed_on_the_right_and_never_split_a_tie(self):
        # 30 values; value 0 carries half the exposure, so the first band is that value
        # alone, and the other half splits into three bands of about equal exposure.
        values = np.arange(30.0)
        exposure = np.where(values == 0, 29.0, 1.0)
        df = pl.DataFrame({"x": values})
        y = np.ones(30)
        (result,) = compute_ave_per_feature(df, ["x"], [], y, y, exposure=exposure, n_bins=4)
        assert result["grouping"] == "equal exposure"
        bins = {b["label"]: b["exposure"] for b in result["bins"]}
        # Of 58, the shares 14.5 and 29 are both reached at 0, so they collapse into one
        # band; 43.5 is reached at 15 (29 + 15), which closes the next band.
        assert bins == {"0": 29.0, "1–15": 15.0, "16–29": 14.0}

    def test_large_numbers_are_labelled_without_an_exponent(self):
        df = pl.DataFrame({"value": np.linspace(100_000, 130_000, 60)})
        y = np.ones(60)
        (result,) = compute_ave_per_feature(df, ["value"], [], y, y, n_bins=2)
        assert [b["label"] for b in result["bins"]] == ["100000–114700", "115300–130000"]

    def test_categories_keep_an_enum_order_a_numbered_order_or_a_natural_order(self):
        enum = pl.Enum(["low", "mid", "high"])
        df = pl.DataFrame(
            {
                "band": pl.Series(["high", "low", "mid", "high"], dtype=enum),
                "group": ["10", "2", "1", "2"],
                "brand": ["B10", "b2", "B1", "A"],
            }
        )
        y = np.ones(4)
        band, group, brand = compute_ave_per_feature(
            df, ["band", "group", "brand"], ["band", "group", "brand"], y, y
        )
        assert (band["grouping"], [b["label"] for b in band["bins"]]) == (
            "in order",
            ["low", "mid", "high"],
        )
        assert (group["grouping"], [b["label"] for b in group["bins"]]) == (
            "in order",
            ["1", "2", "10"],
        )
        assert (brand["grouping"], [b["label"] for b in brand["bins"]]) == (
            "A–Z",
            ["A", "B1", "b2", "B10"],
        )

    def test_many_numbered_levels_are_joined_into_bands(self):
        df = pl.DataFrame({"vehicle_group": [str(v) for v in range(1, 101)]})
        y = np.ones(100)
        (result,) = compute_ave_per_feature(
            df, ["vehicle_group"], ["vehicle_group"], y, y, n_bins=4
        )
        assert result["grouping"] == "equal exposure"
        assert [b["label"] for b in result["bins"]] == ["1–25", "26–50", "51–75", "76–100"]

    def test_missing_is_last_never_ranked_and_never_meets_a_level_of_that_name(self):
        df = pl.DataFrame({"c": [None, None, None, "Missing", "__MISSING__", "a", "b", "c"]})
        y = np.ones(8)
        (result,) = compute_ave_per_feature(df, ["c"], ["c"], y, y, max_categories=2)
        assert result["grouping"] == "by exposure"
        assert [(b["label"], b["exposure"]) for b in result["bins"]] == [
            ("Missing", 1.0),
            ("__MISSING__", 1.0),
            ("Other (3 levels)", 3.0),
            ("Missing", 3.0),
        ]


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
        assert len(labels) == 16  # 15 + "Other"
        assert labels[-1] == "Other (15 levels)"
        assert result[0]["grouping"] == "by exposure"


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


def _bin_values(feature):
    """Each bin's (exposure, avg_actual, avg_predicted), by label in the order reported."""
    return {
        b["label"]: (b["exposure"], b["avg_actual"], b["avg_predicted"]) for b in feature["bins"]
    }


def _bin(exposure, avg_actual, avg_predicted):
    """An expected bin, to the six significant figures a bin is rounded to."""
    return pytest.approx((exposure, avg_actual, avg_predicted), rel=1e-5, abs=1e-12)


class TestAveWithExposure:
    """With an exposure, a bin's averages are per unit of it and its strip is the mass."""

    # Six rows of claim counts and expected claims (the rate times the exposure). ``x``
    # has a bin per row (it has six values); ``c`` groups them as a = 0, 1, 3 and b = 2, 4, 5.
    FRAME = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "c": ["a", "a", "b", "a", "b", "b"]})
    CLAIMS = np.array([1.0, 1.0, 2.0, 0.0, 1.0, 0.0])
    EXPECTED = np.array([0.2, 0.1, 0.5, 0.1, 0.05, 0.3])
    EXPOSURE = np.array([0.5, 0.25, 1.0, 0.5, 0.25, 0.5])

    def test_bins_report_claims_per_unit_of_exposure_and_the_summed_exposure(self):
        numeric, categorical = compute_ave_per_feature(
            self.FRAME,
            ["x", "c"],
            ["c"],
            self.CLAIMS,
            self.EXPECTED,
            exposure=self.EXPOSURE,
            n_bins=2,
        )
        # Each row's claims and expected claims over its own exposure.
        assert _bin_values(numeric) == {
            "1": _bin(0.5, 2.0, 0.4),
            "2": _bin(0.25, 4.0, 0.4),
            "3": _bin(1.0, 2.0, 0.5),
            "4": _bin(0.5, 0.0, 0.2),
            "5": _bin(0.25, 4.0, 0.2),
            "6": _bin(0.5, 0.0, 0.6),
        }
        assert _bin_values(categorical) == {
            "b": _bin(1.75, 3 / 1.75, 0.85 / 1.75),
            "a": _bin(1.25, 1.6, 0.32),
        }

    def test_a_weight_multiplies_the_exposure_and_weights_the_claims(self):
        weight = np.array([1.0, 2.0, 3.0, 4.0, 2.0, 2.0])
        numeric, categorical = compute_ave_per_feature(
            self.FRAME,
            ["x", "c"],
            ["c"],
            self.CLAIMS,
            self.EXPECTED,
            weight,
            exposure=self.EXPOSURE,
            n_bins=2,
        )
        # Mass is weight times exposure: 0.5, 0.5, 3, 2, 0.5, 1. Weighted claims are
        # 1, 2, 6, 0, 2, 0 and weighted expected claims 0.2, 0.2, 1.5, 0.4, 0.1, 0.6.
        assert _bin_values(numeric) == {
            "1": _bin(0.5, 2.0, 0.4),
            "2": _bin(0.5, 4.0, 0.4),
            "3": _bin(3.0, 2.0, 0.5),
            "4": _bin(2.0, 0.0, 0.2),
            "5": _bin(0.5, 4.0, 0.2),
            "6": _bin(1.0, 0.0, 0.6),
        }
        assert _bin_values(categorical) == {
            "b": _bin(4.5, 8 / 4.5, 2.2 / 4.5),
            "a": _bin(3.0, 1.0, 0.8 / 3),
        }

    def test_the_missing_bin_and_other_lump_are_rates_and_levels_rank_by_mass(self):
        """Levels rank by mass; Missing is not ranked and comes last."""
        frame = pl.DataFrame(
            {
                "x": [1.0, 2.0, 3.0, 4.0, None, None, 5.0],
                "c": ["big", "many", "many", "many", "few", "few", None],
            }
        )
        numeric, categorical = compute_ave_per_feature(
            frame,
            ["x", "c"],
            ["c"],
            np.array([2.0, 1.0, 0.0, 2.0, 0.0, 1.0, 3.0]),
            np.array([1.5, 0.2, 0.1, 0.3, 0.1, 0.2, 1.0]),
            np.array([1.0, 0.25, 0.25, 0.5, 3.0, 3.0, 2.0]),
            exposure=np.array([4.0, 1.0, 1.0, 1.0, 0.125, 0.125, 1.0]),
            n_bins=2,
            max_categories=2,
        )
        # The two rows missing ``x`` hold 3 weighted claims and 0.9 weighted expected
        # claims over a mass of 0.75.
        assert _bin_values(numeric)["Missing"] == _bin(0.75, 4.0, 1.2)
        # By mass the levels rank big 4, many 1, few 0.75. The two kept would be few and
        # many by weight, big and many by exposure, many and few by rows.
        values = _bin_values(categorical)
        assert list(values) == ["big", "many", "Other (1 level)", "Missing"]
        assert values == {
            "big": _bin(4.0, 0.5, 0.375),
            "many": _bin(1.0, 1.25, 0.225),
            "Other (1 level)": _bin(0.75, 4.0, 1.2),
            "Missing": _bin(2.0, 3.0, 1.0),
        }

    def test_a_row_dropped_as_non_finite_takes_its_exposure_with_it(self):
        """A missing exposure scores a non-finite prediction; the row is dropped whole."""
        expected = self.EXPECTED.copy()
        exposure = self.EXPOSURE.copy()
        expected[1] = exposure[1] = np.nan

        (categorical,) = compute_ave_per_feature(
            self.FRAME, ["c"], ["c"], self.CLAIMS, expected, exposure=exposure
        )
        assert _bin_values(categorical) == {
            "b": _bin(1.75, 3 / 1.75, 0.85 / 1.75),
            "a": _bin(1.0, 1.0, 0.3),
        }

    @pytest.mark.parametrize("invalid", [0.0, -0.5, float("nan"), float("inf")])
    def test_an_exposure_that_is_not_finite_and_positive_is_refused(self, invalid):
        exposure = self.EXPOSURE.copy()
        exposure[3] = invalid
        with pytest.raises(ValueError, match="'ave_per_feature' must be finite and positive"):
            compute_ave_per_feature(
                self.FRAME, ["x"], [], self.CLAIMS, self.EXPECTED, exposure=exposure
            )


@pytest.mark.parametrize("categorical", [False, True])
def test_categorical_ave_matches_manual_weighted_groups_with_missing_and_other(categorical):
    rng = np.random.default_rng(142)
    levels = ["a", "b", "c", "d", "long" * 1000, None]
    values = rng.choice(levels, 1200)
    column = pl.Series("region", values)
    if categorical:
        column = column.cast(pl.Categorical)
    actual, predicted = rng.random(1200), rng.random(1200)
    weight = rng.integers(0, 4, 1200).astype(float)
    exposure = rng.integers(1, 4, 1200).astype(float)
    result = compute_ave_per_feature(
        pl.DataFrame([column]),
        ["region"],
        ["region"],
        actual,
        predicted,
        weight,
        exposure=exposure,
        max_categories=3,
    )
    groups = {}
    for level in levels:
        mask = np.array([value == level for value in values])
        groups[level] = (
            float((weight[mask] * exposure[mask]).sum()),
            float((weight[mask] * actual[mask]).sum()),
            float((weight[mask] * predicted[mask]).sum()),
        )
    missing = groups.pop(None)
    ranked = sorted(groups.items(), key=lambda item: (-item[1][0], item[0]))
    rest = [sums for _, sums in ranked[3:]]
    kept = [
        *ranked[:3],
        (f"Other ({len(rest)} levels)", tuple(sum(s[i] for s in rest) for i in range(3))),
        ("Missing", missing),
    ]

    def significant(value):
        return float(f"{value:.6g}")

    expected = [
        dict(
            label=label,
            exposure=significant(mass),
            avg_actual=significant(a / mass),
            avg_predicted=significant(p / mass),
        )
        for label, (mass, a, p) in kept
    ]
    assert result[0]["bins"] == expected


def test_categorical_ave_keeps_mixed_length_labels_compact():
    import tracemalloc

    # A single long label must not pad thousands of ordinary levels to its width.
    levels = [f"a{i:04}" for i in range(2000)] + ["z" * 4096]
    data = pl.DataFrame({"level": levels})
    actual = np.ones(len(levels))
    predicted = actual * 1.5
    tracemalloc.start()
    try:
        result = compute_ave_per_feature(
            data, ["level"], ["level"], actual, predicted, max_categories=3
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024**2
    assert result[0]["bins"] == [
        dict(label=label, exposure=mass, avg_actual=1.0, avg_predicted=1.5)
        for label, mass in [
            ("a0000", 1.0),
            ("a0001", 1.0),
            ("a0002", 1.0),
            ("Other (1998 levels)", 1998.0),
        ]
    ]
