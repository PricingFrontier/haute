"""Modelling endpoints: train, status, export."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._logging import get_logger
from haute.modelling._train_config import TrainingConfigError
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.routes._job_lifecycle import require_job_status
from haute.routes._job_store import get_job_store
from haute.routes._train_service import (
    TrainService,
    _assert_json_finite,
    _check_gpu_vram,
    _clamp_row_limit,
    _default_train_timeout,
    _find_modelling_node,
    _VramCheck,
)
from haute.routes.pipeline import _prepare_runtime_graph
from haute.schemas import (
    DispersionEstimateRequest,
    DispersionEstimateResponse,
    DispersionEstimateStatusResponse,
    EvaluationPreviewPayload,
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

    Returns the cancellable job handle before background preparation
    materialises the training DataFrame and launches the supervised fit worker.
    """
    graph = _prepare_runtime_graph(body.graph)
    return _train_service.start(body.model_copy(update={"graph": graph}))


@router.get("/train/status/{job_id}", response_model=TrainStatusResponse)
async def train_status(job_id: str) -> TrainStatusResponse:
    """Poll training job progress."""
    job = _store.require_job(job_id)

    if job.get("status") == "running":
        start = job.get("start_time")
        timeout = job.get("timeout", _default_train_timeout())
        if start and (time.monotonic() - start) > timeout:
            job = _train_service.timeout(job_id, timeout=timeout, start_time=start)

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
            job = _train_service.reject_completed_result(job_id, message=message)
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
        status=require_job_status(job),
        progress=job.get("progress", 0.0),
        message=job.get("message", ""),
        iteration=job.get("iteration", 0),
        total_iterations=job.get("total_iterations", 0),
        train_loss=job.get("train_loss", {}),
        train_loss_history=job.get("train_loss_history", []),
        train_loss_history_truncated=job.get("train_loss_history_truncated", False),
        elapsed_seconds=job.get("elapsed_seconds", 0.0),
        result=job.get("result"),
        warning=job.get("warning"),
        terminal_reason=job.get("terminal_reason"),
        execution_metrics=job.get("execution_metrics"),
        feature_selection=job.get("feature_selection"),
        error_code=job.get("error_code"),
        http_status_code=job.get("http_status_code"),
        error_detail=job.get("error_detail"),
    )


@router.post("/train/cancel/{job_id}", response_model=TrainStatusResponse)
async def cancel_training(job_id: str) -> TrainStatusResponse:
    """Cancel an in-progress training job."""
    job = _train_service.cancel(job_id)
    return TrainStatusResponse(
        status=require_job_status(job),
        progress=job.get("progress", 0.0),
        message=job.get("message", ""),
        iteration=job.get("iteration", 0),
        total_iterations=job.get("total_iterations", 0),
        train_loss=job.get("train_loss", {}),
        train_loss_history=job.get("train_loss_history", []),
        train_loss_history_truncated=job.get("train_loss_history_truncated", False),
        elapsed_seconds=job.get("elapsed_seconds", 0.0),
        result=job.get("result"),
        warning=job.get("warning"),
        terminal_reason=job.get("terminal_reason"),
        execution_metrics=job.get("execution_metrics"),
        feature_selection=job.get("feature_selection"),
        error_code=job.get("error_code"),
        http_status_code=job.get("http_status_code"),
        error_detail=job.get("error_detail"),
    )


def _dispersion_status_response(job: dict) -> DispersionEstimateStatusResponse:
    return DispersionEstimateStatusResponse(
        status=require_job_status(job),
        progress=job.get("progress", 0.0),
        message=job.get("message", ""),
        elapsed_seconds=job.get("elapsed_seconds", 0.0),
        param=job.get("param"),
        value=job.get("value"),
        llf=job.get("llf"),
        n_fits=job.get("n_fits"),
        error=job.get("error"),
        terminal_reason=job.get("terminal_reason"),
    )


@router.post("/dispersion/estimate", response_model=DispersionEstimateResponse)
def estimate_dispersion(body: DispersionEstimateRequest) -> DispersionEstimateResponse:
    """Estimate a GLM dispersion parameter (NB theta / Tweedie var_power).

    Materialises the node's training frame exactly as /train would, then
    profiles the log-likelihood over the parameter in a background job.
    The resolved value is returned for the user to accept into the config —
    the training-objective gate still requires an explicit value; this
    endpoint never sets one silently.
    """
    graph = _prepare_runtime_graph(body.graph)
    return _train_service.start_dispersion_estimate(body.model_copy(update={"graph": graph}))


@router.get("/dispersion/status/{job_id}", response_model=DispersionEstimateStatusResponse)
def dispersion_status(job_id: str) -> DispersionEstimateStatusResponse:
    """Poll a dispersion-estimation job."""
    return _dispersion_status_response(_train_service.dispersion_job(job_id))


@router.post("/dispersion/cancel/{job_id}", response_model=DispersionEstimateStatusResponse)
def cancel_dispersion(job_id: str) -> DispersionEstimateStatusResponse:
    """Cancel an in-progress dispersion-estimation job."""
    return _dispersion_status_response(_train_service.cancel_dispersion(job_id))


