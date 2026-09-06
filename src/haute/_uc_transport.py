"""The Unity Catalog volume half of hosted project storage.

Everything that talks to a UC volume lives here: the Files API plumbing,
the ``uc://`` URL form, the bundle transport (publish / restore / fork /
prune), the claim lease that makes a location behave like a locally-owned
file, and the per-process fencing state behind it all.

:mod:`haute._project_storage` is the policy layer above this module and
the canonical import surface for callers — routes, bootstrap, and tests
reach every public name here through it. The dependency points one way:
that module imports this one, never the reverse; the shared error
family and volume records sit beneath both in
:mod:`haute._storage_types`.

Design: ``specs/hosted-project-storage/``. The volume layout, in one
breath: ``bundles/NNNNNN-<writer>.bundle`` (generation-numbered,
writer-unique, each a complete ``git bundle --all``), immutable
``pointers/NNNNNN.json`` records created after each bundle, ``CLAIM.json``
(the lease), and on forks ``LINEAGE.json`` (provenance). Full bundles,
not incremental: O(history) rather than O(diff), but a pricing project
is small, and every generation being independently complete removes the
whole partial-chain failure class.

Every git subprocess belongs to :mod:`haute._git` (the repository's
one-chokepoint-per-tool rule); this module orchestrates and never shells
out itself.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from haute._logging import get_logger
from haute._storage_types import (
    StorageClaimedError,
    StorageConfigError,
    StorageSupersededError,
    StorageUnavailableError,
    UCClaim,
    UCHead,
    UCLineage,
    now_iso,
)

logger = get_logger(component="project_storage")

REMOTE_NAME = "origin"

_UC_SCHEME = "uc://"
_UC_BUNDLE_DIR = "bundles"
_UC_POINTER_DIR = "pointers"
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

#: Clone-side record of the generation this clone last embodied — what the
#: `present` restore path verifies against before blessing the fence.
_UC_GENERATION_RECORD = "uc-generation.json"

#: Tracking namespace a fork's parent refs are fetched into. Deliberately NOT
#: a configured remote — see ``_git.fetch_bundle_refs``.
UPSTREAM_NAMESPACE = "upstream"


@dataclass(slots=True)
class _UCPublishWork:
    """Constant-space phase totals for one complete-bundle publish attempt."""

    generation: int | None = None
    bundle_bytes: int = 0
    lease_fence_ms: float = 0.0
    bundle_create_ms: float = 0.0
    bundle_verify_ms: float = 0.0
    upload_ms: float = 0.0
    pointer_write_ms: float = 0.0
    local_record_ms: float = 0.0
    cleanup_ms: float = 0.0
    total_ms: float = 0.0

    @property
    def network_ms(self) -> float:
        return self.lease_fence_ms + self.upload_ms + self.pointer_write_ms


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


# ---------------------------------------------------------------------------
# Deployment identity and Files API plumbing
# ---------------------------------------------------------------------------


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


def _is_already_exists(exc: Exception) -> bool:
    """Whether *exc* is the Files API's refusal of a create-only upload."""
    try:
        from databricks.sdk.errors.platform import ResourceConflict
    except ImportError:  # pragma: no cover - SDK absent means we never got here
        return False
    return isinstance(exc, ResourceConflict)


def volume_read(path: str, *, event: str, unavailable: str) -> bytes | str | None:
    """Read a volume file, or ``None`` when it does not exist.

    Any failure other than not-found raises :class:`StorageUnavailableError`
    with the caller's hand-authored *unavailable* message — an unreadable
    record must never be mistaken for an absent one. Every raw volume read
    goes through here so that mapping is decided once, not re-decided at
    each call site.
    """
    try:
        response = _files_api().download(path)
        raw: bytes | str = response.contents.read()
        return raw
    except Exception as exc:
        if _is_not_found(exc):
            return None
        logger.warning(event, error=str(exc))
        raise StorageUnavailableError(unavailable) from exc


