"""Small cooperative lifecycle helpers for thread-backed route jobs."""

from __future__ import annotations

import threading
import time
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any, cast

from haute._execution_context import ExecutionCancellationToken
from haute._logging import get_logger
from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerCrashedError,
    IsolatedWorkerError,
    IsolatedWorkerRemoteError,
    run_isolated_worker,
)
from haute.routes._job_lifecycle import JobLifecycle, TerminalReason

logger = get_logger(component="server.background_jobs")


class BackgroundJobStoppedError(RuntimeError):
    """Raised inside workers when their job has been cancelled or superseded."""

    def __init__(self, job_id: str, terminal_reason: str) -> None:
        super().__init__(f"Job {job_id!r} stopped with terminal reason {terminal_reason!r}")
        self.job_id = job_id
        self.terminal_reason = terminal_reason
        self.status = terminal_reason


@dataclass(slots=True)
class JobCancellation:
    """Cancellation token for one registered background worker."""

    job_id: str
    key: Hashable
    event: threading.Event
    execution_token: ExecutionCancellationToken
    terminal_reason: TerminalReason | None = None

    def cancel(self, reason: TerminalReason) -> None:
        self.terminal_reason = reason
        self.event.set()
        self.execution_token.cancel()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()


class CancellableJobRegistry:
    """Track latest-running jobs and cooperative cancellation tokens.

    The registry deliberately owns only runtime coordination. Persistent job
    metadata remains in ``JobStore`` so route handlers keep one source of truth
    for status, result payloads, and TTL cleanup.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._latest_by_key: dict[Hashable, str] = {}
        self._tokens_by_job_id: dict[str, JobCancellation] = {}

    def register_latest(
        self,
        key: Hashable,
        job_id: str,
        *,
        execution_token: ExecutionCancellationToken | None = None,
    ) -> tuple[JobCancellation, str | None]:
        """Register *job_id* as latest for *key* and cancel any previous job."""
        token = JobCancellation(
            job_id=job_id,
            key=key,
            event=threading.Event(),
            execution_token=execution_token or ExecutionCancellationToken(),
        )
        with self._lock:
            previous_job_id = self._latest_by_key.get(key)
            if previous_job_id is not None:
                previous = self._tokens_by_job_id.get(previous_job_id)
                if previous is not None:
                    previous.cancel("superseded")
            self._latest_by_key[key] = job_id
            self._tokens_by_job_id[job_id] = token
            return token, previous_job_id

    def cancel(self, job_id: str, *, reason: TerminalReason = "cancelled") -> bool:
        """Request cancellation for *job_id* if it still has an active token."""
        with self._lock:
            token = self._tokens_by_job_id.get(job_id)
            if token is None:
                return False
            token.cancel(reason)
            return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            token = self._tokens_by_job_id.get(job_id)
            return bool(token and token.cancelled)

    def cancellation_reason(self, job_id: str) -> TerminalReason | None:
        with self._lock:
            token = self._tokens_by_job_id.get(job_id)
            return token.terminal_reason if token and token.cancelled else None

    def release(self, job_id: str) -> None:
        """Remove active coordination state for *job_id*."""
        with self._lock:
            token = self._tokens_by_job_id.pop(job_id, None)
            if token is None:
                return
            if self._latest_by_key.get(token.key) == job_id:
                del self._latest_by_key[token.key]


@dataclass(frozen=True, slots=True)
class SingleFlightHandle:
    """One active unit of mutually-exclusive work for a shared key."""

    key: Hashable
    job_id: str
    kind: str


class SingleFlightConflictError(RuntimeError):
    """Raised when a single-flight key is already owned by another job."""

    def __init__(self, *, key: Hashable, active_job_id: str, active_kind: str) -> None:
        super().__init__(
            f"Work key {key!r} is already owned by {active_kind!r} job {active_job_id!r}"
        )
        self.key = key
        self.active_job_id = active_job_id
        self.active_kind = active_kind


class SingleFlightCoordinator:
    """Prevent duplicate heavy work for a caller-defined key."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_by_key: dict[Hashable, SingleFlightHandle] = {}

    def active(self, key: Hashable) -> SingleFlightHandle | None:
        """Return the active owner for *key*, if one exists."""
        with self._lock:
            return self._active_by_key.get(key)

    def acquire(self, key: Hashable, *, job_id: str, kind: str) -> SingleFlightHandle:
        """Acquire *key* for *job_id* or raise a typed conflict."""
        handle = SingleFlightHandle(key=key, job_id=job_id, kind=kind)
        with self._lock:
            active = self._active_by_key.get(key)
            if active is not None and active.job_id != job_id:
                raise SingleFlightConflictError(
                    key=key,
                    active_job_id=active.job_id,
                    active_kind=active.kind,
                )
            self._active_by_key[key] = handle
            return handle

    def release(self, key: Hashable, *, job_id: str) -> None:
        """Release *key* only if it is still owned by *job_id*."""
        with self._lock:
            active = self._active_by_key.get(key)
            if active is not None and active.job_id == job_id:
                del self._active_by_key[key]


