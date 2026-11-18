"""Recorded git operations: every ref Haute moves moves as a step of one.

An operation is built from its steps once its refusals are checked and the commit
objects it needs are made. Before its first step it writes what it is about to do to
``.haute/operation.json``: every slot it changes (a ref, HEAD, the working branch the
clone records, a trash tombstone) with its value before the first step and after each
step, the files it writes, and its commit point, the step after which it goes forward
and not back. It removes the record after its last step.

A failure it catches is put back from the record before the call returns, unless the
operation had reached its commit point; then, and after a stop it cannot catch (the
process killed), the record stays. Readiness reads a record that is there as an
interruption, every other git action refuses, and Retry finishes the operation or puts
it back. :func:`recover` is the one routine that reads a record against the refs, for
the caught failure and for Retry alike: it acts on the commits the record holds, never
on what the code noted along the way.
"""

from __future__ import annotations

import contextvars
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from haute._git_core import (
    GitDomainError,
    GitError,
    GitFilesInUseError,
    GitInterruptedError,
    GitTransactionError,
    _held_files,
    _ref_moves_permitted,
    _rewrite_tree,
    _run_git,
    _run_git_capture,
    _run_git_ok,
    _tracked_changes,
    _wipe_volatile_artefacts,
)
from haute._git_state import (
    OPERATIONS,
    OperationRecord,
    clear_operation,
    clear_working_branch,
    name_files,
    read_operation,
    read_trash,
    read_working_branch,
    record_trash,
    remove_trash,
    write_operation,
    write_working_branch,
)
from haute._logging import get_logger
from haute._project_context import current_project

logger = get_logger(component="git_operation")

__all__ = ["Operation", "recover"]

#: The slot for where HEAD is: ``"ref: <full ref>"``, or a commit when detached.
HEAD = "HEAD"
#: The slot for the working branch ``.haute/state.json`` records.
BRANCH = "branch"
_TRASH = "trash:"
_SYMBOLIC = "ref: "
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_StepKind = Literal["refs", "checkout", "fast-forward", "files", "head", "branch", "trash", "call"]


@dataclass(frozen=True, slots=True)
class _Step:
    """One step: the slots it sets, and for a ref transaction the values it expects."""

    kind: _StepKind
    sets: dict[str, object]
    expects: dict[str, object]
    force: bool = False
    fn: Callable[[], None] | None = None


# Set while an operation runs in this context: operations do not nest.
_RUNNING: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "haute_git_operation_running", default=False
)


