"""Modelling endpoints: train, status, export."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._logging import get_logger
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.routes._job_store import get_job_store
from haute.routes._train_service import (
    _DEFAULT_TIMEOUT,
    TrainService,
    _assert_json_finite,
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
    ModelCacheClearResponse,
    TrainEstimateRequest,
    TrainEstimateResponse,
    TrainRequest,
    TrainResponse,
    TrainStatusResponse,
)

logger = get_logger(component="server.modelling")

router = APIRouter(prefix="/api/modelling", tags=["modelling"])

# In-memory job store — acquired through the central factory so the
# "training" namespace is shared across any other importers that look
# it up with the same prefix (see ``haute.routes._job_store``).
_store = get_job_store("training")
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

    if job.get("status") == "running":
        start = job.get("start_time")
        timeout = job.get("timeout", _DEFAULT_TIMEOUT)
        if start and (time.monotonic() - start) > timeout:
            _store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": f"Training timed out after {timeout}s. "
                    "Increase timeout or simplify the model.",
                    "elapsed_seconds": time.monotonic() - start,
                },
                expected_status="running",
            )
            job = _store.require_job(job_id)

    result = job.get("result")
    # ``_result_finite_validated`` is an internal cache flag — it MUST stay
    # private to this module.  Never include it in ``TrainStatusResponse`` or
    # log it in a structured payload that gets shipped to the user; the
    # whitelist in the response constructor below already enforces that.
    if result is not None and not job.get("_result_finite_validated"):
        try:
            _assert_json_finite(result)
        except ValueError as exc:
            message = f"Training result cannot be published: {exc}"
            logger.error("training_result_not_json_finite", error=str(exc), job_id=job_id)
            _store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": message,
                    "result": None,
                },
                expected_status="completed",
            )
            job = _store.require_job(job_id)
        else:
            # Completed-job results are immutable in this store, so we only
            # need to walk them once.  Cache the validation outcome so
            # subsequent polls skip the recursive ``_assert_json_finite``
            # walk — otherwise every status poll re-walks the entire result.
            #
            # ``atomic_update`` may return ``None`` if the status flipped (e.g.
            # to ``error`` in a concurrent request), in which case the cache
            # write is skipped and the next poll just re-validates against
            # the new state — exactly what we want.
            _store.atomic_update(
                job_id,
                {"_result_finite_validated": True},
                expected_status="completed",
            )
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
    import importlib
    import importlib.util

    if importlib.util.find_spec("mlflow") is None:
        return MlflowCheckResponse(
            mlflow_installed=False,
            detail="MLflow package is not installed",
        )

    try:
        importlib.import_module("mlflow")
    except ImportError as exc:
        logger.warning("mlflow_check_package_import_failed", error=str(exc))
        return MlflowCheckResponse(
            mlflow_installed=True,
            mlflow_importable=False,
            tracking_configured=False,
            detail=f"MLflow package import failed: {exc}",
        )

    import os

    from haute.modelling._mlflow_log import resolve_tracking_backend

    try:
        _uri, backend = resolve_tracking_backend()
    except Exception as exc:
        logger.warning("mlflow_check_backend_resolution_failed", error=str(exc))
        return MlflowCheckResponse(
            mlflow_installed=True,
            mlflow_importable=True,
            tracking_configured=False,
            detail=str(exc),
        )

    databricks_host = os.getenv("DATABRICKS_HOST", "") if backend == "databricks" else ""

    return MlflowCheckResponse(
        mlflow_installed=True,
        mlflow_importable=True,
        tracking_configured=True,
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

        log_result = await run_in_threadpool(
            log_experiment,
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


@router.delete("/model-cache", response_model=ModelCacheClearResponse)
async def clear_model_cache(run_id: str | None = None) -> ModelCacheClearResponse:
    """Clear cached model artifacts downloaded from MLflow.

    Pass ``?run_id=...`` to clear a specific run's cache, or omit to
    clear all cached models.  Also evicts the in-memory model LRU cache
    so the next scoring request re-downloads fresh artifacts.
    """
    from haute._mlflow_io import clear_model_cache as _clear

    removed = await run_in_threadpool(_clear, run_id)
    return ModelCacheClearResponse(removed=removed, run_id=run_id)
