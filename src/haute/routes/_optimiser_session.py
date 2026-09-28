"""The solver session's parent side (OPT-W01).

A process-mode solve owns one :class:`SolverSession`: a dedicated worker that
holds the solver, quote grid and factor contexts from the solve until the job
drops its heavy state. The server never holds a native optimiser object.

Every command runs the same sequence under :meth:`SolverSession.run_command`:
take the session's command slot (one command at a time, nothing reserved while
waiting), admit a growth grant sized from the memory free at that moment, run
the command in the child under a cap of its charge plus that grant, publish the
outcome on the parent side while still holding the slot, then release the
reservation and the slot. The session is the job's ``solver_session`` heavy
value: dropping it (expiry, clearing, a failed job) releases it, and a release
while a command runs is deferred until that command returns.

When the worker dies the session records why on the job
(``runtime_unavailable``) and detaches itself, so later requests that need the
live runtime answer 410 with that reason.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Final, TypeVar

from haute._dedicated_workers import DedicatedWorker, DedicatedWorkerDeadError, WorkerDeath
from haute._env import float_env, int_env
from haute._execution_admission import admit_growth_grant
from haute._execution_context import ExecutionCancellationToken, ExecutionProfile
from haute._interactive_workers import (
    InteractiveWorkerError,
    InteractiveWorkerStoppedError,
    InteractiveWorkerTimeoutError,
)
from haute._logging import get_logger
from haute._native_memory_limit import model_thread_address_space_allowance
from haute._step_progress import StepProgress
from haute._worker_isolation import WorkerTerminalReason, resolve_worker_memory_enforcement
from haute.routes._job_store import JobStore
from haute.routes._memory_messages import solver_session_memory_message

logger = get_logger(component="server.optimiser.session")

T = TypeVar("T")

SESSION_KEY: Final = "solver_session"
RUNTIME_MODE_KEY: Final = "runtime_mode"
RUNTIME_UNAVAILABLE_KEY: Final = "runtime_unavailable"
SESSION_RUNTIME: Final = "session"
IN_PROCESS_RUNTIME: Final = "in_process"

# The config panel re-runs the input estimate on every relevant edit, and it
# reserves the whole optimiser-setup budget; session admissions wait it out
# rather than refusing (as training does for its evaluation preview).
ESTIMATE_HOLDERS: Final = frozenset({"optimiser_setup:optimiser_estimate"})
ESTIMATE_WAIT_SECONDS: Final = 30.0

_SLOT_POLL_SECONDS = 0.05
_DEFAULT_RESULT_MAX_BYTES = 64 * 1024 * 1024


def runtime_mode(job: Mapping[str, Any]) -> str:
    """Where a completed solve's runtime lives: its session, or this process."""
    return str(job.get(RUNTIME_MODE_KEY, IN_PROCESS_RUNTIME))


def _session_start_timeout() -> float:
    return float_env("HAUTE_OPTIMISER_SESSION_START_TIMEOUT", 60.0)


def _session_result_max_bytes() -> int:
    return int_env("HAUTE_OPTIMISER_SESSION_RESULT_MAX_BYTES", _DEFAULT_RESULT_MAX_BYTES)


class SessionCommandError(Exception):
    """A session command that failed as a whole: its worker died, or it could not run.

    Carries what the caller publishes: the terminal reason for a job, and the
    HTTP status and detail for a request.
    """

    def __init__(
        self,
        *,
        terminal_reason: WorkerTerminalReason,
        message: str,
        http_status_code: int,
        detail: Any,
    ) -> None:
        super().__init__(message)
        self.terminal_reason = terminal_reason
        self.message = message
        self.http_status_code = http_status_code
        self.detail = detail


