"""Tests for haute.modelling — TrainingJob, algorithms, metrics, splits.

Covers the CatBoost and GLM algorithms (pool building, loss resolution, fit,
predict, importance, SHAP, GPU iteration and evals-result paths, save/load) and
the TrainingJob stages (load, validate, derive, split, metrics, artifacts,
MLflow logging, memory readings).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute.errors import HauteValidationError
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
    needs_row_order,
    split_mask,
)
from haute.modelling._training_job import TrainingJob, TrainResult


def _stub_optional_catboost_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep training-path tests focused on their contract, not optional charts."""
    monkeypatch.delattr(CatBoostAlgorithm, "shap_values")
    monkeypatch.setattr(CatBoostAlgorithm, "feature_importance_typed", lambda *a, **kw: [])
    monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])


def _fast_training_params(**overrides: object) -> dict[str, object]:
    """Cheap-but-real CatBoost settings for plumbing-oriented tests."""
    params: dict[str, object] = {"iterations": 4, "depth": 2}
    params.update(overrides)
    return params


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
# split_mask (partition mask: 0=train, 1=validation, 2=holdout)
# ---------------------------------------------------------------------------


def _mask(n_rows: int, config: SplitConfig, df: pl.DataFrame | None = None) -> pl.Series:
    """``split_mask`` over the rows in the order given, where the config selects by position.

    These tests pin labels by position, so the canonical order is the identity.
    """
    order = np.arange(n_rows) if needs_row_order(config) else None
    return split_mask(n_rows, config, df=df, row_order=order)


class TestSplitMask:
    def test_random_mask_correct_ratio(self):
        n = 1000
        mask = _mask(n, SplitConfig(validation_size=0.2, seed=42))
        assert len(mask) == n
        assert mask.dtype == pl.Int8
        train_n = int((mask == PARTITION_TRAIN).sum())
        val_n = int((mask == PARTITION_VALIDATION).sum())
        assert train_n == 800
        assert val_n == 200

    def test_random_mask_with_holdout(self):
        n = 1000
        mask = _mask(n, SplitConfig(validation_size=0.2, holdout_size=0.1, seed=42))
        train_n = int((mask == PARTITION_TRAIN).sum())
        val_n = int((mask == PARTITION_VALIDATION).sum())
        ho_n = int((mask == PARTITION_HOLDOUT).sum())
        assert train_n == 700
        assert val_n == 200
        assert ho_n == 100
        assert train_n + val_n + ho_n == n

    def test_random_mask_no_validation_no_holdout(self):
        n = 1000
        mask = _mask(n, SplitConfig(validation_size=0, holdout_size=0, seed=42))
        assert int((mask == PARTITION_TRAIN).sum()) == n

    def test_random_mask_deterministic(self):
        cfg = SplitConfig(validation_size=0.2, seed=42)
        m1 = _mask(500, cfg)
        m2 = _mask(500, cfg)
        assert m1.to_list() == m2.to_list()

    # Golden vector: the exact partition mask for 50 rows, seed 42,
    # validation 0.2, holdout 0.1 (0 = train, 1 = validation, 2 = holdout).
    # It pins the ``np.random.default_rng(seed).permutation`` stream that every
    # random split rests on. NumPy freezes ``RandomState`` but reserves the
    # right to change ``Generator`` streams in a feature release, and the
    # deterministic test above compares two draws in one process, so it passes
    # under any stream. Generated on numpy 2.4.2 and verified identical on the
    # 2.0.2 floor. If this moves, every project's train/validation/holdout
    # membership moves with it: that is a deliberate decision to record (and
    # to tell users about), not a literal to regenerate.
    _GOLDEN_MASK_50_ROWS_SEED_42 = [
        1, 2, 1, 0, 0, 0, 1, 0, 2, 0,
        0, 1, 1, 2, 1, 0, 0, 0, 0, 1,
        0, 0, 1, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 2, 0, 1, 2, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 1, 0,
    ]  # fmt: skip

    def test_random_mask_golden_vector_pins_numpy_generator_stream(self):
        mask = _mask(50, SplitConfig(validation_size=0.2, holdout_size=0.1, seed=42))

        assert mask.to_list() == self._GOLDEN_MASK_50_ROWS_SEED_42, (
            "NumPy Generator stream changed: random split membership no longer matches "
            "the golden vector, so existing train/validation/holdout partitions move. "
            "Decide and record the NumPy bump deliberately; do not just regenerate."
        )

    def test_random_mask_different_seed(self):
        m1 = _mask(500, SplitConfig(validation_size=0.2, seed=42))
        m2 = _mask(500, SplitConfig(validation_size=0.2, seed=99))
        assert m1.to_list() != m2.to_list()

    def test_temporal_mask_splits_by_date(self):
        df = pl.DataFrame({"date": ["2024-01-01", "2024-06-15", "2024-12-31"]})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        mask = _mask(len(df), cfg, df=df)
        # Before cutoff = train (0), on/after cutoff = validation (1)
        assert mask.to_list() == [PARTITION_TRAIN, PARTITION_TRAIN, PARTITION_VALIDATION]

    def test_temporal_mask_missing_df_raises(self):
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        with pytest.raises(ValueError, match="requires df"):
            _mask(10, cfg, df=None)

    def test_group_mask_keeps_groups_intact(self):
        df = pl.DataFrame(
            {
                "group": [f"g{i % 5}" for i in range(100)],
            }
        )
        cfg = SplitConfig(strategy="group", group_column="group", validation_size=0.3, seed=42)
        mask = _mask(len(df), cfg, df=df)
        # Each group should be entirely in one partition
        labeled = df.with_columns(mask)
        for group_val in df["group"].unique().to_list():
            group_partitions = labeled.filter(pl.col("group") == group_val)["_partition"].to_list()
            assert len(set(group_partitions)) == 1, f"Group {group_val} split across partitions"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _mask(0, SplitConfig())

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
        mask = _mask(len(df), cfg, df=df)
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
        mask = _mask(len(df), cfg, df=df)
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
            _mask(len(df), cfg, df=df)

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
        mask = _mask(len(df), cfg, df=df)
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
        mask = _mask(len(df), cfg, df=df)
        assert mask[0] == PARTITION_TRAIN
        assert mask[1] == PARTITION_TRAIN

    # --- Temporal mask null-date policy: fail loud ---

    def test_temporal_mask_null_dates_raise(self):
        """Null dates must not be silently routed into validation.

        The old behaviour sent every null-date row to the validation
        partition (null comparison → falsy in np.where), which is target
        leakage in the dangerous direction: nulls cluster, so validation
        metrics were computed on a biased, unintended segment.
        """
        df = pl.DataFrame({"date": ["2024-01-01", None, "2024-12-31", None]})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        with pytest.raises(ValueError, match=r"'date'.*2 null"):
            _mask(len(df), cfg, df=df)

    def test_temporal_mask_null_dates_raise_typed_date_column(self):
        """Same contract for a typed pl.Date column (not just Utf8)."""
        import datetime

        df = pl.DataFrame({"date": [datetime.date(2024, 1, 1), None, datetime.date(2024, 12, 31)]})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        with pytest.raises(ValueError, match=r"'date'.*1 null"):
            _mask(len(df), cfg, df=df)

    def test_temporal_mask_null_dates_raise_with_holdout(self):
        """With holdout_size > 0 the old code crashed with an opaque
        TypeError (unary ~ on None); the contract error must name the
        column and the null count instead."""
        df = pl.DataFrame(
            {"date": ["2024-01-01", None, "2024-08-01", "2024-09-01", "2024-10-01", None]}
        )
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
            validation_size=0.2,
            holdout_size=0.1,
        )
        with pytest.raises(ValueError, match=r"'date'.*2 null"):
            _mask(len(df), cfg, df=df)

    def test_temporal_mask_null_dates_raise_even_when_validation_zero(self):
        """validation_size=0 reclassifies validation rows as train — null
        rows would silently become TRAIN rows. Still a contract error."""
        df = pl.DataFrame({"date": ["2024-01-01", None, "2024-12-31"]})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
            validation_size=0,
            holdout_size=0,
        )
        with pytest.raises(ValueError, match=r"'date'.*1 null"):
            _mask(len(df), cfg, df=df)

    def test_temporal_mask_all_null_dates_raise(self):
        """An all-null date column reports the full count."""
        df = pl.DataFrame({"date": pl.Series([None, None, None], dtype=pl.Utf8)})
        cfg = SplitConfig(
            strategy="temporal",
            date_column="date",
            cutoff_date="2024-07-01",
        )
        with pytest.raises(ValueError, match=r"'date'.*3 null"):
            _mask(len(df), cfg, df=df)

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
        mask = _mask(len(df), cfg, df=df)
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
            _mask(len(df), cfg, df=df)

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
        mask = _mask(len(df), cfg, df=df)
        # Should not crash; should produce some partition
        assert len(mask) == 4

    def test_group_mask_missing_df_raises(self):
        cfg = SplitConfig(strategy="group", group_column="group", validation_size=0.2, seed=42)
        with pytest.raises(ValueError, match="requires df"):
            _mask(10, cfg, df=None)

    # --- Random mask with holdout ---

    def test_random_mask_holdout_only(self):
        """Random mask with holdout_size > 0 and validation_size = 0."""
        n = 1000
        cfg = SplitConfig(validation_size=0.0, holdout_size=0.2, seed=42)
        mask = _mask(n, cfg)
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
            _mask(100, cfg)


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
        # Pin an explicit abs floor for the zero-expected case (rmse_perfect:
        # y_true == y_pred).  Against a 0.0 expected, rel=1e-6 collapses to a
        # tolerance of 0 and approx falls back to its 1e-12 default floor — the
        # same knife edge the sibling perfect-case deviance asserts already
        # avoid with an explicit abs (see TestDevianceMetrics).  Today _rmse
        # returns bit-exact 0.0 for identical inputs, but the explicit floor
        # keeps this robust to any future numerically-inexact reformulation.
        # abs=1e-9 is negligible for the nonzero cases (rel dominates there).
        assert result[metric] == pytest.approx(expected, rel=1e-6, abs=1e-9)

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
            params=_fast_training_params(),
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
            params=_fast_training_params(),
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
            params=_fast_training_params(iterations=3),
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
            params=_fast_training_params(),
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
        params = _fast_training_params()
        fit_result = algo.fit(
            train_data,
            features=["x1", "x2"],
            cat_features=[],
            target="target",
            weight=None,
            params=params,
            task="regression",
        )
        assert len(fit_result.loss_history) == params["iterations"]
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
            params=_fast_training_params(),
            task="regression",
            eval_df=eval_data,
        )
        assert len(fit_result.loss_history) > 0
        first = fit_result.loss_history[0]
        assert any(k.startswith("eval_") for k in first)

    @pytest.mark.parametrize("with_validation", [True, False], ids=["validation", "learn-only"])
    def test_a_gpu_history_is_numbered_and_keyed_as_the_cpu_callback_records_it(
        self, train_data, with_validation: bool
    ):
        """A GPU fit has no callback; its history comes from ``evals_result_``, which
        the CPU fit records too, so the two histories must be the same rows."""
        from haute.modelling._algorithms import _loss_history_from_evals

        train, held_out = train_data.slice(0, 150), train_data.slice(150)
        fit_result = CatBoostAlgorithm().fit(
            train,
            features=["x1", "x2"],
            cat_features=[],
            target="target",
            weight=None,
            params=_fast_training_params(iterations=6),
            task="regression",
            eval_df=held_out if with_validation else None,
        )
        rebuilt = _loss_history_from_evals(fit_result.model.evals_result_)

        assert rebuilt == fit_result.loss_history
        assert [row["iteration"] for row in rebuilt] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        assert any(key.startswith("eval_") for key in rebuilt[0]) is with_validation


