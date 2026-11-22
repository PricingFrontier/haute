"""Local branch-pair archive lifecycle and rollback operations."""

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
    _assert_eligible_working,
    _assert_git_repo,
    _assert_new_branch_name,
    _branch_in_the_way,
    _clash_words,
    _free_pair_name,
    _get_current_branch,
    _list_branches_with_tips,
    _rev_parse,
    _run_git,
    _run_git_ok,
    _serialized_mutation,
    _validate_ref_name,
    ledger_name,
    working_name,
)
from haute._git_operation import Operation
from haute._git_read_models import _broken_pair, _has_unmerged_saves, _normalize_to_working
from haute._logging import get_logger
from haute.schemas import (
    GitArchiveResponse,
    GitDeleteBranchResponse,
    GitRepairPairResponse,
    GitRestoreResponse,
    GitUndeleteResponse,
)

logger = get_logger(component="git")

__all__ = [
    "archive_working_pair",
    "delete_working_pair",
    "undelete_working_pair",
    "restore_working_pair",
    "repair_working_pair",
]


def _leave_if_active(
    operation: Operation,
    working: str,
    ledger: str,
    project_root: Path,
    action: str,
    cwd: Path | None = None,
    discard: bool = False,
) -> bool:
    """Before archiving/deleting a pair, add the steps that move HEAD off it (a
    checked-out branch can't be renamed/deleted) and clear the clone's record of it.

    HEAD detaches at the working branch's tip, its last milestone, so the analyst
    stays on the work they had committed rather than on another branch's files;
    the ledger's later saves go with the archived ledger or the trash pins.

    When *discard* (a confirmed delete — the branch is going away anyway), a
    dirty tree is force-discarded with the checkout, which cannot be put back: the
    commit point goes after it, and the return is True. Otherwise tracked
    modifications refuse the move with actionable guidance naming *action*
    ("archiving", "deleting"), since an archive or an unconfirmed delete must not
    silently throw away volatile work (S12/S38)."""
    from haute._git_state import read_working_branch

    recorded = read_working_branch(project_root)
    if not (recorded == working or _get_current_branch(cwd) in (working, ledger)):
        return False
    # TRACKED modifications would make the checkout abort with a raw, sanitized
    # error. Untracked files (e.g. .haute/state.json) don't block a checkout.
    ok, status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    dirty = ok and bool(status.strip())
    if dirty and not discard:
        raise GitDomainError(
            f"You have unsaved changes on this branch. Save or discard them before {action} it."
        )
    milestone = _rev_parse(working, cwd=cwd) or _rev_parse(ledger, cwd=cwd)
    if milestone is None:
        raise GitDomainError("No safe commit is available to leave the active branch.")
    steps_before = operation.step_count
    operation.checkout_detached(milestone, force=discard)
    discarded = discard and dirty and operation.step_count > steps_before
    if discarded:
        operation.committed()
    if recorded == working:
        operation.working_branch(None)
    return discarded


