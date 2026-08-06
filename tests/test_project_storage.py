"""Durable project storage for hosted sessions (``haute._project_storage``).

The headline scenario is the one the feature exists for: a hosted
container's filesystem is destroyed on every redeploy, so work must
survive the container. It is proven end to end once per transport —
``TestContainerDeathSurvival`` against a real ``file://`` bare
repository standing in for a git remote, and
``TestUcContainerDeathSurvival`` against the in-memory Files API
stand-in for a Unity Catalog volume — bind, save, destroy the
"container", restore into a fresh directory, and find the history
intact.

The remaining classes pin the pieces that make that safe: URL
validation (no credentials in URLs, no traversal in uc:// paths),
binding-record and pointer semantics (an unreadable record must never
read as "unbound"), the askpass helper (the token never reaches a
file, a config, or a command line), the push queue's state machine
(coalescing, retry gating, terminal failures), and transport dispatch.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from haute import _git, _project_storage, _uc_transport
from haute._git import GitDomainError, GitPushRejectedError
from haute._project_storage import (
    GIT_TOKEN_ENV,
    STATE_VOLUME_ENV,
    PushQueue,
    StorageBinding,
    StorageClaimedError,
    StorageConfigError,
    StorageSupersededError,
    StorageUnavailableError,
    UCClaim,
    UCHead,
    UCLineage,
)
from haute.schemas import GitPushRejection, GitRemoteLeg

WORKING = "pricing-dev"


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _configure_identity(repo: Path) -> None:
    """Give *repo* a commit identity, as the UI's identity prompt does.

    A restored or forked clone carries none — `.haute/` is per-clone and git
    identity lives in the clone's own config, which is precisely why the
    product prompts for one before the first commit. A test that commits in
    such a clone must therefore supply it too; relying on the machine's
    global identity passes locally and fails wherever git has none.
    """
    _run_git(repo, "config", "user.name", "Restored Actuary")
    _run_git(repo, "config", "user.email", "restored@example.com")


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
    monkeypatch.setattr(_project_storage, "_session", _project_storage._SessionState())
    monkeypatch.setattr(_uc_transport, "_writer", _uc_transport._WriterState())
    yield
    _project_storage.push_queue().stop()
    _uc_transport._writer.heartbeat.stop()


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


def _replace_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the platform replacing the container mid-test.

    The filesystem (and the volume fake) survives; every per-process
    singleton — the session and the writer alike — starts over, exactly
    as a fresh container's interpreter would. A new writer identity is
    minted on first use.
    """
    monkeypatch.setattr(_project_storage, "_session", _project_storage._SessionState())
    monkeypatch.setattr(_uc_transport, "_writer", _uc_transport._WriterState())


def _bind_and_publish(project: Path, *, content: str = "# priced\n") -> str:
    """Bind ``UC_URL``, save once, publish — generation 2, the common prelude."""
    _project_storage.bind_remote(UC_URL, project)
    (project / "rating.py").write_text(content, encoding="utf-8")
    sha = _git.commit_save(["rating.py"], WORKING, cwd=project)
    assert sha is not None
    _project_storage.publish_bound_project(project)
    return sha


# The directory _forked_project restores the fork into. Named so a test that
# writes inside it can derive the path from tmp_path in its own body, which
# is what the write-sandbox lint requires (a helper return is opaque to it).
_FORK_CONTAINER_DIR = "fork-container"


