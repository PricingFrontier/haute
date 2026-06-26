"""Pipeline CRUD, preview, trace, and sink endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._hashing import content_hash_bytes
from haute._io import read_user_text
from haute._json_safe import rows_to_json_safe
from haute._logging import get_logger
from haute._path_resolution import resolve_runtime_file_path
from haute._polars_utils import DEFAULT_STREAMING_CHUNK_SIZE, temporary_streaming_chunk_size
from haute._sandbox import _get_project_root
from haute._topo import ancestors
from haute.errors import (
    BoundedMemoryUnsupportedError,
    ConfigError,
    ContractMismatchError,
    ParseError,
)
from haute.execution import prune_source_switch_edges
from haute.executor import (
    PreviewProjectionError,
    _preview_cache,
    execute_graph,
    execute_sink,
    resolve_sink_output_path,
)
from haute.graph_utils import (
    NodeType,
    PipelineGraph,
    flatten_graph,
    graph_fingerprint,
)
from haute.parser import parse_pipeline_file
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    discover_pipelines,
    lookup_pipeline_by_name,
    parse_pipeline_to_graph,
    pipeline_dir,
    raise_pipeline_not_found,
    save_lock,
    validate_safe_path,
)
from haute.routes._save_pipeline import SavePipelineService
from haute.routes._supersession import SupersededRequestError, SupersessionCoordinator
from haute.routes._timeouts import (
    BlockingWorkTimeoutError,
    run_blocking_with_response_timeout,
)
from haute.schemas import (
    ExecutionMetricsPayload,
    NodeMemoryInfo,
    NodeTimingInfo,
    PipelineSummary,
    PreviewNodeRequest,
    PreviewNodeResponse,
    ReadJsonRequest,
    ReadJsonResponse,
    SavePipelineRequest,
    SavePipelineResponse,
    SinkRequest,
    SinkResponse,
    TraceRequest,
    TraceResponse,
)
from haute.trace import execute_trace, trace_result_to_dict

logger = get_logger(component="server.pipeline")

router = APIRouter(prefix="/api", tags=["pipeline"])

# ── Timeout constants (seconds) ──────────────────────────────────
_TRACE_TIMEOUT = float(os.environ.get("HAUTE_TRACE_TIMEOUT", "120"))
_PREVIEW_TIMEOUT = float(os.environ.get("HAUTE_PREVIEW_TIMEOUT", "120"))
_SINK_TIMEOUT = float(os.environ.get("HAUTE_SINK_TIMEOUT", "300"))

_preview_supersession = SupersessionCoordinator()
_trace_supersession = SupersessionCoordinator()


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


_PREVIEW_MAX_CONCURRENCY = _positive_int_from_env("HAUTE_PREVIEW_MAX_CONCURRENCY", 2)
_TRACE_MAX_CONCURRENCY = _positive_int_from_env("HAUTE_TRACE_MAX_CONCURRENCY", 2)
_preview_work_slots = asyncio.Semaphore(_PREVIEW_MAX_CONCURRENCY)
_trace_work_slots = asyncio.Semaphore(_TRACE_MAX_CONCURRENCY)


_RUNTIME_INPUT_PATH_CONFIG_BY_NODE_TYPE: dict[NodeType, str] = {
    NodeType.API_INPUT: "path",
    NodeType.DATA_SOURCE: "path",
    NodeType.EXTERNAL_FILE: "path",
}


def _ensure_printable_lookup_id(value: str | None, field_name: str) -> None:
    if value is not None and not value.isprintable():
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} contains control characters",
        )


def _validate_runtime_input_paths(graph: PipelineGraph) -> None:
    """Reject API-submitted input paths that resolve outside the project root."""
    for node in graph.nodes:
        config = node.data.config
        key = _RUNTIME_INPUT_PATH_CONFIG_BY_NODE_TYPE.get(node.data.nodeType)
        if node.data.nodeType == NodeType.OPTIMISER_APPLY and config.get("sourceType") == "file":
            key = "artifact_path"
        if key is None:
            continue

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
        except ValueError as exc:
            status_code = 400 if "embedded null byte" in str(exc) else 403
            raise HTTPException(status_code=status_code, detail=str(exc)) from None


def _validate_sink_output_path(
    graph: PipelineGraph,
    sink_node: Any,
    *,
    project_root: Path,
) -> None:
    raw_path = sink_node.data.config.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return
    fmt = sink_node.data.config.get("format", "parquet")
    try:
        resolve_sink_output_path(
            graph,
            raw_path,
            str(fmt),
            project_root=project_root,
        )
    except ValueError as exc:
        status_code = 400 if "embedded null byte" in str(exc) else 403
        raise HTTPException(status_code=status_code, detail=str(exc)) from None


def _memory_limit_http_exception(exc: ExecutionAdmissionError) -> HTTPException:
    return HTTPException(status_code=507, detail=exc.to_payload())


def _memory_budget_http_exception(exc: ExecutionMemoryLimitExceededError) -> HTTPException:
    return HTTPException(status_code=507, detail=exc.to_payload())


def _supersession_key(
    operation: str,
    graph: PipelineGraph,
    source: str,
) -> tuple[str, ...]:
    return (
        operation,
        graph.source_file or "",
        source,
        graph_fingerprint(graph),
    )


def _preview_supersession_key(
    graph: PipelineGraph,
    source: str,
    node_id: str,
    row_limit: int,
    requested_preview_columns: list[str] | None,
    port_label: str | None,
) -> tuple[str, ...]:
    requested_columns = tuple(requested_preview_columns or ())
    return (
        *_supersession_key("preview", graph, source),
        "node",
        node_id,
        "row_limit",
        str(row_limit),
        "requested_preview_columns",
        *requested_columns,
        # A different frame selection is a DISTINCT request, not a newer
        # version of the same one — so frame B's request must not cancel
        # frame A's mid-flight. "" reproduces the legacy first-frame key
        # exactly for single-frame / default-frame previews.
        "port_label",
        port_label or "",
    )


def _trace_row_values_fingerprint(row_values: dict[str, Any] | None) -> str:
    if row_values is None:
        return ""
    payload = json.dumps(row_values, sort_keys=True, separators=(",", ":"))
    return content_hash_bytes(payload.encode())


def _trace_supersession_key(
    graph: PipelineGraph,
    source: str,
    target_node_id: str | None,
    row_index: int,
    column: str | None,
    row_limit: int,
    row_values: dict[str, Any] | None,
) -> tuple[str, ...]:
    return (
        *_supersession_key("trace", graph, source),
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

    async def _parse_one(f: Path) -> PipelineSummary:
        try:
            graph = await asyncio.to_thread(parse_pipeline_file, f)
            return PipelineSummary(
                name=graph.pipeline_name or f.stem,
                description=graph.pipeline_description or "",
                file=str(f.relative_to(cwd)),
                node_count=len(graph.nodes),
            )
        except Exception as e:
            return PipelineSummary(
                name=f.stem,
                file=str(f),
                error=str(e),
            )

    return list(await asyncio.gather(*[_parse_one(f) for f in files]))


@router.get("/pipeline/{name}", response_model=PipelineGraph)
async def get_pipeline(name: str) -> PipelineGraph:
    """Return the graph for a specific pipeline."""

    def _find() -> PipelineGraph | None:
        # O(1) lookup via cached index
        f = lookup_pipeline_by_name(name)
        if f is not None:
            try:
                return parse_pipeline_to_graph(f)
            except Exception as e:
                logger.warning("parse_failed", file=f.name, error=str(e))

        # Fallback: linear scan (index may be stale)
        for f in discover_pipelines():
            try:
                graph = parse_pipeline_to_graph(f)
                if graph.pipeline_name == name:
                    return graph
            except Exception as e:
                logger.warning("parse_failed", file=f.name, error=str(e))
                continue
        return None

    graph = await asyncio.to_thread(_find)
    if graph is None:
        raise_pipeline_not_found(name)
    return graph


@router.get("/pipeline", response_model=PipelineGraph)
async def get_first_pipeline() -> PipelineGraph:
    """Return the graph for the active pipeline, or an empty canvas.

    Python file is the source of truth. Sidecar .haute.json provides positions.
    """
    cwd = Path.cwd()

    def _find_first() -> PipelineGraph:
        best: PipelineGraph | None = None
        for f in discover_pipelines():
            try:
                graph = parse_pipeline_to_graph(f)
                graph.source_file = str(f.relative_to(cwd))
                if graph.nodes:
                    return graph
                if best is None:
                    best = graph
            except Exception as e:
                logger.warning("parse_failed", file=f.name, error=str(e))
                continue
        return best or PipelineGraph()

    return await asyncio.to_thread(_find_first)


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
            svc = SavePipelineService(project_root=Path.cwd(), pipeline_root=pipeline_dir())
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


@router.post("/pipeline/trace", response_model=TraceResponse)
async def trace_row(body: TraceRequest) -> TraceResponse:
    """Trace a single row through the pipeline, returning per-node snapshots."""
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")
    _ensure_printable_lookup_id(body.target_node_id, "target_node_id")
    _validate_runtime_input_paths(graph)

    try:

        async def _run_trace() -> Any:
            chunk_size = body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE

            def _execute_trace_with_chunk_size() -> Any:
                with temporary_streaming_chunk_size(chunk_size):
                    return execute_trace(
                        graph,
                        row_index=body.row_index,
                        target_node_id=body.target_node_id,
                        column=body.column,
                        row_limit=body.row_limit,
                        source=body.source,
                        row_values=body.row_values,
                        # Inject the executor's preview cache explicitly so the
                        # trace module is not coupled to a private singleton on
                        # another module.  ``FingerprintCache``
                        # already satisfies the :class:`~haute.trace.PreviewReader`
                        # protocol - its ``try_get`` returns the slot dict on hit
                        # or ``None`` on miss.
                        preview=_preview_cache,
                    )

            return await run_blocking_with_response_timeout(
                _execute_trace_with_chunk_size,
                timeout=_TRACE_TIMEOUT,
                operation="pipeline_trace",
            )

        result = await _trace_supersession.run_latest(
            _trace_supersession_key(
                graph,
                body.source,
                body.target_node_id,
                body.row_index,
                body.column,
                body.row_limit,
                body.row_values,
            ),
            _run_trace,
            limiter=_trace_work_slots,
            superseded_message="Trace request superseded by a newer request",
        )
        return TraceResponse(
            status="ok",
            trace=trace_result_to_dict(result),  # type: ignore[arg-type]
        )
    except SupersededRequestError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Trace execution timed out ({_TRACE_TIMEOUT:.0f}s limit)",
        )
    except HTTPException:
        raise
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
        if detail.startswith("row_index ") and "out of range" in detail:
            logger.warning("trace_row_out_of_range", error=detail)
            raise HTTPException(status_code=400, detail=detail)
        if detail.startswith("Target node ") and "not found in graph" in detail:
            logger.warning("trace_target_not_found", error=detail)
            raise HTTPException(status_code=404, detail=detail)
        logger.error("trace_failed", error=detail)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    except Exception as e:
        logger.error("trace_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/pipeline/preview", response_model=PreviewNodeResponse)
async def preview_node(body: PreviewNodeRequest) -> PreviewNodeResponse:
    """Run pipeline up to a specific node and return its output.

    Accepts an optional ``row_limit`` (default 100) that is pushed into
    the Polars lazy query plan so only that many rows are scanned.
    """
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

    preview_token = ExecutionCancellationToken()
    preview_context: ExecutionContext | None = None

    try:

        async def _run_preview() -> dict[str, Any]:
            nonlocal preview_context
            preview_context = create_admitted_execution_context(
                operation="pipeline_preview",
                profile=ExecutionProfile.PREVIEW_EAGER,
                cancellation_token=preview_token,
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

            return await run_blocking_with_response_timeout(
                _execute_graph_with_chunk_size,
                timeout=_PREVIEW_TIMEOUT,
                operation="pipeline_preview",
            )

        results = await _preview_supersession.run_latest(
            _preview_supersession_key(
                graph,
                body.source,
                body.node_id,
                body.row_limit,
                body.requested_preview_columns,
                body.port_label,
            ),
            _run_preview,
            limiter=_preview_work_slots,
            cancel_active=preview_token.cancel,
            superseded_message="Preview request superseded by a newer request",
        )
        if preview_context is None:
            raise RuntimeError("Preview execution did not create an execution context")
        node_result = results.get(body.node_id)
        if not node_result:
            raise HTTPException(
                status_code=404,
                detail=f"Node '{body.node_id}' not found in results",
            )

        node_map = graph.node_map

        # Only include timings/memory for ancestors of the target node
        # (+ itself), pruned by the active source so the unused
        # live_switch branch is excluded.
        if body.node_id:
            pruned = prune_source_switch_edges(
                graph.edges,
                node_map,
                body.source,
            )
            relevant = ancestors(
                body.node_id,
                pruned,
                set(node_map.keys()),
            )
        else:
            relevant = set(results.keys())

        timings = [
            NodeTimingInfo(
                node_id=nid,
                label=node_map[nid].data.label,
                timing_ms=r.timing_ms,
            )
            for nid, r in results.items()
            if nid in node_map and nid in relevant
        ]

        memory = [
            NodeMemoryInfo(
                node_id=nid,
                label=node_map[nid].data.label,
                memory_bytes=r.memory_bytes,
            )
            for nid, r in results.items()
            if nid in node_map and nid in relevant
        ]

        node_statuses = {nid: r.status for nid, r in results.items() if nid in relevant}
        node_columns = {
            nid: r.columns for nid, r in results.items() if nid in node_map and nid in relevant
        }
        node_available_columns = {
            nid: r.available_columns or r.columns
            for nid, r in results.items()
            if nid in node_map and nid in relevant
        }
        # Per-frame columns for multi-port producers, keyed
        # node_id → port_label → columns. Only present for nodes that
        # actually emit 2+ frames (multi-table apiInput today; submodels
        # / external callouts later), so the dict is empty for the common
        # single-frame graph. The OUTPUT editor reads this to learn each
        # incoming frame's schema regardless of source type.
        node_frame_columns = {
            nid: r.frame_columns
            for nid, r in results.items()
            if nid in node_map and nid in relevant and r.frame_columns
        }
        node_schema_warnings = {
            nid: r.schema_warnings
            for nid, r in results.items()
            if nid in node_map and nid in relevant
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
                preview_context.metrics_payload(status="completed")
            ),
        )
    except ExecutionAdmissionError as e:
        raise _memory_limit_http_exception(e) from None
    except ExecutionMemoryLimitExceededError as e:
        raise _memory_budget_http_exception(e) from None
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
            detail=f"Preview execution timed out ({_PREVIEW_TIMEOUT:.0f}s limit)",
        )
    except TimeoutError:
        preview_token.cancel()
        raise HTTPException(
            status_code=504,
            detail=f"Preview execution timed out ({_PREVIEW_TIMEOUT:.0f}s limit)",
        )
    except HTTPException:
        raise
    except ContractMismatchError as e:
        # ``_execute_eager_core`` re-raises ``ContractMismatchError`` even
        # with ``swallow_errors=True`` (API-level violation, not a per-node
        # transient failure), so the preview path can receive one here.
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
    except Exception as e:
        logger.error("preview_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    finally:
        if preview_context is not None:
            preview_context.release_admission()


@router.post("/pipeline/sink", response_model=SinkResponse)
async def execute_sink_node(body: SinkRequest) -> SinkResponse:
    """Execute the pipeline up to a sink node and write output to disk.

    Only called on explicit user action (Write button), not during normal run/preview.
    """
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")
    _ensure_printable_lookup_id(body.node_id, "node_id")
    _validate_runtime_input_paths(graph)
    sink_node = graph.node_map.get(body.node_id)
    if sink_node is None:
        raise HTTPException(status_code=404, detail=f"Sink node '{body.node_id}' not found")
    if sink_node.data.nodeType != NodeType.DATA_SINK:
        raise HTTPException(
            status_code=400,
            detail=f"Node '{body.node_id}' is not a data sink",
        )
    project_root = Path.cwd().resolve()
    _validate_sink_output_path(graph, sink_node, project_root=project_root)

    sink_context: ExecutionContext | None = None
    try:
        sink_context = create_admitted_execution_context(
            operation="pipeline_sink",
            profile=ExecutionProfile.LAZY_SINK,
        )
        result = await run_blocking_with_response_timeout(
            execute_sink,
            graph,
            sink_node_id=body.node_id,
            source=body.source,
            execution_context=sink_context,
            streaming_chunk_size=body.streaming_chunk_size,
            project_root=project_root,
            timeout=_SINK_TIMEOUT,
            operation="pipeline_sink",
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
    except BoundedMemoryUnsupportedError as e:
        logger.warning(
            "sink_bounded_streaming_unsupported",
            error=str(e),
            execution_metrics=(
                sink_context.metrics_payload(status="error") if sink_context is not None else None
            ),
        )
        raise HTTPException(status_code=422, detail=str(e)) from None
    except BlockingWorkTimeoutError as e:
        if sink_context is not None:
            timed_out_context = sink_context
            timed_out_context.cancel()
            e.background_task.add_done_callback(
                lambda _future: timed_out_context.release_admission()
            )
            sink_context = None
        raise HTTPException(
            status_code=504,
            detail=f"Sink execution timed out ({_SINK_TIMEOUT:.0f}s limit)",
        )
    except TimeoutError:
        if sink_context is not None:
            sink_context.cancel()
        raise HTTPException(
            status_code=504,
            detail=f"Sink execution timed out ({_SINK_TIMEOUT:.0f}s limit)",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("sink_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    finally:
        if sink_context is not None:
            sink_context.release_admission()
