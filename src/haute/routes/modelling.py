"""Modelling endpoints: train, status, export, save."""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._file_ops import atomic_copy_files
from haute._logging import get_logger
from haute._path_resolution import RuntimePathError
from haute._sandbox import _get_project_root
from haute.errors import HauteValidationError
from haute.modelling._model_export import (
    MODEL_FILE_SUFFIXES,
    resolve_model_export_destination,
)
from haute.modelling._train_config import TrainingConfigError
from haute.routes._export_receipts import (
    export_receipts,
    mlflow_receipt_for_operation,
    receipt_timestamp,
    record_receipt,
    single_flight_mlflow_log,
)
from haute.routes._job_lifecycle import require_job_status
from haute.routes._job_store import get_job_store
from haute.routes._mlflow_log_errors import mlflow_log_http_exception, require_mlflow_installed
from haute.routes._runtime_path_errors import runtime_path_http_exception
from haute.routes._train_service import (
    TrainService,
    _assert_json_finite,
    _check_gpu_vram,
    _clamp_row_limit,
    _default_train_timeout,
    _find_modelling_node,
    _VramCheck,
)
from haute.routes._training_artifacts import (
    hold_training_artifacts,
    require_training_artifacts,
)
from haute.routes._training_preparation import estimate_training_memory
from haute.routes.pipeline import _prepare_runtime_graph
from haute.schemas import (
    DispersionEstimateRequest,
    DispersionEstimateResponse,
    DispersionEstimateStatusResponse,
    EvaluationPreviewPayload,
    ExportScriptRequest,
    ExportScriptResponse,
    GpuFamilyStatus,
    LogExperimentRequest,
    LogExperimentResponse,
    MlflowExportReceipt,
    ModelCacheClearResponse,
    ModelFileExportReceipt,
    ModellingGpuStatusResponse,
    ModelSaveDestinationRequest,
    ModelSaveDestinationResponse,
    SaveModelRequest,
    SaveModelResponse,
    TrainEstimateRequest,
    TrainEstimateResponse,
    TrainEstimateUnavailable,
    TrainExportReceipts,
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


@router.get("/gpu", response_model=ModellingGpuStatusResponse)
async def gpu_status() -> ModellingGpuStatusResponse:
    """Whether XGBoost can train on a GPU here (probed once per process)."""
    from haute.modelling._gpu import xgboost_gpu_status

    status = await run_in_threadpool(xgboost_gpu_status)
    return ModellingGpuStatusResponse(xgboost=GpuFamilyStatus(**status.to_plain_data()))


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
        export_receipts=TrainExportReceipts.model_validate(export_receipts(job)),
    )


