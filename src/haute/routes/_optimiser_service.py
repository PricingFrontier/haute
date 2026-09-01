"""OptimiserSolveService — orchestrates optimisation solving, extracted from the route handler.

The route handler becomes a thin adapter that delegates to
``OptimiserSolveService.start()``.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import functools
import gc
import math
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
from fastapi import HTTPException

if TYPE_CHECKING:
    import polars as pl
    from price_contour import QuoteGrid

    from haute.chunking import ChunkPlan

from haute._artifact_housekeeping import (
    create_owned_artifact_directory,
    reap_stale_artifact_directories,
)
from haute._banding_config import normalise_banding_factors
from haute._contracts import Contract, get_column_contract
from haute._env import int_env, optional_int_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
    execution_budget_for_profile,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._graph_utils import _sanitize_func_name, upstream_node_ids
from haute._logging import get_logger
from haute._polars_utils import (
    DEFAULT_STREAMING_CHUNK_SIZE,
    bounded_collect_batches,
    bounded_sink,
    read_parquet_metadata,
    streaming_collect,
    temporary_streaming_chunk_size,
)
from haute._rating import (
    normalise_rating_key,
    rating_dtype_descriptor,
    rating_dtype_from_descriptor,
)
from haute._types import (
    GraphNode,
    OnlineSolveResultLike,
    PipelineGraph,
    RatebookSolveResultLike,
    SolveResultLike,
)
from haute.errors import (
    BoundedMemoryUnsupportedError,
    ChunkPlanUnsupportedError,
    ContractMismatchError,
    ProjectionImpossibleError,
    SchemaMismatchError,
)
from haute.execution import (
    build_dataframe_execution_cache_request,
    dataframe_graph_input_fingerprint,
    execute_lazy_graph,
    ratebook_factor_required_columns,
)
from haute.executor import _build_node_fn
from haute.graph_utils import NodeType, flatten_graph, graph_fingerprint
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
)
from haute.routes._helpers import find_typed_node
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
    register_artifact_cleaner,
)
from haute.routes._memory_messages import memory_limit_user_message
from haute.routes._optimiser_limits import (
    enforce_frontier_compute_budget,
    limited_frontier_payload,
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
    _normalise_frontier_range_pair,
)

logger = get_logger(component="server.optimiser.solve")

# ── Default constants ─────────────────────────────────────────────
_HISTOGRAM_BINS = 20  # bin count for scenario-value distribution histogram
_DEFAULT_MAX_ITER = 50  # max solver iterations (online & ratebook)


# Env-tunable defaults — resolved per call so overrides set after import
# take effect.
def _default_solver_timeout() -> int | None:
    return optional_int_env("HAUTE_SOLVER_TIMEOUT")


def _default_auto_range_timeout() -> int:
    return int_env("HAUTE_AUTO_RANGE_TIMEOUT", 1800)


def _default_auto_range_chunk_size() -> int:
    return int_env("HAUTE_AUTO_RANGE_CHUNK_SIZE", 2_000_000)


def _default_auto_range_partitions() -> int:
    # disk buckets for chunked auto-range aggregation
    return int_env("HAUTE_AUTO_RANGE_PARTITIONS", 16)


_DEFAULT_AUTO_RANGE_TARGET_CHUNK_MIN_BYTES = 16 * 1024 * 1024
_DEFAULT_AUTO_RANGE_TARGET_CHUNK_MAX_BYTES = 512 * 1024 * 1024
_DEFAULT_AUTO_RANGE_TARGET_CHUNK_BUDGET_DIVISOR = 16
_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_MIN_BYTES = 16 * 1024 * 1024
_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_MAX_BYTES = 512 * 1024 * 1024
_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_BUDGET_DIVISOR = 16
_DEFAULT_TOLERANCE = 1e-6  # convergence tolerance for solver
_DEFAULT_MAX_CD_ITERATIONS = 10  # max coordinate-descent iterations (ratebook)
_DEFAULT_CD_TOLERANCE = 1e-3  # coordinate-descent convergence tolerance (ratebook)
_APPLY_RESULT_HANDLE_KEY = "apply_result"
_APPLY_RESULT_HANDLE_KIND = "optimiser_apply_result"
_RATEBOOK_FACTORS_HANDLE_KEY = "ratebook_factors"
_RATEBOOK_FACTORS_HANDLE_KIND = "optimiser_ratebook_factors"
_ARTIFACT_HANDLE_VERSION = 1
_APPLY_ARTIFACT_ROOT_NAME = "haute/artifacts/v1/optimiser_apply"
_APPLY_ARTIFACT_DIR_PREFIX = "apply_"
_APPLY_RESULT_FILENAME = "result.parquet"
_RATEBOOK_FACTORS_ARTIFACT_ROOT_NAME = "haute/artifacts/v1/optimiser_ratebook_factors"
_RATEBOOK_FACTORS_ARTIFACT_DIR_PREFIX = "factors_"
_RATEBOOK_FACTORS_FILENAME = "factors.parquet"
_APPLY_ARTIFACT_OWNER = "optimiser_apply"
_RATEBOOK_FACTORS_ARTIFACT_OWNER = "optimiser_ratebook_factors"
_ARTIFACT_STALE_SECONDS_ENV = "HAUTE_ARTIFACT_STALE_SECONDS"
_DEFAULT_ARTIFACT_STALE_SECONDS = 86_400
_JOB_TYPE_KEY = "job_type"


class _OptimiserSolveInputError(Exception):
    """A user-actionable error while adapting optimiser solver input."""


class _OptimiserSolverExecutionError(Exception):
    """An exception raised by the external price-contour solver boundary."""


_SOLVE_JOB_TYPE: Literal["solve"] = "solve"
_ESTIMATE_JOB_TYPE: Literal["estimate"] = "estimate"
_FRONTIER_AUTO_RANGE_JOB_TYPE: Literal["frontier_auto_range"] = "frontier_auto_range"
_FRONTIER_RECOMPUTE_JOB_TYPE: Literal["frontier_recompute"] = "frontier_recompute"
_FRONTIER_GENERATION_KEY = "frontier_generation"
_GRAPH_NODE_SETUP_COORDINATION_TYPE = "optimiser_graph_node_setup"
_NULL_QUOTE_ID_DETAIL_PREFIX = "Null quote_id values found in optimiser input"
_NON_FINITE_DETAIL_PREFIX = "Non-finite values found in optimiser input"
_NULL_VALUE_DETAIL_PREFIX = "Null values found in optimiser input"
_QUOTE_ID_NULL_COUNT_ALIAS = "__haute_quote_id_null_count"
_NON_FINITE_COUNT_ALIAS_PREFIX = "__haute_non_finite_count_"
_NULL_COUNT_ALIAS_PREFIX = "__haute_null_count_"
_AUTO_RANGE_BUCKET_COLUMN = "__haute_frontier_auto_range_bucket"
_FRONTIER_AUTO_RANGE_CANCELLED_STATUS = "cancelled"
_FRONTIER_AUTO_RANGE_SUPERSEDED_STATUS = "superseded"
_FRONTIER_AUTO_RANGE_TERMINAL_STATUSES = TERMINAL_REASONS


class _OptimiserSolveRunningJob(RunningJobFields):
    job_type: Literal["solve"]
    progress: float
    config: dict[str, Any]
    node_label: str
    start_time: float
    timeout: int | None


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

_SOLVER_WORKER_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "haute_optimiser_solver_worker",
    default=False,
)


@contextlib.contextmanager
def solver_worker_context() -> Iterator[None]:
    """Mark the current thread of execution as an optimiser solver worker.

    Entered only by the background job runners (solve worker, frontier sweep
    worker). Guarded entrypoints refuse to run outside it.
    """
    token = _SOLVER_WORKER_ACTIVE.set(True)
    try:
        yield
    finally:
        _SOLVER_WORKER_ACTIVE.reset(token)


def require_solver_worker_context(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Fail loud if a heavy solver entrypoint runs outside a worker context."""

    @functools.wraps(fn)
    def _guarded(*args: Any, **kwargs: Any) -> Any:
        if not _SOLVER_WORKER_ACTIVE.get():
            raise RuntimeError(
                f"{fn.__name__} is a heavy solver entrypoint and must run inside a "
                "background solver worker (solver_worker_context), never inline in a "
                "request handler. Submit a job and poll its status instead."
            )
        return fn(*args, **kwargs)

    return _guarded


def _with_flattened_optimiser_graph(
    body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
) -> OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest:
    """Return an optimiser request whose graph is executable by the lazy engine."""
    flat_graph = flatten_graph(body.graph)
    if flat_graph is body.graph:
        return body
    return body.model_copy(update={"graph": flat_graph})


def _missing_columns_detail(
    required_cols: Iterable[str],
    available_cols: Iterable[str],
) -> str | None:
    missing_cols = sorted(set(required_cols) - set(available_cols))
    if not missing_cols:
        return None
    return f"Missing columns in scored data: {missing_cols}. Available: {sorted(available_cols)}"


def _invalid_quote_id_dtype_detail(schema: Any, qid_col: str) -> str | None:
    import polars as pl

    qid_dtype = schema[qid_col]
    if qid_dtype == pl.String or qid_dtype == pl.Categorical or isinstance(qid_dtype, pl.Enum):
        return None
    return (
        f"{qid_col} must be Utf8 (String), Categorical, or Enum, got {qid_dtype}. "
        "Numeric, binary, and other dtypes are not supported as quote_id columns."
    )


def _quote_id_null_detail(null_count: int) -> str:
    return (
        f"{_NULL_QUOTE_ID_DETAIL_PREFIX} ({null_count} rows). "
        "Every row must have a non-null quote_id; check upstream filters and joins."
    )


def _non_finite_check_columns(schema: Any, column_names: Iterable[str]) -> list[str]:
    return [
        cname
        for cname in dict.fromkeys(column_names)
        if cname in schema and schema[cname].is_float()
    ]


def _null_check_columns(schema: Any, column_names: Iterable[str]) -> list[str]:
    return [cname for cname in dict.fromkeys(column_names) if cname in schema]


def _value_contract_validation_exprs(
    *,
    quote_id_col: str,
    validate_quote_id_nulls: bool,
    non_finite_check_cols: list[str],
    null_check_cols: list[str],
    cast_to_float32_cols: set[str],
) -> list[Any]:
    import polars as pl

    validation_exprs: list[Any] = []
    if validate_quote_id_nulls:
        validation_exprs.append(pl.col(quote_id_col).null_count().alias(_QUOTE_ID_NULL_COUNT_ALIAS))
    for index, cname in enumerate(non_finite_check_cols):
        checked = pl.col(cname).cast(pl.Float32) if cname in cast_to_float32_cols else pl.col(cname)
        validation_exprs.append(
            checked.is_nan().sum().alias(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}nan_{index}")
        )
        validation_exprs.append(
            checked.is_infinite().sum().alias(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}inf_{index}")
        )
    # Nulls are checked on the source dtype: is_nan()/is_infinite() return
    # null for null inputs, so sum() skips them and the finite check alone
    # cannot see a genuinely-null value.
    for index, cname in enumerate(null_check_cols):
        validation_exprs.append(
            pl.col(cname).null_count().alias(f"{_NULL_COUNT_ALIAS_PREFIX}{index}")
        )
    return validation_exprs


def _non_finite_detail_from_counts(
    validation_counts: Any,
    non_finite_check_cols: list[str],
) -> str | None:
    non_finite_summaries = []
    for index, cname in enumerate(non_finite_check_cols):
        nan_count = int(
            validation_counts.get_column(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}nan_{index}").item()
        )
        inf_count = int(
            validation_counts.get_column(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}inf_{index}").item()
        )
        kinds = [
            f"{count} {kind} row{'s' if count != 1 else ''}"
            for count, kind in ((nan_count, "NaN"), (inf_count, "infinite"))
            if count > 0
        ]
        if kinds:
            non_finite_summaries.append(f"'{cname}' ({', '.join(kinds)})")
    if not non_finite_summaries:
        return None
    return (
        f"{_NON_FINITE_DETAIL_PREFIX}: {', '.join(non_finite_summaries)}. "
        "The optimiser requires finite objective, constraint, and scenario values; "
        "check upstream joins and calculations for division by zero or overflow."
    )


def _null_value_detail_from_counts(
    validation_counts: Any,
    null_check_cols: list[str],
) -> str | None:
    null_summaries = []
    for index, cname in enumerate(null_check_cols):
        null_count = int(validation_counts.get_column(f"{_NULL_COUNT_ALIAS_PREFIX}{index}").item())
        if null_count > 0:
            null_summaries.append(
                f"'{cname}' ({null_count} null row{'s' if null_count != 1 else ''})"
            )
    if not null_summaries:
        return None
    return (
        f"{_NULL_VALUE_DETAIL_PREFIX}: {', '.join(null_summaries)}. "
        "The optimiser requires non-null objective, constraint, and scenario values; "
        "check upstream joins and filters for rows with missing values."
    )


def _memory_limit_http_exception(
    exc: ExecutionAdmissionError | ExecutionMemoryLimitExceededError,
) -> HTTPException:
    detail = exc.to_payload()
    # str(exc) names the internal operation and raw byte counts; author the
    # public message from the structured attributes via the shared shape
    # (matching the training and input-snapshot surfaces).
    detail["message"] = memory_limit_user_message(exc, operation_noun="Auto-range")
    return HTTPException(status_code=507, detail=detail)


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
    # A "message" key can only have been stamped by _memory_limit_http_exception
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


def _execution_stage(
    execution_context: ExecutionContext | None,
    name: str,
    *,
    node_id: str | None = None,
) -> Any:
    if execution_context is None:
        return nullcontext()
    return execution_context.stage(name, node_id=node_id)


def _coerce_stopped_terminal_reason(reason: str) -> TerminalReason:
    if reason in TERMINAL_REASONS:
        return cast(TerminalReason, reason)
    return "superseded"


_STREAMING_AUTO_RANGE_ALLOWED_NODE_TYPES = frozenset(
    {
        NodeType.SCENARIO_EXPANDER,
        NodeType.POLARS,
        NodeType.MODEL_SCORE,
    }
)


@dataclass(frozen=True, slots=True)
class _StreamingAutoRangePlan:
    base_node_id: str
    scenario_node_id: str
    chain_node_ids: tuple[str, ...]
    required_output_columns_by_node: Mapping[str, frozenset[str] | set[str] | None]
    base_required_columns: frozenset[str] | None
    chunk_plan: ChunkPlan


@dataclass(frozen=True, slots=True)
class _ChunkSizeDecision:
    chunk_size: int
    provenance: dict[str, int | str | None]


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return int(value)


