"""Coverage gaps for haute.deploy._mlflow.deploy_to_mlflow().

Targets the orchestration branches not exercised by test_deploy.py /
test_deploy_internals.py: the progress callback body, the artifact-copy loop,
the registered-version selection when versions exist, and the BaseException
cleanup that removes the .haute_build directory.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from tests._deploy_helpers import make_resolved_deploy as _make_resolved

if TYPE_CHECKING:
    pass


@pytest.fixture()
def deploy_pipeline_file(tmp_path: Path) -> Path:
    """Scratch pipeline path for mocked deploy tests that write build artifacts."""
    pipeline_file = tmp_path / "pipeline.py"
    pipeline_file.write_text("# mocked deploy pipeline\n", encoding="utf-8")
    return pipeline_file


@dataclass
class MLflowMocks:
    """Named references to every mock in the MLflow deploy patch block."""

    set_tracking_uri: MagicMock
    set_registry_uri: MagicMock
    set_experiment: MagicMock
    start_run: MagicMock
    log_dict: MagicMock
    log_model: MagicMock
    client: MagicMock
    check_connectivity: MagicMock
    build_signature: MagicMock
    create_or_update_endpoint: MagicMock


@contextmanager
def mock_mlflow_deploy():
    """Patch the MLflow/deploy targets used by deploy_to_mlflow()."""
    with (
        patch("mlflow.set_tracking_uri") as m_tracking,
        patch("mlflow.set_registry_uri") as m_registry,
        patch("mlflow.set_experiment") as m_experiment,
        patch("mlflow.start_run") as m_run,
        patch("mlflow.log_dict") as m_log_dict,
        patch("mlflow.pyfunc.log_model") as m_log_model,
        patch("mlflow.tracking.MlflowClient") as m_client,
        patch("haute.deploy._mlflow._check_databricks_connectivity") as m_conn,
        patch("haute.deploy._mlflow._build_signature") as m_sig,
        patch("haute.deploy._mlflow._create_or_update_serving_endpoint") as m_ep,
    ):
        m_client.return_value.search_model_versions.return_value = []
        m_run.return_value.__enter__ = MagicMock()
        m_run.return_value.__exit__ = MagicMock(return_value=False)

        yield MLflowMocks(
            set_tracking_uri=m_tracking,
            set_registry_uri=m_registry,
            set_experiment=m_experiment,
            start_run=m_run,
            log_dict=m_log_dict,
            log_model=m_log_model,
            client=m_client,
            check_connectivity=m_conn,
            build_signature=m_sig,
            create_or_update_endpoint=m_ep,
        )


class TestProgressCallback:
    """The optional progress callback (line 76) must receive step messages."""

    def test_progress_callback_receives_messages(self, deploy_pipeline_file: Path) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
        messages: list[str] = []

        with mock_mlflow_deploy():
            deploy_to_mlflow(resolved, progress=messages.append)

        # The callback body (the guarded `progress(msg)`) must have run.
        assert messages
        assert any("Databricks" in m for m in messages)

    def test_no_progress_callback_does_not_raise(self, deploy_pipeline_file: Path) -> None:
        """When progress is None the guard short-circuits and no error occurs."""
        from haute.deploy._mlflow import DeployResult, deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)

        with mock_mlflow_deploy():
            result = deploy_to_mlflow(resolved, progress=None)

        assert isinstance(result, DeployResult)


class TestArtifactCopy:
    """Resolved artifacts (line 109) must be merged into the log_model artifacts."""

    def test_resolved_artifacts_are_passed_to_log_model(
        self, deploy_pipeline_file: Path, tmp_path: Path
    ) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        artifact_file = tmp_path / "model.cbm"
        artifact_file.write_text("artifact", encoding="utf-8")
        resolved = _make_resolved(
            pipeline_file=deploy_pipeline_file,
            artifacts={"my_model": artifact_file},
        )

        with mock_mlflow_deploy() as mocks:
            deploy_to_mlflow(resolved)

        artifacts = mocks.log_model.call_args.kwargs["artifacts"]
        # The manifest is always present; the resolved artifact must be added too.
        assert artifacts["my_model"] == str(artifact_file)
        assert "deploy_manifest" in artifacts


class TestVersionSelection:
    """When the registry already has versions (line 138), the max is selected."""

    def test_latest_version_is_max_of_existing(self, deploy_pipeline_file: Path) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)

        v1 = MagicMock()
        v1.version = "1"
        v3 = MagicMock()
        v3.version = "3"
        v2 = MagicMock()
        v2.version = "2"

        with (
            mock_mlflow_deploy() as mocks,
            patch("haute.deploy._mlflow.search_versions", return_value=[v1, v3, v2]),
        ):
            result = deploy_to_mlflow(resolved)

        assert result.model_version == 3
        assert result.model_uri.endswith("/3")
        # Endpoint creation must receive the same resolved version.
        assert mocks.create_or_update_endpoint.call_args.kwargs["model_version"] == 3

    def test_no_versions_defaults_to_one(self, deploy_pipeline_file: Path) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)

        with (
            mock_mlflow_deploy(),
            patch("haute.deploy._mlflow.search_versions", return_value=[]),
        ):
            result = deploy_to_mlflow(resolved)

        assert result.model_version == 1
        assert result.model_uri.endswith("/1")


class TestBuildDirCleanup:
    """A failure mid-deploy (lines 168-172) must remove the .haute_build dir."""

    def test_failure_removes_build_dir(self, deploy_pipeline_file: Path) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
        build_dir = deploy_pipeline_file.resolve().parent / ".haute_build"

        with mock_mlflow_deploy() as mocks:
            mocks.log_model.side_effect = RuntimeError("boom")
            with pytest.raises(RuntimeError, match="boom"):
                deploy_to_mlflow(resolved)

        # The except BaseException handler must have torn the build dir down.
        assert not build_dir.exists()

    def test_success_keeps_build_dir(self, deploy_pipeline_file: Path) -> None:
        """The cleanup is failure-only: a clean run leaves the manifest on disk."""
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
        build_dir = deploy_pipeline_file.resolve().parent / ".haute_build"

        with mock_mlflow_deploy():
            result = deploy_to_mlflow(resolved)

        assert build_dir.exists()
        assert result.manifest_path.is_file()
