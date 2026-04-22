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
from typing import Any

from fastapi import HTTPException

_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


class JobStore:
    """Thread-safe dict-backed job store with TTL eviction.

    Each route module creates its own instance so job-ID namespaces stay
    independent (a training job ID will never collide with an optimiser
    job ID, just as before the refactor).

    Mutations linearise on ``_write_lock`` so concurrent ``atomic_update``
    calls cannot lose writes when two threads merge disjoint keys.
    """

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._ttl_seconds = ttl_seconds
        self._write_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_stale(self) -> None:
        """Remove jobs older than TTL to bound memory usage."""
        with self._write_lock:
            cutoff = time.time() - self._ttl_seconds
            stale = [
                jid
                for jid, j in self._jobs.items()
                if j.get("created_at", 0) < cutoff and j.get("status") not in ("running",)
            ]
            for jid in stale:
                del self._jobs[jid]

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
            initial_status.setdefault("created_at", time.time())
            self._jobs[job_id] = dict(initial_status)
            return job_id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
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
        expected_status: str | None = None,
    ) -> dict[str, Any]:
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
        a completed job).

        Raises ``KeyError`` if *job_id* does not exist.
        """
        with self._write_lock:
            old = self._jobs[job_id]
            if expected_status is not None and old.get("status") != expected_status:
                return old
            merged = {**old, **fields}
            self._jobs[job_id] = merged
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

    def clear_result_data(
        self,
        job_id: str,
        keys: tuple[str, ...] = ("solver", "solve_result", "quote_grid"),
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
_KNOWN_PREFIXES: frozenset[str] = frozenset({"training", "optimiser"})


@functools.cache
def get_job_store(prefix: str) -> JobStore:
    """Return the shared ``JobStore`` instance for *prefix*.

    The factory is the **single** legitimate entry-point to construct
    a :class:`JobStore`.  Route modules (``modelling.py``,
    ``optimiser.py``, ...) must acquire their store through this
    helper so every caller in the same route ends up with the same
    in-memory dict — previously each module independently called
    ``JobStore()`` and ended up with four disjoint job-ID namespaces
    (see item #126 of the Phase 5 review).

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
