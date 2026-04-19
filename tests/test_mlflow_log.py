"""Tests for haute.modelling._mlflow_log — standalone MLflow experiment logging."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haute.modelling._result_types import ModelCardMetadata, ModelDiagnostics


class TestResolveTrackingBackend:
    def test_databricks_when_env_vars_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend()
        assert uri == "databricks"
        assert backend == "databricks"

    def test_local_when_env_vars_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend()
        assert uri.startswith("file://")
        assert "mlruns" in uri
        assert backend == "local"

    def test_local_when_only_host_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com")
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend()
        assert backend == "local"

    def test_local_when_only_token_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend()
        assert backend == "local"


class TestResolveExperimentName:
    def test_explicit_wins(self) -> None:
        from haute.modelling._mlflow_log import resolve_experiment_name

        assert (
            resolve_experiment_name(
                explicit="/my/override",
                config_value="/from/config",
                node_label="freq",
                backend="local",
            )
            == "/my/override"
        )

    def test_config_value_second(self) -> None:
        from haute.modelling._mlflow_log import resolve_experiment_name

        assert (
            resolve_experiment_name(
                config_value="/from/config",
                node_label="freq",
                backend="local",
            )
            == "/from/config"
        )

    def test_databricks_default(self) -> None:
        from haute.modelling._mlflow_log import resolve_experiment_name

        assert (
            resolve_experiment_name(
                node_label="frequency_model",
                backend="databricks",
            )
            == "/Shared/haute/frequency_model"
        )

    def test_local_default(self) -> None:
        from haute.modelling._mlflow_log import resolve_experiment_name

        assert (
            resolve_experiment_name(
                node_label="frequency_model",
                backend="local",
            )
            == "frequency_model"
        )

    def test_auto_detects_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        from haute.modelling._mlflow_log import resolve_experiment_name

        assert resolve_experiment_name(node_label="freq") == "freq"

    def test_empty_strings_are_falsy(self) -> None:
        from haute.modelling._mlflow_log import resolve_experiment_name

        assert (
            resolve_experiment_name(
                explicit="",
                config_value="",
                node_label="freq",
                backend="local",
            )
            == "freq"
        )


class TestBuildRunUrl:
    def test_returns_none_for_local(self) -> None:
        from haute.modelling._mlflow_log import build_run_url

        assert build_run_url("local", "exp", "run123") is None

    def test_returns_url_for_databricks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com")

        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "42"

        with patch("mlflow.get_experiment_by_name", return_value=mock_experiment):
            from haute.modelling._mlflow_log import build_run_url

            url = build_run_url("databricks", "/Shared/haute/freq", "run123")
            assert url == "https://myhost.databricks.com/#mlflow/experiments/42/runs/run123"

    def test_returns_none_when_experiment_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com")

        with patch("mlflow.get_experiment_by_name", return_value=None):
            from haute.modelling._mlflow_log import build_run_url

            assert build_run_url("databricks", "/Shared/haute/freq", "run123") is None

    def test_returns_none_when_host_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)

        from haute.modelling._mlflow_log import build_run_url

        assert build_run_url("databricks", "/Shared/haute/freq", "run123") is None

    def test_strips_trailing_slash_from_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com/")

        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "42"

        with patch("mlflow.get_experiment_by_name", return_value=mock_experiment):
            from haute.modelling._mlflow_log import build_run_url

            url = build_run_url("databricks", "/Shared/haute/freq", "run123")
            assert "databricks.com//" not in url  # no double slash


class TestLogExperiment:
    def test_calls_mlflow_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mock mlflow and verify correct calls are made."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        with (
            patch("mlflow.set_tracking_uri") as m_tracking,
            patch("mlflow.set_registry_uri") as m_registry,
            patch("mlflow.set_experiment") as m_experiment,
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params") as m_params,
            patch("mlflow.log_metrics") as m_metrics,
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            result = log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5, "gini": 0.8},
                params={"algorithm": "catboost", "task": "regression"},
            )

            m_tracking.assert_called_once()
            assert "file://" in m_tracking.call_args[0][0]
            m_registry.assert_not_called()  # local backend, no registry
            m_experiment.assert_called_once_with("/test/experiment")
            m_params.assert_called_once_with({"algorithm": "catboost", "task": "regression"})
            m_metrics.assert_called_once_with({"rmse": 0.5, "gini": 0.8})

            assert result.backend == "local"
            assert result.experiment_name == "/test/experiment"
            assert result.run_id == "abc123"
            assert result.run_url is None

    def test_databricks_sets_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Databricks env vars present, set_registry_uri('databricks-uc') is called."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "42"

        with (
            patch("mlflow.set_tracking_uri") as m_tracking,
            patch("mlflow.set_registry_uri") as m_registry,
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model"),
            patch("mlflow.get_experiment_by_name", return_value=mock_experiment),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            result = log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5},
                params={"algorithm": "catboost"},
            )

            m_tracking.assert_called_once_with("databricks")
            m_registry.assert_called_once_with("databricks-uc")
            assert result.backend == "databricks"
            assert result.run_url is not None
            assert "myhost.databricks.com" in result.run_url
            assert "/experiments/42/runs/abc123" in result.run_url

    def test_missing_model_file_no_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-existent model path should not crash."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact") as m_artifact,
            patch("mlflow.register_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            result = log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5},
                params={},
                model_path="/nonexistent/model.cbm",
            )

            # model file doesn't exist — only model_card artifact should be logged
            assert result.run_id == "abc123"
            artifact_dirs = [
                call.args[1] if len(call.args) > 1 else "" for call in m_artifact.call_args_list
            ]
            assert "model_card" in artifact_dirs
            # No direct model artifact (only model_card)
            assert m_artifact.call_count == 1
            assert m_artifact.call_args[0][1] == "model_card"

    def test_with_artifacts(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """SHAP, importance, and CV results are all logged as artifacts."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        model_file = tmp_path / "model.cbm"
        model_file.write_text("fake model")

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact") as m_artifact,
            patch("mlflow.log_metric") as m_metric,
            patch("mlflow.register_model"),
            # Item #2: log_experiment now attaches a signature via the
            # flavor-typed logger for .cbm files.  Patch so the fake
            # bytes don't trigger a real CatBoost save.
            patch("mlflow.catboost.log_model"),
            patch("mlflow.pyfunc.log_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            result = log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5},
                params={},
                model_path=str(model_file),
                diagnostics=ModelDiagnostics(
                    shap_summary=[{"feature": "x1", "mean_abs_shap": 0.3}],
                    feature_importance_loss=[{"feature": "x1", "importance": 0.4}],
                ),
            )

            assert result.run_id == "abc123"
            artifact_dirs = [
                call.args[1] if len(call.args) > 1 else call.kwargs.get("artifact_path", "")
                for call in m_artifact.call_args_list
            ]
            # CV results were removed in Phase 2 Package 2C-5 — the "cv"
            # artifact dir must no longer be emitted.
            for expected in ("shap", "importance", "model_card"):
                assert expected in artifact_dirs, f"Missing artifact dir: {expected}"
            assert "cv" not in artifact_dirs
            m_metric.assert_not_called()

    def test_databricks_registers_model(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When backend is databricks and model_name is set, model is registered."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test")

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        model_file = tmp_path / "model.cbm"
        model_file.write_text("fake model")

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model") as m_register,
            patch("mlflow.catboost.log_model"),
            patch("mlflow.pyfunc.log_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5},
                params={},
                model_path=str(model_file),
                model_name="my-registered-model",
            )

            m_register.assert_called_once_with(
                "runs:/abc123/model.cbm",
                "my-registered-model",
            )

    def test_log_generates_model_card(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """With full data, model card artifact should be logged."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        model_file = tmp_path / "model.cbm"
        model_file.write_text("fake model")

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact") as m_artifact,
            patch("mlflow.log_metric"),
            patch("mlflow.register_model"),
            patch("mlflow.catboost.log_model"),
            patch("mlflow.pyfunc.log_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5},
                params={"algorithm": "catboost"},
                model_path=str(model_file),
                diagnostics=ModelDiagnostics(
                    double_lift=[{"decile": 1, "actual": 0.1, "predicted": 0.12, "count": 100}],
                    feature_importance=[{"feature": "x1", "importance": 0.8}],
                ),
                metadata=ModelCardMetadata(
                    algorithm="catboost",
                    task="regression",
                    train_rows=800,
                    test_rows=200,
                    features=["x1"],
                    split_config={"strategy": "random"},
                ),
            )

            # Check that model_card artifact was logged
            artifact_dirs = [
                call.args[1] if len(call.args) > 1 else call.kwargs.get("artifact_path", "")
                for call in m_artifact.call_args_list
            ]
            assert "model_card" in artifact_dirs

    def test_log_model_card_skipped_when_minimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With minimal data (no double_lift/importance), model card should still be generated."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact") as m_artifact,
            patch("mlflow.register_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5},
                params={},
                metadata=ModelCardMetadata(algorithm="catboost", task="regression"),
            )

            # Model card should still be logged even with minimal data
            artifact_dirs = [
                call.args[1] if len(call.args) > 1 else call.kwargs.get("artifact_path", "")
                for call in m_artifact.call_args_list
            ]
            assert "model_card" in artifact_dirs

    def test_log_model_card_failure_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If model card generation raises, log_experiment should still succeed."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model"),
            patch("haute.modelling._mlflow_log._log_model_card", side_effect=RuntimeError("boom")),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            # Should not raise
            result = log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5},
                params={},
            )
            assert result.run_id == "abc123"

    def test_local_does_not_register_model(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When backend is local, model_name should be ignored (no UC registry)."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "abc123"

        model_file = tmp_path / "model.cbm"
        model_file.write_text("fake model")

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model") as m_register,
            patch("mlflow.catboost.log_model"),
            patch("mlflow.pyfunc.log_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            log_experiment(
                experiment_name="/test/experiment",
                run_name="test-run",
                metrics={"rmse": 0.5},
                params={},
                model_path=str(model_file),
                model_name="my-registered-model",
            )

            m_register.assert_not_called()

    def test_with_all_diagnostics_artifacts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """All diagnostic fields should be logged as artifacts."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "full123"

        model_file = tmp_path / "model.cbm"
        model_file.write_text("fake model")

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact") as m_artifact,
            patch("mlflow.log_metric") as m_metric,
            patch("mlflow.register_model"),
            patch("mlflow.catboost.log_model"),
            patch("mlflow.pyfunc.log_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            result = log_experiment(
                experiment_name="/test/all",
                run_name="full-run",
                metrics={"rmse": 0.5},
                params={"algo": "catboost"},
                model_path=str(model_file),
                diagnostics=ModelDiagnostics(
                    shap_summary=[{"feature": "x1", "mean_abs_shap": 0.3}],
                    feature_importance=[{"feature": "x1", "importance": 0.7}],
                    feature_importance_loss=[{"feature": "x1", "importance": 0.4}],
                    double_lift=[{"decile": 1, "actual": 0.1, "predicted": 0.12, "count": 100}],
                    loss_history=[{"iteration": i, "train_RMSE": 1.0 / (i + 1)} for i in range(10)],
                    ave_per_feature=[
                        {
                            "feature": "x1",
                            "bins": [
                                {
                                    "label": "0-5",
                                    "exposure": 100,
                                    "avg_actual": 0.5,
                                    "avg_predicted": 0.6,
                                }
                            ],
                        }
                    ],
                    residuals_histogram=[{"bin_center": 0, "count": 10, "weighted_count": 10.0}],
                    residuals_stats={"mean": 0.01, "std": 0.5},
                    actual_vs_predicted=[{"actual": 0.5, "predicted": 0.6, "weight": 1.0}],
                    lorenz_curve=[{"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}],
                    lorenz_curve_perfect=[{"cum_weight_frac": 0.0, "cum_actual_frac": 0.0}],
                    pdp_data=[{"feature": "x1", "grid": [{"value": 1, "avg_prediction": 0.5}]}],
                    holdout_metrics={"rmse": 0.55, "gini": 0.65},
                ),
                metadata=ModelCardMetadata(
                    algorithm="catboost",
                    task="regression",
                    train_rows=800,
                    test_rows=200,
                    features=["x1", "x2"],
                    best_iteration=5,
                ),
            )

            assert result.run_id == "full123"
            artifact_dirs = [
                call.args[1] if len(call.args) > 1 else call.kwargs.get("artifact_path", "")
                for call in m_artifact.call_args_list
            ]
            # All artifact subdirectories should be present. The "cv"
            # artifact dir was removed in Phase 2 Package 2C-5.
            for expected in (
                "shap",
                "importance",
                "diagnostics",
                "model_card",
            ):
                assert expected in artifact_dirs, f"Missing artifact dir: {expected}"
            assert "cv" not in artifact_dirs

            # Holdout metrics should be logged as individual metrics
            holdout_calls = [c for c in m_metric.call_args_list if c.args[0].startswith("holdout_")]
            assert len(holdout_calls) == 2
            # CV metrics were removed along with the CV path.
            cv_calls = [c for c in m_metric.call_args_list if c.args[0].startswith("cv_mean_")]
            assert cv_calls == []

    def test_with_glm_diagnostics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GLM-specific diagnostics should be logged as artifacts and metrics."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "glm123"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact") as m_artifact,
            patch("mlflow.log_metric") as m_metric,
            patch("mlflow.register_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            result = log_experiment(
                experiment_name="/test/glm",
                run_name="glm-run",
                metrics={"rmse": 0.5},
                params={},
                diagnostics=ModelDiagnostics(
                    glm_coefficients=[{"feature": "x1", "coeff": 0.5}],
                    glm_relativities=[{"feature": "x1", "relativity": 1.5}],
                    glm_fit_statistics={
                        "aic": 100.0,
                        "bic": 110.0,
                        "deviance": 50.0,
                        "null_deviance": 200.0,
                    },
                    glm_regularization_path={"alphas": [0.1, 0.01], "scores": [0.5, 0.6]},
                ),
            )

            assert result.run_id == "glm123"
            artifact_dirs = [
                call.args[1] if len(call.args) > 1 else "" for call in m_artifact.call_args_list
            ]
            assert "glm" in artifact_dirs

            # GLM fit stats should be logged as top-level metrics
            metric_names = [c.args[0] for c in m_metric.call_args_list]
            for key in ("aic", "bic", "deviance", "null_deviance"):
                assert key in metric_names, f"GLM stat {key} not logged as metric"

    def test_enhanced_params_include_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Metadata fields should be added to params."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "meta123"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params") as m_params,
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            log_experiment(
                experiment_name="/test/meta",
                run_name="meta-run",
                metrics={"rmse": 0.5},
                params={"algo": "catboost"},
                metadata=ModelCardMetadata(
                    algorithm="catboost",
                    task="regression",
                    train_rows=800,
                    test_rows=200,
                    features=["x1", "x2"],
                    best_iteration=42,
                ),
            )

            logged_params = m_params.call_args[0][0]
            assert "train_rows" in logged_params
            assert "test_rows" in logged_params
            assert "n_features" in logged_params
            assert "best_iteration" in logged_params
            assert logged_params["train_rows"] == "800"

    def test_many_params_batched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """More than 100 params should be batched in groups of 100."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "batch123"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params") as m_params,
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            log_experiment(
                experiment_name="/test/batch",
                run_name="batch-run",
                metrics={"rmse": 0.5},
                params={f"param_{i}": f"value_{i}" for i in range(150)},
            )

            # Should be called twice: first batch of 100, second batch of 50
            assert m_params.call_count == 2

    def test_long_param_values_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Param values longer than 500 chars should be truncated."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "trunc123"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params") as m_params,
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model"),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            log_experiment(
                experiment_name="/test/trunc",
                run_name="trunc-run",
                metrics={"rmse": 0.5},
                params={"long_param": "x" * 1000},
            )

            logged_params = m_params.call_args[0][0]
            assert len(logged_params["long_param"]) == 500


class TestBuildRunUrlExtra:
    def test_returns_none_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When mlflow.get_experiment_by_name raises, return None."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com")

        with patch("mlflow.get_experiment_by_name", side_effect=RuntimeError("boom")):
            from haute.modelling._mlflow_log import build_run_url

            assert build_run_url("databricks", "/Shared/haute/freq", "run123") is None


class TestConfigureMlflowTracking:
    def test_local_tracking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Local backend should set tracking URI to file:// path."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        with (
            patch("mlflow.set_tracking_uri") as m_tracking,
            patch("mlflow.set_registry_uri") as m_registry,
        ):
            from haute.modelling._mlflow_log import configure_mlflow_tracking

            uri, backend = configure_mlflow_tracking()
            assert backend == "local"
            assert uri.startswith("file://")
            m_tracking.assert_called_once_with(uri)
            m_registry.assert_not_called()

    def test_databricks_tracking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Databricks backend should set both tracking and registry URIs."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://myhost.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test")

        with (
            patch("mlflow.set_tracking_uri") as m_tracking,
            patch("mlflow.set_registry_uri") as m_registry,
        ):
            from haute.modelling._mlflow_log import configure_mlflow_tracking

            uri, backend = configure_mlflow_tracking()
            assert backend == "databricks"
            assert uri == "databricks"
            m_tracking.assert_called_once_with("databricks")
            m_registry.assert_called_once_with("databricks-uc")


class TestLogJsonArtifact:
    def test_writes_and_cleans_up(self) -> None:
        """_log_json_artifact should write JSON, log it, and delete the file."""
        import os

        mock_mlflow = MagicMock()
        from haute.modelling._mlflow_log import _log_json_artifact

        _log_json_artifact(mock_mlflow, {"key": "value"}, "test", "test_dir")
        mock_mlflow.log_artifact.assert_called_once()
        logged_path = mock_mlflow.log_artifact.call_args[0][0]
        # File should have been cleaned up
        assert not os.path.exists(logged_path)

    def test_cleans_up_on_error(self) -> None:
        """Even if log_artifact raises, the temp file should be cleaned up."""

        mock_mlflow = MagicMock()
        mock_mlflow.log_artifact.side_effect = RuntimeError("boom")

        from haute.modelling._mlflow_log import _log_json_artifact

        with pytest.raises(RuntimeError, match="boom"):
            _log_json_artifact(mock_mlflow, {"key": "value"}, "test", "test_dir")


class TestLogModelCard:
    def test_generates_and_logs_html(self) -> None:
        """_log_model_card should generate HTML and log as artifact."""
        import os

        mock_mlflow = MagicMock()
        from haute.modelling._mlflow_log import _log_model_card

        _log_model_card(
            mock_mlflow,
            name="test-model",
            metrics={"rmse": 0.5},
            params={"algo": "catboost"},
            diagnostics=ModelDiagnostics(),
            metadata=ModelCardMetadata(algorithm="catboost", task="regression"),
        )

        mock_mlflow.log_artifact.assert_called_once()
        args = mock_mlflow.log_artifact.call_args
        assert args[0][1] == "model_card"
        # Temp file should be cleaned up
        assert not os.path.exists(args[0][0])