def _forked_project(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    advance_parent: bool = True,
) -> Path:
    """The fork scenario every upstream test starts from.

    A parent bound to ``UC_URL`` and published, forked to ``FORK_URL``,
    then a fresh "container" bound to the fork — the same construction
    ``TestUcFork`` restores.
    """
    _bind_and_publish(project)
    _project_storage.fork_uc_location(UC_URL, FORK_URL, project)

    if advance_parent:
        # The parent moves on AFTER the fork — this is what the fork is
        # later behind by. Done before the fork's container exists so the
        # publish fence still belongs to the parent's writer.
        (project / "rating.py").write_text("# parent moved on\n", encoding="utf-8")
        assert _git.commit_save(["rating.py"], WORKING, cwd=project) is not None
        # A milestone as well as a save, so BOTH legs of the pair move.
        _git.commit_milestone("parent milestone", project, cwd=project)
        _project_storage.publish_bound_project(project)

    # A different container binds to the fork.
    _project_storage.write_binding(StorageBinding(remote_url=FORK_URL, branch=WORKING))
    _replace_container(monkeypatch)

    fork_root = tmp_path / _FORK_CONTAINER_DIR
    assert _project_storage.restore_if_bound(fork_root) == "restored"
    # The fork's container is a fresh clone, so it has no commit identity
    # until the user supplies one — callers here go on to commit.
    _configure_identity(fork_root)
    return fork_root


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

    def delete(self, path: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        if path not in self.store:
            raise _FakeNotFoundError(path)
        del self.store[path]

    def list_directory_contents(self, directory: str):
        if self.fail_with is not None:
            raise self.fail_with
        prefix = directory.rstrip("/") + "/"
        entries: dict[str, object] = {}
        for path in self.store:
            if not path.startswith(prefix):
                continue
            name = path[len(prefix) :].split("/", 1)[0]
            entries[name] = type(
                "_Entry",
                (),
                {
                    "name": name,
                    "path": prefix + name,
                    "is_directory": "/" in path[len(prefix) :],
                },
            )()
        return iter(entries.values())


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
    monkeypatch.setattr(_uc_transport, "_files_api", lambda: fake)
    monkeypatch.setattr(
        _uc_transport, "_is_not_found", lambda exc: isinstance(exc, _FakeNotFoundError)
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


class TestUcUrlValidation:
    """uc://catalog.schema.volume/path — the volume transport's URL form."""

    def test_accepted_form_round_trips(self) -> None:
        url = "uc://workspace.default.projects/pricing/demo"
        assert _project_storage.validate_remote_url(f"  {url} ") == url

    def test_trailing_slash_is_normalised_away(self) -> None:
        assert (
            _project_storage.validate_remote_url("uc://workspace.default.projects/demo/")
            == "uc://workspace.default.projects/demo"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "uc://workspace.default/demo",  # two-part volume name
            "uc://workspace.default.projects.extra/demo",  # four-part
            "uc://workspace..projects/demo",  # empty part
            "uc://workspace.default.projects",  # no project path
            "uc://workspace.default.projects/",  # empty project path
        ],
    )
    def test_malformed_forms_name_the_accepted_shape(self, url: str) -> None:
        with pytest.raises(StorageConfigError, match="uc://catalog.schema.volume"):
            _project_storage.validate_remote_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "uc://workspace.default.projects/../escape",
            "uc://workspace.default.projects/a/../b",
            "uc://workspace.default.projects/a//b",
            "uc://workspace.default.projects/./a",
        ],
    )
    def test_traversal_segments_are_refused(self, url: str) -> None:
        """The path is joined under /Volumes/, so `..` would escape the volume."""
        with pytest.raises(StorageConfigError, match="segments"):
            _project_storage.validate_remote_url(url)

    def test_volume_path_resolution(self) -> None:
        assert (
            _uc_transport._uc_volume_path("uc://workspace.default.projects/pricing/demo")
            == "/Volumes/workspace/default/projects/pricing/demo"
        )

    def test_uc_urls_need_no_credential_host_allowlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No git credential travels to a uc:// location — the SDK auths itself."""
        monkeypatch.setenv(GIT_TOKEN_ENV, "t")
        assert _project_storage.validate_remote_url("uc://workspace.default.projects/demo")


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

    def test_a_save_during_a_bind_is_counted_not_dropped(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bind invites the user to keep working; those saves must count.

        The bind publishes over the network and the queue only starts once
        it finishes. A save in that window would otherwise be dropped AND
        leave the counter at zero — the UI reporting "synced" over a commit
        that never published, which is the one thing the counter exists to
        prevent.
        """
        push = _RecordingPush()
        monkeypatch.setattr(_git, "push_working_pair", push)
        queue = PushQueue()

        queue.arm()  # the bind starts
        queue.enqueue()  # the user saves while it publishes
        assert queue.status() == _project_storage.SyncStatus(state="pending", pending=1)

        queue.start(project)  # the bind finishes and hands over
        assert push.done.wait(timeout=5)
        _wait_until(lambda: queue.status().state == "synced")
        queue.stop()

    def test_a_bind_that_never_starts_a_queue_leaves_no_phantom_backlog(
        self, project: Path
    ) -> None:
        """A failed or lift bind starts no worker, so it must not leave a count."""
        queue = PushQueue()
        queue.arm()
        queue.enqueue()
        queue.disarm()

        assert queue.status() == _project_storage.SyncStatus(state="synced")
        queue.enqueue()  # and counting stops again
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
        monkeypatch.setattr(_project_storage._session, "queue", PushQueue())
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
        _configure_identity(restored_root)
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

    def test_binding_naming_a_branch_the_remote_lacks_still_serves(
        self, project: Path, bare_remote: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """A phantom branch must not cost the user their app.

        The recorded branch can be absent from the stored project — deleted
        since, or recorded before a switch. Failing the boot here would
        strand the container in a crash loop with no way back, because
        rebinding needs a running app. The project restored fine, so serve
        it with no working branch and let the startup modal ask.
        """
        _project_storage.bind_remote(f"file://{bare_remote}", project)
        _project_storage.write_binding(
            StorageBinding(remote_url=f"file://{bare_remote}", branch="never-pushed")
        )
        restored = tmp_path / "fresh-container"

        assert _project_storage.restore_if_bound(restored) == "restored"

        from haute._git_state import read_working_branch

        assert (restored / ".git").is_dir()
        assert read_working_branch(restored) is None

    def test_lifting_a_populated_location_records_no_branch(
        self, project: Path, bare_remote: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """This session's branch says nothing about the project being lifted.

        Recording it would send the restart looking for a branch the stored
        project has never heard of — the crash-loop above, on the ordinary
        fork and bind-a-colleague's-project paths.
        """
        other = tmp_path / "other"
        other.mkdir()
        _run_git(other, "init", "-b", "main")
        _run_git(other, "config", "user.name", "Other")
        _run_git(other, "config", "user.email", "other@example.com")
        (other / "f.txt").write_text("x", encoding="utf-8")
        _run_git(other, "add", "-A")
        _run_git(other, "commit", "-m", "someone else's project")
        _run_git(other, "push", f"file://{bare_remote}", "main")

        assert _project_storage.bind_remote(f"file://{bare_remote}", project) == "restart-required"

        recorded = _project_storage.read_binding()
        assert recorded is not None
        assert recorded.branch is None

    def test_binding_requires_a_state_volume(self, project: Path, bare_remote: Path) -> None:
        """Without somewhere durable to record it, a binding is a false promise."""
        with pytest.raises(StorageConfigError, match=STATE_VOLUME_ENV):
            _project_storage.bind_remote(f"file://{bare_remote}", project)


# ---------------------------------------------------------------------------
# Unity Catalog bundle transport
# ---------------------------------------------------------------------------

UC_URL = "uc://workspace.default.projects/pricing/demo"
_UC_ROOT = "/Volumes/workspace/default/projects/pricing/demo"
FORK_URL = "uc://workspace.default.projects/pricing/fork"
_FORK_ROOT = "/Volumes/workspace/default/projects/pricing/fork"


def _stored_bundle_generations(files_api: _FakeFiles, root: str = _UC_ROOT) -> list[int]:
    """Leading generation numbers of stored bundles (either filename shape)."""
    prefix = f"{root}/bundles/"
    return sorted(
        int(path[len(prefix) :].split("-", 1)[0].removesuffix(".bundle"))
        for path in files_api.store
        if path.startswith(prefix)
    )


def _stored_head(files_api: _FakeFiles, root: str = _UC_ROOT) -> UCHead:
    return UCHead.from_payload(json.loads(files_api.store[f"{root}/HEAD.json"]))


def _stored_claim(files_api: _FakeFiles, root: str = _UC_ROOT) -> UCClaim | None:
    raw = files_api.store.get(f"{root}/CLAIM.json")
    return UCClaim.from_payload(json.loads(raw)) if raw is not None else None


def _plant_claim(
    files_api: _FakeFiles,
    app_name: str,
    *,
    age_seconds: float = 0.0,
    writer_id: str = "other-writer",
    user: str | None = None,
    root: str = _UC_ROOT,
) -> UCClaim:
    """Write another holder's claim into the fake store, *age_seconds* old."""
    from datetime import UTC, datetime, timedelta

    stamp = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat(timespec="seconds")
    claim = UCClaim(
        app_name=app_name,
        writer_id=writer_id,
        nonce=f"nonce-{app_name}",
        user=user,
        claimed_at=stamp,
        refreshed_at=stamp,
    )
    files_api.store[f"{root}/CLAIM.json"] = claim.to_json().encode("utf-8")
    return claim


class TestUcHeadRecord:
    def test_round_trips_through_json(self) -> None:
        head = UCHead(
            generation=42,
            tip_sha="a" * 40,
            writer_id="haute-spike-abc123",
            bundle_name="000042-haute-spike-abc123.bundle",
            written_at="2026-08-04T00:00:00+00:00",
        )
        assert UCHead.from_payload(json.loads(head.to_json())) == head

    def test_unknown_fields_are_tolerated(self) -> None:
        head = UCHead.from_payload(
            {
                "generation": 1,
                "tip_sha": "s",
                "writer_id": "w",
                "bundle_name": "000001-w.bundle",
                "future": {"x": 1},
            }
        )
        assert head.generation == 1

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {},
            {"generation": 0, "tip_sha": "s", "writer_id": "w", "bundle_name": "b"},
            {"generation": "1", "tip_sha": "s", "writer_id": "w", "bundle_name": "b"},
            {"generation": 1, "tip_sha": " ", "writer_id": "w", "bundle_name": "b"},
            {"generation": 1, "tip_sha": "s", "writer_id": "", "bundle_name": "b"},
            # A pointer with no bundle name is a corrupted record, not an
            # older format: the layout has no released version behind it.
            {"generation": 1, "tip_sha": "s", "writer_id": "w"},
        ],
    )
    def test_malformed_pointers_fail_loudly(self, payload: object) -> None:
        """As StorageConfigError: retrying cannot fix a corrupted pointer,
        so the failure must classify as terminal, not retryable transport."""
        with pytest.raises(StorageConfigError, match="corrupted"):
            UCHead.from_payload(payload)

    def test_unreadable_pointer_is_never_mistaken_for_empty(self, files_api: _FakeFiles) -> None:
        files_api.fail_with = RuntimeError("volume unreachable")
        with pytest.raises(StorageUnavailableError):
            _project_storage.read_uc_head(UC_URL)

    def test_absent_pointer_reads_as_never_published(self, files_api: _FakeFiles) -> None:
        assert _project_storage.read_uc_head(UC_URL) is None


