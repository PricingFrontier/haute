"""Durable project storage for hosted sessions.

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
* a **Unity Catalog volume** (``uc://catalog.schema.volume/path``) — the
  container has no volume mounts, so the volume is reachable only via
  the Files API, which git cannot speak. The bridge is ``git bundle``:
  each publish uploads a complete, generation-numbered bundle and then
  writes a small ``HEAD.json`` pointer LAST, so a torn upload is never
  followed; restore clones straight from the pointed-at bundle.

The collaborating pieces:

* **Binding record** — ``{remote_url, branch, bound_by, bound_at}`` as
  JSON on a Unity Catalog volume via the Files API (JSON travels fine
  over REST; a git *remote protocol* does not — hence the bundle bridge
  when the project itself lives on a volume).
* **Credentials** — a token from an app secret resource, reaching git
  exclusively through a generated ``GIT_ASKPASS`` helper. The token is
  never written into a URL, a git config, a command line, or a log.
  ``uc://`` locations involve no git credential: the Files API uses the
  workspace SDK's own authentication.
* **Push queue** — a single background worker that coalesces pending
  commits into one publish per attempt, so saves never wait on the
  network and a failure is visible rather than silent.

Every git subprocess belongs to :mod:`haute._git` (the repository's
one-chokepoint-per-tool rule); this module orchestrates and never shells
out itself.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from haute._logging import get_logger
from haute.errors import HauteError

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
REMOTE_NAME = "origin"
_BINDING_FILE = "binding.json"
_BINDING_PREFIX = "haute-apps"
# https for real remotes; file:// is the local/bare-repo transport used by
# tests and offline experiments. Plain http and ssh are refused: the former
# would carry a token in clear, the latter needs key material this deployment
# model has nowhere to put.
_ALLOWED_SCHEMES = ("https://", "file://")
# The Unity Catalog volume transport: uc://catalog.schema.volume/path/to/project
# resolves to /Volumes/catalog/schema/volume/path/to/project, reachable only via
# the Files API. Validated separately from the git schemes above.
_UC_SCHEME = "uc://"
_UC_BUNDLE_DIR = "bundles"
_UC_HEAD_FILE = "HEAD.json"
_UC_CLAIM_FILE = "CLAIM.json"
_UC_LINEAGE_FILE = "LINEAGE.json"
#: Generations kept after a publish — cheap rollback without unbounded growth.
_UC_BUNDLE_RETAIN = 5
#: Lease cadence: the holder refreshes CLAIM.json this often...
_UC_CLAIM_HEARTBEAT_SECONDS = 30.0
#: ... and a claim whose heartbeat is older than this is dead and may be
#: taken over (5 missed beats — far above one slow API call, far below a
#: human waiting at a bind dialog).
_UC_CLAIM_STALE_SECONDS = 150.0

StorageState = Literal["unbound", "bound", "unsupported"]
SyncState = Literal["synced", "pending", "failed"]
FailureClass = Literal["transport", "rejected", "config"]
RestoreOutcome = Literal["restored", "unbound", "present"]
BindOutcome = Literal["adopted", "restart-required"]


class StorageError(HauteError):
    """Base for durable-storage failures."""


class StorageConfigError(StorageError):
    """The deployment is misconfigured; the message names what to set."""


class StorageUnavailableError(StorageError):
    """The binding record could not be read or written.

    Distinct from "no binding exists": an unreadable record must gate the
    session, never be mistaken for an unbound one (that would silently
    start a fresh project over durable work).
    """


class StorageSupersededError(StorageError):
    """Another container advanced this project's ``uc://`` pointer.

    Single-writer is the design assumption (one container, one project),
    but a replacement container can start while the old one still holds
    queued saves. The read-before-write fence turns that into a loud,
    terminal stop instead of two writers silently interleaving
    generations.
    """


class StorageClaimedError(StorageError):
    """The ``uc://`` location is under another holder's live lease.

    Carries the structured holder record so the refusal can name who
    holds the location and how fresh their heartbeat is — the user is
    steered, not stonewalled (bind elsewhere, or fork the location).
    """

    def __init__(self, message: str, claim: UCClaim) -> None:
        super().__init__(message)
        self.claim = claim


@dataclass(frozen=True)
class StorageBinding:
    """The durable pointer from this app to its project's remote."""

    remote_url: str
    branch: str | None = None
    bound_by: str | None = None
    bound_at: str | None = None

    def to_json(self) -> str:
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


