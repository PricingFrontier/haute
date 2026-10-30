"""Pipeline CRUD, preview, trace, and sink endpoints."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from haute._cache import GraphFingerprintMemo, canonical_json
from haute._databricks_credentials import DatabricksConfigError
from haute._databricks_output import DatabricksWriteError
from haute._editor_identities import (
    NamedCandidate,
    NamingCandidate,
    name_candidates,
    resolve_editor_identity,
)
from haute._env import int_env
from haute._event_bus import DocumentUpdateExclusion, default_bus
from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_admitted_execution_context,
    create_isolated_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._flatten import flatten_executable_graph
from haute._global_constants import reference_problem
from haute._graph_shape import validate_pipeline_graph_shape_contracts
from haute._graph_utils import upstream_node_ids
from haute._hashing import content_hash_bytes
from haute._interactive_workers import (
    InteractiveWorkerCrashedError,
    InteractiveWorkerError,
    InteractiveWorkerMemoryLimitError,
    InteractiveWorkerRemoteError,
    InteractiveWorkerStoppedError,
    InteractiveWorkerTimeoutError,
    resolve_interactive_execution_mode,
    run_in_interactive_worker,
)
from haute._io import read_user_text
from haute._json_safe import rows_to_json_safe
from haute._logging import get_logger
from haute._native_memory_limit import NativeMemoryLimitUnsupportedError
from haute._node_snapshots import NodeSnapshotStore
from haute._path_resolution import (
    RuntimePathError,
    resolve_runtime_file_path,
    runtime_project_root_scope,
)
from haute._pipeline_recovery import (
    empty_pipeline_editor_document,
    name_violations_payload,
    pipeline_document_fingerprint,
)
from haute._pipeline_repair import (
    PipelineRepairError,
    apply_recover_unavailable_node_plan,
    apply_remove_connection_plan,
    apply_remove_unavailable_node_plan,
)
from haute._pipeline_repair_actions import apply_scoped_node_save
from haute._pipeline_settings import (
    SETTINGS_PATH,
    apply_chunk_rows,
    automatic_pipeline_settings,
    project_pipeline_settings,
    read_pipeline_settings,
    update_pipeline_settings,
)
from haute._polars_io_registry import (
    PolarsIoConfigError,
    data_output_group,
    format_for_config,
    is_databricks_output,
    validate_data_output_config,
)
from haute._polars_steps import (
    PolarsStepError,
    RenderedSteps,
    ResolvedFreeCode,
    render_polars_steps,
    resolve_free_code_columns,
)
from haute._sandbox import _get_project_root, contained_path
from haute._seed_plans import (
    ListedSeed,
    ReadGeneration,
    SeedPlanHandoff,
    open_seed_plan,
    preview_input_node_ids,
)
from haute._source_cache import new_staging_token
from haute._step_progress import (
    StepProgress,
    StepProgressReporter,
    current_job_progress_reporter,
)
from haute._submodel_instances import qualified_runtime_node_id, resolve_submodel_instances
from haute._support_code_names import name_violations, utility_reader
from haute._topo import ancestors
from haute._types import GlobalConstant, GraphEdge, GraphNode, NodeData, SubmodelDefinition
from haute._workbench_config import WorkbenchError
from haute._workbench_tables import WorkbenchTablesError
from haute._worker_isolation import (
    IsolatedWorkerCrashedError,
    IsolatedWorkerMemoryLimitExceededError,
    IsolatedWorkerMemoryLimitUnsupportedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
    isolated_worker_failure_is_memory,
    isolated_worker_memory_detail,
    resolve_worker_memory_enforcement,
    run_isolated_worker,
    worker_config_for_memory_policy,
)
from haute.errors import (
    BoundedMemoryUnsupportedError,
    ConfigError,
    ContractColumnsMissingError,
    ContractMismatchError,
    ParseError,
    SchemaMismatchError,
    SeedPlanExpiredError,
    TraceNodeFailedError,
    TraceRequestError,
    TraceRowAmbiguousError,
    TraceRowMismatchError,
    TraceRowOutOfRangeError,
    TraceTargetNotFoundError,
    TraceTargetUnsupportedError,
    failing_node,
    is_public_contract_error,
)
from haute.execution import _runtime_input_path_fields, prune_source_switch_edges
from haute.executor import (
    DataOutputDestinationExistsError,
    DataOutputDurabilityError,
    DataOutputPublicationError,
    PreparedDataOutput,
    PreviewProjectionError,
    _preview_required_columns_by_node,
    commit_prepared_data_output,
    data_output_seed_plan_request,
    discard_data_output_staging_path,
    discard_prepared_data_output,
    execute_graph,
    new_data_output_staging_path,
    prepare_data_output,
    resolve_data_output_path,
    validate_prepared_data_output_identity,
)
from haute.graph_utils import (
    NodeType,
    PipelineGraph,
    graph_fingerprint,
)
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    SEED_PLAN_EXPIRED_HTTP_STATUS,
    contract_error_http_exception,
    contract_error_payload,
)
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    connection_owner,
    discover_pipelines,
    load_pipeline_editor_document,
    lookup_pipeline_by_name,
    pipeline_dir,
    pipeline_document_update_payload,
    raise_pipeline_not_found,
    save_lock,
    shared_document_updates,
)
from haute.routes._isolated_worker_async import (
    WorkerCancellationGate,
    run_cancellable_worker_transaction,
)
from haute.routes._preview_progress import preview_progress
from haute.routes._runtime_path_errors import runtime_path_http_exception
from haute.routes._save_pipeline import SavePipelineService, StaleDocumentRevisionError
from haute.routes._supersession import SupersededRequestError, SupersessionCoordinator
from haute.routes._synchronous_analysis import await_until_disconnected
from haute.routes._timeouts import (
    BlockingWorkTimeoutError,
    run_blocking_with_response_timeout,
)
from haute.schemas import (
    ColumnInfo,
    EditorIdentitiesRequest,
    EditorIdentitiesResponse,
    EditorIdentityResponseNode,
    ExecutionMetricsPayload,
    FreeCodeColumns,
    NodeMemoryInfo,
    NodeTimingInfo,
    OutputDestinationRequest,
    OutputDestinationResponse,
    PipelineEditorDocument,
    PipelineNodeSaveRequest,
    PipelineRepairApplyResponse,
    PipelineRepairRecoverRequest,
    PipelineRepairRemoveConnectionRequest,
    PipelineRepairRemoveRequest,
    PipelineSettingsAutomatic,
    PipelineSettingsResponse,
    PipelineSettingsValues,
    PipelineSummary,
    PolarsFreeCodeColumnsRequest,
    PolarsFreeCodeColumnsResponse,
    PolarsStepsRenderRequest,
    PolarsStepsRenderResponse,
    PreviewInputsRequest,
    PreviewInputsResponse,
    PreviewNodeRequest,
    PreviewNodeResponse,
    PreviewProgressResponse,
    PreviewSeedPlanEntry,
    ReadJsonRequest,
    ReadJsonResponse,
    RecoveryPreviewRequest,
    SavePipelineRequest,
    SavePipelineResponse,
    TraceRequest,
    TraceResponse,
    TraceSeedPlanEntry,
    WriteOutputRequest,
    WriteOutputResponse,
)
from haute.trace import execute_trace, trace_result_to_dict

logger = get_logger(component="server.pipeline")

_PUBLIC_REMOTE_ERROR_CODES = {
    (error_type.__module__, error_type.__name__): error_type.error_code
    for error_type in PUBLIC_CONTRACT_ERROR_TYPES
}
_MEMORY_REMOTE_ERROR_CODES = {
    (ExecutionAdmissionError.__module__, ExecutionAdmissionError.__name__): "memory_limit",
    (
        ExecutionMemoryLimitExceededError.__module__,
        ExecutionMemoryLimitExceededError.__name__,
    ): "memory_limit",
}
_PREVIEW_PROJECTION_REMOTE_IDENTITY = (
    PreviewProjectionError.__module__,
    PreviewProjectionError.__name__,
)
_PREVIEW_TARGET_REMOTE_IDENTITY = (__name__, "_PreviewTargetNotReturnedError")
_TRACE_CONTRACT_REMOTE_IDENTITIES = frozenset(
    {
        (ContractMismatchError.__module__, ContractMismatchError.__name__),
        (ContractColumnsMissingError.__module__, ContractColumnsMissingError.__name__),
        (SchemaMismatchError.__module__, SchemaMismatchError.__name__),
    }
)
# What each of the trace's refusals answers: 409 when the clicked row has changed since
# its preview (the browser refreshes the preview), 400 for a row or target the trace
# cannot take, 404 for a target the graph lacks. By type in a thread; out of a worker,
# by the exact remote type and the code its payload carries.
_TRACE_REFUSAL_STATUS: dict[type[TraceRequestError], int] = {
    TraceRowMismatchError: 409,
    TraceRowAmbiguousError: 409,
    TraceRowOutOfRangeError: 400,
    TraceTargetUnsupportedError: 400,
    TraceTargetNotFoundError: 404,
}
_TRACE_REFUSAL_REMOTE = {
    (refusal.__module__, refusal.__name__): refusal for refusal in _TRACE_REFUSAL_STATUS
}

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.post("/pipeline/editor-identities", response_model=EditorIdentitiesResponse)
async def resolve_pipeline_editor_identities(
    body: EditorIdentitiesRequest,
) -> EditorIdentitiesResponse:
    """Resolve editor identities without reading or writing project state.

    With the document's naming context (``graph``), also report the name
    violations that remain once the request's nodes are applied.
    """
    # The one project state this reads, with a naming context: the utility
    # files the graph's support code star-imports, as save and the executor do.
    read_utility = utility_reader(pipeline_dir(), Path.cwd().resolve())
    try:
        candidates = [
            NamingCandidate(
                node_id=node.node_id,
                label=node.label,
                node_type=node.node_type,
                alias=node.alias,
                submodel=node.submodel,
            )
            for node in body.nodes
        ]
        if body.graph is None:
            named = [NamedCandidate(c.label, c.alias, None) for c in candidates]
            graph = None
        else:
            named, graph = await run_in_threadpool(
                name_candidates, body.graph, candidates, read_utility, allocate=body.allocate
            )
        identities: list[EditorIdentityResponseNode] = []
        for node, name in zip(body.nodes, named, strict=True):
            identity = resolve_editor_identity(
                node_type=node.node_type,
                label=name.label,
                source_handles=node.source_handles,
                alias=name.alias,
            )
            identities.append(
                EditorIdentityResponseNode(
                    node_id=node.node_id,
                    label=name.label,
                    alias=name.alias,
                    collision=name.collision,
                    function_name=identity.function_name,
                    config_reference=identity.config_reference,
                    default_input_name=identity.default_input_name,
                    source_handle_input_names=identity.source_handle_input_names,
                )
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    violations = (
        None if graph is None else name_violations_payload(name_violations(graph, read_utility))
    )
    return EditorIdentitiesResponse(identities=identities, violations=violations)


@router.post("/pipeline/polars-steps/render", response_model=PolarsStepsRenderResponse)
async def render_polars_steps_endpoint(
    body: PolarsStepsRenderRequest,
) -> PolarsStepsRenderResponse:
    """Render a step list to Polars code without reading or writing project state.

    Rendering runs no authored code, so the code text never waits on a
    snippet; the columns after each free-code step have their own endpoint.
    """
    try:
        rendered = render_polars_steps(body.steps, body.input_names, start=body.start)
    except PolarsStepError as exc:
        return PolarsStepsRenderResponse(ok=False, step_index=exc.step_index, message=exc.message)
    if body.global_constants is not None:
        constants = {constant.name: constant for constant in body.global_constants}
        for reference in rendered.constant_references:
            problem = reference_problem(reference, constants)
            if problem is not None:
                return PolarsStepsRenderResponse(
                    ok=False, step_index=reference.step_index, message=problem
                )
    return PolarsStepsRenderResponse(
        ok=True,
        code=rendered.code,
        step_lines=[list(span) for span in rendered.step_lines],
    )


#: How long resolving a step list's free-code columns may run. The frames are
#: empty, so ordinary code finishes well inside it; code still running at the
#: deadline is stopped with its worker. Read per request.
FREE_CODE_COLUMNS_TIMEOUT_SECONDS = 5.0

# One running resolution per node: a newer request stops an older one.
_free_code_supersession = SupersessionCoordinator()


def _unresolved_free_code(steps: list[dict[str, Any]], message: str) -> list[ResolvedFreeCode]:
    """The same reason for every free-code step of *steps*."""
    return [
        ResolvedFreeCode(index, None, message)
        for index, step in enumerate(steps)
        if step["kind"] == "free_code"
    ]


def _free_code_worker_failure(exc: InteractiveWorkerError, timeout: float) -> str:
    """A parent-authored, data-free reason for a worker that did not answer."""
    if isinstance(exc, InteractiveWorkerTimeoutError):
        return f"The code did not finish within {timeout:g} seconds."
    if exc.terminal_reason == "memory_limited":
        return "Running it used more memory than a preview may."
    logger.error(
        "free_code_columns_worker_failed",
        error_class=type(exc).__name__,
        error_message=str(exc),
    )
    return "The preview worker stopped before it finished."


async def _resolve_free_code_columns_isolated(
    body: PolarsFreeCodeColumnsRequest,
    rendered: RenderedSteps,
    token: ExecutionCancellationToken,
    affinity_key: tuple[str, str],
    timeout: float,
) -> list[ResolvedFreeCode]:
    """Admit a preview, then resolve in the interactive worker under its budget.

    A timeout, memory limit or worker failure becomes every free-code step's
    reason; only a stop (a newer request, a disconnect) is raised. In thread
    mode the deadline bounds the response, not the thread, which keeps the
    admission until it ends.
    """
    resolve = partial(
        resolve_free_code_columns,
        body.steps,
        rendered,
        start=body.start,
        input_names=body.input_names,
        input_columns={
            name: [(c.name, c.dtype) for c in columns]
            for name, columns in body.input_columns.items()
        },
        frame_columns=[(c.name, c.dtype) for c in body.frame_columns],
        global_constants=body.global_constants,
        global_constants_error=body.global_constants_error,
        source=body.source,
    )
    run = _InteractiveRun("polars_free_code_columns", token)
    try:
        return await _run_interactive(
            run,
            worker=resolve,
            worker_args=lambda _budget: (),
            in_thread=lambda _context: resolve(),
            affinity_key=affinity_key,
            timeout=timeout,
        )
    except ExecutionAdmissionError:
        return _unresolved_free_code(body.steps, "There is not enough free memory to run it now.")
    except InteractiveWorkerStoppedError:
        raise
    except InteractiveWorkerError as exc:
        return _unresolved_free_code(body.steps, _free_code_worker_failure(exc, timeout))
    except BlockingWorkTimeoutError as exc:
        run.release(once_done=exc.background_task)
        return _unresolved_free_code(
            body.steps, f"The code did not finish within {timeout:g} seconds."
        )
    finally:
        run.release()


@router.post(
    "/pipeline/polars-steps/free-code-columns",
    response_model=PolarsFreeCodeColumnsResponse,
)
async def resolve_free_code_columns_endpoint(
    body: PolarsFreeCodeColumnsRequest,
    http_request: Request,
) -> PolarsFreeCodeColumnsResponse:
    """The columns of ``df`` after each free-code step, or why they are unknown.

    Resolving them runs the authored code, so it runs where a preview does:
    in the interactive worker, under the preview budget and a short deadline,
    one request per node at a time.
    """
    try:
        rendered = render_polars_steps(body.steps, body.input_names, start=body.start)
    except PolarsStepError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    if not any(step["kind"] == "free_code" for step in body.steps):
        return PolarsFreeCodeColumnsResponse(free_code_columns=[])
    key = ("polars_free_code_columns", body.node_id)
    token = ExecutionCancellationToken()
    timeout = FREE_CODE_COLUMNS_TIMEOUT_SECONDS
    started = False

    async def _resolve() -> list[ResolvedFreeCode]:
        nonlocal started
        started = True
        return await _resolve_free_code_columns_isolated(body, rendered, token, key, timeout)

    try:
        resolved = await await_until_disconnected(
            http_request,
            _free_code_supersession.run_latest(
                key,
                _resolve,
                cancel_active=token.cancel,
                superseded_message=(
                    "A newer free-code column request for this node replaced this one."
                ),
            ),
            cancel=token.cancel,
            # Until it starts, a request waiting behind an older one holds nothing.
            started=lambda: started,
            detail="The client closed the free-code column request before it finished.",
        )
    except (SupersededRequestError, InteractiveWorkerStoppedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return PolarsFreeCodeColumnsResponse(
        free_code_columns=[
            FreeCodeColumns(
                step_index=entry.step_index,
                columns=None
                if entry.columns is None
                else [ColumnInfo(name=name, dtype=dtype) for name, dtype in entry.columns],
                message=entry.message,
            )
            for entry in resolved
        ]
    )


def _pipeline_time_limit() -> float:
    """The pipeline time limit in seconds, read from the pipeline settings per request."""
    return project_pipeline_settings().pipeline_time_limit_seconds


_preview_supersession = SupersessionCoordinator()
_trace_supersession = SupersessionCoordinator()


_PREVIEW_MAX_CONCURRENCY = int_env("HAUTE_PREVIEW_MAX_CONCURRENCY", 2)
_TRACE_MAX_CONCURRENCY = int_env("HAUTE_TRACE_MAX_CONCURRENCY", 2)
_preview_work_slots = asyncio.Semaphore(_PREVIEW_MAX_CONCURRENCY)
_trace_work_slots = asyncio.Semaphore(_TRACE_MAX_CONCURRENCY)


def _ensure_printable_lookup_id(value: str | None, field_name: str) -> None:
    if value is not None and not value.isprintable():
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} contains control characters",
        )


def _validate_runtime_input_paths(graph: PipelineGraph) -> None:
    """Reject API-submitted input paths that resolve outside the project root.

    The runtime-input path fields for each node are taken from the executor's
    shared enumeration (:func:`haute.execution._runtime_input_path_fields`)
    rather than a hand-maintained map. This confines every local file the
    executor consumes at
    preview/trace time — flat-file ``apiInput`` / ``dataInput`` / ``externalFile``
    ``path``, ``modelScore`` ``feature_contract_path``, and file-sourced
    ``optimiserApply`` artifacts — to the project root. The same request check
    rejects traversal-shaped MLflow ``modelScore.artifact_path`` identifiers;
    execution leaves those external identifiers unchanged.
    """
    if graph.source_file:
        try:
            resolve_runtime_file_path(
                graph.source_file,
                project_root=_get_project_root(),
                prefer="project",
                enforce_project_root=True,
            )
        except RuntimePathError as exc:
            raise runtime_path_http_exception(exc) from None

    for node in graph.nodes:
        config = node.data.config
        for key in _runtime_input_path_fields(node):
            raw_path = config.get(key)
            if not isinstance(raw_path, str) or not raw_path:
                continue

            try:
                resolve_runtime_file_path(
                    raw_path,
                    source_file=graph.source_file,
                    prefer="project",
                    enforce_project_root=True,
                )
            except RuntimePathError as exc:
                raise runtime_path_http_exception(exc) from None


def _validate_data_output_path(
    graph: PipelineGraph,
    output_node: Any,
    *,
    project_root: Path,
) -> None:
    raw_path = output_node.data.config.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return
    try:
        resolve_data_output_path(
            graph,
            output_node.data.config,
            project_root=project_root,
        )
    except RuntimePathError as exc:
        raise runtime_path_http_exception(exc) from None


def _prepare_data_output_request(
    graph_payload: Any,
    node_id: str,
) -> tuple[PipelineGraph, Any, dict[str, Any], Path]:
    """Validate the graph/output target shared by destination preview and write."""
    graph = flatten_executable_graph(graph_payload)
    _ensure_source_file(graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")
    _ensure_printable_lookup_id(node_id, "node_id")
    _validate_runtime_input_paths(graph)
    output_node = graph.node_map.get(node_id)
    if output_node is None:
        raise HTTPException(status_code=404, detail=f"Data Output node '{node_id}' not found")
    if output_node.data.nodeType != NodeType.DATA_OUTPUT:
        raise HTTPException(
            status_code=400,
            detail=f"Node '{node_id}' is not a Data Output",
        )
    try:
        config = validate_data_output_config(output_node.data.config)
    except (PolarsIoConfigError, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Invalid Data Output configuration.",
        ) from None
    project_root = _get_project_root().resolve()
    _validate_data_output_path(graph, output_node, project_root=project_root)
    return graph, output_node, config, project_root


def _supersession_key(
    operation: str,
    graph: PipelineGraph,
    source: str,
    *,
    memo: GraphFingerprintMemo | None = None,
) -> tuple[str, ...]:
    return (
        operation,
        graph.source_file or "",
        source,
        graph_fingerprint(graph, memo=memo),
    )


def _preview_supersession_key(
    graph: PipelineGraph,
    source: str,
    node_id: str,
    row_limit: int,
    requested_preview_columns: list[str] | None,
    port_label: str | None,
    port_labels: list[str] | None = None,
    *,
    memo: GraphFingerprintMemo | None = None,
) -> tuple[str, ...]:
    requested_columns = tuple(requested_preview_columns or ())
    return (
        *_supersession_key("preview", graph, source, memo=memo),
        "node",
        node_id,
        "row_limit",
        str(row_limit),
        "requested_preview_columns",
        *requested_columns,
        # A different frame selection is a distinct request, not a newer
        # version of the same one: several frames, in their order, too.
        "port_label",
        repr(port_label),
        "port_labels",
        repr(None if port_labels is None else tuple(port_labels)),
    )


def _trace_row_values_fingerprint(row_values: dict[str, Any] | None) -> str:
    if row_values is None:
        return ""
    payload = canonical_json(row_values)
    return content_hash_bytes(payload.encode())


def _trace_supersession_key(
    graph: PipelineGraph,
    source: str,
    target_node_id: str | None,
    row_index: int,
    column: str | None,
    row_limit: int,
    row_values: dict[str, Any] | None,
    seed_plan: list[TraceSeedPlanEntry] | None = None,
    *,
    port_label: str | None = None,
    memo: GraphFingerprintMemo | None = None,
) -> tuple[str, ...]:
    return (
        *_supersession_key("trace", graph, source, memo=memo),
        "seed_plan",
        *(f"{entry.node_id}={entry.generation_id}" for entry in seed_plan or ()),
        "target",
        target_node_id or "",
        "port_label",
        port_label or "",
        "row_index",
        str(row_index),
        "column",
        column or "",
        "row_limit",
        str(row_limit),
        "row_values",
        _trace_row_values_fingerprint(row_values),
    )


def _interactive_affinity_key(
    graph: PipelineGraph,
    source: str,
    *,
    memo: GraphFingerprintMemo | None = None,
) -> tuple[str, ...]:
    """Route preview and trace for one lineage to the same warm cache owner."""
    return _supersession_key("interactive", graph, source, memo=memo)


class _PreviewTargetNotReturnedError(RuntimeError):
    """The executor completed but omitted the requested target result."""


def _interactive_memory_detail(operation: str, *, reason: str) -> dict[str, object]:
    """Build the parent-authored, data-free 507 detail for a memory outcome."""
    return {
        "error_code": "memory_limit",
        "operation": operation,
        "reason": reason,
    }


def _raise_interactive_worker_crash_http_error(
    exc: InteractiveWorkerCrashedError,
    *,
    operation: str,
) -> NoReturn:
    """Map a pool-worker crash to 507 when its exit code looks memory-limited."""
    if exc.terminal_reason == "memory_limited":
        raise HTTPException(
            status_code=507,
            detail=_interactive_memory_detail(
                operation,
                reason="worker_may_have_exceeded_memory_limit",
            ),
        ) from None
    logger.error(
        "interactive_worker_crashed",
        operation=operation,
        exitcode=exc.exitcode,
    )
    raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None


def _raise_interactive_remote_http_error(
    exc: InteractiveWorkerRemoteError,
    *,
    operation: str,
) -> NoReturn:
    payload = exc.public_payload
    identity = (exc.remote_module, exc.remote_type)
    expected_memory_code = _MEMORY_REMOTE_ERROR_CODES.get(identity)
    if (
        payload is not None
        and expected_memory_code is not None
        and payload.get("error_code") == expected_memory_code
    ):
        raise HTTPException(status_code=507, detail=payload) from None
    # Memory outcomes the child cannot curate: classified by exact identity
    # and answered with a parent-authored detail, never the child payload. A
    # same-named exception from any other module stays an internal 500.
    if identity == ("builtins", "MemoryError"):
        raise HTTPException(
            status_code=507,
            detail=_interactive_memory_detail(operation, reason="worker_memory_exhausted"),
        ) from None
    if identity == (
        NativeMemoryLimitUnsupportedError.__module__,
        NativeMemoryLimitUnsupportedError.__name__,
    ):
        raise HTTPException(
            status_code=507,
            detail=_interactive_memory_detail(operation, reason="native_memory_cap_unavailable"),
        ) from None
    expected_public_code = _PUBLIC_REMOTE_ERROR_CODES.get(identity)
    if (
        payload is not None
        and expected_public_code is not None
        and payload.get("error_code") == expected_public_code
    ):
        status_code = (
            SEED_PLAN_EXPIRED_HTTP_STATUS
            if expected_public_code == SeedPlanExpiredError.error_code
            else 422
        )
        raise HTTPException(status_code=status_code, detail=payload) from None
    if operation == "pipeline_preview":
        if identity == _PREVIEW_PROJECTION_REMOTE_IDENTITY:
            raise HTTPException(status_code=400, detail=exc.remote_message) from None
        if identity == _PREVIEW_TARGET_REMOTE_IDENTITY:
            raise HTTPException(status_code=404, detail=exc.remote_message) from None
    if operation == "pipeline_trace":
        if identity in _TRACE_CONTRACT_REMOTE_IDENTITIES:
            raise HTTPException(status_code=422, detail=exc.remote_message) from None
        refusal = _TRACE_REFUSAL_REMOTE.get(identity)
        if (
            refusal is not None
            and payload is not None
            and payload.get("error_code") == refusal.error_code
        ):
            raise HTTPException(
                status_code=_TRACE_REFUSAL_STATUS[refusal], detail=str(payload["message"])
            ) from None
    # The client gets a generic 500; the server log keeps what the worker raised.
    logger.error(
        "interactive_worker_remote_failure",
        operation=operation,
        remote_type=exc.remote_type,
        remote_module=exc.remote_module,
        remote_message=exc.remote_message,
        remote_traceback=exc.remote_traceback,
    )
    raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None


_Result = TypeVar("_Result")


class _InteractiveRun:
    """One preview, trace, OUTPUT dry run or free-code resolution.

    It holds the run's cancellation token and the admission its work takes when it
    starts, given back when the request settles or, for a thread the response stopped
    waiting for, when that thread ends.
    """

    def __init__(self, operation: str, token: ExecutionCancellationToken | None = None) -> None:
        self.operation = operation
        self.token = token if token is not None else ExecutionCancellationToken()
        # Until it starts, a request queued for a work slot holds nothing.
        self.started = False
        self._context: ExecutionContext | None = None

    def admit(self) -> ExecutionContext:
        self.started = True
        self._context = create_admitted_execution_context(
            operation=self.operation,
            profile=ExecutionProfile.PREVIEW_EAGER,
            cancellation_token=self.token,
        )
        return self._context

    def release(self, *, once_done: asyncio.Future[Any] | None = None) -> None:
        """Give the admission back now, or once *once_done*, a thread still running, ends."""
        context, self._context = self._context, None
        if context is None:
            return
        if once_done is None:
            context.release_admission(preserve_primary_error=True)
        else:
            once_done.add_done_callback(lambda _done: context.release_admission())


async def _run_interactive(
    run: _InteractiveRun,
    *,
    worker: Callable[..., _Result],
    worker_args: Callable[[IsolatedExecutionBudget], tuple[Any, ...]],
    in_thread: Callable[[ExecutionContext], _Result],
    affinity_key: tuple[str, ...],
    timeout: float,
    on_progress: StepProgressReporter | None = None,
    after_worker: Callable[[], None] | None = None,
) -> _Result:
    """Admit *run*, then do its work in the interactive worker or, when the server runs
    interactive work in-process, in a thread whose response *timeout* bounds.

    The worker calls *worker* with ``worker_args(budget)`` for the run's admitted budget,
    and *after_worker* runs once that call settles, however it ends. Whatever either
    raises propagates for :func:`_interactive_failures` to answer.
    """
    context = run.admit()
    if resolve_interactive_execution_mode() == "process":
        budget = isolated_execution_budget(context)
        try:
            return await run_in_interactive_worker(
                worker,
                *worker_args(budget),
                affinity_key=affinity_key,
                timeout_seconds=timeout,
                stop_reason=(lambda: "superseded" if run.token.cancelled else None),
                absolute_rss_limit_bytes=budget.process_rss_limit_bytes,
                memory_growth_limit_bytes=budget.memory_limit_bytes,
                require_memory_limit=resolve_worker_memory_enforcement() == "required",
                on_progress=on_progress,
            )
        finally:
            if after_worker is not None:
                after_worker()
    return await run_blocking_with_response_timeout(
        partial(in_thread, context), timeout=timeout, operation=run.operation
    )


@contextmanager
def _interactive_failures(run: _InteractiveRun, *, timed_out: str) -> Iterator[None]:
    """Answer for what an interactive run raised that is not its route's own, then give
    its admission back.

    The memory budget answers 507; a deadline 504 with *timed_out*, the run's token
    cancelled so its work stops; a newer request 409; a worker that crashed or failed
    507 or 500, by what it reported; a public contract error its own payload. A thread
    still running past its deadline keeps the admission until it ends.
    """
    try:
        yield
    except InteractiveWorkerMemoryLimitError as exc:
        raise HTTPException(status_code=507, detail=exc.to_payload()) from None
    except BlockingWorkTimeoutError as exc:
        run.token.cancel()
        run.release(once_done=exc.background_task)
        raise HTTPException(status_code=504, detail=timed_out) from None
    except (InteractiveWorkerTimeoutError, TimeoutError):
        run.token.cancel()
        raise HTTPException(status_code=504, detail=timed_out) from None
    except (InteractiveWorkerStoppedError, SupersededRequestError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except InteractiveWorkerCrashedError as exc:
        _raise_interactive_worker_crash_http_error(exc, operation=run.operation)
    except InteractiveWorkerRemoteError as exc:
        _raise_interactive_remote_http_error(exc, operation=run.operation)
    except PUBLIC_CONTRACT_ERROR_TYPES as exc:
        logger.warning(
            "interactive_public_contract_error",
            operation=run.operation,
            **contract_error_payload(exc),
        )
        raise contract_error_http_exception(exc) from None
    finally:
        run.release()


def _ensure_source_file(graph: PipelineGraph) -> None:
    """Fill in ``graph.source_file`` from ``haute.toml`` when the frontend
    doesn't provide it.  Without this, the executor can't determine the
    pipeline directory and preamble imports (e.g. ``from utility.features``)
    fail because the pipeline's parent dir isn't on ``sys.path``."""
    if graph.source_file:
        return
    toml_path = Path.cwd() / "haute.toml"
    if not toml_path.exists():
        return
    try:
        with open(toml_path, "rb") as f:
            configured = tomllib.load(f).get("project", {}).get("pipeline")
        if configured:
            graph.source_file = configured
    except (OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        logger.warning("source_file_fallback_failed", error=str(exc))


def _prepare_runtime_graph(graph: PipelineGraph) -> PipelineGraph:
    """Flatten and confine an API-submitted graph before any execution work.

    HTTP graph bodies are untrusted local-client input. They may identify the
    active pipeline within the configured project, but they must not use
    ``source_file`` to redefine the process project root. Flattening first also
    ensures path-bearing nodes embedded in submodels receive the same check.
    """
    prepared = flatten_executable_graph(graph)
    _ensure_source_file(prepared)
    _validate_runtime_input_paths(prepared)
    return prepared


def _read_json_object_blocking(target: Path) -> dict[str, Any]:
    payload = json.loads(read_user_text(target))
    if not isinstance(payload, dict):
        raise ValueError("JSON file must contain an object")
    return payload


@router.get("/pipelines", response_model=list[PipelineSummary])
async def list_pipelines() -> list[PipelineSummary]:
    """List all discovered pipelines."""
    files = discover_pipelines()
    cwd = Path.cwd()

    def _wire_file(f: Path) -> str:
        try:
            return f.relative_to(cwd).as_posix()
        except ValueError:
            return f.name

    async def _parse_one(f: Path) -> PipelineSummary:
        try:
            document = await asyncio.to_thread(
                load_pipeline_editor_document,
                f,
                project_root=cwd,
            )
            return PipelineSummary(
                name=document.pipeline_name or f.stem,
                description=document.pipeline_description or "",
                file=_wire_file(f),
                node_count=len(document.nodes),
                load_status=document.load_status,
                diagnostic_count=len(document.diagnostics) + document.diagnostics_omitted,
            )
        except Exception as e:
            logger.warning("pipeline_list_parse_failed", file=f.name, error=str(e))
            # One unreadable/system-failed pipeline must not make every other
            # project document disappear from the picker. The named load still
            # surfaces the underlying system failure if the user opens it.
            return PipelineSummary(
                name=f.stem,
                description="",
                file=_wire_file(f),
                node_count=0,
                load_status="source_only",
                diagnostic_count=1,
            )

    return list(await asyncio.gather(*[_parse_one(f) for f in files]))


# Names the loaded document's fingerprint so the canvas's first /ws/sync resync can skip it.
DOCUMENT_FINGERPRINT_HEADER = "x-haute-document-fingerprint"


def _name_document_fingerprint(
    response: Response, document: PipelineEditorDocument
) -> PipelineEditorDocument:
    response.headers[DOCUMENT_FINGERPRINT_HEADER] = pipeline_document_fingerprint(
        document.model_dump(mode="json", by_alias=True)
    )
    return document


@router.get("/pipeline/{name}", response_model=PipelineEditorDocument)
async def get_pipeline(name: str, response: Response) -> PipelineEditorDocument:
    """Return the editor document for a specific readable pipeline."""

    def _find() -> PipelineEditorDocument | None:
        first_error: Exception | None = None
        attempted: set[Path] = set()
        # O(1) lookup via cached index
        f = lookup_pipeline_by_name(name)
        if f is not None:
            attempted.add(f.resolve())
            try:
                return load_pipeline_editor_document(f, project_root=Path.cwd())
            except Exception as exc:
                first_error = exc
                logger.warning("pipeline_editor_load_failed", file=f.name, error=str(exc))

        # Fallback: linear scan (index may be stale)
        for f in discover_pipelines():
            if f.resolve() in attempted:
                continue
            try:
                document = load_pipeline_editor_document(f, project_root=Path.cwd())
                if document.pipeline_name == name or f.stem == name:
                    return document
            except Exception as e:
                logger.warning("pipeline_editor_load_failed", file=f.name, error=str(e))
                if first_error is None:
                    first_error = e
                continue
        if first_error is not None:
            raise first_error
        return None

    document = await asyncio.to_thread(_find)
    if document is None:
        raise_pipeline_not_found(name)
    return _name_document_fingerprint(response, document)


@router.get("/pipeline", response_model=PipelineEditorDocument)
async def get_first_pipeline(response: Response) -> PipelineEditorDocument:
    """Return the first authored editor document, or a new empty canvas.

    Python file is the source of truth. Sidecar .haute.json provides positions.
    """
    cwd = Path.cwd()

    def _find_first() -> PipelineEditorDocument:
        files = discover_pipelines()
        first_error: Exception | None = None
        for f in files:
            try:
                document = load_pipeline_editor_document(f, project_root=cwd)
            except Exception as exc:
                logger.warning("pipeline_editor_load_failed", file=f.name, error=str(exc))
                if first_error is None:
                    first_error = exc
                continue
            if document.has_authored_content:
                return document
        if first_error is not None:
            raise first_error
        return empty_pipeline_editor_document()

    return _name_document_fingerprint(response, await asyncio.to_thread(_find_first))


def _pipeline_recovery_error_response(
    status_code: int,
    detail: dict[str, Any],
) -> JSONResponse:
    """Return a deliberate structured recovery error outside HTTPException.

    The application's ordinary HTTPException contract keeps ``detail`` a
    short string. Recovery operations additionally need a stable machine code
    and bounded context, so their named route boundary returns JSON directly.
    """
    return JSONResponse(status_code=status_code, content={"detail": detail})


_Repaired = TypeVar("_Repaired", PipelineRepairApplyResponse, PipelineEditorDocument)


async def _apply_repair(
    apply: Callable[..., _Repaired],
    body: BaseModel,
    *,
    io_event: str,
    noun: str,
) -> _Repaired | JSONResponse:
    """Run one confirmed repair, or a node-scoped save, under the save lock.

    A refusal answers its structured detail. A read or write failure answers 409: a
    restore that fails is its own refusal (``repair_rollback_failed``), so whenever this
    answer is reached the *noun* changed nothing.
    """
    try:
        async with save_lock:
            return await run_in_threadpool(apply, project_root=Path.cwd().resolve(), request=body)
    except PipelineRepairError as exc:
        return _pipeline_recovery_error_response(exc.status_code, exc.detail())
    except OSError as exc:
        logger.warning(io_event, error=str(exc))
        return _pipeline_recovery_error_response(
            409,
            {
                "code": "repair_artifact_unavailable",
                "message": (
                    f"A {noun} artifact could not be read or written, so the {noun} "
                    "changed nothing."
                ),
            },
        )


@router.post(
    "/pipeline/repair/remove/apply",
    response_model=PipelineRepairApplyResponse,
)
async def apply_remove_unavailable_node(
    body: PipelineRepairRemoveRequest,
) -> PipelineRepairApplyResponse | JSONResponse:
    """Plan and apply one explicitly confirmed removal against its named revision."""
    return await _apply_repair(
        apply_remove_unavailable_node_plan,
        body,
        io_event="pipeline_repair_apply_io_failed",
        noun="repair",
    )


@router.post("/pipeline/node/save", response_model=PipelineEditorDocument)
async def scoped_node_save(
    body: PipelineNodeSaveRequest,
) -> PipelineEditorDocument | JSONResponse:
    """Save one scoped-editable node in isolation; the document fences stay."""
    return await _apply_repair(
        apply_scoped_node_save, body, io_event="pipeline_node_save_io_failed", noun="save"
    )


@router.post("/pipeline/repair/connection/remove/apply", response_model=PipelineRepairApplyResponse)
async def apply_remove_broken_connection(
    body: PipelineRepairRemoveConnectionRequest,
) -> PipelineRepairApplyResponse | JSONResponse:
    """Plan and commit one confirmed broken-connection removal through the shared transaction."""
    return await _apply_repair(
        apply_remove_connection_plan,
        body,
        io_event="pipeline_repair_apply_io_failed",
        noun="repair",
    )


@router.post("/pipeline/repair/recover/apply", response_model=PipelineRepairApplyResponse)
async def apply_recover_unavailable_node(
    body: PipelineRepairRecoverRequest,
) -> PipelineRepairApplyResponse | JSONResponse:
    """Plan and commit one confirmed update, reset or recover through the shared transaction."""
    return await _apply_repair(
        apply_recover_unavailable_node_plan,
        body,
        io_event="pipeline_repair_apply_io_failed",
        noun="repair",
    )


@router.post("/pipeline/save", response_model=SavePipelineResponse)
async def save_pipeline(body: SavePipelineRequest, request: Request) -> SavePipelineResponse:
    """Save a graph: .py (source of truth) + config JSON + .haute.json (positions).

    When the graph contains submodels, multiple files are written via
    ``graph_to_code_multi``.

    Bundle 5.M1: acquires the shared ``save_lock`` (defined in
    ``routes/_helpers.py``) so this save is serialised against any
    concurrent ``/api/submodel/create`` or ``/api/submodel/dissolve``.
    See the lock definition for the full rationale + scope.

    The save's own writes never reach the file watcher; the route publishes the saved
    document to every other canvas instead, leaving out the saving tab's socket.
    """
    try:
        async with save_lock:
            project_root = Path.cwd().resolve()
            # The service reads the file once, under the repository's lock; the
            # editor's Save refuses one that is not ready on disk.
            svc = SavePipelineService(
                project_root=project_root,
                pipeline_root=pipeline_dir(),
                require_ready=True,
            )
            response = await run_in_threadpool(svc.save, body)
            committed = svc.committed
            if committed is None:
                raise RuntimeError("A save returned without the document it committed.")
            exclude: DocumentUpdateExclusion | None = (
                None
                if body.connection_id is None
                else {
                    "connection_id": body.connection_id,
                    "owner": connection_owner(request.scope),
                }
            )
            default_bus.publish(
                "pipeline.document.update",
                pipeline_document_update_payload(
                    committed.path, committed.payload, committed.fingerprint, exclude=exclude
                ),
            )
            # Another open document reading what the save wrote (a shared submodel's
            # module or sidecar) is published too: the watcher no longer sees the write.
            # The save has committed, so failing to find them is only logged.
            try:
                shared = await run_in_threadpool(
                    partial(
                        shared_document_updates,
                        committed.artifacts,
                        saved=committed.path,
                        project_root=project_root,
                    )
                )
            except Exception as exc:
                logger.warning("save_shared_documents_unpublished", error=str(exc))
                shared = []
            for update in shared:
                default_bus.publish("pipeline.document.update", update)
            return response
    except StaleDocumentRevisionError as exc:
        # The client's document is behind the disk; nothing was written.
        # The detail keeps the repository's flat-string error shape and
        # leads with a stable code the client can match on.
        logger.warning(
            "save_pipeline_stale_revision",
            expected_revision=exc.expected_revision,
            provided_revision=exc.provided_revision,
        )
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc}") from None
    except ConfigError as exc:
        logger.warning("save_pipeline_config_invalid", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/pipeline/read-json", response_model=ReadJsonResponse)
async def read_json_file(body: ReadJsonRequest) -> ReadJsonResponse:
    """Read a JSON artifact from the project root and return its object payload."""
    target = contained_path(_get_project_root(), body.path)
    if target.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Only .json files are supported")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    try:
        return ReadJsonResponse(await run_in_threadpool(_read_json_object_blocking, target))
    except json.JSONDecodeError as exc:
        logger.warning("read_json_invalid_json", path=body.path, error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid JSON file") from None
    except ValueError as exc:
        logger.warning("read_json_invalid_payload", path=body.path, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _occurrence_output_columns(
    authored: PipelineGraph,
    results: Mapping[str, Any],
) -> dict[str, dict[str, list[ColumnInfo]]]:
    """Columns of every submodel occurrence's output ports, keyed like its edges.

    The executor runs the flattened graph, so its results are keyed by the
    cloned internal nodes and never by the occurrence the editor draws. An
    edge from an occurrence leaves the ``out__<port>`` handle; this maps
    that handle to the columns of the port's internal source (its frame
    when the source is multi-frame), so the editor can offer them downstream.
    """
    columns_by_occurrence: dict[str, dict[str, list[ColumnInfo]]] = {}
    # The preview has already flattened this graph, so every occurrence
    # resolves; a failure here is a defect and must surface.
    for instance_id, instance in resolve_submodel_instances(authored).items():
        ports: dict[str, list[ColumnInfo]] = {}
        for port in instance.definition.output_ports:
            result = results.get(qualified_runtime_node_id(instance_id, port.source.node_id))
            if result is None:
                continue
            frames = result.frame_columns or {}
            source_handle = port.source.handle_id
            columns = frames.get(source_handle) if source_handle else None
            if columns is None:
                columns = result.columns
            if columns:
                ports[f"out__{port.name}"] = list(columns)
        if ports:
            columns_by_occurrence[instance_id] = ports
    return columns_by_occurrence


def _preview_response_from_results(
    graph: PipelineGraph,
    body: PreviewNodeRequest,
    results: dict[str, Any],
    execution_context: ExecutionContext,
) -> PreviewNodeResponse:
    node_result = results.get(body.node_id)
    if not node_result:
        raise _PreviewTargetNotReturnedError(f"Node '{body.node_id}' not found in results")

    node_map = graph.node_map
    pruned = prune_source_switch_edges(
        graph.edges,
        node_map,
        body.source,
        submodels=graph.submodels,
    )
    relevant = ancestors(body.node_id, pruned, set(node_map.keys()))
    timings = [
        NodeTimingInfo(
            node_id=node_id,
            label=node_map[node_id].data.label,
            timing_ms=result.timing_ms,
        )
        for node_id, result in results.items()
        if node_id in node_map and node_id in relevant
    ]
    memory = [
        NodeMemoryInfo(
            node_id=node_id,
            label=node_map[node_id].data.label,
            memory_bytes=result.memory_bytes,
        )
        for node_id, result in results.items()
        if node_id in node_map and node_id in relevant
    ]
    node_statuses = {
        node_id: result.status for node_id, result in results.items() if node_id in relevant
    }
    node_columns = {
        node_id: result.columns
        for node_id, result in results.items()
        if node_id in node_map and node_id in relevant
    }
    node_available_columns = {
        node_id: result.available_columns or result.columns
        for node_id, result in results.items()
        if node_id in node_map and node_id in relevant
    }
    node_frame_columns = {
        node_id: result.frame_columns
        for node_id, result in results.items()
        if node_id in node_map and node_id in relevant and result.frame_columns
    }
    for occurrence_id, ports in _occurrence_output_columns(body.graph, results).items():
        node_frame_columns[occurrence_id] = {**node_frame_columns.get(occurrence_id, {}), **ports}
    node_schema_warnings = {
        node_id: result.schema_warnings
        for node_id, result in results.items()
        if node_id in node_map and node_id in relevant
    }
    return PreviewNodeResponse(
        node_id=body.node_id,
        status=node_result.status,
        row_count=node_result.row_count,
        column_count=node_result.column_count,
        columns=node_result.columns,
        available_columns=node_result.available_columns,
        preview=rows_to_json_safe(node_result.preview),
        preview_columns=node_result.preview_columns,
        preview_row_count=node_result.preview_row_count,
        preview_row_limit=node_result.preview_row_limit,
        preview_truncated=node_result.preview_truncated,
        port_previews={
            label: port.model_copy(update={"preview": rows_to_json_safe(port.preview)})
            for label, port in node_result.port_previews.items()
        },
        error=node_result.error,
        error_line=node_result.error_line,
        timing_ms=node_result.timing_ms,
        memory_bytes=node_result.memory_bytes,
        timings=timings,
        memory=memory,
        schema_warnings=node_result.schema_warnings,
        notes=node_result.notes,
        node_statuses=node_statuses,
        node_columns=node_columns,
        node_available_columns=node_available_columns,
        node_frame_columns=node_frame_columns,
        node_schema_warnings=node_schema_warnings,
        execution_metrics=ExecutionMetricsPayload.model_validate(
            execution_context.metrics_payload(status="completed")
        ),
        seed_plan=_preview_seed_plan_entries(graph, execution_context.preview_seed_plan),
    )


def _preview_seed_plan_entries(
    graph: PipelineGraph, generations: tuple[ReadGeneration, ...]
) -> list[PreviewSeedPlanEntry]:
    node_map = graph.node_map
    return [
        PreviewSeedPlanEntry(
            node_id=generation.node_id,
            node_label=node_map[generation.node_id].data.label,
            identity_digest=generation.identity.digest,
            generation_id=generation.generation_id,
            columns=(
                None if generation.columns.names is None else sorted(generation.columns.names)
            ),
            created_at=datetime.fromtimestamp(generation.created_at, tz=UTC).isoformat(),
            kind=generation.kind,
        )
        for generation in generations
    ]


def _discard_preview_staging(staging_token: str) -> None:
    """Remove capture staging a preview worker left under its token.

    Runs after the worker returned, failed, timed out, or was superseded; a
    plan that closed normally already removed it, so this is then a no-op.
    """
    try:
        NodeSnapshotStore(_get_project_root()).discard_node_output_staging(staging_token)
    except OSError as exc:
        logger.warning("preview_staging_discard_failed", error=str(exc))


def _preview_response(
    graph: PipelineGraph,
    body: PreviewNodeRequest,
    context: ExecutionContext,
    staging_token: str | None = None,
) -> PreviewNodeResponse:
    """Run *graph* to the previewed node and answer its preview, in the worker or a thread.

    A contract, schema, graph-shape or config failure the run meets is shown on the
    node. A public contract error, which some of those are too (a missing rating
    factor, a node config the builder rejects), leaves with its payload instead.
    """
    try:
        results = execute_graph(
            graph,
            target_node_id=body.node_id,
            row_limit=body.row_limit,
            source=body.source,
            target_preview_only=True,
            requested_preview_columns=body.requested_preview_columns,
            include_schema_metadata=True,
            port_label=body.port_label,
            port_labels=body.port_labels,
            execution_context=context,
            shared_snapshots=True,
            staging_token=staging_token,
        )
        return _preview_response_from_results(graph, body, results, context)
    except PUBLIC_CONTRACT_ERROR_TYPES:
        raise
    except (ContractMismatchError, SchemaMismatchError, ParseError, ConfigError) as exc:
        return PreviewNodeResponse(node_id=body.node_id, status="error", error=str(exc))


def _execute_preview_worker(
    graph: PipelineGraph,
    body: PreviewNodeRequest,
    budget: IsolatedExecutionBudget,
    staging_token: str | None = None,
) -> PreviewNodeResponse:
    context = create_isolated_execution_context(budget)
    # The parent reads this job's step progress from the worker's progress cell.
    context.step_progress = current_job_progress_reporter()
    try:
        return _preview_response(graph, body, context, staging_token)
    finally:
        context.release_admission(preserve_primary_error=True)


def _listed_seeds(body: TraceRequest) -> list[ListedSeed]:
    return [
        ListedSeed(entry.node_id, entry.identity_digest, entry.generation_id)
        for entry in body.seed_plan
    ]


def _own_node_failure(exc: BaseException) -> str | None:
    """The node whose own code raised *exc*, or ``None`` for a failure of the run.

    The graph walk marks what it raises with the node it was building or running.
    Contract and schema mismatches, public contract errors, the trace's own refusals
    and execution control (cancellation, the memory budget) end the run wherever they
    are raised, so they are never a node's.
    """
    node_id = failing_node(exc)
    if node_id is None or is_public_contract_error(exc):
        return None
    run_level = (
        ContractMismatchError,
        SchemaMismatchError,
        TraceRequestError,
        ExecutionCancelledError,
        ExecutionAdmissionError,
        ExecutionMemoryLimitExceededError,
        MemoryError,
        TimeoutError,
    )
    return None if isinstance(exc, run_level) else node_id


@contextmanager
def _trace_node_failures(graph: PipelineGraph) -> Iterator[None]:
    """Raise a node's own failure during a trace as the error naming it (422), the
    message a preview shows on the node; anything else passes unchanged."""
    try:
        yield
    except Exception as exc:
        node_id = _own_node_failure(exc)
        if node_id is None:
            raise
        node = graph.node_map.get(node_id)
        label = node.data.label if node is not None else node_id
        raise TraceNodeFailedError(f"Node {label!r} failed: {exc}", node_id=node_id) from exc


def _trace_payload(
    graph: PipelineGraph,
    body: TraceRequest,
    context: ExecutionContext,
    fingerprint_memo: GraphFingerprintMemo,
) -> dict[str, Any]:
    """The trace *body* asks for, as the JSON-safe payload, in the worker or a thread.

    It is serialised where it runs, so the event loop never walks the full trace, and
    validated there too: ``JSONResponse`` bypasses response-model validation, so this is
    where the typed omission, waterfall and provenance contract meets the HTTP boundary.
    """
    with _trace_node_failures(graph):
        result = execute_trace(
            graph,
            row_index=body.row_index,
            target_node_id=body.target_node_id,
            column=body.column,
            row_limit=body.row_limit,
            source=body.source,
            row_values=body.row_values,
            fingerprint_memo=fingerprint_memo,
            execution_context=context,
            seed_plan=_listed_seeds(body),
            port_label=body.port_label,
        )
    trace_payload = trace_result_to_dict(result)
    TraceResponse.model_validate({"status": "ok", "trace": trace_payload})
    return trace_payload


def _execute_trace_worker(
    graph: PipelineGraph,
    body: TraceRequest,
    budget: IsolatedExecutionBudget,
) -> dict[str, Any]:
    context = create_isolated_execution_context(budget)
    try:
        return _trace_payload(graph, body, context, GraphFingerprintMemo())
    finally:
        context.release_admission(preserve_primary_error=True)


@router.post("/pipeline/trace", response_model=TraceResponse)
async def trace_row(body: TraceRequest) -> JSONResponse:
    """Trace a single row through the pipeline, returning per-node snapshots."""
    graph = flatten_executable_graph(body.graph)
    _ensure_source_file(graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")
    _ensure_printable_lookup_id(body.target_node_id, "target_node_id")
    _validate_runtime_input_paths(graph)

    run = _InteractiveRun("pipeline_trace")
    limit = _pipeline_time_limit()
    # One memo per request: the supersession key below and every
    # graph_fingerprint call inside execute_trace share it, so the
    # preamble's utility/**/*.py files are read and hashed at most
    # once per trace click instead of once per fingerprint call.
    fingerprint_memo = GraphFingerprintMemo()

    def _trace_in_thread(context: ExecutionContext) -> dict[str, Any]:
        return _trace_payload(graph, body, context, fingerprint_memo)

    with _interactive_failures(run, timed_out=f"Trace execution timed out ({limit:.0f}s limit)"):
        try:
            # Key computation hashes preamble utility files from disk and
            # json-dumps row_values — off the event loop.
            supersession_key = await run_in_threadpool(
                lambda: _trace_supersession_key(
                    graph,
                    body.source,
                    body.target_node_id,
                    body.row_index,
                    body.column,
                    body.row_limit,
                    body.row_values,
                    body.seed_plan,
                    port_label=body.port_label,
                    memo=fingerprint_memo,
                )
            )
            trace_dict = await _trace_supersession.run_latest(
                supersession_key,
                lambda: _run_interactive(
                    run,
                    worker=_execute_trace_worker,
                    worker_args=lambda budget: (graph, body, budget),
                    in_thread=_trace_in_thread,
                    affinity_key=_interactive_affinity_key(
                        graph, body.source, memo=fingerprint_memo
                    ),
                    timeout=limit,
                ),
                limiter=_trace_work_slots,
                cancel_active=run.token.cancel,
                superseded_message="Trace request superseded by a newer request",
            )
        except PUBLIC_CONTRACT_ERROR_TYPES:
            raise
        except ContractMismatchError as e:
            # Contract mismatches carry the node id and the symmetric column
            # diff in ``str(e)``.  Surface that directly (422 Unprocessable
            # Entity) instead of collapsing it into the generic 500
            # "check the logs" reply — the point of the contract error is
            # that the user can fix the bad contract in one edit.
            logger.warning("trace_contract_mismatch", error=str(e))
            raise HTTPException(status_code=422, detail=str(e)) from None
        except TraceRequestError as e:
            logger.warning("trace_refused", error_code=e.error_code, error=e.message)
            raise HTTPException(
                status_code=_TRACE_REFUSAL_STATUS[type(e)], detail=e.message
            ) from None
        # ``trace_dict`` is already JSON-safe and was validated against
        # ``TraceResponse`` where it ran. Encode it directly so the event
        # loop does not walk the full payload again.
        return JSONResponse({"status": "ok", "trace": trace_dict})


async def _preview_canonical_graph(
    body: PreviewNodeRequest, http_request: Request
) -> PreviewNodeResponse:
    """Run pipeline up to a specific node and return its output.

    Accepts an optional ``row_limit`` (default 100) that is pushed into
    the Polars lazy query plan so only that many rows are scanned. A client
    that disconnects (the browser's Stop) cancels the preview's own token, so
    the worker or thread stops; a request still queued never executes.
    """
    run = _InteractiveRun("pipeline_preview")
    limit = _pipeline_time_limit()
    request_id = body.request_id
    step_progress: StepProgressReporter | None = None
    if request_id is not None:
        preview_progress.open(request_id)

        def step_progress(progress: StepProgress) -> None:
            preview_progress.report(request_id, progress)

    def _preview_in_thread(context: ExecutionContext) -> PreviewNodeResponse:
        context.step_progress = step_progress
        return _preview_response(graph, body, context)

    try:
        with _interactive_failures(
            run, timed_out=f"Preview execution timed out ({limit:.0f}s limit)"
        ):
            try:
                # A graph the flattener refuses (a name collision, an undeclared
                # handle) is shown on the node, as a graph-shape issue the run meets
                # is; every other route that takes a graph answers it 400.
                graph = flatten_executable_graph(body.graph, form=body.workbench_form)
                _ensure_source_file(graph)
                if not graph.nodes:
                    raise HTTPException(status_code=400, detail="Empty graph")
                _ensure_printable_lookup_id(body.node_id, "node_id")
                if body.node_id not in graph.node_map:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Node '{body.node_id}' not found in results",
                    )
                _validate_runtime_input_paths(graph)

                fingerprint_memo = GraphFingerprintMemo()
                # The worker's captures stage under this token; whatever a killed
                # or superseded worker left there is removed once it settles.
                staging_token = new_staging_token()
                return await await_until_disconnected(
                    http_request,
                    _preview_supersession.run_latest(
                        _preview_supersession_key(
                            graph,
                            body.source,
                            body.node_id,
                            body.row_limit,
                            body.requested_preview_columns,
                            body.port_label,
                            body.port_labels,
                            memo=fingerprint_memo,
                        ),
                        lambda: _run_interactive(
                            run,
                            worker=_execute_preview_worker,
                            worker_args=lambda budget: (graph, body, budget, staging_token),
                            in_thread=_preview_in_thread,
                            affinity_key=_interactive_affinity_key(
                                graph, body.source, memo=fingerprint_memo
                            ),
                            timeout=limit,
                            on_progress=step_progress,
                            after_worker=partial(_discard_preview_staging, staging_token),
                        ),
                        limiter=_preview_work_slots,
                        cancel_active=run.token.cancel,
                        superseded_message="Preview request superseded by a newer request",
                    ),
                    cancel=run.token.cancel,
                    started=lambda: run.started,
                    # A thread still running past its response timeout keeps its
                    # admission until it finishes; the ladder defers the release.
                    propagate=(BlockingWorkTimeoutError,),
                    detail="The client closed the preview request before it finished.",
                )
            except PUBLIC_CONTRACT_ERROR_TYPES:
                raise
            except (ContractMismatchError, SchemaMismatchError, ParseError, ConfigError) as e:
                # The run shows these on the node; one met before it starts, while the
                # graph is flattened or fingerprinted, is shown the same way.
                logger.warning("preview_refused_before_run", error=str(e))
                return PreviewNodeResponse(node_id=body.node_id, status="error", error=str(e))
            except PreviewProjectionError as e:
                logger.warning("preview_bad_request", error=str(e))
                raise HTTPException(status_code=400, detail=str(e)) from None
            except _PreviewTargetNotReturnedError as e:
                raise HTTPException(status_code=404, detail=str(e)) from None
    finally:
        if request_id is not None:
            preview_progress.close(request_id)


@router.get(
    "/pipeline/preview/progress/{request_id}",
    response_model=PreviewProgressResponse,
)
def preview_progress_of(request_id: str) -> PreviewProgressResponse:
    """The step progress of the caller's own in-flight preview.

    404 when the id is unknown or its preview has settled, which the client
    treats as "nothing to show", not as a failure of the preview.
    """
    progress = preview_progress.get(request_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="No preview in progress with this id.")
    return PreviewProgressResponse(
        request_id=request_id,
        phase=progress.phase,
        done=progress.done,
        total=progress.total,
        label=progress.label,
    )


@router.post("/pipeline/preview", response_model=PreviewNodeResponse)
async def preview_node(body: PreviewNodeRequest, http_request: Request) -> PreviewNodeResponse:
    """Preview a client-supplied canonical graph."""
    return await _preview_canonical_graph(body, http_request)


@router.post("/pipeline/preview/inputs", response_model=PreviewInputsResponse)
async def preview_inputs(body: PreviewInputsRequest) -> PreviewInputsResponse:
    """The inputs a preview of *node_id* would read, so the browser prepares only those.

    Snapshot-backed Data Inputs and structured API Inputs, read without
    preparing or leasing anything. The answer is advisory: a preview prepares
    whatever its own plan then reads. A graph the preview cannot run as
    authored — a shape, config, or contract error — has nothing to prepare,
    and the preview itself reports that error at the node, as it always has.
    """
    try:
        graph = flatten_executable_graph(body.graph)
    except (ParseError, ConfigError, WorkbenchError, WorkbenchTablesError) as e:
        # A form the workbench nodes cannot read is the preview's to report, as a shape is.
        logger.info("preview_inputs_graph_invalid", error=str(e))
        return PreviewInputsResponse(input_node_ids=[])
    _ensure_source_file(graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")
    _ensure_printable_lookup_id(body.node_id, "node_id")
    if body.node_id not in graph.node_map:
        raise HTTPException(status_code=404, detail=f"Node '{body.node_id}' not found")
    _validate_runtime_input_paths(graph)

    def _resolve() -> tuple[str, ...]:
        with runtime_project_root_scope(graph.source_file):
            return preview_input_node_ids(
                graph,
                body.node_id,
                source=body.source,
                required_columns_by_node=_preview_required_columns_by_node(
                    graph,
                    body.node_id,
                    body.requested_preview_columns,
                )
                or None,
            )

    try:
        node_ids = await run_blocking_with_response_timeout(
            _resolve,
            timeout=_pipeline_time_limit(),
            operation="pipeline_preview_inputs",
        )
    except PreviewProjectionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except (ParseError, ConfigError, ContractMismatchError, SchemaMismatchError) as e:
        logger.info("preview_inputs_graph_invalid", error=str(e))
        return PreviewInputsResponse(input_node_ids=[])
    except (BlockingWorkTimeoutError, TimeoutError):
        raise HTTPException(
            status_code=504,
            detail=f"Preview input resolution timed out ({_pipeline_time_limit():.0f}s limit)",
        ) from None
    except PUBLIC_CONTRACT_ERROR_TYPES as e:
        raise contract_error_http_exception(e) from None
    return PreviewInputsResponse(input_node_ids=list(node_ids))


class _RecoveryPreviewRequestError(ValueError):
    """Expected recovery-preview rejection with stable structured detail."""

    def __init__(self, status_code: int, detail: dict[str, Any]) -> None:
        super().__init__(detail["message"])
        self.status_code = status_code
        self.detail = detail


def _recovery_preview_error(
    code: str,
    message: str,
    *,
    status_code: int = 409,
    **context: Any,
) -> _RecoveryPreviewRequestError:
    return _RecoveryPreviewRequestError(
        status_code,
        {"code": code, "message": message, **context},
    )


def _recovery_ancestor_ids(
    document: PipelineEditorDocument,
    target_id: str,
) -> set[str]:
    # Walked over recovery ids, not through haute._topo.ancestors: that helper
    # takes canonical GraphEdge models, and constructing canonical edges from
    # unvalidated recovery elements would cross the strict/recovery boundary
    # this closure exists to protect.
    incoming: dict[str, list[str]] = {}
    for edge in document.edges:
        incoming.setdefault(edge.target_recovery_id, []).append(edge.source_recovery_id)
    return {target_id, *upstream_node_ids(target_id, incoming)}


def _canonical_snapshot_graph(
    *,
    nodes: list[Any],
    edges: list[Any],
    submodels: dict[str, Any] | None,
    allowed_node_ids: set[str] | None = None,
    pipeline_name: str | None = None,
    pipeline_description: str | None = None,
    preamble: str | None = None,
    preserved_blocks: list[str] | None = None,
    global_constants: list[GlobalConstant] | None = None,
    global_constants_error: str | None = None,
    source_file: str = "",
) -> PipelineGraph:
    """Build a fresh canonical graph from already-validated ready elements."""
    selected_nodes = [
        node for node in nodes if allowed_node_ids is None or node.recovery_id in allowed_node_ids
    ]
    canonical_nodes: list[GraphNode] = []
    referenced_definitions: set[str] = set()
    for node in selected_nodes:
        if node.availability != "ready" or node.node_type is None or node.config is None:
            raise _recovery_preview_error(
                "node_unavailable",
                f"Node {node.authored_id!r} is not available for preview.",
                target_recovery_id=node.recovery_id,
            )
        if node.node_type == NodeType.SUBMODEL:
            definition_id = node.config.get("definitionId")
            if isinstance(definition_id, str):
                referenced_definitions.add(definition_id)
        canonical_nodes.append(
            GraphNode(
                id=node.recovery_id,
                type=node.node_type,
                position=node.display_position,
                data=NodeData(
                    label=node.label,
                    description=node.description,
                    nodeType=node.node_type,
                    config=node.config,
                ),
            )
        )

    selected_ids = {node.id for node in canonical_nodes}
    canonical_edges = [
        GraphEdge(
            id=edge.recovery_id,
            source=edge.source_recovery_id,
            target=edge.target_recovery_id,
            sourceHandle=edge.source_handle,
            targetHandle=edge.target_handle,
        )
        for edge in edges
        if edge.source_recovery_id in selected_ids
        and edge.target_recovery_id in selected_ids
        and edge.availability == "ready"
    ]

    canonical_submodels: dict[str, SubmodelDefinition] = {}
    for definition_id in sorted(referenced_definitions):
        definition = (submodels or {}).get(definition_id)
        if definition is None or definition.availability != "ready":
            raise _recovery_preview_error(
                "submodel_unavailable",
                f"Submodel definition {definition_id!r} is not available for preview.",
                definition_id=definition_id,
            )
        canonical_submodels[definition_id] = SubmodelDefinition(
            definitionId=definition.definition_id,
            file=definition.file,
            graph=_canonical_snapshot_graph(
                nodes=definition.graph.nodes,
                edges=definition.graph.edges,
                submodels=definition.graph.submodels,
                source_file=definition.file,
            ),
            inputPorts=definition.input_ports,
            outputPorts=definition.output_ports,
        )

    return PipelineGraph(
        nodes=canonical_nodes,
        edges=canonical_edges,
        submodels=canonical_submodels or None,
        pipeline_name=pipeline_name,
        pipeline_description=pipeline_description,
        preamble=preamble,
        preserved_blocks=list(preserved_blocks or []),
        global_constants=list(global_constants or []),
        global_constants_error=global_constants_error,
        source_file=source_file,
    )


def _plan_recovery_preview(
    document: PipelineEditorDocument,
    body: RecoveryPreviewRequest,
) -> PreviewNodeRequest:
    if not document.source_selection_trusted:
        sidecar_codes = [
            diagnostic.code
            for diagnostic in document.diagnostics
            if diagnostic.code in {"sidecar_corrupt", "sidecar_unreadable"}
        ]
        raise _recovery_preview_error(
            "source_selection_untrusted",
            "Preview is unavailable because source selection metadata is not trusted.",
            diagnostics=sidecar_codes,
        )
    if body.source not in document.sources:
        raise _recovery_preview_error(
            "source_not_available",
            f"Source {body.source!r} is not available for this document.",
            status_code=400,
            source=body.source,
        )

    node_map = {node.recovery_id: node for node in document.nodes}
    target = node_map.get(body.target_recovery_id)
    if target is None:
        raise _recovery_preview_error(
            "recovery_target_not_found",
            f"Recovery node {body.target_recovery_id!r} was not found.",
            status_code=404,
            target_recovery_id=body.target_recovery_id,
        )
    if target.availability == "unavailable":
        raise _recovery_preview_error(
            "node_unavailable",
            f"Node {target.authored_id!r} is unavailable.",
            target_recovery_id=target.recovery_id,
            diagnostic_ids=target.diagnostic_ids,
        )
    if target.availability == "blocked":
        raise _recovery_preview_error(
            "node_blocked_by_load_error",
            f"Node {target.authored_id!r} depends on an unavailable node.",
            target_recovery_id=target.recovery_id,
            blocking_path=target.blocking_path,
        )

    closure = _recovery_ancestor_ids(document, target.recovery_id)
    for node_id in sorted(closure):
        node = node_map.get(node_id)
        if node is None or node.availability != "ready":
            raise _recovery_preview_error(
                "node_blocked_by_load_error",
                f"Preview closure contains unavailable recovery node {node_id!r}.",
                target_recovery_id=target.recovery_id,
                blocking_path=(node.blocking_path if node is not None else [node_id]),
            )
    unresolved = [
        connection
        for connection in document.unresolved_connections
        if connection.target_recovery_id in closure
    ]
    if unresolved:
        raise _recovery_preview_error(
            "node_blocked_by_unresolved_connection",
            "Preview closure contains an unresolved authored connection.",
            target_recovery_id=target.recovery_id,
            connection_ids=[connection.recovery_id for connection in unresolved],
        )

    graph = _canonical_snapshot_graph(
        nodes=document.nodes,
        edges=document.edges,
        submodels=document.submodels,
        allowed_node_ids=closure,
        pipeline_name=document.pipeline_name,
        pipeline_description=document.pipeline_description,
        preamble=document.preamble,
        preserved_blocks=document.preserved_blocks,
        global_constants=document.global_constants,
        global_constants_error=document.global_constants_error,
        source_file=document.source_file,
    )
    validate_pipeline_graph_shape_contracts(
        graph,
        graph_label=document.pipeline_name or document.source_file or "recovery-preview",
    )
    return PreviewNodeRequest(
        graph=graph,
        node_id=target.recovery_id,
        row_limit=body.row_limit,
        source=body.source,
        requested_preview_columns=body.requested_preview_columns,
        port_label=body.port_label,
        request_id=body.request_id,
    )


@router.post("/pipeline/recovery-preview", response_model=PreviewNodeResponse)
async def recovery_preview_node(
    body: RecoveryPreviewRequest,
    http_request: Request,
) -> PreviewNodeResponse | JSONResponse:
    """Preview one server-validated ready closure from a recovery document."""
    try:
        _ensure_printable_lookup_id(body.target_recovery_id, "target_recovery_id")
        project_root = _get_project_root().resolve()
        source_path = contained_path(project_root, body.source_file)
        if not source_path.is_file():
            raise _recovery_preview_error(
                "pipeline_source_not_found",
                "The pipeline source file no longer exists.",
                status_code=404,
                source_file=body.source_file,
            )
        document = await run_in_threadpool(
            load_pipeline_editor_document,
            source_path,
            project_root=project_root,
        )
        if document.source_revision != body.source_revision:
            raise _recovery_preview_error(
                "stale_document_revision",
                "The pipeline changed after this recovery document was loaded.",
                expected_revision=document.source_revision,
                provided_revision=body.source_revision,
            )
        request = _plan_recovery_preview(document, body)
        return await _preview_canonical_graph(request, http_request)
    except _RecoveryPreviewRequestError as exc:
        return _pipeline_recovery_error_response(exc.status_code, exc.detail)


def _pipeline_settings_response() -> PipelineSettingsResponse:
    root = _get_project_root()
    return PipelineSettingsResponse(
        settings=PipelineSettingsValues.model_validate(read_pipeline_settings(root).values()),
        automatic=PipelineSettingsAutomatic.model_validate(
            dataclasses.asdict(automatic_pipeline_settings(root))
        ),
        path=SETTINGS_PATH,
    )


@router.get("/pipeline-settings", response_model=PipelineSettingsResponse)
async def get_pipeline_settings() -> PipelineSettingsResponse:
    """The project's pipeline settings and each one's automatic figure now."""
    return await run_in_threadpool(_pipeline_settings_response)


@router.patch("/pipeline-settings", response_model=PipelineSettingsResponse)
async def patch_pipeline_settings(body: PipelineSettingsValues) -> PipelineSettingsResponse:
    """Change the settings the body names (``null`` restores automatic) and write the file.

    The server applies written chunk rows at once; every other setting is read
    when each execution starts.
    """

    def _update() -> PipelineSettingsResponse:
        apply_chunk_rows(
            update_pipeline_settings(_get_project_root(), body.model_dump(exclude_unset=True))
        )
        return _pipeline_settings_response()

    return await run_in_threadpool(_update)


@router.post(
    "/pipeline/output-destination",
    response_model=OutputDestinationResponse,
)
async def output_destination(body: OutputDestinationRequest) -> OutputDestinationResponse:
    """Resolve the exact display destination without executing or writing."""
    graph, _output_node, config, project_root = _prepare_data_output_request(
        body.graph,
        body.node_id,
    )
    _resolved, display_path = resolve_data_output_path(
        graph,
        config,
        project_root=project_root,
    )
    if is_databricks_output(config):
        return OutputDestinationResponse(
            path=display_path, format="databricks", suffix_mismatch=False
        )
    fmt = format_for_config(config)
    raw_path = config.get("path")
    suffix = (
        Path(raw_path.replace("\\", "/")).suffix.casefold() if isinstance(raw_path, str) else ""
    )
    return OutputDestinationResponse(
        path=display_path,
        format=fmt.name,
        suffix_mismatch=bool(suffix and fmt.extensions and suffix not in fmt.extensions),
    )


@dataclass(frozen=True, slots=True)
class _OutputWriteWorkerOutcome:
    prepared: PreparedDataOutput | None = None
    failure_kind: str | None = None
    detail: str | None = None
    payload: dict[str, object] | None = None


class _OutputWriteWorkerError(RuntimeError):
    def __init__(
        self,
        kind: str,
        detail: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.payload = payload


def _prepare_data_output_worker(
    graph: PipelineGraph,
    output_node_id: str,
    source: str,
    project_root: str,
    overwrite: bool,
    staging_path: str | None,
    seed_plan: SeedPlanHandoff,
    budget: IsolatedExecutionBudget,
) -> _OutputWriteWorkerOutcome:
    """Execute one sink under the parent's seed plan, leaving publication to the parent."""
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        prepared = prepare_data_output(
            graph,
            output_node_id,
            source,
            execution_context=context,
            project_root=project_root,
            overwrite=overwrite,
            staging_path=staging_path,
            seed_plan=seed_plan,
        )
        return _OutputWriteWorkerOutcome(prepared=prepared)
    except PUBLIC_CONTRACT_ERROR_TYPES as exc:
        return _OutputWriteWorkerOutcome(
            failure_kind="contract",
            detail=str(exc),
            payload=exc.to_payload(),
        )
    except BoundedMemoryUnsupportedError as exc:
        return _OutputWriteWorkerOutcome(failure_kind="bounded", detail=str(exc))
    except DataOutputDestinationExistsError as exc:
        return _OutputWriteWorkerOutcome(failure_kind="destination_exists", detail=str(exc))
    except DatabricksConfigError as exc:
        return _OutputWriteWorkerOutcome(failure_kind="databricks_config", detail=str(exc))
    except DatabricksWriteError as exc:
        return _OutputWriteWorkerOutcome(failure_kind="databricks_write", detail=str(exc))
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        return _OutputWriteWorkerOutcome(
            failure_kind="memory",
            detail=str(exc),
            payload=exc.to_payload(),
        )
    finally:
        if context is not None:
            context.release_admission(preserve_primary_error=True)


def _with_parent_evidence(
    prepared: PreparedDataOutput,
    execution_context: ExecutionContext,
) -> PreparedDataOutput:
    """The worker's result, its metrics carrying the parent's preparation evidence."""
    metrics = prepared.response.execution_metrics
    if metrics is None:
        return prepared
    merged = execution_context.metrics_with_worker_evidence(metrics.model_dump(mode="json"))
    return replace(
        prepared,
        response=prepared.response.model_copy(
            update={"execution_metrics": ExecutionMetricsPayload.model_validate(merged)}
        ),
    )


def _output_write_transaction(
    graph: PipelineGraph,
    output_node_id: str,
    source: str,
    project_root: Path,
    overwrite: bool,
    final_path: Path | None,
    staging_path: Path | None,
    budget: IsolatedExecutionBudget,
    cancellation_requested: WorkerCancellationGate,
    *,
    display_path: str,
    execution_context: ExecutionContext,
) -> WriteOutputResponse:
    """Prepare inputs and open the seed plan, supervise the sink child, and publish.

    A node's signature signs its prepared inputs, so this process prepares them
    and resolves the plan; the child adopts it, and its leases and capture
    staging are released only after the child has exited. The sink timeout
    bounds preparation and the child together.
    """
    prepared: PreparedDataOutput | None = None
    primary_error: BaseException | None = None
    deadline = time.monotonic() + _pipeline_time_limit()
    try:
        if cancellation_requested.is_set():
            raise IsolatedWorkerStoppedError(terminal_reason="cancelled")
        # Preparing inputs here stops with the request.
        cancellation_requested.on_request(execution_context.cancellation_token.cancel)
        try:
            plan = open_seed_plan(
                data_output_seed_plan_request(
                    graph,
                    output_node_id,
                    source,
                    profile=execution_context.profile,
                ),
                execution_context=execution_context,
                deadline=deadline,
            )
        except Exception as exc:
            if cancellation_requested.is_set():
                raise IsolatedWorkerStoppedError(terminal_reason="cancelled") from exc
            if time.monotonic() >= deadline:
                raise IsolatedWorkerTimeoutError(timeout_seconds=_pipeline_time_limit()) from exc
            raise
        with plan:
            # Preparation can finish (a cancelled build reconciled as published)
            # after the request went away; nothing is launched for it then.
            if cancellation_requested.is_set():
                raise IsolatedWorkerStoppedError(terminal_reason="cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IsolatedWorkerTimeoutError(timeout_seconds=_pipeline_time_limit())
            config = worker_config_for_memory_policy(
                memory_limit_bytes=budget.memory_limit_bytes,
                timeout_seconds=remaining,
                stop_reason=(lambda: "cancelled" if cancellation_requested.is_set() else None),
                process_name="haute-output-write",
            )
            outcome = run_isolated_worker(
                _prepare_data_output_worker,
                graph,
                output_node_id,
                source,
                str(project_root),
                overwrite,
                None if staging_path is None else str(staging_path),
                plan.handoff(),
                budget,
                config=config,
            )
        if not isinstance(outcome, _OutputWriteWorkerOutcome):
            raise RuntimeError("Output worker returned an invalid outcome")
        if outcome.failure_kind is not None:
            raise _OutputWriteWorkerError(
                outcome.failure_kind,
                outcome.detail or "Output write failed",
                outcome.payload,
            )
        if outcome.prepared is None:
            raise RuntimeError("Output worker omitted its prepared result")
        validate_prepared_data_output_identity(
            outcome.prepared,
            project_root=str(project_root),
            display_path=display_path,
            final_path=final_path,
            staging_path=staging_path,
            overwrite=overwrite,
            transactional=staging_path is None,
        )
        prepared = _with_parent_evidence(outcome.prepared, execution_context)
        return commit_prepared_data_output(
            prepared,
            publication_guard=cancellation_requested.publication_guard(),
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            if prepared is not None:
                discard_prepared_data_output(prepared)
            elif final_path is not None and staging_path is not None:
                discard_data_output_staging_path(
                    final_path,
                    staging_path,
                    project_root=project_root,
                )
        except BaseException as cleanup_exc:
            if primary_error is None:
                raise
            primary_error.add_note(f"Output staging cleanup failed: {cleanup_exc}")


def _isolated_output_memory_detail(
    exc: BaseException,
    *,
    memory_limit_bytes: int | None,
) -> dict[str, object]:
    return isolated_worker_memory_detail(
        exc,
        operation="pipeline_write_output",
        memory_limit_bytes=memory_limit_bytes,
    )


@router.post("/pipeline/write-output", response_model=WriteOutputResponse)
async def write_output_node(body: WriteOutputRequest) -> WriteOutputResponse:
    """Execute the pipeline up to a Data Output and publish its destination.

    Only called on explicit user action (Write button), not during normal run/preview.
    """
    graph, _output_node, config, project_root = _prepare_data_output_request(
        body.graph,
        body.node_id,
    )

    resolved_output, display_path = resolve_data_output_path(
        graph,
        config,
        project_root=project_root,
    )
    is_file_target = data_output_group(config) == "file"
    if (
        is_file_target
        and resolved_output is not None
        and resolved_output.exists()
        and not body.overwrite
    ):
        raise HTTPException(
            status_code=409,
            detail=str(DataOutputDestinationExistsError(display_path)),
        )
    staging_path = (
        new_data_output_staging_path(resolved_output)
        if is_file_target and resolved_output is not None
        else None
    )

    output_context: ExecutionContext | None = None
    budget: IsolatedExecutionBudget | None = None
    try:
        output_context = create_admitted_execution_context(
            operation="pipeline_write_output",
            profile=ExecutionProfile.LAZY_SINK,
        )
        budget = isolated_execution_budget(output_context)

        def _transaction(cancellation_requested: WorkerCancellationGate) -> WriteOutputResponse:
            assert budget is not None
            assert output_context is not None
            return _output_write_transaction(
                graph,
                body.node_id,
                body.source,
                project_root,
                body.overwrite,
                resolved_output if staging_path is not None else None,
                staging_path,
                budget,
                cancellation_requested,
                display_path=display_path,
                execution_context=output_context,
            )

        result = await run_cancellable_worker_transaction(
            _transaction,
            task_name="haute-output-write-supervisor",
        )
        if result.execution_metrics is not None:
            logger.info(
                "sink_execution_metrics",
                node_id=body.node_id,
                stage_elapsed_ms=result.execution_metrics.stage_elapsed_ms,
                total_elapsed_ms=result.execution_metrics.total_elapsed_ms,
            )
        return result
    except _OutputWriteWorkerError as e:
        if e.kind == "contract":
            raise HTTPException(status_code=422, detail=e.payload or e.detail) from None
        if e.kind == "bounded":
            raise HTTPException(status_code=422, detail=e.detail) from None
        if e.kind == "destination_exists":
            raise HTTPException(status_code=409, detail=e.detail) from None
        if e.kind == "databricks_config":
            raise HTTPException(status_code=400, detail=e.detail) from None
        if e.kind == "databricks_write":
            raise HTTPException(status_code=502, detail=e.detail) from None
        if e.kind == "memory":
            raise HTTPException(status_code=507, detail=e.payload or e.detail) from None
        raise AssertionError(f"unhandled output worker failure kind: {e.kind}")
    except (
        IsolatedWorkerMemoryLimitExceededError,
        IsolatedWorkerMemoryLimitUnsupportedError,
    ) as e:
        raise HTTPException(
            status_code=507,
            detail=_isolated_output_memory_detail(
                e,
                memory_limit_bytes=None if budget is None else budget.memory_limit_bytes,
            ),
        ) from None
    except IsolatedWorkerCrashedError as e:
        if isolated_worker_failure_is_memory(e):
            raise HTTPException(
                status_code=507,
                detail=_isolated_output_memory_detail(
                    e,
                    memory_limit_bytes=None if budget is None else budget.memory_limit_bytes,
                ),
            ) from None
        logger.error("sink_worker_crashed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None
    except IsolatedWorkerRemoteError as e:
        if isolated_worker_failure_is_memory(e):
            raise HTTPException(
                status_code=507,
                detail=_isolated_output_memory_detail(
                    e,
                    memory_limit_bytes=None if budget is None else budget.memory_limit_bytes,
                ),
            ) from None
        logger.error(
            "sink_worker_remote_failure",
            remote_type=e.remote_type,
            error=e.remote_message,
        )
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None
    except IsolatedWorkerTimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Sink execution timed out ({_pipeline_time_limit():.0f}s limit)",
        ) from None
    except IsolatedWorkerStoppedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except PUBLIC_CONTRACT_ERROR_TYPES as e:
        logger.warning("sink_public_contract_error", **contract_error_payload(e))
        raise contract_error_http_exception(e) from None
    except BoundedMemoryUnsupportedError as e:
        logger.warning(
            "sink_bounded_streaming_unsupported",
            error=str(e),
            execution_metrics=(
                output_context.metrics_payload(status="error")
                if output_context is not None
                else None
            ),
        )
        raise HTTPException(status_code=422, detail=str(e)) from None
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Sink execution timed out ({_pipeline_time_limit():.0f}s limit)",
        ) from None
    except DataOutputDestinationExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except DataOutputPublicationError as e:
        logger.warning(
            "sink_publication_unsupported",
            path=e.display_path,
            error=repr(e.__cause__),
        )
        raise HTTPException(status_code=500, detail=str(e)) from None
    except DataOutputDurabilityError as e:
        logger.error(
            "sink_durability_unconfirmed",
            path=e.display_path,
            error=repr(e.__cause__),
        )
        raise HTTPException(status_code=500, detail=str(e)) from None
    finally:
        if output_context is not None:
            output_context.release_admission(preserve_primary_error=True)
