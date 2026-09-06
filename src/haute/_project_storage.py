"""Durable project storage for hosted sessions — the policy layer.

A hosted container's filesystem — including the seeded git repository —
is destroyed by every redeploy, restart, and stop. This module gives a
hosted session a durable home: the project is bound to a durable
location, the binding record lives outside the container, and every save
and milestone commit is published to that location in the background.

Design: ``specs/hosted-project-storage/``. The shape in one line: git is
the store, the bound location is the durable copy, and the container
holds a clone. Two transports share that shape:

* an **https git remote** — the location IS a git remote and publishing
  is ``push_working_pair``;
* a **Unity Catalog volume** (``uc://catalog.schema.volume/path``) —
  mirrored as complete git bundles over the Files API, with a pointer
  written last and a claim lease making the location behave like a
  locally-owned file. The volume mechanics live in
  :mod:`haute._uc_transport`; this module is the canonical import
  surface for both layers.

This layer owns the POLICY: URL validation and the git-credential host
allowlist, the binding record and its lifecycle (bind, restore-at-boot),
the background publishers (:class:`PushQueue`, :class:`BindTask`),
transport dispatch, and the fork's upstream check/catch-up orchestration.

Every git subprocess belongs to :mod:`haute._git` (the repository's
one-chokepoint-per-tool rule); this module orchestrates and never shells
out itself.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from haute._logging import get_logger
from haute._storage_types import (
    StorageClaimedError as StorageClaimedError,
)
from haute._storage_types import (
    StorageConfigError as StorageConfigError,
)
from haute._storage_types import (
    StorageError as StorageError,
)
from haute._storage_types import (
    StorageSupersededError as StorageSupersededError,
)
from haute._storage_types import (
    StorageUnavailableError as StorageUnavailableError,
)
from haute._storage_types import (
    UCClaim as UCClaim,
)
from haute._storage_types import (
    UCHead as UCHead,
)
from haute._storage_types import (
    UCLineage as UCLineage,
)
from haute._storage_types import now_iso
from haute._uc_transport import (
    REMOTE_NAME as REMOTE_NAME,
)
from haute._uc_transport import (
    UPSTREAM_NAMESPACE as UPSTREAM_NAMESPACE,
)
from haute._uc_transport import (
    _scope_name,
    bless_generation,
    clone_matches_head,
    fetch_upstream_refs,
    validate_uc_url,
    volume_read,
    volume_write,
)
from haute._uc_transport import (
    acquire_uc_claim as acquire_uc_claim,
)
from haute._uc_transport import (
    clear_uc_lineage as clear_uc_lineage,
)
from haute._uc_transport import (
    fork_uc_location as fork_uc_location,
)
from haute._uc_transport import (
    is_uc_url as is_uc_url,
)
from haute._uc_transport import (
    publish_to_uc as publish_to_uc,
)
from haute._uc_transport import (
    read_uc_claim as read_uc_claim,
)
from haute._uc_transport import (
    read_uc_head as read_uc_head,
)
from haute._uc_transport import (
    read_uc_lineage as read_uc_lineage,
)
from haute._uc_transport import (
    release_uc_claim as release_uc_claim,
)
from haute._uc_transport import (
    restore_from_uc as restore_from_uc,
)

if TYPE_CHECKING:
    from haute.schemas import GitFastForwardResponse, GitRemoteLeg

logger = get_logger(component="project_storage")

#: ``catalog.schema.volume`` holding binding records (not project data).
STATE_VOLUME_ENV = "HAUTE_STATE_VOLUME"
#: Git credential for the bound remote, from an app secret resource.
GIT_TOKEN_ENV = "HAUTE_GIT_TOKEN"  # noqa: S105 - env var name, not a secret
#: Optional username for HTTPS basic auth; token-as-password is the norm.
GIT_USERNAME_ENV = "HAUTE_GIT_USERNAME"
#: Comma-separated hosts the git credential may be sent to. Required whenever
#: a token is configured: the bind endpoint is reachable by any app user, and
#: git hands the credential to whatever host the URL names.
GIT_ALLOWED_HOSTS_ENV = "HAUTE_GIT_ALLOWED_HOSTS"
#: Where the hosted project lives (kept out of the app source snapshot).
PROJECT_DIR_ENV = "HAUTE_PROJECT_DIR"

DEFAULT_GIT_USERNAME = "x-access-token"
_BINDING_FILE = "binding.json"
_BINDING_PREFIX = "haute-apps"
# https for real remotes; file:// is the local/bare-repo transport used by
# tests and offline experiments. Plain http and ssh are refused: the former
# would carry a token in clear, the latter needs key material this deployment
# model has nowhere to put.
_ALLOWED_SCHEMES = ("https://", "file://")

StorageState = Literal["unbound", "bound", "unsupported"]
SyncState = Literal["synced", "pending", "failed"]
FailureClass = Literal["transport", "rejected", "config"]
RestoreOutcome = Literal["restored", "unbound", "present"]
BindOutcome = Literal["adopted", "restart-required"]
BindState = Literal["idle", "running", "succeeded", "failed"]


@dataclass(frozen=True)
class StorageBinding:
    """The durable pointer from this app to its project's remote."""

    remote_url: str
    branch: str | None = None
    bound_by: str | None = None
    bound_at: str | None = None

    def to_json(self) -> str:
        import json

        return json.dumps(
            {
                "remote_url": self.remote_url,
                "branch": self.branch,
                "bound_by": self.bound_by,
                "bound_at": self.bound_at,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> StorageBinding:
        """Parse a stored record, tolerating fields a newer haute added.

        Unknown keys are ignored on purpose: a container running an older
        haute must not be bricked by a record written by a newer one.
        """
        if not isinstance(payload, dict):
            raise StorageUnavailableError("The stored binding record is not an object.")
        remote_url = payload.get("remote_url")
        if not isinstance(remote_url, str) or not remote_url.strip():
            raise StorageUnavailableError("The stored binding record has no remote URL.")
        branch = payload.get("branch")
        bound_by = payload.get("bound_by")
        bound_at = payload.get("bound_at")
        return cls(
            remote_url=remote_url.strip(),
            branch=branch if isinstance(branch, str) and branch else None,
            bound_by=bound_by if isinstance(bound_by, str) and bound_by else None,
            bound_at=bound_at if isinstance(bound_at, str) and bound_at else None,
        )


@dataclass(frozen=True)
class SyncStatus:
    """What the UI shows beside the branch indicator."""

    state: SyncState
    pending: int = 0
    failure: FailureClass | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Remote URL validation
# ---------------------------------------------------------------------------


def _allowed_hosts() -> list[str]:
    raw = os.environ.get(GIT_ALLOWED_HOSTS_ENV, "")
    return [host.strip().lower() for host in raw.split(",") if host.strip()]


def _assert_credential_may_reach(host: str) -> None:
    """Refuse to let the app's git token travel to an unapproved host.

    ``GIT_ASKPASS`` is process-wide and git offers the credential to
    whatever host a URL names, so without this check any user who can
    reach the bind endpoint could point it at a host they control and
    collect the app's token from the resulting auth challenge. The check
    runs before any git subprocess sees the URL.
    """
    if not os.environ.get(GIT_TOKEN_ENV, "").strip():
        return  # No credential to leak.
    allowed = _allowed_hosts()
    if not allowed:
        raise StorageConfigError(
            f"This deployment has a git credential configured but no "
            f"{GIT_ALLOWED_HOSTS_ENV}, so it cannot tell which hosts may receive it. "
            f"Set {GIT_ALLOWED_HOSTS_ENV} to the git host(s) this app may publish to "
            "(for example 'github.com')."
        )
    if host.lower() not in allowed:
        raise StorageConfigError(
            f"'{host}' is not an approved git host for this app. Approved: {', '.join(allowed)}."
        )


def validate_remote_url(url: str) -> str:
    """Return the normalised *url*, or raise with the accepted forms.

    Rejects embedded credentials outright: the token belongs in the app's
    secret resource, and a URL with a password in it would be written into
    ``.git/config`` and every remote-tracking log line. Also enforces the
    credential host allowlist — see :func:`_assert_credential_may_reach`.
    ``uc://`` locations take their own validation path: no host, no git
    credential — just a volume name and a project path.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise StorageConfigError(
            "Enter the HTTPS URL of the git repository — or the uc:// volume "
            "location — to store this project."
        )
    if any(char.isspace() for char in candidate):
        raise StorageConfigError("A repository URL cannot contain spaces.")
    if is_uc_url(candidate):
        return validate_uc_url(candidate)
    if not candidate.startswith(_ALLOWED_SCHEMES):
        raise StorageConfigError(
            f"'{candidate.split('://')[0]}' URLs are not supported for project storage. "
            "Use an https:// repository URL, or uc://catalog.schema.volume/path for a "
            "Unity Catalog volume."
        )
    authority = candidate.split("://", 1)[1]
    host = authority.split("/", 1)[0]
    if "@" in host:
        raise StorageConfigError(
            "Remove the credentials from the URL — the access token is supplied by the "
            f"app's {GIT_TOKEN_ENV} secret, so it never has to live in the URL."
        )
    if candidate.startswith("https://"):
        # file:// has no host and carries no credential; https does both.
        _assert_credential_may_reach(host.split(":", 1)[0])
    return candidate


# ---------------------------------------------------------------------------
# Binding record (Files API)
# ---------------------------------------------------------------------------


def state_volume_configured() -> bool:
    return bool(os.environ.get(STATE_VOLUME_ENV, "").strip())


def _state_volume_root() -> str:
    raw = os.environ.get(STATE_VOLUME_ENV, "").strip()
    if not raw:
        raise StorageConfigError(
            f"Durable project storage needs {STATE_VOLUME_ENV} set to a Unity Catalog "
            "volume (catalog.schema.volume) the app can read and write."
        )
    parts = raw.split(".")
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise StorageConfigError(
            f"{STATE_VOLUME_ENV} must be a three-part volume name "
            f"(catalog.schema.volume); got '{raw}'."
        )
    return "/Volumes/" + "/".join(part.strip() for part in parts)


def binding_file_path() -> str:
    return f"{_state_volume_root()}/{_BINDING_PREFIX}/{_scope_name()}/{_BINDING_FILE}"


def read_binding() -> StorageBinding | None:
    """Return the recorded binding, or ``None`` when this app has none.

    Raises :class:`StorageUnavailableError` when the record exists but
    cannot be read — the caller must gate rather than treat that as
    unbound.
    """
    import json

    raw = volume_read(
        binding_file_path(),
        event="binding_read_failed",
        unavailable="The project's storage binding could not be read from the state volume.",
    )
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageUnavailableError(
            "The project's storage binding record is unreadable."
        ) from exc
    return StorageBinding.from_payload(payload)


def write_binding(binding: StorageBinding) -> None:
    volume_write(
        binding_file_path(),
        binding.to_json().encode("utf-8"),
        event="binding_write_failed",
        unavailable="The project's storage binding could not be saved to the state volume.",
    )
    logger.info("binding_written", scope=_scope_name())


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

_ASKPASS_SCRIPT = """#!/bin/sh
# Generated by haute for hosted project storage. Reads the token from the
# process environment at call time — the value is never written into this
# file, a git config, or a command line.
case "$1" in
  Username*) printf '%s' "${HAUTE_GIT_USERNAME:-x-access-token}" ;;
  Password*) printf '%s' "${HAUTE_GIT_TOKEN}" ;;
  *) exit 1 ;;
