"""Spawn-safe training worker protocol, execution, and failure translation."""

from __future__ import annotations

import math
import os
import time
import traceback
from collections.abc import Iterable, Mapping
from numbers import Real
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel

from haute._env import int_env
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
    current_rss_bytes,
)
from haute._logging import get_logger
from haute._worker_protocol import (
    WORKER_MAX_MESSAGE_LENGTH,
    WORKER_MAX_TRACEBACK_LENGTH,
    WORKER_USER_MESSAGE_FIELD,
    WorkerFailurePayload,
    WorkerRequest,
    WorkerResultManifest,
    WorkerRuntime,
    build_artifact_manifest,
)
from haute.errors import BoundedMemoryUnsupportedError, HauteValidationError
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_job_fields,
)
from haute.routes._memory_messages import memory_limit_user_message
from haute.routes._training_artifacts import (
    _EVALUATION_ARTIFACT_PATHS,
    _TUNING_ARTIFACT_PATHS,
)
from haute.routes._training_evaluation import _DISPERSION_PARAM_FAMILIES
from haute.schemas import (
    EvaluationReportPayload,
    TrainResponse,
    TuningReportPayload,
)

logger = get_logger(component="server.modelling.train")


def _max_train_loss_history() -> int:
    return int_env("HAUTE_TRAIN_LOSS_HISTORY_LIMIT", 200)


def _training_context_phrase(job_kwargs: Mapping[str, Any] | None) -> str:
    """Name the failing fit's target and objective from bounded config values.

    Used in failure messages so the user learns *which* fit failed without the
    message quoting anything from the data or the filesystem.
    """
    if not isinstance(job_kwargs, Mapping):
        return "the model"
    target = job_kwargs.get("target")
    params = job_kwargs.get("params")
    objective = (
        job_kwargs.get("loss_function")
        or (params.get("family") if isinstance(params, Mapping) else None)
        # Raw node configs (the preparation path) carry the GLM family at the
        # top level rather than under built params.
        or job_kwargs.get("family")
    )
    if not (isinstance(target, str) and target):
        return "the model"
    if isinstance(objective, str) and objective:
        return f"target {target!r} (objective {objective!r})"
    return f"target {target!r}"


def _friendly_error(
    exc: Exception,
    *,
    operation_noun: str = "Training",
    context: str = "the model",
) -> str:
    """Translate a training-path exception into a curated, actionable message.

    Every returned shape is haute-authored and safe to promote verbatim as the
    job's terminal message; the raw exception text stays in the diagnostic
    ``error`` field and the traceback. Apart from the ``HauteValidationError``
    validation channel, no shape interpolates a third-party message body
    (which may carry internal paths or secrets) — third-party failures name
    the exception type and the target/objective context only, and they stay a
    plain system ``error`` — a system fault is never relabelled as a
    ``contract_error``.
    """
    if isinstance(exc, HauteValidationError):
        # HauteValidationError is the package's deliberate validation channel:
        # gates, column checks, and the metric-stage wrap all speak through
        # it. Provenance is enforced by the marker type — a dependency's plain
        # ValueError takes the type-only fallback below instead.
        return str(exc)

    exc_type = type(exc).__name__

    if isinstance(exc, FileNotFoundError):
        # At the fit stage the missing path is typically an internal staged
        # asset, so the message does not quote it; the path stays in the
        # diagnostic fields and traceback.
        return (
            f"{operation_noun} could not find a file it needs. The full error, "
            "including the path, is recorded in the job's error details."
        )

    # Keyed on the exception TYPE: a non-CatBoost error that merely mentions
    # "catboost" in its text must not take a CatBoost-specific shape.
    if "CatBoost" in exc_type:
        msg = str(exc)
        if "nan" in msg.lower() or "inf" in msg.lower():
            return (
                f"{operation_noun} failed: the data contains NaN or infinite "
                "values. Add a polars node upstream to handle missing values "
                "(e.g. .fill_null() or .drop_nulls()) before training."
            )
        if "feature" in msg.lower() and "number" in msg.lower():
            return (
                f"{operation_noun} failed: feature mismatch — the data's "
                "columns do not match what the model expects. The full error "
                "is recorded in the job's error details."
            )
        return (
            f"{operation_noun} of {context} failed with a CatBoost error "
            f"({exc_type}). The full error is recorded in the job's error details."
        )

    if isinstance(exc, OSError):
        # Surface only an OS-authored reason: exc.strerror is
        # constructor-supplied (a dependency can put arbitrary text there), so
        # the reason is re-derived from the numeric errno.
        reason: str | None = None
        if isinstance(exc.errno, int):
            try:
                reason = os.strerror(exc.errno)
            except (ValueError, OverflowError):
                reason = None
        if reason:
            return (
                f"{operation_noun} could not save its output files ({reason}). "
                "Check the server's disk space and file permissions, then try again."
            )
        return (
            f"{operation_noun} could not save its output files because of a "
            "file-system error. Check the server's disk space and file "
            "permissions, then try again."
        )

    return (
        f"{operation_noun} of {context} failed with an unexpected internal error "
        f"({exc_type}). The full error is recorded in the job's error details."
    )


