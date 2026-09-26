"""A long-lived spawn worker pinned to one owner, under a native cap it never lifts.

The warm interactive pool lends a worker to one request at a time and lifts its
cap between requests. A :class:`DedicatedWorker` instead belongs to one owner
(the optimiser's solver session, OPT-W01) and keeps state between the commands
it runs. Every command carries the memory growth its owner was just granted,
and the child re-applies its native cap to its current charge plus that growth
before running it; the cap is never restored, so an idle worker keeps its last
ceiling. A failed worker is never replaced: it is recorded dead, with the
evidence of how it died, and its owner decides what that means.

The child exits when the server process does: it follows the parent *process*
(a process handle on Windows, a pidfd on Linux, a ``getppid`` poll otherwise),
never ``PR_SET_PDEATHSIG``, which follows the spawning thread.

Memory evidence for a death is only as strong as its source. A Windows Job
Object's ``JOB_OBJECT_MSG_JOB_MEMORY_LIMIT`` notification (counted by the child
into a parent-owned counter, read without blocking) or a rise in the private
cgroup's ``memory.events.local`` ``oom`` count is the limiter's own record;
without one, a ``MemoryError`` or a memory-shaped exit code is only suspected.
"""

from __future__ import annotations

import atexit
import importlib
import multiprocessing as mp
import os
import pickle
import queue
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any, Literal, cast

from haute._cpu_performance import configure_process_high_qos
from haute._interactive_workers import (
    InteractiveWorkerCrashedError,
    InteractiveWorkerError,
    InteractiveWorkerMemoryLimitError,
    InteractiveWorkerProtocolError,
    InteractiveWorkerRemoteError,
    InteractiveWorkerStartError,
    InteractiveWorkerStoppedError,
    InteractiveWorkerTimeoutError,
    _public_exception_payload,
)
from haute._logging import get_logger
from haute._native_memory_limit import (
    JOB_OBJECT_MSG_JOB_MEMORY_LIMIT,
    NativeLimitState,
    NativeMemoryLease,
    cleanup_private_cgroups_for_pid,
    memory_error_for_thread_start_failure,
    native_memory_backend_scope,
    private_cgroup_oom_events_for_pid,
    watch_windows_job_messages,
)
from haute._polars_utils import current_streaming_chunk_size, set_streaming_chunk_size
from haute._process_memory import process_rss_bytes
from haute._step_progress import ProgressCell, StepProgress, bind_job_progress
from haute._worker_isolation import (
    WorkerTerminalReason,
    _exitcode_looks_memory_limited,
    _terminate_process,
    create_worker_queue,
    start_process_with_environment,
)

logger = get_logger(component="dedicated_workers")

MemoryEvidence = Literal["cap_confirmed", "watchdog", "suspected", "none"]
DeathKind = Literal["crashed", "stopped", "timed_out", "watchdog", "remote_memory", "terminated"]

_POLL_INTERVAL_SECONDS = 0.05
_EVIDENCE_READ_TIMEOUT_SECONDS = 0.1
_PARENT_POLL_SECONDS = 1.0
_REQUEST_POLL_SECONDS = 1.0
# ``SYNCHRONIZE``: the one right waiting on a process handle needs.
_SYNCHRONIZE = 0x00100000


@dataclass(frozen=True, slots=True)
class WorkerDeath:
    """How a dedicated worker died, and how sure we are that memory killed it."""

    kind: DeathKind
    exit_code: int | None
    memory_evidence: MemoryEvidence
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CommandCap:
    """The native cap one command ran under, as the child reported it."""

    growth_bytes: int | None
    backend: str | None
    baseline_bytes: int | None
    ceiling_bytes: int | None
    oom_events: int | None
    # The backend's charge when the command failed: supporting data, never evidence.
    charge_bytes: int | None = None

    @classmethod
    def from_state(
        cls, growth_bytes: int | None, state: NativeLimitState, *, charge_bytes: int | None = None
    ) -> CommandCap:
        return cls(
            growth_bytes=growth_bytes,
            backend=state.backend,
            baseline_bytes=state.baseline_bytes,
            ceiling_bytes=state.ceiling_bytes,
            oom_events=state.oom_events,
            charge_bytes=charge_bytes,
        )


