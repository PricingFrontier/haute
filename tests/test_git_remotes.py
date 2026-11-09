"""The git engine's remotes: fetch, push and fast-forward.

Fetch is prompt-proof, time-bounded and degrades to local refs, the canonical remote is resolved,
the working/ledger pair is pushed deliberately and never forced, and the pair catches up to its
remote by fast-forward only.
"""

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

import haute._git_core as git_core
import haute._git_remote as git_remote
from haute._git import (
    GitDomainError,
    GitError,
    GitPushRejectedError,
    check_invariants,
    commit_milestone,
    commit_save,
    fast_forward_pair,
    list_remotes,
    push_working_pair,
    resolve_ledger,
    set_working_branch,
)
from haute._project_context import context_thread
from tests import _git_engine_helpers
from tests._git_engine_helpers import (
    LEDGER,
    WORKING,
    _git,
    _write_and_save,
)

# Fixtures shared with the other modules split from one suite.
repo = _git_engine_helpers.repo
unborn_repo = _git_engine_helpers.unborn_repo


def _answer_remote(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: bytes, stderr: bytes = b""
) -> None:
    """Answer every remote git command with this output, as the process launcher would."""
    monkeypatch.setattr(
        git_core,
        "_run_process_group",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, returncode, stdout, stderr),
    )


