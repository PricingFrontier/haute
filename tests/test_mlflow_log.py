"""Tests for haute.modelling._mlflow_log — standalone MLflow experiment logging."""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from haute.modelling._result_types import ModelCardMetadata, ModelDiagnostics

if TYPE_CHECKING:
    from haute.modelling._candidate_run import CandidateRun


@pytest.fixture(autouse=True)
def _clean_tracking_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test opts into its own environment destination, including real-store tests."""
    for var in (
        "MLFLOW_TRACKING_URI",
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_MLFLOW_HOST",
        "DATABRICKS_MLFLOW_TOKEN",
        "DATABRICKS_CONFIG_PROFILE",
        "MLFLOW_ENABLE_DB_SDK",
    ):
        monkeypatch.delenv(var, raising=False)


def _real_catboost_model(directory: Path, features: tuple[str, ...] = ("age",)) -> Path:
    import pandas as pd
    from catboost import CatBoostRegressor

    directory.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({name: [20.0, 30.0, 40.0, 50.0] for name in features})
    model = CatBoostRegressor(iterations=2, depth=1, verbose=0, allow_writing_files=False)
    model.fit(frame, [1.0, 2.0, 3.0, 4.0])
    model_file = directory / "model.cbm"
    model.save_model(str(model_file))
    return model_file


def _candidate(
    directory: Path,
    *,
    model_file: Path | None = None,
    suffix: str = ".cbm",
    features: tuple[str, ...] = ("age",),
    feature_types: dict[str, str] | None = None,
    diagnostics: ModelDiagnostics | None = None,
    metrics: dict[str, float] | None = None,
    params: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
    algorithm: str = "catboost",
) -> CandidateRun:
    """A complete candidate run whose artifact files exist on disk."""
    from haute.modelling._candidate_run import (
        CandidateArtifacts,
        CandidateProvenance,
        CandidateRun,
    )
    from haute.modelling._feature_contract import ModelIdentity, build_contract, save_contract
    from haute.modelling._training_job import model_contract_filename

    directory.mkdir(parents=True, exist_ok=True)
    if model_file is None:
        model_file = directory / f"model{suffix}"
        model_file.write_bytes(b"fake-model")
    types = feature_types or {name: "Float64" for name in features}
    contract_file = directory / model_contract_filename(model_file.stem)
    save_contract(
        build_contract(
            features=list(features),
            feature_types=types,
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
            model=ModelIdentity(
                algorithm=algorithm,
                link="identity",
                engine_name="catboost" if algorithm == "catboost" else "rustystats",
                engine_version="0",
                haute_version="0",
                glm_family="gaussian" if algorithm == "glm" else None,
            ),
        ),
        contract_file,
    )
    evidence = {}
    for kind in ("evaluation_plan", "evaluation_results", "evaluation_report"):
        (directory / f"{model_file.stem}.{kind}.json").write_text("{}", encoding="utf-8")
        evidence[kind] = directory / f"{model_file.stem}.{kind}.json"
    return CandidateRun(
        provenance=CandidateProvenance(
            job_id="job-1",
            node_label="freq",
            trained_at=datetime(2026, 9, 13, 8, 30, tzinfo=UTC),
            training_identity_sha256="a" * 64,
        ),
        run_name="freq · 2026-09-13 08:30 UTC",
        tags=tags or {"haute.contract_version": "1", "haute.job_id": "job-1"},
        params=params or {"algorithm": algorithm, "task": "regression"},
        metrics=metrics or {"final_test_rmse": 0.5},
        diagnostics=diagnostics or ModelDiagnostics(),
        metadata=ModelCardMetadata(
            algorithm=algorithm,
            task="regression",
            features=list(features),
            feature_types=types,
            target_name="y",
            target_type="Float64",
        ),
        artifacts=CandidateArtifacts(
            model=model_file, feature_contract=contract_file, evidence=evidence
        ),
    )


@contextmanager
def _mocked_mlflow(run_id: str = "abc123", **extra_patches: Any) -> Iterator[SimpleNamespace]:
    """Patch every MLflow side effect ``log_experiment`` makes and expose the mocks."""
    mock_run = MagicMock()
    mock_run.info.run_id = run_id
    names = {
        "tracking": "mlflow.set_tracking_uri",
        "registry": "mlflow.set_registry_uri",
        "experiment": "mlflow.set_experiment",
        "run": "mlflow.start_run",
        "params": "mlflow.log_params",
        "metrics": "mlflow.log_metrics",
        "artifact": "mlflow.log_artifact",
        "set_tag": "mlflow.set_tag",
        "catboost_log_model": "mlflow.catboost.log_model",
        "pyfunc_log_model": "mlflow.pyfunc.log_model",
        "register": "mlflow.register_model",
        **extra_patches,
    }
    with ExitStack() as stack:
        mocks = {key: stack.enter_context(patch(target)) for key, target in names.items()}
        mocks["run"].return_value.__enter__ = MagicMock(return_value=mock_run)
        mocks["run"].return_value.__exit__ = MagicMock(return_value=False)
        yield SimpleNamespace(**mocks)


def _artifact_dirs(artifact_mock: MagicMock) -> list[str]:
    return [
        call.args[1] if len(call.args) > 1 else call.kwargs.get("artifact_path", "")
        for call in artifact_mock.call_args_list
    ]


class TestResolveTrackingBackend:
    def test_databricks_when_chosen_and_env_vars_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://myhost.databricks.com")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi_test_token")

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend("databricks")
        assert uri == "databricks"
        assert backend == "databricks"

    def test_local_when_env_vars_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend()
        assert uri.startswith("file://")
        assert "mlruns" in uri
        assert backend == "local"

    def test_local_when_only_host_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://myhost.databricks.com")
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend()
        assert backend == "local"

    def test_local_when_only_token_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi_test_token")

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend()
        assert backend == "local"

    def test_env_tracking_uri_selects_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5000")

        from haute.modelling._mlflow_log import resolve_tracking_backend

        uri, backend = resolve_tracking_backend("server")
        assert uri == "http://localhost:5000"
        assert backend == "server"
        # The environment configures the server destination; it never chooses it.
        assert resolve_tracking_backend()[1] == "local"

    def test_no_destination_is_local_even_with_databricks_credentials(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "t")
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

        from haute.modelling._mlflow_log import resolve_tracking_backend

        assert resolve_tracking_backend("databricks")[1] == "databricks"
        for destination in ("", "local"):
            uri, backend = resolve_tracking_backend(destination)
            assert backend == "local" and uri == (tmp_path / "mlruns").as_uri()

    def test_explicit_unconfigured_destination_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "DATABRICKS_HOST",
            "DATABRICKS_TOKEN",
            "DATABRICKS_MLFLOW_HOST",
            "DATABRICKS_MLFLOW_TOKEN",
            "MLFLOW_TRACKING_URI",
        ):
            monkeypatch.delenv(var, raising=False)

        from haute.errors import MlflowConfigError
        from haute.modelling._mlflow_log import resolve_tracking_backend

        with pytest.raises(MlflowConfigError, match="MLflow server is not configured"):
            resolve_tracking_backend("server")

    def test_unknown_destination_raises_before_resolution(self) -> None:
        from haute.errors import MlflowConfigError
        from haute.modelling._mlflow_log import resolve_tracking_backend

        with pytest.raises(MlflowConfigError, match="databricks, server, or local"):
            resolve_tracking_backend("managed")

    def test_unsupported_env_scheme_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")

        from haute.errors import MlflowConfigError
        from haute.modelling._mlflow_log import resolve_tracking_backend

        with pytest.raises(MlflowConfigError, match="sqlite"):
            resolve_tracking_backend()


def test_decimal_signature_error_happens_before_pyfunc_model_logging(tmp_path: Path) -> None:
    """Unsupported Decimal contracts fail before MLflow receives a log_model call."""
    import mlflow

    from haute.modelling._mlflow_log import _log_model_with_signature

    model_path = tmp_path / "model.rsglm"
    model_path.write_bytes(b"placeholder")
    with patch("mlflow.pyfunc.log_model") as log_model:
        with pytest.raises(ValueError, match="MLflow 3.x"):
            _log_model_with_signature(
                mlflow,
                model_path=model_path,
                contract_path=tmp_path / "unused_contract.json",
                metadata=ModelCardMetadata(
                    algorithm="glm",
                    task="regression",
                    features=["monetary_amount"],
                    feature_types={"monetary_amount": "Decimal(precision=12, scale=3)"},
                    target_name="loss",
                    target_type="Float64",
                ),
            )

    log_model.assert_not_called()


class TestResolveExperimentName:
    def test_explicit_wins(self) -> None:
        from haute.modelling._mlflow_log import resolve_experiment_name

        assert (
            resolve_experiment_name(
                explicit="/my/override",
                node_label="freq",
                backend="local",
            )
            == "/my/override"
        )

    def test_no_training_snapshot_tier_exists(self) -> None:
        import inspect

        from haute.modelling._mlflow_log import resolve_experiment_name

        assert "config_value" not in inspect.signature(resolve_experiment_name).parameters

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
        monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)

        from haute.modelling._mlflow_log import resolve_experiment_name

        assert resolve_experiment_name(node_label="freq") == "freq"

    def test_empty_strings_are_falsy(self) -> None:
        from haute.modelling._mlflow_log import resolve_experiment_name

        assert (
            resolve_experiment_name(
                explicit="",
                node_label="freq",
                backend="local",
            )
            == "freq"
        )

    def test_destination_drives_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "t")
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

        from haute.modelling._mlflow_log import resolve_experiment_name

        assert resolve_experiment_name(node_label="m", destination="local") == "m"
        assert (
            resolve_experiment_name(node_label="m", destination="databricks") == "/Shared/haute/m"
        )


class TestBuildRunUrl:
    def test_returns_none_for_local(self) -> None:
        from haute.modelling._mlflow_log import build_run_url

        assert build_run_url("local", "exp", "run123") is None

    def test_returns_url_for_databricks(self) -> None:
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "42"

        with (
            patch(
                "mlflow.utils.databricks_utils.get_databricks_host_creds",
                return_value=SimpleNamespace(host="https://myhost.databricks.com"),
            ),
            patch("mlflow.get_experiment_by_name", return_value=mock_experiment),
        ):
            from haute.modelling._mlflow_log import build_run_url

            url = build_run_url("databricks", "/Shared/haute/freq", "run123")
            assert url == "https://myhost.databricks.com/#mlflow/experiments/42/runs/run123"

    def test_returns_none_when_experiment_not_found(self) -> None:
        with (
            patch(
                "mlflow.utils.databricks_utils.get_databricks_host_creds",
                return_value=SimpleNamespace(host="https://myhost.databricks.com"),
            ),
            patch("mlflow.get_experiment_by_name", return_value=None),
        ):
            from haute.modelling._mlflow_log import build_run_url

            assert build_run_url("databricks", "/Shared/haute/freq", "run123") is None

    def test_returns_none_when_host_missing(self) -> None:
        with patch(
            "mlflow.utils.databricks_utils.get_databricks_host_creds",
            return_value=SimpleNamespace(host=""),
        ):
            from haute.modelling._mlflow_log import build_run_url

            assert build_run_url("databricks", "/Shared/haute/freq", "run123") is None

    def test_strips_trailing_slash_from_host(self) -> None:
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "42"

        with (
            patch(
                "mlflow.utils.databricks_utils.get_databricks_host_creds",
                return_value=SimpleNamespace(host="https://myhost.databricks.com/"),
            ),
            patch("mlflow.get_experiment_by_name", return_value=mock_experiment),
        ):
            from haute.modelling._mlflow_log import build_run_url

            url = build_run_url("databricks", "/Shared/haute/freq", "run123")
            assert "databricks.com//" not in url  # no double slash

    def test_returns_url_for_server(self) -> None:
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "42"

        with (
            patch("mlflow.get_experiment_by_name", return_value=mock_experiment),
            patch("mlflow.get_tracking_uri", return_value="http://localhost:5000/"),
        ):
            from haute.modelling._mlflow_log import build_run_url

            url = build_run_url("server", "freq", "run123")
            assert url == "http://localhost:5000/#/experiments/42/runs/run123"

    def test_server_returns_none_when_experiment_not_found(self) -> None:
        with (
            patch("mlflow.get_experiment_by_name", return_value=None),
            patch("mlflow.get_tracking_uri", return_value="http://localhost:5000"),
        ):
            from haute.modelling._mlflow_log import build_run_url

            assert build_run_url("server", "freq", "run123") is None

    def test_server_run_url_redacts_embedded_credentials(self) -> None:
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "42"

        with (
            patch("mlflow.get_experiment_by_name", return_value=mock_experiment),
            patch(
                "mlflow.get_tracking_uri",
                return_value="https://alice:hunter2xyz@mlflow.example.com:8443",
            ),
        ):
            from haute.modelling._mlflow_log import build_run_url

            url = build_run_url("server", "freq", "run123")
            assert url == "https://mlflow.example.com:8443/#/experiments/42/runs/run123"
            assert "hunter2xyz" not in url


class TestRegistryUriFollowsDestination:
    def test_databricks_registry_retains_profile(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from haute._sandbox import set_project_root
        from haute.modelling._mlflow_log import configure_mlflow_tracking

        set_project_root(tmp_path)
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team-profile")
        with patch("mlflow.set_tracking_uri"), patch("mlflow.set_registry_uri") as registry:
            configure_mlflow_tracking("databricks")
        registry.assert_called_once_with("databricks-uc://team-profile")

    def test_leaving_databricks_resets_the_registry_uri(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The process-global registry URI must never stay on Unity Catalog
        after the destination switches away from Databricks."""
        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

        from haute.modelling._mlflow_log import configure_mlflow_tracking

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri") as m_registry,
        ):
            monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
            monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi-token")
            configure_mlflow_tracking("databricks")
            assert m_registry.call_args_list[-1].args == ("databricks-uc",)

            tracking_uri, backend = configure_mlflow_tracking("local")
            assert backend == "local"
            # The registry explicitly follows the local tracking store.
            assert m_registry.call_args_list[-1].args == (tracking_uri,)