class IsolatedJobSupervisor:
    """Parent-side adapter from isolated worker outcomes to ``JobLifecycle``.

    The supervisor deliberately keeps the route-owned ``JobStore`` in the
    parent process. Child processes return picklable results or typed failures;
    this adapter performs the terminal state transition in the parent.
    """

    def __init__(self, lifecycle: JobLifecycle) -> None:
        self._lifecycle = lifecycle

    def launch(
        self,
        job_id: str,
        function: Any,
        *args: Any,
        config: IsolatedWorkerConfig | None = None,
        completed_message: str = "Completed",
        **kwargs: Any,
    ) -> threading.Thread:
        start_time = time.monotonic()

        def run() -> None:
            try:
                result = run_isolated_worker(function, *args, config=config, **kwargs)
            except IsolatedWorkerError as exc:
                self._transition_failure(job_id, exc, start_time=start_time)
                return
            except Exception as exc:
                try:
                    self._lifecycle.transition(
                        job_id,
                        to="error",
                        message="Unexpected isolated worker supervisor failure.",
                        fields={"worker_error_class": type(exc).__name__},
                        elapsed_seconds=time.monotonic() - start_time,
                    )
                except Exception:
                    logger.exception(
                        "isolated_job_unexpected_failure_transition_failed",
                        job_id=job_id,
                    )
                raise
            self._lifecycle.transition(
                job_id,
                to="completed",
                message=completed_message,
                fields={"result": result},
                elapsed_seconds=time.monotonic() - start_time,
            )

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def _transition_failure(
        self,
        job_id: str,
        exc: IsolatedWorkerError,
        *,
        start_time: float,
    ) -> None:
        terminal_reason = _coerce_worker_terminal_reason(exc.terminal_reason)
        fields = _isolated_worker_failure_fields(exc)
        self._lifecycle.transition(
            job_id,
            to=terminal_reason,
            message=str(exc),
            fields=fields,
            elapsed_seconds=time.monotonic() - start_time,
        )


def _coerce_worker_terminal_reason(reason: str) -> TerminalReason:
    if reason in {
        "completed",
        "superseded",
        "timed_out",
        "cancelled",
        "memory_limited",
        "contract_error",
        "error",
    }:
        return cast(TerminalReason, reason)
    return "error"


def _isolated_worker_failure_fields(exc: IsolatedWorkerError) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "error": str(exc),
        "worker_error_class": type(exc).__name__,
    }
    if isinstance(exc, IsolatedWorkerRemoteError):
        fields["worker_error_type"] = exc.remote_type
        fields["worker_remote_traceback"] = exc.remote_traceback
    if isinstance(exc, IsolatedWorkerCrashedError):
        fields["worker_exitcode"] = exc.exitcode
    if exc.terminal_reason == "memory_limited":
        fields["error_code"] = "memory_limit"
    return fields