class DedicatedWorkerDeadError(InteractiveWorkerError):
    """A command was sent to a worker that has already died."""

    def __init__(self, death: WorkerDeath) -> None:
        super().__init__(f"Dedicated worker is no longer running ({death.kind})")
        self.death = death


class WorkerResultTooLargeError(RuntimeError):
    """A command's result is larger than its owner allows through the transport."""


class LimitEvidenceCounter:
    """A parent-owned count of limit notifications, written by the child.

    The parent reads it with a bounded lock acquire: a child that died holding
    the lock leaves it unreadable, which counts as no record, never as a wait.
    """

    def __init__(self, ctx: Any) -> None:
        self._value: Any = ctx.Value("q", 0)

    def record(self) -> None:
        with self._value.get_lock():
            self._value.value += 1

    def try_read(self) -> int | None:
        lock = self._value.get_lock()
        if not lock.acquire(timeout=_EVIDENCE_READ_TIMEOUT_SECONDS):
            return None
        try:
            return int(self._value.value)
        finally:
            lock.release()


def record_job_notification(message_id: int, counter: LimitEvidenceCounter) -> None:
    """Count *message_id* when it is the Job Object's memory-limit notification (child side)."""
    if message_id == JOB_OBJECT_MSG_JOB_MEMORY_LIMIT:
        counter.record()


def read_limit_evidence(counter: LimitEvidenceCounter, baseline: int | None) -> bool | None:
    """Whether the limit recorded a refusal since *baseline*; ``None`` if unreadable (parent)."""
    current = counter.try_read()
    if current is None or baseline is None:
        return None
    return current > baseline


def _wait_for_process_exit(pid: int) -> None:
    """Block until process *pid* has exited (or cannot be watched because it already has)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        return
    if os.getppid() != pid:
        return
    pidfd_open = getattr(os, "pidfd_open", None)
    if pidfd_open is not None:
        import select

        try:
            fd = pidfd_open(pid)
        except OSError:
            fd = None
        if fd is not None:
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            poller.poll()
            return
    while os.getppid() == pid:
        time.sleep(_PARENT_POLL_SECONDS)


def _start_parent_watcher(parent_pid: int) -> None:
    def _watch() -> None:
        _wait_for_process_exit(parent_pid)
        # The server is gone: nothing can use this worker's state any more.
        os._exit(0)

    threading.Thread(target=_watch, name="haute-parent-watcher", daemon=True).start()


def _error_envelope(command_id: str, exc: BaseException, cap: CommandCap | None) -> tuple[Any, ...]:
    return (
        "result",
        command_id,
        "error",
        (
            type(exc).__name__,
            type(exc).__module__,
            str(exc),
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            _public_exception_payload(exc),
        ),
        cap,
    )


# The child's limit-notification counter: the Windows watcher's sink, and the
# seam a test command uses to supply a notification through the real path.
_child_evidence_counter: LimitEvidenceCounter | None = None


def child_limit_evidence_counter() -> LimitEvidenceCounter:
    """The running dedicated worker's limit-notification counter (child side)."""
    if _child_evidence_counter is None:
        raise RuntimeError("Not running inside a dedicated worker")
    return _child_evidence_counter


