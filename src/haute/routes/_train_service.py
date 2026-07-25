"""TrainService — orchestrates model training, extracted from the route handler.

The route handler becomes a thin adapter that validates the HTTP request and
delegates to ``TrainService.start()``.
"""

from __future__ import annotations

import gc
import math
import os
import shutil
import tempfile
import threading
import time
import traceback
from collections.abc import Callable, Iterable, Mapping
from numbers import Real
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import HTTPException
from pydantic import BaseModel

from haute._env import int_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
    current_rss_bytes,
)
from haute._graph_utils import upstream_node_ids
from haute._logging import get_logger
from haute._types import GraphNode, PipelineGraph
from haute._worker_isolation import worker_config_for_memory_policy
from haute._worker_protocol import (
    WORKER_MAX_MESSAGE_LENGTH,
    WORKER_MAX_TRACEBACK_LENGTH,
    WorkerArtifactManifest,
    WorkerFailurePayload,
    WorkerProgressEvent,
    WorkerProtocolError,
    WorkerRequest,
    WorkerResultManifest,
    WorkerRuntime,
    build_artifact_manifest,
)
from haute.errors import BoundedMemoryUnsupportedError
from haute.execution import (
    AllExceptColumns,
    build_dataframe_execution_cache_request,
    dataframe_graph_input_fingerprint,
    execute_lazy_graph,
)
from haute.graph_utils import NodeType
from haute.modelling._algorithms import ALGORITHM_REGISTRY, resolve_loss_function
from haute.modelling._split import DEFAULT_SPLIT_DICT
from haute.modelling._train_config import (
    build_train_params,
    build_training_job_kwargs,
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
    contract_error_job_fields,
)
from haute.routes._helpers import find_typed_node
from haute.routes._job_lifecycle import (
    JobLifecycle,
    bind_running_execution_metrics_publisher,
)
from haute.routes._job_store import JobStore
from haute.schemas import (
    DispersionEstimateRequest,
    DispersionEstimateResponse,
    TrainingFeatureSelectionDiagnosticPayload,
    TrainRequest,
    TrainResponse,
)

logger = get_logger(component="server.modelling.train")

# ── Default constants ─────────────────────────────────────────────
_DEFAULT_BORDER_COUNT = 128  # CatBoost border count for VRAM estimation
_DEFAULT_DEPTH = 6  # CatBoost tree depth for VRAM estimation
_TRAINING_JOB_TYPE = "training"
_DISPERSION_JOB_TYPE = "dispersion_estimate"
_JOB_TYPE_KEY = "job_type"

# Row cap for dispersion estimation. The profile search runs ~10-30 IRLS
# fits, so the estimate samples the training frame (seeded, deterministic —
# same sampler as training's RAM downsample) rather than paying full-data
# cost per candidate. 200k rows pins a single dispersion scalar far tighter
# than the search's own tolerance.
_DISPERSION_ESTIMATE_ROW_CAP = 200_000

# Which GLM family owns each estimable dispersion parameter.
_DISPERSION_PARAM_FAMILIES = {"theta": "negbinomial", "var_power": "tweedie"}
# Stub value injected so config machinery built for complete objectives
# (training_objective_issue, build_training_job_kwargs) can run while the
# parameter is still the one being estimated. Never reaches a fit: the
# profile search overrides the parameter at every candidate.
_DISPERSION_PARAM_STUBS = {"theta": 1.0, "var_power": 1.5}


# Env-tunable defaults — resolved per call so overrides set after import
# take effect.
def _default_train_timeout() -> int:
    return int_env("HAUTE_TRAIN_TIMEOUT", 3600)


def _max_train_loss_history() -> int:
    return int_env("HAUTE_TRAIN_LOSS_HISTORY_LIMIT", 200)


def _max_training_artifact_bytes() -> int:
    return int_env("HAUTE_TRAIN_ARTIFACT_MAX_BYTES", 4 * 1024**3)


# Deterministic seed for the RAM/row-limit training downsample. A fixed
# constant (rather than a config knob) keeps training reproducible by default
# and matches the project-wide split-seed default (``SplitConfig.seed == 42``).
_TRAINING_DOWNSAMPLE_SEED = 42


def _seeded_training_sample(lf: pl.LazyFrame, row_limit: int) -> pl.LazyFrame:
    """Uniform random sample of ``row_limit`` rows — deterministic, order-preserving.

    Replaces the previous ``head(row_limit)`` downsample, which was order-biased:
    temporally or target-ordered data trained on the oldest slice only. The
    shuffle-filter idiom samples without replacement, keeps the surviving rows
    in their original relative order, and is a no-op when ``row_limit`` is at
    or above the frame height. Only the row-index column is materialised for
    the mask, so the data columns still stream through the bounded sink.
    """
    if row_limit <= 0:
        raise ValueError(f"row_limit must be positive, got {row_limit}")
    return lf.filter(
        pl.int_range(pl.len()).shuffle(seed=_TRAINING_DOWNSAMPLE_SEED) < row_limit,
    )


def _memory_limit_http_exception(
    exc: ExecutionAdmissionError | ExecutionMemoryLimitExceededError,
) -> HTTPException:
    return HTTPException(status_code=507, detail=exc.to_payload())


def _gpu_vram_http_exception(
    *,
    warning: str,
    estimated_mb: float | None,
    available_mb: float | None,
    job_id: str,
) -> HTTPException:
    payload = {
        "error_code": "gpu_vram_limit",
        "operation": "training_job",
        "job_id": job_id,
        "message": warning,
        "gpu_vram_estimated_mb": estimated_mb,
        "gpu_vram_available_mb": available_mb,
        "reason": "gpu_vram_limit_exceeded",
    }
    return HTTPException(status_code=507, detail=payload)


