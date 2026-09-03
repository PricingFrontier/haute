"""State-owning lifecycle orchestration for training and dispersion jobs."""

from __future__ import annotations

import math
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
from fastapi import HTTPException
from pydantic import ValidationError

from haute._env import int_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_admitted_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._logging import get_logger
from haute._sandbox import _get_project_root
from haute._types import PipelineGraph
from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
    isolated_worker_failure_is_memory,
    isolated_worker_memory_detail,
    run_isolated_worker,
    worker_config_for_memory_policy,
)
from haute._worker_protocol import (
    WorkerProgressEvent,
    WorkerProtocolError,
    WorkerRequest,
    WorkerResultManifest,
)
from haute.errors import BoundedMemoryUnsupportedError, HauteValidationError
from haute.execution import (
    AllExceptColumns,
    build_dataframe_execution_cache_request,
    dataframe_graph_input_fingerprint,
    execute_lazy_graph,
)
from haute.modelling._algorithms import ALGORITHM_REGISTRY, resolve_loss_function
from haute.modelling._evaluation import (
    EvaluationConfig,
    generate_evaluation_plan,
)
from haute.modelling._train_config import (
    TrainingConfigError,
    build_train_params,
    build_training_job_kwargs,
    parse_evaluation_config,
    parse_tuning_config,
    training_objective_issue,
)
from haute.routes._background_jobs import (
    CancellableJobRegistry,
    IsolatedJobSupervisor,
    IsolatedSupervisorThread,
)
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_http_exception,
)
from haute.routes._job_lifecycle import (
    JobLifecycle,
    TerminalReason,
    bind_running_execution_metrics_publisher,
)
from haute.routes._job_store import JobSnapshot, JobStore, RunningJobFields
from haute.routes._training_artifacts import (
    _EVALUATION_ARTIFACT_PATHS,
    _TRAINING_ARTIFACT_KINDS,
    _TUNING_ARTIFACT_PATHS,
    _max_training_artifact_bytes,
    _publish_training_artifacts,
)
from haute.routes._training_evaluation import (
    _DISPERSION_ESTIMATE_ROW_CAP,
    _DISPERSION_PARAM_FAMILIES,
    _DISPERSION_PARAM_STUBS,
    _evaluation_preview_payload,
    _validate_glm_family_link,
)
from haute.routes._training_preparation import (
    TrainingPreparationOutcome,
    TrainingPreparationRequest,
    _check_gpu_vram,
    _clamp_row_limit,
    _declared_categorical_levels_for_training,
    _find_modelling_node,
    _gpu_vram_http_exception,
    _http_failure_job_parts,
    _memory_limit_http_exception,
    _remove_prepared_parquet,
    _seeded_training_sample,
    _training_projection_keep_columns,
    _training_required_columns_by_node,
    create_training_parquet_path,
    prepare_training_data_worker,
)
from haute.routes._training_worker import (
    _assert_json_finite,
    _friendly_error,
    _job_elapsed_seconds,
    _max_train_loss_history,
    _run_dispersion_process_job,
    _run_training_process_job,
    _training_context_phrase,
)
from haute.schemas import (
    DispersionEstimateRequest,
    DispersionEstimateResponse,
    EvaluationReportPayload,
    TrainEstimateRequest,
    TrainingFeatureSelectionDiagnosticPayload,
    TrainRequest,
    TrainResponse,
    TrainStatusResponse,
)

logger = get_logger(component="server.modelling.train")


_TRAINING_JOB_TYPE: Literal["training"] = "training"
_DISPERSION_JOB_TYPE: Literal["dispersion_estimate"] = "dispersion_estimate"
_JOB_TYPE_KEY = "job_type"


class _TrainingRunningJob(RunningJobFields):
    job_type: Literal["training"]
    progress: float
    config: dict[str, Any]
    node_label: str
    start_time: float
    timeout: int | float


class _DispersionRunningJob(RunningJobFields):
    job_type: Literal["dispersion_estimate"]
    progress: float
    param: Literal["theta", "var_power"]
    node_label: str
    start_time: float
    timeout: int | float


# Row cap for dispersion estimation. The profile search runs ~10-30 IRLS
# fits, so the estimate samples the training frame (seeded, deterministic —
# same sampler as training's RAM downsample) rather than paying full-data


def _default_train_timeout() -> int:
    return int_env("HAUTE_TRAIN_TIMEOUT", 3600)


def _worker_timing(job: Mapping[str, Any], *, job_id: str) -> tuple[float, float]:
    raw_start = job.get("start_time")
    if isinstance(raw_start, bool) or not isinstance(raw_start, int | float):
        raise RuntimeError(f"Background job {job_id!r} has no valid start_time")
    raw_timeout = job.get("timeout")
    if (
        isinstance(raw_timeout, bool)
        or not isinstance(raw_timeout, int | float)
        or raw_timeout <= 0
    ):
        raise RuntimeError(f"Background job {job_id!r} has no valid timeout")
    return float(raw_start), float(raw_timeout)


_WINDOWS_ARTIFACT_REPLACE_RETRIES = 3
_WINDOWS_ARTIFACT_REPLACE_RETRY_DELAY_SECONDS = 0.1