def _optional_positive_int(value: object, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    return _positive_int(value, field=field)


def _solve_timeout_from_config(config: Mapping[str, Any]) -> int | None:
    if "timeout" not in config:
        return _default_solver_timeout()
    return _optional_positive_int(config.get("timeout"), field="timeout")


def _explicit_chunk_size_from_config(config: Mapping[str, Any]) -> int | None:
    if "chunk_size" not in config:
        return None
    return _positive_int(config["chunk_size"], field="chunk_size")


def _optimiser_setup_target_chunk_bytes() -> int:
    budget = execution_budget_for_profile(ExecutionProfile.OPTIMISER_SETUP)
    budget_scaled = max(
        1,
        budget.memory_limit_bytes // _DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_BUDGET_DIVISOR,
    )
    return min(
        _DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_MAX_BYTES,
        max(_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_MIN_BYTES, budget_scaled),
    )


def _chunk_size_decision_for_parquet(
    config: Mapping[str, Any],
    parquet_path: Path,
    *,
    source: str,
) -> _ChunkSizeDecision:
    explicit_chunk_size = _explicit_chunk_size_from_config(config)
    if explicit_chunk_size is not None:
        return _ChunkSizeDecision(
            chunk_size=explicit_chunk_size,
            provenance={
                "policy": "explicit_rows",
                "chunk_size": explicit_chunk_size,
                "target_chunk_bytes": None,
                "estimated_row_bytes": None,
                "row_count": None,
                "size_bytes": None,
                "uncompressed_size_bytes": None,
                "source": source,
            },
        )

    metadata = read_parquet_metadata(parquet_path)
    row_count = _positive_int(int(metadata["row_count"]), field="parquet row_count")
    row_bytes_basis = int(metadata.get("uncompressed_size_bytes") or metadata["size_bytes"])
    row_bytes_basis = _positive_int(row_bytes_basis, field="parquet byte size")
    target_chunk_bytes = _optimiser_setup_target_chunk_bytes()
    estimated_row_bytes = max(1, math.ceil(row_bytes_basis / row_count))
    chunk_size = max(1, target_chunk_bytes // estimated_row_bytes)
    return _ChunkSizeDecision(
        chunk_size=chunk_size,
        provenance={
            "policy": "byte_budget",
            "chunk_size": chunk_size,
            "target_chunk_bytes": target_chunk_bytes,
            "estimated_row_bytes": estimated_row_bytes,
            "row_count": int(metadata["row_count"]),
            "size_bytes": int(metadata["size_bytes"]),
            "uncompressed_size_bytes": int(metadata.get("uncompressed_size_bytes") or 0),
            "source": source,
        },
    )


def _auto_range_chunk_size_from_config(config: dict[str, Any]) -> int:
    if "auto_range_chunk_size" in config:
        return _positive_int(
            config["auto_range_chunk_size"],
            field="auto_range_chunk_size",
        )
    return _positive_int(
        config.get("chunk_size", _default_auto_range_chunk_size()),
        field="chunk_size",
    )


def _auto_range_explicit_chunk_size_from_config(config: dict[str, Any]) -> int | None:
    if "auto_range_chunk_size" in config:
        return _positive_int(
            config["auto_range_chunk_size"],
            field="auto_range_chunk_size",
        )
    if "chunk_size" in config:
        return _positive_int(config["chunk_size"], field="chunk_size")
    return None


def _auto_range_target_chunk_bytes() -> int:
    budget = execution_budget_for_profile(ExecutionProfile.AUTO_RANGE)
    budget_scaled = max(
        1,
        budget.memory_limit_bytes // _DEFAULT_AUTO_RANGE_TARGET_CHUNK_BUDGET_DIVISOR,
    )
    return min(
        _DEFAULT_AUTO_RANGE_TARGET_CHUNK_MAX_BYTES,
        max(_DEFAULT_AUTO_RANGE_TARGET_CHUNK_MIN_BYTES, budget_scaled),
    )


def _job_elapsed_seconds(job: Mapping[str, Any], fallback: float = 0.0) -> float:
    """Return wall-clock elapsed seconds for a job when start_time is available."""
    start_time = job.get("start_time")
    fallback_elapsed = max(0.0, float(fallback))
    if isinstance(start_time, bool) or not isinstance(start_time, int | float):
        return fallback_elapsed
    return max(fallback_elapsed, time.monotonic() - float(start_time), 0.0)


def _optimiser_side_input_ids(graph: PipelineGraph, node_id: str) -> frozenset[str]:
    """Return optimiser parent ids that are consumed after graph execution.

    The optimiser node may pass its input frame through, but solve/estimate
    setup resolves the configured ``data_input`` from the output map after the
    lazy executor has finished.  Treat that configured node as a retained
    setup input, just like ratebook's factor source, so checkpoint cleanup does
    not discard an intermediate parent once the optimiser node has consumed it.
    """
    node = _find_optimiser_node(graph, node_id)
    config = node.data.config
    preserved: set[str] = set()
    data_input = config.get("data_input")
    if isinstance(data_input, str) and data_input:
        preserved.add(data_input)
    if config.get("mode", "online") != "ratebook":
        return frozenset(preserved)
    banding_source = config.get("banding_source")
    if isinstance(banding_source, str) and banding_source:
        preserved.add(banding_source)
    return frozenset(preserved)


def _optimiser_dataframe_cache_node_ids(
    graph: PipelineGraph,
    *,
    optimiser_node_id: str,
    execution_target_node_id: str,
    explicit_target_node: bool,
) -> tuple[str, ...]:
    """Return optimiser setup outputs that callers consume after lazy execution."""

    if explicit_target_node:
        candidates = {execution_target_node_id}
    else:
        optimiser_node = _find_optimiser_node(graph, optimiser_node_id)
        preserved = set(_optimiser_side_input_ids(graph, optimiser_node_id))
        data_input_id = _resolve_optimiser_data_input_id(
            graph,
            optimiser_node_id,
            optimiser_node.data.config,
        )
        if isinstance(data_input_id, str) and data_input_id:
            preserved.add(data_input_id)
        candidates = preserved or {execution_target_node_id}

    target_lineage = set(upstream_node_ids(execution_target_node_id, graph.parents_of))
    target_lineage.add(execution_target_node_id)
    return tuple(
        node.id for node in graph.nodes if node.id in candidates and node.id in target_lineage
    )


def _optimiser_input_required_columns(config: dict[str, Any]) -> frozenset[str]:
    """Return the columns needed to validate and consume optimiser input."""
    objective = str(config["objective"])
    qid_col = str(config.get("quote_id", "quote_id"))
    mult_col = str(config.get("scenario_value", "scenario_value"))
    step_col = str(config.get("scenario_index", "scenario_index"))
    constraints = config.get("constraints") or {}
    constraint_cols = [str(cname) for cname in constraints]
    return frozenset({qid_col, step_col, mult_col, objective, *constraint_cols})


def _auto_range_input_required_columns(
    config: dict[str, Any],
    *,
    include_objective: bool = False,
) -> frozenset[str]:
    """Return optimiser input columns needed to validate auto-range data."""
    objective = str(config["objective"])
    qid_col = str(config.get("quote_id", "quote_id"))
    constraints = config.get("constraints") or {}
    constraint_cols = [str(cname) for cname in constraints]
    required = {qid_col, *constraint_cols}
    if include_objective:
        required.add(objective)
    return frozenset(required)


def _node_contract_outputs_column(node: GraphNode, column: str) -> bool:
    declared_raw = node.data.config.get("contract")
    if declared_raw is not None:
        try:
            declared = Contract.from_user_declared(declared_raw)
        except ValueError:
            return False
        if declared is not None and declared.outputs is not None:
            return column in declared.outputs

    try:
        outputs, _inputs = get_column_contract(node.data.nodeType, node.data.config)
    except (KeyError, ValueError):
        return False
    return outputs is not None and column in outputs


def _data_input_schema_has_column(node: GraphNode, column: str) -> bool:
    if node.data.nodeType != NodeType.DATA_INPUT:
        return False
    config = node.data.config
    try:
        from haute._builders import _configured_pipeline_dir
        from haute._input_providers import resolve_data_input

        lf = resolve_data_input(
            config,
            base_dir=_configured_pipeline_dir(),
            profile=ExecutionProfile.AUTO_RANGE,
        )
        return column in set(lf.collect_schema().names())
    except PUBLIC_CONTRACT_ERROR_TYPES:
        raise
    except (OSError, ValueError, BoundedMemoryUnsupportedError, SchemaMismatchError):
        return False


def _auto_range_data_input_has_objective(
    graph: PipelineGraph,
    data_input_id: str | None,
    objective: str,
) -> bool:
    if not data_input_id:
        return False
    node = graph.node_map.get(data_input_id)
    if node is None:
        return False
    return _data_input_schema_has_column(node, objective) or _node_contract_outputs_column(
        node,
        objective,
    )


def _auto_range_partition_count_from_config(config: dict[str, Any]) -> int:
    return _positive_int(
        config.get("auto_range_partition_count", _default_auto_range_partitions()),
        field="auto_range_partition_count",
    )


def _auto_range_timeout_from_config(config: dict[str, Any]) -> int:
    return _positive_int(
        config.get("auto_range_timeout", _default_auto_range_timeout()),
        field="auto_range_timeout",
    )


def _auto_range_required_columns_by_node(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
    *,
    mode: str,
) -> dict[str, frozenset[str]]:
    """Return lazy projection seeds for frontier auto-range.

    Auto-range consumes quote IDs plus constrained columns for range math. It
    also keeps the configured objective for input-contract validation when the
    data input is known to produce that column, then drops it before range
    derivation. When a configured ``data_input`` is a direct optimiser parent,
    seed that node so other optimiser parents do not inherit the projection.
    Ratebook factor-side requirements are routed by the shared optimiser
    parent-demand projection rule.
    """
    if mode not in {"online", "ratebook"}:
        return {}

    data_input_id = _resolve_optimiser_data_input_id(graph, node_id, config)
    required = _auto_range_input_required_columns(
        config,
        include_objective=_auto_range_data_input_has_objective(
            graph,
            data_input_id,
            str(config["objective"]),
        ),
    )
    if isinstance(data_input_id, str) and data_input_id:
        return {data_input_id: required}
    return {node_id: required}


def _optimiser_solve_required_columns_by_node(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> dict[str, frozenset[str]]:
    """Return lazy projection seeds for solve/estimate optimiser input.

    The optimiser node may also receive side inputs, for example ratebook
    banding factors.  Seed the proven data-input parent only, so side-input
    branches are not asked for solver columns they do not own.
    """
    required = _optimiser_input_required_columns(config)
    data_input_id = _resolve_optimiser_data_input_id(graph, node_id, config)
    if isinstance(data_input_id, str) and data_input_id:
        return {data_input_id: required}
    return {}


def _resolve_optimiser_data_input_id(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> str | None:
    """Return the optimiser dataframe input when it can be proven."""
    data_input_id = config.get("data_input")
    direct_parents = list(graph.parents_of.get(node_id, []))
    if isinstance(data_input_id, str) and data_input_id:
        if data_input_id not in set(direct_parents):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Configured optimiser data_input {data_input_id!r} is not connected "
                    f"to optimiser node {node_id!r}."
                ),
            )
        return data_input_id
    if len(direct_parents) == 1:
        return direct_parents[0]
    return None


def _resolve_online_auto_range_data_input_id(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> str | None:
    """Return the optimiser input node id that online auto-range consumes."""
    return _resolve_optimiser_data_input_id(graph, node_id, config)


def _looks_chunk_local_user_code(
    code: object,
    *,
    frame_names: Iterable[str],
) -> bool:
    """Return whether user code is eligible for chunk-local execution.

    The streaming path only uses code that can be proven row-local by a small
    AST allow-list.  Anything global, order-sensitive, or custom falls back to
    the existing full lazy path where Polars can execute the graph as authored.
    """
    from haute.chunking import is_chunk_local_polars_code

    return is_chunk_local_polars_code(code, frame_names=frame_names)


def _streaming_auto_range_node_is_eligible(
    node: GraphNode,
    *,
    frame_names: Iterable[str],
) -> bool:
    node_type = node.data.nodeType
    config = node.data.config
    if node_type not in _STREAMING_AUTO_RANGE_ALLOWED_NODE_TYPES:
        return False
    if node_type == NodeType.MODEL_SCORE:
        # Model-score post-processing and column renames can be arbitrary
        # user-defined transforms; keep them on the full lazy path for now.
        return (
            config.get("model_reuse_lifetime") == "batch"
            and not (config.get("code") or "").strip()
            and not config.get("column_renames")
        )
    if node_type == NodeType.SCENARIO_EXPANDER:
        return _looks_chunk_local_user_code(config.get("code"), frame_names=("df",))
    return _looks_chunk_local_user_code(config.get("code"), frame_names=frame_names)


def _upstream_slice_contains_node_type(
    graph: PipelineGraph,
    node_id: str,
    node_type: NodeType,
    *,
    resolve_node: Callable[[GraphNode, dict[str, GraphNode]], GraphNode],
) -> bool:
    node_map = graph.node_map
    stack = [node_id]
    seen: set[str] = set()
    while stack:
        current_id = stack.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        raw_node = node_map.get(current_id)
        if raw_node is not None and resolve_node(raw_node, node_map).data.nodeType == node_type:
            return True
        stack.extend(graph.parents_of.get(current_id, []))
    return False


def _build_streaming_auto_range_plan(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
    *,
    mode: str,
    required_columns_by_node: Mapping[str, Iterable[str]],
) -> _StreamingAutoRangePlan | None:
    """Build a strict online auto-range plan that chunks before expansion.

    The streaming plan is returned only when the shared chunk planner can prove
    the scenario suffix.  A planner rejection is surfaced loudly so eligible
    online auto-range shapes do not silently broaden into a high-memory path.
    """
    from haute._builders import resolve_instance_node

    if mode != "online":
        return None

    try:
        data_input_id = _resolve_online_auto_range_data_input_id(graph, node_id, config)
    except HTTPException:
        return None
    if not isinstance(data_input_id, str) or not data_input_id:
        return None

    node_map = graph.node_map
    downstream_to_upstream: list[str] = []
    current_id = data_input_id
    seen: set[str] = set()
    base_node_id: str | None = None
    scenario_node_id: str | None = None
    while True:
        if current_id in seen:
            return None
        seen.add(current_id)

        raw_node = node_map.get(current_id)
        if raw_node is None:
            return None
        node = resolve_instance_node(raw_node, node_map)
        parent_ids = graph.parents_of.get(current_id, [])
        if len(parent_ids) != 1:
            return None
        frame_names = [
            _sanitize_func_name(node_map[parent_id].data.label)
            for parent_id in parent_ids
            if parent_id in node_map
        ]
        if not _streaming_auto_range_node_is_eligible(node, frame_names=frame_names):
            return None

        downstream_to_upstream.append(current_id)
        if node.data.nodeType == NodeType.SCENARIO_EXPANDER:
            base_node_id = parent_ids[0]
            scenario_node_id = current_id
            break
        current_id = parent_ids[0]

    if base_node_id is None or scenario_node_id is None:
        return None
    if _upstream_slice_contains_node_type(
        graph,
        base_node_id,
        NodeType.SCENARIO_EXPANDER,
        resolve_node=resolve_instance_node,
    ):
        return None

    chain_node_ids = tuple(reversed(downstream_to_upstream))
    try:
        from haute.chunking import ChunkPlanRequest, chunk_plan

        explicit_chunk_size = _auto_range_explicit_chunk_size_from_config(config)
        generic_chunk_plan = chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id=data_input_id,
                chunk_start_node_id=base_node_id,
                chunk_size=explicit_chunk_size,
                target_chunk_bytes=(
                    None if explicit_chunk_size is not None else _auto_range_target_chunk_bytes()
                ),
                required_columns_by_node=required_columns_by_node,
                source="batch",
            )
        )
    except ChunkPlanUnsupportedError as exc:
        logger.info(
            "frontier_auto_range_generic_chunk_plan_unsupported",
            error=str(exc),
            node_id=node_id,
            data_input_id=data_input_id,
        )
        raise
    needed_by_node = generic_chunk_plan.required_columns_by_node
    base_needed = needed_by_node.get(base_node_id)

    required_output_columns_by_node = {
        chain_id: needed_by_node.get(chain_id) for chain_id in chain_node_ids
    }
    return _StreamingAutoRangePlan(
        base_node_id=base_node_id,
        scenario_node_id=scenario_node_id,
        chain_node_ids=chain_node_ids,
        required_output_columns_by_node=required_output_columns_by_node,
        base_required_columns=(frozenset(base_needed) if base_needed is not None else None),
        chunk_plan=generic_chunk_plan,
    )


def _apply_artifact_root() -> Path:
    return (Path(tempfile.gettempdir()) / _APPLY_ARTIFACT_ROOT_NAME).resolve()


def _ratebook_factors_artifact_root() -> Path:
    return (Path(tempfile.gettempdir()) / _RATEBOOK_FACTORS_ARTIFACT_ROOT_NAME).resolve()


def _prepare_apply_artifact_root() -> Path:
    root = _apply_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prepare_ratebook_factors_artifact_root() -> Path:
    root = _ratebook_factors_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact_stale_seconds() -> int:
    raw = os.environ.get(_ARTIFACT_STALE_SECONDS_ENV)
    if raw is None:
        return _DEFAULT_ARTIFACT_STALE_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{_ARTIFACT_STALE_SECONDS_ENV} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{_ARTIFACT_STALE_SECONDS_ENV} must be a non-negative integer")
    return value


def reap_stale_optimiser_artifacts(
    stale_after_seconds: int,
) -> dict[str, dict[str, int]]:
    """Reap stale marked artifacts from the optimiser's dedicated roots only."""
    reports: dict[str, dict[str, int]] = {}
    for name, root, owner in (
        ("apply", _apply_artifact_root(), _APPLY_ARTIFACT_OWNER),
        ("ratebook_factors", _ratebook_factors_artifact_root(), _RATEBOOK_FACTORS_ARTIFACT_OWNER),
    ):
        if root.is_dir():
            reports[name] = reap_stale_artifact_directories(root, owner, stale_after_seconds)
    logger.info("optimiser_artifact_reap_completed", reports=reports)
    return reports


def _validate_server_owned_parquet_handle(
    handle: dict[str, Any],
    *,
    kind: str,
    root: Path,
    directory_prefix: str,
    filename: str,
    description: str,
) -> tuple[Path, Path]:
    """Return validated ``(path, directory)`` for a server-owned parquet artifact."""
    if handle.get("kind") != kind:
        raise ValueError(f"Invalid {description} artifact handle.")
    if handle.get("version") != _ARTIFACT_HANDLE_VERSION:
        raise ValueError(f"Unsupported {description} artifact handle.")
    if handle.get("format") != "parquet":
        raise ValueError(f"Unsupported {description} artifact format.")

    raw_directory = handle.get("directory")
    if not isinstance(raw_directory, str) or not raw_directory:
        raise ValueError(f"{description} artifact handle has no directory.")
    raw_path = handle.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"{description} artifact handle has no path.")
    if "\x00" in raw_directory or "\x00" in raw_path:
        raise ValueError(f"{description} artifact handle contains an invalid path.")

    directory_input = Path(raw_directory)
    path_input = Path(raw_path)
    if not directory_input.is_absolute() or not path_input.is_absolute():
        raise ValueError(f"{description} artifact handle must use absolute paths.")

    directory = directory_input.resolve(strict=directory_input.exists())
    artifact_path = path_input.resolve(strict=path_input.exists())

    if not directory.is_relative_to(root):
        raise ValueError(f"{description} artifact directory is outside the artifact root.")
    if directory.parent != root or not directory.name.startswith(directory_prefix):
        raise ValueError(f"{description} artifact directory is invalid.")
    if artifact_path.parent != directory:
        raise ValueError(f"{description} artifact path is outside its directory.")
    if artifact_path.name != filename:
        raise ValueError(f"{description} artifact path is invalid.")
    return artifact_path, directory