def volume_write(path: str, payload: bytes | IO[bytes], *, event: str, unavailable: str) -> None:
    """Write a volume file (overwriting), or raise the caller's message."""
    import io

    contents = io.BytesIO(payload) if isinstance(payload, bytes) else payload
    try:
        _files_api().upload(path, contents, overwrite=True)
    except Exception as exc:
        logger.warning(event, error=str(exc))
        raise StorageUnavailableError(unavailable) from exc


def volume_create(path: str, payload: bytes, *, event: str, unavailable: str) -> bool:
    """Create a volume file that must not already exist.

    Returns ``False`` when the API refused because the path already exists —
    the one outcome a caller must distinguish, since it is how a lost
    publication race surfaces. Any other failure raises the caller's
    message; an ambiguous failure (the request may have landed) is a
    retryable :class:`StorageUnavailableError` like every other transport
    fault — the next attempt reads the location and reconciles.
    """
    import io

    try:
        _files_api().upload(path, io.BytesIO(payload), overwrite=False)
    except Exception as exc:
        if _is_already_exists(exc):
            return False
        logger.warning(event, error=str(exc))
        raise StorageUnavailableError(unavailable) from exc
    return True


def volume_download(
    path: str, dest: Path, *, event: str, unavailable: str, **log_fields: Any
) -> None:
    """Download a volume file to *dest*, treating not-found as unavailable.

    Bundles differ from records here: a pointer names its bundle, so a
    missing bundle is damage (or retention outrunning a reader), never a
    benign "nothing published yet".
    """
    try:
        response = _files_api().download(path)
        dest.write_bytes(response.contents.read())
    except Exception as exc:
        logger.warning(event, error=str(exc), **log_fields)
        raise StorageUnavailableError(unavailable) from exc


# ---------------------------------------------------------------------------
# The uc:// URL form
# ---------------------------------------------------------------------------


def is_uc_url(url: str) -> bool:
    """Whether *url* names a Unity Catalog volume location."""
    return url.startswith(_UC_SCHEME)


def validate_uc_url(candidate: str) -> str:
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


def _uc_pointer_filename(generation: int) -> str:
    return f"{generation:06d}.json"


def _uc_pointer_path(url: str, generation: int) -> str:
    return f"{_uc_volume_path(url)}/{_UC_POINTER_DIR}/{_uc_pointer_filename(generation)}"


def _uc_claim_path(url: str) -> str:
    return f"{_uc_volume_path(url)}/{_UC_CLAIM_FILE}"


def _uc_lineage_path(url: str) -> str:
    return f"{_uc_volume_path(url)}/{_UC_LINEAGE_FILE}"


def _uc_bundle_path(url: str, filename: str) -> str:
    return f"{_uc_volume_path(url)}/{_UC_BUNDLE_DIR}/{filename}"


def _list_pointer_generations(url: str) -> list[int]:
    directory = f"{_uc_volume_path(url)}/{_UC_POINTER_DIR}"
    try:
        entries = list(_files_api().list_directory_contents(directory))
    except Exception as exc:
        if _is_not_found(exc):
            return []
        logger.warning("uc_pointer_list_failed", error=str(exc))
        raise StorageUnavailableError(
            "The project's storage pointer could not be read from the volume."
        ) from exc
    generations: list[int] = []
    for entry in entries:
        name = getattr(entry, "name", None) or ""
        if len(name) == 11 and name.endswith(".json") and name[:6].isdigit():
            generations.append(int(name[:6]))
    generations.sort()
    return generations


def read_uc_head(url: str) -> UCHead | None:
    """Return *url*'s pointer, or ``None`` when nothing was ever published.

    Raises :class:`StorageUnavailableError` when the pointer exists but
    cannot be read — like the binding record, an unreadable pointer must
    never be mistaken for an empty location.
    """
    generations = _list_pointer_generations(url)
    if not generations:
        return None
    unavailable = "The project's storage pointer could not be read from the volume."
    raw = volume_read(
        _uc_pointer_path(url, generations[-1]),
        event="uc_head_read_failed",
        unavailable=unavailable,
    )
    if raw is None:
        raise StorageUnavailableError(unavailable)
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageConfigError(
            "The project's storage pointer is unreadable — the location may be "
            "corrupted. Rebind the project, or restore the volume's contents."
        ) from exc
    return UCHead.from_payload(payload)


