"""Tests for deploy dispatch routing in haute.deploy.__init__.deploy().

Covers:
  - target="databricks" → calls deploy_to_mlflow
  - target="container" → calls deploy_to_container
  - target="azure-container-apps" → calls deploy_to_platform_container
  - target="sagemaker" → NotImplementedError (planned but unimplemented)
  - target="unknown" → ValueError (unrecognised target)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURE_DIR = Path("tests/fixtures")
PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"


def _make_config(target: str = "databricks") -> MagicMock:
    """Build a minimal DeployConfig with the given target.

    When ``target == "container"``, a patch-pinned base image is supplied so
    :meth:`DeployConfig.__post_init__` accepts the config.  Non-container
    targets ignore the field.
    """
    from haute.deploy._config import ContainerConfig, DeployConfig

    return DeployConfig(
        pipeline_file=PIPELINE_FILE,
        model_name="test-model",
        target=target,
        container=(
            ContainerConfig(base_image="python:3.11.9-slim")
            if target == "container"
            else ContainerConfig()
        ),
    )


def _make_deploy_result() -> MagicMock:
    """Build a fake DeployResult to be returned by mocked deploy functions."""
    result = MagicMock()
    result.model_name = "test-model"
    result.model_version = 1
    result.model_uri = "models:/test-model/1"
    result.endpoint_url = None
    result.manifest_path = Path("/tmp/deploy_manifest.json")
    return result


class TestDeployDispatchDatabricks:
    """target='databricks' dispatches to deploy_to_mlflow."""

    def test_databricks_calls_deploy_to_mlflow(self) -> None:
        from haute.deploy import deploy

        config = _make_config("databricks")
        fake_result = _make_deploy_result()
        fake_resolved = MagicMock()

        # resolve_config and deploy_to_mlflow are imported at module level in
        # haute.deploy.__init__, so we must patch where they are used.
        with (
            patch("haute.deploy.resolve_config", return_value=fake_resolved) as mock_resolve,
            patch("haute.deploy.validate_deploy", return_value=[]),
            patch("haute.deploy.deploy_to_mlflow", return_value=fake_result) as mock_mlflow,
        ):
            result = deploy(config)

            mock_resolve.assert_called_once_with(config)
            mock_mlflow.assert_called_once_with(fake_resolved)
            assert result is fake_result


class TestDeployDispatchContainer:
    """target='container' dispatches to deploy_to_container."""

    def test_container_calls_deploy_to_container(self) -> None:
        from haute.deploy import deploy

        config = _make_config("container")
        fake_result = _make_deploy_result()
        fake_resolved = MagicMock()

        # resolve_config is imported at module level; deploy_to_container
        # is lazily imported inside the function → patch at source module.
        with (
            patch("haute.deploy.resolve_config", return_value=fake_resolved) as mock_resolve,
            patch("haute.deploy.validate_deploy", return_value=[]),
            patch(
                "haute.deploy._container.deploy_to_container",
                return_value=fake_result,
            ) as mock_container,
        ):
            result = deploy(config)

            mock_resolve.assert_called_once_with(config)
            mock_container.assert_called_once_with(fake_resolved)
            assert result is fake_result


class TestDeployDispatchPlatformContainer:
    """target='azure-container-apps' dispatches to deploy_to_platform_container."""

    def test_azure_container_apps_calls_deploy_to_platform_container(self) -> None:
        from haute.deploy import deploy

        config = _make_config("azure-container-apps")
        fake_result = _make_deploy_result()
        fake_resolved = MagicMock()

        with (
            patch("haute.deploy.resolve_config", return_value=fake_resolved) as mock_resolve,
            patch("haute.deploy.validate_deploy", return_value=[]),
            patch(
                "haute.deploy._container.deploy_to_platform_container",
                return_value=fake_result,
            ) as mock_platform,
        ):
            result = deploy(config)

            mock_resolve.assert_called_once_with(config)
            mock_platform.assert_called_once_with(fake_resolved)
            assert result is fake_result


class TestDeployDispatchPlanned:
    """Planned but unimplemented targets raise NotImplementedError."""

    def test_sagemaker_raises_not_implemented(self) -> None:
        from haute.deploy import deploy

        config = _make_config("sagemaker")

        with pytest.raises(NotImplementedError, match="planned but not yet implemented"):
            deploy(config)


class TestDeployDispatchUnknown:
    """Completely unknown targets raise ValueError."""

    def test_unknown_target_raises_value_error(self) -> None:
        from haute.deploy import deploy

        config = _make_config("unknown-target")

        with pytest.raises(ValueError, match="Unknown deploy target"):
            deploy(config)


class TestDeployDispatchReturnValue:
    """Verify deploy() returns the result from the backend, not a wrapper."""

    def test_return_value_has_expected_attributes(self) -> None:
        from haute.deploy import deploy

        config = _make_config("databricks")
        fake_result = _make_deploy_result()
        fake_resolved = MagicMock()

        with (
            patch("haute.deploy.resolve_config", return_value=fake_resolved),
            patch("haute.deploy.validate_deploy", return_value=[]),
            patch("haute.deploy.deploy_to_mlflow", return_value=fake_result),
        ):
            result = deploy(config)

        assert result.model_name == "test-model"
        assert result.model_version == 1
        assert result.model_uri == "models:/test-model/1"
        assert result.manifest_path == Path("/tmp/deploy_manifest.json")

    def test_resolve_config_failure_propagates(self) -> None:
        """If resolve_config raises, deploy() must not swallow it."""
        from haute.deploy import deploy

        config = _make_config("databricks")

        with patch(
            "haute.deploy.resolve_config",
            side_effect=ValueError("No source nodes found"),
        ):
            with pytest.raises(ValueError, match="No source nodes"):
                deploy(config)
