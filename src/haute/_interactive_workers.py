"""Warm, killable spawn workers for interactive preview and trace execution."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import multiprocessing as mp
import os
import pickle
import queue
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any, Literal, TypeVar, cast

from haute._cpu_performance import configure_process_high_qos
from haute._env import int_env
from haute._logging import get_logger
from haute._native_memory_limit import (
    NativeMemoryLease,
    cleanup_private_cgroups_for_pid,
    memory_error_for_thread_start_failure,
    native_memory_backend_scope,
    native_memory_caps_supported,
)
from haute._parent_watch import exit_with_parent
from haute._polars_utils import current_streaming_chunk_size, set_streaming_chunk_size
from haute._process_memory import process_rss_bytes
from haute._step_progress import ProgressCell, StepProgress, bind_job_progress
from haute._worker_isolation import (
    IsolatedWorkerError,
    IsolatedWorkerHostError,
    IsolatedWorkerTerminationError,
    WorkerTerminalReason,
    _exitcode_looks_memory_limited,
    _terminate_process,
    create_worker_queue,
    start_process_with_environment,
)

logger = get_logger(component="interactive_workers")

T = TypeVar("T")
InteractiveExecutionMode = Literal["process", "thread"]

_MODE_ENV = "HAUTE_INTERACTIVE_EXECUTION_MODE"
_COUNT_ENV = "HAUTE_INTERACTIVE_WORKER_COUNT"
_POLARS_THREADS_ENV = "HAUTE_INTERACTIVE_POLARS_THREADS"
_DEFAULT_INTERACTIVE_POLARS_THREADS = 8
_START_TIMEOUT_SECONDS = 30.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05


def resolve_interactive_execution_mode() -> InteractiveExecutionMode:
    raw = os.environ.get(_MODE_ENV, "process")
    mode = raw.strip().lower()
    if mode not in {"process", "thread"}:
        raise RuntimeError(f"{_MODE_ENV} must be 'process' or 'thread'")
    return cast(InteractiveExecutionMode, mode)


def resolve_interactive_polars_threads() -> int:
    """Return the Polars thread-pool size each interactive worker is capped to.

    Previews are latency-bound by fixed per-request costs, not by join width,
    while Polars' streaming join reserves build-side memory per thread — so on
    a many-core host the extra threads buy committed memory rather than speed
    and push the worker past its native cap. Several interactive workers also
    share the host with each other and the server, so a full-core pool in each
    oversubscribes regardless.
    """
    return int_env(
        _POLARS_THREADS_ENV,
        min(os.cpu_count() or 1, _DEFAULT_INTERACTIVE_POLARS_THREADS),
    )


class InteractiveWorkerError(IsolatedWorkerError):
    """Base class for warm interactive-worker failures."""


class InteractiveWorkerStartError(InteractiveWorkerError):
    """Raised when a warm worker cannot be started and readied."""


class InteractiveWorkerProtocolError(InteractiveWorkerError):
    """Raised when a worker returns a malformed or stale envelope."""


class InteractiveWorkerTimeoutError(InteractiveWorkerError):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            f"Interactive execution exceeded its {timeout_seconds:g} second limit",
            terminal_reason="timed_out",
        )
        self.timeout_seconds = timeout_seconds


class InteractiveWorkerStoppedError(InteractiveWorkerError):
    def __init__(self, reason: WorkerTerminalReason) -> None:
        if reason == "completed":
            raise ValueError("completed is not a valid stop reason")
        super().__init__(
            f"Interactive execution was stopped with reason {reason!r}",
            terminal_reason=reason,
        )


class InteractiveWorkerBusyError(InteractiveWorkerError):
    """A pre-emptible request was refused rather than waiting for its slot.

    The slot was held, its worker was starting or not running, or an
    interactive request was already waiting for it.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"The interactive worker is busy: {reason}", terminal_reason="superseded")


class InteractiveWorkerPreemptedError(InteractiveWorkerError):
    """A pre-emptible request was stopped because an interactive request needed its slot.

    Raised only after its worker was terminated, confirmed dead and replaced,
    so the waiting request runs next on the replacement.
    """

    def __init__(self) -> None:
        super().__init__(
            "Interactive execution was stopped because an editor request needed its worker",
            terminal_reason="superseded",
        )


class InteractiveWorkerMemoryLimitError(InteractiveWorkerError, MemoryError):
    def __init__(self, *, rss_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"Interactive worker exceeded its RSS watchdog limit: {rss_bytes} bytes used > "
            f"{limit_bytes} bytes allowed",
            terminal_reason="memory_limited",
        )
        self.rss_bytes = rss_bytes
        self.limit_bytes = limit_bytes

    def to_payload(self) -> dict[str, object]:
        return {
            "error_code": "memory_limit",
            "rss_bytes": self.rss_bytes,
            "rss_limit_bytes": self.limit_bytes,
            "reason": "worker_rss_limit_exceeded",
        }


