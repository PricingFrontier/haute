"""Submodel create, get, and dissolve endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._logging import get_logger
from haute._submodel_paths import resolve_submodel_by_name
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    load_sidecar_positions,
    pipeline_dir,
    validate_safe_path,
)
from haute.schemas import (
    CreateSubmodelRequest,
    CreateSubmodelResponse,
    DissolveSubmodelRequest,
    DissolveSubmodelResponse,
    SubmodelGraphResponse,
)

logger = get_logger(component="server.submodel")

router = APIRouter(prefix="/api/submodel", tags=["submodel"])


@router.post("/create", response_model=CreateSubmodelResponse)
async def create_submodel(body: CreateSubmodelRequest) -> CreateSubmodelResponse:
    """Group selected nodes into a submodel.

    Creates a new ``modules/<name>.py`` file, updates the main pipeline file,
    and returns the updated parent graph with the submodel node.

    Bundle 5.M1: the implementation is wrapped in the shared
    ``save_lock`` so this operation (which writes the parent .py, the
    submodel .py, multiple config sidecars, and the .haute.json) is
    serialised against any concurrent ``/api/pipeline/save`` or
    ``/api/submodel/dissolve``. See ``routes/_helpers.py::save_lock``
    for the full rationale and scope.
    """
    from haute.routes._helpers import save_lock
    from haute.routes._save_pipeline import SavePipelineService
    from haute.routes._submodel_ops import create_submodel_graph

    def _run() -> CreateSubmodelResponse:
        SavePipelineService._validate_unique_sanitized_names(body.graph)

        try:
            result = create_submodel_graph(body.graph, body.node_ids, body.name)
        except ValueError:
            # The ValueError message may embed graph walk details, absolute
            # paths, or internal identifiers — all unsafe for the HTTP body.
            # Log full detail server-side, emit a sanitized 400 to the client.
            logger.warning(
                "submodel_create_invalid",
                name=body.name,
                node_count=len(body.node_ids or []),
                exc_info=True,
            )
            raise HTTPException(status_code=400, detail=_INTERNAL_ERROR_DETAIL) from None

        if not body.source_file:
            raise HTTPException(
                status_code=400,
                detail="source_file is required — the frontend must track"
                " and send the original pipeline file path",
            )

        svc = SavePipelineService(project_root=Path.cwd(), pipeline_root=pipeline_dir())
        svc.save_graph_transactionally(
            graph=result.graph,
            name=body.pipeline_name,
            description=body.pipeline_description or "",
            preamble=body.preamble,
            source_file=body.source_file,
        )

        return CreateSubmodelResponse(
            status="ok",
            submodel_file=result.sm_file,
            parent_file=body.source_file,
            graph=result.graph,
        )

    async with save_lock:
        return await run_in_threadpool(_run)


@router.get("/{name}", response_model=SubmodelGraphResponse)
async def get_submodel(name: str) -> SubmodelGraphResponse:
    """Return the internal graph of a submodel for drill-down view."""
    from haute.routes._helpers import save_lock

    async with save_lock:
        return await run_in_threadpool(_get_submodel_blocking, name)


def _get_submodel_blocking(name: str) -> SubmodelGraphResponse:
    from haute.parser import parse_submodel_file

    project_root = Path.cwd()
    active_pipeline_dir = pipeline_dir()
    sm_path, config_base = resolve_submodel_by_name(
        name,
        pipeline_dir=active_pipeline_dir,
        project_root=project_root,
    )
    project_root = project_root.resolve()
    if not sm_path.is_relative_to(project_root):
        raise HTTPException(status_code=403, detail="Cannot access paths outside the project root")
    if not sm_path.is_file():
        raise HTTPException(status_code=404, detail=f"Submodel '{name}' not found")

    sm_graph = parse_submodel_file(sm_path, _base_dir=config_base)

    # Load sidecar positions if available
    positions = load_sidecar_positions(sm_path)
    updated_nodes = []
    for node in sm_graph.nodes:
        if node.id in positions:
            node = node.model_copy(update={"position": positions[node.id]})
        updated_nodes.append(node)
    if updated_nodes:
        sm_graph = sm_graph.model_copy(update={"nodes": updated_nodes})

    return SubmodelGraphResponse(
        status="ok",
        submodel_name=sm_graph.pipeline_name or name,
        graph=sm_graph,
    )


@router.post("/dissolve", response_model=DissolveSubmodelResponse)
async def dissolve_submodel(body: DissolveSubmodelRequest) -> DissolveSubmodelResponse:
    """Ungroup a submodel back into the parent pipeline.

    Inlines the submodel's nodes into the parent graph and deletes
    the submodel .py file.

    Bundle 5.M1: acquires the shared ``save_lock`` so this operation
    (which writes the main .py + .haute.json and deletes the submodel
    .py) is serialised against any concurrent ``/api/pipeline/save`` or
    ``/api/submodel/create``. See ``routes/_helpers.py::save_lock`` for
    the full rationale and scope.
    """
    from haute.graph_utils import flatten_graph
    from haute.routes._helpers import save_lock

    def _run() -> DissolveSubmodelResponse:
        graph = body.graph
        sm_name = body.submodel_name
        submodels = graph.submodels or {}

        if sm_name not in submodels:
            raise HTTPException(
                status_code=404,
                detail=f"Submodel '{sm_name}' not found in graph",
            )

        # Flatten just the target submodel
        sm_meta = submodels[sm_name]
        sm_file = sm_meta.get("file", "")

        # Remove the submodel from the graph metadata and flatten
        flat = flatten_graph(graph, target_name=sm_name)

        # Validate name uniqueness on the flattened graph (post-inline)
        from haute.routes._save_pipeline import SavePipelineService

        SavePipelineService._validate_unique_sanitized_names(flat)

        cwd = Path.cwd()
        if not body.source_file:
            raise HTTPException(
                status_code=400,
                detail="source_file is required — the frontend must track"
                " and send the original pipeline file path",
            )
        svc = SavePipelineService(project_root=cwd, pipeline_root=pipeline_dir())
        svc.save_graph_transactionally(
            graph=flat,
            name=body.pipeline_name,
            description=body.pipeline_description or "",
            preamble=body.preamble,
            source_file=body.source_file,
            delete_module_files=[sm_file] if sm_file else (),
        )

        return DissolveSubmodelResponse(status="ok", graph=flat)

    async with save_lock:
        return await run_in_threadpool(_run)