@_serialized_mutation
def archive_working_pair(
    branch: str, project_root: Path, cwd: Path | None = None
) -> GitArchiveResponse:
    """Archive a working branch and its ledger together (S32): bidirectional
    (either name archives both), switches away first if it's the active pair,
    NO unmerged-saves refusal (the saves ride into the archived ledger), and
    no remote side effects (S16). The pair moves in one ref transaction, so no
    failure leaves half of it archived, and the whole archive is one recorded
    operation, put back on any failure."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    working = _normalize_to_working(branch)
    _assert_eligible_working(working)

    working_tip = _rev_parse(working, cwd=cwd)
    if working_tip is None:
        raise GitDomainError(f"Branch '{working}' does not exist.")
    if working.startswith(f"{_ARCHIVE_PREFIX}/"):
        raise GitDomainError(f"'{working}' is already archived.")

    ledger = ledger_name(working)
    ledger_tip = _rev_parse(ledger, cwd=cwd)
    archived = _free_pair_name(f"{_ARCHIVE_PREFIX}/{working}", bare_first=True, cwd=cwd)

    archive = Operation("archive", project_root, cwd)
    _leave_if_active(archive, working, ledger, project_root, "archiving", cwd=cwd)
    archive.refs(_moved_pair(working, archived, working_tip, ledger_tip))
    archive.run()

    logger.info("working_pair_archived", working=working, archived=archived)
    return GitArchiveResponse(archived_as=archived)


def _moved_pair(
    source: str, target: str, working_tip: str, ledger_tip: str | None
) -> dict[str, str | None]:
    """The ref updates renaming a working pair *source* → *target*: both new refs
    created and both old ones deleted, in one transaction."""
    updates: dict[str, str | None] = {
        f"refs/heads/{target}": working_tip,
        f"refs/heads/{source}": None,
    }
    if ledger_tip is not None:
        updates[f"refs/heads/{ledger_name(target)}"] = ledger_tip
        updates[f"refs/heads/{ledger_name(source)}"] = None
    return updates


def _new_trash_id(working: str, project_root: Path, cwd: Path | None) -> str:
    """A trash entry id for deleting *working* now: ``<branch>@<UTC stamp>``, with a
    counter when the same name is deleted twice within a second."""
    from haute._git_state import read_trash

    taken = read_trash(project_root)
    base = f"{working}@{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    candidate, counter = base, 2
    while candidate in taken or _rev_parse(f"refs/haute/trash/{candidate}/working", cwd=cwd):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


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

    The delete is trash-preserving. It is a new trash entry of its own: in one
    ref transaction both tips are pinned under ``refs/haute/trash/<entry>/``
    (shielding the objects from gc) and the pair's refs removed, then a tombstone
    lands in ``.haute/trash.json``, so ``undelete_working_pair`` can rebuild the
    pair exactly and a name deleted twice keeps both. The oldest entries beyond
    the cap then go, pins with them. All of it is one recorded operation, whose
    commit point is the transaction, or the checkout that discarded changes."""
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

    from haute._git_state import read_trash, trash_entry_pins, trash_overflow

    entry_id = _new_trash_id(working, project_root, cwd)
    working_pin = f"refs/haute/trash/{entry_id}/working"
    ledger_pin = f"refs/haute/trash/{entry_id}/ledger"
    entry: dict[str, object] = {
        "branch": working,
        "branch_tip": working_tip,
        "ledger_tip": ledger_tip,
        "working_pin": working_pin,
        "ledger_pin": ledger_pin,
        "was_archived": working.startswith(f"{_ARCHIVE_PREFIX}/"),
        "deleted_at": datetime.now(UTC).isoformat(),
    }

    deletion = Operation("delete", project_root, cwd)
    # A confirmed delete is destructive by intent — discard a dirty tree along
    # with the branch (S38). Without confirmation a dirty tree refuses.
    discarded = _leave_if_active(
        deletion, working, ledger, project_root, "deleting", cwd=cwd, discard=confirm
    )
    # The recovery net and the removal in one transaction: both, or neither.
    removal: dict[str, str | None] = {
        working_pin: working_tip,
        f"refs/heads/{working}": None,
    }
    if ledger_tip is not None:
        removal[ledger_pin] = ledger_tip
        removal[f"refs/heads/{ledger}"] = None
    deletion.refs(removal)
    if not discarded:
        deletion.committed()
    deletion.trash(entry_id, entry)
    # Past the cap the oldest tombstones go, and their pins: nothing could restore them.
    dropped = trash_overflow(read_trash(project_root), entry_id)
    for dropped_id, _dropped in dropped:
        deletion.trash(dropped_id, None)
    deletion.refs(
        {
            pin: None
            for dropped_id, dropped_entry in dropped
            for pin in trash_entry_pins(dropped_id, dropped_entry)
        }
    )
    deletion.run()

    logger.info("working_pair_deleted", working=working, entry=entry_id, confirmed=confirm)
    return GitDeleteBranchResponse(status="deleted", branch=working, trash_id=entry_id)


@_serialized_mutation
def undelete_working_pair(
    entry_id: str,
    project_root: Path,
    as_name: str | None = None,
    cwd: Path | None = None,
) -> GitUndeleteResponse:
    """Restore a deleted working pair from its trash entry — the inverse of
    delete_working_pair's recovery net — under its branch name or *as_name*.

    Pure ref/state ops (no checkout, no HEAD movement): the working and ledger
    refs are created at their recorded tips and the entry's pins deleted in one
    ref transaction, the operation's commit point, then the tombstone is
    consumed. The restored pair is NOT adopted as the working branch; the user
    switches to it deliberately.

    Domain errors (verbatim to the client): no such entry, the name already
    taken by either ref, or the recorded commit no longer exists (tombstones can
    outlive their objects if the pins were hand-deleted and gc ran)."""
    from haute._git_state import read_trash, trash_entry_branch, trash_entry_pins

    _assert_git_repo(cwd)
    entry = read_trash(project_root).get(entry_id)
    if entry is None:
        raise GitDomainError("That deleted branch is no longer in the trash.")
    name = as_name.strip() if as_name is not None else trash_entry_branch(entry_id, entry)
    _assert_new_branch_name(name, cwd=cwd)
    _validate_ref_name(name)
    working = _normalize_to_working(name)
    _assert_eligible_working(working)

    ledger = ledger_name(working)
    for taken in (working, ledger):
        if _rev_parse(taken, cwd=cwd) is not None:
            raise GitDomainError(
                f"A branch named '{taken}' already exists. Restore it under another name."
            )

    branch_tip = entry.get("branch_tip")
    if not isinstance(branch_tip, str) or _rev_parse(branch_tip, cwd=cwd) is None:
        raise GitDomainError(
            f"'{trash_entry_branch(entry_id, entry)}' can no longer be restored — "
            "its recorded commit is gone."
        )
    ledger_tip = entry.get("ledger_tip")
    if not (isinstance(ledger_tip, str) and _rev_parse(ledger_tip, cwd=cwd) is not None):
        ledger_tip = None  # pair deleted before its ledger ever spawned

    working_pin, ledger_pin = trash_entry_pins(entry_id, entry)
    rebuilt: dict[str, str | None] = {
        f"refs/heads/{working}": branch_tip,
        working_pin: None,
        ledger_pin: None,
    }
    if ledger_tip is not None:
        rebuilt[f"refs/heads/{ledger}"] = ledger_tip
    undelete = Operation("undelete", project_root, cwd)
    undelete.refs(rebuilt)
    undelete.committed()
    undelete.trash(entry_id, None)
    undelete.run()
    logger.info("working_pair_undeleted", working=working, entry=entry_id)
    return GitUndeleteResponse(status="restored", branch=working)


