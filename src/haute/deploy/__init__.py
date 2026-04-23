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
    "deploy_to_mlflow",
    "get_deploy_status",
    "resolve_config",
]


from haute.deploy._config import _CONTAINER_BASED_TARGETS

_SUPPORTED_TARGETS = {"databricks", "container"}
_CONTAINER_PLATFORM_TARGETS = _CONTAINER_BASED_TARGETS - {"container"}
_PLANNED_TARGETS = {"sagemaker", "azure-ml"}


def deploy(config: DeployConfig) -> DeployResult:
    """Resolve config and deploy to the configured target."""
    # Validate target *before* resolve_config() so bad targets don't trigger
    # unrelated errors (e.g. "No output node found") from config resolution.
    all_known = _SUPPORTED_TARGETS | _CONTAINER_PLATFORM_TARGETS | _PLANNED_TARGETS
    if config.target not in all_known:
        raise ValueError(
            f"Unknown deploy target '{config.target}'. "
            f"Known targets: {', '.join(sorted(all_known))}."
        )
    if config.target in _PLANNED_TARGETS:
        raise NotImplementedError(
            f"Target '{config.target}' is planned but not yet implemented. "
            f"Supported targets: "
            f"{', '.join(sorted(_SUPPORTED_TARGETS | _CONTAINER_PLATFORM_TARGETS))}."
        )

    resolved = resolve_config(config)

    validate_deploy(resolved)

    if config.target == "databricks":
        return deploy_to_mlflow(resolved)

    if config.target == "container":
        from haute.deploy._container import deploy_to_container

        return deploy_to_container(resolved)

    if config.target in _CONTAINER_PLATFORM_TARGETS:
        from haute.deploy._container import deploy_to_platform_container

        return deploy_to_platform_container(resolved)

    raise ValueError(f"Unhandled deploy target '{config.target}'")  # pragma: no cover