class InteractiveWorkerCrashedError(InteractiveWorkerError):
    """A pool worker exited without a result.

    ``memory_limited`` applies the same parent-side exit-code heuristic as the
    one-shot isolated worker: a SIGKILL/SIGABRT-shaped exit under a configured
    native growth cap is reported as a hedged memory outcome, never a generic
    internal failure.
    """

    def __init__(self, exitcode: int | None, *, memory_limited: bool = False) -> None:
        detail = "" if exitcode is None else f" (exit code {exitcode})"
        message = (
            f"Interactive worker may have run out of memory{detail}"
            if memory_limited
            else f"Interactive worker stopped before returning a result{detail}"
        )
        super().__init__(
            message,
            terminal_reason="memory_limited" if memory_limited else "error",
        )
        self.exitcode = exitcode


class InteractiveWorkerRemoteError(InteractiveWorkerError):
    def __init__(
        self,
        *,
        remote_type: str,
        remote_module: str,
        remote_message: str,
        remote_traceback: str,
        public_payload: dict[str, object] | None,
    ) -> None:
        terminal_reason: WorkerTerminalReason = (
            "contract_error"
            if remote_type.startswith("NativeMemoryLimit")
            else "memory_limited"
            if remote_type.endswith("MemoryError")
            else "error"
        )
        super().__init__(
            f"Interactive worker raised {remote_type}: {remote_message}",
            terminal_reason=terminal_reason,
        )
        self.remote_type = remote_type
        self.remote_module = remote_module
        self.remote_message = remote_message
        self.remote_traceback = remote_traceback
        self.public_payload = public_payload


@dataclass(slots=True)
class _WorkerSlot:
    index: int
    request_queue: Any
    result_queue: Any
    process: BaseProcess
    generation: int
    # The running job's step progress, written by the worker (see _step_progress).
    progress: ProgressCell
    closed: bool = False


@dataclass(slots=True)
class _PreemptibleJob:
    """A pre-emptible request running on a slot index; a waiting request marks it."""

    preempted: bool = False


def _public_exception_payload(exc: BaseException) -> dict[str, object] | None:
    to_payload = getattr(exc, "to_payload", None)
    if not callable(to_payload):
        return None
    try:
        payload = to_payload()
        pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    except BaseException:
        return None
    if not isinstance(payload, dict):
        return None
    return {str(key): value for key, value in payload.items()}