def _validate_apply_result_artifact_handle(handle: dict[str, Any]) -> tuple[Path, Path]:
    """Return validated ``(path, directory)`` for a server-owned apply artifact."""
    return _validate_server_owned_parquet_handle(
        handle,
        kind=_APPLY_RESULT_HANDLE_KIND,
        root=_apply_artifact_root(),
        directory_prefix=_APPLY_ARTIFACT_DIR_PREFIX,
        filename=_APPLY_RESULT_FILENAME,
        description="Optimiser apply",
    )


def _validate_ratebook_factors_artifact_handle(handle: dict[str, Any]) -> tuple[Path, Path]:
    """Return validated ``(path, directory)`` for a server-owned ratebook factor artifact."""
    return _validate_server_owned_parquet_handle(
        handle,
        kind=_RATEBOOK_FACTORS_HANDLE_KIND,
        root=_ratebook_factors_artifact_root(),
        directory_prefix=_RATEBOOK_FACTORS_ARTIFACT_DIR_PREFIX,
        filename=_RATEBOOK_FACTORS_FILENAME,
        description="Optimiser ratebook factors",
    )


def _persist_apply_result_artifact(solve_result: SolveResultLike) -> dict[str, Any] | None:
    """Persist the large apply/detail dataframe behind an explicit handle."""
    if not hasattr(solve_result, "dataframe"):
        return None

    import polars as pl

    df = solve_result.dataframe
    if not isinstance(df, pl.DataFrame):
        return None

    artifact_dir = create_owned_artifact_directory(
        _prepare_apply_artifact_root(), _APPLY_ARTIFACT_DIR_PREFIX, _APPLY_ARTIFACT_OWNER
    )
    artifact_path = artifact_dir / _APPLY_RESULT_FILENAME
    try:
        df.write_parquet(artifact_path)
        row_count = len(df)
    except BaseException:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise
    try:
        cast(Any, solve_result).dataframe = None
    except Exception:
        logger.debug(
            "optimiser_apply_dataframe_reference_not_clearable",
            solve_result_type=type(solve_result).__name__,
        )

    return {
        "kind": _APPLY_RESULT_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
        "path": str(artifact_path),
        "directory": str(artifact_dir),
        "row_count": row_count,
    }


def _persist_ratebook_factors_artifact(factors_df: Any) -> dict[str, Any] | None:
    """Persist ratebook factors behind an explicit handle instead of the job dict."""
    if factors_df is None:
        return None

    import polars as pl

    if not isinstance(factors_df, pl.DataFrame):
        return None

    artifact_dir = create_owned_artifact_directory(
        _prepare_ratebook_factors_artifact_root(),
        _RATEBOOK_FACTORS_ARTIFACT_DIR_PREFIX,
        _RATEBOOK_FACTORS_ARTIFACT_OWNER,
    )
    artifact_path = artifact_dir / _RATEBOOK_FACTORS_FILENAME
    try:
        factors_df.write_parquet(artifact_path)
        metadata = read_parquet_metadata(artifact_path)
        row_count = int(metadata["row_count"])
        size_bytes = int(metadata["size_bytes"])
        columns = list(factors_df.columns)
    except BaseException:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise

    return {
        "kind": _RATEBOOK_FACTORS_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
        "path": str(artifact_path),
        "directory": str(artifact_dir),
        "row_count": row_count,
        "size_bytes": size_bytes,
        "columns": columns,
    }


def _persist_ratebook_factors_lazy_artifact(
    factors_lf: Any,
    *,
    streaming_chunk_size: int | None = None,
) -> dict[str, Any]:
    """Persist projected ratebook factors without collecting them into memory."""
    artifact_dir = create_owned_artifact_directory(
        _prepare_ratebook_factors_artifact_root(),
        _RATEBOOK_FACTORS_ARTIFACT_DIR_PREFIX,
        _RATEBOOK_FACTORS_ARTIFACT_OWNER,
    )
    artifact_path = artifact_dir / _RATEBOOK_FACTORS_FILENAME
    try:
        bounded_sink(
            factors_lf,
            artifact_path,
            streaming_chunk_size=streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
        )
        metadata = read_parquet_metadata(artifact_path)
        row_count = int(metadata["row_count"])
        size_bytes = int(metadata["size_bytes"])
        columns = list(cast(Mapping[str, Any], metadata["columns"]).keys())
    except BaseException:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise

    return {
        "kind": _RATEBOOK_FACTORS_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
        "path": str(artifact_path),
        "directory": str(artifact_dir),
        "row_count": row_count,
        "size_bytes": size_bytes,
        "columns": columns,
    }


def _cleanup_apply_result_artifact(handle: dict[str, Any]) -> None:
    """Remove a newly-created apply artifact that no job owns."""
    _artifact_path, artifact_dir = _validate_apply_result_artifact_handle(handle)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)


def _cleanup_ratebook_factors_artifact(handle: dict[str, Any]) -> None:
    """Remove a persisted ratebook factors artifact owned by an expired job."""
    _artifact_path, artifact_dir = _validate_ratebook_factors_artifact_handle(handle)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)


def _log_artifact_load_failure(
    event: str,
    handle: Mapping[str, Any],
    exc: BaseException,
) -> None:
    logger.error(
        event,
        path=str(handle.get("path") or "<unknown>"),
        error=str(exc),
        exc_info=True,
    )


def _load_apply_result_artifact(handle: dict[str, Any]) -> Any:
    """Load a persisted optimiser apply dataframe from a validated handle."""
    import polars as pl

    try:
        artifact_path, _artifact_dir = _validate_apply_result_artifact_handle(handle)
    except ValueError as exc:
        _log_artifact_load_failure("optimiser_apply_artifact_validation_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "Optimiser apply artifact reference is invalid. Re-run the solve to regenerate it."
            ),
        ) from exc
    if not artifact_path.is_file():
        logger.warning("optimiser_apply_artifact_missing", path=str(artifact_path))
        raise HTTPException(
            status_code=410,
            detail=(
                "Optimiser apply artifact is no longer available. "
                "Re-run the solve to regenerate it."
            ),
        )

    try:
        return pl.read_parquet(artifact_path)
    except Exception as exc:
        _log_artifact_load_failure("optimiser_apply_artifact_read_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail="Optimiser apply artifact is corrupt. Re-run the solve to regenerate it.",
        ) from exc


def _load_ratebook_factors_artifact(handle: dict[str, Any]) -> Any:
    """Load persisted ratebook factors from a validated handle."""
    import polars as pl

    try:
        artifact_path, _artifact_dir = _validate_ratebook_factors_artifact_handle(handle)
    except ValueError as exc:
        _log_artifact_load_failure("optimiser_ratebook_artifact_validation_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail="Optimiser ratebook factor artifact reference is invalid. Re-run the solve.",
        ) from exc
    if not artifact_path.is_file():
        logger.warning("optimiser_ratebook_artifact_missing", path=str(artifact_path))
        raise HTTPException(
            status_code=410,
            detail="Optimiser ratebook factor artifact is no longer available. Re-run the solve.",
        )
    try:
        return pl.read_parquet(artifact_path)
    except Exception as exc:
        _log_artifact_load_failure("optimiser_ratebook_artifact_read_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail="Optimiser ratebook factor artifact is corrupt. Re-run the solve.",
        ) from exc


def _scan_ratebook_factors_artifact(handle: dict[str, Any]) -> Any:
    """Return a lazy scan for a validated ratebook factor artifact."""
    import polars as pl

    try:
        artifact_path, _artifact_dir = _validate_ratebook_factors_artifact_handle(handle)
    except ValueError as exc:
        _log_artifact_load_failure(
            "optimiser_ratebook_artifact_scan_validation_failed",
            handle,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail="Optimiser ratebook factor artifact reference is invalid. Re-run the solve.",
        ) from exc
    if not artifact_path.is_file():
        logger.warning("optimiser_ratebook_artifact_scan_missing", path=str(artifact_path))
        raise HTTPException(
            status_code=410,
            detail="Optimiser ratebook factor artifact is no longer available. Re-run the solve.",
        )
    try:
        return pl.scan_parquet(artifact_path)
    except Exception as exc:
        _log_artifact_load_failure("optimiser_ratebook_artifact_scan_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail="Optimiser ratebook factor artifact is corrupt. Re-run the solve.",
        ) from exc


register_artifact_cleaner(_APPLY_RESULT_HANDLE_KIND, _cleanup_apply_result_artifact)
register_artifact_cleaner(_RATEBOOK_FACTORS_HANDLE_KIND, _cleanup_ratebook_factors_artifact)


def _cleanup_orphan_apply_result_artifact(
    handle: dict[str, Any],
    *,
    job_id: str,
    event: str,
) -> None:
    """Best-effort cleanup for apply artifacts that were never attached to a job."""
    try:
        if handle.get("kind") == _RATEBOOK_FACTORS_HANDLE_KIND:
            _cleanup_ratebook_factors_artifact(handle)
        else:
            _cleanup_apply_result_artifact(handle)
    except Exception as cleanup_exc:
        raw_path = handle.get("directory") or handle.get("path") or "<unknown>"
        logger.warning(
            event,
            job_id=job_id,
            path=str(raw_path),
            error=str(cleanup_exc),
            exc_info=True,
        )


def _find_optimiser_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find and validate an optimiser node in the graph."""
    return find_typed_node(graph, node_id, NodeType.OPTIMISER, "optimiser")


def _compute_scenario_value_stats(
    solve_result: SolveResultLike,
) -> tuple[
    dict[str, float] | None,
    dict[str, list[int] | list[float]] | None,
]:
    """Compute scenario value distribution statistics and histogram from solve result."""
    if not hasattr(solve_result, "dataframe"):
        return None, None
    df = solve_result.dataframe
    if "optimal_scenario_value" not in df.columns:
        return None, None

    col = df["optimal_scenario_value"]
    n = len(col)
    if n == 0:
        return None, None
    # polars' sample std (ddof=1) is undefined (null) for a single quote and
    # would crash the float() cast after the solve already succeeded. A
    # complete one-quote result set has exactly zero spread, so 0.0 is the
    # true population statistic for n == 1 — not a fabricated estimate
    # (mirrors the degenerate-input convention used by the gini metrics).
    # The response schema (OptimiserScenarioValueStats.std) and the frontend
    # guard both require ``std`` to be a number, so omitting or nulling just
    # this field is not a shape the contract permits.
    stats = {
        "mean": float(col.mean()),
        "std": 0.0 if n == 1 else float(col.std()),
        "min": float(col.min()),
        "max": float(col.max()),
        "p5": float(col.quantile(0.05)),
        "p25": float(col.quantile(0.25)),
        "p50": float(col.quantile(0.50)),
        "p75": float(col.quantile(0.75)),
        "p95": float(col.quantile(0.95)),
        "pct_increase": float((col > 1.0).sum() / n) if n else 0.0,
        "pct_decrease": float((col < 1.0).sum() / n) if n else 0.0,
    }

    vals = col.to_numpy()
    counts, edges = np.histogram(vals, bins=_HISTOGRAM_BINS)
    histogram: dict[str, list[int] | list[float]] = {
        "counts": [int(c) for c in counts],
        "edges": [float(e) for e in edges],
    }
    return stats, histogram


@require_solver_worker_context
def _compute_frontier(
    solver: Any,
    quote_grid: QuoteGrid,
    *,
    mode: str,
    ratebook_factors: Any | None,
    threshold_ranges: dict[str, tuple[float, float]],
    n_points_per_dim: int,
    factor_columns: list[list[str]] | None = None,
    initial_lambdas: dict[str, float] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> Any:
    """Call the mode-specific frontier API."""
    if check_cancelled is not None:
        check_cancelled()
    if mode == "ratebook":
        if ratebook_factors is None:
            raise RuntimeError("Ratebook frontier requires prepared factor contexts.")
        frontier_kwargs: dict[str, Any] = {
            "threshold_ranges": threshold_ranges,
            "n_points_per_dim": n_points_per_dim,
        }
        if factor_columns is not None:
            frontier_kwargs["factor_columns"] = factor_columns
        if initial_lambdas is not None:
            frontier_kwargs["initial_lambdas"] = initial_lambdas
        result = solver.frontier(
            quote_grid,
            ratebook_factors,
            **frontier_kwargs,
        )
        if check_cancelled is not None:
            check_cancelled()
        return result
    frontier_kwargs = {
        "threshold_ranges": threshold_ranges,
        "n_points_per_dim": n_points_per_dim,
    }
    if initial_lambdas is not None:
        frontier_kwargs["initial_lambdas"] = initial_lambdas
    result = solver.frontier(quote_grid, **frontier_kwargs)
    if check_cancelled is not None:
        check_cancelled()
    return result


def _auto_frontier_ranges_from_config(config: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Build absolute frontier ranges from canonical per-constraint config."""
    constraints = config.get("constraints") or {}
    if not constraints:
        return {}

    configured_ranges = config.get("frontier_ranges")
    if configured_ranges is not None:
        if not isinstance(configured_ranges, dict):
            raise ValueError("frontier_ranges must be an object keyed by constraint name.")
        ranges: dict[str, tuple[float, float]] = {}
        for cname in constraints:
            if cname not in configured_ranges:
                raise ValueError(f"frontier_ranges is missing a range for constraint {cname!r}.")
            ranges[str(cname)] = _normalise_frontier_range_pair(
                configured_ranges[cname],
                field=f"frontier_ranges.{cname}",
            )
        return ranges

    raise ValueError("frontier_ranges must provide min and max for each constraint.")


