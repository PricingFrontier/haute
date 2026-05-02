"""Shared in-memory job store for background tasks (training, optimisation).

Fine for a single-server dev tool.  Jobs older than ``ttl_seconds`` are
evicted on each ``create_job`` / ``get_job`` call to bound memory usage.

Route modules acquire their ``JobStore`` through the
:func:`get_job_store` factory.  The factory returns exactly one
instance per prefix (``"training"``, ``"optimiser"``, ...), so every
caller in the same route module shares a single namespace.  Direct
``JobStore()`` instantiation outside of this file is forbidden and
is pinned by ``tests/test_routes_hygiene.py::
TestNoDirectJobStoreInstantiation``.
"""

from __future__ import annotations

import functools
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from haute._logging import get_logger

_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_DEFAULT_HEAVY_OBJECT_TTL_SECONDS = 15 * 60  # 15 minutes
_DEFAULT_HEAVY_OBJECT_KEYS = ("solver", "solve_result", "quote_grid")
_HEAVY_OBJECT_KEYS = (*_DEFAULT_HEAVY_OBJECT_KEYS, "factors_df")
_HEAVY_OBJECT_EXPIRES_AT_KEY = "heavy_objects_expires_at"

logger = get_logger(component="server.job_store")

ArtifactCleaner = Callable[[dict[str, Any]], None]
_ARTIFACT_CLEANERS: dict[str, ArtifactCleaner] = {}


def register_artifact_cleaner(kind: str, cleaner: ArtifactCleaner) -> None:
    """Register a typed cleanup hook for server-owned artifact handles."""
    if not kind:
        raise ValueError("artifact cleaner kind must be non-empty")
    existing = _ARTIFACT_CLEANERS.get(kind)
    if existing is not None and existing is not cleaner:
        raise RuntimeError(f"Artifact cleaner already registered for kind {kind!r}")
    _ARTIFACT_CLEANERS[kind] = cleaner