class Operation:
    """The steps of one git action that moves a ref, run with its record.

    Each step reads the value its slots have after the steps before it, from the live
    repository for a slot no earlier step set, and is dropped when it would change
    nothing. :meth:`run` writes the record, performs the steps and removes it."""

    def __init__(
        self,
        name: str,
        project_root: Path,
        cwd: Path | None = None,
        *,
        wipes_caches: bool = False,
    ) -> None:
        if name not in OPERATIONS:
            raise ValueError(f"{name!r} is not an operation a record can name")
        self.name = name
        self._root = project_root
        self._cwd = cwd
        self._wipes = wipes_caches
        self._seen: dict[str, object] = {}
        self._initial: dict[str, object] = {}
        self._current: dict[str, object] = {}
        self._steps: list[_Step] = []
        self._commit_point: int | None = None
        self._files: list[str] = []

    # -- the state the steps so far leave ---------------------------------------------

    def value(self, slot: str) -> object:
        """*slot*'s value after the steps added so far. Only a slot a step sets enters
        the record."""
        if slot in self._current:
            return self._current[slot]
        if slot not in self._seen:
            self._seen[slot] = _read_slot(slot, self._root, self._cwd)
        return self._seen[slot]

    def ref(self, name: str) -> str | None:
        """The commit the full ref *name* is at after the steps added so far."""
        value = self.value(name)
        assert value is None or isinstance(value, str)
        return value

    def _add(
        self,
        kind: _StepKind,
        sets: dict[str, object],
        *,
        force: bool = False,
        fn: Callable[[], None] | None = None,
    ) -> None:
        expects = {slot: self.value(slot) for slot in sets}
        changes = {slot: value for slot, value in sets.items() if expects[slot] != value}
        if not changes and kind not in ("files", "call"):
            return
        self._steps.append(
            _Step(kind, changes, {slot: expects[slot] for slot in changes}, force, fn)
        )
        for slot in changes:
            self._initial.setdefault(slot, expects[slot])
        self._current.update(changes)

    # -- the steps ----------------------------------------------------------------------

    def refs(self, updates: dict[str, str | None]) -> None:
        """Set each full ref name to a commit, or remove it (None), in one transaction."""
        for ref in updates:
            if ref == HEAD or not ref.startswith("refs/") or ref.startswith("refs/remotes/"):
                raise ValueError(f"{ref!r} is not a ref an operation moves")
        self._add("refs", dict(updates))

    def checkout(self, branch: str, *, force: bool = False) -> None:
        """Check out *branch*, writing its files."""
        self._add("checkout", {HEAD: f"{_SYMBOLIC}refs/heads/{branch}"}, force=force)

    def checkout_detached(self, commit: str, *, force: bool = False) -> None:
        """Detach HEAD at *commit*, writing its files; *force* discards changes."""
        self._add("checkout", {HEAD: commit}, force=force)

    def fast_forward(self, commit: str) -> None:
        """Fast-forward the branch HEAD is on to *commit*, writing its files."""
        head = self.value(HEAD)
        if not (isinstance(head, str) and head.startswith(_SYMBOLIC)):
            raise ValueError("a fast-forward needs HEAD on a branch")
        self._add("fast-forward", {head.removeprefix(_SYMBOLIC): commit})

    def write_files(self) -> None:
        """Write the operation's files from HEAD (a restore, once its ledger moved)."""
        self._add("files", {})

    def head(self, ref: str) -> None:
        """Point HEAD at the full ref *ref* without writing any file."""
        self._add("head", {HEAD: f"{_SYMBOLIC}{ref}"})

    def working_branch(self, name: str | None) -> None:
        """Record *name* as the clone's working branch, or none."""
        self._add("branch", {BRANCH: name})

    def trash(self, entry_id: str, entry: dict[str, object] | None) -> None:
        """Add a deleted branch's tombstone, or remove it (None)."""
        self._add("trash", {f"{_TRASH}{entry_id}": entry})

    def call(self, fn: Callable[[], None]) -> None:
        """Run *fn* between two steps (the project's hooks); it sets no slot."""
        self._add("call", {}, fn=fn)

    def committed(self) -> None:
        """Put the commit point after the steps added so far: past it, the operation goes
        forward and not back."""
        if not self._steps or not self._steps[-1].sets:
            raise ValueError("the commit point follows a step that sets a slot")
        self._commit_point = len(self._steps)

    # -- running ----------------------------------------------------------------------

    @property
    def step_count(self) -> int:
        """How many steps have been added (a step that changes nothing is not)."""
        return len(self._steps)

    def run(self) -> None:
        """Write the record, perform the steps, and remove the record.

        An ``Exception`` from a step leaves that step unfinished, whatever its git command
        did before failing: :func:`recover` puts the operation back when that step comes
        before the commit point, and keeps the record when it does not. The failure is
        re-raised, from what ``recover`` raised when it could not put back. Any other stop
        leaves the record."""
        if not any(step.sets for step in self._steps):
            return
        if self._steps[-1].kind == "call":
            # Only the refs show how far an operation got, and a call sets none: one
            # at the end could not be told done from not done.
            raise ValueError("a call step needs a step that sets a slot after it")
        if _RUNNING.get():
            raise RuntimeError("a git operation is already running in this thread")
        existing = read_operation(self._root)
        if existing is not None:
            raise GitInterruptedError(existing)
        self._files = self._written_files()
        write_operation(self._root, self._record())
        logger.info("git_operation_started", operation=self.name, steps=len(self._steps))
        running = _RUNNING.set(True)
        failed_step = 0
        try:
            for failed_step, step in enumerate(self._steps):
                _perform(step, self._files, self._root, self._cwd)
        except Exception as failure:
            try:
                recover(self._root, self._cwd, failed_step=failed_step)
            except (GitError, OSError) as unrecovered:
                raise unrecovered from failure
            raise
        finally:
            _RUNNING.reset(running)
        if self._wipes:
            _wipe_volatile_artefacts(_worktree(self._cwd))
        clear_operation(self._root)
        logger.info("git_operation_finished", operation=self.name)

    def _record(self) -> OperationRecord:
        return OperationRecord(
            operation=self.name,
            at=datetime.now(UTC).isoformat(),
            initial=dict(self._initial),
            steps=[dict(step.sets) for step in self._steps],
            commit_point=len(self._steps) if self._commit_point is None else self._commit_point,
            files=self._files,
            wipes_caches=self._wipes,
        )

    def _written_files(self) -> list[str]:
        """Every path that differs between HEAD's first commit and its commit after any
        step, less those not in step now: the analyst's tracked changes, and a path the
        first commit lacks that is on disk. Those are the analyst's, never the record's."""
        states = _states(self._initial, [step.sets for step in self._steps])
        commits = [_head_commit(state, self._cwd) for state in states]
        first = commits[0] or _EMPTY_TREE
        changed: dict[str, bool] = {}
        for commit in dict.fromkeys(commits[1:]):
            if commit == commits[0]:
                continue
            for status, path in _diff_status(first, commit or _EMPTY_TREE, self._cwd):
                changed[path] = changed.get(path, False) or status == "A"
        if not changed:
            return []
        root = _worktree(self._cwd)
        not_in_step = _tracked_changes(self._cwd)
        return sorted(
            path
            for path, added in changed.items()
            if path not in not_in_step and not (added and os.path.lexists(root / path))
        )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _perform(step: _Step, files: list[str], project_root: Path, cwd: Path | None) -> None:
    """Perform one step; ref moves are permitted only here and in :func:`recover`."""
    with _ref_moves_permitted():
        if step.kind == "refs":
            _ref_transaction({ref: (step.expects[ref], new) for ref, new in step.sets.items()}, cwd)
        elif step.kind == "checkout":
            target = step.sets[HEAD]
            assert isinstance(target, str)
            force = ("-f",) if step.force else ()
            if target.startswith(_SYMBOLIC):
                branch = target.removeprefix(_SYMBOLIC).removeprefix("refs/heads/")
                _rewrite_tree("checkout", *force, branch, cwd=cwd)
            else:
                _rewrite_tree("checkout", *force, "--detach", target, cwd=cwd)
        elif step.kind == "fast-forward":
            (commit,) = step.sets.values()
            assert isinstance(commit, str)
            _rewrite_tree("merge", "--ff-only", commit, cwd=cwd)
        elif step.kind == "files":
            _write_files_from_head(files, cwd, verify=True)
        elif step.kind == "head":
            target = step.sets[HEAD]
            assert isinstance(target, str)
            _run_git("symbolic-ref", HEAD, target.removeprefix(_SYMBOLIC), cwd=cwd)
        elif step.kind == "branch":
            _set_working_branch(step.sets[BRANCH], project_root)
        elif step.kind == "trash":
            for slot, entry in step.sets.items():
                _set_trash(slot, entry, project_root)
        else:
            assert step.fn is not None
            step.fn()


