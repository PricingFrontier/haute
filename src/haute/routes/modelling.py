"""Modelling endpoints: train, status, export."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from haute._logging import get_logger
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.routes._job_store import JobStore
from haute.routes._train_service import (
    TrainService,
    _check_gpu_vram,
    _clamp_row_limit,
    _find_modelling_node,
    _VramCheck,
)
from haute.schemas import (
    ExportScriptRequest,
    ExportScriptResponse,
    LogExperimentRequest,
    LogExperimentResponse,
    MlflowCheckResponse,
    TrainEstimateRequest,
    TrainEstimateResponse,
    TrainRequest,
    TrainResponse,
    TrainStatusResponse,
)

logger = get_logger(component="server.modelling")

router = APIRouter(prefix="/api/modelling", tags=["modelling"])

# In-memory job store — fine for single-server dev tool.
_store = JobStore()
_train_service = TrainService(_store)


@router.post("/train", response_model=TrainResponse)
def train_model(body: TrainRequest) -> TrainResponse:
    """Start model training for a modelling node.

    Executes the pipeline up to the modelling node to materialise the
    training DataFrame, then runs TrainingJob in a background thread.
    """
    return _train_service.start(body)


@router.get("/train/status/{job_id}", response_model=TrainStatusResponse)
async def train_status(job_id: str) -> TrainStatusResponse:
    """Poll training job progress."""
    job = _store.require_job(job_id)

    return TrainStatusResponse(
        status=job.get("status", "unknown"),
        progress=job.get("progress", 0.0),
        message=job.get("message", ""),
        iteration=job.get("iteration", 0),
        total_iterations=job.get("total_iterations", 0),
        train_loss=job.get("train_loss", {}),
        elapsed_seconds=job.get("elapsed_seconds", 0.0),
        result=job.get("result"),
        warning=job.get("warning"),
    )


@router.post("/estimate", response_model=TrainEstimateResponse)
def estimate_training(body: TrainEstimateRequest) -> TrainEstimateResponse:
    """Estimate RAM and row requirements for training a modelling node.

    Reads parquet metadata from ancestor source nodes to estimate
    dataset size analytically.  Returns immediately — typically <100 ms.
    Also estimates GPU VRAM if the node's params specify ``task_type: GPU``.
    """
    node = _find_modelling_node(body.graph, body.node_id)

    from haute._ram_estimate import estimate_safe_training_rows

    try:
        ram_est = estimate_safe_training_rows(
            body.graph,
            body.node_id,
            source=body.source,
        )
    except Exception as exc:
        logger.warning("estimate_failed", error=str(exc), node_id=body.node_id)
        return TrainEstimateResponse()

    # estimated_bytes already includes all training phases (split, pools,
    # CatBoost internals, diagnostics SHAP/PDP, CV if enabled).
    data_mb = ram_est.estimated_bytes / 1024**2
    training_mb = data_mb  # phase model already accounts for overhead

    # Apply user row limit to the estimate
    user_limit = node.data.config.get("row_limit")
    safe_limit = _clamp_row_limit(ram_est.safe_row_limit, user_limit)

    # If user's row_limit is the binding constraint, suppress the RAM warning
    warning = ram_est.warning
    was_downsampled = ram_est.was_downsampled
    if (
        warning
        and user_limit
        and isinstance(user_limit, (int, float))
        and int(user_limit) > 0
        and (safe_limit is not None and safe_limit == int(user_limit))
    ):
        warning = None
        was_downsampled = False

    # GPU VRAM estimation — use feature count (not total columns),
    # since CatBoost only loads features to GPU.
    vram_check = _VramCheck()
    node_params = node.data.config.get("params", {})
    if str(node_params.get("task_type", "")).upper() == "GPU":
        effective_rows = ram_est.total_rows or 0
        # Feature count = total cols - excluded - target - weight
        n_excluded = len(node.data.config.get("exclude", []))
        n_non_feature = n_excluded + 1  # +1 for target
        if node.data.config.get("weight"):
            n_non_feature += 1
        n_features = max(ram_est.probe_columns - n_non_feature, 1)
        vram_check = _check_gpu_vram(effective_rows, n_features, node_params)
        if vram_check.warning:
            vram_check.warning += " Training will fall back to CPU automatically."

    return TrainEstimateResponse(
        total_rows=ram_est.total_rows,
        safe_row_limit=safe_limit,
        estimated_mb=round(data_mb, 1),
        training_mb=round(training_mb, 1),
        available_mb=round(ram_est.available_bytes / 1024**2, 1),
        bytes_per_row=round(ram_est.bytes_per_row, 1),
        was_downsampled=was_downsampled,
        warning=warning,
        gpu_vram_estimated_mb=vram_check.estimated_mb,
        gpu_vram_available_mb=vram_check.available_mb,
        gpu_warning=vram_check.warning,
    )


@router.get("/mlflow/check", response_model=MlflowCheckResponse)
async def mlflow_check() -> MlflowCheckResponse:
    """Check whether MLflow is installed and detect the tracking backend."""
    try:
        import mlflow as _mlflow  # noqa: F401

        mlflow_installed = True
    except ImportError:
        return MlflowCheckResponse(mlflow_installed=False)

    import os

    from haute.modelling._mlflow_log import resolve_tracking_backend

    _uri, backend = resolve_tracking_backend()
    databricks_host = os.getenv("DATABRICKS_HOST", "") if backend == "databricks" else ""

    return MlflowCheckResponse(
        mlflow_installed=mlflow_installed,
        backend=backend,
        databricks_host=databricks_host,
    )


@router.post("/mlflow/log", response_model=LogExperimentResponse)
async def mlflow_log(body: LogExperimentRequest) -> LogExperimentResponse:
    """Log a completed training job's results to MLflow."""
    job = _store.require_completed_job(body.job_id)

    result: TrainResponse | None = job.get("result")
    if result is None:
        raise HTTPException(status_code=400, detail="Job has no result data")

    config = job.get("config", {})
    node_label = job.get("node_label", "model")

    # Build experiment name: user override > config > backend-aware default
    from haute.modelling._mlflow_log import resolve_experiment_name

    experiment_name = resolve_experiment_name(
        explicit=body.experiment_name,
        config_value=config.get("mlflow_experiment"),
        node_label=node_label,
    )
    model_name = body.model_name or config.get("model_name") or None

    try:
        from haute.modelling._mlflow_log import log_experiment
        from haute.modelling._result_types import (
            ModelCardMetadata,
            ModelDiagnostics,
        )

        diagnostics = ModelDiagnostics(
            feature_importance=result.feature_importance,
            shap_summary=result.shap_summary,
            feature_importance_loss=result.feature_importance_loss,
            double_lift=result.double_lift,
            loss_history=result.loss_history,
            cv_results=result.cv_results,
            ave_per_feature=result.ave_per_feature,
            residuals_histogram=result.residuals_histogram,
            residuals_stats=result.residuals_stats,
            actual_vs_predicted=result.actual_vs_predicted,
            lorenz_curve=result.lorenz_curve,
            lorenz_curve_perfect=result.lorenz_curve_perfect,
            pdp_data=result.pdp_data,
            holdout_metrics=result.holdout_metrics,
            diagnostics_set=result.diagnostics_set,
        )
        metadata = ModelCardMetadata(
            algorithm=config.get("algorithm", "catboost"),
            task=config.get("task", "regression"),
            train_rows=result.train_rows,
            test_rows=result.test_rows,
            holdout_rows=result.holdout_rows,
            features=result.features,
            split_config=config.get("split", {}),
            best_iteration=result.best_iteration,
        )

        log_result = log_experiment(
            experiment_name=experiment_name,
            run_name=node_label,
            metrics=result.metrics,
            params={
                "algorithm": config.get("algorithm", "catboost"),
                "task": config.get("task", "regression"),
                "target": config.get("target", ""),
                "weight": config.get("weight", ""),
            },
            diagnostics=diagnostics,
            metadata=metadata,
            model_path=result.model_path or None,
            model_name=model_name,
        )

        return LogExperimentResponse(
            status="ok",
            backend=log_result.backend,
            experiment_name=log_result.experiment_name,
            run_id=log_result.run_id,
            run_url=log_result.run_url,
            tracking_uri=log_result.tracking_uri,
        )
    except Exception as exc:
        logger.error("mlflow_log_failed", error=str(exc), job_id=body.job_id)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/export", response_model=ExportScriptResponse)
async def export_script(body: ExportScriptRequest) -> ExportScriptResponse:
    """Generate a standalone training script from a modelling node's config."""
    node = _find_modelling_node(body.graph, body.node_id)
    config = dict(node.data.config)

    # Use the node label as the default name
    if "name" not in config:
        config["name"] = node.data.label

    from haute.modelling import generate_training_script

    data_path = body.data_path or f"output/{config.get('name', 'model')}.parquet"
    script = generate_training_script(config, data_path)
    filename = f"train_{config.get('name', 'model')}.py"

    return ExportScriptResponse(script=script, filename=filename)


@router.delete("/model-cache")
async def clear_model_cache(run_id: str | None = None) -> dict:
    """Clear cached model artifacts downloaded from MLflow.

    Pass ``?run_id=...`` to clear a specific run's cache, or omit to
    clear all cached models.  Also evicts the in-memory model LRU cache
    so the next scoring request re-downloads fresh artifacts.
    """
    from haute._mlflow_io import clear_model_cache as _clear

    removed = _clear(run_id)
    return {"removed": removed, "run_id": run_id}
