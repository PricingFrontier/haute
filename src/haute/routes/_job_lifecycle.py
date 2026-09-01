"""Shared lifecycle transitions for route-backed jobs."""

from __future__ import annotations

import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from haute._execution_context import ExecutionContext, ExecutionMemoryPressureEvent
from haute.routes._job_store import (
    JOB_STATUSES,
    RUNNING_STATUS,
    TERMINAL_REASONS,
    JobSnapshot,
    JobStore,
    LifecycleExpectedStatus,
    TerminalReason,
)
from haute.schemas import JobStatus


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
        job = store.require_job(job_id)
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
    fault_injector: Callable[[str], None] | None = None

    def transition(
        self,
        job_id: str,
        *,
        to: TerminalReason,
        message: str | None = None,
        fields: Mapping[str, Any] | None = None,
        expected_status: LifecycleExpectedStatus = RUNNING_STATUS,
        elapsed_seconds: float | None = None,
        now: float | None = None,
    ) -> JobSnapshot | None:
        """Move a job to a terminal state according to reason precedence.

        ``expected_status`` protects normal running-to-terminal writes. If a
        terminal write already won the race, a later higher-precedence terminal
        reason may still replace it; lower-precedence reasons are ignored. The
        only direct terminal-status correction is completed-to-error for a
        result that failed publication validation.
        """
        if to not in TERMINAL_REASONS:
            raise ValueError(f"Unsupported terminal reason: {to!r}")
        if expected_status not in {RUNNING_STATUS, "completed"}:
            raise ValueError("Lifecycle transitions may expect only 'running' or 'completed'")
        if expected_status == "completed" and to != "error":
            raise ValueError("A completed lifecycle record may only be corrected to 'error'")
        return self.store.transition_terminal(
            job_id,
            to=to,
            message=message,
            fields=fields,
            expected_status=expected_status,
            elapsed_seconds=elapsed_seconds,
            now=now,
            fault_injector=self.fault_injector,
        )

    def publish_completion(
        self,
        job_id: str,
        *,
        publish: Callable[[], Mapping[str, Any]],
        message: str | None = None,
        elapsed_seconds: float | None = None,
        now: float | None = None,
    ) -> JobSnapshot | None:
        """Atomically publish job-specific fields and mark a running job complete."""
        return self.store.compare_and_publish_completion(
            job_id,
            publish=publish,
            message=message,
            elapsed_seconds=elapsed_seconds,
            now=now,
        )