def _ref_transaction(updates: dict[str, tuple[object, object]], cwd: Path | None) -> None:
    """One ``update-ref --stdin`` transaction: each ref from its expected value to its new
    one (None for no ref), all or nothing."""
    lines = []
    for ref, (old, new) in updates.items():
        if new is None:
            lines.append(f"delete {ref} {old}")
        elif old is None:
            lines.append(f"create {ref} {new}")
        else:
            lines.append(f"update {ref} {new} {old}")
    _run_git("update-ref", "--stdin", cwd=cwd, input_text="".join(f"{line}\n" for line in lines))


def _worktree(cwd: Path | None) -> Path:
    """The repository's top level, where the paths git reports start."""
    return cwd or current_project().root


def _set_working_branch(name: object, project_root: Path) -> None:
    if name is None:
        clear_working_branch(project_root)
    else:
        assert isinstance(name, str)
        write_working_branch(project_root, name)


def _set_trash(slot: str, entry: object, project_root: Path) -> None:
    entry_id = slot.removeprefix(_TRASH)
    if entry is None:
        remove_trash(project_root, entry_id)
    else:
        assert isinstance(entry, dict)
        record_trash(project_root, entry_id, entry)


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


def _read_slot(slot: str, project_root: Path, cwd: Path | None) -> object:
    """*slot*'s live value."""
    if slot == HEAD:
        return _head_value(cwd)
    if slot == BRANCH:
        return read_working_branch(project_root)
    if slot.startswith(_TRASH):
        return read_trash(project_root).get(slot.removeprefix(_TRASH))
    return _ref_value(slot, cwd)


def _head_value(cwd: Path | None) -> str | None:
    attached, ref = _run_git_ok("symbolic-ref", "-q", HEAD, cwd=cwd)
    return f"{_SYMBOLIC}{ref}" if attached else _ref_value(HEAD, cwd)


def _ref_value(ref: str, cwd: Path | None) -> str | None:
    ok, value = _run_git_ok("rev-parse", "--verify", "--quiet", ref, cwd=cwd)
    return value if ok and value else None


