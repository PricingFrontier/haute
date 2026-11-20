"""Haute Deploy - package a pipeline as a live scoring API.

Public API::

    from haute.deploy import deploy, DeployConfig, DeployResult

    config = DeployConfig.from_project(Path.cwd())
    result = deploy(config)
    print(result.model_uri)
"""

from haute.deploy._config import (
    AwsEcsConfig,
    AzureContainerAppsConfig,
    CIConfig,
    ContainerConfig,
    DatabricksConfig,
    DeployConfig,
    GcpRunConfig,
    ResolvedDeploy,
    SafetyConfig,
    in_resolved_project,
    resolve_config,
)
from haute.deploy._mlflow import DeployResult, deploy_to_mlflow, get_deploy_status
from haute.deploy._validators import validate_deploy

__all__ = [
    "AwsEcsConfig",
    "AzureContainerAppsConfig",
    "CIConfig",
    "ContainerConfig",
    "DatabricksConfig",
    "DeployConfig",
    "DeployResult",
    "GcpRunConfig",
    "ResolvedDeploy",
    "SafetyConfig",
    "check_credentials",
    "deploy",
    "deploy_resolved",
    "deploy_to_mlflow",
    "get_deploy_status",
    "resolve_config",
    "_SUPPORTED_TARGETS",
]


from haute.deploy._config import (
    _CONTAINER_PLATFORM_TARGETS,
    _SUPPORTED_TARGETS,
)


def check_credentials(config: DeployConfig) -> None:
    """Refuse a deploy whose target's credentials are not all set, before any other work.

    ``haute deploy`` calls it before parsing the pipeline, so a missing secret fails in
    seconds and names every gap at once. Databricks needs the MLflow destination and
    the serving pair (``_mlflow.check_databricks_credentials``); the container targets
    read theirs where they use them.
    """
    if config.target == "databricks":
        from haute.deploy._mlflow import check_databricks_credentials

        check_databricks_credentials()


@in_resolved_project
def _dispatch_resolved(resolved: ResolvedDeploy, *, keep_build: bool) -> DeployResult:
    """Ship an already validated deployment to its configured backend."""
    target = resolved.config.target

    if target == "databricks":
        return deploy_to_mlflow(resolved, keep_build=keep_build)

    if target == "container":
        from haute.deploy._container import deploy_to_container

        return deploy_to_container(resolved, keep_build=keep_build)

    if target in _CONTAINER_PLATFORM_TARGETS:
        from haute.deploy._container import deploy_to_platform_container

        return deploy_to_platform_container(resolved, keep_build=keep_build)

    raise ValueError(f"Unhandled deploy target '{target}'")  # pragma: no cover


def deploy_resolved(resolved: ResolvedDeploy, *, keep_build: bool = False) -> DeployResult:
    """Deploy a resolved deployment object without resolving or validating again.

    Callers must have already run ``validate_deploy(resolved)``. This path is
    used by ``haute deploy`` after it resolves, validates, and scores test
    quotes so the backend receives that exact same resolved object. The build's
    own directory under the project's ``.haute_build`` is removed once it has
    shipped, unless *keep_build*.
    """
    try:
        return _dispatch_resolved(resolved, keep_build=keep_build)
    finally:
        resolved.close()


def deploy(config: DeployConfig, *, keep_build: bool = False) -> DeployResult:
    """Resolve config, validate the resolved deployment, and deploy it.

    Resolution checks the settings first (``check_deploy_settings``), so a bad target
    is refused before an unrelated error from the pipeline could be.
    """
    resolved = resolve_config(config)

    try:
        validate_deploy(resolved)
        return _dispatch_resolved(resolved, keep_build=keep_build)
    finally:
        resolved.close()
