"""HTTP boundary for durable recovery drafts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from haute._cache import canonical_json
from haute._event_bus import default_bus
from haute._logging import get_logger
from haute._pipeline_recovery import _recovery_artifacts, load_pipeline_editor_document
from haute._pipeline_recovery_drafts import (
    apply_draft,
    contracts,
    create_draft,
    discard_draft,
    edit_draft,
    get_draft,
    list_drafts,
    preview_draft,
)
from haute._pipeline_repair import PipelineRepairError
from haute._recovery_schemas import (
    RecoveryConfigContracts,
    RecoveryDraft,
    RecoveryDraftApply,
    RecoveryDraftApplyResponse,
    RecoveryDraftCreate,
    RecoveryDraftList,
    RecoveryDraftPatch,
    RecoveryDraftPreview,
    RecoveryDraftRevision,
)
from haute.discovery import discover_pipelines
from haute.execution import invalidate_dataframe_execution_cache
from haute.routes._helpers import save_lock

router = APIRouter(prefix="/api", tags=["recovery"])
logger = get_logger(component="server.recovery")
_Result = TypeVar("_Result")


def _error(exc: PipelineRepairError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail()})


def _io_error() -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": {
                "code": "repair_artifact_unavailable",
                "message": "A recovery artifact is unavailable; reload and try again.",
            }
        },
    )


async def _call(
    operation: Callable[..., _Result], *args: Any, **kwargs: Any
) -> _Result | JSONResponse:
    try:
        async with save_lock:
            return await run_in_threadpool(operation, Path.cwd().resolve(), *args, **kwargs)
    except PipelineRepairError as exc:
        return _error(exc)
    except OSError:
        logger.warning("recovery_artifact_io_failed")
        return _io_error()


def _current_document_events(
    root: Path, result: RecoveryDraftApplyResponse
) -> list[dict[str, Any]]:
    """Read current affected documents after a committed recovery transaction."""
    applied = set(result.applied_artifacts)
    active = (root / result.draft.source_file).resolve()
    sources = {active}
    for source in discover_pipelines(root):
        artifacts = _recovery_artifacts(source, root)
        if any(path.relative_to(root).as_posix() in applied for _role, path in artifacts):
            sources.add(source.resolve())
    events: list[dict[str, Any]] = []
    for source in sorted(sources):
        document = load_pipeline_editor_document(source, project_root=root)
        payload = document.model_dump(mode="json", by_alias=True)
        events.append(
            {
                "document": payload,
                "document_fingerprint": hashlib.sha256(
                    canonical_json(payload).encode("utf-8")
                ).hexdigest(),
                "source_file": source.relative_to(root).as_posix(),
            }
        )
    return events


async def _apply_and_notify(
    draft_id: str, body: RecoveryDraftApply, *, restore: bool
) -> RecoveryDraftApplyResponse | JSONResponse:
    events: list[dict[str, Any]] = []
    try:
        async with save_lock:
            root = Path.cwd().resolve()
            result = await run_in_threadpool(apply_draft, root, draft_id, body, restore=restore)
            try:
                await run_in_threadpool(invalidate_dataframe_execution_cache)
            except Exception as exc:
                logger.warning("recovery_cache_invalidation_failed", code=type(exc).__name__)
            try:
                events = await run_in_threadpool(_current_document_events, root, result)
            except Exception as exc:
                logger.warning("recovery_notification_prepare_failed", code=type(exc).__name__)
    except PipelineRepairError as exc:
        return _error(exc)
    except OSError:
        logger.warning("recovery_artifact_io_failed")
        return _io_error()
    for event in events:
        default_bus.publish("pipeline.document.update", event)
    return result


@router.get("/pipeline/repair/contracts", response_model=RecoveryConfigContracts)
async def get_contracts() -> RecoveryConfigContracts:
    async with save_lock:
        return await run_in_threadpool(contracts)


@router.get("/pipeline/repair/drafts", response_model=RecoveryDraftList)
async def get_drafts(
    source_file: str = Query(..., min_length=1),
) -> RecoveryDraftList | JSONResponse:
    return await _call(list_drafts, source_file)


@router.post("/pipeline/repair/drafts", response_model=RecoveryDraft)
async def post_draft(body: RecoveryDraftCreate) -> RecoveryDraft | JSONResponse:
    return await _call(create_draft, body)


@router.get("/pipeline/repair/drafts/{draft_id}", response_model=RecoveryDraft)
async def get_draft_route(draft_id: str) -> RecoveryDraft | JSONResponse:
    return await _call(get_draft, draft_id)


@router.post("/pipeline/repair/drafts/{draft_id}/edit", response_model=RecoveryDraft)
async def edit_draft_route(draft_id: str, body: RecoveryDraftPatch) -> RecoveryDraft | JSONResponse:
    return await _call(edit_draft, draft_id, body)


@router.post("/pipeline/repair/drafts/{draft_id}/preview", response_model=RecoveryDraftPreview)
async def preview_draft_route(
    draft_id: str, body: RecoveryDraftRevision
) -> RecoveryDraftPreview | JSONResponse:
    return await _call(preview_draft, draft_id, body.draft_revision)


@router.post("/pipeline/repair/drafts/{draft_id}/apply", response_model=RecoveryDraftApplyResponse)
async def apply_draft_route(
    draft_id: str, body: RecoveryDraftApply
) -> RecoveryDraftApplyResponse | JSONResponse:
    return await _apply_and_notify(draft_id, body, restore=False)


@router.post("/pipeline/repair/drafts/{draft_id}/discard", response_model=RecoveryDraft)
async def discard_draft_route(
    draft_id: str, body: RecoveryDraftRevision
) -> RecoveryDraft | JSONResponse:
    return await _call(discard_draft, draft_id, body.draft_revision)


@router.post(
    "/pipeline/repair/drafts/{draft_id}/restore-preview", response_model=RecoveryDraftPreview
)
async def restore_preview_route(
    draft_id: str, body: RecoveryDraftRevision
) -> RecoveryDraftPreview | JSONResponse:
    return await _call(preview_draft, draft_id, body.draft_revision, restore=True)


@router.post(
    "/pipeline/repair/drafts/{draft_id}/restore", response_model=RecoveryDraftApplyResponse
)
async def restore_draft_route(
    draft_id: str, body: RecoveryDraftApply
) -> RecoveryDraftApplyResponse | JSONResponse:
    return await _apply_and_notify(draft_id, body, restore=True)
