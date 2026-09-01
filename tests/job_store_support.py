"""Narrow white-box helpers for route tests that need stable job identifiers.

Production callers must create and mutate jobs through :class:`JobStore`.  Route
tests occasionally need a deterministic identifier or a precisely prepared
record, so those exceptional writes are centralised here rather than exposing
the store's mutable backing mapping as production API.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, cast

from haute.routes._job_store import (
    JOB_STATUSES,
    RUNNING_STATUS,
    JobStore,
    _detach_builtin,
    _validate_common_record,
)

JobTransform = Callable[[dict[str, Any]], Mapping[str, Any] | None]


def _normalise_lifecycle(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached, valid stored record without inventing payload fields."""
    job = cast(dict[str, Any], _detach_builtin(dict(record)))
    status = job.get("status")
    if status not in JOB_STATUSES:
        raise ValueError(f"Test job has invalid status: {status!r}")

    now = time.time()
    job.setdefault("created_at", now)
    if status == RUNNING_STATUS:
        unexpected = {"terminal_reason", "ended_at", "completed_at"}.intersection(job)
        if unexpected:
            raise ValueError(
                f"Running test job may not contain terminal metadata: {sorted(unexpected)}"
            )
    else:
        job.setdefault("terminal_reason", status)
        job.setdefault("ended_at", job.get("completed_at", job["created_at"]))
        if status == "completed":
            job.setdefault("completed_at", job["ended_at"])

    _validate_common_record(job)
    return job


def seed_job(
    store: JobStore,
    job_id: str,
    record: Mapping[str, Any],
) -> None:
    """Install a validated test fixture under a deterministic, unused ID."""
    job = _normalise_lifecycle(record)
    with store._write_lock:  # noqa: SLF001 - explicit test-only boundary
        if job_id in store._jobs:  # noqa: SLF001
            raise ValueError(f"Test job already exists: {job_id!r}")
        store._jobs[job_id] = job  # noqa: SLF001
        store._running_activity_at.pop(job_id, None)  # noqa: SLF001


def replace_job(store: JobStore, job_id: str, transform: JobTransform) -> None:
    """Atomically transform an existing fixture while preserving its validity.

    The callable receives a detached mutable copy.  It may mutate that copy and
    return ``None``, or return a replacement mapping.  Validation happens before
    the backing record is swapped, so a failed transform leaves the store intact.
    """
    with store._write_lock:  # noqa: SLF001 - explicit test-only boundary
        current = store._jobs[job_id]  # noqa: SLF001
        working = cast(dict[str, Any], _detach_builtin(current))
        replacement = transform(working)
        next_record = working if replacement is None else replacement
        job = _normalise_lifecycle(next_record)
        store._jobs[job_id] = job  # noqa: SLF001
        store._record_running_activity_locked(  # noqa: SLF001
            job_id,
            job,
            now=time.time(),
        )


def discard_corrupt_job(store: JobStore, job_id: str) -> None:
    """Remove a deliberately malformed fixture without interpreting its payload.

    This is only for corruption tests whose artifact metadata cannot safely enter
    the production cleanup path.  Valid records must use :meth:`JobStore.delete_job`
    so owned files and timers are released normally.
    """
    with store._write_lock:  # noqa: SLF001 - explicit test-only boundary
        store._jobs.pop(job_id, None)  # noqa: SLF001
        store._running_activity_at.pop(job_id, None)  # noqa: SLF001
        store._cancel_heavy_object_timer_locked(job_id)  # noqa: SLF001
