"""The git test helper's own contract: a failed command raises with git's message."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._git_helpers import git_run, init_repo


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
