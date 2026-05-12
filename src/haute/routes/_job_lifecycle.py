"""Shared lifecycle transitions for route-backed jobs."""

from __future__ import annotations

import time
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from haute._execution_context import ExecutionContext, ExecutionMemoryPressureEvent
from haute.routes._job_store import JobStore
from haute.schemas import JobStatus

RUNNING_STATUS = "running"
COMPLETED_STATUS = "completed"
CANCELLED_STATUS = "cancelled"
SUPERSEDED_STATUS = "superseded"
TIMED_OUT_STATUS = "timed_out"
MEMORY_LIMITED_STATUS = "memory_limited"
CONTRACT_ERROR_STATUS = "contract_error"
ERROR_STATUS = "error"

TerminalReason = Literal[
    "completed",
    "superseded",
    "timed_out",
    "cancelled",
    "memory_limited",
    "contract_error",
    "error",
]

TERMINAL_REASON_TO_STATUS: Mapping[TerminalReason, str] = {
    "completed": COMPLETED_STATUS,
    "superseded": SUPERSEDED_STATUS,
    "timed_out": TIMED_OUT_STATUS,
    "cancelled": CANCELLED_STATUS,
    "memory_limited": MEMORY_LIMITED_STATUS,
    "contract_error": CONTRACT_ERROR_STATUS,
    "error": ERROR_STATUS,
}

TERMINAL_STATUSES = frozenset(TERMINAL_REASON_TO_STATUS.values())
JOB_STATUSES = frozenset({RUNNING_STATUS, *TERMINAL_STATUSES})
NON_RUNNING_STATUSES = TERMINAL_STATUSES

_TERMINAL_REASON_PRECEDENCE: Mapping[TerminalReason, int] = {
    "error": 10,
    "contract_error": 20,
    "memory_limited": 30,
    "cancelled": 40,
    "timed_out": 50,
    "superseded": 60,
}


def bind_running_execution_metrics_publisher(
    store: JobStore,
    job_id: str,
    execution_context: ExecutionContext,
) -> None:
    """Publish bounded metrics to a running job when pressure thresholds fire."""
    context_ref = weakref.ref(execution_context)

    def publish(_event: ExecutionMemoryPressureEvent) -> None:
        context = context_ref()
        if context is None:
            return
        try:
            job = store.require_job(job_id)
        except Exception:
            return
        status = require_job_status(job)
        if status != RUNNING_STATUS:
            return
        store.atomic_update(
            job_id,
            {
                "execution_metrics": context.metrics_payload(
                    status=RUNNING_STATUS,
                    terminal_reason=None,
                )
            },
            expected_status=RUNNING_STATUS,
        )

    execution_context.memory_pressure_callback = publish


def require_job_status(job: Mapping[str, Any]) -> JobStatus:
    """Return a valid persisted job status or fail loudly on corrupt state."""
    status = job.get("status")
    if not isinstance(status, str) or status not in JOB_STATUSES:
        raise ValueError(f"Job record has invalid status: {status!r}")
    return cast(JobStatus, status)


@dataclass(frozen=True, slots=True)
class JobLifecycle:
    """Typed transition helper for one ``JobStore`` namespace."""

    store: JobStore

    def transition(
        self,
        job_id: str,
        *,
        to: TerminalReason,
        message: str | None = None,
        fields: Mapping[str, Any] | None = None,
        expected_status: str | None = RUNNING_STATUS,
        elapsed_seconds: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Move a job to a terminal state according to reason precedence.

        ``expected_status`` protects normal running-to-terminal writes. If a
        terminal write already won the race, a later higher-precedence terminal
        reason may still replace it; lower-precedence reasons are ignored.
        """
        timestamp = time.time() if now is None else now
        update: dict[str, Any] = dict(fields or {})
        update["status"] = TERMINAL_REASON_TO_STATUS[to]
        update["terminal_reason"] = to
        update["ended_at"] = timestamp
        if to == "completed":
            update.setdefault("completed_at", timestamp)
        if message is not None:
            update["message"] = message
        if elapsed_seconds is not None:
            update["elapsed_seconds"] = elapsed_seconds

        schedule_cleanup = False
        expires_at: float | None = None
        with self.store._write_lock:  # noqa: SLF001 - lifecycle is a JobStore collaborator.
            old = self.store.jobs[job_id]
            old_status = old.get("status")
            if old_status == expected_status:
                merged, schedule_cleanup, expires_at = self.store._store_merged_job_locked(  # noqa: SLF001
                    job_id,
                    old,
                    update,
                    now=timestamp,
                )
                result: dict[str, Any] | None = merged
            else:
                old_reason = old.get("terminal_reason")
                if not isinstance(old_reason, str) or old_reason not in TERMINAL_REASON_TO_STATUS:
                    return None
                typed_old_reason = cast(TerminalReason, old_reason)
                if typed_old_reason == "completed" or to == "completed":
                    return None
                if _TERMINAL_REASON_PRECEDENCE[to] <= _TERMINAL_REASON_PRECEDENCE[typed_old_reason]:
                    return None
                merged, schedule_cleanup, expires_at = self.store._store_merged_job_locked(  # noqa: SLF001
                    job_id,
                    old,
                    update,
                    now=timestamp,
                )
                result = merged
        self.store._schedule_heavy_object_cleanup_if_needed(  # noqa: SLF001
            job_id,
            schedule_cleanup,
            expires_at,
        )
        return result
