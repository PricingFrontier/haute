"""MLflow deployment target - log model + create Databricks serving endpoint."""

from __future__ import annotations

import importlib.resources
import json
import shutil
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from haute._logging import get_logger
from haute._mlflow_utils import (
    ensure_experiment,
    mlflow_fluent_operation,
    registry_uri_for_tracking,
    search_versions,
    set_tracking_uri_preserving_env,
)
from haute._polars_dtypes import (
    holds_wide_integer,
    parse_rendered_dtype,
    rendered_dtype_mlflow_type_name,
    wide_integer_message,
)
from haute._polars_environment import polars_process_environment, polars_spill_root
from haute._project_python import deployed_python_version
from haute.deploy._config import ResolvedDeploy
from haute.deploy._databricks_serving import (
    ServingEndpointError,
    endpoint_update_state,
    missing_serving_credentials,
    missing_serving_credentials_message,
    pat_workspace_client,
    serving_credentials,
    serving_workspace_client,
    wait_for_endpoint,
)
from haute.deploy._request_rows import copy_verified
from haute.deploy._served_haute import ServedHaute, served_haute
from haute.deploy._utils import build_manifest, model_source_line
from haute.errors import DeployError, SchemaMismatchError

logger = get_logger(component="deploy.mlflow")

# Resolve the path to the models-from-code script shipped with the package
_MODEL_CODE_PATH = str(importlib.resources.files("haute.deploy") / "_model_code.py")