def _assert_json_finite(value: Any, path: str = "result") -> None:
    """Raise when a training result contains a non-JSON numeric value.

    Deliberately a plain ``ValueError``, not ``HauteValidationError``: a
    malformed result is a system fault (the marker would relabel it a
    ``contract_error`` and surface it verbatim as the user's fault). The same
    holds for the result-shape and persisted-artifact linkage checks below.
    """
    if isinstance(value, BaseModel):
        _assert_json_finite(value.model_dump(mode="python"), path)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_json_finite(item, f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _assert_json_finite(item, f"{path}[{index}]")
        return
    if isinstance(value, Real) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"non-finite numeric value at {path}")


def _job_elapsed_seconds(job: Mapping[str, Any], fallback: float = 0.0) -> float:
    start = job.get("start_time")
    if isinstance(start, int | float):
        return time.monotonic() - float(start)
    elapsed = job.get("elapsed_seconds", fallback)
    return float(elapsed) if isinstance(elapsed, int | float) else fallback


def _bounded_loss_history(
    history: Iterable[dict[str, float]],
) -> tuple[list[dict[str, float]], bool]:
    rows = list(history)
    if len(rows) <= _max_train_loss_history():
        return rows, False
    return rows[-_max_train_loss_history() :], True


def _worker_request_payload(request: WorkerRequest, *, expected_kind: str) -> dict[str, Any]:
    if request.kind != expected_kind:
        raise HauteValidationError(
            f"Worker request kind must be {expected_kind!r}, got {request.kind!r}"
        )
    payload = request.payload
    if not isinstance(payload, dict):
        raise HauteValidationError("Worker request payload must be an object")
    return payload


def _child_execution_context(
    request: WorkerRequest,
    payload: dict[str, Any],
    *,
    operation: str,
) -> ExecutionContext:
    raw_profile = payload.get("profile")
    if not isinstance(raw_profile, str):
        raise HauteValidationError("Worker profile must be a string")
    try:
        profile = ExecutionProfile(raw_profile)
    except ValueError:
        # The enum's own ValueError is a dependency exception — re-raise as
        # the marker so protocol validation stays on the curated channel.
        raise HauteValidationError(
            f"Worker profile {raw_profile!r} is not a recognised execution profile"
        ) from None
    raw_limit = payload.get("memory_limit_bytes")
    if raw_limit is not None and (
        isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit <= 0
    ):
        raise HauteValidationError("Worker memory_limit_bytes must be a positive integer or null")
    baseline = current_rss_bytes()
    rss_limit = (
        baseline + raw_limit if baseline is not None and isinstance(raw_limit, int) else raw_limit
    )
    return ExecutionContext(
        operation=operation,
        profile=profile,
        job_id=request.request_id,
        memory_limit_bytes=raw_limit,
        memory_baseline_bytes=baseline,
        rss_limit_bytes=rss_limit,
    )


