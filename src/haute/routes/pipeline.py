"""Pipeline CRUD, preview, trace, and sink endpoints."""

from __future__ import annotations

import asyncio
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from haute._cache import GraphFingerprintMemo, canonical_json
from haute._editor_identities import resolve_editor_identity
from haute._env import float_env, int_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_admitted_execution_context,
    create_isolated_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._graph_shape import validate_pipeline_graph_shape_contracts
from haute._hashing import content_hash_bytes
from haute._interactive_workers import (
    InteractiveWorkerCrashedError,
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
from haute._path_resolution import RuntimePathError, resolve_runtime_file_path
from haute._pipeline_recovery import empty_pipeline_editor_document
from haute._pipeline_repair import (
    PipelineRepairError,
    apply_remove_unavailable_node_plan,
    build_remove_unavailable_node_plan,
)
from haute._polars_io_registry import (
    PolarsIoConfigError,
    format_for_config,
    format_group,
    validate_data_output_config,
)
from haute._polars_utils import DEFAULT_STREAMING_CHUNK_SIZE, temporary_streaming_chunk_size
from haute._sandbox import _get_project_root
from haute._topo import ancestors
from haute._types import GraphEdge, GraphNode, NodeData, SubmodelDefinition
from haute._worker_isolation import (
    IsolatedWorkerCrashedError,
    IsolatedWorkerMemoryLimitExceededError,
    IsolatedWorkerMemoryLimitUnsupportedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
    resolve_worker_memory_enforcement,
    run_isolated_worker,
    worker_config_for_memory_policy,
)
from haute.errors import (
    BoundedMemoryUnsupportedError,
    ConfigError,
    ContractMismatchError,
    ParseError,
    SchemaMismatchError,
)
from haute.execution import _runtime_input_path_fields, prune_source_switch_edges
from haute.executor import (
    DataOutputDestinationExistsError,
    DataOutputDurabilityError,
    DataOutputPublicationError,
    PreparedDataOutput,
    PreviewProjectionError,
    _preview_cache,
    commit_prepared_data_output,
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
    flatten_graph,
    graph_fingerprint,
)
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_http_exception,
    contract_error_payload,
)
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    discover_pipelines,
    load_pipeline_editor_document,
    lookup_pipeline_by_name,
    pipeline_dir,
    raise_pipeline_not_found,
    save_lock,
    validate_safe_path,
)
from haute.routes._isolated_worker_async import (
    WorkerCancellationGate,
    run_cancellable_worker_transaction,
)
from haute.routes._runtime_path_errors import runtime_path_http_exception
from haute.routes._save_pipeline import SavePipelineService
from haute.routes._supersession import SupersededRequestError, SupersessionCoordinator
from haute.routes._timeouts import (
    BlockingWorkTimeoutError,
    run_blocking_with_response_timeout,
)
from haute.schemas import (
    EditorIdentitiesRequest,
    EditorIdentitiesResponse,
    EditorIdentityResponseNode,
    ExecutionMetricsPayload,
    NodeMemoryInfo,
    NodeTimingInfo,
    OutputDestinationRequest,
    OutputDestinationResponse,
    PipelineEditorDocument,
    PipelineRepairApplyRequest,
    PipelineRepairApplyResponse,
    PipelineRepairDryRunRequest,
    PipelineRepairPlanResponse,
    PipelineSummary,
    PreviewNodeRequest,
    PreviewNodeResponse,
    ReadJsonRequest,
    ReadJsonResponse,
    RecoveryPreviewRequest,
    SavePipelineRequest,
    SavePipelineResponse,
    TraceRequest,
    TraceResponse,
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
        (SchemaMismatchError.__module__, SchemaMismatchError.__name__),
    }
)
_VALUE_ERROR_REMOTE_IDENTITY = (ValueError.__module__, ValueError.__name__)

router = APIRouter(prefix="/api", tags=["pipeline"])


