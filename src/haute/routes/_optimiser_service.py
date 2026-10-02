"""OptimiserSolveService — orchestrates optimisation solving, extracted from the route handler.

The route handler becomes a thin adapter that delegates to
``OptimiserSolveService.start()``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import gc
import math
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NotRequired, cast

import numpy as np
from fastapi import HTTPException

if TYPE_CHECKING:
    import polars as pl
    from price_contour import QuoteGrid


from haute._config_validation import validate_optimiser_analysis_config
from haute._env import int_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    admit_growth_grant,
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
from haute._graph_utils import (
    upstream_node_ids,
)
from haute._interactive_workers import (
    InteractiveWorkerError,
    InteractiveWorkerStoppedError,
    resolve_interactive_execution_mode,
)
from haute._logging import get_logger
from haute._memory_errors import memory_error_in
from haute._pipeline_settings import project_pipeline_settings
from haute._polars_utils import (
    bounded_collect_batches,
    current_streaming_chunk_size,
    streaming_collect,
)
from haute._sandbox import _get_project_root
from haute._seed_plans import SeedPlan, SeedPlanHandoff, SeedPlanRequest, open_seed_plan
from haute._step_progress import StepProgress
from haute._types import (
    GraphNode,
    PipelineGraph,
)
from haute._worker_isolation import (
    IsolatedWorkerError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
    isolated_worker_failure_is_memory,
    isolated_worker_memory_detail,
    run_isolated_worker,
    worker_config_for_memory_policy,
)
from haute.errors import (
    BoundedMemoryUnsupportedError,
    ConfigError,
    ContractMismatchError,
    SchemaMismatchError,
)
from haute.execution import (
    execute_lazy_graph,
)
from haute.graph_utils import flatten_graph, graph_fingerprint
from haute.routes import _optimiser_artifacts
from haute.routes._background_jobs import (
    BackgroundJobStoppedError,
    CancellableJobRegistry,
    SingleFlightCoordinator,
    SingleFlightHandle,
)
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_http_exception,
    contract_error_job_fields,
    contract_error_terminal_reason,
    memory_limit_http_exception,
)
from haute.routes._job_lifecycle import (
    TERMINAL_REASONS,
    JobLifecycle,
    TerminalReason,
    bind_running_execution_metrics_publisher,
    require_job_status,
)
from haute.routes._job_store import (
    JobSnapshot,
    JobStore,
    RunningJobFields,
)
from haute.routes._optimiser_input import (
    _NULL_QUOTE_ID_DETAIL_PREFIX,
    AutoRangeValueCheck,
    OptimiserSetupError,
    _execution_stage,
    _find_optimiser_node,
    _optimiser_side_input_ids,
    _optimiser_solve_required_columns_by_node,
    _positive_int,
    _resolve_optimiser_data_input_id,
    _setup_execution_target_node_id,
    build_quote_grid,
    estimate_input_metrics,
    extract_ratebook_factors,
    grid_chunk_decision,
    grid_construction_failures,
    resolve_analysis_frame,
    resolve_analysis_plan,
    resolve_data_input_frame,
    scenario_step_count,
    validate_and_project,
    validate_and_project_auto_range,
    write_solver_input,
)
from haute.routes._optimiser_outcomes import (
    require_one_row_per_solved_quote,
    scenario_grid_from_values,
    write_quote_analysis,
)
from haute.routes._optimiser_session import (
    ESTIMATE_HOLDERS,
    ESTIMATE_WAIT_SECONDS,
    RUNTIME_MODE_KEY,
    SESSION_KEY,
    SESSION_RUNTIME,
    SessionCommandError,
    SolverSession,
)
from haute.routes._optimiser_session_worker import (
    SessionSolveOutcome,
    SessionSolveRequest,
    build_and_solve,
)
from haute.routes._optimiser_solver import (
    SolveContext,
    _compute_ratebook_factor_level_order,
    _job_elapsed_seconds,
    _OptimiserSolveInputError,
    _OptimiserSolverExecutionError,
    _solve_online,
    _solve_ratebook,
    anchor_swept_constraints,
    solver_worker_context,
)
from haute.routes._optimiser_worker import (
    FrontierAutoRangeWorkerOutcome,
    FrontierAutoRangeWorkerRequest,
    OptimiserWorkerFailureError,
    SolveInput,
    SolveInputWorkerOutcome,
    SolveInputWorkerRequest,
    frontier_auto_range_worker,
    materialise_solve_input_worker,
    resolve_optimiser_polars_threads,
    worker_scratch_directory,
)
from haute.schemas import (
    OptimiserEstimateRequest,
    OptimiserFrontierAutoRangeRequest,
    OptimiserFrontierAutoRangeResponse,
    OptimiserFrontierAutoRangeStartResponse,
    OptimiserFrontierAutoRangeStatusResponse,
    OptimiserFrontierRange,
    OptimiserSolveRequest,
    OptimiserSolveResponse,
)

logger = get_logger(component="server.optimiser.solve")


@dataclass(frozen=True, slots=True)
class _AnalysisSource:
    """The analysis columns to keep, and the side-input frame they come from.

    ``frame`` is ``None`` on the data-input path, where the columns are read
    back from the written solver input.
    """

    columns: tuple[str, ...]
    frame: Any


@dataclass(frozen=True, slots=True)
class SetupGrid:
    """The quote grid setup built, and the analysis table it wrote (setup-owned until adopted)."""

    grid: QuoteGrid
    quote_analysis_handle: dict[str, Any] | None


def _optimisation_time_limit() -> int | None:
    """The optimisation time limit in whole seconds, ``None`` for no limit.

    Read from the pipeline settings per call; a solve or auto-range whose
    config sets its own timeout uses that instead.
    """
    seconds = project_pipeline_settings().optimisation_time_limit_seconds
    return None if seconds is None else math.ceil(seconds)


def _default_reducer_budget_mb() -> int:
    # the auto-range reducer's byte budget cap, before the headroom term
    return int_env("HAUTE_OPTIMISER_REDUCER_BUDGET_MB", 512)


_JOB_TYPE_KEY = "job_type"


_SOLVE_JOB_TYPE: Literal["solve"] = "solve"
_ESTIMATE_JOB_TYPE: Literal["estimate"] = "estimate"
_FRONTIER_AUTO_RANGE_JOB_TYPE: Literal["frontier_auto_range"] = "frontier_auto_range"
_FRONTIER_RECOMPUTE_JOB_TYPE: Literal["frontier_recompute"] = "frontier_recompute"
_GRAPH_NODE_SETUP_COORDINATION_TYPE = "optimiser_graph_node_setup"
_FRONTIER_AUTO_RANGE_CANCELLED_STATUS = "cancelled"
_FRONTIER_AUTO_RANGE_SUPERSEDED_STATUS = "superseded"
_FRONTIER_AUTO_RANGE_TERMINAL_STATUSES = TERMINAL_REASONS


class _OptimiserSolveRunningJob(RunningJobFields):
    job_type: Literal["solve"]
    progress: float
    config: dict[str, Any]
    node_label: str
    # Absent on the worker process's private setup job, which never publishes.
    input_provenance: NotRequired[dict[str, str | None]]
    start_time: float
    timeout: int | None


def _solve_input_provenance(
    graph: PipelineGraph,
    node_id: str,
    *,
    graph_fingerprint: str,
) -> dict[str, str | None]:
    """Cheap provenance for a solve's published artifacts, recorded at job creation.

    Every value is already at hand when the solve starts; nothing is hashed or read.
    """
    from haute.executor import _resolve_batch_scenario

    return {
        "node_id": node_id,
        "data_source": _resolve_batch_scenario(graph) or "batch",
        "source_file": graph.source_file,
        "graph_fingerprint": graph_fingerprint,
    }


class _OptimiserEstimateRunningJob(RunningJobFields):
    job_type: Literal["estimate"]
    config: dict[str, Any]
    node_label: str


class _FrontierAutoRangeRunningJob(RunningJobFields):
    job_type: Literal["frontier_auto_range"]
    progress: float
    config: dict[str, Any]
    node_label: str


_NON_BLOCKING_RUNNING_JOB_TYPES = frozenset(
    {
        _ESTIMATE_JOB_TYPE,
        _FRONTIER_AUTO_RANGE_JOB_TYPE,
        # A frontier recompute re-solves on a completed job's stored runtime
        # state; it never reserved the solve slot when it ran inline, and the
        # background offload keeps that semantics.
        _FRONTIER_RECOMPUTE_JOB_TYPE,
    }
)


# ---------------------------------------------------------------------------
# Solver worker-context guard
#
# The heavy solver entrypoints (full solves, frontier sweeps) are minutes of
# sequential CPU work; running one inline in a request handler silently
# starves the FastAPI worker pool. The guard turns that regression class into
# an immediate loud failure: only the background job runners enter
# ``solver_worker_context()``, and every guarded entrypoint refuses to run
# outside it. Pinned by
# ``tests/test_optimiser_routes.py::TestSolverWorkerContextGuard``.
# ---------------------------------------------------------------------------


def _with_flattened_optimiser_graph(
    body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
) -> OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest:
    """Return an optimiser request whose graph is executable by the lazy engine."""
    flat_graph = flatten_graph(body.graph)
    if flat_graph is body.graph:
        return body
    return body.model_copy(update={"graph": flat_graph})


def _is_memory_limit_http_exception(exc: HTTPException) -> bool:
    return (
        exc.status_code == 507
        and isinstance(exc.detail, Mapping)
        and exc.detail.get("error_code") == "memory_limit"
    )


def _normalise_memory_limit_payload(detail: object) -> dict[str, object]:
    if isinstance(detail, Mapping):
        payload = {str(key): value for key, value in detail.items()}
    else:
        payload = {"message": str(detail)}
    payload.setdefault("error_code", "memory_limit")
    return payload


def _memory_limit_message(payload: Mapping[str, object]) -> str:
    # A "message" key can only have been stamped by memory_limit_http_exception
    # (the exceptions' to_payload() carries no message) — prefer that curated
    # wording so the job's terminal message matches the HTTP surface.
    message = payload.get("message")
    if isinstance(message, str) and message:
        return message
    reason = payload.get("reason")
    if isinstance(reason, str) and reason:
        return f"Auto-range exceeded its memory budget ({reason})."
    return "Auto-range exceeded its memory budget."


def _memory_limit_job_update(
    *,
    detail: object,
    elapsed_seconds: float,
    execution_context: ExecutionContext,
) -> dict[str, object]:
    payload = _normalise_memory_limit_payload(detail)
    error_code = payload.get("error_code")
    if not isinstance(error_code, str) or not error_code:
        error_code = "memory_limit"
        payload["error_code"] = error_code
    return {
        "message": _memory_limit_message(payload),
        "elapsed_seconds": elapsed_seconds,
        "error_code": error_code,
        "http_status_code": 507,
        "error_detail": payload,
        "execution_metrics": execution_context.metrics_payload(
            status="memory_limited",
            terminal_reason="memory_limited",
        ),
    }


def _http_error_job_update(
    *,
    status_code: int,
    detail: object,
    elapsed_seconds: float,
    execution_context: ExecutionContext,
    terminal_reason: TerminalReason,
) -> dict[str, object]:
    return {
        "message": str(detail),
        "elapsed_seconds": elapsed_seconds,
        "http_status_code": status_code,
        "error_detail": detail,
        "execution_metrics": execution_context.metrics_payload(
            status=terminal_reason,
            terminal_reason=terminal_reason,
        ),
    }


def _http_exception_job_update(
    *,
    exc: HTTPException,
    elapsed_seconds: float,
    execution_context: ExecutionContext,
    terminal_reason: TerminalReason,
) -> dict[str, object]:
    return _http_error_job_update(
        status_code=exc.status_code,
        detail=exc.detail,
        elapsed_seconds=elapsed_seconds,
        execution_context=execution_context,
        terminal_reason=terminal_reason,
    )


def _coerce_stopped_terminal_reason(reason: str) -> TerminalReason:
    if reason in TERMINAL_REASONS:
        return cast(TerminalReason, reason)
    return "superseded"


_SOLVE_MEMORY_MESSAGE = (
    "The optimisation ran out of memory while solving. Reduce the number of quotes or "
    "scenario steps, or close other applications, then try again."
)


def solve_failure_transition(
    exc: Exception,
    *,
    node_id: str,
    elapsed_seconds: float,
) -> tuple[TerminalReason, str, dict[str, Any]]:
    """Classify one solve failure into its terminal reason, message and job fields.

    Shared by the thread-mode solver thread and the solver session's child, so
    both modes publish the same outcome for the same failure. A ``MemoryError``
    behind any translation is ``memory_limited``, never an algorithm error.
    """
    if isinstance(exc, ExecutionCancelledError):
        return "cancelled", "Cancelled", {}
    if isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
        return contract_error_terminal_reason(exc), str(exc), contract_error_job_fields(exc)
    if memory_error_in(exc) is not None:
        logger.error("solve_failed", error=str(exc), node_id=node_id, category="memory")
        return (
            "memory_limited",
            _SOLVE_MEMORY_MESSAGE,
            {
                "error_code": "memory_limit",
                "http_status_code": 507,
                "error_detail": {
                    "error_code": "memory_limit",
                    "operation": "optimiser_solve",
                    "reason": "solver_memory_error",
                    "message": _SOLVE_MEMORY_MESSAGE,
                },
            },
        )
    if isinstance(exc, _OptimiserSolveInputError):
        category, error_msg, reason = "data", f"Data error: {exc}", "contract_error"
    elif isinstance(exc, _OptimiserSolverExecutionError):
        category, error_msg, reason = "algorithm", f"Algorithm error: {exc}", "error"
    else:
        category, error_msg, reason = "unexpected", f"Unexpected error: {exc}", "error"
    logger.error(
        "solve_failed",
        error=str(exc),
        node_id=node_id,
        category=category,
        exc_info=True,
    )
    return (
        cast(TerminalReason, reason),
        error_msg,
        {"message": error_msg, "elapsed_seconds": elapsed_seconds},
    )


def _optional_positive_int(value: object, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    return _positive_int(value, field=field)


def _solve_timeout_from_config(config: Mapping[str, Any]) -> int | None:
    if "timeout" not in config:
        return _optimisation_time_limit()
    return _optional_positive_int(config.get("timeout"), field="timeout")


def _auto_range_timeout_from_config(config: dict[str, Any]) -> int | None:
    if "auto_range_timeout" not in config:
        return _optimisation_time_limit()
    return _positive_int(config["auto_range_timeout"], field="auto_range_timeout")


_MIB = 1024 * 1024
# The fallback finish's measured fixed cost (engine and allocator retention at
# eight threads): below it the finish's private growth exceeded its budget.
_REDUCER_MEASURED_MIN_BUDGET_BYTES = 384 * _MIB
# Row groups per partial file: bounds one file's footer while a pass still
# prunes most of a file's row groups by their bucket statistics.
_REDUCER_ROW_GROUPS_PER_PART = 64
# One footer column chunk as held in memory (4x its ~110-byte encoding).
_REDUCER_FOOTER_CHUNK_BYTES = 440
# Group-by state per distinct quote relative to its decoded partial row.
_REDUCER_GROUP_STATE_FACTOR = 4
# A finished-quote hash costs 8 bytes, plus 1 for the final duplicate scan
# and room for the one-off concatenation before the sort.
_REDUCER_HASH_BUDGET_DIVISOR = 18
_REDUCER_BUCKET_COUNT = 65_536
_REDUCER_BUCKET_DIVISOR = 2**48
_REDUCER_BUCKET_COLUMN = "__haute_frontier_range_bucket"
_REDUCER_ROW_COLUMN = "__haute_frontier_range_row"
_REDUCER_FIRST_COLUMN = "__haute_frontier_range_first"
_REDUCER_LAST_COLUMN = "__haute_frontier_range_last"
_REDUCER_COUNT_COLUMN = "__haute_frontier_range_count"
_REDUCER_HASH_COLUMN = "__haute_frontier_range_hash"
_REDUCER_BUDGET_SETTING = "HAUTE_OPTIMISER_REDUCER_BUDGET_MB"
_REDUCER_HEADROOM_SETTING = "HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB"

ReducerFallbackReason = Literal["batch_not_contiguous", "quote_reappeared", "hash_buffer_full"]


@dataclass(frozen=True, slots=True)
class _ReducerBudget:
    """The reducer's byte budget G_r and the setting that bounds it."""

    budget_bytes: int
    setting: str