esac
"""


def configure_git_credentials(runtime_dir: Path) -> Path | None:
    """Install the askpass helper when a token is configured.

    Returns the helper path, or ``None`` when no token is set (an
    unauthenticated remote — a public repo or a ``file://`` path — still
    works). Sets ``GIT_ASKPASS`` process-wide: every git invocation in a
    hosted container serves the one bound project.
    """
    if not os.environ.get(GIT_TOKEN_ENV, "").strip():
        return None
    runtime_dir.mkdir(parents=True, exist_ok=True)
    helper = runtime_dir / "git-askpass.sh"
    helper.write_text(_ASKPASS_SCRIPT, encoding="utf-8")
    helper.chmod(0o700)
    os.environ["GIT_ASKPASS"] = str(helper)
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    logger.info("git_credentials_configured", helper=helper.name)
    return helper


# ---------------------------------------------------------------------------
# Push queue
# ---------------------------------------------------------------------------


class PushQueue:
    """Serialised, coalescing background publisher for one project.

    One worker thread, one project. ``enqueue`` never blocks a save: it
    bumps a counter and returns. Each attempt publishes the CURRENT ref
    state, so N queued commits collapse into one push — the queue tracks
    how many saves are unpublished, not a list of work items.

    After a failure the worker stops attempting until something changes:
    a transport failure clears on the next save or a manual retry; a
    rejection or configuration failure needs the user to act, so only a
    manual retry clears it.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._project_root: Path | None = None
        self._pending = 0
        self._blocked = False
        self._terminal = False
        self._failure: FailureClass | None = None
        self._message: str | None = None
        self._stopped = False
        self._armed = False

    # -- lifecycle ---------------------------------------------------------

    def start(self, project_root: Path) -> None:
        with self._condition:
            self._project_root = project_root
            self._stopped = False
            # Anything counted while armed is now the worker's to publish.
            self._armed = False
            if self._pending:
                self._condition.notify_all()
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="haute-project-push", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    @property
    def active(self) -> bool:
        return self._project_root is not None and not self._stopped

    # -- producer side -----------------------------------------------------

    def arm(self) -> None:
        """Count enqueues from now on, before a project root exists.

        A bind publishes over the network, and the user is invited to keep
        working while it runs. A save landing in that window commits
        locally but has no queue to join yet — without this the enqueue
        would be dropped AND the pending counter would stay at zero, so
        the UI would report ``synced`` over an unpublished commit. Armed,
        the count survives until :meth:`start` hands it to the worker.
        """
        with self._condition:
            self._armed = True

    def disarm(self) -> None:
        """Stop counting: no queue will start for the bind that armed us.

        The count is dropped with it — those commits are unpublished, but
        the session is not bound, so "unpublished" is not the state to
        show; the storage surface says the project is unstored instead.
        """
        with self._condition:
            self._armed = False
            if self._project_root is None:
                self._pending = 0

    def enqueue(self) -> None:
        """Record one more unpublished commit and wake the worker."""
        if not self.active and not self._armed:
            return
        with self._condition:
            self._pending += 1
            if not self._terminal:
                self._blocked = False
            self._condition.notify_all()

    def retry_now(self) -> None:
        """Clear any failure state and attempt again immediately."""
        if not self.active:
            # Without a worker nothing would consume the request, and the
            # forced pending count below would pin the UI to "unpublished".
            return
        with self._condition:
            self._blocked = False
            self._terminal = False
            if self._pending == 0:
                self._pending = 1
            self._condition.notify_all()

    def status(self) -> SyncStatus:
        with self._condition:
            if self._failure is not None:
                return SyncStatus(
                    state="failed",
                    pending=self._pending,
                    failure=self._failure,
                    message=self._message,
                )
            if self._pending > 0:
                return SyncStatus(state="pending", pending=self._pending)
            return SyncStatus(state="synced")

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._stopped and (self._pending == 0 or self._blocked):
                    self._condition.wait()
                if self._stopped:
                    return
                batch = self._pending
                project_root = self._project_root
            if project_root is None:  # pragma: no cover - start() sets it first
                continue
            self._attempt(batch, project_root)

    def _attempt(self, batch: int, project_root: Path) -> None:
        try:
            publish_bound_project(project_root)
        except Exception as exc:
            failure, message, terminal = _classify_push_failure(exc)
            with self._condition:
                self._failure = failure
                self._message = message
                self._blocked = True
                self._terminal = terminal
            logger.warning("project_push_failed", failure=failure, pending=batch)
            return
        with self._condition:
            self._pending = max(0, self._pending - batch)
            self._failure = None
            self._message = None
        logger.info("project_pushed", published=batch)