class TrainService:
    """Orchestrates the full training lifecycle.

    Parameters
    ----------
    store:
        The in-memory job store used to track training jobs.
    """

    def __init__(
        self,
        store: JobStore,
        *,
        protocol_runner: Any | None = None,
    ) -> None:
        self._store = store
        self._lifecycle = JobLifecycle(store)
        self._supervisor = IsolatedJobSupervisor(
            self._lifecycle,
            protocol_runner=protocol_runner,
        )
        self._training_jobs = CancellableJobRegistry()
        self._start_lock = threading.Lock()
        self._preparation_threads_lock = threading.Lock()
        self._preparation_threads: dict[str, threading.Thread] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start(self, body: TrainRequest) -> TrainResponse:
        """Validate and return a cancellable handle before preparing training data.

        Returns a ``TrainResponse`` with status ``"started"`` and the job ID.
        Raises ``HTTPException`` only for synchronous validation or preparation-thread
        launch failures. Preparation and fit outcomes are published through job status.
        """
        node = _find_modelling_node(body.graph, body.node_id)
        config = node.data.config
        declared_categorical_levels = _declared_categorical_levels_for_training(
            body.graph,
            body.node_id,
            config,
        )
        if declared_categorical_levels:
            config = {**config, "categorical_levels": declared_categorical_levels}

        self._validate_config(config)

        with self._start_lock:
            self._check_no_concurrent_jobs()
            start_time = time.monotonic()
            initial_job: _TrainingRunningJob = {
                "status": "running",
                "job_type": _TRAINING_JOB_TYPE,
                "progress": 0.0,
                "message": "Preparing training data...",
                "config": dict(config),
                "node_label": node.data.label,
                "start_time": start_time,
                "timeout": config.get("timeout", _default_train_timeout()),
            }
            job_id = self._store.create_job(initial_job)

        cancellation_token = ExecutionCancellationToken()
        self._training_jobs.register_latest(
            (_TRAINING_JOB_TYPE, job_id), job_id, execution_token=cancellation_token
        )

        def prepare() -> None:
            try:
                self._prepare_and_launch_training(
                    job_id, body, body.node_id, config, cancellation_token
                )
            except Exception:
                # The preparation owner converts expected failures to terminal
                # job state itself. This final boundary prevents teardown/store
                # infrastructure races from escaping as an unhandled daemon-thread
                # exception while still leaving a server-side diagnostic.
                logger.exception(
                    "training_preparation_thread_failed",
                    job_id=job_id,
                )
            finally:
                with self._preparation_threads_lock:
                    self._preparation_threads.pop(job_id, None)

        thread = threading.Thread(
            target=prepare,
            name=f"haute-training-prep-{job_id}",
            daemon=True,
        )
        with self._preparation_threads_lock:
            self._preparation_threads[job_id] = thread
        try:
            thread.start()
        except Exception as exc:
            with self._preparation_threads_lock:
                self._preparation_threads.pop(job_id, None)
            self._training_jobs.release(job_id)
            logger.exception(
                "training_preparation_thread_start_failed",
                job_id=job_id,
            )
            self._lifecycle.transition(
                job_id,
                to="error",
                message="Training preparation failed to start. Check the server logs for details.",
                fields={"error": str(exc)},
            )
            raise HTTPException(
                status_code=500,
                detail="Training preparation failed to start. Check the server logs for details.",
            ) from exc
        return TrainResponse(status="started", job_id=job_id)

    def evaluation_preview(
        self,
        body: TrainEstimateRequest,
        *,
        row_limit: int | None,
    ) -> dict[str, Any] | None:
        """Materialise only evaluation keys and return the exact bounded plan summary.

        An incomplete setup deliberately has no preview. Once target, objective,
        and canonical evaluation fields are complete, data-dependent failures
        (class counts, empty group/date partitions, invalid temporal values)
        fail preflight explicitly instead of inventing an approximate summary.
        """
        node = _find_modelling_node(body.graph, body.node_id)
        config = node.data.config
        target = config.get("target")
        task = config.get("task", "regression")
        raw_evaluation = config.get("evaluation")
        if (
            not isinstance(target, str)
            or not target
            or task not in {"regression", "classification"}
            or not isinstance(raw_evaluation, dict)
            or training_objective_issue(config) is not None
        ):
            return None
        try:
            evaluation = EvaluationConfig.from_plain_data(raw_evaluation)
        except (TypeError, ValueError):
            return None

        key_column = (
            evaluation.group_column
            if evaluation.strategy == "group"
            else (evaluation.date_column if evaluation.strategy == "temporal" else None)
        )
        selected_columns = list(
            dict.fromkeys(
                [
                    target,
                    *([key_column] if key_column is not None else []),
                ]
            )
        )
        execution_context: ExecutionContext | None = None
        try:
            execution_context = create_admitted_execution_context(
                operation="training_evaluation_preview",
                profile=ExecutionProfile.TRAINING_PREP,
            )
            preamble_ns = self._compile_preamble(body.graph)
            required_columns_by_node = _training_required_columns_by_node(
                body.node_id,
                config,
            )
            from haute._polars_utils import (
                DEFAULT_STREAMING_CHUNK_SIZE,
                streaming_collect,
            )
            from haute.executor import _build_node_fn

            cache_request = build_dataframe_execution_cache_request(
                body.graph,
                node_ids=[body.node_id],
                namespace="training_evaluation_preview",
                source=body.source,
                profile=execution_context.profile,
                input_fingerprint=dataframe_graph_input_fingerprint(
                    body.graph,
                    target_node_id=body.node_id,
                    source=body.source,
                ),
                target_node_id=body.node_id,
                required_columns_by_node=required_columns_by_node,
                enforce_contracts=True,
                preamble_ns_supplied=preamble_ns is not None,
                streaming_chunk_size=DEFAULT_STREAMING_CHUNK_SIZE,
            )
            lazy_outputs, *_ = execute_lazy_graph(
                body.graph,
                _build_node_fn,
                target_node_id=body.node_id,
                preamble_ns=preamble_ns,
                source=body.source,
                enforce_contracts=True,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
                dataframe_cache_request=cache_request,
            )
            evaluation_lf = lazy_outputs.get(body.node_id)
            if evaluation_lf is None:
                raise HauteValidationError("No training data arrived at the modelling node.")
            if row_limit is not None:
                evaluation_lf = _seeded_training_sample(
                    evaluation_lf,
                    row_limit,
                )
            available_columns = set(evaluation_lf.collect_schema().names())
            missing_columns = sorted(set(selected_columns) - available_columns)
            if missing_columns:
                raise HauteValidationError(
                    f"evaluation preview is missing required column(s): {missing_columns}"
                )
            projection = [
                (
                    pl.col(column).cast(pl.String).alias(column)
                    if column == evaluation.date_column
                    else pl.col(column)
                )
                for column in selected_columns
            ]
            frame = streaming_collect(
                evaluation_lf.filter(pl.col(target).is_not_null()).select(projection),
                execution_context=execution_context,
            )
            if frame.height < 1:
                raise HauteValidationError(f"Target column {target!r} contains only null values")
            target_values = frame[target].to_list() if task == "classification" else None
            group_values = (
                frame[evaluation.group_column].to_list()
                if evaluation.group_column is not None
                else None
            )
            date_values = (
                frame[evaluation.date_column].to_list()
                if evaluation.date_column is not None
                else None
            )
            plan = generate_evaluation_plan(
                evaluation,
                source_sha256="0" * 64,
                row_count=frame.height,
                task=str(task),
                target_values=target_values,
                group_values=group_values,
                date_values=date_values,
            )
            return _evaluation_preview_payload(
                plan,
                date_values=date_values,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Evaluation preview failed: {exc}",
            ) from exc
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            raise _memory_limit_http_exception(exc) from None
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            raise contract_error_http_exception(exc) from None
        except BoundedMemoryUnsupportedError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Evaluation preview cannot run in bounded mode: {exc}",
            ) from exc
        finally:
            if execution_context is not None:
                execution_context.release_admission(preserve_primary_error=True)

    def _join_preparation(self, job_id: str, *, timeout: float = 10.0) -> None:
        """Wait for an in-flight preparation owner (used by shutdown/tests)."""
        with self._preparation_threads_lock:
            thread = self._preparation_threads.get(job_id)
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise TimeoutError(f"Training preparation for job {job_id!r} is still running")

    def _prepare_and_launch_training(
        self,
        job_id: str,
        body: TrainRequest,
        node_id: str,
        config: dict[str, Any],
        cancellation_token: ExecutionCancellationToken,
    ) -> None:
        """Run expensive preparation in the request-owned daemon thread."""
        execution_context: ExecutionContext | None = None
        worker_owns_cleanup = False
        try:
            cancellation_token.throw_if_cancelled("training_preparation", job_id=job_id)
            preamble_ns = self._compile_preamble(body.graph)
            cancellation_token.throw_if_cancelled("training_preamble", job_id=job_id)
            ram_warning, row_limit, total_source_rows, probe_columns = self._estimate_ram(
                body.graph, node_id, preamble_ns, job_id, source=body.source
            )
            cancellation_token.throw_if_cancelled("training_memory_estimate", job_id=job_id)
            user_limit = config.get("row_limit")
            row_limit = _clamp_row_limit(row_limit, user_limit)
            if (
                ram_warning
                and user_limit
                and isinstance(user_limit, (int, float))
                and int(user_limit) > 0
                and row_limit == int(user_limit)
            ):
                ram_warning = None
                self._store.update_job(job_id, warning=None)
            train_params = build_train_params(config)
            ram_warning = self._check_gpu_vram_before_launch(
                train_params, row_limit, total_source_rows, probe_columns, ram_warning, job_id
            )
            cancellation_token.throw_if_cancelled("training_preparation", job_id=job_id)
            execution_context = create_admitted_execution_context(
                operation="training_pipeline",
                profile=ExecutionProfile.TRAINING_PREP,
                job_id=job_id,
                cancellation_token=cancellation_token,
            )
            bind_running_execution_metrics_publisher(self._store, job_id, execution_context)
            execution_context.checkpoint(label="training_preparation")
            tmp_parquet = self._execute_and_sink(
                body,
                preamble_ns,
                row_limit,
                job_id,
                exclude=config.get("exclude") or None,
                keep_columns=_training_projection_keep_columns(config),
                required_columns_by_node=_training_required_columns_by_node(node_id, config),
                execution_context=execution_context,
            )
            execution_context.checkpoint(label="training_preparation_complete")
            feature_selection = self._store.require_job(job_id).get("feature_selection")
            launch_config = config
            if "output_dir" not in launch_config:
                from haute.executor import _pipeline_dir

                pipeline_dir = _pipeline_dir(body.graph)
                launch_config = {
                    **launch_config,
                    "output_dir": str(pipeline_dir / "outputs") if pipeline_dir else "outputs",
                }
            self._launch_background(
                job_id,
                node_id,
                launch_config,
                train_params,
                tmp_parquet,
                ram_warning,
                total_source_rows,
                feature_selection=feature_selection,
                execution_context=execution_context,
            )
            worker_owns_cleanup = True
        except ExecutionCancelledError:
            reason = self._training_jobs.cancellation_reason(job_id) or "cancelled"
            self._lifecycle.transition(
                job_id,
                to=reason,
                message="Cancelled" if reason == "cancelled" else reason,
                elapsed_seconds=_job_elapsed_seconds(self._store.require_job(job_id)),
            )
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            self._persist_preparation_http_failure(job_id, _memory_limit_http_exception(exc))
        except HTTPException as exc:
            self._persist_preparation_http_failure(job_id, exc)
        except Exception as exc:
            logger.exception("training_preparation_failed", job_id=job_id)
            self._lifecycle.transition(
                job_id,
                to="error",
                message=_friendly_error(exc, context=_training_context_phrase(config)),
                fields={"error": str(exc)},
            )
        finally:
            if not worker_owns_cleanup:
                if execution_context is not None:
                    execution_context.release_admission(preserve_primary_error=True)
                self._training_jobs.release(job_id)

    def _persist_preparation_http_failure(self, job_id: str, exc: HTTPException) -> None:
        message, fields = _http_failure_job_parts(exc, job_id=job_id)
        terminal: TerminalReason
        if exc.status_code == 507:
            terminal = "memory_limited"
        elif 400 <= exc.status_code < 500:
            terminal = "contract_error"
        else:
            terminal = "error"
        self._lifecycle.transition(
            job_id,
            to=terminal,
            message=message,
            fields=fields,
        )

    def cancel(self, job_id: str) -> JobSnapshot:
        """Cancel a running training job."""
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _TRAINING_JOB_TYPE:
            raise HTTPException(status_code=404, detail=f"Training job '{job_id}' not found")
        if job.get("status") != "running":
            return job
        self._training_jobs.cancel(job_id, reason="cancelled")
        updated_job = self._lifecycle.transition(
            job_id,
            to="cancelled",
            message="Cancelled",
            elapsed_seconds=_job_elapsed_seconds(job),
        )
        return updated_job if updated_job is not None else self._store.require_job(job_id)

    def reject_completed_result(self, job_id: str, *, message: str) -> JobSnapshot:
        """Correct a completed job whose result cannot satisfy the API contract."""
        corrected = self._lifecycle.transition(
            job_id,
            to="error",
            message=message,
            fields={"result": None},
            expected_status="completed",
        )
        return corrected if corrected is not None else self._store.require_job(job_id)

    def timeout(self, job_id: str, *, timeout: int, start_time: float) -> JobSnapshot:
        """Mark a running training job as timed out and request worker cancellation."""
        self._training_jobs.cancel(job_id, reason="timed_out")
        updated_job = self._lifecycle.transition(
            job_id,
            to="timed_out",
            message=(
                f"Training timed out after {timeout}s. Increase timeout or simplify the model."
            ),
            elapsed_seconds=time.monotonic() - start_time,
        )
        return updated_job if updated_job is not None else self._store.require_job(job_id)

    # ------------------------------------------------------------------
    # Dispersion estimation (NB theta / Tweedie var_power)
    # ------------------------------------------------------------------

    def start_dispersion_estimate(
        self, body: DispersionEstimateRequest
    ) -> DispersionEstimateResponse:
        """Estimate a GLM dispersion parameter on the node's training data.

        Materialises the training frame exactly as ``start()`` would (same
        pipeline execution, projection, and seeded row sampling), then runs
        a profile-likelihood search in a supervised spawn worker. The estimate is
        an explicit user action: the resolved value lands in the node config
        where the training-objective gate requires it — never as a hidden
        default (RustyStats fits silently at theta=1.0 / var_power=1.5).
        """
        node = _find_modelling_node(body.graph, body.node_id)
        config = dict(node.data.config)
        self._validate_dispersion_config(config, body.param)

        with self._start_lock:
            self._check_no_concurrent_jobs()
            start_time = time.monotonic()
            initial_job: _DispersionRunningJob = {
                "status": "running",
                "job_type": _DISPERSION_JOB_TYPE,
                "progress": 0.0,
                "message": "Starting",
                "param": body.param,
                "node_label": node.data.label,
                "start_time": start_time,
                "timeout": config.get("timeout", _default_train_timeout()),
            }
            job_id = self._store.create_job(initial_job)

        execution_context: ExecutionContext | None = None
        launch_started = False
        try:
            preamble_ns = self._compile_preamble(body.graph)
            _ram_warning, row_limit, _total_rows, _probe_cols = self._estimate_ram(
                body.graph,
                body.node_id,
                preamble_ns,
                job_id,
                source=body.source,
            )
            row_limit = _clamp_row_limit(row_limit, config.get("row_limit"))
            row_limit = min(row_limit or _DISPERSION_ESTIMATE_ROW_CAP, _DISPERSION_ESTIMATE_ROW_CAP)

            excluded = config.get("exclude", [])
            keep_cols = _training_projection_keep_columns(config)
            required_columns_by_node = _training_required_columns_by_node(
                body.node_id,
                config,
            )
            execution_context = create_admitted_execution_context(
                operation="dispersion_estimate",
                profile=ExecutionProfile.TRAINING_PREP,
                job_id=job_id,
            )
            bind_running_execution_metrics_publisher(self._store, job_id, execution_context)
            train_body = TrainRequest(
                graph=body.graph,
                node_id=body.node_id,
                source=body.source,
            )
            tmp_parquet = self._execute_and_sink(
                train_body,
                preamble_ns,
                row_limit,
                job_id,
                exclude=excluded or None,
                keep_columns=keep_cols,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
            )
            self._launch_dispersion_background(
                job_id,
                body.node_id,
                config,
                body.param,
                tmp_parquet,
                execution_context=execution_context,
            )
            launch_started = True
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            http_exc = _memory_limit_http_exception(exc)
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                message=str(http_exc.detail),
                fields={"error": str(http_exc.detail)},
            )
            raise http_exc from None
        except HTTPException as exc:
            self._lifecycle.transition(
                job_id,
                to="contract_error" if 400 <= exc.status_code < 500 else "error",
                message=str(exc.detail),
                fields={"error": str(exc.detail)},
            )
            raise
        except Exception as exc:
            self._lifecycle.transition(
                job_id,
                to="error",
                message=str(exc),
                fields={"error": str(exc)},
            )
            raise
        finally:
            if execution_context is not None and not launch_started:
                execution_context.release_admission(preserve_primary_error=True)

        return DispersionEstimateResponse(status="started", job_id=job_id)

    def dispersion_job(self, job_id: str) -> JobSnapshot:
        """Return a dispersion-estimation job, 404ing other job types."""
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _DISPERSION_JOB_TYPE:
            raise HTTPException(
                status_code=404,
                detail=f"Dispersion estimation job '{job_id}' not found",
            )
        return job

    def cancel_dispersion(self, job_id: str) -> JobSnapshot:
        """Cancel a running dispersion-estimation job."""
        job = self.dispersion_job(job_id)
        if job.get("status") != "running":
            return job
        self._training_jobs.cancel(job_id, reason="cancelled")
        updated_job = self._lifecycle.transition(
            job_id,
            to="cancelled",
            message="Cancelled",
            elapsed_seconds=_job_elapsed_seconds(job),
        )
        return updated_job if updated_job is not None else self._store.require_job(job_id)

    def _validate_dispersion_config(self, config: dict[str, Any], param: str) -> None:
        """Fast upfront validation for a dispersion-estimation request."""
        expected_family = _DISPERSION_PARAM_FAMILIES.get(param)
        if expected_family is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown dispersion parameter '{param}'. Estimable "
                    f"parameters: {', '.join(_DISPERSION_PARAM_FAMILIES)}."
                ),
            )
        if str(config.get("algorithm", "catboost")).lower() != "glm":
            raise HTTPException(
                status_code=400,
                detail="Dispersion estimation applies to GLM modelling nodes only.",
            )
        train_params = build_train_params(config)
        family = str(train_params.get("family", "") or "")
        link = str(train_params.get("link", "") or "")
        _validate_glm_family_link(family, link)
        if family != expected_family:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Dispersion parameter '{param}' belongs to the "
                    f"{expected_family} family, not '{family}'."
                ),
            )
        if not isinstance(config.get("target"), str) or not config.get("target"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "No target column selected. Open the config panel and choose a target column."
                ),
            )
        # The rest of the training objective must be complete before the
        # estimate is meaningful (the profile is conditional on the design).
        # Stub the parameter being estimated so its own gate doesn't fire.
        issue = training_objective_issue({**config, param: _DISPERSION_PARAM_STUBS[param]})
        if issue is not None:
            raise HTTPException(status_code=400, detail=issue)

    def _launch_dispersion_protocol(
        self,
        job_id: str,
        node_id: str,
        config: dict[str, Any],
        param: str,
        tmp_parquet: str,
        *,
        execution_context: ExecutionContext,
    ) -> IsolatedSupervisorThread | None:
        stored_job = self._store.require_job(job_id)
        start_time, timeout_seconds = _worker_timing(stored_job, job_id=job_id)
        self._training_jobs.register_latest(
            (_DISPERSION_JOB_TYPE, job_id),
            job_id,
            execution_token=execution_context.cancellation_token,
        )
        launch_cleanup = self._parent_worker_cleanup(
            job_id,
            execution_context=execution_context,
            tmp_parquet=Path(tmp_parquet),
            artifact_root=None,
        )
        try:
            if self._store.require_job(job_id).get("status") != "running":
                launch_cleanup()
                return None
        except BaseException:
            launch_cleanup()
            raise
        try:
            artifact_root = Path(
                tempfile.mkdtemp(
                    prefix=f".haute-dispersion-{job_id}-",
                    dir=Path(tmp_parquet).resolve().parent,
                )
            )
        except Exception:
            launch_cleanup()
            raise
        cleanup = self._parent_worker_cleanup(
            job_id,
            execution_context=execution_context,
            tmp_parquet=Path(tmp_parquet),
            artifact_root=artifact_root,
        )
        remaining = timeout_seconds - (time.monotonic() - start_time)
        if remaining <= 0:
            self.timeout(
                job_id,
                timeout=int(timeout_seconds),
                start_time=start_time,
            )
            cleanup()
            return None

        try:
            stub_config = {**config, param: _DISPERSION_PARAM_STUBS[param]}
            job_kwargs = build_training_job_kwargs(
                stub_config,
                data=str(Path(tmp_parquet).resolve()),
                default_name=node_id,
            )
            request = WorkerRequest(
                request_id=job_id,
                kind="dispersion",
                payload={
                    "job_kwargs": job_kwargs,
                    "param": param,
                    "profile": ExecutionProfile.TRAINING_PREP.value,
                    "memory_limit_bytes": execution_context.memory_limit_bytes,
                },
            )
        except Exception:
            cleanup()
            raise

        def on_progress(event: WorkerProgressEvent) -> None:
            if event.kind not in {"progress", "dispersion_fit"}:
                raise WorkerProtocolError(f"Unknown dispersion progress event kind {event.kind!r}")
            self._store.atomic_update(
                job_id,
                {
                    "progress": event.progress,
                    "message": event.message,
                    "elapsed_seconds": time.monotonic() - start_time,
                },
                expected_status="running",
            )

        def completed_fields(result: WorkerResultManifest) -> dict[str, Any]:
            if not isinstance(result.metadata, dict):
                raise WorkerProtocolError("Dispersion result metadata must be an object")
            metadata = result.metadata
            if metadata.get("param") != param:
                raise WorkerProtocolError("Dispersion result parameter does not match request")
            value = metadata.get("value")
            llf = metadata.get("llf")
            n_fits = metadata.get("n_fits")
            execution_metrics = metadata.get("execution_metrics")
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or isinstance(llf, bool)
                or not isinstance(llf, int | float)
                or isinstance(n_fits, bool)
                or not isinstance(n_fits, int)
                or n_fits < 0
                or not isinstance(execution_metrics, dict)
            ):
                raise WorkerProtocolError("Dispersion result metadata is malformed")
            fields = {
                "param": metadata["param"],
                "value": value,
                "llf": llf,
                "n_fits": n_fits,
                "execution_metrics": execution_metrics,
                "progress": 1.0,
            }
            _assert_json_finite(fields)
            return fields

        try:
            worker_config = worker_config_for_memory_policy(
                memory_limit_bytes=execution_context.memory_limit_bytes,
                timeout_seconds=remaining,
                stop_reason=lambda: self._training_jobs.cancellation_reason(job_id),
                process_name=f"haute-dispersion-{job_id}",
            )
            return self._supervisor.launch_protocol(
                job_id,
                _run_dispersion_process_job,
                request,
                artifact_root=artifact_root,
                artifact_kinds=frozenset(),
                max_artifact_size_bytes=0,
                config=worker_config,
                on_progress=on_progress,
                completed_fields=completed_fields,
                on_finished=cleanup,
                start_time=start_time,
            )
        except Exception as exc:
            cleanup()
            logger.error("dispersion_worker_start_failed", error=str(exc), node_id=node_id)
            raise HTTPException(
                status_code=500,
                detail=(
                    "Dispersion estimation worker failed to start. "
                    "Check the server logs for details."
                ),
            ) from exc

    def _launch_dispersion_background(
        self,
        job_id: str,
        node_id: str,
        config: dict[str, Any],
        param: str,
        tmp_parquet: str,
        *,
        execution_context: ExecutionContext,
    ) -> IsolatedSupervisorThread | None:
        """Delegate profile likelihood to the supervised process protocol."""
        return self._launch_dispersion_protocol(
            job_id,
            node_id,
            config,
            param,
            tmp_parquet,
            execution_context=execution_context,
        )

    def _parent_worker_cleanup(
        self,
        job_id: str,
        *,
        execution_context: ExecutionContext,
        tmp_parquet: Path,
        artifact_root: Path | None,
        artifact_publication_committed: Callable[[], bool] | None = None,
    ) -> Callable[[], None]:
        """Return an idempotent parent cleanup that attempts every owned resource."""
        lock = threading.Lock()
        finished = False

        def cleanup() -> None:
            nonlocal finished
            with lock:
                if finished:
                    return
                finished = True
            errors: list[BaseException] = []
            for cleanup_kind, action in (
                ("registry", lambda: self._training_jobs.release(job_id)),
                ("admission", execution_context.release_admission),
                (
                    "prepared_data",
                    lambda: (
                        tmp_parquet.unlink()
                        if tmp_parquet.exists() or tmp_parquet.is_symlink()
                        else None
                    ),
                ),
                (
                    "artifact_root",
                    lambda: (
                        shutil.rmtree(artifact_root)
                        if artifact_root is not None and artifact_root.exists()
                        else None
                    ),
                ),
            ):
                try:
                    action()
                except BaseException as exc:
                    if (
                        cleanup_kind == "artifact_root"
                        and isinstance(exc, OSError)
                        and artifact_publication_committed is not None
                        and artifact_publication_committed()
                    ):
                        logger.warning(
                            "training_artifact_post_commit_cleanup_failed",
                            path=str(artifact_root),
                            cleanup_kind="staging_root_retry",
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        continue
                    errors.append(exc)
            if errors:
                primary = errors[0]
                for extra in errors[1:]:
                    primary.add_note(f"Additional cleanup failure: {extra}")
                raise primary

        return cleanup

    # ------------------------------------------------------------------
    # Private orchestration steps
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> None:
        """Fast upfront validation — no pipeline execution yet."""
        target = config.get("target")
        if not target:
            raise HTTPException(
                status_code=400,
                detail="No target column selected."
                " Open the config panel and choose a target column.",
            )

        algorithm = config.get("algorithm", "catboost")
        if algorithm not in ALGORITHM_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown algorithm '{algorithm}'. "
                    f"Available algorithms: {', '.join(ALGORITHM_REGISTRY.keys())}."
                ),
            )

        # Validity checks first, so a wrong value beats an incomplete one:
        # GLM family/link combination (unknown family, bad link); CatBoost
        # loss-vs-task. _validate_glm_family_link also raises on an empty
        # family, and an absent loss is caught by the completeness gate below.
        if algorithm == "glm":
            train_params = build_train_params(config)
            family = str(train_params.get("family", "") or "")
            link = str(train_params.get("link", "") or "")
            _validate_glm_family_link(family, link)
        else:
            loss_function = config.get("loss_function")
            if loss_function:
                try:
                    resolve_loss_function(
                        loss_function,
                        str(config.get("task", "regression")),
                        config.get("variance_power"),
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Then require a complete training objective. An unset loss/family, or
        # an unset objective parameter (Tweedie variance power, elastic-net L1
        # ratio, empty GLM factor set), would silently fall through to a
        # library/literal failover — plausible numbers, wrong model. Shares the
        # single source of truth with the build-time gate so they can't drift.
        objective_issue = training_objective_issue(config)
        if objective_issue is not None:
            raise HTTPException(status_code=400, detail=objective_issue)
        try:
            legacy_fields = [key for key in ("split", "cross_validation") if key in config]
            if legacy_fields:
                raise TrainingConfigError(
                    "Invalid legacy modelling config: public split/cross_validation "
                    "fields were replaced by the canonical versioned evaluation object."
                )
            evaluation = parse_evaluation_config(config.get("evaluation"))
            metrics = config.get("metrics") or []
            if not metrics:
                # The builder derives objective-aware defaults. Reuse it rather
                # than creating a second default-metric contract in the route.
                build_training_job_kwargs(config, data="__config_validation__")
            else:
                parse_tuning_config(
                    config.get("tuning"),
                    algorithm=str(algorithm),
                    base_params=build_train_params(config),
                    evaluation=evaluation,
                    configured_metrics=list(metrics),
                )
        except TrainingConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _check_no_concurrent_jobs(self) -> None:
        """Reject if a training job is already running."""
        if self._store.has_job_with_status("running"):
            raise HTTPException(
                status_code=409,
                detail="A training job is already running. Please wait for it to finish.",
            )

    @staticmethod
    def _compile_preamble(graph: PipelineGraph) -> dict[str, Any] | None:
        from haute.executor import _compile_preamble, _pipeline_dir

        return (
            _compile_preamble(
                graph.preamble or "",
                pipeline_dir=_pipeline_dir(graph),
            )
            or None
        )

    def _estimate_ram(
        self,
        graph: PipelineGraph,
        node_id: str,
        preamble_ns: dict[str, Any] | None,
        job_id: str,
        source: str = "live",
    ) -> tuple[str | None, int | None, int | None, int]:
        """Estimate safe row limit from available RAM.

        Returns (ram_warning, row_limit, total_source_rows, probe_columns).
        """
        from haute.executor import _build_node_fn

        ram_warning: str | None = None
        total_source_rows: int | None = None
        probe_columns: int = 0

        try:
            from haute._ram_estimate import estimate_safe_training_rows

            self._store.update_job(job_id, message="Estimating memory requirements")
            ram_est = estimate_safe_training_rows(
                graph,
                node_id,
                _build_node_fn,
                preamble_ns=preamble_ns,
                source=source,
            )
            row_limit = ram_est.safe_row_limit
            ram_warning = ram_est.warning
            total_source_rows = ram_est.total_rows
            probe_columns = ram_est.probe_columns
            if ram_warning:
                self._store.update_job(job_id, warning=ram_warning)
        except Exception as exc:
            logger.warning("ram_estimate_failed", error=str(exc), exc_info=True)
            detail = {
                "error_code": "training_memory_estimate_failed",
                "operation": "training_pipeline",
                "job_id": job_id,
                "reason": "memory_estimate_failed",
                "message": (
                    "Training memory estimate failed before execution. "
                    "Fix the estimate error or simplify the upstream graph."
                ),
                "error": str(exc),
            }
            raise HTTPException(
                status_code=422,
                detail=detail,
            ) from None

        return ram_warning, row_limit, total_source_rows, probe_columns

    def _check_gpu_vram_before_launch(
        self,
        train_params: dict[str, Any],
        row_limit: int | None,
        total_source_rows: int | None,
        probe_columns: int,
        ram_warning: str | None,
        job_id: str,
    ) -> str | None:
        """Check GPU VRAM and refuse a job that cannot fit on the selected GPU."""
        if str(train_params.get("task_type", "")).upper() != "GPU":
            return ram_warning

        try:
            effective_rows = row_limit or (total_source_rows or 0)
            vram_check = _check_gpu_vram(
                effective_rows,
                probe_columns,
                train_params,
            )
            if vram_check.insufficient:
                gpu_warning = (
                    f"{vram_check.warning} Select CPU and retry, or reduce rows/features "
                    "before retrying GPU training."
                )
                logger.warning(
                    "gpu_vram_refused",
                    estimated_mb=vram_check.estimated_mb,
                    available_mb=vram_check.available_mb,
                )
                self._store.update_job(
                    job_id,
                    gpu_warning=gpu_warning,
                    warning=f"{ram_warning}\n{gpu_warning}" if ram_warning else gpu_warning,
                )
                raise _gpu_vram_http_exception(
                    warning=gpu_warning,
                    estimated_mb=vram_check.estimated_mb,
                    available_mb=vram_check.available_mb,
                    job_id=job_id,
                )
            if vram_check.warning:
                # Unknown VRAM: advisory only — the launch proceeds and
                # CatBoost's own device errors remain the hard gate.
                logger.warning(
                    "gpu_vram_unknown",
                    estimated_mb=vram_check.estimated_mb,
                )
                self._store.update_job(
                    job_id,
                    gpu_warning=vram_check.warning,
                    warning=(
                        f"{ram_warning}\n{vram_check.warning}"
                        if ram_warning
                        else vram_check.warning
                    ),
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("vram_estimate_failed", error=str(exc))
            check_failed = (
                "GPU VRAM feasibility could not be checked before launch; "
                "GPU training may fail or exhaust GPU memory."
            )
            self._store.update_job(
                job_id,
                gpu_warning=check_failed,
                warning=f"{ram_warning}\n{check_failed}" if ram_warning else check_failed,
            )

        return ram_warning

    def _execute_and_sink(
        self,
        body: TrainRequest,
        preamble_ns: dict[str, Any] | None,
        row_limit: int | None,
        job_id: str,
        *,
        exclude: list[str] | None = None,
        keep_columns: list[str] | None = None,
        required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> str:
        """Supervise one hard-capped preparation worker and own its parquet.

        The parent creates the destination path, hands the child a picklable
        request plus the admitted budget envelope, and converts the child's
        single outcome (or a typed worker failure) into exactly the terminal
        job state and HTTP shape the former in-thread path produced. Admission
        is released by the caller, never here, so it is released exactly once.

        Returns the path to the temp parquet file.
        Raises ``HTTPException`` on failure (no parquet is left behind).
        """
        if execution_context is None:
            raise ValueError("training preparation requires an admitted execution context")
        budget = isolated_execution_budget(execution_context)

        # Every fallible setup step runs before the temp path exists, so a
        # setup failure cannot orphan one; from creation onward the try/except
        # below owns it on every exit that is not a successful hand-off.
        self._store.update_job(job_id, message="Executing pipeline")
        stored_job = self._store.require_job(job_id)
        start_time, timeout_seconds = _worker_timing(stored_job, job_id=job_id)
        remaining = timeout_seconds - (time.monotonic() - start_time)
        if remaining <= 0:
            self.timeout(job_id, timeout=int(timeout_seconds), start_time=start_time)
            raise ExecutionCancelledError("training_preparation", job_id=job_id)
        worker_config = worker_config_for_memory_policy(
            memory_limit_bytes=budget.memory_limit_bytes,
            timeout_seconds=remaining,
            stop_reason=lambda: self._training_jobs.cancellation_reason(job_id),
            process_name="haute-training-prep",
        )

        tmp_parquet = create_training_parquet_path()
        try:
            return self._supervise_preparation_worker(
                body,
                preamble_ns,
                row_limit,
                job_id,
                exclude=exclude,
                keep_columns=keep_columns,
                required_columns_by_node=required_columns_by_node,
                budget=budget,
                worker_config=worker_config,
                tmp_parquet=tmp_parquet,
                start_time=start_time,
                timeout_seconds=timeout_seconds,
            )
        except BaseException:
            # Ownership backstop: no exit from supervision may leave the
            # parent-owned temp parquet behind.
            self._discard_prepared_parquet(job_id, tmp_parquet)
            raise

    def _discard_prepared_parquet(self, job_id: str, tmp_parquet: str) -> None:
        """Remove the parent-owned parquet, failing the job loudly if it survives.

        A removal failure is never swallowed: a surviving partial training file
        contradicts every terminal state this path can record, so it becomes a
        500 ``error`` naming the cleanup failure.
        """
        try:
            _remove_prepared_parquet(tmp_parquet)
        except OSError as exc:
            logger.error(
                "training_preparation_temp_cleanup_failed",
                job_id=job_id,
                path=tmp_parquet,
                error=str(exc),
            )
            raise self._fail_preparation_worker(
                job_id,
                message=f"Training preparation cleanup failed: {exc}",
            ) from exc

    def _supervise_preparation_worker(
        self,
        body: TrainRequest,
        preamble_ns: dict[str, Any] | None,
        row_limit: int | None,
        job_id: str,
        *,
        exclude: list[str] | None,
        keep_columns: list[str] | None,
        required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None,
        budget: IsolatedExecutionBudget,
        worker_config: IsolatedWorkerConfig,
        tmp_parquet: str,
        start_time: float,
        timeout_seconds: float,
    ) -> str:
        """Run the preparation child and map its single outcome onto the job."""
        request = TrainingPreparationRequest(
            graph=body.graph,
            node_id=body.node_id,
            job_id=job_id,
            source=body.source,
            parquet_path=tmp_parquet,
            config=dict(body.graph.node_map[body.node_id].data.config),
            project_root=str(_get_project_root()),
            streaming_chunk_size=body.streaming_chunk_size,
            row_limit=row_limit,
            exclude=list(exclude) if exclude else None,
            keep_columns=list(keep_columns) if keep_columns else None,
            required_columns_by_node=(
                None
                if required_columns_by_node is None
                else {
                    node_id: (
                        demand
                        if isinstance(demand, AllExceptColumns)
                        else frozenset(str(column) for column in demand)
                    )
                    for node_id, demand in required_columns_by_node.items()
                }
            ),
            preamble_supplied=preamble_ns is not None,
        )

        try:
            outcome = run_isolated_worker(
                prepare_training_data_worker,
                request,
                budget,
                config=worker_config,
            )
        except IsolatedWorkerStoppedError:
            self._discard_prepared_parquet(job_id, tmp_parquet)
            raise ExecutionCancelledError("training_preparation", job_id=job_id) from None
        except IsolatedWorkerTimeoutError:
            self._discard_prepared_parquet(job_id, tmp_parquet)
            self.timeout(job_id, timeout=int(timeout_seconds), start_time=start_time)
            raise ExecutionCancelledError("training_preparation", job_id=job_id) from None
        except IsolatedWorkerError as exc:
            self._discard_prepared_parquet(job_id, tmp_parquet)
            if isolated_worker_failure_is_memory(exc):
                http_exc = HTTPException(
                    status_code=507,
                    detail=isolated_worker_memory_detail(
                        exc,
                        operation=budget.operation,
                        memory_limit_bytes=budget.memory_limit_bytes,
                    ),
                )
                message, fields = _http_failure_job_parts(http_exc, job_id=job_id)
                self._lifecycle.transition(
                    job_id,
                    to="memory_limited",
                    message=message,
                    fields=fields,
                )
                raise http_exc from None
            logger.error(
                "training_preparation_worker_failed",
                job_id=job_id,
                node_id=body.node_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise self._fail_preparation_worker(job_id) from None

        if not isinstance(outcome, TrainingPreparationOutcome):
            self._discard_prepared_parquet(job_id, tmp_parquet)
            logger.error(
                "training_preparation_worker_failed",
                job_id=job_id,
                node_id=body.node_id,
                error="worker returned an invalid outcome",
                error_type=type(outcome).__name__,
            )
            raise self._fail_preparation_worker(job_id)

        if outcome.execution_metrics is not None:
            self._store.update_job(job_id, execution_metrics=outcome.execution_metrics)

        failure = outcome.failure
        if failure is not None:
            self._discard_prepared_parquet(job_id, tmp_parquet)
            self._lifecycle.transition(
                job_id,
                to=failure.terminal_reason,
                message=failure.message,
                fields=dict(failure.fields),
            )
            raise HTTPException(
                status_code=failure.http_status_code,
                detail=failure.http_detail,
            )

        prepared_path = Path(tmp_parquet)
        if (
            outcome.parquet_path != tmp_parquet
            or not prepared_path.exists()
            or prepared_path.stat().st_size == 0
        ):
            self._discard_prepared_parquet(job_id, tmp_parquet)
            raise self._fail_preparation_worker(
                job_id,
                message="Training preparation worker did not produce its prepared data.",
            )

        if outcome.feature_selection is not None:
            self._store.update_job(
                job_id,
                feature_selection=TrainingFeatureSelectionDiagnosticPayload.model_validate(
                    outcome.feature_selection
                ),
            )
        return tmp_parquet

    def _fail_preparation_worker(
        self,
        job_id: str,
        *,
        message: str = "Training preparation failed. Check the server logs for details.",
    ) -> HTTPException:
        """Record a supervisor-side preparation failure and return its 500."""
        http_exc = HTTPException(status_code=500, detail=message)
        job_message, fields = _http_failure_job_parts(http_exc, job_id=job_id)
        self._lifecycle.transition(
            job_id,
            to="error",
            message=job_message,
            fields=fields,
        )
        return http_exc

    def _launch_training_protocol(
        self,
        job_id: str,
        node_id: str,
        config: dict[str, Any],
        train_params: dict[str, Any],
        tmp_parquet: str,
        ram_warning: str | None,
        total_source_rows: int | None,
        *,
        execution_context: ExecutionContext,
        feature_selection: TrainingFeatureSelectionDiagnosticPayload | None = None,
    ) -> IsolatedSupervisorThread | None:
        stored_job = self._store.require_job(job_id)
        start_time, timeout_seconds = _worker_timing(stored_job, job_id=job_id)
        launch_cleanup = self._parent_worker_cleanup(
            job_id,
            execution_context=execution_context,
            tmp_parquet=Path(tmp_parquet),
            artifact_root=None,
        )
        try:
            if self._store.require_job(job_id).get("status") != "running":
                launch_cleanup()
                return None
        except BaseException:
            launch_cleanup()
            raise
        try:
            job_kwargs = build_training_job_kwargs(
                config,
                data=str(Path(tmp_parquet).resolve()),
                default_name=node_id,
            )
            job_kwargs["params"] = train_params
            output_root = Path(str(job_kwargs.pop("output_dir"))).expanduser().resolve()
            output_root.mkdir(parents=True, exist_ok=True)
            artifact_root = Path(
                tempfile.mkdtemp(
                    prefix=f".haute-training-{job_id}-",
                    dir=output_root,
                )
            )
        except Exception:
            launch_cleanup()
            raise

        artifact_publication_committed = threading.Event()
        cleanup = self._parent_worker_cleanup(
            job_id,
            execution_context=execution_context,
            tmp_parquet=Path(tmp_parquet),
            artifact_root=artifact_root,
            artifact_publication_committed=artifact_publication_committed.is_set,
        )
        # The first closure may have been constructed before the staging root.
        # It has not run; use only the complete owner from this point onward.
        remaining = timeout_seconds - (time.monotonic() - start_time)
        if remaining <= 0:
            self.timeout(
                job_id,
                timeout=int(timeout_seconds),
                start_time=start_time,
            )
            cleanup()
            return None

        try:
            request = WorkerRequest(
                request_id=job_id,
                kind="training",
                payload={
                    "job_kwargs": job_kwargs,
                    "profile": ExecutionProfile.TRAINING_PREP.value,
                    "memory_limit_bytes": execution_context.memory_limit_bytes,
                },
            )
        except Exception:
            cleanup()
            raise

        def on_progress(event: WorkerProgressEvent) -> None:
            if event.kind == "progress":
                self._store.atomic_update(
                    job_id,
                    {
                        "progress": event.progress,
                        "message": event.message,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                    expected_status="running",
                )
                return
            if event.kind == "tuning":
                if not isinstance(event.fields, dict) or set(event.fields) != {
                    "phase",
                    "trial_index",
                    "trial_count",
                    "fold_index",
                    "fold_count",
                    "completed_fits",
                    "total_fits",
                    "best_objective",
                }:
                    raise WorkerProtocolError("Training tuning progress event fields are malformed")
                try:
                    validated_status = TrainStatusResponse.model_validate(
                        {"status": "running", **event.fields}
                    )
                except ValueError as exc:
                    raise WorkerProtocolError(
                        f"Training tuning progress event fields are malformed: {exc}"
                    ) from exc
                current_job = self._store.get_job(job_id)
                if current_job is None:
                    raise KeyError(f"Training job {job_id!r} disappeared during tuning progress")
                previous_completed = current_job.get("completed_fits")
                previous_total = current_job.get("total_fits")
                if (
                    previous_completed is not None
                    and validated_status.completed_fits is not None
                    and validated_status.completed_fits < previous_completed
                ):
                    raise WorkerProtocolError("Training tuning progress completed_fits regressed")
                if previous_total is not None and validated_status.total_fits != previous_total:
                    raise WorkerProtocolError("Training tuning progress total_fits changed")
                tuning_fields = {
                    key: getattr(validated_status, key)
                    for key in (
                        "phase",
                        "trial_index",
                        "trial_count",
                        "fold_index",
                        "fold_count",
                        "completed_fits",
                        "total_fits",
                        "best_objective",
                    )
                }
                self._store.atomic_update(
                    job_id,
                    {
                        **tuning_fields,
                        "progress": event.progress,
                        "message": event.message,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                    expected_status="running",
                )
                return
            if event.kind != "iteration" or not isinstance(event.fields, dict):
                raise WorkerProtocolError(f"Unknown training progress event kind {event.kind!r}")
            iteration = event.fields.get("iteration")
            total = event.fields.get("total")
            metrics = event.fields.get("metrics")
            if (
                isinstance(iteration, bool)
                or not isinstance(iteration, int)
                or iteration < 0
                or isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
                or not isinstance(metrics, dict)
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int | float)
                    or not math.isfinite(float(value))
                    for value in metrics.values()
                )
            ):
                raise WorkerProtocolError("Training iteration event fields are malformed")
            current_job = self._store.get_job(job_id)
            if current_job is None:
                raise KeyError(f"Training job {job_id!r} disappeared during progress")
            history = list(current_job.get("train_loss_history") or [])
            history.append({"iteration": float(iteration), **metrics})
            truncated = bool(current_job.get("train_loss_history_truncated"))
            if len(history) > _max_train_loss_history():
                history = history[-_max_train_loss_history() :]
                truncated = True
            self._store.atomic_update(
                job_id,
                {
                    "progress": event.progress,
                    "message": event.message,
                    "iteration": iteration,
                    "total_iterations": total,
                    "train_loss": metrics,
                    "train_loss_history": history,
                    "train_loss_history_truncated": truncated,
                    "elapsed_seconds": time.monotonic() - start_time,
                },
                expected_status="running",
            )

        def completed_fields(result: WorkerResultManifest) -> dict[str, Any]:
            if not isinstance(result.metadata, dict):
                raise WorkerProtocolError("Training result metadata must be an object")
            raw_response = result.metadata.get("response")
            if not isinstance(raw_response, dict):
                raise WorkerProtocolError("Training result response must be an object")
            # Cheap identity and manifest-shape guards run on the raw payload
            # first so a cross-job or artifact-less response is diagnosed as
            # such rather than as a schema failure.
            if raw_response.get("status") != "completed" or raw_response.get("job_id") != job_id:
                raise WorkerProtocolError(
                    "Training response status or job identifier does not match request"
                )
            model_artifacts = [
                artifact for artifact in result.artifacts if artifact.kind == "model"
            ]
            if (
                len(model_artifacts) != 1
                or raw_response.get("model_path") != model_artifacts[0].relative_path
            ):
                raise WorkerProtocolError(
                    "Training response model path does not match the staged model manifest"
                )
            execution_metrics = result.metadata.get("execution_metrics")
            if not isinstance(execution_metrics, dict):
                raise WorkerProtocolError("Training execution metrics must be an object")
            response_fields = dict(raw_response)
            response_fields.update(
                {
                    "warning": ram_warning,
                    "total_source_rows": total_source_rows,
                    "feature_selection": (
                        feature_selection.model_dump(mode="json")
                        if feature_selection is not None
                        else None
                    ),
                }
            )
            try:
                staged_response = TrainResponse.model_validate(response_fields)
            except ValidationError as exc:
                raise WorkerProtocolError(f"Training response is malformed: {exc}") from exc
            _assert_json_finite(staged_response)
            artifacts_by_kind = {artifact.kind: artifact for artifact in result.artifacts}
            for kind, response_field in _EVALUATION_ARTIFACT_PATHS.items():
                artifact = artifacts_by_kind.get(kind)
                if (
                    staged_response.evaluation is None
                    or artifact is None
                    or getattr(staged_response.evaluation, response_field) != artifact.relative_path
                ):
                    raise WorkerProtocolError(
                        "Training evaluation response path does not match "
                        f"the staged {kind} manifest"
                    )
            if staged_response.tuning is None:
                if any(kind in artifacts_by_kind for kind in _TUNING_ARTIFACT_PATHS):
                    raise WorkerProtocolError("Training response omits declared tuning artifacts")
            else:
                for kind, response_field in _TUNING_ARTIFACT_PATHS.items():
                    artifact = artifacts_by_kind.get(kind)
                    if (
                        artifact is None
                        or getattr(staged_response.tuning, response_field) != artifact.relative_path
                    ):
                        raise WorkerProtocolError(
                            "Training tuning response path does not match "
                            f"the staged {kind} manifest"
                        )
            staged_evaluation = cast(EvaluationReportPayload, staged_response.evaluation)

            # The store owns the lifecycle claim around this callback. A
            # cancellation that wins first suppresses publication; one that
            # arrives afterwards observes the paired completed record.
            def publish_completion_fields() -> Mapping[str, Any]:
                published = _publish_training_artifacts(
                    result,
                    artifact_root=artifact_root,
                    output_root=output_root,
                    job_id=job_id,
                    expected_model_name=str(job_kwargs["name"]),
                    expected_evaluation=staged_evaluation,
                    expected_tuning=staged_response.tuning,
                )
                artifact_publication_committed.set()
                response_fields["model_path"] = str(published["model"])
                evaluation_fields = staged_evaluation.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                for kind, response_field in _EVALUATION_ARTIFACT_PATHS.items():
                    evaluation_fields[response_field] = str(published[kind])
                response_fields["evaluation"] = evaluation_fields
                if staged_response.tuning is not None:
                    tuning_fields = staged_response.tuning.model_dump(
                        mode="json",
                        exclude_none=True,
                    )
                    for kind, response_field in _TUNING_ARTIFACT_PATHS.items():
                        tuning_fields[response_field] = str(published[kind])
                    response_fields["tuning"] = tuning_fields
                response = TrainResponse.model_validate(response_fields)
                _assert_json_finite(response)
                completed_progress_fields: dict[str, Any] = {}
                if response.tuning is not None:
                    completed_progress_fields = {
                        "phase": "completed",
                        "trial_index": None,
                        "trial_count": response.tuning.trial_count,
                        "fold_index": None,
                        "fold_count": (
                            response.tuning.trial_fit_count // response.tuning.trial_count
                        ),
                        "completed_fits": response.tuning.total_fit_count,
                        "total_fits": response.tuning.total_fit_count,
                        "best_objective": response.tuning.winner_objective,
                    }
                return {
                    "result": response,
                    "execution_metrics": execution_metrics,
                    "progress": 1.0,
                    "elapsed_seconds": time.monotonic() - start_time,
                    **completed_progress_fields,
                }

            self._lifecycle.publish_completion(
                job_id,
                publish=publish_completion_fields,
                message="Training completed",
            )
            # The supervisor will make its normal terminal write after this
            # callback; it is intentionally a no-op because the committed
            # result is already durable alongside its artifacts.
            return {}

        try:
            worker_config = worker_config_for_memory_policy(
                memory_limit_bytes=execution_context.memory_limit_bytes,
                timeout_seconds=remaining,
                stop_reason=lambda: self._training_jobs.cancellation_reason(job_id),
                process_name=f"haute-training-{job_id}",
            )
            return self._supervisor.launch_protocol(
                job_id,
                _run_training_process_job,
                request,
                artifact_root=artifact_root,
                artifact_kinds=_TRAINING_ARTIFACT_KINDS,
                max_artifact_size_bytes=_max_training_artifact_bytes(),
                config=worker_config,
                on_progress=on_progress,
                completed_fields=completed_fields,
                on_finished=cleanup,
                start_time=start_time,
            )
        except Exception as exc:
            cleanup()
            logger.error("training_worker_start_failed", error=str(exc), node_id=node_id)
            raise HTTPException(
                status_code=500,
                detail="Training worker failed to start. Check the server logs for details.",
            ) from exc

    def _launch_background(
        self,
        job_id: str,
        node_id: str,
        config: dict[str, Any],
        train_params: dict[str, Any],
        tmp_parquet: str,
        ram_warning: str | None,
        total_source_rows: int | None,
        *,
        execution_context: ExecutionContext,
        feature_selection: TrainingFeatureSelectionDiagnosticPayload | None = None,
    ) -> IsolatedSupervisorThread | None:
        """Delegate fit/evaluation to the supervised process protocol."""
        return self._launch_training_protocol(
            job_id,
            node_id,
            config,
            train_params,
            tmp_parquet,
            ram_warning,
            total_source_rows,
            execution_context=execution_context,
            feature_selection=feature_selection,
        )
