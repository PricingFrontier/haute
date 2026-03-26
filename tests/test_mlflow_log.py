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
                    cv_results={
                        "mean_metrics": {"rmse": 0.45},
                        "std_metrics": {"rmse": 0.02},
                        "n_folds": 3,
                    },
                ),
            )

            assert result.run_id == "abc123"
            artifact_dirs = [
                call.args[1] if len(call.args) > 1 else call.kwargs.get("artifact_path", "")
                for call in m_artifact.call_args_list
            ]
            for expected in ("shap", "importance", "cv", "model_card"):
                assert expected in artifact_dirs, f"Missing artifact dir: {expected}"
            # model artifact logged without artifact_path subdir
            artifact_files = [call.args[0] for call in m_artifact.call_args_list]
            assert any(str(model_file) in str(f) for f in artifact_files)
            # CV mean metric logged
            m_metric.assert_called_once_with("cv_mean_rmse", 0.45)

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
