"""Tests for haute.modelling — TrainingJob, algorithms, metrics, splits."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from haute.modelling._algorithms import (
    ALGORITHM_REGISTRY,
    CatBoostAlgorithm,
    FitResult,
    resolve_loss_function,
)
from haute.modelling._metrics import compute_double_lift, compute_metrics
from haute.modelling._split import (
    PARTITION_HOLDOUT,
    PARTITION_TRAIN,
    PARTITION_VALIDATION,
    SplitConfig,
    _assign_group_split,
    split_data,
    split_mask,
)
from haute.modelling._training_job import TrainingJob, TrainResult

# ---------------------------------------------------------------------------
# SplitConfig validation
# ---------------------------------------------------------------------------


class TestSplitConfig:
    def test_invalid_validation_size_zero(self):
        """validation_size=0 is valid (no validation set)."""
        SplitConfig(validation_size=0)
        with pytest.raises(ValueError, match="validation_size"):
            SplitConfig(validation_size=-0.1)

    def test_invalid_validation_size_one(self):
        with pytest.raises(ValueError, match="validation_size"):
            SplitConfig(validation_size=1.0)

    def test_temporal_requires_date_column(self):
        with pytest.raises(ValueError, match="date_column"):
            SplitConfig(strategy="temporal", cutoff_date="2024-01-01")

    def test_temporal_requires_cutoff_date(self):
        with pytest.raises(ValueError, match="cutoff_date"):
            SplitConfig(strategy="temporal", date_column="date")

    def test_group_requires_group_column(self):
        with pytest.raises(ValueError, match="group_column"):
            SplitConfig(strategy="group")


# ---------------------------------------------------------------------------
# split_data
# ---------------------------------------------------------------------------


class TestSplitData:
    @pytest.fixture()
    def sample_df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "x": list(range(100)),
                "y": [float(i % 3) for i in range(100)],
                "group": [f"g{i % 5}" for i in range(100)],
                "date": [f"2024-{(i % 12) + 1:02d}-15" for i in range(100)],
            }
        )

    def test_empty_dataframe_raises(self):
        df = pl.DataFrame({"x": pl.Series([], dtype=pl.Int64)})
        with pytest.raises(ValueError, match="empty"):
            split_data(df, SplitConfig())

    def test_random_split_proportions(self, sample_df):
        train, test = split_data(sample_df, SplitConfig(validation_size=0.2, seed=42))
        assert len(train) == 80
        assert len(test) == 20
        # No row overlap
        train_idx = set(train["x"].to_list())
        test_idx = set(test["x"].to_list())
        assert train_idx & test_idx == set()

    def test_random_split_seed_reproducible(self, sample_df):
        t1, _ = split_data(sample_df, SplitConfig(validation_size=0.2, seed=42))
        t2, _ = split_data(sample_df, SplitConfig(validation_size=0.2, seed=42))
        assert t1["x"].to_list() == t2["x"].to_list()

    def test_random_split_different_seed(self, sample_df):
        t1, _ = split_data(sample_df, SplitConfig(validation_size=0.2, seed=42))
        t2, _ = split_data(sample_df, SplitConfig(validation_size=0.2, seed=99))
        assert t1["x"].to_list() != t2["x"].to_list()

    def test_temporal_split(self, sample_df):
        config = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        train, test = split_data(sample_df, config)
        assert len(train) + len(test) == len(sample_df)
        # All train dates < cutoff, all test dates >= cutoff
        assert all(d < "2024-07-01" for d in train["date"].to_list())
        assert all(d >= "2024-07-01" for d in test["date"].to_list())

    def test_temporal_split_missing_column(self, sample_df):
        config = SplitConfig(
            strategy="temporal",
            date_column="nonexistent",
            cutoff_date="2024-07-01",
        )
        with pytest.raises(ValueError, match="not found"):
            split_data(sample_df, config)

    def test_group_split(self, sample_df):
        config = SplitConfig(strategy="group", group_column="group", validation_size=0.3, seed=42)
        train, test = split_data(sample_df, config)
        assert len(train) + len(test) == len(sample_df)
        # All rows of a group go to the same set
        train_groups = set(train["group"].unique().to_list())
        test_groups = set(test["group"].unique().to_list())
        assert train_groups & test_groups == set()

    def test_group_split_missing_column(self, sample_df):
        config = SplitConfig(strategy="group", group_column="nonexistent")
        with pytest.raises(ValueError, match="not found"):
            split_data(sample_df, config)


# ---------------------------------------------------------------------------
# split_mask (partition mask: 0=train, 1=validation, 2=holdout)
# ---------------------------------------------------------------------------


class TestSplitMask:
    def test_random_mask_correct_ratio(self):
        n = 1000
        mask = split_mask(n, SplitConfig(validation_size=0.2, seed=42))
        assert len(mask) == n
        assert mask.dtype == pl.Int8
        train_n = int((mask == PARTITION_TRAIN).sum())
        val_n = int((mask == PARTITION_VALIDATION).sum())
        assert train_n == 800
        assert val_n == 200

    def test_random_mask_with_holdout(self):
        n = 1000
        mask = split_mask(n, SplitConfig(validation_size=0.2, holdout_size=0.1, seed=42))
        train_n = int((mask == PARTITION_TRAIN).sum())
        val_n = int((mask == PARTITION_VALIDATION).sum())
        ho_n = int((mask == PARTITION_HOLDOUT).sum())
        assert train_n == 700
        assert val_n == 200
        assert ho_n == 100
        assert train_n + val_n + ho_n == n

    def test_random_mask_no_validation_no_holdout(self):
        n = 1000
        mask = split_mask(n, SplitConfig(validation_size=0, holdout_size=0, seed=42))
        assert int((mask == PARTITION_TRAIN).sum()) == n

    def test_random_mask_deterministic(self):
        cfg = SplitConfig(validation_size=0.2, seed=42)
        m1 = split_mask(500, cfg)
        m2 = split_mask(500, cfg)
        assert m1.to_list() == m2.to_list()

    def test_random_mask_different_seed(self):
        m1 = split_mask(500, SplitConfig(validation_size=0.2, seed=42))
        m2 = split_mask(500, SplitConfig(validation_size=0.2, seed=99))
        assert m1.to_list() != m2.to_list()

    def test_temporal_mask_splits_by_date(self):
        df = pl.DataFrame({"date": ["2024-01-01", "2024-06-15", "2024-12-31"]})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        mask = split_mask(len(df), cfg, df=df)
        # Before cutoff = train (0), on/after cutoff = validation (1)
        assert mask.to_list() == [PARTITION_TRAIN, PARTITION_TRAIN, PARTITION_VALIDATION]

    def test_temporal_mask_missing_df_raises(self):
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        with pytest.raises(ValueError, match="requires df"):
            split_mask(10, cfg, df=None)

    def test_group_mask_keeps_groups_intact(self):
        df = pl.DataFrame(
            {
                "group": [f"g{i % 5}" for i in range(100)],
            }
        )
        cfg = SplitConfig(strategy="group", group_column="group", validation_size=0.3, seed=42)
        mask = split_mask(len(df), cfg, df=df)
        # Each group should be entirely in one partition
        labeled = df.with_columns(mask)
        for group_val in df["group"].unique().to_list():
            group_partitions = labeled.filter(pl.col("group") == group_val)["_partition"].to_list()
            assert len(set(group_partitions)) == 1, f"Group {group_val} split across partitions"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            split_mask(0, SplitConfig())

    # --- Temporal mask with holdout ---

    def test_temporal_mask_with_holdout(self):
        """Temporal split: holdout = most recent, validation = next, train = oldest."""
        dates = [f"2024-{m:02d}-15" for m in range(1, 13)]
        df = pl.DataFrame({"date": dates})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
            validation_size=0.2,
            holdout_size=0.1,
        )
        mask = split_mask(len(df), cfg, df=df)
        assert len(mask) == 12
        # First 6 months (Jan-Jun) are train
        for i in range(6):
            assert mask[i] == PARTITION_TRAIN
        # Post-cutoff rows are split into validation and holdout
        post_cutoff = mask[6:].to_list()
        # At least some holdout and some validation among the 6 post-cutoff rows
        assert PARTITION_HOLDOUT in post_cutoff
        assert PARTITION_VALIDATION in post_cutoff

    def test_temporal_mask_holdout_is_most_recent(self):
        """Holdout should be the most-recent rows within post-cutoff data."""
        dates = [f"2024-{m:02d}-01" for m in range(1, 11)]
        df = pl.DataFrame({"date": dates})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-06-01",
            validation_size=0.3,
            holdout_size=0.2,
        )
        mask = split_mask(len(df), cfg, df=df)
        # Pre-cutoff (Jan-May) = train
        for i in range(5):
            assert mask[i] == PARTITION_TRAIN
        # Post-cutoff: 5 rows (Jun-Oct)
        # Holdout should contain the most-recent rows
        post_labels = mask[5:].to_list()
        holdout_positions = [i for i, v in enumerate(post_labels) if v == PARTITION_HOLDOUT]
        val_positions = [i for i, v in enumerate(post_labels) if v == PARTITION_VALIDATION]
        if holdout_positions and val_positions:
            # Holdout positions should be later than validation positions
            assert min(holdout_positions) >= min(val_positions)

    def test_temporal_mask_date_column_not_found(self):
        """Missing date column should raise ValueError."""
        df = pl.DataFrame({"x": [1, 2, 3]})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        with pytest.raises(ValueError, match="not found"):
            split_mask(len(df), cfg, df=df)

    def test_temporal_mask_no_holdout_no_validation(self):
        """Temporal split with validation_size=0 should reclassify to train."""
        dates = ["2024-01-01", "2024-06-15", "2024-12-31"]
        df = pl.DataFrame({"date": dates})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
            validation_size=0,
            holdout_size=0,
        )
        mask = split_mask(len(df), cfg, df=df)
        # All should be train when validation_size=0 and holdout_size=0
        assert all(v == PARTITION_TRAIN for v in mask.to_list())

    def test_temporal_mask_string_dates(self):
        """Temporal mask should handle Utf8 date columns properly."""
        df = pl.DataFrame({"date": ["2024-01-15", "2024-06-15", "2024-08-15", "2024-12-15"]})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
            validation_size=0.3,
            holdout_size=0.2,
        )
        mask = split_mask(len(df), cfg, df=df)
        assert mask[0] == PARTITION_TRAIN
        assert mask[1] == PARTITION_TRAIN

    # --- Group mask with holdout ---

    def test_group_mask_with_holdout(self):
        """Group mask should produce train, validation, and holdout partitions."""
        df = pl.DataFrame({"group": [f"g{i % 10}" for i in range(200)]})
        cfg = SplitConfig(
            strategy="group",
            group_column="group",
            validation_size=0.3,
            holdout_size=0.2,
            seed=42,
        )
        mask = split_mask(len(df), cfg, df=df)
        partitions = set(mask.to_list())
        # With 10 groups and 50% non-train, we should get all three partitions
        assert PARTITION_TRAIN in partitions
        # Groups should stay intact
        labeled = df.with_columns(mask)
        for gv in df["group"].unique().to_list():
            group_parts = labeled.filter(pl.col("group") == gv)["_partition"].to_list()
            assert len(set(group_parts)) == 1

    def test_group_mask_missing_column_raises(self):
        df = pl.DataFrame({"x": [1, 2, 3]})
        cfg = SplitConfig(strategy="group", group_column="group", validation_size=0.2, seed=42)
        with pytest.raises(ValueError, match="not found"):
            split_mask(len(df), cfg, df=df)

    def test_group_mask_no_validation_assigned_fallback(self):
        """When no groups hash to validation, fallback forces a train group to validation."""
        # Use 2 groups with a seed that hashes both to train (requires holdout_size=0
        # and a seed where both hash above the validation_size threshold).
        # We use a large holdout_size to make both groups fall into holdout,
        # then check the fallback forces one into validation.
        df = pl.DataFrame({"group": ["X", "X", "Y", "Y"]})
        cfg = SplitConfig(
            strategy="group",
            group_column="group",
            validation_size=0.01,  # tiny validation fraction — likely 0 groups hash to it
            holdout_size=0.0,
            seed=12345,
        )
        mask = split_mask(len(df), cfg, df=df)
        # Should not crash; should produce some partition
        assert len(mask) == 4

    def test_group_mask_missing_df_raises(self):
        cfg = SplitConfig(strategy="group", group_column="group", validation_size=0.2, seed=42)
        with pytest.raises(ValueError, match="requires df"):
            split_mask(10, cfg, df=None)

    # --- Random mask with holdout ---

    def test_random_mask_holdout_only(self):
        """Random mask with holdout_size > 0 and validation_size = 0."""
        n = 1000
        cfg = SplitConfig(validation_size=0.0, holdout_size=0.2, seed=42)
        mask = split_mask(n, cfg)
        ho_n = int((mask == PARTITION_HOLDOUT).sum())
        assert ho_n == 200
        # No validation
        assert int((mask == PARTITION_VALIDATION).sum()) == 0

    # --- Unknown strategy ---

    def test_split_mask_unknown_strategy_raises(self):
        """split_mask should raise on unknown strategy."""
        cfg = SplitConfig.__new__(SplitConfig)
        cfg.strategy = "invalid"
        cfg.validation_size = 0.2
        cfg.holdout_size = 0.0
        cfg.seed = 42
        cfg.date_column = None
        cfg.cutoff_date = None
        cfg.group_column = None
        with pytest.raises(ValueError, match="Unknown split strategy"):
            split_mask(100, cfg)

    def test_split_data_unknown_strategy_raises(self):
        """split_data should raise on unknown strategy."""
        cfg = SplitConfig.__new__(SplitConfig)
        cfg.strategy = "invalid"
        cfg.validation_size = 0.2
        cfg.holdout_size = 0.0
        cfg.seed = 42
        cfg.date_column = None
        cfg.cutoff_date = None
        cfg.group_column = None
        df = pl.DataFrame({"x": [1, 2, 3]})
        with pytest.raises(ValueError, match="Unknown split strategy"):
            split_data(df, cfg)


class TestSplitConfigEdgeCases:
    def test_holdout_size_negative_raises(self):
        with pytest.raises(ValueError, match="holdout_size"):
            SplitConfig(holdout_size=-0.1)

    def test_holdout_size_one_raises(self):
        with pytest.raises(ValueError, match="holdout_size"):
            SplitConfig(holdout_size=1.0)

    def test_validation_plus_holdout_ge_one_raises(self):
        with pytest.raises(ValueError, match="must be less than 1"):
            SplitConfig(validation_size=0.6, holdout_size=0.5)

    def test_validation_plus_holdout_exactly_one_raises(self):
        with pytest.raises(ValueError, match="must be less than 1"):
            SplitConfig(validation_size=0.5, holdout_size=0.5)

    def test_validation_size_negative_raises(self):
        with pytest.raises(ValueError, match="validation_size"):
            SplitConfig(validation_size=-0.5)

    def test_valid_temporal_config(self):
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-01-01",
            holdout_size=0.1,
        )
        assert cfg.strategy == "temporal"

    def test_valid_group_config(self):
        cfg = SplitConfig(
            strategy="group",
            group_column="grp",
            holdout_size=0.05,
        )
        assert cfg.strategy == "group"


# ---------------------------------------------------------------------------
# _assign_group_split — hash-based group assignment
# ---------------------------------------------------------------------------


class TestAssignGroupSplit:
    def test_deterministic(self):
        """Same inputs always produce the same test groups."""
        groups = ["alpha", "beta", "gamma", "delta"]
        r1 = _assign_group_split(groups, 0.3, seed=42)
        r2 = _assign_group_split(groups, 0.3, seed=42)
        assert r1 == r2

    def test_different_seed_different_assignment(self):
        groups = ["alpha", "beta", "gamma", "delta"]
        r1 = _assign_group_split(groups, 0.3, seed=42)
        r2 = _assign_group_split(groups, 0.3, seed=99)
        # With 4 groups, very likely to differ (not guaranteed but astronomically unlikely to match)
        assert r1 != r2

    def test_fallback_forces_first_sorted_group(self):
        """When all groups hash to train, fallback forces first sorted group to test.

        With seed=0 and groups=['A', 'B'], validation_size=0.3, both A (frac=0.93)
        and B (frac=0.60) hash above the threshold, so no groups are
        initially assigned to test.  The fallback should force 'A' (first
        sorted) into the test set.
        """
        result = _assign_group_split(["A", "B"], validation_size=0.3, seed=0)
        assert result == {"A"}, f"Expected fallback to force 'A', got {result}"

    def test_single_group_no_fallback(self):
        """A single group should NOT trigger the fallback (needs >1 group)."""
        result = _assign_group_split(["only"], validation_size=0.3, seed=0)
        # With 1 group, the condition `len(unique_groups) > 1` is False
        assert result == set()

    def test_returns_set_of_strings(self):
        """Even if input groups are integers, result should be string set."""
        result = _assign_group_split([1, 2, 3, 4, 5], validation_size=0.5, seed=42)
        assert all(isinstance(g, str) for g in result)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def test_unknown_metric_raises(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            compute_metrics(np.array([1]), np.array([1]), None, ["nonexistent"])

    @pytest.mark.parametrize(
        "metric, y_true, y_pred, sklearn_fn, sklearn_kwargs",
        [
            pytest.param(
                "rmse",
                [1.0, 2.0, 3.0],
                [1.0, 2.0, 3.0],
                "root_mean_squared_error",
                {},
                id="rmse_perfect",
            ),
            pytest.param(
                "rmse",
                [1.0, 2.0, 3.0, 4.0, 5.0],
                [1.1, 2.3, 2.8, 4.2, 5.5],
                "root_mean_squared_error",
                {},
                id="rmse_known",
            ),
            pytest.param(
                "mae",
                [1.0, 2.0, 3.0],
                [1.5, 2.5, 3.5],
                "mean_absolute_error",
                {},
                id="mae_known",
            ),
            pytest.param(
                "mse",
                [1.0, 2.0, 3.0],
                [2.0, 2.0, 2.0],
                "mean_squared_error",
                {},
                id="mse_known",
            ),
            pytest.param(
                "r2",
                [1.0, 2.0, 3.0, 4.0],
                [1.0, 2.0, 3.0, 4.0],
                "r2_score",
                {},
                id="r2_perfect",
            ),
        ],
    )
    def test_core_metric_against_sklearn(self, metric, y_true, y_pred, sklearn_fn, sklearn_kwargs):
        """Validate metric computation against sklearn as independent oracle."""
        from sklearn import metrics as sk_metrics

        yt = np.array(y_true)
        yp = np.array(y_pred)
        result = compute_metrics(yt, yp, None, [metric])
        expected = getattr(sk_metrics, sklearn_fn)(yt, yp, **sklearn_kwargs)
        assert result[metric] == pytest.approx(expected, rel=1e-6)

    def test_gini_perfect_ranking(self):
        """Perfect ranking gives Gini = 1.0."""
        y_true = np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0])
        y_pred = y_true.copy()  # perfect prediction
        result = compute_metrics(y_true, y_pred, None, ["gini"])
        assert result["gini"] == pytest.approx(1.0, abs=0.01)

    def test_gini_inverted_model(self):
        """Inverted predictions should produce negative Gini."""
        y_true = np.array([0, 0, 0, 1, 1, 1])
        y_pred = np.array([0.9, 0.8, 0.7, 0.1, 0.2, 0.3])  # inverted ranking
        result = compute_metrics(y_true, y_pred, None, ["gini"])
        assert result["gini"] < -0.8, "Inverted model should have strongly negative Gini"

    def test_gini_random_seeded(self):
        """Random predictions with seed 42 give a specific known Gini value."""
        rng = np.random.RandomState(42)
        y_true = rng.choice([0, 1], size=1000)
        y_pred = rng.random(1000)
        result = compute_metrics(y_true, y_pred, None, ["gini"])
        assert result["gini"] == pytest.approx(0.0339417125, abs=1e-6)

    def test_metrics_are_finite(self):
        """All returned metrics must be finite numbers."""
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([1.5, 2.5, 2.8, 4.2, 5.1])
        result = compute_metrics(y_true, y_pred, None, ["rmse", "mse", "mae", "r2"])
        for name, value in result.items():
            assert np.isfinite(value), f"{name} is not finite: {value}"
        assert result["rmse"] >= 0
        assert result["mse"] >= 0
        assert result["mae"] >= 0

    def test_weighted_rmse(self):
        from sklearn.metrics import root_mean_squared_error

        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.0, 2.0, 4.0])
        weights = np.array([1.0, 1.0, 2.0])
        result = compute_metrics(y_true, y_pred, weights, ["rmse"])
        expected = root_mean_squared_error(y_true, y_pred, sample_weight=weights)
        assert result["rmse"] == pytest.approx(expected, rel=1e-6)

    def test_multiple_metrics(self):
        y = np.array([1.0, 2.0, 3.0])
        result = compute_metrics(y, y, None, ["rmse", "mae", "r2"])
        assert set(result.keys()) == {"rmse", "mae", "r2"}


# ---------------------------------------------------------------------------
# CatBoostAlgorithm
# ---------------------------------------------------------------------------


class TestCatBoostAlgorithm:
    @pytest.fixture()
    def train_data(self) -> pl.DataFrame:
        rng = np.random.RandomState(42)
        n = 200
        return pl.DataFrame(
            {
                "x1": rng.randn(n),
                "x2": rng.randn(n),
                "cat1": rng.choice(["a", "b", "c"], n),
                "target": rng.randn(n),
                "weight": np.ones(n),
            }
        )

    def test_algorithm_registry(self):
        assert "catboost" in ALGORITHM_REGISTRY
        assert ALGORITHM_REGISTRY["catboost"] is CatBoostAlgorithm

    def test_fit_predict(self, train_data):
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            train_data,
            features=["x1", "x2", "cat1"],
            cat_features=["cat1"],
            target="target",
            weight="weight",
            params={"iterations": 10, "depth": 3},
            task="regression",
        )
        assert isinstance(fit_result, FitResult)
        preds = algo.predict(fit_result.model, train_data, ["x1", "x2", "cat1"])
        assert len(preds) == len(train_data)
        assert isinstance(preds, np.ndarray)

    def test_feature_importance(self, train_data):
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            train_data,
            features=["x1", "x2", "cat1"],
            cat_features=["cat1"],
            target="target",
            weight=None,
            params={"iterations": 10, "depth": 3},
            task="regression",
        )
        importance = algo.feature_importance(fit_result.model)
        assert len(importance) == 3
        assert all("feature" in fi and "importance" in fi for fi in importance)

    def test_save_load(self, train_data, tmp_path):
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            train_data,
            features=["x1", "x2"],
            cat_features=[],
            target="target",
            weight=None,
            params={"iterations": 5},
            task="regression",
        )
        model = fit_result.model
        model_path = tmp_path / "test.cbm"
        algo.save(model, model_path)
        assert model_path.exists()

        # Load and predict
        from catboost import CatBoostRegressor

        loaded = CatBoostRegressor()
        loaded.load_model(str(model_path))
        x_data = train_data.select(["x1", "x2"]).to_pandas()
        preds_orig = model.predict(x_data)
        preds_loaded = loaded.predict(x_data)
        np.testing.assert_array_almost_equal(preds_orig, preds_loaded)

    def test_classification(self, train_data):
        # Add binary target
        df = train_data.with_columns(
            (pl.col("target") > 0).cast(pl.Int32).alias("binary_target"),
        )
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            df,
            features=["x1", "x2"],
            cat_features=[],
            target="binary_target",
            weight=None,
            params={"iterations": 10},
            task="classification",
        )
        preds = algo.predict(fit_result.model, df, ["x1", "x2"])
        assert len(preds) == len(df)

    def test_early_stopping_with_eval_df(self, train_data):
        """When eval_df is provided with early stopping, best_iteration is set."""
        algo = CatBoostAlgorithm()
        # Split data to get an eval set
        train = train_data[:150]
        eval_data = train_data[150:]
        fit_result = algo.fit(
            train,
            features=["x1", "x2", "cat1"],
            cat_features=["cat1"],
            target="target",
            weight=None,
            params={"iterations": 10000, "depth": 3, "early_stopping_rounds": 5},
            task="regression",
            eval_df=eval_data,
        )
        assert isinstance(fit_result, FitResult)
        # Should stop early — best_iteration should be much less than 10000
        assert fit_result.best_iteration is not None
        assert fit_result.best_iteration < 10000

    def test_loss_history_collected(self, train_data):
        """Loss history is collected even without eval_df."""
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            train_data,
            features=["x1", "x2"],
            cat_features=[],
            target="target",
            weight=None,
            params={"iterations": 10},
            task="regression",
        )
        assert len(fit_result.loss_history) == 10
        assert "iteration" in fit_result.loss_history[0]
        assert any(k.startswith("train_") for k in fit_result.loss_history[0])

    def test_eval_df_loss_history_has_eval_keys(self, train_data):
        """With eval_df, loss history includes eval_ prefixed keys."""
        algo = CatBoostAlgorithm()
        train = train_data[:150]
        eval_data = train_data[150:]
        fit_result = algo.fit(
            train,
            features=["x1", "x2"],
            cat_features=[],
            target="target",
            weight=None,
            params={"iterations": 10, "depth": 3},
            task="regression",
            eval_df=eval_data,
        )
        assert len(fit_result.loss_history) > 0
        first = fit_result.loss_history[0]
        assert any(k.startswith("eval_") for k in first)


# ---------------------------------------------------------------------------
# TrainingJob
# ---------------------------------------------------------------------------


class TestTrainingJob:
    @pytest.fixture()
    def synth_data(self) -> pl.DataFrame:
        rng = np.random.RandomState(42)
        n = 100
        x1 = rng.randn(n)
        x2 = rng.randn(n)
        return pl.DataFrame(
            {
                "IDpol": list(range(n)),
                "x1": x1,
                "x2": x2,
                "Exposure": np.ones(n),
                "ClaimCount": (x1 + x2 + rng.randn(n) * 0.5).clip(0),
            }
        )

    def test_basic_training(self, synth_data, tmp_path):
        job = TrainingJob(
            name="test_model",
            data=synth_data,
            target="ClaimCount",
            weight="Exposure",
            exclude=["IDpol"],
            params={"iterations": 10, "depth": 3},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert isinstance(result, TrainResult)
        assert "gini" in result.metrics
        assert "rmse" in result.metrics
        assert result.train_rows + result.test_rows == len(synth_data)
        assert len(result.features) == 2  # x1, x2
        assert (tmp_path / "test_model.cbm").exists()

    def test_feature_derivation(self, synth_data, tmp_path):
        """Features = all columns - target - weight - exclude."""
        job = TrainingJob(
            name="feat_test",
            data=synth_data,
            target="ClaimCount",
            weight="Exposure",
            exclude=["IDpol"],
            params={"iterations": 5},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert set(result.features) == {"x1", "x2"}

    def test_missing_target_raises(self, synth_data, tmp_path):
        job = TrainingJob(
            name="bad",
            data=synth_data,
            target="nonexistent",
            output_dir=str(tmp_path),
        )
        with pytest.raises(ValueError, match="Target column"):
            job.run()

    def test_empty_dataframe_raises(self, tmp_path):
        df = pl.DataFrame(
            {
                "x": pl.Series([], dtype=pl.Float64),
                "y": pl.Series([], dtype=pl.Float64),
            }
        )
        job = TrainingJob(
            name="empty",
            data=df,
            target="y",
            output_dir=str(tmp_path),
        )
        with pytest.raises(ValueError, match="empty"):
            job.run()

    def test_with_weight_column(self, synth_data, tmp_path):
        job = TrainingJob(
            name="weighted",
            data=synth_data,
            target="ClaimCount",
            weight="Exposure",
            exclude=["IDpol"],
            params={"iterations": 5},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.metrics, "metrics dict should not be empty"
        for k, v in result.metrics.items():
            assert np.isfinite(v), f"metric '{k}' is not finite: {v}"

    def test_classification_task(self, tmp_path):
        rng = np.random.RandomState(42)
        n = 100
        df = pl.DataFrame(
            {
                "x1": rng.randn(n),
                "x2": rng.randn(n),
                "label": rng.choice([0, 1], n),
            }
        )
        job = TrainingJob(
            name="cls",
            data=df,
            target="label",
            task="classification",
            params={"iterations": 10},
            metrics=["auc", "logloss"],
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert "auc" in result.metrics
        assert "logloss" in result.metrics

    def test_progress_callback(self, synth_data, tmp_path):
        messages: list[tuple[str, float]] = []

        def _progress(msg: str, frac: float) -> None:
            messages.append((msg, frac))

        job = TrainingJob(
            name="progress",
            data=synth_data,
            target="ClaimCount",
            exclude=["IDpol", "Exposure"],
            params={"iterations": 5},
            output_dir=str(tmp_path),
        )
        job.run(progress=_progress)
        assert len(messages) > 0
        # Progress fractions should be monotonically non-decreasing and in [0, 1]
        fracs = [frac for _, frac in messages]
        assert all(0.0 <= f <= 1.0 for f in fracs)
        assert fracs == sorted(fracs)
        assert fracs[-1] == 1.0

    def test_split_config_from_dict(self, synth_data, tmp_path):
        job = TrainingJob(
            name="split_dict",
            data=synth_data,
            target="ClaimCount",
            exclude=["IDpol", "Exposure"],
            params={"iterations": 5},
            split={"strategy": "random", "validation_size": 0.3, "seed": 99},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.test_rows == 30

    def test_unknown_algorithm_raises(self, synth_data, tmp_path):
        job = TrainingJob(
            name="bad_algo",
            data=synth_data,
            target="ClaimCount",
            algorithm="xgboost",
            output_dir=str(tmp_path),
        )
        with pytest.raises(ValueError, match="Unknown algorithm"):
            job.run()

    def test_data_from_lazyframe(self, synth_data, tmp_path):
        lf = synth_data.lazy()
        job = TrainingJob(
            name="lazy",
            data=lf,
            target="ClaimCount",
            exclude=["IDpol", "Exposure"],
            params={"iterations": 5},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.train_rows > 0

    def test_poisson_loss(self, synth_data, tmp_path):
        job = TrainingJob(
            name="poisson",
            data=synth_data,
            target="ClaimCount",
            exclude=["IDpol", "Exposure"],
            params={"iterations": 10},
            loss_function="Poisson",
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.metrics, "Poisson loss should produce metrics"
        for k, v in result.metrics.items():
            assert np.isfinite(v), f"metric '{k}' is not finite: {v}"

    def test_tweedie_loss(self, synth_data, tmp_path):
        job = TrainingJob(
            name="tweedie",
            data=synth_data,
            target="ClaimCount",
            exclude=["IDpol", "Exposure"],
            params={"iterations": 10},
            loss_function="Tweedie",
            variance_power=1.5,
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.metrics, "Tweedie loss should produce metrics"
        for k, v in result.metrics.items():
            assert np.isfinite(v), f"metric '{k}' is not finite: {v}"

    def test_offset_column(self, synth_data, tmp_path):
        # Add a log-exposure offset column
        data = synth_data.with_columns(pl.col("Exposure").log().alias("log_exposure"))
        job = TrainingJob(
            name="offset",
            data=data,
            target="ClaimCount",
            weight="Exposure",
            exclude=["IDpol"],
            offset="log_exposure",
            params={"iterations": 10},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.metrics, "Offset column training should produce metrics"
        for k, v in result.metrics.items():
            assert np.isfinite(v), f"metric '{k}' is not finite: {v}"
        # Offset column should not be in features
        assert "log_exposure" not in result.features

    def test_double_lift_computed(self, synth_data, tmp_path):
        job = TrainingJob(
            name="dlift",
            data=synth_data,
            target="ClaimCount",
            exclude=["IDpol", "Exposure"],
            params={"iterations": 10},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert len(result.double_lift) == 10
        assert all(k in result.double_lift[0] for k in ("decile", "actual", "predicted", "count"))


# ---------------------------------------------------------------------------
# Loss function resolution
# ---------------------------------------------------------------------------


class TestResolveLossFunction:
    def test_none_returns_none(self):
        assert resolve_loss_function(None, "regression") is None
        assert resolve_loss_function("", "regression") is None

    def test_regression_losses(self):
        assert resolve_loss_function("RMSE", "regression") == "RMSE"
        assert resolve_loss_function("MAE", "regression") == "MAE"
        assert resolve_loss_function("Poisson", "regression") == "Poisson"

    def test_tweedie_includes_variance_power(self):
        result = resolve_loss_function("Tweedie", "regression", 1.5)
        assert result == "Tweedie:variance_power=1.5"

    def test_tweedie_default_variance_power(self):
        result = resolve_loss_function("Tweedie", "regression")
        assert result == "Tweedie:variance_power=1.5"

    def test_classification_losses(self):
        assert resolve_loss_function("Logloss", "classification") == "Logloss"
        assert resolve_loss_function("CrossEntropy", "classification") == "CrossEntropy"

    def test_invalid_loss_for_task(self):
        with pytest.raises(ValueError, match="not valid"):
            resolve_loss_function("Poisson", "classification")
        with pytest.raises(ValueError, match="not valid"):
            resolve_loss_function("Logloss", "regression")


# ---------------------------------------------------------------------------
# Double-lift
# ---------------------------------------------------------------------------


class TestDoubleLift:
    def test_basic_double_lift(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        y_pred = y_true * 1.1
        result = compute_double_lift(y_true, y_pred, n_bins=5)
        assert len(result) == 5
        assert result[0]["decile"] == 1
        # Lowest decile predictions should be lowest
        assert result[0]["predicted"] < result[-1]["predicted"]

    def test_empty_arrays(self):
        result = compute_double_lift(np.array([]), np.array([]))
        assert result == []

    def test_weighted_double_lift(self):
        y_true = np.arange(20, dtype=float)
        y_pred = y_true + 1
        w = np.ones(20)
        w[:10] = 2.0
        result = compute_double_lift(y_true, y_pred, w, n_bins=4)
        assert len(result) == 4


# ---------------------------------------------------------------------------
# Deviance metrics
# ---------------------------------------------------------------------------


class TestDevianceMetrics:
    def test_poisson_deviance(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = y_true.copy()
        result = compute_metrics(y_true, y_pred, None, ["poisson_deviance"])
        assert result["poisson_deviance"] == pytest.approx(0.0, abs=1e-8)

    def test_poisson_deviance_nonzero(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([2.0, 2.0, 2.0])
        result = compute_metrics(y_true, y_pred, None, ["poisson_deviance"])
        assert result["poisson_deviance"] > 0

    def test_tweedie_deviance(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0])
        y_pred = y_true.copy()
        result = compute_metrics(y_true, y_pred, None, ["tweedie_deviance"])
        assert result["tweedie_deviance"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Monotonic Constraints
# ---------------------------------------------------------------------------


class TestMonotonicConstraints:
    def test_monotone_constraint_training(self):
        """Training with monotone_constraints should succeed."""
        rng = np.random.RandomState(42)
        n = 200
        x1 = rng.randn(n)
        df = pl.DataFrame(
            {
                "x1": x1,
                "x2": rng.randn(n),
                "y": x1 + rng.randn(n) * 0.1,
            }
        )
        job = TrainingJob(
            name="mono",
            data=df,
            target="y",
            params={"iterations": 20, "depth": 3},
            monotone_constraints={"x1": 1},
            output_dir="/tmp/test_mono",
        )
        result = job.run()
        assert result.metrics, "Monotone constraint training should produce metrics"
        for k, v in result.metrics.items():
            assert np.isfinite(v), f"metric '{k}' is not finite: {v}"

    def test_monotone_constraint_decreasing(self):
        """With monotone_constraints x1=-1, predictions must decrease with x1."""
        rng = np.random.RandomState(42)
        n = 500
        x1 = rng.randn(n)
        df = pl.DataFrame(
            {
                "x1": x1,
                "x2": rng.randn(n),
                "y": -x1 * 2 + rng.randn(n) * 0.1,
            }
        )
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            df,
            features=["x1", "x2"],
            cat_features=[],
            target="y",
            weight=None,
            params={"iterations": 50, "depth": 4},
            task="regression",
            monotone_constraints={"x1": -1},
        )
        test_df = pl.DataFrame({"x1": np.linspace(-3, 3, 20), "x2": np.zeros(20)})
        preds = algo.predict(fit_result.model, test_df, ["x1", "x2"])
        for i in range(1, len(preds)):
            assert preds[i] <= preds[i - 1] + 1e-6

    def test_monotone_constraint_enforced(self):
        """With monotone_constraints x1=+1, predictions must increase with x1."""
        rng = np.random.RandomState(42)
        n = 500
        x1 = rng.randn(n)
        df = pl.DataFrame(
            {
                "x1": x1,
                "x2": rng.randn(n),
                "y": x1 * 2 + rng.randn(n) * 0.1,
            }
        )
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            df,
            features=["x1", "x2"],
            cat_features=[],
            target="y",
            weight=None,
            params={"iterations": 50, "depth": 4},
            task="regression",
            monotone_constraints={"x1": 1},
        )
        # Predict on a grid varying x1 with x2 fixed
        test_df = pl.DataFrame({"x1": np.linspace(-3, 3, 20), "x2": np.zeros(20)})
        preds = algo.predict(fit_result.model, test_df, ["x1", "x2"])
        # Predictions should be non-decreasing
        for i in range(1, len(preds)):
            assert preds[i] >= preds[i - 1] - 1e-6


# ---------------------------------------------------------------------------
# SHAP Values + Feature Analysis
# ---------------------------------------------------------------------------


class TestSHAP:
    @pytest.fixture()
    def trained_model(self):
        rng = np.random.RandomState(42)
        n = 200
        x1 = rng.randn(n)
        x2 = rng.randn(n)
        df = pl.DataFrame(
            {
                "x1": x1,
                "x2": x2,
                "y": x1 * 2 + x2 + rng.randn(n) * 0.1,
            }
        )
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            df,
            features=["x1", "x2"],
            cat_features=[],
            target="y",
            weight=None,
            params={"iterations": 30, "depth": 4},
            task="regression",
        )
        return algo, fit_result.model, df

    def test_shap_summary(self, trained_model):
        algo, model, df = trained_model
        summary = algo.shap_summary(model, df, ["x1", "x2"])
        assert len(summary) == 2
        assert "feature" in summary[0]
        assert "mean_abs_shap" in summary[0]
        # x1 has 2x coefficient so should have higher SHAP
        x1_shap = next(s for s in summary if s["feature"] == "x1")
        x2_shap = next(s for s in summary if s["feature"] == "x2")
        assert x1_shap["mean_abs_shap"] > x2_shap["mean_abs_shap"]

    def test_shap_summary_subsamples(self, trained_model):
        algo, model, df = trained_model
        summary = algo.shap_summary(model, df, ["x1", "x2"], max_rows=50)
        assert len(summary) == 2

    def test_feature_importance_typed(self, trained_model):
        from catboost import Pool

        algo, model, df = trained_model
        x_data = df.select(["x1", "x2"]).to_pandas()
        y = df["y"].to_numpy()
        pool = Pool(data=x_data, label=y)
        loss_imp = algo.feature_importance_typed(model, pool, "LossFunctionChange")
        assert len(loss_imp) == 2
        assert all("feature" in fi and "importance" in fi for fi in loss_imp)

    def test_shap_summary_with_categorical_features(self):
        """SHAP must work when the model was trained with categorical features.

        Regression: shap_summary previously called _build_pool without
        cat_features, so CatBoost tried to cast string columns to float
        and raised a Polars casting error.
        """
        rng = np.random.RandomState(42)
        n = 200
        categories = rng.choice(["comprehensive", "third_party", "fire_theft"], n)
        x_num = rng.randn(n)
        target = np.where(categories == "comprehensive", 2.0, 1.0) + x_num + rng.randn(n) * 0.1
        df = pl.DataFrame(
            {
                "cover_type": categories,
                "x_num": x_num,
                "y": target,
            }
        )
        algo = CatBoostAlgorithm()
        fit_result = algo.fit(
            df,
            features=["cover_type", "x_num"],
            cat_features=["cover_type"],
            target="y",
            weight=None,
            params={"iterations": 30, "depth": 4},
            task="regression",
        )

        summary = algo.shap_summary(
            fit_result.model, df, ["cover_type", "x_num"], cat_features=["cover_type"]
        )
        assert len(summary) == 2
        shap_features = {s["feature"] for s in summary}
        assert shap_features == {"cover_type", "x_num"}
        assert all(s["mean_abs_shap"] >= 0 for s in summary)

    def test_training_job_shap_with_categorical_features(self, tmp_path):
        """End-to-end: TrainingJob produces SHAP values when data has string columns."""
        rng = np.random.RandomState(42)
        n = 200
        df = pl.DataFrame(
            {
                "cat_col": rng.choice(["a", "b", "c"], n),
                "num_col": rng.randn(n),
                "y": rng.randn(n),
            }
        )
        job = TrainingJob(
            name="shap_cat_test",
            data=df,
            target="y",
            params={"iterations": 10},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert len(result.shap_summary) == 2
        shap_features = {s["feature"] for s in result.shap_summary}
        assert shap_features == {"cat_col", "num_col"}

    def test_training_job_includes_shap(self, tmp_path):
        rng = np.random.RandomState(42)
        n = 200
        df = pl.DataFrame(
            {
                "x1": rng.randn(n),
                "x2": rng.randn(n),
                "y": rng.randn(n),
            }
        )
        job = TrainingJob(
            name="shap_test",
            data=df,
            target="y",
            params={"iterations": 10},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert len(result.shap_summary) == 2
        shap_features = {s["feature"] for s in result.shap_summary}
        assert shap_features == {"x1", "x2"}
        assert len(result.feature_importance_loss) == 2


# ---------------------------------------------------------------------------
# Cross-Validation (removed — Phase 2 Package 2C-5)
#
# The GLM CV code path in ``TrainingJob`` has been fully deleted. The
# ``cv_folds`` kwarg, ``cv_results`` field on ``TrainResult``, and the
# ``cross_validate`` algorithm methods are all gone. The regression
# contract is enforced by ``tests/test_training_job_no_glm_cv.py``.
# ---------------------------------------------------------------------------


class TestNoCvResultsField:
    def test_training_job_no_cv_results_attr(self, tmp_path):
        """After the delete, ``TrainResult`` no longer exposes ``cv_results``.

        Also confirms that ``cv_folds`` is not accepted as a kwarg — passing
        it must raise ``TypeError`` because the argument has been removed.
        """
        rng = np.random.RandomState(42)
        n = 100
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="no_cv",
            data=df,
            target="y",
            params={"iterations": 5},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert not hasattr(result, "cv_results")

        with pytest.raises(TypeError, match="cv_folds"):
            TrainingJob(
                name="no_cv",
                data=df,
                target="y",
                params={"iterations": 5},
                cv_folds=3,  # type: ignore[call-arg]
                output_dir=str(tmp_path),
            )
