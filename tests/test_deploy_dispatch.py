"""Tests for deploy dispatch routing in haute.deploy.__init__.deploy().

Covers:
  - target="databricks" → calls deploy_to_mlflow
  - target="container" → calls deploy_to_container
  - target="azure-container-apps" → calls deploy_to_platform_container
  - target="sagemaker" → DeployError (planned but unimplemented), before resolution
  - target="unknown" → DeployError (unrecognised target), before resolution
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"


def _make_config(target: str = "databricks") -> MagicMock:
    """Build a minimal DeployConfig with the given target.

    For container-based targets (``container``, ``azure-container-apps``,
    ``aws-ecs``, ``gcp-run``), a patch-pinned base image is supplied so
    :meth:`DeployConfig.__post_init__` accepts the config.  Non-container
    targets ignore the field.
    """
    from haute.deploy._config import ContainerConfig, DeployConfig

    container_based = {"container", "azure-container-apps", "aws-ecs", "gcp-run"}
    return DeployConfig(
        pipeline_file=PIPELINE_FILE,
        model_name="test-model",
        target=target,
        container=(
            ContainerConfig(base_image="python:3.11.9-slim")
            if target in container_based
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
    result.build_dir = None
    return result


def _make_resolved(config: object) -> MagicMock:
    """Build a fake ResolvedDeploy that carries the config used for dispatch."""
    resolved = MagicMock()
    resolved.config = config
    return resolved


class TestDeployDispatchDatabricks:
    """target='databricks' dispatches to deploy_to_mlflow."""

    def test_databricks_calls_deploy_to_mlflow(self) -> None:
        from haute.deploy import deploy

        config = _make_config("databricks")
        fake_result = _make_deploy_result()
        fake_resolved = _make_resolved(config)

        # resolve_config and deploy_to_mlflow are imported at module level in
        # haute.deploy.__init__, so we must patch where they are used.
        with (
            patch("haute.deploy.resolve_config", return_value=fake_resolved) as mock_resolve,
            patch("haute.deploy.validate_deploy", return_value=[]),
            patch("haute.deploy.deploy_to_mlflow", return_value=fake_result) as mock_mlflow,
        ):
            result = deploy(config)

            mock_resolve.assert_called_once_with(config)
            mock_mlflow.assert_called_once_with(fake_resolved, keep_build=False)
            assert result is fake_result

    def test_deploy_resolved_dispatches_without_resolving_or_validating(self) -> None:
        import haute.deploy as deploy_mod

        config = _make_config("databricks")
        fake_result = _make_deploy_result()
        fake_resolved = _make_resolved(config)

        with (
            patch(
                "haute.deploy.resolve_config",
                side_effect=AssertionError("resolved deploy must not be resolved again"),
            ),
            patch(
                "haute.deploy.validate_deploy",
                side_effect=AssertionError("resolved deploy must not be validated again"),
            ),
            patch("haute.deploy.deploy_to_mlflow", return_value=fake_result) as mock_mlflow,
        ):
            result = deploy_mod.deploy_resolved(fake_resolved)

        mock_mlflow.assert_called_once_with(fake_resolved, keep_build=False)
        assert result is fake_result


class TestDeployDispatchContainer:
    """target='container' dispatches to deploy_to_container."""

    def test_container_calls_deploy_to_container(self) -> None:
        from haute.deploy import deploy

        config = _make_config("container")
        fake_result = _make_deploy_result()
        fake_resolved = _make_resolved(config)

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
            mock_container.assert_called_once_with(fake_resolved, keep_build=False)
            assert result is fake_result


class TestDeployDispatchPlatformContainer:
    """target='azure-container-apps' dispatches to deploy_to_platform_container."""

    def test_azure_container_apps_calls_deploy_to_platform_container(self) -> None:
        from haute.deploy import deploy

        config = _make_config("azure-container-apps")
        fake_result = _make_deploy_result()
        fake_resolved = _make_resolved(config)

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
            mock_platform.assert_called_once_with(fake_resolved, keep_build=False)
            assert result is fake_result


class TestDeployDispatchPlanned:
    """Planned but unimplemented targets are refused before the pipeline is read."""

    def test_sagemaker_is_refused_as_planned(self) -> None:
        from haute.deploy import deploy
        from haute.errors import DeployError

        config = _make_config("sagemaker")

        with (
            patch("haute.parser.parse_pipeline_file") as parse,
            pytest.raises(DeployError, match="planned but not yet implemented"),
        ):
            deploy(config)
        parse.assert_not_called()


class TestDeployDispatchUnknown:
    """Completely unknown targets are refused before the pipeline is read."""

    def test_unknown_target_is_refused_naming_the_targets(self) -> None:
        from haute.deploy import deploy
        from haute.errors import DeployError

        config = _make_config("unknown-target")

        with (
            patch("haute.parser.parse_pipeline_file") as parse,
            pytest.raises(DeployError, match="'unknown-target' is not a deploy target") as caught,
        ):
            deploy(config)
        parse.assert_not_called()
        assert "aws-ecs, azure-container-apps, container, databricks, gcp-run" in str(caught.value)


class TestDeployDispatchReturnValue:
    """Verify deploy() returns the result from the backend, not a wrapper."""

    def test_return_value_has_expected_attributes(self) -> None:
        from haute.deploy import deploy

        config = _make_config("databricks")
        fake_result = _make_deploy_result()
        fake_resolved = _make_resolved(config)

        with (
            patch("haute.deploy.resolve_config", return_value=fake_resolved),
            patch("haute.deploy.validate_deploy", return_value=[]),
            patch("haute.deploy.deploy_to_mlflow", return_value=fake_result),
        ):
            result = deploy(config)

        assert result.model_name == "test-model"
        assert result.model_version == 1
        assert result.model_uri == "models:/test-model/1"
        assert result.build_dir is None

    def test_deploy_releases_resolved_resources_after_success(self) -> None:
        from haute.deploy import deploy

        config = _make_config("databricks")
        fake_resolved = _make_resolved(config)
        with (
            patch("haute.deploy.resolve_config", return_value=fake_resolved),
            patch("haute.deploy.validate_deploy"),
            patch("haute.deploy.deploy_to_mlflow", return_value=_make_deploy_result()),
        ):
            deploy(config)

        fake_resolved.close.assert_called_once_with()

    def test_deploy_releases_resolved_resources_after_validation_failure(self) -> None:
        from haute.deploy import deploy

        config = _make_config("databricks")
        fake_resolved = _make_resolved(config)
        with (
            patch("haute.deploy.resolve_config", return_value=fake_resolved),
            patch("haute.deploy.validate_deploy", side_effect=RuntimeError("invalid")),
        ):
            with pytest.raises(RuntimeError, match="invalid"):
                deploy(config)

        fake_resolved.close.assert_called_once_with()

    def test_deploy_resolved_releases_resources_after_backend_failure(self) -> None:
        from haute.deploy import deploy_resolved

        config = _make_config("databricks")
        fake_resolved = _make_resolved(config)
        with patch(
            "haute.deploy.deploy_to_mlflow",
            side_effect=RuntimeError("ship failed"),
        ):
            with pytest.raises(RuntimeError, match="ship failed"):
                deploy_resolved(fake_resolved)

        fake_resolved.close.assert_called_once_with()

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