# Valid GLM family → link combinations.  The canonical link (used when
# the user leaves "link" empty) is listed first.
_VALID_GLM_LINKS: dict[str, tuple[str, ...]] = {
    "gaussian": ("identity", "log", "inverse"),
    "binomial": ("logit", "probit", "cloglog"),
    "poisson": ("log", "identity", "sqrt"),
    # Quasi-Poisson estimates its dispersion from Pearson residuals (a fitted
    # scale, no user parameter), so it is safe to offer. RustyStats accepts
    # only log/identity for it — no sqrt.
    "quasipoisson": ("log", "identity"),
    # Negative Binomial's dispersion `theta` is not estimated by RustyStats —
    # an unset theta silently fits at theta=1.0 — so the training objective
    # gate (training_objective_issue) requires an explicit theta; the config
    # panel offers profile-likelihood estimation on demand. RustyStats accepts
    # only log/identity for it.
    "negbinomial": ("log", "identity"),
    "gamma": ("inverse", "log", "identity"),
    "tweedie": ("log", "identity"),
    "inverse_gaussian": ("inverse_squared", "inverse", "log", "identity"),
}


def _validate_glm_family_link(family: str, link: str) -> None:
    """Raise HTTPException(400) if the family is unset or the combination invalid."""
    if not family:
        raise HTTPException(
            status_code=400,
            detail=(
                "No GLM family selected. Open the config panel and choose a "
                "distribution family explicitly (e.g. poisson for claim counts, "
                "gamma for severity) — an unset family would silently train a "
                "gaussian model."
            ),
        )
    if family not in _VALID_GLM_LINKS:
        raise HTTPException(
            status_code=400,
            detail=(f"Unknown GLM family '{family}'. Available: {', '.join(_VALID_GLM_LINKS)}."),
        )
    if not link:
        return  # canonical link will be used
    valid = _VALID_GLM_LINKS[family]
    if link not in valid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Link '{link}' is not valid for the {family} family. "
                f"Valid links: {', '.join(valid)}. "
                f"For a binary target like sale_flag, use family='binomial' with link='logit'."
            ),
        )


class _VramCheck:
    """Result of a GPU VRAM feasibility check."""

    __slots__ = ("estimated_mb", "available_mb", "warning")

    def __init__(
        self,
        estimated_mb: float | None = None,
        available_mb: float | None = None,
        warning: str | None = None,
    ) -> None:
        self.estimated_mb = estimated_mb
        self.available_mb = available_mb
        self.warning = warning


def _clamp_row_limit(
    current_limit: int | None,
    user_limit: object,
) -> int | None:
    """Apply a user-specified row_limit, taking the minimum with *current_limit*."""
    if user_limit and isinstance(user_limit, (int, float)):
        clamped = int(user_limit)
        if clamped > 0:
            return min(current_limit, clamped) if current_limit else clamped
    return current_limit


def _glm_training_term_columns(config: dict[str, Any]) -> frozenset[str] | None:
    if str(config.get("algorithm", "catboost")).lower() != "glm":
        return None
    raw_terms = config.get("terms")
    params = config.get("params")
    if raw_terms is None and isinstance(params, dict):
        raw_terms = params.get("terms")
    if not isinstance(raw_terms, dict) or not raw_terms:
        return None
    terms = frozenset(name for name in raw_terms if isinstance(name, str) and name)
    return terms or None


def _string_list_config(config: Mapping[str, Any], key: str) -> list[str]:
    raw = config.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a list of column names")
    columns: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must contain non-empty string column names")
        if value in seen:
            continue
        columns.append(value)
        seen.add(value)
    return columns


def _training_required_metadata_columns(config: Mapping[str, Any]) -> set[str]:
    target = config.get("target")
    columns = {target} if isinstance(target, str) and target else set()
    for aux_key in ("weight", "offset", "fold_column"):
        aux_col = config.get(aux_key)
        if isinstance(aux_col, str) and aux_col:
            columns.add(aux_col)

    split = config.get("split") or DEFAULT_SPLIT_DICT
    if isinstance(split, dict):
        strategy = split.get("strategy", "random")
        split_col = None
        if strategy == "temporal":
            split_col = split.get("date_column")
        elif strategy == "group":
            split_col = split.get("group_column")
        if isinstance(split_col, str) and split_col:
            columns.add(split_col)

    columns.update(_string_list_config(config, "id_columns"))
    return columns


def _training_metadata_reasons(config: Mapping[str, Any]) -> dict[str, str]:
    """Return configured non-feature columns in deterministic role precedence."""
    reasons: dict[str, str] = {}

    def add(raw_column: object, reason: str) -> None:
        if isinstance(raw_column, str) and raw_column:
            reasons.setdefault(raw_column, reason)

    add(config.get("target"), "target")
    add(config.get("weight"), "weight")
    add(config.get("offset"), "offset")
    add(config.get("fold_column"), "fold")
    for column in _string_list_config(config, "id_columns"):
        add(column, "identifier")
    split = config.get("split") or DEFAULT_SPLIT_DICT
    if isinstance(split, dict):
        strategy = split.get("strategy", "random")
        if strategy == "temporal":
            add(split.get("date_column"), "split")
        elif strategy == "group":
            add(split.get("group_column"), "split")
    return reasons


def _bounded_training_detail(items: list[Any], *, cap: int = 128) -> dict[str, Any]:
    retained = items[:cap]
    return {
        "state": "truncated" if len(items) > len(retained) else "available",
        "total_count": len(items),
        "items": retained,
    }


