"""Submodel create, get, and dissolve endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from haute._logging import get_logger
from haute.routes._helpers import (
    load_sidecar_positions,
    mark_self_write,
    save_sidecar,
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
    """
    from haute.codegen import graph_to_code_multi
    from haute.routes._save_pipeline import SavePipelineService
    from haute.routes._submodel_ops import create_submodel_graph

    SavePipelineService._validate_unique_sanitized_names(body.graph)

    try:
        result = create_submodel_graph(body.graph, body.node_ids, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not body.source_file:
        raise HTTPException(
            status_code=400,
            detail="source_file is required — the frontend must track"
            " and send the original pipeline file path",
        )

    # Validate source_file stays within project root
    cwd = Path.cwd()
    py_path = validate_safe_path(cwd, body.source_file)

    # Write files to disk
    mark_self_write()
    files = graph_to_code_multi(
        result.graph,
        pipeline_name=body.pipeline_name,
        description=body.pipeline_description or "",
        preamble=body.preamble,
        source_file=body.source_file,
    )
    for rel_path, code in files.items():
        out_path = validate_safe_path(cwd, rel_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(code)

    # Save sidecar
    save_sidecar(py_path, result.graph)

    return CreateSubmodelResponse(
        status="ok",
        submodel_file=result.sm_file,
        parent_file=body.source_file,
        graph=result.graph,
    )


@router.get("/{name}", response_model=SubmodelGraphResponse)
async def get_submodel(name: str) -> SubmodelGraphResponse:
    """Return the internal graph of a submodel for drill-down view."""
    from haute.parser import parse_submodel_file

    cwd = Path.cwd()
    sm_path = validate_safe_path(cwd / "modules", f"{name}.py")
    if not sm_path.is_file():
        raise HTTPException(status_code=404, detail=f"Submodel '{name}' not found")

    sm_graph = parse_submodel_file(sm_path)

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
    """
    from haute.graph_utils import flatten_graph

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

    # Write the updated main file
    from haute.codegen import graph_to_code

    cwd = Path.cwd()
    mark_self_write()

    if not body.source_file:
        raise HTTPException(
            status_code=400,
            detail="source_file is required — the frontend must track"
            " and send the original pipeline file path",
        )
    py_path = validate_safe_path(cwd, body.source_file)

    code = graph_to_code(
        flat,
        pipeline_name=body.pipeline_name,
        description=body.pipeline_description or "",
        preamble=body.preamble,
    )
    py_path.write_text(code)
    save_sidecar(py_path, flat)

    # Delete the submodel file
    if sm_file:
        try:
            sm_path = validate_safe_path(cwd, sm_file)
        except HTTPException:
            logger.warning("dissolve_skip_delete_traversal", file=sm_file)
            sm_path = None
        if sm_path is not None and sm_path.is_file():
            sm_path.unlink()

    return DissolveSubmodelResponse(status="ok", graph=flat)
