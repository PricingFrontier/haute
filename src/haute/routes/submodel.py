"""Submodel create, get, and dissolve endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from haute._logging import get_logger
from haute._submodel_paths import (
    MalformedSubmodelPathError,
    SubmodelPathOutsideProjectError,
    resolve_submodel_reference,
    validate_submodel_definition_id,
)
from haute._types import NodeType, PipelineGraph, SubmodelInstanceConfig
from haute.errors import ConfigError, ParseError
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    load_pipeline_editor_document,
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
    editor_document = load_pipeline_editor_document(
        parent_path,
        project_root=project_root,
    )
    if editor_document.load_status != "ready":
        raise HTTPException(
            status_code=409,
            detail=(
                "The parent pipeline is not ready on disk. Reload it and resolve "
                "its diagnostics before changing submodels."
            ),
        )
    # The recovery revision deliberately hashes raw malformed artifacts for
    # recovery-preview/repair admission.  Submodel operations retain their
    # existing canonical graph revision contract; this guard only adds the
    # independent server-side disk-readiness fence.
    graph = parse_pipeline_to_graph(parent_path, project_root=project_root)
    return project_root, parent_path, graph


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
    """Group selected nodes in memory and return the transformed graph.

    The route validates the current persisted revision and performs a
    read-only no-clobber check. Only the explicit pipeline Save action writes
    the parent, child module, config files, or sidecars.
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
        try:
            svc.validate_new_module_files([result.sm_file])
        except ConfigError as exc:
            logger.warning(
                "submodel_create_config_invalid",
                name=body.name,
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc)) from None

        source_revision = current_graph.source_revision
        if source_revision is None:
            raise RuntimeError("Current pipeline did not produce a source revision.")
        response_graph = result.graph.model_copy(update={"source_revision": source_revision})

        return CreateSubmodelResponse(
            status="ok",
            submodel_file=result.sm_file,
            parent_file=body.source_file,
            source_revision=source_revision,
            graph=response_graph,
        )

    async with save_lock:
        return await run_in_threadpool(_run)


@router.get("/{definition_id}", response_model=SubmodelGraphResponse)
async def get_submodel(
    definition_id: str,
    source_file: str = Query(default=""),
) -> SubmodelGraphResponse:
    """Return one definition graph for drill-down view."""
    from haute.routes._helpers import save_lock

    async with save_lock:
        return await run_in_threadpool(
            _get_submodel_blocking,
            definition_id,
            source_file,
        )


def _get_submodel_blocking(
    definition_id: str,
    source_file: str,
) -> SubmodelGraphResponse:
    from haute.parser import parse_submodel_file

    try:
        validate_submodel_definition_id(definition_id)
    except MalformedSubmodelPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    project_root, parent_path, parent_graph = _load_parent_document(source_file)
    metadata = (parent_graph.submodels or {}).get(definition_id)
    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail=f"Submodel definition {definition_id!r} not found",
        )
    recorded_path, sm_path, config_base = _resolve_recorded_child(
        recorded_path=metadata.file,
        parent_path=parent_path,
        project_root=project_root,
    )
    if not sm_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Submodel definition {definition_id!r} not found",
        )

    sm_graph = parse_submodel_file(sm_path, _base_dir=config_base)
    sm_graph = _apply_sidecar_positions(sm_graph, sm_path)
    if sm_graph._parser_definition_id != definition_id:
        raise HTTPException(
            status_code=409,
            detail="The submodel definition identity changed on disk. Reload the pipeline.",
        )

    return SubmodelGraphResponse(
        status="ok",
        submodel_name=sm_graph.pipeline_name or definition_id,
        submodel_file=recorded_path,
        graph=sm_graph,
        definition_id=definition_id,
    )


def _definition_id_for_instance(
    graph: PipelineGraph,
    instance_id: str,
) -> str:
    node = graph.node_map.get(instance_id)
    if node is None or node.data.nodeType != NodeType.SUBMODEL:
        raise HTTPException(
            status_code=404,
            detail=f"Submodel instance {instance_id!r} not found in graph.",
        )
    try:
        config = SubmodelInstanceConfig.model_validate(node.data.config)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Submodel instance {instance_id!r} has invalid identity metadata.",
        ) from None
    return config.definition_id


@router.post("/dissolve", response_model=DissolveSubmodelResponse)
async def dissolve_submodel(body: DissolveSubmodelRequest) -> DissolveSubmodelResponse:
    """Ungroup one occurrence in memory and return the transformed graph.

    The submitted canonical definition is authoritative for this editor
    transform, including definitions created since the last Save. Child-file
    deletion is derived later by the explicit Save transaction.
    """
    from haute.graph_utils import flatten_graph
    from haute.routes._helpers import save_lock

    def _run() -> DissolveSubmodelResponse:
        _require_parent_source_file(body.source_file)
        project_root, parent_path, current_graph = _load_parent_document(body.source_file)
        _require_current_revision(current_graph, body.base_revision)

        graph = body.graph.model_copy(
            update={
                "preamble": body.preamble,
                "preserved_blocks": list(body.preserved_blocks),
            }
        )
        submodels = graph.submodels or {}
        target_instance_id = body.instance_id
        definition_id = _definition_id_for_instance(graph, target_instance_id)
        definition = submodels.get(definition_id)
        if definition is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Submodel instance {target_instance_id!r} references missing "
                    f"definition {definition_id!r}."
                ),
            )
        _resolve_recorded_child(
            recorded_path=definition.file,
            parent_path=parent_path,
            project_root=project_root,
        )
        try:
            flat = flatten_graph(graph, target_instance_id=target_instance_id)
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from None

        source_revision = current_graph.source_revision
        if source_revision is None:
            raise RuntimeError("Current pipeline did not produce a source revision.")
        response_graph = flat.model_copy(update={"source_revision": source_revision})

        return DissolveSubmodelResponse(
            status="ok",
            instance_id=target_instance_id,
            definition_id=definition_id,
            graph=response_graph,
            source_revision=source_revision,
        )

    async with save_lock:
        return await run_in_threadpool(_run)