def _build_training_feature_selection(
    config: Mapping[str, Any],
    schema_columns: Iterable[str],
) -> TrainingFeatureSelectionDiagnosticPayload:
    """Validate and explain the final ordered training feature selection.

    This operates on schema metadata only. It is intentionally called before
    the training sink, so missing features or an empty feature set cannot
    trigger a data collection first.
    """
    schema = list(schema_columns)
    if any(not isinstance(column, str) or not column for column in schema):
        raise ValueError("training schema must contain non-empty column names")
    if len(schema) != len(set(schema)):
        raise ValueError("training schema contains duplicate column names")
    schema_set = set(schema)
    metadata_reasons = _training_metadata_reasons(config)
    missing_metadata = [column for column in metadata_reasons if column not in schema_set]
    if missing_metadata:
        raise ValueError(
            "Training input is missing required column(s): "
            f"{missing_metadata}. Available columns: {schema}"
        )

    explicit_features = _string_list_config(config, "feature_columns")
    term_columns = _glm_training_term_columns(dict(config))
    configured_exclusions = set(_string_list_config(config, "exclude"))
    if explicit_features:
        mode = "explicit"
        missing_features = [column for column in explicit_features if column not in schema_set]
        if missing_features:
            raise ValueError(
                "Configured feature column(s) not found in training data: "
                f"{missing_features}. Available columns: {schema}"
            )
        features = explicit_features
    elif term_columns is not None:
        mode = "glm_terms"
        missing_terms = sorted(term_columns - schema_set)
        if missing_terms:
            raise ValueError(
                "GLM terms reference columns not found in training data: "
                f"{missing_terms}. Available columns: {schema}"
            )
        features = [column for column in schema if column in term_columns]
    else:
        mode = "all_except"
        non_features = set(metadata_reasons) | configured_exclusions
        features = [column for column in schema if column not in non_features]

    if not features:
        raise ValueError(
            "No feature columns remaining after applying target, metadata, and exclusion settings."
        )

    retained_metadata = [
        {"column": column, "reason": metadata_reasons[column]}
        for column in schema
        if column in metadata_reasons
    ]
    feature_set = set(features)
    excluded: list[dict[str, str]] = []
    for column in schema:
        if column in feature_set:
            continue
        if column in metadata_reasons:
            reason = metadata_reasons[column]
        elif column in configured_exclusions:
            reason = "configured_exclusion"
        elif mode == "glm_terms":
            reason = "not_in_formula"
        else:
            reason = "not_selected"
        excluded.append({"column": column, "reason": reason})

    features_payload = _bounded_training_detail(features)
    metadata_payload = _bounded_training_detail(retained_metadata)
    excluded_payload = _bounded_training_detail(excluded)
    detail_state = (
        "truncated"
        if "truncated"
        in {
            features_payload["state"],
            metadata_payload["state"],
            excluded_payload["state"],
        }
        else "available"
    )
    return TrainingFeatureSelectionDiagnosticPayload.model_validate(
        {
            "schema_version": 1,
            "mode": mode,
            "feature_count": len(features),
            "detail_state": detail_state,
            "features": features_payload,
            "retained_metadata": metadata_payload,
            "excluded_columns": excluded_payload,
        }
    )


def _training_required_columns_by_node(
    node_id: str,
    config: dict[str, Any],
) -> dict[str, frozenset[str] | AllExceptColumns] | None:
    """Return modelling-node output demand needed by training.

    GLM with explicit terms has an exact feature contract before the target
    schema is materialised. CatBoost derives features from the target schema as
    all columns except configured non-feature columns, so it advertises an
    all-except demand rather than pretending the feature set is unknown.
    """
    term_columns = _glm_training_term_columns(config)
    target = config.get("target")
    if not isinstance(target, str) or not target:
        return None

    if term_columns is None:
        algorithm = str(config.get("algorithm", "catboost")).lower()
        if algorithm != "catboost":
            return None
        keep_columns = _training_required_metadata_columns(config)
        feature_columns = _string_list_config(config, "feature_columns")
        if feature_columns:
            return {node_id: frozenset([*feature_columns, *sorted(keep_columns)])}
        raw_exclude = _string_list_config(config, "exclude")
        exclude = {
            column
            for column in raw_exclude
            if isinstance(column, str) and column and column not in keep_columns
        }
        return {
            node_id: AllExceptColumns(
                required_columns=frozenset(keep_columns),
                excluded_columns=frozenset(keep_columns | exclude),
            )
        }

    columns = set(term_columns)
    columns.update(_training_required_metadata_columns(config))

    return {node_id: frozenset(columns)}


