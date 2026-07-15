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
from collections.abc import Iterable, Mapping
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
)
from haute._graph_utils import upstream_node_ids
from haute._logging import get_logger
from haute._types import GraphNode, PipelineGraph
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
from haute.routes._background_jobs import BackgroundJobStoppedError, CancellableJobRegistry
from haute.routes._helpers import find_typed_node
from haute.routes._job_lifecycle import (
    JobLifecycle,
    bind_running_execution_metrics_publisher,
)
from haute.routes._job_store import JobStore
from haute.schemas import TrainRequest, TrainResponse

logger = get_logger(component="server.modelling.train")

# ── Default constants ─────────────────────────────────────────────
_DEFAULT_BORDER_COUNT = 128  # CatBoost border count for VRAM estimation
_DEFAULT_DEPTH = 6  # CatBoost tree depth for VRAM estimation
_TRAINING_JOB_TYPE = "training"
_JOB_TYPE_KEY = "job_type"


# Env-tunable defaults — resolved per call so overrides set after import
# take effect.
def _default_train_timeout() -> int:
    return int_env("HAUTE_TRAIN_TIMEOUT", 3600)


def _max_train_loss_history() -> int:
    return int_env("HAUTE_TRAIN_LOSS_HISTORY_LIMIT", 200)


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

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._lifecycle = JobLifecycle(store)
        self._training_jobs = CancellableJobRegistry()
        self._start_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start(self, body: TrainRequest) -> TrainResponse:
        """Validate config, execute pipeline, and launch training in a background thread.

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
            job_id = self._store.create_job(
                {
                    "status": "running",
                    _JOB_TYPE_KEY: _TRAINING_JOB_TYPE,
                    "progress": 0.0,
                    "message": "Starting",
                    "config": dict(config),
                    "node_label": node.data.label,
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

        return TrainResponse(status="started", job_id=job_id)

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
        self._training_jobs.release(job_id)
        return updated_job if updated_job is not None else self._store.require_job(job_id)

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
        self._training_jobs.release(job_id)
        return updated_job if updated_job is not None else self._store.require_job(job_id)

    def _raise_if_training_stopped(
        self,
        job_id: str,
        *,
        execution_context: ExecutionContext,
    ) -> None:
        reason = self._training_jobs.cancellation_reason(job_id)
        if reason is not None:
            raise BackgroundJobStoppedError(job_id, reason)
        execution_context.checkpoint(label="training_worker_checkpoint")
        job = self._store.require_job(job_id)
        status = str(job.get("status", "running"))
        if status != "running":
            raise BackgroundJobStoppedError(job_id, str(job.get("terminal_reason", status)))

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
    ) -> None:
        """Run TrainingJob in a background thread, owning admission release."""
        # Ownership of ``execution_context`` transfers here from ``start()``;
        # the worker's ``finally`` releases admission so the in-flight
        # reservation and memory ceiling stay armed across fit/eval/MLflow.
        from haute.modelling import TrainingJob

        start_time = time.monotonic()
        self._training_jobs.register_latest(
            (_TRAINING_JOB_TYPE, job_id),
            job_id,
            execution_token=execution_context.cancellation_token,
        )
        self._store.atomic_update(
            job_id,
            {
                "start_time": start_time,
                "timeout": config.get("timeout", _default_train_timeout()),
            },
        )

        def _progress(msg: str, frac: float) -> None:
            self._raise_if_training_stopped(job_id, execution_context=execution_context)
            self._store.atomic_update(
                job_id,
                {
                    "progress": frac,
                    "message": msg,
                    "elapsed_seconds": time.monotonic() - start_time,
                },
                expected_status="running",
            )

        def _on_iteration(iteration: int, total: int, metrics: dict[str, float]) -> None:
            self._raise_if_training_stopped(job_id, execution_context=execution_context)
            current_job = self._store.get_job(job_id) or {}
            history = list(current_job.get("train_loss_history") or [])
            history.append({"iteration": float(iteration), **metrics})
            truncated = bool(current_job.get("train_loss_history_truncated"))
            if len(history) > _max_train_loss_history():
                history = history[-_max_train_loss_history() :]
                truncated = True
            self._store.atomic_update(
                job_id,
                {
                    "iteration": iteration,
                    "total_iterations": total,
                    "train_loss": metrics,
                    "train_loss_history": history,
                    "train_loss_history_truncated": truncated,
                    "elapsed_seconds": time.monotonic() - start_time,
                },
                expected_status="running",
            )

        # Shared config→kwargs builder (also used by script export) so live
        # training and exported scripts can never train different models.
        job_kwargs = build_training_job_kwargs(config, data=tmp_parquet, default_name=node_id)
        # ``train_params`` is the canonical params dict built in ``start()``
        # and threaded through the GPU feasibility check (which may adjust it
        # in place) — it supersedes the freshly built copy.
        job_kwargs["params"] = train_params
        job = TrainingJob(**job_kwargs)

        def _train_background() -> None:
            try:
                train_result = job.run(
                    _progress,
                    _on_iteration,
                    check_cancelled=lambda: self._raise_if_training_stopped(
                        job_id,
                        execution_context=execution_context,
                    ),
                    execution_context=execution_context,
                )
                loss_history, loss_history_truncated = _bounded_loss_history(
                    train_result.loss_history,
                )
                response = TrainResponse(
                    status="completed",
                    job_id=job_id,
                    metrics=train_result.metrics,
                    feature_importance=train_result.feature_importance,
                    model_path=train_result.model_path,
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
                    warning=ram_warning,
                    total_source_rows=total_source_rows,
                )
                _assert_json_finite(response)
                self._lifecycle.transition(
                    job_id,
                    to="completed",
                    message="Completed",
                    fields={
                        "result": response,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                )
            except BackgroundJobStoppedError as exc:
                logger.info(
                    "training_worker_stopped",
                    job_id=job_id,
                    terminal_reason=exc.terminal_reason,
                )
            except ExecutionCancelledError:
                self._lifecycle.transition(
                    job_id,
                    to="cancelled",
                    message="Cancelled",
                    elapsed_seconds=time.monotonic() - start_time,
                )
            except ExecutionMemoryLimitExceededError as exc:
                payload = exc.to_payload()
                self._lifecycle.transition(
                    job_id,
                    to="memory_limited",
                    message=str(exc),
                    fields={
                        "error": str(exc),
                        "error_detail": payload,
                        "error_code": "memory_limit",
                        "http_status_code": 507,
                    },
                    elapsed_seconds=time.monotonic() - start_time,
                )
            except BoundedMemoryUnsupportedError as exc:
                error_msg = f"Training cannot run in bounded streaming mode: {exc}"
                self._lifecycle.transition(
                    job_id,
                    to="contract_error",
                    message=error_msg,
                    fields={"error": error_msg},
                    elapsed_seconds=time.monotonic() - start_time,
                )
            except ValueError as exc:
                error_msg = str(exc)
                logger.warning("training_validation_error", error=error_msg, node_id=node_id)
                self._lifecycle.transition(
                    job_id,
                    to="contract_error",
                    message=error_msg,
                    elapsed_seconds=time.monotonic() - start_time,
                )
            except Exception as exc:
                error_msg = _friendly_error(exc)
                logger.error("training_failed", error=str(exc), node_id=node_id)
                self._lifecycle.transition(
                    job_id,
                    to="error",
                    message=error_msg,
                    elapsed_seconds=time.monotonic() - start_time,
                )
            finally:
                current = self._store.get_job(job_id)
                if current is not None:
                    self._store.update_job(
                        job_id,
                        execution_metrics=execution_context.metrics_payload(
                            status=str(current.get("status"))
                            if current.get("status") is not None
                            else None,
                            terminal_reason=(
                                str(current.get("terminal_reason"))
                                if current.get("terminal_reason") is not None
                                else None
                            ),
                        ),
                    )
                self._training_jobs.release(job_id)
                execution_context.release_admission()
                if Path(tmp_parquet).exists():
                    os.unlink(tmp_parquet)

        try:
            thread = threading.Thread(target=_train_background, daemon=True)
            thread.start()
        except Exception as exc:
            if Path(tmp_parquet).exists():
                os.unlink(tmp_parquet)
            logger.error("training_worker_start_failed", error=str(exc), node_id=node_id)
            self._lifecycle.transition(
                job_id,
                to="error",
                message=f"Failed to start training worker: {exc}",
                elapsed_seconds=time.monotonic() - start_time,
            )
            self._training_jobs.release(job_id)
            execution_context.release_admission()
            raise HTTPException(
                status_code=500,
                detail="Training worker failed to start. Check the server logs for details.",
            ) from exc
