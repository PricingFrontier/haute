"""Failure-path tests for the v1 git engine that the recorded-operation tests do
not cover: the branch-away user-facing guard refusals, and the atomic-push /
fast-forward error raises. A failure part-way through an operation, and its put-back,
is tested per step in ``tests/test_git_operations.py``.

The fixtures, helpers and tmp-repo conventions mirror the git engine tests
(``tests/_git_engine_helpers.py``).
"""

import subprocess
from pathlib import Path

import pytest

import haute._git_core as git_core
import haute._git_remote as git_remote
from haute._git import (
    GitDomainError,
    GitError,
    branch_away,
    commit_milestone,
    commit_save,
    fast_forward_pair,
    push_working_pair,
    resolve_ledger,
)

WORKING = "pricing-dev"
LEDGER = "pricing-dev-save"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _write_and_save(
    repo: Path, working: str, files: dict[str, str], message: str | None = None
) -> str | None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return commit_save(list(files), working, cwd=repo, message=message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "pipeline_repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test Actuary")
    _git(root, "config", "user.email", "test@example.com")
    (root / "rating.py").write_text("# pipeline\n")
    _git(root, "add", "rating.py")
    _git(root, "commit", "-m", "initial pipeline")
    _git(root, "checkout", "-b", WORKING)
    return root


class TestBranchAwayGuards:
    """branch_away user-facing guard refusals: detached HEAD (viewing history) and no
    working branch are refused before any ref is touched. A file changed outside Haute is
    named once the remote is read (tests/test_git_working_tree.py::TestFilesChangedOutsideHaute)."""

    def _setup_pair_with_remote(self, repo: Path, tmp_path: Path) -> None:
        from haute._git_state import write_working_branch

        resolve_ledger(WORKING, cwd=repo)
        write_working_branch(repo, WORKING)
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))

    def test_refuses_on_detached_head(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair_with_remote(repo, tmp_path)
        # Detach HEAD: simulate "viewing history" — HEAD is no longer on the ledger.
        _git(repo, "checkout", "--detach", LEDGER)
        with pytest.raises(GitDomainError, match="viewing history"):
            branch_away("origin", repo, cwd=repo)

    def test_refuses_when_no_working_branch(self, repo: Path, tmp_path: Path) -> None:
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        # No working-branch state recorded.
        with pytest.raises(GitDomainError, match="No working branch is set"):
            branch_away("origin", repo, cwd=repo)


class TestPushAndFastForwardRaises:
    """The atomic-push non-FF-vs-other raise and the fast-forward CAS ref-resolve
    failure: error paths that don't surface a structured fork."""

    def _setup_pair(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        resolve_ledger(WORKING, cwd=repo)
        write_working_branch(repo, WORKING)

    def test_push_transport_failure_answers_the_refresh_message(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # A remote whose URL points nowhere fails with a transport error (NOT a
        # non-fast-forward rejection), so push_working_pair says it could not reach
        # the remote, in Catch up's words, rather than raising the structured rejection.
        from haute._git import GitRemoteUnavailableError

        self._setup_pair(repo)
        _git(repo, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
        with pytest.raises(GitRemoteUnavailableError) as exc:
            push_working_pair("origin", repo, cwd=repo)
        assert str(exc.value) == (
            "Could not refresh 'origin'. Check your connection or credentials and try again."
        )

    def test_fast_forward_ref_resolve_failure_raises(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import write_working_branch

        # Set up a clone that is strictly behind the remote on the working leg so
        # fast_forward_pair reaches the working-ref CAS branch, then break the
        # ref resolution so the resolve-failure raise fires.
        self._setup_pair(repo)
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        push_working_pair("origin", repo, cwd=repo)

        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        write_working_branch(other, WORKING)
        resolve_ledger(WORKING, cwd=other)
        (other / "rating.py").write_text("# remote advance\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        commit_milestone("remote m", other, cwd=other)
        push_working_pair("origin", other, cwd=other)

        # Catch-up first verifies the freshly fetched remote leg exists, then the
        # leg-state read resolves it again to classify the working branch as
        # "behind". Break only the THIRD resolution inside the working-ref CAS
        # block, so the CAS branch is entered and its resolve-failure raise fires.
        real_rev = git_core._rev_parse
        tracking = f"refs/remotes/origin/{WORKING}"
        seen = {"n": 0}

        def fake_rev(ref: str, cwd: Path | None = None) -> str | None:
            if ref == tracking:
                seen["n"] += 1
                if seen["n"] >= 3:
                    return None
            return real_rev(ref, cwd=cwd)

        # The split remote domain performs the initial/CAS probes, while the
        # shared leg-state helper performs the classification probe in core.
        # Patch both lookup sites so the injected failure remains the third
        # resolution across the complete operation.
        monkeypatch.setattr(git_remote, "_rev_parse", fake_rev)
        monkeypatch.setattr(git_core, "_rev_parse", fake_rev)
        with pytest.raises(GitError, match="could not resolve refs"):
            fast_forward_pair("origin", repo, cwd=repo)
