"""Crash-rollback safety-net tests for the v1 git engine.

These exercise the mid-sequence failure paths in `haute._git` that never run on
the happy path: the fork rollback (`_rollback_fork`), the parallel-fork lone-ref
cleanup, the branch-away rollback (`_rollback_branch_away`), the branch-away
user-facing guard refusals, and the atomic-push / fast-forward error raises.

The fixtures, helpers and tmp-repo conventions mirror ``test_git_engine.py``.
Failures are injected by wrapping the module-level ``_run_git`` so a chosen git
subcommand raises mid-sequence; the rollback helpers use ``_run_git_ok`` (which
is left intact) and so still run to completion, letting us assert the repo lands
back in a coherent state.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

import haute._git_core as git_core
import haute._git_remote as git_remote
import haute._git_transactions as git_transactions
from haute._git import (
    GitDomainError,
    GitError,
    branch_away,
    commit_milestone,
    commit_save,
    create_working_branch,
    fast_forward_pair,
    ledger_name,
    push_working_pair,
    resolve_ledger,
    set_working_branch,
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


def _fork_setup(repo: Path) -> dict[str, str]:
    """pricing-dev with one milestone M1 then two pending saves; HEAD on ledger."""
    set_working_branch(WORKING, repo, cwd=repo)
    _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="save 1")
    m1 = commit_milestone("M1", repo, cwd=repo).sha
    s2 = _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="save 2")
    s3 = _write_and_save(repo, WORKING, {"rating.py": "# v4\n"}, message="save 3")
    assert s2 is not None and s3 is not None
    return {"m1": m1, "s2": s2, "s3": s3}


def _fail_run_git_on(
    monkeypatch: pytest.MonkeyPatch,
    target: ModuleType,
    trigger: Callable[[tuple[str, ...]], bool],
    exc: Exception | None = None,
) -> None:
    """Patch ``_run_git`` so the FIRST call whose args satisfy *trigger* raises
    *exc* (default ``GitError``); every other call passes through to the real
    implementation. ``_run_git_ok`` (used by the rollback helpers) is untouched.
    """
    real = target._run_git
    fired = {"done": False}

    def wrapper(*args: str, **kwargs: object) -> str:
        if not fired["done"] and trigger(args):
            fired["done"] = True
            raise exc if exc is not None else GitError("injected mid-sequence failure")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(target, "_run_git", wrapper)


class TestRollbackFork:
    """`_rollback_fork`: a mid-sequence move-mode fork failure rolls back cleanly —
    ledger restored to its prior tip, HEAD off the new ledger, and no half-forked
    refs leaked."""

    def test_move_mode_failure_rolls_back(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import read_working_branch

        _fork_setup(repo)
        ledger_tip_before = _git(repo, "rev-parse", LEDGER)
        working_before = _git(repo, "rev-parse", WORKING)

        # Fail the spawning-ledger rewind (`branch -f <ledger> <point>`), which runs
        # AFTER the new ledger is checked out — so HEAD is on the new ledger and the
        # full restore path (`branch -f` + `checkout`) in _rollback_fork runs.
        _fail_run_git_on(
            monkeypatch,
            git_transactions,
            lambda a: a[:2] == ("branch", "-f") and len(a) > 2 and a[2] == LEDGER,
        )
        with pytest.raises(GitError):
            create_working_branch("moved", repo, move=True, cwd=repo)

        # Spawning ledger restored to its prior tip; working branch untouched.
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip_before
        assert _git(repo, "rev-parse", WORKING) == working_before
        # HEAD is back on the spawning ledger, not the new one.
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        # The new pair's refs are gone — retry is not blocked.
        assert _git(repo, "branch", "--list", "moved") == ""
        assert _git(repo, "branch", "--list", "moved-save") == ""
        assert read_working_branch(repo) == WORKING

    def test_move_mode_unexpected_exception_rolls_back(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import read_working_branch

        _fork_setup(repo)
        ledger_tip_before = _git(repo, "rev-parse", LEDGER)
        working_before = _git(repo, "rev-parse", WORKING)
        _fail_run_git_on(
            monkeypatch,
            git_transactions,
            lambda a: a[:2] == ("branch", "-f") and len(a) > 2 and a[2] == LEDGER,
            RuntimeError("injected unexpected failure"),
        )

        with pytest.raises(RuntimeError, match="injected unexpected failure"):
            create_working_branch("moved-runtime", repo, move=True, cwd=repo)

        assert _git(repo, "rev-parse", LEDGER) == ledger_tip_before
        assert _git(repo, "rev-parse", WORKING) == working_before
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert _git(repo, "branch", "--list", "moved-runtime") == ""
        assert _git(repo, "branch", "--list", "moved-runtime-save") == ""
        assert read_working_branch(repo) == WORKING

    def test_parallel_fork_lone_ref_cleanup(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Parallel (move=False) fork: if the ledger ref creation fails after the
        working ref was created, the lone working ref is deleted (no leak)."""
        _fork_setup(repo)

        # Fail the SECOND `branch <ledger> <base>` — the new ledger ref creation,
        # which runs after the working ref already exists.
        _fail_run_git_on(
            monkeypatch,
            git_transactions,
            lambda a: a[:1] == ("branch",) and len(a) >= 2 and a[1] == ledger_name("para"),
        )
        with pytest.raises(GitError):
            create_working_branch("para", repo, cwd=repo)

        # The lone working ref was cleaned up — neither ref of the pair survives.
        assert _git(repo, "branch", "--list", "para") == ""
        assert _git(repo, "branch", "--list", "para-save") == ""


