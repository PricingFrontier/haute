"""Git panel endpoints — working-branch management, milestones, and history.

Provides a simplified git workflow for pricing analysts who don't use
git directly.  All operations go through ``haute._git`` which enforces
guardrails (no writes to protected branches, no egress except the
deliberate push surface).

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
    GitPushRejectedError,
    archive_working_pair,
    commit_context,
    commit_milestone,
    create_working_branch,
    delete_working_pair,
    get_prefs,
    get_status,
    list_remotes,
    milestone_saves,
    move_to_commit,
    pending_ledger_saves,
    push_working_pair,
    restore_working_pair,
    set_identity,
    set_prefs,
    set_working_branch,
    working_branch_status,
    working_branches,
    working_milestones,
)
from haute._logging import get_logger
from haute.graph_utils import PipelineGraph
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, commit_pipeline_graph, pause_watcher
from haute.schemas import (
    GitArchiveRequest,
    GitArchiveResponse,
    GitCommitContext,
    GitCommitRequest,
    GitCommitResponse,
    GitCreateWorkingBranchRequest,
    GitCreateWorkingBranchResponse,
    GitDeleteBranchRequest,
    GitDeleteBranchResponse,
    GitLedgerSavesResponse,
    GitMilestonesResponse,
    GitMoveRequest,
    GitMoveResponse,
    GitPrefs,
    GitPushRejection,
    GitPushRequest,
    GitPushResponse,
    GitRemotesResponse,
    GitRestoreRequest,
    GitRestoreResponse,
    GitSetIdentityRequest,
    GitSetIdentityResponse,
    GitSetWorkingBranchRequest,
    GitSetWorkingBranchResponse,
    GitStatusResponse,
    GitWorkingBranchesResponse,
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
# POST /api/git/move — move to a historical commit (detached checkout, §3.4)
# ---------------------------------------------------------------------------


@router.post("/move", response_model=GitMoveResponse)
def git_move(body: GitMoveRequest) -> GitMoveResponse:
    """Move the working directory to a historical commit (detached checkout).

    The watcher is paused for the wholesale tree replacement (S30); the move
    enforces the §3.9 floors (refuse dirty tree / in-progress git op) and clears
    the working branch, so the next save spawns a fresh one (S13)."""
    try:
        with pause_watcher():
            return move_to_commit(body.sha, Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_move_failed", error=str(e), exc_info=True)
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
def git_milestones(
    limit: int = Query(20, ge=1, le=500), branch: str | None = Query(None)
) -> GitMilestonesResponse:
    """Milestone history (first-parent chain). Defaults to the working branch;
    ``?branch=`` peeks at another branch without switching."""
    try:
        return working_milestones(Path.cwd(), limit=limit, branch=branch)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_milestones_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# Ledger expansion — the per-save commits behind a milestone, and the pending
# saves on the ledger ahead of the working tip (next-milestone preview).
# ---------------------------------------------------------------------------


@router.get("/milestones/{sha}/saves", response_model=GitLedgerSavesResponse)
def git_milestone_saves(sha: str) -> GitLedgerSavesResponse:
    """The ledger saves a milestone folded in (its second-parent run)."""
    try:
        return milestone_saves(sha)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_milestone_saves_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.get("/pending-saves", response_model=GitLedgerSavesResponse)
def git_pending_saves(branch: str | None = Query(None)) -> GitLedgerSavesResponse:
    """Saves on a branch's ledger ahead of its tip — the next milestone preview.
    Defaults to the working branch; ``?branch=`` peeks at another."""
    try:
        return pending_ledger_saves(Path.cwd(), branch=branch)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_pending_saves_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/archive
# ---------------------------------------------------------------------------


@router.post("/archive", response_model=GitArchiveResponse)
def git_archive(body: GitArchiveRequest) -> GitArchiveResponse:
    """Archive a working branch and its ledger together (S32, pair-aware)."""
    try:
        return archive_working_pair(body.branch, Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_archive_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# DELETE /api/git/branches
# ---------------------------------------------------------------------------


@router.delete("/branches", response_model=GitDeleteBranchResponse)
def git_delete_branch(body: GitDeleteBranchRequest) -> GitDeleteBranchResponse:
    """Delete a working branch and its ledger together; refuses on unmerged
    ledger saves unless ``confirm`` (§8, pair-aware)."""
    try:
        return delete_working_pair(body.branch, Path.cwd(), confirm=body.confirm)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_delete_branch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# GET /api/git/working-branches — branch manager view (version lines)
# ---------------------------------------------------------------------------


@router.get("/working-branches", response_model=GitWorkingBranchesResponse)
def git_working_branches() -> GitWorkingBranchesResponse:
    """List working branches (active + archived) for the branch manager."""
    try:
        return working_branches(Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_working_branches_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/restore", response_model=GitRestoreResponse)
def git_restore(body: GitRestoreRequest) -> GitRestoreResponse:
    """Un-archive a working branch and its ledger together (inverse of archive)."""
    try:
        return restore_working_pair(body.branch, Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_restore_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/working-branches — fork a new working branch (P5d/S38)
# ---------------------------------------------------------------------------


@router.post("/working-branches", response_model=GitCreateWorkingBranchResponse)
def git_create_working_branch(
    body: GitCreateWorkingBranchRequest,
) -> GitCreateWorkingBranchResponse:
    """Fork a new working branch off the current one. ``at``/``move`` select the
    fork point and whether in-progress work is relocated onto it (S38)."""
    try:
        return create_working_branch(
            body.name, Path.cwd(), at=body.at, move=body.move
        )
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_create_working_branch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# GET/POST /api/git/prefs — per-clone UI preferences (S38)
# ---------------------------------------------------------------------------


@router.get("/prefs", response_model=GitPrefs)
def git_get_prefs() -> GitPrefs:
    """This clone's local UI preferences (e.g. switch-confirm 'don't ask again')."""
    try:
        return get_prefs(Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_get_prefs_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/prefs", response_model=GitPrefs)
def git_set_prefs(body: GitPrefs) -> GitPrefs:
    """Persist this clone's local UI preferences."""
    try:
        return set_prefs(body, Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_set_prefs_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# GET /api/git/remotes — existing remotes for the deliberate-push dropdown (S16)
# ---------------------------------------------------------------------------


@router.get("/remotes", response_model=GitRemotesResponse)
def git_remotes() -> GitRemotesResponse:
    """Configured remotes + the working branch's ahead/behind vs each (no fetch)."""
    try:
        return list_remotes(Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_remotes_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# GET /api/git/show/{sha} — read-only view of a commit's pipeline (S11)
# ---------------------------------------------------------------------------


@router.get("/show/{sha}", response_model=PipelineGraph)
def git_show(sha: str) -> PipelineGraph:
    """Parse the active pipeline as it was at commit *sha* — a read-only view
    (view ≠ move): no checkout, no HEAD change, any number of visits (S11)."""
    try:
        return commit_pipeline_graph(sha)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_show_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# GET /api/git/commit-context/{sha} — a commit's nearest ancestor milestone +
# distance ("breadcrumb context") for the version-compare UI. Read-only.
# ---------------------------------------------------------------------------


@router.get("/commit-context/{sha}", response_model=GitCommitContext)
def git_commit_context(sha: str, base: str | None = Query(None)) -> GitCommitContext:
    """A commit's nearest ancestor milestone and the distance from it — the
    breadcrumb shown in the version-compare UI. ``?base=`` additionally reports the
    commit delta ``base..sha`` (the historic↔current span). Read-only (no checkout)."""
    try:
        return commit_context(Path.cwd(), sha, base=base)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_commit_context_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/push — deliberately push the working/ledger pair (S16/S33)
# ---------------------------------------------------------------------------


@router.post(
    "/push",
    response_model=GitPushResponse,
    responses={409: {"model": GitPushRejection}},
)
def git_push(body: GitPushRequest) -> GitPushResponse:
    """Push the working branch + its ledger to a chosen existing remote, atomically
    and never force (S16/S33). Deliberate — never invoked from a plain save.

    A non-fast-forward rejection returns **409** with a structured
    :class:`GitPushRejection` body (per-leg divergence + a leg-naming message) so
    the client can show the honest fork instead of a dead-end (M7)."""
    try:
        return push_working_pair(body.remote, Path.cwd())
    except GitPushRejectedError as e:
        logger.warning("git_push_rejected", remote=body.remote, message=e.rejection.message)
        raise HTTPException(status_code=409, detail=e.rejection.model_dump())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_push_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
