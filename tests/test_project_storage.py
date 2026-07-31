"""Durable project storage for hosted sessions (``haute._project_storage``).

The headline scenario is the one the feature exists for: a hosted
container's filesystem is destroyed on every redeploy, so work must
survive the container. ``TestContainerDeathSurvival`` proves it end to
end against a real ``file://`` bare repository standing in for the
remote — bind, save, destroy the "container", restore into a fresh
directory, and find the history intact.

The remaining classes pin the pieces that make that safe: URL
validation (no credentials in URLs), binding-record semantics (an
unreadable record must never read as "unbound"), the askpass helper
(the token never reaches a file, a config, or a command line), and the
push queue's state machine (coalescing, retry gating, terminal
failures).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from haute import _git, _project_storage
from haute._git import GitDomainError, GitPushRejectedError
from haute._project_storage import (
    GIT_TOKEN_ENV,
    STATE_VOLUME_ENV,
    PushQueue,
    StorageBinding,
    StorageConfigError,
    StorageUnavailableError,
)
from haute.schemas import GitPushRejection, GitRemoteLeg

WORKING = "pricing-dev"


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(autouse=True)
def _isolated_storage_state(monkeypatch: pytest.MonkeyPatch):
    """Keep module singletons and env out of each other's way.

    The queue and active binding are process-level singletons (one hosted
    container serves one project); tests must not inherit each other's.
    """
    monkeypatch.delenv(STATE_VOLUME_ENV, raising=False)
    monkeypatch.delenv(GIT_TOKEN_ENV, raising=False)
    monkeypatch.delenv("GIT_ASKPASS", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    monkeypatch.setattr(_project_storage, "_queue", PushQueue())
    monkeypatch.setattr(_project_storage, "_active_binding", None)
    yield
    _project_storage.push_queue().stop()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A haute-shaped project: a repo with a managed working branch + ledger."""
    root = tmp_path / "project"
    root.mkdir()
    _run_git(root, "init", "-b", "main")
    _run_git(root, "config", "user.name", "Test Actuary")
    _run_git(root, "config", "user.email", "test@example.com")
    (root / "rating.py").write_text("# pipeline\n", encoding="utf-8")
    _run_git(root, "add", "rating.py")
    _run_git(root, "commit", "-m", "initial pipeline")
    # cwd is NOT optional here: without it every git command would run against
    # the process's own checkout instead of this fixture's repository.
    _git.set_working_branch(WORKING, root, cwd=root, create=True)
    return root


@pytest.fixture()
def bare_remote(tmp_path: Path) -> Path:
    """An empty bare repository, used as a real remote over ``file://``."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return remote


class _FakeFiles:
    """In-memory stand-in for the Databricks Files API."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.fail_with: Exception | None = None

    def download(self, path: str):
        if self.fail_with is not None:
            raise self.fail_with
        if path not in self.store:
            raise _FakeNotFoundError(path)

        class _Response:
            contents = _Reader(self.store[path])

        return _Response()

    def upload(self, path: str, contents, overwrite: bool = False) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.store[path] = contents.read()


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeNotFoundError(Exception):
    pass


@pytest.fixture()
def files_api(monkeypatch: pytest.MonkeyPatch) -> _FakeFiles:
    """Route binding reads/writes to an in-memory store."""
    fake = _FakeFiles()
    monkeypatch.setattr(_project_storage, "_files_api", lambda: fake)
    monkeypatch.setattr(
        _project_storage, "_is_not_found", lambda exc: isinstance(exc, _FakeNotFoundError)
    )
    monkeypatch.setenv(STATE_VOLUME_ENV, "workspace.default.haute_state")
    return fake


# ---------------------------------------------------------------------------
# Remote URL validation
# ---------------------------------------------------------------------------


class TestRemoteUrlValidation:
    @pytest.mark.parametrize(
        "url",
        ["https://github.com/org/repo.git", "file:///tmp/remote.git"],
    )
    def test_supported_schemes_are_accepted(self, url: str) -> None:
        assert _project_storage.validate_remote_url(f"  {url} ") == url

    @pytest.mark.parametrize("url", ["ssh://git@host/r.git", "http://host/r.git", "git@host:r.git"])
    def test_unsupported_schemes_name_the_accepted_form(self, url: str) -> None:
        with pytest.raises(StorageConfigError, match="https://"):
            _project_storage.validate_remote_url(url)

    def test_embedded_credentials_are_refused(self) -> None:
        """A URL credential would land in .git/config and every remote log line."""
        with pytest.raises(StorageConfigError, match=GIT_TOKEN_ENV):
            _project_storage.validate_remote_url("https://user:token@github.com/org/repo.git")

    def test_empty_url_asks_for_one(self) -> None:
        with pytest.raises(StorageConfigError, match="Enter the HTTPS URL"):
            _project_storage.validate_remote_url("   ")