def read_uc_claim(url: str) -> UCClaim | None:
    """Return the location's lease, or ``None`` when unclaimed.

    A malformed record also reads as ``None`` (a corrupt lease must not
    brick the location it guards — the publish fence still backstops
    writes). An API failure other than not-found raises: an unreadable
    lease store must gate a bind, not read as "unclaimed".
    """
    raw = volume_read(
        _uc_claim_path(url),
        event="uc_claim_read_failed",
        unavailable="The storage location's claim record could not be read from the volume.",
    )
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        return None
    return UCClaim.from_payload(payload)


def read_uc_lineage(url: str) -> UCLineage | None:
    """The location's fork provenance, or ``None`` when it is not a fork.

    Lenient on every failure: lineage is informational, and an unreadable
    record degrades to "not a fork", never to a gated session.
    """
    try:
        raw = volume_read(
            _uc_lineage_path(url),
            event="uc_lineage_read_failed",
            unavailable="unreadable lineage",  # swallowed below
        )
        if raw is None:
            return None
        payload = json.loads(raw)
    except (StorageUnavailableError, UnicodeError, json.JSONDecodeError):
        return None
    return UCLineage.from_payload(payload)


def _write_uc_claim(url: str, claim: UCClaim) -> None:
    volume_write(
        _uc_claim_path(url),
        claim.to_json().encode("utf-8"),
        event="uc_claim_write_failed",
        unavailable="The storage location's claim record could not be written to the volume.",
    )


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


def clone_matches_head(project_dir: Path, head: UCHead) -> bool:
    """Whether this clone's generation record matches the volume's pointer.

    The `present` restore path blesses the supersession fence only on an
    exact match of generation, tip, AND writer — mere presence of the tip
    commit is not identity, since another writer can legitimately publish
    a commit this clone also happens to contain.
    """
    record = _read_uc_generation_record(project_dir)
    return (
        record is not None
        and record.get("generation") == head.generation
        and record.get("tip_sha") == head.tip_sha
        and record.get("writer_id") == head.writer_id
    )


# ---------------------------------------------------------------------------
# Per-process writer state: fencing identity and the held lease
# ---------------------------------------------------------------------------


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
        claim, url = _writer.claim, _writer.claim_url
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
        try:
            refreshed = claim.refreshed()
            _write_uc_claim(url, refreshed)
            _writer.claim = refreshed
        except StorageUnavailableError:
            pass  # Retry on the next beat; the staleness margin absorbs it.
        return True


@dataclass
class _WriterState:
    """Everything this process knows about itself as a volume writer.

    One hosted container serves one project, so a single fencing identity,
    a single last-seen generation, and at most one held lease are the
    invariant, not a limitation. Collected in one object so tests reset
    the whole writer with one assignment instead of five.
    """

    #: Fencing identity, minted lazily once per process.
    writer_id: str | None = None
    #: Newest generation this process has itself written or restored from.
    last_seen_generation: int | None = None
    #: The lease this process holds, and where.
    claim: UCClaim | None = None
    claim_url: str | None = None
    release_registered: bool = False
    heartbeat: _ClaimHeartbeat = field(default_factory=_ClaimHeartbeat)


_writer = _WriterState()


def _writer_id() -> str:
    """This process's fencing identity, minted once per container process."""
    if _writer.writer_id is None:
        import uuid

        _writer.writer_id = f"{_scope_name()}-{uuid.uuid4().hex[:12]}"
    return _writer.writer_id


def last_seen_generation() -> int | None:
    return _writer.last_seen_generation


def bless_generation(generation: int) -> None:
    """Teach the supersession fence which generation this clone embodies."""
    _writer.last_seen_generation = generation


def _uc_bundle_filename(generation: int) -> str:
    """This writer's unique filename for *generation*.

    The writer suffix is what makes bundle bytes immutable: two writers
    racing to the same generation upload to different paths, so the loser
    of the pointer race loses loudly at the fence — never by having its
    bytes overwritten underneath a pointer that still names them.
    """
    return f"{generation:06d}-{_writer_id()}.bundle"


