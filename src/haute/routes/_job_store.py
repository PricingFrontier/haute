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
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, Literal, NotRequired, TypedDict, cast

from fastapi import HTTPException

from haute._logging import get_logger
from haute.schemas import JobStatus

_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_DEFAULT_HEAVY_OBJECT_TTL_SECONDS = 15 * 60  # 15 minutes
_DEFAULT_HEAVY_OBJECT_KEYS = ("solver", "solve_result", "quote_grid")
_HEAVY_OBJECT_KEYS = (*_DEFAULT_HEAVY_OBJECT_KEYS, "factors_df", "ratebook_factor_contexts")
_HEAVY_OBJECT_EXPIRES_AT_KEY = "heavy_objects_expires_at"

logger = get_logger(component="server.job_store")

ArtifactCleaner = Callable[[dict[str, Any]], None]
_ArtifactCleanup = tuple[str, tuple[dict[str, Any], ...]]
_ARTIFACT_CLEANERS: dict[str, ArtifactCleaner] = {}

RUNNING_STATUS: Literal["running"] = "running"
TerminalReason = Literal[
    "completed", "superseded", "timed_out", "cancelled", "memory_limited", "contract_error", "error"
]
LifecycleExpectedStatus = Literal["running", "completed"]
TERMINAL_REASONS: frozenset[TerminalReason] = frozenset(
    {
        "completed",
        "superseded",
        "timed_out",
        "cancelled",
        "memory_limited",
        "contract_error",
        "error",
    }
)
JOB_STATUSES: frozenset[JobStatus] = frozenset({RUNNING_STATUS, *TERMINAL_REASONS})
_TERMINAL_REASON_PRECEDENCE: Mapping[TerminalReason, int] = {
    "error": 10,
    "contract_error": 20,
    "memory_limited": 30,
    "cancelled": 40,
    "timed_out": 50,
    "superseded": 60,
}
_LIFECYCLE_KEYS = frozenset({"status", "terminal_reason", "created_at", "ended_at", "completed_at"})


class JobCommonFields(TypedDict):
    status: JobStatus
    created_at: float
    message: NotRequired[str]
    terminal_reason: NotRequired[TerminalReason]
    ended_at: NotRequired[float]
    completed_at: NotRequired[float]


class RunningJobFields(TypedDict):
    status: Literal["running"]
    created_at: NotRequired[float]
    message: NotRequired[str]


