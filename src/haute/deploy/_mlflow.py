"""MLflow deployment target - log model + create Databricks serving endpoint."""

from __future__ import annotations

import importlib.resources
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from haute._logging import get_logger
from haute._mlflow_utils import search_versions
from haute.deploy._config import ResolvedDeploy
from haute.deploy._utils import build_manifest
from haute.errors import DeployError

logger = get_logger(component="deploy.mlflow")

# Resolve the path to the models-from-code script shipped with the package
_MODEL_CODE_PATH = str(importlib.resources.files("haute.deploy") / "_model_code.py")

if TYPE_CHECKING:
    from haute.deploy._config import DeployConfig


@dataclass
class DeployResult:
    """Returned by deploy_to_mlflow()."""

    model_name: str
    model_version: int
    model_uri: str
    endpoint_url: str | None
    manifest_path: Path


def build_uc_model_name(config: DeployConfig) -> str:
    """Build the Unity Catalog three-level namespace model name.

    Format: ``catalog.schema.model_name[-suffix]``
    """
    effective = config.model_name + (config.endpoint_suffix or "")
    return f"{config.databricks.catalog}.{config.databricks.schema}.{effective}"


def build_experiment_name(config: DeployConfig) -> str:
    """Build the MLflow experiment name, appending suffix if present."""
    name = config.databricks.experiment_name
    if config.endpoint_suffix:
        name = name + config.endpoint_suffix
    return name


def deploy_to_mlflow(
    resolved: ResolvedDeploy,
    progress: Callable[[str], None] | None = None,
) -> DeployResult:
    """Deploy a resolved pipeline to MLflow + Databricks Model Serving.

    Steps:
        1. Build deployment manifest JSON
        2. Log HauteModel as mlflow.pyfunc with artifacts + signature
        3. Register model version in MLflow Model Registry
        4. Return DeployResult with model URI and endpoint URL

    Args:
        resolved: Fully resolved deployment config (from ``resolve_config()``).
        progress: Optional callback for step-by-step progress messages.

    Returns:
        DeployResult with model URI, version, and endpoint URL.
    """

    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    import mlflow

    config = resolved.config
    model_name = config.model_name
    logger.info("deploy_started", model_name=model_name, target="mlflow")

    # Point MLflow at the Databricks workspace (uses DATABRICKS_RATING_HOST/TOKEN env vars)
    _log("Connecting to Databricks MLflow...")
    _check_databricks_connectivity(_log)
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")

    # Use Unity Catalog three-level namespace: catalog.schema.model_name
    uc_model_name = build_uc_model_name(config)

    # 1. Build deployment manifest
    manifest = build_manifest(resolved)

    # 2. Write manifest to a persistent location (not a temp dir that gets deleted)
    build_dir = config.pipeline_file.resolve().parent / ".haute_build"
    build_dir.mkdir(exist_ok=True)

    try:
        manifest_path = build_dir / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        # 3. Build artifact dict for mlflow.pyfunc.log_model
        artifacts: dict[str, str] = {
            "deploy_manifest": str(manifest_path),
        }
        for artifact_name, artifact_path in resolved.artifacts.items():
            artifacts[artifact_name] = str(artifact_path)

        # 4. Build MLflow model signature
        signature = _build_signature(resolved)

        # 5. Set experiment - append endpoint suffix for staging isolation
        experiment_name = build_experiment_name(config)
        _log(f"Setting experiment: {experiment_name}")
        mlflow.set_experiment(experiment_name)

        # 6. Log the model
        _log("Logging model to MLflow (this may take a minute)...")
        with mlflow.start_run(run_name=f"deploy-{model_name}"):
            mlflow.log_dict(manifest, "deploy_manifest.json")

            mlflow.pyfunc.log_model(
                name="model",
                python_model=_MODEL_CODE_PATH,
                artifacts=artifacts,
                signature=signature,
                conda_env=_conda_env(resolved),
                registered_model_name=uc_model_name,
            )

        _log(f"Model logged. Fetching registered version for {uc_model_name}...")
        # 7. Get the registered model version
        client = mlflow.tracking.MlflowClient()
        versions = search_versions(client, uc_model_name)
        if not versions:
            raise DeployError(
                "MLflow did not return a registered model version after logging. "
                "Refusing to deploy a synthetic version.",
                registered_model=uc_model_name,
            )
        latest_version = max(versions, key=lambda v: int(v.version)).version

        model_uri = f"models:/{uc_model_name}/{latest_version}"

        _log(f"Model URI: {model_uri}")

        # 8. Create or update the serving endpoint
        _log(f"Creating/updating serving endpoint: {config.effective_endpoint_name}...")
        endpoint_url = _create_or_update_serving_endpoint(
            config=config,
            uc_model_name=uc_model_name,
            model_version=int(latest_version),
        )

        logger.info(
            "deploy_completed",
            model_name=model_name,
            model_uri=model_uri,
            version=int(latest_version),
            endpoint=config.effective_endpoint_name,
        )
        return DeployResult(
            model_name=model_name,
            model_version=int(latest_version),
            model_uri=model_uri,
            endpoint_url=endpoint_url,
            manifest_path=manifest_path,
        )
    except BaseException:
        import shutil

        shutil.rmtree(build_dir, ignore_errors=True)
        raise