class JobStore:
    """Thread-safe dict-backed job store with TTL eviction.

    Each route module creates its own instance so job-ID namespaces stay
    independent (a training job ID will never collide with an optimiser
    job ID, just as before the refactor).

    Completed jobs keep their lightweight status/result metadata for
    ``ttl_seconds`` but shed known heavy runtime objects after
    ``heavy_object_ttl_seconds``.  Optimiser status polling can therefore
    keep working without retaining solver/dataframe/grid objects for the
    full completed-job TTL.

    Mutations linearise on ``_write_lock`` so concurrent ``atomic_update``
    calls cannot lose writes when two threads merge disjoint keys.
    """

    def __init__(
        self,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        heavy_object_ttl_seconds: int = _DEFAULT_HEAVY_OBJECT_TTL_SECONDS,
        *,
        heavy_object_timer_factory: Callable[
            [float, Callable[[], None]], threading.Timer
        ] = threading.Timer,
    ) -> None:
        if heavy_object_ttl_seconds < 0:
            raise ValueError("heavy_object_ttl_seconds must be >= 0")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._ttl_seconds = ttl_seconds
        self._heavy_object_ttl_seconds = heavy_object_ttl_seconds
        self._heavy_object_timer_factory = heavy_object_timer_factory
        self._heavy_object_timers: dict[str, Any] = {}
        self._write_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_stale(self) -> None:
        """Remove expired jobs and slim expired heavy payloads."""
        with self._write_lock:
            now = time.time()
            self._clear_expired_heavy_objects_locked(now)
            cutoff = now - self._ttl_seconds
            stale = [
                jid
                for jid, j in self._jobs.items()
                if j.get("created_at", 0) < cutoff and j.get("status") not in ("running",)
            ]
            for jid in stale:
                self._cleanup_artifact_handles(jid, self._jobs[jid])
                self._cancel_heavy_object_timer_locked(jid)
                del self._jobs[jid]

    @staticmethod
    def _cleanup_artifact_handles(job_id: str, job: dict[str, Any]) -> None:
        """Remove persisted artifact files when the owning job expires."""
        handles = job.get("artifact_handles")
        if not isinstance(handles, dict):
            return
        for handle in handles.values():
            if not isinstance(handle, dict):
                continue
            kind = handle.get("kind")
            cleaner = _ARTIFACT_CLEANERS.get(kind) if isinstance(kind, str) else None
            if cleaner is None:
                logger.warning(
                    "job_artifact_cleanup_unknown_handle_kind",
                    job_id=job_id,
                    kind=kind,
                )
                continue
            try:
                cleaner(handle)
            except Exception as exc:
                raw_path = handle.get("directory") or handle.get("path") or "<unknown>"
                logger.warning(
                    "job_artifact_cleanup_failed",
                    job_id=job_id,
                    path=str(raw_path),
                    kind=kind,
                    error=str(exc),
                    exc_info=True,
                )

    def _clear_expired_heavy_objects_locked(self, now: float) -> None:
        """Strip completed-job heavy objects once their short retention expires."""
        for job_id, job in list(self._jobs.items()):
            if job.get("status") != "completed":
                continue
            if not any(key in job for key in _HEAVY_OBJECT_KEYS):
                continue
            expires_at = self._heavy_objects_expires_at(job)
            if expires_at > now:
                continue
            self._clear_heavy_objects_locked(job_id, job, now=now)

    def _clear_expired_heavy_objects(
        self,
        job_id: str | None = None,
        timer: Any | None = None,
    ) -> None:
        """Timer entry point: slim heavy completed-job payloads if due."""
        with self._write_lock:
            now = time.time()
            if job_id is not None:
                if self._heavy_object_timers.get(job_id) is timer:
                    self._heavy_object_timers.pop(job_id, None)
                job = self._jobs.get(job_id)
                if job is not None and job.get("status") == "completed":
                    expires_at = self._heavy_objects_expires_at(job)
                    if expires_at <= now and any(key in job for key in _HEAVY_OBJECT_KEYS):
                        self._clear_heavy_objects_locked(job_id, job, now=now)
                return
            self._clear_expired_heavy_objects_locked(now)

    def _cancel_heavy_object_timer_locked(self, job_id: str) -> None:
        timer = self._heavy_object_timers.pop(job_id, None)
        if timer is not None:
            timer.cancel()

    def _clear_heavy_objects_locked(
        self,
        job_id: str,
        job: dict[str, Any],
        *,
        now: float,
    ) -> None:
        cleaned = {k: v for k, v in job.items() if k not in _HEAVY_OBJECT_KEYS}
        cleaned.pop(_HEAVY_OBJECT_EXPIRES_AT_KEY, None)
        cleaned["heavy_objects_cleared_at"] = now
        cleaned["heavy_objects_retention_seconds"] = self._heavy_object_ttl_seconds
        self._jobs[job_id] = cleaned
        self._cancel_heavy_object_timer_locked(job_id)

    def _heavy_objects_expires_at(self, job: dict[str, Any]) -> float:
        """Return the expiry timestamp for completed-job heavy fields."""
        expires_at = job.get(_HEAVY_OBJECT_EXPIRES_AT_KEY)
        if expires_at is not None:
            return float(expires_at)
        completed_at = job.get("completed_at", job.get("created_at", 0.0))
        return float(completed_at) + self._heavy_object_ttl_seconds

    def _prepare_heavy_object_policy_locked(
        self,
        job: dict[str, Any],
        *,
        now: float,
    ) -> bool:
        """Stamp lifecycle metadata and report whether a cleanup timer is needed."""
        if job.get("status") != "completed":
            return False

        job.setdefault("completed_at", now)
        has_heavy_objects = any(key in job for key in _HEAVY_OBJECT_KEYS)
        if not has_heavy_objects:
            job.pop(_HEAVY_OBJECT_EXPIRES_AT_KEY, None)
            return False

        if _HEAVY_OBJECT_EXPIRES_AT_KEY in job:
            return False

        job[_HEAVY_OBJECT_EXPIRES_AT_KEY] = self._heavy_objects_expires_at(job)
        return True

    def _store_merged_job_locked(
        self,
        job_id: str,
        old: dict[str, Any],
        fields: dict[str, Any],
        *,
        now: float,
    ) -> tuple[dict[str, Any], bool, float | None]:
        merged = {**old, **fields}
        if old.get("status") != "completed" and merged.get("status") == "completed":
            merged.setdefault("completed_at", now)
        schedule_cleanup = self._prepare_heavy_object_policy_locked(merged, now=now)
        expires_at = merged.get(_HEAVY_OBJECT_EXPIRES_AT_KEY)
        self._jobs[job_id] = merged
        if merged.get("status") != "completed" or not any(
            key in merged for key in _HEAVY_OBJECT_KEYS
        ):
            self._cancel_heavy_object_timer_locked(job_id)
        return merged, schedule_cleanup, expires_at

    def _schedule_heavy_object_cleanup(self, job_id: str, expires_at: float) -> None:
        """Schedule active heavy-object cleanup; lazy sweeps remain the backstop."""
        delay = max(0.0, expires_at - time.time())
        timer_ref: dict[str, Any] = {}

        def callback() -> None:
            self._clear_expired_heavy_objects(job_id, timer_ref["timer"])

        timer = self._heavy_object_timer_factory(delay, callback)
        timer_ref["timer"] = timer
        timer.daemon = True
        with self._write_lock:
            job = self._jobs.get(job_id)
            if job is None or job.get("status") != "completed":
                return
            if not any(key in job for key in _HEAVY_OBJECT_KEYS):
                self._cancel_heavy_object_timer_locked(job_id)
                return
            if self._heavy_objects_expires_at(job) != expires_at:
                return
            self._cancel_heavy_object_timer_locked(job_id)
            self._heavy_object_timers[job_id] = timer
        timer.start()

    def _schedule_heavy_object_cleanup_if_needed(
        self,
        job_id: str,
        schedule_cleanup: bool,
        expires_at: object | None,
    ) -> None:
        if not schedule_cleanup:
            return
        if not isinstance(expires_at, int | float):
            raise RuntimeError("heavy object cleanup scheduled without an expiry")
        self._schedule_heavy_object_cleanup(job_id, float(expires_at))

    def touch_heavy_objects(
        self,
        job_id: str,
        *,
        required_keys: tuple[str, ...] = _DEFAULT_HEAVY_OBJECT_KEYS,
    ) -> bool:
        """Extend a completed job's heavy-object window after successful access.

        Returns ``False`` when the job exists but any required heavy object is
        missing.  Callers should keep raising their domain-specific error in
        that case; this method never fabricates or restores cleared objects.
        """
        schedule_cleanup = False
        expires_at: float | None = None
        with self._write_lock:
            self._evict_stale()
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.get("status") != "completed":
                return False
            if any(job.get(key) is None for key in required_keys):
                return False

            now = time.time()
            metadata_expires_at = float(job.get("created_at", now)) + self._ttl_seconds
            refreshed_expires_at = min(now + self._heavy_object_ttl_seconds, metadata_expires_at)
            current_expires_at = self._heavy_objects_expires_at(job)
            if refreshed_expires_at <= current_expires_at:
                return True

            updated = dict(job)
            updated[_HEAVY_OBJECT_EXPIRES_AT_KEY] = refreshed_expires_at
            self._jobs[job_id] = updated
            schedule_cleanup = True
            expires_at = refreshed_expires_at
        self._schedule_heavy_object_cleanup_if_needed(job_id, schedule_cleanup, expires_at)
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_job(self, initial_status: dict[str, Any]) -> str:
        """Generate a UUID, store *initial_status* with a timestamp, return the ID.

        Automatically evicts stale jobs before inserting.
        """
        with self._write_lock:
            self._evict_stale()
            job_id = uuid.uuid4().hex[:12]
            now = time.time()
            job = dict(initial_status)
            job.setdefault("created_at", now)
            schedule_cleanup = self._prepare_heavy_object_policy_locked(job, now=now)
            expires_at = job.get(_HEAVY_OBJECT_EXPIRES_AT_KEY)
            self._jobs[job_id] = job
        self._schedule_heavy_object_cleanup_if_needed(job_id, schedule_cleanup, expires_at)
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any] | None:  # pragma: no mutate
        """Return the job dict for *job_id*, or ``None`` if not found.

        Evicts stale jobs first so callers never see expired entries.
        """
        with self._write_lock:
            self._evict_stale()
            return self._jobs.get(job_id)

    def update_job(self, job_id: str, **fields: Any) -> None:
        """Merge *fields* into the stored job dict — atomic swap.

        Delegates to :meth:`atomic_update` so callers never expose a
        partially-updated dict to concurrent readers.  The existing dict
        object is replaced wholesale via a single GIL-atomic
        ``dict.__setitem__``; a reader holding the previous reference
        continues to see the pre-update state.

        Raises ``KeyError`` if *job_id* does not exist.
        """
        self.atomic_update(job_id, fields)

    def atomic_update(
        self,
        job_id: str,
        fields: dict[str, Any],
        *,
        expected_status: str | None = None,  # pragma: no mutate
    ) -> dict[str, Any] | None:
        """Replace the job dict with a merged copy — thread-safe.

        Instead of mutating the existing dict (which can race with
        concurrent readers), this builds a **new** dict and swaps it in
        with a single pointer assignment.  CPython's GIL guarantees that
        ``dict.__setitem__`` is atomic, so a reader will always see
        either the old dict or the new one — never a half-updated state.

        Read-merge-swap is itself serialised on ``_write_lock`` so two
        concurrent writers updating disjoint keys cannot lose each
        other's writes — a hazard that bare ``{**old, **fields}`` still
        has because the read of ``old`` and the write of the new dict
        are not atomic as a unit.

        When *expected_status* is provided, the update is skipped if the
        current status does not match (prevents timeout from overwriting
        a completed job). A skipped guarded update returns ``None`` so
        callers must handle the race explicitly.

        Raises ``KeyError`` if *job_id* does not exist.
        """
        schedule_cleanup = False
        expires_at: float | None = None
        with self._write_lock:
            old = self._jobs[job_id]
            if expected_status is not None and old.get("status") != expected_status:
                return None
            merged, schedule_cleanup, expires_at = self._store_merged_job_locked(
                job_id,
                old,
                fields,
                now=time.time(),
            )
        self._schedule_heavy_object_cleanup_if_needed(job_id, schedule_cleanup, expires_at)
        return merged

    def atomic_update_if_heavy_present(
        self,
        job_id: str,
        fields: dict[str, Any],
        *,
        required_keys: tuple[str, ...],
        expected_status: str | None = None,  # pragma: no mutate
    ) -> dict[str, Any] | None:
        """Atomically update a job only if required heavy keys still exist.

        Returns ``None`` if the job no longer matches the expected status or if
        another path has already cleared required heavy runtime state. Callers
        can then fail loudly without merging partial heavy state back in.

        Raises ``KeyError`` if *job_id* does not exist.
        """
        schedule_cleanup = False
        expires_at: float | None = None
        with self._write_lock:
            old = self._jobs[job_id]
            if expected_status is not None and old.get("status") != expected_status:
                return None
            if any(old.get(key) is None for key in required_keys):
                return None
            merged, schedule_cleanup, expires_at = self._store_merged_job_locked(
                job_id,
                old,
                fields,
                now=time.time(),
            )
        self._schedule_heavy_object_cleanup_if_needed(job_id, schedule_cleanup, expires_at)
        return merged

    def require_job(self, job_id: str) -> dict[str, Any]:
        """Return the job dict for *job_id*, or raise HTTP 404 if not found.

        Convenience wrapper around :meth:`get_job` that eliminates the
        repetitive ``if job is None: raise HTTPException(...)`` guard at
        every call site.
        """
        job = self.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return job

    def delete_job(self, job_id: str) -> None:
        """Remove a job and clean up any owned artifacts."""
        with self._write_lock:
            job = self._jobs.pop(job_id, None)
            if job is None:
                return
            self._cleanup_artifact_handles(job_id, job)
            self._cancel_heavy_object_timer_locked(job_id)

    def require_completed_job(self, job_id: str) -> dict[str, Any]:
        """Return the job dict for *job_id*, raising if missing or not completed.

        Combines :meth:`require_job` (404 if not found) with a status
        check (400 if not ``"completed"``).  Eliminates the repetitive
        two-step guard pattern at call sites that need a finished job.
        """
        job = self.require_job(job_id)
        if job.get("status") != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Job '{job_id}' is not completed (status: {job.get('status')})",
            )
        return job

    def has_job_with_status(self, status: str) -> bool:
        """Return ``True`` if any live job currently has *status*.

        The check runs under the store lock and performs normal TTL
        eviction first, so callers such as concurrency guards never race
        on a raw ``jobs.items()`` iteration and never count already-expired
        entries.
        """
        with self._write_lock:
            self._evict_stale()
            return any(job.get("status") == status for job in self._jobs.values())

    def clear_result_data(
        self,
        job_id: str,
        keys: tuple[str, ...] = _HEAVY_OBJECT_KEYS,
    ) -> None:
        """Remove heavy objects from a completed job to free memory.

        After a solve result has been saved or logged to MLflow, the full
        solver, solve result (entire scored DataFrame), and QuoteGrid are
        no longer needed.  This method strips those keys from the job dict
        while keeping lightweight metadata (status, config, result summary)
        intact, allowing the TTL eviction to work on a much smaller dict.

        No-op if *job_id* does not exist or keys are already absent.
        """
        with self._write_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            cleaned = {k: v for k, v in job.items() if k not in keys}
            if not any(key in cleaned for key in _HEAVY_OBJECT_KEYS):
                cleaned.pop(_HEAVY_OBJECT_EXPIRES_AT_KEY, None)
                self._cancel_heavy_object_timer_locked(job_id)
            self._jobs[job_id] = cleaned

    @property
    def jobs(self) -> dict[str, dict[str, Any]]:
        """Direct access to the underlying dict.

        Provided for callsites that need to iterate (e.g. checking for
        running jobs).  Prefer ``get_job`` / ``update_job`` for single-key
        access.
        """
        return self._jobs