@router.post("/pipeline/editor-identities", response_model=EditorIdentitiesResponse)
async def resolve_pipeline_editor_identities(
    body: EditorIdentitiesRequest,
) -> EditorIdentitiesResponse:
    """Resolve editor identities without reading or writing project state."""
    try:
        identities: list[EditorIdentityResponseNode] = []
        for node in body.nodes:
            identity = resolve_editor_identity(
                node_type=node.node_type,
                label=node.label,
                source_handles=node.source_handles,
                submodel_alias=node.submodel_alias,
            )
            identities.append(
                EditorIdentityResponseNode(
                    node_id=node.node_id,
                    function_name=identity.function_name,
                    config_reference=identity.config_reference,
                    default_input_name=identity.default_input_name,
                    source_handle_input_names=identity.source_handle_input_names,
                )
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return EditorIdentitiesResponse(identities=identities)


# ── Timeouts (seconds) — resolved per request so env overrides set
# after import take effect ───────────────────────────────────────
def _trace_timeout() -> float:
    return float_env("HAUTE_TRACE_TIMEOUT", 120.0)


def _preview_timeout() -> float:
    return float_env("HAUTE_PREVIEW_TIMEOUT", 120.0)


def _sink_timeout() -> float:
    return float_env("HAUTE_SINK_TIMEOUT", 300.0)


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
    graph = flatten_graph(graph_payload)
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


def _memory_limit_http_exception(exc: ExecutionAdmissionError) -> HTTPException:
    return HTTPException(status_code=507, detail=exc.to_payload())


def _memory_budget_http_exception(exc: ExecutionMemoryLimitExceededError) -> HTTPException:
    return HTTPException(status_code=507, detail=exc.to_payload())


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
        # version of the same one.
        "port_label",
        repr(port_label),
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
    *,
    memo: GraphFingerprintMemo | None = None,
) -> tuple[str, ...]:
    return (
        *_supersession_key("trace", graph, source, memo=memo),
        "target",
        target_node_id or "",
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
        raise HTTPException(status_code=422, detail=payload) from None
    if operation == "pipeline_preview":
        if identity == _PREVIEW_PROJECTION_REMOTE_IDENTITY:
            raise HTTPException(status_code=400, detail=exc.remote_message) from None
        if identity == _PREVIEW_TARGET_REMOTE_IDENTITY:
            raise HTTPException(status_code=404, detail=exc.remote_message) from None
    if operation == "pipeline_trace":
        if identity in _TRACE_CONTRACT_REMOTE_IDENTITIES:
            raise HTTPException(status_code=422, detail=exc.remote_message) from None
        if identity == _VALUE_ERROR_REMOTE_IDENTITY:
            detail = exc.remote_message
            if detail.startswith(("Trace data does not match", "Trace row match is ambiguous")):
                raise HTTPException(status_code=409, detail=detail) from None
            if detail.startswith("row_index ") and "out of range" in detail:
                raise HTTPException(status_code=400, detail=detail) from None
            if detail.startswith("Target node") and "multiple frames" in detail:
                raise HTTPException(status_code=400, detail=detail) from None
            if detail.startswith("Target node ") and "not found in graph" in detail:
                raise HTTPException(status_code=404, detail=detail) from None
    logger.error(
        "interactive_worker_remote_failure",
        operation=operation,
        remote_type=exc.remote_type,
        remote_module=exc.remote_module,
    )
    raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None


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
    prepared = flatten_graph(graph)
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
            return str(f.relative_to(cwd))
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


@router.get("/pipeline/{name}", response_model=PipelineEditorDocument)
async def get_pipeline(name: str) -> PipelineEditorDocument:
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
    return document


@router.get("/pipeline", response_model=PipelineEditorDocument)
async def get_first_pipeline() -> PipelineEditorDocument:
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

    return await asyncio.to_thread(_find_first)


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


@router.post(
    "/pipeline/repair/remove/dry-run",
    response_model=PipelineRepairPlanResponse,
)
async def dry_run_remove_unavailable_node(
    body: PipelineRepairDryRunRequest,
) -> PipelineRepairPlanResponse | JSONResponse:
    """Plan one exact remove-only recovery repair without writing."""
    try:
        async with save_lock:
            plan = await run_in_threadpool(
                build_remove_unavailable_node_plan,
                project_root=Path.cwd().resolve(),
                request=body,
            )
        return plan.response
    except PipelineRepairError as exc:
        return _pipeline_recovery_error_response(exc.status_code, exc.detail())
    except OSError as exc:
        logger.warning("pipeline_repair_dry_run_io_failed", error=str(exc))
        return _pipeline_recovery_error_response(
            409,
            {
                "code": "repair_artifact_unavailable",
                "message": "A repair artifact could not be read; reload and try again.",
            },
        )


@router.post(
    "/pipeline/repair/remove/apply",
    response_model=PipelineRepairApplyResponse,
)
async def apply_remove_unavailable_node(
    body: PipelineRepairApplyRequest,
) -> PipelineRepairApplyResponse | JSONResponse:
    """Apply one freshly recomputed and explicitly confirmed repair plan."""
    try:
        async with save_lock:
            return await run_in_threadpool(
                apply_remove_unavailable_node_plan,
                project_root=Path.cwd().resolve(),
                request=body,
            )
    except PipelineRepairError as exc:
        return _pipeline_recovery_error_response(exc.status_code, exc.detail())
    except OSError as exc:
        logger.warning("pipeline_repair_apply_io_failed", error=str(exc))
        return _pipeline_recovery_error_response(
            409,
            {
                "code": "repair_artifact_unavailable",
                "message": (
                    "A repair artifact could not be written; original artifacts were restored."
                ),
            },
        )


@router.post("/pipeline/save", response_model=SavePipelineResponse)
async def save_pipeline(body: SavePipelineRequest) -> SavePipelineResponse:
    """Save a graph: .py (source of truth) + config JSON + .haute.json (positions).

    When the graph contains submodels, multiple files are written via
    ``graph_to_code_multi``.

    Bundle 5.M1: acquires the shared ``save_lock`` (defined in
    ``routes/_helpers.py``) so this save is serialised against any
    concurrent ``/api/submodel/create`` or ``/api/submodel/dissolve``.
    See the lock definition for the full rationale + scope.
    """
    try:
        async with save_lock:
            project_root = Path.cwd().resolve()
            if body.source_file.strip():
                target = validate_safe_path(project_root, body.source_file)
                if target.is_file():
                    current_document = await run_in_threadpool(
                        load_pipeline_editor_document,
                        target,
                        project_root=project_root,
                    )
                    if current_document.load_status != "ready":
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "The pipeline is not ready on disk. Reload it and resolve "
                                "its diagnostics before saving."
                            ),
                        )
            svc = SavePipelineService(
                project_root=project_root,
                pipeline_root=pipeline_dir(),
            )
            return await run_in_threadpool(svc.save, body)
    except ConfigError as exc:
        logger.warning("save_pipeline_config_invalid", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/pipeline/read-json", response_model=ReadJsonResponse)
async def read_json_file(body: ReadJsonRequest) -> ReadJsonResponse:
    """Read a JSON artifact from the project root and return its object payload."""
    target = validate_safe_path(_get_project_root(), body.path)
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
    except Exception as exc:
        logger.error("read_json_failed", path=body.path, error=str(exc))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None


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
    pruned = prune_source_switch_edges(graph.edges, node_map, body.source)
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
        error=node_result.error,
        error_line=node_result.error_line,
        timing_ms=node_result.timing_ms,
        memory_bytes=node_result.memory_bytes,
        timings=timings,
        memory=memory,
        schema_warnings=node_result.schema_warnings,
        node_statuses=node_statuses,
        node_columns=node_columns,
        node_available_columns=node_available_columns,
        node_frame_columns=node_frame_columns,
        node_schema_warnings=node_schema_warnings,
        execution_metrics=ExecutionMetricsPayload.model_validate(
            execution_context.metrics_payload(status="completed")
        ),
    )