def get_deploy_status(
    model_name: str,
    catalog: str = "main",
    schema: str = "default",
) -> dict[str, str | int]:
    """Query MLflow Model Registry for current model versions and status.

    Args:
        model_name: Short model name (e.g. ``"motor-pricing"``).
        catalog: Unity Catalog catalog name.
        schema: Unity Catalog schema name.

    Returns:
        Dict with keys: model_name, latest_version, latest_stage, status.
    """
    import mlflow

    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")

    uc_model_name = f"{catalog}.{schema}.{model_name}"
    client = mlflow.tracking.MlflowClient()
    versions = search_versions(client, uc_model_name)

    if not versions:
        return {
            "model_name": model_name,
            "latest_version": 0,
            "status": "not_found",
        }

    latest = max(versions, key=lambda v: int(v.version))
    return {
        "model_name": model_name,
        "latest_version": int(latest.version),
        "latest_stage": getattr(latest, "current_stage", "None"),
        "status": latest.status,
        "run_id": latest.run_id or "",
    }


def _build_signature(resolved: ResolvedDeploy) -> object:
    """Build an MLflow ModelSignature from resolved schemas."""
    from mlflow.models import ModelSignature
    from mlflow.types import ColSpec, DataType, Schema

    dtype_map = {
        "Int8": DataType.integer,
        "Int16": DataType.integer,
        "Int32": DataType.integer,
        "Int64": DataType.long,
        "UInt8": DataType.integer,
        "UInt16": DataType.integer,
        "UInt32": DataType.long,
        "UInt64": DataType.long,
        "Float32": DataType.float,
        "Float64": DataType.double,
        "String": DataType.string,
        "Utf8": DataType.string,
        "Boolean": DataType.boolean,
        "Date": DataType.datetime,
        "Datetime": DataType.datetime,
    }

    def _to_colspecs(schema: dict[str, str]) -> list[ColSpec]:
        specs = []
        for col_name, dtype_str in schema.items():
            # Handle parameterized types like Datetime('us', 'UTC')
            base_type = dtype_str.split("(")[0] if "(" in dtype_str else dtype_str
            try:
                mlflow_type = dtype_map[base_type]
            except KeyError as exc:
                raise DeployError(
                    f"Cannot build an MLflow signature for column {col_name!r}: "
                    f"unsupported Polars dtype {dtype_str!r}.",
                    column=col_name,
                    dtype=dtype_str,
                ) from exc
            specs.append(ColSpec(type=mlflow_type, name=col_name))
        return specs

    input_schema = Schema(_to_colspecs(resolved.input_schema))  # type: ignore[arg-type]
    output_schema = Schema(_to_colspecs(resolved.output_schema))  # type: ignore[arg-type]
    return ModelSignature(inputs=input_schema, outputs=output_schema)


def _pip_requirements(resolved: ResolvedDeploy) -> list[str]:
    """Build pip requirements for the deployed model."""
    import haute

    reqs = [
        f"haute=={haute.__version__}",
        "polars>=1.39.2",
    ]

    # Check if catboost is used
    for node in resolved.pruned_graph.nodes:
        if node.data.config.get("fileType") == "catboost":
            reqs.append("catboost>=1.2.8")
            break

    return reqs


