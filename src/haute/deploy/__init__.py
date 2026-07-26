"""Haute Deploy - package a pipeline as a live scoring API.

Public API::

    from haute.deploy import deploy, DeployConfig, DeployResult

    config = DeployConfig.from_toml(Path("haute.toml"))
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
    "deploy",
    "deploy_resolved",
    "deploy_to_mlflow",
    "get_deploy_status",
    "resolve_config",
]


from haute.deploy._config import _CONTAINER_BASED_TARGETS

_SUPPORTED_TARGETS = {"databricks", "container"}
_CONTAINER_PLATFORM_TARGETS = _CONTAINER_BASED_TARGETS - {"container"}
_PLANNED_TARGETS = {"sagemaker", "azure-ml"}


def _validate_target(target: str) -> None:
    """Reject unknown or planned deploy targets before backend dispatch."""
    all_known = _SUPPORTED_TARGETS | _CONTAINER_PLATFORM_TARGETS | _PLANNED_TARGETS
    if target not in all_known:
        raise ValueError(
            f"Unknown deploy target '{target}'. Known targets: {', '.join(sorted(all_known))}."
        )
    if target in _PLANNED_TARGETS:
        raise NotImplementedError(
            f"Target '{target}' is planned but not yet implemented. "
            f"Supported targets: "
            f"{', '.join(sorted(_SUPPORTED_TARGETS | _CONTAINER_PLATFORM_TARGETS))}."
        )


def _dispatch_resolved(resolved: ResolvedDeploy) -> DeployResult:
    """Ship an already validated deployment to its configured backend."""
    target = resolved.config.target

    if target == "databricks":
        return deploy_to_mlflow(resolved)

    if target == "container":
        from haute.deploy._container import deploy_to_container

        return deploy_to_container(resolved)

    if target in _CONTAINER_PLATFORM_TARGETS:
        from haute.deploy._container import deploy_to_platform_container

        return deploy_to_platform_container(resolved)

    raise ValueError(f"Unhandled deploy target '{target}'")  # pragma: no cover


def deploy_resolved(resolved: ResolvedDeploy) -> DeployResult:
    """Deploy a resolved deployment object without resolving or validating again.

    Callers must have already run ``validate_deploy(resolved)``. This path is
    used by ``haute deploy`` after it resolves, validates, and scores test
    quotes so the backend receives that exact same resolved object.
    """
    try:
        _validate_target(resolved.config.target)
        return _dispatch_resolved(resolved)
    finally:
        resolved.close()


def deploy(config: DeployConfig) -> DeployResult:
    """Resolve config, validate the resolved deployment, and deploy it."""
    # Validate target *before* resolve_config() so bad targets don't trigger
    # unrelated errors (e.g. "No output node found") from config resolution.
    _validate_target(config.target)

    resolved = resolve_config(config)

    try:
        validate_deploy(resolved)
        return _dispatch_resolved(resolved)
    finally:
        resolved.close()
