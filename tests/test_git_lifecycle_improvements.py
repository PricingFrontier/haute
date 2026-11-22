"""Focused transactional regressions for working-pair lifecycle operations."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import haute._git_operation as git_operation
from haute._git import (
    GitError,
    GitTransactionError,
    archive_working_pair,
    commit_milestone,
    commit_save,
    delete_working_pair,
    fast_forward_pair,
    push_working_pair,
    resolve_ledger,
    restore_working_pair,
    set_working_branch,
    undelete_working_pair,
)
from haute._git_state import read_operation, read_trash, read_working_branch, write_working_branch
from tests._git_engine_helpers import seen_now

WORKING = "pricing-dev"
LEDGER = "pricing-dev-save"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    (root / "rating.py").write_text("# initial\n")
    _git(root, "add", "rating.py")
    _git(root, "commit", "-m", "initial")
    _git(root, "checkout", "-b", WORKING)
    return root


def _set_up_pair(repo: Path) -> None:
    set_working_branch(WORKING, repo, cwd=repo)


def _fail_first_transaction(
    monkeypatch: pytest.MonkeyPatch, naming: str = "", message: str = "injected"
) -> None:
    """Fail the first ref transaction an operation runs that names *naming*; the
    put-back's transaction after it runs as git would."""
    real_run = git_operation._run_git
    failed: list[bool] = []

    def fail_once(*args: str, **kwargs: object) -> str:
        text = kwargs.get("input_text")
        if (
            not failed
            and args[:2] == ("update-ref", "--stdin")
            and isinstance(text, str)
            and naming in text
        ):
            failed.append(True)
            raise GitError(message)
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_operation, "_run_git", fail_once)