def _without_configured_ssh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No ssh transport of the analyst's own: nothing in the environment or global config."""
    for name in ("GIT_SSH_COMMAND", "GIT_SSH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


class TestFetchHardening:
    """F1: a prompt-proof, time-bounded fetch that degrades to local refs."""

    def test_fetch_refs_degrades_on_bad_remote(self, repo: Path) -> None:

        # A remote pointing nowhere must fail fast and return False — never raise
        # or prompt (F1: a background fetch must not hang the UI).
        _git(repo, "remote", "add", "origin", str(repo / "nonexistent.git"))
        assert git_core._fetch_refs("origin", "main", cwd=repo) is False

    def test_fetch_refs_times_out_to_false_with_prompt_proof_env(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # F1: a hung fetch is killed by the timeout and degrades to False — never
        # raises, never blocks the UI — and the invocation is prompt-proof:
        # GIT_TERMINAL_PROMPT=0, no OpenSSH askpass, and with no transport of the
        # analyst's own, SSH BatchMode, so it can't sit on a credential or host-key
        # prompt to begin with.
        _without_configured_ssh(monkeypatch, tmp_path)
        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))  # type: ignore[arg-type]

        monkeypatch.setattr(git_core, "_run_process_group", fake_run)
        assert git_core._fetch_refs("origin", "main", cwd=repo) is False
        env = captured["env"]
        assert isinstance(env, dict)
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["SSH_ASKPASS_REQUIRE"] == "never"
        assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
        cmd = captured["cmd"]
        assert isinstance(cmd, list) and cmd[:2] == ["git", "fetch"]

    def test_best_effort_remote_readers_degrade_on_unicode_output(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        _answer_remote(monkeypatch, 0, b"\xff\xfe not utf-8\n")

        assert git_core._fetch_refs("origin", "main", cwd=repo) is False
        assert git_mod._ls_remote_version_tags("origin", cwd=repo) == {}


class TestCanonicalRemote:
    """X5: the read-side divergence baseline resolves a single canonical remote
    (origin → sole remote → none) so a non-origin clone isn't silently reported
    'in sync' against a non-existent ``origin/<default>``."""

    def test_none_when_no_remote(self, repo: Path) -> None:
        from haute._git import _canonical_remote

        assert _canonical_remote(cwd=repo) is None

    def test_prefers_origin_over_others(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import _canonical_remote

        _git(repo, "remote", "add", "upstream", str(tmp_path / "u.git"))
        _git(repo, "remote", "add", "origin", str(tmp_path / "o.git"))
        assert _canonical_remote(cwd=repo) == "origin"

    def test_sole_non_origin_remote_is_canonical(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import _canonical_remote

        _git(repo, "remote", "add", "upstream", str(tmp_path / "u.git"))
        assert _canonical_remote(cwd=repo) == "upstream"

    def test_multiple_non_origin_remotes_are_ambiguous(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import _canonical_remote

        _git(repo, "remote", "add", "upstream", str(tmp_path / "u.git"))
        _git(repo, "remote", "add", "fork", str(tmp_path / "f.git"))
        assert _canonical_remote(cwd=repo) is None

    def test_default_branch_resolved_via_non_origin_remote(
        self, repo: Path, tmp_path: Path
    ) -> None:
        from haute._git import _get_default_branch

        bare = tmp_path / "upstream.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "upstream", str(bare))
        _git(repo, "push", "upstream", "main")
        _git(repo, "remote", "set-head", "upstream", "main")
        assert _get_default_branch(cwd=repo) == "main"

    def test_default_branch_ref_is_read_live(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import _get_default_branch

        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "branch", "trunk", "main")
        _git(repo, "remote", "add", "origin", str(bare))
        _git(repo, "push", "origin", "main", "trunk")
        _git(repo, "remote", "set-head", "origin", "main")
        assert _get_default_branch(cwd=repo) == "main"

        _git(repo, "remote", "set-head", "origin", "trunk")

        assert _get_default_branch(cwd=repo) == "trunk"


class TestRemotesAndPush:
    """Deliberate push of the working/ledger pair to an existing remote (S16),
    never force (S33); ahead/behind read from local remote refs only (no fetch)."""

    def _setup_pair(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        resolve_ledger(WORKING, cwd=repo)  # spawn the ledger; HEAD → ledger
        write_working_branch(repo, WORKING)

    def _add_bare_remote(self, repo: Path, tmp_path: Path, name: str = "origin") -> Path:
        bare = tmp_path / f"{name}.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", name, str(bare))
        return bare

    def test_no_remotes_returns_empty(self, repo: Path) -> None:
        self._setup_pair(repo)
        res = list_remotes(repo, cwd=repo)
        assert res.remotes == []
        assert res.working_branch == WORKING

    def test_lists_remote_url_and_null_ahead_before_any_push(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        res = list_remotes(repo, cwd=repo)
        assert [r.name for r in res.remotes] == ["origin"]
        r = res.remotes[0]
        assert r.url == str(bare)
        # No remote-tracking ref exists yet, so divergence is unknown (not 0).
        assert r.working is not None and r.working.status == "untracked"

    def test_push_sends_both_working_and_ledger(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})  # ledger advances
        self._add_bare_remote(repo, tmp_path)
        res = push_working_pair("origin", repo, cwd=repo)
        assert set(res.pushed_refs) == {"main", WORKING, LEDGER}
        remote_refs = _git(repo, "ls-remote", "origin")
        assert f"refs/heads/{WORKING}" in remote_refs
        assert f"refs/heads/{LEDGER}" in remote_refs

    @pytest.mark.parametrize(
        "failure",
        [subprocess.TimeoutExpired(["git", "push"], 1), OSError("injected launch failure")],
        ids=["timeout", "launch-failure"],
    )
    def test_push_transport_is_bounded_and_errors_release_the_repository(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception,
    ) -> None:
        from haute._git import GitRemoteUnavailableError
        from haute._git_lock import repository_mutation

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "push", "origin", "main")  # established: the pair push is the only one
        real_run = git_core._run_process_group
        captured_timeout: list[object] = []

        def fail_push(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            if cmd[:2] == ["git", "push"]:
                captured_timeout.append(kwargs.get("timeout"))
                raise failure
            return real_run(cmd, **kwargs)

        monkeypatch.setattr(git_core, "_run_process_group", fail_push)

        with pytest.raises(GitRemoteUnavailableError) as refused:
            push_working_pair("origin", repo, cwd=repo)
        assert str(refused.value) == (
            "Could not push to 'origin'. Check your connection or credentials and try again."
        )
        assert self._remote_refs(repo, bare) == ["refs/heads/main"]

        assert captured_timeout == [git_core._PUSH_TIMEOUT_SECONDS]
        # The decorator's finally path must release the repository lock for
        # another request thread, not merely permit a same-thread RLock re-entry.
        reacquired = threading.Event()

        def acquire_after_failure() -> None:
            with repository_mutation(repo):
                reacquired.set()

        thread = context_thread(acquire_after_failure, daemon=True)
        thread.start()
        assert reacquired.wait(timeout=2)
        thread.join(timeout=2)

    def test_first_use_unborn_repo_publishes_default_working_and_ledger(
        self, unborn_repo: Path, tmp_path: Path
    ) -> None:
        """The first branch chosen in a fresh project has a publishable shared ancestry."""
        set_working_branch(WORKING, unborn_repo, create=True, cwd=unborn_repo)
        self._add_bare_remote(unborn_repo, tmp_path)

        result = push_working_pair("origin", unborn_repo, cwd=unborn_repo)

        assert result.pushed_refs == ["main", WORKING, LEDGER]
        assert _git(unborn_repo, "merge-base", "--is-ancestor", "main", WORKING) == ""
        assert _git(unborn_repo, "merge-base", "--is-ancestor", "main", LEDGER) == ""
        remote_refs = _git(unborn_repo, "ls-remote", "origin")
        assert {f"refs/heads/{name}" for name in ("main", WORKING, LEDGER)} <= set(
            line.split()[1] for line in remote_refs.splitlines()
        )

    def test_empty_bootstrap_without_ledger_publishes_only_default_and_working(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Pre-save branches retain their ledger name without inventing its ref."""
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.ledger_branch == LEDGER
        assert result.pushed_refs == ["main", WORKING]
        assert f"refs/heads/{LEDGER}" not in _git(repo, "ls-remote", "origin")

    def test_empty_bootstrap_resolves_main_when_a_tag_has_the_same_name(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Branch enumeration stays unambiguous when a tag is also named ``main``."""
        self._setup_pair(repo)
        _git(repo, "tag", "main", "refs/heads/main")
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "main"
        assert result.bootstrapped_default is True
        assert result.pushed_refs == ["main", WORKING, LEDGER]
        assert "refs/heads/main" in _git(repo, "ls-remote", "origin")

    def test_ahead_after_a_local_milestone_following_a_push(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        _git(repo, "fetch", "origin")  # establish remote-tracking refs locally
        # Just pushed: the working branch is level with the remote.
        synced = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert synced.working is not None
        assert synced.working.ahead == 0 and synced.working.behind == 0
        # A local milestone advances the working branch beyond the remote. It is a
        # merge commit, so raw commit-count ahead is 2 (the folded ledger save +
        # the merge commit itself) — ahead/behind are honest git commit counts.
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("local milestone", repo, cwd=repo)
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.working is not None
        assert r.working.ahead == 2 and r.working.behind == 0

    def test_legs_untracked_before_any_push(self, repo: Path, tmp_path: Path) -> None:
        # F2 honesty: never pushed ⇒ both legs "untracked", NOT "synced" — the
        # user must not read "can't tell" as "in sync".
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.working is not None and r.working.status == "untracked"
        assert r.ledger is not None and r.ledger.status == "untracked"

    def test_legs_synced_after_push(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.working is not None and r.working.status == "synced"
        assert r.ledger is not None and r.ledger.status == "synced"

    def test_ledger_behind_is_visible_independently_of_working(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # F6: the two-machine save accident — the remote ledger advances while the
        # working leg stays level. It must surface on the LEDGER leg rather than be
        # structurally invisible (the pre-P7 engine only computed the working leg).

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        # Another clone adds a save (a ledger commit) and pushes only the ledger.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", LEDGER)
        (other / "x.txt").write_text("remote save\n")
        _git(other, "add", "x.txt")
        _git(other, "commit", "-m", "remote save")
        _git(other, "push", "origin", LEDGER)
        # Refresh is an explicit action; listing remotes reads local refs only.
        _git(repo, "fetch", "origin")
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.working is not None and r.working.status == "synced"  # working level
        assert r.ledger is not None and r.ledger.status == "behind"  # ledger VISIBLE
        assert r.ledger.behind == 1

    def test_remote_url_credentials_are_redacted(self, repo: Path, tmp_path: Path) -> None:
        # A token embedded in an https remote URL must never cross the API
        # boundary (threat model: remote URLs/credentials stay server-side).
        self._setup_pair(repo)
        _git(
            repo,
            "remote",
            "add",
            "origin",
            "https://x-access-token:ghp_SECRET123@github.com/org/repo.git",
        )
        res = list_remotes(repo, cwd=repo)
        url = res.remotes[0].url or ""
        assert "ghp_SECRET123" not in url
        assert "x-access-token" not in url
        assert "github.com/org/repo.git" in url  # the host/path survives

    def test_push_is_atomic_working_not_sent_when_ledger_rejected(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # --atomic: if either ref is rejected, NEITHER lands. Here the working
        # ref would fast-forward cleanly, but the ledger diverged on the remote —
        # so the whole push must fail and the working ref must stay put.
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)  # remote now has working + ledger
        before = _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}").split()[0]

        # A teammate advances ONLY the ledger on the remote → our ledger push is non-ff.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", LEDGER)
        (other / "x.txt").write_text("remote ledger edit\n")
        _git(other, "add", "x.txt")
        _git(other, "commit", "-m", "remote ledger change")
        _git(other, "push", "origin", LEDGER)

        # Locally make a milestone: the working branch advances (would fast-forward
        # the remote) while the ledger advances on a different line (non-ff).
        _write_and_save(repo, WORKING, {"rating.py": "# local\n"})
        commit_milestone("local milestone", repo, cwd=repo)
        assert _git(repo, "rev-parse", WORKING) != before  # local working moved on

        with pytest.raises(GitDomainError, match="never force-pushes"):
            push_working_pair("origin", repo, cwd=repo)
        # The fast-forwardable working ref was NOT pushed — the atomic push as a
        # whole was rejected because of the ledger.
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}").split()[0] == before

    def test_push_to_unknown_remote_is_refused(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        with pytest.raises(GitDomainError, match="No remote named"):
            push_working_pair("does-not-exist", repo, cwd=repo)

    def test_first_push_bootstraps_default_and_reports_it(self, repo: Path, tmp_path: Path) -> None:
        """An advertised-empty remote receives the local merge target with the pair.

        This is the analyst's first-push journey: the working pair alone is not a
        useful shared history unless its local default branch is published too.
        """
        from haute._git_state import read_pushed_shas

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "main"
        assert result.bootstrapped_default is True
        assert result.pushed_refs == ["main", WORKING, LEDGER]
        advertised = _git(repo, "ls-remote", "origin")
        assert "refs/heads/main" in advertised
        assert "refs/heads/" + WORKING in advertised
        # The default is a one-time bootstrap ref, not rewrite-detection state.
        assert "origin/main" not in read_pushed_shas(repo)

    def test_second_push_does_not_resubmit_established_default(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "main"
        assert result.bootstrapped_default is False
        assert result.pushed_refs == [WORKING, LEDGER]

    def test_selected_remote_does_not_use_another_remotes_tracking_evidence(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """No-HEAD default resolution is isolated to the selected remote."""
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path, "origin")
        self._add_bare_remote(repo, tmp_path, "upstream")
        base = _git(repo, "rev-parse", "main")
        _git(repo, "branch", "trunk", base)
        _git(repo, "push", "origin", "trunk")
        _git(repo, "branch", "-D", "trunk")
        _git(repo, "branch", "-D", "main")
        _git(repo, "update-ref", "-d", "refs/remotes/origin/trunk")
        _git(repo, "update-ref", "refs/remotes/upstream/trunk", base)

        with pytest.raises(GitDomainError, match="determine the remote default"):
            push_working_pair("origin", repo, cwd=repo)

    def test_related_remote_main_is_never_advanced_when_publishing_pair(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "push", "origin", "main")
        _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
        main_before = _git(repo, "ls-remote", "origin", "refs/heads/main")

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.bootstrapped_default is False
        assert result.pushed_refs == [WORKING, LEDGER]
        assert _git(repo, "ls-remote", "origin", "refs/heads/main") == main_before

    def test_established_push_pins_validated_tips_and_records_that_snapshot(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ref move at the push boundary cannot change what gets published or recorded."""
        from haute._git_state import read_pushed_shas

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)

        base = _git(repo, "rev-parse", "main")
        tree = _git(repo, "rev-parse", f"{base}^{{tree}}")
        working_tip = _git(repo, "commit-tree", tree, "-p", base, "-m", "validated working")
        ledger_tip = _git(repo, "commit-tree", tree, "-p", base, "-m", "validated ledger")
        moved_working = _git(repo, "commit-tree", tree, "-p", working_tip, "-m", "raced working")
        moved_ledger = _git(repo, "commit-tree", tree, "-p", ledger_tip, "-m", "raced ledger")
        _git(repo, "update-ref", f"refs/heads/{WORKING}", working_tip)
        _git(repo, "update-ref", f"refs/heads/{LEDGER}", ledger_tip)

        real_run = subprocess.run
        real_launch = git_core._run_process_group
        push_command: list[str] = []

        def move_refs_at_push(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            if args[:2] == ["git", "push"]:
                push_command.extend(args)
                real_run(
                    ["git", "update-ref", f"refs/heads/{WORKING}", moved_working],
                    cwd=repo,
                    check=True,
                )
                real_run(
                    ["git", "update-ref", f"refs/heads/{LEDGER}", moved_ledger],
                    cwd=repo,
                    check=True,
                )
            return real_launch(args, **kwargs)

        monkeypatch.setattr(git_core, "_run_process_group", move_refs_at_push)

        result = push_working_pair("origin", repo, cwd=repo)

        assert f"{working_tip}:refs/heads/{WORKING}" in push_command
        assert f"{ledger_tip}:refs/heads/{LEDGER}" in push_command
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}").split()[0] == working_tip
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{LEDGER}").split()[0] == ledger_tip
        assert _git(repo, "rev-parse", WORKING) == moved_working
        assert _git(repo, "rev-parse", LEDGER) == moved_ledger
        recorded = read_pushed_shas(repo)
        assert recorded[f"origin/{WORKING}"] == working_tip
        assert recorded[f"origin/{LEDGER}"] == ledger_tip
        assert result.pushed_refs == [WORKING, LEDGER]

    def test_empty_bootstrap_pins_default_tip_to_validated_snapshot(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bootstrap's create-only default push uses the preflight commit, not a live ref."""

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        default_tip = _git(repo, "rev-parse", "main")
        tree = _git(repo, "rev-parse", f"{default_tip}^{{tree}}")
        moved_default = _git(repo, "commit-tree", tree, "-p", default_tip, "-m", "raced default")

        real_run = subprocess.run
        real_launch = git_core._run_process_group
        push_command: list[str] = []

        def move_default_at_push(
            args: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            if args[:2] == ["git", "push"]:
                push_command.extend(args)
                real_run(
                    ["git", "update-ref", "refs/heads/main", moved_default],
                    cwd=repo,
                    check=True,
                )
            return real_launch(args, **kwargs)

        monkeypatch.setattr(git_core, "_run_process_group", move_default_at_push)

        result = push_working_pair("origin", repo, cwd=repo)

        assert f"{default_tip}:refs/heads/main" in push_command
        assert _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == default_tip
        assert _git(repo, "rev-parse", "main") == moved_default
        assert result.pushed_refs == ["main", WORKING, LEDGER]

    def test_default_validation_does_not_import_remote_tags(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """The authoritative default fetch cannot mutate local tag state."""
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        other = tmp_path / "tagged-unrelated"
        _git(tmp_path, "init", "-b", "main", str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        (other / "other.txt").write_text("unrelated\n")
        _git(other, "add", "other.txt")
        _git(other, "commit", "-m", "unrelated tagged default")
        _git(other, "tag", "remote-only-validation-tag")
        _git(other, "remote", "add", "origin", str(bare))
        _git(other, "push", "origin", "main", "refs/tags/remote-only-validation-tag")
        _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")

        with pytest.raises(GitDomainError, match="unrelated"):
            push_working_pair("origin", repo, cwd=repo)

        assert _git(repo, "tag", "--list", "remote-only-validation-tag") == ""

    def test_established_remote_missing_local_main_refuses_before_pair_publish(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "branch", "trunk", "main")
        _git(repo, "push", "origin", "trunk")

        with pytest.raises(GitDomainError, match="expected local default branch 'main' is missing"):
            push_working_pair("origin", repo, cwd=repo)

        advertised = _git(repo, "ls-remote", str(bare))
        assert f"refs/heads/{WORKING}" not in advertised
        assert f"refs/heads/{LEDGER}" not in advertised

    @pytest.mark.parametrize(
        ("returncode", "stdout", "stderr", "expected"),
        [
            (
                0,
                "ref: refs/heads/main\tHEAD\n"
                + "a" * 40
                + "\tHEAD\n"
                + "a" * 40
                + "\trefs/heads/main\n",
                "",
                None,
            ),
            (0, "ref: refs/heads/main\tHEAD\nnot-a-sha\trefs/heads/main\n", "", "malformed"),
            (2, "", "network unavailable", "Could not refresh 'origin'"),
        ],
    )
    def test_inspect_remote_strictly_parses_advertisement(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        returncode: int,
        stdout: str,
        stderr: str,
        expected: str | None,
    ) -> None:
        import haute._git as git_mod

        _answer_remote(monkeypatch, returncode, stdout.encode(), stderr.encode())

        if expected is None:
            assert git_mod._inspect_remote("origin", cwd=repo) == ({"main"}, "main", True)
        else:
            with pytest.raises(git_mod.GitError, match=expected):
                git_mod._inspect_remote("origin", cwd=repo)

    @pytest.mark.parametrize(
        "failure",
        [subprocess.TimeoutExpired(["git"], 1), OSError("no git")],
        ids=["timeout", "launch-failure"],
    )
    def test_inspect_remote_translates_process_failures(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
    ) -> None:
        import haute._git as git_mod

        def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            raise failure

        monkeypatch.setattr(git_core, "_run_process_group", failing_run)
        with pytest.raises(git_mod.GitRemoteUnavailableError, match="Could not refresh 'origin'"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_refuses_an_unreadable_advertisement(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        _answer_remote(monkeypatch, 0, b"\xff\trefs/heads/main\n")
        with pytest.raises(git_mod.GitError, match="unreadable") as refused:
            git_mod._inspect_remote("origin", cwd=repo)
        assert not isinstance(refused.value, git_mod.GitDomainError)

    @pytest.mark.parametrize(
        "failure",
        [subprocess.TimeoutExpired(["git"], 1), OSError("no git")],
        ids=["timeout", "launch-failure"],
    )
    def test_fetch_expected_default_translates_process_failures(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
    ) -> None:
        import haute._git as git_mod

        def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            raise failure

        monkeypatch.setattr(git_core, "_run_process_group", failing_run)
        with pytest.raises(git_mod.GitRemoteUnavailableError, match="Could not refresh 'origin'"):
            git_mod._fetch_expected_default("origin", "main", cwd=repo)

    @pytest.mark.parametrize(
        "advertised_ref",
        [
            "refs/heads/foo..bar",
            "refs/heads/x.lock",
            "refs/tags/foo..bar",
            "refs/",
            "refs/heads/main^{}",
        ],
    )
    def test_inspect_remote_rejects_git_invalid_object_refs(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        advertised_ref: str,
    ) -> None:
        import haute._git as git_mod

        _answer_remote(monkeypatch, 0, f"{'a' * 40}\t{advertised_ref}\n".encode())

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_accepts_valid_peeled_tag_advertisement(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        sha = "a" * 40
        peeled = "b" * 40
        stdout = (
            "ref: refs/heads/main\tHEAD\n"
            f"{sha}\tHEAD\n"
            f"{sha}\trefs/heads/main\n"
            f"{sha}\trefs/heads/feature]x\n"
            f"{sha}\trefs/tags/version/1.0\n"
            f"{peeled}\trefs/tags/version/1.0^{{}}\n"
        )
        _answer_remote(monkeypatch, 0, stdout.encode())

        assert git_mod._inspect_remote("origin", cwd=repo) == (
            {"main", "feature]x"},
            "main",
            True,
        )

    def test_inspect_remote_treats_unborn_symbolic_head_as_empty(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        _answer_remote(monkeypatch, 0, b"ref: refs/heads/main\tHEAD\n")

        assert git_mod._inspect_remote("origin", cwd=repo) == (set(), "main", False)

    @pytest.mark.parametrize("zero_oid", ["0" * 40, "0" * 64])
    def test_inspect_remote_rejects_zero_object_ids(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, zero_oid: str
    ) -> None:
        import haute._git as git_mod

        _answer_remote(monkeypatch, 0, f"{zero_oid}\trefs/heads/main\n".encode())

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_rejects_mixed_object_id_widths(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        stdout = f"{'a' * 40}\trefs/heads/main\n{'b' * 64}\trefs/tags/version/1.0\n"
        _answer_remote(monkeypatch, 0, stdout.encode())

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_rejects_contradictory_duplicate_object_ref(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        stdout = f"{'a' * 40}\trefs/heads/main\n{'b' * 40}\trefs/heads/main\n"
        _answer_remote(monkeypatch, 0, stdout.encode())

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_rejects_head_object_mismatching_its_target(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        stdout = f"ref: refs/heads/main\tHEAD\n{'a' * 40}\tHEAD\n{'b' * 40}\trefs/heads/main\n"
        _answer_remote(monkeypatch, 0, stdout.encode())

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_bootstrap_race_fails_create_only_lease_without_publishing_pair(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The create-only lease rejects even a concurrent fast-forwardable main."""
        import haute._git as git_mod
        from haute._git import GitBootstrapIncompleteError

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        remote_tip = _git(repo, "rev-parse", "main")
        _git(repo, "push", "origin", "main")
        tree = _git(repo, "rev-parse", f"{remote_tip}^{{tree}}")
        local_default = _git(
            repo, "commit-tree", tree, "-p", remote_tip, "-m", "local default advance"
        )
        _git(repo, "update-ref", "refs/heads/main", local_default)
        monkeypatch.setattr(
            git_remote, "_inspect_remote", lambda remote, cwd=None: (set(), None, False)
        )

        with pytest.raises(git_mod.GitError) as exc:
            push_working_pair("origin", repo, cwd=repo)

        assert not isinstance(exc.value, GitPushRejectedError)
        # Nothing of this call landed, so it must not claim the default was published.
        assert not isinstance(exc.value, GitBootstrapIncompleteError)
        # It passes: the next Push finds an established remote, so publishing retries it.
        assert isinstance(exc.value, git_mod.GitRemoteUnavailableError)
        assert str(exc.value) == (
            "'origin' changed while Haute was publishing to it, so nothing was published. "
            "Push again."
        )
        advertised = _git(repo, "ls-remote", str(bare))
        assert _git(repo, "ls-remote", str(bare), "refs/heads/main").split()[0] == remote_tip
        assert f"refs/heads/{WORKING}" not in advertised
        assert f"refs/heads/{LEDGER}" not in advertised

    def test_a_push_the_remote_refuses_says_so_in_words(self, repo: Path, tmp_path: Path) -> None:
        """A branch rule or a hook declining the push is a refusal, not a connection
        problem: it answers in words, never "Operation failed", and nothing lands."""
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "push", "origin", "main")
        hook = bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'branch rule: no pushes' >&2\nexit 1\n", newline="\n")
        hook.chmod(0o755)

        with pytest.raises(GitDomainError) as refused:
            push_working_pair("origin", repo, cwd=repo)

        assert str(refused.value) == (
            "'origin' refused the push, so nothing was published and your work is unchanged. "
            "The Haute server log has its reason, for example a branch rule or missing write "
            "access."
        )
        assert self._remote_refs(repo, bare) == ["refs/heads/main"]

    def test_bootstrap_race_with_same_default_tip_publishes_pair(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An idempotent concurrent creation at the snapshot SHA remains safe."""
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        default_tip = _git(repo, "rev-parse", "main")
        _git(repo, "push", "origin", "main")
        monkeypatch.setattr(
            git_remote, "_inspect_remote", lambda remote, cwd=None: (set(), None, False)
        )

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "main"
        assert result.bootstrapped_default is True
        assert result.pushed_refs == ["main", WORKING, LEDGER]
        assert _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == default_tip
        advertised = _git(repo, "ls-remote", "origin")
        assert f"refs/heads/{WORKING}" in advertised
        assert f"refs/heads/{LEDGER}" in advertised

    # -- default-first bootstrap ---------------------------------------------

    @staticmethod
    def _log_receive_sessions(bare: Path) -> Path:
        """Record each receive session's refs: a pre-receive hook runs once per push."""
        hook = bare / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            'echo "--- session" >> receive-sessions.log\n'
            'while read old new ref; do echo "$ref" >> receive-sessions.log; done\n',
            newline="\n",
        )
        hook.chmod(0o755)
        return bare / "receive-sessions.log"

    @staticmethod
    def _receive_sessions(log: Path) -> list[list[str]]:
        sessions: list[list[str]] = []
        for line in log.read_text(encoding="utf-8").splitlines():
            if line == "--- session":
                sessions.append([])
            elif line:
                sessions[-1].append(line)
        return sessions

    @staticmethod
    def _refuse_all_but_main(bare: Path) -> Path:
        """A host rule that accepts the default branch and refuses every other ref."""
        hook = bare / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "while read old new ref; do\n"
            '  [ "$ref" = refs/heads/main ] || { echo "SECRET-STDERR branch rule" >&2; exit 1; }\n'
            "done\n",
            newline="\n",
        )
        hook.chmod(0o755)
        return hook

    @staticmethod
    def _remote_refs(repo: Path, bare: Path) -> list[str]:
        refs = (line.split()[1] for line in _git(repo, "ls-remote", str(bare)).splitlines())
        return sorted(ref for ref in refs if ref != "HEAD")

    def test_bootstrap_initialising_push_carries_only_the_default(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """An empty remote's first receive session is the default alone.

        GitHub never triggers push or pull_request workflows in a repository whose
        first push created several refs. A configured ``push.followTags`` must not
        add the default's label to that push; the label travels with the pair.
        """
        self._setup_pair(repo)
        _git(repo, "config", "push.followTags", "true")
        _git(repo, "tag", "-a", "version/0.0.0", "-m", "seed label", "main")
        bare = self._add_bare_remote(repo, tmp_path)
        log = self._log_receive_sessions(bare)

        result = push_working_pair("origin", repo, cwd=repo)

        first, *rest = self._receive_sessions(log)
        assert first == ["refs/heads/main"]
        assert [sorted(session) for session in rest] == [
            sorted([f"refs/heads/{WORKING}", f"refs/heads/{LEDGER}", "refs/tags/version/0.0.0"])
        ]
        assert result.bootstrapped_default is True
        assert result.pushed_refs == ["main", WORKING, LEDGER]

    def test_pair_refused_after_the_default_landed_names_the_published_default(
        self, repo: Path, tmp_path: Path
    ) -> None:
        from haute._git import GitBootstrapIncompleteError
        from haute._git_state import read_pushed_shas

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        self._refuse_all_but_main(bare)

        with pytest.raises(GitBootstrapIncompleteError) as exc:
            push_working_pair("origin", repo, cwd=repo)

        message = str(exc.value)
        assert message.startswith("'main' is now on 'origin' as your merge target")
        assert "did not accept" in message
        assert "SECRET-STDERR" not in message  # stderr is logged, never returned
        assert self._remote_refs(repo, bare) == ["refs/heads/main"]
        assert read_pushed_shas(repo) == {}

    def test_push_after_an_interrupted_bootstrap_publishes_the_pair(
        self, repo: Path, tmp_path: Path
    ) -> None:
        from haute._git import GitBootstrapIncompleteError
        from haute._git_state import read_pushed_shas

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        rule = self._refuse_all_but_main(bare)
        with pytest.raises(GitBootstrapIncompleteError):
            push_working_pair("origin", repo, cwd=repo)
        rule.unlink()

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.bootstrapped_default is False
        assert result.pushed_refs == [WORKING, LEDGER]
        assert self._remote_refs(repo, bare) == sorted(
            ["refs/heads/main", f"refs/heads/{WORKING}", f"refs/heads/{LEDGER}"]
        )
        assert read_pushed_shas(repo) == {
            f"origin/{WORKING}": _git(repo, "rev-parse", WORKING),
            f"origin/{LEDGER}": _git(repo, "rev-parse", LEDGER),
        }

    @pytest.mark.parametrize(
        "failure",
        [
            subprocess.TimeoutExpired(["git", "push"], 1),
            OSError("injected launch failure"),
            # A non-zero exit is not a refusal: the remote may have applied the refs.
            subprocess.CompletedProcess(
                ["git", "push"], 128, b"", b"fatal: the remote end hung up unexpectedly\n"
            ),
        ],
        ids=["timeout", "launch-failure", "lost-connection"],
    )
    def test_pair_transport_failure_after_the_default_landed_is_unconfirmed(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception | subprocess.CompletedProcess[bytes],
    ) -> None:
        from haute._git import GitBootstrapIncompleteError

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        real_run = git_core._run_process_group
        pushes = 0

        def fail_the_pair_push(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            nonlocal pushes
            if cmd[:2] == ["git", "push"]:
                pushes += 1
                if pushes == 2:
                    if isinstance(failure, Exception):
                        raise failure
                    return failure
            return real_run(cmd, **kwargs)

        monkeypatch.setattr(git_core, "_run_process_group", fail_the_pair_push)

        with pytest.raises(GitBootstrapIncompleteError, match="could not confirm"):
            push_working_pair("origin", repo, cwd=repo)

        assert self._remote_refs(repo, bare) == ["refs/heads/main"]

    def test_working_branch_landing_between_the_two_pushes_is_the_structured_rejection(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A teammate's same-named working branch can only land once the default exists."""
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        default_tip = _git(repo, "rev-parse", "main")
        teammate = tmp_path / "teammate"
        real_run = subprocess.run
        real_launch = git_core._run_process_group
        pushes = 0

        def teammate_pushes_in_the_gap(
            cmd: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            nonlocal pushes
            if cmd[:2] == ["git", "push"]:
                pushes += 1
                if pushes == 2:
                    identity = ["-c", "user.name=Teammate", "-c", "user.email=t@example.com"]
                    for args in (
                        ["clone", str(bare), str(teammate)],
                        [*identity, "-C", str(teammate), "commit", "--allow-empty", "-m", "theirs"],
                        ["-C", str(teammate), "push", "origin", f"HEAD:refs/heads/{WORKING}"],
                    ):
                        real_run(["git", *args], check=True, capture_output=True)
            return real_launch(cmd, **kwargs)

        monkeypatch.setattr(git_core, "_run_process_group", teammate_pushes_in_the_gap)

        with pytest.raises(GitPushRejectedError) as exc:
            push_working_pair("origin", repo, cwd=repo)

        # The remote has no ledger yet; the fork must still be measured, not "untracked".
        rejection = exc.value.rejection
        assert rejection.working.status in ("behind", "diverged")
        assert "working branch" in rejection.message
        remote = dict(
            reversed(line.split()) for line in _git(repo, "ls-remote", str(bare)).splitlines()
        )
        assert remote["refs/heads/main"] == default_tip
        assert remote[f"refs/heads/{WORKING}"] == _git(teammate, "rev-parse", "HEAD")
        assert f"refs/heads/{LEDGER}" not in remote  # the pair is atomic

    def test_remote_holds_no_other_history_when_empty_or_only_our_default(
        self, repo: Path, tmp_path: Path
    ) -> None:
        from haute._git import remote_holds_other_history

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        assert remote_holds_other_history("origin", repo, cwd=repo) is False

        _git(repo, "push", "origin", "main")

        assert remote_holds_other_history("origin", repo, cwd=repo) is False

    @pytest.mark.parametrize("extra", ["a tag", "another branch", "a default past ours"])
    def test_remote_holds_other_history_beyond_our_own_default(
        self, repo: Path, tmp_path: Path, extra: str
    ) -> None:
        from haute._git import remote_holds_other_history

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        main_sha = _git(repo, "rev-parse", "main")
        _git(repo, "tag", "-a", "elsewhere", "-m", "someone's label", "main")
        tree = _git(repo, "rev-parse", f"{main_sha}^{{tree}}")
        ahead = _git(repo, "commit-tree", tree, "-p", main_sha, "-m", "someone's commit")
        refspecs = {
            "a tag": ["refs/heads/main", "refs/tags/elsewhere"],
            "another branch": ["refs/heads/main", f"{main_sha}:refs/heads/other"],
            "a default past ours": [f"{ahead}:refs/heads/main"],
        }[extra]
        _git(repo, "push", "origin", *refspecs)

        assert remote_holds_other_history("origin", repo, cwd=repo) is True

    def test_remote_advertising_only_head_holds_other_history(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Hidden refs can leave an object-valued HEAD as the whole advertisement."""
        from haute._git import remote_holds_other_history

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "push", "origin", "main")
        _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(bare, "config", "uploadpack.hideRefs", "refs/heads/")

        assert remote_holds_other_history("origin", repo, cwd=repo) is True

    def test_remote_holds_other_history_propagates_an_unreachable_remote(
        self, repo: Path, tmp_path: Path
    ) -> None:
        from haute._git import remote_holds_other_history

        self._setup_pair(repo)
        _git(repo, "remote", "add", "origin", str(tmp_path / "missing.git"))

        with pytest.raises(GitError):
            remote_holds_other_history("origin", repo, cwd=repo)

    def test_remote_is_empty_only_until_it_advertises_a_ref(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """The editor's Push asks this to know a first publication is coming."""
        from haute._git import remote_is_empty

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        assert remote_is_empty("origin", cwd=repo) is True

        _git(repo, "tag", "outside")
        _git(repo, "push", "origin", "refs/tags/outside")
        assert remote_is_empty("origin", cwd=repo) is False

        _git(repo, "remote", "set-url", "origin", str(bare.parent / "missing.git"))
        with pytest.raises(GitError):
            remote_is_empty("origin", cwd=repo)

    def test_remote_push_urls_are_where_a_push_lands(self, repo: Path, tmp_path: Path) -> None:
        """A pushurl, not the fetch URL, is where the editor's Push sends the secrets."""
        from haute._git import remote_push_urls

        bare = self._add_bare_remote(repo, tmp_path)
        assert remote_push_urls("origin", cwd=repo) == [str(bare)]

        other = "https://github.com/acme/other.git"
        _git(repo, "remote", "set-url", "--add", "--push", "origin", other)
        assert remote_push_urls("origin", cwd=repo) == [other]
        _git(
            repo, "remote", "set-url", "--add", "--push", "origin", "git@github.com:acme/third.git"
        )
        assert remote_push_urls("origin", cwd=repo) == [other, "git@github.com:acme/third.git"]
        assert remote_push_urls("absent", cwd=repo) == []

    def test_tags_only_remote_is_not_mistaken_for_empty(self, repo: Path, tmp_path: Path) -> None:
        """A tag is an object ref, so it cannot authorize default bootstrap."""
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "tag", "outside")
        _git(repo, "push", "origin", "refs/tags/outside")

        with pytest.raises(GitDomainError, match="default branch"):
            push_working_pair("origin", repo, cwd=repo)

        assert "refs/heads/" + WORKING not in _git(repo, "ls-remote", str(bare))

    def test_unrelated_established_default_refuses_before_pair_publish(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        other = tmp_path / "unrelated"
        _git(tmp_path, "init", str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        (other / "other.txt").write_text("unrelated\n")
        _git(other, "add", "other.txt")
        _git(other, "commit", "-m", "unrelated")
        _git(other, "branch", "-M", "main")
        _git(other, "remote", "add", "origin", str(bare))
        _git(other, "push", "origin", "main")

        with pytest.raises(GitDomainError, match="unrelated"):
            push_working_pair("origin", repo, cwd=repo)

        assert "refs/heads/" + WORKING not in _git(repo, "ls-remote", str(bare))

    def test_empty_remote_refuses_unrelated_local_default_and_pair(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Bootstrap cannot publish an orphan working history beside the merge target."""
        from haute._git_state import write_working_branch

        orphan = "orphan-work"
        _git(repo, "checkout", "--orphan", orphan)
        _git(repo, "rm", "-rf", ".")
        (repo / "orphan.txt").write_text("unrelated\n")
        _git(repo, "add", "orphan.txt")
        _git(repo, "commit", "-m", "orphan root")
        write_working_branch(repo, orphan)
        bare = self._add_bare_remote(repo, tmp_path)

        with pytest.raises(GitDomainError, match="unrelated"):
            push_working_pair("origin", repo, cwd=repo)

        assert _git(repo, "ls-remote", str(bare)) == ""

    def test_empty_remote_bootstraps_a_unique_custom_default(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # The managed pair must not make custom ``trunk`` resolution ambiguous.
        self._setup_pair(repo)
        _git(repo, "branch", "trunk", "main")
        _git(repo, "branch", "-D", "main")
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "trunk"
        assert result.pushed_refs == ["trunk", WORKING, LEDGER]

    def test_push_without_a_working_branch_is_refused(self, repo: Path, tmp_path: Path) -> None:
        self._add_bare_remote(repo, tmp_path)  # no working-branch state recorded
        with pytest.raises(GitDomainError, match="No working branch"):
            push_working_pair("origin", repo, cwd=repo)

    @pytest.mark.parametrize("corrupted_working", ["HEAD", "@"])
    def test_push_rejects_pseudoref_as_corrupted_working_branch_state(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        corrupted_working: str,
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, corrupted_working)
        self._add_bare_remote(repo, tmp_path)
        monkeypatch.setattr(
            git_remote,
            "_inspect_remote",
            lambda *args, **kwargs: pytest.fail("invalid clone state reached remote inspection"),
        )

        with pytest.raises(GitDomainError, match="Invalid working branch"):
            push_working_pair("origin", repo, cwd=repo)

    def test_option_like_working_state_is_rejected_before_git_subprocess(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        monkeypatch.setattr(
            git_core,
            "_run_git_ok",
            lambda *args, **kwargs: pytest.fail("invalid state reached a Git subprocess"),
        )

        with pytest.raises(GitDomainError, match="Invalid working branch"):
            git_mod._validate_managed_working_branch("--upload-pack=attacker", cwd=repo)

    def test_tag_named_like_working_branch_does_not_satisfy_branch_state(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import write_working_branch

        working = "tag-only-working"
        _git(repo, "tag", working, "main")
        write_working_branch(repo, working)
        self._add_bare_remote(repo, tmp_path)
        monkeypatch.setattr(
            git_remote,
            "_inspect_remote",
            lambda *args, **kwargs: pytest.fail("missing branch reached remote inspection"),
        )

        with pytest.raises(GitDomainError, match="does not exist"):
            push_working_pair("origin", repo, cwd=repo)

    def test_tag_named_like_missing_ledger_is_not_published_as_a_branch(
        self, repo: Path, tmp_path: Path
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _git(repo, "tag", LEDGER, "main")
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.pushed_refs == ["main", WORKING]
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{LEDGER}") == ""

    def test_push_never_force_overwrites_a_diverged_remote(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # The critical S33 guarantee: a non-fast-forward push is refused and the
        # remote ref is left untouched — never force-overwritten.
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)  # initial sync

        # A teammate advances the remote's working branch out from under us.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        (other / "rating.py").write_text("# remote edit\n")
        _git(other, "commit", "-am", "remote change")
        _git(other, "push", "origin", WORKING)
        remote_tip = _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}")

        # We advance our own working branch on a different line → divergence.
        _write_and_save(repo, WORKING, {"rating.py": "# local edit\n"})
        commit_milestone("local milestone", repo, cwd=repo)

        with pytest.raises(GitDomainError, match="never force-pushes"):
            push_working_pair("origin", repo, cwd=repo)
        # The remote ref is exactly as the teammate left it — no force overwrite.
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}") == remote_tip

    def test_rejection_is_data_bearing_with_per_leg_divergence(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # M7/M6: a non-FF rejection raises GitPushRejectedError carrying the freshly
        # recomputed per-leg divergence, and the message names the BLOCKING leg.
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)  # initial sync

        # A teammate diverges the WORKING branch on the remote (working only).
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        (other / "rating.py").write_text("# remote edit\n")
        _git(other, "commit", "-am", "remote change")
        _git(other, "push", "origin", WORKING)

        # We advance our own working branch on a different line via a milestone.
        _write_and_save(repo, WORKING, {"rating.py": "# local edit\n"})
        commit_milestone("local milestone", repo, cwd=repo)

        with pytest.raises(GitPushRejectedError) as exc:
            push_working_pair("origin", repo, cwd=repo)
        rej = exc.value.rejection
        assert rej.status == "rejected_diverged"
        assert rej.remote == "origin"
        assert rej.working.status == "diverged"
        # Our local save left the ledger ahead-only (teammate never pushed it), so
        # it isn't a blocking leg and the message names the working branch only.
        assert rej.ledger is not None and rej.ledger.status == "ahead"
        assert "working branch" in rej.message
        assert "save history" not in rej.message
        assert "never force-pushes" in rej.message

    def test_push_sends_version_label_tags(self, repo: Path, tmp_path: Path) -> None:
        # X4: annotated version/<label> tags travel with the push (--follow-tags).
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        _write_and_save(repo, WORKING, {"rating.py": "# v1\n"})
        commit_milestone("milestone 1", repo, version_label="1.0", cwd=repo)
        push_working_pair("origin", repo, cwd=repo)
        assert "refs/tags/version/1.0" in _git(repo, "ls-remote", "--tags", "origin")

    def test_push_refuses_a_reused_version_label(self, repo: Path, tmp_path: Path) -> None:
        # X4 / decision A: a label already on the remote at a different object is a
        # reused canonical name → refuse before the push.
        from haute._git_state import write_working_branch

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)  # sync, no tags yet

        # Clone before any label exists, so 'other' won't have it locally and the
        # local create-time dup-check can't pre-empt the push-time collision check.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        write_working_branch(other, WORKING)
        resolve_ledger(WORKING, cwd=other)
        (other / "rating.py").write_text("# other v\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        commit_milestone("other milestone", other, version_label="1.0", cwd=other)

        # Meanwhile the canonical 1.0 is published on a DIFFERENT milestone.
        _write_and_save(repo, WORKING, {"rating.py": "# repo v\n"})
        commit_milestone("repo milestone", repo, version_label="1.0", cwd=repo)
        push_working_pair("origin", repo, cwd=repo)

        with pytest.raises(GitDomainError, match="already exist"):
            push_working_pair("origin", other, cwd=other)

    def test_push_is_idempotent_for_a_published_version_label(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # X4 regression (the blocker): version/<label> tags are ANNOTATED, so the
        # tag object's sha != the commit it points to. The collision pre-check
        # compares the underlying release COMMIT (peeled both sides), so a label
        # already on the remote at the SAME commit is a clean idempotent re-push —
        # NOT a reused-name refusal. Comparing tag objects false-positived here.
        import haute._git as git_mod

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        _write_and_save(repo, WORKING, {"rating.py": "# v1\n"})
        commit_milestone("milestone 1", repo, version_label="1.0", cwd=repo)
        push_working_pair("origin", repo, cwd=repo)  # publishes version/1.0
        assert "refs/tags/version/1.0" in _git(repo, "ls-remote", "--tags", "origin")
        # The published label points at THIS release commit → not a collision.
        assert git_mod._tag_collisions("origin", WORKING, cwd=repo) == []
        # …and the whole pair re-pushes cleanly (pre-fix this raised "already exist").
        res = push_working_pair("origin", repo, cwd=repo)
        assert res.pushed_refs == [WORKING, LEDGER]

    def test_leg_state_unknown_when_count_is_unreadable(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # F2 tri-state: both refs resolve, but the rev-list ahead/behind count comes
        # back unreadable → status "unknown", never silently "synced". The UI must
        # not render "can't tell" as "in sync".
        import haute._git as git_mod

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        _git(repo, "fetch", "origin")  # both branch and remote-tracking ref resolve
        real_ok = git_core._run_git_ok

        def malformed_count(*args: str, **kwargs: object) -> tuple[bool, str]:
            # Only intercept the rev-list count; let the _rev_parse probes through.
            if args and args[0] == "rev-list":
                return True, "not-a-number garbage"  # parses to a non-int → unknown
            return real_ok(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(git_core, "_run_git_ok", malformed_count)
        leg = git_mod._leg_state(WORKING, "origin", cwd=repo)
        assert leg.status == "unknown"
        assert leg.ahead is None and leg.behind is None

        def failed_revlist(*args: str, **kwargs: object) -> tuple[bool, str]:
            if args and args[0] == "rev-list":
                return False, ""  # the rev-list itself fails → same tri-state
            return real_ok(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(git_core, "_run_git_ok", failed_revlist)
        assert git_mod._leg_state(WORKING, "origin", cwd=repo).status == "unknown"

    def test_push_records_last_pushed_shas(self, repo: Path, tmp_path: Path) -> None:
        # §6.8: a successful push records the published tips (keyed <remote>/<ref>)
        # so X3 rewrite detection survives a pruned reflog.
        from haute._git_state import read_pushed_shas

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        recorded = read_pushed_shas(repo)
        assert recorded[f"origin/{WORKING}"] == _git(repo, "rev-parse", WORKING)
        assert recorded[f"origin/{LEDGER}"] == _git(repo, "rev-parse", LEDGER)

    def test_rejection_flags_a_remote_rewrite(self, repo: Path, tmp_path: Path) -> None:
        # X3: when the remote dropped a commit we published (a force-push upstream),
        # the rejection is flagged as a rewrite with a distinct message.

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _write_and_save(repo, WORKING, {"rating.py": "# m1\n"})
        commit_milestone("m1", repo, cwd=repo)
        push_working_pair("origin", repo, cwd=repo)  # records origin/<W> = m1

        # A teammate force-pushes a DIFFERENT line over the working branch.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        root = _git(other, "rev-list", "--max-parents=0", WORKING)
        _git(other, "reset", "--hard", root)  # back before the published milestone
        (other / "rating.py").write_text("# rewritten\n")
        _git(other, "add", "rating.py")
        _git(other, "commit", "-m", "rewritten line")
        _git(other, "push", "--force", "origin", WORKING)

        with pytest.raises(GitPushRejectedError) as exc:
            push_working_pair("origin", repo, cwd=repo)
        assert exc.value.rejection.is_rewrite is True
        assert "rewritten" in exc.value.rejection.message


class TestFastForwardPair:
    """D1/D2: conflict-free catch-up to the remote by fast-forward only. Refuses
    anything that isn't a clean ff (local work present → branch-away)."""

    def _setup_pair(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        resolve_ledger(WORKING, cwd=repo)
        write_working_branch(repo, WORKING)

    def _add_bare_remote(self, repo: Path, tmp_path: Path) -> Path:
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        return bare

    def _clone_with_pair(self, repo: Path, bare: Path, tmp_path: Path) -> Path:
        from haute._git_state import write_working_branch

        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        write_working_branch(other, WORKING)
        resolve_ledger(WORKING, cwd=other)
        return other

    def test_d1_fast_forwards_both_legs_when_behind_clean(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        # Teammate milestones and pushes the whole pair → both legs move ahead.
        other = self._clone_with_pair(repo, bare, tmp_path)
        (other / "rating.py").write_text("# teammate\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        commit_milestone("teammate milestone", other, cwd=other)
        push_working_pair("origin", other, cwd=other)

        res = fast_forward_pair("origin", repo, cwd=repo)
        assert set(res.fast_forwarded) == {WORKING, LEDGER}
        assert _git(repo, "rev-parse", WORKING) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{WORKING}"
        )
        assert _git(repo, "rev-parse", LEDGER) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{LEDGER}"
        )
        assert check_invariants(WORKING, cwd=repo) == []  # healthy after the catch-up

    def test_required_fetch_failure_refuses_before_any_ref_mutation(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        working_tip = _git(repo, "rev-parse", WORKING)
        ledger_tip = _git(repo, "rev-parse", LEDGER)
        monkeypatch.setattr(git_remote, "_fetch_refs", lambda *_args, **_kwargs: False)

        with pytest.raises(GitDomainError, match="Could not refresh 'origin'"):
            fast_forward_pair("origin", repo, cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER

    @pytest.mark.parametrize(
        ("deleted_branch", "kind"),
        [(WORKING, "working branch"), (LEDGER, "save ledger")],
    )
    def test_deleted_remote_leg_has_a_distinct_refusal_after_prune(
        self, repo: Path, tmp_path: Path, deleted_branch: str, kind: str
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        _git(repo, "fetch", "origin")
        working_tip = _git(repo, "rev-parse", WORKING)
        ledger_tip = _git(repo, "rev-parse", LEDGER)
        tracking = f"origin/{deleted_branch}"
        assert _git(repo, "branch", "--remotes", "--list", tracking) != ""

        # Delete directly in the bare remote so this clone's tracking ref stays
        # stale until Catch up performs its required authoritative refresh.
        _git(bare, "update-ref", "-d", f"refs/heads/{deleted_branch}")

        with pytest.raises(
            GitDomainError,
            match=rf"{kind} '{deleted_branch}' is missing",
        ):
            fast_forward_pair("origin", repo, cwd=repo)

        assert _git(repo, "branch", "--remotes", "--list", tracking) == ""
        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip

    def test_d2_fast_forwards_ledger_only(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        # Teammate saves (a ledger commit) and pushes only the ledger.
        other = self._clone_with_pair(repo, bare, tmp_path)
        (other / "rating.py").write_text("# teammate save\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        _git(other, "push", "origin", LEDGER)

        res = fast_forward_pair("origin", repo, cwd=repo)
        assert res.fast_forwarded == [LEDGER]
        assert _git(repo, "rev-parse", LEDGER) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{LEDGER}"
        )

    def test_refuses_when_local_has_unpushed_work(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        other = self._clone_with_pair(repo, bare, tmp_path)
        (other / "rating.py").write_text("# teammate\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        _git(other, "push", "origin", LEDGER)
        # Local also saved on a different line → the ledger diverges, not a clean ff.
        _write_and_save(repo, WORKING, {"local.py": "# local\n"})
        with pytest.raises(GitDomainError, match="Spin off a copy"):
            fast_forward_pair("origin", repo, cwd=repo)

    def test_refuses_when_already_synced(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        with pytest.raises(GitDomainError, match="Already up to date"):
            fast_forward_pair("origin", repo, cwd=repo)