def _classify_push_failure(exc: Exception) -> tuple[FailureClass, str, bool]:
    """Map a publish exception to (class, user-facing message, terminal).

    Messages name the object and the action, never raw git stderr — which
    routinely carries the remote URL and any credential inside it.
    """
    from haute._git import GitDomainError, GitPushRejectedError

    if isinstance(exc, StorageSupersededError):
        # The uc:// analogue of a rejected push: someone else moved the
        # durable state, so only a deliberate act may resume publishing.
        return "rejected", str(exc), True
    if isinstance(exc, StorageClaimedError):
        # The lease was taken over while this process stalled; the new
        # holder is named and only a deliberate act may resume.
        return "rejected", str(exc), True
    if isinstance(exc, StorageConfigError):
        return "config", str(exc), True
    if isinstance(exc, StorageUnavailableError):
        # Hand-authored transport prose; retried on the next save.
        return "transport", str(exc), False
    if isinstance(exc, GitPushRejectedError):
        return (
            "rejected",
            "The remote has commits this session does not — publishing stopped so "
            "nothing is overwritten. Resolve the divergence, then retry.",
            True,
        )
    if isinstance(exc, GitDomainError):
        # Hand-authored, already user-facing (guardrail and validation text).
        return "config", str(exc), True
    return (
        "transport",
        "Could not reach the project's remote. Saves are kept locally and will "
        "publish on the next save, or retry now.",
        False,
    )


