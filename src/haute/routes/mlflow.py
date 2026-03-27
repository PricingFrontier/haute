"""MLflow discovery endpoints for the Model Score node.

Lists experiments, runs (with model artifacts), registered models,
and model versions so the frontend can populate dropdowns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    import types as _types

    from mlflow.tracking import MlflowClient

from haute._logging import get_logger
from haute._mlflow_utils import search_versions
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.schemas import (
    MlflowExperimentSummary,
    MlflowModelSummary,
    MlflowModelVersionSummary,
    MlflowRunSummary,
    MlflowVersionBrief,
)

logger = get_logger(component="server.mlflow")

router = APIRouter(prefix="/api/mlflow", tags=["mlflow"])


def _ensure_tracking() -> tuple[_types.ModuleType, MlflowClient]:
    """Import mlflow, configure tracking URI, and return ``(mlflow, client)``.

    Raises ``HTTPException(503)`` if mlflow is not installed, or
    ``HTTPException(502)`` if the tracking backend cannot be resolved.
    """
    try:
        import mlflow
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="mlflow is not installed. Install it with: pip install mlflow",
        )

    from mlflow.tracking import MlflowClient

    from haute.modelling._mlflow_log import resolve_tracking_backend

    tracking_uri, _backend = resolve_tracking_backend()
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    return mlflow, client


@router.get("/experiments", response_model=list[MlflowExperimentSummary])
def list_experiments() -> list[MlflowExperimentSummary]:
    """List all MLflow experiments."""
    mlflow, _client = _ensure_tracking()

    try:
        experiments = mlflow.search_experiments()
    except Exception as exc:
        logger.error("mlflow_list_experiments_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=_INTERNAL_ERROR_DETAIL)

    return [
        MlflowExperimentSummary(
            experiment_id=exp.experiment_id,
            name=exp.name,
        )
        for exp in experiments
    ]


@router.get("/runs", response_model=list[MlflowRunSummary])
def list_runs(
    experiment_id: str = Query(..., description="MLflow experiment ID"),
    max_results: int = Query(20, ge=1, le=100),
    artifact_filter: str = Query(
        "model",
        description=(
            "Filter runs by artifact type: "
            "'model' for any model artifact (.cbm, .rsglm), "
            "'optimiser' for optimiser results (optimiser_result.json)"
        ),
    ),
) -> list[MlflowRunSummary]:
    """List runs for an experiment, filtered to FINISHED runs with matching artifacts.

    Note: Each run requires a separate ``list_artifacts`` call to check for
    matching files.  MLflow has no batch artifacts API, so this is O(N) in
    the number of runs.  The ``max_results`` cap bounds the total calls.
    """
    mlflow, client = _ensure_tracking()

    try:
        runs = mlflow.search_runs(
            experiment_ids=[experiment_id],
            filter_string="status = 'FINISHED'",
            max_results=max_results,
            output_format="list",
        )
    except Exception as exc:
        logger.error("mlflow_list_runs_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=_INTERNAL_ERROR_DETAIL)

    model_extensions = (".cbm", ".rsglm")

    def _match(path: str) -> bool:
        if artifact_filter == "optimiser":
            return path == "optimiser_result.json"
        return any(path.endswith(ext) for ext in model_extensions)

    results: list[MlflowRunSummary] = []
    for run in runs:
        run_id = run.info.run_id
        # Check for matching artifacts (N+1 — unavoidable without batch API)
        try:
            artifacts = client.list_artifacts(run_id)
            matched = [a.path for a in artifacts if _match(a.path)]
            if not matched:
                continue
        except Exception as exc:
            logger.warning("artifact_list_failed", run_id=run_id, error=str(exc))
            continue

        results.append(
            MlflowRunSummary(
                run_id=run_id,
                run_name=run.info.run_name or "",
                status=run.info.status,
                start_time=run.info.start_time,
                metrics=run.data.metrics or {},
                params=run.data.params or {},
                artifacts=matched,
            )
        )

    return results


@router.get("/models", response_model=list[MlflowModelSummary])
def list_models(
    max_results: int = Query(100, ge=1, le=1000),
    page_token: str | None = Query(None),
) -> list[MlflowModelSummary]:
    """List registered models."""
    _mlflow, client = _ensure_tracking()

    try:
        result = client.search_registered_models(
            max_results=max_results,
            page_token=page_token if page_token else None,
        )
    except Exception as exc:
        logger.error("mlflow_list_models_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=_INTERNAL_ERROR_DETAIL)

    return [
        MlflowModelSummary(
            name=m.name,
            latest_versions=[
                MlflowVersionBrief(
                    version=v.version,
                    status=v.status,
                    run_id=v.run_id,
                )
                for v in (m.latest_versions or [])
            ],
        )
        for m in result
    ]


@router.get("/model-versions", response_model=list[MlflowModelVersionSummary])
def list_model_versions(
    model_name: str = Query(..., description="Registered model name"),
) -> list[MlflowModelVersionSummary]:
    """List versions of a registered model."""
    _mlflow, client = _ensure_tracking()

    try:
        versions = search_versions(client, model_name)
    except Exception as exc:
        logger.error("mlflow_list_versions_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=_INTERNAL_ERROR_DETAIL)

    return [
        MlflowModelVersionSummary(
            version=v.version,
            run_id=v.run_id or "",
            status=v.status,
            creation_timestamp=v.creation_timestamp,
            description=getattr(v, "description", ""),
        )
        for v in sorted(versions, key=lambda v: int(v.version), reverse=True)
    ]
