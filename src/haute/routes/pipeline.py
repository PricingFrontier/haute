"""Pipeline CRUD, preview, trace, and sink endpoints."""

from __future__ import annotations

import asyncio
import os
import tomllib
from pathlib import Path

from fastapi import APIRouter, HTTPException

from haute._logging import get_logger
from haute._topo import ancestors
from haute.errors import ContractMismatchError
from haute.executor import _preview_cache, execute_graph, execute_sink
from haute.graph_utils import (
    PipelineGraph,
    _prune_live_switch_edges,
    flatten_graph,
)
from haute.parser import parse_pipeline_file
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    discover_pipelines,
    lookup_pipeline_by_name,
    parse_pipeline_to_graph,
    pipeline_dir,
    raise_pipeline_not_found,
)
from haute.routes._save_pipeline import SavePipelineService
from haute.routes._timeouts import run_blocking_with_response_timeout
from haute.schemas import (
    NodeMemoryInfo,
    NodeTimingInfo,
    PipelineSummary,
    PreviewNodeRequest,
    PreviewNodeResponse,
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
    """
    svc = SavePipelineService(project_root=Path.cwd(), pipeline_root=pipeline_dir())
    return svc.save(body)


@router.post("/pipeline/trace", response_model=TraceResponse)
async def trace_row(body: TraceRequest) -> TraceResponse:
    """Trace a single row through the pipeline, returning per-node snapshots."""
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")

    try:
        result = await run_blocking_with_response_timeout(
            execute_trace,
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
            # protocol — its ``try_get`` returns the slot dict on hit
            # or ``None`` on miss.
            preview=_preview_cache,
            timeout=_TRACE_TIMEOUT,
            operation="pipeline_trace",
        )
        return TraceResponse(
            status="ok",
            trace=trace_result_to_dict(result),  # type: ignore[arg-type]
        )
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

    try:
        results = await run_blocking_with_response_timeout(
            execute_graph,
            graph,
            target_node_id=body.node_id,
            row_limit=body.row_limit,
            source=body.source,
            target_preview_only=True,
            timeout=_PREVIEW_TIMEOUT,
            operation="pipeline_preview",
        )
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
            pruned = _prune_live_switch_edges(
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

        return PreviewNodeResponse(
            node_id=body.node_id,
            status=node_result.status,
            row_count=node_result.row_count,
            column_count=node_result.column_count,
            columns=node_result.columns,
            available_columns=node_result.available_columns,
            preview=node_result.preview,
            error=node_result.error,
            error_line=node_result.error_line,
            timing_ms=node_result.timing_ms,
            memory_bytes=node_result.memory_bytes,
            timings=timings,
            memory=memory,
            schema_warnings=node_result.schema_warnings,
            node_statuses=node_statuses,
        )
    except TimeoutError:
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
    except Exception as e:
        logger.error("preview_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/pipeline/sink", response_model=SinkResponse)
async def execute_sink_node(body: SinkRequest) -> SinkResponse:
    """Execute the pipeline up to a sink node and write output to disk.

    Only called on explicit user action (Write button), not during normal run/preview.
    """
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")

    try:
        result = await run_blocking_with_response_timeout(
            execute_sink,
            graph,
            sink_node_id=body.node_id,
            source=body.source,
            timeout=_SINK_TIMEOUT,
            operation="pipeline_sink",
        )
        return result
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Sink execution timed out ({_SINK_TIMEOUT:.0f}s limit)",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("sink_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