def _states(initial: dict[str, object], steps: list[dict[str, object]]) -> list[dict[str, object]]:
    """The state before the first step and after each: *initial* overlaid with the steps."""
    states = [dict(initial)]
    for sets in steps:
        states.append({**states[-1], **sets})
    return states


def _head_commit(state: dict[str, object], cwd: Path | None) -> str | None:
    """The commit HEAD is at in *state*, reading a slot the operation does not change
    from the repository; None for an unborn HEAD."""
    head = state[HEAD] if HEAD in state else _head_value(cwd)
    if not isinstance(head, str):
        return None
    if not head.startswith(_SYMBOLIC):
        return head
    ref = head.removeprefix(_SYMBOLIC)
    value = state[ref] if ref in state else _ref_value(ref, cwd)
    return value if isinstance(value, str) else None


def _diff_status(old: str, new: str, cwd: Path | None) -> list[tuple[str, str]]:
    """(status letter, path) for each path that differs between two trees or commits."""
    raw = _run_git("diff", "--name-status", "--no-renames", "-z", old, new, "--", cwd=cwd)
    tokens = [token for token in raw.split("\0") if token]
    return [(tokens[i][0], tokens[i + 1]) for i in range(0, len(tokens) - 1, 2)]


def _blobs(commit: str | None, cwd: Path | None) -> dict[str, str]:
    """Path → blob of every file in *commit* (none for an unborn HEAD)."""
    if commit is None:
        return {}
    raw = _run_git("ls-tree", "-r", "-z", "--full-tree", commit, cwd=cwd)
    blobs: dict[str, str] = {}
    for entry in raw.split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            blobs[path] = meta.split()[2]
    return blobs


def _indexed(cwd: Path | None) -> set[str]:
    return {path for path in _run_git("ls-files", "-z", cwd=cwd).split("\0") if path}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _write_files_from_head(files: list[str], cwd: Path | None, *, verify: bool) -> None:
    """Bring *files* to HEAD: written from it where the index or HEAD's tree has them,
    removed where neither does (a leftover of the operation). With *verify*, files not
    then in step raise ``GitFilesInUseError``."""
    if not files:
        return
    root = _worktree(cwd)
    # An unborn HEAD (a clone whose remote names no branch it has) holds the empty tree:
    # restoring from it removes the files, as git restore does any its source lacks.
    head = _head_commit({}, cwd)
    known = _indexed(cwd) | set(_blobs(head, cwd))
    stderr = ""
    tracked = [path for path in files if path in known]
    if tracked:
        code, stderr = _run_git_capture(
            "restore",
            f"--source={head or _EMPTY_TREE}",
            "--staged",
            "--worktree",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            cwd=cwd,
            input_text="".join(f":(literal){path}\0" for path in tracked),
        )
        if code != 0:
            # Held files are the usual cause; whatever it was, the check below names the
            # files left, and the log keeps git's own words.
            logger.warning("git_restore_failed", files=len(tracked), stderr=stderr)
    for path in files:
        if path not in known and os.path.lexists(root / path):
            try:
                (root / path).unlink()
            except OSError as exc:
                logger.warning("git_leftover_not_removed", path=path, error=str(exc))
    if verify:
        left = _files_out_of_step(files, cwd)
        if left:
            raise GitFilesInUseError(_held_files(stderr) or left)


def _files_out_of_step(files: list[str], cwd: Path | None) -> list[str]:
    """Those of *files* not as HEAD has them: changed where HEAD's tree has them, indexed
    or on disk where it does not."""
    root = _worktree(cwd)
    in_head = set(_blobs(_head_commit({}, cwd), cwd))
    indexed = _indexed(cwd)
    changed = _tracked_changes(cwd)

    def in_step(path: str) -> bool:
        on_disk = os.path.lexists(root / path)
        if path in in_head:
            return on_disk and path not in changed
        return not on_disk and path not in indexed

    return [path for path in files if not in_step(path)]


def _changed_since(files: list[str], commits: list[str | None], cwd: Path | None) -> list[str]:
    """Those of *files* on disk holding no version any of *commits* gives them: changed
    by someone after the operation stopped. A file git cannot read is left out."""
    root = _worktree(cwd)
    present = [path for path in files if os.path.isfile(root / path)]
    if not present:
        return []
    versions: dict[str, set[str | None]] = {path: set() for path in present}
    for commit in dict.fromkeys(commits):
        blobs = _blobs(commit, cwd)
        for path in present:
            versions[path].add(blobs.get(path))
    hashes = _hash_files(present, cwd)
    return [path for path in present if path in hashes and hashes[path] not in versions[path]]


