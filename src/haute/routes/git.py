"""Git panel endpoints — branch management, save, revert, and submit.

Provides a simplified git workflow for pricing analysts who don't use
git directly.  All operations go through ``haute._git`` which enforces
guardrails (no writes to protected branches, backup tags before revert).

All handlers are plain ``def`` (not ``async def``) so that FastAPI runs
them in a thread pool, avoiding event-loop blocking on slow git operations.
"""

from __future__ import annotations

import dataclasses
from typing import Any, NoReturn, TypeVar

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from haute._git import (
    GitDomainError,
    GitError,
    GitGuardrailError,
    archive_branch,
    create_branch,
    delete_branch,
    get_history,
    get_status,
    list_branches,
    pull_latest,
    revert_to,
    save_progress,
    submit_for_review,
    switch_branch,
)
from haute._logging import get_logger
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.schemas import (
    GitArchiveRequest,
    GitArchiveResponse,
    GitBranchListResponse,
    GitCreateBranchRequest,
    GitCreateBranchResponse,
    GitDeleteBranchRequest,
    GitHistoryEntry,
    GitHistoryResponse,
    GitPullResponse,
    GitRevertRequest,
    GitRevertResponse,
    GitSaveResponse,
    GitStatusResponse,
    GitSubmitResponse,
    GitSwitchBranchRequest,
)

_M = TypeVar("_M", bound=BaseModel)

logger = get_logger(component="server.git")

router = APIRouter(prefix="/api/git", tags=["git"])


def _handle_git_error(e: GitError) -> NoReturn:
    """Convert git errors to appropriate HTTP responses.

    Three error families are distinguished (item #11):

    * :class:`GitGuardrailError` — hand-written guardrail block
      (protected branch, already-archived branch) → 403 with verbatim
      message.
    * :class:`GitDomainError` — other hand-written user-facing messages
      (missing repo, duplicate branch, no changes to save) → 400 with
      verbatim message.  Safe because we author the text and never
      forward raw subprocess stderr through this class.
    * Plain :class:`GitError` — raw ``git`` subprocess stderr from
      :func:`_run_git`.  Unsafe: may embed absolute paths, remote
      URLs, SSL error text, or credential fragments.  Full detail is
      logged server-side, HTTP body gets the sanitized constant → 400.
    """
    if isinstance(e, GitGuardrailError):
        logger.warning("git_guardrail_error", error=str(e))
        raise HTTPException(status_code=403, detail=str(e))
    if isinstance(e, GitDomainError):
        logger.warning("git_domain_error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    logger.warning("git_error", error=str(e))
    raise HTTPException(status_code=400, detail=_INTERNAL_ERROR_DETAIL)


def _dc_to_pydantic(dc_instance: Any, model: type[_M]) -> _M:
    """Convert a dataclass instance to a Pydantic model via ``model_validate``.

    Handles nested dataclass fields (e.g. lists of dataclasses) by recursively
    converting them with ``dataclasses.asdict``.
    """
    return model.model_validate(dataclasses.asdict(dc_instance))


# ---------------------------------------------------------------------------
# GET /api/git/status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=GitStatusResponse)
def git_status() -> GitStatusResponse:
    """Current branch, changed files, and main-ahead status."""
    try:
        s = get_status()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_status_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return _dc_to_pydantic(s, GitStatusResponse)


# ---------------------------------------------------------------------------
# GET /api/git/branches
# ---------------------------------------------------------------------------


@router.get("/branches", response_model=GitBranchListResponse)
def git_branches() -> GitBranchListResponse:
    """List all branches (user's first, then others, archived last)."""
    try:
        result = list_branches()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_branches_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return _dc_to_pydantic(result, GitBranchListResponse)


# ---------------------------------------------------------------------------
# POST /api/git/branches — create a new branch
# ---------------------------------------------------------------------------


@router.post("/branches", response_model=GitCreateBranchResponse)
def git_create_branch(body: GitCreateBranchRequest) -> GitCreateBranchResponse:
    """Create a new branch from current HEAD."""
    if not body.description.strip():
        raise HTTPException(status_code=400, detail="Branch description cannot be empty.")
    try:
        branch = create_branch(body.description)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_create_branch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return GitCreateBranchResponse(branch=branch)


# ---------------------------------------------------------------------------
# POST /api/git/switch
# ---------------------------------------------------------------------------


@router.post("/switch")
def git_switch(body: GitSwitchBranchRequest) -> dict[str, str]:
    """Switch to a branch (auto-commits pending changes first)."""
    try:
        switch_branch(body.branch)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_switch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return {"status": "ok", "branch": body.branch}


# ---------------------------------------------------------------------------
# POST /api/git/save
# ---------------------------------------------------------------------------


@router.post("/save", response_model=GitSaveResponse)
def git_save() -> GitSaveResponse:
    """Stage, commit, and push all changes."""
    try:
        result = save_progress()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_save_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return _dc_to_pydantic(result, GitSaveResponse)


# ---------------------------------------------------------------------------
# POST /api/git/submit
# ---------------------------------------------------------------------------


@router.post("/submit", response_model=GitSubmitResponse)
def git_submit() -> GitSubmitResponse:
    """Push and return a comparison URL for PR creation."""
    try:
        result = submit_for_review()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_submit_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return _dc_to_pydantic(result, GitSubmitResponse)


# ---------------------------------------------------------------------------
# GET /api/git/history
# ---------------------------------------------------------------------------


@router.get("/history", response_model=GitHistoryResponse)
def git_history(limit: int = Query(20, ge=1, le=500)) -> GitHistoryResponse:
    """Commit history for the current branch."""
    try:
        entries = get_history(limit=limit)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_history_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return GitHistoryResponse(
        entries=[_dc_to_pydantic(e, GitHistoryEntry) for e in entries],
    )


# ---------------------------------------------------------------------------
# POST /api/git/revert
# ---------------------------------------------------------------------------


@router.post("/revert", response_model=GitRevertResponse)
def git_revert(body: GitRevertRequest) -> GitRevertResponse:
    """Reset to a specific commit (creates a backup tag first)."""
    try:
        result = revert_to(body.sha)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_revert_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return _dc_to_pydantic(result, GitRevertResponse)


# ---------------------------------------------------------------------------
# POST /api/git/pull
# ---------------------------------------------------------------------------


@router.post("/pull", response_model=GitPullResponse)
def git_pull() -> GitPullResponse:
    """Pull latest default branch into current branch."""
    try:
        result = pull_latest()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_pull_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return _dc_to_pydantic(result, GitPullResponse)


# ---------------------------------------------------------------------------
# POST /api/git/archive
# ---------------------------------------------------------------------------


@router.post("/archive", response_model=GitArchiveResponse)
def git_archive(body: GitArchiveRequest) -> GitArchiveResponse:
    """Archive a branch (rename to archive/<name>)."""
    try:
        archived_as = archive_branch(body.branch)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_archive_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return GitArchiveResponse(archived_as=archived_as)


# ---------------------------------------------------------------------------
# DELETE /api/git/branches
# ---------------------------------------------------------------------------


@router.delete("/branches")
def git_delete_branch(body: GitDeleteBranchRequest) -> dict[str, str]:
    """Permanently delete a branch."""
    try:
        delete_branch(body.branch)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_delete_branch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return {"status": "ok", "branch": body.branch}
