"""Focused transactional regressions for working-pair lifecycle operations."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import haute._git as git_mod
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
from haute._git_state import read_trash, read_working_branch, write_working_branch

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
    git_mod._fetch_cooldowns.clear()


def test_adopting_existing_branch_restores_head_ledger_and_prior_state_on_failure(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed adoption leaves no spawned ledger or replacement association."""
    _git(repo, "checkout", "main")
    write_working_branch(repo, "previous-branch")

    import haute._git_state as git_state

    real_write = git_state.write_working_branch

    def fail_only_new_association(project_root: Path, branch: str) -> None:
        if branch == WORKING:
            raise OSError("injected association-write failure")
        real_write(project_root, branch)

    monkeypatch.setattr(git_state, "write_working_branch", fail_only_new_association)

    with pytest.raises(OSError, match="injected association-write failure"):
        set_working_branch(WORKING, repo, cwd=repo)

    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert _git(repo, "branch", "--list", LEDGER) == ""
    assert read_working_branch(repo) == "previous-branch"


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

    delete_working_pair(WORKING, repo, confirm=True, cwd=repo)

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
    real_run = git_mod._run_git

    def fail_working_cas(*args: str, **kwargs: object) -> str:
        if args[:2] == ("update-ref", f"refs/heads/{WORKING}"):
            raise GitError("injected working CAS failure")
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_mod, "_run_git", fail_working_cas)

    with pytest.raises(GitError, match="injected working CAS failure"):
        fast_forward_pair("origin", repo, cwd=repo)

    assert _git(repo, "rev-parse", WORKING) == old_working
    assert _git(repo, "rev-parse", LEDGER) == old_ledger
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
    assert (repo / "rating.py").read_text() == old_content


def test_fast_forward_raises_transaction_error_when_ledger_compensation_fails(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_up_pair(repo)
    _advance_remote_pair(repo, tmp_path)
    real_run = git_mod._run_git
    real_run_ok = git_mod._run_git_ok

    def fail_working_cas(*args: str, **kwargs: object) -> str:
        if args[:2] == ("update-ref", f"refs/heads/{WORKING}"):
            raise GitError("injected working CAS failure")
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    def fail_compensating_reset(*args: str, **kwargs: object) -> tuple[bool, str]:
        if args[:2] == ("reset", "--hard"):
            return False, "injected reset rollback failure"
        return real_run_ok(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_mod, "_run_git", fail_working_cas)
    monkeypatch.setattr(git_mod, "_run_git_ok", fail_compensating_reset)

    with pytest.raises(GitTransactionError, match="automatic rollback was incomplete"):
        fast_forward_pair("origin", repo, cwd=repo)


def test_archive_state_failure_restores_head_and_association(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving off the active pair and clearing clone state is one transaction."""
    _set_up_pair(repo)

    import haute._git_state as git_state

    def fail_clear(_project_root: Path) -> None:
        raise OSError("injected state-clear failure")

    monkeypatch.setattr(git_state, "clear_working_branch", fail_clear)

    with pytest.raises(OSError, match="injected state-clear failure"):
        archive_working_pair(WORKING, repo, cwd=repo)

    assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
    assert _git(repo, "branch", "--list", WORKING) == WORKING
    assert _git(repo, "branch", "--list", LEDGER) == f"* {LEDGER}"
    assert read_working_branch(repo) == WORKING


def test_restore_compensates_when_ledger_rename_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_up_pair(repo)
    archived = archive_working_pair(WORKING, repo, cwd=repo).archived_as
    archived_ledger = f"{archived}-save"
    real_run = git_mod._run_git

    def fail_ledger_rename(*args: str, **kwargs: object) -> str:
        if args[:4] == ("branch", "-m", archived_ledger, LEDGER):
            raise GitError("injected ledger rename failure")
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_mod, "_run_git", fail_ledger_rename)

    with pytest.raises(GitError, match="injected ledger rename failure"):
        restore_working_pair(archived, repo, cwd=repo)

    assert _git(repo, "branch", "--list", WORKING) == ""
    assert _git(repo, "branch", "--list", LEDGER) == ""
    assert _git(repo, "branch", "--list", archived) == archived
    assert _git(repo, "branch", "--list", archived_ledger) == archived_ledger


def test_undelete_failure_removes_partial_heads_and_preserves_recovery_net(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_up_pair(repo)
    delete_working_pair(WORKING, repo, confirm=True, cwd=repo)

    import haute._git_state as git_state

    def fail_remove_trash(_project_root: Path, _branch: str) -> None:
        raise OSError("injected tombstone failure")

    monkeypatch.setattr(git_state, "remove_trash", fail_remove_trash)

    with pytest.raises(OSError, match="injected tombstone failure"):
        undelete_working_pair(WORKING, repo, cwd=repo)

    assert _git(repo, "branch", "--list", WORKING) == ""
    assert _git(repo, "branch", "--list", LEDGER) == ""
    assert _git(repo, "rev-parse", "--verify", f"refs/haute/trash/{WORKING}")
    assert _git(repo, "rev-parse", "--verify", f"refs/haute/trash/{LEDGER}")
    assert WORKING in read_trash(repo)