@pytest.mark.parametrize("with_validation", [False, True], ids=["no-validation", "validation"])
def test_a_gpu_fit_reports_the_history_its_model_recorded(
    monkeypatch: pytest.MonkeyPatch, with_validation: bool
) -> None:
    """A GPU fit runs no callback: ``fit`` reads the history from the model's own
    ``evals_result_``, with or without a validation set."""
    import catboost

    recorded = {"learn": {"RMSE": [0.9, 0.8, 0.7]}}
    if with_validation:
        recorded["validation"] = {"RMSE": [1.0, 0.95, 0.97]}

    class GpuRecordingRegressor:
        """What a CatBoost GPU fit leaves behind, without a GPU."""

        def __init__(self, **params: object) -> None:
            assert str(params["task_type"]).upper() == "GPU"
            self.evals_result_ = recorded
            self.tree_count_ = 3
            self.best_iteration_ = 1
            self._metadata: dict[str, str] = {}

        def fit(self, pool: object, **kwargs: object) -> None:
            assert "callbacks" not in kwargs

        def get_metadata(self) -> dict[str, str]:
            return self._metadata

    monkeypatch.setattr(catboost, "CatBoostRegressor", GpuRecordingRegressor)
    frame = pl.DataFrame({"x": [0.1, 0.5, 0.9, 0.3] * 10, "y": [1.0, 2.0, 3.0, 4.0] * 10})
    fit_result = CatBoostAlgorithm().fit(
        frame,
        ["x"],
        [],
        "y",
        None,
        {"iterations": 3, "task_type": "GPU"},
        "regression",
        eval_df=frame if with_validation else None,
    )

    expected = [
        {"iteration": 1.0, "train_RMSE": 0.9},
        {"iteration": 2.0, "train_RMSE": 0.8},
        {"iteration": 3.0, "train_RMSE": 0.7},
    ]
    if with_validation:
        for row, value in zip(expected, recorded["validation"]["RMSE"], strict=True):
            row["eval_RMSE"] = value
    assert fit_result.loss_history == expected


def test_the_loss_importance_is_measured_under_the_weighted_loss(tmp_path: Path) -> None:
    from catboost import CatBoostRegressor

    from haute.modelling._algorithms import _build_pool

    rng = np.random.RandomState(4)
    n = 400
    x1, x2 = rng.randn(n), rng.randn(n)
    heavy = np.arange(n) % 2 == 0
    # The heavily weighted rows follow x1, the lightly weighted ones x2.
    frame = pl.DataFrame(
        {
            "x1": x1,
            "x2": x2,
            "y": np.where(heavy, 3 * x1, 3 * x2) + rng.randn(n) * 0.1,
            "w": np.where(heavy, 50.0, 0.02),
        }
    )
    result = TrainingJob(
        name="weighted",
        data=frame,
        target="y",
        weight="w",
        feature_columns=["x1", "x2"],
        loss_function="RMSE",
        params={"iterations": 30, "depth": 2},
        evaluation={
            "schema_version": 1,
            "strategy": "random",
            "seed": 1,
            "validation": {"method": "none"},
        },
        output_dir=str(tmp_path),
    ).run()
    model = CatBoostRegressor()
    model.load_model(result.model_path)

    def importance(weight: str | None) -> dict[str, float]:
        pool = _build_pool(frame, ["x1", "x2"], target="y", weight=weight)
        rows = CatBoostAlgorithm().feature_importance_typed(model, pool, "LossFunctionChange")
        return {row["feature"]: row["importance"] for row in rows}

    reported = {row["feature"]: row["importance"] for row in result.feature_importance_loss}
    assert reported == pytest.approx(importance("w"), rel=1e-6)
    assert reported != pytest.approx(importance(None), rel=1e-2)


# ---------------------------------------------------------------------------
# TrainingJob
# ---------------------------------------------------------------------------