# ---------------------------------------------------------------------------
# The claim lease
# ---------------------------------------------------------------------------


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


def _claim_location(url: str, user: str | None) -> UCClaim:
    """Write our lease and prove it took (there is no compare-and-swap).

    Write a claim with a fresh nonce, read it back, and proceed only if
    the nonce is ours — a lost race raises with whoever won. On success
    the heartbeat starts and a best-effort release is registered for
    clean shutdown.
    """
    import uuid

    now = now_iso()
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

    _writer.claim = ours
    _writer.claim_url = url
    if not _writer.release_registered:
        import atexit

        atexit.register(release_uc_claim)
        _writer.release_registered = True
    _writer.heartbeat.start()
    logger.info("uc_claim_acquired", scope=_scope_name())
    return ours


def hold_claim(url: str, user: str | None = None, *, claim_when_absent: bool = True) -> None:
    """Ensure this process holds *url*'s lease, or raise naming the holder.

    The one decision table for every claim-taking path — bind, restore,
    and publish all route through it, so the rules cannot drift apart:

    * absent → claim it; except on the publish path (*claim_when_absent*
      false) with nothing held, where a claimless location stays claimless
      (pre-claim locations, non-claiming callers). Absent while we HOLD a
      lease is different — a departing predecessor's release can delete a
      successor's fresh claim (read-to-delete window, no CAS), and that is
      "reassert", not "lost": we are demonstrably alive.
    * ours (nonce match) → refresh the heartbeat in place; a failed
      refresh is what the staleness margin absorbs.
    * our writer under a lost local handle, a dead lease, or our own
      app's predecessor (one container per app is a platform guarantee,
      so that claim cannot be a live stranger — but only when a real app
      name exists; local processes arbitrate by expiry like strangers) →
      take over with a fresh nonce.
    * a live foreign holder → :class:`StorageClaimedError`, naming them.
    """
    current = read_uc_claim(url)
    held = _writer.claim

    if current is None:
        if held is None and not claim_when_absent:
            return
        if held is not None:
            user = user if user is not None else held.user
        _writer.claim = None
        _writer.claim_url = None
        _claim_location(url, user)
        return

    if held is not None and current.nonce == held.nonce and current.writer_id == held.writer_id:
        try:
            refreshed = held.refreshed()
            _write_uc_claim(url, refreshed)
            _writer.claim = refreshed
        except StorageUnavailableError:
            logger.warning("uc_claim_refresh_skipped")
        return

    own_writer = current.writer_id == _writer_id()
    own_predecessor = _app_name() is not None and current.app_name == _app_name()
    if own_writer or own_predecessor or _claim_is_stale(current):
        if held is not None:
            user = user if user is not None else held.user
        _writer.claim = None
        _writer.claim_url = None
        _claim_location(url, user)
        return

    raise _claimed_error(current)


def acquire_uc_claim(url: str, user: str | None = None) -> UCClaim:
    """Take the lease on *url* for a bind or restore."""
    hold_claim(url, user, claim_when_absent=True)
    claim = _writer.claim
    if claim is None:  # pragma: no cover - hold_claim either sets it or raises
        raise StorageUnavailableError("The storage location's claim was not established.")
    return claim


