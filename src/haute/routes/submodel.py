"""Submodel create, get, and dissolve endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._file_ops import Writer
from haute._logging import get_logger
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
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


def _write_self_marked_text(path: Path, content: str) -> None:
    with Writer(path, mark_self_write=mark_self_write) as writer:
        writer.write_text(content)


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
    from haute.codegen import graph_to_code_multi
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

        # Validate source_file stays within project root
        cwd = Path.cwd()
        py_path = validate_safe_path(cwd, body.source_file)

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
            _write_self_marked_text(out_path, code)

        # Also materialise the per-node config sidecar JSON files so a
        # subsequent reparse of the submodel .py file can resolve
        # @pipeline.<type>(config="...") references.  Without this the
        # parser raises ConfigError.
        # Walk both the parent graph and every nested submodel graph so
        # child-node configs are written alongside their parent's.
        from haute._config_io import collect_node_configs
        from haute._types import PipelineGraph

        configs: dict[str, str] = dict(collect_node_configs(result.graph))
        for sm_meta in (result.graph.submodels or {}).values():
            sm_graph_dict = sm_meta.get("graph", {})
            nested = PipelineGraph.model_validate(
                {"nodes": sm_graph_dict.get("nodes", []), "edges": []}
            )
            configs.update(collect_node_configs(nested))
        for rel_path, json_content in configs.items():
            cfg_path = validate_safe_path(cwd, rel_path)
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            _write_self_marked_text(cfg_path, json_content)

        # Save sidecar
        save_sidecar(py_path, result.graph)

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

    cwd = Path.cwd()
    sm_path = validate_safe_path(cwd / "modules", f"{name}.py")
    if not sm_path.is_file():
        raise HTTPException(status_code=404, detail=f"Submodel '{name}' not found")

    # Config sidecar files live at project-root ``config/<type>/<name>.json``
    # not inside ``modules/``, so pass cwd (not the default sm_path.parent)
    # so the parser resolves them correctly.
    sm_graph = parse_submodel_file(sm_path, _base_dir=cwd)

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

        # Write the updated main file
        from haute.codegen import graph_to_code

        cwd = Path.cwd()
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
        _write_self_marked_text(py_path, code)
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

    async with save_lock:
        return await run_in_threadpool(_run)
