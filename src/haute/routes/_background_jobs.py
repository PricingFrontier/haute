"""Small cooperative lifecycle helpers for thread-backed route jobs."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path
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
from haute._worker_protocol import (
    WorkerFunction,
    WorkerProgressEvent,
    WorkerRequest,
    WorkerResultManifest,
    run_worker_protocol,
)
from haute.routes._job_lifecycle import (
    TERMINAL_REASON_TO_STATUS,
    JobLifecycle,
    TerminalReason,
    require_job_status,
)

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


class SupervisorInfrastructureError(RuntimeError):
    """A supervisor could not persist or verify its terminal outcome."""

    def __init__(self, job_id: str, cause: BaseException) -> None:
        super().__init__(f"Could not persist terminal outcome for isolated job {job_id!r}: {cause}")
        self.job_id = job_id
        self.cause = cause


class IsolatedSupervisorThread(threading.Thread):
    """Supervisor thread whose parent-side infrastructure failure is observable."""

    def __init__(self, *, target: Any) -> None:
        super().__init__(target=target, daemon=True)
        self._infrastructure_failure: SupervisorInfrastructureError | None = None

    @property
    def infrastructure_failure(self) -> SupervisorInfrastructureError | None:
        return self._infrastructure_failure

    def _record_infrastructure_failure(self, failure: SupervisorInfrastructureError) -> None:
        self._infrastructure_failure = failure

    def join_and_raise(self, timeout: float | None = None) -> None:
        """Join, then raise a stored parent-side failure."""
        self.join(timeout=timeout)
        if self.is_alive():
            raise TimeoutError(f"Isolated supervisor thread {self.name!r} is still running")
        if self._infrastructure_failure is not None:
            raise self._infrastructure_failure


@dataclass(frozen=True, slots=True)
class _SupervisorOutcome:
    terminal_reason: TerminalReason
    message: str
    fields: dict[str, Any]
    exception_to_report: BaseException | None = None


class IsolatedJobSupervisor:
    """Parent-side adapter from isolated worker outcomes to ``JobLifecycle``.

    The supervisor deliberately keeps the route-owned ``JobStore`` in the
    parent process. Child processes return picklable results or typed failures;
    this adapter performs the terminal state transition in the parent.
    """

    def __init__(
        self,
        lifecycle: JobLifecycle,
        *,
        protocol_runner: Callable[..., WorkerResultManifest] | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._protocol_runner = protocol_runner

    def launch(
        self,
        job_id: str,
        function: Any,
        *args: Any,
        config: IsolatedWorkerConfig | None = None,
        completed_message: str = "Completed",
        **kwargs: Any,
    ) -> IsolatedSupervisorThread:
        return self._launch_callable(
            job_id,
            execute=lambda: run_isolated_worker(
                function,
                *args,
                config=config,
                **kwargs,
            ),
            completed_fields=lambda result: {"result": result},
            completed_message=completed_message,
            start_time=time.monotonic(),
        )

    def launch_protocol(
        self,
        job_id: str,
        function: WorkerFunction,
        request: WorkerRequest,
        *,
        artifact_root: Path,
        artifact_kinds: frozenset[str],
        max_artifact_size_bytes: int,
        config: IsolatedWorkerConfig | None = None,
        on_progress: Callable[[WorkerProgressEvent], None] | None = None,
        completed_fields: Callable[[WorkerResultManifest], Mapping[str, Any]] | None = None,
        completed_message: str = "Completed",
        on_finished: Callable[[], None] | None = None,
        start_time: float | None = None,
    ) -> IsolatedSupervisorThread:
        """Launch one versioned protocol worker under lifecycle supervision."""
        runner = self._protocol_runner or run_worker_protocol
        map_completed = completed_fields or (lambda result: {"result": result})
        return self._launch_callable(
            job_id,
            execute=lambda: runner(
                function,
                request,
                artifact_root=artifact_root,
                artifact_kinds=artifact_kinds,
                max_artifact_size_bytes=max_artifact_size_bytes,
                on_progress=on_progress,
                config=config,
            ),
            completed_fields=map_completed,
            completed_message=completed_message,
            on_finished=on_finished,
            start_time=time.monotonic() if start_time is None else start_time,
        )

    def _launch_callable(
        self,
        job_id: str,
        *,
        execute: Callable[[], Any],
        completed_fields: Callable[[Any], Mapping[str, Any]],
        completed_message: str,
        start_time: float,
        on_finished: Callable[[], None] | None = None,
    ) -> IsolatedSupervisorThread:
        thread: IsolatedSupervisorThread

        def run() -> None:
            try:
                outcome = self._produce_outcome(
                    execute,
                    completed_fields=completed_fields,
                    completed_message=completed_message,
                )
                outcome = self._finish_outcome(outcome, on_finished)
                self._persist_terminal_outcome(
                    job_id,
                    to=outcome.terminal_reason,
                    message=outcome.message,
                    fields=outcome.fields,
                    start_time=start_time,
                )
            except BaseException as exc:
                failure = SupervisorInfrastructureError(job_id, exc)
                thread._record_infrastructure_failure(failure)
                logger.error(
                    "isolated_supervisor_terminal_persistence_failed",
                    job_id=job_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                return
            if outcome.exception_to_report is not None:
                raise outcome.exception_to_report

        thread = IsolatedSupervisorThread(target=run)
        try:
            thread.start()
        except BaseException as exc:
            outcome = _unexpected_supervisor_outcome(
                exc,
                message_prefix="Failed to start isolated supervisor: ",
            )
            outcome = self._finish_outcome(outcome, on_finished)
            self._persist_terminal_outcome(
                job_id,
                to=outcome.terminal_reason,
                message=outcome.message,
                fields=outcome.fields,
                start_time=start_time,
            )
            raise
        return thread

    @staticmethod
    def _produce_outcome(
        execute: Callable[[], Any],
        *,
        completed_fields: Callable[[Any], Mapping[str, Any]],
        completed_message: str,
    ) -> _SupervisorOutcome:
        try:
            result = execute()
            fields = dict(completed_fields(result))
        except IsolatedWorkerError as exc:
            return _SupervisorOutcome(
                terminal_reason=_coerce_worker_terminal_reason(exc.terminal_reason),
                message=str(exc),
                fields=_isolated_worker_failure_fields(exc),
            )
        except BaseException as exc:
            return _unexpected_supervisor_outcome(
                exc,
                generic_message=True,
                report_exception=True,
            )
        return _SupervisorOutcome(
            terminal_reason="completed",
            message=completed_message,
            fields=fields,
        )

    @staticmethod
    def _finish_outcome(
        outcome: _SupervisorOutcome,
        on_finished: Callable[[], None] | None,
    ) -> _SupervisorOutcome:
        if on_finished is None:
            return outcome
        try:
            on_finished()
        except BaseException as exc:
            if outcome.terminal_reason == "completed":
                return _unexpected_supervisor_outcome(
                    exc,
                    message_prefix="Isolated supervisor cleanup failed: ",
                )
            fields = dict(outcome.fields)
            fields.update(
                {
                    "cleanup_error": str(exc),
                    "cleanup_error_class": type(exc).__name__,
                }
            )
            return _SupervisorOutcome(
                terminal_reason=outcome.terminal_reason,
                message=outcome.message,
                fields=fields,
                exception_to_report=outcome.exception_to_report,
            )
        return outcome

    def _persist_terminal_outcome(
        self,
        job_id: str,
        *,
        to: TerminalReason,
        message: str,
        fields: dict[str, Any],
        start_time: float,
    ) -> None:
        transitioned = self._lifecycle.transition(
            job_id,
            to=to,
            message=message,
            fields=fields,
            elapsed_seconds=time.monotonic() - start_time,
        )
        if transitioned is not None:
            return

        current = self._lifecycle.store.get_job(job_id)
        if current is None:
            raise KeyError(f"Isolated job {job_id!r} disappeared before terminal persistence")
        current_status = require_job_status(current)
        if current_status == "running":
            raise RuntimeError(
                f"Isolated job {job_id!r} remained running after terminal persistence"
            )
        current_reason = current.get("terminal_reason")
        if (
            not isinstance(current_reason, str)
            or current_reason not in TERMINAL_REASON_TO_STATUS
            or TERMINAL_REASON_TO_STATUS[cast(TerminalReason, current_reason)] != current_status
        ):
            raise RuntimeError(f"Isolated job {job_id!r} has incoherent terminal status and reason")


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


def _unexpected_supervisor_outcome(
    exc: BaseException,
    *,
    message_prefix: str = "",
    generic_message: bool = False,
    report_exception: bool = False,
) -> _SupervisorOutcome:
    message = (
        "Unexpected isolated worker supervisor failure."
        if generic_message
        else f"{message_prefix}{exc}"
    )
    fields: dict[str, Any] = {
        "supervisor_error_class": type(exc).__name__,
    }
    if not generic_message:
        fields["error"] = str(exc)
    return _SupervisorOutcome(
        terminal_reason="error",
        message=message,
        fields=fields,
        exception_to_report=exc if report_exception else None,
    )


def _isolated_worker_failure_fields(exc: IsolatedWorkerError) -> dict[str, Any]:
    worker_fields = getattr(exc, "fields", None)
    fields: dict[str, Any] = dict(worker_fields) if isinstance(worker_fields, Mapping) else {}
    fields.update(
        {
            "error": str(exc),
            "worker_error_class": type(exc).__name__,
        }
    )
    if isinstance(exc, IsolatedWorkerRemoteError):
        fields["worker_error_type"] = exc.remote_type
        fields["worker_remote_traceback"] = exc.remote_traceback
    if isinstance(exc, IsolatedWorkerCrashedError):
        fields["worker_exitcode"] = exc.exitcode
    if exc.terminal_reason == "memory_limited":
        fields["error_code"] = "memory_limit"
    notes = getattr(exc, "__notes__", None)
    if notes:
        fields["worker_diagnostic_notes"] = list(notes)
    return fields