# One MLflow deploy at a time in this process: deploys share the pipeline's
# ``.haute_build`` directory, and each picks the version it registered by
# searching the registry after logging.
_DEPLOY_LOCK = threading.Lock()

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
        4. Create or update the serving endpoint and wait until it serves that version
        5. Return DeployResult with model URI and endpoint URL

    Args:
        resolved: Fully resolved deployment config (from ``resolve_config()``).
        progress: Optional callback for step-by-step progress messages.

    Returns:
        DeployResult with model URI, version, and endpoint URL.

    The experiment, the run, the manifest artifact, the run's terminal status and
    the version lookup go through a client bound to the MLflow destination; only
    ``mlflow.pyfunc.log_model`` (with its registration) holds MLflow's
    process-global state, inside :func:`mlflow_fluent_operation`. Deploys run one
    at a time in the process, because they share the pipeline's build directory
    and read their registered version back from the registry.
    """
    with _DEPLOY_LOCK:
        return _deploy_to_mlflow(resolved, progress)


def _deploy_to_mlflow(
    resolved: ResolvedDeploy,
    progress: Callable[[str], None] | None,
) -> DeployResult:
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    import mlflow
    from mlflow.tracking import MlflowClient

    config = resolved.config
    model_name = config.model_name
    logger.info("deploy_started", model_name=model_name, target="mlflow")

    # MLflow credentials first, before any request: the MLflow pair or a selected
    # profile (never the data-access pair). The connectivity pre-check and the
    # serving endpoint use the rating pair.
    tracking_uri, registry_uri = _resolve_mlflow_databricks()
    _log("Connecting to Databricks MLflow...")
    _check_databricks_connectivity(_log)
    _check_registry_schema(config, registry_uri)
    client = MlflowClient(tracking_uri=tracking_uri, registry_uri=registry_uri)

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

        # 3. The model to log: code, artefacts, project modules, signature, environment.
        # Its artefacts are staged under the digests resolution recorded, so MLflow
        # copies the bytes the build checked.
        staging = Path(tempfile.mkdtemp(prefix="haute_mlflow_artifacts_", dir=build_dir))
        haute = served_haute(staging / "haute-wheel")
        _log(f"Served model installs {haute.requirement}")
        model_arguments = _pyfunc_model_arguments(
            resolved, manifest_path, _stage_artifacts(resolved, staging), haute
        )

        for node_id, source in resolved.model_sources.items():
            _log(model_source_line(node_id, source))

        # 4. Set experiment - append endpoint suffix for staging isolation
        experiment_name = build_experiment_name(config)
        _log(f"Setting experiment: {experiment_name}")
        experiment_id = ensure_experiment(client, tracking_uri, experiment_name)

        # 5. Log the model
        _log("Logging model to MLflow (this may take a minute)...")
        run_id = client.create_run(experiment_id, run_name=f"deploy-{model_name}").info.run_id
        status = "FAILED"
        try:
            client.log_dict(run_id, manifest, "deploy_manifest.json")
            # The one fluent call: pyfunc.log_model resolves the global tracking
            # URI and the thread's active run.
            with mlflow_fluent_operation():
                set_tracking_uri_preserving_env(mlflow, tracking_uri)
                mlflow.set_registry_uri(registry_uri)
                with mlflow.start_run(run_id=run_id):
                    mlflow.pyfunc.log_model(
                        name="model",
                        registered_model_name=uc_model_name,
                        **model_arguments,
                    )
            status = "FINISHED"
        finally:
            client.set_terminated(run_id, status)
            shutil.rmtree(staging, ignore_errors=True)

        _log(f"Model logged. Fetching registered version for {uc_model_name}...")
        # 6. Get the registered model version
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

        # 7. Create or update the serving endpoint, and wait until it serves the version
        endpoint_url = _create_or_update_serving_endpoint(
            config=config,
            uc_model_name=uc_model_name,
            model_version=int(latest_version),
            progress=_log,
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
    tracking_uri, registry_uri = _resolve_mlflow_databricks()

    import mlflow

    uc_model_name = f"{catalog}.{schema}.{model_name}"
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri, registry_uri=registry_uri)
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


def _stage_artifacts(resolved: ResolvedDeploy, staging: Path) -> dict[str, Path]:
    """Copy each bundled artefact into *staging* under the digest resolution recorded."""
    staged: dict[str, Path] = {}
    for artifact_name, artifact_path in resolved.artifacts.items():
        dest = staging / artifact_name
        copy_verified(
            artifact_path,
            dest,
            digest=resolved.artifact_digests[artifact_name],
            name=artifact_name,
        )
        staged[artifact_name] = dest
    return staged


def _pyfunc_model_arguments(
    resolved: ResolvedDeploy,
    manifest_path: Path,
    staged_artifacts: Mapping[str, Path],
    haute: ServedHaute,
) -> dict[str, Any]:
    """The pyfunc model a deploy logs: code, artefacts, project modules, signature, env.

    *staged_artifacts* are the bundled artefacts as ``_stage_artifacts`` copied them.
    The model's code paths are the bundled ``utility`` package and, when *haute* ships
    one, the haute wheel. MLflow copies each under the model's ``code/`` directory and
    puts that directory on ``sys.path`` when the model loads, so the served preamble
    imports the validated files, and the environment installs the wheel from there.
    """
    artifacts: dict[str, str] = {"deploy_manifest": str(manifest_path)}
    for artifact_name, artifact_path in staged_artifacts.items():
        artifacts[artifact_name] = str(artifact_path)
    utility = resolved.project_modules.utility
    code_paths = [str(path) for path in (utility, haute.wheel) if path is not None]
    return {
        "python_model": _MODEL_CODE_PATH,
        "artifacts": artifacts,
        "code_paths": code_paths or None,
        "signature": _build_signature(resolved),
        "conda_env": _conda_env(
            deployed_python_version(
                resolved.config.project_dir or resolved.config.pipeline_file.parent
            ),
            haute.requirement,
        ),
    }


def _build_signature(resolved: ResolvedDeploy) -> object:
    """Build an MLflow ModelSignature from resolved schemas.

    A flat column is its MLflow type. A workbench pipeline's request and response hold
    their tables as nested columns (specs/workbench): a one-row table is a struct and a
    many-row table a list of structs. The signature says what a table is, a ``Map`` of
    anything or an ``Array`` of them, not which columns it has: a quote may carry columns
    the tables do not name, which the Workbench Input leaves unread, and MLflow's ``Object``
    would refuse them before the pipeline saw the quote. The table's columns are in the
    manifest's input schema, and the reader holds the request to them, refusing a misfit
    naming the spot, as it does for the container's ``/quote``. A table's column is not
    required, as a quote need not hold every table.
    """
    import polars as pl
    from mlflow.models import ModelSignature
    from mlflow.types import ColSpec, DataType, Schema
    from mlflow.types.schema import AnyType, Array, Map

    def unsupported(col_name: str, dtype_str: str) -> DeployError:
        return DeployError(
            f"Cannot build an MLflow signature for column {col_name!r}: "
            f"unsupported Polars dtype {dtype_str!r}.",
            column=col_name,
            dtype=dtype_str,
        )

    def mlflow_type(dtype: Any, *, col_name: str, dtype_str: str) -> Any:
        if isinstance(dtype, pl.Struct):
            return Map(AnyType())
        if isinstance(dtype, pl.List):
            return Array(mlflow_type(dtype.inner, col_name=col_name, dtype_str=dtype_str))
        name = rendered_dtype_mlflow_type_name(str(dtype))
        if name is None:
            raise unsupported(col_name, dtype_str)
        return DataType[name]

    def _to_colspecs(schema: dict[str, str]) -> list[ColSpec]:
        specs = []
        for col_name, dtype_str in schema.items():
            try:
                dtype = parse_rendered_dtype(dtype_str, column=col_name)
            except SchemaMismatchError as exc:
                raise unsupported(col_name, dtype_str) from exc
            if holds_wide_integer(dtype):
                raise DeployError(
                    wide_integer_message(col_name, dtype), column=col_name, dtype=dtype_str
                )
            kind = mlflow_type(dtype, col_name=col_name, dtype_str=dtype_str)
            table = isinstance(dtype, pl.Struct | pl.List)
            specs.append(ColSpec(type=kind, name=col_name, required=not table))
        return specs

    input_schema = Schema(_to_colspecs(resolved.input_schema))  # type: ignore[arg-type]
    output_schema = Schema(_to_colspecs(resolved.output_schema))  # type: ignore[arg-type]
    return ModelSignature(inputs=input_schema, outputs=output_schema)


def _pip_requirements(haute_requirement: str) -> list[str]:
    """Build pip requirements for the deployed model.

    Haute is the one that validated the deploy (``haute.deploy._served_haute``): a
    release pinned by version, or a wheel the model carries. MLflow and Polars are
    pinned to the deploying environment's versions: Model Serving needs ``mlflow``
    itself in the environment to build the model's container, and a Polars release can
    change the results a pipeline gives and the memory it takes. Haute brings its own
    model engines, so no node adds one.
    """
    import mlflow
    import polars

    return [
        haute_requirement,
        f"mlflow=={mlflow.__version__}",
        f"polars=={polars.__version__}",
    ]


def _conda_env(python_version: str, haute_requirement: str) -> dict:
    """Build a conda environment dict for Databricks Model Serving.

    Databricks Model Serving builds the container from it. Python is the project's
    (``haute._project_python.deployed_python_version``), the one CI tested the pipeline
    on, never MLflow's guess from the host; ``3.13`` takes conda's newest 3.13 release.
    """
    return {
        "channels": ["conda-forge"],
        "dependencies": [
            f"python={python_version}",
            "pip",
            {"pip": _pip_requirements(haute_requirement)},
        ],
        "name": "mlflow-env",
    }


# Where a served model's Polars would spill. Spilling stays off, as in every process
# Haute starts, and Model Serving's /tmp is writable.
_SERVED_PROJECT_ROOT = PurePosixPath("/tmp/haute")


def _served_polars_environment() -> dict[str, str]:
    """The Polars settings a served model runs under: those of every process Haute starts.

    Set on the endpoint, so they apply before the model's process imports Polars.
    """
    return polars_process_environment(polars_spill_root(_SERVED_PROJECT_ROOT))


def _create_or_update_serving_endpoint(
    config: DeployConfig,
    uc_model_name: str,
    model_version: int,
    progress: Callable[[str], None],
) -> str | None:
    """Create or update a Databricks Model Serving endpoint, and wait until it serves.

    Creates the endpoint when it does not exist, or points its served entity at
    *model_version*, with the serving pair (``haute.deploy._databricks_serving``). An
    endpoint already being updated is refused rather than overridden, and a request
    Databricks refuses fails the deploy with the endpoint, the version and its reason.
    The deploy then waits until the endpoint serves *model_version*: a model whose
    environment cannot be built, or that fails to start, fails the deploy with what
    Databricks reports, instead of leaving the endpoint on the previous version behind
    a green deploy.

    Args:
        config: The deployment configuration (provides endpoint_name and
                serving settings from haute.toml).
        uc_model_name: The Unity Catalog three-level model name
                       (e.g. ``"workspace.default.haute-test"``).
        model_version: The model version number to serve.
        progress: Hears the endpoint's progress while the deploy waits.

    Returns:
        The endpoint invocation URL, or ``None`` if endpoint_name is not
        configured.
    """
    endpoint_name = config.effective_endpoint_name
    if not endpoint_name:
        return None

    from databricks.sdk.errors import DatabricksError, NotFound
    from databricks.sdk.service.serving import (
        EndpointCoreConfigInput,
        ServedEntityInput,
    )

    credentials = serving_credentials()
    ws = serving_workspace_client(credentials)

    def refused(action: str, exc: Exception) -> ServingEndpointError:
        # The registered version stays registered: the next deploy registers another.
        return ServingEndpointError(
            f"Databricks refused to {action} serving endpoint '{endpoint_name}' for "
            f"{uc_model_name} version {model_version}: {exc}"
        )

    served_entity = ServedEntityInput(
        entity_name=uc_model_name,
        entity_version=str(model_version),
        workload_size=config.databricks.serving_workload_size,
        scale_to_zero_enabled=config.databricks.serving_scale_to_zero,
        environment_vars=_served_polars_environment(),
    )

    try:
        endpoint = ws.serving_endpoints.get(endpoint_name)
    except NotFound:
        progress(f"Creating serving endpoint {endpoint_name} for version {model_version}...")
        try:
            ws.serving_endpoints.create(
                name=endpoint_name,
                config=EndpointCoreConfigInput(
                    name=endpoint_name,
                    served_entities=[served_entity],
                ),
            )
        except DatabricksError as exc:
            raise refused("create", exc) from exc
    except DatabricksError as exc:
        raise refused("read", exc) from exc
    else:
        if endpoint_update_state(endpoint) == "IN_PROGRESS":
            raise ServingEndpointError(
                f"Endpoint '{endpoint_name}' is already being updated, by another deploy or "
                "in the Databricks workspace. Wait for that update to finish, then deploy "
                "again."
            )
        progress(f"Updating serving endpoint {endpoint_name} to version {model_version}...")
        try:
            ws.serving_endpoints.update_config(
                name=endpoint_name,
                served_entities=[served_entity],
            )
        except DatabricksError as exc:
            raise refused("update", exc) from exc

    wait_for_endpoint(ws, endpoint_name, version=str(model_version), progress=progress)
    progress(f"Endpoint {endpoint_name} serves version {model_version}")
    return f"{credentials.host}/serving-endpoints/{endpoint_name}/invocations"


def check_databricks_credentials() -> None:
    """Refuse a Databricks deploy before any work when its credentials are not all set.

    Names every gap at once: the MLflow destination (``DATABRICKS_MLFLOW_HOST`` and
    ``DATABRICKS_MLFLOW_TOKEN``, or a profile ``MLFLOW_TRACKING_URI=databricks://<profile>``
    selects, read as MLflow reads it, which must give a host and a token) that registers
    the model, and the serving pair that creates or updates its endpoint. Whether the
    workspace accepts them is checked when the deploy first reaches it.
    """
    from haute.errors import MlflowConfigError
    from haute.modelling._mlflow_settings import resolve_destination

    problems: list[str] = []
    try:
        destination = resolve_destination("databricks")
    except MlflowConfigError as exc:
        problems.append(str(exc))
    else:
        problem = _profile_problem(destination.tracking_uri)
        if problem is not None:
            problems.append(problem)
    missing = missing_serving_credentials()
    if missing:
        problems.append(missing_serving_credentials_message(missing))
    if problems:
        raise DeployError(
            "The Databricks deploy's credentials are not set:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )


def _check_registry_schema(config: DeployConfig, registry_uri: str) -> None:
    """Refuse a deploy whose Unity Catalog schema does not exist, before the model uploads.

    MLflow registers the model as ``<catalog>.<schema>.<model>`` only after uploading it,
    and many workspaces have no ``main`` catalog (a newer workspace's is named
    ``workspace``), so a missing schema is looked up first, with the registry's own
    credentials, and its error lists the catalogs the token can see.
    """
    from databricks.sdk.errors import NotFound
    from mlflow.utils.databricks_utils import get_databricks_host_creds

    catalog, schema = config.databricks.catalog, config.databricks.schema
    creds = get_databricks_host_creds(registry_uri)
    ws = pat_workspace_client(creds.host, creds.token)
    try:
        ws.schemas.get(full_name=f"{catalog}.{schema}")
    except NotFound:
        catalogs = sorted(c.name for c in ws.catalogs.list() if c.name)
        raise DeployError(
            f"Unity Catalog schema {catalog}.{schema} does not exist, so the model cannot be "
            f"registered there. Create it (CREATE SCHEMA `{catalog}`.`{schema}`), or set "
            "[deploy.databricks] catalog and schema in haute.toml to a schema that exists. "
            f"The catalogs this token can see: {', '.join(catalogs) or 'none'}.",
            catalog=catalog,
            schema=schema,
        ) from None


def _profile_problem(tracking_uri: str) -> str | None:
    """Why the profile a ``databricks://<profile>`` URI selects cannot register a model.

    ``None`` for the pair form (``databricks``) and for a profile MLflow reads a host and
    a token from: the Unity Catalog model upload needs a personal access token.
    """
    if not tracking_uri.startswith("databricks://"):
        return None
    from mlflow.utils.databricks_utils import get_databricks_host_creds

    profile = tracking_uri.removeprefix("databricks://")
    try:
        creds = get_databricks_host_creds(tracking_uri)
    except Exception as exc:  # noqa: BLE001 - reported by type; see below
        # The message is left out: a malformed config file's parse error quotes its lines,
        # whichever profile they belong to, credentials included.
        return (
            f"MLFLOW_TRACKING_URI selects the Databricks profile '{profile}', which MLflow "
            f"cannot read ({type(exc).__name__}): the Databricks config file does not hold it, "
            "or the file or the profile is incomplete or malformed."
        )
    if not creds.host or not creds.token:
        return (
            f"MLFLOW_TRACKING_URI selects the Databricks profile '{profile}', which has no "
            "host and personal access token for registering the model."
        )
    return None


def _resolve_mlflow_databricks() -> tuple[str, str]:
    """``(tracking_uri, registry_uri)`` for deploy's MLflow calls, or ``DeployError``.

    Resolves the Databricks MLflow destination (which binds MLflow's credentials and
    rejects ``MLFLOW_ENABLE_DB_SDK=true`` or a conflicting ``DATABRICKS_CONFIG_PROFILE``)
    before any MLflow or HTTP request, so a misconfiguration fails fast with its
    non-secret reason.
    """
    from haute.errors import MlflowConfigError
    from haute.modelling._mlflow_settings import resolve_destination

    try:
        config = resolve_destination("databricks")
    except MlflowConfigError as exc:
        raise DeployError(f"Databricks MLflow is not ready for deploy: {exc}") from None
    return config.tracking_uri, registry_uri_for_tracking(config.tracking_uri)


def _check_databricks_connectivity(
    _log: Callable[[str], None],
    timeout: int = 10,
) -> None:
    """Verify the Databricks workspace is reachable before slow MLflow calls.

    Makes a lightweight HTTP GET to DATABRICKS_HOST with a short timeout so CI
    fails fast with a clear error instead of hanging forever.
    """
    import urllib.request

    credentials = serving_credentials()
    host = credentials.host
    url = f"{host}/api/2.0/clusters/list-zones"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {credentials.token}"})
    try:
        urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        _log("Databricks workspace reachable")
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise DeployError(
                f"Databricks returned 403 Forbidden. "
                f"Check that your DATABRICKS_RATING_TOKEN is valid and has workspace access. "
                f"Host: {host}"
            ) from exc
        # Other HTTP errors (e.g. 404) are fine - it means the host is reachable
        _log("Databricks workspace reachable")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DeployError(
            f"Cannot reach Databricks workspace at {host} "
            f"(timed out after {timeout}s). Check DATABRICKS_RATING_HOST is correct "
            f"and that the workspace allows connections from this network "
            f"(e.g. GitHub Actions IP ranges may need allowlisting)."
        ) from exc