def _advance_remote_pair(repo: Path, tmp_path: Path) -> None:
    """Make *repo* behind both legs of its pair at a local bare origin."""
    bare = tmp_path / "origin.git"
    _git(repo, "init", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    push_working_pair("origin", repo, cwd=repo)

    other = tmp_path / "other"
    _git(repo, "clone", str(bare), str(other))
    _git(other, "config", "user.name", "Other User")
    _git(other, "config", "user.email", "other@example.com")
    _git(other, "checkout", WORKING)
    write_working_branch(other, WORKING)
    resolve_ledger(WORKING, cwd=other)
    (other / "rating.py").write_text("# remote advance\n")
    assert commit_save(["rating.py"], WORKING, cwd=other) is not None
    commit_milestone("remote milestone", other, cwd=other)
    push_working_pair("origin", other, cwd=other)


def test_adopting_existing_branch_restores_head_ledger_and_prior_state_on_failure(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed adoption leaves no spawned ledger or replacement association."""
    _git(repo, "checkout", "main")
    write_working_branch(repo, "previous-branch")

    real_write = git_operation.write_working_branch

    def fail_only_new_association(project_root: Path, branch: str) -> None:
        if branch == WORKING:
            raise OSError("injected association-write failure")
        real_write(project_root, branch)

    monkeypatch.setattr(git_operation, "write_working_branch", fail_only_new_association)

    with pytest.raises(OSError, match="injected association-write failure"):
        set_working_branch(WORKING, repo, cwd=repo)

    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert _git(repo, "branch", "--list", LEDGER) == ""
    assert read_working_branch(repo) == "previous-branch"
    assert read_operation(repo) is None


def test_confirmed_delete_of_only_active_pair_detaches_when_no_default_exists(
    tmp_path: Path,
) -> None:
    """Deleting an adopted sole pair does not try to check out its own default."""
    repo = tmp_path / "sole-pair"
    repo.mkdir()
    _git(repo, "init", "-b", WORKING)
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "rating.py").write_text("# initial\n")
    _git(repo, "add", "rating.py")
    _git(repo, "commit", "-m", "initial")
    resolve_ledger(WORKING, cwd=repo)
    write_working_branch(repo, WORKING)

    delete_working_pair(WORKING, repo, seen=seen_now(repo, WORKING), confirm=True, cwd=repo)

    assert _git(repo, "branch", "--list", WORKING) == ""
    assert _git(repo, "branch", "--list", LEDGER) == ""
    assert (
        subprocess.run(
            ["git", "symbolic-ref", "--quiet", "HEAD"], cwd=repo, capture_output=True
        ).returncode
        != 0
    )
    assert _git(repo, "cat-file", "-e", "HEAD^{commit}") == ""
    assert read_working_branch(repo) is None


def test_fast_forward_failure_restores_both_refs_head_and_worktree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_up_pair(repo)
    _advance_remote_pair(repo, tmp_path)
    old_working = _git(repo, "rev-parse", WORKING)
    old_ledger = _git(repo, "rev-parse", LEDGER)
    old_content = (repo / "rating.py").read_text()
    # Caches are wiped only once the caught-up tree is in place.
    (repo / ".haute_cache").mkdir()
    _fail_first_transaction(monkeypatch, f"refs/heads/{WORKING} ", "injected working CAS failure")

    with pytest.raises(GitError, match="injected working CAS failure"):
        fast_forward_pair("origin", repo, cwd=repo)

    assert _git(repo, "rev-parse", WORKING) == old_working
    assert _git(repo, "rev-parse", LEDGER) == old_ledger
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
    assert (repo / "rating.py").read_text() == old_content
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""
    assert (repo / ".haute_cache").is_dir()
    assert read_operation(repo) is None


def test_fast_forward_keeps_its_record_when_the_put_back_cannot_finish(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._git import retry_interrupted, working_branch_status

    _set_up_pair(repo)
    _advance_remote_pair(repo, tmp_path)
    old_ledger = _git(repo, "rev-parse", LEDGER)
    real_run = git_operation._run_git

    def refuse_every_transaction_after_the_ledger(*args: str, **kwargs: object) -> str:
        if args[:2] == ("update-ref", "--stdin"):
            raise GitError("injected ref transaction failure")
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_operation, "_run_git", refuse_every_transaction_after_the_ledger)

    with pytest.raises(GitTransactionError, match="Catching up could not be put back"):
        fast_forward_pair("origin", repo, cwd=repo)

    record = read_operation(repo)
    assert record is not None and record.operation == "catch-up"
    assert working_branch_status(repo, cwd=repo).state == "interrupted"

    monkeypatch.undo()
    retry_interrupted(repo, cwd=repo)

    assert _git(repo, "rev-parse", LEDGER) == old_ledger
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""
    assert read_operation(repo) is None


def test_archive_state_failure_restores_head_and_association(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving off the active pair and clearing clone state is one transaction."""
    _set_up_pair(repo)

    def fail_clear(_project_root: Path) -> None:
        raise OSError("injected state-clear failure")

    monkeypatch.setattr(git_operation, "clear_working_branch", fail_clear)

    with pytest.raises(OSError, match="injected state-clear failure"):
        archive_working_pair(WORKING, repo, cwd=repo)

    assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
    assert _git(repo, "branch", "--list", WORKING) == WORKING
    assert _git(repo, "branch", "--list", LEDGER) == f"* {LEDGER}"
    assert read_working_branch(repo) == WORKING
    assert read_operation(repo) is None


def test_restore_moves_nothing_when_its_ref_transaction_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_up_pair(repo)
    archived = archive_working_pair(WORKING, repo, cwd=repo).archived_as
    archived_ledger = f"{archived}-save"
    _fail_first_transaction(monkeypatch, message="injected ref transaction failure")

    with pytest.raises(GitError, match="injected ref transaction failure"):
        restore_working_pair(archived, repo, cwd=repo)

    assert _git(repo, "branch", "--list", WORKING) == ""
    assert _git(repo, "branch", "--list", LEDGER) == ""
    assert _git(repo, "branch", "--list", archived) == archived
    assert _git(repo, "branch", "--list", archived_ledger) == archived_ledger


def test_undelete_failure_removes_partial_heads_and_preserves_recovery_net(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_up_pair(repo)
    entry_id = delete_working_pair(
        WORKING, repo, seen=seen_now(repo, WORKING), confirm=True, cwd=repo
    ).trash_id
    _fail_first_transaction(monkeypatch, message="injected ref transaction failure")

    with pytest.raises(GitError, match="injected ref transaction failure"):
        undelete_working_pair(entry_id, repo, cwd=repo)

    assert _git(repo, "branch", "--list", WORKING) == ""
    assert _git(repo, "branch", "--list", LEDGER) == ""
    assert _git(repo, "rev-parse", "--verify", f"refs/haute/trash/{entry_id}/working")
    assert _git(repo, "rev-parse", "--verify", f"refs/haute/trash/{entry_id}/ledger")
    assert entry_id in read_trash(repo)
    assert read_operation(repo) is None