@_serialized_mutation
def restore_working_pair(
    branch: str, project_root: Path, cwd: Path | None = None
) -> GitRestoreResponse:
    """Un-archive a pair: move ``archive/<X>`` → ``<X>`` and its ledger back (the
    inverse of archive_working_pair) in one ref transaction. Bidirectional
    (accepts either archived name); refuses if a live branch already occupies
    either restored name."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    archived_working = _normalize_to_working(branch)
    prefix = f"{_ARCHIVE_PREFIX}/"
    if not archived_working.startswith(prefix):
        raise GitDomainError(f"'{archived_working}' is not an archived branch.")
    working_tip = _rev_parse(archived_working, cwd=cwd)
    if working_tip is None:
        raise GitDomainError(f"Branch '{archived_working}' does not exist.")

    restored = archived_working[len(prefix) :]
    _assert_eligible_working(restored)
    if _rev_parse(restored, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{restored}' already exists.")
    restored_ledger = ledger_name(restored)
    if _rev_parse(restored_ledger, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{restored_ledger}' already exists.")
    in_the_way = _branch_in_the_way(restored, cwd)
    if in_the_way is not None:
        raise GitDomainError(
            f"Cannot restore '{restored}': {_clash_words(restored, in_the_way)}. "
            f"Delete '{in_the_way}' first: Deleted keeps it, to restore under another name."
        )

    ledger_tip = _rev_parse(ledger_name(archived_working), cwd=cwd)
    unarchive = Operation("unarchive", project_root, cwd)
    unarchive.refs(_moved_pair(archived_working, restored, working_tip, ledger_tip))
    unarchive.run()
    logger.info("working_pair_restored", restored=restored)
    return GitRestoreResponse(restored_as=restored)


@_serialized_mutation
def repair_working_pair(
    ledger: str, project_root: Path, cwd: Path | None = None
) -> GitRepairPairResponse:
    """Make whole a pair a crash split, as readiness lists it in ``broken_pairs``.

    A half-archived pair's archive is finished: the ledger moves beside its archived
    working branch. An orphan ledger gets its working branch back at the ledger's last
    milestone, the saves since then pending again for the next milestone. Each is one
    ref transaction; HEAD, the files and clone state are left as they are."""
    _assert_git_repo(cwd)
    _validate_ref_name(ledger)
    _, tips = _list_branches_with_tips(cwd=cwd)
    pair = _broken_pair(ledger, tips, cwd)
    if pair is None:
        raise GitDomainError(f"'{ledger}' is not part of a broken branch.")
    ledger_tip = tips[ledger]
    repair = Operation("repair", project_root, cwd)
    if pair.kind == "half-archived":
        archived_ledger = ledger_name(pair.working)
        repair.refs({f"refs/heads/{archived_ledger}": ledger_tip, f"refs/heads/{ledger}": None})
        repair.run()
        logger.info("working_pair_repaired", kind=pair.kind, working=pair.working)
        return GitRepairPairResponse(working=pair.working, ledger=archived_ledger)
    milestone = _ledger_last_milestone(ledger, tips, cwd)
    repair.refs({f"refs/heads/{pair.working}": milestone})
    repair.run()
    logger.info("working_pair_repaired", kind=pair.kind, working=pair.working, at=milestone[:8])
    return GitRepairPairResponse(working=pair.working, ledger=ledger)


def _ledger_last_milestone(ledger: str, tips: dict[str, str], cwd: Path | None) -> str:
    """The commit *ledger*'s saves were recorded on.

    Walking its first-parent chain back from the tip: the first commit that is a merge (a
    milestone, or the anchoring milestone of a branch started at a save), has no parent,
    or lies on the first-parent chain of a local branch that is not a save ledger (where a
    branch started on the default branch). A save is none of these: one parent, and on no
    branch's chain but a ledger's (another ledger shares them after Spin off a copy)."""
    others = "".join(f"{tip}\n" for name, tip in tips.items() if working_name(name) is None)
    on_other_chains = (
        set(_run_git("rev-list", "--first-parent", "--stdin", cwd=cwd, input_text=others).split())
        if others
        else set()
    )
    chain = _run_git("rev-list", "--first-parent", "--parents", tips[ledger], cwd=cwd)
    for line in chain.splitlines():
        sha, *parents = line.split()
        if len(parents) != 1 or sha in on_other_chains:
            return sha
    raise GitError(f"the first-parent chain of {ledger} ends without a root")