class _ScenarioFrontierRangeAccumulator:
    """Accumulate per-quote scenario extrema through disk-backed buckets."""

    def __init__(
        self,
        *,
        quote_id_col: str,
        constraint_cols: list[str],
        partition_count: int,
        parts_root: Path,
    ) -> None:
        import polars as pl

        self.quote_id_col = quote_id_col
        self.constraint_cols = list(constraint_cols)
        self.partition_count = partition_count
        self.parts_root = parts_root
        self.bucket_files: dict[int, list[Path]] = {}
        self.row_count = 0
        self.null_quote_id_count = 0
        self.aliases: dict[str, tuple[str, str]] = {}
        self.aggregate_exprs = []
        self.combine_exprs = []
        self.bucket_total_exprs = []
        for idx, cname in enumerate(self.constraint_cols):
            min_alias = f"__haute_frontier_min_{idx}"
            max_alias = f"__haute_frontier_max_{idx}"
            self.aliases[cname] = (min_alias, max_alias)
            self.aggregate_exprs.extend(
                [
                    pl.col(cname).min().alias(min_alias),
                    pl.col(cname).max().alias(max_alias),
                ]
            )
            self.combine_exprs.extend(
                [
                    pl.col(min_alias).min().alias(min_alias),
                    pl.col(max_alias).max().alias(max_alias),
                ]
            )
            self.bucket_total_exprs.extend(
                [
                    pl.col(min_alias).sum().alias(min_alias),
                    pl.col(max_alias).sum().alias(max_alias),
                ]
            )

    def add_batch(self, batch: pl.DataFrame, *, batch_index: int) -> None:
        import polars as pl

        if batch.height == 0:
            return
        self.row_count += batch.height
        null_count = int(batch[self.quote_id_col].null_count())
        if null_count > 0:
            self.null_quote_id_count += null_count
            return

        partial = (
            batch.group_by(self.quote_id_col)
            .agg(self.aggregate_exprs)
            .with_columns(
                (pl.col(self.quote_id_col).hash(seed=0) % self.partition_count)
                .cast(pl.UInt32)
                .alias(_AUTO_RANGE_BUCKET_COLUMN)
            )
        )
        bucket_ids = (
            partial.select(_AUTO_RANGE_BUCKET_COLUMN)
            .unique(maintain_order=False)
            .get_column(_AUTO_RANGE_BUCKET_COLUMN)
            .to_list()
        )
        for raw_bucket in bucket_ids:
            bucket = int(raw_bucket)
            bucket_df = partial.filter(pl.col(_AUTO_RANGE_BUCKET_COLUMN) == bucket).drop(
                _AUTO_RANGE_BUCKET_COLUMN
            )
            bucket_dir = self.parts_root / f"bucket_{bucket:04d}"
            bucket_dir.mkdir(exist_ok=True)
            part_path = bucket_dir / f"part_{batch_index:08d}.parquet"
            bucket_df.write_parquet(part_path, compression="lz4")
            self.bucket_files.setdefault(bucket, []).append(part_path)

    def finish(
        self,
        *,
        check_cancelled: Callable[[], None] | None = None,
        execution_context: ExecutionContext | None = None,
        streaming_chunk_size: int | None = None,
    ) -> dict[str, dict[str, float]]:
        import polars as pl

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

        range_totals = {cname: {"min": 0.0, "max": 0.0} for cname in self.constraint_cols}
        for paths in self.bucket_files.values():
            if check_cancelled is not None:
                check_cancelled()
            if execution_context is not None:
                execution_context.checkpoint(label="frontier_range_bucket_start")
            with _execution_stage(
                execution_context,
                "frontier_range_bucket_reduce",
            ):
                bucket_totals_lf = (
                    pl.scan_parquet([str(path) for path in paths])
                    .group_by(self.quote_id_col)
                    .agg(self.combine_exprs)
                    .select(self.bucket_total_exprs)
                )
                chunk_size = streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
                with temporary_streaming_chunk_size(chunk_size):
                    bucket_totals = streaming_collect(
                        bucket_totals_lf,
                        execution_context=execution_context,
                    )
            if execution_context is not None:
                execution_context.checkpoint(label="frontier_range_bucket_done")
            if bucket_totals.height != 1:
                raise ValueError("Unable to estimate frontier ranges from a scenario bucket.")
            row = bucket_totals.row(0, named=True)
            for cname, (min_alias, max_alias) in self.aliases.items():
                range_totals[cname]["min"] += float(row[min_alias])
                range_totals[cname]["max"] += float(row[max_alias])

        ranges: dict[str, dict[str, float]] = {}
        for cname, values in range_totals.items():
            min_value = values["min"]
            max_value = values["max"]
            if not np.isfinite(min_value) or not np.isfinite(max_value):
                raise ValueError(f"Estimated frontier range for {cname!r} is not finite.")
            if min_value > max_value:
                raise ValueError(f"Estimated frontier range for {cname!r} is invalid.")
            ranges[cname] = {"min": min_value, "max": max_value}
        return ranges


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

    chunk_size: int = dataclasses.field(default_factory=_default_auto_range_chunk_size)
    partition_count: int = dataclasses.field(default_factory=_default_auto_range_partitions)
    execution_context: ExecutionContext | None = None
    streaming_chunk_size: int | None = None


def _estimate_scenario_frontier_ranges(
    ctx: FrontierAutoRangeContext,
    *,
    scored_lf: Any,
    quote_id_col: str,
    constraint_cols: list[str],
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, dict[str, float]]:
    """Return exact online achievable min/max totals from the scenario frame.

    For each constraint, each quote can independently choose the scenario that
    minimises or maximises that constraint total.  The input is read in bounded
    batches, reduced to per-batch quote extrema, then hash-partitioned to
    temporary parquet files so quotes split across read batches are recombined
    without one global per-quote aggregate table.
    """
    import polars as pl

    if not constraint_cols:
        return {}

    if check_cancelled is not None:
        check_cancelled()
    chunk_size = _positive_int(ctx.chunk_size, field="chunk_size")
    partition_count = _positive_int(ctx.partition_count, field="partition_count")
    execution_context = ctx.execution_context
    streaming_chunk_size = ctx.streaming_chunk_size
    selected_lf = scored_lf.select(
        [
            pl.col(quote_id_col).cast(pl.String).alias(quote_id_col),
            *[pl.col(cname) for cname in constraint_cols],
        ]
    )

    with tempfile.TemporaryDirectory(prefix="haute_frontier_range_parts_") as raw_dir:
        accumulator = _ScenarioFrontierRangeAccumulator(
            quote_id_col=quote_id_col,
            constraint_cols=constraint_cols,
            partition_count=partition_count,
            parts_root=Path(raw_dir),
        )
        # ``chunk_size`` is the per-batch row count for the auto-range reducer;
        # ``streaming_chunk_size`` is the ambient Polars streaming chunk size
        # that drives the underlying batched scan/collect pipeline.
        with temporary_streaming_chunk_size(streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE):
            for batch_index, batch in enumerate(
                bounded_collect_batches(
                    selected_lf,
                    chunk_size=chunk_size,
                    maintain_order=False,
                    execution_context=execution_context,
                    stage_name="frontier_range_collect_batch",
                )
            ):
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
            streaming_chunk_size=streaming_chunk_size,
        )


_RATEBOOK_FACTOR_LEVEL_SEPARATOR = "\x1f"
_RATEBOOK_FACTOR_LEVEL_ORDER_KEY = "factor_level_order"


def _ratebook_factor_table_name(columns: list[str]) -> str:
    return ":".join(columns)


def _ratebook_factor_level_key(
    values: list[Any],
    dtypes: list[pl.DataType],
) -> str:
    """Canonical level key for one observed factor-level tuple (3b.10).

    Components are canonicalised through the shared
    :func:`haute._rating.normalise_rating_key`, so save-time level keys agree
    with the keys the apply-side rating join derives from frame values
    (Float64 ``25.0`` -> ``"25"``; strings stay verbatim).
    """
    parts: list[str] = []
    if len(dtypes) != len(values):
        raise ValueError("Ratebook factor values and dtypes must have the same length.")
    for value, dtype in zip(values, dtypes):
        canonical = normalise_rating_key(value, dtype)
        if canonical is None:
            raise ValueError("Ratebook factor counts cannot be computed with null factor levels.")
        parts.append(canonical)
    return _RATEBOOK_FACTOR_LEVEL_SEPARATOR.join(parts)


def _canonical_ratebook_table_level(
    name: str,
    level: Any,
    level_counts: dict[str, int],
    dtypes: list[pl.DataType],
) -> str:
    """Save-time canonical key for a solver-emitted factor level (3b.10).

    price-contour stringifies typed factor values, which widens Float32 values
    to Python Float64 representations. Reconstruct each emitted component
    through the exact originating dtype from the solved factor artifact before
    canonicalisation. ``level_counts`` was built from that same typed artifact,
    so the resulting key must exist exactly; no candidate search or dtype
    inference is permitted.
    """
    if len(dtypes) == 1:
        components = [level]
    elif isinstance(level, str):
        components = level.split(_RATEBOOK_FACTOR_LEVEL_SEPARATOR)
    else:
        raise ValueError(
            f"Ratebook factor table {name!r} has a non-string composite level {level!r}."
        )
    if len(components) != len(dtypes):
        raise ValueError(
            f"Ratebook factor table {name!r} level {level!r} has {len(components)} "
            f"component(s), expected {len(dtypes)}."
        )
    canonical = _ratebook_factor_level_key(components, dtypes)
    if canonical not in level_counts:
        raise ValueError(
            f"Ratebook factor counts missing for level {level!r} in factor table {name!r}."
        )
    return canonical


def _append_unique_factor_level(levels: list[str], seen: set[str], value: object) -> None:
    if value is None or value == "":
        return
    level = str(value)
    if level in seen:
        return
    seen.add(level)
    levels.append(level)


def _banding_rule_output_level(rule: dict[str, Any]) -> object:
    """Return the rule's output level (``assignment`` or ``label``)."""
    if "assignment" in rule:
        return rule["assignment"]
    return rule["label"]


def _find_node_by_id(graph: PipelineGraph, node_id: str) -> GraphNode | None:
    """Return the graph node with the given id, or ``None`` if absent."""
    return next((node for node in graph.nodes if node.id == node_id), None)


def _ratebook_factor_level_order(
    graph: PipelineGraph,
    config: dict[str, Any],
) -> dict[str, list[str]]:
    """Extract factor-level display order from the configured banding source."""
    banding_source_id = config.get("banding_source")
    if not isinstance(banding_source_id, str) or not banding_source_id:
        return {}

    banding_node = _find_node_by_id(graph, banding_source_id)
    if banding_node is None or banding_node.data.nodeType != NodeType.BANDING:
        return {}

    order: dict[str, list[str]] = {}
    for factor in normalise_banding_factors(banding_node.data.config):
        output_column = factor.get("outputColumn")
        if not isinstance(output_column, str) or not output_column:
            continue

        levels: list[str] = []
        seen: set[str] = set()
        rules = factor.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, dict):
                    _append_unique_factor_level(levels, seen, _banding_rule_output_level(rule))
        _append_unique_factor_level(levels, seen, factor.get("default"))
        if levels:
            order[output_column] = levels
    return order


def _ratebook_factor_table_level_order(
    table_name: str,
    factor_level_order: dict[str, list[str]],
) -> list[str]:
    direct_order = factor_level_order.get(table_name)
    if direct_order is not None:
        return direct_order

    columns = table_name.split(":")
    if len(columns) <= 1:
        return []

    component_orders = [factor_level_order.get(column) for column in columns]
    if any(order is None for order in component_orders):
        return []
    populated_orders = [order for order in component_orders if order is not None]
    return [_RATEBOOK_FACTOR_LEVEL_SEPARATOR.join(values) for values in product(*populated_orders)]


def _ratebook_factor_table_position(
    table_name: str,
    factor_level_order: dict[str, list[str]],
) -> tuple[int, ...] | None:
    factor_positions = {name: index for index, name in enumerate(factor_level_order)}
    direct_position = factor_positions.get(table_name)
    if direct_position is not None:
        return (direct_position,)

    columns = table_name.split(":")
    if len(columns) <= 1:
        return None
    positions = [factor_positions.get(column) for column in columns]
    if any(position is None for position in positions):
        return None
    return tuple(position for position in positions if position is not None)


def _ratebook_factor_table_sort_key(
    index_and_item: tuple[int, tuple[Any, Any]],
    factor_level_order: dict[str, list[str]],
) -> tuple[int, tuple[int, ...]]:
    original_index, (name, _table) = index_and_item
    fallback = (1, (original_index,))
    if not isinstance(name, str):
        return fallback
    position = _ratebook_factor_table_position(name, factor_level_order)
    return (0, position) if position is not None else fallback


def _ratebook_factor_level_counts(
    factors_df: Any | None,
    factor_columns: list[list[str]] | None,
    *,
    streaming_chunk_size: int | None = None,
) -> dict[str, dict[str, int]]:
    """Count quote exposure for each ratebook factor level.

    Table keys mirror price-contour's factor table output: single-column
    groups use the column name, composite groups join column names with
    ":".  Level keys are CANONICAL (3b.10): each component goes through the
    shared ``normalise_rating_key`` (joined with the unit separator), so the
    saved keys agree with what the apply-side rating join derives from frame
    values.  Two raw levels collapsing to one canonical key — possible only
    when a source column mixes value types — fail loudly, never merge.
    """
    import polars as pl

    if factors_df is None:
        return {}

    is_lazy = isinstance(factors_df, pl.LazyFrame)
    schema = factors_df.collect_schema() if is_lazy else factors_df.schema
    schema_names = set(schema.names())
    counts: dict[str, dict[str, int]] = {}
    for columns in factor_columns or []:
        if not columns:
            continue
        missing = [column for column in columns if column not in schema_names]
        if missing:
            raise ValueError(
                "Ratebook factor count columns are missing from aligned factors dataframe: "
                f"{missing}"
            )
        table_name = _ratebook_factor_table_name(columns)
        grouped = factors_df.group_by(columns).agg(pl.len().alias("quote_count"))
        if is_lazy:
            chunk_size = streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
            with temporary_streaming_chunk_size(chunk_size):
                count_rows = streaming_collect(grouped).to_dicts()
        else:
            count_rows = grouped.to_dicts()
        table_counts: dict[str, int] = {}
        level_sources: dict[str, list[Any]] = {}
        column_dtypes = [schema[column] for column in columns]
        for row in count_rows:
            values = [row[column] for column in columns]
            level_key = _ratebook_factor_level_key(values, column_dtypes)
            if level_key in table_counts:
                raise ValueError(
                    f"Ratebook factor levels {level_sources[level_key]!r} and {values!r} in "
                    f"factor table {table_name!r} both canonicalise to {level_key!r}; the "
                    "source column mixes value types. Cast it to a single type upstream."
                )
            table_counts[level_key] = int(row["quote_count"])
            level_sources[level_key] = values
        counts[table_name] = table_counts
    return counts