class TestPublishDispatch:
    """publish_bound_project selects the transport from the binding's scheme."""

    def test_no_binding_defaults_to_the_git_push(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A queue started without a binding behaves exactly as before."""
        push = _RecordingPush()
        monkeypatch.setattr(_git, "push_working_pair", push)
        _project_storage.publish_bound_project(project)
        assert push.calls == 1

    def test_uc_binding_routes_to_the_bundle_publish(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        published: list[str] = []
        monkeypatch.setattr(
            _project_storage, "publish_to_uc", lambda url, root: published.append(url)
        )
        monkeypatch.setattr(
            _git, "push_working_pair", _RecordingPush(AssertionError("no git push for uc://"))
        )
        monkeypatch.setattr(
            _project_storage._session, "binding", StorageBinding(remote_url=UC_URL, branch=WORKING)
        )
        _project_storage.publish_bound_project(project)
        assert published == [UC_URL]

    def test_supersession_classifies_as_terminal_rejection(self) -> None:
        failure, message, terminal = _project_storage._classify_push_failure(
            StorageSupersededError("Another app container has published newer work.")
        )
        assert failure == "rejected"
        assert terminal is True
        assert "Another app container" in message

    def test_unavailable_storage_classifies_as_retryable_transport(self) -> None:
        failure, message, terminal = _project_storage._classify_push_failure(
            StorageUnavailableError("The project bundle could not be uploaded.")
        )
        assert failure == "transport"
        assert terminal is False
        assert "bundle" in message


class TestUcContainerDeathSurvival:
    """Bind to a volume, work, lose the container, and get the work back."""

    def test_bind_adopts_an_empty_location_and_publishes(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        outcome = _project_storage.bind_remote(UC_URL, project, bound_by="someone@example.com")
        assert outcome == "adopted"

        # A complete first generation and its pointer are on the volume...
        assert _stored_bundle_generations(files_api) == [1]
        head = _stored_head(files_api)
        assert head.generation == 1
        assert head.tip_sha
        # ... origin carries the uc:// URL as the clone's identity marker...
        assert _git.remote_url("origin", cwd=project) == UC_URL
        # ... and the binding was recorded durably.
        binding = _project_storage.read_binding()
        assert binding is not None
        assert binding.remote_url == UC_URL
        assert binding.branch == WORKING

    def test_populated_location_defers_to_a_restart(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """A location with published history is another project — never adopt it."""
        files_api.store[f"{_UC_ROOT}/HEAD.json"] = (
            UCHead(
                generation=3,
                tip_sha="s",
                writer_id="another-app",
                bundle_name="000003-x.bundle",
            )
            .to_json()
            .encode("utf-8")
        )

        outcome = _project_storage.bind_remote(UC_URL, project)
        assert outcome == "restart-required"
        assert _project_storage.read_binding() is not None
        assert _project_storage.active_binding() is None
        # Nothing was published over the existing generations.
        assert _stored_bundle_generations(files_api) == []

    def test_saved_work_survives_container_replacement(
        self,
        project: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The headline guarantee over the volume transport, end to end.

        Bind → save → publish → destroy the container → restore into a
        fresh directory → the save is there and publishing still works.
        """
        monkeypatch.setenv("DATABRICKS_APP_NAME", "test-app")
        _project_storage.bind_remote(UC_URL, project)

        (project / "rating.py").write_text("# priced\n", encoding="utf-8")
        sha = _git.commit_save(["rating.py"], WORKING, cwd=project)
        assert sha is not None
        _project_storage.publish_bound_project(project)
        assert _stored_head(files_api).generation == 2

        # The container is replaced: fresh filesystem, fresh process state,
        # same volume. A new writer identity is minted in the new container.
        restored_root = tmp_path / "new-container"
        _replace_container(monkeypatch)
        outcome = _project_storage.restore_if_bound(restored_root)

        assert outcome == "restored"
        ledger_log = _run_git(restored_root, "log", "--format=%H", f"{WORKING}-save")
        assert sha in ledger_log.splitlines()

        # Usable, not merely present: local working pair, recorded branch,
        # and origin repointed from the temporary bundle file to the uc://
        # URL (the clone-identity check depends on it).
        local_branches = _run_git(restored_root, "branch", "--format=%(refname:short)").splitlines()
        assert WORKING in local_branches
        assert f"{WORKING}-save" in local_branches
        from haute._git_state import read_working_branch

        assert read_working_branch(restored_root) == WORKING
        assert _git.remote_url("origin", cwd=restored_root) == UC_URL

        # A further save publishes the next generation — the restored-from
        # pointer is exempt from the supersession fence even though it was
        # written by the previous container's writer identity.
        _configure_identity(restored_root)
        (restored_root / "rating.py").write_text("# repriced\n", encoding="utf-8")
        next_sha = _git.commit_save(["rating.py"], WORKING, cwd=restored_root)
        assert next_sha is not None
        _project_storage.publish_bound_project(restored_root)
        assert _stored_head(files_api).generation == 3

    def test_restore_reuses_an_existing_clone(self, project: Path, files_api: _FakeFiles) -> None:
        """Origin carrying the uc:// URL is what identifies the directory."""
        _project_storage.bind_remote(UC_URL, project)
        assert _project_storage.restore_if_bound(project) == "present"

    def test_process_restart_over_a_surviving_clone_can_still_publish(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `present` path must arm the fence, not trip over it.

        A new process mints a new writer identity, so without learning
        which generation the surviving clone derives from, its own first
        publish would read as another writer's work.
        """
        monkeypatch.setenv("DATABRICKS_APP_NAME", "test-app")
        _project_storage.bind_remote(UC_URL, project)
        # Same filesystem, new process: fresh writer id, no seen generation.
        _replace_container(monkeypatch)

        assert _project_storage.restore_if_bound(project) == "present"
        _project_storage.publish_bound_project(project)
        assert _stored_head(files_api).generation == 2

    def test_a_surviving_clone_the_volume_moved_past_stops_publishing(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A published tip the clone does not contain is someone else's work."""
        monkeypatch.setenv("DATABRICKS_APP_NAME", "test-app")
        _project_storage.bind_remote(UC_URL, project)
        # Another container published a generation this clone knows nothing of.
        files_api.store[f"{_UC_ROOT}/HEAD.json"] = (
            UCHead(
                generation=2,
                tip_sha="e" * 40,
                writer_id="replacement-container",
                bundle_name="000002-replacement.bundle",
            )
            .to_json()
            .encode("utf-8")
        )
        _replace_container(monkeypatch)

        assert _project_storage.restore_if_bound(project) == "present"
        with pytest.raises(StorageSupersededError, match="Another app container"):
            _project_storage.publish_bound_project(project)

    def test_a_generation_without_its_pointer_is_never_restored(
        self, project: Path, files_api: _FakeFiles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pointer-written-last is the read-side contract for torn uploads.

        A bundle uploaded without its pointer (the container died between
        the two writes) must be invisible: restore follows HEAD.json only.
        """
        _project_storage.bind_remote(UC_URL, project)
        sha_gen1 = _stored_head(files_api).tip_sha
        # A torn publish: generation 2's bundle arrived, its pointer did not.
        files_api.store[f"{_UC_ROOT}/bundles/000002.bundle"] = b"torn partial upload"

        restored_root = tmp_path / "new-container"
        monkeypatch.setattr(_project_storage, "_session", _project_storage._SessionState())
        assert _project_storage.restore_if_bound(restored_root) == "restored"
        assert _run_git(restored_root, "rev-parse", "HEAD") == sha_gen1

    def test_retention_prunes_beyond_the_newest_five(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        _project_storage.bind_remote(UC_URL, project)
        for generation in range(2, 8):
            (project / "rating.py").write_text(f"# rev {generation}\n", encoding="utf-8")
            assert _git.commit_save(["rating.py"], WORKING, cwd=project) is not None
            _project_storage.publish_bound_project(project)

        assert _stored_head(files_api).generation == 7
        assert _stored_bundle_generations(files_api) == [3, 4, 5, 6, 7]

    def test_a_superseding_writer_stops_publishing(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """Two containers, one project: the loser stops loudly, not silently."""
        _project_storage.bind_remote(UC_URL, project)
        # A replacement container published generation 2 behind our back.
        files_api.store[f"{_UC_ROOT}/HEAD.json"] = (
            UCHead(
                generation=2,
                tip_sha="f" * 40,
                writer_id="replacement-container",
                bundle_name="000002-replacement.bundle",
            )
            .to_json()
            .encode("utf-8")
        )

        with pytest.raises(StorageSupersededError, match="Another app container"):
            _project_storage.publish_to_uc(UC_URL, project)
        # Nothing was uploaded over the superseding generation.
        assert _stored_head(files_api).writer_id == "replacement-container"

    def test_failed_upload_leaves_the_previous_pointer_intact(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The write-side of pointer-written-last: a torn publish is retryable.

        When the bundle upload dies, the pointer must still name the last
        complete generation — the failure surfaces as retryable transport,
        and a restore meanwhile would get generation 1, not garbage.
        """
        _project_storage.bind_remote(UC_URL, project)

        def broken_upload(path: str, contents, overwrite: bool = False) -> None:
            raise RuntimeError("volume went away mid-upload")

        monkeypatch.setattr(files_api, "upload", broken_upload)
        with pytest.raises(StorageUnavailableError, match="uploaded"):
            _project_storage.publish_to_uc(UC_URL, project)
        assert _stored_head(files_api).generation == 1

    def test_bundle_packaging_failure_is_retryable_and_names_the_action(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A corrupt bundle must never be uploaded as the only durable copy."""
        _project_storage.bind_remote(UC_URL, project)

        def failing_verify(bundle: Path, cwd: Path | None = None) -> None:
            raise _git.GitError("bundle verify stderr")

        monkeypatch.setattr(_git, "bundle_verify", failing_verify)
        with pytest.raises(StorageUnavailableError, match="packaged"):
            _project_storage.publish_to_uc(UC_URL, project)
        assert _stored_head(files_api).generation == 1
        failure, _, terminal = _project_storage._classify_push_failure(
            StorageUnavailableError("The project could not be packaged for the storage volume.")
        )
        assert (failure, terminal) == ("transport", False)

    def test_retention_failure_never_fails_a_publish(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pruning is best-effort: the publish already succeeded."""
        _project_storage.bind_remote(UC_URL, project)
        for generation in range(2, 8):
            _project_storage.publish_bound_project(project)

        def broken_listing(directory: str):
            raise RuntimeError("listing unavailable")

        monkeypatch.setattr(files_api, "list_directory_contents", broken_listing)
        _project_storage.publish_bound_project(project)
        assert _stored_head(files_api).generation == 8

    def test_bundle_filenames_are_writer_unique(self, project: Path, files_api: _FakeFiles) -> None:
        """Racing writers can contend only on the pointer, never on bytes:
        each generation's bundle carries its writer's name in the path."""
        _project_storage.bind_remote(UC_URL, project)
        head = _stored_head(files_api)
        assert head.bundle_name.startswith("000001-")
        assert _uc_transport._writer_id() in head.bundle_name
        assert f"{_UC_ROOT}/bundles/{head.bundle_name}" in files_api.store

    def test_a_mid_flight_publish_by_another_writer_stops_before_the_pointer(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-pointer fence re-check: another writer landing a
        generation while ours is packaging/uploading must not have its
        pointer overwritten — that would silently discard its publish."""
        _project_storage.bind_remote(UC_URL, project)
        foreign = UCHead(
            generation=2,
            tip_sha="d" * 40,
            writer_id="racing-container",
            bundle_name="000002-racing-container.bundle",
        )

        original_upload = files_api.upload

        def interleaved_upload(path: str, contents, overwrite: bool = False) -> None:
            original_upload(path, contents, overwrite=overwrite)
            if "/bundles/" in path:
                # The rival's pointer lands while our bundle bytes are in
                # flight — after our packaging fence, before our pointer.
                files_api.store[f"{_UC_ROOT}/HEAD.json"] = foreign.to_json().encode("utf-8")

        monkeypatch.setattr(files_api, "upload", interleaved_upload)
        with pytest.raises(StorageSupersededError, match="Another app container"):
            _project_storage.publish_to_uc(UC_URL, project)
        # The rival's generation survives untouched.
        assert _stored_head(files_api) == foreign

    def test_restore_gates_when_pointer_and_bundle_disagree(
        self, project: Path, files_api: _FakeFiles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pointer describing history its bundle does not contain is the
        trace of a torn multi-writer publish — never restore it silently."""
        _project_storage.bind_remote(UC_URL, project)
        head = _stored_head(files_api)
        torn = UCHead(
            generation=head.generation,
            tip_sha="a" * 40,  # not a commit in the bundle
            writer_id=head.writer_id,
            bundle_name=head.bundle_name,
        )
        files_api.store[f"{_UC_ROOT}/HEAD.json"] = torn.to_json().encode("utf-8")

        monkeypatch.setattr(_project_storage, "_session", _project_storage._SessionState())
        with pytest.raises(StorageUnavailableError, match="does not contain"):
            _project_storage.restore_if_bound(tmp_path / "fresh")

    def test_restore_gates_when_the_pointer_is_missing(
        self, project: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """A binding promises history; an empty location must gate, not seed."""
        _project_storage.write_binding(StorageBinding(remote_url=UC_URL, branch=WORKING))
        with pytest.raises(StorageUnavailableError, match="no published"):
            _project_storage.restore_if_bound(tmp_path / "fresh")

    def test_restore_gates_when_the_bundle_is_missing(
        self, project: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """A pointer to a vanished generation is unreadable state, not 'unbound'."""
        _project_storage.write_binding(StorageBinding(remote_url=UC_URL, branch=WORKING))
        files_api.store[f"{_UC_ROOT}/HEAD.json"] = (
            UCHead(generation=9, tip_sha="s", writer_id="w", bundle_name="000009-w.bundle")
            .to_json()
            .encode("utf-8")
        )
        with pytest.raises(StorageUnavailableError, match="Generation 9"):
            _project_storage.restore_if_bound(tmp_path / "fresh")


class TestBindTask:
    """Bind runs in the background so a publish never blocks the session."""

    def test_precheck_catches_what_belongs_beside_the_field(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        with pytest.raises(StorageConfigError, match="https://"):
            _project_storage.precheck_bind("ssh://host/r.git")
        # A valid URL passes and comes back normalised.
        assert _project_storage.precheck_bind(f" {UC_URL} ") == UC_URL

    def test_precheck_refuses_without_a_state_volume(self, project: Path) -> None:
        with pytest.raises(StorageConfigError, match=STATE_VOLUME_ENV):
            _project_storage.precheck_bind(UC_URL)

    def test_a_completed_bind_reports_its_outcome(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        task = _project_storage.bind_task()
        assert task.status().state == "idle"
        task.start(UC_URL, project)
        _wait_until(lambda: task.status().state == "succeeded")
        assert task.status().outcome == "adopted"
        assert task.status().remote_url == UC_URL

    def test_a_failed_bind_keeps_the_holder_for_the_dialog(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        _plant_claim(files_api, "other-app", user="colleague@example.com")
        task = _project_storage.bind_task()
        task.start(UC_URL, project)
        _wait_until(lambda: task.status().state == "failed")
        status = task.status()
        assert status.claim is not None
        assert status.claim.app_name == "other-app"
        assert "fork" in (status.message or "")
        # The URL is kept so the reopened dialog can prefill it.
        assert status.remote_url == UC_URL

    def test_a_result_persists_until_acknowledged(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """A UI polling every few seconds must not miss the outcome."""
        task = _project_storage.bind_task()
        task.start(UC_URL, project)
        _wait_until(lambda: task.status().state == "succeeded")
        assert task.status().state == "succeeded"  # still there on a later read
        task.acknowledge()
        assert task.status().state == "idle"

    def test_only_one_bind_runs_at_a_time(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release = threading.Event()

        def slow(url, project_root, bound_by=None):
            release.wait(timeout=5)
            return "adopted"

        monkeypatch.setattr(_project_storage, "bind_remote", slow)
        task = _project_storage.bind_task()
        task.start(UC_URL, project)
        _wait_until(lambda: task.status().state == "running")
        with pytest.raises(StorageConfigError, match="already being saved"):
            task.start(FORK_URL, project)
        release.set()

    def test_an_unexpected_failure_is_sanitised(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raw errors must never reach the dialog verbatim."""
        secret_ish = f"fatal: could not read Password for 'https://token@host': {project}"

        def explode(url, project_root, bound_by=None):
            raise _git.GitError(secret_ish)

        monkeypatch.setattr(_project_storage, "bind_remote", explode)
        task = _project_storage.bind_task()
        task.start(UC_URL, project)
        _wait_until(lambda: task.status().state == "failed")
        message = task.status().message or ""
        assert "token@host" not in message
        assert str(project) not in message


# ---------------------------------------------------------------------------
# Claim lease — the location behaves like a locally-owned file
# ---------------------------------------------------------------------------


class TestUcClaim:
    def test_bind_claims_the_location(self, project: Path, files_api: _FakeFiles) -> None:
        _project_storage.bind_remote(UC_URL, project, bound_by="someone@example.com")
        claim = _stored_claim(files_api)
        assert claim is not None
        assert claim.app_name == "local"  # _scope_name() without an app name
        assert claim.user == "someone@example.com"
        assert claim.refreshed_at is not None

    def test_bind_refuses_a_location_under_a_live_claim(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """The refusal names the holder — steering, not stonewalling."""
        _plant_claim(files_api, "other-app", user="colleague@example.com")
        with pytest.raises(StorageClaimedError, match="other-app") as excinfo:
            _project_storage.bind_remote(UC_URL, project)
        assert "colleague@example.com" in str(excinfo.value)
        assert "fork" in str(excinfo.value)
        # Nothing was bound or published behind the holder's back.
        assert _project_storage.read_binding() is None
        assert _stored_bundle_generations(files_api) == []
        # The holder's claim is untouched.
        stored = _stored_claim(files_api)
        assert stored is not None and stored.app_name == "other-app"

    def test_a_stale_claim_is_taken_over(self, project: Path, files_api: _FakeFiles) -> None:
        """Lease expiry, not liveness probing, is what declares a session dead."""
        _plant_claim(files_api, "other-app", age_seconds=600.0)
        assert _project_storage.bind_remote(UC_URL, project) == "adopted"
        stored = _stored_claim(files_api)
        assert stored is not None and stored.app_name == "local"

    def test_an_own_app_claim_is_taken_over_even_when_fresh(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One container per app: our own name can only be a predecessor's."""
        monkeypatch.setenv("DATABRICKS_APP_NAME", "test-app")
        _plant_claim(files_api, "test-app", writer_id="predecessor-writer")
        assert _project_storage.bind_remote(UC_URL, project) == "adopted"

    def test_the_own_app_shortcut_needs_a_real_app_name(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """Off the platform every process shares the fallback scope 'local' —
        two local processes must arbitrate by lease expiry like strangers,
        not seize each other's claim on sight."""
        _plant_claim(files_api, "local", writer_id="another-local-process")
        with pytest.raises(StorageClaimedError, match="local"):
            _project_storage.bind_remote(UC_URL, project)

    def test_a_malformed_claim_reads_as_stale_not_as_a_gate(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """A corrupt lease must not brick the location it guards."""
        files_api.store[f"{_UC_ROOT}/CLAIM.json"] = b"{not json"
        assert _project_storage.bind_remote(UC_URL, project) == "adopted"

    def test_losing_the_write_race_names_the_winner(
        self, project: Path, files_api: _FakeFiles, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No compare-and-swap on the Files API: write-then-verify catches it."""
        winner = UCClaim(app_name="racing-app", writer_id="w-race", nonce="race-nonce")

        original_upload = files_api.upload

        def interleaved_upload(path: str, contents, overwrite: bool = False) -> None:
            original_upload(path, contents, overwrite=overwrite)
            if path.endswith("CLAIM.json"):
                files_api.store[path] = winner.to_json().encode("utf-8")

        monkeypatch.setattr(files_api, "upload", interleaved_upload)
        with pytest.raises(StorageClaimedError, match="racing-app"):
            _project_storage.acquire_uc_claim(UC_URL)

    def test_release_removes_only_our_own_claim(self, project: Path, files_api: _FakeFiles) -> None:
        _project_storage.bind_remote(UC_URL, project)
        _project_storage.release_uc_claim()
        assert _stored_claim(files_api) is None

        # A foreign claim is never deleted by our release.
        _project_storage.acquire_uc_claim(UC_URL)
        _plant_claim(files_api, "thief-app")
        _project_storage.release_uc_claim()
        stored = _stored_claim(files_api)
        assert stored is not None and stored.app_name == "thief-app"

    def test_publish_stops_when_the_lease_was_stolen(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """A stolen lease stops the old holder loudly — no interleaving."""
        _project_storage.bind_remote(UC_URL, project)
        _plant_claim(files_api, "thief-app")
        with pytest.raises(StorageClaimedError, match="thief-app"):
            _project_storage.publish_to_uc(UC_URL, project)
        failure, _, terminal = _project_storage._classify_push_failure(
            StorageClaimedError("x", _plant_claim(files_api, "thief-app"))
        )
        assert (failure, terminal) == ("rejected", True)

    def test_publish_refreshes_the_held_lease(self, project: Path, files_api: _FakeFiles) -> None:
        _project_storage.bind_remote(UC_URL, project)
        held = _stored_claim(files_api)
        assert held is not None
        # Age the stored record; the next publish must re-stamp it.
        stale_copy = UCClaim(
            app_name=held.app_name,
            writer_id=held.writer_id,
            nonce=held.nonce,
            user=held.user,
            claimed_at=held.claimed_at,
            refreshed_at="2020-01-01T00:00:00+00:00",
        )
        files_api.store[f"{_UC_ROOT}/CLAIM.json"] = stale_copy.to_json().encode("utf-8")
        _project_storage.publish_to_uc(UC_URL, project)
        refreshed = _stored_claim(files_api)
        assert refreshed is not None
        assert refreshed.nonce == held.nonce
        assert refreshed.refreshed_at != "2020-01-01T00:00:00+00:00"

    def test_heartbeat_refreshes_ours_and_stops_on_foreign(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """A stolen lease is never re-stolen by a background thread."""
        _project_storage.bind_remote(UC_URL, project)
        beat = _uc_transport._writer.heartbeat._beat
        assert beat() is True  # ours: refreshed, keep beating

        _plant_claim(files_api, "thief-app")
        assert beat() is False  # foreign: stop, do not overwrite
        stored = _stored_claim(files_api)
        assert stored is not None and stored.app_name == "thief-app"

    def test_restore_gates_when_a_foreign_claim_is_live(
        self, project: Path, files_api: _FakeFiles, tmp_path: Path
    ) -> None:
        """A boot cannot offer a dialog, so it gates with the holder named."""
        _project_storage.write_binding(StorageBinding(remote_url=UC_URL, branch=WORKING))
        _plant_claim(files_api, "other-app")
        with pytest.raises(StorageClaimedError, match="other-app"):
            _project_storage.restore_if_bound(tmp_path / "fresh")

    def test_publish_reasserts_a_vanished_lease(self, project: Path, files_api: _FakeFiles) -> None:
        """A predecessor's release can delete a successor's live lease (no
        compare-and-swap); the holder must reassert, not shrug — a claimless
        location would let a third writer bind with no refusal at all."""
        _project_storage.bind_remote(UC_URL, project)
        del files_api.store[f"{_UC_ROOT}/CLAIM.json"]
        _project_storage.publish_bound_project(project)
        stored = _stored_claim(files_api)
        assert stored is not None and stored.app_name == "local"

    def test_heartbeat_reasserts_a_vanished_lease(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        _project_storage.bind_remote(UC_URL, project)
        del files_api.store[f"{_UC_ROOT}/CLAIM.json"]
        assert _uc_transport._writer.heartbeat._beat() is True
        assert _stored_claim(files_api) is not None

    def test_restarting_the_heartbeat_after_stop_beats_again(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        """A stop() racing a start() must not strand the new claim beatless
        (the lease would silently expire and another writer take over)."""
        _project_storage.bind_remote(UC_URL, project)
        heartbeat = _uc_transport._writer.heartbeat
        first_thread = heartbeat._thread
        heartbeat.stop()
        heartbeat.start()
        assert heartbeat._thread is not first_thread
        assert not heartbeat._stop.is_set()
        assert heartbeat._thread is not None and heartbeat._thread.is_alive()


# ---------------------------------------------------------------------------
# Fork — the honest way past a held location
# ---------------------------------------------------------------------------


class TestUcFork:
    def test_fork_copies_the_latest_published_generation(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        _bind_and_publish(project)
        lineage = _project_storage.fork_uc_location(UC_URL, FORK_URL, project)

        assert lineage.parent_url == UC_URL
        assert lineage.parent_generation == 2
        # The fork holds the parent's newest bundle as its own generation 1...
        assert _stored_bundle_generations(files_api, _FORK_ROOT) == [1]
        source_head = _stored_head(files_api)
        fork_head = _stored_head(files_api, _FORK_ROOT)
        assert (
            files_api.store[f"{_FORK_ROOT}/bundles/{fork_head.bundle_name}"]
            == files_api.store[f"{_UC_ROOT}/bundles/{source_head.bundle_name}"]
        )
        # ... with its own pointer and provenance recorded.
        assert fork_head.generation == 1
        assert fork_head.tip_sha == source_head.tip_sha
        stored_lineage = UCLineage.from_payload(
            json.loads(files_api.store[f"{_FORK_ROOT}/LINEAGE.json"])
        )
        assert stored_lineage == lineage
        # The fork takes no claim — binding to it later claims it.
        assert _stored_claim(files_api, _FORK_ROOT) is None

    def test_fork_copies_published_state_only(self, project: Path, files_api: _FakeFiles) -> None:
        """The holder's unpublished work is theirs alone."""
        _bind_and_publish(project)
        (project / "rating.py").write_text("# unpublished\n", encoding="utf-8")
        assert _git.commit_save(["rating.py"], WORKING, cwd=project) is not None
        # No publish: the fork must carry generation 2, not the local commit.
        lineage = _project_storage.fork_uc_location(UC_URL, FORK_URL, project)
        assert lineage.parent_generation == 2

    def test_fork_refuses_a_populated_target(self, project: Path, files_api: _FakeFiles) -> None:
        """A fork never overwrites."""
        _bind_and_publish(project)
        files_api.store[f"{_FORK_ROOT}/HEAD.json"] = (
            UCHead(generation=1, tip_sha="s", writer_id="w", bundle_name="000001-w.bundle")
            .to_json()
            .encode("utf-8")
        )
        with pytest.raises(StorageConfigError, match="already has a stored project"):
            _project_storage.fork_uc_location(UC_URL, FORK_URL, project)

    def test_fork_refuses_an_unpublished_source(self, project: Path, files_api: _FakeFiles) -> None:
        with pytest.raises(StorageConfigError, match="nothing to copy"):
            _project_storage.fork_uc_location(UC_URL, FORK_URL, project)

    def test_fork_refuses_self_and_non_uc_urls(self, project: Path, files_api: _FakeFiles) -> None:
        with pytest.raises(StorageConfigError, match="onto itself"):
            _project_storage.fork_uc_location(UC_URL, UC_URL, project)
        with pytest.raises(StorageConfigError, match="uc://"):
            _project_storage.fork_uc_location("https://host/r.git", FORK_URL, project)

    def test_a_fork_restores_and_reports_its_provenance(
        self,
        project: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bind → publish → fork → restore the fork elsewhere: history and
        lineage both come through, and the fork publishes independently."""
        sha = _bind_and_publish(project)
        _project_storage.fork_uc_location(UC_URL, FORK_URL, project)

        # A different container binds to the fork.
        _project_storage.write_binding(StorageBinding(remote_url=FORK_URL, branch=WORKING))
        _replace_container(monkeypatch)

        restored_root = tmp_path / "fork-container"
        assert _project_storage.restore_if_bound(restored_root) == "restored"
        ledger_log = _run_git(restored_root, "log", "--format=%H", f"{WORKING}-save")
        assert sha in ledger_log.splitlines()
        assert _git.remote_url("origin", cwd=restored_root) == FORK_URL
        lineage = _project_storage.active_lineage()
        assert lineage is not None and lineage.parent_url == UC_URL

        # The fork publishes to its own location, not the parent's.
        _configure_identity(restored_root)
        (restored_root / "rating.py").write_text("# forked work\n", encoding="utf-8")
        assert _git.commit_save(["rating.py"], WORKING, cwd=restored_root) is not None
        _project_storage.publish_bound_project(restored_root)
        assert _stored_head(files_api, _FORK_ROOT).generation == 2
        assert _stored_head(files_api).generation == 2  # parent untouched


class TestUpstreamSync:
    """A fork can see and catch up to its parent, but never merge it.

    The shape every test here starts from: a parent bound to ``UC_URL`` and
    published, forked to ``FORK_URL``, then a second "container" bound to
    the fork — the same construction ``TestUcFork`` restores.
    """

    def test_fetching_the_parent_adds_tracking_refs_but_no_remote(
        self,
        project: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The parent must never become a configured remote.

        ``_canonical_remote`` picks from configured remotes, so a second one
        would give the divergence baseline (and the milestone fork-gate) two
        answers. Fetching straight from a bundle leaves ``git remote`` alone.
        """
        fork_root = _forked_project(project, tmp_path, monkeypatch)
        _project_storage.check_upstream(fork_root)

        assert _run_git(fork_root, "remote").split() == ["origin"]
        tracking = _run_git(fork_root, "for-each-ref", "--format=%(refname)", "refs/remotes/")
        assert f"refs/remotes/upstream/{WORKING}" in tracking.splitlines()
        assert f"refs/remotes/upstream/{WORKING}-save" in tracking.splitlines()

    def test_an_undiverged_fork_is_behind_and_can_catch_up(
        self,
        project: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fork_root = _forked_project(project, tmp_path, monkeypatch)
        status = _project_storage.check_upstream(fork_root)

        assert status.parent_url == UC_URL
        assert status.parent_generation == 3
        assert status.working.status == "behind"
        assert status.ledger.status == "behind"
        assert status.can_fast_forward is True

        parent_tip = _run_git(project, "rev-parse", WORKING)
        response = _project_storage.pull_upstream(fork_root)

        assert set(response.fast_forwarded) == {WORKING, f"{WORKING}-save"}
        assert _run_git(fork_root, "rev-parse", WORKING) == parent_tip
        assert _run_git(fork_root, "rev-parse", f"{WORKING}-save") == _run_git(
            project, "rev-parse", f"{WORKING}-save"
        )
        assert (fork_root / "rating.py").read_text(encoding="utf-8") == "# parent moved on\n"

    def test_a_fork_with_its_own_work_is_refused_and_left_intact(
        self,
        project: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fast-forward only: both sides moved is a dead end, not a merge."""
        fork_root = _forked_project(project, tmp_path, monkeypatch)
        (tmp_path / _FORK_CONTAINER_DIR / "rating.py").write_text(
            "# fork's own work\n", encoding="utf-8"
        )
        own_sha = _git.commit_save(["rating.py"], WORKING, cwd=fork_root)
        assert own_sha is not None

        status = _project_storage.check_upstream(fork_root)
        # A save moves the ledger only; that alone is enough to rule out a
        # fast-forward, and the working leg is still merely behind.
        assert status.ledger.status == "diverged"
        assert status.can_fast_forward is False

        both_moved = "both this project and the parent project have changed"
        with pytest.raises(GitDomainError, match=both_moved):
            _project_storage.pull_upstream(fork_root)

        # The fork's own history survives the refusal untouched.
        assert own_sha in _run_git(fork_root, "log", "--format=%H", f"{WORKING}-save").splitlines()
        assert (fork_root / "rating.py").read_text(encoding="utf-8") == "# fork's own work\n"

    def test_a_project_that_is_not_a_fork_is_refused(
        self, project: Path, files_api: _FakeFiles
    ) -> None:
        _project_storage.bind_remote(UC_URL, project)
        with pytest.raises(StorageConfigError, match="was not forked"):
            _project_storage.check_upstream(project)

    def test_a_parent_with_nothing_published_is_refused(
        self,
        project: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fork_root = _forked_project(project, tmp_path, monkeypatch)
        del files_api.store[f"{_UC_ROOT}/HEAD.json"]
        with pytest.raises(StorageConfigError, match="nothing published"):
            _project_storage.check_upstream(fork_root)

    def test_an_undownloadable_parent_fails_the_check_only(
        self,
        project: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A fork is a complete project: an unreachable parent breaks nothing."""
        fork_root = _forked_project(project, tmp_path, monkeypatch)
        before = _run_git(fork_root, "for-each-ref", "--format=%(refname) %(objectname)")
        head = _stored_head(files_api)
        del files_api.store[f"{_UC_ROOT}/bundles/{head.bundle_name}"]

        with pytest.raises(StorageUnavailableError, match=UC_URL):
            _project_storage.check_upstream(fork_root)

        assert _run_git(fork_root, "for-each-ref", "--format=%(refname) %(objectname)") == before

    def test_catching_up_publishes_to_the_forks_own_location(
        self,
        project: Path,
        files_api: _FakeFiles,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fork_root = _forked_project(project, tmp_path, monkeypatch)
        assert _stored_head(files_api, _FORK_ROOT).generation == 1

        _project_storage.pull_upstream(fork_root)

        _wait_until(lambda: _stored_head(files_api, _FORK_ROOT).generation == 2)
        assert _stored_head(files_api).generation == 3  # the parent is untouched


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
