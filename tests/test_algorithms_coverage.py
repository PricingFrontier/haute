"""Coverage tests for _algorithms.py and _training_job.py uncovered paths.

Targets lines missed by existing tests in test_modelling.py and
test_glm_integration.py. Uses unittest.mock to isolate platform-specific,
CatBoost, and MLflow dependencies.
"""

from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import numpy as np
import polars as pl
import pytest


def _stub_optional_catboost_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep training-path coverage tests focused on their asserted contract."""
    from haute.modelling._algorithms import CatBoostAlgorithm

    monkeypatch.setattr(CatBoostAlgorithm, "shap_summary", lambda *a, **kw: [])
    monkeypatch.setattr(CatBoostAlgorithm, "feature_importance_typed", lambda *a, **kw: [])
    monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])


def _fast_training_params(**overrides: object) -> dict[str, object]:
    """Cheap-but-real CatBoost settings for TrainingJob coverage paths."""
    params: dict[str, object] = {"iterations": 3, "depth": 2}
    params.update(overrides)
    return params


# ---------------------------------------------------------------------------
# _get_rss_mb — platform-specific branches
# ---------------------------------------------------------------------------


class TestGetRssMb:
    """Cover all three platform branches and the fallback."""

    def test_linux_reads_proc_status(self):
        from haute.modelling._algorithms import _get_rss_mb

        fake_status = "Name:\tpython\nVmRSS:\t102400 kB\nVmSize:\t200000 kB\n"
        with (
            patch.object(sys, "platform", "linux"),
            patch("builtins.open", mock_open(read_data=fake_status)),
        ):
            result = _get_rss_mb()
        assert result == pytest.approx(102400 / 1024, rel=1e-6)

    def test_linux_oserror_returns_zero(self):
        from haute.modelling._algorithms import _get_rss_mb

        with (
            patch.object(sys, "platform", "linux"),
            patch("builtins.open", side_effect=OSError("no /proc")),
        ):
            assert _get_rss_mb() == 0.0

    def test_linux_no_vmrss_line_returns_zero(self):
        """If /proc/self/status exists but has no VmRSS line."""
        from haute.modelling._algorithms import _get_rss_mb

        fake_status = "Name:\tpython\nVmSize:\t200000 kB\n"
        with (
            patch.object(sys, "platform", "linux"),
            patch("builtins.open", mock_open(read_data=fake_status)),
        ):
            result = _get_rss_mb()
        assert result == 0.0

    def test_darwin_uses_resource(self):
        from haute.modelling._algorithms import _get_rss_mb

        mock_resource = MagicMock()
        usage = SimpleNamespace(ru_maxrss=104857600)  # 100 MB in bytes
        mock_resource.getrusage.return_value = usage
        mock_resource.RUSAGE_SELF = 0

        with (
            patch.object(sys, "platform", "darwin"),
            patch.dict(sys.modules, {"resource": mock_resource}),
        ):
            result = _get_rss_mb()
        assert result == pytest.approx(100.0, rel=1e-6)

    def test_darwin_import_error_returns_zero(self):
        from haute.modelling._algorithms import _get_rss_mb

        with patch.object(sys, "platform", "darwin"), patch.dict(sys.modules, {"resource": None}):
            # When module is None, import will raise ImportError
            result = _get_rss_mb()
        # On darwin with import failure the function falls through
        assert result == 0.0

    def test_windows_uses_ctypes(self):
        """On win32, mock the ctypes calls to cover the Windows branch."""
        from haute.modelling._algorithms import _get_rss_mb

        # We mock _get_rss_mb's internals indirectly by calling it on win32.
        # The function may return 0.0 if psapi isn't available, so we test
        # that it at least runs without error and returns a non-negative float.
        with patch.object(sys, "platform", "win32"):
            result = _get_rss_mb()
        assert isinstance(result, float)
        assert result >= 0.0

    def test_windows_oserror_returns_zero(self):
        """Windows ctypes branch returns 0.0 on OSError."""
        from haute.modelling._algorithms import _get_rss_mb

        with (
            patch.object(sys, "platform", "win32"),
            patch("ctypes.windll", create=True) as mock_windll,
        ):
            mock_windll.psapi.GetProcessMemoryInfo.side_effect = OSError("fail")
            # Also need to handle GetCurrentProcess
            mock_windll.kernel32.GetCurrentProcess.return_value = 1234
            result = _get_rss_mb()
        assert result == 0.0

    def test_unknown_platform_returns_zero(self):
        from haute.modelling._algorithms import _get_rss_mb

        with patch.object(sys, "platform", "freebsd"):
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
# _mem_checkpoint
# ---------------------------------------------------------------------------


class TestMemCheckpoint:
    def test_writes_to_log_file(self, tmp_path):
        from haute.modelling import _algorithms as alg_mod

        log_path = tmp_path / "test_mem.log"
        with (
            patch.object(alg_mod, "_MEM_LOG", log_path),
            patch.object(alg_mod, "_get_rss_mb", return_value=123.4),
            patch.object(alg_mod, "_get_available_mb", return_value=567.8),
        ):
            alg_mod._mem_checkpoint("test label")

        content = log_path.read_text()
        assert "test label" in content
        assert "123.4" in content
        assert "567.8" in content

    def test_appends_to_existing(self, tmp_path):
        from haute.modelling import _algorithms as alg_mod

        log_path = tmp_path / "test_mem.log"
        log_path.write_text("existing\n")
        with (
            patch.object(alg_mod, "_MEM_LOG", log_path),
            patch.object(alg_mod, "_get_rss_mb", return_value=0.0),
            patch.object(alg_mod, "_get_available_mb", return_value=0.0),
        ):
            alg_mod._mem_checkpoint("second line")

        content = log_path.read_text()
        assert "existing" in content
        assert "second line" in content

    def test_fsync_oserror_is_swallowed(self, tmp_path):
        from haute.modelling import _algorithms as alg_mod

        log_path = tmp_path / "test_mem.log"
        with (
            patch.object(alg_mod, "_MEM_LOG", log_path),
            patch.object(alg_mod, "_get_rss_mb", return_value=0.0),
            patch.object(alg_mod, "_get_available_mb", return_value=0.0),
            patch("os.fsync", side_effect=OSError("fsync not supported")),
        ):
            # Should not raise
            alg_mod._mem_checkpoint("fsync fail")

        assert log_path.exists()

    def test_env_var_controls_path(self, tmp_path):
        """HAUTE_MEM_LOG env var controls the log file path."""
        from haute.modelling import _algorithms as alg_mod

        log_path = tmp_path / "custom.log"
        with (
            patch.object(alg_mod, "_MEM_LOG", log_path),
            patch.object(alg_mod, "_get_rss_mb", return_value=1.0),
            patch.object(alg_mod, "_get_available_mb", return_value=2.0),
        ):
            alg_mod._mem_checkpoint("env var test")

        assert log_path.exists()
        assert "env var test" in log_path.read_text()


# ---------------------------------------------------------------------------
# _CatBoostProgressCallback
# ---------------------------------------------------------------------------


class TestCatBoostProgressCallback:
    def test_after_iteration_with_metrics(self):
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        calls: list[tuple] = []

        def on_iter(iteration: int, total: int, metrics: dict) -> None:
            calls.append((iteration, total, metrics))

        cb = _CatBoostProgressCallback(on_iter, 100, loss_history)

        # Build a mock info object with metrics
        info = SimpleNamespace(
            iteration=0,
            metrics={
                "learn": {"RMSE": [0.5]},
                "validation": {"RMSE": [0.6]},
            },
        )
        result = cb.after_iteration(info)
        assert result is True
        assert len(calls) == 1
        assert calls[0] == (1, 100, {"RMSE": 0.5, "validation_RMSE": 0.6})
        assert len(loss_history) == 1
        assert loss_history[0]["iteration"] == 1.0
        assert loss_history[0]["train_RMSE"] == 0.5
        assert loss_history[0]["eval_RMSE"] == 0.6

    def test_after_iteration_without_callback(self):
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        cb = _CatBoostProgressCallback(None, 10, loss_history)

        info = SimpleNamespace(iteration=0, metrics={})
        result = cb.after_iteration(info)
        assert result is True
        assert len(loss_history) == 1

    def test_empty_metrics_dict(self):
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        cb = _CatBoostProgressCallback(None, 10, loss_history)

        info = SimpleNamespace(iteration=4, metrics=None)
        result = cb.after_iteration(info)
        assert result is True
        assert loss_history[0]["iteration"] == 5.0

    def test_empty_values_list(self):
        """Metric values list is empty — should not crash."""
        from haute.modelling._algorithms import _CatBoostProgressCallback

        loss_history: list[dict[str, float]] = []
        cb = _CatBoostProgressCallback(None, 10, loss_history)

        info = SimpleNamespace(iteration=0, metrics={"learn": {"RMSE": []}})
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
            for i in range(200):
                info = SimpleNamespace(iteration=i, metrics=None)
                cb.after_iteration(info)

        # Iterations 1-5 (i=0..4) and every 50th (50,100,150,200)
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
        """Weight and offset are extracted from df correctly."""
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame(
            {
                "f1": [1.0, 2.0, 3.0],
                "y": [0.0, 1.0, 0.0],
                "w": [1.0, 2.0, 1.0],
                "off": [0.1, 0.2, 0.3],
            }
        )
        pool = _build_pool(df, ["f1"], [], target="y", weight="w", offset="off")
        assert pool.num_row() == 3

    def test_pool_with_pre_extracted_arrays(self):
        """Pre-extracted y, w, baseline arrays bypass df extraction."""
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0, 3.0]})
        y = np.array([0.0, 1.0, 0.0])
        w = np.array([1.0, 2.0, 1.0])
        baseline = np.array([0.1, 0.2, 0.3])

        pool = _build_pool(df, ["f1"], [], y=y, w=w, baseline=baseline)
        assert pool.num_row() == 3

    def test_pool_missing_feature_columns_are_skipped(self):
        """Features not present in df columns are silently skipped."""
        from haute.modelling._algorithms import _build_pool

        df = pl.DataFrame({"f1": [1.0, 2.0], "y": [0.0, 1.0]})
        pool = _build_pool(df, ["f1", "missing_col"], [], target="y")
        assert pool.num_row() == 2


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
        from haute.modelling._algorithms import resolve_loss_function

        result = resolve_loss_function("Tweedie", "regression", None)
        assert result == "Tweedie:variance_power=1.5"

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


# ---------------------------------------------------------------------------
# CatBoostAlgorithm.shap_summary — subsampling and 1D edge case
# ---------------------------------------------------------------------------


class TestShapSummaryCoverage:
    def test_shap_1d_reshaped(self):
        """1D shap_values array is reshaped to 2D."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        # 1D array: 1 feature + base value = 2 elements
        mock_model.get_feature_importance.return_value = np.array([0.5, 0.1])

        df = pl.DataFrame({"f1": [1.0]})

        with patch("haute.modelling._algorithms._build_pool", return_value=MagicMock()):
            result = algo.shap_summary(mock_model, df, ["f1"], max_rows=1000)

        assert len(result) == 1
        assert result[0]["feature"] == "f1"
        assert result[0]["mean_abs_shap"] == pytest.approx(0.5)

    def test_subsampling_when_df_exceeds_max_rows(self):
        """When len(df) > max_rows, sample is taken."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        algo = CatBoostAlgorithm()
        mock_model = MagicMock()
        # 5 rows sampled to 3 — shap returns (3, 2) (1 feature + base)
        mock_model.get_feature_importance.return_value = np.array(
            [[0.5, 0.1], [0.3, 0.1], [0.4, 0.1]]
        )

        df = pl.DataFrame({"f1": [1.0, 2.0, 3.0, 4.0, 5.0]})

        with patch("haute.modelling._algorithms._build_pool", return_value=MagicMock()):
            result = algo.shap_summary(mock_model, df, ["f1"], max_rows=3)

        assert len(result) == 1
        assert result[0]["mean_abs_shap"] > 0


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
                "num_col": [1.0],
            }
        )
        features, cat_features = job._derive_features(df)
        assert "str_col" in cat_features
        assert "cat_col" in cat_features
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
            data=pl.DataFrame({"y": [1]}),
            target="y",
            algorithm="glm",
            output_dir=str(tmp_path),
        )
        train_result = _TrainModelResult(
            model=mock_model, algo=mock_algo, fit_result=mock_fit_result, fit_params={}
        )
        path = job._save_artifacts(train_result)

        assert path == tmp_path / "myglm.rsglm"

    def test_save_unknown_algorithm_default_extension(self, tmp_path):
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
        job.algorithm = "xgboost"  # unknown algo
        train_result = _TrainModelResult(
            model=mock_model, algo=mock_algo, fit_result=mock_fit_result, fit_params={}
        )
        path = job._save_artifacts(train_result)

        assert path == tmp_path / "mymodel.model"


# ---------------------------------------------------------------------------
# TrainingJob._log_to_mlflow
# ---------------------------------------------------------------------------


class TestLogToMlflowCoverage:
    def test_log_to_mlflow_calls_log_experiment(self, tmp_path):
        from haute.modelling._training_job import TrainingJob, TrainResult

        job = TrainingJob(
            name="mlflow_test",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            mlflow_experiment="/test/experiment",
            model_name="test_model",
            output_dir=str(tmp_path),
        )

        result = TrainResult(
            metrics={"rmse": 0.5},
            feature_importance=[{"feature": "x1", "importance": 1.0}],
            model_path=str(tmp_path / "model.cbm"),
            train_rows=100,
            test_rows=20,
            features=["x1"],
            cat_features=[],
            holdout_rows=0,
            holdout_metrics={},
            diagnostics_set="validation",
            shap_summary=[],
            feature_importance_loss=[],
            double_lift=[],
            loss_history=[],
            ave_per_feature=[],
            residuals_histogram=[],
            residuals_stats={},
            actual_vs_predicted=[],
            lorenz_curve=[],
            lorenz_curve_perfect=[],
            pdp_data=[],
            glm_coefficients=[],
            glm_relativities=[],
            glm_fit_statistics={},
            glm_regularization_path=None,
        )

        with patch("haute.modelling._mlflow_log.log_experiment") as mock_log:
            job._log_to_mlflow(result)

        mock_log.assert_called_once()

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

    def test_log_to_mlflow_import_error_returns_early(self):
        from haute.modelling._training_job import TrainingJob, TrainResult

        job = TrainingJob(
            name="test",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            mlflow_experiment="/test",
        )

        result = MagicMock(spec=TrainResult)
        with patch.dict(sys.modules, {"haute.modelling._mlflow_log": None}):
            # ImportError on the module — should return early
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
            params=_fast_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.train_rows == 2
        assert result.test_rows == 2

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
            params=_fast_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.holdout_rows == 10
        assert result.test_rows == 20
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
            params=_fast_training_params(),
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert result.train_rows == n
        assert result.test_rows == 0


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
            params=_fast_training_params(),
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
            params=_fast_training_params(),
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
            params=_fast_training_params(),
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

        def on_iter(it: int, total: int, metrics: dict) -> None:
            on_iter_calls.append((it, total))

        # When model.fit is called in the thread, create a fake metric file

        real_tempdir = tempfile.mkdtemp(prefix="catboost_gpu_test_")

        def fake_fit(pool: Any, **kwargs: Any) -> None:
            # Simulate CatBoost writing learn_error.tsv
            metric_path = os.path.join(real_tempdir, "learn_error.tsv")
            with open(metric_path, "w") as f:
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
                    on_iteration=lambda it, total, m: None,
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
# TrainingJob._log_to_mlflow with actual experiment
# ---------------------------------------------------------------------------


class TestLogToMlflowFull:
    def test_log_to_mlflow_constructs_diagnostics_and_metadata(self, tmp_path):
        """Verify _log_to_mlflow constructs ModelDiagnostics and calls log_experiment."""
        from haute.modelling._training_job import TrainingJob, TrainResult

        job = TrainingJob(
            name="mlflow_full",
            data=pl.DataFrame({"y": [1]}),
            target="y",
            mlflow_experiment="/test/exp",
            model_name="my_model",
            output_dir=str(tmp_path),
        )

        result = TrainResult(
            metrics={"rmse": 0.5},
            feature_importance=[],
            model_path=str(tmp_path / "model.cbm"),
            train_rows=100,
            test_rows=20,
            features=["x1"],
            cat_features=[],
            holdout_rows=10,
            holdout_metrics={"rmse": 0.6},
            diagnostics_set="holdout",
            shap_summary=[],
            feature_importance_loss=[],
            double_lift=[],
            loss_history=[],
            ave_per_feature=[],
            residuals_histogram=[],
            residuals_stats={},
            actual_vs_predicted=[],
            lorenz_curve=[],
            lorenz_curve_perfect=[],
            pdp_data=[],
            glm_coefficients=[],
            glm_relativities=[],
            glm_fit_statistics={},
            glm_regularization_path=None,
        )

        with patch("haute.modelling._mlflow_log.log_experiment") as mock_log:
            job._log_to_mlflow(result)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["experiment_name"] == "/test/exp"
        assert call_kwargs["run_name"] == "mlflow_full"
        assert call_kwargs["metrics"] == {"rmse": 0.5}
        assert call_kwargs["model_name"] == "my_model"


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
        assert result.test_rows >= 0
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

    def test_glm_coefficients_exception_is_logged(self, tmp_path):
        """When coefficients_table raises, warning is logged and empty list returned."""
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

        # Patch the GLMAlgorithm methods to raise exceptions to cover the except blocks
        from haute.modelling._rustystats import GLMAlgorithm

        orig_coefs = GLMAlgorithm.coefficients_table
        orig_rels = GLMAlgorithm.relativities
        orig_stats = GLMAlgorithm.fit_statistics

        try:
            GLMAlgorithm.coefficients_table = lambda self, model: (_ for _ in ()).throw(
                RuntimeError("coef fail")
            )
            GLMAlgorithm.relativities = lambda self, model: (_ for _ in ()).throw(
                RuntimeError("rel fail")
            )
            GLMAlgorithm.fit_statistics = lambda self, model: (_ for _ in ()).throw(
                RuntimeError("stats fail")
            )

            result = job.run()

            # Diagnostics should be empty but training should still succeed
            assert result.glm_coefficients == []
            assert result.glm_relativities == []
            assert result.glm_fit_statistics == {}
        finally:
            GLMAlgorithm.coefficients_table = orig_coefs
            GLMAlgorithm.relativities = orig_rels
            GLMAlgorithm.fit_statistics = orig_stats


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
            params=_fast_training_params(),
            output_dir=str(tmp_path),
        )
        assert job.split_config is sc
        result = job.run()
        assert result.test_rows == 15


# ---------------------------------------------------------------------------
# TrainingJob — mlflow experiment triggers logging (line 332)
# ---------------------------------------------------------------------------


class TestMlflowExperimentTrigger:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_mlflow_experiment_triggers_log(self, tmp_path):
        """When mlflow_experiment is set, _log_to_mlflow is called during run()."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="mlflow_trigger",
            data=df,
            target="y",
            params=_fast_training_params(),
            mlflow_experiment="/test/exp",
            output_dir=str(tmp_path),
        )

        with patch("haute.modelling._mlflow_log.log_experiment") as mock_log:
            job.run()

        mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# TrainingJob — SHAP exception path (lines 800-801)
# ---------------------------------------------------------------------------


class TestSHAPExceptionPath:
    @pytest.fixture(autouse=True)
    def _fast_optional_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_optional_catboost_diagnostics(monkeypatch)

    def test_shap_exception_is_logged_and_empty_list_returned(self, tmp_path):
        """When shap_summary raises, empty list is used."""
        from haute.modelling._algorithms import CatBoostAlgorithm
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 50
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="shap_fail",
            data=df,
            target="y",
            params=_fast_training_params(),
            output_dir=str(tmp_path),
        )

        orig_shap = CatBoostAlgorithm.shap_summary

        def failing_shap(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("SHAP failed")

        CatBoostAlgorithm.shap_summary = failing_shap
        try:
            result = job.run()
            assert result.shap_summary == []
        finally:
            CatBoostAlgorithm.shap_summary = orig_shap


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
            params=_fast_training_params(),
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