def _remove_gated_temp_parquet(tmp_parquet: str) -> None:
    """Best-effort removal of the sunk training parquet at the gate.

    An unlink failure (e.g. a permissions race) is logged, never raised — it
    must not replace the gate's actionable 422 with a filesystem error.
    """
    try:
        Path(tmp_parquet).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "training_target_gate_temp_cleanup_failed",
            path=tmp_parquet,
            error=str(exc),
        )


def _worker_failure_payload(
    exc: Exception,
    *,
    terminal_reason: str,
    message: str | None = None,
    fields: dict[str, Any] | None = None,
    user_facing: bool,
) -> WorkerFailurePayload:
    detail = message if message is not None else str(exc)
    detail = detail[:WORKER_MAX_MESSAGE_LENGTH] or type(exc).__name__
    remote_traceback = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[
        :WORKER_MAX_TRACEBACK_LENGTH
    ]
    payload_fields = dict(fields) if fields is not None else {"error": detail}
    # Only deliberately curated messages are marked user-facing (haute-authored
    # wording: the gates, the metric wrap, HauteValidationError validation
    # messages, the _friendly_error shapes — whose fallback names only the
    # target/objective context and exception type, never the third-party
    # message body). Raw third-party text stays behind the typed
    # "Isolated worker raised {type}: {message}" wrapper.
    if user_facing:
        payload_fields.setdefault(WORKER_USER_MESSAGE_FIELD, detail)
    return WorkerFailurePayload(
        terminal_reason=terminal_reason,
        error_type=type(exc).__name__,
        message=detail,
        traceback=remote_traceback or type(exc).__name__,
        fields=payload_fields,
    )


def _known_training_worker_failure(
    exc: Exception,
    *,
    bounded_memory_prefix: str,
    operation_noun: str = "Training",
) -> WorkerFailurePayload | None:
    if isinstance(exc, ExecutionCancelledError):
        # Match the preparation path's terminal message: the internal
        # operation/job-id wording of str(exc) is diagnostics, not a
        # user-facing message.
        return _worker_failure_payload(
            exc,
            terminal_reason="cancelled",
            message="Cancelled",
            fields={"error": str(exc)},
            user_facing=True,
        )
    if isinstance(exc, ExecutionMemoryLimitExceededError):
        payload = exc.to_payload()
        return _worker_failure_payload(
            exc,
            terminal_reason="memory_limited",
            message=memory_limit_user_message(exc, operation_noun=operation_noun),
            fields={
                "error": str(exc),
                "error_detail": payload,
                "error_code": "memory_limit",
                "http_status_code": 507,
            },
            user_facing=True,
        )
    if isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
        return _worker_failure_payload(
            exc,
            terminal_reason="contract_error",
            fields=contract_error_job_fields(exc),
            user_facing=True,
        )
    if isinstance(exc, BoundedMemoryUnsupportedError):
        message = f"{bounded_memory_prefix}: {exc}"
        return _worker_failure_payload(
            exc,
            terminal_reason="contract_error",
            message=message,
            fields={"error": message},
            user_facing=True,
        )
    if isinstance(exc, HauteValidationError):
        # The marker type vouches the message is haute-authored validation
        # wording; a dependency's plain ValueError falls through to the
        # entrypoint's _friendly_error fallback (a plain "error") instead.
        return _worker_failure_payload(exc, terminal_reason="contract_error", user_facing=True)
    if isinstance(exc, MemoryError):
        # str(MemoryError) is raw third-party text (usually empty) — keep the
        # typed wrapper surface; error_code drives the UI's memory handling.
        return _worker_failure_payload(
            exc,
            terminal_reason="memory_limited",
            fields={"error": str(exc), "error_code": "memory_limit"},
            user_facing=False,
        )
    return None


def _with_worker_failure_metrics(
    failure: WorkerFailurePayload,
    execution_context: ExecutionContext | None,
) -> WorkerFailurePayload:
    if execution_context is None:
        return failure
    fields = dict(failure.fields) if isinstance(failure.fields, dict) else {}
    fields["execution_metrics"] = execution_context.metrics_payload(
        status=failure.terminal_reason,
        terminal_reason=failure.terminal_reason,
    )
    return WorkerFailurePayload(
        terminal_reason=failure.terminal_reason,
        error_type=failure.error_type,
        message=failure.message,
        traceback=failure.traceback,
        fields=fields,
    )


