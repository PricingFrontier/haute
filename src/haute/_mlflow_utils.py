"""Shared MLflow helpers used by _mlflow_io, _optimiser_io, and deploy/_bundler.

Eliminates duplication of:
  - ``resolve_version()``: resolve "latest" to a concrete version number
  - ``search_versions()``: safely quote model name and search
  - ``resolve_mlflow_source()``: import mlflow, set tracking URI, create client,
    and resolve a source_type/run_id/registered_model to a concrete run_id
"""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mlflow.entities.model_registry import ModelVersion
    from mlflow.tracking import MlflowClient


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
    return sorted_versions[0].version


def resolve_mlflow_source(
    *,
    source_type: str,
    run_id: str = "",
    registered_model: str = "",
    version: str = "",
    tracking_uri: str = "",
) -> tuple[str, str, ModuleType, Any]:
    """Import mlflow, configure tracking, and resolve a model source.

    Handles the boilerplate shared across ``_mlflow_io``, ``_optimiser_io``,
    and ``deploy/_bundler``:

    1. Import ``mlflow`` (with a friendly :class:`ImportError`).
    2. Resolve/set the tracking URI.
    3. Create an :class:`~mlflow.tracking.MlflowClient`.
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

    if not tracking_uri:
        tracking_uri, _ = resolve_tracking_backend()
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)

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