def _interactive_worker_entrypoint(
    request_queue: Any,
    result_queue: Any,
    preload_modules: tuple[str, ...],
    progress_cell: ProgressCell | None = None,
) -> None:
    exit_with_parent()
    lease = NativeMemoryLease()
    try:
        configure_process_high_qos()
        for module_name in preload_modules:
            importlib.import_module(module_name)
        result_queue.put(pickle.dumps(("ready", os.getpid()), protocol=pickle.HIGHEST_PROTOCOL))
        while True:
            raw_request = request_queue.get()
            request = pickle.loads(raw_request)
            if request == ("shutdown",):
                return
            if not isinstance(request, tuple) or len(request) != 8 or request[0] != "run":
                raise RuntimeError("interactive worker received a malformed request")
            (
                _kind,
                job_id,
                function,
                args,
                kwargs,
                native_growth,
                native_required,
                streaming_chunk_size,
            ) = request
            # A warm worker outlives the server's setting: each task carries the
            # value the server runs with when it hands the task over.
            set_streaming_chunk_size(streaming_chunk_size)
            applied = False
            try:
                if native_growth is not None:
                    applied = lease.apply(native_growth, required=native_required)
            except BaseException as exc:
                envelope = (
                    "result",
                    job_id,
                    "error",
                    (
                        type(exc).__name__,
                        type(exc).__module__,
                        str(exc),
                        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                        _public_exception_payload(exc),
                    ),
                )
                backend = None
                run_function = False
            else:
                # Only a cap installed by THIS request is evidence for it.
                backend = lease.backend if applied else None
                run_function = True
            with native_memory_backend_scope(backend, lease):
                if run_function:
                    try:
                        with bind_job_progress(progress_cell, job_id):
                            value = function(*args, **kwargs)
                        envelope = ("result", job_id, "ok", value)
                    except BaseException as raised:
                        failure = (
                            memory_error_for_thread_start_failure(raised) if applied else raised
                        )
                        envelope = (
                            "result",
                            job_id,
                            "error",
                            (
                                type(failure).__name__,
                                type(failure).__module__,
                                str(failure),
                                "".join(
                                    traceback.format_exception(
                                        type(failure), failure, failure.__traceback__
                                    )
                                ),
                                _public_exception_payload(failure),
                            ),
                        )
                # Serialise synchronously so an unpicklable return value becomes a
                # deterministic remote error instead of dying in Queue's feeder.
                try:
                    payload = pickle.dumps(envelope, protocol=pickle.HIGHEST_PROTOCOL)
                except BaseException as exc:
                    payload = pickle.dumps(
                        (
                            "result",
                            job_id,
                            "error",
                            (
                                type(exc).__name__,
                                type(exc).__module__,
                                f"worker result was not serialisable: {exc}",
                                "".join(
                                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                                ),
                                None,
                            ),
                        ),
                        protocol=pickle.HIGHEST_PROTOCOL,
                    )
                result_queue.put(payload)
                raw_ack = request_queue.get()
                acknowledgement = pickle.loads(raw_ack)
                if acknowledgement != ("ack", job_id):
                    raise RuntimeError(
                        "interactive worker received a malformed or stale acknowledgement"
                    )
            try:
                if native_growth is not None:
                    lease.restore()
            except BaseException as exc:
                result_queue.put(
                    pickle.dumps(
                        (
                            "released",
                            job_id,
                            "error",
                            (
                                type(exc).__name__,
                                type(exc).__module__,
                                str(exc),
                                "".join(
                                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                                ),
                                _public_exception_payload(exc),
                            ),
                        ),
                        protocol=pickle.HIGHEST_PROTOCOL,
                    )
                )
                raise
            result_queue.put(
                pickle.dumps(
                    ("released", job_id, "ok", None),
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            )
    except BaseException:
        # The parent classifies an entrypoint/protocol failure from exitcode;
        # trying to use a possibly broken queue here can hide the original exit.
        raise
    finally:
        lease.close()


def _validate_memory_limits(
    *,
    absolute_rss_limit_bytes: int | None,
    memory_growth_limit_bytes: int | None,
    require_memory_limit: bool,
) -> None:
    if absolute_rss_limit_bytes is not None and absolute_rss_limit_bytes <= 0:
        raise ValueError("absolute_rss_limit_bytes must be positive")
    if memory_growth_limit_bytes is not None and memory_growth_limit_bytes <= 0:
        raise ValueError("memory_growth_limit_bytes must be positive")
    if require_memory_limit and memory_growth_limit_bytes is None:
        raise ValueError("required memory enforcement needs a native memory growth limit")
    if (
        require_memory_limit
        and memory_growth_limit_bytes is not None
        and not native_memory_caps_supported()
    ):
        raise RuntimeError("Interactive worker native memory caps are unavailable on this host")


class InteractiveWorkerPool:
    """Fixed-size affinity pool whose failed tasks kill their owning process.

    Each slot index has one scheduling lock, created with the pool and kept
    across worker generations: a request holds it while its job runs, and a
    replacement happens while the job that ended the old worker still holds
    it, so a request that waited through a replacement submits to the
    replacement. Interactive requests wait for the lock and are counted while
    they wait; a pre-emptible request (``run_preemptible``) never waits, is
    refused while one is counted, and is stopped when one arrives.
    """

    def __init__(
        self,
        *,
        size: int,
        polars_threads: int,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        preload_modules: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("interactive worker pool size must be a positive integer")
        if (
            not isinstance(polars_threads, int)
            or isinstance(polars_threads, bool)
            or polars_threads <= 0
        ):
            raise ValueError("interactive worker polars_threads must be a positive integer")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if any(not isinstance(name, str) or not name for name in preload_modules):
            raise ValueError("preload_modules must contain non-empty module names")
        self._size = size
        self._polars_threads = polars_threads
        self._poll_interval_seconds = poll_interval_seconds
        self._preload_modules = preload_modules
        self._ctx = mp.get_context("spawn")
        self._state_lock = threading.RLock()
        self._slots: list[_WorkerSlot] = []
        self._closed = False
        self._shutdown_event = threading.Event()
        # Per slot index, stable across worker generations (see the class docstring).
        self._scheduling_locks = tuple(threading.Lock() for _ in range(size))
        # Interactive requests waiting for each index's scheduling lock; read
        # and written only under ``_state_lock``.
        self._pending = [0] * size
        self._preemptible: dict[int, _PreemptibleJob] = {}

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("interactive worker pool is closed")
            if self._slots:
                return
            started: list[_WorkerSlot] = []
            try:
                for index in range(self._size):
                    started.append(self._start_slot(index=index, generation=1))
            except BaseException:
                for slot in started:
                    self._close_slot(slot, graceful=False)
                raise
            self._slots = started

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._shutdown_event.set()
            slots, self._slots = self._slots, []
        errors: list[BaseException] = []
        for slot in slots:
            with self._scheduling_locks[slot.index]:
                try:
                    self._close_slot(slot, graceful=True)
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            first, *rest = errors
            for error in rest:
                first.add_note(f"Additional worker shutdown failure: {error}")
            raise first

    def run(
        self,
        function: Callable[..., T],
        *args: Any,
        affinity_key: Hashable,
        timeout_seconds: float,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
        absolute_rss_limit_bytes: int | None = None,
        memory_growth_limit_bytes: int | None = None,
        require_memory_limit: bool = False,
        on_progress: Callable[[StepProgress], None] | None = None,
        **kwargs: Any,
    ) -> T:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        _validate_memory_limits(
            absolute_rss_limit_bytes=absolute_rss_limit_bytes,
            memory_growth_limit_bytes=memory_growth_limit_bytes,
            require_memory_limit=require_memory_limit,
        )
        self.start()
        index = self._index_for_affinity(affinity_key)
        self._wait_for_scheduling_lock(index, stop_reason)
        try:
            slot = self._current_slot(index)
            job_id = uuid.uuid4().hex
            absolute_rss_limit_bytes = self._absolute_rss_limit(
                slot,
                absolute_rss_limit_bytes=absolute_rss_limit_bytes,
                memory_growth_limit_bytes=memory_growth_limit_bytes,
                require_memory_limit=require_memory_limit,
            )
            serialised = self._serialised_request(
                job_id,
                function,
                args,
                kwargs,
                memory_growth_limit_bytes=memory_growth_limit_bytes,
                require_memory_limit=require_memory_limit,
            )
            slot.request_queue.put(serialised)
            return cast(
                T,
                self._wait_for_result(
                    slot,
                    job_id=job_id,
                    timeout_seconds=timeout_seconds,
                    stop_reason=stop_reason,
                    absolute_rss_limit_bytes=absolute_rss_limit_bytes,
                    memory_growth_limit_bytes=memory_growth_limit_bytes,
                    require_memory_limit=require_memory_limit,
                    on_progress=on_progress,
                ),
            )
        finally:
            self._scheduling_locks[index].release()

    def run_preemptible(
        self,
        function: Callable[..., T],
        *args: Any,
        affinity_key: Hashable,
        deadline: float,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
        absolute_rss_limit_bytes: int | None = None,
        memory_growth_limit_bytes: int | None = None,
        require_memory_limit: bool = False,
        **kwargs: Any,
    ) -> T:
        """Run one job only if its slot is free now, yielding it to any interactive request.

        Never starts the pool and never waits: a pool that has not started,
        a held or not-running slot, and a slot an interactive request is
        waiting for all raise :class:`InteractiveWorkerBusyError`. While it
        runs, an interactive request that asks for the slot stops it: its
        worker is terminated, confirmed dead and replaced before the slot is
        released, and :class:`InteractiveWorkerPreemptedError` is raised.
        *deadline* is absolute (``time.monotonic()``); it bounds the
        submission, the result wait and the release acknowledgement alike.
        """
        _validate_memory_limits(
            absolute_rss_limit_bytes=absolute_rss_limit_bytes,
            memory_growth_limit_bytes=memory_growth_limit_bytes,
            require_memory_limit=require_memory_limit,
        )
        timeout_seconds = deadline - time.monotonic()
        if timeout_seconds <= 0:
            raise InteractiveWorkerTimeoutError(0.0)
        index, slot, job = self._acquire_preemptible(affinity_key)
        try:
            job_id = uuid.uuid4().hex
            absolute_rss_limit_bytes = self._absolute_rss_limit(
                slot,
                absolute_rss_limit_bytes=absolute_rss_limit_bytes,
                memory_growth_limit_bytes=memory_growth_limit_bytes,
                require_memory_limit=require_memory_limit,
            )
            serialised = self._serialised_request(
                job_id,
                function,
                args,
                kwargs,
                memory_growth_limit_bytes=memory_growth_limit_bytes,
                require_memory_limit=require_memory_limit,
            )
            self._submit_before(
                slot, serialised, deadline=deadline, timeout_seconds=timeout_seconds
            )
            return cast(
                T,
                self._wait_for_result(
                    slot,
                    job_id=job_id,
                    deadline=deadline,
                    timeout_seconds=timeout_seconds,
                    stop_reason=stop_reason,
                    absolute_rss_limit_bytes=absolute_rss_limit_bytes,
                    memory_growth_limit_bytes=memory_growth_limit_bytes,
                    require_memory_limit=require_memory_limit,
                    preempted=lambda: job.preempted,
                ),
            )
        finally:
            with self._state_lock:
                if self._preemptible.get(index) is job:
                    del self._preemptible[index]
            self._scheduling_locks[index].release()

    def _index_for_affinity(self, affinity_key: Hashable) -> int:
        digest = hashlib.sha256(repr(affinity_key).encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % self._size

    def _wait_for_scheduling_lock(
        self,
        index: int,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None,
    ) -> None:
        """Wait for *index*'s scheduling lock, counted as pending until it is held.

        Arriving marks a pre-emptible job running on the index, and the count
        refuses any pre-emptible request until this one holds the lock, so it
        runs next on the replacement worker.
        """
        lock = self._scheduling_locks[index]
        with self._state_lock:
            self._pending[index] += 1
            running = self._preemptible.get(index)
            if running is not None:
                running.preempted = True
        try:
            while not lock.acquire(timeout=self._poll_interval_seconds):
                if stop_reason is not None:
                    reason = stop_reason()
                    if reason is not None:
                        raise InteractiveWorkerStoppedError(reason)
        finally:
            with self._state_lock:
                self._pending[index] -= 1

    def _acquire_preemptible(
        self, affinity_key: Hashable
    ) -> tuple[int, _WorkerSlot, _PreemptibleJob]:
        """Take the affinity slot's scheduling lock without waiting, and register the job.

        Acquisition and registration share one critical section, so an
        interactive request arriving at any later moment finds the job to mark.
        """
        index = self._index_for_affinity(affinity_key)
        lock = self._scheduling_locks[index]
        with self._state_lock:
            if self._closed or not self._slots:
                raise InteractiveWorkerBusyError("the worker pool is not running")
            if self._pending[index] > 0:
                raise InteractiveWorkerBusyError("an editor request is waiting for the worker")
            if not lock.acquire(blocking=False):
                raise InteractiveWorkerBusyError("the worker is running another request")
            slot = self._slots[index]
            if slot.closed:
                lock.release()
                raise InteractiveWorkerBusyError("the worker is not running")
            job = _PreemptibleJob()
            self._preemptible[index] = job
        return index, slot, job

    def _current_slot(self, index: int) -> _WorkerSlot:
        """The open slot at *index*, read once its scheduling lock is held."""
        with self._state_lock:
            if self._closed:
                raise RuntimeError("interactive worker pool is closed")
            if not self._slots:
                raise RuntimeError("interactive worker pool did not start")
            slot = self._slots[index]
            if slot.closed:
                raise InteractiveWorkerStartError(
                    f"Interactive worker {index} is not running: its replacement did not start",
                    terminal_reason="error",
                )
            return slot

    @staticmethod
    def _absolute_rss_limit(
        slot: _WorkerSlot,
        *,
        absolute_rss_limit_bytes: int | None,
        memory_growth_limit_bytes: int | None,
        require_memory_limit: bool,
    ) -> int | None:
        if memory_growth_limit_bytes is None:
            return absolute_rss_limit_bytes
        baseline_rss = process_rss_bytes(cast(int, slot.process.pid))
        if baseline_rss is None:
            logger.warning(
                "interactive_worker_rss_baseline_unavailable",
                worker_index=slot.index,
            )
            # Native enforcement remains valid; RSS is only a secondary
            # watchdog and must not reject this request.
            return None if require_memory_limit else absolute_rss_limit_bytes
        growth_cap = baseline_rss + memory_growth_limit_bytes
        return (
            growth_cap
            if absolute_rss_limit_bytes is None
            else min(absolute_rss_limit_bytes, growth_cap)
        )

    @staticmethod
    def _serialised_request(
        job_id: str,
        function: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        memory_growth_limit_bytes: int | None,
        require_memory_limit: bool,
    ) -> bytes:
        request = (
            "run",
            job_id,
            function,
            args,
            kwargs,
            memory_growth_limit_bytes,
            require_memory_limit,
            current_streaming_chunk_size(),
        )
        try:
            return pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL)
        except BaseException as exc:
            raise InteractiveWorkerProtocolError(
                f"Interactive worker request is not serialisable: {exc}",
                terminal_reason="contract_error",
            ) from exc

    def _submit_before(
        self, slot: _WorkerSlot, serialised: bytes, *, deadline: float, timeout_seconds: float
    ) -> None:
        """Hand the request to the worker's queue, or replace the worker at the deadline."""
        try:
            slot.request_queue.put(serialised, timeout=max(deadline - time.monotonic(), 0.0))
        except queue.Full:
            self._stop_and_replace(slot)
            raise InteractiveWorkerTimeoutError(timeout_seconds) from None

    def _start_slot(self, *, index: int, generation: int) -> _WorkerSlot:
        request_queue = create_worker_queue(self._ctx, 1)
        result_queue = create_worker_queue(self._ctx, 1)
        # A replaced slot gets a fresh progress cell: a worker killed while
        # holding the old cell's lock leaves it behind with the old process.
        progress = ProgressCell(self._ctx)
        process = self._ctx.Process(
            target=_interactive_worker_entrypoint,
            name=f"haute-interactive-{index}-{generation}",
            args=(request_queue, result_queue, self._preload_modules, progress),
        )
        try:
            # Polars reads POLARS_MAX_THREADS only at import, which happens
            # during the child's module import — before its entrypoint runs —
            # so the cap has to be in the environment the child inherits.
            start_process_with_environment(
                process,
                {"POLARS_MAX_THREADS": str(self._polars_threads)},
            )
            self._wait_for_ready(process, result_queue)
        except IsolatedWorkerHostError:
            self._close_unstarted_queues(request_queue, result_queue)
            raise
        except BaseException as exc:
            if process.pid is not None:
                try:
                    _terminate_process(process)
                except BaseException as termination_exc:
                    exc.add_note(f"worker termination failed: {termination_exc}")
            self._close_unstarted_queues(request_queue, result_queue)
            raise InteractiveWorkerStartError(
                f"Failed to start interactive worker {index}: {exc}",
                terminal_reason="error",
            ) from exc
        return _WorkerSlot(
            index=index,
            request_queue=request_queue,
            result_queue=result_queue,
            process=process,
            generation=generation,
            progress=progress,
        )

    def _wait_for_ready(self, process: BaseProcess, result_queue: Any) -> None:
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Interactive worker did not become ready within "
                    f"{_START_TIMEOUT_SECONDS:g} seconds"
                )
            try:
                raw_ready = result_queue.get(timeout=min(self._poll_interval_seconds, remaining))
            except queue.Empty:
                if not process.is_alive():
                    raise InteractiveWorkerCrashedError(process.exitcode) from None
                continue
            ready = pickle.loads(raw_ready)
            if ready != ("ready", process.pid):
                raise InteractiveWorkerProtocolError(
                    "Interactive worker returned an invalid ready envelope",
                    terminal_reason="contract_error",
                )
            return

    @staticmethod
    def _close_unstarted_queues(*queues: Any) -> None:
        for work_queue in queues:
            try:
                work_queue.close()
            finally:
                work_queue.join_thread()

    def _replace_slot(self, slot: _WorkerSlot) -> None:
        self._close_slot(slot, graceful=False)
        replacement = self._start_slot(index=slot.index, generation=slot.generation + 1)
        with self._state_lock:
            if self._closed:
                self._close_slot(replacement, graceful=False)
                raise RuntimeError("interactive worker pool closed during replacement")
            current = self._slots[slot.index]
            if current is not slot:
                self._close_slot(replacement, graceful=False)
                raise InteractiveWorkerProtocolError(
                    "Interactive worker slot generation changed unexpectedly",
                    terminal_reason="contract_error",
                )
            self._slots[slot.index] = replacement

    def _stop_and_replace(self, slot: _WorkerSlot) -> None:
        self._replace_slot(slot)

    def _wait_for_result(
        self,
        slot: _WorkerSlot,
        *,
        job_id: str,
        timeout_seconds: float,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None,
        absolute_rss_limit_bytes: int | None,
        memory_growth_limit_bytes: int | None,
        require_memory_limit: bool,
        on_progress: Callable[[StepProgress], None] | None = None,
        deadline: float | None = None,
        preempted: Callable[[], bool] | None = None,
    ) -> Any:
        """Wait for the job's result and release, until *deadline* (``time.monotonic()``).

        Without a *deadline* the wait has *timeout_seconds* from now; with one,
        *timeout_seconds* is the budget it was set from, for the timeout's
        message. *preempted* stops a pre-emptible job.
        """
        if deadline is None:
            deadline = time.monotonic() + timeout_seconds
        sampler_unavailable_logged = False
        progress_sequence = 0
        while True:
            if self._shutdown_event.is_set():
                self._close_slot(slot, graceful=False)
                raise InteractiveWorkerStoppedError("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_and_replace(slot)
                raise InteractiveWorkerTimeoutError(timeout_seconds)
            try:
                raw_result = slot.result_queue.get(
                    timeout=min(self._poll_interval_seconds, remaining)
                )
            except queue.Empty:
                raw_result = None

            if on_progress is not None and raw_result is None:
                # Never waits: a held lock (or a worker that died holding it)
                # only skips this poll's reading.
                update = slot.progress.try_read(job_id)
                if update is not None and update[0] > progress_sequence:
                    progress_sequence = update[0]
                    on_progress(update[1])

            if raw_result is not None:
                try:
                    envelope = pickle.loads(raw_result)
                    result: Any = None
                    user_error: InteractiveWorkerRemoteError | None = None
                    try:
                        result = self._interpret_result(slot, job_id=job_id, envelope=envelope)
                    except InteractiveWorkerRemoteError as exc:
                        user_error = exc
                    try:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise InteractiveWorkerTimeoutError(timeout_seconds)
                        slot.request_queue.put(
                            pickle.dumps(("ack", job_id), protocol=pickle.HIGHEST_PROTOCOL),
                            timeout=remaining,
                        )
                    except InteractiveWorkerTimeoutError as timed_out:
                        if user_error is not None:
                            user_error.add_note(f"Worker release confirmation failed: {timed_out}")
                            raise user_error from None
                        raise
                    except BaseException as exc:
                        release_error = InteractiveWorkerProtocolError(
                            f"Interactive worker acknowledgement could not be sent: {exc}",
                            terminal_reason="contract_error",
                        )
                        if user_error is not None:
                            user_error.add_note(
                                f"Worker release confirmation failed: {release_error}"
                            )
                            raise user_error
                        raise release_error from exc
                    try:
                        self._wait_for_release(
                            slot,
                            job_id=job_id,
                            deadline=deadline,
                            timeout_seconds=timeout_seconds,
                            stop_reason=stop_reason,
                            absolute_rss_limit_bytes=absolute_rss_limit_bytes,
                            memory_growth_limit_bytes=memory_growth_limit_bytes,
                            require_memory_limit=require_memory_limit,
                            sampler_unavailable_logged=sampler_unavailable_logged,
                        )
                    except BaseException as release_error:
                        if user_error is not None:
                            user_error.add_note(
                                f"Worker release confirmation failed: {release_error}"
                            )
                            raise user_error
                        raise
                    if user_error is not None:
                        raise user_error
                    return result
                except BaseException:
                    self._stop_and_replace(slot)
                    raise

            if not slot.process.is_alive():
                exitcode = slot.process.exitcode
                self._replace_slot(slot)
                raise InteractiveWorkerCrashedError(
                    exitcode,
                    memory_limited=_exitcode_looks_memory_limited(
                        exitcode,
                        memory_growth_limit_bytes,
                    ),
                )

            if stop_reason is not None:
                reason = stop_reason()
                if reason is not None:
                    self._stop_and_replace(slot)
                    raise InteractiveWorkerStoppedError(reason)

            if preempted is not None and preempted():
                self._stop_and_replace(slot)
                raise InteractiveWorkerPreemptedError()

            try:
                sampler_unavailable_logged = self._enforce_rss_watchdog(
                    slot,
                    absolute_rss_limit_bytes=absolute_rss_limit_bytes,
                    require_memory_limit=require_memory_limit,
                    sampler_unavailable_logged=sampler_unavailable_logged,
                )
            except BaseException:
                self._stop_and_replace(slot)
                raise

    def _wait_for_release(
        self,
        slot: _WorkerSlot,
        *,
        job_id: str,
        deadline: float,
        timeout_seconds: float,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None,
        absolute_rss_limit_bytes: int | None,
        memory_growth_limit_bytes: int | None,
        require_memory_limit: bool,
        sampler_unavailable_logged: bool,
    ) -> None:
        while True:
            if self._shutdown_event.is_set():
                raise InteractiveWorkerStoppedError("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InteractiveWorkerTimeoutError(timeout_seconds)
            try:
                raw_release = slot.result_queue.get(
                    timeout=min(self._poll_interval_seconds, remaining)
                )
            except queue.Empty:
                raw_release = None
            if raw_release is not None:
                try:
                    release = pickle.loads(raw_release)
                    self._interpret_release(job_id=job_id, envelope=release)
                    return
                except BaseException:
                    raise
            if not slot.process.is_alive():
                exitcode = slot.process.exitcode
                raise InteractiveWorkerCrashedError(
                    exitcode,
                    memory_limited=_exitcode_looks_memory_limited(
                        exitcode,
                        memory_growth_limit_bytes,
                    ),
                )
            if stop_reason is not None:
                reason = stop_reason()
                if reason is not None:
                    raise InteractiveWorkerStoppedError(reason)
            sampler_unavailable_logged = self._enforce_rss_watchdog(
                slot,
                absolute_rss_limit_bytes=absolute_rss_limit_bytes,
                require_memory_limit=require_memory_limit,
                sampler_unavailable_logged=sampler_unavailable_logged,
            )

    def _enforce_rss_watchdog(
        self,
        slot: _WorkerSlot,
        *,
        absolute_rss_limit_bytes: int | None,
        require_memory_limit: bool,
        sampler_unavailable_logged: bool,
    ) -> bool:
        """Sample the worker RSS once, raising on breach or required-mode loss.

        Returns the updated log-once state; the caller decides whether a raise
        additionally replaces the slot (the result wait replaces immediately,
        the release wait defers to its caller's replacement handling).
        """
        if absolute_rss_limit_bytes is None:
            return sampler_unavailable_logged
        rss = process_rss_bytes(cast(int, slot.process.pid))
        if rss is None:
            if not slot.process.is_alive():
                # The worker died between the caller's liveness check and this
                # sample. Let the next supervision pass classify the crash
                # (including its memory-limited exit-code heuristic) instead
                # of misreporting a dead child as a lost sampler.
                return sampler_unavailable_logged
            if require_memory_limit:
                # Teardown can release the address space before is_alive()
                # observes an exit. Bound that observation gap to one poll
                # (at most 50 ms), retaining the native cap while we wait.
                slot.process.join(timeout=min(self._poll_interval_seconds, 0.05))
                if not slot.process.is_alive():
                    return sampler_unavailable_logged
                raise RuntimeError(
                    "Interactive worker memory enforcement could not sample child RSS"
                )
            if not sampler_unavailable_logged:
                logger.warning(
                    "interactive_worker_rss_unavailable",
                    worker_index=slot.index,
                )
            return True
        if rss > absolute_rss_limit_bytes:
            raise InteractiveWorkerMemoryLimitError(
                rss_bytes=rss,
                limit_bytes=absolute_rss_limit_bytes,
            )
        return sampler_unavailable_logged

    def _interpret_result(self, slot: _WorkerSlot, *, job_id: str, envelope: Any) -> Any:
        if not isinstance(envelope, tuple) or len(envelope) != 4:
            raise InteractiveWorkerProtocolError(
                "Interactive worker returned a malformed result envelope",
                terminal_reason="contract_error",
            )
        kind, returned_job_id, status, payload = envelope
        if kind != "result" or returned_job_id != job_id or status not in {"ok", "error"}:
            raise InteractiveWorkerProtocolError(
                "Interactive worker returned a stale or invalid result envelope",
                terminal_reason="contract_error",
            )
        if status == "ok":
            return payload
        if not isinstance(payload, tuple) or len(payload) != 5:
            raise InteractiveWorkerProtocolError(
                "Interactive worker returned malformed error evidence",
                terminal_reason="contract_error",
            )
        remote_type, remote_module, remote_message, remote_traceback, public_payload = payload
        raise InteractiveWorkerRemoteError(
            remote_type=str(remote_type),
            remote_module=str(remote_module),
            remote_message=str(remote_message),
            remote_traceback=str(remote_traceback),
            public_payload=(public_payload if isinstance(public_payload, dict) else None),
        )

    def _interpret_release(self, *, job_id: str, envelope: Any) -> None:
        if not isinstance(envelope, tuple) or len(envelope) != 4:
            raise InteractiveWorkerProtocolError(
                "Interactive worker returned a malformed release envelope",
                terminal_reason="contract_error",
            )
        kind, returned_job_id, status, payload = envelope
        if kind != "released" or returned_job_id != job_id or status not in {"ok", "error"}:
            raise InteractiveWorkerProtocolError(
                "Interactive worker returned a stale or invalid release envelope",
                terminal_reason="contract_error",
            )
        if status == "ok":
            if payload is not None:
                raise InteractiveWorkerProtocolError(
                    "Interactive worker returned a malformed successful release envelope",
                    terminal_reason="contract_error",
                )
            return
        if not isinstance(payload, tuple) or len(payload) != 5:
            raise InteractiveWorkerProtocolError(
                "Interactive worker returned malformed release error evidence",
                terminal_reason="contract_error",
            )
        remote_type, remote_module, remote_message, remote_traceback, public_payload = payload
        raise InteractiveWorkerRemoteError(
            remote_type=str(remote_type),
            remote_module=str(remote_module),
            remote_message=str(remote_message),
            remote_traceback=str(remote_traceback),
            public_payload=(public_payload if isinstance(public_payload, dict) else None),
        )

    @staticmethod
    def _close_slot(slot: _WorkerSlot, *, graceful: bool) -> None:
        if slot.closed:
            return
        primary: BaseException | None = None
        if graceful and slot.process.is_alive():
            try:
                slot.request_queue.put(
                    pickle.dumps(("shutdown",), protocol=pickle.HIGHEST_PROTOCOL),
                    timeout=0.5,
                )
                slot.process.join(timeout=2.0)
            except BaseException as exc:
                primary = exc
        if slot.process.is_alive():
            try:
                _terminate_process(slot.process)
            except BaseException as exc:
                if primary is None:
                    primary = exc
                else:
                    primary.add_note(f"worker termination failed: {exc}")
        try:
            slot.process.join(timeout=2.0)
        except BaseException as exc:
            if primary is None:
                primary = exc
            else:
                primary.add_note(f"worker join failed: {exc}")
        try:
            pid = getattr(slot.process, "pid", None)
            if pid is not None and not slot.process.is_alive():
                cleanup_private_cgroups_for_pid(pid)
        except BaseException as exc:
            if primary is None:
                primary = exc
            else:
                primary.add_note(f"native memory resource cleanup failed: {exc}")
        for name, work_queue in (
            ("request", slot.request_queue),
            ("result", slot.result_queue),
        ):
            try:
                work_queue.close()
                work_queue.join_thread()
            except BaseException as exc:
                if primary is None:
                    primary = exc
                else:
                    primary.add_note(f"{name} queue close failed: {exc}")
        if slot.process.is_alive() and primary is None:
            primary = IsolatedWorkerTerminationError()
        slot.closed = True
        if primary is not None:
            raise primary


_POOL_LOCK = threading.RLock()
_POOL: InteractiveWorkerPool | None = None


def interactive_worker_pool() -> InteractiveWorkerPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = InteractiveWorkerPool(
                size=int_env(_COUNT_ENV, 2),
                polars_threads=resolve_interactive_polars_threads(),
                preload_modules=("haute.routes.pipeline",),
            )
        return _POOL


def start_interactive_worker_pool() -> None:
    if resolve_interactive_execution_mode() == "process":
        interactive_worker_pool().start()


def shutdown_interactive_worker_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.close()


async def run_in_interactive_worker(
    function: Callable[..., T],
    *args: Any,
    affinity_key: Hashable,
    timeout_seconds: float,
    stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
    absolute_rss_limit_bytes: int | None = None,
    memory_growth_limit_bytes: int | None = None,
    require_memory_limit: bool = False,
    on_progress: Callable[[StepProgress], None] | None = None,
    **kwargs: Any,
) -> T:
    """Run one pool call without allowing ASGI cancellation to orphan its thread."""

    def _run(combined_stop_reason: Callable[[], WorkerTerminalReason | None]) -> T:
        return interactive_worker_pool().run(
            function,
            *args,
            affinity_key=affinity_key,
            timeout_seconds=timeout_seconds,
            stop_reason=combined_stop_reason,
            absolute_rss_limit_bytes=absolute_rss_limit_bytes,
            memory_growth_limit_bytes=memory_growth_limit_bytes,
            require_memory_limit=require_memory_limit,
            on_progress=on_progress,
            **kwargs,
        )

    return await _run_pool_call(_run, stop_reason)


async def run_preemptible_in_interactive_worker(
    function: Callable[..., T],
    *args: Any,
    affinity_key: Hashable,
    deadline: float,
    stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
    absolute_rss_limit_bytes: int | None = None,
    memory_growth_limit_bytes: int | None = None,
    require_memory_limit: bool = False,
    **kwargs: Any,
) -> T:
    """``InteractiveWorkerPool.run_preemptible`` on the shared pool, cancellation-safe.

    The pool is never started here: a pool that has not started is busy.
    """

    def _run(combined_stop_reason: Callable[[], WorkerTerminalReason | None]) -> T:
        return interactive_worker_pool().run_preemptible(
            function,
            *args,
            affinity_key=affinity_key,
            deadline=deadline,
            stop_reason=combined_stop_reason,
            absolute_rss_limit_bytes=absolute_rss_limit_bytes,
            memory_growth_limit_bytes=memory_growth_limit_bytes,
            require_memory_limit=require_memory_limit,
            **kwargs,
        )

    return await _run_pool_call(_run, stop_reason)


async def _run_pool_call(
    call: Callable[[Callable[[], WorkerTerminalReason | None]], T],
    stop_reason: Callable[[], WorkerTerminalReason | None] | None,
) -> T:
    """Run *call* in a thread; cancelling the awaiting task stops it as ``cancelled``.

    The cancellation waits for the pool call to stop its worker before it
    propagates, so no worker outlives the request that started it.
    """
    cancellation = threading.Event()

    def combined_stop_reason() -> WorkerTerminalReason | None:
        if cancellation.is_set():
            return "cancelled"
        return stop_reason() if stop_reason is not None else None

    task = asyncio.create_task(asyncio.to_thread(call, combined_stop_reason))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancellation.set()
        try:
            await asyncio.shield(task)
        except InteractiveWorkerStoppedError:
            pass
        except BaseException as exc:
            logger.error(
                "interactive_worker_cancel_cleanup_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
        raise