def _training_response_payload(
    train_result: Any,
    *,
    job_id: str,
    model_path: str,
    evaluation: Mapping[str, Any],
    tuning: Mapping[str, Any] | None,
) -> dict[str, Any]:
    loss_history, loss_history_truncated = _bounded_loss_history(
        train_result.loss_history,
    )
    diagnostics_set: Literal["development", "final_test"] = train_result.diagnostics_set
    diagnostic_metrics = (
        train_result.final_test_metrics if diagnostics_set == "final_test" else train_result.metrics
    )
    evaluation_payload = EvaluationReportPayload.model_validate(evaluation)
    tuning_payload = TuningReportPayload.model_validate(tuning) if tuning is not None else None
    response = TrainResponse(
        status="completed",
        job_id=job_id,
        diagnostic_metrics=diagnostic_metrics,
        final_test_metrics=train_result.final_test_metrics,
        feature_importance=train_result.feature_importance,
        model_path=model_path,
        development_rows=train_result.development_rows,
        final_test_rows=train_result.final_test_rows,
        diagnostics_set=diagnostics_set,
        features=train_result.features,
        cat_features=train_result.cat_features,
        best_iteration=train_result.best_iteration,
        loss_history=loss_history,
        loss_history_truncated=loss_history_truncated,
        double_lift=train_result.double_lift,
        shap_summary=train_result.shap_summary,
        feature_importance_loss=train_result.feature_importance_loss,
        ave_per_feature=train_result.ave_per_feature,
        residuals_histogram=train_result.residuals_histogram,
        residuals_stats=train_result.residuals_stats,
        actual_vs_predicted=train_result.actual_vs_predicted,
        lorenz_curve=train_result.lorenz_curve,
        lorenz_curve_perfect=train_result.lorenz_curve_perfect,
        pdp_data=train_result.pdp_data,
        glm_coefficients=train_result.glm_coefficients,
        glm_relativities=train_result.glm_relativities,
        glm_fit_statistics=train_result.glm_fit_statistics,
        glm_regularization_path=train_result.glm_regularization_path,
        diagnostics_errors=train_result.diagnostics_errors,
        evaluation=evaluation_payload,
        tuning=tuning_payload,
    )
    _assert_json_finite(response)
    return response.model_dump(mode="json", exclude_none=True)