def _log_response_from_receipt(receipt: Mapping[str, Any]) -> LogExperimentResponse:
    return LogExperimentResponse(
        status="ok",
        backend=receipt["backend"],
        experiment_name=receipt["experiment_name"],
        run_id=receipt["run_id"],
        run_url=receipt.get("run_url"),
        tracking_uri=receipt.get("tracking_uri", ""),
        operation_id=receipt["operation_id"],
        logged_at=receipt["logged_at"],
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


def _dispersion_status_response(job: Mapping[str, Any]) -> DispersionEstimateStatusResponse:
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

    Reads reusable snapshot/ancestor metadata for the analytical RAM/VRAM
    estimate and, once the evaluation configuration is complete, materialises
    only the bounded target/evaluation-key projection needed for an exact
    partition preview.
    """
    graph = _prepare_runtime_graph(body.graph)
    body = body.model_copy(update={"graph": graph})
    node = _find_modelling_node(body.graph, body.node_id)

    # A size the estimator cannot prove comes back as an unavailable estimate
    # with its reason; an exception here is a failure and reaches the error path.
    ram_est = estimate_training_memory(
        body.graph,
        body.node_id,
        source=body.source,
    )

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

    # GPU VRAM estimation — use feature count (not total columns), since
    # CatBoost and XGBoost only load features to the GPU.
    # An unavailable estimate has no rows or columns to size VRAM from.
    vram_check = _VramCheck()
    node_params = node.data.config.get("params", {})
    algorithm = str(node.data.config.get("algorithm", "catboost")).lower()
    xgboost_gpu = algorithm == "xgboost" and node.data.config.get("device") == "gpu"
    if ram_est.unavailable_reason is None and (
        xgboost_gpu or str(node_params.get("task_type", "")).upper() == "GPU"
    ):
        effective_rows = ram_est.total_rows or 0
        # Feature count = total cols - excluded - target - weight
        n_excluded = len(node.data.config.get("exclude", []))
        n_non_feature = n_excluded + 1  # +1 for target
        if node.data.config.get("weight"):
            n_non_feature += 1
        n_features = max(ram_est.probe_columns - n_non_feature, 1)
        vram_check = _check_gpu_vram(
            effective_rows,
            n_features,
            node_params,
            algorithm="xgboost" if xgboost_gpu else "catboost",
        )
        if vram_check.insufficient and vram_check.warning:
            vram_check.warning += (
                " Train on CPU or reduce rows/features before starting GPU training."
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

    # estimated_bytes already includes all training phases (evaluation
    # partitions, pools, CatBoost internals, diagnostics, and bounded tuning),
    # so the training figure needs no further overhead.
    data_mb = (
        round(ram_est.estimated_bytes / 1024**2, 1) if ram_est.estimated_bytes is not None else None
    )
    unavailable = (
        TrainEstimateUnavailable(
            reason=ram_est.unavailable_reason.value,
            blocking_node_id=ram_est.blocking_node_id,
        )
        if ram_est.unavailable_reason is not None
        else None
    )

    return TrainEstimateResponse(
        total_rows=ram_est.total_rows,
        safe_row_limit=safe_limit,
        estimated_mb=data_mb,
        training_mb=data_mb,
        available_mb=round(ram_est.available_bytes / 1024**2, 1),
        bytes_per_row=(
            round(ram_est.bytes_per_row, 1) if ram_est.bytes_per_row is not None else None
        ),
        unavailable=unavailable,
        unbounded_join_node_ids=list(ram_est.unbounded_join_node_ids),
        was_downsampled=was_downsampled,
        warning=warning,
        gpu_vram_estimated_mb=vram_check.estimated_mb,
        gpu_vram_available_mb=vram_check.available_mb,
        gpu_warning=vram_check.warning,
        evaluation_preview=evaluation_preview_payload,
    )


@router.post("/mlflow/log", response_model=LogExperimentResponse)
async def mlflow_log(body: LogExperimentRequest) -> LogExperimentResponse:
    """Log a completed training job to MLflow as a contracted candidate run."""
    from haute.errors import MlflowConfigError
    from haute.modelling._candidate_run import (
        CandidateArtifacts,
        CandidateProvenance,
        build_candidate_run,
    )
    from haute.modelling._descriptors import algorithm_descriptor, project_refit_params
    from haute.modelling._mlflow_log import (
        log_experiment,
        resolve_experiment_name,
        resolve_tracking_backend,
    )
    from haute.modelling._result_types import ModelDiagnostics

    require_mlflow_installed()
    recorded = mlflow_receipt_for_operation(
        _store.require_completed_job(body.job_id), body.operation_id
    )
    if recorded is not None:
        return _log_response_from_receipt(recorded)
    with hold_training_artifacts(body.job_id), single_flight_mlflow_log(body.job_id):
        job = _store.require_completed_job(body.job_id)
        # A concurrent request for this operation may have finished while this
        # one waited to start.
        recorded = mlflow_receipt_for_operation(job, body.operation_id)
        if recorded is not None:
            return _log_response_from_receipt(recorded)
        result: TrainResponse | None = job.get("result")
        if result is None or result.evaluation is None:
            raise HTTPException(
                status_code=400,
                detail="Completed training result has no evaluation report",
            )
        artifacts = require_training_artifacts(job)
        config = job.get("config", {})
        node_label = job.get("node_label", "model")

        try:
            _tracking_uri, backend = resolve_tracking_backend(body.destination)
        except MlflowConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # The request carries the node's current Export setting; the job's
        # training-time config snapshot is never a fallback.
        experiment_name = resolve_experiment_name(
            explicit=body.experiment_name,
            node_label=node_label,
            backend=backend,
        )

        try:
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
                tuning=(
                    result.tuning.model_dump(mode="json") if result.tuning is not None else None
                ),
                diagnostics_set=result.diagnostics_set,
                glm_coefficients=result.glm_coefficients,
                glm_relativities=result.glm_relativities,
                glm_fit_statistics=result.glm_fit_statistics,
                glm_inference=result.glm_inference,
                glm_smooth_terms=result.glm_smooth_terms,
                glm_regularization=result.glm_regularization,
                ebm_terms=result.ebm_terms,
            )
            if result.tuning is not None:
                final_params = result.tuning.final_params
            elif (
                result.final_tree_count is not None
                and result.evaluation.refit_on_development
                and algorithm_descriptor(str(config.get("algorithm", "catboost"))).refit_policy
                == "validation_weighted_rounds"
            ):
                final_params = project_refit_params(
                    algorithm_descriptor(str(config.get("algorithm", "catboost"))),
                    dict(config.get("params") or {}),
                    result.final_tree_count,
                )
            else:
                final_params = config.get("params", {})
            candidate = build_candidate_run(
                provenance=CandidateProvenance.from_plain_data(job["provenance"]),
                algorithm=str(config.get("algorithm", "catboost")),
                weight=str(config.get("weight") or ""),
                evaluation_strategy=result.evaluation.strategy,
                validation_method=result.evaluation.validation_method,
                evaluation_config=config.get("evaluation", {}),
                evaluation_plan_sha256=result.evaluation.plan_sha256,
                final_params=final_params,
                final_test_metrics=result.final_test_metrics,
                development_metrics=(
                    result.diagnostic_metrics if result.diagnostics_set == "development" else {}
                ),
                diagnostics=diagnostics,
                development_rows=result.development_rows,
                final_test_rows=result.final_test_rows,
                best_iteration=result.best_iteration,
                fit_evidence=(
                    result.fit_evidence.model_dump() if result.fit_evidence is not None else None
                ),
                artifacts=CandidateArtifacts(
                    model=artifacts.model,
                    feature_contract=artifacts.feature_contract,
                    evidence=artifacts.evidence_paths(),
                ),
            )
            log_result = await run_in_threadpool(
                log_experiment,
                experiment_name=experiment_name,
                candidate=candidate,
                destination=body.destination,
            )

            receipt = MlflowExportReceipt(
                operation_id=body.operation_id or uuid.uuid4().hex,
                destination=body.destination,
                backend=log_result.backend,
                experiment_name=log_result.experiment_name,
                run_id=log_result.run_id,
                run_url=log_result.run_url,
                tracking_uri=log_result.tracking_uri,
                logged_at=receipt_timestamp(),
            )
            record_receipt(_store, body.job_id, "mlflow", receipt.model_dump(mode="json"))
            return _log_response_from_receipt(receipt.model_dump(mode="json"))
        except HTTPException:
            raise
        except HauteValidationError as exc:
            # Haute's own artifact checks (an unloadable model, an incomplete
            # contract) fail before any run is created and say how to fix it.
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:
            raise mlflow_log_http_exception(exc, job_id=body.job_id) from None


@router.post("/save/destination", response_model=ModelSaveDestinationResponse)
def model_save_destination(body: ModelSaveDestinationRequest) -> ModelSaveDestinationResponse:
    """Resolve where "Save model to file" would write, without writing."""
    try:
        destination = resolve_model_export_destination(
            body.output_path,
            model_suffix=MODEL_FILE_SUFFIXES[body.algorithm],
            project_root=_get_project_root(),
        )
    except RuntimePathError as exc:
        raise runtime_path_http_exception(exc) from None
    return ModelSaveDestinationResponse(
        path=destination.display_path,
        suffix_mismatch=destination.suffix_mismatch,
    )


_model_destination_locks_guard = threading.Lock()
_model_destination_locks: dict[str, threading.Lock] = {}


@contextmanager
def _model_destination_lock(contract_path: Path) -> Iterator[None]:
    """Serialise saves that publish to the same feature contract path.

    The contract path follows from the model file's stem, so every save that
    could write either file of a pair shares it. Holding it across the
    existence check and the publication means two saves can never both pass
    ``overwrite=False`` or leave a model from one job beside another's contract.
    """
    key = os.path.normcase(os.path.abspath(contract_path))
    with _model_destination_locks_guard:
        lock = _model_destination_locks.setdefault(key, threading.Lock())
    with lock:
        yield


@router.post("/save", response_model=SaveModelResponse)
def save_model(body: SaveModelRequest) -> SaveModelResponse:
    """Save a completed training job's model and feature contract to a project file."""
    from haute.modelling._training_job import model_contract_filename

    with hold_training_artifacts(body.job_id):
        job = _store.require_completed_job(body.job_id)
        artifacts = require_training_artifacts(job)
        source_model = artifacts.model

        try:
            destination = resolve_model_export_destination(
                body.output_path,
                model_suffix=source_model.suffix,
                project_root=_get_project_root(),
            )
        except RuntimePathError as exc:
            raise runtime_path_http_exception(exc) from None
        if destination.suffix_mismatch:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model file path must end with {source_model.suffix}, "
                    "the trained model's format."
                ),
            )

        contract_name = model_contract_filename(destination.path.stem)
        destination_contract = destination.path.with_name(contract_name)
        contract_display_path = str(
            PurePosixPath(destination.display_path).with_name(contract_name)
        )
        with _model_destination_lock(destination_contract):
            if not body.overwrite and (destination.path.exists() or destination_contract.exists()):
                # A structured detail: the Export pane dispatches on the code, not the status.
                exists_detail = {
                    "error_code": "model_file_exists",
                    "message": f"Model file already exists: {destination.display_path}",
                }
                raise HTTPException(status_code=409, detail=exists_detail)

            try:
                destination.path.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy_files(
                    [
                        (artifacts.feature_contract, destination_contract),
                        (source_model, destination.path),
                    ]
                )
            except OSError as exc:
                logger.error("model_save_failed", error=str(exc), job_id=body.job_id, exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail="Filesystem error saving the model. Check the server logs for details.",
                ) from None
    logger.info("model_saved", path=str(destination.path), job_id=body.job_id)
    record_receipt(
        _store,
        body.job_id,
        "model_files",
        ModelFileExportReceipt(
            path=destination.display_path,
            feature_contract_path=contract_display_path,
            saved_at=receipt_timestamp(),
        ).model_dump(mode="json"),
    )
    return SaveModelResponse(
        status="ok",
        path=destination.display_path,
        feature_contract_path=contract_display_path,
    )


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
