"""Submodel create, get, and dissolve endpoints."""

from __future__ import annotations

from os.path import normcase
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from haute._logging import get_logger
from haute._submodel_paths import (
    MalformedSubmodelPathError,
    SubmodelPathOutsideProjectError,
    resolve_submodel_reference,
    validate_submodel_name,
)
from haute._types import PipelineGraph
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    discover_pipelines,
    load_sidecar,
    load_sidecar_positions,
    parse_pipeline_to_graph,
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


def _require_parent_source_file(source_file: str) -> None:
    if not source_file.strip():
        raise HTTPException(
            status_code=400,
            detail="source_file is required to identify the parent pipeline.",
        )


def _load_parent_document(source_file: str) -> tuple[Path, Path, PipelineGraph]:
    _require_parent_source_file(source_file)
    project_root = Path.cwd().resolve()
    parent_path = validate_safe_path(project_root, source_file)
    if not parent_path.is_file():
        raise HTTPException(status_code=404, detail="Parent pipeline file not found.")
    return (
        project_root,
        parent_path,
        parse_pipeline_to_graph(parent_path, project_root=project_root),
    )


def _require_current_revision(graph: PipelineGraph, base_revision: str) -> None:
    if graph.source_revision != base_revision:
        raise HTTPException(
            status_code=409,
            detail="The pipeline changed on disk. Reload it before editing submodels.",
        )


def _resolve_recorded_child(
    *,
    recorded_path: object,
    parent_path: Path,
    project_root: Path,
) -> tuple[str, Path, Path]:
    if not isinstance(recorded_path, str) or not recorded_path:
        raise HTTPException(
            status_code=400,
            detail="Submodel metadata has no valid source file path.",
        )
    try:
        child_path, config_base = resolve_submodel_reference(
            recorded_path,
            pipeline_dir=parent_path.parent,
            project_root=project_root,
        )
    except MalformedSubmodelPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except SubmodelPathOutsideProjectError:
        raise HTTPException(
            status_code=403,
            detail="Cannot access paths outside the project root",
        ) from None
    return recorded_path, child_path, config_base


def _can_delete_managed_child(
    *,
    child_path: Path,
    parent_path: Path,
    project_root: Path,
) -> bool:
    parent_relative = parent_path.relative_to(project_root).as_posix()
    if load_sidecar(child_path).get("managed_parent") != parent_relative:
        return False

    try:
        candidates = discover_pipelines()
    except Exception:
        logger.warning("submodel_reference_audit_failed", exc_info=True)
        return False

    parent_key = normcase(str(parent_path.resolve()))
    child_key = normcase(str(child_path.resolve()))
    for candidate in candidates:
        if normcase(str(candidate.resolve())) == parent_key:
            continue
        try:
            sibling = parse_pipeline_to_graph(candidate, project_root=project_root)
            for metadata in (sibling.submodels or {}).values():
                recorded = metadata.get("file") if isinstance(metadata, dict) else None
                if not isinstance(recorded, str) or not recorded:
                    return False
                sibling_child, _base = resolve_submodel_reference(
                    recorded,
                    pipeline_dir=candidate.resolve().parent,
                    project_root=project_root,
                )
                if normcase(str(sibling_child.resolve())) == child_key:
                    return False
        except Exception:
            logger.warning(
                "submodel_reference_audit_incomplete",
                file=str(candidate),
                exc_info=True,
            )
            return False
    return True


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
    from haute.routes._submodel_ops import (
        SubmodelValidationError,
        create_submodel_graph,
    )

    def _run() -> CreateSubmodelResponse:
        _require_parent_source_file(body.source_file)
        project_root, _parent_path, current_graph = _load_parent_document(body.source_file)
        _require_current_revision(current_graph, body.base_revision)

        # Reject reserved device names at creation, before the graph is
        # transformed: a submodel named ``NUL`` would mint ``modules/NUL.py``,
        # which names a device, not a file, on Windows (any casing, any
        # extension). Enforced on every platform — mirroring the save
        # pipeline's casefold and reserved-name guards — so a pipeline saved
        # on Linux/macOS stays loadable on a Windows checkout. The save-time
        # allowlist (``_validate_output_rel_path``) backstops this check.
        from haute._config_io import is_windows_reserved_filename
        from haute.graph_utils import _sanitize_func_name

        sm_filename = f"{_sanitize_func_name(body.name.strip())}.py"
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
            submitted_graph = body.graph.model_copy(
                update={
                    "preamble": body.preamble,
                    "preserved_blocks": list(body.preserved_blocks),
                }
            )
            result = create_submodel_graph(submitted_graph, body.node_ids, body.name)
        except SubmodelValidationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
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

        svc = SavePipelineService(project_root=project_root, pipeline_root=pipeline_dir())
        saved = svc.save_graph_transactionally(
            graph=result.graph,
            name=body.pipeline_name,
            description=body.pipeline_description or "",
            preamble=body.preamble,
            source_file=body.source_file,
            require_absent_module_files=[result.sm_file],
            claim_managed_module_files=[result.sm_file],
        )
        response_graph = result.graph.model_copy(update={"source_revision": saved.source_revision})

        return CreateSubmodelResponse(
            status="ok",
            submodel_file=result.sm_file,
            parent_file=body.source_file,
            source_revision=saved.source_revision,
            graph=response_graph,
        )

    async with save_lock:
        return await run_in_threadpool(_run)