def _dedicated_worker_entrypoint(
    request_queue: Any,
    result_queue: Any,
    parent_pid: int,
    preload_modules: tuple[str, ...],
    progress_cell: ProgressCell,
    evidence_counter: LimitEvidenceCounter,
) -> None:
    global _child_evidence_counter
    _child_evidence_counter = evidence_counter
    _start_parent_watcher(parent_pid)
    lease = NativeMemoryLease()
    watching_job = False
    configure_process_high_qos()
    for module_name in preload_modules:
        importlib.import_module(module_name)
    result_queue.put(pickle.dumps(("ready", os.getpid()), protocol=pickle.HIGHEST_PROTOCOL))
    while True:
        try:
            raw_request = request_queue.get(timeout=_REQUEST_POLL_SECONDS)
        except queue.Empty:
            continue
        request = pickle.loads(raw_request)
        if request == ("shutdown",):
            return
        if not isinstance(request, tuple) or len(request) != 10 or request[0] != "run":
            raise RuntimeError("dedicated worker received a malformed request")
        (
            _kind,
            command_id,
            function,
            args,
            kwargs,
            growth,
            required,
            allowance,
            streaming_chunk_size,
            max_result_bytes,
        ) = request
        set_streaming_chunk_size(streaming_chunk_size)
        envelope: tuple[Any, ...]
        cap: CommandCap | None = None
        applied = False
        try:
            if growth is not None:
                applied = lease.apply(
                    growth, required=required, address_space_allowance_bytes=allowance
                )
            if applied and sys.platform == "win32" and not watching_job:
                watch_windows_job_messages(
                    lease.windows_job,
                    lambda message: record_job_notification(message, evidence_counter),
                )
                watching_job = True
            state = lease.limit_state() if applied else NativeLimitState(None, None, None)
            cap = CommandCap.from_state(growth, state)
        except BaseException as exc:
            envelope = _error_envelope(command_id, exc, cap)
        else:
            with native_memory_backend_scope(state.backend):
                try:
                    with bind_job_progress(progress_cell, command_id):
                        value = function(*args, **kwargs)
                    envelope = ("result", command_id, "ok", value, cap)
                except BaseException as raised:
                    failure = memory_error_for_thread_start_failure(raised) if applied else raised
                    failed_cap = CommandCap.from_state(
                        growth, state, charge_bytes=lease.current_charge_bytes()
                    )
                    envelope = _error_envelope(command_id, failure, failed_cap)
        try:
            payload = pickle.dumps(envelope, protocol=pickle.HIGHEST_PROTOCOL)
            if max_result_bytes is not None and len(payload) > max_result_bytes:
                raise WorkerResultTooLargeError(
                    f"The command's result is {len(payload)} bytes, more than the "
                    f"{max_result_bytes} bytes its owner accepts."
                )
        except BaseException as exc:
            payload = pickle.dumps(
                _error_envelope(command_id, exc, cap), protocol=pickle.HIGHEST_PROTOCOL
            )
        result_queue.put(payload)


_REGISTRY_LOCK = threading.Lock()
_REGISTRY: set[DedicatedWorker] = set()
_SHUTTING_DOWN = False