def _run_training_process_job(
    runtime: WorkerRuntime,
    request: WorkerRequest,
) -> WorkerResultManifest | WorkerFailurePayload:
    """Spawn entrypoint for fit, evaluation, diagnostics, and staged artifacts."""
    execution_context: ExecutionContext | None = None
    failure_context = "the model"
    try:
        payload = _worker_request_payload(request, expected_kind="training")
        raw_kwargs = payload.get("job_kwargs")
        if not isinstance(raw_kwargs, dict):
            raise HauteValidationError("Training worker job_kwargs must be an object")
        job_kwargs = dict(raw_kwargs)
        failure_context = _training_context_phrase(job_kwargs)
        staged_output = runtime.staged_path("output")
        staged_output.mkdir()
        job_kwargs["output_dir"] = str(staged_output)

        from haute.modelling import TrainingJob
        from haute.modelling._training_job import model_contract_filename

        execution_context = _child_execution_context(
            request,
            payload,
            operation="training_job",
        )
        job = TrainingJob(**job_kwargs)

        def progress(message: str, fraction: float) -> None:
            execution_context.checkpoint(label="training_progress")
            runtime.emit_progress(
                progress=fraction,
                message=message,
                kind="progress",
                fields={},
            )

        def iteration(
            iteration_number: int,
            total: int,
            metrics: dict[str, float],
        ) -> None:
            execution_context.checkpoint(label="training_iteration")
            runtime.emit_progress(
                progress=(min(max(iteration_number / total, 0.0), 1.0) if total > 0 else 0.0),
                message=f"Iteration {iteration_number}",
                kind="iteration",
                fields={
                    "iteration": iteration_number,
                    "total": total,
                    "metrics": metrics,
                },
            )

        def tuning_progress(fields: dict[str, Any]) -> None:
            execution_context.checkpoint(label="training_tuning_progress")
            completed = fields.get("completed_fits")
            total = fields.get("total_fits")
            fraction = (
                min(max(float(completed) / float(total), 0.0), 1.0)
                if isinstance(completed, int)
                and not isinstance(completed, bool)
                and isinstance(total, int)
                and not isinstance(total, bool)
                and total > 0
                else 0.0
            )
            phase = fields.get("phase")
            runtime.emit_progress(
                progress=fraction,
                message=f"Tuning: {phase}",
                kind="tuning",
                fields=fields,
            )

        train_result = job.run(
            progress,
            iteration,
            check_cancelled=lambda: execution_context.checkpoint(
                label="training_cancel_checkpoint"
            ),
            execution_context=execution_context,
            on_tuning_progress=tuning_progress,
        )
        model_path = Path(train_result.model_path).resolve()
        contract_path = model_path.parent / model_contract_filename(model_path.stem)
        model_manifest = build_artifact_manifest(
            artifact_root=staged_output.parent,
            path=model_path,
            kind="model",
            lifetime="staged",
        )
        contract_manifest = build_artifact_manifest(
            artifact_root=staged_output.parent,
            path=contract_path,
            kind="feature_contract",
            lifetime="staged",
        )
        artifacts = [model_manifest, contract_manifest]
        if not isinstance(train_result.evaluation, dict):
            raise ValueError("Training evaluation result must be an object")
        response_evaluation = dict(train_result.evaluation)
        for kind, response_field in _EVALUATION_ARTIFACT_PATHS.items():
            raw_path = response_evaluation.get(response_field)
            if not isinstance(raw_path, str) or not raw_path:
                raise ValueError(f"Training evaluation result has no {response_field}")
            artifact = build_artifact_manifest(
                artifact_root=staged_output.parent,
                path=Path(raw_path).resolve(),
                kind=kind,
                lifetime="staged",
            )
            artifacts.append(artifact)
            response_evaluation[response_field] = artifact.relative_path

        response_tuning: dict[str, Any] | None = None
        if train_result.tuning is not None:
            if not isinstance(train_result.tuning, dict):
                raise ValueError("Training tuning result must be an object")
            response_tuning = dict(train_result.tuning)
            for kind, response_field in _TUNING_ARTIFACT_PATHS.items():
                raw_path = response_tuning.get(response_field)
                if not isinstance(raw_path, str) or not raw_path:
                    raise ValueError(f"Training tuning result has no {response_field}")
                artifact = build_artifact_manifest(
                    artifact_root=staged_output.parent,
                    path=Path(raw_path).resolve(),
                    kind=kind,
                    lifetime="staged",
                )
                artifacts.append(artifact)
                response_tuning[response_field] = artifact.relative_path

        response = _training_response_payload(
            train_result,
            job_id=request.request_id,
            model_path=model_manifest.relative_path,
            evaluation=response_evaluation,
            tuning=response_tuning,
        )
        return WorkerResultManifest(
            metadata={
                "response": response,
                "execution_metrics": execution_context.metrics_payload(
                    status="completed",
                    terminal_reason="completed",
                ),
            },
            artifacts=tuple(artifacts),
        )
    except Exception as exc:
        known = _known_training_worker_failure(
            exc,
            bounded_memory_prefix="Training cannot run in bounded streaming mode",
            operation_noun="Training",
        )
        if known is not None:
            return _with_worker_failure_metrics(known, execution_context)
        return _with_worker_failure_metrics(
            _worker_failure_payload(
                exc,
                terminal_reason="error",
                message=_friendly_error(exc, context=failure_context),
                fields={"error": str(exc) or type(exc).__name__},
                user_facing=True,
            ),
            execution_context,
        )