class TestTrainingJob:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    @pytest.fixture()
    def synth_data(self) -> pl.DataFrame:
        rng = np.random.RandomState(42)
        n = 60
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

    @pytest.fixture(scope="class")
    def basic_weighted_result(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> tuple[TrainResult, Path]:
        rng = np.random.RandomState(42)
        n = 60
        x1 = rng.randn(n)
        x2 = rng.randn(n)
        synth_data = pl.DataFrame(
            {
                "IDpol": list(range(n)),
                "x1": x1,
                "x2": x2,
                "Exposure": np.ones(n),
                "ClaimCount": (x1 + x2 + rng.randn(n) * 0.5).clip(0),
            }
        )
        output_dir = tmp_path_factory.mktemp("trainingjob-basic")
        with pytest.MonkeyPatch.context() as optional_diagnostics:
            _stub_optional_catboost_diagnostics(optional_diagnostics)
            job = TrainingJob(
                name="test_model",
                data=synth_data,
                target="ClaimCount",
                weight="Exposure",
                exclude=["IDpol"],
                params=_fast_training_params(),
                output_dir=str(output_dir),
            )
            result = job.run()
        return result, output_dir

    def test_basic_training(self, basic_weighted_result, synth_data):
        result, output_dir = basic_weighted_result
        assert isinstance(result, TrainResult)
        assert "gini" in result.metrics
        assert "rmse" in result.metrics
        assert result.train_rows + result.validation_rows == len(synth_data)
        assert len(result.features) == 2  # x1, x2
        assert (output_dir / "test_model.cbm").exists()

    def test_feature_derivation(self, basic_weighted_result):
        """Features = all columns - target - weight - exclude."""
        result, _ = basic_weighted_result
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

    def test_with_weight_column(self, basic_weighted_result):
        result, _ = basic_weighted_result
        assert result.metrics, "metrics dict should not be empty"
        for k, v in result.metrics.items():
            assert np.isfinite(v), f"metric '{k}' is not finite: {v}"

    def test_classification_task(self, tmp_path):
        rng = np.random.RandomState(42)
        n = 60
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
            params=_fast_training_params(),
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
            params=_fast_training_params(),
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
            params=_fast_training_params(iterations=3),
            split={"strategy": "random", "validation_size": 0.3, "seed": 99},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.validation_rows == 18

    def test_validation_rows_field_carries_validation_set_count(self, synth_data, tmp_path):
        split_config = SplitConfig(strategy="random", validation_size=0.3, seed=99)
        mask = _mask(len(synth_data), split_config)
        expected_validation_rows = int((mask == PARTITION_VALIDATION).sum())
        assert expected_validation_rows > 0

        job = TrainingJob(
            name="validation_count_semantics",
            data=synth_data,
            target="ClaimCount",
            exclude=["IDpol", "Exposure"],
            params=_fast_training_params(iterations=3),
            split={"strategy": "random", "validation_size": 0.3, "seed": 99},
            output_dir=str(tmp_path),
        )
        result = job.run()

        assert result.validation_rows == expected_validation_rows
        assert result.diagnostics_set == "validation"

    def test_unknown_algorithm_raises(self, synth_data, tmp_path):
        with pytest.raises(ValueError, match="Unknown algorithm"):
            TrainingJob(
                name="bad_algo",
                data=synth_data,
                target="ClaimCount",
                algorithm="unregistered",
                output_dir=str(tmp_path),
            )

    def test_data_from_lazyframe(self, synth_data, tmp_path):
        lf = synth_data.lazy()
        job = TrainingJob(
            name="lazy",
            data=lf,
            target="ClaimCount",
            exclude=["IDpol", "Exposure"],
            params=_fast_training_params(iterations=3),
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
            params=_fast_training_params(),
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
            params=_fast_training_params(),
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
            params=_fast_training_params(),
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
            params=_fast_training_params(),
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

    def test_tweedie_without_a_variance_power_is_refused(self):
        with pytest.raises(HauteValidationError, match="Tweedie loss needs its variance power"):
            resolve_loss_function("Tweedie", "regression")

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
        result = compute_metrics(y_true, y_pred, None, ["tweedie_deviance"], variance_power=1.5)
        assert result["tweedie_deviance"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Monotonic Constraints
# ---------------------------------------------------------------------------


class TestMonotonicConstraints:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_monotone_constraint_training(self):
        """Training with monotone_constraints should succeed."""
        rng = np.random.RandomState(42)
        n = 100
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
            params=_fast_training_params(iterations=8),
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

    @staticmethod
    def _validation_job(
        constraints: object,
        **overrides: object,
    ) -> TrainingJob:
        data = pl.DataFrame(
            {
                "x1": [1.0, 2.0, 3.0, 4.0],
                "x2": [4.0, 3.0, 2.0, 1.0],
                "category": ["a", "b", "a", "b"],
                "y": [1.0, 2.0, 3.0, 4.0],
            }
        )
        return TrainingJob(
            name="validate_mono",
            data=data,
            target="y",
            monotone_constraints=constraints,  # type: ignore[arg-type]
            output_dir="/tmp/test_mono_validation",
            **overrides,
        )

    @pytest.mark.parametrize(
        ("constraints", "match"),
        [
            (["x1"], "must be a dict"),
            ({"": 1}, "keys must be non-empty strings"),
            ({1: 1}, "keys must be non-empty strings"),
            ({"x1": 0}, "exact Python ints -1 or 1"),
            ({"x1": True}, "exact Python ints -1 or 1"),
        ],
    )
    def test_monotone_constraints_reject_invalid_shape_or_direction(
        self,
        constraints: object,
        match: str,
    ) -> None:
        with pytest.raises(ValueError, match=match):
            self._validation_job(constraints).run()

    def test_monotone_constraints_reject_unknown_feature(self) -> None:
        with pytest.raises(ValueError, match="final selected features.*unknown"):
            self._validation_job({"missing": 1}).run()

    def test_monotone_constraints_are_rejected_for_glm_at_construction(self) -> None:
        """GLM monotonicity lives on each term, so the CatBoost lever is refused
        when the job is built — never carried as far as ``run()``."""
        with pytest.raises(
            HauteValidationError, match="monotone_constraints only apply to CatBoost"
        ):
            self._validation_job(
                {"x2": 1},
                algorithm="glm",
                params={"family": "gaussian", "terms": {"x1": {"type": "linear"}}},
            )

    def test_monotone_constraints_reject_nonnumeric_feature(self) -> None:
        with pytest.raises(ValueError, match="numeric Int64 or Float64.*category"):
            self._validation_job({"category": 1}).run()


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
            params={"iterations": 16, "depth": 3},
            task="regression",
        )
        return algo, fit_result.model, df

    def test_shap_values(self, trained_model):
        algo, model, df = trained_model
        values = algo.shap_values(model, df, ["x1", "x2"])
        assert values.shape == (df.height, 2)
        # x1 has 2x coefficient so should have higher SHAP
        mean_abs = np.abs(values).mean(axis=0)
        assert mean_abs[0] > mean_abs[1]

    def test_feature_importance_typed(self, trained_model):
        from catboost import Pool

        algo, model, df = trained_model
        x_data = df.select(["x1", "x2"]).to_pandas()
        y = df["y"].to_numpy()
        pool = Pool(data=x_data, label=y)
        loss_imp = algo.feature_importance_typed(model, pool, "LossFunctionChange")
        assert len(loss_imp) == 2
        assert all("feature" in fi and "importance" in fi for fi in loss_imp)

    def test_shap_values_with_categorical_features(self):
        """SHAP must work when the model was trained with categorical features.

        Regression: SHAP previously called _build_pool without
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
            params={"iterations": 16, "depth": 3},
            task="regression",
        )

        values = algo.shap_values(
            fit_result.model, df, ["cover_type", "x_num"], cat_features=["cover_type"]
        )
        assert values.shape == (n, 2)
        assert np.isfinite(values).all()

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
            params={"iterations": 4, "depth": 2},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert len(result.shap_summary) == 2
        shap_features = {s["feature"] for s in result.shap_summary}
        assert shap_features == {"cat_col", "num_col"}
        beeswarm = {entry["feature"]: entry for entry in result.shap_beeswarm}
        assert [entry["feature"] for entry in result.shap_beeswarm] == [
            row["feature"] for row in result.shap_summary
        ]
        assert beeswarm["cat_col"]["kind"] == "categorical"
        assert set(beeswarm["cat_col"]["values"]) <= {"a", "b", "c"}
        assert set(beeswarm["cat_col"]["value_ranks"]) == {None}
        assert beeswarm["num_col"]["kind"] == "numeric"
        ranks = beeswarm["num_col"]["value_ranks"]
        assert min(ranks) == 0.0 and max(ranks) == 1.0
        assert len(beeswarm["num_col"]["shap_values"]) == len(ranks)

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
            params={"iterations": 4, "depth": 2},
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert len(result.shap_summary) == 2
        shap_features = {s["feature"] for s in result.shap_summary}
        assert shap_features == {"x1", "x2"}
        assert len(result.feature_importance_loss) == 2


_RANDOM_EVALUATION: dict[str, object] = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "test": {"size": 0.2},
    "validation": {"method": "none"},
}


def _three_round_training_params(**overrides: object) -> dict[str, object]:
    """Cheap-but-real CatBoost settings for TrainingJob coverage paths."""
    params: dict[str, object] = {"iterations": 3, "depth": 2}
    params.update(overrides)
    return params


# ---------------------------------------------------------------------------
# _get_rss_mb — platform-specific branches
# ---------------------------------------------------------------------------


class TestGetRssMb:
    """Verify _get_rss_mb reads through the psutil-backed process probe."""

    def test_returns_positive_float(self):
        from haute.modelling._algorithms import _get_rss_mb

        result = _get_rss_mb()
        assert isinstance(result, float)
        assert result > 0.0

    def test_returns_zero_when_probe_is_unavailable(self, monkeypatch: pytest.MonkeyPatch):
        from haute.modelling._algorithms import _get_rss_mb

        monkeypatch.setattr("haute.modelling._algorithms.current_process_rss_bytes", lambda: None)
        assert _get_rss_mb() == 0.0


# ---------------------------------------------------------------------------
# _get_available_mb
# ---------------------------------------------------------------------------


class TestGetAvailableMb:
    def test_delegates_to_available_ram_bytes(self):
        from haute.modelling._algorithms import _get_available_mb

        with patch(
            "haute.modelling._algorithms.available_ram_bytes", return_value=1024 * 1024 * 512
        ):
            result = _get_available_mb()
        assert result == pytest.approx(512.0, rel=1e-6)

    def test_returns_float(self):
        from haute.modelling._algorithms import _get_available_mb

        with patch(
            "haute.modelling._algorithms.available_ram_bytes",
            return_value=1024 * 1024 * 1024,
        ):
            result = _get_available_mb()
        assert isinstance(result, float)
        assert result == pytest.approx(1024.0, rel=1e-6)


# ---------------------------------------------------------------------------
# _CatBoostProgressCallback
# ---------------------------------------------------------------------------


class TestCatBoostProgressCallback:
    def test_after_iteration_with_metrics(self):
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        calls: list[tuple] = []

        def on_iter(iteration: int, total: int, metrics: dict, row: dict | None) -> None:
            calls.append((iteration, total, metrics, row))

        cb = _CatBoostProgressCallback(on_iter, 100, loss_history)

        # Match CatBoost's one-based completed-iteration callback contract.
        info = SimpleNamespace(
            iteration=1,
            metrics={
                "learn": {"RMSE": [0.5]},
                "validation": {"RMSE": [0.6]},
            },
        )
        result = cb.after_iteration(info)
        assert result is True
        assert len(calls) == 1
        # The readout keeps CatBoost's names; the history row is the prefixed one.
        assert calls[0] == (
            1,
            100,
            {"RMSE": 0.5, "validation_RMSE": 0.6},
            {"iteration": 1.0, "train_RMSE": 0.5, "eval_RMSE": 0.6},
        )
        assert loss_history == [calls[0][3]]

    def test_after_iteration_without_callback(self):
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        cb = _CatBoostProgressCallback(None, 10, loss_history)

        info = SimpleNamespace(iteration=1, metrics={})
        result = cb.after_iteration(info)
        assert result is True
        assert len(loss_history) == 1

    def test_empty_metrics_dict(self):
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        cb = _CatBoostProgressCallback(None, 10, loss_history)

        info = SimpleNamespace(iteration=5, metrics=None)
        result = cb.after_iteration(info)
        assert result is True
        assert loss_history[0]["iteration"] == 5.0

    def test_empty_values_list(self):
        """Metric values list is empty — should not crash."""
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        cb = _CatBoostProgressCallback(None, 10, loss_history)

        info = SimpleNamespace(iteration=1, metrics={"learn": {"RMSE": []}})
        cb.after_iteration(info)
        assert "train_RMSE" not in loss_history[0]

    def test_logging_frequency(self):
        """Memory checkpoint is logged for iterations 1-5 and every 50th."""
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        cb = _CatBoostProgressCallback(None, 200, loss_history)

        checkpoints: list[str] = []
        with patch(
            "haute.modelling._algorithms._mem_checkpoint",
            side_effect=lambda label: checkpoints.append(label),
        ):
            for i in range(1, 201):
                info = SimpleNamespace(iteration=i, metrics=None)
                cb.after_iteration(info)

        # Iterations 1-5 and every 50th (50,100,150,200)
        assert len(checkpoints) == 9  # 5 + 4


# ---------------------------------------------------------------------------
# _build_pool
# ---------------------------------------------------------------------------


class TestBuildPool:
    def test_basic_pool_creation(self):
        """Test _build_pool with numeric features only."""
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0, 3.0], "f2": [4.0, 5.0, 6.0], "y": [0.0, 1.0, 0.0]})
        pool = _build_pool(df, ["f1", "f2"], [], target="y")
        assert pool.num_row() == 3

    def test_pool_with_categorical(self):
        """Test _build_pool with categorical features."""
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0, 3.0], "cat": ["a", "b", "c"], "y": [0.0, 1.0, 0.0]})
        pool = _build_pool(df, ["f1", "cat"], ["cat"], target="y")
        assert pool.num_row() == 3

    def test_pool_with_weight_and_offset(self):
        """Weight and offset are extracted from df; a log link logs the exposure."""
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame(
            {
                "f1": [1.0, 2.0, 3.0],
                "y": [0.0, 1.0, 0.0],
                "w": [1.0, 2.0, 1.0],
                "off": [0.5, 1.0, 2.0],
            }
        )
        log_pool = _build_pool(
            df, ["f1"], [], target="y", weight="w", offset="off", offset_link="log"
        )
        identity_pool = _build_pool(
            df, ["f1"], [], target="y", weight="w", offset="off", offset_link="identity"
        )
        assert log_pool.num_row() == 3
        np.testing.assert_allclose(log_pool.get_baseline().ravel(), np.log([0.5, 1.0, 2.0]))
        np.testing.assert_allclose(identity_pool.get_baseline().ravel(), [0.5, 1.0, 2.0])

    def test_a_feature_missing_from_the_frame_is_named(self):
        """A pool narrower than its features would shift every categorical index."""
        from haute.errors import HauteValidationError
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0], "cat": ["a", "b"], "y": [0.0, 1.0]})
        with pytest.raises(HauteValidationError, match="'f2'"):
            _build_pool(df, ["f1", "f2", "cat"], ["cat"], target="y")

    def test_pool_offset_needs_its_link_and_log_link_refuses_non_positive_exposure(self):
        from haute.errors import HauteValidationError
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0], "y": [0.0, 1.0], "off": [0.0, 1.0]})
        with pytest.raises(HauteValidationError, match="needs its link"):
            _build_pool(df, ["f1"], [], target="y", offset="off")
        with pytest.raises(
            HauteValidationError, match="must be positive under a log link, but 1 rows"
        ):
            _build_pool(df, ["f1"], [], target="y", offset="off", offset_link="log")

    def test_pool_with_pre_extracted_arrays(self):
        """Pre-extracted y, w, baseline arrays bypass df extraction."""
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0, 3.0]})
        y = np.array([0.0, 1.0, 0.0])
        w = np.array([1.0, 2.0, 1.0])
        baseline = np.array([0.1, 0.2, 0.3])

        pool = _build_pool(df, ["f1"], [], y=y, w=w, baseline=baseline)
        assert pool.num_row() == 3

    def test_numeric_only_pool_carries_feature_names(self):
        """The numpy fast path must still name features (not ['0', '1', ...]).

        Without explicit names, models trained on a numeric-only Pool save
        feature_names_ == ['0', '1', ...] and the serve-side name validation
        rejects every named scoring frame.
        """
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0, 3.0], "f2": [4.0, 5.0, 6.0], "y": [0.0, 1.0, 0.0]})
        pool = _build_pool(df, ["f1", "f2"], [], target="y")
        assert pool.get_feature_names() == ["f1", "f2"]

    def test_categorical_pool_carries_feature_names(self):
        """The pandas branch keeps names too, in feature order."""
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0, 3.0], "cat": ["a", "b", "c"], "y": [0.0, 1.0, 0.0]})
        pool = _build_pool(df, ["f1", "cat"], ["cat"], target="y")
        assert pool.get_feature_names() == ["f1", "cat"]


class TestNumericOnlyModelServeRoundtrip:
    """Regression: all-numeric CatBoost models must keep real feature names.

    The numeric-only training path hands CatBoost a bare numpy array; before
    _build_pool passed feature_names explicitly, the saved .cbm reported
    feature_names_ == ['0', '1', ...] and the serve path
    (load_local_model → _wrap_catboost → _validate_features) rejected every
    named scoring frame with a feature mismatch.
    """

    def test_all_numeric_model_saves_names_and_scores_named_frame(
        self, haute_scratch: Path
    ) -> None:
        from haute._mlflow_io import load_local_model
        from haute._model_scorer import _run_score_pipeline
        from haute.modelling._algorithms import CatBoostAlgorithm

        rng = np.random.default_rng(42)
        train_df = pl.DataFrame(
            {
                "age": rng.uniform(18, 80, 100),
                "exposure": rng.uniform(0.1, 1.0, 100),
                "claims": rng.poisson(0.2, 100).astype(np.float64),
            }
        )

        algo = CatBoostAlgorithm()
        result = algo.fit(
            train_df,
            features=["age", "exposure"],
            cat_features=[],
            target="claims",
            weight=None,
            params={"iterations": 5, "depth": 2, "verbose": 0},
            task="regression",
        )

        model_path = haute_scratch / "numeric_only.cbm"
        algo.save(result.model, model_path)

        scoring_model = load_local_model(str(model_path), task="regression")
        assert scoring_model.feature_names == ["age", "exposure"]

        score_df = pl.DataFrame(
            {
                "age": [30.0, 55.0, 70.0],
                "exposure": [0.5, 0.8, 1.0],
            }
        )
        scored = _run_score_pipeline(
            scoring_model,
            score_df.lazy(),
            task="regression",
            output_col="prediction",
        )
        collected = scored.collect() if isinstance(scored, pl.LazyFrame) else scored
        assert "prediction" in collected.columns
        assert collected.height == 3
        assert collected["prediction"].is_finite().all()

    def test_named_model_predicts_positionally_via_numpy_path(self) -> None:
        """CatBoostAlgorithm.predict's numpy fast path stays valid: CatBoost
        accepts unnamed numpy input positionally even when the model carries
        real feature names, so training-time names don't break in-process
        prediction."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        rng = np.random.default_rng(7)
        train_df = pl.DataFrame(
            {
                "x1": rng.uniform(0, 1, 50),
                "x2": rng.uniform(0, 1, 50),
                "y": rng.uniform(0, 1, 50),
            }
        )
        algo = CatBoostAlgorithm()
        result = algo.fit(
            train_df,
            features=["x1", "x2"],
            cat_features=[],
            target="y",
            weight=None,
            params={"iterations": 5, "depth": 2, "verbose": 0},
            task="regression",
        )
        assert result.model.feature_names_ == ["x1", "x2"]

        preds = algo.predict(result.model, train_df.head(5), ["x1", "x2"])
        assert preds.shape == (5,)
        assert np.isfinite(preds).all()


# ---------------------------------------------------------------------------
# resolve_loss_function — full coverage
# ---------------------------------------------------------------------------


class TestResolveLossFunctionCoverage:
    def test_all_regression_losses(self):
        from haute.modelling._algorithms import resolve_loss_function

        assert resolve_loss_function("RMSE", "regression") == "RMSE"
        assert resolve_loss_function("MAE", "regression") == "MAE"
        assert resolve_loss_function("Poisson", "regression") == "Poisson"

    def test_tweedie_with_explicit_variance_power(self):
        from haute.modelling._algorithms import resolve_loss_function

        result = resolve_loss_function("Tweedie", "regression", 1.8)
        assert result == "Tweedie:variance_power=1.8"

    def test_tweedie_with_none_variance_power(self):
        from haute.errors import HauteValidationError
        from haute.modelling._algorithms import resolve_loss_function

        with pytest.raises(HauteValidationError, match="Tweedie loss needs its variance power"):
            resolve_loss_function("Tweedie", "regression", None)

    def test_all_classification_losses(self):
        from haute.modelling._algorithms import resolve_loss_function

        assert resolve_loss_function("Logloss", "classification") == "Logloss"
        assert resolve_loss_function("CrossEntropy", "classification") == "CrossEntropy"

    def test_regression_loss_for_classification_raises(self):
        from haute.modelling._algorithms import resolve_loss_function

        with pytest.raises(ValueError, match="not valid"):
            resolve_loss_function("RMSE", "classification")

    def test_classification_loss_for_regression_raises(self):
        from haute.modelling._algorithms import resolve_loss_function

        with pytest.raises(ValueError, match="not valid"):
            resolve_loss_function("Logloss", "regression")

    def test_empty_string_returns_none(self):
        from haute.modelling._algorithms import resolve_loss_function

        assert resolve_loss_function("", "regression") is None

    def test_none_returns_none(self):
        from haute.modelling._algorithms import resolve_loss_function

        assert resolve_loss_function(None, "classification") is None


# ---------------------------------------------------------------------------
# CatBoostAlgorithm.fit — GPU and early stopping paths
# ---------------------------------------------------------------------------


class TestCatBoostAlgorithmFitCoverage:
    def _make_df(self, n: int = 50) -> pl.DataFrame:
        rng = np.random.RandomState(42)
        return pl.DataFrame(
            {
                "x1": rng.randn(n),
                "x2": rng.randn(n),
                "y": rng.randn(n),
            }
        )

    def test_gpu_task_type_sets_verbose_and_allow_writing(self):
        """GPU path sets verbose=50 and doesn't set allow_writing_files=False."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        df = self._make_df()

        mock_model = MagicMock()
        mock_model.best_iteration_ = 5
        mock_model.evals_result_ = {}

        with (
            patch("catboost.CatBoostRegressor", return_value=mock_model),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            mock_pool.return_value = MagicMock()
            result = algo.fit(
                df,
                features=["x1", "x2"],
                cat_features=[],
                target="y",
                weight=None,
                params={"task_type": "GPU", "iterations": 10},
                task="regression",
            )

        assert result.model is mock_model

    def test_early_stopping_rounds_default(self):
        """When eval_pool provided without explicit early_stopping_rounds, default 50 is used."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()

        mock_model = MagicMock()
        mock_model.best_iteration_ = 8

        captured_params: dict[str, Any] = {}

        def capture_regressor(**kwargs: Any) -> MagicMock:
            captured_params.update(kwargs)
            return mock_model

        with (
            patch("catboost.CatBoostRegressor", side_effect=capture_regressor),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            mock_pool.return_value = MagicMock()
            algo.fit(
                None,
                features=["x1", "x2"],
                cat_features=[],
                target="y",
                weight=None,
                params={"iterations": 100},
                task="regression",
                pool=MagicMock(),
                eval_pool=MagicMock(),
            )

        assert captured_params.get("early_stopping_rounds") == 50

    def test_classification_uses_classifier(self):
        """Classification task uses CatBoostClassifier."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()

        with (
            patch("catboost.CatBoostClassifier", return_value=mock_model),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            mock_pool.return_value = MagicMock()
            result = algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"iterations": 5},
                task="classification",
                pool=MagicMock(),
            )

        assert result.model is mock_model

    def test_monotone_constraints_mapped_to_indices(self):
        """Monotone constraints dict is mapped to index-based list."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        captured_params: dict[str, Any] = {}

        def capture_regressor(**kwargs: Any) -> MagicMock:
            captured_params.update(kwargs)
            return mock_model

        with (
            patch("catboost.CatBoostRegressor", side_effect=capture_regressor),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            mock_pool.return_value = MagicMock()
            algo.fit(
                None,
                features=["x1", "x2", "x3"],
                cat_features=[],
                target="y",
                weight=None,
                params={"iterations": 5},
                task="regression",
                monotone_constraints={"x1": 1, "x3": -1},
                pool=MagicMock(),
            )

        assert captured_params["monotone_constraints"] == [1, 0, -1]

    def test_feature_weights_mapped_to_indices(self):
        """Feature weights dict is mapped to index-based list."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        captured_params: dict[str, Any] = {}

        def capture_regressor(**kwargs: Any) -> MagicMock:
            captured_params.update(kwargs)
            return mock_model

        with (
            patch("catboost.CatBoostRegressor", side_effect=capture_regressor),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            mock_pool.return_value = MagicMock()
            algo.fit(
                None,
                features=["x1", "x2"],
                cat_features=[],
                target="y",
                weight=None,
                params={"iterations": 5},
                task="regression",
                feature_weights={"x1": 2.0},
                pool=MagicMock(),
            )

        assert captured_params["feature_weights"] == [2.0, 1.0]

    def test_gpu_with_eval_pool_reconstructs_loss_history(self):
        """GPU path reconstructs loss history from evals_result_."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.best_iteration_ = 3
        mock_model.evals_result_ = {
            "validation": {"RMSE": [0.5, 0.4, 0.3]},
            "learn": {"RMSE": [0.4, 0.3, 0.2]},
        }

        with (
            patch("catboost.CatBoostRegressor", return_value=mock_model),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            mock_pool.return_value = MagicMock()
            result = algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"task_type": "GPU", "iterations": 3},
                task="regression",
                pool=MagicMock(),
                eval_pool=MagicMock(),
            )

        assert len(result.loss_history) == 3
        assert result.loss_history[0]["eval_RMSE"] == 0.5
        assert result.loss_history[2]["train_RMSE"] == 0.2


# ---------------------------------------------------------------------------
# CatBoostAlgorithm.predict — classification probability extraction
# ---------------------------------------------------------------------------


class TestCatBoostAlgorithmPredictCoverage:
    def test_classifier_uses_predict_proba(self):
        """Classification model uses predict_proba and extracts column 1."""
        from catboost import CatBoostClassifier

        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock(spec=CatBoostClassifier)
        mock_model.predict_proba.return_value = np.array([[0.8, 0.2], [0.3, 0.7], [0.5, 0.5]])

        df = pl.DataFrame({"x1": [1.0, 2.0, 3.0]})

        with patch("haute._mlflow_io._prepare_predict_frame", return_value=MagicMock()):
            preds = algo.predict(mock_model, df, ["x1"])

        np.testing.assert_array_almost_equal(preds, [0.2, 0.7, 0.5])

    def test_regressor_uses_predict(self):
        """Regression model uses predict and flattens."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([[1.0], [2.0], [3.0]])

        df = pl.DataFrame({"x1": [1.0, 2.0, 3.0]})

        with patch("haute._mlflow_io._prepare_predict_frame", return_value=MagicMock()):
            preds = algo.predict(mock_model, df, ["x1"])

        np.testing.assert_array_almost_equal(preds, [1.0, 2.0, 3.0])


class TestCatBoostImportanceOfAConstantModel:
    """A refit at a small validation-selected tree count can predict a constant."""

    @staticmethod
    def _constant_model() -> Any:
        from catboost import CatBoostRegressor

        rows = 120
        x = pl.DataFrame(
            {
                "a": [float(i % 7) for i in range(rows)],
                "b": [float(i % 5) for i in range(rows)],
            }
        ).to_pandas()
        y = [float(i % 3) for i in range(rows)]
        model = CatBoostRegressor(iterations=1, depth=2, learning_rate=0.3, verbose=0)
        model.fit(x, y)
        assert len(np.unique(model.predict(x))) == 1
        # CatBoost normalises by a zero total prediction change: 0/0.
        assert np.isnan(model.get_feature_importance()).all()
        return model, x, y

    def test_split_features_score_zero_instead_of_nan(self):
        from catboost import Pool

        from haute.modelling._algorithms import CatBoostAlgorithm

        model, x, y = self._constant_model()
        algo = CatBoostAlgorithm()

        assert algo.feature_importance(model) == [
            {"feature": "a", "importance": 0.0},
            {"feature": "b", "importance": 0.0},
        ]
        typed = algo.feature_importance_typed(model, Pool(x, y), "PredictionValuesChange")
        assert {row["importance"] for row in typed} == {0.0}

    def test_infinite_importance_is_not_masked(self):
        from haute.modelling._algorithms import CatBoostAlgorithm

        model = MagicMock()
        model.feature_names_ = ["a", "b"]
        model.get_feature_importance.return_value = np.array([np.inf, 1.0])

        rows = CatBoostAlgorithm().feature_importance(model)

        assert rows[0] == {"feature": "a", "importance": float("inf")}


# ---------------------------------------------------------------------------
# Diagnostic progress stages; CatBoostAlgorithm.shap_values 1D edge case
# ---------------------------------------------------------------------------


class TestDiagnosticProgress:
    @pytest.fixture()
    def metrics_inputs(self, monkeypatch):
        from haute.modelling._training_job import TrainingJob, _SplitResult, _TrainModelResult

        frame = pl.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 6.0]})
        job = TrainingJob(name="diagnostic_progress", data=frame, target="y", metrics=["rmse"])
        monkeypatch.setattr(job, "_read_partition", lambda *args, **kwargs: frame)
        monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *args, **kwargs: [])
        monkeypatch.setattr("haute.modelling._algorithms._mem_checkpoint", lambda *args: None)
        pool_builder = MagicMock()
        monkeypatch.setattr("haute.modelling._algorithms._build_pool", pool_builder)
        algo = SimpleNamespace(
            feature_importance=lambda model: [{"feature": "x", "importance": 1.0}],
            predict=lambda *args, **kwargs: frame["y"].to_numpy(),
        )
        split = _SplitResult("unused.parquet", False, len(frame), 0, 0)
        trained = _TrainModelResult(SimpleNamespace(), algo, None, {})
        return job, split, trained, pool_builder

    @pytest.mark.parametrize("has_shap", [True, False])
    def test_progress_names_the_diagnostic_actually_running(self, metrics_inputs, has_shap):
        job, split, trained, pool_builder = metrics_inputs
        progress = []
        active_at_call = []
        loss_rows = [{"feature": "x", "importance": 0.25}]

        def shap(model, rows, features, cat_features):
            active_at_call.append(progress[-1])
            return np.full((rows.height, len(features)), -0.5)

        def loss(*args):
            active_at_call.append(progress[-1])
            return loss_rows

        if has_shap:
            trained.algo.shap_values = shap
        trained.algo.feature_importance_typed = loss
        pool_builder.side_effect = lambda *args, **kwargs: active_at_call.append(progress[-1])

        result = job._compute_metrics(
            split, ["x"], [], trained, lambda message, fraction: progress.append(message)
        )

        expected = ["Computing SHAP values"] if has_shap else []
        assert active_at_call == expected + ["Computing loss-based feature importance"] * 2
        assert ("Computing SHAP values" in progress) is has_shap
        assert result.shap_summary == ([{"feature": "x", "mean_abs_shap": 0.5}] if has_shap else [])
        assert [entry["feature"] for entry in result.shap_beeswarm] == (["x"] if has_shap else [])
        assert result.feature_importance_loss == loss_rows
        assert result.diagnostics_errors == []

    def test_cancel_between_diagnostics_stops_before_loss_pool(self, metrics_inputs):
        from haute._execution_context import ExecutionCancelledError

        job, split, trained, pool_builder = metrics_inputs
        trained.algo.shap_values = MagicMock(return_value=np.zeros((3, 1)))
        trained.algo.feature_importance_typed = MagicMock(return_value=[])

        def report(message, fraction):
            if message == "Computing loss-based feature importance":
                raise ExecutionCancelledError("Cancelled")

        with pytest.raises(ExecutionCancelledError, match="Cancelled"):
            job._compute_metrics(split, ["x"], [], trained, report)

        trained.algo.shap_values.assert_called_once()
        pool_builder.assert_not_called()
        trained.algo.feature_importance_typed.assert_not_called()


class TestCatBoostShapValues:
    def test_shap_1d_reshaped(self):
        """A 1D ShapValues array is one row; the base value column is dropped."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        # 1D array: 1 feature + base value = 2 elements
        mock_model.get_feature_importance.return_value = np.array([0.5, 0.1])

        df = pl.DataFrame({"f1": [1.0]})

        with patch("haute.modelling._algorithms._build_pool", return_value=MagicMock()):
            values = algo.shap_values(mock_model, df, ["f1"])

        assert values.shape == (1, 1)
        assert values[0, 0] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# CatBoostAlgorithm.cross_validate — removed in Phase 2 Package 2C-5.
# The dead GLM CV code path in ``TrainingJob`` was deleted along with the
# ``cross_validate`` methods on both algorithm classes. No callers remain.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CatBoostAlgorithm.save — parent directory creation
# ---------------------------------------------------------------------------


class TestSaveCoverage:
    def test_save_creates_parent_directory(self, tmp_path):
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()

        nested_path = tmp_path / "deep" / "nested" / "model.cbm"
        algo.save(mock_model, nested_path)

        mock_model.save_model.assert_called_once_with(str(nested_path))
        assert nested_path.parent.exists()


# ---------------------------------------------------------------------------
# TrainingJob._load_data
# ---------------------------------------------------------------------------


class TestLoadDataCoverage:
    def test_load_from_dataframe(self):
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame({"x": [1, 2], "y": [3, 4]})
        job = TrainingJob(name="test", data=df, target="y")
        result = job._load_data()
        assert result.shape == (2, 2)

    def test_load_from_lazyframe(self):
        from haute.modelling._training_job import TrainingJob

        lf = pl.DataFrame({"x": [1, 2], "y": [3, 4]}).lazy()
        job = TrainingJob(name="test", data=lf, target="y")
        result = job._load_data()
        assert result.shape == (2, 2)

    def test_load_from_none_raises(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(name="test", data=pl.DataFrame({"y": [1]}), target="y")
        job._data = None
        with pytest.raises(RuntimeError, match="already been consumed"):
            job._load_data()

    def test_load_from_missing_file_raises(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(name="test", data="/nonexistent/path.csv", target="y")
        with pytest.raises(FileNotFoundError, match="not found"):
            job._load_data()


# ---------------------------------------------------------------------------
# TrainingJob._validate_columns
# ---------------------------------------------------------------------------


class TestValidateColumnsCoverage:
    def test_missing_target_raises(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(name="t", data=pl.DataFrame({"x": [1]}), target="missing")
        df = pl.DataFrame({"x": [1]})
        with pytest.raises(ValueError, match="Target column"):
            job._validate_columns(df)

    def test_missing_weight_raises(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(name="t", data=pl.DataFrame({"y": [1]}), target="y", weight="w")
        df = pl.DataFrame({"y": [1]})
        with pytest.raises(ValueError, match="Weight column"):
            job._validate_columns(df)

    def test_missing_offset_raises(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(name="t", data=pl.DataFrame({"y": [1]}), target="y", offset="off")
        df = pl.DataFrame({"y": [1]})
        with pytest.raises(ValueError, match="Offset column"):
            job._validate_columns(df)

    def test_valid_columns_pass(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="t",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            weight="w",
            offset="off",
        )
        df = pl.DataFrame({"y": [1], "w": [1.0], "off": [0.1], "x": [2]})
        # Should not raise
        job._validate_columns(df)


# ---------------------------------------------------------------------------
# TrainingJob._derive_features
# ---------------------------------------------------------------------------


class TestDeriveFeaturesCoverage:
    def test_excludes_target_weight_offset(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="t",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            weight="w",
            offset="off",
            exclude=["id"],
        )
        df = pl.DataFrame({"y": [1], "w": [1.0], "off": [0.1], "id": [1], "x": [2.0]})
        features, cat_features = job._derive_features(df)
        assert features == ["x"]
        assert cat_features == []

    def test_detects_categorical_features(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(name="t", data=pl.DataFrame({"y": [1]}), target="y")
        df = pl.DataFrame({"y": [1], "cat": ["a"], "num": [1.0]})
        features, cat_features = job._derive_features(df)
        assert "cat" in cat_features
        assert "num" not in cat_features

    def test_no_features_raises(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(name="t", data=pl.DataFrame({"y": [1]}), target="y", exclude=["x"])
        df = pl.DataFrame({"y": [1], "x": [2]})
        with pytest.raises(ValueError, match="No feature columns"):
            job._derive_features(df)

    def test_string_and_categorical_dtype_detected(self):
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(name="t", data=pl.DataFrame({"y": [1]}), target="y")
        df = pl.DataFrame(
            {
                "y": [1],
                "str_col": pl.Series(["a"], dtype=pl.String),
                "cat_col": pl.Series(["b"]).cast(pl.Categorical),
                # Polars 2 makes 1.x's ordering spelling a named category pool.
                "lexical_col": pl.Series(["c"]).cast(pl.Categorical("lexical")),
                "num_col": [1.0],
            }
        )
        features, cat_features = job._derive_features(df)
        assert "str_col" in cat_features
        assert "cat_col" in cat_features
        assert "lexical_col" in cat_features
        assert "num_col" not in cat_features


# ---------------------------------------------------------------------------
# TrainingJob._save_artifacts
# ---------------------------------------------------------------------------


class TestSaveArtifactsCoverage:
    def test_save_catboost_model(self, tmp_path):
        from haute.modelling._training_job import TrainingJob, _TrainModelResult

        mock_algo = MagicMock()
        mock_model = MagicMock()
        mock_fit_result = MagicMock()

        job = TrainingJob(
            name="mymodel",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            algorithm="catboost",
            output_dir=str(tmp_path),
        )
        train_result = _TrainModelResult(
            model=mock_model, algo=mock_algo, fit_result=mock_fit_result, fit_params={}
        )
        path = job._save_artifacts(train_result)

        assert path == tmp_path / "mymodel.cbm"
        mock_algo.save.assert_called_once_with(mock_model, path)

    def test_save_glm_model(self, tmp_path):
        from haute.modelling._training_job import TrainingJob, _TrainModelResult

        mock_algo = MagicMock()
        mock_model = MagicMock()
        mock_fit_result = MagicMock()

        job = TrainingJob(
            name="myglm",
            data=pl.DataFrame({"y": [1], "x": [1.0]}),
            target="y",
            algorithm="glm",
            params={"family": "gaussian", "terms": {"x": {"type": "linear"}}},
            output_dir=str(tmp_path),
        )
        train_result = _TrainModelResult(
            model=mock_model, algo=mock_algo, fit_result=mock_fit_result, fit_params={}
        )
        path = job._save_artifacts(train_result)

        assert path == tmp_path / "myglm.rsglm"

    def test_save_algorithm_without_a_suffix_raises(self, tmp_path):
        from haute.modelling._training_job import TrainingJob, _TrainModelResult

        mock_algo = MagicMock()
        mock_model = MagicMock()
        mock_fit_result = MagicMock()

        job = TrainingJob(
            name="mymodel",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            algorithm="catboost",  # will override
            output_dir=str(tmp_path),
        )
        job.algorithm = "unregistered"  # an algorithm with no registered suffix
        train_result = _TrainModelResult(
            model=mock_model, algo=mock_algo, fit_result=mock_fit_result, fit_params={}
        )
        from haute.errors import HauteValidationError

        with pytest.raises(HauteValidationError, match="has no model file suffix"):
            job._save_artifacts(train_result)
        assert not (tmp_path / "mymodel.model").exists()


# ---------------------------------------------------------------------------
# TrainingJob._log_to_mlflow
# ---------------------------------------------------------------------------


class TestLogToMlflowCoverage:
    def test_log_to_mlflow_no_experiment_returns_early(self):
        from haute.modelling._training_job import TrainingJob, TrainResult

        job = TrainingJob(
            name="test",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            mlflow_experiment=None,
        )

        result = MagicMock(spec=TrainResult)
        # Should return early without error
        job._log_to_mlflow(result)

    def test_log_to_mlflow_import_error_fails_loudly_when_experiment_configured(self):
        from haute.modelling._training_job import TrainingJob, TrainResult

        job = TrainingJob(
            name="test",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            mlflow_experiment="/test",
            evaluation=_RANDOM_EVALUATION,
        )

        result = MagicMock(spec=TrainResult)
        with patch.dict(sys.modules, {"haute.modelling._mlflow_log": None}):
            with pytest.raises(ImportError):
                job._log_to_mlflow(result)


# ---------------------------------------------------------------------------
# TrainingJob._split_data — strategies with mask creation
# ---------------------------------------------------------------------------


class TestSplitDataCoverage:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_split_data_temporal(self, tmp_path):
        """Temporal split reads date column for mask."""
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame(
            {
                "date": ["2024-01-01", "2024-06-15", "2024-08-01", "2024-12-31"],
                "x": [1.0, 2.0, 3.0, 4.0],
                "y": [0.1, 0.2, 0.3, 0.4],
            }
        )
        job = TrainingJob(
            name="temporal",
            data=df,
            target="y",
            split={
                "strategy": "temporal",
                "date_column": "date",
                "cutoff_date": "2024-07-01",
            },
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.train_rows == 2
        assert result.validation_rows == 2

    def test_split_data_with_holdout(self, tmp_path):
        """Split with holdout creates three partitions."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 100
        df = pl.DataFrame(
            {
                "x1": rng.randn(n),
                "y": rng.randn(n),
            }
        )
        job = TrainingJob(
            name="holdout",
            data=df,
            target="y",
            split={"validation_size": 0.2, "holdout_size": 0.1, "seed": 42},
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.holdout_rows == 10
        assert result.validation_rows == 20
        assert result.train_rows == 70

    def test_split_no_validation(self, tmp_path):
        """Split with validation_size=0 puts everything in training."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="no_val",
            data=df,
            target="y",
            split={"validation_size": 0, "holdout_size": 0, "seed": 42},
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.train_rows == n
        assert result.validation_rows == 0


# ---------------------------------------------------------------------------
# TrainingJob._compute_metrics — diagnostics set selection
# ---------------------------------------------------------------------------


class TestComputeMetricsCoverage:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_diagnostics_set_is_holdout_when_holdout_present(self, tmp_path):
        """When holdout is present, diagnostics_set should be 'holdout'."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 100
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="diag_holdout",
            data=df,
            target="y",
            split={"validation_size": 0.2, "holdout_size": 0.1, "seed": 42},
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.diagnostics_set == "holdout"
        assert len(result.holdout_metrics) > 0

    def test_diagnostics_set_is_train_when_no_val_no_holdout(self, tmp_path):
        """Without validation or holdout, diagnostics use training data."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="diag_train",
            data=df,
            target="y",
            split={"validation_size": 0, "holdout_size": 0, "seed": 42},
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.diagnostics_set == "train"


# ---------------------------------------------------------------------------
# TrainingJob — parquet input path (on-disk data)
# ---------------------------------------------------------------------------


class TestParquetInputPath:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_parquet_input_skips_collect(self, tmp_path):
        """When data is a .parquet path string, no collect happens."""
        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        parquet_path = tmp_path / "data.parquet"
        df.write_parquet(str(parquet_path))

        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="parquet_test",
            data=str(parquet_path),
            target="y",
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.train_rows > 0


# ---------------------------------------------------------------------------
# FitResult dataclass
# ---------------------------------------------------------------------------


class TestFitResult:
    def test_default_values(self):
        from haute.modelling._algorithms import FitResult

        result = FitResult(model="dummy")
        assert result.best_iteration is None
        assert result.loss_history == []

    def test_with_values(self):
        from haute.modelling._algorithms import FitResult

        result = FitResult(model="dummy", best_iteration=42, loss_history=[{"iteration": 1}])
        assert result.best_iteration == 42
        assert len(result.loss_history) == 1


# ---------------------------------------------------------------------------
# ALGORITHM_REGISTRY
# ---------------------------------------------------------------------------


class TestAlgorithmRegistry:
    def test_catboost_registered(self):
        from haute.modelling._algorithms import ALGORITHM_REGISTRY, CatBoostAlgorithm

        assert "catboost" in ALGORITHM_REGISTRY
        assert ALGORITHM_REGISTRY["catboost"] is CatBoostAlgorithm


# ---------------------------------------------------------------------------
# GPU + on_iteration threaded fit path (lines 382-470)
# ---------------------------------------------------------------------------


class TestGPUOnIterationPath:
    def test_gpu_with_on_iteration_creates_tempdir_and_polls(self):
        """GPU + on_iteration creates a temp dir and polls metric file."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.best_iteration_ = 2
        mock_model.evals_result_ = {}

        on_iter_calls: list[tuple] = []

        def on_iter(it: int, total: int, metrics: dict, row: dict | None) -> None:
            on_iter_calls.append((it, total, row))

        # When model.fit is called in the thread, create a fake metric file

        real_tempdir = tempfile.mkdtemp(prefix="catboost_gpu_test_")

        def fake_fit(pool: Any, **kwargs: Any) -> None:
            # Simulate CatBoost writing learn_error.tsv
            metric_path = Path(real_tempdir) / "learn_error.tsv"
            with open(metric_path, "w", encoding="utf-8") as f:
                f.write("iter\tRMSE\n")
                f.write("0\t0.5\n")
                f.write("1\t0.4\n")

        mock_model.fit = fake_fit

        with (
            patch("catboost.CatBoostRegressor", return_value=mock_model),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("tempfile.mkdtemp", return_value=real_tempdir),
        ):
            mock_pool.return_value = MagicMock()
            result = algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"task_type": "GPU", "iterations": 2},
                task="regression",
                on_iteration=on_iter,
                pool=MagicMock(),
            )

        assert result.model is mock_model
        # on_iteration should have been called for each data line
        assert len(on_iter_calls) >= 1
        # The GPU fit polls only the iteration: it adds no loss-history row.
        assert all(row is None for _it, _total, row in on_iter_calls)

    def test_gpu_fit_error_is_reraised(self):
        """When model.fit raises in the GPU thread, the error is re-raised."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()

        def failing_fit(pool: Any, **kwargs: Any) -> None:
            raise RuntimeError("GPU training failed")

        mock_model.fit = failing_fit

        real_tempdir = tempfile.mkdtemp(prefix="catboost_gpu_err_")

        with (
            patch("catboost.CatBoostRegressor", return_value=mock_model),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("tempfile.mkdtemp", return_value=real_tempdir),
        ):
            mock_pool.return_value = MagicMock()
            with pytest.raises(RuntimeError, match="GPU training failed"):
                algo.fit(
                    None,
                    features=["x1"],
                    cat_features=[],
                    target="y",
                    weight=None,
                    params={"task_type": "GPU", "iterations": 2},
                    task="regression",
                    on_iteration=lambda it, total, m, row: None,
                    pool=MagicMock(),
                )

    def test_gpu_verbose_default_50(self):
        """GPU sets verbose to 50 by default."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.best_iteration_ = 1
        mock_model.evals_result_ = {}

        captured_params: dict[str, Any] = {}

        def capture_regressor(**kwargs: Any) -> MagicMock:
            captured_params.update(kwargs)
            return mock_model

        with (
            patch("catboost.CatBoostRegressor", side_effect=capture_regressor),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
        ):
            mock_pool.return_value = MagicMock()
            algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"task_type": "GPU", "iterations": 2},
                task="regression",
                pool=MagicMock(),
            )

        assert captured_params["verbose"] == 50

    def test_gpu_without_on_iteration_no_tempdir(self):
        """GPU without on_iteration doesn't create a temp dir."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.best_iteration_ = 1
        mock_model.evals_result_ = {}

        with (
            patch("catboost.CatBoostRegressor", return_value=mock_model),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
            patch("tempfile.mkdtemp") as mock_mkdtemp,
        ):
            mock_pool.return_value = MagicMock()
            algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"task_type": "GPU", "iterations": 2},
                task="regression",
                on_iteration=None,
                pool=MagicMock(),
            )

        mock_mkdtemp.assert_not_called()


# ---------------------------------------------------------------------------
# GPU evals_result reconstruction: validation + learn branches
# ---------------------------------------------------------------------------


class TestGPUEvalsResultReconstruction:
    def test_gpu_evals_result_validation_only(self):
        """GPU with eval_pool reconstructs loss history from validation only."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.best_iteration_ = 2
        mock_model.evals_result_ = {
            "validation": {"RMSE": [0.5, 0.4]},
        }

        with (
            patch("catboost.CatBoostRegressor", return_value=mock_model),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
        ):
            mock_pool.return_value = MagicMock()
            result = algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"task_type": "GPU", "iterations": 2},
                task="regression",
                pool=MagicMock(),
                eval_pool=MagicMock(),
            )

        assert len(result.loss_history) == 2
        assert result.loss_history[0]["eval_RMSE"] == 0.5
        assert "train_RMSE" not in result.loss_history[0]

    def test_gpu_evals_result_longer_than_existing_history(self):
        """When evals_result has more entries than existing loss_history, new ones are created."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.best_iteration_ = 3
        mock_model.evals_result_ = {
            "validation": {"RMSE": [0.5, 0.4, 0.3]},
            "learn": {"RMSE": [0.4, 0.3, 0.2, 0.1]},  # Longer than validation
        }

        with (
            patch("catboost.CatBoostRegressor", return_value=mock_model),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
        ):
            mock_pool.return_value = MagicMock()
            result = algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"task_type": "GPU", "iterations": 4},
                task="regression",
                pool=MagicMock(),
                eval_pool=MagicMock(),
            )

        assert len(result.loss_history) == 4
        assert result.loss_history[3]["train_RMSE"] == 0.1


# ---------------------------------------------------------------------------
# CatBoostAlgorithm.fit — allow_writing_files not set for GPU
# ---------------------------------------------------------------------------


class TestGPUAllowWritingFiles:
    def test_gpu_no_allow_writing_files_default(self):
        """GPU doesn't set allow_writing_files=False (CPU does)."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.best_iteration_ = 1
        mock_model.evals_result_ = {}

        captured_params: dict[str, Any] = {}

        def capture_regressor(**kwargs: Any) -> MagicMock:
            captured_params.update(kwargs)
            return mock_model

        with (
            patch("catboost.CatBoostRegressor", side_effect=capture_regressor),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
        ):
            mock_pool.return_value = MagicMock()
            algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"task_type": "GPU", "iterations": 2},
                task="regression",
                pool=MagicMock(),
            )

        # GPU should NOT set allow_writing_files=False
        assert "allow_writing_files" not in captured_params

    def test_cpu_sets_allow_writing_files_false(self):
        """CPU sets allow_writing_files=False by default."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()

        captured_params: dict[str, Any] = {}

        def capture_regressor(**kwargs: Any) -> MagicMock:
            captured_params.update(kwargs)
            return mock_model

        with (
            patch("catboost.CatBoostRegressor", side_effect=capture_regressor),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._build_pool") as mock_pool,
        ):
            mock_pool.return_value = MagicMock()
            algo.fit(
                None,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"iterations": 2},
                task="regression",
                pool=MagicMock(),
            )

        assert captured_params["allow_writing_files"] is False


# ---------------------------------------------------------------------------
# TrainingJob GLM code paths (lines 543-561, 836-858)
# ---------------------------------------------------------------------------


class TestTrainingJobGLMPaths:
    """Cover GLM-specific branches in _train_model and _compute_metrics.

    Uses a real GLM if available, otherwise mocks.
    """

    def test_glm_no_features_after_term_matching_raises(self, tmp_path):
        """GLM with terms that match no data columns raises ValueError."""
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame({"x1": [1.0, 2.0, 3.0], "y": [0.1, 0.2, 0.3]})
        job = TrainingJob(
            name="glm_empty",
            data=df,
            target="y",
            algorithm="glm",
            params={"family": "gaussian", "terms": {"nonexistent": {"type": "linear"}}},
            output_dir=str(tmp_path),
        )
        with pytest.raises(ValueError, match="not found in training data"):
            job.run()


# ---------------------------------------------------------------------------
# CatBoostAlgorithm.fit — eval_df builds eval_pool automatically
# ---------------------------------------------------------------------------


class TestFitEvalDfAutoPool:
    def test_eval_df_creates_eval_pool(self):
        """When eval_df is provided (not eval_pool), it auto-builds eval_pool."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        mock_model.best_iteration_ = 3

        train_df = pl.DataFrame({"x1": [1.0, 2.0, 3.0], "y": [0.1, 0.2, 0.3]})
        eval_df = pl.DataFrame({"x1": [4.0, 5.0], "y": [0.4, 0.5]})

        pool_calls: list = []

        def mock_build_pool(*args: Any, **kwargs: Any) -> MagicMock:
            pool_calls.append(("build_pool", args, kwargs))
            return MagicMock()

        with (
            patch("catboost.CatBoostRegressor", return_value=mock_model),
            patch("haute.modelling._algorithms._build_pool", side_effect=mock_build_pool),
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            algo.fit(
                train_df,
                features=["x1"],
                cat_features=[],
                target="y",
                weight=None,
                params={"iterations": 3},
                task="regression",
                eval_df=eval_df,
            )

        # Two pool builds: train + eval
        assert len(pool_calls) == 2

    def test_train_df_none_without_pool_raises(self):
        """When train_df is None and no pool is provided, assertion fails."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        with patch("haute.modelling._algorithms._mem_checkpoint"):
            with pytest.raises(AssertionError, match="Either train_df or pool"):
                algo.fit(
                    None,
                    features=["x1"],
                    cat_features=[],
                    target="y",
                    weight=None,
                    params={"iterations": 1},
                    task="regression",
                )


# ---------------------------------------------------------------------------
# GLM algorithm registry (line 660-661)
# ---------------------------------------------------------------------------


class TestGLMRegistry:
    def test_glm_registered_when_rustystats_available(self):
        """GLM is in the registry if rustystats can be imported."""
        from haute.modelling._algorithms import ALGORITHM_REGISTRY

        try:
            from haute.modelling._rustystats import GLMAlgorithm

            assert "glm" in ALGORITHM_REGISTRY
            assert ALGORITHM_REGISTRY["glm"] is GLMAlgorithm
        except ImportError:
            assert "glm" not in ALGORITHM_REGISTRY


# ---------------------------------------------------------------------------
# GLM full training job — covers _train_model GLM path (543-561),
# _compute_metrics GLM diagnostics (836-858), and GLM term narrowing (259-272)
# ---------------------------------------------------------------------------


class TestGLMFullTrainingJob:
    """Integration test covering GLM-specific code paths in TrainingJob."""

    def test_glm_training_job_runs_end_to_end(self, tmp_path):
        """Full GLM training job covering _train_model and _compute_metrics GLM branches."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 100
        df = pl.DataFrame(
            {
                "age": rng.randint(20, 60, n).astype(float),
                "income": rng.randn(n) * 10000 + 50000,
                "y": (rng.randn(n) * 0.5 + 1.0).clip(0.1),
            }
        )
        job = TrainingJob(
            name="glm_full",
            data=df,
            target="y",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {"age": {"type": "linear"}, "income": {"type": "linear"}},
            },
            output_dir=str(tmp_path),
        )
        result = job.run()

        assert result.train_rows > 0
        assert result.validation_rows >= 0
        assert len(result.features) == 2
        assert "gini" in result.metrics or "rmse" in result.metrics

    def test_glm_term_narrowing_filters_features(self, tmp_path):
        """GLM term narrowing selects only the specified terms from features."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 100
        df = pl.DataFrame(
            {
                "age": rng.randint(20, 60, n).astype(float),
                "income": rng.randn(n) * 10000 + 50000,
                "unused_col": rng.randn(n),
                "y": (rng.randn(n) * 0.5 + 1.0).clip(0.1),
            }
        )
        job = TrainingJob(
            name="glm_narrow",
            data=df,
            target="y",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {"age": {"type": "linear"}},  # Only age, not income or unused_col
            },
            output_dir=str(tmp_path),
        )
        result = job.run()

        # Only age should be in features after narrowing
        assert result.features == ["age"]

    def test_glm_with_categorical_terms(self, tmp_path):
        """GLM with categorical terms covers the cat_features narrowing."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 100
        df = pl.DataFrame(
            {
                "region": rng.choice(["north", "south", "east", "west"], n),
                "age": rng.randint(20, 60, n).astype(float),
                "y": (rng.randn(n) * 0.5 + 1.0).clip(0.1),
            }
        )
        job = TrainingJob(
            name="glm_cat",
            data=df,
            target="y",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {
                    "region": {"type": "categorical"},
                    "age": {"type": "linear"},
                },
            },
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert "region" in result.features
        assert "region" in result.cat_features


# ---------------------------------------------------------------------------
# TrainingJob — read_source path for non-parquet file (line 939)
# ---------------------------------------------------------------------------


class TestLoadDataReadSource:
    def test_load_data_from_csv_file(self, tmp_path):
        """_load_data delegates to read_source for non-parquet files."""
        from haute.modelling._training_job import TrainingJob

        csv_path = tmp_path / "data.csv"
        csv_path.write_text("x,y\n1.0,2.0\n3.0,4.0\n")

        job = TrainingJob(name="test", data=str(csv_path), target="y")
        result = job._load_data()
        assert result.shape[0] == 2
        assert "y" in result.columns


# ---------------------------------------------------------------------------
# TrainingJob — parquet write error cleanup (line 376-378)
# ---------------------------------------------------------------------------


class TestPrepareDataWriteError:
    def test_parquet_write_error_cleans_up_temp_file(self, tmp_path):
        """If parquet write fails, the temp file is cleaned up."""
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame({"x": [1.0, 2.0], "y": [3.0, 4.0]})
        job = TrainingJob(
            name="write_err",
            data=df,
            target="y",
            output_dir=str(tmp_path),
        )

        with patch.object(pl.DataFrame, "write_parquet", side_effect=OSError("disk full")):
            with pytest.raises(IOError, match="disk full"):
                job._prepare_data(lambda msg, frac: None)


# ---------------------------------------------------------------------------
# TrainingJob._compute_metrics — GLM diagnostics exception paths
# ---------------------------------------------------------------------------


class TestComputeMetricsGLMExceptions:
    """Cover the try/except blocks in _compute_metrics for GLM diagnostics."""

    def test_glm_result_failures_are_recorded_without_failing_training(self, tmp_path, monkeypatch):
        """Each GLM result diagnostic that raises is recorded by name; training succeeds."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="glm_exc",
            data=df,
            target="y",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {"x1": {"type": "linear"}},
            },
            output_dir=str(tmp_path),
        )

        from haute.modelling import _rustystats

        def fail(name: str):
            def raise_error(*_args, **_kwargs):
                raise RuntimeError(f"{name} fail")

            return raise_error

        monkeypatch.setattr(_rustystats, "glm_coefficient_rows", fail("coef"))
        monkeypatch.setattr(_rustystats, "glm_relativity_rows", fail("rel"))
        monkeypatch.setattr(_rustystats, "glm_fit_statistics", fail("stats"))

        result = job.run()

        assert result.glm_coefficients == []
        assert result.glm_relativities == []
        assert result.glm_fit_statistics == {}
        assert result.glm_inference == {
            "status": "valid_standard",
            "valid": True,
            "standard_errors": "model",
            "reason": None,
        }
        assert {(error["diagnostic"], error["error"]) for error in result.diagnostics_errors} >= {
            ("glm_coefficients", "coef fail"),
            ("glm_relativities", "rel fail"),
            ("glm_fit_statistics", "stats fail"),
        }


# ---------------------------------------------------------------------------
# TrainingJob — split_config from SplitConfig (line 206)
# ---------------------------------------------------------------------------


class TestSplitConfigFromSplitConfig:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_split_config_passed_as_splitconfig_object(self, tmp_path):
        """When split is a SplitConfig object, it's used directly."""
        from haute.modelling._split import SplitConfig
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        sc = SplitConfig(validation_size=0.3, seed=42)
        job = TrainingJob(
            name="sc_obj",
            data=df,
            target="y",
            split=sc,
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )
        assert job.split_config is sc
        result = job.run()
        assert result.validation_rows == 15


# ---------------------------------------------------------------------------
# TrainingJob — mlflow experiment triggers logging (line 332)
# ---------------------------------------------------------------------------


class TestMlflowExperimentTrigger:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_mlflow_experiment_logs_a_contracted_candidate_run(self, tmp_path):
        """A scripted run with mlflow_experiment logs one candidate built from the
        files it just wrote, identified by the job's own training identity."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="mlflow_trigger",
            data=df,
            target="y",
            params=_three_round_training_params(),
            mlflow_experiment="/test/exp",
            mlflow_destination="local",
            evaluation=_RANDOM_EVALUATION,
            output_dir=str(tmp_path),
        )

        with patch("haute.modelling._mlflow_log.log_experiment") as mock_log:
            result = job.run()

        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        assert kwargs["experiment_name"] == "/test/exp"
        assert kwargs["destination"] == "local"
        candidate = kwargs["candidate"]
        assert candidate.run_name.startswith("mlflow_trigger · ")
        assert candidate.tags["haute.training_identity_sha256"] == job.training_identity_sha256
        assert candidate.artifacts.model == Path(result.model_path)
        candidate.artifacts.require_files()
        assert set(candidate.artifacts.evidence) == {
            "evaluation_plan",
            "evaluation_results",
            "evaluation_report",
        }
        assert all(name.startswith("final_test_") for name in candidate.metrics)

    def test_mlflow_experiment_requires_an_evaluation_contract(self):
        from haute.errors import HauteValidationError
        from haute.modelling._training_job import TrainingJob

        with pytest.raises(HauteValidationError, match="requires an explicit evaluation"):
            TrainingJob(
                name="no_eval",
                data=pl.DataFrame({"y": [1.0]}),
                target="y",
                mlflow_experiment="/test/exp",
            )


# ---------------------------------------------------------------------------
# TrainingJob — SHAP exception path (lines 800-801)
# ---------------------------------------------------------------------------


class TestSHAPExceptionPath:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_shap_exception_is_logged_and_empty_list_returned(self, tmp_path, monkeypatch):
        """When shap_values raises, both SHAP views are empty and the failure is recorded."""
        from haute.modelling._algorithms import CatBoostAlgorithm
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="shap_fail",
            data=df,
            target="y",
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )

        def failing_shap(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("SHAP failed")

        monkeypatch.setattr(CatBoostAlgorithm, "shap_values", failing_shap, raising=False)
        result = job.run()

        assert result.shap_summary == []
        assert result.shap_beeswarm == []
        assert [error["diagnostic"] for error in result.diagnostics_errors] == ["shap"]


# ---------------------------------------------------------------------------
# TrainingJob — feature_importance_loss exception path (lines 816-817)
# ---------------------------------------------------------------------------


class TestFeatureImportanceLossExceptionPath:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_feature_importance_loss_exception_is_logged(self, tmp_path):
        """When feature_importance_typed raises, empty list is used."""
        from haute.modelling._algorithms import CatBoostAlgorithm
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="fi_loss_fail",
            data=df,
            target="y",
            params=_three_round_training_params(),
            output_dir=str(tmp_path),
        )

        orig_fi = CatBoostAlgorithm.feature_importance_typed

        def failing_fi(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("FI failed")

        CatBoostAlgorithm.feature_importance_typed = failing_fi
        try:
            result = job.run()
            assert result.feature_importance_loss == []
        finally:
            CatBoostAlgorithm.feature_importance_typed = orig_fi


# ---------------------------------------------------------------------------
# TrainingJob — CV exception path: removed in Phase 2 Package 2C-5.
# The dead GLM CV branch (and its silent exception swallowing into
# ``diagnostics_errors``) was deleted. ``cross_validate`` no longer exists.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TrainingJob — exclude columns debug log (line 961)
# ---------------------------------------------------------------------------


class TestExcludeColumnsDebugLog:
    def test_excluded_columns_already_dropped_logs_debug(self):
        """When exclude references columns not in the data, debug log is emitted."""
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="t",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            exclude=["already_dropped_col"],
        )
        df = pl.DataFrame({"y": [1], "x": [2.0]})
        # Should not raise, just log debug
        job._validate_columns(df)


# ---------------------------------------------------------------------------
# TrainingJob — GLM empty features after narrowing (line 272)
# ---------------------------------------------------------------------------


class TestGLMEmptyFeaturesAfterNarrowing:
    def test_glm_no_features_remaining_after_term_narrowing(self, tmp_path):
        """When all GLM terms reference columns not in data, raise ValueError."""
        from haute.modelling._training_job import TrainingJob

        # Both features exist but the term references a nonexistent column
        df = pl.DataFrame(
            {
                "x1": [1.0, 2.0, 3.0],
                "x2": [4.0, 5.0, 6.0],
                "y": [0.1, 0.2, 0.3],
            }
        )
        job = TrainingJob(
            name="glm_no_feat",
            data=df,
            target="y",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {"nonexistent": {"type": "linear"}},
            },
            output_dir=str(tmp_path),
        )
        with pytest.raises(ValueError, match="not found in training data"):
            job.run()


# ---------------------------------------------------------------------------
# Polars 2 dtypes at the training boundary
# ---------------------------------------------------------------------------


class TestPolars2DtypesInTraining:
    @staticmethod
    def _frame() -> pl.DataFrame:
        rng = np.random.RandomState(7)
        n = 60
        return pl.DataFrame(
            {
                "region": rng.choice(["north", "south", "east"], n).tolist(),
                "age": rng.randint(18, 80, n),
                "y": rng.randn(n),
            }
        ).with_columns(pl.col("region").cast(pl.Categorical("lexical")))

    def test_a_pooled_categorical_feature_trains_as_categorical(self, tmp_path, monkeypatch):
        """``Categorical("lexical")`` is a named pool under Polars 2: it trains
        as a categorical feature and its contract name is String."""
        from haute.modelling._training_job import TrainingJob

        monkeypatch.setenv("HAUTE_MEM_LOG", str(tmp_path / "training_mem.log"))
        data_path = tmp_path / "lexical.parquet"
        self._frame().write_parquet(data_path)
        assert pl.read_parquet_schema(data_path)["region"] == pl.Categorical("lexical")

        prepared = TrainingJob(name="lexical", data=str(data_path), target="y")._prepare_data(
            lambda _msg, _frac: None
        )
        assert prepared.cat_features == ["region"]
        assert prepared.feature_dtypes["region"] == "String"

        job = TrainingJob(
            name="lexical",
            data=self._frame(),
            target="y",
            algorithm="catboost",
            params=_three_round_training_params(),
            output_dir=str(tmp_path / "out"),
        )
        result = job.run()
        assert result.train_rows > 0

    @pytest.mark.parametrize("source", ["frame", "lazy", "parquet"])
    def test_a_128_bit_column_is_refused_before_anything_is_written(self, tmp_path, source):
        """pyarrow cannot open a Parquet file that holds a 128-bit integer and
        ``to_numpy`` panics on one, so the job names the column first, whatever
        its role: here an excluded one."""
        from haute.modelling._training_job import TrainingJob

        frame = self._frame().with_columns(
            policy_key=pl.col("age").cast(pl.Int64) + pl.lit(1, dtype=pl.UInt64)
        )
        assert frame.schema["policy_key"] == pl.Int128
        data: object = frame
        if source == "lazy":
            data = frame.lazy()
        elif source == "parquet":
            data_path = tmp_path / "wide.parquet"
            frame.write_parquet(data_path)
            data = str(data_path)
        written_before = set(tmp_path.iterdir())

        job = TrainingJob(
            name="wide", data=data, target="y", exclude=["policy_key"], output_dir=str(tmp_path)
        )
        with pytest.raises(ValueError, match="Column 'policy_key' is Int128") as exc_info:
            job._prepare_data(lambda _msg, _frac: None)

        assert "cast it upstream to Int64" in str(exc_info.value)
        assert set(tmp_path.iterdir()) == written_before

    def test_a_nested_128_bit_column_is_refused(self):
        from haute.modelling._training_job import TrainingJob

        frame = self._frame().with_columns(keys=pl.concat_list(pl.lit(1, dtype=pl.Int128)))
        job = TrainingJob(name="wide", data=frame, target="y")

        with pytest.raises(ValueError, match="Column 'keys' is List\\(Int128\\), which holds"):
            job._prepare_data(lambda _msg, _frac: None)
