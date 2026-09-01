"""Local branch-pair movement, archive lifecycle, and rollback operations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

# The command core is the sole process boundary.  Its helpers are imported
# explicitly into this domain's namespace so extracted functions retain their
# original signatures and semantics without a facade dependency.
from haute._git_core import (
    _ARCHIVE_PREFIX,
    GitDomainError,
    GitError,
    GitGuardrailError,
    GitTransactionError,
    _assert_eligible_working,
    _assert_git_repo,
    _assert_no_git_op_in_progress,
    _get_current_branch,
    _get_default_branch,
    _rev_parse,
    _run_git,
    _run_git_ok,
    _serialized_mutation,
    _validate_ref_name,
    _wipe_volatile_artefacts,
    ledger_name,
)
from haute._git_read_models import _has_unmerged_saves, _normalize_to_working
from haute._logging import get_logger
from haute.schemas import (
    GitArchiveResponse,
    GitDeleteBranchResponse,
    GitMoveResponse,
    GitRestoreResponse,
    GitUndeleteResponse,
)

logger = get_logger(component="git")

__all__ = [
    "move_to_commit",
    "archive_working_pair",
    "delete_working_pair",
    "undelete_working_pair",
    "restore_working_pair",
]


@_serialized_mutation
def move_to_commit(sha: str, project_root: Path, cwd: Path | None = None) -> GitMoveResponse:
    """Move the working directory to *sha* — its tree becomes the repo state.

    A detached-HEAD checkout (§3.4): creates nothing and moves no ref, so the
    prior branch keeps pointing at its tip and stays fully reachable (unlike v0's
    revert, which reset a ref and could orphan milestones). The working-branch
    association is cleared, leaving the clone in the 'unset' state so the next
    save spawns a fresh working+ledger pair here (S13).

    Pre-move floors (§3.9): refuse if a git operation is in progress (row H) or
    if the tree has uncommitted tracked changes (row A / S21) — resolution
    happens via save-or-discard *before* the move, never silently here. Volatile
    on-disk artefacts are wiped (S12).
    """
    from haute._git_state import clear_working_branch

    _assert_git_repo(cwd)
    _validate_ref_name(sha)

    # Floor (row H): no haute git op while a merge/rebase/cherry-pick is unfinished.
    _assert_no_git_op_in_progress(cwd)

    # Floor (row A / S21): a dirty tracked tree means unsaved or external edits.
    # Refuse — the caller saves or discards first. Untracked files (e.g.
    # .haute/state.json) don't block a checkout, so they're ignored here.
    ok_status, status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    if ok_status and status.strip():
        raise GitDomainError(
            "You have unsaved changes. Save or discard them before moving to another version."
        )

    target = _rev_parse(sha, cwd=cwd)
    if target is None:
        raise GitDomainError(f"No commit found for {sha!r}.")

    prior_branch = _get_current_branch(cwd)

    # Volatile artefacts (S12): wipe so a stale cache can't survive into the
    # moved-to tree. Best-effort and before the checkout — reconstructable.
    _wipe_volatile_artefacts(cwd or Path.cwd())

    # Detached checkout: materialise the target's tree as the working directory.
    _run_git("checkout", "--detach", target, cwd=cwd)

    # The clone now serves no working branch — the next save spawns one (S13).
    clear_working_branch(project_root)

    logger.info("moved_to_commit", sha=target, prior_branch=prior_branch)
    return GitMoveResponse(
        sha=target,
        short_sha=target[:8],
        prior_branch=prior_branch,
        is_detached=True,
    )


def _switch_away_if_active(
    working: str,
    ledger: str,
    project_root: Path,
    cwd: Path | None = None,
    discard: bool = False,
) -> bool:
    """Before archiving/deleting a pair, move HEAD off it (a checked-out branch
    can't be renamed/deleted). Return whether clone state records this pair, so
    the caller can clear that state inside its wider transaction.

    When *discard* (a confirmed delete — the branch is going away anyway), a
    dirty tree is force-discarded with the checkout. Otherwise tracked
    modifications refuse the move with actionable guidance, since a lossless
    archive must not silently throw away volatile work (S12/S38)."""
    from haute._git_state import read_working_branch

    recorded = read_working_branch(project_root)
    if recorded == working or _get_current_branch(cwd) in (working, ledger):
        if not discard:
            # TRACKED modifications would make the checkout abort with a raw,
            # sanitized error. Refuse with actionable guidance instead. Untracked
            # files (e.g. .haute/state.json) don't block a checkout, so ignore.
            ok, status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
            if ok and status.strip():
                raise GitDomainError(
                    "You have unsaved changes on this branch. Save or discard "
                    "them before archiving it."
                )
        default = _get_default_branch(cwd)
        if default not in (working, ledger) and _rev_parse(default, cwd=cwd) is not None:
            checkout_args = (("-f",) if discard else ()) + (default,)
        else:
            # An adopted repository may have no branch outside its active pair.
            # Detach at the pair's current commit so the refs can still be
            # renamed/deleted without inventing a branch or failing checkout.
            fallback = _rev_parse(ledger, cwd=cwd) or _rev_parse(working, cwd=cwd)
            if fallback is None:
                raise GitDomainError("No safe commit is available to leave the active branch.")
            checkout_args = (("-f",) if discard else ()) + ("--detach", fallback)
        _run_git("checkout", *checkout_args, cwd=cwd)
        return recorded == working
    return False


def _unique_archive_name(working: str, cwd: Path | None = None) -> str:
    """An ``archive/<working>`` name for which BOTH it and its ledger
    (``archive/<working>-save``) are free, so the pair can't collide with an
    existing branch on either ref. Disambiguates with the date, then a counter."""

    def taken(name: str) -> bool:
        return (
            _rev_parse(name, cwd=cwd) is not None
            or _rev_parse(ledger_name(name), cwd=cwd) is not None
        )

    base = f"{_ARCHIVE_PREFIX}/{working}"
    if not taken(base):
        return base
    date = datetime.now(UTC).strftime("%Y%m%d")
    candidate = f"{base}-{date}"
    counter = 2
    while taken(candidate):
        candidate = f"{base}-{date}-{counter}"
        counter += 1
    return candidate


@_serialized_mutation
def archive_working_pair(
    branch: str, project_root: Path, cwd: Path | None = None
) -> GitArchiveResponse:
    """Archive a working branch and its ledger together (S32): bidirectional
    (either name archives both), switches away first if it's the active pair,
    NO unmerged-saves refusal (the saves ride into the archived ledger), and
    no remote side effects (S16)."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    working = _normalize_to_working(branch)
    _assert_eligible_working(working)

    if _rev_parse(working, cwd=cwd) is None:
        raise GitDomainError(f"Branch '{working}' does not exist.")
    if working.startswith(f"{_ARCHIVE_PREFIX}/"):
        raise GitDomainError(f"'{working}' is already archived.")

    from haute._git_state import clear_working_branch, read_working_branch, write_working_branch

    ledger = ledger_name(working)
    previous_working = read_working_branch(project_root)
    archived = _unique_archive_name(working, cwd=cwd)
    head_attached, previous_head = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
    if not head_attached:
        previous_head = _run_git("rev-parse", "HEAD", cwd=cwd)

    renamed_working = renamed_ledger = False
    try:
        if _switch_away_if_active(working, ledger, project_root, cwd=cwd):
            clear_working_branch(project_root)
        _run_git("branch", "-m", working, archived, cwd=cwd)
        renamed_working = True
        if _rev_parse(ledger, cwd=cwd) is not None:
            _run_git("branch", "-m", ledger, ledger_name(archived), cwd=cwd)
            renamed_ledger = True
    except (GitError, OSError) as exc:
        restored = True
        if renamed_ledger:
            ok, _ = _run_git_ok("branch", "-m", ledger_name(archived), ledger, cwd=cwd)
            restored &= ok
        if renamed_working:
            ok, _ = _run_git_ok("branch", "-m", archived, working, cwd=cwd)
            restored &= ok
        try:
            if previous_working is None:
                clear_working_branch(project_root)
            else:
                write_working_branch(project_root, previous_working)
            state_restored = True
        except OSError:
            state_restored = False
        if head_attached:
            ok, _ = _run_git_ok("checkout", previous_head, cwd=cwd)
        else:
            ok, _ = _run_git_ok("checkout", "--detach", previous_head, cwd=cwd)
        restored &= ok and state_restored
        if not restored:
            raise GitTransactionError(
                "Archiving the branch failed and automatic rollback was incomplete. "
                "Inspect the repository before retrying."
            ) from exc
        raise

    logger.info("working_pair_archived", working=working, archived=archived)
    return GitArchiveResponse(archived_as=archived)


def _trash_ref(branch: str) -> str:
    """The ``refs/haute/trash/`` ref pinning a deleted branch's tip. A plain
    ref outside ``refs/heads/`` — invisible to the branch surfaces, but it
    keeps the commit chain reachable so gc can never collect a deleted pair
    while its tombstone is alive."""
    return f"refs/haute/trash/{branch}"


@_serialized_mutation
def delete_working_pair(
    branch: str,
    project_root: Path,
    confirm: bool = False,
    cwd: Path | None = None,
) -> GitDeleteBranchResponse:
    """Delete a working branch and its ledger together (§8): bidirectional,
    refuses when the ledger has unmerged saves unless *confirm* (loss is real),
    switches away first if active, no remote side effects (S16).

    The delete is trash-preserving: before the branch refs go, both tips are
    pinned under ``refs/haute/trash/`` (an instant ref write that also shields
    the objects from gc) and a tombstone — tips, archived flag, delete time —
    lands in ``.haute/trash.json``, so
    ``undelete_working_pair`` can rebuild the pair exactly. The deleted
    lineage therefore survives locally even though the branches vanish."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    working = _normalize_to_working(branch)
    _assert_eligible_working(working)

    working_tip = _rev_parse(working, cwd=cwd)
    if working_tip is None:
        raise GitDomainError(f"Branch '{working}' does not exist.")

    ledger = ledger_name(working)
    ledger_tip = _rev_parse(ledger, cwd=cwd)

    if not confirm and _has_unmerged_saves(working_tip, ledger_tip, cwd=cwd):
        raise GitGuardrailError(
            f"'{working}' has saves that were never committed to a milestone — "
            "deleting it loses them. Confirm to delete anyway."
        )

    from haute._git_state import (
        clear_working_branch,
        read_working_branch,
        record_trash,
        remove_trash,
        write_working_branch,
    )

    previous_working = read_working_branch(project_root)
    head_attached, previous_head = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
    if not head_attached:
        previous_head = _run_git("rev-parse", "HEAD", cwd=cwd)

    try:
        # A confirmed delete is destructive by intent — discard a dirty tree
        # along with the branch rather than refusing (S38).
        if _switch_away_if_active(working, ledger, project_root, cwd=cwd, discard=True):
            clear_working_branch(project_root)
        # Recovery net FIRST, refs second — a failure between the two remains
        # compensatable without making history unreachable.
        _run_git("update-ref", _trash_ref(working), working_tip, cwd=cwd)
        if ledger_tip is not None:
            _run_git("update-ref", _trash_ref(ledger), ledger_tip, cwd=cwd)
        else:
            _run_git_ok("update-ref", "-d", _trash_ref(ledger), cwd=cwd)
        record_trash(
            project_root,
            working,
            {
                "branch_tip": working_tip,
                "ledger_tip": ledger_tip,
                "was_archived": working.startswith(f"{_ARCHIVE_PREFIX}/"),
                "deleted_at": datetime.now(UTC).isoformat(),
            },
        )

        _run_git("branch", "-D", working, cwd=cwd)
        if ledger_tip is not None and _rev_parse(ledger, cwd=cwd) is not None:
            _run_git("branch", "-D", ledger, cwd=cwd)
    except (GitError, OSError) as exc:
        restored = True
        if _rev_parse(working, cwd=cwd) is None:
            ok, _ = _run_git_ok("update-ref", f"refs/heads/{working}", working_tip, cwd=cwd)
            restored &= ok
        if ledger_tip is not None and _rev_parse(ledger, cwd=cwd) is None:
            ok, _ = _run_git_ok("update-ref", f"refs/heads/{ledger}", ledger_tip, cwd=cwd)
            restored &= ok
        try:
            if previous_working is None:
                clear_working_branch(project_root)
            else:
                write_working_branch(project_root, previous_working)
            remove_trash(project_root, working)
            state_restored = True
        except OSError:
            state_restored = False
        ok_trash_w, _ = _run_git_ok("update-ref", "-d", _trash_ref(working), cwd=cwd)
        ok_trash_l, _ = _run_git_ok("update-ref", "-d", _trash_ref(ledger), cwd=cwd)
        if head_attached:
            ok_head, _ = _run_git_ok("checkout", previous_head, cwd=cwd)
        else:
            ok_head, _ = _run_git_ok("checkout", "--detach", previous_head, cwd=cwd)
        restored &= state_restored and ok_trash_w and ok_trash_l and ok_head
        if not restored:
            raise GitTransactionError(
                "Deleting the branch failed and automatic rollback was incomplete. "
                "Inspect the repository before retrying."
            ) from exc
        raise

    logger.info("working_pair_deleted", working=working, confirmed=confirm)
    return GitDeleteBranchResponse(status="deleted", branch=working)


@_serialized_mutation
def undelete_working_pair(
    branch: str, project_root: Path, cwd: Path | None = None
) -> GitUndeleteResponse:
    """Restore a deleted working pair from its trash pins + tombstone — the
    inverse of delete_working_pair's recovery net.

    Pure ref/state ops (no checkout, no HEAD movement): the working and
    ledger refs are recreated at their recorded tips, and the trash refs +
    tombstone are consumed. The archived flag needs no separate restore —
    archived-ness IS the ``archive/`` name prefix, and the pair is recreated
    under the exact name it was deleted as. The restored pair is NOT adopted
    as the working branch; the user switches to it deliberately.

    Domain errors (verbatim to the client): no tombstone for the name, either
    restored name already occupied, or the recorded commit no longer exists
    (tombstones can outlive their objects if the trash refs were hand-deleted
    and gc ran)."""
    from haute._git_state import read_trash, record_trash, remove_trash

    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    working = _normalize_to_working(branch)
    _assert_eligible_working(working)

    entry = read_trash(project_root).get(working)
    if entry is None:
        raise GitDomainError(f"No deleted branch named '{working}' to restore.")

    ledger = ledger_name(working)
    if _rev_parse(working, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{working}' already exists.")
    if _rev_parse(ledger, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{ledger}' already exists.")

    branch_tip = entry.get("branch_tip")
    if not isinstance(branch_tip, str) or _rev_parse(branch_tip, cwd=cwd) is None:
        raise GitDomainError(
            f"'{working}' can no longer be restored — its recorded commit is gone."
        )
    ledger_tip = entry.get("ledger_tip")
    if not (isinstance(ledger_tip, str) and _rev_parse(ledger_tip, cwd=cwd) is not None):
        ledger_tip = None  # pair deleted before its ledger ever spawned

    created_working = created_ledger = False
    try:
        _run_git("update-ref", f"refs/heads/{working}", branch_tip, cwd=cwd)
        created_working = True
        if ledger_tip is not None:
            _run_git("update-ref", f"refs/heads/{ledger}", ledger_tip, cwd=cwd)
            created_ledger = True

        _run_git("update-ref", "-d", _trash_ref(working), cwd=cwd)
        _run_git("update-ref", "-d", _trash_ref(ledger), cwd=cwd)
        remove_trash(project_root, working)
    except (GitError, OSError) as exc:
        restored = True
        if created_ledger:
            ok, _ = _run_git_ok("update-ref", "-d", f"refs/heads/{ledger}", cwd=cwd)
            restored &= ok
        if created_working:
            ok, _ = _run_git_ok("update-ref", "-d", f"refs/heads/{working}", cwd=cwd)
            restored &= ok
        ok, _ = _run_git_ok("update-ref", _trash_ref(working), branch_tip, cwd=cwd)
        restored &= ok
        if ledger_tip is not None:
            ok, _ = _run_git_ok("update-ref", _trash_ref(ledger), ledger_tip, cwd=cwd)
            restored &= ok
        try:
            if working not in read_trash(project_root):
                record_trash(project_root, working, entry)
            state_restored = True
        except OSError:
            state_restored = False
        if not restored or not state_restored:
            raise GitTransactionError(
                "Restoring the deleted branch failed and automatic rollback was incomplete. "
                "Inspect the repository before retrying."
            ) from exc
        raise
    logger.info("working_pair_undeleted", working=working)
    return GitUndeleteResponse(status="restored", branch=working)


@_serialized_mutation
def restore_working_pair(
    branch: str, project_root: Path, cwd: Path | None = None
) -> GitRestoreResponse:
    """Un-archive a pair: rename ``archive/<X>`` → ``<X>`` and its ledger back
    (the inverse of archive_working_pair). Bidirectional (accepts either archived
    name); refuses if a live branch already occupies either restored name. Ref
    changes compensate as one transaction on failure."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    archived_working = _normalize_to_working(branch)
    prefix = f"{_ARCHIVE_PREFIX}/"
    if not archived_working.startswith(prefix):
        raise GitDomainError(f"'{archived_working}' is not an archived branch.")
    if _rev_parse(archived_working, cwd=cwd) is None:
        raise GitDomainError(f"Branch '{archived_working}' does not exist.")

    restored = archived_working[len(prefix) :]
    _assert_eligible_working(restored)
    if _rev_parse(restored, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{restored}' already exists.")
    restored_ledger = ledger_name(restored)
    if _rev_parse(restored_ledger, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{restored_ledger}' already exists.")

    archived_ledger = ledger_name(archived_working)
    renamed_working = renamed_ledger = False
    try:
        _run_git("branch", "-m", archived_working, restored, cwd=cwd)
        renamed_working = True
        if _rev_parse(archived_ledger, cwd=cwd) is not None:
            _run_git("branch", "-m", archived_ledger, restored_ledger, cwd=cwd)
            renamed_ledger = True
    except (GitError, OSError) as exc:
        restored_ok = True
        if renamed_ledger:
            ok, _ = _run_git_ok("branch", "-m", restored_ledger, archived_ledger, cwd=cwd)
            restored_ok &= ok
        if renamed_working:
            ok, _ = _run_git_ok("branch", "-m", restored, archived_working, cwd=cwd)
            restored_ok &= ok
        if not restored_ok:
            raise GitTransactionError(
                "Restoring the archived branch failed and automatic rollback was incomplete. "
                "Inspect the repository before retrying."
            ) from exc
        raise
    logger.info("working_pair_restored", restored=restored)
    return GitRestoreResponse(restored_as=restored)