def _run_dispersion_process_job(
    runtime: WorkerRuntime,
    request: WorkerRequest,
) -> WorkerResultManifest | WorkerFailurePayload:
    """Spawn entrypoint for the bounded GLM profile-likelihood search."""
    execution_context: ExecutionContext | None = None
    failure_context = "the model"
    try:
        payload = _worker_request_payload(request, expected_kind="dispersion")
        raw_kwargs = payload.get("job_kwargs")
        param = payload.get("param")
        if not isinstance(raw_kwargs, dict):
            raise HauteValidationError("Dispersion worker job_kwargs must be an object")
        if param not in _DISPERSION_PARAM_FAMILIES:
            raise HauteValidationError(f"Unknown dispersion parameter {param!r}")

        from haute.modelling import TrainingJob
        from haute.modelling._rustystats import (
            _build_interactions,
            _resolve_glm_terms,
            estimate_glm_dispersion,
        )

        execution_context = _child_execution_context(
            request,
            payload,
            operation="dispersion_estimate",
        )
        job_kwargs = dict(raw_kwargs)
        failure_context = _training_context_phrase(job_kwargs)
        job = TrainingJob(**job_kwargs)
        train_params = job_kwargs["params"]

        def progress(message: str, fraction: float) -> None:
            execution_context.checkpoint(label="dispersion_progress")
            runtime.emit_progress(
                progress=fraction,
                message=message,
                kind="progress",
                fields={},
            )

        prepared = job._prepare_data(progress, execution_context=execution_context)
        features = prepared.features
        cat_features = prepared.cat_features
        raw_terms = train_params.get("terms") or {}
        if raw_terms:
            term_names = set(raw_terms)
            missing = term_names - set(features)
            if missing:
                raise HauteValidationError(
                    "GLM terms reference columns not present in the training data: "
                    f"{sorted(missing)}."
                )
            features = [feature for feature in features if feature in term_names]
            cat_features = [feature for feature in cat_features if feature in term_names]

        terms = _resolve_glm_terms(train_params, features, cat_features)
        interactions = _build_interactions(
            train_params.get("interactions", []) or [],
            terms,
        )
        target = str(job_kwargs["target"])
        weight = job_kwargs.get("weight") or None
        offset = job_kwargs.get("offset") or None
        needed = list(
            dict.fromkeys(
                [
                    *terms,
                    target,
                    *([weight] if weight else []),
                    *([offset] if offset else []),
                ]
            )
        )
        progress("Loading estimation sample", 0.35)
        from haute._polars_utils import streaming_collect

        frame = streaming_collect(
            pl.scan_parquet(prepared.data_path).filter(pl.col(target).is_not_null()).select(needed),
            execution_context=execution_context,
        )

        def on_fit(fit_index: int) -> None:
            execution_context.checkpoint(label="dispersion_fit")
            runtime.emit_progress(
                progress=0.4 + 0.55 * min(fit_index / 30.0, 1.0),
                message=f"Profile likelihood fit {fit_index + 1}",
                kind="dispersion_fit",
                fields={"fit_index": fit_index},
            )

        estimate = estimate_glm_dispersion(
            data=frame,
            terms=terms,
            target=target,
            family=str(train_params.get("family")),
            param=param,
            link=train_params.get("link") or None,
            intercept=bool(train_params.get("intercept", True)),
            weight=weight,
            offset=offset,
            interactions=interactions or None,
            on_fit=on_fit,
        )
        return WorkerResultManifest(
            metadata={
                "param": estimate.param,
                "value": estimate.value,
                "llf": estimate.llf,
                "n_fits": estimate.n_fits,
                "execution_metrics": execution_context.metrics_payload(
                    status="completed",
                    terminal_reason="completed",
                ),
            }
        )
    except Exception as exc:
        known = _known_training_worker_failure(
            exc,
            bounded_memory_prefix=("Dispersion estimation cannot run in bounded streaming mode"),
            operation_noun="Dispersion estimation",
        )
        if known is not None:
            return _with_worker_failure_metrics(known, execution_context)
        return _with_worker_failure_metrics(
            _worker_failure_payload(
                exc,
                terminal_reason="error",
                message=_friendly_error(
                    exc,
                    operation_noun="Dispersion estimation",
                    context=failure_context,
                ),
                fields={"error": str(exc) or type(exc).__name__},
                user_facing=True,
            ),
            execution_context,
        )
