"""Git panel endpoints — branch management, save, revert, and submit.

Provides a simplified git workflow for pricing analysts who don't use
git directly.  All operations go through ``haute._git`` which enforces
guardrails (no writes to protected branches, backup tags before revert).

All handlers are plain ``def`` (not ``async def``) so that FastAPI runs
them in a thread pool, avoiding event-loop blocking on slow git operations.

``_git`` returns Pydantic response models directly, so each route body
collapses to a single ``return _git.<op>(...)`` inside the
try/except — no dataclass-to-dict-to-model shim here.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Query

from haute._git import (
    GitDomainError,
    GitError,
    GitGuardrailError,
    archive_branch,
    commit_milestone,
    create_branch,
    delete_branch,
    get_history,
    get_status,
    list_branches,
    pull_latest,
    revert_to,
    save_progress,
    set_identity,
    set_working_branch,
    submit_for_review,
    switch_branch,
    working_branch_status,
    working_milestones,
)
from haute._logging import get_logger
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.schemas import (
    GitArchiveRequest,
    GitArchiveResponse,
    GitBranchListResponse,
    GitCommitRequest,
    GitCommitResponse,
    GitCreateBranchRequest,
    GitCreateBranchResponse,
    GitDeleteBranchRequest,
    GitDeleteBranchResponse,
    GitHistoryResponse,
    GitMilestonesResponse,
    GitPullResponse,
    GitRevertRequest,
    GitRevertResponse,
    GitSaveResponse,
    GitSetIdentityRequest,
    GitSetIdentityResponse,
    GitSetWorkingBranchRequest,
    GitSetWorkingBranchResponse,
    GitStatusResponse,
    GitSubmitResponse,
    GitSwitchBranchRequest,
    GitSwitchBranchResponse,
    GitWorkingBranchResponse,
)

logger = get_logger(component="server.git")

router = APIRouter(prefix="/api/git", tags=["git"])


def _handle_git_error(e: GitError) -> NoReturn:
    """Convert git errors to appropriate HTTP responses.

    Three error families are distinguished:

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


# ---------------------------------------------------------------------------
# GET /api/git/status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=GitStatusResponse)
def git_status() -> GitStatusResponse:
    """Current branch, changed files, and main-ahead status."""
    try:
        return get_status()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_status_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# GET /api/git/working-branch — readiness signal for the startup flow
# ---------------------------------------------------------------------------


@router.get("/working-branch", response_model=GitWorkingBranchResponse)
def git_get_working_branch() -> GitWorkingBranchResponse:
    """Working-branch state (ready/unset/invalid/divergent), identity, and the
    branches choosable as a working branch — everything the startup modal and
    toolbar indicator need in one call."""
    try:
        return working_branch_status(Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_working_branch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/working-branch — adopt (optionally create) a working branch
# ---------------------------------------------------------------------------


@router.post("/working-branch", response_model=GitSetWorkingBranchResponse)
def git_set_working_branch(body: GitSetWorkingBranchRequest) -> GitSetWorkingBranchResponse:
    """Adopt a working branch for this clone, spawning its ledger and recording
    the association. Confirms both the startup modal and the save-gate."""
    try:
        return set_working_branch(body.branch, Path.cwd(), create=body.create)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_set_working_branch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/identity — set commit identity (repo-local, or global)
# ---------------------------------------------------------------------------


@router.post("/identity", response_model=GitSetIdentityResponse)
def git_set_identity(body: GitSetIdentityRequest) -> GitSetIdentityResponse:
    """Set git user.name / user.email — repo-local by default, global on request."""
    try:
        return set_identity(body.user_name, body.user_email, set_global=body.set_global)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_set_identity_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/commit — milestone-merge the ledger onto the working branch
# ---------------------------------------------------------------------------


@router.post("/commit", response_model=GitCommitResponse)
def git_commit(body: GitCommitRequest) -> GitCommitResponse:
    """Record a milestone on the working branch (save & commit): merge the
    ledger's accumulated saves with the user's message + optional version tag."""
    try:
        return commit_milestone(body.message, Path.cwd(), version_label=body.version_label)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_commit_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# GET /api/git/milestones — working-branch milestone history (first-parent)
# ---------------------------------------------------------------------------


@router.get("/milestones", response_model=GitMilestonesResponse)
def git_milestones(limit: int = Query(20, ge=1, le=500)) -> GitMilestonesResponse:
    """Milestone history of the working branch (its first-parent chain)."""
    try:
        return working_milestones(Path.cwd(), limit=limit)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_milestones_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# GET /api/git/branches
# ---------------------------------------------------------------------------


@router.get("/branches", response_model=GitBranchListResponse)
def git_branches() -> GitBranchListResponse:
    """List all branches (user's first, then others, archived last)."""
    try:
        return list_branches()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_branches_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


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


@router.post("/switch", response_model=GitSwitchBranchResponse)
def git_switch(body: GitSwitchBranchRequest) -> GitSwitchBranchResponse:
    """Switch to a branch (auto-commits pending changes first)."""
    try:
        switch_branch(body.branch)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_switch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return GitSwitchBranchResponse(branch=body.branch)


# ---------------------------------------------------------------------------
# POST /api/git/save
# ---------------------------------------------------------------------------


@router.post("/save", response_model=GitSaveResponse)
def git_save() -> GitSaveResponse:
    """Stage, commit, and push all changes."""
    try:
        return save_progress()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_save_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/submit
# ---------------------------------------------------------------------------


@router.post("/submit", response_model=GitSubmitResponse)
def git_submit() -> GitSubmitResponse:
    """Push and return a comparison URL for PR creation."""
    try:
        return submit_for_review()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_submit_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


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
    return GitHistoryResponse(entries=entries)


# ---------------------------------------------------------------------------
# POST /api/git/revert
# ---------------------------------------------------------------------------


@router.post("/revert", response_model=GitRevertResponse)
def git_revert(body: GitRevertRequest) -> GitRevertResponse:
    """Reset to a specific commit (creates a backup tag first)."""
    try:
        return revert_to(body.sha)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_revert_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/pull
# ---------------------------------------------------------------------------


@router.post("/pull", response_model=GitPullResponse)
def git_pull() -> GitPullResponse:
    """Pull latest default branch into current branch."""
    try:
        return pull_latest()
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_pull_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


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


@router.delete("/branches", response_model=GitDeleteBranchResponse)
def git_delete_branch(body: GitDeleteBranchRequest) -> GitDeleteBranchResponse:
    """Permanently delete a branch."""
    try:
        delete_branch(body.branch)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_delete_branch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return GitDeleteBranchResponse(branch=body.branch)