@dataclass(frozen=True)
class UCHead:
    """The ``HEAD.json`` pointer under a ``uc://`` location.

    Written LAST on every publish — the Files API has no atomic rename,
    so the pointer arriving after its bundle is what makes a torn upload
    harmless: readers only ever follow a generation that is complete.
    """

    generation: int
    tip_sha: str
    writer_id: str
    written_at: str | None = None
    #: The bundle file this pointer describes. Writer-unique names make
    #: bundle bytes immutable by construction (racing writers can never
    #: overwrite each other's upload); ``None`` means a pointer written by
    #: an earlier build, read as the legacy ``NNNNNN.bundle``.
    bundle_name: str | None = None

    def bundle_filename(self) -> str:
        return self.bundle_name or f"{self.generation:06d}.bundle"

    def to_json(self) -> str:
        return json.dumps(
            {
                "generation": self.generation,
                "tip_sha": self.tip_sha,
                "writer_id": self.writer_id,
                "written_at": self.written_at,
                "bundle_name": self.bundle_name,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> UCHead:
        """Parse a stored pointer, tolerating fields a newer haute added.

        A malformed pointer raises :class:`StorageConfigError`: retrying
        cannot fix a corrupted record, so the failure must classify as
        terminal, not as retryable transport.
        """
        if not isinstance(payload, dict):
            raise StorageConfigError(
                "The stored storage pointer is not an object — the location may be "
                "corrupted. Rebind the project, or restore the volume's contents."
            )
        generation = payload.get("generation")
        tip_sha = payload.get("tip_sha")
        writer_id = payload.get("writer_id")
        if not isinstance(generation, int) or generation < 1:
            raise StorageConfigError(
                "The stored storage pointer has no valid generation — the location "
                "may be corrupted. Rebind the project, or restore the volume's contents."
            )
        if not isinstance(tip_sha, str) or not tip_sha.strip():
            raise StorageConfigError(
                "The stored storage pointer has no tip commit — the location may be "
                "corrupted. Rebind the project, or restore the volume's contents."
            )
        if not isinstance(writer_id, str) or not writer_id.strip():
            raise StorageConfigError(
                "The stored storage pointer has no writer identity — the location "
                "may be corrupted. Rebind the project, or restore the volume's contents."
            )
        written_at = payload.get("written_at")
        bundle_name = payload.get("bundle_name")
        return cls(
            generation=generation,
            tip_sha=tip_sha.strip(),
            writer_id=writer_id.strip(),
            written_at=written_at if isinstance(written_at, str) and written_at else None,
            bundle_name=bundle_name if isinstance(bundle_name, str) and bundle_name else None,
        )


@dataclass(frozen=True)
class UCClaim:
    """The ``CLAIM.json`` lease beside a location's pointer.

    The claim makes a shared volume location behave like a locally-owned
    file: one holder at a time, visible to everyone else by name. It is
    a lease, not a liveness probe — the holder refreshes ``refreshed_at``
    on a heartbeat and on every publish, and a record whose heartbeat is
    older than ``_UC_CLAIM_STALE_SECONDS`` is dead. The ``nonce`` exists
    because the Files API has no compare-and-swap: acquisition writes a
    fresh nonce and reads it back to detect a lost race.
    """

    app_name: str
    writer_id: str
    nonce: str
    user: str | None = None
    claimed_at: str | None = None
    refreshed_at: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "app_name": self.app_name,
                "writer_id": self.writer_id,
                "nonce": self.nonce,
                "user": self.user,
                "claimed_at": self.claimed_at,
                "refreshed_at": self.refreshed_at,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> UCClaim | None:
        """Parse a stored claim; a malformed record reads as ``None``.

        Deliberately lenient where the pointer parser is strict: a corrupt
        lease must not brick the location it guards — it reads as stale
        and is taken over, and the publish fence still backstops writes.
        """
        if not isinstance(payload, dict):
            return None
        app_name = payload.get("app_name")
        writer_id = payload.get("writer_id")
        nonce = payload.get("nonce")
        if not isinstance(app_name, str) or not app_name.strip():
            return None
        if not isinstance(writer_id, str) or not writer_id.strip():
            return None
        if not isinstance(nonce, str) or not nonce.strip():
            return None
        user = payload.get("user")
        claimed_at = payload.get("claimed_at")
        refreshed_at = payload.get("refreshed_at")
        return cls(
            app_name=app_name.strip(),
            writer_id=writer_id.strip(),
            nonce=nonce.strip(),
            user=user if isinstance(user, str) and user else None,
            claimed_at=claimed_at if isinstance(claimed_at, str) and claimed_at else None,
            refreshed_at=refreshed_at if isinstance(refreshed_at, str) and refreshed_at else None,
        )


@dataclass(frozen=True)
class UCLineage:
    """The ``LINEAGE.json`` provenance record on a forked location.

    Written once at fork time and never updated: it is what makes a fork
    signposted rather than silent, and what a future synchronise-from-
    upstream feature would walk.
    """

    parent_url: str
    parent_generation: int
    parent_tip_sha: str
    forked_at: str | None = None
    forked_by: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "parent_url": self.parent_url,
                "parent_generation": self.parent_generation,
                "parent_tip_sha": self.parent_tip_sha,
                "forked_at": self.forked_at,
                "forked_by": self.forked_by,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> UCLineage | None:
        """Parse a stored lineage record; malformed reads as ``None``.

        Lineage is informational provenance — a corrupt record degrades
        to "not a fork", never to a gated session.
        """
        if not isinstance(payload, dict):
            return None
        parent_url = payload.get("parent_url")
        parent_generation = payload.get("parent_generation")
        parent_tip_sha = payload.get("parent_tip_sha")
        if not isinstance(parent_url, str) or not parent_url.strip():
            return None
        if not isinstance(parent_generation, int) or parent_generation < 1:
            return None
        if not isinstance(parent_tip_sha, str) or not parent_tip_sha.strip():
            return None
        forked_at = payload.get("forked_at")
        forked_by = payload.get("forked_by")
        return cls(
            parent_url=parent_url.strip(),
            parent_generation=parent_generation,
            parent_tip_sha=parent_tip_sha.strip(),
            forked_at=forked_at if isinstance(forked_at, str) and forked_at else None,
            forked_by=forked_by if isinstance(forked_by, str) and forked_by else None,
        )


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


def is_uc_url(url: str) -> bool:
    """Whether *url* names a Unity Catalog volume location."""
    return url.startswith(_UC_SCHEME)


def _validate_uc_url(candidate: str) -> str:
    """Validate ``uc://catalog.schema.volume/path/to/project`` and return it.

    The path is joined under ``/Volumes/`` for the Files API, so empty,
    ``.`` and ``..`` segments are refused — a traversal segment would
    escape the volume. A trailing slash is normalised away.
    """
    accepted = "A Unity Catalog storage URL looks like uc://catalog.schema.volume/path/to/project."
    rest = candidate[len(_UC_SCHEME) :]
    volume, _, path = rest.partition("/")
    parts = volume.split(".")
    if len(parts) != 3 or not all(parts):
        raise StorageConfigError(
            f"'{volume}' is not a three-part Unity Catalog volume name. {accepted}"
        )
    path = path.rstrip("/")
    if not path:
        raise StorageConfigError(
            f"Add a project path inside the volume so the project has its own home. {accepted}"
        )
    if any(segment in ("", ".", "..") for segment in path.split("/")):
        raise StorageConfigError(
            f"The project path cannot contain empty or dot segments. {accepted}"
        )
    return f"{_UC_SCHEME}{volume}/{path}"


def _uc_volume_path(url: str) -> str:
    """The ``/Volumes/...`` root a validated ``uc://`` URL resolves to."""
    rest = url[len(_UC_SCHEME) :]
    volume, _, path = rest.partition("/")
    return "/Volumes/" + "/".join(volume.split(".")) + "/" + path


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
        return _validate_uc_url(candidate)
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


def _app_name() -> str | None:
    """The hosted app's platform name, or ``None`` off the platform.

    The distinction matters to the claim layer: "one container per app"
    is a platform guarantee, so it only justifies the own-app claim
    takeover when a real app name exists — every process WITHOUT one
    shares the fallback scope and must arbitrate by lease expiry.
    """
    raw = os.environ.get("DATABRICKS_APP_NAME", "").strip()
    return raw or None


def _scope_name() -> str:
    """Binding records are per app, so several apps can share one volume."""
    return _app_name() or "local"


def binding_file_path() -> str:
    return f"{_state_volume_root()}/{_BINDING_PREFIX}/{_scope_name()}/{_BINDING_FILE}"


def _files_api() -> Any:
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:  # pragma: no cover - exercised via stub in tests
        raise StorageConfigError(
            "Durable project storage needs the databricks-sdk package. "
            "Install the databricks extra: haute[databricks]."
        ) from exc
    return WorkspaceClient().files


def _is_not_found(exc: Exception) -> bool:
    try:
        from databricks.sdk.errors import NotFound
    except ImportError:  # pragma: no cover - SDK absent means we never got here
        return False
    return isinstance(exc, NotFound)


def read_binding() -> StorageBinding | None:
    """Return the recorded binding, or ``None`` when this app has none.

    Raises :class:`StorageUnavailableError` when the record exists but
    cannot be read — the caller must gate rather than treat that as
    unbound.
    """
    path = binding_file_path()
    try:
        response = _files_api().download(path)
        raw = response.contents.read()
    except Exception as exc:
        if _is_not_found(exc):
            return None
        logger.warning("binding_read_failed", error=str(exc))
        raise StorageUnavailableError(
            "The project's storage binding could not be read from the state volume."
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageUnavailableError(
            "The project's storage binding record is unreadable."
        ) from exc
    return StorageBinding.from_payload(payload)


def write_binding(binding: StorageBinding) -> None:
    import io

    path = binding_file_path()
    try:
        _files_api().upload(path, io.BytesIO(binding.to_json().encode("utf-8")), overwrite=True)
    except Exception as exc:
        logger.warning("binding_write_failed", error=str(exc))
        raise StorageUnavailableError(
            "The project's storage binding could not be saved to the state volume."
        ) from exc
    logger.info("binding_written", scope=_scope_name())


# ---------------------------------------------------------------------------
# Unity Catalog bundle transport
# ---------------------------------------------------------------------------
#
# Layout under /Volumes/<catalog>/<schema>/<volume>/<path>:
#   bundles/000042.bundle   — generation-numbered, each a complete `--all` bundle
#   HEAD.json               — {generation, tip_sha, writer_id, written_at}, LAST
#
# Full bundles, not incremental: O(history) rather than O(diff), but a pricing
# project (code + config JSON; data gitignored) is small, and every generation
# being independently complete removes the whole partial-chain failure class.

# Fencing state, per process: who this writer is, and the newest generation it
# has itself written or restored from. See publish_to_uc for the rule.
_uc_writer_id: str | None = None
_uc_last_seen_generation: int | None = None


def _writer_id() -> str:
    """This process's fencing identity, minted once per container process."""
    global _uc_writer_id
    if _uc_writer_id is None:
        import uuid

        _uc_writer_id = f"{_scope_name()}-{uuid.uuid4().hex[:12]}"
    return _uc_writer_id


def _uc_head_path(url: str) -> str:
    return f"{_uc_volume_path(url)}/{_UC_HEAD_FILE}"


def _uc_bundle_path(url: str, filename: str) -> str:
    return f"{_uc_volume_path(url)}/{_UC_BUNDLE_DIR}/{filename}"


def _uc_bundle_filename(generation: int) -> str:
    """This writer's unique filename for *generation*.

    The writer suffix is what makes bundle bytes immutable: two writers
    racing to the same generation upload to different paths, so the loser
    of the pointer race loses loudly at the fence — never by having its
    bytes overwritten underneath a pointer that still names them.
    """
    return f"{generation:06d}-{_writer_id()}.bundle"


#: Clone-side record of the generation this clone last embodied — what the
#: `present` restore path verifies against before blessing the fence.
_UC_GENERATION_RECORD = "uc-generation.json"


def _write_uc_generation_record(project_dir: Path, head: UCHead) -> None:
    """Remember, inside the clone, which published generation it embodies.

    Mere presence of a commit is not identity — another writer can publish
    a commit this clone also happens to contain — so the `present` path
    needs an exact record to compare the volume's pointer against.
    Best-effort: a failed write only leaves the fence armed (loud), never
    lets it bless wrongly.
    """
    state_dir = project_dir / ".haute"
    try:
        state_dir.mkdir(exist_ok=True)
        (state_dir / _UC_GENERATION_RECORD).write_text(
            json.dumps(
                {
                    "generation": head.generation,
                    "tip_sha": head.tip_sha,
                    "writer_id": head.writer_id,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("uc_generation_record_write_failed", error=str(exc))


def _read_uc_generation_record(project_dir: Path) -> dict[str, Any] | None:
    """The clone's generation record, or ``None`` (missing/unreadable = armed fence)."""
    path = project_dir / ".haute" / _UC_GENERATION_RECORD
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def read_uc_head(url: str) -> UCHead | None:
    """Return *url*'s pointer, or ``None`` when nothing was ever published.

    Raises :class:`StorageUnavailableError` when the pointer exists but
    cannot be read — like the binding record, an unreadable pointer must
    never be mistaken for an empty location.
    """
    path = _uc_head_path(url)
    try:
        response = _files_api().download(path)
        raw = response.contents.read()
    except Exception as exc:
        if _is_not_found(exc):
            return None
        logger.warning("uc_head_read_failed", error=str(exc))
        raise StorageUnavailableError(
            "The project's storage pointer could not be read from the volume."
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageConfigError(
            "The project's storage pointer is unreadable — the location may be "
            "corrupted. Rebind the project, or restore the volume's contents."
        ) from exc
    return UCHead.from_payload(payload)


# -- claim lease ------------------------------------------------------------

# The claim this process holds, if any. One hosted container serves one
# project, so a single held claim is the invariant, not a limitation.
_uc_claim: UCClaim | None = None
_uc_claim_url: str | None = None
_uc_release_registered = False


def _uc_claim_path(url: str) -> str:
    return f"{_uc_volume_path(url)}/{_UC_CLAIM_FILE}"


def _uc_lineage_path(url: str) -> str:
    return f"{_uc_volume_path(url)}/{_UC_LINEAGE_FILE}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_uc_claim(url: str) -> UCClaim | None:
    """Return the location's lease, or ``None`` when unclaimed.

    A malformed record also reads as ``None`` (a corrupt lease must not
    brick the location it guards — the publish fence still backstops
    writes). An API failure other than not-found raises: an unreadable
    lease store must gate a bind, not read as "unclaimed".
    """
    path = _uc_claim_path(url)
    try:
        response = _files_api().download(path)
        raw = response.contents.read()
    except Exception as exc:
        if _is_not_found(exc):
            return None
        logger.warning("uc_claim_read_failed", error=str(exc))
        raise StorageUnavailableError(
            "The storage location's claim record could not be read from the volume."
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeError, json.JSONDecodeError):
        return None
    return UCClaim.from_payload(payload)


def _write_uc_claim(url: str, claim: UCClaim) -> None:
    import io

    try:
        _files_api().upload(
            _uc_claim_path(url), io.BytesIO(claim.to_json().encode("utf-8")), overwrite=True
        )
    except Exception as exc:
        logger.warning("uc_claim_write_failed", error=str(exc))
        raise StorageUnavailableError(
            "The storage location's claim record could not be written to the volume."
        ) from exc


def _claim_age_seconds(claim: UCClaim) -> float | None:
    """Seconds since the claim's last heartbeat, or ``None`` if unparseable."""
    stamp = claim.refreshed_at or claim.claimed_at
    if not stamp:
        return None
    try:
        last = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return (datetime.now(UTC) - last).total_seconds()


def _claim_is_stale(claim: UCClaim) -> bool:
    """A lease with no parseable heartbeat, or one past the threshold, is dead."""
    age = _claim_age_seconds(claim)
    return age is None or age > _UC_CLAIM_STALE_SECONDS


def _claimed_error(claim: UCClaim) -> StorageClaimedError:
    age = _claim_age_seconds(claim)
    freshness = f"{int(age)} seconds ago" if age is not None else "recently"
    holder = f"app '{claim.app_name}'"
    if claim.user:
        holder += f" (bound by {claim.user})"
    return StorageClaimedError(
        f"This storage location is in use by {holder} — its last heartbeat was "
        f"{freshness}. Bind a different location, or fork this one to work on "
        "a copy.",
        claim,
    )


def acquire_uc_claim(url: str, user: str | None = None) -> UCClaim:
    """Take the lease on *url*, or raise naming the live holder.

    Absent, stale, malformed, and own-app claims are taken over (the
    platform runs one container per app, so a claim carrying this app's
    own name can only be a predecessor's). The Files API has no
    compare-and-swap, so acquisition is write-then-verify: write a claim
    with a fresh nonce, read it back, and proceed only if the nonce is
    ours — a lost race raises with whoever won. On success the heartbeat
    starts and a best-effort release is registered for clean shutdown.
    """
    global _uc_claim, _uc_claim_url, _uc_release_registered
    import uuid

    existing = read_uc_claim(url)
    if (
        existing is not None
        and existing.writer_id != _writer_id()
        and not _claim_is_stale(existing)
    ):
        # "One container per app" is a platform guarantee, so a claim
        # carrying this app's own name can only be a predecessor's — but
        # only when a real app name exists. Off the platform every process
        # shares the fallback scope, and two local processes must arbitrate
        # by lease expiry like strangers, not seize each other's claim.
        own_predecessor = _app_name() is not None and existing.app_name == _app_name()
        if not own_predecessor:
            raise _claimed_error(existing)

    now = _now_iso()
    ours = UCClaim(
        app_name=_scope_name(),
        writer_id=_writer_id(),
        nonce=uuid.uuid4().hex,
        user=user,
        claimed_at=now,
        refreshed_at=now,
    )
    _write_uc_claim(url, ours)
    written = read_uc_claim(url)
    if written is None or written.nonce != ours.nonce or written.writer_id != ours.writer_id:
        # Someone else wrote between our write and read-back. Whoever the
        # record now names holds the lease; if it is unreadable, say so
        # without pretending to know the holder.
        if written is not None:
            raise _claimed_error(written)
        raise StorageUnavailableError(
            "The storage location's claim could not be confirmed after writing it. Retry the bind."
        )

    _uc_claim = ours
    _uc_claim_url = url
    if not _uc_release_registered:
        import atexit

        atexit.register(release_uc_claim)
        _uc_release_registered = True
    _claim_heartbeat.start()
    logger.info("uc_claim_acquired", scope=_scope_name())
    return ours


def release_uc_claim() -> None:
    """Release the held lease if it is still ours (best-effort).

    Called at clean shutdown. Unclean death is the platform's normal
    case and is what lease expiry exists for, so every failure here is
    logged and swallowed — release must never turn a shutdown into an
    error.
    """
    global _uc_claim, _uc_claim_url

    _claim_heartbeat.stop()
    claim, url = _uc_claim, _uc_claim_url
    _uc_claim = None
    _uc_claim_url = None
    if claim is None or url is None:
        return
    try:
        current = read_uc_claim(url)
        if current is not None and current.nonce == claim.nonce:
            _files_api().delete(_uc_claim_path(url))
            logger.info("uc_claim_released", scope=_scope_name())
    except Exception as exc:
        logger.warning("uc_claim_release_failed", error=str(exc))


def _verify_uc_claim_for_publish(url: str) -> None:
    """Publishing only while holding the lease — the local-file analogy.

    Absent claims proceed (pre-claim locations, non-claiming callers);
    ours proceeds and refreshes the lease; a stale foreign claim is taken
    over (the writing session IS the live one); a live foreign claim
    stops the publish — a stolen lease must stop the old holder loudly,
    not let two writers interleave.
    """
    global _uc_claim, _uc_claim_url

    current = read_uc_claim(url)
    held = _uc_claim
    if current is None:
        if held is None:
            return  # A claimless location and a non-claiming caller.
        # Our live lease vanished — a departing predecessor's release can
        # delete a successor's fresh claim (read-to-delete window, no CAS).
        # That is "reassert", not "lost": we are demonstrably alive.
        user = held.user
        _uc_claim = None
        _uc_claim_url = None
        acquire_uc_claim(url, user=user)
        return
    if held is not None and current.nonce == held.nonce and current.writer_id == held.writer_id:
        refreshed = UCClaim(
            app_name=held.app_name,
            writer_id=held.writer_id,
            nonce=held.nonce,
            user=held.user,
            claimed_at=held.claimed_at,
            refreshed_at=_now_iso(),
        )
        try:
            _write_uc_claim(url, refreshed)
            _uc_claim = refreshed
        except StorageUnavailableError:
            # A missed refresh is what the staleness margin absorbs; the
            # publish itself should not fail over it.
            logger.warning("uc_claim_refresh_skipped")
        return
    if current.writer_id == _writer_id() or _claim_is_stale(current):
        # Our own record under a lost local handle, or a dead lease:
        # reassert ownership with a fresh nonce before writing.
        user = held.user if held is not None else None
        _uc_claim = None
        _uc_claim_url = None
        acquire_uc_claim(url, user=user)
        return
    raise _claimed_error(current)


class _ClaimHeartbeat:
    """One daemon thread refreshing the held lease while the process lives.

    Each beat re-reads the claim and refreshes ``refreshed_at`` only if
    the record is still ours. A foreign record stops the beat without
    overwriting: re-stealing a stolen lease from a background thread
    would turn one loud stop into a silent tug-of-war — the publish-time
    verification is where the loss surfaces.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            if not self._stop.is_set():
                return
            # A stop() raced this start(): the old thread will exit on its
            # set event. It must not strand the NEW claim beatless, so hand
            # the new thread its own fresh event rather than reusing one
            # the outgoing thread still watches.
            self._thread.join(timeout=2.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(self._stop,), name="haute-uc-claim", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(_UC_CLAIM_HEARTBEAT_SECONDS):
            if not self._beat():
                return

    def _beat(self) -> bool:
        """One refresh attempt; ``False`` means the beat must stop.

        Factored out of the thread loop so tests can drive a beat
        synchronously instead of waiting out the cadence.
        """
        global _uc_claim
        claim, url = _uc_claim, _uc_claim_url
        if claim is None or url is None:
            return False
        try:
            current = read_uc_claim(url)
        except StorageUnavailableError:
            return True  # Transient API failure; the staleness margin absorbs it.
        if current is None:
            # A departing predecessor's release deleted our live lease (no
            # CAS on the Files API). Reassert it: we are alive, and leaving
            # the location claimless would let a third writer bind with no
            # 409 at all.
            try:
                _write_uc_claim(url, claim)
            except StorageUnavailableError:
                pass
            return True
        if current.nonce != claim.nonce:
            # A FOREIGN record is different: never re-steal from a
            # background thread — the loss surfaces loudly at the next
            # publish's claim verification instead.
            logger.warning("uc_claim_lost", scope=_scope_name())
            return False
        refreshed = UCClaim(
            app_name=claim.app_name,
            writer_id=claim.writer_id,
            nonce=claim.nonce,
            user=claim.user,
            claimed_at=claim.claimed_at,
            refreshed_at=_now_iso(),
        )
        try:
            _write_uc_claim(url, refreshed)
            _uc_claim = refreshed
        except StorageUnavailableError:
            pass  # Retry on the next beat; the staleness margin absorbs it.
        return True


_claim_heartbeat = _ClaimHeartbeat()


# -- fork and lineage -------------------------------------------------------


def read_uc_lineage(url: str) -> UCLineage | None:
    """The location's fork provenance, or ``None`` when it is not a fork.

    Lenient on every failure: lineage is informational, and an unreadable
    record degrades to "not a fork", never to a gated session.
    """
    try:
        response = _files_api().download(_uc_lineage_path(url))
        raw = response.contents.read()
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except Exception as exc:
        if not _is_not_found(exc):
            logger.warning("uc_lineage_read_failed", error=str(exc))
        return None
    return UCLineage.from_payload(payload)


def fork_uc_location(
    source_url: str, target_url: str, project_root: Path, forked_by: str | None = None
) -> UCLineage:
    """Copy *source_url*'s latest published generation to empty *target_url*.

    The honest way past a held location: work on a copy, with provenance
    recorded (``LINEAGE.json``) so the fork is signposted and a future
    synchronise-from-upstream feature has something to walk. Copies only
    PUBLISHED state — the holder's unpublished work is theirs alone —
    and takes no claim: binding to the target later claims it. The
    pointer is written last, as everywhere.
    """
    import io
    import tempfile

    from haute import _git

    source = validate_remote_url(source_url)
    target = validate_remote_url(target_url)
    if not is_uc_url(source) or not is_uc_url(target):
        raise StorageConfigError("Forking is only defined between uc:// storage locations.")
    if source == target:
        raise StorageConfigError("A location cannot be forked onto itself.")

    head = read_uc_head(source)
    if head is None:
        raise StorageConfigError(
            "The location to fork has no published project yet — there is nothing to copy."
        )
    if read_uc_head(target) is not None:
        raise StorageConfigError(
            "The fork target already has a stored project. Choose an empty location."
        )

    target_bundle = _uc_bundle_filename(1)
    with tempfile.TemporaryDirectory(prefix="haute-uc-fork-") as tmp:
        bundle = Path(tmp) / head.bundle_filename()
        try:
            response = _files_api().download(_uc_bundle_path(source, head.bundle_filename()))
            bundle.write_bytes(response.contents.read())
        except Exception as exc:
            logger.warning("uc_fork_download_failed", generation=head.generation, error=str(exc))
            raise StorageUnavailableError(
                f"Generation {head.generation} of the project to fork could not be "
                "downloaded from the storage volume."
            ) from exc
        try:
            _git.bundle_verify(bundle, cwd=project_root)
        except _git.GitError as exc:
            raise StorageUnavailableError(
                "The stored project to fork failed verification — it cannot be copied as-is."
            ) from exc
        try:
            with bundle.open("rb") as handle:
                _files_api().upload(_uc_bundle_path(target, target_bundle), handle, overwrite=True)
        except Exception as exc:
            logger.warning("uc_fork_upload_failed", error=str(exc))
            raise StorageUnavailableError(
                "The forked project bundle could not be uploaded to the storage volume."
            ) from exc

    lineage = UCLineage(
        parent_url=source,
        parent_generation=head.generation,
        parent_tip_sha=head.tip_sha,
        forked_at=_now_iso(),
        forked_by=forked_by,
    )
    pointer = UCHead(
        generation=1,
        tip_sha=head.tip_sha,
        writer_id=_writer_id(),
        written_at=_now_iso(),
        bundle_name=target_bundle,
    )
    # Pointer first, provenance after: a crash between the two loses only
    # the fork label — it can never leave a label describing a project
    # that did not arrive.
    try:
        _files_api().upload(
            _uc_head_path(target), io.BytesIO(pointer.to_json().encode("utf-8")), overwrite=True
        )
        _files_api().upload(
            _uc_lineage_path(target), io.BytesIO(lineage.to_json().encode("utf-8")), overwrite=True
        )
    except Exception as exc:
        logger.warning("uc_fork_pointer_write_failed", error=str(exc))
        raise StorageUnavailableError(
            "The forked location's records could not be written to the storage volume."
        ) from exc
    logger.info("uc_location_forked", parent_generation=head.generation)
    return lineage


def publish_to_uc(url: str, project_root: Path) -> None:
    """Publish the whole repository to *url* as the next bundle generation.

    Order matters: bundle locally (the only step under the repository
    mutation lock) → verify — the upload is the only durable copy, so it
    is proven readable before it is trusted → upload the bundle → write
    the pointer LAST → prune old generations (best-effort). A torn upload
    therefore leaves the previous pointer intact and harmless.
    """
    global _uc_last_seen_generation
    import io
    import tempfile

    from haute import _git

    # Publishing only while holding the lease (or on a claimless location);
    # a live foreign claim stops here, before any bytes move.
    _verify_uc_claim_for_publish(url)
    head = read_uc_head(url)
    # The generation this process restored from is exempt: that pointer was
    # legitimately written by the predecessor container whose lineage this
    # one adopted. Anything newer from another writer means we lost the race.
    if (
        head is not None
        and head.writer_id != _writer_id()
        and head.generation != _uc_last_seen_generation
    ):
        raise StorageSupersededError(
            "Another app container has published newer work to this project's "
            "storage location — publishing stopped so nothing is overwritten. "
            "Restart the app to continue from the latest published project."
        )
    generation = (head.generation if head is not None else 0) + 1
    bundle_filename = _uc_bundle_filename(generation)

    with tempfile.TemporaryDirectory(prefix="haute-uc-publish-") as tmp:
        bundle = Path(tmp) / bundle_filename
        try:
            tip_sha = _git.bundle_create(bundle, cwd=project_root)
            _git.bundle_verify(bundle, cwd=project_root)
        except _git.GitDomainError:
            raise  # Hand-authored and user-facing; surfaces verbatim.
        except _git.GitError as exc:
            raise StorageUnavailableError(
                "The project could not be packaged for the storage volume. "
                "Saves are kept locally and will publish on the next save, or retry now."
            ) from exc
        bundle_bytes = bundle.stat().st_size
        try:
            with bundle.open("rb") as handle:
                _files_api().upload(_uc_bundle_path(url, bundle_filename), handle, overwrite=True)
        except Exception as exc:
            logger.warning("uc_bundle_upload_failed", generation=generation, error=str(exc))
            raise StorageUnavailableError(
                "The project bundle could not be uploaded to the storage volume. "
                "Saves are kept locally and will publish on the next save, or retry now."
            ) from exc

    # The initial fence guarded the packaging; this one guards the pointer.
    # Any movement in between means another writer published mid-flight —
    # overwriting its pointer would silently discard its generation.
    latest = read_uc_head(url)
    moved = (latest is None) != (head is None) or (
        latest is not None
        and head is not None
        and (latest.generation != head.generation or latest.writer_id != head.writer_id)
    )
    if moved:
        raise StorageSupersededError(
            "Another app container has published newer work to this project's "
            "storage location — publishing stopped so nothing is overwritten. "
            "Restart the app to continue from the latest published project."
        )

    pointer = UCHead(
        generation=generation,
        tip_sha=tip_sha,
        writer_id=_writer_id(),
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
        bundle_name=bundle_filename,
    )
    try:
        _files_api().upload(
            _uc_head_path(url), io.BytesIO(pointer.to_json().encode("utf-8")), overwrite=True
        )
    except Exception as exc:
        logger.warning("uc_head_write_failed", generation=generation, error=str(exc))
        raise StorageUnavailableError(
            "The project's storage pointer could not be written to the volume. "
            "Saves are kept locally and will publish on the next save, or retry now."
        ) from exc
    _uc_last_seen_generation = generation
    _write_uc_generation_record(project_root, pointer)
    # Full-bundle publishes are O(history); the size is logged so growth is
    # visible long before incremental chains would be worth their complexity.
    logger.info("uc_project_published", generation=generation, bundle_bytes=bundle_bytes)
    _prune_uc_bundles(url, generation)


def _prune_uc_bundles(url: str, newest: int) -> None:
    """Drop generations older than the newest ``_UC_BUNDLE_RETAIN`` ones.

    Best-effort by design: retention failing must never fail a publish
    that already succeeded — it is logged, and the next publish retries
    it implicitly.
    """
    cutoff = newest - _UC_BUNDLE_RETAIN
    if cutoff < 1:
        return
    directory = f"{_uc_volume_path(url)}/{_UC_BUNDLE_DIR}"
    api = _files_api()
    try:
        entries = list(api.list_directory_contents(directory))
    except Exception as exc:
        logger.warning("uc_bundle_prune_failed", error=str(exc))
        return
    for entry in entries:
        name = getattr(entry, "name", None) or ""
        stem, _, suffix = name.partition(".")
        # Both filename shapes prune by their leading generation number:
        # writer-suffixed `000008-<writer>.bundle` and legacy `000008.bundle`.
        generation_text = stem.split("-", 1)[0]
        if suffix != "bundle" or not generation_text.isdigit() or int(generation_text) > cutoff:
            continue
        try:
            api.delete(f"{directory}/{name}")
        except Exception as exc:
            logger.warning("uc_bundle_prune_failed", name=name, error=str(exc))


def _restore_from_uc(url: str, project_dir: Path) -> None:
    """Materialise the pointed-at generation of *url* into *project_dir*."""
    global _uc_last_seen_generation
    import tempfile

    from haute import _git

    head = read_uc_head(url)
    if head is None:
        raise StorageUnavailableError(
            "This app is bound to a storage location that has no published "
            "project to restore. Rebind the app, or restore the volume's "
            "contents, before starting."
        )
    with tempfile.TemporaryDirectory(prefix="haute-uc-restore-") as tmp:
        bundle = Path(tmp) / head.bundle_filename()
        try:
            response = _files_api().download(_uc_bundle_path(url, head.bundle_filename()))
            bundle.write_bytes(response.contents.read())
        except Exception as exc:
            logger.warning("uc_bundle_download_failed", generation=head.generation, error=str(exc))
            raise StorageUnavailableError(
                f"Generation {head.generation} of the stored project could not be "
                "downloaded from the storage volume."
            ) from exc
        # A corrupt bundle fails the clone loudly; the durable-copy verify
        # already happened at publish time, before the bundle was trusted.
        _git.clone_project(str(bundle), project_dir, branch=None)
    if not _git.commit_exists(head.tip_sha, cwd=project_dir):
        # The pointer describes history its bundle does not contain — the
        # trace a torn multi-writer publish leaves behind. Gate loudly
        # rather than restore the wrong project as if it were the right one.
        raise StorageUnavailableError(
            "The stored project's pointer describes history its bundle does not "
            "contain — the location may have been damaged by an interrupted "
            "publish. Rebind the project, or restore the volume, before starting."
        )
    # A bundle clone leaves origin pointing at the temporary bundle file;
    # repoint it at the uc:// location so the next boot's "is this clone the
    # bound project?" check recognises the directory.
    _git.ensure_remote(REMOTE_NAME, url, cwd=project_dir)
    _uc_last_seen_generation = head.generation
    _write_uc_generation_record(project_dir, head)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

_ASKPASS_SCRIPT = """#!/bin/sh
# Generated by haute for hosted project storage. Reads the token from the
# process environment at call time — the value is never written into this
# file, a git config, or a command line.
case "$1" in
  Username*) printf '%s' "${HAUTE_GIT_USERNAME:-x-access-token}" ;;
  *) printf '%s' "${HAUTE_GIT_TOKEN}" ;;
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

    # -- lifecycle ---------------------------------------------------------

    def start(self, project_root: Path) -> None:
        with self._condition:
            self._project_root = project_root
            self._stopped = False
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

    def enqueue(self) -> None:
        """Record one more unpublished commit and wake the worker."""
        if not self.active:
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


_queue = PushQueue()
# The binding in force for this process, cached once at restore/bind time so
# the readiness endpoint (polled by the UI) never costs a Files API round trip.
_active_binding: StorageBinding | None = None
# The bound location's fork provenance, cached at the same moment and for the
# same reason (readiness must stay off the Files API).
_active_lineage: UCLineage | None = None


def push_queue() -> PushQueue:
    return _queue


def active_binding() -> StorageBinding | None:
    return _active_binding


def active_lineage() -> UCLineage | None:
    return _active_lineage


def enqueue_push() -> None:
    """Publish-after-commit hook. A no-op for unbound or local sessions."""
    _queue.enqueue()


def publish_bound_project(project_root: Path) -> None:
    """Publish current history to the bound location, per its transport.

    The transport is selected from the active binding's URL scheme: a
    ``uc://`` binding publishes a bundle generation, anything else — a git
    binding, or no recorded binding at all — is the pre-existing push to
    ``origin``. The no-binding default keeps a queue started without a
    binding (harnesses, tests) behaving exactly as before.
    """
    binding = _active_binding
    if binding is not None and is_uc_url(binding.remote_url):
        publish_to_uc(binding.remote_url, project_root)
        return
    from haute import _git

    _git.push_working_pair(REMOTE_NAME, project_root, cwd=project_root)


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
    global _active_binding, _active_lineage, _uc_last_seen_generation

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
            # counts. Mere presence of the tip commit is not identity:
            # another writer can legitimately publish a commit this clone
            # also happens to contain. No record, or any mismatch, leaves
            # the fence armed and the first publish stops loudly instead of
            # overwriting the newer generation.
            head = read_uc_head(remote_url)
            record = _read_uc_generation_record(project_dir)
            if (
                head is not None
                and record is not None
                and record.get("generation") == head.generation
                and record.get("tip_sha") == head.tip_sha
                and record.get("writer_id") == head.writer_id
            ):
                _uc_last_seen_generation = head.generation
            _active_lineage = read_uc_lineage(remote_url)
        _active_binding = binding
        _queue.start(project_dir)
        return "present"

    logger.info("project_restore_started", scope=_scope_name())
    if is_uc_url(remote_url):
        acquire_uc_claim(remote_url, user=binding.bound_by)
        _restore_from_uc(remote_url, project_dir)
        _active_lineage = read_uc_lineage(remote_url)
    else:
        _git.clone_project(remote_url, project_dir, branch=None)
    if binding.branch:
        from haute._git_state import write_working_branch

        # A plain clone materialises only the remote's default branch, so the
        # managed lineage has to be recreated locally before the session can
        # show the user's saves or publish again.
        _git.adopt_cloned_lineage(binding.branch, REMOTE_NAME, cwd=project_dir)
        # `.haute/` is per-clone and untracked by design, so the working
        # branch does not travel in the repository — the binding carries it
        # so a restored container resumes on the same lineage.
        write_working_branch(project_dir, binding.branch)
    _active_binding = binding
    _queue.start(project_dir)
    logger.info("project_restored", scope=_scope_name())
    return "restored"


def bind_remote(url: str, project_root: Path, bound_by: str | None = None) -> BindOutcome:
    """Bind this project to *url* and make its history durable.

    An empty remote adopts the current project immediately: the local
    history is published and the session continues uninterrupted. A
    populated remote records the binding and reports that a restart is
    needed — lifting a different project over a running server's working
    directory is not safe to do live, and the boot path already does it
    cleanly.
    """
    global _active_binding, _active_lineage

    from haute import _git
    from haute._git_state import read_working_branch

    if _active_binding is not None:
        # Repointing origin under a live publisher would send this project's
        # history to a remote the session was never verified against.
        raise StorageConfigError(
            "This project is already bound to durable storage. Restart the app to "
            "bind it somewhere else."
        )
    remote_url = validate_remote_url(url)
    if not state_volume_configured():
        raise StorageConfigError(
            f"This deployment has no state volume configured, so a binding cannot be "
            f"remembered across restarts. Set {STATE_VOLUME_ENV} to a Unity Catalog "
            "volume (catalog.schema.volume) the app can write."
        )

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
        bound_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )

    if populated:
        write_binding(binding)
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
        try:
            _files_api().delete(_uc_lineage_path(remote_url))
        except Exception as exc:
            if not _is_not_found(exc):
                logger.warning("uc_lineage_cleanup_failed", error=str(exc))
        publish_to_uc(remote_url, project_root)
    else:
        _git.push_working_pair(REMOTE_NAME, project_root, cwd=project_root)
    write_binding(binding)
    _active_binding = binding
    # An adopted location was empty, so it cannot be a fork.
    _active_lineage = None
    _queue.start(project_root)
    logger.info("project_bound", outcome="adopted")
    return "adopted"


def storage_state() -> StorageState:
    """Coarse state for the readiness surface.

    ``unsupported`` means this deployment cannot remember a binding at
    all (no state volume, i.e. every local session) — the UI hides the
    storage surface rather than offering an action that cannot work.
    """
    if not state_volume_configured():
        return "unsupported"
    return "bound" if _active_binding is not None else "unbound"
