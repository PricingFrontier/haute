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

from fastapi import APIRouter, HTTPException, Query, Request

from haute import _project_storage
from haute._git import (
    GitDomainError,
    GitError,
    GitGuardrailError,
    GitMilestoneForkError,
    GitPushRejectedError,
    archive_working_pair,
    branch_away,
    commit_context,
    commit_milestone,
    create_working_branch,
    delete_working_pair,
    fast_forward_pair,
    get_prefs,
    graph_topology,
    list_remotes,
    milestone_saves,
    move_to_commit,
    pending_ledger_saves,
    push_working_pair,
    restore_working_pair,
    set_identity,
    set_prefs,
    set_working_branch,
    undelete_working_pair,
    working_branch_status,
    working_branches,
    working_milestones,
)
from haute._logging import get_logger
from haute.graph_utils import PipelineGraph
from haute.hosted import FORWARDED_USER_SCOPE_KEY
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, commit_pipeline_graph, pause_watcher
from haute.schemas import (
    GitArchiveRequest,
    GitArchiveResponse,
    GitBindStorageRequest,
    GitBindStorageResponse,
    GitBranchAwayRequest,
    GitBranchAwayResponse,
    GitCommitContext,
    GitCommitRequest,
    GitCommitResponse,
    GitCreateWorkingBranchRequest,
    GitCreateWorkingBranchResponse,
    GitDeleteBranchRequest,
    GitDeleteBranchResponse,
    GitFastForwardRequest,
    GitFastForwardResponse,
    GitForkStorageRequest,
    GitForkStorageResponse,
    GitGraphResponse,
    GitLedgerSavesResponse,
    GitMilestoneFork,
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
    GitStorageBind,
    GitStorageClaim,
    GitStorageSync,
    GitUndeleteRequest,
    GitUndeleteResponse,
    GitUpstreamStatusResponse,
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


def _with_storage_state(status: GitWorkingBranchResponse) -> GitWorkingBranchResponse:
    """Attach durable-storage state to a readiness response.

    Never raises: storage is additive to git readiness, and a storage
    fault must not blank the branch indicator. The failure surfaces
    through the sync state instead.
    """
    binding = _project_storage.active_binding()
    lineage = _project_storage.active_lineage()
    sync = _project_storage.push_queue().status()
    bind = _project_storage.bind_task().status()
    return status.model_copy(
        update={
            "storage": _project_storage.storage_state(),
            "storage_remote": binding.remote_url if binding is not None else None,
            "storage_forked_from": lineage.parent_url if lineage is not None else None,
            "sync": GitStorageSync(
                state=sync.state,
                pending=sync.pending,
                failure=sync.failure,
                message=sync.message,
            ),
            "storage_bind": GitStorageBind(
                state=bind.state,
                outcome=bind.outcome,
                message=bind.message,
                claim=_claim_model(bind.claim, bind.message),
                remote_url=bind.remote_url,
            ),
        }
    )


def _claim_model(claim: object, message: str | None) -> GitStorageClaim | None:
    """Render a lease holder for the client, or ``None`` when unheld."""
    if claim is None:
        return None
    return GitStorageClaim(
        app_name=getattr(claim, "app_name", ""),
        user=getattr(claim, "user", None),
        refreshed_at=getattr(claim, "refreshed_at", None),
        message=message or "",
    )


# ---------------------------------------------------------------------------
# GET /api/git/working-branch — readiness signal for the startup flow
# ---------------------------------------------------------------------------


@router.get("/working-branch", response_model=GitWorkingBranchResponse)
def git_get_working_branch() -> GitWorkingBranchResponse:
    """Repository readiness, identity, durable-storage state, and the
    branches choosable as a working branch — everything the startup modal and
    toolbar indicator need in one call."""
    try:
        status = working_branch_status(Path.cwd())
        return _with_storage_state(status)
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
        with pause_watcher():  # M4: adopting a branch checks out its ledger (tree swap)
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


@router.post(
    "/commit",
    response_model=GitCommitResponse,
    responses={409: {"model": GitMilestoneFork}},
)
def git_commit(body: GitCommitRequest) -> GitCommitResponse:
    """Record a milestone on the working branch (save & commit): merge the
    ledger's accumulated saves with the user's message + optional version tag.

    When the working branch is behind its remote, a milestone would fork it; the
    route returns **409** with a structured :class:`GitMilestoneFork` body so the
    UI can warn (U4/D4). ``allow_fork`` is the user's "commit anyway" override."""
    try:
        response = commit_milestone(
            body.message,
            Path.cwd(),
            version_label=body.version_label,
            allow_fork=body.allow_fork,
        )
        # Publish the milestone (and any ledger saves it folded in) when the
        # project is bound to durable storage. A no-op for local sessions.
        _project_storage.enqueue_push()
        return response
    except GitMilestoneForkError as e:
        logger.info("git_commit_would_fork", remote=e.fork.remote)
        raise HTTPException(status_code=409, detail=e.fork.model_dump())
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
# GET /api/git/graph — whole-forest topology for the panel's graph rail
# ---------------------------------------------------------------------------


@router.get("/graph", response_model=GitGraphResponse)
def git_graph(limit: int = Query(50, ge=1, le=500)) -> GitGraphResponse:
    """Every working pair's first-parent spine with ancestry-derived fork
    attachments — the data behind the graph rail. Entries are windowed to
    ``limit`` per branch; fork points come from full spines and are reported
    even when outside the window. Read-only (no checkout, no HEAD change)."""
    try:
        return graph_topology(Path.cwd(), limit=limit)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_graph_failed", error=str(e), exc_info=True)
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
        with pause_watcher():  # M4: archiving the active pair switches away (tree swap)
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
        with pause_watcher():  # M4: deleting the active pair switches away (tree swap)
            return delete_working_pair(body.branch, Path.cwd(), confirm=body.confirm)
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_delete_branch_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/undelete
# ---------------------------------------------------------------------------


@router.post("/undelete", response_model=GitUndeleteResponse)
def git_undelete(body: GitUndeleteRequest) -> GitUndeleteResponse:
    """Restore a deleted working pair from its trash refs + tombstone (the
    inverse of DELETE /branches). Pure ref/state ops — no checkout, no HEAD
    movement — so no watcher pause is needed."""
    try:
        return undelete_working_pair(body.branch, Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_undelete_failed", error=str(e), exc_info=True)
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
        with pause_watcher():  # M4: move-mode forks check out the new ledger (tree swap)
            return create_working_branch(body.name, Path.cwd(), at=body.at, move=body.move)
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
    """Deliberately publish managed history to a chosen existing remote.

    An advertised-empty remote receives its resolved default branch and the
    working/ledger pair atomically.  Established remotes receive only the pair,
    after their default branch has been fetched and validated.  Existing refs
    are never force-updated (S16/S33), and plain saves never invoke this route.

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


# ---------------------------------------------------------------------------
# POST /api/git/storage/bind — bind this project to durable storage
# ---------------------------------------------------------------------------


@router.post("/storage/bind", response_model=GitBindStorageResponse)
def git_bind_storage(body: GitBindStorageRequest, request: Request) -> GitBindStorageResponse:
    """Start making this hosted project's history durable.

    Only the instant, local checks run here — a malformed URL, an
    already-bound project, a deployment with nowhere to record a binding
    — because those are the answers that belong beside the input field.
    The network work (claim the location, inspect it, publish the whole
    project, record the binding) then runs in the background, so a bind
    never holds the session open for the length of a publish. Progress
    and the real outcome arrive on the readiness response's
    ``storage_bind``."""
    try:
        remote_url = _project_storage.precheck_bind(body.remote_url)
        _project_storage.bind_task().start(
            remote_url,
            Path.cwd(),
            # Platform-authenticated visitor, when hosted behind an SSO proxy.
            bound_by=request.scope.get(FORWARDED_USER_SCOPE_KEY),
        )
    except _project_storage.StorageConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _project_storage.StorageUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("storage_bind_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)

    return GitBindStorageResponse(
        remote_url=remote_url,
        message="Saving this project to storage — you can keep working.",
    )


# ---------------------------------------------------------------------------
# POST /api/git/storage/bind/ack — clear a finished bind result
# ---------------------------------------------------------------------------


@router.post("/storage/bind/ack", response_model=GitWorkingBranchResponse)
def git_acknowledge_bind() -> GitWorkingBranchResponse:
    """Clear a finished bind result once the UI has shown it.

    The result persists after the bind completes so a slow poll cannot
    miss it; this is how the UI says it no longer needs it."""
    _project_storage.bind_task().acknowledge()
    try:
        return _with_storage_state(working_branch_status(Path.cwd()))
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("storage_bind_ack_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/storage/fork — copy a held location's published state
# ---------------------------------------------------------------------------


@router.post("/storage/fork", response_model=GitForkStorageResponse)
def git_fork_storage(body: GitForkStorageRequest, request: Request) -> GitForkStorageResponse:
    """Fork a uc:// location's latest published generation to an empty one.

    The honest way past a location someone else holds: work on a copy,
    with provenance recorded so the fork is signposted, not silent. Takes
    no claim — binding to the target afterwards claims it."""
    try:
        lineage = _project_storage.fork_uc_location(
            body.source_url,
            body.target_url,
            Path.cwd(),
            forked_by=request.scope.get(FORWARDED_USER_SCOPE_KEY),
        )
    except _project_storage.StorageConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _project_storage.StorageUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("storage_fork_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)

    target = _project_storage.validate_remote_url(body.target_url)
    return GitForkStorageResponse(
        target_url=target,
        parent_url=lineage.parent_url,
        parent_generation=lineage.parent_generation,
        message=(
            f"Forked generation {lineage.parent_generation} into {target}. "
            "Bind this session to the new location to work on the copy."
        ),
    )


# ---------------------------------------------------------------------------
# POST /api/git/storage/upstream/{check,pull} — a fork's parent relationship
# ---------------------------------------------------------------------------


def _upstream_message(status: _project_storage.UpstreamStatus) -> str:
    """Hand-authored prose for the upstream dialog.

    The three states the dialog serves: nothing to do, a clean catch-up, and
    the honest dead end where both sides moved (no merge exists yet — see the
    approved change contract).
    """
    behind = max(status.working.behind or 0, status.ledger.behind or 0)
    ahead = max(status.working.ahead or 0, status.ledger.ahead or 0)
    if status.can_fast_forward:
        changes = "change" if behind == 1 else "changes"
        return (
            f"The parent project has {behind} {changes} this copy doesn't have yet. "
            "Catching up brings them in without touching your own work."
        )
    if status.working.status in ("ahead", "diverged") or status.ledger.status in (
        "ahead",
        "diverged",
    ):
        if ahead and behind:
            return (
                f"Both projects have moved since the fork — {ahead} change(s) here and "
                f"{behind} in the parent. Catching up would need a merge, which this "
                "version can't do for you."
            )
        return (
            f"This copy is {ahead} change(s) ahead of its parent and the parent has "
            "nothing new — there is nothing to catch up to."
        )
    if status.working.status in ("unknown", "untracked") or status.ledger.status in (
        "unknown",
        "untracked",
    ):
        return (
            "This copy's relationship to its parent couldn't be measured — the parent's "
            "stored project may not carry the same branches."
        )
    return "This copy is up to date with the project it was forked from."


@router.post("/storage/upstream/check", response_model=GitUpstreamStatusResponse)
def git_check_upstream() -> GitUpstreamStatusResponse:
    """Measure this fork against the parent it was forked from.

    On demand only — it downloads the parent's whole stored bundle, which
    is why it is deliberately absent from the polled readiness response."""
    try:
        status = _project_storage.check_upstream(Path.cwd())
    except _project_storage.StorageConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _project_storage.StorageUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("storage_upstream_check_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)

    return GitUpstreamStatusResponse(
        parent_url=status.parent_url,
        parent_generation=status.parent_generation,
        working=status.working,
        ledger=status.ledger,
        can_fast_forward=status.can_fast_forward,
        checked_at=status.checked_at,
        message=_upstream_message(status),
    )


@router.post("/storage/upstream/pull", response_model=GitFastForwardResponse)
def git_pull_upstream() -> GitFastForwardResponse:
    """Catch this fork up to its parent by fast-forward only.

    One-directional by design (parent → fork). The watcher is paused for
    the wholesale tree replacement, exactly as the remote catch-up does."""
    try:
        with pause_watcher():
            return _project_storage.pull_upstream(Path.cwd())
    except _project_storage.StorageConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _project_storage.StorageUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("storage_upstream_pull_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/storage/retry — retry a failed publication now
# ---------------------------------------------------------------------------


@router.post("/storage/retry", response_model=GitWorkingBranchResponse)
def git_retry_storage_sync() -> GitWorkingBranchResponse:
    """Retry publishing after a failure, returning the refreshed readiness."""
    _project_storage.push_queue().retry_now()
    try:
        return _with_storage_state(working_branch_status(Path.cwd()))
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("storage_retry_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/fast-forward — conflict-free catch-up to the remote (D1/D2)
# ---------------------------------------------------------------------------


@router.post("/fast-forward", response_model=GitFastForwardResponse)
def git_fast_forward(body: GitFastForwardRequest) -> GitFastForwardResponse:
    """Catch the working pair up to a remote's tips by fast-forward only (D1/D2) —
    a pure ref advance, never a merge. Refuses anything that isn't a clean
    fast-forward (the user spins off a copy instead). The watcher is paused for
    the wholesale tree replacement (S30/M4)."""
    try:
        with pause_watcher():
            return fast_forward_pair(body.remote, Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_fast_forward_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


# ---------------------------------------------------------------------------
# POST /api/git/branch-away — set the local fork aside, adopt the remote (M3)
# ---------------------------------------------------------------------------


@router.post("/branch-away", response_model=GitBranchAwayResponse)
def git_branch_away(body: GitBranchAwayRequest) -> GitBranchAwayResponse:
    """Resolve a remote fork by setting the local pair aside under a dated name and
    repointing the canonical name to the remote's tips (M3) — both lineages kept,
    nothing rewritten, never a merge. The watcher is paused for the tree
    replacement (S30/M4)."""
    try:
        with pause_watcher():
            return branch_away(body.remote, Path.cwd())
    except GitError as e:
        _handle_git_error(e)
    except Exception as e:
        logger.error("git_branch_away_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
