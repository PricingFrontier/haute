"""Recorded git operations: every action that moves a ref writes its intent first.

``OPERATIONS`` is one table of every operation that moves a ref, each arranged in a
fresh repository. For each, the process is stopped after each step in turn and each
step is failed in turn; the record must say where the refs are, readiness must name
the stop, and Retry (or the caught failure's put-back) must leave the operation
finished or as it was. A child process killed with ``os._exit`` leaves what the
in-process stop leaves. The runners refuse a ref move outside an operation's step.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import haute._git as haute_git
import haute._git_core as git_core
import haute._git_operation as git_operation
from haute._git import (
    GitDomainError,
    GitError,
    GitInterruptedError,
    GitTransactionError,
    archive_working_pair,
    commit_milestone,
    commit_save,
    create_working_branch,
    delete_working_pair,
    fast_forward_pair,
    push_working_pair,
    resolve_ledger,
    restore_version,
    retry_interrupted,
    set_working_branch,
    working_branch_status,
)
from haute._git_core import GitFilesInUseError
from haute._git_state import (
    OperationRecord,
    interruption_message,
    read_operation,
    record_trash,
    write_working_branch,
)
from tests._git_engine_helpers import LEDGER, WORKING, _git, _write_and_save, seen_now
from tests._git_helpers import install_hook
from tests._source_files import source_files

ROOT = "<root>"
# A delete's confirmation: the branch (the call's first argument) as listed just before it.
SEEN = "<seen>"
SRC = Path(__file__).resolve().parents[1] / "src" / "haute"


@pytest.fixture(autouse=True)
def _fixed_commit_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same arrangement makes the same commits in every repository, so a stop in
    one can be compared with a stop in its twin."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-11-18T12:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-11-18T12:00:00+00:00")


# ---------------------------------------------------------------------------
# Arrangements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Call:
    """A public git action, with ``ROOT`` standing for the repository it runs in."""

    fn: str
    args: tuple[object, ...] = ()
    kwargs: dict[str, object] = field(default_factory=dict)

    def run(self, root: Path) -> object:
        def at(value: object) -> object:
            if value == SEEN:
                return seen_now(root, str(self.args[0]))
            return root if value == ROOT else value

        action = getattr(haute_git, self.fn)
        return action(*map(at, self.args), **{key: at(v) for key, v in self.kwargs.items()})

    def spec(self, root: Path) -> dict[str, object]:
        """What a child process needs to run the same call."""
        return {"root": str(root), "fn": self.fn, "args": self.args, "kwargs": self.kwargs}


def _write(root: Path, path: str, text: str) -> None:
    """Write *text* to *path* under the repository *root* (a test's own sandbox)."""
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def _new_repo(base: Path) -> Path:
    root = base / "repo"
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test Actuary")
    _git(root, "config", "user.email", "test@example.com")
    (root / "rating.py").write_text("# pipeline\n")
    (root / "utility").mkdir()
    (root / "utility" / "old_rates.py").write_text("RATE = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial pipeline")
    _git(root, "checkout", "-q", "-b", WORKING)
    return root


def _pair(root: Path) -> str:
    """Adopt the working branch and land two saves: the second changes, deletes and adds
    a file. Returns the first save."""
    set_working_branch(WORKING, root, cwd=root)
    first = _write_and_save(
        root, WORKING, {"rating.py": "# v1\n", "keep.txt": "keep\n", "gone.txt": "old\n"}
    )
    (root / "rating.py").write_text("# v2\n")
    (root / "added.txt").write_text("new\n")
    (root / "gone.txt").unlink()
    assert commit_save(["rating.py", "added.txt", "gone.txt"], WORKING, cwd=root)
    assert first is not None
    return first


def _milestone(root: Path) -> None:
    commit_milestone("M1", root, cwd=root)


def _remote(root: Path, base: Path) -> Path:
    bare = base / "origin.git"
    _git(root, "init", "-q", "--bare", str(bare))
    _git(root, "remote", "add", "origin", str(bare))
    push_working_pair("origin", root, cwd=root)
    return bare


def _colleague(
    bare: Path,
    base: Path,
    change: Callable[[Path], list[str]],
    *,
    milestone: bool = True,
    label: str | None = None,
) -> None:
    """A colleague's clone makes *change*, saves it (as a milestone unless not, with
    *label* as its version label) and pushes."""
    other = base / "other"
    _git(base, "clone", "-q", str(bare), str(other))
    _git(other, "config", "user.name", "Colleague")
    _git(other, "config", "user.email", "colleague@example.com")
    _git(other, "checkout", "-q", WORKING)
    write_working_branch(other, WORKING)
    resolve_ledger(WORKING, cwd=other)
    assert commit_save(change(other), WORKING, cwd=other)
    if milestone:
        commit_milestone("Colleague's milestone", other, version_label=label, cwd=other)
    push_working_pair("origin", other, cwd=other)


def _rewrites_rating_and_deletes_a_utility(clone: Path) -> list[str]:
    (clone / "rating.py").write_text("# the colleague's rating\n")
    (clone / "utility" / "old_rates.py").unlink()
    return ["rating.py", "utility/old_rates.py"]


def _arrange_restore(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    first = _pair(root)
    return root, _Call("restore_version", (first, ROOT), {"cwd": ROOT})


def _arrange_catch_up(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    _colleague(_remote(root, base), base, _rewrites_rating_and_deletes_a_utility)
    return root, _Call("fast_forward_pair", ("origin", ROOT), {"cwd": ROOT})


def _arrange_catch_up_labelled(base: Path) -> tuple[Path, _Call]:
    """The colleague's milestone carries a version label, which Catch up brings."""
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    bare = _remote(root, base)
    _colleague(bare, base, _rewrites_rating_and_deletes_a_utility, label="2.0")
    return root, _Call("fast_forward_pair", ("origin", ROOT), {"cwd": ROOT})


def _arrange_catch_up_ledger_only(base: Path) -> tuple[Path, _Call]:
    """Only the ledger is behind: the colleague saved and pushed, with no milestone."""
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    bare = _remote(root, base)
    _colleague(bare, base, _rewrites_rating_and_deletes_a_utility, milestone=False)
    return root, _Call("fast_forward_pair", ("origin", ROOT), {"cwd": ROOT})


def _arrange_select(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    create_working_branch("other", root, cwd=root)
    _write_and_save(root, WORKING, {"rating.py": "# v3\n"})
    return root, _Call("set_working_branch", ("other", ROOT), {"cwd": ROOT})


def _arrange_select_spawning(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _git(root, "branch", "plain", "main")
    return root, _Call("set_working_branch", ("plain", ROOT), {"cwd": ROOT})


def _arrange_open_ledger(base: Path) -> tuple[Path, _Call]:
    return _new_repo(base), _Call("resolve_ledger", (WORKING,), {"cwd": ROOT})


def _arrange_seed(base: Path) -> tuple[Path, _Call]:
    root = base / "repo"
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test Actuary")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "checkout", "-q", "-b", "initial-branch")
    (root / "main.py").write_text("x = 1\n")
    return root, _Call(
        "set_working_branch", ("initial-branch", ROOT), {"create": True, "cwd": ROOT}
    )


def _arrange_create(base: Path) -> tuple[Path, _Call]:
    return _new_repo(base), _Call(
        "set_working_branch", ("fresh", ROOT), {"create": True, "cwd": ROOT}
    )


def _arrange_create_from_detached(base: Path) -> tuple[Path, _Call]:
    """A project left on a save by a checkout outside Haute, kept on a new branch."""
    root = _new_repo(base)
    first = _pair(root)
    _git(root, "checkout", "-q", "--detach", first)
    return root, _Call("set_working_branch", ("kept", ROOT), {"create": True, "cwd": ROOT})


def _arrange_fork(**options: object) -> Callable[[Path], tuple[Path, _Call]]:
    def arrange(base: Path) -> tuple[Path, _Call]:
        root = _new_repo(base)
        _pair(root)
        _milestone(root)
        _write_and_save(root, WORKING, {"rating.py": "# v3\n"})
        return root, _Call("create_working_branch", ("forked", ROOT), {"cwd": ROOT, **options})

    return arrange


def _arrange_spin_off(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    _colleague(_remote(root, base), base, _rewrites_rating_and_deletes_a_utility)
    _write_and_save(root, WORKING, {"local.py": "# local line\n"})
    commit_milestone("Local milestone", root, cwd=root, allow_fork=True)
    return root, _Call("branch_away", ("origin", ROOT), {"cwd": ROOT})


def _arrange_archive(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    return root, _Call("archive_working_pair", (WORKING, ROOT), {"cwd": ROOT})


def _arrange_delete(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    return root, _Call("delete_working_pair", (WORKING, ROOT), {"seen": SEEN, "cwd": ROOT})


def _arrange_delete_confirmed_dirty(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _write(root, "keep.txt", "edited, not saved\n")
    return root, _Call(
        "delete_working_pair", (WORKING, ROOT), {"seen": SEEN, "confirm": True, "cwd": ROOT}
    )


def _arrange_delete_at_the_cap(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    tip = _git(root, "rev-parse", "main")
    for i in range(20):
        entry_id = f"old{i:02d}@20260101T0000{i:02d}Z"
        pins = (f"refs/haute/trash/{entry_id}/working", f"refs/haute/trash/{entry_id}/ledger")
        for pin in pins:
            _git(root, "update-ref", pin, tip)
        record_trash(
            root,
            entry_id,
            {
                "branch": f"old{i:02d}",
                "branch_tip": tip,
                "ledger_tip": tip,
                "working_pin": pins[0],
                "ledger_pin": pins[1],
                "was_archived": False,
                "deleted_at": "2026-01-01T00:00:00+00:00",
            },
        )
    return root, _Call("delete_working_pair", (WORKING, ROOT), {"seen": SEEN, "cwd": ROOT})


def _arrange_undelete(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    entry_id = delete_working_pair(WORKING, root, seen=seen_now(root, WORKING), cwd=root).trash_id
    return root, _Call("undelete_working_pair", (entry_id, ROOT), {"cwd": ROOT})


def _arrange_unarchive(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    archived = archive_working_pair(WORKING, root, cwd=root).archived_as
    return root, _Call("restore_working_pair", (archived, ROOT), {"cwd": ROOT})


def _arrange_repair(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    set_working_branch(WORKING, root, cwd=root)
    _git(root, "branch", "lost-save", "main")
    return root, _Call("repair_working_pair", ("lost-save", ROOT), {"cwd": ROOT})


def _arrange_milestone(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    return root, _Call("merge_to_working", (WORKING, "Labelled"), {"tag_label": "1.0", "cwd": ROOT})


def _arrange_milestone_hooks(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    install_hook(root, "pre-commit", "#!/bin/sh\nexit 0\n")
    return root, _Call("commit_milestone", ("With hooks", ROOT), {"cwd": ROOT})


def _arrange_adopt_clone(base: Path) -> tuple[Path, _Call]:
    root = _new_repo(base)
    _pair(root)
    _milestone(root)
    bare = _remote(root, base)
    clone = base / "clone"
    _git(base, "clone", "-q", str(bare), str(clone))
    return clone, _Call("adopt_cloned_lineage", (WORKING, "origin"), {"cwd": ROOT})


@dataclass(frozen=True)
class _Operation:
    """One row of the table: an operation, its steps and commit point, and how to
    arrange a repository about to run it."""

    id: str
    operation: str
    steps: int
    commit_point: int
    arrange: Callable[[Path], tuple[Path, _Call]]
    # A confirmed delete discards the analyst's edits by design, in its first step.
    discards: bool = False


OPERATIONS = [
    _Operation("restore", "restore", 2, 1, _arrange_restore),
    _Operation("catch-up", "catch-up", 2, 2, _arrange_catch_up),
    _Operation("catch-up-ledger-only", "catch-up", 1, 1, _arrange_catch_up_ledger_only),
    _Operation("catch-up-labelled", "catch-up", 3, 3, _arrange_catch_up_labelled),
    _Operation("select-branch", "select-branch", 2, 2, _arrange_select),
    _Operation("select-branch-spawning", "select-branch", 3, 3, _arrange_select_spawning),
    _Operation("open-ledger", "open-ledger", 2, 2, _arrange_open_ledger),
    _Operation("seed", "seed", 2, 2, _arrange_seed),
    _Operation("create-branch", "create-branch", 3, 3, _arrange_create),
    _Operation("create-from-detached", "create-branch", 3, 3, _arrange_create_from_detached),
    _Operation("fork-parallel", "create-branch", 1, 1, _arrange_fork()),
    _Operation("fork-switch", "create-branch", 3, 3, _arrange_fork(switch=True)),
    _Operation("fork-move", "create-branch", 4, 4, _arrange_fork(move=True)),
    _Operation("spin-off", "spin-off", 3, 3, _arrange_spin_off),
    _Operation("archive", "archive", 3, 3, _arrange_archive),
    _Operation("delete", "delete", 4, 3, _arrange_delete),
    _Operation(
        "delete-confirmed-dirty", "delete", 4, 1, _arrange_delete_confirmed_dirty, discards=True
    ),
    _Operation("delete-at-the-cap", "delete", 6, 3, _arrange_delete_at_the_cap),
    _Operation("undelete", "undelete", 2, 1, _arrange_undelete),
    _Operation("unarchive", "unarchive", 1, 1, _arrange_unarchive),
    _Operation("repair", "repair", 1, 1, _arrange_repair),
    _Operation("milestone", "milestone", 1, 1, _arrange_milestone),
    _Operation("milestone-hooks", "milestone-hooks", 3, 3, _arrange_milestone_hooks),
    _Operation("adopt-clone", "adopt-clone", 2, 2, _arrange_adopt_clone),
]
_BY_ID = {row.id: row for row in OPERATIONS}

STOPS = [
    pytest.param(row, after, id=f"{row.id}-after-{after}")
    for row in OPERATIONS
    for after in range(row.steps + 1)
]
FAILURES = [
    pytest.param(row, failing, id=f"{row.id}-step-{failing}")
    for row in OPERATIONS
    for failing in range(row.steps)
]
# A step whose git command did its work and then failed (the fence finding a file
# git left behind) is as unfinished as one that failed first.
LATE_FAILURES = [
    pytest.param(row, failing, id=f"{row.id}-step-{failing}")
    for row in OPERATIONS
    for failing in range(row.commit_point)
]


# ---------------------------------------------------------------------------
# Stopping and failing an operation part-way
# ---------------------------------------------------------------------------


class _Stopped(BaseException):
    """A stop that is not an error, as a killed process makes: nothing catches it."""


@dataclass
class _Began:
    """The operation's record as first written, and the files as they were when its
    first step was about to run."""

    record: OperationRecord | None = None
    worktree: dict[str, bytes] = field(default_factory=dict)
    untracked: set[str] = field(default_factory=set)
    edited: set[str] = field(default_factory=set)


def _interrupt(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    at: int,
    *,
    fail: bool = False,
    late: bool = False,
    began: _Began | None = None,
) -> None:
    """Stop *operation* once *at* of its steps are done, or with *fail* fail its step
    *at* (from 0) with a caught error, after the step's own work when *late*. What it
    began from goes in *began*."""
    real = git_operation._perform
    done = [0]

    def perform(step: object, files: list[str], project_root: Path, cwd: Path | None) -> None:
        record = read_operation(project_root)
        if record is None or record.operation != operation:
            real(step, files, project_root, cwd)  # type: ignore[arg-type]
            return
        if began is not None and done[0] == 0 and began.record is None:
            began.record = record
            began.worktree = _worktree(project_root)
            began.untracked = _untracked(project_root)
            began.edited = git_operation._tracked_changes(project_root)
        if fail and done[0] == at:
            if late:
                real(step, files, project_root, cwd)  # type: ignore[arg-type]
            raise GitError("injected step failure")
        if not fail and at == 0:
            raise _Stopped
        real(step, files, project_root, cwd)  # type: ignore[arg-type]
        done[0] += 1
        if not fail and done[0] == at:
            raise _Stopped

    monkeypatch.setattr(git_operation, "_perform", perform)


def _states(record: OperationRecord) -> list[dict[str, object]]:
    return git_operation._states(record.initial, record.steps)


def _slots(record: OperationRecord, root: Path) -> dict[str, object]:
    return {slot: git_operation._read_slot(slot, root, root) for slot in record.initial}


def _worktree(root: Path) -> dict[str, bytes]:
    """Every file's content, outside ``.git`` and ``.haute``."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).parts[0] not in (".git", ".haute")
    }


def _untracked(root: Path) -> set[str]:
    listed = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return {path for path in listed.split("\0") if path and not path.startswith(".haute/")}


def _refuse_while_interrupted(root: Path) -> None:
    for action in (
        lambda: commit_milestone("Not now", root, cwd=root),
        lambda: restore_version("HEAD", root, cwd=root),
        lambda: commit_save(["rating.py"], WORKING, cwd=root),
    ):
        with pytest.raises(GitInterruptedError, match="stopped part-way. Choose Retry first."):
            action()


@pytest.mark.parametrize(("row", "after"), STOPS)
def test_a_stop_after_each_step_is_found_and_retry_finishes_or_puts_it_back(
    row: _Operation, after: int, tmp_path: Path
) -> None:
    root, call = row.arrange(tmp_path)
    began = _Began()

    with pytest.MonkeyPatch.context() as patch:
        _interrupt(patch, row.operation, after, began=began)
        with pytest.raises(_Stopped):
            call.run(root)

    record = read_operation(root)
    assert record is not None and record.operation == row.operation
    assert (len(record.steps), record.commit_point) == (row.steps, row.commit_point)
    states = _states(record)
    # The refs, HEAD, the recorded working branch and the tombstones are exactly where
    # the record says this step leaves them.
    assert _slots(record, root) == states[after]
    status = working_branch_status(root, cwd=root)
    assert status.state == "interrupted"
    assert status.errors == [interruption_message(record)]
    assert status.errors[0].startswith(f"{record.words} stopped part-way")
    _refuse_while_interrupted(root)

    retry_interrupted(root, cwd=root)

    forward = after >= record.commit_point
    assert read_operation(root) is None
    assert _slots(record, root) == states[-1 if forward else 0]
    assert git_operation._files_out_of_step(record.files, root) == []
    assert _untracked(root) <= began.untracked
    if not forward:
        assert _worktree(root) == began.worktree
    assert working_branch_status(root, cwd=root).state != "interrupted"


@pytest.mark.parametrize(("row", "failing"), FAILURES)
def test_a_caught_failure_at_each_step_is_put_back_or_kept_past_the_commit_point(
    row: _Operation, failing: int, tmp_path: Path
) -> None:
    root, call = row.arrange(tmp_path)
    began = _Began()

    with pytest.MonkeyPatch.context() as patch:
        _interrupt(patch, row.operation, failing, fail=True, began=began)
        with pytest.raises(GitError, match="injected step failure"):
            call.run(root)

    planned = began.record
    assert planned is not None
    states = _states(planned)
    if failing < planned.commit_point:
        # Put back before the call returned: every slot and file as it was.
        assert read_operation(root) is None
        assert _slots(planned, root) == states[0]
        assert _worktree(root) == began.worktree
        assert git_operation._files_out_of_step(planned.files, root) == []
        return
    # Past the commit point: the steps done are kept, the record stays, and Retry
    # finishes the operation.
    kept = read_operation(root)
    assert kept is not None and kept.steps == planned.steps
    assert _slots(planned, root) == states[failing]
    assert working_branch_status(root, cwd=root).state == "interrupted"

    retry_interrupted(root, cwd=root)

    assert read_operation(root) is None
    assert _slots(planned, root) == states[-1]
    assert git_operation._files_out_of_step(planned.files, root) == []


@pytest.mark.parametrize(("row", "failing"), LATE_FAILURES)
def test_a_step_that_failed_after_its_own_work_before_the_commit_point_is_put_back(
    row: _Operation, failing: int, tmp_path: Path
) -> None:
    root, call = row.arrange(tmp_path)
    began = _Began()

    with pytest.MonkeyPatch.context() as patch:
        _interrupt(patch, row.operation, failing, fail=True, late=True, began=began)
        with pytest.raises(GitError, match="injected step failure"):
            call.run(root)

    planned = began.record
    assert planned is not None
    assert read_operation(root) is None
    assert _slots(planned, root) == _states(planned)[0]
    # Every file as it began, but an edit a confirmed delete's forced checkout discarded.
    discarded = began.edited if row.discards else set()
    worktree = {path: data for path, data in _worktree(root).items() if path not in discarded}
    assert worktree == {
        path: data for path, data in began.worktree.items() if path not in discarded
    }
    assert git_operation._files_out_of_step(planned.files, root) == []


# What a child process runs: the call, killed with os._exit once the operation has
# done *after* of its steps, so nothing after that runs, not even a ``finally``.
_KILL_SCRIPT = """
import json, os, sys
from pathlib import Path

import haute._git as haute_git
import haute._git_operation as git_operation

spec = json.loads(sys.argv[1])
root = Path(spec["root"])
real, done = git_operation._perform, [0]

def perform(step, files, project_root, cwd):
    record = git_operation.read_operation(project_root)
    if record is None or record.operation != spec["operation"]:
        return real(step, files, project_root, cwd)
    if spec["after"] == 0:
        os._exit(9)
    real(step, files, project_root, cwd)
    done[0] += 1
    if done[0] == spec["after"]:
        os._exit(9)

git_operation._perform = perform
def at(value):
    if value == "<seen>":
        from haute.schemas import GitBranchSeen

        branch = spec["args"][0]
        listed = haute_git.working_branches(root, cwd=root).branches
        row = next(b for b in listed if b.name == branch)
        return GitBranchSeen(tip=row.tip, ledger_tip=row.ledger_tip, is_current=row.is_current)
    return root if value == "<root>" else value

kwargs = {key: at(value) for key, value in spec["kwargs"].items()}
getattr(haute_git, spec["fn"])(*map(at, spec["args"]), **kwargs)
sys.exit(0)
"""


def _comparable(record: OperationRecord) -> str:
    """The record without what differs between two runs: when it was written, and the
    timestamp in a deleted branch's trash entry id."""
    text = json.dumps({**record.to_json(), "at": None}, sort_keys=True)
    text = re.sub(r'"deleted_at": "[^"]*"', '"deleted_at": null', text)
    return re.sub(r"@\d{8}T\d{6}Z(-\d+)?", "@<stamp>", text)


@pytest.mark.parametrize(
    ("row_id", "after"),
    [
        ("restore", 1),
        ("catch-up", 1),
        ("spin-off", 2),
        ("delete-confirmed-dirty", 2),
        ("milestone-hooks", 1),
    ],
)
def test_a_killed_process_leaves_what_the_stop_leaves(
    row_id: str, after: int, tmp_path: Path
) -> None:
    row = _BY_ID[row_id]
    stopped, stopped_call = row.arrange(tmp_path / "stopped")
    with pytest.MonkeyPatch.context() as patch:
        _interrupt(patch, row.operation, after)
        with pytest.raises(_Stopped):
            stopped_call.run(stopped)

    killed, killed_call = row.arrange(tmp_path / "killed")
    script = tmp_path / "kill.py"
    script.write_text(_KILL_SCRIPT, encoding="utf-8")
    spec = {**killed_call.spec(killed), "operation": row.operation, "after": after}
    result = subprocess.run(
        [sys.executable, str(script), json.dumps(spec)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert result.returncode == 9, result.stderr

    record, twin = read_operation(killed), read_operation(stopped)
    assert record is not None and twin is not None
    assert _comparable(record) == _comparable(twin)
    assert _slots(record, killed) == _states(record)[after]
    assert _worktree(killed) == _worktree(stopped)

    retry_interrupted(killed, cwd=killed)

    forward = after >= record.commit_point
    assert read_operation(killed) is None
    assert _slots(record, killed) == _states(record)[-1 if forward else 0]


# ---------------------------------------------------------------------------
# What Retry refuses, and what a record never names
# ---------------------------------------------------------------------------


def _stop(root: Path, call: _Call, operation: str, after: int) -> OperationRecord:
    with pytest.MonkeyPatch.context() as patch:
        _interrupt(patch, operation, after)
        with pytest.raises(_Stopped):
            call.run(root)
    record = read_operation(root)
    assert record is not None
    return record


def test_retry_refuses_a_file_changed_after_the_stop_and_keeps_the_record(
    tmp_path: Path,
) -> None:
    root, call = _arrange_restore(tmp_path)
    _stop(root, call, "restore", 1)  # the ledger moved, the files not yet written
    _write(root, "rating.py", "# edited after the server stopped\n")

    with pytest.raises(GitDomainError, match="rating.py changed after restoring a version stopped"):
        retry_interrupted(root, cwd=root)

    assert read_operation(root) is not None
    assert (root / "rating.py").read_text() == "# edited after the server stopped\n"

    (root / "rating.py").unlink()  # moved aside
    retry_interrupted(root, cwd=root)

    assert read_operation(root) is None
    assert (root / "rating.py").read_text() == "# v1\n"


def _interrupt_recovery(monkeypatch: pytest.MonkeyPatch, after: int, *, fail: bool) -> None:
    """Stop recovery, or fail it with a caught error, once *after* of its transitions are
    done."""
    real = git_operation._apply_transition
    done = [0]

    def apply(updates: object, expects: object, project_root: Path, cwd: Path | None) -> None:
        if done[0] == after:
            if fail:
                raise GitError("injected recovery failure")
            raise _Stopped
        real(updates, expects, project_root, cwd)  # type: ignore[arg-type]
        done[0] += 1

    monkeypatch.setattr(git_operation, "_apply_transition", apply)


def _transitions(record: OperationRecord, after: int) -> int:
    """How many transitions Retry makes from a stop after *after* steps."""
    steps = record.steps[after:] if after >= record.commit_point else record.steps[:after]
    return sum(1 for sets in steps if sets)


RECOVERY_STOPS = [
    pytest.param(row, fail, id=f"{row.id}-{'failed' if fail else 'stopped'}")
    for row in OPERATIONS
    for fail in (False, True)
]


@pytest.mark.parametrize(("row", "fail"), RECOVERY_STOPS)
def test_a_recovery_that_itself_stops_leaves_a_state_retry_goes_on_from(
    row: _Operation, fail: bool, tmp_path: Path
) -> None:
    """Recovery moves the slots one recorded step at a time, so stopping or failing it
    between any two leaves a state the record knows, and the next Retry finishes."""
    for after in {row.commit_point - 1, row.commit_point}:
        if not 0 <= after <= row.steps:
            continue
        probe = _stop(*row.arrange(tmp_path / f"probe-{after}"), row.operation, after)
        for transition in range(_transitions(probe, after)):
            root, call = row.arrange(tmp_path / f"{after}-{transition}")
            record = _stop(root, call, row.operation, after)
            states = _states(record)
            with pytest.MonkeyPatch.context() as patch:
                _interrupt_recovery(patch, transition, fail=fail)
                expected = GitTransactionError if fail else _Stopped
                with pytest.raises(expected):
                    retry_interrupted(root, cwd=root)

            assert read_operation(root) is not None
            assert _slots(record, root) in states
            retry_interrupted(root, cwd=root)

            assert read_operation(root) is None
            forward = after >= record.commit_point
            assert _slots(record, root) == states[-1 if forward else 0]
            assert git_operation._files_out_of_step(record.files, root) == []


def test_retry_refuses_a_branch_head_names_that_moved_after_the_stop(tmp_path: Path) -> None:
    """A switch between two existing ledgers changes neither ledger, yet the files come
    from them: the record pins both, so a ledger moved outside Haute is found."""
    root, call = _arrange_select(tmp_path)
    record = _stop(root, call, "select-branch", 1)  # on the other ledger, files written
    assert {f"refs/heads/{LEDGER}", "refs/heads/other-save", "HEAD"} <= set(record.initial)
    _git(root, "update-ref", f"refs/heads/{LEDGER}", _git(root, "rev-parse", "main"))

    with pytest.raises(GitTransactionError, match="Choosing a branch stopped part-way"):
        retry_interrupted(root, cwd=root)

    assert read_operation(root) is not None


def test_a_hook_that_rewrites_a_file_and_refuses_leaves_head_on_the_ledger(
    tmp_path: Path,
) -> None:
    """The hooks' pass moves HEAD and writes no file, so its record names none: a
    formatter's change to a pending file stays the analyst's, and HEAD goes back."""
    root = _new_repo(tmp_path)
    _pair(root)
    install_hook(root, "pre-commit", "#!/bin/sh\necho '# formatted' > rating.py\nexit 1\n")
    stop_root, stop_call = _arrange_milestone_hooks(tmp_path / "stopped")
    assert _stop(stop_root, stop_call, "milestone-hooks", 0).files == []

    with pytest.raises(GitDomainError, match="pre-commit hook refused the milestone"):
        commit_milestone("Rated", root, cwd=root)

    assert _git(root, "symbolic-ref", "--short", "HEAD") == LEDGER
    assert read_operation(root) is None
    assert (root / "rating.py").read_text().strip() == "# formatted"


def test_a_fetch_adds_no_label_and_catch_up_brings_the_colleagues_as_a_step(
    tmp_path: Path,
) -> None:
    root, call = _arrange_catch_up_labelled(tmp_path)
    label = "refs/tags/version/2.0"
    with git_core._fetch_exec_lock:
        assert git_core._fetch_refs("origin", cwd=root)
    assert _git(root, "for-each-ref", "refs/tags/") == ""
    held = f"{git_core.LABELS_NAMESPACE}/origin/version/2.0"
    assert _git(root, "rev-parse", "--verify", held)

    record = _stop(root, call, "catch-up", 3)
    assert record.kinds == ["fast-forward", "refs", "refs"]
    assert record.steps[-1] == {label: _git(root, "rev-parse", held)}
    retry_interrupted(root, cwd=root)

    assert _git(root, "rev-parse", label) == _git(root, "rev-parse", held)
    assert _git(root, "rev-parse", f"{label}^{{commit}}") == _git(root, "rev-parse", WORKING)


@pytest.mark.parametrize("named", [(), (WORKING,)], ids=["everything", "the-mapped-branch"])
def test_a_fetch_writes_no_ref_a_configured_mapping_names(
    named: tuple[str, ...], tmp_path: Path
) -> None:
    """A remote's configured fetch refspecs apply beside the named ones unless the
    fetch empties them: one mapping a branch into refs/heads/ must not create it."""
    root, _call = _arrange_catch_up_labelled(tmp_path)
    _git(root, "config", "--add", "remote.origin.fetch", f"+refs/heads/{WORKING}:refs/heads/mapped")
    _git(root, "config", "--add", "remote.origin.fetch", "+refs/tags/*:refs/tags/*")

    with git_core._fetch_exec_lock:
        assert git_core._fetch_refs("origin", *named, cwd=root)

    assert _git(root, "branch", "--list", "mapped") == ""
    assert _git(root, "for-each-ref", "refs/tags/") == ""


def test_catch_up_brings_a_label_published_for_a_milestone_already_here(tmp_path: Path) -> None:
    """Both branches already match the remote; the colleague then labels the shared
    milestone. Catch up adds the label, as a recorded step, and writes no file."""
    root = _new_repo(tmp_path)
    _pair(root)
    _milestone(root)
    bare = _remote(root, tmp_path)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    _git(other, "config", "user.name", "Colleague")
    _git(other, "config", "user.email", "colleague@example.com")
    _git(other, "tag", "-a", "version/1.5", "-m", "1.5", f"origin/{WORKING}")
    _git(other, "push", "-q", "origin", "version/1.5")
    (root / ".haute_cache").mkdir()

    response = fast_forward_pair("origin", root, cwd=root)

    assert response.fast_forwarded == []
    assert response.labels_added == ["1.5"]
    assert _git(root, "rev-parse", "refs/tags/version/1.5^{commit}") == _git(
        root, "rev-parse", WORKING
    )
    assert (root / ".haute_cache").is_dir()  # no file was written, so the caches stay
    assert read_operation(root) is None
    with pytest.raises(GitDomainError, match="Already up to date"):
        fast_forward_pair("origin", root, cwd=root)


def test_spin_off_brings_the_colleagues_labels_with_their_line(tmp_path: Path) -> None:
    root = _new_repo(tmp_path)
    _pair(root)
    _milestone(root)
    bare = _remote(root, tmp_path)
    _colleague(bare, tmp_path, _rewrites_rating_and_deletes_a_utility, label="2.0")
    _write_and_save(root, WORKING, {"local.py": "# local line\n"})
    commit_milestone("Local milestone", root, cwd=root, allow_fork=True)

    haute_git.branch_away("origin", root, cwd=root)

    label = _git(root, "rev-parse", "refs/tags/version/2.0^{commit}")
    assert label == _git(root, "rev-parse", WORKING)


def test_retry_refuses_refs_that_moved_after_the_stop_and_keeps_the_record(
    tmp_path: Path,
) -> None:
    root, call = _arrange_catch_up(tmp_path)
    _stop(root, call, "catch-up", 1)
    _git(root, "update-ref", f"refs/heads/{WORKING}", _git(root, "rev-parse", "main"))

    with pytest.raises(GitTransactionError, match="Catching up stopped part-way, and its branches"):
        retry_interrupted(root, cwd=root)

    assert read_operation(root) is not None


def test_operations_do_not_nest_and_none_runs_over_a_record(tmp_path: Path) -> None:
    root = _new_repo(tmp_path)
    inner = git_operation.Operation("open-ledger", root, root)
    inner.refs({f"refs/heads/{LEDGER}": _git(root, "rev-parse", WORKING)})
    outer = git_operation.Operation("repair", root, root)
    outer.refs({"refs/heads/elsewhere": _git(root, "rev-parse", WORKING)})
    outer.call(inner.run)
    outer.refs({"refs/heads/after-the-call": _git(root, "rev-parse", WORKING)})

    with pytest.raises(RuntimeError, match="already running"):
        outer.run()
    assert _git(root, "branch", "--list", "elsewhere") == ""
    assert _git(root, "branch", "--list", LEDGER) == ""
    assert read_operation(root) is None  # the outer operation was put back

    last_is_a_call = git_operation.Operation("repair", root, root)
    last_is_a_call.refs({"refs/heads/elsewhere": _git(root, "rev-parse", WORKING)})
    last_is_a_call.call(lambda: None)
    with pytest.raises(ValueError, match="a call step needs a step that sets a slot after it"):
        last_is_a_call.run()

    stopped, call = _arrange_restore(tmp_path / "stopped")
    _stop(stopped, call, "restore", 1)
    late = git_operation.Operation("repair", stopped, stopped)
    late.refs({"refs/heads/elsewhere": _git(stopped, "rev-parse", WORKING)})
    with pytest.raises(GitInterruptedError, match="Restoring a version stopped part-way"):
        late.run()
    assert _git(stopped, "branch", "--list", "elsewhere") == ""


def test_retry_finds_a_changed_file_when_git_cannot_hash_them_all_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file git cannot read fails the hash of the whole list; each is hashed alone, so
    the readable ones are still checked, and the one git cannot read is left for later."""
    root, call = _arrange_restore(tmp_path)
    _stop(root, call, "restore", 1)
    _write(root, "rating.py", "# edited after the server stopped\n")
    real = git_operation._run_git

    def batch_refused(*args: str, **kwargs: object) -> str:
        if args[:2] == ("hash-object", "--stdin-paths"):
            raise GitError("fatal: could not open 'keep.txt' for reading")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_operation, "_run_git", batch_refused)
    with pytest.raises(GitDomainError, match="rating.py changed after restoring a version"):
        retry_interrupted(root, cwd=root)
    assert read_operation(root) is not None


def test_a_record_that_cannot_be_read_is_an_interruption_retry_will_not_guess(
    tmp_path: Path,
) -> None:
    root = _new_repo(tmp_path)
    set_working_branch(WORKING, root, cwd=root)
    _write(root, ".haute/operation.json", "{ torn")

    status = working_branch_status(root, cwd=root)
    assert status.state == "interrupted"
    assert status.errors == ["A git action stopped part-way."]
    with pytest.raises(GitTransactionError, match="cannot be read"):
        retry_interrupted(root, cwd=root)
    assert (root / ".haute" / "operation.json").exists()


def test_the_analysts_own_edits_are_never_in_a_record_nor_touched_by_a_put_back(
    tmp_path: Path,
) -> None:
    """Create & Move carries the analyst's edit to the new branch by design, beside an
    untracked file. Neither is the operation's: the record names neither, and the put-back
    leaves both as they are. A switch, which would carry the edit off its branch, refuses
    it by name before any record is written."""
    from haute._git import GitUnrecordedFilesError

    root, call = _arrange_fork(move=True)(tmp_path / "move")
    _write(root, "rating.py", "# the analyst's own edit\n")
    _write(root, "notes.md", "untracked\n")
    record = _stop(root, call, "create-branch", 0)
    assert record.files == []
    retry_interrupted(root, cwd=root)
    assert (root / "rating.py").read_text() == "# the analyst's own edit\n"
    assert (root / "notes.md").read_text() == "untracked\n"

    switching, select = _arrange_select(tmp_path / "switch")
    _write(switching, "rating.py", "# the analyst's own edit\n")
    with pytest.raises(GitUnrecordedFilesError):
        select.run(switching)
    assert (switching / "rating.py").read_text() == "# the analyst's own edit\n"
    assert _git(switching, "symbolic-ref", "--short", "HEAD") == LEDGER
    assert read_operation(switching) is None


# ---------------------------------------------------------------------------
# A catch-up over a file the remote deleted that another program holds
# ---------------------------------------------------------------------------


def _behind_a_colleague(
    tmp_path: Path, arrange: Callable[[Path], tuple[Path, _Call]] = _arrange_catch_up
) -> tuple[Path, dict[str, str]]:
    root, _call = arrange(tmp_path)
    tips = {ref: _git(root, "rev-parse", ref) for ref in (WORKING, LEDGER)}
    return root, tips


def _as_before(root: Path, tips: dict[str, str], before: dict[str, bytes]) -> None:
    assert _git(root, "rev-parse", WORKING) == tips[WORKING]
    assert _git(root, "rev-parse", LEDGER) == tips[LEDGER]
    assert _git(root, "symbolic-ref", "--short", "HEAD") == LEDGER
    assert _worktree(root) == before
    assert _git(root, "status", "--porcelain", "--untracked-files=no") == ""
    assert _untracked(root) == set()  # nothing left behind, tracked or not
    assert read_operation(root) is None


@pytest.mark.parametrize(
    "arrange",
    [_arrange_catch_up, _arrange_catch_up_ledger_only],
    ids=["both-legs-behind", "only-the-ledger-behind"],
)
def test_a_catch_up_git_cannot_remove_a_file_for_leaves_everything_as_it_was(
    arrange: Callable[[Path], tuple[Path, _Call]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git moves the branch and exits 0 with the file left on disk, untracked."""
    root, tips = _behind_a_colleague(tmp_path, arrange)
    before = _worktree(root)
    real = git_core._run_git_capture

    def merge_leaving_the_file(*args: str, cwd: Path | None = None) -> tuple[int, str]:
        code, stderr = real(*args, cwd=cwd)
        if args[0] == "merge":
            _write(root, "utility/old_rates.py", "RATE = 1\n")
            return 0, "warning: unable to unlink 'utility/old_rates.py': Invalid argument"
        return code, stderr

    monkeypatch.setattr(git_core, "_run_git_capture", merge_leaving_the_file)
    with pytest.raises(GitFilesInUseError, match="utility/old_rates.py is open in another"):
        fast_forward_pair("origin", root, cwd=root)
    _as_before(root, tips, before)

    monkeypatch.undo()
    fast_forward_pair("origin", root, cwd=root)

    assert _git(root, "rev-parse", LEDGER) == _git(root, "rev-parse", f"origin/{LEDGER}")
    assert _git(root, "rev-parse", LEDGER) != tips[LEDGER]
    assert not (root / "utility" / "old_rates.py").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows holds open files against replacement")
@pytest.mark.parametrize("held", ["utility/old_rates.py", "rating.py"], ids=["deleted", "changed"])
def test_a_catch_up_over_a_file_held_open_on_windows_changes_nothing_until_it_closes(
    held: str, tmp_path: Path
) -> None:
    root, tips = _behind_a_colleague(tmp_path)
    before = _worktree(root)

    with open(root / held, "rb"), pytest.raises(GitFilesInUseError, match=re.escape(held)):
        fast_forward_pair("origin", root, cwd=root)
    _as_before(root, tips, before)

    fast_forward_pair("origin", root, cwd=root)

    assert _git(root, "rev-parse", LEDGER) == _git(root, "rev-parse", f"origin/{LEDGER}")
    assert _git(root, "rev-parse", LEDGER) != tips[LEDGER]
    assert not (root / "utility" / "old_rates.py").exists()
    assert (root / "rating.py").read_text() == "# the colleague's rating\n"


# ---------------------------------------------------------------------------
# The runners refuse a ref move outside an operation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "moves"),
    [
        (("status", "--porcelain"), False),
        (("init", "-q"), False),
        (("rev-parse", "HEAD"), False),
        (("restore", "--source=HEAD", "--", "a"), False),
        (("-c", "core.hooksPath=/dev/null", "commit", "-m", "x"), True),
        (("update-ref", "--stdin"), True),
        (("branch", "x"), True),
        (("checkout", "x"), True),
        (("merge", "--ff-only", "x"), True),
        (("symbolic-ref", "--short", "-q", "HEAD"), False),
        (("symbolic-ref", "HEAD", "refs/heads/x"), True),
        (("symbolic-ref", "--delete", "HEAD"), True),
        (("tag", "--points-at", "x", "--list", "version/*"), False),
        (("tag", "version/1.0"), True),
        # git follows tags into refs/tags/ by default, and a remote's configured fetch
        # refspecs may name any ref and apply beside named ones: only a fetch with
        # --no-tags, --refmap= and every destination named under refs/remotes/ moves none.
        (("fetch", "--prune", "origin", "--quiet"), True),
        (("fetch", "bundle", "+refs/heads/*:refs/remotes/upstream/*"), True),
        (("fetch", "--no-tags", "--refmap=", "--prune", "origin", "--quiet"), True),
        (("fetch", "--no-tags", "origin", "+refs/heads/*:refs/remotes/origin/*"), True),
        (("fetch", *git_core.SAFE_FETCH, "bundle", "+refs/heads/*:refs/remotes/up/*"), False),
        (
            ("fetch", *git_core.SAFE_FETCH, "C:\\t\\p.bundle", "+refs/heads/*:refs/remotes/up/*"),
            False,
        ),
        (("fetch", *git_core.SAFE_FETCH, "origin", "refs/heads/a:refs/heads/a"), True),
        (("fetch", *git_core.SAFE_FETCH, "origin", "+refs/tags/*:refs/tags/*"), True),
    ],
)
def test_each_command_is_classified_as_moving_a_ref_or_not(
    args: tuple[str, ...], moves: bool
) -> None:
    assert git_core._moves_local_refs(args) is moves


def test_a_command_nobody_classified_is_refused() -> None:
    with pytest.raises(GitError, match="git gc is not classified"):
        git_core._git_argv(("gc",))


_RUNNERS = (
    "_run_git",
    "_run_git_ok",
    "_run_git_rc",
    "_run_git_capture",
    "_run_git_process",
    "_rewrite_tree",
)


@pytest.mark.meta
def test_every_git_command_the_source_runs_is_classified() -> None:
    """A command nobody classified refuses at run time, so every literal subcommand the
    source hands a runner is classified here first, past ``-c`` settings and NO_HOOKS."""
    subcommands: set[str] = set()
    for name in _RUNNERS:
        for _path, call, _scope in _calls_to(name):
            args = [arg for arg in call.args if not isinstance(arg, ast.Starred)]
            literal = [arg.value for arg in args if isinstance(arg, ast.Constant)]
            while literal[:1] == ["-c"]:
                literal = literal[2:]
            if literal and isinstance(literal[0], str):
                subcommands.add(literal[0])
    assert {"rev-parse", "update-ref", "status", "archive", "fetch"} <= subcommands
    for subcommand in sorted(subcommands):
        git_core._moves_local_refs((subcommand,))  # raises for one nobody classified


def test_a_ref_move_outside_an_operation_is_refused(tmp_path: Path) -> None:
    root = _new_repo(tmp_path)
    with pytest.raises(GitError, match="git branch would move a ref outside a recorded operation"):
        git_core._run_git("branch", "stray", cwd=root)
    assert _git(root, "branch", "--list", "stray") == ""

    with git_core._ref_moves_permitted():
        git_core._run_git("branch", "stray", cwd=root)
    assert _git(root, "branch", "--list", "stray") == "stray"


def test_a_save_may_make_one_commit_and_move_nothing_else() -> None:
    with git_core._save_commit():
        assert git_core._git_argv(("commit", "-m", "save"))[:2] == ["git", "commit"]
        with pytest.raises(GitError, match="git commit would move a ref"):
            git_core._git_argv(("commit", "-m", "a second"))
    with git_core._save_commit(), pytest.raises(GitError, match="git update-ref would move"):
        git_core._git_argv(("update-ref", "refs/heads/x", "HEAD"))


def _calls_to(name: str) -> list[tuple[Path, ast.Call, list[str]]]:
    """Every call of *name* in the source, with the functions it is inside."""
    found: list[tuple[Path, ast.Call, list[str]]] = []
    for path in source_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node: ast.AST, scope: list[str], path: Path = path) -> None:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                scope = [*scope, node.name]
            if isinstance(node, ast.Call):
                callee = node.func
                called = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
                if called == name:
                    found.append((path, node, scope))
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(tree, [])
    return found


@pytest.mark.meta
def test_only_the_operation_steps_move_refs_and_only_a_save_commits_outside_one() -> None:
    permitted = {(path.name, tuple(scope)) for path, _, scope in _calls_to("_ref_moves_permitted")}
    assert {name for name, _ in permitted} == {"_git_operation.py"}
    assert {scope[0] for _, scope in permitted} == {"_perform", "recover"}
    saves = {(path.name, tuple(scope)) for path, _, scope in _calls_to("_save_commit")}
    assert saves == {("_git_transactions.py", ("commit_save",))}


@pytest.mark.meta
def test_every_git_process_the_core_starts_is_built_by_the_classifying_builder() -> None:
    core = SRC / "_git_core.py"
    tree = ast.parse(core.read_text(encoding="utf-8"))
    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        built = {
            target.id
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", "") == "_git_argv"
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            callee = call.func
            spawns = (
                isinstance(callee, ast.Attribute)
                and getattr(callee.value, "id", "") == "subprocess"
                and callee.attr in ("run", "Popen")
            ) or getattr(callee, "id", "") == "_run_process_group"
            if not spawns or function.name == "_run_process_group":
                continue
            argv = call.args[0]
            assert (isinstance(argv, ast.Call) and getattr(argv.func, "id", "") == "_git_argv") or (
                isinstance(argv, ast.Name) and argv.id in built
            ), f"{function.name} starts git without _git_argv (line {call.lineno})"
    # The one function that starts a process from its argument is only given built argv.
    for _, call, _ in _calls_to("_run_process_group"):
        assert getattr(call.args[0].func, "id", "") == "_git_argv"  # type: ignore[attr-defined]


@pytest.mark.meta
def test_only_an_operation_writes_and_clears_its_record() -> None:
    for name in ("write_operation", "clear_operation"):
        callers = {(path.name, scope[0]) for path, _, scope in _calls_to(name)}
        assert callers == {("_git_operation.py", "run"), ("_git_operation.py", "recover")} - (
            {("_git_operation.py", "recover")} if name == "write_operation" else set()
        ), name
