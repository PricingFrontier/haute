"""OUTPUT assembler dry-run route (MULTI_FRAME_PLAN piece 8 / commit-7 aux).

The OUTPUT editor previews the assembled response JSON from an *in-progress*
(unsaved/volatile) ``outputMapping`` before persisting it. This route:

1. validates the mapping (schema-only — A4, data-independent → the loudest,
   data-free failure);
2. swaps it into the target OUTPUT node's config (overriding whatever is on
   disk, so the editor previews unsaved edits);
3. runs the graph up to that node and returns the rendered document.

The render points already prune (``render_output_document``), so the returned
``document`` is the real response shape — equality with the assembled document
up to empty collections.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from haute._execution_admission import (
    IsolatedExecutionBudget,
    create_isolated_execution_context,
)
from haute._execution_context import ExecutionContext
from haute._flatten import flatten_executable_graph
from haute._logging import get_logger
from haute._output_assembler import OutputMappingSchemaError, validate_v2_output_mapping
from haute._pipeline_settings import project_pipeline_settings
from haute.errors import ConfigError, ContractMismatchError
from haute.executor import execute_graph
from haute.graph_utils import NodeType
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_http_exception,
)
from haute.routes.pipeline import (
    _interactive_affinity_key,
    _interactive_failures,
    _InteractiveRun,
    _run_interactive,
    _validate_runtime_input_paths,
)
from haute.schemas import Graph, OutputAssembleDryRunRequest, OutputAssembleDryRunResponse

logger = get_logger(component="server.output_assemble")

router = APIRouter(prefix="/api/output-assemble", tags=["output-assemble"])


def _dry_run_timeout() -> float:
    """The pipeline time limit in seconds, read from the pipeline settings per request."""
    return project_pipeline_settings().pipeline_time_limit_seconds


def _assembled_document(
    graph: Graph,
    node_id: str,
    row_limit: int,
    source: str,
    context: ExecutionContext,
) -> OutputAssembleDryRunResponse:
    """Run *graph* up to the OUTPUT node and answer its document, in the worker or a thread.

    The render points prune the assembled frame, so the node's preview is the
    response document.
    """
    results = execute_graph(
        graph,
        target_node_id=node_id,
        row_limit=row_limit,
        source=source,
        target_preview_only=True,
        execution_context=context,
    )
    node_result = results.get(node_id)
    if node_result is None or node_result.status != "ok":
        detail = (node_result.error if node_result else None) or "Assembly failed"
        return OutputAssembleDryRunResponse(status="error", error=detail)
    return OutputAssembleDryRunResponse(
        status="ok",
        document=node_result.preview,
        row_count=node_result.row_count,
    )


def _execute_output_assemble_dry_run_worker(
    graph: Graph,
    node_id: str,
    row_limit: int,
    source: str,
    budget: IsolatedExecutionBudget,
) -> OutputAssembleDryRunResponse:
    """Execute one OUTPUT dry-run with worker-local memory accounting."""
    context = create_isolated_execution_context(budget)
    try:
        return _assembled_document(graph, node_id, row_limit, source, context)
    finally:
        context.release_admission(preserve_primary_error=True)


@router.post("/dry-run", response_model=OutputAssembleDryRunResponse)
async def output_assemble_dry_run(
    body: OutputAssembleDryRunRequest,
) -> OutputAssembleDryRunResponse:
    """Assemble an OUTPUT node's document from an unsaved ``outputMapping``."""
    # 1. Mapping schema validation — fires on the mapping alone, before any
    #    data is touched (A4). The loudest, cheapest failure.
    try:
        validate_v2_output_mapping(body.output_mapping)
    except PUBLIC_CONTRACT_ERROR_TYPES as exc:
        raise contract_error_http_exception(exc) from None
    except OutputMappingSchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    graph = flatten_executable_graph(body.graph)
    if not graph.nodes:
        raise HTTPException(status_code=400, detail="Empty graph")
    node = graph.node_map.get(body.node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node '{body.node_id}' not found")
    if node.data.nodeType != NodeType.OUTPUT:
        raise HTTPException(
            status_code=400,
            detail=f"Node '{body.node_id}' is not an OUTPUT node",
        )
    _validate_runtime_input_paths(graph)

    # 2. Swap the volatile mapping into the OUTPUT node's config — the dry-run
    #    overrides whatever is persisted so the editor previews unsaved edits.
    node.data.config = {
        "outputMapping": body.output_mapping,
        "outputFormat": body.output_format,
    }

    # 3. Run up to the OUTPUT node.
    run = _InteractiveRun("output_assemble_dry_run")
    with _interactive_failures(run, timed_out="Output dry-run timed out"):
        try:
            return await _run_interactive(
                run,
                worker=_execute_output_assemble_dry_run_worker,
                worker_args=lambda budget: (
                    graph,
                    body.node_id,
                    body.row_limit,
                    body.source,
                    budget,
                ),
                in_thread=lambda context: _assembled_document(
                    graph, body.node_id, body.row_limit, body.source, context
                ),
                affinity_key=_interactive_affinity_key(graph, body.source),
                timeout=_dry_run_timeout(),
            )
        except PUBLIC_CONTRACT_ERROR_TYPES:
            raise
        except (OutputMappingSchemaError, ConfigError, ContractMismatchError) as exc:
            # A port the mapping names is not wired into the OUTPUT node, etc.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