def _execute_preview_worker(
    graph: PipelineGraph,
    body: PreviewNodeRequest,
    budget: IsolatedExecutionBudget,
) -> PreviewNodeResponse:
    context = create_isolated_execution_context(budget)
    try:
        chunk_size = body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
        try:
            with temporary_streaming_chunk_size(chunk_size):
                results = execute_graph(
                    graph,
                    target_node_id=body.node_id,
                    row_limit=body.row_limit,
                    source=body.source,
                    target_preview_only=True,
                    requested_preview_columns=body.requested_preview_columns,
                    include_schema_metadata=True,
                    port_label=body.port_label,
                    execution_context=context,
                )
            return _preview_response_from_results(graph, body, results, context)
        except (ContractMismatchError, SchemaMismatchError, ParseError, ConfigError) as exc:
            return PreviewNodeResponse(node_id=body.node_id, status="error", error=str(exc))
    finally:
        context.release_admission(preserve_primary_error=True)


def _execute_trace_worker(
    graph: PipelineGraph,
    body: TraceRequest,
    budget: IsolatedExecutionBudget,
) -> dict[str, Any]:
    context = create_isolated_execution_context(budget)
    try:
        chunk_size = body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
        with temporary_streaming_chunk_size(chunk_size):
            result = execute_trace(
                graph,
                row_index=body.row_index,
                target_node_id=body.target_node_id,
                column=body.column,
                row_limit=body.row_limit,
                source=body.source,
                row_values=body.row_values,
                preview=_preview_cache,
                fingerprint_memo=GraphFingerprintMemo(),
                execution_context=context,
            )
            trace_payload = trace_result_to_dict(result)
            TraceResponse.model_validate({"status": "ok", "trace": trace_payload})
            return trace_payload
    finally:
        context.release_admission(preserve_primary_error=True)


