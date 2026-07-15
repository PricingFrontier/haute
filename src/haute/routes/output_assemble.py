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

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from haute._env import float_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
)
from haute._execution_context import ExecutionProfile
from haute._logging import get_logger
from haute._output_assembler import OutputMappingSchemaError, validate_v2_output_mapping
from haute.errors import ConfigError, ContractMismatchError
from haute.executor import execute_graph
from haute.graph_utils import NodeType, flatten_graph
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.routes._timeouts import (
    BlockingWorkTimeoutError,
    run_blocking_with_response_timeout,
)
from haute.routes.pipeline import _validate_runtime_input_paths
from haute.schemas import Graph

logger = get_logger(component="server.output_assemble")

router = APIRouter(prefix="/api/output-assemble", tags=["output-assemble"])


# Timeout (seconds) — resolved per request so env overrides set after
# import take effect.
def _dry_run_timeout() -> float:
    return float_env("HAUTE_OUTPUT_DRY_RUN_TIMEOUT", 120.0)


class OutputAssembleDryRunRequest(BaseModel):
    graph: Graph
    node_id: str
    #: The in-progress (volatile, unsaved) outputMapping to preview.
    output_mapping: list[dict[str, Any]] = Field(default_factory=list)
    output_format: str = "json"
    #: Cap upstream source rows so the preview stays cheap.
    row_limit: int = Field(default=100, ge=1, le=10000)
    source: str = "live"


class OutputAssembleDryRunResponse(BaseModel):
    status: str
    #: The assembled response document (already pruned by the render path).
    document: list[Any] = Field(default_factory=list)
    row_count: int = 0
    error: str | None = None


@router.post("/dry-run", response_model=OutputAssembleDryRunResponse)
async def output_assemble_dry_run(
    body: OutputAssembleDryRunRequest,
) -> OutputAssembleDryRunResponse:
    """Assemble an OUTPUT node's document from an unsaved ``outputMapping``."""
    # 1. Mapping schema validation — fires on the mapping alone, before any
    #    data is touched (A4). The loudest, cheapest failure.
    try:
        validate_v2_output_mapping(body.output_mapping)
    except OutputMappingSchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    graph = flatten_graph(body.graph)
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

    # 3. Run up to the OUTPUT node. The render points prune the assembled frame,
    #    so ``preview`` is the response document.
    context = create_admitted_execution_context(
        operation="output_assemble_dry_run",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )

    def _run() -> dict[str, Any]:
        return execute_graph(
            graph,
            target_node_id=body.node_id,
            row_limit=body.row_limit,
            source=body.source,
            target_preview_only=True,
            execution_context=context,
        )

    try:
        results = await run_blocking_with_response_timeout(
            _run,
            timeout=_dry_run_timeout(),
            operation="output_assemble_dry_run",
        )
    except BlockingWorkTimeoutError as exc:
        raise HTTPException(status_code=504, detail="Output dry-run timed out") from exc
    except OutputMappingSchemaError as exc:
        # A port the mapping names is not wired into the OUTPUT node, etc.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ConfigError, ContractMismatchError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExecutionAdmissionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("output_assemble_dry_run failed")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None

    node_result = results.get(body.node_id)
    if node_result is None or node_result.status != "ok":
        detail = (node_result.error if node_result else None) or "Assembly failed"
        return OutputAssembleDryRunResponse(status="error", error=detail)

    return OutputAssembleDryRunResponse(
        status="ok",
        document=node_result.preview,
        row_count=node_result.row_count,
    )