@router.post("/estimate", response_model=TrainEstimateResponse)
def estimate_training(body: TrainEstimateRequest) -> TrainEstimateResponse:
    """Estimate RAM and row requirements for training a modelling node.

    Reads ancestor metadata for the analytical RAM/VRAM estimate and, once
    the evaluation configuration is complete, materialises only the bounded
    target/evaluation-key projection needed for an exact partition preview.
    """
    graph = _prepare_runtime_graph(body.graph)
    body = body.model_copy(update={"graph": graph})
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

    # estimated_bytes already includes all training phases (evaluation
    # partitions, pools, CatBoost internals, diagnostics, and bounded tuning).
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
        if vram_check.insufficient and vram_check.warning:
            vram_check.warning += (
                " Switch task_type to CPU or reduce rows/features before starting GPU training."
            )

    evaluation_preview = _train_service.evaluation_preview(
        body,
        row_limit=safe_limit,
    )
    evaluation_preview_payload = (
        EvaluationPreviewPayload.model_validate(evaluation_preview)
        if evaluation_preview is not None
        else None
    )

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
        evaluation_preview=evaluation_preview_payload,
    )


@router.get("/mlflow/check", response_model=MlflowCheckResponse)
async def mlflow_check() -> MlflowCheckResponse:
    """Check whether MLflow is installed and detect the tracking backend."""
    import importlib
    import importlib.util

    if importlib.util.find_spec("mlflow") is None:
        return MlflowCheckResponse(
            mlflow_installed=False,
            mlflow_importable=False,
            tracking_configured=False,
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
    if result.evaluation is None:
        raise HTTPException(
            status_code=400,
            detail="Completed training result has no evaluation report",
        )

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
            final_test_metrics=result.final_test_metrics,
            selection_metrics={
                name: summary.model_dump(mode="json")
                for name, summary in result.evaluation.selection_metrics.items()
            },
            evaluation=result.evaluation.model_dump(mode="json"),
            tuning=(result.tuning.model_dump(mode="json") if result.tuning is not None else None),
            diagnostics_set=result.diagnostics_set,
            # GLM diagnostics must reach MLflow too — dropping them meant a
            # GLM logged via this button lost its coefficients, relativities,
            # fit statistics, and regularization path (CODE_REVIEW 4b.8).
            glm_coefficients=result.glm_coefficients,
            glm_relativities=result.glm_relativities,
            glm_fit_statistics=result.glm_fit_statistics,
            glm_regularization_path=result.glm_regularization_path,
        )

        # Signature metadata comes from the model's persisted feature
        # contract — ``TrainingJob._save_artifacts`` writes it next to the
        # model file on every real run (per-model name, remediation 4b.9).
        # Guessing here (the old behaviour defaulted every feature to
        # Float64) logged a signature that contradicted what the model
        # consumes at scoring time, so a logged-then-reloaded model could
        # not score (CODE_REVIEW 4b.8).  A model file without a contract is
        # an error, not a reason to fabricate one.
        features = result.features
        feature_types: dict[str, str] = {}
        categorical_features = list(result.cat_features)
        target_name = str(config.get("target", "") or "")
        target_type = ""
        offset_name = str(config.get("offset", "") or "")
        model_file = Path(result.model_path) if result.model_path else None
        if model_file is not None and model_file.exists():
            from haute.modelling._feature_contract import load_contract_cached
            from haute.modelling._training_job import model_contract_filename

            contract = load_contract_cached(
                model_file.parent / model_contract_filename(model_file.stem)
            )
            features = list(contract.features)
            feature_types = dict(contract.feature_types)
            categorical_features = list(contract.categorical_features)
            target_name = contract.target_name
            target_type = contract.target_type
            offset_name = contract.offset_column or ""

        metadata = ModelCardMetadata(
            algorithm=config.get("algorithm", "catboost"),
            task=config.get("task", "regression"),
            development_rows=result.development_rows,
            final_test_rows=result.final_test_rows,
            features=features,
            evaluation_config=config.get("evaluation", {}),
            best_iteration=result.best_iteration,
            feature_types=feature_types,
            categorical_features=categorical_features,
            target_name=target_name,
            target_type=target_type,
            offset_name=offset_name,
            offset_type="Float64" if offset_name else "",
        )

        final_params = (
            result.tuning.final_params if result.tuning is not None else config.get("params", {})
        )
        artifact_paths = {
            "evaluation_plan": result.evaluation.plan_path,
            "evaluation_results": result.evaluation.results_path,
            "evaluation_report": result.evaluation.report_path,
        }
        if result.tuning is not None:
            artifact_paths.update(
                {
                    "tuning_plan": result.tuning.plan_path,
                    "tuning_trials": result.tuning.trials_path,
                    "tuning_report": result.tuning.report_path,
                }
            )
        log_result = await run_in_threadpool(
            log_experiment,
            experiment_name=experiment_name,
            run_name=node_label,
            metrics=result.final_test_metrics or result.diagnostic_metrics,
            params={
                "algorithm": config.get("algorithm", "catboost"),
                "task": config.get("task", "regression"),
                "target": config.get("target", ""),
                "weight": config.get("weight", ""),
                "evaluation_strategy": config.get("evaluation", {}).get("strategy", ""),
                **{f"param_{key}": value for key, value in final_params.items()},
            },
            diagnostics=diagnostics,
            metadata=metadata,
            model_path=result.model_path or None,
            model_name=model_name,
            artifact_paths=artifact_paths,
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
    try:
        script = generate_training_script(config, data_path)
    except TrainingConfigError as exc:
        logger.warning("modelling_export_invalid_config", error=str(exc), node_id=body.node_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