def _validate_timestamp(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite, non-negative numeric value")
    return float(value)


def _validate_common_record(job: Mapping[str, Any]) -> None:
    """Validate the lifecycle-owned portion of a stored record."""
    status = job.get("status")
    if not isinstance(status, str) or status not in JOB_STATUSES:
        raise ValueError(f"Job record has invalid status: {status!r}")
    if "created_at" not in job:
        raise ValueError("Job record is missing created_at")
    _validate_timestamp("created_at", job["created_at"])
    if "message" in job and not isinstance(job["message"], str):
        raise ValueError("Job record message must be a string")

    if status == RUNNING_STATUS:
        forbidden = {"terminal_reason", "ended_at", "completed_at"}.intersection(job)
        if forbidden:
            raise ValueError(f"Running job may not contain terminal metadata: {sorted(forbidden)}")
        return

    if job.get("terminal_reason") != status:
        raise ValueError("Terminal job reason must match its status")
    if "ended_at" not in job:
        raise ValueError("Terminal job is missing ended_at")
    _validate_timestamp("ended_at", job["ended_at"])
    if status == "completed" and "completed_at" not in job:
        raise ValueError("Completed job is missing completed_at")
    if "completed_at" in job:
        _validate_timestamp("completed_at", job["completed_at"])


_DETACHED_CONTAINER_TYPES = (dict, list, set, tuple)


def _detach_builtin(value: Any) -> Any:
    """Copy a built-in container graph while preserving opaque object identity.

    Seeding ``deepcopy``'s memo with every non-container object gives us its mature
    cycle handling without accidentally copying solver, dataframe, timer, or other
    runtime-owned payloads. Container subclasses are opaque as well: their copy
    semantics belong to their defining type rather than to this storage boundary.
    """
    memo: dict[int, Any] = {}
    visited: set[int] = set()

    def preserve_opaque(item: Any) -> None:
        item_id = id(item)
        if item_id in visited:
            return
        visited.add(item_id)
        if type(item) not in _DETACHED_CONTAINER_TYPES:
            memo[item_id] = item
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                preserve_opaque(key)
                preserve_opaque(nested)
            return
        for nested in item:
            preserve_opaque(nested)

    preserve_opaque(value)
    return deepcopy(value, memo)


@dataclass(frozen=True, slots=True, eq=False)
class JobSnapshot(Mapping[str, Any]):
    """Immutable, detached view of one stored job record."""

    _record: Mapping[str, Any]

    def __init__(self, record: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_record", MappingProxyType(_detach_builtin(dict(record))))

    def __getitem__(self, key: str) -> Any:
        return self._record[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._record)

    def __len__(self) -> int:
        return len(self._record)


class _ArtifactCleanupState(threading.local):
    """Per-thread cleanup collector shared by nested store operations."""

    def __init__(self) -> None:
        self.depth = 0
        self.cleanups: list[_ArtifactCleanup] = []


_ARTIFACT_CLEANUP_STATE = _ArtifactCleanupState()


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
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")
        if heavy_object_ttl_seconds < 0:
            raise ValueError("heavy_object_ttl_seconds must be >= 0")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._running_activity_at: dict[str, float] = {}
        self._ttl_seconds = ttl_seconds
        self._heavy_object_ttl_seconds = heavy_object_ttl_seconds
        self._heavy_object_timer_factory = heavy_object_timer_factory
        self._heavy_object_timers: dict[str, Any] = {}
        self._write_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _write_locked_with_artifact_cleanup(self) -> Iterator[list[_ArtifactCleanup]]:
        """Drain shared cleanup work only after the outermost store lock releases."""
        state = _ARTIFACT_CLEANUP_STATE
        if state.depth == 0:
            state.cleanups = []
        state.depth += 1
        try:
            with self._write_lock:
                yield state.cleanups
        finally:
            state.depth -= 1
            if state.depth == 0:
                cleanups = state.cleanups
                state.cleanups = []
                self._run_artifact_cleanups(cleanups)

    def _evict_stale_locked(
        self,
        now: float,
        cleanups: list[_ArtifactCleanup],
    ) -> None:
        """Detach expired jobs and append their artifact cleanup work."""
        for job in self._jobs.values():
            _validate_common_record(job)
        self._clear_expired_heavy_objects_locked(now)
        cutoff = now - self._ttl_seconds
        stale = [
            jid
            for jid, job in self._jobs.items()
            if self._job_eviction_timestamp_locked(jid, job) < cutoff
        ]
        for job_id in stale:
            self._remove_job_locked(job_id, cleanups)

    def _job_eviction_timestamp_locked(self, job_id: str, job: dict[str, Any]) -> float:
        """Return the timestamp used for metadata TTL eviction."""
        if job.get("status") == "running":
            return self._running_activity_at.get(job_id, float(job["created_at"]))
        self._running_activity_at.pop(job_id, None)
        return float(job["created_at"])

    def _remove_job_locked(
        self,
        job_id: str,
        cleanups: list[_ArtifactCleanup],
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        handles = tuple(dict(handle) for handle in job.get("artifact_handles", {}).values())
        self._jobs.pop(job_id)
        cleanups.append((job_id, handles))
        self._running_activity_at.pop(job_id, None)
        self._cancel_heavy_object_timer_locked(job_id)

    @staticmethod
    def _cleanup_artifact_handles(
        job_id: str,
        handles: tuple[dict[str, Any], ...],
    ) -> None:
        """Remove persisted artifact files when the owning job expires."""
        for handle in handles:
            kind = handle["kind"]
            cleaner = _ARTIFACT_CLEANERS.get(kind)
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

    @classmethod
    def _run_artifact_cleanups(cls, cleanups: list[_ArtifactCleanup]) -> None:
        """Run detached filesystem cleanup without blocking store operations."""
        for job_id, handles in cleanups:
            cls._cleanup_artifact_handles(job_id, handles)

    def _clear_expired_heavy_objects_locked(self, now: float) -> None:
        """Strip completed-job heavy objects once their short retention expires."""
        for job_id, job in list(self._jobs.items()):
            _validate_common_record(job)
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
        job_id: str | None = None,  # pragma: no mutate
        timer: Any | None = None,  # pragma: no mutate
    ) -> None:
        """Timer entry point: slim heavy completed-job payloads if due."""
        with self._write_lock:
            now = time.time()
            if job_id is not None:
                job = self._jobs.get(job_id)
                if job is not None:
                    _validate_common_record(job)
                if self._heavy_object_timers.get(job_id) is timer:
                    self._heavy_object_timers.pop(job_id, None)
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
        *,  # pragma: no mutate
        now: float,
    ) -> None:
        _validate_common_record(job)
        cleaned = {k: v for k, v in job.items() if k not in _HEAVY_OBJECT_KEYS}
        cleaned.pop(_HEAVY_OBJECT_EXPIRES_AT_KEY, None)
        cleaned["heavy_objects_cleared_at"] = now
        cleaned["heavy_objects_retention_seconds"] = self._heavy_object_ttl_seconds
        _validate_common_record(cleaned)
        self._jobs[job_id] = cleaned
        self._cancel_heavy_object_timer_locked(job_id)

    def _heavy_objects_expires_at(self, job: dict[str, Any]) -> float:
        """Return the expiry timestamp for completed-job heavy fields."""
        expires_at = job.get(_HEAVY_OBJECT_EXPIRES_AT_KEY)
        if expires_at is not None:
            return float(expires_at)
        return float(job["completed_at"]) + self._heavy_object_ttl_seconds

    def _prepare_heavy_object_policy_locked(
        self,
        job: dict[str, Any],
        *,  # pragma: no mutate
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

    def _record_running_activity_locked(
        self,
        job_id: str,
        job: dict[str, Any],
        *,
        now: float,
    ) -> None:
        if job.get("status") == "running":
            self._running_activity_at[job_id] = now
            return
        self._running_activity_at.pop(job_id, None)

    def _store_merged_job_locked(
        self,
        job_id: str,
        old: dict[str, Any],
        fields: dict[str, Any],
        *,  # pragma: no mutate
        now: float,
    ) -> tuple[dict[str, Any], bool, float | None]:  # pragma: no mutate
        owned_fields = cast(dict[str, Any], _detach_builtin(fields))
        merged = {**old, **owned_fields}
        schedule_cleanup = self._prepare_heavy_object_policy_locked(merged, now=now)
        _validate_common_record(merged)
        expires_at = merged.get(_HEAVY_OBJECT_EXPIRES_AT_KEY)
        self._jobs[job_id] = merged
        self._record_running_activity_locked(job_id, merged, now=now)
        if merged.get("status") != "completed" or not any(
            key in merged for key in _HEAVY_OBJECT_KEYS
        ):
            self._cancel_heavy_object_timer_locked(job_id)
        return merged, schedule_cleanup, expires_at

    @staticmethod
    def _validate_expected_status(expected_status: str | None) -> None:
        if expected_status is not None and expected_status not in JOB_STATUSES:
            raise ValueError(f"Unknown expected job status: {expected_status!r}")

    @staticmethod
    def _validate_payload_fields(fields: Mapping[str, Any]) -> None:
        forbidden = _LIFECYCLE_KEYS.intersection(fields)
        if forbidden:
            raise ValueError(
                f"Generic job updates may not set lifecycle fields: {sorted(forbidden)}"
            )

    @staticmethod
    def _snapshot(job: dict[str, Any]) -> JobSnapshot:
        _validate_common_record(job)
        return JobSnapshot(job)

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
            if job is None:
                return
            _validate_common_record(job)
            if job.get("status") != "completed":
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
        expires_at: object | None,  # pragma: no mutate
    ) -> None:
        if not schedule_cleanup:
            return
        if not isinstance(expires_at, int | float):
            raise RuntimeError("heavy object cleanup scheduled without an expiry")
        self._schedule_heavy_object_cleanup(job_id, float(expires_at))

    def touch_heavy_objects(
        self,
        job_id: str,
        *,  # pragma: no mutate
        required_keys: tuple[str, ...] = _DEFAULT_HEAVY_OBJECT_KEYS,
    ) -> bool:
        """Extend a completed job's heavy-object window after successful access.

        Returns ``False`` when the job exists but any required heavy object is
        missing.  Callers should keep raising their domain-specific error in
        that case; this method never fabricates or restores cleared objects.
        """
        schedule_cleanup = False  # pragma: no mutate
        expires_at: float | None = None
        result = False
        with self._write_locked_with_artifact_cleanup() as artifact_cleanups:
            self._evict_stale_locked(time.time(), artifact_cleanups)
            job = self._jobs.get(job_id)
            if (
                job is not None
                and job.get("status") == "completed"
                and not any(job.get(key) is None for key in required_keys)
            ):
                result = True
                now = time.time()
                metadata_expires_at = float(job["created_at"]) + self._ttl_seconds
                refreshed_expires_at = min(
                    now + self._heavy_object_ttl_seconds,
                    metadata_expires_at,
                )
                current_expires_at = self._heavy_objects_expires_at(job)
                if refreshed_expires_at > current_expires_at:
                    updated = dict(job)
                    updated[_HEAVY_OBJECT_EXPIRES_AT_KEY] = refreshed_expires_at
                    _validate_common_record(updated)
                    self._jobs[job_id] = updated
                    schedule_cleanup = True
                    expires_at = refreshed_expires_at
        self._schedule_heavy_object_cleanup_if_needed(job_id, schedule_cleanup, expires_at)
        return result

    def transition_terminal(
        self,
        job_id: str,
        *,
        to: TerminalReason,
        message: str | None = None,
        fields: Mapping[str, Any] | None = None,
        expected_status: LifecycleExpectedStatus = RUNNING_STATUS,
        elapsed_seconds: float | None = None,
        now: float | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> JobSnapshot | None:
        """Apply the only public terminal-state transition protocol."""
        if to not in TERMINAL_REASONS:
            raise ValueError(f"Unsupported terminal reason: {to!r}")
        if expected_status not in {RUNNING_STATUS, "completed"}:
            raise ValueError("Lifecycle transitions may expect only 'running' or 'completed'")
        if expected_status == "completed" and to != "error":
            raise ValueError("A completed lifecycle record may only be corrected to 'error'")
        payload = dict(fields or {})
        self._validate_payload_fields(payload)
        timestamp = _validate_timestamp("now", time.time() if now is None else now)
        payload.update(status=to, terminal_reason=to, ended_at=timestamp)
        if to == "completed":
            payload.setdefault("completed_at", timestamp)
        if message is not None:
            payload["message"] = message
        if elapsed_seconds is not None:
            payload["elapsed_seconds"] = elapsed_seconds
        if fault_injector is not None:
            fault_injector("terminal_transition_before_write")
        schedule_cleanup = False
        expires_at: float | None = None
        with self._write_lock:
            old = self._jobs[job_id]
            _validate_common_record(old)
            if old.get("status") == expected_status:
                merged, schedule_cleanup, expires_at = self._store_merged_job_locked(
                    job_id, old, payload, now=timestamp
                )
            else:
                old_reason = old.get("terminal_reason")
                if not isinstance(old_reason, str) or old_reason not in TERMINAL_REASONS:
                    return None
                typed_old_reason = cast(TerminalReason, old_reason)
                if typed_old_reason == "completed" or to == "completed":
                    return None
                if _TERMINAL_REASON_PRECEDENCE[to] <= _TERMINAL_REASON_PRECEDENCE[typed_old_reason]:
                    return None
                merged, schedule_cleanup, expires_at = self._store_merged_job_locked(
                    job_id, old, payload, now=timestamp
                )
        if fault_injector is not None:
            fault_injector("terminal_transition_before_cleanup_schedule")
        self._schedule_heavy_object_cleanup_if_needed(job_id, schedule_cleanup, expires_at)
        return self._snapshot(merged)

    def compare_and_publish_completion(
        self,
        job_id: str,
        *,
        publish: Callable[[], Mapping[str, Any]],
        message: str | None = None,
        elapsed_seconds: float | None = None,
        now: float | None = None,
    ) -> JobSnapshot | None:
        """Claim a running job, publish its result, and complete in one swap."""
        if now is not None:
            _validate_timestamp("now", now)
        schedule_cleanup = False
        expires_at: float | None = None
        with self._write_lock:
            old = self._jobs[job_id]
            _validate_common_record(old)
            if old.get("status") != RUNNING_STATUS:
                return None
            fields = publish()
            if self._jobs.get(job_id) is not old:
                raise RuntimeError("completion publisher must not mutate its job record")
            if not isinstance(fields, Mapping):
                raise ValueError("completion publisher must return a mapping")
            payload = dict(fields)
            self._validate_payload_fields(payload)
            timestamp = _validate_timestamp("now", time.time() if now is None else now)
            payload.update(
                status="completed",
                terminal_reason="completed",
                ended_at=timestamp,
                completed_at=timestamp,
            )
            if message is not None:
                payload["message"] = message
            if elapsed_seconds is not None:
                payload["elapsed_seconds"] = elapsed_seconds
            merged, schedule_cleanup, expires_at = self._store_merged_job_locked(
                job_id, old, payload, now=timestamp
            )
        self._schedule_heavy_object_cleanup_if_needed(job_id, schedule_cleanup, expires_at)
        return self._snapshot(merged)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_job(self, initial_status: RunningJobFields) -> str:
        """Generate a UUID, store *initial_status* with a timestamp, return the ID.

        Automatically evicts stale jobs before inserting.
        """
        job = cast(dict[str, Any], _detach_builtin(dict(initial_status)))
        if job.get("status") != RUNNING_STATUS:
            raise ValueError("New jobs must have status 'running'")
        terminal_fields = {
            key for key in ("terminal_reason", "ended_at", "completed_at") if key in job
        }
        if terminal_fields:
            raise ValueError(
                f"New jobs may not include terminal metadata: {sorted(terminal_fields)}"
            )
        created_at = job.get("created_at")
        if created_at is not None:
            _validate_timestamp("created_at", created_at)
        with self._write_locked_with_artifact_cleanup() as artifact_cleanups:
            self._evict_stale_locked(time.time(), artifact_cleanups)
            job_id = uuid.uuid4().hex[:12]
            now = time.time()
            job.setdefault("created_at", now)
            _validate_common_record(job)
            self._jobs[job_id] = job
            self._record_running_activity_locked(job_id, job, now=now)
        return job_id

    def get_job(self, job_id: str) -> JobSnapshot | None:  # pragma: no mutate
        """Return a detached job snapshot, or ``None`` if not found.

        Evicts stale jobs first so callers never see expired entries.
        """
        with self._write_locked_with_artifact_cleanup() as artifact_cleanups:
            self._evict_stale_locked(time.time(), artifact_cleanups)
            job = self._jobs.get(job_id)
        return None if job is None else self._snapshot(job)

    def list_jobs(self) -> Mapping[str, JobSnapshot]:
        """Return an immutable detached snapshot mapping after normal TTL eviction."""
        with self._write_locked_with_artifact_cleanup() as artifact_cleanups:
            self._evict_stale_locked(time.time(), artifact_cleanups)
            snapshots = {job_id: self._snapshot(job) for job_id, job in self._jobs.items()}
        return MappingProxyType(snapshots)

    def update_job(self, job_id: str, **fields: Any) -> JobSnapshot:
        """Merge *fields* into the stored job dict — atomic swap.

        Delegates to :meth:`atomic_update` so callers never expose a
        partially-updated dict to concurrent readers.  The existing dict
        object is replaced wholesale via a single GIL-atomic
        ``dict.__setitem__``; a reader holding the previous reference
        continues to see the pre-update state.

        Raises ``KeyError`` if *job_id* does not exist.
        """
        result = self.atomic_update(job_id, fields)
        if result is None:  # No guard was supplied, so this is unreachable by contract.
            raise RuntimeError("unguarded job update was unexpectedly skipped")
        return result

    def atomic_update(
        self,
        job_id: str,
        fields: dict[str, Any],
        *,
        expected_status: str | None = None,  # pragma: no mutate
    ) -> JobSnapshot | None:  # pragma: no mutate
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
        self._validate_payload_fields(fields)
        self._validate_expected_status(expected_status)
        schedule_cleanup = False  # pragma: no mutate
        expires_at: float | None = None
        with self._write_lock:
            old = self._jobs[job_id]
            _validate_common_record(old)
            if expected_status is not None and old.get("status") != expected_status:
                return None
            merged, schedule_cleanup, expires_at = self._store_merged_job_locked(
                job_id,
                old,
                fields,
                now=time.time(),
            )
        self._schedule_heavy_object_cleanup_if_needed(job_id, schedule_cleanup, expires_at)
        return self._snapshot(merged)

    def atomic_update_if_heavy_present(
        self,
        job_id: str,
        fields: dict[str, Any],
        *,  # pragma: no mutate
        required_keys: tuple[str, ...],
        expected_status: str | None = None,  # pragma: no mutate
    ) -> JobSnapshot | None:  # pragma: no mutate
        """Atomically update a job only if required heavy keys still exist.

        Returns ``None`` if the job no longer matches the expected status or if
        another path has already cleared required heavy runtime state. Callers
        can then fail loudly without merging partial heavy state back in.

        Raises ``KeyError`` if *job_id* does not exist.
        """
        self._validate_payload_fields(fields)
        self._validate_expected_status(expected_status)
        schedule_cleanup = False  # pragma: no mutate
        expires_at: float | None = None
        with self._write_lock:
            old = self._jobs[job_id]
            _validate_common_record(old)
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
        return self._snapshot(merged)

    def require_job(self, job_id: str) -> JobSnapshot:
        """Return a detached snapshot, or raise HTTP 404 if not found.

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
        with self._write_locked_with_artifact_cleanup() as artifact_cleanups:
            self._remove_job_locked(job_id, artifact_cleanups)

    def require_completed_job(self, job_id: str) -> JobSnapshot:
        """Return a detached snapshot, raising if missing or not completed.

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
        self._validate_expected_status(status)
        with self._write_locked_with_artifact_cleanup() as artifact_cleanups:
            self._evict_stale_locked(time.time(), artifact_cleanups)
            result = any(job.get("status") == status for job in self._jobs.values())
        return result

    def has_job_matching(self, predicate: Callable[[JobSnapshot], bool]) -> bool:
        """Return ``True`` if any live job matches *predicate* under the store lock."""
        with self._write_locked_with_artifact_cleanup() as artifact_cleanups:
            self._evict_stale_locked(time.time(), artifact_cleanups)
            result = any(predicate(self._snapshot(job)) for job in self._jobs.values())
        return result

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
            _validate_common_record(job)
            cleaned = {k: v for k, v in job.items() if k not in keys}
            if not any(key in cleaned for key in _HEAVY_OBJECT_KEYS):
                cleaned.pop(_HEAVY_OBJECT_EXPIRES_AT_KEY, None)
                self._cancel_heavy_object_timer_locked(job_id)
            _validate_common_record(cleaned)
            self._jobs[job_id] = cleaned

    def clear_all(self) -> None:
        """Remove every job and clear all namespace-owned auxiliary state."""
        with self._write_locked_with_artifact_cleanup() as artifact_cleanups:
            for job_id in tuple(self._jobs):
                self._remove_job_locked(job_id, artifact_cleanups)
            for job_id in tuple(self._heavy_object_timers):
                self._cancel_heavy_object_timer_locked(job_id)
            self._running_activity_at.clear()


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
_KNOWN_PREFIXES: frozenset[str] = frozenset(  # pragma: no mutate
    {"training", "optimiser", "explore", "input_cache"}
)


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