@router.post("/pipeline/trace", response_model=TraceResponse)
async def trace_row(body: TraceRequest) -> JSONResponse:
    """Trace a single row through the pipeline, returning per-node snapshots."""
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")
    _ensure_printable_lookup_id(body.target_node_id, "target_node_id")
    _validate_runtime_input_paths(graph)

    trace_token = ExecutionCancellationToken()
    trace_context: ExecutionContext | None = None

    try:
        # One memo per request: the supersession key below and every
        # graph_fingerprint call inside execute_trace share it, so the
        # preamble's utility/**/*.py files are read and hashed at most
        # once per trace click instead of once per fingerprint call.
        fingerprint_memo = GraphFingerprintMemo()
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
                memo=fingerprint_memo,
            )
        )

        async def _run_trace() -> dict[str, Any]:
            nonlocal trace_context
            trace_context = create_admitted_execution_context(
                operation="pipeline_trace",
                profile=ExecutionProfile.PREVIEW_EAGER,
                cancellation_token=trace_token,
            )
            if resolve_interactive_execution_mode() == "process":
                budget = isolated_execution_budget(trace_context)
                return await run_in_interactive_worker(
                    _execute_trace_worker,
                    graph,
                    body,
                    budget,
                    affinity_key=_interactive_affinity_key(
                        graph,
                        body.source,
                        memo=fingerprint_memo,
                    ),
                    timeout_seconds=_trace_timeout(),
                    stop_reason=(lambda: "superseded" if trace_token.cancelled else None),
                    absolute_rss_limit_bytes=budget.process_rss_limit_bytes,
                    memory_growth_limit_bytes=budget.memory_limit_bytes,
                    require_memory_limit=resolve_worker_memory_enforcement() == "required",
                )
            chunk_size = body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE

            def _execute_trace_with_chunk_size() -> dict[str, Any]:
                with temporary_streaming_chunk_size(chunk_size):
                    result = execute_trace(
                        graph,
                        row_index=body.row_index,
                        target_node_id=body.target_node_id,
                        column=body.column,
                        row_limit=body.row_limit,
                        source=body.source,
                        row_values=body.row_values,
                        # Inject the executor's preview cache explicitly so the
                        # trace module is not coupled to a private singleton on
                        # another module.
                        preview=_preview_cache,
                        fingerprint_memo=fingerprint_memo,
                        execution_context=trace_context,
                    )
                    # Serialise to a JSON-safe dict here, still in the
                    # worker thread, so the event loop never walks the
                    # full trace payload.
                    trace_payload = trace_result_to_dict(result)
                    # JSONResponse bypasses FastAPI's response-model
                    # validation. Validate explicitly in this worker so the
                    # typed omission, waterfall and provenance contract is a
                    # real HTTP boundary without moving payload work back onto
                    # the event loop.
                    TraceResponse.model_validate({"status": "ok", "trace": trace_payload})
                    return trace_payload

            return await run_blocking_with_response_timeout(
                _execute_trace_with_chunk_size,
                timeout=_trace_timeout(),
                operation="pipeline_trace",
            )

        trace_dict = await _trace_supersession.run_latest(
            supersession_key,
            _run_trace,
            limiter=_trace_work_slots,
            cancel_active=trace_token.cancel,
            superseded_message="Trace request superseded by a newer request",
        )
        # ``trace_dict`` is already JSON-safe and was validated against
        # ``TraceResponse`` in the worker. Encode it directly so the event
        # loop does not walk the full payload again.
        return JSONResponse({"status": "ok", "trace": trace_dict})
    except ExecutionAdmissionError as e:
        raise _memory_limit_http_exception(e) from None
    except ExecutionMemoryLimitExceededError as e:
        raise _memory_budget_http_exception(e) from None
    except InteractiveWorkerMemoryLimitError as e:
        raise HTTPException(status_code=507, detail=e.to_payload()) from None
    except InteractiveWorkerTimeoutError:
        trace_token.cancel()
        raise HTTPException(
            status_code=504,
            detail=f"Trace execution timed out ({_trace_timeout():.0f}s limit)",
        ) from None
    except InteractiveWorkerStoppedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except InteractiveWorkerCrashedError as e:
        _raise_interactive_worker_crash_http_error(e, operation="pipeline_trace")
    except InteractiveWorkerRemoteError as e:
        _raise_interactive_remote_http_error(e, operation="pipeline_trace")
    except SupersededRequestError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except BlockingWorkTimeoutError as e:
        trace_token.cancel()
        if trace_context is not None:
            timed_out_context = trace_context
            e.background_task.add_done_callback(
                lambda _future: timed_out_context.release_admission()
            )
            trace_context = None
        raise HTTPException(
            status_code=504,
            detail=f"Trace execution timed out ({_trace_timeout():.0f}s limit)",
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Trace execution timed out ({_trace_timeout():.0f}s limit)",
        )
    except HTTPException:
        raise
    except PUBLIC_CONTRACT_ERROR_TYPES as e:
        logger.warning("trace_public_contract_error", **contract_error_payload(e))
        raise contract_error_http_exception(e) from None
    except ContractMismatchError as e:
        # Contract mismatches carry the node id and the symmetric column
        # diff in ``str(e)``.  Surface that directly (422 Unprocessable
        # Entity) instead of collapsing it into the generic 500
        # "check the logs" reply — the point of the contract error is
        # that the user can fix the bad contract in one edit.
        logger.warning("trace_contract_mismatch", error=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        detail = str(e)
        if detail.startswith("Trace data does not match"):
            logger.warning("trace_row_mismatch", error=detail)
            raise HTTPException(status_code=409, detail=detail)
        if detail.startswith("Trace row match is ambiguous"):
            logger.warning("trace_row_match_ambiguous", error=detail)
            raise HTTPException(status_code=409, detail=detail)
        if detail.startswith("row_index ") and "out of range" in detail:
            logger.warning("trace_row_out_of_range", error=detail)
            raise HTTPException(status_code=400, detail=detail)
        if detail.startswith("Target node") and "multiple frames" in detail:
            logger.warning("trace_target_multi_frame", error=detail)
            raise HTTPException(status_code=400, detail=detail)
        if detail.startswith("Target node ") and "not found in graph" in detail:
            logger.warning("trace_target_not_found", error=detail)
            raise HTTPException(status_code=404, detail=detail)
        logger.error("trace_failed", error=detail)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    except Exception as e:
        logger.error("trace_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    finally:
        if trace_context is not None:
            trace_context.release_admission(preserve_primary_error=True)


async def _preview_canonical_graph(body: PreviewNodeRequest) -> PreviewNodeResponse:
    """Run pipeline up to a specific node and return its output.

    Accepts an optional ``row_limit`` (default 100) that is pushed into
    the Polars lazy query plan so only that many rows are scanned.
    """
    preview_token = ExecutionCancellationToken()
    preview_context: ExecutionContext | None = None

    try:
        graph = flatten_graph(body.graph)
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

        async def _run_preview() -> PreviewNodeResponse:
            nonlocal preview_context
            preview_context = create_admitted_execution_context(
                operation="pipeline_preview",
                profile=ExecutionProfile.PREVIEW_EAGER,
                cancellation_token=preview_token,
            )
            if resolve_interactive_execution_mode() == "process":
                budget = isolated_execution_budget(preview_context)
                return await run_in_interactive_worker(
                    _execute_preview_worker,
                    graph,
                    body,
                    budget,
                    affinity_key=_interactive_affinity_key(
                        graph,
                        body.source,
                        memo=fingerprint_memo,
                    ),
                    timeout_seconds=_preview_timeout(),
                    stop_reason=(lambda: "superseded" if preview_token.cancelled else None),
                    absolute_rss_limit_bytes=budget.process_rss_limit_bytes,
                    memory_growth_limit_bytes=budget.memory_limit_bytes,
                    require_memory_limit=resolve_worker_memory_enforcement() == "required",
                )
            chunk_size = body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE

            def _execute_graph_with_chunk_size() -> dict[str, Any]:
                with temporary_streaming_chunk_size(chunk_size):
                    return execute_graph(
                        graph,
                        target_node_id=body.node_id,
                        row_limit=body.row_limit,
                        source=body.source,
                        target_preview_only=True,
                        requested_preview_columns=body.requested_preview_columns,
                        include_schema_metadata=True,
                        port_label=body.port_label,
                        execution_context=preview_context,
                    )

            results = await run_blocking_with_response_timeout(
                _execute_graph_with_chunk_size,
                timeout=_preview_timeout(),
                operation="pipeline_preview",
            )
            return _preview_response_from_results(graph, body, results, preview_context)

        response = await _preview_supersession.run_latest(
            _preview_supersession_key(
                graph,
                body.source,
                body.node_id,
                body.row_limit,
                body.requested_preview_columns,
                body.port_label,
                memo=fingerprint_memo,
            ),
            _run_preview,
            limiter=_preview_work_slots,
            cancel_active=preview_token.cancel,
            superseded_message="Preview request superseded by a newer request",
        )
        return response
    except ExecutionAdmissionError as e:
        raise _memory_limit_http_exception(e) from None
    except ExecutionMemoryLimitExceededError as e:
        raise _memory_budget_http_exception(e) from None
    except InteractiveWorkerMemoryLimitError as e:
        raise HTTPException(status_code=507, detail=e.to_payload()) from None
    except InteractiveWorkerTimeoutError:
        preview_token.cancel()
        raise HTTPException(
            status_code=504,
            detail=f"Preview execution timed out ({_preview_timeout():.0f}s limit)",
        ) from None
    except InteractiveWorkerStoppedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except InteractiveWorkerCrashedError as e:
        _raise_interactive_worker_crash_http_error(e, operation="pipeline_preview")
    except InteractiveWorkerRemoteError as e:
        _raise_interactive_remote_http_error(e, operation="pipeline_preview")
    except SupersededRequestError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except BlockingWorkTimeoutError as e:
        preview_token.cancel()
        if preview_context is not None:
            timed_out_context = preview_context
            e.background_task.add_done_callback(
                lambda _future: timed_out_context.release_admission()
            )
            preview_context = None
        raise HTTPException(
            status_code=504,
            detail=f"Preview execution timed out ({_preview_timeout():.0f}s limit)",
        )
    except TimeoutError:
        preview_token.cancel()
        raise HTTPException(
            status_code=504,
            detail=f"Preview execution timed out ({_preview_timeout():.0f}s limit)",
        )
    except HTTPException:
        raise
    except PUBLIC_CONTRACT_ERROR_TYPES as e:
        logger.warning("preview_public_contract_error", **contract_error_payload(e))
        raise contract_error_http_exception(e) from None
    except (ContractMismatchError, SchemaMismatchError) as e:
        # ``_execute_eager_core`` re-raises contract and schema mismatches even
        # with ``swallow_errors=True`` (API-level violations, not per-node
        # transient failures), so the preview path can receive one here.
        # Surface the node + column diagnostic from ``str(e)`` via the
        # target node's ``NodeResult.error`` — the frontend renders that
        # field in-situ, which is a better UX than a generic 500 banner.
        logger.warning("preview_contract_mismatch", error=str(e))
        return PreviewNodeResponse(
            node_id=body.node_id,
            status="error",
            error=str(e),
        )
    except ParseError as e:
        logger.warning("preview_graph_shape_invalid", error=str(e))
        return PreviewNodeResponse(
            node_id=body.node_id,
            status="error",
            error=str(e),
        )
    except ConfigError as e:
        logger.warning("preview_config_invalid", error=str(e))
        return PreviewNodeResponse(
            node_id=body.node_id,
            status="error",
            error=str(e),
        )
    except PreviewProjectionError as e:
        logger.warning("preview_bad_request", error=str(e))
        raise HTTPException(status_code=400, detail=str(e)) from None
    except _PreviewTargetNotReturnedError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except Exception as e:
        logger.error("preview_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    finally:
        if preview_context is not None:
            preview_context.release_admission(preserve_primary_error=True)


@router.post("/pipeline/preview", response_model=PreviewNodeResponse)
async def preview_node(body: PreviewNodeRequest) -> PreviewNodeResponse:
    """Preview a client-supplied canonical graph."""
    return await _preview_canonical_graph(body)


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
    # Deliberately not haute._topo.ancestors: that helper walks canonical
    # GraphEdge models, and constructing canonical edges from unvalidated
    # recovery elements would cross the strict/recovery boundary this
    # closure exists to protect.
    incoming: dict[str, list[str]] = {}
    for edge in document.edges:
        incoming.setdefault(edge.target_recovery_id, []).append(edge.source_recovery_id)
    closure = {target_id}
    pending = [target_id]
    while pending:
        current = pending.pop()
        for source in incoming.get(current, []):
            if source in closure:
                continue
            closure.add(source)
            pending.append(source)
    return closure


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
            sourcePort=edge.source_port,
            targetPort=edge.target_port,
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
        **(
            {"streaming_chunk_size": body.streaming_chunk_size}
            if body.streaming_chunk_size is not None
            else {}
        ),
    )


@router.post("/pipeline/recovery-preview", response_model=PreviewNodeResponse)
async def recovery_preview_node(
    body: RecoveryPreviewRequest,
) -> PreviewNodeResponse | JSONResponse:
    """Preview one server-validated ready closure from a recovery document."""
    try:
        _ensure_printable_lookup_id(body.target_recovery_id, "target_recovery_id")
        project_root = _get_project_root().resolve()
        source_path = validate_safe_path(project_root, body.source_file)
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
        return await _preview_canonical_graph(request)
    except _RecoveryPreviewRequestError as exc:
        return _pipeline_recovery_error_response(exc.status_code, exc.detail)


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
    streaming_chunk_size: int | None,
    project_root: str,
    overwrite: bool,
    staging_path: str | None,
    budget: IsolatedExecutionBudget,
) -> _OutputWriteWorkerOutcome:
    """Execute one sink while leaving file publication to the parent."""
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        prepared = prepare_data_output(
            graph,
            output_node_id,
            source,
            execution_context=context,
            streaming_chunk_size=streaming_chunk_size,
            project_root=project_root,
            overwrite=overwrite,
            staging_path=staging_path,
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
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        return _OutputWriteWorkerOutcome(
            failure_kind="memory",
            detail=str(exc),
            payload=exc.to_payload(),
        )
    finally:
        if context is not None:
            context.release_admission(preserve_primary_error=True)


def _output_write_transaction(
    graph: PipelineGraph,
    output_node_id: str,
    source: str,
    streaming_chunk_size: int | None,
    project_root: Path,
    overwrite: bool,
    final_path: Path | None,
    staging_path: Path | None,
    budget: IsolatedExecutionBudget,
    cancellation_requested: WorkerCancellationGate,
    *,
    display_path: str,
) -> WriteOutputResponse:
    """Supervise a sink child and own its only publication boundary."""
    prepared: PreparedDataOutput | None = None
    primary_error: BaseException | None = None
    try:
        if cancellation_requested.is_set():
            raise IsolatedWorkerStoppedError(terminal_reason="cancelled")
        config = worker_config_for_memory_policy(
            memory_limit_bytes=budget.memory_limit_bytes,
            timeout_seconds=_sink_timeout(),
            stop_reason=(lambda: "cancelled" if cancellation_requested.is_set() else None),
            process_name="haute-output-write",
        )
        outcome = run_isolated_worker(
            _prepare_data_output_worker,
            graph,
            output_node_id,
            source,
            streaming_chunk_size,
            str(project_root),
            overwrite,
            None if staging_path is None else str(staging_path),
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
        prepared = outcome.prepared
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
    payload: dict[str, object] = {
        "error_code": "memory_limit",
        "operation": "pipeline_write_output",
        "reason": "worker_memory_limit",
    }
    if memory_limit_bytes is not None:
        payload["memory_limit_bytes"] = memory_limit_bytes
    if isinstance(exc, IsolatedWorkerMemoryLimitExceededError):
        payload.update(
            rss_bytes=exc.rss_bytes,
            rss_limit_bytes=exc.rss_limit_bytes,
            reason="worker_rss_limit_exceeded",
        )
    elif isinstance(exc, IsolatedWorkerMemoryLimitUnsupportedError) or (
        isinstance(exc, IsolatedWorkerRemoteError)
        and exc.remote_type == "NativeMemoryLimitUnsupportedError"
    ):
        payload["reason"] = "native_memory_cap_unavailable"
    elif isinstance(exc, IsolatedWorkerCrashedError):
        payload["reason"] = "worker_may_have_exceeded_memory_limit"
    return payload


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
    is_file_target = format_group(format_for_config(config)) == "file"
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
            return _output_write_transaction(
                graph,
                body.node_id,
                body.source,
                body.streaming_chunk_size,
                project_root,
                body.overwrite,
                resolved_output if staging_path is not None else None,
                staging_path,
                budget,
                cancellation_requested,
                display_path=display_path,
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
    except ExecutionAdmissionError as e:
        raise _memory_limit_http_exception(e) from None
    except ExecutionMemoryLimitExceededError as e:
        raise _memory_budget_http_exception(e) from None
    except _OutputWriteWorkerError as e:
        if e.kind == "contract":
            raise HTTPException(status_code=422, detail=e.payload or e.detail) from None
        if e.kind == "bounded":
            raise HTTPException(status_code=422, detail=e.detail) from None
        if e.kind == "destination_exists":
            raise HTTPException(status_code=409, detail=e.detail) from None
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
        if e.terminal_reason == "memory_limited":
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
        if e.remote_type in {
            "MemoryError",
            "ExecutionAdmissionError",
            "ExecutionMemoryLimitExceededError",
            "NativeMemoryLimitUnsupportedError",
        }:
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
            detail=f"Sink execution timed out ({_sink_timeout():.0f}s limit)",
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
            detail=f"Sink execution timed out ({_sink_timeout():.0f}s limit)",
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error("sink_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None
    finally:
        if output_context is not None:
            output_context.release_admission(preserve_primary_error=True)