class SolverSession:
    """One solve's dedicated worker, its command slot and its lifetime (see the module doc)."""

    def __init__(self, *, job_id: str, store: JobStore) -> None:
        self.job_id = job_id
        self._store = store
        self._worker = DedicatedWorker(
            owner=f"optimiser_solve:{job_id}",
            polars_threads=os.cpu_count() or 1,
            process_name=f"haute-solver-{job_id}",
            preload_modules=("haute.routes._optimiser_session_worker",),
        )
        self._slot = threading.Lock()
        self._state_lock = threading.Lock()
        self._pins = 0
        self._closing = False
        self.stage = "starting"
        # The last command's admitted context and what it ran under, for the job's metrics.
        self.last_context: Any = None
        self.last_command: dict[str, Any] | None = None

    @property
    def death(self) -> WorkerDeath | None:
        return self._worker.death

    @property
    def alive(self) -> bool:
        return self._worker.alive

    @property
    def pid(self) -> int | None:
        return self._worker.pid

    @property
    def last_cap(self) -> Any:
        return self._worker.last_cap

    def start(self, *, stop_reason: Callable[[], WorkerTerminalReason | None]) -> None:
        self._worker.start(start_timeout_seconds=_session_start_timeout(), stop_reason=stop_reason)

    def run_command(
        self,
        command: Callable[..., Any],
        request: Any,
        *,
        operation: str,
        stage: str,
        publish: Callable[[Any], T],
        stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
        timeout_seconds: float | None = None,
        on_progress: Callable[[StepProgress], None] | None = None,
        cancellation_token: ExecutionCancellationToken | None = None,
        on_slot: Callable[[], None] | None = None,
    ) -> T:
        """Run one command and publish its value while still holding the slot.

        *on_slot* runs as soon as the slot is held, before anything is reserved
        or sent: a caller re-checks there whatever may have changed while it
        waited (a frontier point's generation).
        """
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while not self._slot.acquire(timeout=_SLOT_POLL_SECONDS):
            if stop_reason is not None:
                reason = stop_reason()
                if reason is not None:
                    raise InteractiveWorkerStoppedError(reason)
            if deadline is not None and time.monotonic() >= deadline:
                raise InteractiveWorkerTimeoutError(float(timeout_seconds or 0.0))
        try:
            if on_slot is not None:
                on_slot()
            with self._state_lock:
                if self._closing:
                    raise DedicatedWorkerDeadError(
                        WorkerDeath(
                            kind="terminated",
                            exit_code=None,
                            memory_evidence="none",
                            reason="expired",
                        )
                    )
                self._pins += 1
            try:
                return self._run_admitted(
                    command,
                    request,
                    operation=operation,
                    stage=stage,
                    publish=publish,
                    stop_reason=stop_reason,
                    deadline=deadline,
                    on_progress=on_progress,
                    cancellation_token=cancellation_token,
                )
            finally:
                with self._state_lock:
                    self._pins -= 1
                    terminate_now = self._closing and self._pins == 0
                if terminate_now:
                    self._worker.terminate("expired")
        finally:
            self._slot.release()

    def _run_admitted(
        self,
        command: Callable[..., Any],
        request: Any,
        *,
        operation: str,
        stage: str,
        publish: Callable[[Any], T],
        stop_reason: Callable[[], WorkerTerminalReason | None] | None,
        deadline: float | None,
        on_progress: Callable[[StepProgress], None] | None,
        cancellation_token: ExecutionCancellationToken | None,
    ) -> T:
        self.stage = stage
        context = admit_growth_grant(
            operation=operation,
            profile=ExecutionProfile.OPTIMISER_SOLVE,
            job_id=self.job_id,
            cancellation_token=cancellation_token,
            wait_out_holders=ESTIMATE_HOLDERS,
            wait_seconds=ESTIMATE_WAIT_SECONDS,
        )
        grant = int(context.memory_limit_bytes or 0)
        self.last_context = context

        def forward_progress(update: StepProgress) -> None:
            if update.label:
                self.stage = update.label.lower()
            if on_progress is not None:
                on_progress(update)

        try:
            try:
                value = self._worker.run(
                    command,
                    request,
                    growth_bytes=grant,
                    required=resolve_worker_memory_enforcement() == "required",
                    allowance_bytes=model_thread_address_space_allowance(),
                    timeout_seconds=(
                        None if deadline is None else max(deadline - time.monotonic(), 0.001)
                    ),
                    stop_reason=stop_reason,
                    on_progress=forward_progress,
                    max_result_bytes=_session_result_max_bytes(),
                )
            except (InteractiveWorkerStoppedError, InteractiveWorkerTimeoutError):
                raise
            except InteractiveWorkerError as exc:
                raise self._command_failure(exc, operation=operation, grant=grant) from exc
            if self._closing:
                # Detached while it ran (expiry, clearing): its runtime is gone.
                raise DedicatedWorkerDeadError(
                    WorkerDeath(
                        kind="terminated", exit_code=None, memory_evidence="none", reason="expired"
                    )
                )
            return publish(value)
        finally:
            cap = self._worker.last_cap
            self.last_command = {
                "operation": operation,
                "grant_bytes": grant,
                "backend": None if cap is None else cap.backend,
                "baseline_bytes": None if cap is None else cap.baseline_bytes,
                "ceiling_bytes": None if cap is None else cap.ceiling_bytes,
                "charge_bytes_at_failure": None if cap is None else cap.charge_bytes,
            }
            context.release_admission()

    def _command_failure(
        self, exc: InteractiveWorkerError, *, operation: str, grant: int
    ) -> SessionCommandError:
        death = self._worker.death
        stage = self.stage
        if death is not None:
            self._record_death(death)
        if exc.terminal_reason == "memory_limited" or (
            death is not None and death.memory_evidence != "none"
        ):
            evidence = death.memory_evidence if death is not None else "suspected"
            message = solver_session_memory_message(evidence, cap_bytes=grant, stage=stage)
            return SessionCommandError(
                terminal_reason="memory_limited",
                message=message,
                http_status_code=507,
                detail={
                    "error_code": "memory_limit",
                    "operation": operation,
                    "reason": "solver_session_memory_limit",
                    "memory_limit_bytes": grant,
                    "memory_evidence": evidence,
                    "stage": stage,
                    "message": message,
                },
            )
        logger.error(
            "solver_session_command_failed",
            job_id=self.job_id,
            operation=operation,
            error=str(exc),
            error_type=type(exc).__name__,
            remote_traceback=getattr(exc, "remote_traceback", None),
        )
        terminal_reason: WorkerTerminalReason = (
            "contract_error" if exc.terminal_reason == "contract_error" else "error"
        )
        return SessionCommandError(
            terminal_reason=terminal_reason,
            message=f"The optimiser's solver process failed while {stage}: {exc}",
            http_status_code=500,
            detail="The optimiser's solver process failed. Check the server logs for details.",
        )

    def _record_death(self, death: WorkerDeath) -> None:
        """Tell the job its runtime is gone, and why; then detach the session from it."""
        reason = (
            "solver_session_memory_limited"
            if death.memory_evidence != "none"
            else "solver_session_crashed"
        )
        self._record_unavailable(reason, death.memory_evidence)
        self._store.clear_result_data(self.job_id, keys=(SESSION_KEY,))

    def _record_unavailable(self, reason: str, memory_evidence: str) -> None:
        job = self._store.get_job(self.job_id)
        if job is None or job.get("status") != "completed" or RUNTIME_UNAVAILABLE_KEY in job:
            return
        self._store.atomic_update(
            self.job_id,
            {
                RUNTIME_UNAVAILABLE_KEY: {
                    "reason": reason,
                    "memory_evidence": memory_evidence,
                    "at": time.time(),
                }
            },
            expected_status="completed",
        )

    def terminate(self, reason: str) -> None:
        """End the worker now, even mid-command (solve cancellation and timeout)."""
        self._worker.terminate(reason)

    def release(self) -> None:
        """The job dropped this session: end it, after any running command returns."""
        with self._state_lock:
            self._closing = True
            terminate_now = self._pins == 0
        if self._worker.death is None:
            self._record_unavailable("expired", "none")
        if terminate_now:
            self._worker.terminate("expired")
