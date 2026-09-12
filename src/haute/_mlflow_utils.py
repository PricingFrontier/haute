"""Shared MLflow helpers used by _mlflow_io, _optimiser_io, and deploy/_bundler.

Eliminates duplication of:
  - ``resolve_version()``: resolve "latest" to a concrete version number
  - ``search_versions()``: safely quote model name and search
  - ``resolve_mlflow_source()``: import mlflow, create a destination-pinned client,
    and resolve a source_type/run_id/registered_model to a concrete run_id
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mlflow.entities.model_registry import ModelVersion
    from mlflow.tracking import MlflowClient


_FLUENT_LOCK = threading.RLock()
_tracking_environment_snapshot: tuple[str | None] | None = None


def tracking_uri_from_environment() -> str:
    """Read configured credentials even while MLflow updates its own environment."""
    snapshot = _tracking_environment_snapshot
    value = snapshot[0] if snapshot is not None else os.environ.get("MLFLOW_TRACKING_URI")
    return value or ""


def registry_uri_for_tracking(tracking_uri: str) -> str:
    """Keep registry selection and Databricks profile aligned with tracking."""
    if tracking_uri == "databricks" or tracking_uri.startswith("databricks://"):
        return "databricks-uc" + tracking_uri[len("databricks") :]
    return tracking_uri


def set_tracking_uri_preserving_env(mlflow: Any, tracking_uri: str) -> None:
    """Configure fluent MLflow without overwriting the user's credential source."""
    global _tracking_environment_snapshot
    with _FLUENT_LOCK:
        configured_uri = os.environ.get("MLFLOW_TRACKING_URI")
        previous_snapshot = _tracking_environment_snapshot
        _tracking_environment_snapshot = (configured_uri,)
        try:
            mlflow.set_tracking_uri(tracking_uri)
        finally:
            _restore_env("MLFLOW_TRACKING_URI", configured_uri)
            _tracking_environment_snapshot = previous_snapshot


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


@contextmanager
def mlflow_fluent_operation() -> Iterator[None]:
    """Serialize global-state SDK operations and restore state on every exit.

    Discovery and native reads use pinned clients without this lock. Pyfunc
    downloads share it for MLflow's nested global-state model lookup. Settings
    can change while a log is in progress; its fluent URI remains fixed until
    the run has terminated. The next writer then resolves the new settings.
    """
    with _FLUENT_LOCK:
        import mlflow

        tracking_uri, registry_uri = mlflow.get_tracking_uri(), mlflow.get_registry_uri()
        environment = {
            name: os.environ.get(name)
            for name in (
                "MLFLOW_TRACKING_URI",
                "MLFLOW_REGISTRY_URI",
                "MLFLOW_EXPERIMENT_ID",
            )
        }
        try:
            yield
        finally:
            try:
                if mlflow.get_tracking_uri() != tracking_uri:
                    set_tracking_uri_preserving_env(mlflow, tracking_uri)
                if mlflow.get_registry_uri() != registry_uri:
                    mlflow.set_registry_uri(registry_uri)
            finally:
                for name, value in environment.items():
                    _restore_env(name, value)


def search_versions(
    client: MlflowClient,
    model_name: str,
) -> list[ModelVersion]:
    """Search model versions, safely quoting the model name."""
    safe_name = model_name.replace("'", "\\'")
    return client.search_model_versions(f"name='{safe_name}'")


def resolve_version(
    client: Any,
    model_name: str,
    version: str,
) -> str:
    """Resolve ``"latest"`` or empty version to a concrete version number.

    Raises:
        ValueError: if no versions are found for the model.
    """
    if version and version != "latest":
        return version

    versions = search_versions(client, model_name)
    if not versions:
        raise ValueError(f"No versions found for registered model '{model_name}'.")
    sorted_versions = sorted(versions, key=lambda v: int(v.version), reverse=True)
    # int on the file store, str on Databricks — callers expect str.
    return str(sorted_versions[0].version)


def allow_file_store_if_local(tracking_uri: str, backend: str = "") -> None:
    """Opt into MLflow's local file backend before constructing a client."""
    uri = tracking_uri.lower()
    is_local_uri = uri.startswith("file:") or ("://" not in uri and uri != "databricks")
    if backend == "local" or is_local_uri:
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")


def resolve_mlflow_source(
    *,
    source_type: str,
    run_id: str = "",
    registered_model: str = "",
    version: str = "",
    tracking_uri: str = "",
) -> tuple[str, str, ModuleType, Any]:
    """Import mlflow, pin a client to the destination, and resolve a model source.

    Handles the boilerplate shared across ``_mlflow_io``, ``_optimiser_io``,
    and ``deploy/_bundler``:

    1. Import ``mlflow`` (with a friendly :class:`ImportError`).
    2. Resolve the tracking URI without changing fluent state.
    3. Create an :class:`~mlflow.tracking.MlflowClient` with explicit tracking
       and registry URIs.
    4. Map *source_type* (``"registered"`` or ``"run"``) to a concrete
       ``run_id`` and ``version``.

    Args:
        source_type: ``"run"`` or ``"registered"``.
        run_id: MLflow run ID (required when *source_type* is ``"run"``).
        registered_model: Registered model name (required when
            *source_type* is ``"registered"``).
        version: Model version (``"1"``, ``"latest"``, etc.).
        tracking_uri: Override tracking URI; auto-detected if empty.

    Returns:
        ``(resolved_run_id, resolved_version, mlflow_module, client)``
        where *mlflow_module* is the imported ``mlflow`` package and
        *client* is an :class:`~mlflow.tracking.MlflowClient`.

    Raises:
        ImportError: If ``mlflow`` is not installed.
        ValueError: If required arguments are missing or *source_type* is
            invalid.
    """
    try:
        import mlflow
    except ImportError:
        raise ImportError("mlflow is not installed. Install it with: pip install mlflow") from None

    from mlflow.tracking import MlflowClient

    from haute.modelling._mlflow_log import resolve_tracking_backend

    backend = ""
    if not tracking_uri:
        tracking_uri, backend = resolve_tracking_backend()
    allow_file_store_if_local(tracking_uri, backend)
    client = MlflowClient(
        tracking_uri=tracking_uri,
        registry_uri=registry_uri_for_tracking(tracking_uri),
    )

    resolved_run_id = run_id
    resolved_version = version

    if source_type == "registered":
        if not registered_model:
            raise ValueError("registered_model is required when sourceType is 'registered'")
        resolved_version = resolve_version(client, registered_model, version)
        mv = client.get_model_version(registered_model, resolved_version)
        resolved_run_id = mv.run_id or ""
    elif source_type == "run":
        if not resolved_run_id:
            raise ValueError("run_id is required when sourceType is 'run'")
    else:
        raise ValueError(f"Invalid sourceType: {source_type!r}. Expected 'run' or 'registered'.")

    return resolved_run_id, resolved_version, mlflow, client