def _declared_categorical_levels_for_training(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> dict[str, list[str | None]]:
    from haute.modelling._feature_contract import merge_categorical_level_declarations

    declarations: list[tuple[str, Any]] = [(node_id, config.get("categorical_levels"))]
    declarations.extend(
        (upstream_id, graph.node_map[upstream_id].data.config.get("categorical_levels"))
        for upstream_id in upstream_node_ids(node_id, graph.parents_of)
        if upstream_id in graph.node_map
    )
    return merge_categorical_level_declarations(declarations)


def _find_modelling_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find and validate a modelling node in the graph."""
    return find_typed_node(graph, node_id, NodeType.MODELLING, "modelling")


def _friendly_error(exc: Exception) -> str:
    """Translate common training exceptions into actionable messages."""
    msg = str(exc)

    if isinstance(exc, ValueError):
        return msg

    if isinstance(exc, FileNotFoundError):
        return f"File not found: {msg}"

    exc_type = type(exc).__name__
    if "CatBoost" in exc_type or "catboost" in msg.lower():
        if "nan" in msg.lower() or "inf" in msg.lower():
            return (
                "Training failed: the data contains NaN or infinite values. "
                "Add a polars node upstream to handle missing values "
                "(e.g. .fill_null() or .drop_nulls()) before training."
            )
        if "feature" in msg.lower() and "number" in msg.lower():
            return f"Training failed: feature mismatch. {msg}"
        return f"CatBoost error: {msg}"

    if isinstance(exc, OSError):
        return f"Could not save model file: {msg}"

    return f"Training failed ({exc_type}): {msg}"


def _assert_json_finite(value: Any, path: str = "result") -> None:
    """Raise when a training result contains a non-JSON numeric value."""
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


def _job_elapsed_seconds(job: dict[str, Any], fallback: float = 0.0) -> float:
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
        raise ValueError(f"Worker request kind must be {expected_kind!r}, got {request.kind!r}")
    payload = request.payload
    if not isinstance(payload, dict):
        raise ValueError("Worker request payload must be an object")
    return payload


def _child_execution_context(
    request: WorkerRequest,
    payload: dict[str, Any],
    *,
    operation: str,
) -> ExecutionContext:
    raw_profile = payload.get("profile")
    if not isinstance(raw_profile, str):
        raise ValueError("Worker profile must be a string")
    profile = ExecutionProfile(raw_profile)
    raw_limit = payload.get("memory_limit_bytes")
    if raw_limit is not None and (
        isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit <= 0
    ):
        raise ValueError("Worker memory_limit_bytes must be a positive integer or null")
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


def _worker_failure_payload(
    exc: Exception,
    *,
    terminal_reason: str,
    message: str | None = None,
    fields: dict[str, Any] | None = None,
) -> WorkerFailurePayload:
    detail = message if message is not None else str(exc)
    detail = detail[:WORKER_MAX_MESSAGE_LENGTH] or type(exc).__name__
    remote_traceback = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[
        :WORKER_MAX_TRACEBACK_LENGTH
    ]
    return WorkerFailurePayload(
        terminal_reason=terminal_reason,
        error_type=type(exc).__name__,
        message=detail,
        traceback=remote_traceback or type(exc).__name__,
        fields=fields or {"error": detail},
    )


def _known_training_worker_failure(
    exc: Exception,
    *,
    bounded_memory_prefix: str,
) -> WorkerFailurePayload | None:
    if isinstance(exc, ExecutionCancelledError):
        return _worker_failure_payload(exc, terminal_reason="cancelled")
    if isinstance(exc, ExecutionMemoryLimitExceededError):
        payload = exc.to_payload()
        return _worker_failure_payload(
            exc,
            terminal_reason="memory_limited",
            fields={
                "error": str(exc),
                "error_detail": payload,
                "error_code": "memory_limit",
                "http_status_code": 507,
            },
        )
    if isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
        return _worker_failure_payload(
            exc,
            terminal_reason="contract_error",
            fields=contract_error_job_fields(exc),
        )
    if isinstance(exc, BoundedMemoryUnsupportedError):
        message = f"{bounded_memory_prefix}: {exc}"
        return _worker_failure_payload(
            exc,
            terminal_reason="contract_error",
            message=message,
            fields={"error": message},
        )
    if isinstance(exc, ValueError):
        return _worker_failure_payload(exc, terminal_reason="contract_error")
    if isinstance(exc, MemoryError):
        return _worker_failure_payload(
            exc,
            terminal_reason="memory_limited",
            fields={"error": str(exc), "error_code": "memory_limit"},
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
) -> dict[str, Any]:
    loss_history, loss_history_truncated = _bounded_loss_history(
        train_result.loss_history,
    )
    response = TrainResponse(
        status="completed",
        job_id=job_id,
        metrics=train_result.metrics,
        feature_importance=train_result.feature_importance,
        model_path=model_path,
        train_rows=train_result.train_rows,
        test_rows=train_result.test_rows,
        holdout_rows=train_result.holdout_rows,
        holdout_metrics=train_result.holdout_metrics,
        diagnostics_set=train_result.diagnostics_set,
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
    )
    _assert_json_finite(response)
    return response.model_dump(mode="json")


def _run_training_process_job(
    runtime: WorkerRuntime,
    request: WorkerRequest,
) -> WorkerResultManifest | WorkerFailurePayload:
    """Spawn entrypoint for fit, evaluation, diagnostics, and staged artifacts."""
    execution_context: ExecutionContext | None = None
    try:
        payload = _worker_request_payload(request, expected_kind="training")
        raw_kwargs = payload.get("job_kwargs")
        if not isinstance(raw_kwargs, dict):
            raise ValueError("Training worker job_kwargs must be an object")
        job_kwargs = dict(raw_kwargs)
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

        train_result = job.run(
            progress,
            iteration,
            check_cancelled=lambda: execution_context.checkpoint(
                label="training_cancel_checkpoint"
            ),
            execution_context=execution_context,
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
        response = _training_response_payload(
            train_result,
            job_id=request.request_id,
            model_path=model_manifest.relative_path,
        )
        return WorkerResultManifest(
            metadata={
                "response": response,
                "execution_metrics": execution_context.metrics_payload(
                    status="completed",
                    terminal_reason="completed",
                ),
            },
            artifacts=(model_manifest, contract_manifest),
        )
    except Exception as exc:
        known = _known_training_worker_failure(
            exc,
            bounded_memory_prefix="Training cannot run in bounded streaming mode",
        )
        if known is not None:
            return _with_worker_failure_metrics(known, execution_context)
        return _with_worker_failure_metrics(
            _worker_failure_payload(
                exc,
                terminal_reason="error",
                message=_friendly_error(exc),
            ),
            execution_context,
        )


def _run_dispersion_process_job(
    runtime: WorkerRuntime,
    request: WorkerRequest,
) -> WorkerResultManifest | WorkerFailurePayload:
    """Spawn entrypoint for the bounded GLM profile-likelihood search."""
    execution_context: ExecutionContext | None = None
    try:
        payload = _worker_request_payload(request, expected_kind="dispersion")
        raw_kwargs = payload.get("job_kwargs")
        param = payload.get("param")
        if not isinstance(raw_kwargs, dict):
            raise ValueError("Dispersion worker job_kwargs must be an object")
        if param not in _DISPERSION_PARAM_FAMILIES:
            raise ValueError(f"Unknown dispersion parameter {param!r}")

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
                raise ValueError(
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
            profile=ExecutionProfile.TRAINING_PREP,
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
        )
        if known is not None:
            return _with_worker_failure_metrics(known, execution_context)
        return _with_worker_failure_metrics(
            _worker_failure_payload(
                exc,
                terminal_reason="error",
                message=_friendly_error(exc),
            ),
            execution_context,
        )


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


def _publish_training_artifacts(
    manifest: WorkerResultManifest,
    *,
    artifact_root: Path,
    output_root: Path,
    job_id: str,
    expected_model_name: str,
) -> dict[str, Path]:
    """Publish a validated model/contract pair with same-filesystem rollback."""
    by_kind: dict[str, WorkerArtifactManifest] = {}
    for artifact in manifest.artifacts:
        if artifact.kind in by_kind:
            raise WorkerProtocolError(f"Duplicate training artifact kind {artifact.kind!r}")
        if artifact.lifetime != "staged":
            raise WorkerProtocolError("Training artifacts must have staged lifetime")
        by_kind[artifact.kind] = artifact
    if set(by_kind) != {"model", "feature_contract"}:
        raise WorkerProtocolError(
            "Training completion requires exactly one model and feature contract"
        )

    root = artifact_root.resolve()
    destination_root = output_root.resolve()
    staged_and_final: dict[str, tuple[Path, Path]] = {}
    for kind, artifact in by_kind.items():
        relative = Path(artifact.relative_path)
        if len(relative.parts) != 2 or relative.parts[0] != "output":
            raise WorkerProtocolError(
                f"Training artifact {artifact.relative_path!r} is not in the staged output"
            )
        staged = (root / relative).resolve()
        final = (destination_root / relative.name).resolve()
        if not final.is_relative_to(destination_root):
            raise WorkerProtocolError("Training artifact destination escapes output root")
        staged_and_final[kind] = (staged, final)

    from haute.modelling._feature_contract import CONTRACT_FILENAME
    from haute.modelling._training_job import model_contract_filename

    model_staged, _model_final = staged_and_final["model"]
    contract_staged, _contract_final = staged_and_final["feature_contract"]
    if model_staged.stem != expected_model_name:
        raise WorkerProtocolError("Training model filename does not match the requested name")
    if contract_staged.name != model_contract_filename(model_staged.stem):
        raise WorkerProtocolError("Training model and feature contract filenames do not match")
    destination_root.mkdir(parents=True, exist_ok=True)
    legacy_contract = destination_root / CONTRACT_FILENAME
    if legacy_contract.exists():
        logger.warning(
            "legacy_shared_feature_contract_present",
            legacy_path=str(legacy_contract),
            per_model_path=str(staged_and_final["feature_contract"][1]),
            model_name=model_staged.stem,
        )

    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for _staged, final in staged_and_final.values():
            if final.exists() or final.is_symlink():
                backup = final.with_name(f".{final.name}.{job_id}.haute-backup")
                if backup.exists() or backup.is_symlink():
                    raise FileExistsError(f"Training artifact backup already exists: {backup}")
                os.replace(final, backup)
                backups[final] = backup
        for staged, final in staged_and_final.values():
            os.replace(staged, final)
            published.append(final)
    except BaseException as exc:
        rollback_errors: list[BaseException] = []
        for final in reversed(published):
            try:
                if final.exists() or final.is_symlink():
                    final.unlink()
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
        for final, backup in reversed(tuple(backups.items())):
            try:
                if backup.exists() or backup.is_symlink():
                    os.replace(backup, final)
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
        for rollback_error in rollback_errors:
            exc.add_note(f"Artifact rollback failed: {rollback_error}")
        raise

    for backup in backups.values():
        try:
            backup.unlink()
        except OSError as exc:
            logger.warning(
                "training_artifact_post_commit_cleanup_failed",
                path=str(backup),
                cleanup_kind="backup",
                error=str(exc),
                error_type=type(exc).__name__,
            )
    try:
        shutil.rmtree(root)
    except OSError as exc:
        logger.warning(
            "training_artifact_post_commit_cleanup_failed",
            path=str(root),
            cleanup_kind="staging_root",
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return {kind: final for kind, (_staged, final) in staged_and_final.items()}


def _check_gpu_vram(
    effective_rows: int,
    probe_columns: int,
    params: dict[str, Any],
) -> _VramCheck:
    """Estimate GPU VRAM requirements and return a check result."""
    if effective_rows <= 0 or probe_columns <= 0:
        return _VramCheck()

    from haute._ram_estimate import available_vram_bytes, estimate_gpu_vram_bytes

    vram_needed = estimate_gpu_vram_bytes(
        effective_rows,
        probe_columns,
        border_count=params.get("border_count", _DEFAULT_BORDER_COUNT),
        depth=params.get("depth", _DEFAULT_DEPTH),
    )
    estimated_mb = round(vram_needed / 1024**2, 1)

    vram = available_vram_bytes()
    available_mb = round(vram / 1024**2, 1) if vram is not None else None

    warning: str | None = None
    if vram is not None and vram_needed > vram:
        warning = (
            f"GPU training needs ~{vram_needed / 1024**3:.1f} GB VRAM "
            f"but GPU has {vram / 1024**3:.1f} GB."
        )

    return _VramCheck(
        estimated_mb=estimated_mb,
        available_mb=available_mb,
        warning=warning,
    )


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

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start(self, body: TrainRequest) -> TrainResponse:
        """Validate, prepare data, and launch training in a supervised spawn worker.

        Returns a ``TrainResponse`` with status ``"started"`` and the job ID.
        Raises ``HTTPException`` on validation or pipeline execution failures.
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
            job_id = self._store.create_job(
                {
                    "status": "running",
                    _JOB_TYPE_KEY: _TRAINING_JOB_TYPE,
                    "progress": 0.0,
                    "message": "Starting",
                    "config": dict(config),
                    "node_label": node.data.label,
                    "start_time": start_time,
                    "timeout": config.get("timeout", _default_train_timeout()),
                }
            )

        execution_context: ExecutionContext | None = None
        launch_started = False
        try:
            preamble_ns = self._compile_preamble(body.graph)
            ram_warning, row_limit, total_source_rows, probe_columns = self._estimate_ram(
                body.graph,
                body.node_id,
                preamble_ns,
                job_id,
                source=body.source,
            )
            user_limit = config.get("row_limit")
            row_limit = _clamp_row_limit(row_limit, user_limit)

            # If the user's row_limit is the binding constraint, the RAM
            # downsample warning is irrelevant — suppress it.
            if (
                ram_warning
                and user_limit
                and isinstance(user_limit, (int, float))
                and int(user_limit) > 0
                and (row_limit is not None and row_limit == int(user_limit))
            ):
                ram_warning = None
                self._store.update_job(job_id, warning=None)

            # Shared config→params builder (also used by script export).
            # GLM keys are merged for GLM only — CatBoost has no **kwargs.
            train_params = build_train_params(config)

            ram_warning = self._check_gpu_fallback(
                train_params,
                row_limit,
                total_source_rows,
                probe_columns,
                ram_warning,
                job_id,
            )

            # Build the list of columns that must survive projection
            # (target, weight, offset — even if they're in the exclude list).
            excluded = config.get("exclude", [])
            keep_cols = list(_training_required_metadata_columns(config))

            required_columns_by_node = _training_required_columns_by_node(
                body.node_id,
                config,
            )
            execution_context = create_admitted_execution_context(
                operation="training_pipeline",
                profile=ExecutionProfile.TRAINING_PREP,
                job_id=job_id,
            )
            bind_running_execution_metrics_publisher(self._store, job_id, execution_context)
            tmp_parquet = self._execute_and_sink(
                body,
                preamble_ns,
                row_limit,
                job_id,
                exclude=excluded or None,
                keep_columns=keep_cols,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
            )
            feature_selection = self._store.require_job(job_id).get("feature_selection")

            # Default output_dir to <pipeline_dir>/outputs when not explicitly set.
            if "output_dir" not in config:
                from haute.executor import _pipeline_dir

                p_dir = _pipeline_dir(body.graph)
                config = {
                    **config,
                    "output_dir": str(p_dir / "outputs") if p_dir else "outputs",
                }

            self._launch_background(
                job_id,
                body.node_id,
                config,
                train_params,
                tmp_parquet,
                ram_warning,
                total_source_rows,
                feature_selection=feature_selection,
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
            if exc.status_code == 507:
                self._lifecycle.transition(
                    job_id,
                    to="memory_limited",
                    message=str(exc.detail),
                    fields={
                        "error": str(exc.detail),
                        "error_detail": exc.detail,
                        "error_code": (
                            exc.detail.get("error_code") if isinstance(exc.detail, dict) else None
                        ),
                        "http_status_code": exc.status_code,
                    },
                )
            elif 400 <= exc.status_code < 500:
                self._lifecycle.transition(
                    job_id,
                    to="contract_error",
                    message=str(exc.detail),
                    fields={
                        "error": str(exc.detail),
                        "error_detail": exc.detail,
                        "error_code": (
                            exc.detail.get("error_code") if isinstance(exc.detail, dict) else None
                        ),
                        "http_status_code": exc.status_code,
                    },
                )
            else:
                self._lifecycle.transition(
                    job_id,
                    to="error",
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
                execution_context.release_admission()

        return TrainResponse(
            status="started",
            job_id=job_id,
            feature_selection=feature_selection,
        )

    def cancel(self, job_id: str) -> dict[str, Any]:
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

    def reject_completed_result(self, job_id: str, *, message: str) -> dict[str, Any]:
        """Correct a completed job whose result cannot satisfy the API contract."""
        corrected = self._lifecycle.transition(
            job_id,
            to="error",
            message=message,
            fields={"result": None},
            expected_status="completed",
        )
        return corrected if corrected is not None else self._store.require_job(job_id)

    def timeout(self, job_id: str, *, timeout: int, start_time: float) -> dict[str, Any]:
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
            job_id = self._store.create_job(
                {
                    "status": "running",
                    _JOB_TYPE_KEY: _DISPERSION_JOB_TYPE,
                    "progress": 0.0,
                    "message": "Starting",
                    "param": body.param,
                    "node_label": node.data.label,
                    "start_time": start_time,
                    "timeout": config.get("timeout", _default_train_timeout()),
                }
            )

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
            keep_cols = list(_training_required_metadata_columns(config))
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
                execution_context.release_admission()

        return DispersionEstimateResponse(status="started", job_id=job_id)

    def dispersion_job(self, job_id: str) -> dict[str, Any]:
        """Return a dispersion-estimation job, 404ing other job types."""
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _DISPERSION_JOB_TYPE:
            raise HTTPException(
                status_code=404,
                detail=f"Dispersion estimation job '{job_id}' not found",
            )
        return job

    def cancel_dispersion(self, job_id: str) -> dict[str, Any]:
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
        params = config.get("params") or {}
        family = str(params.get("family") or config.get("family", "") or "")
        link = str(config.get("link", "") or "")
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
            params = config.get("params") or {}
            family = params.get("family") or config.get("family", "")
            link = config.get("link", "")
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

    def _check_gpu_fallback(
        self,
        train_params: dict[str, Any],
        row_limit: int | None,
        total_source_rows: int | None,
        probe_columns: int,
        ram_warning: str | None,
        job_id: str,
    ) -> str | None:
        """Check GPU VRAM; fall back to CPU if insufficient.

        Mutates *train_params* in-place.  Returns updated ram_warning.
        """
        if str(train_params.get("task_type", "")).upper() != "GPU":
            return ram_warning

        try:
            effective_rows = row_limit or (total_source_rows or 0)
            vram_check = _check_gpu_vram(
                effective_rows,
                probe_columns,
                train_params,
            )
            if vram_check.warning:
                gpu_warning = (
                    f"{vram_check.warning} Switch task_type to CPU or reduce rows/features "
                    "before starting GPU training."
                )
                logger.warning(
                    "gpu_vram_refused",
                    estimated_mb=vram_check.estimated_mb,
                    available_mb=vram_check.available_mb,
                )
                self._store.update_job(job_id, gpu_warning=gpu_warning)
                self._store.update_job(
                    job_id,
                    warning=f"{ram_warning}\n{gpu_warning}" if ram_warning else gpu_warning,
                )
                raise _gpu_vram_http_exception(
                    warning=gpu_warning,
                    estimated_mb=vram_check.estimated_mb,
                    available_mb=vram_check.available_mb,
                    job_id=job_id,
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("vram_estimate_failed", error=str(exc))

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
        """Execute the pipeline lazily and sink to a temp parquet file.

        If *exclude* and *keep_columns* are provided, projects down to
        only the needed columns before sinking.  This reduces peak memory
        by dropping columns that won't be used for training.

        Returns the path to the temp parquet file.
        Raises ``HTTPException`` on failure (cleans up temp file first).
        """
        from haute.executor import _build_node_fn
        from haute.modelling._algorithms import _mem_checkpoint, _mem_log_path

        mem_log = _mem_log_path()
        mem_log.parent.mkdir(parents=True, exist_ok=True)
        mem_log.write_text("")
        _mem_checkpoint("train_model endpoint START")

        # Free the preview cache to reclaim memory
        from haute.executor import _preview_cache

        _preview_cache.invalidate()
        from haute.trace import _cache as _trace_cache

        _trace_cache.invalidate()
        gc.collect()
        _mem_checkpoint("cleared preview cache")

        tmp_fd, tmp_parquet = tempfile.mkstemp(suffix=".parquet", prefix="haute_train_")
        os.close(tmp_fd)

        checkpoint_dir: Path | None = None
        try:
            self._store.update_job(job_id, message="Executing pipeline")
            _mem_checkpoint("before _execute_lazy")

            checkpoint_dir = Path(tempfile.mkdtemp(prefix="haute_train_ckpt_"))
            from haute._polars_utils import DEFAULT_STREAMING_CHUNK_SIZE
            from haute.executor import ENFORCE_CONTRACTS

            chunk_size = body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
            dataframe_cache_request = build_dataframe_execution_cache_request(
                body.graph,
                node_ids=[body.node_id],
                namespace="training_prep",
                source=body.source,
                profile=(
                    execution_context.profile
                    if execution_context is not None
                    else ExecutionProfile.LAZY_SINK
                ),
                input_fingerprint=dataframe_graph_input_fingerprint(
                    body.graph,
                    target_node_id=body.node_id,
                    source=body.source,
                ),
                target_node_id=body.node_id,
                required_columns_by_node=required_columns_by_node,
                enforce_contracts=ENFORCE_CONTRACTS,
                preamble_ns_supplied=preamble_ns is not None,
                streaming_chunk_size=chunk_size,
            )

            lazy_outputs, _order, _parents, _id_to_name = execute_lazy_graph(
                body.graph,
                _build_node_fn,
                target_node_id=body.node_id,
                preamble_ns=preamble_ns,
                source=body.source,
                checkpoint_dir=checkpoint_dir,
                enforce_contracts=ENFORCE_CONTRACTS,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
                dataframe_cache_request=dataframe_cache_request,
            )

            target_lf = lazy_outputs.get(body.node_id)
            if target_lf is None:
                raise ValueError(
                    "No training data arrived at the modelling node. "
                    "Make sure an upstream data source is connected and producing data."
                )

            if row_limit:
                target_lf = _seeded_training_sample(target_lf, row_limit)

            schema_cols = (
                target_lf.collect_schema().names()
                if hasattr(target_lf, "collect_schema")
                else target_lf.columns
            )
            schema_set = set(schema_cols)
            try:
                feature_selection = _build_training_feature_selection(
                    body.graph.node_map[body.node_id].data.config,
                    schema_cols,
                )
            except ValueError as exc:
                self._lifecycle.transition(
                    job_id,
                    to="contract_error",
                    message=str(exc),
                )
                raise HTTPException(status_code=422, detail=str(exc)) from None
            self._store.update_job(job_id, feature_selection=feature_selection)
            required_training_columns = set(keep_columns or [])
            node_demand = (
                required_columns_by_node.get(body.node_id)
                if required_columns_by_node is not None
                else None
            )
            if isinstance(node_demand, AllExceptColumns):
                required_training_columns.update(node_demand.required_columns)
            elif node_demand is not None:
                required_training_columns.update(str(column) for column in node_demand)
            missing_training_columns = sorted(required_training_columns - schema_set)
            if missing_training_columns:
                self._lifecycle.transition(
                    job_id,
                    to="contract_error",
                    message=(
                        f"Training input is missing required column(s): {missing_training_columns}"
                    ),
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Training input is missing required column(s): "
                        f"{missing_training_columns}. Available columns: {schema_cols}"
                    ),
                )

            # Project down to only the columns needed for training.
            # This reduces peak memory during sink and all subsequent
            # phases (split, pool construction, diagnostics).
            if exclude and keep_columns:
                all_cols = schema_cols
                drop_cols = [c for c in all_cols if c in exclude and c not in keep_columns]
                if drop_cols:
                    target_lf = target_lf.drop(drop_cols)
                    _mem_checkpoint(f"projected: dropped {len(drop_cols)} excluded columns")

            from haute._polars_utils import (
                _malloc_trim,
                bounded_sink,
            )

            _mem_checkpoint("before sink_parquet")
            if execution_context is not None:
                execution_context.checkpoint(
                    label="before_training_sink_write",
                    node_id=body.node_id,
                )
                with execution_context.stage("training_sink_write", node_id=body.node_id):
                    bounded_sink(
                        target_lf,
                        tmp_parquet,
                        streaming_chunk_size=chunk_size,
                    )
                execution_context.checkpoint(
                    label="after_training_sink_write",
                    node_id=body.node_id,
                )
            else:
                bounded_sink(
                    target_lf,
                    tmp_parquet,
                    streaming_chunk_size=chunk_size,
                )

            del lazy_outputs, target_lf
            gc.collect()
            _malloc_trim()
            _mem_checkpoint("sunk to temp parquet")
        except ExecutionMemoryLimitExceededError as exc:
            if Path(tmp_parquet).exists():
                os.unlink(tmp_parquet)
            logger.warning(
                "pipeline_exec_memory_limited",
                error=str(exc),
                node_id=body.node_id,
            )
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                message=str(exc),
            )
            raise _memory_limit_http_exception(exc) from None
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            if Path(tmp_parquet).exists():
                os.unlink(tmp_parquet)
            self._lifecycle.transition(
                job_id,
                to="contract_error",
                message=str(exc),
                fields=contract_error_job_fields(exc),
            )
            raise contract_error_http_exception(exc) from None
        except BoundedMemoryUnsupportedError as exc:
            if Path(tmp_parquet).exists():
                os.unlink(tmp_parquet)
            error_msg = f"Pipeline cannot run in bounded streaming mode: {exc}"
            logger.warning(
                "pipeline_bounded_streaming_unsupported",
                error=str(exc),
                node_id=body.node_id,
            )
            self._lifecycle.transition(
                job_id,
                to="contract_error",
                message=error_msg,
            )
            raise HTTPException(status_code=422, detail=error_msg) from None
        except HTTPException:
            if Path(tmp_parquet).exists():
                os.unlink(tmp_parquet)
            raise
        except Exception as exc:
            if Path(tmp_parquet).exists():
                os.unlink(tmp_parquet)
            error_msg = f"Pipeline execution failed: {exc}"
            logger.error("pipeline_exec_failed", error=str(exc), node_id=body.node_id)
            self._lifecycle.transition(
                job_id,
                to="error",
                message=error_msg,
            )
            raise HTTPException(
                status_code=500,
                detail="Pipeline execution failed. Check the server logs for details.",
            )
        finally:
            if execution_context is not None:
                self._store.update_job(
                    job_id,
                    execution_metrics=execution_context.metrics_payload(),
                )
            if checkpoint_dir and checkpoint_dir.exists():
                shutil.rmtree(checkpoint_dir, ignore_errors=True)

        return tmp_parquet

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
        self._training_jobs.register_latest(
            (_TRAINING_JOB_TYPE, job_id),
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
            staged_response = TrainResponse.model_validate(response_fields)
            _assert_json_finite(staged_response)
            if staged_response.status != "completed" or staged_response.job_id != job_id:
                raise WorkerProtocolError(
                    "Training response status or job identifier does not match request"
                )
            model_artifacts = [
                artifact for artifact in result.artifacts if artifact.kind == "model"
            ]
            if (
                len(model_artifacts) != 1
                or staged_response.model_path != model_artifacts[0].relative_path
            ):
                raise WorkerProtocolError(
                    "Training response model path does not match the staged model manifest"
                )
            execution_metrics = result.metadata.get("execution_metrics")
            if not isinstance(execution_metrics, dict):
                raise WorkerProtocolError("Training execution metrics must be an object")
            published = _publish_training_artifacts(
                result,
                artifact_root=artifact_root,
                output_root=output_root,
                job_id=job_id,
                expected_model_name=str(job_kwargs["name"]),
            )
            artifact_publication_committed.set()
            response_fields["model_path"] = str(published["model"])
            response = TrainResponse.model_validate(response_fields)
            _assert_json_finite(response)
            return {
                "result": response,
                "execution_metrics": execution_metrics,
                "progress": 1.0,
            }

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
                artifact_kinds=frozenset({"model", "feature_contract"}),
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