@router.get("/{name}", response_model=SubmodelGraphResponse)
async def get_submodel(
    name: str,
    source_file: str = Query(default=""),
) -> SubmodelGraphResponse:
    """Return the internal graph of a submodel for drill-down view."""
    from haute.routes._helpers import save_lock

    async with save_lock:
        return await run_in_threadpool(_get_submodel_blocking, name, source_file)


def _get_submodel_blocking(name: str, source_file: str) -> SubmodelGraphResponse:
    from haute.parser import parse_submodel_file

    try:
        validate_submodel_name(name)
    except MalformedSubmodelPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    project_root, parent_path, parent_graph = _load_parent_document(source_file)
    metadata = (parent_graph.submodels or {}).get(name)
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=404, detail=f"Submodel '{name}' not found")
    recorded_path, sm_path, config_base = _resolve_recorded_child(
        recorded_path=metadata.get("file"),
        parent_path=parent_path,
        project_root=project_root,
    )
    if not sm_path.is_file():
        raise HTTPException(status_code=404, detail=f"Submodel '{name}' not found")

    sm_graph = parse_submodel_file(sm_path, _base_dir=config_base)
    sm_graph = _apply_sidecar_positions(sm_graph, sm_path)

    return SubmodelGraphResponse(
        status="ok",
        submodel_name=sm_graph.pipeline_name or name,
        submodel_file=recorded_path,
        graph=sm_graph,
    )


@router.post("/dissolve", response_model=DissolveSubmodelResponse)
async def dissolve_submodel(body: DissolveSubmodelRequest) -> DissolveSubmodelResponse:
    """Ungroup a submodel back into the parent pipeline.

    Inlines the submodel's nodes into the parent graph. The child source and
    sidecar are deleted only when persisted ownership and the reference audit
    both authorise deletion.

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

        _require_parent_source_file(body.source_file)
        project_root, parent_path, current_graph = _load_parent_document(body.source_file)
        _require_current_revision(current_graph, body.base_revision)

        graph = body.graph
        sm_name = body.submodel_name
        submodels = graph.submodels or {}
        disk_submodels = current_graph.submodels or {}

        if sm_name not in submodels or sm_name not in disk_submodels:
            raise HTTPException(
                status_code=404,
                detail=f"Submodel '{sm_name}' not found in graph",
            )
        submitted_raw = submodels[sm_name]
        disk_raw = disk_submodels[sm_name]
        if not isinstance(submitted_raw, dict) or not isinstance(disk_raw, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Submodel {sm_name!r} has invalid metadata.",
            )
        submitted_meta = dict(submitted_raw)
        disk_meta = dict(disk_raw)
        sm_file, sm_path, config_base = _resolve_recorded_child(
            recorded_path=disk_meta.get("file"),
            parent_path=parent_path,
            project_root=project_root,
        )
        if not sm_path.is_file():
            raise HTTPException(status_code=404, detail=f"Submodel '{sm_name}' not found")

        disk_graph = parse_submodel_file(sm_path, _base_dir=config_base)
        disk_graph = _apply_sidecar_positions(disk_graph, sm_path)
        submitted_meta["file"] = sm_file
        submitted_meta["managed"] = disk_meta.get("managed") is True
        submitted_meta["graph"] = disk_graph.model_dump()
        authoritative_submodels = dict(submodels)
        authoritative_submodels[sm_name] = submitted_meta
        authoritative_graph = graph.model_copy(
            update={
                "submodels": authoritative_submodels,
                "preamble": body.preamble,
                "preserved_blocks": list(body.preserved_blocks),
            }
        )

        # Flatten just the target submodel from its authoritative disk graph.
        flat = flatten_graph(authoritative_graph, target_name=sm_name)
        delete_child = _can_delete_managed_child(
            child_path=sm_path,
            parent_path=parent_path,
            project_root=project_root,
        )
        from haute.routes._save_pipeline import SavePipelineService

        svc = SavePipelineService(project_root=project_root, pipeline_root=pipeline_dir())
        saved = svc.save_graph_transactionally(
            graph=flat,
            name=body.pipeline_name,
            description=body.pipeline_description or "",
            preamble=flat.preamble,
            source_file=body.source_file,
            delete_module_files=[sm_file] if delete_child else (),
        )
        response_graph = flat.model_copy(update={"source_revision": saved.source_revision})

        return DissolveSubmodelResponse(
            status="ok",
            graph=response_graph,
            source_revision=saved.source_revision,
            submodel_file_deleted=delete_child,
            retained_submodel_file=None if delete_child else sm_file,
        )

    async with save_lock:
        return await run_in_threadpool(_run)
