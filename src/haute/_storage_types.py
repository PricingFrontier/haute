"""The shared vocabulary of hosted project storage.

The error family both storage layers raise, and the three JSON records
that live beside a project on a Unity Catalog volume: the ``HEAD.json``
pointer, the ``CLAIM.json`` lease, and the ``LINEAGE.json`` fork
provenance. Pure data and parsing — no IO, no process state. The IO
lives in :mod:`haute._uc_transport`; the policy in
:mod:`haute._project_storage`, which is the canonical import surface
for callers.

Each record hand-rolls ``to_json``/``from_payload`` rather than sharing
a serialiser because their parse strictness DIFFERS BY DESIGN: a
malformed pointer is terminal corruption, a malformed claim reads as a
stale lease, and malformed lineage degrades to "not a fork".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from haute.errors import HauteError


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


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
class UCHead:
    """The ``HEAD.json`` pointer under a ``uc://`` location.

    Written LAST on every publish — the Files API has no atomic rename,
    so the pointer arriving after its bundle is what makes a torn upload
    harmless: readers only ever follow a generation that is complete.
    """

    generation: int
    tip_sha: str
    writer_id: str
    #: The bundle file this pointer describes. Writer-unique names make
    #: bundle bytes immutable by construction: racing writers can never
    #: overwrite each other's upload, only contend on this pointer.
    bundle_name: str
    written_at: str | None = None

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
        bundle_name = payload.get("bundle_name")
        if not isinstance(bundle_name, str) or not bundle_name.strip():
            raise StorageConfigError(
                "The stored storage pointer does not name its bundle — the location "
                "may be corrupted. Rebind the project, or restore the volume's contents."
            )
        written_at = payload.get("written_at")
        return cls(
            generation=generation,
            tip_sha=tip_sha.strip(),
            writer_id=writer_id.strip(),
            bundle_name=bundle_name.strip(),
            written_at=written_at if isinstance(written_at, str) and written_at else None,
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

    def refreshed(self) -> UCClaim:
        """A copy stamped with a fresh heartbeat, same nonce."""
        return UCClaim(
            app_name=self.app_name,
            writer_id=self.writer_id,
            nonce=self.nonce,
            user=self.user,
            claimed_at=self.claimed_at,
            refreshed_at=now_iso(),
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
    signposted rather than silent, and what upstream catch-up walks.
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