# ---------------------------------------------------------------------------
# Factory: one JobStore singleton per prefix
# ---------------------------------------------------------------------------
#
# The prefix allow-list is deliberately closed.  ``get_job_store`` is
# called from route handlers whose prefix is a compile-time constant
# (``"training"`` for modelling, ``"optimiser"`` for optimiser).  There
# is no code path through which an externally-supplied value reaches
# the factory today, and closing the list prevents one from being
# added accidentally — a user-controlled prefix plus an unbounded
# ``functools.cache`` would be an unbounded memory-growth vector.
#
# Adding a new prefix:
#   1. Add the literal to :data:`_KNOWN_PREFIXES` below.
#   2. Update the caller in its route module.
#   3. Add a test that asserts the new prefix returns a store distinct
#      from the existing ones.
_KNOWN_PREFIXES: frozenset[str] = frozenset({"training", "optimiser"})  # pragma: no mutate


@functools.cache
def get_job_store(prefix: str) -> JobStore:
    """Return the shared ``JobStore`` instance for *prefix*.

    The factory is the **single** legitimate entry-point to construct
    a :class:`JobStore`.  Route modules (``modelling.py``,
    ``optimiser.py``, ...) must acquire their store through this
    helper so every caller in the same route ends up with the same
    in-memory dict.  Constructing stores independently would create
    disjoint job-ID namespaces.

    Raises :class:`ValueError` on any prefix not listed in
    :data:`_KNOWN_PREFIXES`.  This closes the door on an
    externally-supplied value reaching an unbounded
    :func:`functools.cache` — every legitimate prefix is a
    compile-time constant, so there is no flexibility to lose.

    Semantics:

    * **Singleton per known prefix** — two calls with the same *prefix*
      return the same instance.  Backed by :func:`functools.cache` with
      size bounded by the ``_KNOWN_PREFIXES`` allow-list, so the
      mapping from prefix → store is permanent for the process
      lifetime.
    * **Isolated across prefixes** — ``get_job_store("training")``
      and ``get_job_store("optimiser")`` return independent stores,
      so a job ID created in one prefix is never visible in another.
    * **Test reset hook** — the autouse fixture in
      ``tests/test_routes_hygiene.py`` calls
      ``get_job_store.cache_clear()`` between tests so singleton
      state does not leak across test runs.  Production code should
      never call ``cache_clear``; the lifetime is a process-level
      invariant.
    """
    if prefix not in _KNOWN_PREFIXES:
        raise ValueError(
            f"Unknown JobStore prefix {prefix!r}.  Add it to "
            f"haute.routes._job_store._KNOWN_PREFIXES if this is a "
            f"genuine new route; currently registered: "
            f"{sorted(_KNOWN_PREFIXES)}."
        )
    return JobStore()
