"""MLflow discovery endpoints for the Model Score node.

Lists experiments, runs (with model artifacts), registered models,
and model versions so the frontend can populate dropdowns.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    import types as _types

    from mlflow.tracking import MlflowClient

from haute._logging import get_logger
from haute._mlflow_utils import allow_file_store_if_local, search_versions
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


def _elapsed_ms(started_at: float, ended_at: float) -> float:
    """Return elapsed monotonic-clock time in milliseconds."""
    return (ended_at - started_at) * 1000


@dataclass(slots=True)
class _RunDiscoveryMeasurement:
    """Constant-space aggregate for one MLflow run discovery attempt."""

    max_results: int
    started_at: float
    search_calls: int = 0
    artifact_calls: int = 0
    runs_scanned: int = 0
    runs_returned: int = 0
    artifact_failures: int = 0
    search_ms: float = 0.0
    artifact_ms: float = 0.0

    def record_search(self, started_at: float, ended_at: float) -> None:
        self.search_calls += 1
        self.search_ms += _elapsed_ms(started_at, ended_at)

    def record_artifact_call(self, started_at: float, ended_at: float) -> None:
        self.artifact_calls += 1
        self.artifact_ms += _elapsed_ms(started_at, ended_at)

    def emit(self, *, outcome: str) -> None:
        total_ms = _elapsed_ms(self.started_at, perf_counter())
        assembly_ms = max(0.0, total_ms - self.search_ms - self.artifact_ms)
        logger.info(
            "mlflow_run_discovery_completed",
            outcome=outcome,
            max_results=self.max_results,
            search_calls=self.search_calls,
            artifact_calls=self.artifact_calls,
            runs_scanned=self.runs_scanned,
            runs_returned=self.runs_returned,
            artifact_failures=self.artifact_failures,
            search_ms=self.search_ms,
            artifact_ms=self.artifact_ms,
            assembly_ms=assembly_ms,
            total_ms=total_ms,
        )


def _run_summaries(
    runs: list[Any],
    client: MlflowClient,
    artifact_filter: str,
    measurement: _RunDiscoveryMeasurement,
) -> list[MlflowRunSummary]:
    """Build filtered summaries while updating only aggregate work counters."""
    model_extensions = (".cbm", ".rsglm")

    def _match(path: str) -> bool:
        if artifact_filter == "optimiser":
            return path == "optimiser_result.json"
        return any(path.endswith(ext) for ext in model_extensions)

    results: list[MlflowRunSummary] = []
    for run in runs:
        measurement.runs_scanned += 1
        run_id = run.info.run_id
        # Check for matching artifacts (N+1 — unavoidable without batch API)
        artifact_started_at = perf_counter()
        try:
            artifacts = client.list_artifacts(run_id)
            matched = [a.path for a in artifacts if _match(a.path)]
            if not matched:
                continue
        except Exception as exc:
            measurement.artifact_failures += 1
            logger.warning("artifact_list_failed", run_id=run_id, error=str(exc))
            continue
        finally:
            measurement.record_artifact_call(artifact_started_at, perf_counter())

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
        measurement.runs_returned += 1
    return results


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

    try:
        from mlflow.tracking import MlflowClient

        from haute.modelling._mlflow_log import resolve_tracking_backend

        tracking_uri, backend = resolve_tracking_backend()
        allow_file_store_if_local(tracking_uri, backend)
        mlflow.set_tracking_uri(tracking_uri)
        client = MlflowClient(tracking_uri=tracking_uri)
        return mlflow, client
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("mlflow_tracking_setup_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=_INTERNAL_ERROR_DETAIL)


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
    measurement = _RunDiscoveryMeasurement(max_results=max_results, started_at=perf_counter())

    search_started_at = perf_counter()
    try:
        runs = mlflow.search_runs(
            experiment_ids=[experiment_id],
            filter_string="status = 'FINISHED'",
            max_results=max_results,
            output_format="list",
        )
    except Exception as exc:
        measurement.record_search(search_started_at, perf_counter())
        measurement.emit(outcome="search_failed")
        logger.error("mlflow_list_runs_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=_INTERNAL_ERROR_DETAIL)
    measurement.record_search(search_started_at, perf_counter())

    try:
        results = _run_summaries(runs, client, artifact_filter, measurement)
    except BaseException:
        measurement.emit(outcome="processing_failed")
        raise
    measurement.emit(outcome="success")
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


def _model_version_run_params(client: MlflowClient, run_id: str) -> dict[str, str]:
    """Fetch a registered model version's backing-run params.

    A registered model version may reference a run that has been deleted or
    is otherwise inaccessible — in that case the version itself is still
    valid, so swallow the lookup error and return ``{}`` rather than failing
    the whole ``/model-versions`` response. The exception is logged with a
    stack trace so the underlying cause is diagnosable.
    """
    if not run_id:
        return {}
    try:
        run = client.get_run(run_id)
    except Exception:
        logger.exception(
            "mlflow_model_version_params_unavailable",
            run_id=run_id,
        )
        return {}
    return dict(run.data.params)


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
            params=_model_version_run_params(client, v.run_id or ""),
        )
        for v in sorted(versions, key=lambda v: int(v.version), reverse=True)
    ]