class TestCredentialHostAllowlist:
    """The app's git token must not travel to a host a caller chose.

    GIT_ASKPASS is process-wide and git offers the credential to whatever
    host the URL names, so any user who can reach the bind endpoint could
    otherwise point it at a host they control and collect the token from
    the auth challenge.
    """

    def test_no_token_means_no_restriction(self) -> None:
        assert (
            _project_storage.validate_remote_url("https://anywhere.example/r.git")
            == "https://anywhere.example/r.git"
        )

    def test_token_without_allowlist_refuses_every_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(GIT_TOKEN_ENV, "t")
        with pytest.raises(StorageConfigError, match=_project_storage.GIT_ALLOWED_HOSTS_ENV):
            _project_storage.validate_remote_url("https://github.com/org/r.git")

    def test_unapproved_host_is_refused_and_names_the_approved_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(GIT_TOKEN_ENV, "t")
        monkeypatch.setenv(_project_storage.GIT_ALLOWED_HOSTS_ENV, "github.com")
        with pytest.raises(StorageConfigError, match="attacker.example"):
            _project_storage.validate_remote_url("https://attacker.example/x.git")

    def test_approved_host_passes_including_a_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(GIT_TOKEN_ENV, "t")
        monkeypatch.setenv(_project_storage.GIT_ALLOWED_HOSTS_ENV, "github.com, git.corp.example")
        assert _project_storage.validate_remote_url("https://git.corp.example:8443/r.git")

    def test_file_urls_are_unaffected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A file:// remote has no host and receives no credential."""
        monkeypatch.setenv(GIT_TOKEN_ENV, "t")
        assert _project_storage.validate_remote_url("file:///tmp/r.git")

    def test_bind_refuses_before_any_git_command_runs(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal must precede ls-remote, which is what leaks the token."""
        monkeypatch.setenv(GIT_TOKEN_ENV, "t")
        monkeypatch.setenv(_project_storage.GIT_ALLOWED_HOSTS_ENV, "github.com")

        def fail(*args: object, **kwargs: object):
            raise AssertionError("a git subprocess must not see an unapproved URL")

        monkeypatch.setattr(_git, "ensure_remote", fail)
        monkeypatch.setattr(_git, "remote_has_content", fail)
        with pytest.raises(StorageConfigError):
            _project_storage.bind_remote("https://attacker.example/x.git", project)


# ---------------------------------------------------------------------------
# Binding record
# ---------------------------------------------------------------------------


class TestBindingRecord:
    def test_round_trips_through_json(self) -> None:
        binding = StorageBinding(
            remote_url="https://host/r.git",
            branch=WORKING,
            bound_by="someone@example.com",
            bound_at="2026-07-30T00:00:00+00:00",
        )
        assert StorageBinding.from_payload(json.loads(binding.to_json())) == binding

    def test_unknown_fields_are_tolerated(self) -> None:
        """A record written by a newer haute must not brick an older container."""
        binding = StorageBinding.from_payload(
            {"remote_url": "https://host/r.git", "future_field": {"nested": 1}}
        )
        assert binding.remote_url == "https://host/r.git"
        assert binding.branch is None

    @pytest.mark.parametrize("payload", [{}, {"remote_url": "  "}, [], "text"])
    def test_malformed_records_fail_loudly(self, payload: object) -> None:
        with pytest.raises(StorageUnavailableError):
            StorageBinding.from_payload(payload)

    def test_state_volume_must_be_three_parts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(STATE_VOLUME_ENV, "workspace.default")
        with pytest.raises(StorageConfigError, match="three-part"):
            _project_storage.binding_file_path()

    def test_binding_path_is_scoped_per_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(STATE_VOLUME_ENV, "workspace.default.haute_state")
        monkeypatch.setenv("DATABRICKS_APP_NAME", "haute-spike")
        assert _project_storage.binding_file_path() == (
            "/Volumes/workspace/default/haute_state/haute-apps/haute-spike/binding.json"
        )

    def test_absent_record_reads_as_unbound(self, files_api: _FakeFiles) -> None:
        assert _project_storage.read_binding() is None

    def test_unreadable_record_is_never_mistaken_for_unbound(self, files_api: _FakeFiles) -> None:
        """The invariant that protects durable work from being overwritten."""
        files_api.fail_with = RuntimeError("volume unreachable")
        with pytest.raises(StorageUnavailableError):
            _project_storage.read_binding()

    def test_corrupt_json_is_not_unbound_either(self, files_api: _FakeFiles) -> None:
        files_api.store[_project_storage.binding_file_path()] = b"{not json"
        with pytest.raises(StorageUnavailableError):
            _project_storage.read_binding()

    def test_write_then_read(self, files_api: _FakeFiles) -> None:
        binding = StorageBinding(remote_url="https://host/r.git", branch=WORKING)
        _project_storage.write_binding(binding)
        assert _project_storage.read_binding() == binding


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class TestCredentialHandling:
    def test_no_token_installs_no_helper(self, tmp_path: Path) -> None:
        assert _project_storage.configure_git_credentials(tmp_path / "runtime") is None
        assert "GIT_ASKPASS" not in __import__("os").environ

    def test_helper_never_contains_the_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(GIT_TOKEN_ENV, "super-secret-token")
        helper = _project_storage.configure_git_credentials(tmp_path / "runtime")
        assert helper is not None
        contents = helper.read_text(encoding="utf-8")
        assert "super-secret-token" not in contents
        # It reads the value from the environment at call time instead.
        assert GIT_TOKEN_ENV in contents

    def test_helper_is_owner_only_and_registered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os
        import stat

        monkeypatch.setenv(GIT_TOKEN_ENV, "t")
        helper = _project_storage.configure_git_credentials(tmp_path / "runtime")
        assert helper is not None
        assert stat.S_IMODE(helper.stat().st_mode) == 0o700
        assert os.environ["GIT_ASKPASS"] == str(helper)
        assert os.environ["GIT_TERMINAL_PROMPT"] == "0"

    def test_helper_emits_username_and_token_on_the_right_prompts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Git asks twice; the helper must answer each prompt correctly."""
        monkeypatch.setenv(GIT_TOKEN_ENV, "super-secret-token")
        helper = _project_storage.configure_git_credentials(tmp_path / "runtime")
        assert helper is not None

        def ask(prompt: str) -> str:
            result = subprocess.run(
                [str(helper), prompt], capture_output=True, text=True, check=True
            )
            return result.stdout

        assert ask("Username for 'https://github.com': ") == "x-access-token"
        assert ask("Password for 'https://x@github.com': ") == "super-secret-token"


# ---------------------------------------------------------------------------
# Push queue state machine
# ---------------------------------------------------------------------------


def _rejection() -> GitPushRejectedError:
    leg = GitRemoteLeg(branch=WORKING, status="diverged", ahead=1, behind=1)
    return GitPushRejectedError(GitPushRejection(remote="origin", message="diverged", working=leg))


class _RecordingPush:
    """Stands in for ``_git.push_working_pair`` with scripted outcomes."""

    def __init__(self, *outcomes: Exception | None) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0
        self.done = threading.Event()

    def __call__(self, remote: str, project_root: Path, cwd: Path | None = None):
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else None
        self.done.set()
        if outcome is not None:
            raise outcome
        return None


class TestPushQueue:
    """The queue's contract: never block a save, never lose a commit, never spin."""

    def test_status_starts_synced(self) -> None:
        assert PushQueue().status().state == "synced"

    def test_enqueue_is_inert_until_started(self) -> None:
        """A local (unbound) session must not queue anything."""
        queue = PushQueue()
        queue.enqueue()
        assert queue.status() == _project_storage.SyncStatus(state="synced")

    def test_successful_push_clears_pending(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        push = _RecordingPush()
        monkeypatch.setattr(_git, "push_working_pair", push)
        queue = PushQueue()
        queue.start(project)
        queue.enqueue()
        assert push.done.wait(timeout=5)
        _wait_until(lambda: queue.status().state == "synced")
        queue.stop()

    def test_multiple_saves_coalesce_into_one_push(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N queued commits publish in one attempt — the push sends ref state."""
        release = threading.Event()
        calls = {"count": 0}

        def slow_push(remote, project_root, cwd=None):
            calls["count"] += 1
            release.wait(timeout=5)

        monkeypatch.setattr(_git, "push_working_pair", slow_push)
        queue = PushQueue()
        queue.start(project)
        queue.enqueue()
        _wait_until(lambda: calls["count"] == 1)
        for _ in range(5):
            queue.enqueue()
        release.set()
        _wait_until(lambda: queue.status().state == "synced")
        # One push for the first save, one for the five that arrived during it.
        assert calls["count"] == 2
        queue.stop()

    def test_transport_failure_reports_and_retries_on_next_save(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        push = _RecordingPush(OSError("network down"), None)
        monkeypatch.setattr(_git, "push_working_pair", push)
        queue = PushQueue()
        queue.start(project)
        queue.enqueue()
        _wait_until(lambda: queue.status().state == "failed")

        status = queue.status()
        assert status.failure == "transport"
        assert status.pending == 1
        assert "retry" in (status.message or "").lower()

        queue.enqueue()  # the next save clears the block
        _wait_until(lambda: queue.status().state == "synced")
        queue.stop()

    def test_rejected_push_is_terminal_until_manual_retry(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A diverged remote must not be hammered by every subsequent save."""
        push = _RecordingPush(_rejection(), _rejection(), None)
        monkeypatch.setattr(_git, "push_working_pair", push)
        queue = PushQueue()
        queue.start(project)
        queue.enqueue()
        _wait_until(lambda: queue.status().state == "failed")
        assert queue.status().failure == "rejected"

        queue.enqueue()  # further saves must NOT trigger another attempt
        _sleep_briefly()
        assert push.calls == 1

        queue.retry_now()
        _wait_until(lambda: push.calls == 2)
        queue.stop()

    def test_domain_error_surfaces_its_own_message(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hand-authored guardrail text is user-facing and safe verbatim."""
        push = _RecordingPush(GitDomainError("No working branch is set for this clone."))
        monkeypatch.setattr(_git, "push_working_pair", push)
        queue = PushQueue()
        queue.start(project)
        queue.enqueue()
        _wait_until(lambda: queue.status().state == "failed")
        status = queue.status()
        assert status.failure == "config"
        assert status.message == "No working branch is set for this clone."
        queue.stop()

    def test_failure_message_never_carries_raw_git_stderr(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret_ish = f"fatal: could not read Password for 'https://token@host': {project}"
        push = _RecordingPush(_git.GitError(secret_ish))
        monkeypatch.setattr(_git, "push_working_pair", push)
        queue = PushQueue()
        queue.start(project)
        queue.enqueue()
        _wait_until(lambda: queue.status().state == "failed")
        message = queue.status().message or ""
        assert "token@host" not in message
        assert str(project) not in message
        queue.stop()


# ---------------------------------------------------------------------------
# The scenario the feature exists for
# ---------------------------------------------------------------------------


class TestContainerDeathSurvival:
    """Bind, work, lose the container, and get the work back."""

    def test_bind_adopts_an_empty_remote_and_publishes(
        self, project: Path, bare_remote: Path, files_api: _FakeFiles
    ) -> None:
        outcome = _project_storage.bind_remote(
            f"file://{bare_remote}", project, bound_by="someone@example.com"
        )
        assert outcome == "adopted"

        # The remote now carries the working branch.
        remote_branches = _run_git(bare_remote, "branch", "--list")
        assert WORKING in remote_branches
        # ... and the binding was recorded durably, with who bound it.
        binding = _project_storage.read_binding()
        assert binding is not None
        assert binding.branch == WORKING
        assert binding.bound_by == "someone@example.com"

    def test_populated_remote_defers_to_a_restart(
        self, project: Path, bare_remote: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """Lifting another project over a running server is not done live."""
        other = tmp_path / "other"
        other.mkdir()
        _run_git(other, "init", "-b", "main")
        _run_git(other, "config", "user.name", "Other")
        _run_git(other, "config", "user.email", "other@example.com")
        (other / "f.txt").write_text("x", encoding="utf-8")
        _run_git(other, "add", "-A")
        _run_git(other, "commit", "-m", "other project")
        _run_git(other, "push", f"file://{bare_remote}", "main")

        outcome = _project_storage.bind_remote(f"file://{bare_remote}", project)
        assert outcome == "restart-required"
        # The binding is recorded, but this process is NOT publishing: the
        # local project is not the bound remote's project.
        assert _project_storage.read_binding() is not None
        assert _project_storage.active_binding() is None

    def test_saved_work_survives_container_replacement(
        self,
        project: Path,
        bare_remote: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The headline guarantee, end to end.

        Bind → save → publish → destroy the container → restore into a
        fresh directory → the save is there.
        """
        _project_storage.bind_remote(f"file://{bare_remote}", project)

        (project / "rating.py").write_text("# priced\n", encoding="utf-8")
        sha = _git.commit_save(["rating.py"], WORKING, cwd=project)
        assert sha is not None

        # Publish synchronously — the queue's own timing is covered above.
        _git.push_working_pair("origin", project, cwd=project)

        # The container is replaced: a brand-new filesystem, same binding.
        restored_root = tmp_path / "new-container"
        monkeypatch.setattr(_project_storage, "_queue", PushQueue())
        outcome = _project_storage.restore_if_bound(restored_root)

        assert outcome == "restored"
        assert (restored_root / ".git").is_dir()
        ledger_log = _run_git(restored_root, "log", "--format=%H", f"origin/{WORKING}-save")
        assert sha in ledger_log.splitlines()

        # A restored session must be USABLE, not merely present: the working
        # branch and its ledger must exist as local refs (a plain clone only
        # materialises the remote's default branch), and the session must be
        # able to publish again without the user re-adopting a branch.
        local_branches = _run_git(restored_root, "branch", "--format=%(refname:short)").splitlines()
        assert WORKING in local_branches
        assert f"{WORKING}-save" in local_branches
        (restored_root / "rating.py").write_text("# repriced\n", encoding="utf-8")
        next_sha = _git.commit_save(["rating.py"], WORKING, cwd=restored_root)
        assert next_sha is not None
        _git.push_working_pair("origin", restored_root, cwd=restored_root)
        # The working branch is recorded again, so the session resumes on the
        # same lineage rather than asking the user to pick one.
        from haute._git_state import read_working_branch

        assert read_working_branch(restored_root) == WORKING

    def test_restore_without_a_binding_is_unbound(
        self, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        assert _project_storage.restore_if_bound(tmp_path / "fresh") == "unbound"

    def test_restore_gates_when_the_record_is_unreadable(
        self, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """Never seed a fresh project over durable work."""
        files_api.fail_with = RuntimeError("volume unreachable")
        with pytest.raises(StorageUnavailableError):
            _project_storage.restore_if_bound(tmp_path / "fresh")

    def test_existing_clone_is_reused_not_recloned(
        self, project: Path, bare_remote: Path, files_api: _FakeFiles
    ) -> None:
        _project_storage.bind_remote(f"file://{bare_remote}", project)
        assert _project_storage.restore_if_bound(project) == "present"

    def test_existing_clone_of_a_different_remote_is_refused(
        self, project: Path, bare_remote: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """Reusing a stale directory would publish one project into another."""
        _project_storage.bind_remote(f"file://{bare_remote}", project)
        elsewhere = tmp_path / "elsewhere.git"
        subprocess.run(["git", "init", "--bare", str(elsewhere)], check=True, capture_output=True)
        _git.ensure_remote("origin", f"file://{elsewhere}", cwd=project)
        with pytest.raises(StorageUnavailableError, match="different repository"):
            _project_storage.restore_if_bound(project)

    def test_rebinding_a_live_project_is_refused(
        self, project: Path, bare_remote: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """Repointing origin under a live publisher would misdirect history."""
        _project_storage.bind_remote(f"file://{bare_remote}", project)
        other = tmp_path / "other.git"
        subprocess.run(["git", "init", "--bare", str(other)], check=True, capture_output=True)
        with pytest.raises(StorageConfigError, match="already bound"):
            _project_storage.bind_remote(f"file://{other}", project)

    def test_binding_naming_a_branch_the_remote_lacks_fails_loudly(
        self, project: Path, bare_remote: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """Better a clear boot failure than a session on a phantom branch."""
        _project_storage.bind_remote(f"file://{bare_remote}", project)
        _project_storage.write_binding(
            StorageBinding(remote_url=f"file://{bare_remote}", branch="never-pushed")
        )
        with pytest.raises(GitDomainError, match="never-pushed"):
            _project_storage.restore_if_bound(tmp_path / "fresh-container")

    def test_binding_requires_a_state_volume(self, project: Path, bare_remote: Path) -> None:
        """Without somewhere durable to record it, a binding is a false promise."""
        with pytest.raises(StorageConfigError, match=STATE_VOLUME_ENV):
            _project_storage.bind_remote(f"file://{bare_remote}", project)


# ---------------------------------------------------------------------------
# Small polling helpers (the queue is a background thread)
# ---------------------------------------------------------------------------


def _wait_until(predicate, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached within timeout")


def _sleep_briefly() -> None:
    """Give a worker thread a chance to do something it must NOT do."""
    import time

    time.sleep(0.2)