# ---------------------------------------------------------------------------
# Background bind
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BindStatus:
    """Progress of a background bind, as the UI sees it."""

    state: BindState = "idle"
    outcome: BindOutcome | None = None
    message: str | None = None
    claim: UCClaim | None = None
    remote_url: str | None = None


class BindTask:
    """Runs one bind in the background so a save-blocking modal isn't needed.

    A bind publishes the whole project, so its duration is the project's
    size plus the volume's latency. The route keeps only the instant,
    local checks (a typo belongs beside the input field) and hands the
    rest here. The result stays readable after completion — a UI polling
    every few seconds must not miss it — until the user acknowledges it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = BindStatus()
        self._thread: threading.Thread | None = None

    def status(self) -> BindStatus:
        with self._lock:
            return self._status

    @property
    def running(self) -> bool:
        with self._lock:
            return self._status.state == "running"

    def acknowledge(self) -> None:
        """Clear a finished result once the UI has shown it."""
        with self._lock:
            if self._status.state in ("succeeded", "failed"):
                self._status = BindStatus()

    def start(self, url: str, project_root: Path, bound_by: str | None = None) -> None:
        """Begin a bind. Raises if one is already in flight."""
        with self._lock:
            if self._status.state == "running":
                raise StorageConfigError(
                    "This project is already being saved to storage. Wait for that to "
                    "finish before binding somewhere else."
                )
            self._status = BindStatus(state="running", remote_url=url)
        # The user is told they can keep working; saves made from here until
        # the queue starts must still be counted as unpublished.
        _session.queue.arm()
        thread = threading.Thread(
            target=self._run,
            args=(url, project_root, bound_by),
            name="haute-storage-bind",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _run(self, url: str, project_root: Path, bound_by: str | None) -> None:
        try:
            outcome = bind_remote(url, project_root, bound_by=bound_by)
        except StorageClaimedError as exc:
            self._fail(url, str(exc), claim=exc.claim)
            return
        except StorageError as exc:
            self._fail(url, str(exc))
            return
        except Exception as exc:
            failure, message, _ = _classify_push_failure(exc)
            logger.warning("storage_bind_failed_async", failure=failure)
            self._fail(url, message)
            return
        if outcome != "adopted":
            # A lift starts no queue in this process — the restart does.
            _session.queue.disarm()
        with self._lock:
            self._status = BindStatus(state="succeeded", outcome=outcome, remote_url=url)

    def _fail(self, url: str, message: str, claim: UCClaim | None = None) -> None:
        _session.queue.disarm()
        with self._lock:
            self._status = BindStatus(state="failed", message=message, claim=claim, remote_url=url)


# ---------------------------------------------------------------------------
# Session state: what this process is bound to, and its publishers
# ---------------------------------------------------------------------------


@dataclass
class _SessionState:
    """One hosted container serves one project; this is that fact, in one place.

    The binding and lineage are cached at bind/restore time so the
    readiness endpoint (polled by every open tab) never costs a Files API
    round trip. Collected in one object so tests reset the whole session
    with one assignment instead of four.
    """

    binding: StorageBinding | None = None
    lineage: UCLineage | None = None
    queue: PushQueue = field(default_factory=PushQueue)
    bind: BindTask = field(default_factory=BindTask)


_session = _SessionState()


def push_queue() -> PushQueue:
    return _session.queue


def bind_task() -> BindTask:
    return _session.bind


def active_binding() -> StorageBinding | None:
    return _session.binding


def active_lineage() -> UCLineage | None:
    return _session.lineage


def enqueue_push() -> None:
    """Publish-after-commit hook. A no-op for unbound or local sessions."""
    _session.queue.enqueue()


def publish_bound_project(project_root: Path) -> None:
    """Publish current history to the bound location, then record the restart target.

    The transport is selected from the active binding's URL scheme: a
    ``uc://`` binding publishes a bundle generation, anything else — a git
    binding, or no recorded binding at all — is the pre-existing push to
    ``origin``. The no-binding default keeps a queue started without a
    binding (harnesses, tests) behaving exactly as before.

    A successful publish then points the durable binding at the working
    branch it published (see :func:`_record_restart_target`), so a
    replacement container resumes the branch the user last published on,
    not the one recorded when the project was bound.
    """
    binding = _session.binding
    if binding is not None and is_uc_url(binding.remote_url):
        publish_to_uc(binding.remote_url, project_root)
    else:
        from haute import _git

        _git.push_working_pair(REMOTE_NAME, project_root, cwd=project_root)
    _record_restart_target(binding, project_root)


def _record_restart_target(binding: StorageBinding | None, project_root: Path) -> None:
    """Point the durable binding at the branch a publish just carried.

    The restart target is the working branch in effect at the most recent
    SUCCESSFUL publication: it is refreshed only after the transport
    succeeded, so it never names a branch the stored project lacks, and a
    failed publish leaves the previous (published) target advertised. A
    branch selected, forked, archived or deleted without a later publish is
    clone-local and moves nothing durable. The record write is part of the
    publication — its :class:`StorageUnavailableError` propagates, so the
    queue reports a transport failure and retries instead of showing
    ``synced`` over a restart target the volume never received.
    """
    if binding is None:
        return
    from haute._git_state import read_working_branch

    working = read_working_branch(project_root)
    if working is None or working == binding.branch:
        return
    refreshed = replace(binding, branch=working)
    write_binding(refreshed)
    _session.binding = refreshed
    logger.info("restart_target_recorded", branch=working)


# ---------------------------------------------------------------------------
# Project directory, restore, bind
# ---------------------------------------------------------------------------


def resolve_project_dir() -> Path:
    """Where a hosted project lives.

    Deliberately outside the deployed source snapshot: the snapshot is
    replaced wholesale on every deploy and mixing project files into it
    put the app's own bundle under haute's file watcher.
    """
    configured = os.environ.get(PROJECT_DIR_ENV, "").strip()
    return Path(configured) if configured else Path.home() / "haute-project"


def restore_if_bound(project_dir: Path) -> RestoreOutcome:
    """Materialise the bound project into *project_dir* before serving.

    ``present`` means the directory already holds the clone (a restart
    that kept the filesystem); ``unbound`` means this app has no binding
    and the caller should seed a volatile project. Any failure raises —
    a hosted boot must gate rather than quietly start a fresh project
    over durable work.
    """
    if not state_volume_configured():
        return "unbound"
    binding = read_binding()
    if binding is None:
        return "unbound"

    from haute import _git

    # The record lives outside this process; re-validate it rather than
    # trusting it to still satisfy the rules bind enforced (scheme, absent
    # userinfo, approved credential host).
    remote_url = validate_remote_url(binding.remote_url)

    if (project_dir / ".git").exists():
        existing = _git.remote_url(REMOTE_NAME, cwd=project_dir)
        if existing != remote_url:
            raise StorageUnavailableError(
                "The project directory holds a clone of a different repository than "
                "this app is bound to. Remove it, or rebind, before starting."
            )
        if is_uc_url(remote_url):
            # Take the lease before reusing the clone — a boot cannot offer
            # a dialog, so a live foreign claim gates with its holder named;
            # a predecessor's claim carries this app's own name and is taken
            # over immediately.
            acquire_uc_claim(remote_url, user=binding.bound_by)
            # A new process means a new writer identity, so the supersession
            # fence must learn which generation this clone derives from — and
            # only an EXACT match against the clone's own generation record
            # counts. No record, or any mismatch, leaves the fence armed and
            # the first publish stops loudly instead of overwriting the
            # newer generation.
            head = read_uc_head(remote_url)
            if head is not None and clone_matches_head(project_dir, head):
                bless_generation(head.generation)
            _session.lineage = read_uc_lineage(remote_url)
        _session.binding = binding
        _session.queue.start(project_dir)
        return "present"

    logger.info("project_restore_started", scope=_scope_name())
    if is_uc_url(remote_url):
        acquire_uc_claim(remote_url, user=binding.bound_by)
        restore_from_uc(remote_url, project_dir)
        _session.lineage = read_uc_lineage(remote_url)
    else:
        _git.clone_project(remote_url, project_dir, branch=None)
    if binding.branch:
        from haute._git_state import write_working_branch

        # A plain clone materialises only the remote's default branch, so the
        # managed lineage has to be recreated locally before the session can
        # show the user's saves or publish again.
        try:
            _git.adopt_cloned_lineage(binding.branch, REMOTE_NAME, cwd=project_dir)
        except _git.GitDomainError as exc:
            # The recorded branch is not in the stored project — it was
            # deleted, or the record predates a change of branch. The project
            # itself restored fine, so serve it and let the user choose a
            # working branch; failing the boot here would strand the app with
            # no route back, since rebinding needs a running app.
            logger.warning("restored_branch_missing", branch=binding.branch, error=str(exc))
        else:
            # `.haute/` is per-clone and untracked by design, so the working
            # branch does not travel in the repository — the binding carries it
            # so a restored container resumes on the same lineage.
            write_working_branch(project_dir, binding.branch)
    _session.binding = binding
    _session.queue.start(project_dir)
    logger.info("project_restored", scope=_scope_name())
    return "restored"


def precheck_bind(url: str) -> str:
    """Run the instant, local half of bind and return the normalised URL.

    These are exactly the checks whose answer belongs beside the input
    field — a malformed URL, an already-bound project, a deployment with
    nowhere to record a binding. Everything beyond this point is network
    work and runs in the background (:class:`BindTask`).
    """
    remote_url = validate_remote_url(url)
    if _session.binding is not None:
        # Repointing origin under a live publisher would send this project's
        # history to a remote the session was never verified against.
        raise StorageConfigError(
            "This project is already bound to durable storage. Restart the app to "
            "bind it somewhere else."
        )
    if not state_volume_configured():
        raise StorageConfigError(
            f"This deployment has no state volume configured, so a binding cannot be "
            f"remembered across restarts. Set {STATE_VOLUME_ENV} to a Unity Catalog "
            "volume (catalog.schema.volume) the app can write."
        )
    return remote_url


def bind_remote(url: str, project_root: Path, bound_by: str | None = None) -> BindOutcome:
    """Bind this project to *url* and make its history durable.

    An empty remote adopts the current project immediately: the local
    history is published and the session continues uninterrupted. A
    populated remote records the binding and reports that a restart is
    needed — lifting a different project over a running server's working
    directory is not safe to do live, and the boot path already does it
    cleanly.
    """
    from haute import _git
    from haute._git_state import read_working_branch

    remote_url = precheck_bind(url)

    if is_uc_url(remote_url):
        # Claim first: the emptiness check and everything after it happen
        # under our lease, and a location another app actively holds is
        # refused with its holder named before any state is touched.
        acquire_uc_claim(remote_url, user=bound_by)
        # `git ls-remote` cannot inspect a uc:// location, so "is the remote
        # empty?" becomes "was anything ever published there?".
        populated = read_uc_head(remote_url) is not None
    else:
        _git.ensure_remote(REMOTE_NAME, remote_url, cwd=project_root)
        populated = _git.remote_has_content(REMOTE_NAME, cwd=project_root)
    binding = StorageBinding(
        remote_url=remote_url,
        branch=read_working_branch(project_root),
        bound_by=bound_by,
        bound_at=now_iso(),
    )

    if populated:
        # The stored project is NOT this session's project, so this session's
        # working branch says nothing about it — recording it would have the
        # restart look for a branch the clone has never heard of. Left unset,
        # the restored session lands in the branch-selection modal, which is
        # the honest state: a project arrived, choose where to work in it.
        write_binding(replace(binding, branch=None))
        # Deliberately NOT activated in this process: the project on disk is
        # not yet the bound remote's project, so publishing from here would
        # push the wrong history. The restart's restore path activates it.
        logger.info("project_bound", outcome="restart-required")
        return "restart-required"

    # Publish first: a binding that points at a remote we could not write to
    # would promise durability the next boot cannot deliver.
    if is_uc_url(remote_url):
        # Origin carries the uc:// URL as the clone's identity marker, so the
        # restore path can recognise this directory as the bound project.
        _git.ensure_remote(REMOTE_NAME, remote_url, cwd=project_root)
        # An aborted fork can leave a LINEAGE.json with no pointer; adopting
        # this (empty) location must not let that label attach itself to an
        # unrelated project.
        clear_uc_lineage(remote_url)
        publish_to_uc(remote_url, project_root)
    else:
        _git.push_working_pair(REMOTE_NAME, project_root, cwd=project_root)
    write_binding(binding)
    _session.binding = binding
    # An adopted location was empty, so it cannot be a fork.
    _session.lineage = None
    _session.queue.start(project_root)
    logger.info("project_bound", outcome="adopted")
    return "adopted"


# ---------------------------------------------------------------------------
# Upstream: a fork's view of its parent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpstreamStatus:
    """A fork's measured relationship to the parent it was forked from."""

    parent_url: str
    parent_generation: int
    working: GitRemoteLeg
    ledger: GitRemoteLeg
    can_fast_forward: bool
    checked_at: str