class TestLocalRegistrationEndToEnd:
    def test_destination_switch_during_log_keeps_run_at_original_store(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        import mlflow
        from mlflow.tracking import MlflowClient

        from haute._sandbox import set_project_root
        from haute.modelling._mlflow_log import log_experiment
        from haute.modelling._mlflow_settings import MlflowSettings, save_mlflow_settings
        from haute.routes.mlflow import list_experiments

        set_project_root(tmp_path)
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        save_mlflow_settings(MlflowSettings(folder="a"), tmp_path)
        checks = 0

        def interleave() -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                save_mlflow_settings(MlflowSettings(folder="b"), tmp_path)
                list_experiments()

        previous_tracking, previous_registry = mlflow.get_tracking_uri(), mlflow.get_registry_uri()
        try:
            with (
                patch("haute.modelling._mlflow_log._log_model_card"),
                patch("haute.modelling._mlflow_log._log_model_with_signature"),
            ):
                result = log_experiment(
                    experiment_name="review",
                    candidate=_candidate(tmp_path / "candidate", params={"key": "value"}),
                    check_cancelled=interleave,
                )
            original = MlflowClient(tracking_uri=(tmp_path / "a").as_uri())
            assert original.get_run(result.run_id).info.status == "FINISHED"
            assert original.get_run(result.run_id).data.params == {"key": "value"}
            assert mlflow.get_tracking_uri() == previous_tracking
            assert mlflow.get_registry_uri() == previous_registry
            assert "MLFLOW_TRACKING_URI" not in os.environ
        finally:
            mlflow.set_tracking_uri(previous_tracking)
            mlflow.set_registry_uri(previous_registry)

    def test_log_register_discover_load_and_score_locally(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A real local file store: haute logs a genuine CatBoost model, an
        external promotion step registers the logged run (haute never
        registers), and the registered-model path (including "latest"
        resolution over the file store's int versions) loads a
        scoring-capable model."""
        import pandas as pd

        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        frame = pd.DataFrame({"age": [20.0, 30.0, 40.0, 50.0]})
        artifacts = tmp_path / "artifacts"
        candidate = _candidate(artifacts, model_file=_real_catboost_model(artifacts))

        import mlflow

        from haute.modelling._mlflow_log import log_experiment

        try:
            result = log_experiment(experiment_name="e2e-exp", candidate=candidate)
            assert result.backend == "local"
            # The external promotion process registers the candidate run
            # against the tracking store the run was logged to.
            mlflow.set_tracking_uri(result.tracking_uri)
            mlflow.set_registry_uri(result.tracking_uri)
            mlflow.register_model(f"runs:/{result.run_id}/model", "e2e-model")

            from haute._mlflow_io import load_mlflow_model

            scoring = load_mlflow_model(
                source_type="registered",
                registered_model="e2e-model",
                version="latest",
                task="regression",
            )
            predictions = scoring.raw_model.predict(frame)
            assert len(predictions) == 4
            assert all(math.isfinite(float(p)) for p in predictions)
        finally:
            mlflow.set_tracking_uri(None)
            mlflow.set_registry_uri(None)


class TestLoggingNeverRegisters:
    """Haute logs candidate runs; registering and promoting them is external."""

    def test_log_experiment_has_no_registry_parameter(self) -> None:
        import inspect

        from haute.modelling._mlflow_log import log_experiment

        assert "model_name" not in inspect.signature(log_experiment).parameters

    def test_logging_never_calls_the_registry(self, tmp_path: Path) -> None:
        from haute.modelling._mlflow_log import log_experiment

        with _mocked_mlflow(
            model_card="haute.modelling._mlflow_log._log_model_card",
            model_logger="haute.modelling._mlflow_log._log_model_with_signature",
        ) as m:
            log_experiment(experiment_name="exp", candidate=_candidate(tmp_path))
        m.register.assert_not_called()


class TestLogExperiment:
    def test_logs_the_candidate_run_name_tags_params_and_metrics(self, tmp_path: Path) -> None:
        from haute.modelling._mlflow_log import log_experiment

        candidate = _candidate(
            tmp_path,
            params={"algorithm": "catboost", "task": "regression"},
            metrics={"final_test_rmse": 0.5, "selection_gini_mean": 0.8},
            tags={"haute.contract_version": "1", "haute.node_id": "freq"},
        )
        with _mocked_mlflow(
            model_card="haute.modelling._mlflow_log._log_model_card",
            model_logger="haute.modelling._mlflow_log._log_model_with_signature",
        ) as m:
            result = log_experiment(experiment_name="/test/experiment", candidate=candidate)

        assert "file://" in m.tracking.call_args[0][0]
        m.registry.assert_called_once()  # registry follows the local tracking store
        m.experiment.assert_called_once_with("/test/experiment")
        m.run.assert_called_once_with(
            run_name="freq · 2026-09-13 08:30 UTC",
            tags={"haute.contract_version": "1", "haute.node_id": "freq"},
        )
        m.params.assert_called_once_with({"algorithm": "catboost", "task": "regression"})
        m.metrics.assert_called_once_with({"final_test_rmse": 0.5, "selection_gini_mean": 0.8})
        m.model_logger.assert_called_once()
        assert result.backend == "local"
        assert result.experiment_name == "/test/experiment"
        assert result.run_id == "abc123"
        assert result.run_url is None

    def test_logs_the_feature_contract_and_evaluation_evidence(self, tmp_path: Path) -> None:
        from haute.modelling._mlflow_log import log_experiment

        candidate = _candidate(tmp_path)
        tuning_plan = tmp_path / "model.tuning-plan.json"
        tuning_plan.write_text("{}", encoding="utf-8")
        candidate = replace(
            candidate,
            artifacts=replace(
                candidate.artifacts,
                evidence={**candidate.artifacts.evidence, "tuning_plan": tuning_plan},
            ),
        )
        with _mocked_mlflow(
            model_card="haute.modelling._mlflow_log._log_model_card",
            model_logger="haute.modelling._mlflow_log._log_model_with_signature",
        ) as m:
            log_experiment(experiment_name="exp", candidate=candidate)

        logged = list(
            zip(
                (Path(call.args[0]).name for call in m.artifact.call_args_list),
                _artifact_dirs(m.artifact),
                strict=True,
            )
        )
        assert (candidate.artifacts.feature_contract.name, "") in logged
        for kind, path in candidate.artifacts.evidence.items():
            expected_dir = "tuning" if kind == "tuning_plan" else "evaluation"
            assert (path.name, expected_dir) in logged

    def test_databricks_sets_registry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When Databricks env vars present, set_registry_uri('databricks-uc') is called."""
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://myhost.databricks.com")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi_test_token")
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "42"

        from haute.modelling._mlflow_log import log_experiment

        with _mocked_mlflow(
            model_card="haute.modelling._mlflow_log._log_model_card",
            model_logger="haute.modelling._mlflow_log._log_model_with_signature",
            get_experiment="mlflow.get_experiment_by_name",
        ) as m:
            m.get_experiment.return_value = mock_experiment
            result = log_experiment(
                experiment_name="/test/experiment",
                candidate=_candidate(tmp_path),
                destination="databricks",
            )

        m.tracking.assert_called_once_with("databricks")
        m.registry.assert_called_once_with("databricks-uc")
        assert result.backend == "databricks"
        assert result.run_url is not None
        assert "myhost.databricks.com" in result.run_url
        assert "/experiments/42/runs/abc123" in result.run_url

    def test_missing_artifact_fails_before_any_tracking_call(self, tmp_path: Path) -> None:
        from haute.errors import HauteValidationError
        from haute.modelling._mlflow_log import log_experiment

        candidate = _candidate(tmp_path)
        candidate.artifacts.feature_contract.unlink()
        with _mocked_mlflow() as m, pytest.raises(HauteValidationError, match="feature_contract"):
            log_experiment(experiment_name="exp", candidate=candidate)
        m.tracking.assert_not_called()
        m.run.assert_not_called()

    def test_rustystats_model_is_a_haute_pyfunc_plus_native_root_artifact(
        self, tmp_path: Path
    ) -> None:
        """The GLM logs as a pyfunc whose loader scores with haute, and the native
        file sits at the run root where haute's run discovery looks for it."""
        from haute.modelling._mlflow_log import log_experiment

        candidate = _candidate(tmp_path, suffix=".rsglm", algorithm="glm")
        with (
            _mocked_mlflow(model_card="haute.modelling._mlflow_log._log_model_card") as m,
            patch("haute.modelling._native_pyfunc.NativePyfuncModel"),
        ):
            log_experiment(experiment_name="/test/glm", candidate=candidate)

        m.pyfunc_log_model.assert_called_once()
        kwargs = m.pyfunc_log_model.call_args.kwargs
        assert kwargs["name"] == "model"
        assert kwargs["loader_module"] == "haute.modelling._native_pyfunc"
        assert Path(kwargs["data_path"]).name == "model"
        assert kwargs["signature"] is not None
        native = [c for c in m.artifact.call_args_list if Path(c.args[0]).name == "model.rsglm"]
        assert [c.args for c in native] == [(str(candidate.artifacts.model),)]

    def test_catboost_model_is_the_shared_haute_pyfunc_plus_root_artifact(
        self, tmp_path: Path
    ) -> None:
        from haute.modelling._mlflow_log import log_experiment

        candidate = _candidate(tmp_path, model_file=_real_catboost_model(tmp_path))
        with _mocked_mlflow(model_card="haute.modelling._mlflow_log._log_model_card") as m:
            log_experiment(experiment_name="/test/cbm", candidate=candidate)

        m.catboost_log_model.assert_not_called()
        m.pyfunc_log_model.assert_called_once()
        kwargs = m.pyfunc_log_model.call_args.kwargs
        # MLflow 3 spelling: the LoggedModel is named, never ``artifact_path``.
        assert kwargs["name"] == "model"
        assert "artifact_path" not in kwargs
        assert kwargs["loader_module"] == "haute.modelling._native_pyfunc"
        signature = kwargs["signature"]
        assert signature.inputs.input_names() == ["age"]
        native = [c for c in m.artifact.call_args_list if Path(c.args[0]).name == "model.cbm"]
        assert len(native) == 1

    def test_unloadable_catboost_model_fails_before_log_model(self, tmp_path: Path) -> None:
        from haute.errors import HauteValidationError
        from haute.modelling._mlflow_log import log_experiment

        candidate = _candidate(tmp_path)  # b"fake-model" is not a CatBoost file
        with (
            _mocked_mlflow(model_card="haute.modelling._mlflow_log._log_model_card") as m,
            pytest.raises(HauteValidationError, match="could not be loaded"),
        ):
            log_experiment(experiment_name="/test/cbm", candidate=candidate)
        m.pyfunc_log_model.assert_not_called()

    def test_unknown_model_suffix_is_rejected(self, tmp_path: Path) -> None:
        from haute.errors import HauteValidationError
        from haute.modelling._mlflow_log import log_experiment

        candidate = _candidate(tmp_path, suffix=".pkl")
        with (
            _mocked_mlflow(model_card="haute.modelling._mlflow_log._log_model_card") as m,
            pytest.raises(
                HauteValidationError, match=r"expected one of \.cbm, \.lgbm, \.rsglm, \.ubj"
            ),
        ):
            log_experiment(experiment_name="exp", candidate=candidate)
        m.catboost_log_model.assert_not_called()
        m.pyfunc_log_model.assert_not_called()

    def test_diagnostics_are_logged_as_artifacts(self, tmp_path: Path) -> None:
        from haute.modelling._mlflow_log import log_experiment

        diagnostics = ModelDiagnostics(
            shap_summary=[{"feature": "x1", "mean_abs_shap": 0.3}],
            feature_importance=[{"feature": "x1", "importance": 0.7}],
            feature_importance_loss=[{"feature": "x1", "importance": 0.4}],
            double_lift=[{"decile": 1, "actual": 0.1, "predicted": 0.12, "count": 100}],
            loss_history=[{"iteration": i, "train_RMSE": 1.0 / (i + 1)} for i in range(10)],
            residuals_stats={"mean": 0.01, "std": 0.5},
            pdp_data=[{"feature": "x1", "grid": [{"value": 1, "avg_prediction": 0.5}]}],
            glm_coefficients=[{"feature": "x1", "coeff": 0.5}],
            glm_fit_statistics={"aic": 100.0},
        )
        with _mocked_mlflow(
            model_logger="haute.modelling._mlflow_log._log_model_with_signature",
        ) as m:
            result = log_experiment(
                experiment_name="/test/all",
                candidate=_candidate(tmp_path, diagnostics=diagnostics),
            )

        assert result.run_id == "abc123"
        dirs = _artifact_dirs(m.artifact)
        for expected in ("shap", "importance", "diagnostics", "glm", "model_card"):
            assert expected in dirs, f"Missing artifact dir: {expected}"
        assert "cv" not in dirs

    def test_model_card_failure_is_tagged_not_hidden(self, tmp_path: Path) -> None:
        from haute.modelling._mlflow_log import log_experiment

        with _mocked_mlflow(
            model_card="haute.modelling._mlflow_log._log_model_card",
            model_logger="haute.modelling._mlflow_log._log_model_with_signature",
        ) as m:
            m.model_card.side_effect = RuntimeError("boom")
            result = log_experiment(experiment_name="exp", candidate=_candidate(tmp_path))
        assert result.run_id == "abc123"
        m.set_tag.assert_called_once_with("haute.model_card", "unavailable")

    def test_many_params_batched_and_long_values_truncated(self, tmp_path: Path) -> None:
        from haute.modelling._mlflow_log import log_experiment

        params: dict[str, Any] = {f"param_{i}": f"value_{i}" for i in range(149)}
        params["long_param"] = "x" * 1000
        with _mocked_mlflow(
            model_card="haute.modelling._mlflow_log._log_model_card",
            model_logger="haute.modelling._mlflow_log._log_model_with_signature",
        ) as m:
            log_experiment(experiment_name="exp", candidate=_candidate(tmp_path, params=params))
        assert m.params.call_count == 2
        logged = {k: v for call in m.params.call_args_list for k, v in call.args[0].items()}
        assert len(logged["long_param"]) == 500

    def test_rustystats_run_yields_native_artifact_discoverable_end_to_end(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Against a real file store, the logged GLM run's native ``.rsglm`` is what
        haute's run-artifact discovery finds."""
        import mlflow

        from haute._mlflow_io import _find_model_artifact
        from haute._sandbox import set_project_root
        from haute.modelling._mlflow_log import log_experiment

        set_project_root(tmp_path)
        candidate = _candidate(
            tmp_path / "artifacts",
            suffix=".rsglm",
            algorithm="glm",
            features=("difference_to_market",),
        )
        try:
            # The fake .rsglm is not loadable; this test is about artifact discovery.
            with patch("haute.modelling._native_pyfunc.NativePyfuncModel"):
                result = log_experiment(experiment_name="rustystats_e2e", candidate=candidate)

            from mlflow.tracking import MlflowClient

            client = MlflowClient(tracking_uri=result.tracking_uri)
            artifact_path, flavor = _find_model_artifact(client, result.run_id)
            assert flavor == "rustystats"
            assert Path(artifact_path).name == "model.rsglm"
            root = {Path(f.path).name for f in client.list_artifacts(result.run_id)}
            assert candidate.artifacts.feature_contract.name in root
            assert client.get_run(result.run_id).data.tags["haute.contract_version"] == "1"
        finally:
            mlflow.set_tracking_uri("")
            mlflow.set_registry_uri(None)


class TestBuildRunUrlExtra:
    def test_returns_none_on_exception(self) -> None:
        """When mlflow.get_experiment_by_name raises, return None."""
        with (
            patch(
                "mlflow.utils.databricks_utils.get_databricks_host_creds",
                return_value=SimpleNamespace(host="https://myhost.databricks.com"),
            ),
            patch("mlflow.get_experiment_by_name", side_effect=RuntimeError("boom")),
        ):
            from haute.modelling._mlflow_log import build_run_url

            assert build_run_url("databricks", "/Shared/haute/freq", "run123") is None


class TestConfigureMlflowTracking:
    def test_local_tracking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Local backend should set tracking URI to file:// path."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)

        with (
            patch("mlflow.set_tracking_uri") as m_tracking,
            patch("mlflow.set_registry_uri") as m_registry,
        ):
            from haute.modelling._mlflow_log import configure_mlflow_tracking

            uri, backend = configure_mlflow_tracking()
            assert backend == "local"
            assert uri.startswith("file://")
            m_tracking.assert_called_once_with(uri)
            # The registry explicitly follows the tracking store, so a
            # leftover databricks-uc registry URI can never capture local
            # registrations.
            m_registry.assert_called_once_with(uri)

    def test_databricks_tracking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Databricks backend should set both tracking and registry URIs."""
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://myhost.databricks.com")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi_test")

        with (
            patch("mlflow.set_tracking_uri") as m_tracking,
            patch("mlflow.set_registry_uri") as m_registry,
        ):
            from haute.modelling._mlflow_log import configure_mlflow_tracking

            uri, backend = configure_mlflow_tracking("databricks")
            assert backend == "databricks"
            assert uri == "databricks"
            m_tracking.assert_called_once_with("databricks")
            m_registry.assert_called_once_with("databricks-uc")

    def test_cold_process_logging_binds_the_selected_profile_not_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cold-process logging binds profile credentials over conflicting environment."""
        import mlflow
        import requests

        from haute._mlflow_utils import mlflow_fluent_operation
        from haute.modelling._mlflow_log import configure_mlflow_tracking

        cfg = tmp_path / "databrickscfg"
        cfg.write_text(
            "[team]\nhost = https://profile-host.example.net\ntoken = profile-token-value\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(cfg))
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://env-host.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "env-token-value")
        monkeypatch.delenv("MLFLOW_ENABLE_DB_SDK", raising=False)
        captured: dict[str, object] = {}

        def fake_request(self_, method, url, **kwargs):
            captured["url"] = url
            captured["auth"] = dict(kwargs.get("headers") or {}).get("Authorization")
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b'{"experiments": []}'
            resp.url = url
            return resp

        with patch("requests.Session.request", new=fake_request):
            with mlflow_fluent_operation():
                configure_mlflow_tracking("databricks")
                mlflow.search_experiments(max_results=1)

        assert str(captured["url"]).startswith("https://profile-host.example.net/")
        assert captured["auth"] == "Bearer profile-token-value"


class TestLogJsonArtifact:
    def test_writes_and_cleans_up(self) -> None:
        """_log_json_artifact should write JSON, log it, and delete the file."""

        mock_mlflow = MagicMock()
        from haute.modelling._mlflow_log import _log_json_artifact

        _log_json_artifact(mock_mlflow, {"key": "value"}, "test", "test_dir")
        mock_mlflow.log_artifact.assert_called_once()
        logged_path = mock_mlflow.log_artifact.call_args[0][0]
        # File should have been cleaned up
        assert not Path(logged_path).exists()

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
        assert not Path(args[0][0]).exists()


# ---------------------------------------------------------------------------
# Keyword names haute hardcodes when calling MLflow
# ---------------------------------------------------------------------------

# ``(module, callable, keywords)`` haute passes literally in _mlflow_log.py.
# Most logging tests mock these calls, so this is where the installed MLflow is
# asked whether it still accepts each name. Both ``log_model`` calls use MLflow
# 3's ``name`` spelling; the native model file is logged at the run root as well
# because ``_find_model_artifact`` in _mlflow_io.py walks run artifacts.
_MLFLOW_LITERAL_KEYWORDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("mlflow", "start_run", ("run_name", "tags")),
    ("mlflow.catboost", "log_model", ("cb_model", "name", "signature")),
    ("mlflow.pyfunc", "log_model", ("name", "loader_module", "data_path", "signature")),
)


def test_keywords_haute_passes_to_mlflow_are_still_accepted() -> None:
    import inspect
    from importlib import import_module

    import mlflow

    problems: list[str] = []
    for module, name, keywords in _MLFLOW_LITERAL_KEYWORDS:
        parameters = inspect.signature(getattr(import_module(module), name)).parameters
        missing = sorted(set(keywords) - set(parameters))
        if missing:
            problems.append(f"{module}.{name}: {missing}")
    assert not problems, (
        f"The installed mlflow {mlflow.__version__} no longer accepts keyword argument(s) "
        f"haute passes literally: {problems}. Those call sites in _mlflow_log.py will raise "
        "TypeError; fix them and keep _MLFLOW_LITERAL_KEYWORDS in step."
    )


class TestLoggedModelEnvironment:
    """The logged model's environment is the interpreter that trained it."""

    def test_non_catboost_flavor_is_logged_as_the_named_model(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("MLFLOW_UV_AUTO_DETECT", raising=False)
        seen: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> None:
            # The switches must be off *while* MLflow infers the environment.
            seen["auto_detect"] = os.environ.get("MLFLOW_UV_AUTO_DETECT")
            seen["log_uv_files"] = os.environ.get("MLFLOW_LOG_UV_FILES")
            seen["kwargs"] = sorted(kwargs)

        from haute.modelling._mlflow_log import log_experiment

        with (
            _mocked_mlflow(model_card="haute.modelling._mlflow_log._log_model_card") as m,
            patch("haute.modelling._native_pyfunc.NativePyfuncModel"),
        ):
            m.pyfunc_log_model.side_effect = _capture
            log_experiment(
                experiment_name="/test/rsglm",
                candidate=_candidate(tmp_path, suffix=".rsglm", algorithm="glm"),
            )

        assert seen["kwargs"] == ["data_path", "loader_module", "name", "signature"]
        assert seen["auto_detect"] == "false"
        assert seen["log_uv_files"] == "false"
        assert "MLFLOW_UV_AUTO_DETECT" not in os.environ

    def test_logged_model_records_the_executing_interpreter_not_a_cwd_lock(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A uv.lock in the working directory never becomes the model's environment.

        MLflow 3.15 would ``uv export`` a lock found in the cwd (and log the
        lock as an artifact) instead of capturing the imported packages, so a
        stale or foreign lock would describe an environment that never
        trained the model. Against a real local file store, the recorded
        requirements pin the installed CatBoost and carry no lock artefact.
        """
        import warnings

        import catboost

        from haute._mlflow_utils import mlflow_fluent_operation
        from haute._sandbox import set_project_root

        monkeypatch.delenv("MLFLOW_UV_AUTO_DETECT", raising=False)
        monkeypatch.delenv("MLFLOW_LOG_UV_FILES", raising=False)
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        set_project_root(tmp_path)
        # A foreign uv project in the working directory, exactly the trap.
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "foreign"\nversion = "0.1.0"\ndependencies = ["bogus-package"]\n',
            encoding="utf-8",
        )
        (tmp_path / "uv.lock").write_text(
            'version = 1\n\n[[package]]\nname = "bogus-package"\nversion = "9.9.9"\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        artifacts = tmp_path / "artifacts"
        candidate = _candidate(artifacts, model_file=_real_catboost_model(artifacts))

        import mlflow

        from haute.modelling._mlflow_log import log_experiment

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = log_experiment(experiment_name="env-exp", candidate=candidate)
        assert not [w for w in caught if "artifact_path" in str(w.message)]
        assert "MLFLOW_UV_AUTO_DETECT" not in os.environ

        with mlflow_fluent_operation():
            mlflow.set_tracking_uri(result.tracking_uri)
            model_dir = Path(mlflow.artifacts.download_artifacts(f"runs:/{result.run_id}/model"))
        requirements = (model_dir / "requirements.txt").read_text(encoding="utf-8").splitlines()
        assert f"catboost=={catboost.__version__}" in requirements
        assert not [line for line in requirements if "bogus-package" in line]
        assert not (model_dir / "uv.lock").exists()
