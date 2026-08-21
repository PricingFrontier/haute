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

from haute._env import int_env
from haute._logging import get_logger
from haute._native_memory_limit import (
    NativeMemoryLease,
    cleanup_private_cgroups_for_pid,
    native_memory_backend_scope,
    native_memory_caps_supported,
)
from haute._process_memory import process_rss_bytes
from haute._worker_isolation import (
    IsolatedWorkerError,
    IsolatedWorkerHostError,
    IsolatedWorkerTerminationError,
    WorkerTerminalReason,
    _terminate_process,
    create_worker_queue,
)

logger = get_logger(component="interactive_workers")

T = TypeVar("T")
InteractiveExecutionMode = Literal["process", "thread"]

_MODE_ENV = "HAUTE_INTERACTIVE_EXECUTION_MODE"
_COUNT_ENV = "HAUTE_INTERACTIVE_WORKER_COUNT"
_START_TIMEOUT_SECONDS = 30.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05


def resolve_interactive_execution_mode() -> InteractiveExecutionMode:
    raw = os.environ.get(_MODE_ENV, "process")
    mode = raw.strip().lower()
    if mode not in {"process", "thread"}:
        raise RuntimeError(f"{_MODE_ENV} must be 'process' or 'thread'")
    return cast(InteractiveExecutionMode, mode)


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
    def __init__(self, exitcode: int | None) -> None:
        detail = "" if exitcode is None else f" (exit code {exitcode})"
        super().__init__(
            f"Interactive worker stopped before returning a result{detail}",
            terminal_reason="error",
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
    lock: threading.Lock
    generation: int
    closed: bool = False


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
) -> None:
    lease = NativeMemoryLease()
    try:
        for module_name in preload_modules:
            importlib.import_module(module_name)
        result_queue.put(pickle.dumps(("ready", os.getpid()), protocol=pickle.HIGHEST_PROTOCOL))
        while True:
            raw_request = request_queue.get()
            request = pickle.loads(raw_request)
            if request == ("shutdown",):
                return
            if not isinstance(request, tuple) or len(request) != 7 or request[0] != "run":
                raise RuntimeError("interactive worker received a malformed request")
            _kind, job_id, function, args, kwargs, native_growth, native_required = request
            try:
                if native_growth is not None:
                    lease.apply(native_growth, required=native_required)
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
                backend = lease.backend
                run_function = True
            with native_memory_backend_scope(backend):
                if run_function:
                    try:
                        value = function(*args, **kwargs)
                        envelope = ("result", job_id, "ok", value)
                    except BaseException as exc:
                        envelope = (
                            "result",
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


class InteractiveWorkerPool:
    """Fixed-size affinity pool whose failed tasks kill their owning process."""

    def __init__(
        self,
        *,
        size: int,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        preload_modules: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("interactive worker pool size must be a positive integer")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if any(not isinstance(name, str) or not name for name in preload_modules):
            raise ValueError("preload_modules must contain non-empty module names")
        self._size = size
        self._poll_interval_seconds = poll_interval_seconds
        self._preload_modules = preload_modules
        self._ctx = mp.get_context("spawn")
        self._state_lock = threading.RLock()
        self._slots: list[_WorkerSlot] = []
        self._closed = False
        self._shutdown_event = threading.Event()

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
            with slot.lock:
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
        **kwargs: Any,
    ) -> T:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
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
        self.start()
        slot = self._slot_for_affinity(affinity_key)
        while not slot.lock.acquire(timeout=self._poll_interval_seconds):
            if stop_reason is not None:
                reason = stop_reason()
                if reason is not None:
                    raise InteractiveWorkerStoppedError(reason)
        try:
            with self._state_lock:
                if self._closed:
                    raise RuntimeError("interactive worker pool is closed")
            job_id = uuid.uuid4().hex
            if memory_growth_limit_bytes is not None:
                baseline_rss = process_rss_bytes(cast(int, slot.process.pid))
                if baseline_rss is None:
                    if require_memory_limit:
                        # Native enforcement remains valid; RSS is only a
                        # secondary watchdog and must not reject this request.
                        absolute_rss_limit_bytes = None
                    logger.warning(
                        "interactive_worker_rss_baseline_unavailable",
                        worker_index=slot.index,
                    )
                else:
                    growth_cap = baseline_rss + memory_growth_limit_bytes
                    absolute_rss_limit_bytes = (
                        growth_cap
                        if absolute_rss_limit_bytes is None
                        else min(absolute_rss_limit_bytes, growth_cap)
                    )
            request = (
                "run",
                job_id,
                function,
                args,
                kwargs,
                memory_growth_limit_bytes,
                require_memory_limit,
            )
            try:
                serialised = pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL)
            except BaseException as exc:
                raise InteractiveWorkerProtocolError(
                    f"Interactive worker request is not serialisable: {exc}",
                    terminal_reason="contract_error",
                ) from exc
            slot.request_queue.put(serialised)
            return cast(
                T,
                self._wait_for_result(
                    slot,
                    job_id=job_id,
                    timeout_seconds=timeout_seconds,
                    stop_reason=stop_reason,
                    absolute_rss_limit_bytes=absolute_rss_limit_bytes,
                    require_memory_limit=require_memory_limit,
                ),
            )
        finally:
            slot.lock.release()

    def _slot_for_affinity(self, affinity_key: Hashable) -> _WorkerSlot:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("interactive worker pool is closed")
            if not self._slots:
                raise RuntimeError("interactive worker pool did not start")
            digest = hashlib.sha256(repr(affinity_key).encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % len(self._slots)
            return self._slots[index]

    def _start_slot(self, *, index: int, generation: int) -> _WorkerSlot:
        request_queue = create_worker_queue(self._ctx, 1)
        result_queue = create_worker_queue(self._ctx, 1)
        process = self._ctx.Process(
            target=_interactive_worker_entrypoint,
            name=f"haute-interactive-{index}-{generation}",
            args=(request_queue, result_queue, self._preload_modules),
        )
        try:
            process.start()
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
            lock=threading.Lock(),
            generation=generation,
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
        require_memory_limit: bool,
    ) -> Any:
        deadline = time.monotonic() + timeout_seconds
        sampler_unavailable_logged = False
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
                        slot.request_queue.put(
                            pickle.dumps(("ack", job_id), protocol=pickle.HIGHEST_PROTOCOL)
                        )
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
                raise InteractiveWorkerCrashedError(exitcode)

            if stop_reason is not None:
                reason = stop_reason()
                if reason is not None:
                    self._stop_and_replace(slot)
                    raise InteractiveWorkerStoppedError(reason)

            if absolute_rss_limit_bytes is not None:
                rss = process_rss_bytes(cast(int, slot.process.pid))
                if rss is None:
                    if require_memory_limit:
                        self._stop_and_replace(slot)
                        raise RuntimeError(
                            "Interactive worker memory enforcement could not sample child RSS"
                        )
                    if not sampler_unavailable_logged:
                        sampler_unavailable_logged = True
                        logger.warning(
                            "interactive_worker_rss_unavailable",
                            worker_index=slot.index,
                        )
                elif rss > absolute_rss_limit_bytes:
                    self._stop_and_replace(slot)
                    raise InteractiveWorkerMemoryLimitError(
                        rss_bytes=rss,
                        limit_bytes=absolute_rss_limit_bytes,
                    )

    def _wait_for_release(
        self,
        slot: _WorkerSlot,
        *,
        job_id: str,
        deadline: float,
        timeout_seconds: float,
        stop_reason: Callable[[], WorkerTerminalReason | None] | None,
        absolute_rss_limit_bytes: int | None,
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
                raise InteractiveWorkerCrashedError(exitcode)
            if stop_reason is not None:
                reason = stop_reason()
                if reason is not None:
                    raise InteractiveWorkerStoppedError(reason)
            if absolute_rss_limit_bytes is not None:
                rss = process_rss_bytes(cast(int, slot.process.pid))
                if rss is None:
                    if require_memory_limit:
                        raise RuntimeError(
                            "Interactive worker memory enforcement could not sample child RSS"
                        )
                    if not sampler_unavailable_logged:
                        sampler_unavailable_logged = True
                        logger.warning(
                            "interactive_worker_rss_unavailable",
                            worker_index=slot.index,
                        )
                elif rss > absolute_rss_limit_bytes:
                    raise InteractiveWorkerMemoryLimitError(
                        rss_bytes=rss,
                        limit_bytes=absolute_rss_limit_bytes,
                    )

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
    **kwargs: Any,
) -> T:
    """Run one pool call without allowing ASGI cancellation to orphan its thread."""
    cancellation = threading.Event()

    def combined_stop_reason() -> WorkerTerminalReason | None:
        if cancellation.is_set():
            return "cancelled"
        return stop_reason() if stop_reason is not None else None

    def _run() -> T:
        return interactive_worker_pool().run(
            function,
            *args,
            affinity_key=affinity_key,
            timeout_seconds=timeout_seconds,
            stop_reason=combined_stop_reason,
            absolute_rss_limit_bytes=absolute_rss_limit_bytes,
            memory_growth_limit_bytes=memory_growth_limit_bytes,
            require_memory_limit=require_memory_limit,
            **kwargs,
        )

    task = asyncio.create_task(asyncio.to_thread(_run))
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