def _reducer_budget(execution_context: ExecutionContext | None) -> _ReducerBudget:
    """G_r = min(cap, headroom // 4); the cap alone when there is no effective limit.

    The headroom is the smaller of the execution context's remaining RSS
    headroom and the worker's native cap headroom (ceiling minus current
    charge): equal grants do not leave equal allowances, since a worker can
    near its private-byte cap with RSS to spare.
    """
    from haute._native_memory_limit import native_headroom_bytes

    cap = _default_reducer_budget_mb() * _MIB
    allowances = [
        allowance
        for allowance in (
            None if execution_context is None else execution_context.remaining_memory_bytes(),
            native_headroom_bytes(),
        )
        if allowance is not None
    ]
    headroom = min(allowances) if allowances else None
    if headroom is None or cap <= headroom // 4:
        return _ReducerBudget(budget_bytes=cap, setting=_REDUCER_BUDGET_SETTING)
    return _ReducerBudget(budget_bytes=headroom // 4, setting=_REDUCER_HEADROOM_SETTING)


def _reducer_partial_row_bytes(constraint_count: int, key_width: float) -> float:
    """b_raw: one decoded partial row (key, a Float64 min and max per constraint, bucket)."""
    return key_width + 16 * constraint_count + 16


def _reducer_min_budget_bytes(constraint_count: int, key_width: float, batch_row_cap: int) -> int:
    """G_min, below which the exact fallback finish cannot run within its budget.

    The larger of the measured fixed cost and eight times one partial file's
    in-memory footer plus two of its decoded row groups.
    """
    footer = _REDUCER_ROW_GROUPS_PER_PART * (2 * constraint_count + 2) * _REDUCER_FOOTER_CHUNK_BYTES
    rows_per_group = math.ceil(batch_row_cap / _REDUCER_ROW_GROUPS_PER_PART)
    structural = 8 * (
        footer + 2 * rows_per_group * _reducer_partial_row_bytes(constraint_count, key_width)
    )
    return max(_REDUCER_MEASURED_MIN_BUDGET_BYTES, math.ceil(structural))


def _reducer_fallback_pass_count(
    partial_rows: int, row_state_bytes: float, budget_bytes: int
) -> int:
    """P: the smallest power of two whose passes keep group state within half the budget."""
    needed = math.ceil(partial_rows * row_state_bytes / max(1, budget_bytes // 2))
    passes = 1
    while passes < needed and passes < _REDUCER_BUCKET_COUNT:
        passes *= 2
    return passes


class AutoRangeReducerBudgetError(ExecutionMemoryLimitExceededError):
    """The frame needs the exact fallback, which the reducer's budget cannot hold."""

    def __init__(
        self,
        *,
        budget_bytes: int,
        minimum_bytes: int,
        violation: str,
        setting: str,
        job_id: str | None = None,
    ) -> None:
        super().__init__(
            "frontier_auto_range_reducer",
            rss_bytes=minimum_bytes,
            limit_bytes=budget_bytes,
            job_id=job_id,
            reason="reducer_budget_below_minimum",
        )
        self.budget_bytes = budget_bytes
        self.minimum_bytes = minimum_bytes
        self.violation = violation
        self.setting = setting
        self.args = (
            f"The auto-range reducer's memory budget ({budget_bytes} bytes) is below "
            f"its minimum ({minimum_bytes} bytes), and the scenario frame needs its "
            f"exact fallback ({violation}); raise {setting}.",
        )

    def to_payload(self) -> dict[str, object]:
        payload = super().to_payload()
        payload.update(
            {
                "reducer_budget_bytes": self.budget_bytes,
                "reducer_min_budget_bytes": self.minimum_bytes,
                "violation": self.violation,
                "setting": self.setting,
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class _CarriedQuote:
    """The previous batch's last quote, held back in case the next batch continues it."""

    quote_id: str
    quote_hash: int
    values: tuple[float | None, ...]


def _combine_extremum(left: float | None, right: float | None, *, is_min: bool) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right) if is_min else max(left, right)


class _ScenarioFrontierRangeAccumulator:
    """Exact per-quote scenario extrema totals over bounded batches, in Float64.

    Only final per-quote extrema are summed: a quote's extrema from several
    batches are combined (min of mins, max of maxes) first, and nothing is
    ever subtracted.

    **Carry path.** Batches are almost always grouped by quote (the expander
    emits a quote's scenarios together and captures keep order), so one
    group-by per batch gives each quote's extrema plus its first/last row and
    count. The batch is contiguous iff ``last - first + 1 == n`` for every
    quote. The batch's last quote is held back and combined with the next
    batch's first quote when they match; every other quote is finished, its
    extrema added to running sums and its hash buffered. At the finish a
    repeated hash (a quote that reappeared, or a collision) means the sums
    cannot be trusted.

    **Fallback.** Every batch also writes its per-quote partial to one sorted,
    bucketed parquet file, because a violation can appear after earlier
    batches were reduced. On a violation the carry state is dropped and the
    finish combines the partials by quote value in bucket-range passes,
    reading one file at a time. Below :func:`_reducer_min_budget_bytes` the
    fallback is unavailable: no partials are written and a violation fails the
    job as ``memory_limited``.
    """

    def __init__(
        self,
        *,
        quote_id_col: str,
        constraint_cols: list[str],
        parts_root: Path,
        budget: _ReducerBudget,
        batch_row_cap: int,
        job_id: str | None = None,
    ) -> None:
        import polars as pl

        self.quote_id_col = quote_id_col
        self.constraint_cols = list(constraint_cols)
        self.parts_root = parts_root
        self.budget = budget
        self.batch_row_cap = batch_row_cap
        self.job_id = job_id
        self.row_count = 0
        self.null_quote_id_count = 0
        self.aliases: dict[str, tuple[str, str]] = {}
        self.extrema_columns: list[str] = []
        self.is_min: list[bool] = []
        self.aggregate_exprs = []
        self.combine_exprs = []
        self.sum_exprs = []
        for idx, cname in enumerate(self.constraint_cols):
            min_alias = f"__haute_frontier_min_{idx}"
            max_alias = f"__haute_frontier_max_{idx}"
            self.aliases[cname] = (min_alias, max_alias)
            self.extrema_columns.extend([min_alias, max_alias])
            self.is_min.extend([True, False])
            self.aggregate_exprs.extend(
                [
                    pl.col(cname).min().cast(pl.Float64).alias(min_alias),
                    pl.col(cname).max().cast(pl.Float64).alias(max_alias),
                ]
            )
            self.combine_exprs.extend(
                [
                    pl.col(min_alias).min().alias(min_alias),
                    pl.col(max_alias).max().alias(max_alias),
                ]
            )
        self.sum_exprs = [pl.col(alias).sum().alias(alias) for alias in self.extrema_columns]
        # Fallback: one partial file per batch while the fallback is available.
        self.key_width = 0.0
        self.min_budget_bytes: int | None = None
        self.fallback_available: bool | None = None
        self.part_files: list[Path] = []
        self.partial_rows = 0
        # Carry path state, dropped when the carry path is turned off.
        self.carry_path = True
        self.fallback_reason: ReducerFallbackReason | None = None
        self.carry: _CarriedQuote | None = None
        self.totals: list[float] = [0.0] * len(self.extrema_columns)
        self.hash_chunks: list[np.ndarray] = []
        self.hash_count = 0
        self.hash_capacity = budget.budget_bytes // _REDUCER_HASH_BUDGET_DIVISOR

    def add_batch(self, batch: pl.DataFrame, *, batch_index: int) -> None:
        import polars as pl

        if batch.height == 0:
            return
        self.row_count += batch.height
        null_count = int(batch[self.quote_id_col].null_count())
        if null_count > 0:
            self.null_quote_id_count += null_count
            return
        if self.null_quote_id_count > 0:
            # The finish raises the null-quote error; reducing further is waste.
            return

        partial = (
            batch.with_row_index(_REDUCER_ROW_COLUMN)
            .group_by(self.quote_id_col)
            .agg(
                *self.aggregate_exprs,
                pl.col(_REDUCER_ROW_COLUMN).min().alias(_REDUCER_FIRST_COLUMN),
                pl.col(_REDUCER_ROW_COLUMN).max().alias(_REDUCER_LAST_COLUMN),
                pl.len().alias(_REDUCER_COUNT_COLUMN),
            )
            .with_columns(pl.col(self.quote_id_col).hash(seed=0).alias(_REDUCER_HASH_COLUMN))
        )
        self._update_fallback_availability(partial)
        if self.fallback_available:
            self._write_partial(partial, batch_index=batch_index)
        if not self.carry_path:
            return
        contiguous = bool(
            partial.select(
                (
                    pl.col(_REDUCER_LAST_COLUMN) - pl.col(_REDUCER_FIRST_COLUMN) + 1
                    == pl.col(_REDUCER_COUNT_COLUMN)
                ).all()
            ).item()
        )
        if not contiguous:
            self._leave_carry_path("batch_not_contiguous", batch_index=batch_index)
            return
        self._carry_batch(partial, height=batch.height, batch_index=batch_index)

    def _update_fallback_availability(self, partial: pl.DataFrame) -> None:
        """Re-check G_min with the widest key seen; once unavailable, the fallback stays so."""
        from haute._ram_estimate import string_view_bytes_per_row

        if self.fallback_available is False:
            return
        self.key_width = max(
            self.key_width,
            string_view_bytes_per_row(partial.get_column(self.quote_id_col)),
        )
        self.min_budget_bytes = _reducer_min_budget_bytes(
            len(self.constraint_cols),
            self.key_width,
            self.batch_row_cap,
        )
        available = self.budget.budget_bytes >= self.min_budget_bytes
        was_available = self.fallback_available
        self.fallback_available = available
        if not available:
            # Partials already written can no longer be completed by later ones.
            self._discard_partials()
            if was_available and not self.carry_path and self.fallback_reason is not None:
                raise self._budget_error(self.fallback_reason)

    def _discard_partials(self) -> None:
        for path in self.part_files:
            path.unlink(missing_ok=True)
        self.part_files = []
        self.partial_rows = 0

    def _write_partial(self, partial: pl.DataFrame, *, batch_index: int) -> None:
        """One lz4 file per batch, sorted by a 16-bit bucket, at most 64 row groups."""
        import polars as pl

        part = partial.select(
            self.quote_id_col,
            *self.extrema_columns,
            (pl.col(_REDUCER_HASH_COLUMN) // pl.lit(_REDUCER_BUCKET_DIVISOR, dtype=pl.UInt64))
            .cast(pl.UInt16)
            .alias(_REDUCER_BUCKET_COLUMN),
        ).sort(_REDUCER_BUCKET_COLUMN)
        path = self.parts_root / f"part_{batch_index:08d}.parquet"
        part.write_parquet(
            path,
            compression="lz4",
            statistics=True,
            row_group_size=max(1, math.ceil(part.height / _REDUCER_ROW_GROUPS_PER_PART)),
        )
        self.part_files.append(path)
        self.partial_rows += part.height

    def _budget_error(self, violation: str) -> AutoRangeReducerBudgetError:
        return AutoRangeReducerBudgetError(
            budget_bytes=self.budget.budget_bytes,
            minimum_bytes=int(self.min_budget_bytes or 0),
            violation=violation,
            setting=self.budget.setting,
            job_id=self.job_id,
        )

    def _leave_carry_path(self, reason: ReducerFallbackReason, *, batch_index: int | None) -> None:
        """Drop the carry state; the exact fallback finish, if available, takes over."""
        self.carry_path = False
        self.fallback_reason = reason
        self.carry = None
        self.totals = []
        self.hash_chunks = []
        self.hash_count = 0
        logger.info(
            "auto_range_reducer_fallback",
            reason=reason,
            batch_index=batch_index,
            fallback_available=bool(self.fallback_available),
            reducer_budget_bytes=self.budget.budget_bytes,
            reducer_min_budget_bytes=self.min_budget_bytes,
        )
        if not self.fallback_available:
            raise self._budget_error(reason)

    def _row_values(self, frame: pl.DataFrame) -> tuple[float | None, ...]:
        return tuple(frame.select(self.extrema_columns).row(0))

    def _combine_values(
        self,
        left: tuple[float | None, ...],
        right: tuple[float | None, ...],
    ) -> tuple[float | None, ...]:
        return tuple(
            _combine_extremum(a, b, is_min=is_min)
            for a, b, is_min in zip(left, right, self.is_min, strict=True)
        )

    def _carry_batch(self, partial: pl.DataFrame, *, height: int, batch_index: int) -> None:
        """Finish every quote but the batch's last, which becomes the new carry."""
        import polars as pl

        first_col = pl.col(_REDUCER_FIRST_COLUMN)
        last_col = pl.col(_REDUCER_LAST_COLUMN)
        head = partial.filter(first_col == 0)
        tail = partial.filter(last_col == height - 1)
        middle = partial.filter((first_col != 0) & (last_col != height - 1))
        head_quote = _CarriedQuote(
            quote_id=str(head.get_column(self.quote_id_col).item()),
            quote_hash=int(head.get_column(_REDUCER_HASH_COLUMN).item()),
            values=self._row_values(head),
        )
        finished: list[_CarriedQuote] = []
        if self.carry is not None:
            if self.carry.quote_id == head_quote.quote_id:
                head_quote = dataclasses.replace(
                    head_quote,
                    values=self._combine_values(self.carry.values, head_quote.values),
                )
            else:
                finished.append(self.carry)
        if partial.height == 1:
            new_carry = head_quote
        else:
            finished.append(head_quote)
            new_carry = _CarriedQuote(
                quote_id=str(tail.get_column(self.quote_id_col).item()),
                quote_hash=int(tail.get_column(_REDUCER_HASH_COLUMN).item()),
                values=self._row_values(tail),
            )
        new_hashes = np.concatenate(
            [
                np.fromiter((quote.quote_hash for quote in finished), dtype=np.uint64),
                middle.get_column(_REDUCER_HASH_COLUMN).to_numpy().astype(np.uint64, copy=False),
            ]
        )
        if not self._append_hashes(new_hashes, batch_index=batch_index):
            return
        middle_sums = middle.select(self.sum_exprs).row(0) if middle.height else ()
        for values in [*(quote.values for quote in finished), middle_sums]:
            self._add_to_totals(values)
        self.carry = new_carry

    def _append_hashes(self, hashes: np.ndarray, *, batch_index: int | None) -> bool:
        """Buffer finished-quote hashes; a buffer that would overflow ends the carry path."""
        if self.hash_count + hashes.size > self.hash_capacity:
            self._leave_carry_path("hash_buffer_full", batch_index=batch_index)
            return False
        if hashes.size:
            self.hash_chunks.append(hashes)
            self.hash_count += int(hashes.size)
        return True

    def _add_to_totals(self, values: Iterable[float | None]) -> None:
        for idx, value in enumerate(values):
            if value is not None:
                self.totals[idx] += float(value)

    def _carry_totals(self) -> list[float] | None:
        """The carry path's totals, or ``None`` when it must fall back at the finish."""
        if self.carry is not None:
            carry = self.carry
            self.carry = None
            if not self._append_hashes(
                np.array([carry.quote_hash], dtype=np.uint64), batch_index=None
            ):
                return None
            self._add_to_totals(carry.values)
        hashes = np.concatenate(self.hash_chunks) if self.hash_chunks else np.empty(0, np.uint64)
        self.hash_chunks = []
        hashes.sort()
        repeated = hashes.size > 1 and bool((hashes[1:] == hashes[:-1]).any())
        del hashes
        if repeated:
            self._leave_carry_path("quote_reappeared", batch_index=None)
            return None
        return self.totals

    def finish(
        self,
        *,
        check_cancelled: Callable[[], None] | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> dict[str, dict[str, float]]:
        if check_cancelled is not None:
            check_cancelled()
        if self.row_count == 0:
            raise ValueError("Unable to estimate frontier ranges from an empty scenario frame.")
        if self.null_quote_id_count > 0:
            detail = (
                f"{_NULL_QUOTE_ID_DETAIL_PREFIX} ({self.null_quote_id_count} rows). "
                "Every row must have a non-null quote_id; check upstream filters and joins."
            )
            raise ValueError(detail)

        totals = self._carry_totals() if self.carry_path else None
        if totals is None:
            totals = self._fallback_totals(
                check_cancelled=check_cancelled,
                execution_context=execution_context,
            )

        ranges: dict[str, dict[str, float]] = {}
        for cname, (min_alias, max_alias) in self.aliases.items():
            min_value = totals[self.extrema_columns.index(min_alias)]
            max_value = totals[self.extrema_columns.index(max_alias)]
            if not np.isfinite(min_value) or not np.isfinite(max_value):
                raise ValueError(f"Estimated frontier range for {cname!r} is not finite.")
            if min_value > max_value:
                raise ValueError(f"Estimated frontier range for {cname!r} is invalid.")
            ranges[cname] = {"min": min_value, "max": max_value}
        return ranges

    def _fallback_totals(
        self,
        *,
        check_cancelled: Callable[[], None] | None,
        execution_context: ExecutionContext | None,
    ) -> list[float]:
        """Today's bucketed combine-then-sum, in Float64, over bucket-range passes."""
        row_state_bytes = _REDUCER_GROUP_STATE_FACTOR * _reducer_partial_row_bytes(
            len(self.constraint_cols), self.key_width
        )
        passes = _reducer_fallback_pass_count(
            self.partial_rows, row_state_bytes, self.budget.budget_bytes
        )
        width = _REDUCER_BUCKET_COUNT // passes
        piece_threshold = max(1, self.budget.budget_bytes // 8)
        totals = [0.0] * len(self.extrema_columns)
        for pass_index in range(passes):
            if check_cancelled is not None:
                check_cancelled()
            if execution_context is not None:
                execution_context.checkpoint(label="frontier_range_fallback_pass_start")
            with _execution_stage(execution_context, "frontier_range_fallback_pass"):
                state = self._fallback_pass_state(
                    pass_index * width,
                    (pass_index + 1) * width - 1,
                    piece_threshold=piece_threshold,
                    check_cancelled=check_cancelled,
                    execution_context=execution_context,
                )
                if state is not None:
                    for idx, value in enumerate(state.select(self.sum_exprs).row(0)):
                        if value is not None:
                            totals[idx] += float(value)
                del state
            if execution_context is not None:
                execution_context.checkpoint(label="frontier_range_fallback_pass_done")
        return totals

    def _fallback_pass_state(
        self,
        low_bucket: int,
        high_bucket: int,
        *,
        piece_threshold: int,
        check_cancelled: Callable[[], None] | None,
        execution_context: ExecutionContext | None,
    ) -> pl.DataFrame | None:
        """One pass's per-quote state: every file's pieces in the bucket range, by quote value."""
        import polars as pl

        state: pl.DataFrame | None = None
        pieces: list[pl.DataFrame] = []
        buffered_bytes = 0
        for path in self.part_files:
            if check_cancelled is not None:
                check_cancelled()
            piece = streaming_collect(
                pl.scan_parquet(path)
                .filter(pl.col(_REDUCER_BUCKET_COLUMN).is_between(low_bucket, high_bucket))
                .drop(_REDUCER_BUCKET_COLUMN),
                execution_context=execution_context,
            )
            if piece.height == 0:
                continue
            pieces.append(piece)
            buffered_bytes += int(piece.estimated_size())
            if buffered_bytes >= piece_threshold:
                state = self._merge_pass_pieces(state, pieces)
                pieces = []
                buffered_bytes = 0
        if pieces:
            # The final flush: pieces below the threshold, or after the last
            # merge, belong in this pass's totals too.
            state = self._merge_pass_pieces(state, pieces)
        return state

    def _merge_pass_pieces(
        self,
        state: pl.DataFrame | None,
        pieces: list[pl.DataFrame],
    ) -> pl.DataFrame:
        import polars as pl

        frames = pieces if state is None else [state, *pieces]
        return pl.concat(frames, how="vertical").group_by(self.quote_id_col).agg(self.combine_exprs)


def _add_frontier_range_batch(
    accumulator: Any,
    batch: Any,
    *,
    batch_index: int,
    execution_context: ExecutionContext | None = None,
) -> None:
    if execution_context is not None:
        execution_context.checkpoint(label="frontier_range_batch_start")
    with _execution_stage(execution_context, "frontier_range_batch_reduce"):
        accumulator.add_batch(batch, batch_index=batch_index)
    if execution_context is not None:
        execution_context.checkpoint(label="frontier_range_batch_done")


@dataclass(frozen=True, slots=True)
class FrontierAutoRangeContext:
    """Per-job context for frontier auto-range estimation."""

    chunk_size: int = dataclasses.field(default_factory=current_streaming_chunk_size)
    execution_context: ExecutionContext | None = None


def _estimate_scenario_frontier_ranges(
    ctx: FrontierAutoRangeContext,
    *,
    scored_lf: Any,
    quote_id_col: str,
    constraint_cols: list[str],
    value_check: AutoRangeValueCheck | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, dict[str, float]]:
    """Return exact online achievable min/max totals from the scenario frame.

    For each constraint, each quote can independently choose the scenario that
    minimises or maximises that constraint total. The input is read in bounded
    batches in the frame's order and reduced by
    :class:`_ScenarioFrontierRangeAccumulator` without one global per-quote
    aggregate table.
    """
    if not constraint_cols:
        return {}

    if check_cancelled is not None:
        check_cancelled()
    chunk_size = _positive_int(ctx.chunk_size, field="chunk_size")
    execution_context = ctx.execution_context
    range_columns = _frontier_range_batch_columns(quote_id_col, constraint_cols)
    # ``chunk_size`` is the per-batch row count for the auto-range reducer;
    # the underlying scan and collect stream at the process chunk size. The
    # frame's quote order reaches the reducer, whose carry path relies on it
    # (and verifies it).
    raw_batches = bounded_collect_batches(
        scored_lf,
        chunk_size=chunk_size,
        maintain_order=True,
        execution_context=execution_context,
        stage_name="frontier_range_collect_batch",
    )

    def range_batches() -> Iterator[pl.DataFrame]:
        # Each batch is value-checked before it is reduced; once a batch
        # fails, later ones are still counted (for whole-frame totals) but no
        # longer reduced, and the violation is raised after the last one.
        for batch in raw_batches:
            if value_check is not None and not value_check.add(batch):
                continue
            yield batch.select(range_columns)
        if value_check is not None:
            value_check.raise_if_invalid()

    return _reduce_frontier_range_batches(
        range_batches(),
        quote_id_col=quote_id_col,
        constraint_cols=constraint_cols,
        batch_row_cap=chunk_size,
        check_cancelled=check_cancelled,
        execution_context=execution_context,
    )


def _frontier_range_batch_columns(quote_id_col: str, constraint_cols: list[str]) -> list[Any]:
    """The columns one range batch carries: the quote id as text, then each constraint as Float32.

    Float32 is the solver's precision for constraint values, so the ranges
    describe what the solver will see.
    """
    import polars as pl

    return [
        pl.col(quote_id_col).cast(pl.String).alias(quote_id_col),
        *[pl.col(cname).cast(pl.Float32) for cname in constraint_cols],
    ]


def _reduce_frontier_range_batches(
    batches: Iterable[pl.DataFrame],
    *,
    quote_id_col: str,
    constraint_cols: list[str],
    batch_row_cap: int,
    check_cancelled: Callable[[], None] | None = None,
    execution_context: ExecutionContext | None = None,
) -> dict[str, dict[str, float]]:
    """Reduce range batches of at most *batch_row_cap* rows to exact Float64 range totals.

    The reducer's budget is fixed when it starts; its partial files live in a
    private temporary directory removed on every exit.
    """
    budget = _reducer_budget(execution_context)
    with _optimiser_artifacts._range_parts_directory() as parts_root:
        accumulator = _ScenarioFrontierRangeAccumulator(
            quote_id_col=quote_id_col,
            constraint_cols=constraint_cols,
            parts_root=parts_root,
            budget=budget,
            batch_row_cap=batch_row_cap,
            job_id=None if execution_context is None else execution_context.job_id,
        )
        for batch_index, batch in enumerate(batches):
            if check_cancelled is not None:
                check_cancelled()
            _add_frontier_range_batch(
                accumulator,
                batch,
                batch_index=batch_index,
                execution_context=execution_context,
            )
        return accumulator.finish(
            check_cancelled=check_cancelled,
            execution_context=execution_context,
        )

    # The job store keeps these heavy runtime objects for its short
    # heavy-object retention window, then slims the completed job down to
    # API-facing summaries/metadata while preserving the 24h status record.


# price-contour names each constraint's outputs ``total_<name>``, ``lambda_<name>``
# and ``optimal_<name>``; these names collide with its own ``total_objective``,
# ``optimal_step`` and ``optimal_scenario_value`` columns.
RESERVED_OPTIMISER_CONSTRAINT_NAMES = frozenset({"objective", "step", "scenario_value"})


class OptimiserSolveService:
    """Orchestrates the full optimisation solve lifecycle.

    Parameters
    ----------
    store:
        The in-memory job store used to track optimisation jobs.
    """

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._lifecycle = JobLifecycle(store)
        self._start_lock = threading.Lock()
        self._jobs = CancellableJobRegistry()
        self._graph_node_setup_singleflight = SingleFlightCoordinator()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start(self, body: OptimiserSolveRequest) -> OptimiserSolveResponse:
        """Validate config, register a job, and launch setup in the background.

        Expensive data work must be attached to a pollable job before it
        starts. Otherwise large local runs can outlive the browser request and
        surface as an unhelpful aborted signal in the GUI.
        """
        body = cast(OptimiserSolveRequest, _with_flattened_optimiser_graph(body))
        node = _find_optimiser_node(body.graph, body.node_id)
        # A swept constraint is solved at its range's start; the job records that bound.
        config = anchor_swept_constraints(dict(node.data.config))

        mode = self._validate_config(config)
        factor_level_order = _compute_ratebook_factor_level_order(
            body.graph,
            body.node_id,
            config,
            mode,
        )
        required_columns_by_node = _optimiser_solve_required_columns_by_node(
            body.graph,
            body.node_id,
            config,
        )
        setup_job_key = self._graph_node_setup_job_key(body.graph, body.node_id)

        with self._start_lock:
            self._check_no_concurrent_jobs()
            active_setup = self._active_graph_node_setup(setup_job_key)
            if active_setup is not None:
                raise self._graph_node_setup_conflict(active_setup)
            start_time = time.monotonic()
            initial_job: _OptimiserSolveRunningJob = {
                "status": "running",
                "job_type": _SOLVE_JOB_TYPE,
                "progress": 0.0,
                "message": "Preparing optimiser input",
                "config": dict(config),
                "node_label": node.data.label,
                "input_provenance": _solve_input_provenance(
                    body.graph,
                    body.node_id,
                    graph_fingerprint=setup_job_key[2],
                ),
                "start_time": start_time,
                "timeout": _solve_timeout_from_config(config),
            }
            job_id = self._store.create_job(initial_job)
            execution_token = ExecutionCancellationToken()
            self._graph_node_setup_singleflight.acquire(
                setup_job_key,
                job_id=job_id,
                kind=_SOLVE_JOB_TYPE,
            )
            self._jobs.register_latest(
                (_SOLVE_JOB_TYPE, job_id),
                job_id,
                execution_token=execution_token,
            )
        logger.info("solve_started", node_id=body.node_id, mode=mode, job_id=job_id)

        self._launch_setup_background(
            body,
            job_id,
            config,
            mode,
            required_columns_by_node=required_columns_by_node,
            factor_level_order=factor_level_order,
            setup_job_key=setup_job_key,
            execution_token=execution_token,
        )
        return OptimiserSolveResponse(status="started", job_id=job_id)

    def _launch_setup_background(
        self,
        body: OptimiserSolveRequest,
        job_id: str,
        config: dict[str, Any],
        mode: str,
        *,
        required_columns_by_node: Mapping[str, frozenset[str]],
        factor_level_order: dict[str, list[str]],
        setup_job_key: tuple[str, str, str],
        execution_token: ExecutionCancellationToken,
    ) -> None:
        """Start the heavy solve setup path in a background thread."""

        def _setup_background() -> None:
            self._run_solve_setup_and_launch(
                body,
                job_id,
                config,
                mode,
                required_columns_by_node=required_columns_by_node,
                factor_level_order=factor_level_order,
                setup_job_key=setup_job_key,
                execution_token=execution_token,
            )

        thread = threading.Thread(target=_setup_background, daemon=True)
        try:
            thread.start()
        except Exception as exc:
            logger.error(
                "solve_setup_worker_start_failed",
                error=str(exc),
                node_id=body.node_id,
                job_id=job_id,
                exc_info=True,
            )
            self._lifecycle.transition(
                job_id,
                to="error",
                message=f"Failed to start optimiser setup worker: {exc}",
                elapsed_seconds=_job_elapsed_seconds(self._store.require_job(job_id)),
            )
            self._release_job_ownership(job_id, setup_singleflight_key=setup_job_key)
            raise HTTPException(
                status_code=500,
                detail="Optimiser setup worker failed to start. Check the server logs for details.",
            ) from exc

    def _run_solve_setup_and_launch(
        self,
        body: OptimiserSolveRequest,
        job_id: str,
        config: dict[str, Any],
        mode: str,
        *,
        required_columns_by_node: Mapping[str, frozenset[str]],
        factor_level_order: dict[str, list[str]],
        setup_job_key: tuple[str, str, str],
        execution_token: ExecutionCancellationToken,
    ) -> None:
        """Execute solve setup, then hand the prepared grid to the solver worker.

        In process mode the pipeline is materialised by a hard-capped worker
        into a setup-owned parquet and the grid is built from that file here;
        the explicit thread compatibility mode materialises on this thread.
        """
        execution_context: ExecutionContext | None = None
        launch_started = False
        ratebook_factors_handle: Any = None
        quote_analysis_handle: dict[str, Any] | None = None
        solver_input_path: str | None = None
        ratebook_factors_dir: Path | None = None
        quote_analysis_dir: Path | None = None
        analysis_plan = resolve_analysis_plan(body.graph, body.node_id, config)
        job = self._store.require_job(job_id)
        raw_start_time = job.get("start_time")
        start_time = (
            float(raw_start_time)
            if isinstance(raw_start_time, int | float) and not isinstance(raw_start_time, bool)
            else time.monotonic()
        )
        # The seed plan entered on this stack stays held until setup has read
        # every frame it needs, and releases on any exit.
        with contextlib.ExitStack() as resources:
            try:
                execution_context = admit_growth_grant(
                    operation="optimiser_solve",
                    profile=ExecutionProfile.OPTIMISER_SOLVE,
                    job_id=job_id,
                    cancellation_token=execution_token,
                    wait_out_holders=ESTIMATE_HOLDERS,
                    wait_seconds=ESTIMATE_WAIT_SECONDS,
                )
                bind_running_execution_metrics_publisher(
                    self._store,
                    job_id,
                    execution_context,
                )
                self._store.atomic_update(
                    job_id,
                    {"message": "Preparing optimiser input", "progress": 0.02},
                    expected_status="running",
                )
                self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                if resolve_interactive_execution_mode() == "process":
                    solver_input_path = _optimiser_artifacts._new_solver_input_path()
                    if analysis_plan is not None:
                        quote_analysis_dir = _optimiser_artifacts._new_quote_analysis_directory()
                    if mode == "ratebook":
                        ratebook_factors_dir = (
                            _optimiser_artifacts._new_ratebook_factors_directory()
                        )
                    solve_input = self._materialise_solve_input_in_worker(
                        body,
                        job_id,
                        resources,
                        config=config,
                        mode=mode,
                        required_columns_by_node=required_columns_by_node,
                        execution_context=execution_context,
                        output_path=solver_input_path,
                        ratebook_factors_dir=ratebook_factors_dir,
                        quote_analysis_dir=quote_analysis_dir,
                    )
                    ratebook_factors_handle = solve_input.ratebook_factors_handle
                    quote_analysis_handle = solve_input.quote_analysis_handle
                    self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                    # Setup's grant and its seed plan end with its worker: the
                    # input is written, and the session admits afresh, sized from
                    # what setup has given back.
                    execution_context.release_admission()
                    resources.close()
                    launch_started = True
                    self._solve_in_session(
                        body,
                        job_id,
                        config,
                        mode,
                        solve_input=solve_input,
                        factor_level_order=factor_level_order,
                        setup_job_key=setup_job_key,
                        execution_token=execution_token,
                        execution_context=execution_context,
                        start_time=start_time,
                    )
                    return
                else:
                    constraint_cols, scored_lf, ratebook_factors_handle, analysis = (
                        self._prepare_solver_frame(
                            body,
                            job_id,
                            resources,
                            config=config,
                            mode=mode,
                            required_columns_by_node=required_columns_by_node,
                            execution_context=execution_context,
                        )
                    )
                    self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                    setup_grid = self._build_grid(
                        scored_lf,
                        constraint_cols,
                        config,
                        body.node_id,
                        job_id,
                        execution_context=execution_context,
                        analysis=analysis,
                    )
                    quote_grid = setup_grid.grid
                    quote_analysis_handle = setup_grid.quote_analysis_handle
                self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                self._record_execution_metrics(job_id, execution_context)
                self._launch_background(
                    SolveContext(
                        job_id=job_id,
                        node_id=body.node_id,
                        mode=mode,
                        execution_context=execution_context,
                        setup_singleflight_key=setup_job_key,
                        registration_already_active=True,
                    ),
                    config=config,
                    quote_grid=quote_grid,
                    ratebook_factors_handle=ratebook_factors_handle,
                    quote_analysis_handle=quote_analysis_handle,
                    factor_level_order=factor_level_order,
                )
                launch_started = True
            except Exception as exc:
                self._record_solve_setup_failure(
                    job_id,
                    exc,
                    node_id=body.node_id,
                    execution_context=execution_context,
                    start_time=start_time,
                )
            finally:
                if solver_input_path is not None:
                    _optimiser_artifacts._remove_solver_input(solver_input_path)
                if not launch_started:
                    if execution_context is not None:
                        execution_context.release_admission()
                    self._release_job_ownership(job_id, setup_singleflight_key=setup_job_key)
                    if quote_analysis_handle is not None:
                        # Setup still owns the table: no solve will adopt it.
                        _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                            quote_analysis_handle,
                            job_id=job_id,
                            event="setup_orphan_quote_analysis_cleanup_failed",
                        )
                    elif quote_analysis_dir is not None:
                        # A worker that failed or was stopped handed back no table.
                        _optimiser_artifacts._remove_quote_analysis_directory(quote_analysis_dir)
                    if (
                        mode == "ratebook"
                        and isinstance(ratebook_factors_handle, dict)
                        and ratebook_factors_handle.get("kind")
                        == _optimiser_artifacts._RATEBOOK_FACTORS_HANDLE_KIND
                    ):
                        _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                            ratebook_factors_handle,
                            job_id=job_id,
                            event="setup_orphan_ratebook_factors_cleanup_failed",
                        )
                    elif ratebook_factors_dir is not None:
                        # A worker that failed or was stopped handed back no handle.
                        _optimiser_artifacts._remove_ratebook_factors_directory(
                            ratebook_factors_dir
                        )

    def _solve_in_session(
        self,
        body: OptimiserSolveRequest,
        job_id: str,
        config: dict[str, Any],
        mode: str,
        *,
        solve_input: SolveInput,
        factor_level_order: dict[str, list[str]],
        setup_job_key: tuple[str, str, str],
        execution_token: ExecutionCancellationToken,
        execution_context: ExecutionContext,
        start_time: float,
    ) -> None:
        """Build the grid, solve and publish in the job's solver session (process mode).

        The session is on the job before it is spawned, so cancelling finds it;
        the job's cancellation reason is its stop signal, so a cancelled or
        timed-out solve ends the process mid-call. A published solve keeps its
        session; any other outcome terminates it and removes what setup handed
        over, and only then releases the graph/node ownership.
        """
        session = SolverSession(job_id=job_id, store=self._store)
        adopted = False
        outcome_handles: list[dict[str, Any]] = []
        # The session writes the as-solved apply artifact here; removed unless adopted.
        apply_artifact_dir: Path | None = None

        def stop_reason() -> Any:
            return self._jobs.cancellation_reason(job_id)

        def on_progress(update: StepProgress) -> None:
            fraction = update.done / update.total if update.total else 0.0
            self._store.atomic_update(
                job_id,
                {
                    "message": update.label,
                    "progress": 0.05 + 0.9 * fraction,
                    "elapsed_seconds": time.monotonic() - start_time,
                },
                expected_status="running",
            )

        def publish(outcome: SessionSolveOutcome) -> bool:
            if outcome.grid_forecast_bytes is not None:
                self._store.atomic_update(
                    job_id,
                    {"grid_forecast_bytes": outcome.grid_forecast_bytes},
                    expected_status="running",
                )
            if outcome.failure is not None:
                raise OptimiserWorkerFailureError(outcome.failure)
            fields = dict(outcome.completion_fields or {})
            handles = fields.get("artifact_handles") or {}
            outcome_handles.extend(dict(handle) for handle in handles.values())
            _optimiser_artifacts._validate_apply_result_artifact_handle(
                handles[_optimiser_artifacts._APPLY_RESULT_HANDLE_KEY]
            )
            fields[SESSION_KEY] = session
            fields[RUNTIME_MODE_KEY] = SESSION_RUNTIME
            published = self._lifecycle.publish_completion(
                job_id,
                publish=lambda: fields,
                message="Completed",
                elapsed_seconds=time.monotonic() - start_time,
            )
            return published is not None

        try:
            apply_artifact_dir = _optimiser_artifacts._new_apply_artifact_directory()
            if (
                self._store.atomic_update(
                    job_id,
                    {SESSION_KEY: session, "message": "Starting the solver", "progress": 0.04},
                    expected_status="running",
                )
                is None
            ):
                return
            session.start(stop_reason=stop_reason)
            job = self._store.require_job(job_id)
            adopted = session.run_command(
                build_and_solve,
                SessionSolveRequest(
                    session_id=job_id,
                    project_root=str(_get_project_root()),
                    node_id=body.node_id,
                    mode=mode,
                    config=dict(config),
                    node_label=str(job.get("node_label", body.node_id)),
                    input_provenance=dict(job["input_provenance"]),
                    setup_chunking=dict(job.get("setup_chunking") or {}),
                    input_path=solve_input.path,
                    constraint_cols=list(solve_input.constraint_cols),
                    ratebook_factors_handle=solve_input.ratebook_factors_handle,
                    quote_analysis_handle=solve_input.quote_analysis_handle,
                    factor_level_order=factor_level_order,
                    apply_artifact_dir=str(apply_artifact_dir),
                ),
                operation="optimiser_solve",
                stage="building the quote grid",
                publish=publish,
                stop_reason=stop_reason,
                on_progress=on_progress,
                cancellation_token=execution_token,
            )
        except InteractiveWorkerStoppedError:
            # Cancel and timeout transition the job themselves.
            logger.info("solve_session_stopped", job_id=job_id)
        except SessionCommandError as exc:
            self._lifecycle.transition(
                job_id,
                to=exc.terminal_reason,
                message=exc.message,
                fields={
                    "http_status_code": exc.http_status_code,
                    "error_detail": exc.detail,
                    **(
                        {"error_code": "memory_limit"}
                        if exc.terminal_reason == "memory_limited"
                        else {}
                    ),
                },
                elapsed_seconds=time.monotonic() - start_time,
            )
        except InteractiveWorkerError as exc:
            self._record_solve_setup_failure(
                job_id,
                HTTPException(
                    status_code=500,
                    detail=f"The optimiser's solver process could not run: {exc}",
                ),
                node_id=body.node_id,
                execution_context=execution_context,
                start_time=start_time,
            )
        except Exception as exc:
            self._record_solve_setup_failure(
                job_id,
                exc,
                node_id=body.node_id,
                execution_context=execution_context,
                start_time=start_time,
            )
        finally:
            if not adopted:
                session.terminate("error")
                self._store.clear_result_data(job_id, keys=(SESSION_KEY,))
                for handle in (
                    *outcome_handles,
                    solve_input.quote_analysis_handle,
                    solve_input.ratebook_factors_handle,
                ):
                    if handle is not None:
                        _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                            handle,
                            job_id=job_id,
                            event="solve_session_orphan_artifact_cleanup_failed",
                        )
                if apply_artifact_dir is not None:
                    _optimiser_artifacts._remove_apply_artifact_directory(apply_artifact_dir)
            # The session command's own admission and cap describe the solve, not setup's.
            command_context = session.last_context
            self._record_execution_metrics(
                job_id, command_context if command_context is not None else execution_context
            )
            if session.last_command is not None:
                self._store.update_job(job_id, solver_session_command=dict(session.last_command))
            self._release_job_ownership(job_id, setup_singleflight_key=setup_job_key)

    def _run_optimiser_input_stage(
        self,
        body: OptimiserSolveRequest | OptimiserFrontierAutoRangeRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        config: dict[str, Any],
        required_columns_by_node: Mapping[str, Iterable[str]],
        execution_context: ExecutionContext,
        seed_plan: SeedPlanHandoff | None,
        check_stopped: Callable[[], None],
    ) -> tuple[dict[str, Any], Any]:
        """The optimiser's pipeline stage, shared by solve setup and auto-range.

        Executes the pipeline under the setup seed plan with the solve's
        demand, then resolves the data-input frame. Both jobs run exactly this,
        so the shared cache decides what is seeded, rebuilt and captured the
        same way for either. Returns the lazy outputs and the data-input frame,
        valid while *resources* holds the plan.
        """
        lazy_outputs = self._execute_pipeline(
            body,
            job_id,
            resources,
            required_columns_by_node=required_columns_by_node,
            execution_context=execution_context,
            seed_plan=seed_plan,
        )
        check_stopped()
        source_lf = self._resolve_data_input_frame(
            lazy_outputs,
            body.graph,
            config,
            body.node_id,
            job_id,
            execution_context=execution_context,
        )
        return lazy_outputs, source_lf

    def _prepare_solver_frame(
        self,
        body: OptimiserSolveRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        config: dict[str, Any],
        mode: str,
        required_columns_by_node: Mapping[str, frozenset[str]],
        execution_context: ExecutionContext,
        seed_plan: SeedPlanHandoff | None = None,
        ratebook_factors_dir: str | None = None,
    ) -> tuple[list[str], Any, Any, _AnalysisSource | None]:
        """Execute, resolve, validate and project the solve's input; persist ratebook factors.

        Returns the constraint columns, the projected solver frame (carrying
        the analysis columns on the data-input path), the ratebook factors
        handle (``None`` in online mode) and the analysis columns to reduce,
        with the projected side-input frame they come from (``None`` without
        analysis columns). The frames stay valid while *resources* holds the
        run's seed plan.
        """
        analysis_plan = resolve_analysis_plan(body.graph, body.node_id, config)
        lazy_outputs, source_lf = self._run_optimiser_input_stage(
            body,
            job_id,
            resources,
            config=config,
            required_columns_by_node=required_columns_by_node,
            execution_context=execution_context,
            seed_plan=seed_plan,
            check_stopped=lambda: self._raise_if_solve_stopped(
                job_id, execution_context=execution_context
            ),
        )
        constraint_cols, scored_lf = self._validate_and_project(
            source_lf,
            config,
            job_id,
            analysis_columns=(
                analysis_plan.columns
                if analysis_plan is not None and analysis_plan.path == "data_input"
                else ()
            ),
            execution_context=execution_context,
        )
        analysis: _AnalysisSource | None = None
        if analysis_plan is not None and analysis_plan.path == "side_input":
            with self._recorded_setup_failures(job_id, execution_context):
                analysis = _AnalysisSource(
                    analysis_plan.columns,
                    resolve_analysis_frame(lazy_outputs, config, analysis_plan),
                )
        elif analysis_plan is not None:
            analysis = _AnalysisSource(analysis_plan.columns, None)
        self._raise_if_solve_stopped(job_id, execution_context=execution_context)
        ratebook_factors_handle = self._extract_factors(
            lazy_outputs,
            body.graph,
            body.node_id,
            config,
            mode,
            execution_context=execution_context,
            artifact_dir=ratebook_factors_dir,
        )
        del lazy_outputs
        gc.collect()
        return constraint_cols, scored_lf, ratebook_factors_handle, analysis

    def _materialise_solve_input(
        self,
        body: OptimiserSolveRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        config: dict[str, Any],
        mode: str,
        required_columns_by_node: Mapping[str, frozenset[str]],
        execution_context: ExecutionContext,
        output_path: str,
        ratebook_factors_dir: str | None,
        quote_analysis_dir: str | None = None,
        seed_plan: SeedPlanHandoff | None = None,
    ) -> SolveInput:
        """Write the projected, validated solver input to *output_path* (a worker's step).

        The input is always written, never borrowed: a snapshot this run
        captured is released when the worker's plan closes, before the parent
        reads the file. Ratebook factors go to the parent's *ratebook_factors_dir*,
        which the parent removes if the job never adopts them. With analysis
        columns, the quote-analysis table is reduced here, under the worker's
        cap, into the parent's *quote_analysis_dir* (see
        ``_write_quote_analysis``).
        """
        constraint_cols, scored_lf, ratebook_factors_handle, analysis = self._prepare_solver_frame(
            body,
            job_id,
            resources,
            config=config,
            mode=mode,
            required_columns_by_node=required_columns_by_node,
            execution_context=execution_context,
            seed_plan=seed_plan,
            ratebook_factors_dir=ratebook_factors_dir,
        )
        input_path = self._write_solver_input(
            scored_lf,
            output_path,
            body.node_id,
            job_id,
            execution_context=execution_context,
            allow_borrow=False,
        )
        quote_analysis_handle = None
        if analysis is not None:
            if quote_analysis_dir is None:
                raise RuntimeError("Analysis columns need the parent's quote-analysis directory")
            quote_analysis_handle = self._write_quote_analysis(
                input_path,
                analysis,
                config,
                body.node_id,
                job_id,
                directory=Path(quote_analysis_dir),
                execution_context=execution_context,
            )
        return SolveInput(
            path=input_path,
            constraint_cols=constraint_cols,
            ratebook_factors_handle=ratebook_factors_handle,
            quote_analysis_handle=quote_analysis_handle,
        )

    def _materialise_solve_input_in_worker(
        self,
        body: OptimiserSolveRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        config: dict[str, Any],
        mode: str,
        required_columns_by_node: Mapping[str, frozenset[str]],
        execution_context: ExecutionContext,
        output_path: str,
        ratebook_factors_dir: Path | None,
        quote_analysis_dir: Path | None,
    ) -> SolveInput:
        """Supervise one hard-capped worker that materialises the solve's input.

        The seed plan is opened here and adopted by the worker. The worker
        writes only into locations this setup created: *output_path*, the
        *ratebook_factors_dir* and a scratch directory removed when it exits.
        """
        handoff = self._open_setup_seed_plan(
            body,
            job_id,
            resources,
            required_columns_by_node=required_columns_by_node,
            execution_context=execution_context,
        )
        self._raise_if_solve_stopped(job_id, execution_context=execution_context)
        with worker_scratch_directory() as scratch_dir:
            outcome = self._run_optimiser_worker(
                materialise_solve_input_worker,
                SolveInputWorkerRequest(
                    body=body,
                    config=dict(config),
                    mode=mode,
                    required_columns_by_node={
                        node_id: frozenset(columns)
                        for node_id, columns in required_columns_by_node.items()
                    },
                    project_root=str(_get_project_root()),
                    seed_plan=handoff,
                    output_path=output_path,
                    scratch_dir=scratch_dir,
                    ratebook_factors_dir=(
                        str(ratebook_factors_dir) if ratebook_factors_dir is not None else None
                    ),
                    quote_analysis_dir=(
                        str(quote_analysis_dir) if quote_analysis_dir is not None else None
                    ),
                ),
                job_id=job_id,
                node_id=body.node_id,
                execution_context=execution_context,
                timeout_seconds=None,
                process_name="haute-optimiser-setup",
            )
        if not isinstance(outcome, SolveInputWorkerOutcome):
            raise RuntimeError(f"Optimiser setup worker returned {type(outcome).__name__}")
        if outcome.execution_metrics is not None:
            execution_context.adopt_worker_evidence(outcome.execution_metrics)
        if outcome.failure is not None:
            raise OptimiserWorkerFailureError(outcome.failure)
        solve_input = outcome.solve_input
        if solve_input is None:
            raise RuntimeError("Optimiser setup worker returned neither an input nor a failure")
        if solve_input.path != output_path:
            raise RuntimeError("Optimiser setup worker wrote its input outside the setup's file")
        analysis_handle = solve_input.quote_analysis_handle
        if (analysis_handle is None) != (quote_analysis_dir is None):
            raise RuntimeError("Optimiser setup worker's analysis table disagrees with its setup")
        if analysis_handle is not None:
            assert quote_analysis_dir is not None
            _analysis_path, analysis_dir = (
                _optimiser_artifacts._validate_quote_analysis_artifact_handle(analysis_handle)
            )
            if analysis_dir != quote_analysis_dir.resolve():
                raise RuntimeError(
                    "Optimiser setup worker wrote its analysis table outside the setup's directory"
                )
        handle = solve_input.ratebook_factors_handle
        if handle is not None:
            _factors_path, factors_dir = (
                _optimiser_artifacts._validate_ratebook_factors_artifact_handle(handle)
            )
            if ratebook_factors_dir is None or factors_dir != ratebook_factors_dir.resolve():
                raise RuntimeError(
                    "Optimiser setup worker persisted factors outside the setup's directory"
                )
        return solve_input

    def _run_optimiser_worker(
        self,
        function: Callable[..., Any],
        request: Any,
        *,
        job_id: str,
        node_id: str,
        execution_context: ExecutionContext,
        timeout_seconds: float | None,
        process_name: str,
        on_timeout: Callable[[], BaseException] | None = None,
    ) -> Any:
        """Run one optimiser materialisation worker under the job's admitted headroom.

        The headroom is the worker's execution budget and its native cap, and
        the job's cancellation reason is its stop signal, so cancellation,
        supersession and a polled timeout terminate the worker. Worker-level
        failures become the exceptions the job's failure mapping already
        classifies: a stop is the job's stop, the worker's own timeout is
        whatever *on_timeout* publishes, a memory-shaped failure is a 507
        ``memory_limit`` and anything else is a 500.
        """
        budget = isolated_execution_budget(execution_context)
        worker_config = worker_config_for_memory_policy(
            memory_limit_bytes=budget.memory_limit_bytes,
            timeout_seconds=timeout_seconds,
            stop_reason=lambda: self._jobs.cancellation_reason(job_id),
            process_name=process_name,
            environment={"POLARS_MAX_THREADS": str(resolve_optimiser_polars_threads())},
        )
        try:
            return run_isolated_worker(function, request, budget, config=worker_config)
        except IsolatedWorkerStoppedError as exc:
            raise BackgroundJobStoppedError(job_id, exc.terminal_reason) from None
        except IsolatedWorkerTimeoutError:
            if on_timeout is None:
                raise RuntimeError("An optimiser worker without a timeout timed out") from None
            raise on_timeout() from None
        except IsolatedWorkerError as exc:
            if isolated_worker_failure_is_memory(exc):
                raise HTTPException(
                    status_code=507,
                    detail=isolated_worker_memory_detail(
                        exc,
                        operation=budget.operation,
                        memory_limit_bytes=budget.memory_limit_bytes,
                    ),
                ) from None
            logger.error(
                "optimiser_worker_failed",
                job_id=job_id,
                node_id=node_id,
                operation=budget.operation,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Optimiser worker failed. Check the server logs for details.",
            ) from None

    def _record_solve_setup_failure(
        self,
        job_id: str,
        exc: Exception,
        *,
        node_id: str,
        execution_context: ExecutionContext | None,
        start_time: float,
    ) -> None:
        """Publish one solve-setup failure as the job's terminal state."""
        elapsed_seconds = time.monotonic() - start_time
        if isinstance(exc, OptimiserWorkerFailureError):
            failure = exc.failure
            fields = dict(failure.fields)
            worker_metrics = fields.get("execution_metrics")
            if execution_context is not None:
                fields["execution_metrics"] = (
                    execution_context.metrics_with_worker_evidence(worker_metrics)
                    if isinstance(worker_metrics, Mapping)
                    else execution_context.metrics_payload(
                        status=failure.terminal_reason,
                        terminal_reason=failure.terminal_reason,
                    )
                )
            self._lifecycle.transition(
                job_id,
                to=failure.terminal_reason,
                message=failure.message,
                fields=fields,
                elapsed_seconds=elapsed_seconds,
            )
        elif isinstance(exc, BackgroundJobStoppedError):
            terminal_reason = _coerce_stopped_terminal_reason(exc.terminal_reason)
            self._lifecycle.transition(
                job_id,
                to=terminal_reason,
                message=exc.terminal_reason,
                fields=(
                    {
                        "execution_metrics": execution_context.metrics_payload(
                            status=terminal_reason,
                            terminal_reason=terminal_reason,
                        )
                    }
                    if execution_context is not None
                    else None
                ),
                elapsed_seconds=elapsed_seconds,
            )
        elif isinstance(exc, HTTPException):
            http_terminal_reason: TerminalReason = (
                "memory_limited"
                if _is_memory_limit_http_exception(exc)
                else "contract_error"
                if exc.status_code in (400, 422)
                else "error"
            )
            error_update: dict[str, Any] = {
                "message": str(exc.detail),
                "http_status_code": exc.status_code,
                "error_detail": exc.detail,
            }
            if execution_context is not None:
                error_update["execution_metrics"] = execution_context.metrics_payload(
                    status=http_terminal_reason,
                    terminal_reason=http_terminal_reason,
                )
            self._lifecycle.transition(
                job_id,
                to=http_terminal_reason,
                fields=error_update,
                elapsed_seconds=elapsed_seconds,
            )
        elif isinstance(exc, (ExecutionAdmissionError, ExecutionMemoryLimitExceededError)):
            http_exc = memory_limit_http_exception(exc, operation_noun="Optimisation")
            if execution_context is not None:
                memory_error_update = _memory_limit_job_update(
                    detail=http_exc.detail,
                    elapsed_seconds=elapsed_seconds,
                    execution_context=execution_context,
                )
            else:
                payload = _normalise_memory_limit_payload(http_exc.detail)
                memory_error_update = {
                    "message": str(payload),
                    "elapsed_seconds": elapsed_seconds,
                    "error_code": payload.get("error_code", "memory_limit"),
                    "http_status_code": http_exc.status_code,
                    "error_detail": payload,
                }
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                fields=memory_error_update,
                elapsed_seconds=elapsed_seconds,
            )
        elif isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
            contract_reason = contract_error_terminal_reason(exc)
            contract_fields = contract_error_job_fields(exc)
            contract_fields["elapsed_seconds"] = elapsed_seconds
            if execution_context is not None:
                contract_fields["execution_metrics"] = execution_context.metrics_payload(
                    status=contract_reason,
                    terminal_reason=contract_reason,
                )
            self._lifecycle.transition(
                job_id,
                to=contract_reason,
                message=str(exc),
                fields=contract_fields,
                elapsed_seconds=elapsed_seconds,
            )
        elif isinstance(exc, BoundedMemoryUnsupportedError):
            detail = f"Optimiser setup cannot run in bounded streaming mode: {exc}"
            logger.warning(
                "optimiser_setup_bounded_streaming_unsupported",
                error=str(exc),
                node_id=node_id,
                job_id=job_id,
            )
            bounded_fields: dict[str, Any] = {
                "http_status_code": 422,
                "error_detail": detail,
                "elapsed_seconds": elapsed_seconds,
            }
            if execution_context is not None:
                bounded_fields["execution_metrics"] = execution_context.metrics_payload(
                    status="contract_error",
                    terminal_reason="contract_error",
                )
            self._lifecycle.transition(
                job_id,
                to="contract_error",
                message=detail,
                fields=bounded_fields,
                elapsed_seconds=elapsed_seconds,
            )
        else:
            detail = f"Optimiser setup failed: {exc}"
            logger.error(
                "optimiser_setup_failed",
                error=str(exc),
                node_id=node_id,
                job_id=job_id,
                exc_info=True,
            )
            error_fields: dict[str, Any] = {"elapsed_seconds": elapsed_seconds}
            if execution_context is not None:
                error_fields["execution_metrics"] = execution_context.metrics_payload(
                    status="error",
                    terminal_reason="error",
                )
            self._lifecycle.transition(
                job_id,
                to="error",
                message=detail,
                fields=error_fields,
                elapsed_seconds=elapsed_seconds,
            )

    def start_frontier_auto_range(
        self,
        body: OptimiserFrontierAutoRangeRequest,
    ) -> OptimiserFrontierAutoRangeStartResponse:
        """Start auto-range in a background thread and return a pollable job."""
        body = cast(OptimiserFrontierAutoRangeRequest, _with_flattened_optimiser_graph(body))
        job_key = self._frontier_auto_range_job_key(body)
        setup_job_key = self._graph_node_setup_job_key(body.graph, body.node_id)
        node, prepared = self._prepare_frontier_auto_range(body)
        config = prepared["config"]
        with self._start_lock:
            active = self._active_frontier_auto_range_start(setup_job_key)
            if active is not None:
                return active
            initial_job: _FrontierAutoRangeRunningJob = {
                "status": "running",
                "job_type": _FRONTIER_AUTO_RANGE_JOB_TYPE,
                "progress": 0.0,
                "message": "Estimating frontier range",
                "config": dict(config),
                "node_label": node.data.label,
            }
            job_id = self._store.create_job(initial_job)
            execution_token = ExecutionCancellationToken()
            self._graph_node_setup_singleflight.acquire(
                setup_job_key,
                job_id=job_id,
                kind=_FRONTIER_AUTO_RANGE_JOB_TYPE,
            )
            _token, previous_job_id = self._jobs.register_latest(
                job_key,
                job_id,
                execution_token=execution_token,
            )
            if previous_job_id is not None:
                self._stop_frontier_auto_range_job(
                    previous_job_id,
                    status=_FRONTIER_AUTO_RANGE_SUPERSEDED_STATUS,
                    message="Superseded by a newer auto-range request.",
                )
        try:
            self._launch_frontier_auto_range_background(
                body,
                job_id,
                setup_singleflight_key=setup_job_key,
                execution_token=execution_token,
                **prepared,
            )
        except Exception:
            self._release_job_ownership(job_id, setup_singleflight_key=setup_job_key)
            raise
        return OptimiserFrontierAutoRangeStartResponse(status="started", job_id=job_id)

    def _active_frontier_auto_range_start(
        self,
        setup_job_key: tuple[str, str, str],
    ) -> OptimiserFrontierAutoRangeStartResponse | None:
        """Return the running auto-range job for this node, or raise the setup conflict.

        Must be called under ``_start_lock``. ``None`` means no setup job owns
        the node and a new job may be created.
        """
        active_setup = self._active_graph_node_setup(setup_job_key)
        if active_setup is None:
            return None
        if active_setup.kind == _FRONTIER_AUTO_RANGE_JOB_TYPE:
            active_job = self._store.require_job(active_setup.job_id)
            if active_job.get("status") == "running":
                return OptimiserFrontierAutoRangeStartResponse(
                    status="started",
                    job_id=active_setup.job_id,
                )
        raise self._graph_node_setup_conflict(active_setup)

    def frontier_auto_range_status(
        self,
        job_id: str,
    ) -> OptimiserFrontierAutoRangeStatusResponse:
        """Return status for a background auto-range job."""
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _FRONTIER_AUTO_RANGE_JOB_TYPE:
            raise HTTPException(status_code=404, detail=f"Auto-range job '{job_id}' not found")

        if job.get("status") == "running":
            start = job.get("start_time")
            timeout = job.get("timeout")
            if start and timeout is not None and (time.monotonic() - start) > timeout:
                self._time_out_frontier_auto_range(job_id, timeout)
                job = self._store.require_job(job_id)

        return self._frontier_auto_range_status_response(job)

    def cancel_frontier_auto_range(
        self,
        job_id: str,
    ) -> OptimiserFrontierAutoRangeStatusResponse:
        """Cancel a running background auto-range job."""
        job = self._stop_frontier_auto_range_job(
            job_id,
            status=_FRONTIER_AUTO_RANGE_CANCELLED_STATUS,
            message="Cancelled",
        )
        return self._frontier_auto_range_status_response(job)

    def cancel_solve(self, job_id: str) -> JobSnapshot:
        """Cancel a running optimiser solve job."""
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _SOLVE_JOB_TYPE:
            raise HTTPException(status_code=404, detail=f"Solve job '{job_id}' not found")
        if job.get("status") != "running":
            return job
        self._jobs.cancel(job_id, reason="cancelled")
        updated_job = self._lifecycle.transition(
            job_id,
            to="cancelled",
            message="Cancelled",
            elapsed_seconds=_job_elapsed_seconds(job),
        )
        return updated_job if updated_job is not None else self._store.require_job(job_id)

    def timeout_solve(
        self,
        job_id: str,
        *,
        timeout: int | float,
        start_time: float,
    ) -> JobSnapshot:
        """Mark a running optimiser solve as timed out and request cancellation."""
        self._jobs.cancel(job_id, reason="timed_out")
        updated_job = self._lifecycle.transition(
            job_id,
            to="timed_out",
            message=(
                f"Solve timed out after {timeout}s. Increase timeout or simplify the problem."
            ),
            elapsed_seconds=time.monotonic() - start_time,
        )
        return updated_job if updated_job is not None else self._store.require_job(job_id)

    def reject_completed_result(self, job_id: str, *, message: str) -> JobSnapshot:
        """Correct a completed solve whose result cannot satisfy the API contract."""
        corrected = self._lifecycle.transition(
            job_id,
            to="error",
            message=message,
            fields={"result": None, "frontier_data": None},
            expected_status="completed",
        )
        return corrected if corrected is not None else self._store.require_job(job_id)

    def _frontier_auto_range_status_response(
        self,
        job: Mapping[str, Any],
    ) -> OptimiserFrontierAutoRangeStatusResponse:
        stored_status = require_job_status(job)
        result = None
        if stored_status == "completed" and job.get("result") is not None:
            result = OptimiserFrontierAutoRangeResponse.model_validate(job["result"])
        elapsed_seconds = job.get("elapsed_seconds", 0.0)
        if stored_status == "running":
            elapsed_seconds = _job_elapsed_seconds(job, elapsed_seconds)
        return OptimiserFrontierAutoRangeStatusResponse(
            status=stored_status,
            progress=job.get("progress", 0.0),
            message=job.get("message", ""),
            elapsed_seconds=elapsed_seconds,
            result=result,
            terminal_reason=job.get("terminal_reason"),
            error_code=job.get("error_code"),
            http_status_code=job.get("http_status_code"),
            error_detail=job.get("error_detail"),
            execution_metrics=job.get("execution_metrics"),
        )

    @staticmethod
    def _frontier_auto_range_job_key(
        body: OptimiserFrontierAutoRangeRequest,
    ) -> tuple[str, str, str]:
        return (_FRONTIER_AUTO_RANGE_JOB_TYPE, body.node_id, graph_fingerprint(body.graph))

    @staticmethod
    def _graph_node_setup_job_key(
        graph: PipelineGraph,
        node_id: str,
    ) -> tuple[str, str, str]:
        return (_GRAPH_NODE_SETUP_COORDINATION_TYPE, node_id, graph_fingerprint(graph))

    def _active_graph_node_setup(
        self,
        key: tuple[str, str, str],
    ) -> SingleFlightHandle | None:
        """Return the active graph/node heavy job, clearing only deleted stale owners."""
        active = self._graph_node_setup_singleflight.active(key)
        if active is None:
            return None
        if self._store.get_job(active.job_id) is None:
            self._graph_node_setup_singleflight.release(key, job_id=active.job_id)
            return None
        return active

    @staticmethod
    def _graph_node_setup_conflict(active: SingleFlightHandle) -> HTTPException:
        return HTTPException(
            status_code=409,
            detail=(
                "Optimiser work is already running for this graph/node "
                f"(job_id={active.job_id}, job_type={active.kind}). "
                "Wait for it to finish or cancel it before starting another run."
            ),
        )

    def _release_job_ownership(
        self,
        job_id: str,
        *,
        setup_singleflight_key: tuple[str, str, str] | None = None,
    ) -> None:
        """Release cancellation and graph/node ownership after worker exit."""

        self._jobs.release(job_id)
        if setup_singleflight_key is not None:
            self._graph_node_setup_singleflight.release(
                setup_singleflight_key,
                job_id=job_id,
            )

    @contextlib.contextmanager
    def _job_ownership_scope(
        self,
        job_id: str,
        *,
        setup_singleflight_key: tuple[str, str, str] | None = None,
    ) -> Iterator[None]:
        """Hold cancellation and graph/node ownership until the worker exits."""

        try:
            yield
        finally:
            self._release_job_ownership(
                job_id,
                setup_singleflight_key=setup_singleflight_key,
            )

    def _stop_frontier_auto_range_job(
        self,
        job_id: str,
        *,
        status: str,
        message: str,
    ) -> JobSnapshot:
        if status not in _FRONTIER_AUTO_RANGE_TERMINAL_STATUSES:
            raise ValueError(f"Unsupported auto-range stop status: {status!r}")
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _FRONTIER_AUTO_RANGE_JOB_TYPE:
            raise HTTPException(status_code=404, detail=f"Auto-range job '{job_id}' not found")
        if job.get("status") in _FRONTIER_AUTO_RANGE_TERMINAL_STATUSES:
            return job

        terminal_reason = cast(TerminalReason, status)
        self._jobs.cancel(job_id, reason=terminal_reason)
        updated_job = self._lifecycle.transition(
            job_id,
            to=terminal_reason,
            message=message,
            elapsed_seconds=_job_elapsed_seconds(job),
        )
        return updated_job if updated_job is not None else self._store.require_job(job_id)

    def _raise_if_frontier_auto_range_stopped(self, job_id: str) -> None:
        job = self._store.require_job(job_id)
        status = str(job.get("status", "running"))
        if status != "running":
            raise BackgroundJobStoppedError(
                job_id,
                str(job.get("terminal_reason", status)),
            )
        reason = self._jobs.cancellation_reason(job_id)
        if reason is not None:
            raise BackgroundJobStoppedError(job_id, reason)

    def _raise_if_solve_stopped(
        self,
        job_id: str,
        *,
        execution_context: ExecutionContext,
    ) -> None:
        job = self._store.require_job(job_id)
        status = str(job.get("status", "running"))
        if status != "running":
            raise BackgroundJobStoppedError(
                job_id,
                str(job.get("terminal_reason", status)),
            )
        token_reason = self._jobs.cancellation_reason(job_id)
        if token_reason is not None:
            raise BackgroundJobStoppedError(job_id, token_reason)
        try:
            execution_context.cancellation_token.throw_if_cancelled(
                execution_context.operation,
                job_id=execution_context.job_id,
            )
        except ExecutionCancelledError as exc:
            job = self._store.require_job(job_id)
            status = str(job.get("status", "running"))
            stopped_reason = str(
                job.get("terminal_reason", status if status != "running" else "cancelled")
            )
            raise BackgroundJobStoppedError(job_id, stopped_reason) from exc

    def _record_execution_metrics(
        self,
        job_id: str,
        execution_context: ExecutionContext,
        *,
        status: str | None = None,
        terminal_reason: str | None = None,
    ) -> None:
        try:
            job = self._store.require_job(job_id)
        except HTTPException:
            return
        payload_status = status or str(job.get("status", "running"))
        stored_reason = job.get("terminal_reason")
        payload_terminal_reason = terminal_reason
        if payload_terminal_reason is None and isinstance(stored_reason, str):
            payload_terminal_reason = stored_reason
        self._store.atomic_update(
            job_id,
            {
                "execution_metrics": execution_context.metrics_payload(
                    status=payload_status,
                    terminal_reason=payload_terminal_reason,
                )
            },
        )

    def _job_elapsed(self, job_id: str, fallback: float = 0.0) -> float:
        """Read elapsed time through the store API without exposing its backing mapping."""
        return _job_elapsed_seconds(self._store.get_job(job_id) or {}, fallback)

    def _record_setup_failure(
        self,
        job_id: str,
        *,
        to: TerminalReason,
        message: str,
        fields: Mapping[str, Any] | None = None,
        execution_context: ExecutionContext | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        update = dict(fields or {})
        if execution_context is not None:
            update.setdefault(
                "execution_metrics",
                execution_context.metrics_payload(
                    status=to,
                    terminal_reason=to,
                ),
            )
        self._lifecycle.transition(
            job_id,
            to=to,
            message=message,
            fields=update,
            elapsed_seconds=elapsed_seconds,
        )

    def _record_http_setup_failure(
        self,
        job_id: str,
        *,
        status_code: int,
        detail: object,
        to: TerminalReason = "contract_error",
        execution_context: ExecutionContext | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        self._record_setup_failure(
            job_id,
            to=to,
            message=str(detail),
            fields={"http_status_code": status_code, "error_detail": detail},
            execution_context=execution_context,
            elapsed_seconds=elapsed_seconds,
        )

    def _prepare_frontier_auto_range(
        self,
        body: OptimiserFrontierAutoRangeRequest,
    ) -> tuple[GraphNode, dict[str, Any]]:
        """Validate an auto-range request and resolve the solve's demand for it.

        Auto-range runs the solve setup's pipeline stage, so it asks the
        pipeline for exactly what the solve asks for: the cache decision is the
        solve's, and a capture it publishes serves the next solve unchanged.
        Returns the optimiser node and the job's keyword arguments.
        """
        node = _find_optimiser_node(body.graph, body.node_id)
        config = dict(node.data.config)
        mode = self._validate_config(config)
        try:
            timeout = _auto_range_timeout_from_config(config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return node, {
            "config": config,
            "mode": mode,
            "timeout": timeout,
            "required_columns_by_node": _optimiser_solve_required_columns_by_node(
                body.graph,
                body.node_id,
                config,
            ),
        }

    def _run_frontier_auto_range_job(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        config: dict[str, Any],
        mode: str,
        timeout: int | None,
        required_columns_by_node: Mapping[str, Iterable[str]],
        execution_context: ExecutionContext | None = None,
        execution_token: ExecutionCancellationToken | None = None,
        seed_plan: SeedPlanHandoff | None = None,
        isolate: bool = True,
    ) -> OptimiserFrontierAutoRangeResponse:
        """Run one auto-range job: the solve setup's pipeline stage, then the range reducer.

        The job owns admission, cancellation, completion and failure
        classification. Without a caller-owned *execution_context* it admits
        exactly as solve setup does (a growth grant under the solve profile,
        after waiting out a running estimate) and returns the grant on every
        exit. In process mode an isolated worker computes the ranges
        (*isolate* is false only inside that worker, which passes the
        *seed_plan* handoff).
        """
        job_kwargs: dict[str, Any] = {
            "config": config,
            "mode": mode,
            "timeout": timeout,
            "required_columns_by_node": required_columns_by_node,
            "seed_plan": seed_plan,
            "isolate": isolate,
        }
        if execution_context is not None:
            return self._run_admitted_frontier_auto_range_job(
                body,
                job_id,
                execution_context=execution_context,
                **job_kwargs,
            )
        try:
            owned_context = admit_growth_grant(
                operation="frontier_auto_range",
                profile=ExecutionProfile.OPTIMISER_SOLVE,
                job_id=job_id,
                cancellation_token=execution_token,
                wait_out_holders=ESTIMATE_HOLDERS,
                wait_seconds=ESTIMATE_WAIT_SECONDS,
            )
        except ExecutionCancelledError as exc:
            reason = self._jobs.cancellation_reason(job_id) or "cancelled"
            raise BackgroundJobStoppedError(job_id, reason) from exc
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            http_exc = memory_limit_http_exception(exc, operation_noun="Auto-range")
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                message=str(http_exc.detail),
            )
            raise http_exc from None
        try:
            bind_running_execution_metrics_publisher(self._store, job_id, owned_context)
            return self._run_admitted_frontier_auto_range_job(
                body,
                job_id,
                execution_context=owned_context,
                **job_kwargs,
            )
        finally:
            try:
                stored_reason = self._store.require_job(job_id).get("terminal_reason")
            except HTTPException:
                stored_reason = None
            self._record_execution_metrics(
                job_id,
                owned_context,
                terminal_reason=(
                    stored_reason if isinstance(stored_reason, str) and stored_reason else None
                ),
            )
            owned_context.release_admission(preserve_primary_error=True)

    def _run_admitted_frontier_auto_range_job(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        execution_context: ExecutionContext,
        config: dict[str, Any],
        mode: str,
        timeout: int | None,
        required_columns_by_node: Mapping[str, Iterable[str]],
        seed_plan: SeedPlanHandoff | None,
        isolate: bool,
    ) -> OptimiserFrontierAutoRangeResponse:
        try:
            execution_context.checkpoint(label="frontier_auto_range_start")
        except ExecutionCancelledError as exc:
            status = str(self._store.require_job(job_id).get("status", "running"))
            raise BackgroundJobStoppedError(job_id, status) from exc
        self._raise_if_frontier_auto_range_stopped(job_id)
        worker_metrics: Mapping[str, Any] | None = None

        # The seed plan entered on this stack is released on every exit.
        try:
            with contextlib.ExitStack() as resources:
                if isolate and resolve_interactive_execution_mode() == "process":
                    ranges, worker_metrics = self._frontier_ranges_in_worker(
                        body,
                        job_id,
                        config=config,
                        mode=mode,
                        required_columns_by_node=required_columns_by_node,
                        timeout=timeout,
                        execution_context=execution_context,
                    )
                else:
                    ranges = self._frontier_ranges(
                        body,
                        job_id,
                        resources,
                        config=config,
                        required_columns_by_node=required_columns_by_node,
                        execution_context=execution_context,
                        seed_plan=seed_plan,
                    )
                self._raise_if_frontier_auto_range_stopped(job_id)
                response = OptimiserFrontierAutoRangeResponse(
                    status="ok",
                    ranges={
                        name: OptimiserFrontierRange(min=value["min"], max=value["max"])
                        for name, value in ranges.items()
                    },
                )
                self._lifecycle.transition(
                    job_id,
                    to="completed",
                    message="Completed",
                    fields={
                        "progress": 1.0,
                        "elapsed_seconds": self._job_elapsed(job_id),
                        "result": response.model_dump(),
                        "execution_metrics": (
                            execution_context.metrics_payload(status="completed")
                            if worker_metrics is None
                            else execution_context.metrics_with_worker_evidence(worker_metrics)
                        ),
                    },
                )
                return response
        except BackgroundJobStoppedError:
            raise
        except OptimiserWorkerFailureError as exc:
            failure = exc.failure
            fields = dict(failure.fields)
            failed_metrics = fields.get("execution_metrics")
            fields["execution_metrics"] = (
                execution_context.metrics_with_worker_evidence(failed_metrics)
                if isinstance(failed_metrics, Mapping)
                else execution_context.metrics_payload(
                    status=failure.terminal_reason,
                    terminal_reason=failure.terminal_reason,
                )
            )
            fields["elapsed_seconds"] = self._job_elapsed(job_id)
            self._lifecycle.transition(
                job_id,
                to=failure.terminal_reason,
                message=failure.message,
                fields=fields,
            )
            raise HTTPException(
                status_code=failure.http_status_code,
                detail=failure.http_detail,
            ) from None
        except ExecutionCancelledError as exc:
            reason = self._jobs.cancellation_reason(job_id) or "cancelled"
            raise BackgroundJobStoppedError(job_id, reason) from exc
        except ExecutionMemoryLimitExceededError as exc:
            http_exc = memory_limit_http_exception(exc, operation_noun="Auto-range")
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                fields=_memory_limit_job_update(
                    detail=http_exc.detail,
                    elapsed_seconds=self._job_elapsed(job_id),
                    execution_context=execution_context,
                ),
            )
            raise http_exc from None
        except HTTPException as exc:
            if _is_memory_limit_http_exception(exc):
                self._lifecycle.transition(
                    job_id,
                    to="memory_limited",
                    fields=_memory_limit_job_update(
                        detail=exc.detail,
                        elapsed_seconds=self._job_elapsed(job_id),
                        execution_context=execution_context,
                    ),
                )
                raise
            terminal_reason: TerminalReason = (
                "contract_error" if exc.status_code in (400, 422) else "error"
            )
            self._lifecycle.transition(
                job_id,
                to=terminal_reason,
                fields=_http_exception_job_update(
                    exc=exc,
                    elapsed_seconds=self._job_elapsed(job_id),
                    execution_context=execution_context,
                    terminal_reason=terminal_reason,
                ),
            )
            raise
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            elapsed_seconds = self._job_elapsed(job_id)
            contract_reason = contract_error_terminal_reason(exc)
            fields = contract_error_job_fields(exc)
            fields["elapsed_seconds"] = elapsed_seconds
            fields["execution_metrics"] = execution_context.metrics_payload(
                status=contract_reason,
                terminal_reason=contract_reason,
            )
            self._lifecycle.transition(job_id, to=contract_reason, fields=fields)
            raise contract_error_http_exception(exc) from None
        except BoundedMemoryUnsupportedError as exc:
            detail = f"Frontier auto range cannot run in bounded streaming mode: {exc}"
            logger.warning(
                "frontier_auto_range_bounded_streaming_unsupported",
                error=str(exc),
                node_id=body.node_id,
                job_id=job_id,
            )
            self._lifecycle.transition(
                job_id,
                to="contract_error",
                fields=_http_error_job_update(
                    status_code=422,
                    detail=detail,
                    elapsed_seconds=self._job_elapsed(job_id),
                    execution_context=execution_context,
                    terminal_reason="contract_error",
                ),
            )
            raise HTTPException(status_code=422, detail=detail) from exc
        except ValueError as exc:
            detail = str(exc)
            self._lifecycle.transition(
                job_id,
                to="contract_error",
                fields=_http_error_job_update(
                    status_code=400,
                    detail=detail,
                    elapsed_seconds=self._job_elapsed(job_id),
                    execution_context=execution_context,
                    terminal_reason="contract_error",
                ),
            )
            raise HTTPException(status_code=400, detail=detail) from exc
        except Exception as exc:
            logger.error(
                "frontier_auto_range_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._lifecycle.transition(
                job_id,
                to="error",
                fields={
                    "message": f"Frontier auto range failed: {exc}",
                    "elapsed_seconds": self._job_elapsed(job_id),
                    "execution_metrics": execution_context.metrics_payload(status="error"),
                },
            )
            raise HTTPException(
                status_code=500,
                detail="Frontier auto range failed. Check the server logs for details.",
            ) from exc

    def _frontier_ranges(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        config: dict[str, Any],
        required_columns_by_node: Mapping[str, Iterable[str]],
        execution_context: ExecutionContext,
        seed_plan: SeedPlanHandoff | None = None,
    ) -> dict[str, dict[str, float]]:
        """Run the solve setup's pipeline stage, then reduce its frame in bounded batches.

        Batches are the pipeline's streaming chunk size, the setting every job
        inherits.
        """
        self._store.atomic_update(
            job_id,
            {
                "message": "Executing pipeline",
                "progress": 0.05,
                "elapsed_seconds": self._job_elapsed(job_id),
            },
            expected_status="running",
        )
        self._raise_if_frontier_auto_range_stopped(job_id)
        lazy_outputs, source_lf = self._run_optimiser_input_stage(
            body,
            job_id,
            resources,
            config=config,
            required_columns_by_node=required_columns_by_node,
            execution_context=execution_context,
            seed_plan=seed_plan,
            check_stopped=lambda: self._raise_if_frontier_auto_range_stopped(job_id),
        )
        self._store.atomic_update(
            job_id,
            {
                "message": "Projecting auto-range columns",
                "progress": 0.65,
                "elapsed_seconds": self._job_elapsed(job_id),
            },
            expected_status="running",
        )
        self._raise_if_frontier_auto_range_stopped(job_id)
        constraint_cols, scored_lf, value_check = self._validate_and_project_auto_range(
            source_lf,
            config,
            job_id,
            execution_context=execution_context,
        )
        self._raise_if_frontier_auto_range_stopped(job_id)
        del lazy_outputs
        gc.collect()

        self._store.atomic_update(
            job_id,
            {
                "message": "Aggregating scenario envelope",
                "progress": 0.75,
                "elapsed_seconds": self._job_elapsed(job_id),
            },
            expected_status="running",
        )
        self._raise_if_frontier_auto_range_stopped(job_id)
        # A value-contract violation surfaces when the last batch is read,
        # recorded as setup's refusal exactly as the solve records it.
        with self._recorded_setup_failures(job_id, execution_context):
            return _estimate_scenario_frontier_ranges(
                FrontierAutoRangeContext(
                    chunk_size=current_streaming_chunk_size(),
                    execution_context=execution_context,
                ),
                scored_lf=scored_lf,
                quote_id_col=str(config.get("quote_id", "quote_id")),
                constraint_cols=constraint_cols,
                value_check=value_check,
                check_cancelled=lambda: self._raise_if_frontier_auto_range_stopped(job_id),
            )

    def _frontier_ranges_in_worker(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        config: dict[str, Any],
        mode: str,
        required_columns_by_node: Mapping[str, Iterable[str]],
        timeout: int | None,
        execution_context: ExecutionContext,
    ) -> tuple[dict[str, dict[str, float]], Mapping[str, Any] | None]:
        """Supervise the hard-capped worker that computes the auto-range totals.

        The seed plan is opened here exactly as solve setup opens it, and the
        worker adopts it; the worker receives the demand resolved in the
        request thread and never re-plans. The job's remaining timeout bounds
        the worker. Returns the totals and the worker's execution metrics.
        """
        self._store.atomic_update(
            job_id,
            {
                "message": "Estimating frontier range",
                "progress": 0.05,
                "elapsed_seconds": self._job_elapsed(job_id),
            },
            expected_status="running",
        )
        self._raise_if_frontier_auto_range_stopped(job_id)
        # The seed plan is held until the worker has exited.
        with contextlib.ExitStack() as resources:
            handoff = self._open_setup_seed_plan(
                body,
                job_id,
                resources,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
            )
            self._raise_if_frontier_auto_range_stopped(job_id)
            remaining = None if timeout is None else timeout - self._job_elapsed(job_id)
            if timeout is not None and remaining is not None and remaining <= 0:
                raise self._time_out_frontier_auto_range(job_id, timeout)
            with worker_scratch_directory() as scratch_dir:
                outcome = self._run_optimiser_worker(
                    frontier_auto_range_worker,
                    FrontierAutoRangeWorkerRequest(
                        body=body,
                        config=dict(config),
                        mode=mode,
                        timeout=timeout,
                        required_columns_by_node={
                            node_id: frozenset(columns)
                            for node_id, columns in required_columns_by_node.items()
                        },
                        project_root=str(_get_project_root()),
                        seed_plan=handoff,
                        scratch_dir=scratch_dir,
                    ),
                    job_id=job_id,
                    node_id=body.node_id,
                    execution_context=execution_context,
                    timeout_seconds=remaining,
                    process_name="haute-optimiser-auto-range",
                    on_timeout=(
                        None
                        if timeout is None
                        else functools.partial(self._time_out_frontier_auto_range, job_id, timeout)
                    ),
                )
        if not isinstance(outcome, FrontierAutoRangeWorkerOutcome):
            raise RuntimeError(f"Auto-range worker returned {type(outcome).__name__}")
        if outcome.failure is not None:
            raise OptimiserWorkerFailureError(outcome.failure)
        if outcome.ranges is None:
            raise RuntimeError("Auto-range worker returned neither ranges nor a failure")
        return outcome.ranges, outcome.execution_metrics

    def _time_out_frontier_auto_range(
        self,
        job_id: str,
        timeout: int | float,
    ) -> BackgroundJobStoppedError:
        """Publish an auto-range timeout and return the stop that ends its worker."""
        self._jobs.cancel(job_id, reason="timed_out")
        self._lifecycle.transition(
            job_id,
            to="timed_out",
            message=(
                f"Auto range timed out after {timeout}s. "
                "Reduce the input size or raise the optimisation time limit in the "
                "pipeline settings."
            ),
            elapsed_seconds=_job_elapsed_seconds(self._store.require_job(job_id)),
        )
        return BackgroundJobStoppedError(job_id, "timed_out")

    def _launch_frontier_auto_range_background(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        setup_singleflight_key: tuple[str, str, str] | None = None,
        execution_token: ExecutionCancellationToken | None = None,
        **prepared: Any,
    ) -> None:
        start_time = time.monotonic()
        self._store.atomic_update(
            job_id,
            {
                "start_time": start_time,
                "timeout": prepared["timeout"],
            },
        )

        def _auto_range_background() -> None:
            with self._job_ownership_scope(
                job_id,
                setup_singleflight_key=setup_singleflight_key,
            ):
                try:
                    self._run_frontier_auto_range_job(
                        body,
                        job_id,
                        execution_token=execution_token,
                        **prepared,
                    )
                except BackgroundJobStoppedError:
                    return
                except HTTPException:
                    return
                except Exception as exc:
                    logger.error(
                        "frontier_auto_range_worker_failed",
                        error=str(exc),
                        node_id=body.node_id,
                        exc_info=True,
                    )

        thread = threading.Thread(target=_auto_range_background, daemon=True)
        try:
            thread.start()
        except Exception as exc:
            logger.error(
                "frontier_auto_range_worker_start_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._lifecycle.transition(
                job_id,
                to="error",
                message=f"Failed to start auto-range worker: {exc}",
                elapsed_seconds=time.monotonic() - start_time,
            )
            raise HTTPException(
                status_code=500,
                detail="Auto-range worker failed to start. Check the server logs for details.",
            ) from exc

    # ------------------------------------------------------------------
    # Private orchestration steps
    # ------------------------------------------------------------------

    def estimate_input(
        self,
        body: OptimiserEstimateRequest,
        *,
        execution_context: ExecutionContext,
    ) -> dict[str, int | float | None]:
        """Count the optimiser's projected input for ``POST /estimate``.

        Cost contract (pinned by the single-scan tests in
        ``tests/test_optimiser_routes_real_library.py``): execute the pipeline
        up to the optimiser's data input, then run exactly ONE streaming
        aggregation scan over the quote-id column, with the null-``quote_id``
        check folded in. Solve-grade value validation is left to the solve.
        The estimate job is tagged so it never blocks a solve, and is removed
        on every exit. The caller owns *execution_context*'s admission.
        """
        body = cast(OptimiserEstimateRequest, _with_flattened_optimiser_graph(body))
        node = _find_optimiser_node(body.graph, body.node_id)
        config = node.data.config
        self._validate_config(config)
        data_input_id = _resolve_optimiser_data_input_id(body.graph, body.node_id, config)
        required_columns_by_node = _optimiser_solve_required_columns_by_node(
            body.graph,
            body.node_id,
            config,
        )
        initial_job: _OptimiserEstimateRunningJob = {
            "status": "running",
            "job_type": _ESTIMATE_JOB_TYPE,
            "message": "Estimating optimiser input",
            "config": dict(config),
            "node_label": node.data.label,
        }
        job_id = self._store.create_job(initial_job)
        try:
            # The seed plan entered on this stack is held while the estimate
            # reads its frames, and released on every exit.
            with contextlib.ExitStack() as resources:
                lazy_outputs = self._execute_pipeline(
                    body,
                    job_id,
                    resources,
                    required_columns_by_node=required_columns_by_node,
                    target_node_id=data_input_id or body.node_id,
                    execution_context=execution_context,
                )
                source_lf = self._resolve_data_input_frame(
                    lazy_outputs,
                    body.graph,
                    config,
                    body.node_id,
                    job_id,
                )
                return estimate_input_metrics(source_lf, config)
        finally:
            self._store.delete_job(job_id)

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> str:
        """Validate optimiser config; return the mode ('online' or 'ratebook')."""
        objective = config.get("objective")
        if not objective:
            raise HTTPException(
                status_code=400,
                detail="No objective column configured."
                " Open the config panel and set an objective.",
            )

        mode = config.get("mode", "online")
        if mode not in ("online", "ratebook"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported optimiser mode '{mode}'."
                " Currently supported: online, ratebook.",
            )

        constraints = config.get("constraints") or {}
        reserved = sorted(
            name for name in constraints if name in RESERVED_OPTIMISER_CONSTRAINT_NAMES
        )
        if reserved:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Constraint name(s) {reserved} are reserved: price-contour already names "
                    "its outputs total_objective, optimal_step and optimal_scenario_value, so "
                    "a constraint called objective, step or scenario_value would overwrite "
                    "one of them. Rename the constraint."
                ),
            )

        if mode == "ratebook":
            factor_columns = config.get("factor_columns")
            if not factor_columns:
                raise HTTPException(
                    status_code=400,
                    detail="Ratebook mode requires factor_columns. Add at least one factor group.",
                )

        try:
            _solve_timeout_from_config(config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            validate_optimiser_analysis_config(config)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return str(mode)

    @staticmethod
    def _is_blocking_solve_job(job: JobSnapshot) -> bool:
        """Return whether a job should reserve the real optimiser solve slot."""
        if job.get("status") != "running":
            return False
        return job.get(_JOB_TYPE_KEY, _SOLVE_JOB_TYPE) not in _NON_BLOCKING_RUNNING_JOB_TYPES

    def _has_running_solve_job(self) -> bool:
        return self._store.has_job_matching(self._is_blocking_solve_job)

    def _check_no_concurrent_jobs(self) -> None:
        """Reject if an optimisation solve job is already running."""
        if self._has_running_solve_job():
            raise HTTPException(
                status_code=409,
                detail="An optimisation job is already running. Please wait for it to finish.",
            )

    @staticmethod
    def _setup_execution_scope(
        body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
        target_node_id: str | None,
    ) -> tuple[str, frozenset[str], tuple[str, ...]]:
        """Return setup's execution target, preserved side inputs and consumed nodes.

        Consumed are every node setup reads afterwards: an explicit target alone;
        otherwise the resolved execution target (an Optimiser resolves to its data
        input) and each banding side input from its own edges that the run
        executes, API inputs included.
        """
        execution_target_node_id = target_node_id or _setup_execution_target_node_id(
            body.graph, body.node_id
        )
        preserved_node_ids = _optimiser_side_input_ids(body.graph, body.node_id)
        consumed_node_ids: tuple[str, ...] = (execution_target_node_id,)
        if target_node_id is None:
            target_lineage = set(upstream_node_ids(execution_target_node_id, body.graph.parents_of))
            consumed_node_ids += tuple(sorted(preserved_node_ids & target_lineage))
        return execution_target_node_id, preserved_node_ids, consumed_node_ids

    def _setup_seed_plan_request(
        self,
        body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
        *,
        required_columns_by_node: Mapping[str, Iterable[str]] | None,
        target_node_id: str | None,
        execution_context: ExecutionContext | None,
    ) -> SeedPlanRequest:
        """The seed plan one setup execution runs under.

        Solve setup and auto-range build it from the same demand, so they make
        the same cache decision and a capture either publishes serves the
        other. The plan decides separately which consumed nodes it can capture.
        """
        from haute.executor import _resolve_batch_scenario

        # Resolve scenario: optimiser runs on batch data, not live.
        scenario = _resolve_batch_scenario(body.graph) or "batch"
        execution_target_node_id, _preserved, consumed_node_ids = self._setup_execution_scope(
            body, target_node_id
        )
        return SeedPlanRequest(
            graph=body.graph,
            target_node_id=execution_target_node_id,
            source=scenario,
            profile=(
                execution_context.profile
                if execution_context is not None
                else ExecutionProfile.LAZY_SINK
            ),
            consumed_node_ids=consumed_node_ids,
            required_columns_by_node=required_columns_by_node,
        )

    def _open_setup_seed_plan(
        self,
        body: OptimiserSolveRequest | OptimiserFrontierAutoRangeRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        required_columns_by_node: Mapping[str, Iterable[str]] | None = None,
        target_node_id: str | None = None,
        execution_context: ExecutionContext,
    ) -> SeedPlanHandoff:
        """Open, on *resources*, the seed plan a setup worker adopts, and return its handoff.

        Input preparation runs here, under the parent's admitted context,
        because a node's signature signs its prepared inputs; the plan's seed
        leases and capture staging are held until the worker has exited.
        """
        flattened = _with_flattened_optimiser_graph(body)
        with self._setup_execution_failures(flattened, job_id, execution_context):
            plan = resources.enter_context(
                open_seed_plan(
                    self._setup_seed_plan_request(
                        flattened,
                        required_columns_by_node=required_columns_by_node,
                        target_node_id=target_node_id,
                        execution_context=execution_context,
                    ),
                    execution_context=execution_context,
                )
            )
            return plan.handoff()

    def _execute_pipeline(
        self,
        body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        required_columns_by_node: Mapping[str, Iterable[str]] | None = None,
        target_node_id: str | None = None,
        execution_context: ExecutionContext | None = None,
        preamble_fingerprint: str | None = None,
        seed_plan: SeedPlanHandoff | None = None,
    ) -> dict[str, Any]:
        """Execute the pipeline lazily up to the optimiser node, under a seed plan.

        The plan is entered on the caller's *resources* stack, so its seed
        leases and captures stay held until the caller has finished reading
        the returned frames. A setup worker passes the *seed_plan* handoff its
        supervising parent opened and adopts it instead of opening its own.
        """
        body = _with_flattened_optimiser_graph(body)
        with self._setup_execution_failures(body, job_id, execution_context):
            from haute._cache import preamble_execution_fingerprint
            from haute.executor import (
                _build_node_fn,
                _compile_preamble,
                _pipeline_dir,
                _resolve_batch_scenario,
            )

            scenario = _resolve_batch_scenario(body.graph) or "batch"
            pinned = (
                preamble_fingerprint
                if preamble_fingerprint is not None
                else preamble_execution_fingerprint(
                    body.graph.preamble or "",
                    pipeline_dir=_pipeline_dir(body.graph),
                )
            )

            preamble_ns = (
                _compile_preamble(
                    body.graph.preamble or "",
                    pipeline_dir=_pipeline_dir(body.graph),
                    execution_fingerprint=pinned,
                )
                or None
            )

            execution_target_node_id, preserved_node_ids, _consumed = self._setup_execution_scope(
                body, target_node_id
            )
            if seed_plan is not None:
                plan = resources.enter_context(SeedPlan.adopt(seed_plan))
            else:
                plan = resources.enter_context(
                    open_seed_plan(
                        self._setup_seed_plan_request(
                            body,
                            required_columns_by_node=required_columns_by_node,
                            target_node_id=target_node_id,
                            execution_context=execution_context,
                        ),
                        execution_context=execution_context,
                    )
                )
            lazy_outputs, *_ = execute_lazy_graph(
                body.graph,
                _build_node_fn,
                target_node_id=execution_target_node_id,
                preamble_ns=preamble_ns,
                source=scenario,
                enforce_contracts=True,
                preserve_node_ids=preserved_node_ids,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
                prepare_inputs=False,
                snapshot_plan=plan,
            )
            return lazy_outputs

    @contextlib.contextmanager
    def _setup_execution_failures(
        self,
        body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
        job_id: str,
        execution_context: ExecutionContext | None,
    ) -> Iterator[None]:
        """Record and translate a failure while opening or executing setup's pipeline."""
        try:
            yield
        except HTTPException:
            raise
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            self._record_setup_failure(
                job_id,
                to=contract_error_terminal_reason(exc),
                message=str(exc),
                fields=contract_error_job_fields(exc),
                execution_context=execution_context,
            )
            raise contract_error_http_exception(exc) from None
        except (ContractMismatchError, SchemaMismatchError) as exc:
            error_msg = f"Pipeline execution failed: {exc}"
            logger.warning(
                "pipeline_contract_mismatch",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._record_http_setup_failure(
                job_id,
                status_code=400,
                detail=error_msg,
                execution_context=execution_context,
            )
            raise HTTPException(status_code=400, detail=error_msg) from exc
        except (ExecutionCancelledError, ExecutionMemoryLimitExceededError):
            raise
        except BoundedMemoryUnsupportedError as exc:
            error_msg = f"Pipeline cannot run in bounded streaming mode: {exc}"
            logger.warning(
                "pipeline_bounded_streaming_unsupported",
                error=str(exc),
                node_id=body.node_id,
            )
            self._record_http_setup_failure(
                job_id,
                status_code=422,
                detail=error_msg,
                execution_context=execution_context,
            )
            raise HTTPException(status_code=422, detail=error_msg) from exc
        except Exception as exc:
            error_msg = f"Pipeline execution failed: {exc}"
            logger.error(
                "pipeline_exec_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._record_setup_failure(
                job_id,
                to="error",
                message=error_msg,
                execution_context=execution_context,
            )
            raise HTTPException(
                status_code=500,
                detail="Pipeline execution failed. Check the server logs for details.",
            ) from exc
        finally:
            if execution_context is not None:
                self._record_execution_metrics(job_id, execution_context)

    @contextlib.contextmanager
    def _recorded_setup_failures(
        self,
        job_id: str,
        execution_context: ExecutionContext | None,
    ) -> Iterator[None]:
        """Record a setup step's refusal as the job's terminal state, then answer it."""
        try:
            yield
        except OptimiserSetupError as caught:
            failure = caught
        else:
            return
        self._record_setup_failure(
            job_id,
            to=failure.reason,
            message=failure.message,
            fields=failure.fields,
            execution_context=execution_context,
        )
        # No explicit cause. Python still sets the setup error as the answer's
        # implicit context, which the worker's memory-error detection walks.
        raise failure.http_exception()

    def _resolve_data_input_frame(
        self,
        lazy_outputs: dict[str, Any],
        graph: PipelineGraph,
        config: dict[str, Any],
        node_id: str,
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> Any:
        """Pick the correct lazy source from pipeline outputs."""
        with self._recorded_setup_failures(job_id, execution_context):
            return resolve_data_input_frame(lazy_outputs, graph, config, node_id)

    def _validate_and_project(
        self,
        source_lf: Any,
        config: dict[str, Any],
        job_id: str,
        *,
        analysis_columns: Iterable[str] = (),
        validate_quote_id_nulls: bool = True,
        execution_context: ExecutionContext | None = None,
    ) -> tuple[list[str], Any]:
        """Validate columns and build the projection for the solver.

        Returns (constraint_cols, projected_lazy_frame).
        """
        with (
            _execution_stage(execution_context, "optimiser_validate_and_project"),
            self._recorded_setup_failures(job_id, execution_context),
        ):
            return validate_and_project(
                source_lf,
                config,
                analysis_columns=analysis_columns,
                validate_quote_id_nulls=validate_quote_id_nulls,
                execution_context=execution_context,
            )

    def _validate_and_project_auto_range(
        self,
        source_lf: Any,
        config: dict[str, Any],
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> tuple[list[str], Any, AutoRangeValueCheck]:
        """Check auto-range's schema and project only the columns it reads."""
        with self._recorded_setup_failures(job_id, execution_context):
            return validate_and_project_auto_range(source_lf, config)

    @staticmethod
    def _extract_factors(
        lazy_outputs: dict[str, Any],
        graph: PipelineGraph,
        optimiser_node_id: str,
        config: dict[str, Any],
        mode: str,
        *,
        execution_context: ExecutionContext | None = None,
        artifact_dir: str | None = None,
    ) -> Any:
        """Extract ratebook factors DataFrame (None for online mode).

        A refusal is answered but not recorded here: the setup failure mapping
        records it as the job's terminal state.
        """
        try:
            return extract_ratebook_factors(
                lazy_outputs,
                graph,
                optimiser_node_id,
                config,
                mode,
                execution_context=execution_context,
                artifact_dir=artifact_dir,
            )
        except OptimiserSetupError as failure:
            raise failure.http_exception() from failure

    def _write_solver_input(
        self,
        scored_lf: Any,
        output_path: str,
        node_id: str,
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
        allow_borrow: bool = True,
    ) -> str:
        """Write the projected solver input to *output_path*, or borrow its snapshot."""
        with self._recorded_setup_failures(job_id, execution_context):
            return write_solver_input(
                scored_lf,
                output_path,
                node_id,
                execution_context=execution_context,
                allow_borrow=allow_borrow,
            )

    def _write_quote_analysis(
        self,
        input_path: str,
        analysis: _AnalysisSource,
        config: dict[str, Any],
        node_id: str,
        job_id: str,
        *,
        directory: Path | None,
        execution_context: ExecutionContext | None,
    ) -> dict[str, Any]:
        """Reduce the analysis columns to ``quote_analysis.parquet``; setup owns the handle.

        A side-input frame is executed here, so a pipeline failure is typed as a
        solver-input write failure is.
        """
        with (
            self._recorded_setup_failures(job_id, execution_context),
            grid_construction_failures(node_id),
            _execution_stage(
                execution_context, "optimiser_extract_quote_analysis", node_id=node_id
            ),
        ):
            return write_quote_analysis(
                solver_input_path=input_path,
                analysis_frame=analysis.frame,
                quote_id=str(config.get("quote_id", "quote_id")),
                columns=analysis.columns,
                directory=directory,
                execution_context=execution_context,
            )

    def _build_grid(
        self,
        scored_lf: Any,
        constraint_cols: list[str],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
        analysis: _AnalysisSource | None = None,
    ) -> SetupGrid:
        """Sink scored data to parquet, build the QuoteGrid, then any analysis table.

        The thread compatibility path. The table is reduced after the grid is
        built: in one process, extracting first measured a higher peak, because
        the grid build does not reuse the memory the extraction frees.
        """
        tmp_path = _optimiser_artifacts._new_solver_input_path()
        try:
            input_path = self._write_solver_input(
                scored_lf,
                tmp_path,
                node_id,
                job_id,
                execution_context=execution_context,
            )
            del scored_lf
            grid = self._build_grid_from_parquet(
                input_path,
                constraint_cols,
                config,
                node_id,
                job_id,
                execution_context=execution_context,
            )
            if analysis is None:
                return SetupGrid(grid=grid, quote_analysis_handle=None)
            handle = self._write_quote_analysis(
                input_path,
                analysis,
                config,
                node_id,
                job_id,
                directory=None,
                execution_context=execution_context,
            )
            try:
                require_one_row_per_solved_quote(handle, grid.n_quotes)
            except BaseException:
                _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                    handle,
                    job_id=job_id,
                    event="setup_quote_analysis_row_count_cleanup_failed",
                )
                raise
            return SetupGrid(grid=grid, quote_analysis_handle=handle)
        finally:
            _optimiser_artifacts._remove_solver_input(tmp_path)

    def _build_grid_from_parquet(
        self,
        input_path: str,
        constraint_cols: list[str],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
        n_steps: int | None = None,
    ) -> QuoteGrid:
        """Build the solver's QuoteGrid from a written or borrowed solver-input parquet.

        The job records the grid's ``scenario_grid`` as soon as it exists. A
        caller that already read the file's step count passes it as *n_steps*.
        """
        with (
            self._recorded_setup_failures(job_id, execution_context),
            grid_construction_failures(node_id),
            _execution_stage(execution_context, "optimiser_build_grid", node_id=node_id),
        ):
            if n_steps is None:
                n_steps = scenario_step_count(
                    Path(input_path),
                    str(config.get("scenario_index", "scenario_index")),
                    execution_context,
                )
            decision = grid_chunk_decision(n_steps)
            self._record_setup_chunking(job_id, "optimiser_grid", decision.provenance)
            grid = build_quote_grid(
                input_path,
                constraint_cols,
                config,
                decision.chunk_size,
                n_steps=n_steps,
                execution_context=execution_context,
            )
            self._record_scenario_grid(job_id, grid)
            return grid

    def _record_scenario_grid(self, job_id: str, quote_grid: QuoteGrid) -> None:
        """Record the solver input's complete grid on the job, once, before the solve."""
        self._store.atomic_update(
            job_id,
            {"scenario_grid": scenario_grid_from_values(quote_grid.scenario_values)},
            expected_status="running",
        )

    def _record_setup_chunking(
        self,
        job_id: str,
        name: str,
        provenance: dict[str, int | str | None],
    ) -> None:
        job = self._store.get_job(job_id)
        if job is None:
            raise KeyError(f"Optimiser job {job_id!r} disappeared before chunk provenance update.")
        current = job.get("setup_chunking")
        setup_chunking = dict(current) if isinstance(current, Mapping) else {}
        setup_chunking[name] = provenance
        self._store.update_job(job_id, setup_chunking=setup_chunking)

    def _launch_background(
        self,
        ctx: SolveContext,
        *,
        config: dict[str, Any],
        quote_grid: QuoteGrid,
        ratebook_factors_handle: Any,
        quote_analysis_handle: dict[str, Any] | None = None,
        factor_level_order: dict[str, list[str]] | None = None,
    ) -> None:
        """Start the solver in a background thread.

        The solve adopts *quote_analysis_handle* at completion; the worker
        removes it when the job never did.
        """
        job_id = ctx.job_id
        node_id = ctx.node_id
        mode = ctx.mode
        setup_singleflight_key = ctx.setup_singleflight_key
        execution_context = ctx.execution_context

        existing_job: Mapping[str, Any] = self._store.get_job(job_id) or {}
        raw_start_time = existing_job.get("start_time")
        start_time = (
            float(raw_start_time)
            if isinstance(raw_start_time, int | float) and not isinstance(raw_start_time, bool)
            else time.monotonic()
        )
        self._store.atomic_update(
            job_id,
            {
                "start_time": start_time,
                "timeout": _solve_timeout_from_config(config),
            },
        )
        if execution_context is None:
            execution_token = ExecutionCancellationToken()
            self._jobs.register_latest(
                (_SOLVE_JOB_TYPE, job_id),
                job_id,
                execution_token=execution_token,
            )
            execution_context = create_admitted_execution_context(
                operation="optimiser_solve_worker",
                profile=ExecutionProfile.OPTIMISER_SETUP,
                job_id=job_id,
                cancellation_token=execution_token,
            )
        elif not ctx.registration_already_active:
            self._jobs.register_latest(
                (_SOLVE_JOB_TYPE, job_id),
                job_id,
                execution_token=execution_context.cancellation_token,
            )

        def _solve_background() -> None:
            # Set only by a published completion; the job then owns the handles.
            adopted = False
            try:
                self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                # Use atomic_update so status-polling reads see a consistent snapshot.
                progress_job = self._store.atomic_update(
                    job_id,
                    {
                        "message": "Solving",
                        "progress": 0.1,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                    expected_status="running",
                )
                if progress_job is None:
                    logger.info("solve_start_skipped", job_id=job_id, expected_status="running")
                    return
                if mode == "ratebook":
                    with execution_context.stage("optimiser_solver_solve", node_id=node_id):
                        solve_ctx = dataclasses.replace(
                            ctx,
                            store=self._store,
                            start_time=start_time,
                            check_cancelled=lambda: self._raise_if_solve_stopped(
                                job_id,
                                execution_context=execution_context,
                            ),
                        )
                        adopted = _solve_ratebook(
                            solve_ctx,
                            quote_grid=quote_grid,
                            config=config,
                            ratebook_factors_handle=ratebook_factors_handle,
                            factor_level_order=factor_level_order,
                            quote_analysis_handle=quote_analysis_handle,
                        )
                else:
                    with execution_context.stage("optimiser_solver_solve", node_id=node_id):
                        solve_ctx = dataclasses.replace(
                            ctx,
                            store=self._store,
                            start_time=start_time,
                            check_cancelled=lambda: self._raise_if_solve_stopped(
                                job_id,
                                execution_context=execution_context,
                            ),
                        )
                        adopted = _solve_online(
                            solve_ctx,
                            quote_grid=quote_grid,
                            config=config,
                            quote_analysis_handle=quote_analysis_handle,
                        )
            except BackgroundJobStoppedError:
                logger.info("solve_worker_stopped", job_id=job_id)
            except Exception as exc:
                reason, message, fields = solve_failure_transition(
                    exc,
                    node_id=node_id,
                    elapsed_seconds=time.monotonic() - start_time,
                )
                error_job = self._lifecycle.transition(
                    job_id,
                    to=reason,
                    message=message,
                    fields=fields,
                    elapsed_seconds=time.monotonic() - start_time,
                )
                if error_job is None:
                    logger.info("solve_error_update_skipped", job_id=job_id)
            finally:
                current = self._store.get_job(job_id)
                if current is not None:
                    self._store.update_job(
                        job_id,
                        execution_metrics=execution_context.metrics_payload(
                            status=(
                                str(current.get("status"))
                                if current.get("status") is not None
                                else None
                            ),
                            terminal_reason=(
                                str(current.get("terminal_reason"))
                                if current.get("terminal_reason") is not None
                                else None
                            ),
                        ),
                    )
                execution_context.release_admission()
                if (
                    mode == "ratebook"
                    and isinstance(ratebook_factors_handle, dict)
                    and ratebook_factors_handle.get("kind")
                    == _optimiser_artifacts._RATEBOOK_FACTORS_HANDLE_KIND
                ):
                    current = self._store.get_job(job_id)
                    handles = current.get("artifact_handles") if current is not None else None
                    attached = (
                        isinstance(handles, dict)
                        and isinstance(
                            handles.get(_optimiser_artifacts._RATEBOOK_FACTORS_HANDLE_KEY), dict
                        )
                        and handles[_optimiser_artifacts._RATEBOOK_FACTORS_HANDLE_KEY].get("path")
                        == ratebook_factors_handle.get("path")
                    )
                    if not attached:
                        _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                            ratebook_factors_handle,
                            job_id=job_id,
                            event="solve_worker_orphan_ratebook_factors_cleanup_failed",
                        )
                if quote_analysis_handle is not None and not adopted:
                    # Never re-read the job here: once adopted, only the job's own
                    # removal may delete the table, which a reader's lease defers.
                    _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                        quote_analysis_handle,
                        job_id=job_id,
                        event="solve_worker_orphan_quote_analysis_cleanup_failed",
                    )

        def _solve_background_in_worker_context() -> None:
            with self._job_ownership_scope(
                job_id,
                setup_singleflight_key=setup_singleflight_key,
            ):
                with solver_worker_context():
                    _solve_background()

        thread = threading.Thread(target=_solve_background_in_worker_context, daemon=True)
        try:
            thread.start()
        except Exception as exc:
            logger.error(
                "solve_worker_start_failed",
                error=str(exc),
                node_id=node_id,
                exc_info=True,
            )
            self._lifecycle.transition(
                job_id,
                to="error",
                message=f"Failed to start optimiser worker: {exc}",
                elapsed_seconds=time.monotonic() - start_time,
            )
            if not ctx.registration_already_active:
                self._release_job_ownership(
                    job_id,
                    setup_singleflight_key=setup_singleflight_key,
                )
                execution_context.release_admission()
            if (
                mode == "ratebook"
                and isinstance(ratebook_factors_handle, dict)
                and ratebook_factors_handle.get("kind")
                == _optimiser_artifacts._RATEBOOK_FACTORS_HANDLE_KIND
            ):
                _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                    ratebook_factors_handle,
                    job_id=job_id,
                    event="solve_worker_start_orphan_ratebook_factors_cleanup_failed",
                )
            raise HTTPException(
                status_code=500,
                detail="Optimiser worker failed to start. Check the server logs for details.",
            ) from exc
