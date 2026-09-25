"""OptimiserSolveService — orchestrates optimisation solving, extracted from the route handler.

The route handler becomes a thin adapter that delegates to
``OptimiserSolveService.start()``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import gc
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

    from haute.chunking import ChunkPlan

from haute._contracts import Contract, get_column_contract
from haute._env import int_env, optional_int_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
    execution_budget_for_profile,
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
    _sanitize_func_name,
    upstream_node_ids,
)
from haute._interactive_workers import resolve_interactive_execution_mode
from haute._logging import get_logger
from haute._polars_utils import (
    bounded_collect_batches,
    streaming_collect,
)
from haute._sandbox import _get_project_root
from haute._seed_plans import SeedPlan, SeedPlanHandoff, SeedPlanRequest, open_seed_plan
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
    ChunkPlanUnsupportedError,
    ContractMismatchError,
    SchemaMismatchError,
)
from haute.execution import (
    execute_lazy_graph,
    prune_source_switch_edges,
)
from haute.executor import _build_node_fn
from haute.graph_utils import NodeType, flatten_graph, graph_fingerprint
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
    OptimiserSetupError,
    _data_source_feeds_optimiser_through_parallel_edges,
    _execution_stage,
    _explicit_chunk_size_from_config,
    _find_optimiser_node,
    _optimiser_side_input_ids,
    _optimiser_solve_required_columns_by_node,
    _positive_int,
    _resolve_optimiser_data_input_id,
    _setup_execution_target_node_id,
    _solve_columns_by_node,
    build_quote_grid,
    estimate_input_metrics,
    extract_ratebook_factors,
    grid_chunk_decision,
    grid_construction_failures,
    resolve_data_input_frame,
    validate_and_project,
    validate_and_project_auto_range,
    write_solver_input,
)
from haute.routes._optimiser_solver import (
    SolveContext,
    _compute_ratebook_factor_level_order,
    _job_elapsed_seconds,
    _OptimiserSolveInputError,
    _OptimiserSolverExecutionError,
    _solve_online,
    _solve_ratebook,
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
    worker_scratch_directory,
)
from haute.schemas import (
    OptimiserChunkFallback,
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
_JOB_TYPE_KEY = "job_type"


_SOLVE_JOB_TYPE: Literal["solve"] = "solve"
_ESTIMATE_JOB_TYPE: Literal["estimate"] = "estimate"
_FRONTIER_AUTO_RANGE_JOB_TYPE: Literal["frontier_auto_range"] = "frontier_auto_range"
_FRONTIER_RECOMPUTE_JOB_TYPE: Literal["frontier_recompute"] = "frontier_recompute"
_GRAPH_NODE_SETUP_COORDINATION_TYPE = "optimiser_graph_node_setup"
_AUTO_RANGE_BUCKET_COLUMN = "__haute_frontier_auto_range_bucket"
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
    chunk_fallback: dict[str, Any] | None


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
    # False for a structural plan (no row was sampled): it fixes the base
    # node and its columns, but its chunk size is a placeholder.
    sized: bool = True


@dataclass(frozen=True, slots=True)
class _ChunkFallback:
    """A lost chunk optimisation recorded on an auto-range job.

    Chunk ineligibility never fails the request: the classic full-lazy path
    produces identical results, so the job records why chunking was lost and
    the completed result carries the message as a warning.
    """

    code: Literal[
        "chunk_user_code_ineligible",
        "model_score_ineligible",
        "chunk_plan_unsupported",
    ]
    node_id: str | None
    operator: str | None
    reason: str | None
    line: int | None
    column: int | None
    message: str

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "node_id": self.node_id,
            "operator": self.operator,
            "reason": self.reason,
            "line": self.line,
            "column": self.column,
            "message": self.message,
        }


def _chunk_fallback_message(
    *,
    reason: str | None,
    node_id: str | None,
    operator: str | None,
    line: int | None,
) -> str:
    detail = reason or "chunking unavailable"
    if node_id:
        detail = f"{detail} at node '{node_id}'"
    qualifiers = [part for part in (operator, f"line {line}" if line is not None else None) if part]
    if qualifiers:
        detail = f"{detail} ({', '.join(qualifiers)})"
    return (
        f"Auto-range ran without chunking: {detail}; "
        "results are identical but memory use may be higher."
    )


def _optional_positive_int(value: object, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    return _positive_int(value, field=field)


def _solve_timeout_from_config(config: Mapping[str, Any]) -> int | None:
    if "timeout" not in config:
        return _default_solver_timeout()
    return _optional_positive_int(config.get("timeout"), field="timeout")


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
    if (
        isinstance(data_input_id, str)
        and data_input_id
        and not _data_source_feeds_optimiser_through_parallel_edges(graph, node_id, data_input_id)
    ):
        return {data_input_id: required}
    return {node_id: required}


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
    selector_aliases: frozenset[str] = frozenset(),
) -> tuple[bool, _ChunkFallback | None]:
    """Return chunk eligibility for one node plus any lost-optimisation record.

    Structural rejections (a node type the streaming path never handles) are
    silent; a node whose user code cannot be proven chunk-local reports the
    classifier decision so the job can warn about the lost optimisation.
    """
    from haute.chunking import classify_chunk_local_polars_code

    node_type = node.data.nodeType
    config = node.data.config
    if node_type not in _STREAMING_AUTO_RANGE_ALLOWED_NODE_TYPES:
        return False, None
    if node_type == NodeType.MODEL_SCORE:
        # Model-score post-processing and column renames can be arbitrary
        # user-defined transforms; keep them on the full lazy path for now.
        # Each ineligibility has its own stable reason so the warning names the
        # actual blocker rather than a blanket "post-processing" label.
        if config.get("model_reuse_lifetime") != "batch":
            model_score_reason = "model_reuse_lifetime"
        elif (config.get("code") or "").strip():
            model_score_reason = "post_processing_code"
        elif config.get("column_renames"):
            model_score_reason = "column_renames"
        else:
            return True, None
        return False, _ChunkFallback(
            code="model_score_ineligible",
            node_id=node.id,
            operator="modelScore",
            reason=model_score_reason,
            line=None,
            column=None,
            message=_chunk_fallback_message(
                reason=model_score_reason,
                node_id=node.id,
                operator="modelScore",
                line=None,
            ),
        )
    decision = classify_chunk_local_polars_code(
        config.get("code"),
        frame_names=("df",) if node_type == NodeType.SCENARIO_EXPANDER else frame_names,
        selector_aliases=selector_aliases,
    )
    if decision.eligible:
        return True, None
    return False, _ChunkFallback(
        code="chunk_user_code_ineligible",
        node_id=node.id,
        operator=decision.blocking_operator,
        reason=decision.reason,
        line=decision.line,
        column=decision.column,
        message=_chunk_fallback_message(
            reason=decision.reason,
            node_id=node.id,
            operator=decision.blocking_operator,
            line=decision.line,
        ),
    )


def _chunked_base_required_columns(
    streaming_plan: _StreamingAutoRangePlan,
) -> dict[str, frozenset[str]] | None:
    """The column demand a chunked auto-range executes its base node with."""
    if streaming_plan.base_required_columns is None:
        return None
    return {streaming_plan.base_node_id: streaming_plan.base_required_columns}


def _upstream_slice_contains_node_type(
    graph: PipelineGraph,
    node_id: str,
    node_type: NodeType,
    *,
    resolve_node: Callable[[GraphNode, dict[str, GraphNode]], GraphNode],
) -> bool:
    node_map = graph.node_map
    for current_id in (node_id, *upstream_node_ids(node_id, graph.parents_of)):
        raw_node = node_map.get(current_id)
        if raw_node is not None and resolve_node(raw_node, node_map).data.nodeType == node_type:
            return True
    return False


def _build_streaming_auto_range_plan(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
    *,
    mode: str,
    required_columns_by_node: Mapping[str, Iterable[str]],
    sample_row_widths: bool = True,
) -> tuple[_StreamingAutoRangePlan | None, _ChunkFallback | None]:
    """Build a strict online auto-range plan that chunks before expansion.

    The streaming plan is returned only when the shared chunk planner can prove
    the scenario suffix.  Structural mismatches fall back silently; a lost
    chunk optimisation is returned as a fallback record so the job can warn.

    Without *sample_row_widths* and without a configured chunk size, the plan
    is structural: byte-budget sizing, which samples rows of the target
    plan, is left to the worker that runs the job, and the plan is unsized.
    """
    from haute._builders import resolve_instance_node

    if mode != "online":
        return None, None

    try:
        data_input_id = _resolve_online_auto_range_data_input_id(graph, node_id, config)
    except HTTPException:
        return None, None
    if not isinstance(data_input_id, str) or not data_input_id:
        return None, None

    from haute._polars_selectors import preamble_selector_aliases

    node_map = graph.node_map
    selector_aliases = preamble_selector_aliases(graph.preamble or "")
    downstream_to_upstream: list[str] = []
    current_id = data_input_id
    seen: set[str] = set()
    base_node_id: str | None = None
    scenario_node_id: str | None = None
    while True:
        if current_id in seen:
            return None, None
        seen.add(current_id)

        raw_node = node_map.get(current_id)
        if raw_node is None:
            return None, None
        node = resolve_instance_node(raw_node, node_map)
        parent_ids = graph.parents_of.get(current_id, [])
        if len(parent_ids) != 1:
            return None, None
        frame_names = [
            _sanitize_func_name(node_map[parent_id].data.label)
            for parent_id in parent_ids
            if parent_id in node_map
        ]
        eligible, fallback = _streaming_auto_range_node_is_eligible(
            node, frame_names=frame_names, selector_aliases=selector_aliases
        )
        if not eligible:
            return None, fallback

        downstream_to_upstream.append(current_id)
        if node.data.nodeType == NodeType.SCENARIO_EXPANDER:
            base_node_id = parent_ids[0]
            scenario_node_id = current_id
            break
        current_id = parent_ids[0]

    if base_node_id is None or scenario_node_id is None:
        return None, None
    if _upstream_slice_contains_node_type(
        graph,
        base_node_id,
        NodeType.SCENARIO_EXPANDER,
        resolve_node=resolve_instance_node,
    ):
        return None, None

    chain_node_ids = tuple(reversed(downstream_to_upstream))
    try:
        from haute.chunking import ChunkPlanRequest, chunk_plan

        explicit_chunk_size = _auto_range_explicit_chunk_size_from_config(config)
        sized = explicit_chunk_size is not None or sample_row_widths
        structural_chunk_size = explicit_chunk_size if sized else 1
        generic_chunk_plan = chunk_plan(
            ChunkPlanRequest(
                graph=graph,
                target_node_id=data_input_id,
                chunk_start_node_id=base_node_id,
                chunk_size=structural_chunk_size,
                target_chunk_bytes=(
                    None if structural_chunk_size is not None else _auto_range_target_chunk_bytes()
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
        payload = exc.to_payload() if exc.error_code is not None else {}
        # A generic chunk-plan rejection still names the node it rejected in
        # its context; never substitute the optimiser node for it.
        context_node = exc.context.get("node_id") or exc.context.get("target_node_id")
        fallback_node_id = (
            payload.get("node_id") or (str(context_node) if context_node else None) or node_id
        )
        operator = payload.get("blocking_operator") or (
            None if payload else exc.context.get("node_type")
        )
        reason = payload.get("reason") if payload else exc.message
        line = payload.get("line")
        return None, _ChunkFallback(
            code="chunk_plan_unsupported",
            node_id=fallback_node_id,
            operator=operator,
            reason=reason,
            line=line,
            column=payload.get("column"),
            message=_chunk_fallback_message(
                reason=reason,
                node_id=fallback_node_id,
                operator=operator,
                line=line,
            ),
        )
    needed_by_node = generic_chunk_plan.required_columns_by_node
    base_needed = needed_by_node.get(base_node_id)

    required_output_columns_by_node = {
        chain_id: needed_by_node.get(chain_id) for chain_id in chain_node_ids
    }
    return (
        _StreamingAutoRangePlan(
            base_node_id=base_node_id,
            scenario_node_id=scenario_node_id,
            chain_node_ids=chain_node_ids,
            required_output_columns_by_node=required_output_columns_by_node,
            base_required_columns=(frozenset(base_needed) if base_needed is not None else None),
            chunk_plan=generic_chunk_plan,
            sized=sized,
        ),
        None,
    )


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
    if not constraint_cols:
        return {}

    if check_cancelled is not None:
        check_cancelled()
    chunk_size = _positive_int(ctx.chunk_size, field="chunk_size")
    partition_count = _positive_int(ctx.partition_count, field="partition_count")
    execution_context = ctx.execution_context
    selected_lf = scored_lf.select(_frontier_range_batch_columns(quote_id_col, constraint_cols))
    # ``chunk_size`` is the per-batch row count for the auto-range reducer;
    # the underlying scan and collect stream at the process chunk size.
    return _reduce_frontier_range_batches(
        bounded_collect_batches(
            selected_lf,
            chunk_size=chunk_size,
            maintain_order=False,
            execution_context=execution_context,
            stage_name="frontier_range_collect_batch",
        ),
        quote_id_col=quote_id_col,
        constraint_cols=constraint_cols,
        partition_count=partition_count,
        check_cancelled=check_cancelled,
        execution_context=execution_context,
    )


def _frontier_range_batch_columns(quote_id_col: str, constraint_cols: list[str]) -> list[Any]:
    """The columns one range batch carries: the quote id as text, then each constraint."""
    import polars as pl

    return [
        pl.col(quote_id_col).cast(pl.String).alias(quote_id_col),
        *[pl.col(cname) for cname in constraint_cols],
    ]


def _reduce_frontier_range_batches(
    batches: Iterable[pl.DataFrame],
    *,
    quote_id_col: str,
    constraint_cols: list[str],
    partition_count: int,
    check_cancelled: Callable[[], None] | None = None,
    execution_context: ExecutionContext | None = None,
) -> dict[str, dict[str, float]]:
    """Reduce range batches, whichever path produced them, to exact range totals.

    Each batch is reduced to per-quote extrema and hash-partitioned to
    temporary parquet parts, so a quote split across batches is recombined in
    ``finish()`` without one global per-quote aggregate table.
    """
    with _optimiser_artifacts._range_parts_directory() as parts_root:
        accumulator = _ScenarioFrontierRangeAccumulator(
            quote_id_col=quote_id_col,
            constraint_cols=constraint_cols,
            partition_count=partition_count,
            parts_root=parts_root,
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
        solver_input_path: str | None = None
        ratebook_factors_dir: Path | None = None
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
                if resolve_interactive_execution_mode() == "process":
                    solver_input_path = _optimiser_artifacts._new_solver_input_path()
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
                    )
                    ratebook_factors_handle = solve_input.ratebook_factors_handle
                    self._raise_if_solve_stopped(job_id, execution_context=execution_context)
                    quote_grid = self._build_grid_from_parquet(
                        solve_input.path,
                        solve_input.constraint_cols,
                        config,
                        body.node_id,
                        job_id,
                        execution_context=execution_context,
                    )
                else:
                    constraint_cols, scored_lf, ratebook_factors_handle = (
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
                    quote_grid = self._build_grid(
                        scored_lf,
                        constraint_cols,
                        config,
                        body.node_id,
                        job_id,
                        execution_context=execution_context,
                    )
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
    ) -> tuple[list[str], Any, Any]:
        """Execute, resolve, validate and project the solve's input; persist ratebook factors.

        Returns the constraint columns, the projected solver frame, and the
        ratebook factors handle (``None`` in online mode). The frame stays
        valid while *resources* holds the run's seed plan.
        """
        lazy_outputs = self._execute_pipeline(
            body,
            job_id,
            resources,
            required_columns_by_node=required_columns_by_node,
            execution_context=execution_context,
            seed_plan=seed_plan,
        )
        self._raise_if_solve_stopped(job_id, execution_context=execution_context)
        source_lf = self._resolve_data_input_frame(
            lazy_outputs,
            body.graph,
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
        )
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
        return constraint_cols, scored_lf, ratebook_factors_handle

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
        seed_plan: SeedPlanHandoff | None = None,
    ) -> SolveInput:
        """Write the projected, validated solver input to *output_path* (a worker's step).

        The input is always written, never borrowed: a snapshot this run
        captured is released when the worker's plan closes, before the parent
        reads the file. Ratebook factors go to the parent's *ratebook_factors_dir*,
        which the parent removes if the job never adopts them.
        """
        constraint_cols, scored_lf, ratebook_factors_handle = self._prepare_solver_frame(
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
        return SolveInput(
            path=input_path,
            constraint_cols=constraint_cols,
            ratebook_factors_handle=ratebook_factors_handle,
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
            http_exc = memory_limit_http_exception(exc, operation_noun="Auto-range")
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
        # Preparation may build a snapshot and plan chunking, so an already
        # running job for this node answers before that work starts; the same
        # check repeats under the creation lock to close the race.
        with self._start_lock:
            active = self._active_frontier_auto_range_start(setup_job_key)
            if active is not None:
                return active
        prepared = self._prepare_frontier_auto_range(
            body,
            sample_row_widths=resolve_interactive_execution_mode() != "process",
        )
        node = prepared["node"]
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
                "chunk_fallback": prepared.get("chunk_fallback"),
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
                http_exc = memory_limit_http_exception(exc, operation_noun="Auto-range")
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
            timeout = job.get("timeout", _default_auto_range_timeout())
            if start and (time.monotonic() - start) > timeout:
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
        *,
        prepare_snapshot_inputs: bool = True,
        sample_row_widths: bool = True,
    ) -> dict[str, Any]:
        """Validate an auto-range request and plan it, chunked when the chain allows.

        An auto-range worker re-plans with ``prepare_snapshot_inputs=False``:
        its parent prepared the inputs and holds the plan's leases. A parent
        that runs the job in a worker plans with ``sample_row_widths=False``,
        so no row is read in the server process; the worker sizes the chunks.
        """
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
        if prepare_snapshot_inputs:
            self._prepare_auto_range_snapshot_inputs(body.graph, body.node_id)
        streaming_plan, chunk_fallback = _build_streaming_auto_range_plan(
            body.graph,
            body.node_id,
            config,
            mode=mode,
            required_columns_by_node=required_columns_by_node,
            sample_row_widths=sample_row_widths,
        )
        return {
            "node": node,
            "config": config,
            "mode": mode,
            "chunk_size": chunk_size,
            "partition_count": partition_count,
            "timeout": timeout,
            "required_columns_by_node": required_columns_by_node,
            "streaming_plan": streaming_plan,
            "chunk_fallback": (chunk_fallback.payload() if chunk_fallback is not None else None),
        }

    @staticmethod
    def _prepare_auto_range_snapshot_inputs(graph: PipelineGraph, node_id: str) -> None:
        """Prepare snapshot-backed inputs before chunk planning runs schema-only.

        Chunk planning executes the engine under the schema-only declaration,
        which never builds a snapshot, so a missing or stale generation would
        otherwise cost the first run its chunk plan. Preparation needs an
        admitted context to spawn a hard-capped build, and the job's own
        admission does not exist yet, so a scoped admission covers exactly this
        step and is released before the job admits.
        """
        from haute._input_preparation import preparation_base_dir, prepare_input_snapshots
        from haute._path_resolution import runtime_project_root_scope
        from haute._topo import ancestors
        from haute.executor import _resolve_batch_scenario

        scenario = _resolve_batch_scenario(graph) or "batch"
        node_map = graph.node_map
        edges = prune_source_switch_edges(
            graph.edges,
            node_map,
            scenario,
            submodels=graph.submodels,
        )
        order = sorted(ancestors(node_id, edges, set(node_map)))
        try:
            execution_context = create_admitted_execution_context(
                operation="frontier_auto_range_preparation",
                profile=ExecutionProfile.AUTO_RANGE,
            )
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            raise memory_limit_http_exception(exc, operation_noun="Auto-range") from None
        try:
            with runtime_project_root_scope(graph.source_file):
                prepare_input_snapshots(
                    order,
                    node_map,
                    profile=ExecutionProfile.AUTO_RANGE,
                    execution_context=execution_context,
                    base_dir=preparation_base_dir(graph),
                    schema_only=False,
                )
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            raise contract_error_http_exception(exc) from None
        finally:
            execution_context.release_admission(preserve_primary_error=True)

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
        chunk_fallback: dict[str, Any] | None = None,
        execution_context: ExecutionContext | None = None,
        seed_plan: SeedPlanHandoff | None = None,
        isolate: bool = True,
    ) -> OptimiserFrontierAutoRangeResponse:
        """Run one auto-range job: chunk by chunk when a plan was proven, else whole-frame.

        Both paths feed the same range reducer; the job owns admission,
        cancellation, completion and failure classification for either. In
        process mode an isolated worker computes the ranges (*isolate* is
        false only inside that worker, which passes the *seed_plan* handoff).
        """
        del node, mode
        if execution_context is not None:
            return self._run_admitted_frontier_auto_range_job(
                body,
                job_id,
                execution_context=execution_context,
                config=config,
                chunk_size=chunk_size,
                partition_count=partition_count,
                timeout=timeout,
                required_columns_by_node=required_columns_by_node,
                streaming_plan=streaming_plan,
                chunk_fallback=chunk_fallback,
                seed_plan=seed_plan,
                isolate=isolate,
            )
        # A job entered without a caller-owned context still runs under
        # admission: a materialising boundary is never admitted bare. The job
        # owns that admission and returns it on every exit, rather than leaving
        # the memory reservation to garbage collection.
        owned_context = create_admitted_execution_context(
            operation="frontier_auto_range",
            profile=ExecutionProfile.AUTO_RANGE,
            job_id=job_id,
        )
        try:
            bind_running_execution_metrics_publisher(self._store, job_id, owned_context)
            return self._run_admitted_frontier_auto_range_job(
                body,
                job_id,
                execution_context=owned_context,
                config=config,
                chunk_size=chunk_size,
                partition_count=partition_count,
                timeout=timeout,
                required_columns_by_node=required_columns_by_node,
                streaming_plan=streaming_plan,
                chunk_fallback=chunk_fallback,
                seed_plan=seed_plan,
                isolate=isolate,
            )
        finally:
            owned_context.release_admission(preserve_primary_error=True)

    def _run_admitted_frontier_auto_range_job(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        execution_context: ExecutionContext,
        config: dict[str, Any],
        chunk_size: int,
        partition_count: int,
        timeout: int,
        required_columns_by_node: Mapping[str, Iterable[str]],
        streaming_plan: _StreamingAutoRangePlan | None,
        chunk_fallback: dict[str, Any] | None,
        seed_plan: SeedPlanHandoff | None,
        isolate: bool,
    ) -> OptimiserFrontierAutoRangeResponse:
        try:
            execution_context.checkpoint(label="frontier_auto_range_start")
        except ExecutionCancelledError as exc:
            status = str(self._store.require_job(job_id).get("status", "running"))
            raise BackgroundJobStoppedError(job_id, status) from exc
        self._raise_if_frontier_auto_range_stopped(job_id)
        chunked = streaming_plan is not None
        worker_metrics: Mapping[str, Any] | None = None

        # The seed plan entered on this stack is released on every exit.
        try:
            with contextlib.ExitStack() as resources:
                if isolate and resolve_interactive_execution_mode() == "process":
                    ranges, worker_metrics, worker_fallback = self._frontier_ranges_in_worker(
                        body,
                        job_id,
                        streaming_plan=streaming_plan,
                        required_columns_by_node=required_columns_by_node,
                        timeout=timeout,
                        execution_context=execution_context,
                    )
                    if worker_fallback is not None:
                        chunk_fallback = worker_fallback
                elif streaming_plan is not None:
                    ranges = self._chunked_frontier_ranges(
                        body,
                        job_id,
                        resources,
                        config=config,
                        partition_count=partition_count,
                        streaming_plan=streaming_plan,
                        execution_context=execution_context,
                        seed_plan=seed_plan,
                    )
                else:
                    ranges = self._full_frame_frontier_ranges(
                        body,
                        job_id,
                        resources,
                        config=config,
                        chunk_size=chunk_size,
                        partition_count=partition_count,
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
                    warning=(
                        str(chunk_fallback["message"]) if chunk_fallback is not None else None
                    ),
                    chunk_fallback=(
                        OptimiserChunkFallback.model_validate(chunk_fallback)
                        if chunk_fallback is not None
                        else None
                    ),
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
                chunked=chunked,
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
                chunked=chunked,
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

    def _full_frame_frontier_ranges(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        config: dict[str, Any],
        chunk_size: int,
        partition_count: int,
        required_columns_by_node: Mapping[str, Iterable[str]],
        execution_context: ExecutionContext,
        seed_plan: SeedPlanHandoff | None = None,
    ) -> dict[str, dict[str, float]]:
        """Execute the whole pipeline to the data input and reduce it in bounded batches."""
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
            resources,
            required_columns_by_node=required_columns_by_node,
            execution_context=execution_context,
            seed_plan=seed_plan,
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
            body.graph,
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
        return _estimate_scenario_frontier_ranges(
            FrontierAutoRangeContext(
                chunk_size=chunk_size,
                partition_count=partition_count,
                execution_context=execution_context,
            ),
            scored_lf=scored_lf,
            quote_id_col=str(config.get("quote_id", "quote_id")),
            constraint_cols=constraint_cols,
            check_cancelled=lambda: self._raise_if_frontier_auto_range_stopped(job_id),
        )

    def _chunked_frontier_ranges(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        resources: contextlib.ExitStack,
        *,
        config: dict[str, Any],
        partition_count: int,
        streaming_plan: _StreamingAutoRangePlan,
        execution_context: ExecutionContext,
        seed_plan: SeedPlanHandoff | None = None,
    ) -> dict[str, dict[str, float]]:
        """Execute to the base node, then expand, score and reduce one base chunk at a time."""
        import polars as pl

        if not streaming_plan.sized:
            raise RuntimeError("An unsized auto-range chunk plan cannot run in process.")

        from haute._cache import preamble_execution_fingerprint
        from haute.chunking import ChunkRunnerRequest, iter_chunked_frames
        from haute.executor import _compile_preamble, _pipeline_dir

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
        base_required = _chunked_base_required_columns(streaming_plan)
        pinned = preamble_execution_fingerprint(
            body.graph.preamble or "",
            pipeline_dir=_pipeline_dir(body.graph),
        )
        lazy_outputs = self._execute_pipeline(
            body,
            job_id,
            resources,
            required_columns_by_node=base_required,
            target_node_id=streaming_plan.base_node_id,
            execution_context=execution_context,
            preamble_fingerprint=pinned,
            seed_plan=seed_plan,
        )
        self._raise_if_frontier_auto_range_stopped(job_id)
        base_lf = lazy_outputs.get(streaming_plan.base_node_id)
        if base_lf is None:
            raise ValueError(
                "Streaming auto-range base node did not produce a dataframe: "
                f"{streaming_plan.base_node_id!r}."
            )

        qid_col = str(config.get("quote_id", "quote_id"))
        constraints = config.get("constraints")
        constraint_cols = list(constraints.keys()) if isinstance(constraints, dict) else []
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
        preamble_ns = (
            _compile_preamble(
                body.graph.preamble or "",
                pipeline_dir=_pipeline_dir(body.graph),
                execution_fingerprint=pinned,
            )
            or None
        )
        chunk_frames = iter_chunked_frames(
            ChunkRunnerRequest(
                graph=body.graph,
                plan=streaming_plan.chunk_plan,
                build_node_fn=_build_node_fn,
                preamble_ns=preamble_ns,
                execution_context=execution_context,
                start_frame=(base_lf if isinstance(base_lf, pl.LazyFrame) else base_lf.lazy()),
            )
        )

        def scored_chunk_batches() -> Iterator[pl.DataFrame]:
            for chunk_index, chunk in enumerate(chunk_frames, start=1):
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
                    batch = streaming_collect(
                        scored_lf.select(_frontier_range_batch_columns(qid_col, constraint_cols)),
                        execution_context=execution_context,
                    )
                yield batch
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

        return _reduce_frontier_range_batches(
            scored_chunk_batches(),
            quote_id_col=qid_col,
            constraint_cols=constraint_cols,
            partition_count=partition_count,
            check_cancelled=lambda: self._raise_if_frontier_auto_range_stopped(job_id),
            execution_context=execution_context,
        )

    def _frontier_ranges_in_worker(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        streaming_plan: _StreamingAutoRangePlan | None,
        required_columns_by_node: Mapping[str, Iterable[str]],
        timeout: int,
        execution_context: ExecutionContext,
    ) -> tuple[dict[str, dict[str, float]], Mapping[str, Any] | None, dict[str, Any] | None]:
        """Supervise the hard-capped worker that computes the auto-range totals.

        The seed plan the worker's execution adopts is opened here: at the
        streaming base for a chunked job, at the data input otherwise. The
        worker sizes a chunked plan's chunks; when sizing loses the plan it
        reports the fallback, which is recorded on the job, and a whole-frame
        worker runs under a whole-frame seed plan. The job's remaining timeout
        bounds each worker. Returns the totals, the worker's execution metrics
        and the fallback, if any.
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
        outcome = self._frontier_ranges_attempt(
            body,
            job_id,
            streaming_plan=streaming_plan,
            required_columns_by_node=required_columns_by_node,
            timeout=timeout,
            execution_context=execution_context,
        )
        chunk_fallback = outcome.chunk_fallback
        if chunk_fallback is not None:
            self._store.atomic_update(
                job_id,
                {"chunk_fallback": chunk_fallback},
                expected_status="running",
            )
            outcome = self._frontier_ranges_attempt(
                body,
                job_id,
                streaming_plan=None,
                required_columns_by_node=required_columns_by_node,
                timeout=timeout,
                execution_context=execution_context,
            )
            if outcome.chunk_fallback is not None:
                raise RuntimeError("A whole-frame auto-range worker reported a chunk fallback")
        if outcome.ranges is None:
            raise RuntimeError("Auto-range worker returned neither ranges nor a failure")
        return outcome.ranges, outcome.execution_metrics, chunk_fallback

    def _frontier_ranges_attempt(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        streaming_plan: _StreamingAutoRangePlan | None,
        required_columns_by_node: Mapping[str, Iterable[str]],
        timeout: int,
        execution_context: ExecutionContext,
    ) -> FrontierAutoRangeWorkerOutcome:
        """Open one attempt's seed plan and run one auto-range worker under it."""
        self._raise_if_frontier_auto_range_stopped(job_id)
        # The attempt's seed plan is held until its worker has exited.
        with contextlib.ExitStack() as resources:
            if streaming_plan is not None:
                handoff = self._open_setup_seed_plan(
                    body,
                    job_id,
                    resources,
                    required_columns_by_node=_chunked_base_required_columns(streaming_plan),
                    target_node_id=streaming_plan.base_node_id,
                    execution_context=execution_context,
                )
            else:
                handoff = self._open_setup_seed_plan(
                    body,
                    job_id,
                    resources,
                    required_columns_by_node=required_columns_by_node,
                    execution_context=execution_context,
                )
            self._raise_if_frontier_auto_range_stopped(job_id)
            remaining = timeout - self._job_elapsed(job_id)
            if remaining <= 0:
                raise self._time_out_frontier_auto_range(job_id, timeout)
            with worker_scratch_directory() as scratch_dir:
                outcome = self._run_optimiser_worker(
                    frontier_auto_range_worker,
                    FrontierAutoRangeWorkerRequest(
                        body=body,
                        project_root=str(_get_project_root()),
                        seed_plan=handoff,
                        chunked=streaming_plan is not None,
                        scratch_dir=scratch_dir,
                    ),
                    job_id=job_id,
                    node_id=body.node_id,
                    execution_context=execution_context,
                    timeout_seconds=remaining,
                    process_name="haute-optimiser-auto-range",
                    on_timeout=lambda: self._time_out_frontier_auto_range(job_id, timeout),
                )
        if not isinstance(outcome, FrontierAutoRangeWorkerOutcome):
            raise RuntimeError(f"Auto-range worker returned {type(outcome).__name__}")
        if outcome.failure is not None:
            raise OptimiserWorkerFailureError(outcome.failure)
        return outcome

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
                "Reduce the input size or increase HAUTE_AUTO_RANGE_TIMEOUT."
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

        The plan decides separately which consumed nodes it can capture.
        Auto-range captures, at whichever nodes it captures, the columns the
        following solve reads there — the data input, or the streaming base
        below the scenario expander — so the solve seeds instead of
        recomputing. A column a node does not produce is simply not captured.
        """
        from haute.executor import _resolve_batch_scenario

        # Resolve scenario: optimiser runs on batch data, not live.
        scenario = _resolve_batch_scenario(body.graph) or "batch"
        execution_target_node_id, _preserved, consumed_node_ids = self._setup_execution_scope(
            body, target_node_id
        )
        capture_columns_by_node: Mapping[str, frozenset[str]] = {}
        if (
            execution_context is not None
            and execution_context.profile == ExecutionProfile.AUTO_RANGE
        ):
            optimiser_node = _find_optimiser_node(body.graph, body.node_id)
            if str(optimiser_node.data.config.get("mode", "online")) in {"online", "ratebook"}:
                capture_columns_by_node = _solve_columns_by_node(
                    body.graph,
                    body.node_id,
                    optimiser_node.data.config,
                    source=scenario,
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
            capture_columns_by_node=capture_columns_by_node,
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
    ) -> tuple[list[str], Any]:
        """Validate and project only the columns auto-range needs."""
        with self._recorded_setup_failures(job_id, execution_context):
            return validate_and_project_auto_range(
                source_lf,
                config,
                execution_context=execution_context,
            )

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

    def _build_grid(
        self,
        scored_lf: Any,
        constraint_cols: list[str],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> QuoteGrid:
        """Sink scored data to parquet and build the QuoteGrid."""
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
            return self._build_grid_from_parquet(
                input_path,
                constraint_cols,
                config,
                node_id,
                job_id,
                execution_context=execution_context,
            )
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
    ) -> QuoteGrid:
        """Build the solver's QuoteGrid from a written or borrowed solver-input parquet."""
        with (
            self._recorded_setup_failures(job_id, execution_context),
            grid_construction_failures(node_id),
            _execution_stage(execution_context, "optimiser_build_grid", node_id=node_id),
        ):
            decision = grid_chunk_decision(config, input_path)
            self._record_setup_chunking(job_id, "optimiser_grid", decision.provenance)
            return build_quote_grid(
                input_path,
                constraint_cols,
                config,
                decision.chunk_size,
                execution_context=execution_context,
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
        factor_level_order: dict[str, list[str]] | None = None,
    ) -> None:
        """Start the solver in a background thread."""
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
                    to=contract_error_terminal_reason(exc),
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