class TestRollbackBranchAway:
    """`_rollback_branch_away`: a mid-sequence branch_away failure restores both
    lineages — the canonical name is not left half-repointed, and the dated
    set-aside name does not strand the original pair."""

    def _add_bare_remote(self, repo: Path, tmp_path: Path) -> Path:
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        return bare

    def _diverge(self, repo: Path, bare: Path, tmp_path: Path) -> None:
        """Push, advance the remote on another clone, advance locally → diverged."""
        push_working_pair("origin", repo, cwd=repo)
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        from haute._git_state import write_working_branch

        write_working_branch(other, WORKING)
        resolve_ledger(WORKING, cwd=other)
        (other / "rating.py").write_text("# remote line\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        commit_milestone("remote milestone", other, cwd=other)
        push_working_pair("origin", other, cwd=other)
        _write_and_save(repo, WORKING, {"local.py": "# local line\n"})
        commit_milestone("local milestone", repo, cwd=repo, allow_fork=True)

    def test_failure_restores_both_lineages(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import read_working_branch

        resolve_ledger(WORKING, cwd=repo)
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        bare = self._add_bare_remote(repo, tmp_path)
        self._diverge(repo, bare, tmp_path)

        old_w = _git(repo, "rev-parse", WORKING)
        old_l = _git(repo, "rev-parse", LEDGER)

        # Fail the canonical working re-point (`branch <working> <remote_w>`),
        # which runs after BOTH refs were renamed aside — so the rollback must
        # rename both back and restore HEAD onto the original ledger.
        _fail_run_git_on(
            monkeypatch,
            git_remote,
            lambda a: a[:1] == ("branch",) and len(a) >= 3 and a[1] == WORKING,
        )
        with pytest.raises(GitError):
            branch_away("origin", repo, cwd=repo)

        # Both lineages intact at their original tips; canonical name NOT
        # half-repointed to the remote.
        assert _git(repo, "rev-parse", WORKING) == old_w
        assert _git(repo, "rev-parse", LEDGER) == old_l
        # HEAD restored onto the original ledger.
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert read_working_branch(repo) == WORKING
        # No dated set-aside pair was left behind.
        aside_refs = _git(repo, "branch", "--list", f"{WORKING}-local-*")
        assert aside_refs == ""


class TestBranchAwayGuards:
    """branch_away user-facing guard refusals: dirty/unsaved tree and detached
    HEAD (viewing history) are refused before any ref is touched."""

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

    def test_refuses_on_dirty_tree(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair_with_remote(repo, tmp_path)
        # Unsaved tracked edit on the checked-out ledger.
        (repo / "rating.py").write_text("# unsaved edit\n")
        with pytest.raises(GitDomainError, match="unsaved changes"):
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

    def test_push_transport_failure_raises_plain_giterror(self, repo: Path, tmp_path: Path) -> None:
        # A remote whose URL points nowhere fails with a transport error (NOT a
        # non-fast-forward rejection), so push_working_pair raises a plain
        # GitError rather than the structured push-rejection.
        self._setup_pair(repo)
        _git(repo, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
        with pytest.raises(GitError) as exc:
            push_working_pair("origin", repo, cwd=repo)
        # Not the structured rejection subclass — a bare transport GitError.
        assert exc.type is GitError

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

        git_core._fetch_cooldowns.clear()

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