# Databricks Model Serving uses conda to build the container.
# Pin Python to 3.11 which is widely available on Databricks' internal
# conda channel, regardless of which Python the CI runner uses.
_SERVING_PYTHON_VERSION = "3.11.11"


def _conda_env(resolved: ResolvedDeploy) -> dict:
    """Build a conda environment dict for Databricks Model Serving.

    Pins Python to a version available on Databricks' internal conda
    channel instead of letting MLflow auto-detect the host Python
    (which may be 3.13+ and unavailable on Databricks).
    """
    return {
        "channels": ["conda-forge"],
        "dependencies": [
            f"python={_SERVING_PYTHON_VERSION}",
            "pip",
            {"pip": _pip_requirements(resolved)},
        ],
        "name": "mlflow-env",
    }


def _create_or_update_serving_endpoint(
    config: DeployConfig,
    uc_model_name: str,
    model_version: int,
) -> str | None:
    """Create or update a Databricks Model Serving endpoint.

    Uses the Databricks SDK to create an endpoint if it doesn't exist,
    or update the served model version if it does.

    Args:
        config: The deployment configuration (provides endpoint_name and
                serving settings from haute.toml).
        uc_model_name: The Unity Catalog three-level model name
                       (e.g. ``"workspace.default.haute-test"``).
        model_version: The model version number to serve.

    Returns:
        The endpoint invocation URL, or ``None`` if endpoint_name is not
        configured.
    """
    import os

    endpoint_name = config.effective_endpoint_name
    if not endpoint_name:
        return None

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.serving import (
        EndpointCoreConfigInput,
        ServedEntityInput,
    )

    host = os.environ.get("DATABRICKS_RATING_HOST", "").rstrip("/")

    ws = WorkspaceClient(
        host=os.environ.get("DATABRICKS_RATING_HOST", ""),
        token=os.environ.get("DATABRICKS_RATING_TOKEN", ""),
    )

    served_entity = ServedEntityInput(
        entity_name=uc_model_name,
        entity_version=str(model_version),
        workload_size=config.databricks.serving_workload_size,
        scale_to_zero_enabled=config.databricks.serving_scale_to_zero,
    )

    try:
        ws.serving_endpoints.get(endpoint_name)
        # Endpoint exists - update the served model version
        ws.serving_endpoints.update_config(
            name=endpoint_name,
            served_entities=[served_entity],
        )
    except NotFound:
        # Endpoint doesn't exist - create it
        ws.serving_endpoints.create(
            name=endpoint_name,
            config=EndpointCoreConfigInput(
                name=endpoint_name,
                served_entities=[served_entity],
            ),
        )

    return f"{host}/serving-endpoints/{endpoint_name}/invocations"


def _check_databricks_connectivity(
    _log: Callable[[str], None],
    timeout: int = 10,
) -> None:
    """Verify the Databricks workspace is reachable before slow MLflow calls.

    Makes a lightweight HTTP GET to DATABRICKS_HOST with a short timeout so CI
    fails fast with a clear error instead of hanging forever.
    """
    import os
    import urllib.request

    host = os.environ.get("DATABRICKS_RATING_HOST", "")
    token = os.environ.get("DATABRICKS_RATING_TOKEN", "")

    if not host:
        raise RuntimeError(
            "DATABRICKS_RATING_HOST is not set. Add it to .env or set it as a CI secret."
        )
    if not token:
        raise RuntimeError(
            "DATABRICKS_RATING_TOKEN is not set. Add it to .env or set it as a CI secret."
        )

    url = f"{host.rstrip('/')}/api/2.0/clusters/list-zones"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        _log("Databricks workspace reachable")
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError(
                f"Databricks returned 403 Forbidden. "
                f"Check that your DATABRICKS_RATING_TOKEN is valid and has workspace access. "
                f"Host: {host}"
            ) from exc
        # Other HTTP errors (e.g. 404) are fine - it means the host is reachable
        _log("Databricks workspace reachable")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Cannot reach Databricks workspace at {host} "
            f"(timed out after {timeout}s). Check DATABRICKS_RATING_HOST is correct "
            f"and that the workspace allows connections from this network "
            f"(e.g. GitHub Actions IP ranges may need allowlisting)."
        ) from exc