def _hash_files(paths: list[str], cwd: Path | None) -> dict[str, str]:
    """Path → the blob git would record for it, with its path's filters; a file git
    cannot read is left out."""
    try:
        out = _run_git("hash-object", "--stdin-paths", cwd=cwd, input_text="\n".join(paths))
    except GitError:
        out = ""
    if len(out.split()) == len(paths):
        return dict(zip(paths, out.split(), strict=True))
    hashes: dict[str, str] = {}
    for path in paths:
        ok, out = _run_git_ok("hash-object", "--", path, cwd=cwd)
        if ok and out:
            hashes[path] = out
    return hashes


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def recover(project_root: Path, cwd: Path | None, *, failed_step: int | None = None) -> bool:
    """Finish the recorded operation or put it back, from its record and the refs as they
    are, and remove the record. Return whether there was one to act on.

    The live slots must equal the state after some step; with none, the refs moved after
    the operation stopped (``GitTransactionError``). For Retry the refs decide: the last
    step they match at or past the commit point goes forward to the last step, and before
    it back to the first state. For a caught failure, *failed_step* is the step that
    raised, unfinished whatever its git command did first: before the commit point the
    operation goes back, and at or past it the record is kept. A file that holds neither
    version the operation knew is refused by name; files not in step afterwards raise
    ``GitFilesInUseError``. Either way the record stays."""
    record = read_operation(project_root)
    if record is None:
        return False
    if not record.readable:
        raise GitTransactionError(
            "A git action stopped part-way and its record in .haute/operation.json cannot "
            "be read. Inspect the repository before retrying."
        )
    states = _states(record.initial, record.steps)
    live = {slot: _read_slot(slot, project_root, cwd) for slot in record.initial}
    reached = [k for k, state in enumerate(states) if all(state[s] == live[s] for s in live)]
    if not reached:
        raise GitTransactionError(
            f"{record.words} stopped part-way, and its branches have moved since. "
            "Inspect the repository before retrying."
        )
    if failed_step is not None and failed_step >= record.commit_point:
        logger.warning("git_operation_kept", operation=record.operation, step=failed_step)
        return False
    forward = failed_step is None and max(reached) >= record.commit_point
    target = states[-1] if forward else states[0]

    changed = _changed_since(record.files, [_head_commit(s, cwd) for s in states], cwd)
    if changed:
        raise GitDomainError(
            f"{name_files(changed)} changed after {record.words.lower()} stopped. "
            "Move the changes aside, then choose Retry."
        )
    unfinished = GitTransactionError(
        f"{record.words} could not be {'finished' if forward else 'put back'}. "
        "Inspect the repository before retrying."
    )
    try:
        with _ref_moves_permitted():
            _apply(target, live, project_root, cwd)
    except (GitError, OSError) as refused:
        raise unfinished from refused
    _write_files_from_head(record.files, cwd, verify=True)
    if any(_read_slot(slot, project_root, cwd) != target[slot] for slot in live):
        raise unfinished
    if forward and record.wipes_caches:
        _wipe_volatile_artefacts(_worktree(cwd))
    clear_operation(project_root)
    logger.info(
        "git_operation_recovered",
        operation=record.operation,
        direction="forward" if forward else "back",
    )
    return True


def _apply(
    target: dict[str, object], live: dict[str, object], project_root: Path, cwd: Path | None
) -> None:
    """Set every slot to *target*: the refs in one transaction expecting *live*, then HEAD,
    the recorded working branch and the tombstones."""
    refs = {
        slot: (live[slot], value)
        for slot, value in target.items()
        if slot.startswith("refs/") and live[slot] != value
    }
    if refs:
        _ref_transaction(refs, cwd)
    head = target.get(HEAD)
    if HEAD in target and live[HEAD] != head:
        assert isinstance(head, str)
        if head.startswith(_SYMBOLIC):
            _run_git("symbolic-ref", HEAD, head.removeprefix(_SYMBOLIC), cwd=cwd)
        else:
            _run_git("update-ref", "--no-deref", HEAD, head, cwd=cwd)
    if BRANCH in target and live[BRANCH] != target[BRANCH]:
        if target[BRANCH] is None:
            clear_working_branch(project_root)
        else:
            branch = target[BRANCH]
            assert isinstance(branch, str)
            write_working_branch(project_root, branch)
    for slot, value in target.items():
        if slot.startswith(_TRASH) and live[slot] != value:
            entry_id = slot.removeprefix(_TRASH)
            if value is None:
                remove_trash(project_root, entry_id)
            else:
                assert isinstance(value, dict)
                record_trash(project_root, entry_id, value)