def _ratebook_factor_dtypes(
    factors_df: Any | None,
    factor_columns: list[list[str]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Describe every solved factor table's ordered originating dtypes."""
    import polars as pl

    if factors_df is None:
        return {}
    schema = (
        factors_df.collect_schema() if isinstance(factors_df, pl.LazyFrame) else factors_df.schema
    )
    schema_names = set(schema.names())
    result: dict[str, list[dict[str, Any]]] = {}
    for columns in factor_columns or []:
        if not columns:
            continue
        missing = [column for column in columns if column not in schema_names]
        if missing:
            raise ValueError(
                "Ratebook factor dtype columns are missing from aligned factors "
                f"dataframe: {missing}"
            )
        result[_ratebook_factor_table_name(columns)] = [
            {
                "column": column,
                "dtype": rating_dtype_descriptor(schema[column]),
            }
            for column in columns
        ]
    return result


def _ratebook_factor_level_counts_from_artifact(
    handle: dict[str, Any],
    factor_columns: list[list[str]] | None,
    *,
    streaming_chunk_size: int | None = None,
) -> dict[str, dict[str, int]]:
    """Count ratebook factor levels from the persisted factor artifact lazily."""
    return _ratebook_factor_level_counts(
        _scan_ratebook_factors_artifact(handle),
        factor_columns,
        streaming_chunk_size=streaming_chunk_size,
    )


def _ratebook_factor_dtypes_from_artifact(
    handle: dict[str, Any],
    factor_columns: list[list[str]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Read ratebook dtype metadata from the persisted solved-factor schema."""
    return _ratebook_factor_dtypes(
        _scan_ratebook_factors_artifact(handle),
        factor_columns,
    )


def _quote_grid_quote_ids(quote_grid: Any) -> list[str]:
    """Return quote ids from a price-contour QuoteGrid as concrete strings."""
    return [str(quote_id) for quote_id in quote_grid.quote_ids]


def _ratebook_factor_artifact_quote_id(
    handle: dict[str, Any],
    config: Mapping[str, Any],
) -> str:
    """Resolve the quote-id column available in a ratebook factor artifact."""
    columns = handle.get("columns")
    available = set(columns) if isinstance(columns, list) else set()
    qid_col = str(config.get("quote_id", "quote_id"))
    if qid_col in available:
        return qid_col
    raise RuntimeError(f"Ratebook banding source must include quote id column {qid_col!r}.")


def _build_ratebook_factor_contexts(
    handle: dict[str, Any],
    quote_grid: Any,
    config: Mapping[str, Any],
    factor_columns: list[list[str]],
    *,
    chunk_decision: _ChunkSizeDecision | None = None,
) -> Any:
    """Build price-contour factor contexts from a persisted ratebook factor artifact."""
    from price_contour import build_ratebook_factor_contexts_from_parquet_chunked

    artifact_path, _artifact_dir = _validate_ratebook_factors_artifact_handle(handle)
    quote_ids = _quote_grid_quote_ids(quote_grid)
    try:
        if chunk_decision is None:
            chunk_decision = _chunk_size_decision_for_parquet(
                config,
                artifact_path,
                source="ratebook_factor_contexts",
            )
        chunk_size = chunk_decision.chunk_size
    except ValueError as exc:
        raise RuntimeError(f"Ratebook factor context chunk sizing failed: {exc}") from exc
    return build_ratebook_factor_contexts_from_parquet_chunked(
        str(artifact_path),
        factor_columns,
        chunk_size,
        quote_id=_ratebook_factor_artifact_quote_id(handle, config),
        expected_quote_ids=quote_ids,
        expected_n_quotes=quote_grid.n_quotes,
    )


def _sort_ratebook_factor_tables(
    factor_tables: dict[Any, Any],
    factor_level_order: dict[str, list[str]],
) -> list[tuple[Any, Any]]:
    """Order factor tables by the configured banding-rule order.

    Tables not present in ``factor_level_order`` retain their original
    insertion order behind the configured ones (fallback bucket ``1``).
    """
    return [
        item
        for _index, item in sorted(
            enumerate(factor_tables.items()),
            key=lambda item: _ratebook_factor_table_sort_key(item, factor_level_order),
        )
    ]


def _serialise_ratebook_factor_table_rows(
    name: str,
    table: dict[Any, Any],
    level_counts: dict[str, int],
    factor_level_order: dict[str, list[str]],
    factor_dtypes: list[pl.DataType],
) -> list[dict[str, Any]]:
    """Serialise one factor table's rows, ordered by configured level order.

    Levels not present in the configured order fall through to insertion order
    behind the ordered ones, matching the table-level ordering convention.

    Saved ``__factor_group__`` labels are CANONICAL (3b.10): solver-emitted
    levels are translated through :func:`_canonical_ratebook_table_level`
    using the solved frame's exact ordered factor dtypes, so a Float32 value
    widened by Python is reconstructed before the apply key is saved. Two
    emitted levels collapsing to one canonical key fail loudly —
    last-writer-wins would silently drop a solved rate.
    """
    configured_level_order = _ratebook_factor_table_level_order(name, factor_level_order)
    level_positions = {level: index for index, level in enumerate(configured_level_order)}
    ordered_rows: list[tuple[tuple[int, int], dict[str, Any]]] = []
    emitted_by_canonical: dict[str, Any] = {}
    for original_index, (level, scenario_value) in enumerate(table.items()):
        level_key = _canonical_ratebook_table_level(
            name,
            level,
            level_counts,
            factor_dtypes,
        )
        if level_key in emitted_by_canonical:
            raise ValueError(
                f"Ratebook factor table {name!r} levels {emitted_by_canonical[level_key]!r} "
                f"and {level!r} both canonicalise to {level_key!r}; the solver input mixed "
                "value types in one factor column. Cast it to a single type and re-solve."
            )
        emitted_by_canonical[level_key] = level
        scenario_float = float(scenario_value)
        if not np.isfinite(scenario_float):
            raise ValueError(f"Ratebook factor table {name!r} contains a non-finite rate.")
        sort_key = (
            (0, level_positions[level_key]) if level_key in level_positions else (1, original_index)
        )
        ordered_rows.append(
            (
                sort_key,
                {
                    "__factor_group__": level_key,
                    "optimal_scenario_value": scenario_float,
                    # The canonical key is always counted: the translation
                    # fails loudly when no counts key matches the level.
                    "quote_count": int(level_counts[level_key]),
                },
            )
        )
    return [row for _sort_key, row in sorted(ordered_rows, key=lambda item: item[0])]


def _ratebook_serialisation_dtypes(
    table_name: str,
    records: object,
) -> list[pl.DataType]:
    """Validate and reconstruct one table's ordered factor dtype metadata."""
    expected_columns = table_name.split(":")
    if not isinstance(records, list) or len(records) != len(expected_columns):
        raise ValueError(
            f"Ratebook factor_dtypes for {table_name!r} must contain one ordered "
            "record per factor column."
        )
    dtypes: list[pl.DataType] = []
    for index, (expected_column, record) in enumerate(zip(expected_columns, records)):
        if not isinstance(record, dict) or set(record) != {"column", "dtype"}:
            raise ValueError(
                f"Ratebook factor_dtypes for {table_name!r} has a malformed "
                f"record at index {index}."
            )
        column = record.get("column")
        descriptor = record.get("dtype")
        if column != expected_column:
            raise ValueError(
                f"Ratebook factor_dtypes for {table_name!r} expected column "
                f"{expected_column!r} at index {index}, got {column!r}."
            )
        try:
            dtypes.append(rating_dtype_from_descriptor(descriptor))
        except ValueError as exc:
            raise ValueError(
                f"Ratebook factor_dtypes for {table_name!r} has an invalid dtype "
                f"descriptor at index {index}."
            ) from exc
    return dtypes


def _serialise_ratebook_factor_tables(
    factor_tables: Any,
    factor_level_counts: dict[str, dict[str, int]],
    factor_level_order: dict[str, list[str]],
    factor_dtypes: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Serialise ratebook factor tables for the API, ordered by banding rules.

    Level labels are canonicalised against ``factor_level_counts`` at save
    time (3b.10) — see :func:`_serialise_ratebook_factor_table_rows`.
    """
    if not isinstance(factor_tables, dict):
        raise ValueError("Ratebook factor tables are invalid")
    if not isinstance(factor_dtypes, dict):
        raise ValueError("Ratebook factor_dtypes are invalid")

    serialised: dict[str, list[dict[str, Any]]] = {}
    for name, table in _sort_ratebook_factor_tables(factor_tables, factor_level_order):
        if not isinstance(name, str) or not isinstance(table, dict):
            raise ValueError("Ratebook factor tables are invalid")
        level_counts = factor_level_counts.get(name)
        if level_counts is None:
            raise ValueError(f"Ratebook factor counts missing for factor table {name!r}.")
        table_dtypes = _ratebook_serialisation_dtypes(name, factor_dtypes.get(name))
        serialised[name] = _serialise_ratebook_factor_table_rows(
            name,
            table,
            level_counts,
            factor_level_order,
            table_dtypes,
        )
    return serialised


def _compute_ratebook_factor_level_order(
    graph: PipelineGraph,
    config: dict[str, Any],
    mode: str,
) -> dict[str, list[str]]:
    """Return the banding-rule level order for ratebook mode, ``{}`` otherwise.

    Computed once at solve start from the graph and threaded through to the
    background solver as an explicit parameter — never injected into the
    user-facing config dict.
    """
    if mode != "ratebook":
        return {}
    return _ratebook_factor_level_order(graph, config)


def _finalize_solve_result(
    solve_result: SolveResultLike,
    *,
    mode: str,
    solver: Any,
    quote_grid: QuoteGrid,
    store: JobStore,
    job_id: str,
    elapsed: float,
    extra_fields: dict[str, Any] | None = None,
    extra_job_fields: dict[str, Any] | None = None,
    factors_df: pl.DataFrame | None = None,
    ratebook_factors_handle: dict[str, Any] | None = None,
    ratebook_factor_contexts: Any | None = None,
    factor_columns: list[list[str]] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    """Build the result dict and update the job with the solve outcome.

    Shared by ``_solve_online`` and ``_solve_ratebook`` to avoid duplicating
    the ~30 lines of result-dict construction, convergence warning, and
    store update boilerplate.

    Parameters
    ----------
    solve_result:
        The solver result object (online or ratebook).
    mode:
        ``"online"`` or ``"ratebook"``.
    solver:
        The solver instance (stored on the job for later use).
    quote_grid:
        The QuoteGrid (stored on the job for apply/frontier operations).
    store:
        The job store — updates are applied atomically via dict replacement.
    job_id:
        The job ID to update in the store.
    elapsed:
        Wall-clock seconds since the solve started.
    extra_fields:
        Mode-specific keys to merge into the result dict (e.g.
        ``iterations``, ``factor_tables``).
    """
    scenario_value_stats, scenario_value_histogram = _compute_scenario_value_stats(solve_result)

    result_dict: dict[str, Any] = {
        "mode": mode,
        "total_objective": solve_result.total_objective,
        "baseline_objective": solve_result.baseline_objective,
        "constraints": solve_result.total_constraints,
        "baseline_constraints": solve_result.baseline_constraints,
        "lambdas": solve_result.lambdas,
        "converged": solve_result.converged,
        "scenario_value_stats": scenario_value_stats,
        "scenario_value_histogram": scenario_value_histogram,
    }
    if extra_fields:
        result_dict.update(extra_fields)
    if not solve_result.converged:
        result_dict["warning"] = (
            "Solver did not converge. Consider increasing max_iter or relaxing tolerance."
        )

    # ── Compute efficient frontier when explicitly requested (non-fatal) ────
    frontier_data = None
    frontier_error = None
    # Read through JobStore so concurrent eviction cannot race this snapshot.
    job_snapshot: Mapping[str, Any] = store.get_job(job_id) or {}
    config = job_snapshot.get("config", {})
    constraints = config.get("constraints")
    if constraints and config.get("frontier_enabled") is True:
        try:
            frontier_steps = config.get("frontier_steps", 15)
            ranges = _auto_frontier_ranges_from_config(config)
            if ranges:
                enforce_frontier_compute_budget(
                    n_points_per_dim=frontier_steps,
                    n_constraints=len(ranges),
                )
                progress_job = store.atomic_update(
                    job_id,
                    {
                        "message": "Computing efficient frontier",
                        "progress": 0.8,
                        "elapsed_seconds": _job_elapsed_seconds(job_snapshot, elapsed),
                    },
                    expected_status="running",
                )
                if progress_job is None:
                    logger.info(
                        "frontier_start_skipped",
                        job_id=job_id,
                        expected_status="running",
                    )
                    return
                job_snapshot = progress_job
                frontier_result = _compute_frontier(
                    solver,
                    quote_grid,
                    mode=mode,
                    ratebook_factors=ratebook_factor_contexts if mode == "ratebook" else None,
                    factor_columns=factor_columns,
                    threshold_ranges=ranges,
                    n_points_per_dim=frontier_steps,
                    initial_lambdas=solve_result.lambdas,
                    check_cancelled=check_cancelled,
                )
                frontier_data = limited_frontier_payload(
                    frontier_result.points,
                    constraint_names=list(ranges.keys()),
                )
                logger.info(
                    "frontier_computed",
                    n_points=frontier_data["n_points"],
                    job_id=job_id,
                )
        except (BackgroundJobStoppedError, ExecutionCancelledError):
            raise
        except Exception as exc:
            frontier_error = f"Frontier unavailable: {exc}"
            logger.warning(
                "frontier_computation_failed",
                error=str(exc),
                job_id=job_id,
                exc_info=True,
            )

    result_dict["frontier"] = frontier_data
    if frontier_error is not None:
        result_dict["frontier_error"] = frontier_error
    completion_elapsed = _job_elapsed_seconds(
        store.get_job(job_id) or job_snapshot,
        elapsed,
    )
    uncommitted_handles: list[tuple[dict[str, Any], str]] = []
    if ratebook_factors_handle is not None:
        uncommitted_handles.append(
            (
                ratebook_factors_handle,
                "solve_completion_orphan_factor_artifact_cleanup_failed",
            )
        )

    def publish_completion_fields() -> Mapping[str, Any]:
        """Persist durable artifacts only after this worker owns completion."""
        artifact_handles: dict[str, Any] = {}
        apply_result_handle = _persist_apply_result_artifact(solve_result)
        if apply_result_handle is not None:
            artifact_handles[_APPLY_RESULT_HANDLE_KEY] = apply_result_handle
            uncommitted_handles.append(
                (
                    apply_result_handle,
                    "solve_completion_orphan_apply_artifact_cleanup_failed",
                )
            )

        factor_handle = ratebook_factors_handle
        if factor_handle is None:
            factor_handle = _persist_ratebook_factors_artifact(factors_df)
            if factor_handle is not None:
                uncommitted_handles.append(
                    (
                        factor_handle,
                        "solve_completion_orphan_factor_artifact_cleanup_failed",
                    )
                )
        if factor_handle is not None:
            artifact_handles[_RATEBOOK_FACTORS_HANDLE_KEY] = factor_handle

        completion_fields: dict[str, Any] = {
            "progress": 1.0,
            "solver": solver,
            "solve_result": solve_result,
            "quote_grid": quote_grid,
            "factor_columns_valid": factor_columns,
            "result": result_dict,
            "base_result": dict(result_dict),
            "frontier_data": frontier_data,
            "artifact_handles": artifact_handles,
            **(extra_job_fields or {}),
            _FRONTIER_GENERATION_KEY: 0,
        }
        if ratebook_factor_contexts is not None:
            completion_fields["ratebook_factor_contexts"] = ratebook_factor_contexts
        return completion_fields

    def cleanup_uncommitted_handles() -> None:
        for handle, event in uncommitted_handles:
            _cleanup_orphan_apply_result_artifact(
                handle,
                job_id=job_id,
                event=event,
            )

    # Artifact persistence and the terminal record now share the store's one
    # running-job claim. Cancellation either wins before the publisher runs or
    # observes the complete artifact/result pair afterwards.
    try:
        updated_job = JobLifecycle(store).publish_completion(
            job_id,
            publish=publish_completion_fields,
            message="Completed",
            elapsed_seconds=completion_elapsed,
        )
    except BaseException:
        cleanup_uncommitted_handles()
        raise
    if updated_job is None:
        logger.info("solve_completion_skipped", job_id=job_id, expected_status="running")
        cleanup_uncommitted_handles()
        return

    # The job store keeps these heavy runtime objects for its short
    # heavy-object retention window, then slims the completed job down to
    # API-facing summaries/metadata while preserving the 24h status record.


@require_solver_worker_context
def _solve_online(
    ctx: SolveContext,
    *,
    quote_grid: QuoteGrid,
    config: dict[str, Any],
) -> None:
    """Run the online optimiser solver on a pre-built QuoteGrid."""
    from price_contour import OnlineOptimiser

    if ctx.store is None:
        raise RuntimeError("_solve_online requires SolveContext.store to be set.")
    store = ctx.store
    job_id = ctx.job_id
    check_cancelled = ctx.check_cancelled
    if ctx.start_time is None:
        raise RuntimeError("_solve_online requires SolveContext.start_time to be set.")
    start_time = ctx.start_time

    if check_cancelled is not None:
        check_cancelled()
    try:
        solver = OnlineOptimiser(
            objective=config["objective"],
            constraints=config["constraints"] or None,
            max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
            tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
            record_history=config.get("record_history", False),
        )
        solve_result: OnlineSolveResultLike = solver.solve(quote_grid)
    except (BackgroundJobStoppedError, ExecutionCancelledError):
        raise
    except Exception as exc:
        raise _OptimiserSolverExecutionError(str(exc)) from exc
    if check_cancelled is not None:
        check_cancelled()
    elapsed = time.monotonic() - start_time
    logger.info(
        "solve_completed",
        mode="online",
        elapsed=f"{elapsed:.2f}s",
        converged=solve_result.converged,
    )

    _finalize_solve_result(
        solve_result,
        mode="online",
        solver=solver,
        quote_grid=solve_result.grid,
        factors_df=None,
        store=store,
        job_id=job_id,
        elapsed=elapsed,
        extra_fields={
            "iterations": solve_result.iterations,
            "n_quotes": solve_result.n_quotes,
            "n_steps": solve_result.n_steps,
            "history": solve_result.history if config.get("record_history") else None,
        },
        check_cancelled=check_cancelled,
    )


@dataclass(frozen=True, slots=True)
class SolveContext:
    """Per-solve context that travels end-to-end through the solver pipeline."""

    job_id: str
    node_id: str
    mode: str
    store: JobStore | None = None
    execution_context: ExecutionContext | None = None
    streaming_chunk_size: int | None = None
    setup_singleflight_key: tuple[str, str, str] | None = None
    registration_already_active: bool = False
    start_time: float | None = None
    check_cancelled: Callable[[], None] | None = None


@require_solver_worker_context
def _solve_ratebook(
    ctx: SolveContext,
    *,
    quote_grid: QuoteGrid,
    config: dict[str, Any],
    ratebook_factors_handle: dict[str, Any] | None,
    factor_level_order: dict[str, list[str]] | None = None,
) -> None:
    """Run the ratebook optimiser solver on a pre-built QuoteGrid."""
    from price_contour import RatebookOptimiser

    if ctx.store is None:
        raise RuntimeError("_solve_ratebook requires SolveContext.store to be set.")
    store = ctx.store
    job_id = ctx.job_id
    streaming_chunk_size = ctx.streaming_chunk_size
    check_cancelled = ctx.check_cancelled
    if ctx.start_time is None:
        raise RuntimeError("_solve_ratebook requires SolveContext.start_time to be set.")
    start_time = ctx.start_time

    if ratebook_factors_handle is None:
        raise _OptimiserSolveInputError(
            "Ratebook mode requires a banding source. "
            "Select a banding node in the Rating Factor Source dropdown."
        )
    if check_cancelled is not None:
        check_cancelled()

    constraints = config["constraints"]

    raw_factor_columns = config.get("factor_columns", [])
    available_raw = ratebook_factors_handle.get("columns")
    available_cols = set(available_raw) if isinstance(available_raw, list) else set()
    missing = [c for group in raw_factor_columns for c in group if c not in available_cols]
    if missing:
        raise _OptimiserSolveInputError(
            f"Missing ratebook factor columns in banding source: {missing}. "
            f"Available columns: {sorted(available_cols)}"
        )
    factor_columns_valid = [list(group) for group in raw_factor_columns]

    factor_artifact_path, _factor_artifact_dir = _validate_ratebook_factors_artifact_handle(
        ratebook_factors_handle
    )
    factor_chunk_decision = _chunk_size_decision_for_parquet(
        config,
        factor_artifact_path,
        source="ratebook_factor_contexts",
    )
    try:
        factor_contexts = _build_ratebook_factor_contexts(
            ratebook_factors_handle,
            quote_grid,
            config,
            factor_columns_valid,
            chunk_decision=factor_chunk_decision,
        )
    except ValueError as exc:
        raise _OptimiserSolveInputError(str(exc)) from exc

    try:
        solver = RatebookOptimiser(
            objective=config["objective"],
            constraints=constraints,
            factor_columns=factor_columns_valid,
            max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
            max_cd_iterations=config.get("max_cd_iterations", _DEFAULT_MAX_CD_ITERATIONS),
            cd_tolerance=config.get("cd_tolerance", _DEFAULT_CD_TOLERANCE),
            tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
        )
        solve_result: RatebookSolveResultLike = solver.solve(quote_grid, factor_contexts)
    except (BackgroundJobStoppedError, ExecutionCancelledError):
        raise
    except Exception as exc:
        raise _OptimiserSolverExecutionError(str(exc)) from exc
    if check_cancelled is not None:
        check_cancelled()
    elapsed = time.monotonic() - start_time
    converged = solve_result.converged
    logger.info("solve_completed", mode="ratebook", elapsed=f"{elapsed:.2f}s", converged=converged)

    factor_level_counts = _ratebook_factor_level_counts_from_artifact(
        ratebook_factors_handle,
        factor_columns_valid,
        streaming_chunk_size=streaming_chunk_size,
    )
    factor_dtypes = _ratebook_factor_dtypes_from_artifact(
        ratebook_factors_handle,
        factor_columns_valid,
    )
    resolved_level_order = factor_level_order or {}
    existing_setup_chunking = store.require_job(job_id).get("setup_chunking")
    setup_chunking = (
        dict(existing_setup_chunking) if isinstance(existing_setup_chunking, Mapping) else {}
    )
    setup_chunking["ratebook_factor_contexts"] = factor_chunk_decision.provenance
    factor_tables_serialised = _serialise_ratebook_factor_tables(
        solve_result.factor_tables,
        factor_level_counts,
        resolved_level_order,
        factor_dtypes,
    )

    _finalize_solve_result(
        solve_result,
        mode="ratebook",
        solver=solver,
        quote_grid=quote_grid,
        ratebook_factors_handle=ratebook_factors_handle,
        ratebook_factor_contexts=factor_contexts,
        factor_columns=factor_columns_valid,
        store=store,
        job_id=job_id,
        elapsed=elapsed,
        extra_fields={
            "cd_iterations": solve_result.cd_iterations,
            "factor_tables": factor_tables_serialised,
            "factor_dtypes": factor_dtypes,
            "clamp_rate": getattr(solve_result, "clamp_rate", None),
            "history": None,
        },
        extra_job_fields={
            "factor_level_counts": factor_level_counts,
            "factor_dtypes": factor_dtypes,
            _RATEBOOK_FACTOR_LEVEL_ORDER_KEY: resolved_level_order,
            "setup_chunking": setup_chunking,
        },
        check_cancelled=check_cancelled,
    )


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
        config = dict(node.data.config)

        mode = self._validate_config(config)
        factor_level_order = _compute_ratebook_factor_level_order(body.graph, config, mode)
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
        """Execute solve setup, then hand the prepared grid to the solver worker."""
        execution_context: ExecutionContext | None = None
        launch_started = False
        ratebook_factors_handle: Any = None
        job = self._store.require_job(job_id)
        raw_start_time = job.get("start_time")
        start_time = (
            float(raw_start_time)
            if isinstance(raw_start_time, int | float) and not isinstance(raw_start_time, bool)
            else time.monotonic()
        )
        # ``TemporaryDirectory`` removes the checkpoint dir even on signal/
        # crash; an interrupted long solve will not leak GBs of staging data.
        with tempfile.TemporaryDirectory(prefix="haute_opt_") as raw_dir:
            checkpoint_dir = Path(raw_dir)
            try:
                execution_context = create_admitted_execution_context(
                    operation="optimiser_solve",
                    profile=ExecutionProfile.OPTIMISER_SETUP,
                    job_id=job_id,
                    cancellation_token=execution_token,
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
                lazy_outputs = self._execute_pipeline(
                    body,
                    job_id,
                    checkpoint_dir,
                    required_columns_by_node=required_columns_by_node,
                    execution_context=execution_context,
                )
                self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                source_lf = self._resolve_data_input_frame(
                    lazy_outputs,
                    config,
                    body.node_id,
                    job_id,
                    execution_context=execution_context,
                )
                constraint_cols, scored_lf = self._validate_and_project(
                    source_lf,
                    config,
                    job_id,
                    execution_context=execution_context,
                    streaming_chunk_size=body.streaming_chunk_size,
                )
                self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                ratebook_factors_handle = self._extract_factors(
                    lazy_outputs,
                    config,
                    mode,
                    execution_context=execution_context,
                    streaming_chunk_size=body.streaming_chunk_size,
                )
                self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                del lazy_outputs
                gc.collect()

                quote_grid = self._build_grid(
                    scored_lf,
                    constraint_cols,
                    config,
                    body.node_id,
                    job_id,
                    execution_context=execution_context,
                    streaming_chunk_size=body.streaming_chunk_size,
                )
                self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                self._record_execution_metrics(job_id, execution_context)
                self._launch_background(
                    SolveContext(
                        job_id=job_id,
                        node_id=body.node_id,
                        mode=mode,
                        execution_context=execution_context,
                        streaming_chunk_size=body.streaming_chunk_size,
                        setup_singleflight_key=setup_job_key,
                        registration_already_active=True,
                    ),
                    config=config,
                    quote_grid=quote_grid,
                    ratebook_factors_handle=ratebook_factors_handle,
                    factor_level_order=factor_level_order,
                )
                launch_started = True
            except BackgroundJobStoppedError as exc:
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
                    elapsed_seconds=time.monotonic() - start_time,
                )
            except HTTPException as exc:
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
                    elapsed_seconds=time.monotonic() - start_time,
                )
            except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
                http_exc = _memory_limit_http_exception(exc)
                elapsed_seconds = time.monotonic() - start_time
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
            except PUBLIC_CONTRACT_ERROR_TYPES as exc:
                elapsed_seconds = time.monotonic() - start_time
                contract_fields = contract_error_job_fields(exc)
                contract_fields["elapsed_seconds"] = elapsed_seconds
                if execution_context is not None:
                    contract_fields["execution_metrics"] = execution_context.metrics_payload(
                        status="contract_error",
                        terminal_reason="contract_error",
                    )
                self._lifecycle.transition(
                    job_id,
                    to="contract_error",
                    message=str(exc),
                    fields=contract_fields,
                    elapsed_seconds=elapsed_seconds,
                )
            except BoundedMemoryUnsupportedError as exc:
                detail = f"Optimiser setup cannot run in bounded streaming mode: {exc}"
                logger.warning(
                    "optimiser_setup_bounded_streaming_unsupported",
                    error=str(exc),
                    node_id=body.node_id,
                    job_id=job_id,
                )
                elapsed_seconds = time.monotonic() - start_time
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
            except Exception as exc:
                detail = f"Optimiser setup failed: {exc}"
                logger.error(
                    "optimiser_setup_failed",
                    error=str(exc),
                    node_id=body.node_id,
                    job_id=job_id,
                    exc_info=True,
                )
                elapsed_seconds = time.monotonic() - start_time
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
            finally:
                if not launch_started:
                    if execution_context is not None:
                        execution_context.release_admission()
                    self._release_job_ownership(job_id, setup_singleflight_key=setup_job_key)
                    if (
                        mode == "ratebook"
                        and isinstance(ratebook_factors_handle, dict)
                        and ratebook_factors_handle.get("kind") == _RATEBOOK_FACTORS_HANDLE_KIND
                    ):
                        _cleanup_orphan_apply_result_artifact(
                            ratebook_factors_handle,
                            job_id=job_id,
                            event="setup_orphan_ratebook_factors_cleanup_failed",
                        )

    def start_frontier_auto_range(
        self,
        body: OptimiserFrontierAutoRangeRequest,
    ) -> OptimiserFrontierAutoRangeStartResponse:
        """Start auto-range in a background thread and return a pollable job."""
        body = cast(OptimiserFrontierAutoRangeRequest, _with_flattened_optimiser_graph(body))
        prepared = self._prepare_frontier_auto_range(body)
        node = prepared["node"]
        config = prepared["config"]
        job_key = self._frontier_auto_range_job_key(body)
        setup_job_key = self._graph_node_setup_job_key(body.graph, body.node_id)
        with self._start_lock:
            active_setup = self._active_graph_node_setup(setup_job_key)
            if active_setup is not None:
                if active_setup.kind == _FRONTIER_AUTO_RANGE_JOB_TYPE:
                    active_job = self._store.require_job(active_setup.job_id)
                    if active_job.get("status") == "running":
                        return OptimiserFrontierAutoRangeStartResponse(
                            status="started",
                            job_id=active_setup.job_id,
                        )
                raise self._graph_node_setup_conflict(active_setup)
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
            try:
                execution_context = create_admitted_execution_context(
                    operation="frontier_auto_range",
                    profile=ExecutionProfile.AUTO_RANGE,
                    job_id=job_id,
                    cancellation_token=execution_token,
                )
                bind_running_execution_metrics_publisher(
                    self._store,
                    job_id,
                    execution_context,
                )
            except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
                http_exc = _memory_limit_http_exception(exc)
                self._lifecycle.transition(
                    job_id,
                    to="memory_limited",
                    message=str(http_exc.detail),
                )
                raise http_exc from None
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
                execution_context=execution_context,
                **prepared,
            )
        except Exception:
            execution_context.release_admission()
            self._release_job_ownership(job_id, setup_singleflight_key=setup_job_key)
            raise
        return OptimiserFrontierAutoRangeStartResponse(status="started", job_id=job_id)

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
            timeout = job.get("timeout", _default_auto_range_timeout())
            if start and (time.monotonic() - start) > timeout:
                self._jobs.cancel(job_id, reason="timed_out")
                updated_job = self._lifecycle.transition(
                    job_id,
                    to="timed_out",
                    message=(
                        f"Auto range timed out after {timeout}s. "
                        "Reduce the input size or increase HAUTE_AUTO_RANGE_TIMEOUT."
                    ),
                    elapsed_seconds=time.monotonic() - start,
                )
                job = updated_job if updated_job is not None else self._store.require_job(job_id)

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
    ) -> dict[str, Any]:
        node = _find_optimiser_node(body.graph, body.node_id)
        config = dict(node.data.config)
        mode = self._validate_config(config)
        try:
            chunk_size = _auto_range_chunk_size_from_config(config)
            partition_count = _auto_range_partition_count_from_config(config)
            timeout = _auto_range_timeout_from_config(config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        required_columns_by_node = _auto_range_required_columns_by_node(
            body.graph,
            body.node_id,
            config,
            mode=mode,
        )
        try:
            streaming_plan = _build_streaming_auto_range_plan(
                body.graph,
                body.node_id,
                config,
                mode=mode,
                required_columns_by_node=required_columns_by_node,
            )
        except ProjectionImpossibleError as exc:
            logger.info(
                "frontier_auto_range_streaming_plan_projection_impossible",
                error=str(exc),
                node_id=body.node_id,
            )
            streaming_plan = None
        except ChunkPlanUnsupportedError as exc:
            detail = f"Frontier auto range cannot run in bounded streaming mode: {exc}"
            raise HTTPException(status_code=422, detail=detail) from exc
        return {
            "node": node,
            "config": config,
            "mode": mode,
            "chunk_size": chunk_size,
            "partition_count": partition_count,
            "timeout": timeout,
            "required_columns_by_node": required_columns_by_node,
            "streaming_plan": streaming_plan,
        }

    def _run_frontier_auto_range_job(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        node: GraphNode,
        config: dict[str, Any],
        mode: str,
        chunk_size: int,
        partition_count: int,
        timeout: int,
        required_columns_by_node: Mapping[str, Iterable[str]],
        streaming_plan: _StreamingAutoRangePlan | None,
        execution_context: ExecutionContext | None = None,
    ) -> OptimiserFrontierAutoRangeResponse:
        del node, mode, timeout
        if execution_context is None:
            execution_context = ExecutionContext(
                operation="frontier_auto_range",
                profile=ExecutionProfile.AUTO_RANGE,
                job_id=job_id,
            )
        try:
            execution_context.checkpoint(label="frontier_auto_range_start")
        except ExecutionCancelledError as exc:
            status = str(self._store.require_job(job_id).get("status", "running"))
            raise BackgroundJobStoppedError(job_id, status) from exc
        self._raise_if_frontier_auto_range_stopped(job_id)
        if streaming_plan is not None:
            return self._run_streaming_frontier_auto_range_job(
                body,
                job_id,
                config=config,
                chunk_size=chunk_size,
                partition_count=partition_count,
                streaming_plan=streaming_plan,
                execution_context=execution_context,
            )

        # ``TemporaryDirectory`` ensures the checkpoint dir is removed even
        # on signal/abort, where ``mkdtemp`` + ``rmtree`` in finally would leak.
        try:
            with tempfile.TemporaryDirectory(prefix="haute_frontier_range_") as raw_dir:
                checkpoint_dir = Path(raw_dir)
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
                lazy_outputs = self._execute_pipeline(
                    body,
                    job_id,
                    checkpoint_dir,
                    required_columns_by_node=required_columns_by_node,
                    execution_context=execution_context,
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
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
                source_lf = self._resolve_data_input_frame(
                    lazy_outputs,
                    config,
                    body.node_id,
                    job_id,
                    execution_context=execution_context,
                )
                constraint_cols, scored_lf = self._validate_and_project_auto_range(
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
                ranges = _estimate_scenario_frontier_ranges(
                    FrontierAutoRangeContext(
                        chunk_size=chunk_size,
                        partition_count=partition_count,
                        execution_context=execution_context,
                        streaming_chunk_size=body.streaming_chunk_size,
                    ),
                    scored_lf=scored_lf,
                    quote_id_col=str(config.get("quote_id", "quote_id")),
                    constraint_cols=constraint_cols,
                    check_cancelled=lambda: self._raise_if_frontier_auto_range_stopped(job_id),
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                response_ranges = {
                    name: OptimiserFrontierRange(min=value["min"], max=value["max"])
                    for name, value in ranges.items()
                }
                response = OptimiserFrontierAutoRangeResponse(
                    status="ok",
                    ranges=response_ranges,
                    warning=None,
                )
                self._lifecycle.transition(
                    job_id,
                    to="completed",
                    message="Completed",
                    fields={
                        "progress": 1.0,
                        "elapsed_seconds": self._job_elapsed(job_id),
                        "result": response.model_dump(),
                        "execution_metrics": execution_context.metrics_payload(status="completed"),
                    },
                )
                return response
        except BackgroundJobStoppedError:
            raise
        except ExecutionCancelledError as exc:
            reason = self._jobs.cancellation_reason(job_id) or "cancelled"
            raise BackgroundJobStoppedError(job_id, reason) from exc
        except ExecutionMemoryLimitExceededError as exc:
            http_exc = _memory_limit_http_exception(exc)
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
            fields = contract_error_job_fields(exc)
            fields["elapsed_seconds"] = elapsed_seconds
            fields["execution_metrics"] = execution_context.metrics_payload(
                status="contract_error",
                terminal_reason="contract_error",
            )
            self._lifecycle.transition(job_id, to="contract_error", fields=fields)
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

    def _run_streaming_frontier_auto_range_job(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        config: dict[str, Any],
        chunk_size: int,
        partition_count: int,
        streaming_plan: _StreamingAutoRangePlan,
        execution_context: ExecutionContext | None = None,
    ) -> OptimiserFrontierAutoRangeResponse:
        import polars as pl

        if execution_context is None:
            execution_context = ExecutionContext(
                operation="frontier_auto_range_streaming",
                profile=ExecutionProfile.AUTO_RANGE,
                job_id=job_id,
            )
            bind_running_execution_metrics_publisher(
                self._store,
                job_id,
                execution_context,
            )
        try:
            self._raise_if_frontier_auto_range_stopped(job_id)
            with (
                tempfile.TemporaryDirectory(prefix="haute_frontier_range_") as raw_dir,
                tempfile.TemporaryDirectory(prefix="haute_frontier_range_parts_") as parts_dir,
            ):
                checkpoint_dir = Path(raw_dir)
                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Executing base pipeline",
                        "progress": 0.05,
                        "elapsed_seconds": self._job_elapsed(job_id),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                base_required = None
                if streaming_plan.base_required_columns is not None:
                    base_required = {
                        streaming_plan.base_node_id: streaming_plan.base_required_columns,
                    }
                lazy_outputs = self._execute_pipeline(
                    body,
                    job_id,
                    checkpoint_dir,
                    required_columns_by_node=base_required,
                    target_node_id=streaming_plan.base_node_id,
                    execution_context=execution_context,
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                base_lf = lazy_outputs.get(streaming_plan.base_node_id)
                if base_lf is None:
                    raise ValueError(
                        "Streaming auto-range base node did not produce a dataframe: "
                        f"{streaming_plan.base_node_id!r}."
                    )

                qid_col = str(config.get("quote_id", "quote_id"))
                constraint_cols = (
                    list(config["constraints"].keys())
                    if isinstance(config.get("constraints"), dict)
                    else []
                )
                accumulator = _ScenarioFrontierRangeAccumulator(
                    quote_id_col=qid_col,
                    constraint_cols=constraint_cols,
                    partition_count=partition_count,
                    parts_root=Path(parts_dir),
                )

                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Streaming scenario chunks",
                        "progress": 0.30,
                        "elapsed_seconds": self._job_elapsed(job_id),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                chunk_index = 0
                from haute.chunking import ChunkRunnerRequest, iter_chunked_frames
                from haute.executor import _compile_preamble, _pipeline_dir

                preamble_ns = (
                    _compile_preamble(
                        body.graph.preamble or "",
                        force_refresh=False,
                        pipeline_dir=_pipeline_dir(body.graph),
                    )
                    or None
                )
                chunk_batches = iter_chunked_frames(
                    ChunkRunnerRequest(
                        graph=body.graph,
                        plan=streaming_plan.chunk_plan,
                        build_node_fn=_build_node_fn,
                        preamble_ns=preamble_ns,
                        execution_context=execution_context,
                        start_frame=(
                            base_lf if isinstance(base_lf, pl.LazyFrame) else base_lf.lazy()
                        ),
                        streaming_chunk_size=body.streaming_chunk_size,
                    )
                )
                for chunk in chunk_batches:
                    self._raise_if_frontier_auto_range_stopped(job_id)
                    validated_constraints, scored_lf = self._validate_and_project_auto_range(
                        chunk.frame.lazy(),
                        config,
                        job_id,
                        execution_context=execution_context,
                    )
                    self._raise_if_frontier_auto_range_stopped(job_id)
                    if validated_constraints != constraint_cols:
                        raise ValueError("Streaming auto-range constraint columns changed.")
                    with execution_context.stage(
                        "frontier_stream_score_collect",
                        node_id=streaming_plan.scenario_node_id,
                    ):
                        with temporary_streaming_chunk_size(
                            body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
                        ):
                            batch = streaming_collect(
                                scored_lf.select(
                                    [
                                        pl.col(qid_col).cast(pl.String).alias(qid_col),
                                        *[pl.col(cname) for cname in constraint_cols],
                                    ]
                                ),
                                execution_context=execution_context,
                            )
                    self._raise_if_frontier_auto_range_stopped(job_id)
                    _add_frontier_range_batch(
                        accumulator,
                        batch,
                        batch_index=chunk_index,
                        execution_context=execution_context,
                    )
                    chunk_index += 1
                    if chunk_index % 10 == 0:
                        self._store.atomic_update(
                            job_id,
                            {
                                "message": f"Streaming scenario chunks ({chunk_index})",
                                "progress": 0.30,
                                "elapsed_seconds": self._job_elapsed(job_id),
                            },
                            expected_status="running",
                        )

                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Combining scenario envelope",
                        "progress": 0.85,
                        "elapsed_seconds": self._job_elapsed(job_id),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                ranges = accumulator.finish(
                    check_cancelled=lambda: self._raise_if_frontier_auto_range_stopped(job_id),
                    execution_context=execution_context,
                    streaming_chunk_size=body.streaming_chunk_size,
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                response_ranges = {
                    name: OptimiserFrontierRange(min=value["min"], max=value["max"])
                    for name, value in ranges.items()
                }
                response = OptimiserFrontierAutoRangeResponse(
                    status="ok",
                    ranges=response_ranges,
                    warning=None,
                )
                self._lifecycle.transition(
                    job_id,
                    to="completed",
                    message="Completed",
                    fields={
                        "progress": 1.0,
                        "elapsed_seconds": self._job_elapsed(job_id),
                        "result": response.model_dump(),
                        "execution_metrics": execution_context.metrics_payload(status="completed"),
                    },
                )
                return response
        except BackgroundJobStoppedError:
            raise
        except ExecutionCancelledError as exc:
            reason = self._jobs.cancellation_reason(job_id) or "cancelled"
            raise BackgroundJobStoppedError(job_id, reason) from exc
        except ExecutionMemoryLimitExceededError as exc:
            http_exc = _memory_limit_http_exception(exc)
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
            fields = contract_error_job_fields(exc)
            fields["elapsed_seconds"] = elapsed_seconds
            fields["execution_metrics"] = execution_context.metrics_payload(
                status="contract_error",
                terminal_reason="contract_error",
            )
            self._lifecycle.transition(job_id, to="contract_error", fields=fields)
            raise contract_error_http_exception(exc) from None
        except BoundedMemoryUnsupportedError as exc:
            detail = f"Frontier auto range cannot run in bounded streaming mode: {exc}"
            logger.warning(
                "frontier_auto_range_streaming_bounded_streaming_unsupported",
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
                "frontier_auto_range_streaming_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._lifecycle.transition(
                job_id,
                to="error",
                fields={
                    "message": f"Streaming frontier auto range failed: {exc}",
                    "elapsed_seconds": self._job_elapsed(job_id),
                    "execution_metrics": execution_context.metrics_payload(status="error"),
                },
            )
            raise HTTPException(
                status_code=500,
                detail="Frontier auto range failed. Check the server logs for details.",
            ) from exc

    def _launch_frontier_auto_range_background(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        setup_singleflight_key: tuple[str, str, str] | None = None,
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
                    self._run_frontier_auto_range_job(body, job_id, **prepared)
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
                finally:
                    execution_context = prepared.get("execution_context")
                    if isinstance(execution_context, ExecutionContext):
                        terminal_reason = None
                        try:
                            job: Mapping[str, Any] = self._store.require_job(job_id)
                        except HTTPException:
                            job = {}
                        stored_reason = job.get("terminal_reason")
                        if isinstance(stored_reason, str) and stored_reason:
                            terminal_reason = stored_reason
                        self._record_execution_metrics(
                            job_id,
                            execution_context,
                            terminal_reason=terminal_reason,
                        )
                        execution_context.release_admission()

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

        if mode == "ratebook":
            factor_columns = config.get("factor_columns")
            if not factor_columns:
                raise HTTPException(
                    status_code=400,
                    detail="Ratebook mode requires factor_columns. Add at least one factor group.",
                )

        try:
            _solve_timeout_from_config(config)
            _explicit_chunk_size_from_config(config)
        except ValueError as exc:
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

    def _execute_pipeline(
        self,
        body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
        job_id: str,
        checkpoint_dir: Path,
        *,
        required_columns_by_node: Mapping[str, Iterable[str]] | None = None,
        target_node_id: str | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> dict[str, Any]:
        """Execute the pipeline lazily up to the optimiser node.

        The caller owns *checkpoint_dir* lifecycle (creation + cleanup).
        """
        body = _with_flattened_optimiser_graph(body)
        try:
            from haute.executor import (
                _build_node_fn,
                _compile_preamble,
                _pipeline_dir,
                _resolve_batch_scenario,
            )

            # Resolve scenario: optimiser runs on batch data, not live.
            scenario = _resolve_batch_scenario(body.graph) or "batch"

            preamble_ns = (
                _compile_preamble(
                    body.graph.preamble or "",
                    force_refresh=False,
                    pipeline_dir=_pipeline_dir(body.graph),
                )
                or None
            )

            chunk_size = body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
            with temporary_streaming_chunk_size(chunk_size):
                execution_target_node_id = target_node_id or body.node_id
                if target_node_id is None:
                    optimiser_node = _find_optimiser_node(body.graph, body.node_id)
                    configured_data_input = optimiser_node.data.config.get("data_input")
                    if (
                        optimiser_node.data.config.get("mode", "online") == "online"
                        and isinstance(configured_data_input, str)
                        and configured_data_input
                    ):
                        data_input_id = _resolve_optimiser_data_input_id(
                            body.graph,
                            body.node_id,
                            optimiser_node.data.config,
                        )
                        if isinstance(data_input_id, str) and data_input_id:
                            execution_target_node_id = data_input_id
                preserved_node_ids = _optimiser_side_input_ids(body.graph, body.node_id)
                cache_node_ids = _optimiser_dataframe_cache_node_ids(
                    body.graph,
                    optimiser_node_id=body.node_id,
                    execution_target_node_id=execution_target_node_id,
                    explicit_target_node=target_node_id is not None,
                )
                # Opportunistic cache warming for the later solve.  The
                # cache key advertises solver-required columns so a
                # subsequent OPTIMISER_SETUP run can hit it, but the
                # executor still runs with the narrower auto-range
                # projection demand.  When the node's actual output
                # happens to include the solver columns (e.g. passthrough
                # nodes that don't drop upstream columns), the artifact
                # is reusable; when it doesn't, the seed-time column
                # check rejects the hit and the solve rebuilds.  This
                # preserves AUTO_RANGE's narrow-projection contract
                # because the executor's demand is unchanged.
                #
                # The merge invariant in ``_execute_lazy`` requires that
                # ``auto_range_required ⊆ solver_required`` so the
                # re-derived expected_key still matches the cache key.
                # ``_optimiser_solve_required_columns_by_node`` and
                # ``_auto_range_required_columns_by_node`` satisfy this
                # by construction (auto-range columns are a subset of
                # solver columns).
                cache_required_columns_by_node = required_columns_by_node
                if (
                    execution_context is not None
                    and execution_context.profile == ExecutionProfile.AUTO_RANGE
                ):
                    optimiser_node = _find_optimiser_node(body.graph, body.node_id)
                    mode = str(optimiser_node.data.config.get("mode", "online"))
                    if mode in {"online", "ratebook"}:
                        solver_required_columns_by_node = _optimiser_solve_required_columns_by_node(
                            body.graph,
                            body.node_id,
                            optimiser_node.data.config,
                        )
                        solver_cache_node_ids = set(cache_node_ids).intersection(
                            solver_required_columns_by_node
                        )
                        if solver_cache_node_ids:
                            cache_required_columns = dict(required_columns_by_node or {})
                            for node_id in solver_cache_node_ids:
                                cache_required_columns[node_id] = solver_required_columns_by_node[
                                    node_id
                                ]
                            cache_required_columns_by_node = cache_required_columns
                dataframe_cache_request = build_dataframe_execution_cache_request(
                    body.graph,
                    node_ids=cache_node_ids,
                    namespace="optimiser_setup",
                    source=scenario,
                    profile=(
                        execution_context.profile
                        if execution_context is not None
                        else ExecutionProfile.LAZY_SINK
                    ),
                    input_fingerprint=dataframe_graph_input_fingerprint(
                        body.graph,
                        target_node_id=execution_target_node_id,
                        source=scenario,
                    ),
                    target_node_id=execution_target_node_id,
                    preserve_node_ids=preserved_node_ids,
                    required_columns_by_node=cache_required_columns_by_node,
                    enforce_contracts=True,
                    preamble_ns_supplied=preamble_ns is not None,
                    streaming_chunk_size=chunk_size,
                )
                lazy_outputs, *_ = execute_lazy_graph(
                    body.graph,
                    _build_node_fn,
                    target_node_id=execution_target_node_id,
                    preamble_ns=preamble_ns,
                    source=scenario,
                    checkpoint_dir=checkpoint_dir,
                    enforce_contracts=True,
                    preserve_node_ids=preserved_node_ids,
                    required_columns_by_node=required_columns_by_node,
                    execution_context=execution_context,
                    dataframe_cache_request=dataframe_cache_request,
                )
            return lazy_outputs
        except HTTPException:
            raise
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            self._record_setup_failure(
                job_id,
                to="contract_error",
                message=str(exc),
                fields=contract_error_job_fields(exc),
                execution_context=execution_context,
            )
            raise contract_error_http_exception(exc) from None
        except ProjectionImpossibleError as exc:
            error_msg = f"Pipeline cannot run with bounded projection: {exc}"
            logger.warning(
                "pipeline_projection_impossible",
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
            )
        finally:
            if execution_context is not None:
                self._record_execution_metrics(job_id, execution_context)

    def _resolve_data_input_frame(
        self,
        lazy_outputs: dict[str, Any],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> Any:
        """Pick the correct lazy source from pipeline outputs."""
        data_input_id = config.get("data_input")
        if isinstance(data_input_id, str) and data_input_id:
            if data_input_id in lazy_outputs:
                source_lf = lazy_outputs[data_input_id]
            else:
                error_msg = (
                    f"Configured optimiser data_input {data_input_id!r} did not produce data. "
                    "Make sure it is connected to the optimiser node and produces a dataframe."
                )
                self._record_http_setup_failure(
                    job_id,
                    status_code=400,
                    detail=error_msg,
                    execution_context=execution_context,
                )
                raise HTTPException(status_code=400, detail=error_msg)
        else:
            source_lf = lazy_outputs.get(node_id)

        if source_lf is None:
            error_msg = (
                "No data arrived at the optimiser node. "
                "Make sure an upstream data source is connected and producing data."
            )
            self._record_http_setup_failure(
                job_id,
                status_code=400,
                detail=error_msg,
                execution_context=execution_context,
            )
            raise HTTPException(status_code=400, detail=error_msg)

        return source_lf

    def _validate_and_project(
        self,
        source_lf: Any,
        config: dict[str, Any],
        job_id: str,
        *,
        validate_quote_id_nulls: bool = True,
        execution_context: ExecutionContext | None = None,
        streaming_chunk_size: int | None = None,
    ) -> tuple[list[str], Any]:
        """Validate columns and build the projection for the solver.

        Returns (constraint_cols, projected_lazy_frame).
        """
        import polars as pl

        with _execution_stage(
            execution_context,
            "optimiser_validate_and_project",
        ):
            objective = str(config["objective"])
            constraints = config["constraints"]
            qid_col = str(config.get("quote_id", "quote_id"))
            mult_col = str(config.get("scenario_value", "scenario_value"))
            step_col = str(config.get("scenario_index", "scenario_index"))

            schema = source_lf.collect_schema()
            available_cols = set(schema.names())
            required_cols = {objective, qid_col, mult_col, step_col}
            for cname in constraints:
                required_cols.add(cname)
            detail = _missing_columns_detail(required_cols, available_cols)
            if detail is not None:
                self._record_http_setup_failure(
                    job_id,
                    status_code=400,
                    detail=detail,
                    execution_context=execution_context,
                )
                raise HTTPException(status_code=400, detail=detail)

            constraint_cols = list(constraints.keys()) if isinstance(constraints, dict) else []
            qid_dtype = schema[qid_col]
            detail = _invalid_quote_id_dtype_detail(schema, qid_col)
            if detail is not None:
                self._record_http_setup_failure(
                    job_id,
                    status_code=400,
                    detail=detail,
                    execution_context=execution_context,
                )
                raise HTTPException(status_code=400, detail=detail)

            # ── Value contracts, computed in one streaming pass ─────────────
            # Non-finite objective/constraint/scenario values must fail here
            # as an explicit contract error naming the column — downstream
            # library behaviour silently accepts e.g. a NaN objective and
            # "converges" on wrong totals (C7). Only float-typed columns can
            # carry NaN/inf; the solver consumes Float32 (see cast_map below),
            # so float columns are checked at that precision to also reject
            # Float64 values that overflow to ±inf on the cast. scenario_index
            # is cast to Int32 downstream, so its source values are checked.
            # Genuinely-null values (any dtype) are rejected in the same pass:
            # the external aggregation's treatment of null is undefined.
            self._validate_input_value_contracts(
                source_lf,
                schema,
                job_id,
                quote_id_col=qid_col,
                validate_quote_id_nulls=validate_quote_id_nulls,
                finite_columns=[objective, mult_col, step_col, *constraint_cols],
                cast_to_float32_columns={objective, mult_col, *constraint_cols},
                execution_context=execution_context,
                streaming_chunk_size=streaming_chunk_size,
                profile=ExecutionProfile.OPTIMISER_SETUP,
            )

            solver_cols = [qid_col, step_col, mult_col, objective] + [
                c for c in constraint_cols if c in available_cols
            ]
            cast_map: dict[str, pl.DataType] = {
                step_col: pl.Int32(),
                mult_col: pl.Float32(),
                objective: pl.Float32(),
            }
            for c in constraint_cols:
                cast_map[c] = pl.Float32()
            cast_exprs = [pl.col(c).cast(t) for c, t in cast_map.items()]
            if qid_dtype == pl.String:
                cast_exprs.append(pl.col(qid_col).cast(pl.Categorical))

            scored_lf = source_lf.select(solver_cols).with_columns(cast_exprs)
            return constraint_cols, scored_lf

    def _validate_input_value_contracts(
        self,
        source_lf: Any,
        schema: Any,
        job_id: str,
        *,
        quote_id_col: str,
        validate_quote_id_nulls: bool,
        finite_columns: Iterable[str],
        cast_to_float32_columns: Iterable[str],
        execution_context: ExecutionContext | None,
        streaming_chunk_size: int | None,
        profile: ExecutionProfile,
    ) -> None:
        finite_columns = list(finite_columns)
        non_finite_check_cols = _non_finite_check_columns(schema, finite_columns)
        null_check_cols = _null_check_columns(schema, finite_columns)
        validation_exprs = _value_contract_validation_exprs(
            quote_id_col=quote_id_col,
            validate_quote_id_nulls=validate_quote_id_nulls,
            non_finite_check_cols=non_finite_check_cols,
            null_check_cols=null_check_cols,
            cast_to_float32_cols=set(cast_to_float32_columns),
        )
        if not validation_exprs:
            return

        chunk_size = streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
        with temporary_streaming_chunk_size(chunk_size):
            validation_counts = streaming_collect(
                source_lf.select(validation_exprs),
                execution_context=execution_context,
            )
        if validate_quote_id_nulls:
            null_count = int(validation_counts.get_column(_QUOTE_ID_NULL_COUNT_ALIAS).item())
            if null_count > 0:
                detail = _quote_id_null_detail(null_count)
                self._record_http_setup_failure(
                    job_id,
                    status_code=400,
                    detail=detail,
                    execution_context=execution_context,
                )
                raise HTTPException(status_code=400, detail=detail)

        non_finite_detail = _non_finite_detail_from_counts(
            validation_counts,
            non_finite_check_cols,
        )
        if non_finite_detail is not None:
            self._record_http_setup_failure(
                job_id,
                status_code=400,
                detail=non_finite_detail,
                execution_context=execution_context,
            )
            raise HTTPException(status_code=400, detail=non_finite_detail)

        null_value_detail = _null_value_detail_from_counts(
            validation_counts,
            null_check_cols,
        )
        if null_value_detail is not None:
            self._record_http_setup_failure(
                job_id,
                status_code=400,
                detail=null_value_detail,
                execution_context=execution_context,
            )
            raise HTTPException(status_code=400, detail=null_value_detail)

    def _validate_and_project_auto_range(
        self,
        source_lf: Any,
        config: dict[str, Any],
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> tuple[list[str], Any]:
        """Validate and project only the columns auto-range needs.

        Auto-range computes per-quote extrema for configured constraints. When
        the projected input includes the configured objective, it validates the
        objective for parity with solver input contracts, but it never passes
        objective, scenario index, or scenario value columns to the range
        estimator.
        """
        import polars as pl

        constraints = config["constraints"]
        objective = str(config["objective"])
        qid_col = str(config.get("quote_id", "quote_id"))

        schema = source_lf.collect_schema()
        available_cols = set(schema.names())
        constraint_cols = list(constraints.keys()) if isinstance(constraints, dict) else []
        required_cols = {qid_col, *constraint_cols}
        detail = _missing_columns_detail(required_cols, available_cols)
        if detail is not None:
            self._record_http_setup_failure(
                job_id,
                status_code=400,
                detail=detail,
                execution_context=execution_context,
            )
            raise HTTPException(status_code=400, detail=detail)

        qid_dtype = schema[qid_col]
        detail = _invalid_quote_id_dtype_detail(schema, qid_col)
        if detail is not None:
            self._record_http_setup_failure(
                job_id,
                status_code=400,
                detail=detail,
                execution_context=execution_context,
            )
            raise HTTPException(status_code=400, detail=detail)

        value_check_cols = [*constraint_cols]
        if objective in available_cols:
            value_check_cols.insert(0, objective)
        self._validate_input_value_contracts(
            source_lf,
            schema,
            job_id,
            quote_id_col=qid_col,
            validate_quote_id_nulls=True,
            finite_columns=value_check_cols,
            cast_to_float32_columns=value_check_cols,
            execution_context=execution_context,
            streaming_chunk_size=None,
            profile=ExecutionProfile.AUTO_RANGE,
        )

        auto_range_cols = [qid_col, *constraint_cols]
        cast_exprs = [pl.col(c).cast(pl.Float32()) for c in constraint_cols]
        if qid_dtype == pl.String:
            cast_exprs.append(pl.col(qid_col).cast(pl.Categorical))
        scored_lf = source_lf.select(auto_range_cols).with_columns(cast_exprs)
        return constraint_cols, scored_lf

    @staticmethod
    def _extract_factors(
        lazy_outputs: dict[str, Any],
        config: dict[str, Any],
        mode: str,
        *,
        execution_context: ExecutionContext | None = None,
        streaming_chunk_size: int | None = None,
    ) -> Any:
        """Extract ratebook factors DataFrame (None for online mode)."""
        import polars as pl

        banding_source_id = config.get("banding_source")
        node_id = banding_source_id if isinstance(banding_source_id, str) else None
        with _execution_stage(
            execution_context,
            "optimiser_extract_factors",
            node_id=node_id,
        ):
            if mode != "ratebook":
                return None
            if not isinstance(banding_source_id, str) or not banding_source_id:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Ratebook mode requires a configured banding_source. "
                        "Select a banding node in the Rating Factor Source dropdown."
                    ),
                )
            if banding_source_id not in lazy_outputs:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Configured ratebook banding_source {banding_source_id!r} did not "
                        "produce data. Make sure it is connected to the optimiser node."
                    ),
                )

            source = lazy_outputs[banding_source_id]
            factors_lf = source.lazy() if isinstance(source, pl.DataFrame) else source
            schema = factors_lf.collect_schema()
            available_cols = set(schema.names())
            required_cols = ratebook_factor_required_columns(config)
            missing_cols = sorted(required_cols - available_cols)
            if missing_cols:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Missing columns in ratebook banding source: "
                        f"{missing_cols}. Available: {sorted(available_cols)}"
                    ),
                )

            qid_col = str(config.get("quote_id", "quote_id"))
            raw_factor_columns = config.get("factor_columns") or []
            factor_cols = list(
                dict.fromkeys(
                    column
                    for group in raw_factor_columns
                    for column in group
                    if isinstance(column, str)
                )
            )
            ordered_cols = list(dict.fromkeys([qid_col, *factor_cols]))
            projected = factors_lf.select([pl.col(column) for column in ordered_cols])

            handle = _persist_ratebook_factors_lazy_artifact(
                projected,
                streaming_chunk_size=streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
            )
            if int(handle["row_count"]) == 0:
                _cleanup_orphan_apply_result_artifact(
                    handle,
                    job_id="<setup>",
                    event="empty_ratebook_factor_artifact_cleanup_failed",
                )
                raise HTTPException(
                    status_code=400,
                    detail="Ratebook banding source is empty.",
                )
            if execution_context is not None:
                # If checkpoint raises, the handle never returns and the
                # caller's finally cannot see it — clean up here.
                try:
                    execution_context.checkpoint(
                        label="after_ratebook_factor_sink",
                        node_id=node_id,
                    )
                except BaseException:
                    _cleanup_orphan_apply_result_artifact(
                        handle,
                        job_id="<setup>",
                        event="extract_factors_post_sink_checkpoint_cleanup_failed",
                    )
                    raise
            return handle

    def _build_grid(
        self,
        scored_lf: Any,
        constraint_cols: list[str],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
        streaming_chunk_size: int | None = None,
    ) -> QuoteGrid:
        """Sink scored data to parquet and build the QuoteGrid."""
        from price_contour import build_grid_from_parquet_chunked

        objective = config["objective"]
        qid_col = config.get("quote_id", "quote_id")
        mult_col = config.get("scenario_value", "scenario_value")
        step_col = config.get("scenario_index", "scenario_index")

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".parquet")
        os.close(tmp_fd)
        try:
            with _execution_stage(
                execution_context,
                "optimiser_build_grid",
                node_id=node_id,
            ):
                bounded_sink(
                    scored_lf,
                    tmp_path,
                    streaming_chunk_size=streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
                )
                del scored_lf

                try:
                    chunk_decision = _chunk_size_decision_for_parquet(
                        config,
                        Path(tmp_path),
                        source="optimiser_grid",
                    )
                except ValueError as exc:
                    detail = f"Grid construction failed: {exc}"
                    self._record_http_setup_failure(
                        job_id,
                        status_code=400,
                        detail=detail,
                        execution_context=execution_context,
                    )
                    raise HTTPException(status_code=400, detail=detail) from exc
                chunk_size = chunk_decision.chunk_size
                self._record_setup_chunking(job_id, "optimiser_grid", chunk_decision.provenance)

                build_kwargs = {
                    "quote_id": qid_col,
                    "scenario_index": step_col,
                    "scenario_value": mult_col,
                    "objective": objective,
                }
                quote_grid = build_grid_from_parquet_chunked(
                    tmp_path,
                    constraint_cols,
                    chunk_size,
                    **build_kwargs,
                )
        except HTTPException:
            raise
        except (ExecutionCancelledError, ExecutionMemoryLimitExceededError):
            raise
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            self._record_setup_failure(
                job_id,
                to="contract_error",
                message=str(exc),
                fields=contract_error_job_fields(exc),
                execution_context=execution_context,
            )
            raise contract_error_http_exception(exc) from None
        except BoundedMemoryUnsupportedError as exc:
            detail = f"Grid construction cannot run in bounded streaming mode: {exc}"
            logger.warning(
                "grid_bounded_streaming_unsupported",
                error=str(exc),
                node_id=node_id,
            )
            self._record_http_setup_failure(
                job_id,
                status_code=422,
                detail=detail,
                execution_context=execution_context,
            )
            raise HTTPException(status_code=422, detail=detail) from exc
        except Exception as exc:
            detail = "Grid construction failed. Check the server logs for details."
            logger.error("grid_build_failed", error=str(exc), node_id=node_id, exc_info=True)
            self._record_http_setup_failure(
                job_id,
                status_code=500,
                detail=detail,
                to="error",
                execution_context=execution_context,
            )
            raise HTTPException(status_code=500, detail=detail) from exc
        finally:
            if Path(tmp_path).exists():
                try:
                    os.unlink(tmp_path)
                except Exception as cleanup_exc:
                    logger.warning(
                        "optimiser_grid_temp_cleanup_failed",
                        path=tmp_path,
                        error=str(cleanup_exc),
                        exc_info=True,
                    )

        return quote_grid

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
        factor_level_order: dict[str, list[str]] | None = None,
    ) -> None:
        """Start the solver in a background thread."""
        job_id = ctx.job_id
        node_id = ctx.node_id
        mode = ctx.mode
        streaming_chunk_size = ctx.streaming_chunk_size
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
            execution_context = ExecutionContext(
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
                with temporary_streaming_chunk_size(
                    streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
                ):
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
                            _solve_ratebook(
                                solve_ctx,
                                quote_grid=quote_grid,
                                config=config,
                                ratebook_factors_handle=ratebook_factors_handle,
                                factor_level_order=factor_level_order,
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
                            _solve_online(
                                solve_ctx,
                                quote_grid=quote_grid,
                                config=config,
                            )
            except BackgroundJobStoppedError:
                logger.info("solve_worker_stopped", job_id=job_id)
            except ExecutionCancelledError as exc:
                self._lifecycle.transition(
                    job_id,
                    to="cancelled",
                    message="Cancelled",
                    elapsed_seconds=time.monotonic() - start_time,
                )
                logger.info("solve_worker_cancelled", job_id=job_id, error=str(exc))
            except PUBLIC_CONTRACT_ERROR_TYPES as exc:
                self._lifecycle.transition(
                    job_id,
                    to="contract_error",
                    message=str(exc),
                    fields=contract_error_job_fields(exc),
                    elapsed_seconds=time.monotonic() - start_time,
                )
            except _OptimiserSolveInputError as exc:
                error_msg = f"Data error: {exc}"
                logger.error(
                    "solve_failed",
                    error=str(exc),
                    node_id=node_id,
                    category="data",
                    exc_info=True,
                )
                self._lifecycle.transition(
                    job_id,
                    to="contract_error",
                    message=error_msg,
                    fields={
                        "message": error_msg,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                )
            except _OptimiserSolverExecutionError as exc:
                error_msg = f"Algorithm error: {exc}"
                logger.error(
                    "solve_failed",
                    error=str(exc),
                    node_id=node_id,
                    category="algorithm",
                    exc_info=True,
                )
                self._lifecycle.transition(
                    job_id,
                    to="error",
                    message=error_msg,
                    fields={
                        "message": error_msg,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                )
            except Exception as exc:
                error_msg = f"Unexpected error: {exc}"
                logger.error(
                    "solve_failed",
                    error=str(exc),
                    node_id=node_id,
                    category="unexpected",
                    exc_info=True,
                )
                error_job = self._lifecycle.transition(
                    job_id,
                    to="error",
                    message=error_msg,
                    fields={
                        "message": error_msg,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
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
                    and ratebook_factors_handle.get("kind") == _RATEBOOK_FACTORS_HANDLE_KIND
                ):
                    current = self._store.get_job(job_id)
                    handles = current.get("artifact_handles") if current is not None else None
                    attached = (
                        isinstance(handles, dict)
                        and isinstance(handles.get(_RATEBOOK_FACTORS_HANDLE_KEY), dict)
                        and handles[_RATEBOOK_FACTORS_HANDLE_KEY].get("path")
                        == ratebook_factors_handle.get("path")
                    )
                    if not attached:
                        _cleanup_orphan_apply_result_artifact(
                            ratebook_factors_handle,
                            job_id=job_id,
                            event="solve_worker_orphan_ratebook_factors_cleanup_failed",
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
                and ratebook_factors_handle.get("kind") == _RATEBOOK_FACTORS_HANDLE_KIND
            ):
                _cleanup_orphan_apply_result_artifact(
                    ratebook_factors_handle,
                    job_id=job_id,
                    event="solve_worker_start_orphan_ratebook_factors_cleanup_failed",
                )
            raise HTTPException(
                status_code=500,
                detail="Optimiser worker failed to start. Check the server logs for details.",
            ) from exc
