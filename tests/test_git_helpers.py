"""The git test helper's own contract: a failed command raises with git's message, and
no git process a test starts leaves background maintenance writing in its repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._git_core import _run_git
from tests._git_helpers import git_run, init_repo


def test_a_commit_starts_no_background_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "repo").mkdir()
    repo = init_repo(tmp_path / "repo")
    trace = tmp_path / "git-trace.txt"
    monkeypatch.setenv("GIT_TRACE", str(trace))

    git_run(repo, "commit", "--allow-empty", "-m", "a save")

    assert "git commit" in trace.read_text(encoding="utf-8")
    assert "maintenance" not in trace.read_text(encoding="utf-8")


def test_haute_s_git_processes_see_the_suite_s_maintenance_setting(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    repo = init_repo(tmp_path / "repo")

    assert _run_git("config", "--get", "maintenance.auto", cwd=repo).strip() == "false"


def test_a_failed_git_command_raises_with_git_s_own_message(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)

    with pytest.raises(RuntimeError) as raised:
        git_run(repo, "rev-parse", "--verify", "no-such-branch")

    message = str(raised.value)
    assert "git rev-parse --verify no-such-branch failed" in message
    assert "(exit 128)" in message
    assert "fatal: Needed a single revision" in message


def test_a_query_whose_failure_is_its_answer_returns_instead(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)

    assert git_run(repo, "rev-parse", "--verify", "no-such-branch", check=False) == ""
    assert len(git_run(repo, "rev-parse", "--verify", "main", check=False)) == 40