def check_upstream(project_root: Path) -> UpstreamStatus:
    """Measure how far this fork sits from its parent's published tips.

    On-demand only: it downloads the parent's whole bundle, so it must
    never join the polled readiness path. A fork is a complete project,
    not a dependent one — every failure here is a failure of the CHECK,
    and leaves the fork's own saves, publishes, and boot untouched.
    """
    from haute import _git

    binding = _session.binding
    if binding is None or not is_uc_url(binding.remote_url):
        raise StorageConfigError(
            "Only projects stored on a Unity Catalog volume have a parent to compare against."
        )
    lineage = read_uc_lineage(binding.remote_url)
    if lineage is None:
        raise StorageConfigError(
            "This project was not forked from another one, so there is no parent to catch up to."
        )
    head = read_uc_head(lineage.parent_url)
    if head is None:
        raise StorageConfigError(
            f"The parent project at {lineage.parent_url} has nothing published to compare against."
        )

    fetch_upstream_refs(lineage.parent_url, head, project_root)
    working, ledger = _git.pair_divergence(UPSTREAM_NAMESPACE, project_root, cwd=project_root)
    measurable = all(leg.status in ("synced", "behind") for leg in (working, ledger))
    can_fast_forward = measurable and any(leg.status == "behind" for leg in (working, ledger))
    return UpstreamStatus(
        parent_url=lineage.parent_url,
        parent_generation=head.generation,
        working=working,
        ledger=ledger,
        can_fast_forward=can_fast_forward,
        checked_at=now_iso(),
    )


def pull_upstream(project_root: Path) -> GitFastForwardResponse:
    """Catch this fork up to its parent's published tips, fast-forward only.

    One-directional by design: a fork's work is never written back to the
    parent, whose location another app holds the claim on. The check is
    re-run first so the decision is made on a fresh snapshot, exactly as
    :func:`_git.fast_forward_pair` re-fetches before deciding.
    """
    from haute import _git

    check_upstream(project_root)
    response = _git.fast_forward_pair_from_tracking(
        UPSTREAM_NAMESPACE,
        project_root,
        cwd=project_root,
        source_label="the parent project",
    )
    # The caught-up state belongs in the FORK's own location, not the parent's.
    enqueue_push()
    return response


def storage_state() -> StorageState:
    """Coarse state for the readiness surface.

    ``unsupported`` means this deployment cannot remember a binding at
    all (no state volume, i.e. every local session) — the UI hides the
    storage surface rather than offering an action that cannot work.
    """
    if not state_volume_configured():
        return "unsupported"
    return "bound" if _session.binding is not None else "unbound"
