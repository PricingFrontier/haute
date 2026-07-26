"""Submodel create, get, and dissolve endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._logging import get_logger
from haute._submodel_paths import (
    MalformedSubmodelPathError,
    SubmodelPathOutsideProjectError,
    resolve_submodel_by_name,
    resolve_submodel_reference,
    validate_submodel_name,
)
from haute._types import PipelineGraph
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    discover_pipelines,
    load_sidecar_positions,
    pipeline_dir,
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


def _apply_sidecar_positions(graph: PipelineGraph, source_path: Path) -> PipelineGraph:
    """Merge the submodel sidecar's canvas positions into a parsed graph."""
    positions = load_sidecar_positions(source_path)
    if not any(node.id in positions for node in graph.nodes):
        return graph
    updated_nodes = [
        node.model_copy(update={"position": positions[node.id]}) if node.id in positions else node
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": updated_nodes})


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
        # Reject reserved device names at creation, before the graph is
        # transformed: a submodel named ``NUL`` would mint ``modules/NUL.py``,
        # which names a device, not a file, on Windows (any casing, any
        # extension). Enforced on every platform — mirroring the save
        # pipeline's casefold and reserved-name guards — so a pipeline saved
        # on Linux/macOS stays loadable on a Windows checkout. The save-time
        # allowlist (``_validate_output_rel_path``) backstops this check.
        from haute._config_io import is_windows_reserved_filename
        from haute.graph_utils import _sanitize_func_name

        sm_filename = f"{_sanitize_func_name(body.name)}.py"
        if is_windows_reserved_filename(sm_filename):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Submodel name {body.name!r} would create module file "
                    f"'modules/{sm_filename}', whose name is a reserved "
                    "device name on Windows (CON, PRN, AUX, NUL, COM1-COM9, "
                    "LPT1-LPT9 — any casing, any extension). Windows treats "
                    "it as a device, not a file, so it cannot be written or "
                    "checked out there. Choose a different submodel name."
                ),
            )

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
    from haute.parser import parse_pipeline_file, parse_submodel_file

    project_root = Path.cwd()
    try:
        validate_submodel_name(name)
        resolved: tuple[Path, Path] | None = None
        for pipeline_path in discover_pipelines():
            try:
                parent_graph = parse_pipeline_file(pipeline_path)
            except Exception as exc:
                logger.warning(
                    "submodel_parent_parse_failed",
                    file=pipeline_path.name,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                continue
            sm_meta = (parent_graph.submodels or {}).get(name)
            if sm_meta is None:
                continue
            recorded_path = sm_meta.get("file")
            if not isinstance(recorded_path, str) or not recorded_path:
                raise MalformedSubmodelPathError(
                    "Active pipeline has no valid path for the requested submodel.",
                )
            resolved = resolve_submodel_reference(
                recorded_path,
                pipeline_dir=pipeline_path.resolve().parent,
                project_root=project_root,
            )
            break
        if resolved is None:
            resolved = resolve_submodel_by_name(
                name,
                pipeline_dir=pipeline_dir(),
                project_root=project_root,
            )
        sm_path, config_base = resolved
    except MalformedSubmodelPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except SubmodelPathOutsideProjectError:
        raise HTTPException(status_code=403, detail="Cannot access paths outside the project root")
    if not sm_path.is_file():
        raise HTTPException(status_code=404, detail=f"Submodel '{name}' not found")

    sm_graph = parse_submodel_file(sm_path, _base_dir=config_base)
    sm_graph = _apply_sidecar_positions(sm_graph, sm_path)

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
        from haute.parser import parse_submodel_file

        graph = body.graph
        sm_name = body.submodel_name
        submodels = graph.submodels or {}

        if sm_name not in submodels:
            raise HTTPException(
                status_code=404,
                detail=f"Submodel '{sm_name}' not found in graph",
            )
        if not body.source_file:
            raise HTTPException(
                status_code=400,
                detail="source_file is required — the frontend must track"
                " and send the original pipeline file path",
            )

        # Resolve and parse the recorded file before trusting client metadata.
        sm_meta = dict(submodels[sm_name])
        sm_file = sm_meta.get("file", "")
        if not isinstance(sm_file, str) or not sm_file:
            raise HTTPException(
                status_code=400,
                detail="Submodel metadata has no valid source file path",
            )

        cwd = Path.cwd()
        try:
            sm_path, config_base = resolve_submodel_reference(
                sm_file,
                pipeline_dir=pipeline_dir(),
                project_root=cwd,
            )
        except MalformedSubmodelPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except SubmodelPathOutsideProjectError:
            raise HTTPException(
                status_code=403,
                detail="Cannot access paths outside the project root",
            ) from None
        if not sm_path.is_file():
            raise HTTPException(status_code=404, detail=f"Submodel '{sm_name}' not found")

        disk_graph = parse_submodel_file(sm_path, _base_dir=config_base)
        disk_graph = _apply_sidecar_positions(disk_graph, sm_path)
        sm_meta["graph"] = disk_graph.model_dump()
        authoritative_submodels = dict(submodels)
        authoritative_submodels[sm_name] = sm_meta
        authoritative_graph = graph.model_copy(
            update={
                "submodels": authoritative_submodels,
                "preamble": body.preamble,
            }
        )

        # Flatten just the target submodel from its authoritative disk graph.
        flat = flatten_graph(authoritative_graph, target_name=sm_name)
        from haute.routes._save_pipeline import SavePipelineService

        svc = SavePipelineService(project_root=cwd, pipeline_root=pipeline_dir())
        svc.save_graph_transactionally(
            graph=flat,
            name=body.pipeline_name,
            description=body.pipeline_description or "",
            preamble=flat.preamble,
            source_file=body.source_file,
            delete_module_files=[sm_file] if sm_file else (),
        )

        return DissolveSubmodelResponse(status="ok", graph=flat)

    async with save_lock:
        return await run_in_threadpool(_run)