class DedicatedWorker:
    """One long-lived spawn worker that belongs to a single owner (see the module doc)."""

    def __init__(
        self,
        *,
        owner: str,
        polars_threads: int,
        process_name: str,
        preload_modules: tuple[str, ...] = (),
    ) -> None:
        self.owner = owner
        self._polars_threads = polars_threads
        self._process_name = process_name
        self._preload_modules = preload_modules
        self._ctx = mp.get_context("spawn")
        self._state_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._request_queue: Any = None
        self._result_queue: Any = None
        self._progress: ProgressCell | None = None
        self._evidence: LimitEvidenceCounter | None = None
        self._death: WorkerDeath | None = None
        self._cleaned_up = False
        self._oom_events_before = 0
        self.last_cap: CommandCap | None = None

    @property
    def death(self) -> WorkerDeath | None:
        return self._death

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def alive(self) -> bool:
        return self._death is None and self._process is not None and self._process.is_alive()

    def start(
        self,
        *,
        start_timeout_seconds: float,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
    ) -> None:
        """Spawn the child and wait until it is ready; registered before it is spawned."""
        with _REGISTRY_LOCK:
            if _SHUTTING_DOWN:
                raise InteractiveWorkerStartError(
                    "The server is shutting down; no new worker can start.",
                    terminal_reason="cancelled",
                )
            _REGISTRY.add(self)
        self._request_queue = create_worker_queue(self._ctx, 1)
        self._result_queue = create_worker_queue(self._ctx, 1)
        self._progress = ProgressCell(self._ctx)
        self._evidence = LimitEvidenceCounter(self._ctx)
        process = self._ctx.Process(
            target=_dedicated_worker_entrypoint,
            name=self._process_name,
            args=(
                self._request_queue,
                self._result_queue,
                os.getpid(),
                self._preload_modules,
                self._progress,
                self._evidence,
            ),
        )
        try:
            # Spawn under the state lock, and only if nothing terminated us first: a
            # concurrent terminate then either stops the spawn or sees its pid.
            with self._state_lock:
                if self._death is not None:
                    raise InteractiveWorkerStoppedError(
                        cast(WorkerTerminalReason, self._death.reason or "cancelled")
                    )
                self._process = process
                start_process_with_environment(
                    process, {"POLARS_MAX_THREADS": str(self._polars_threads)}
                )
            self._wait_for_ready(start_timeout_seconds, stop_reason)
        except InteractiveWorkerError:
            self.terminate("error")
            raise
        except BaseException as exc:
            self.terminate("error")
            raise InteractiveWorkerStartError(
                f"Failed to start dedicated worker {self._process_name}: {exc}",
                terminal_reason="error",
            ) from exc

    def _wait_for_ready(
        self,
        timeout_seconds: float,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None,
    ) -> None:
        process = cast(BaseProcess, self._process)
        deadline = time.monotonic() + timeout_seconds
        while True:
            if stop_reason is not None:
                reason = stop_reason()
                if reason is not None:
                    raise InteractiveWorkerStoppedError(reason)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InteractiveWorkerTimeoutError(timeout_seconds)
            try:
                raw_ready = self._result_queue.get(timeout=min(_POLL_INTERVAL_SECONDS, remaining))
            except queue.Empty:
                if not process.is_alive():
                    raise InteractiveWorkerCrashedError(process.exitcode) from None
                continue
            if pickle.loads(raw_ready) != ("ready", process.pid):
                raise InteractiveWorkerProtocolError(
                    "Dedicated worker returned an invalid ready envelope",
                    terminal_reason="contract_error",
                )
            return

    def run(
        self,
        function: Callable[..., Any],
        *args: Any,
        growth_bytes: int | None,
        required: bool,
        allowance_bytes: int = 0,
        timeout_seconds: float | None = None,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
        on_progress: Callable[[StepProgress], None] | None = None,
        max_result_bytes: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run one command under a cap of *growth_bytes* above the child's current charge.

        Returns the command's value. A remote exception is raised as
        ``InteractiveWorkerRemoteError`` and leaves the worker running, unless it
        is memory-shaped: then the worker's state is suspect and it is
        terminated, recording its death. A crash, stop, timeout or RSS-watchdog
        breach also ends the worker; its :attr:`death` carries the evidence.
        """
        if growth_bytes is not None and growth_bytes <= 0:
            raise ValueError("growth_bytes must be positive")
        if required and growth_bytes is None:
            raise ValueError("required memory enforcement needs a growth limit")
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("A dedicated worker runs one command at a time")
        try:
            if self._death is not None:
                raise DedicatedWorkerDeadError(self._death)
            process = cast(BaseProcess, self._process)
            evidence = cast(LimitEvidenceCounter, self._evidence)
            command_id = uuid.uuid4().hex
            evidence_before = evidence.try_read()
            # The private cgroup's own refusals before this command; a group the
            # child has not created yet (its first cap) starts from none.
            self._oom_events_before = private_cgroup_oom_events_for_pid(cast(int, process.pid)) or 0
            request = (
                "run",
                command_id,
                function,
                args,
                kwargs,
                growth_bytes,
                required,
                allowance_bytes,
                current_streaming_chunk_size(),
                max_result_bytes,
            )
            try:
                serialised = pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL)
            except BaseException as exc:
                raise InteractiveWorkerProtocolError(
                    f"Dedicated worker request is not serialisable: {exc}",
                    terminal_reason="contract_error",
                ) from exc
            rss_limit: int | None = None
            if growth_bytes is not None:
                baseline_rss = process_rss_bytes(cast(int, process.pid))
                if baseline_rss is not None:
                    rss_limit = baseline_rss + growth_bytes
            self._request_queue.put(serialised)
            return self._wait_for_result(
                command_id,
                growth_bytes=growth_bytes,
                evidence_before=evidence_before,
                rss_limit=rss_limit,
                deadline=None if timeout_seconds is None else time.monotonic() + timeout_seconds,
                timeout_seconds=timeout_seconds,
                stop_reason=stop_reason,
                on_progress=on_progress,
            )
        finally:
            self._run_lock.release()

    def _wait_for_result(
        self,
        command_id: str,
        *,
        growth_bytes: int | None,
        evidence_before: int | None,
        rss_limit: int | None,
        deadline: float | None,
        timeout_seconds: float | None,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None,
        on_progress: Callable[[StepProgress], None] | None,
    ) -> Any:
        process = cast(BaseProcess, self._process)
        progress = cast(ProgressCell, self._progress)
        progress_sequence = 0
        while True:
            death = self._death
            if death is not None:
                raise InteractiveWorkerStoppedError(
                    cast(WorkerTerminalReason, death.reason or "cancelled")
                )
            if deadline is not None and time.monotonic() >= deadline:
                self._die("timed_out", evidence="none", reason="timed_out")
                raise InteractiveWorkerTimeoutError(cast(float, timeout_seconds))
            try:
                raw_result = self._result_queue.get(timeout=_POLL_INTERVAL_SECONDS)
            except queue.Empty:
                raw_result = None
            except (OSError, ValueError, EOFError):
                # The queue was closed under us by a concurrent terminate.
                raw_result = None
                if self._death is not None:
                    continue
                raise
            if raw_result is None and on_progress is not None:
                update = progress.try_read(command_id)
                if update is not None and update[0] > progress_sequence:
                    progress_sequence = update[0]
                    on_progress(update[1])
            if raw_result is not None:
                return self._interpret_result(
                    command_id, raw_result, evidence_before=evidence_before
                )
            if not process.is_alive():
                if self._death is not None:
                    # Terminated deliberately while we polled: a stop, not a crash.
                    continue
                exit_code = process.exitcode
                heuristic = _exitcode_looks_memory_limited(exit_code, growth_bytes)
                confirmed = self._limit_recorded(evidence_before)
                evidence: MemoryEvidence = (
                    "cap_confirmed" if confirmed else "suspected" if heuristic else "none"
                )
                self._die("crashed", evidence=evidence, exit_code=exit_code)
                raise InteractiveWorkerCrashedError(exit_code, memory_limited=evidence != "none")
            if stop_reason is not None:
                reason = stop_reason()
                if reason is not None:
                    self._die("stopped", evidence="none", reason=reason)
                    raise InteractiveWorkerStoppedError(reason)
            if rss_limit is not None:
                rss = process_rss_bytes(cast(int, process.pid))
                if rss is not None and rss > rss_limit:
                    self._die("watchdog", evidence="watchdog", reason="memory_limited")
                    raise InteractiveWorkerMemoryLimitError(rss_bytes=rss, limit_bytes=rss_limit)

    def _interpret_result(
        self, command_id: str, raw_result: Any, *, evidence_before: int | None
    ) -> Any:
        try:
            envelope = pickle.loads(raw_result)
        except BaseException as exc:
            self._die("crashed", evidence="none")
            raise InteractiveWorkerProtocolError(
                f"Dedicated worker returned an unreadable result: {exc}",
                terminal_reason="contract_error",
            ) from exc
        if (
            not isinstance(envelope, tuple)
            or len(envelope) != 5
            or envelope[0] != "result"
            or envelope[1] != command_id
            or envelope[2] not in {"ok", "error"}
        ):
            self._die("crashed", evidence="none")
            raise InteractiveWorkerProtocolError(
                "Dedicated worker returned a malformed or stale result envelope",
                terminal_reason="contract_error",
            )
        _kind, _command_id, status, payload, cap = envelope
        self.last_cap = cap if isinstance(cap, CommandCap) else None
        if status == "ok":
            return payload
        remote_type, remote_module, remote_message, remote_traceback, public_payload = payload
        error = InteractiveWorkerRemoteError(
            remote_type=str(remote_type),
            remote_module=str(remote_module),
            remote_message=str(remote_message),
            remote_traceback=str(remote_traceback),
            public_payload=public_payload if isinstance(public_payload, dict) else None,
        )
        if remote_type == "ExecutionMemoryLimitExceededError":
            self._die("remote_memory", evidence="watchdog", reason="memory_limited")
        elif str(remote_type).endswith("MemoryError"):
            confirmed = self._limit_recorded(evidence_before)
            self._die(
                "remote_memory",
                evidence="cap_confirmed" if confirmed else "suspected",
                reason="memory_limited",
            )
        elif str(remote_type).startswith("NativeMemoryLimit"):
            # A cap that could not be installed leaves the worker uncapped.
            self._die("terminated", evidence="none", reason="contract_error")
        raise error

    def _limit_recorded(self, evidence_before: int | None) -> bool:
        """Whether the memory limiter itself recorded a refusal during this command.

        Two records count: the Job Object notifications the child's watcher
        counts (only Windows posts them) and the private cgroup's own ``oom``
        refusals (only a cgroup cap has them).
        """
        counter = cast(LimitEvidenceCounter, self._evidence)
        if read_limit_evidence(counter, evidence_before) is True:
            return True
        pid = self.pid
        oom_now = None if pid is None else private_cgroup_oom_events_for_pid(pid)
        return oom_now is not None and oom_now > self._oom_events_before

    def _die(
        self,
        kind: DeathKind,
        *,
        evidence: MemoryEvidence,
        reason: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        with self._state_lock:
            if self._death is None:
                process = self._process
                code = exit_code
                if code is None and process is not None and not process.is_alive():
                    code = process.exitcode
                self._death = WorkerDeath(
                    kind=kind, exit_code=code, memory_evidence=evidence, reason=reason
                )
        self._kill_and_clean_up()

    def terminate(self, reason: str) -> None:
        """End the worker now, whatever it is doing; idempotent and safe from any thread."""
        with self._state_lock:
            if self._death is None:
                self._death = WorkerDeath(
                    kind="terminated", exit_code=None, memory_evidence="none", reason=reason
                )
        self._kill_and_clean_up()

    def _kill_and_clean_up(self) -> None:
        with self._state_lock:
            if self._cleaned_up:
                return
            self._cleaned_up = True
            process = self._process
        try:
            if process is not None and process.pid is not None:
                if process.is_alive():
                    _terminate_process(process)
                process.join(timeout=2.0)
                cleanup_private_cgroups_for_pid(process.pid)
        except BaseException as exc:
            logger.warning(
                "dedicated_worker_cleanup_failed",
                owner=self.owner,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        finally:
            for work_queue in (self._request_queue, self._result_queue):
                if work_queue is None:
                    continue
                try:
                    work_queue.close()
                    work_queue.join_thread()
                except BaseException:
                    pass
            with _REGISTRY_LOCK:
                _REGISTRY.discard(self)


def shutdown_dedicated_workers() -> None:
    """Refuse new workers and end every live one (server shutdown and interpreter exit)."""
    global _SHUTTING_DOWN
    with _REGISTRY_LOCK:
        _SHUTTING_DOWN = True
        workers = list(_REGISTRY)
    for worker in workers:
        worker.terminate("cancelled")


def open_dedicated_workers() -> None:
    """Accept new workers (server startup; a later lifespan in the same process reopens)."""
    global _SHUTTING_DOWN
    with _REGISTRY_LOCK:
        _SHUTTING_DOWN = False


atexit.register(shutdown_dedicated_workers)