def release_uc_claim() -> None:
    """Release the held lease if it is still ours (best-effort).

    Called at clean shutdown. Unclean death is the platform's normal
    case and is what lease expiry exists for, so every failure here is
    logged and swallowed — release must never turn a shutdown into an
    error.
    """
    _writer.heartbeat.stop()
    claim, url = _writer.claim, _writer.claim_url
    _writer.claim = None
    _writer.claim_url = None
    if claim is None or url is None:
        return
    try:
        current = read_uc_claim(url)
        if current is not None and current.nonce == claim.nonce:
            _files_api().delete(_uc_claim_path(url))
            logger.info("uc_claim_released", scope=_scope_name())
    except Exception as exc:
        logger.warning("uc_claim_release_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Publish, restore, fork, prune
# ---------------------------------------------------------------------------


def publish_to_uc(url: str, project_root: Path) -> None:
    """Publish a complete repository bundle and record bounded phase evidence."""
    work = _UCPublishWork()
    started_at = time.perf_counter()
    outcome = "failed"
    try:
        _publish_to_uc(url, project_root, work)
        outcome = "published"
    finally:
        work.total_ms = _elapsed_ms(started_at)
        logger.info(
            "uc_publish_measurement",
            outcome=outcome,
            generation=work.generation,
            bundle_bytes=work.bundle_bytes,
            lease_fence_ms=work.lease_fence_ms,
            bundle_create_ms=work.bundle_create_ms,
            bundle_verify_ms=work.bundle_verify_ms,
            upload_ms=work.upload_ms,
            pointer_write_ms=work.pointer_write_ms,
            local_record_ms=work.local_record_ms,
            cleanup_ms=work.cleanup_ms,
            network_ms=work.network_ms,
            total_ms=work.total_ms,
        )


def _publish_to_uc(url: str, project_root: Path, work: _UCPublishWork) -> None:
    """Publish the whole repository to *url* as the next bundle generation.

    Order matters: hold the lease → bundle locally (the only step under
    the repository mutation lock) → verify — the upload is the only
    durable copy, so it is proven readable before it is trusted → upload
    the bundle → create the generation's pointer, which is the fence → prune old
    generations (best-effort). A torn upload therefore leaves the
    previous pointer intact and harmless.
    """
    import tempfile

    from haute import _git

    # Publishing only while holding the lease (or on a claimless location);
    # a live foreign claim stops here, before any bytes move.
    phase_started_at = time.perf_counter()
    try:
        hold_claim(url, claim_when_absent=False)
        head = read_uc_head(url)
    finally:
        work.lease_fence_ms += _elapsed_ms(phase_started_at)
    # The generation this process restored from is exempt: that pointer was
    # legitimately written by the predecessor container whose lineage this
    # one adopted. Anything newer from another writer means we lost the race.
    if (
        head is not None
        and head.writer_id != _writer_id()
        and head.generation != _writer.last_seen_generation
    ):
        raise StorageSupersededError(
            "Another app container has published newer work to this project's "
            "storage location — publishing stopped so nothing is overwritten. "
            "Restart the app to continue from the latest published project."
        )
    generation = (head.generation if head is not None else 0) + 1
    work.generation = generation
    bundle_filename = _uc_bundle_filename(generation)

    with tempfile.TemporaryDirectory(prefix="haute-uc-publish-") as tmp:
        bundle = Path(tmp) / bundle_filename
        try:
            phase_started_at = time.perf_counter()
            try:
                tip_sha = _git.bundle_create(bundle, cwd=project_root)
            finally:
                work.bundle_create_ms += _elapsed_ms(phase_started_at)
            phase_started_at = time.perf_counter()
            try:
                _git.bundle_verify(bundle, cwd=project_root)
            finally:
                work.bundle_verify_ms += _elapsed_ms(phase_started_at)
        except _git.GitDomainError:
            raise  # Hand-authored and user-facing; surfaces verbatim.
        except _git.GitError as exc:
            raise StorageUnavailableError(
                "The project could not be packaged for the storage volume. "
                "Saves are kept locally and will publish on the next save, or retry now."
            ) from exc
        bundle_bytes = bundle.stat().st_size
        work.bundle_bytes = bundle_bytes
        with bundle.open("rb") as handle:
            phase_started_at = time.perf_counter()
            try:
                volume_write(
                    _uc_bundle_path(url, bundle_filename),
                    handle,
                    event="uc_bundle_upload_failed",
                    unavailable=(
                        "The project bundle could not be uploaded to the storage volume. "
                        "Saves are kept locally and will publish on the next save, or retry now."
                    ),
                )
            finally:
                work.upload_ms += _elapsed_ms(phase_started_at)

    pointer = UCHead(
        generation=generation,
        tip_sha=tip_sha,
        writer_id=_writer_id(),
        written_at=now_iso(),
        bundle_name=bundle_filename,
    )
    phase_started_at = time.perf_counter()
    try:
        committed = volume_create(
            _uc_pointer_path(url, generation),
            pointer.to_json().encode("utf-8"),
            event="uc_pointer_write_failed",
            unavailable=(
                "The project's storage pointer could not be written to the volume. "
                "Saves are kept locally and will publish on the next save, or retry now."
            ),
        )
    finally:
        work.pointer_write_ms += _elapsed_ms(phase_started_at)
    if not committed:
        # Another writer committed this generation between our read and our
        # create. The create-only write is the fence: nothing of theirs was
        # touched, and our bundle is an orphan the next prune would remove
        # anyway — drop it now, best-effort, so the loser leaves no litter.
        _discard_orphaned_bundle(url, bundle_filename)
        raise StorageSupersededError(
            "Another app container has published newer work to this project's "
            "storage location — publishing stopped so nothing is overwritten. "
            "Restart the app to continue from the latest published project."
        )

    _writer.last_seen_generation = generation
    phase_started_at = time.perf_counter()
    try:
        _write_uc_generation_record(project_root, pointer)
    finally:
        work.local_record_ms += _elapsed_ms(phase_started_at)
    # Full-bundle publishes are O(history); the size is logged so growth is
    # visible long before incremental chains would be worth their complexity.
    logger.info("uc_project_published", generation=generation, bundle_bytes=bundle_bytes)
    phase_started_at = time.perf_counter()
    try:
        _prune_uc_bundles(url, generation)
    finally:
        work.cleanup_ms += _elapsed_ms(phase_started_at)


def _discard_orphaned_bundle(url: str, filename: str) -> None:
    try:
        _files_api().delete(_uc_bundle_path(url, filename))
    except Exception as exc:
        logger.warning("uc_orphan_bundle_delete_failed", error=str(exc))


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
        # `000008-<writer>.bundle` — prune by the leading generation number,
        # whichever writer produced it.
        generation_text = stem.split("-", 1)[0]
        if suffix != "bundle" or not generation_text.isdigit() or int(generation_text) > cutoff:
            continue
        try:
            api.delete(f"{directory}/{name}")
        except Exception as exc:
            logger.warning("uc_bundle_prune_failed", name=name, error=str(exc))

    pointer_dir = f"{_uc_volume_path(url)}/{_UC_POINTER_DIR}"
    try:
        pointer_entries = list(api.list_directory_contents(pointer_dir))
    except Exception as exc:
        logger.warning("uc_bundle_prune_failed", error=str(exc))
        return
    for entry in pointer_entries:
        name = getattr(entry, "name", None) or ""
        if len(name) == 11 and name.endswith(".json") and name[:6].isdigit():
            generation = int(name[:6])
            if generation <= cutoff and generation != newest:
                try:
                    api.delete(f"{pointer_dir}/{name}")
                except Exception as exc:
                    logger.warning("uc_bundle_prune_failed", name=name, error=str(exc))


def download_bundle(url: str, head: UCHead, dest_dir: Path, *, what: str) -> Path:
    """Download and verify *head*'s bundle into *dest_dir*, returning its path.

    *what* names the project in the failure message ("the stored project",
    "the parent project at uc://…") — the same damage reads differently
    at restore time and at comparison time.
    """
    bundle = dest_dir / head.bundle_name
    volume_download(
        _uc_bundle_path(url, head.bundle_name),
        bundle,
        event="uc_bundle_download_failed",
        unavailable=(
            f"Generation {head.generation} of {what} could not be downloaded "
            "from the storage volume."
        ),
        generation=head.generation,
    )
    return bundle


def restore_from_uc(url: str, project_dir: Path) -> UCHead:
    """Materialise the pointed-at generation of *url* into *project_dir*."""
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
        bundle = download_bundle(url, head, Path(tmp), what="the stored project")
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
    _writer.last_seen_generation = head.generation
    _write_uc_generation_record(project_dir, head)
    return head


def fork_uc_location(
    source_url: str, target_url: str, project_root: Path, forked_by: str | None = None
) -> UCLineage:
    """Copy *source_url*'s latest published generation to empty *target_url*.

    The honest way past a held location: work on a copy, with provenance
    recorded (``LINEAGE.json``) so the fork is signposted and upstream
    catch-up has something to walk. Copies only PUBLISHED state — the
    holder's unpublished work is theirs alone — and takes no claim:
    binding to the target later claims it. The pointer is written before
    the lineage label, so a crash between the two loses only the label
    and can never leave a label describing a project that did not arrive.
    """
    import tempfile

    from haute import _git

    source = source_url.strip()
    target = target_url.strip()
    if not is_uc_url(source) or not is_uc_url(target):
        raise StorageConfigError("Forking is only defined between uc:// storage locations.")
    source = validate_uc_url(source)
    target = validate_uc_url(target)
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
        bundle = download_bundle(source, head, Path(tmp), what="the project to fork")
        try:
            _git.bundle_verify(bundle, cwd=project_root)
        except _git.GitError as exc:
            raise StorageUnavailableError(
                "The stored project to fork failed verification — it cannot be copied as-is."
            ) from exc
        with bundle.open("rb") as handle:
            volume_write(
                _uc_bundle_path(target, target_bundle),
                handle,
                event="uc_fork_upload_failed",
                unavailable=(
                    "The forked project bundle could not be uploaded to the storage volume."
                ),
            )

    lineage = UCLineage(
        parent_url=source,
        parent_generation=head.generation,
        parent_tip_sha=head.tip_sha,
        forked_at=now_iso(),
        forked_by=forked_by,
    )
    pointer = UCHead(
        generation=1,
        tip_sha=head.tip_sha,
        writer_id=_writer_id(),
        written_at=now_iso(),
        bundle_name=target_bundle,
    )
    records_unavailable = (
        "The forked location's records could not be written to the storage volume."
    )
    if not volume_create(
        _uc_pointer_path(target, 1),
        pointer.to_json().encode("utf-8"),
        event="uc_fork_pointer_write_failed",
        unavailable=records_unavailable,
    ):
        raise StorageConfigError(
            "The fork target already has a stored project. Choose an empty location."
        )
    volume_write(
        _uc_lineage_path(target),
        lineage.to_json().encode("utf-8"),
        event="uc_fork_pointer_write_failed",
        unavailable=records_unavailable,
    )
    logger.info("uc_location_forked", parent_generation=head.generation)
    return lineage


def clear_uc_lineage(url: str) -> None:
    """Delete a stale ``LINEAGE.json`` (best-effort).

    An aborted fork can leave a lineage label with no pointer; adopting
    the (empty) location must not let that label attach itself to an
    unrelated project.
    """
    try:
        _files_api().delete(_uc_lineage_path(url))
    except Exception as exc:
        if not _is_not_found(exc):
            logger.warning("uc_lineage_cleanup_failed", error=str(exc))


def fetch_upstream_refs(parent_url: str, head: UCHead, project_root: Path) -> None:
    """Fetch the parent's published refs into ``refs/remotes/upstream/*``.

    Downloads and verifies the parent's pointed-at bundle, then fetches
    from it — which deliberately configures NO git remote, so ``origin``
    stays the one divergence baseline. The tracking refs outlive the
    temporary bundle, which is all a comparison (and a later catch-up)
    reads.
    """
    import tempfile

    from haute import _git

    with tempfile.TemporaryDirectory(prefix="haute-uc-upstream-") as tmp:
        bundle = download_bundle(
            parent_url, head, Path(tmp), what=f"the parent project at {parent_url}"
        )
        try:
            _git.bundle_verify(bundle, cwd=project_root)
        except _git.GitError as exc:
            raise StorageUnavailableError(
                f"The parent project at {parent_url} failed verification — "
                "its stored copy cannot be compared against."
            ) from exc
        _git.fetch_bundle_refs(bundle, UPSTREAM_NAMESPACE, cwd=project_root)
