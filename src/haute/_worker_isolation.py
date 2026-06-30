"""Small process-isolation primitive for heavy execution workers."""

from __future__ import annotations

import multiprocessing as mp
import queue
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing.process import BaseProcess
from typing import Any, Literal, TypeVar, cast

T = TypeVar("T")

WorkerTerminalReason = Literal[
    "completed",
    "superseded",
    "timed_out",
    "cancelled",
    "memory_limited",
    "contract_error",
    "error",
]


@dataclass(frozen=True, slots=True)
class IsolatedWorkerConfig:
    """Runtime controls for one isolated worker process."""

    timeout_seconds: float | None = None
    memory_limit_bytes: int | None = None
    require_memory_limit: bool = False
    cleanup_callbacks: tuple[Callable[[], None], ...] = field(default_factory=tuple)
    stop_reason: Callable[[], WorkerTerminalReason | None] | None = None
    stop_poll_interval_seconds: float = 0.1
    process_name: str = "haute-isolated-worker"

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.memory_limit_bytes is not None and self.memory_limit_bytes <= 0:
            raise ValueError("memory_limit_bytes must be positive")
        if self.stop_poll_interval_seconds <= 0:
            raise ValueError("stop_poll_interval_seconds must be positive")


class IsolatedWorkerError(RuntimeError):
    """Base class for typed isolated-worker failures."""

    terminal_reason: WorkerTerminalReason

    def __init__(self, message: str, *, terminal_reason: WorkerTerminalReason = "error") -> None:
        super().__init__(message)
        self.terminal_reason = terminal_reason


class IsolatedWorkerStartError(IsolatedWorkerError):
    """Raised when the parent cannot start the worker process."""


class IsolatedWorkerRemoteError(IsolatedWorkerError):
    """Raised when the child process returns a Python exception."""

    def __init__(
        self,
        *,
        remote_type: str,
        remote_message: str,
        remote_traceback: str,
    ) -> None:
        terminal_reason: WorkerTerminalReason = (
            "memory_limited" if remote_type == "MemoryError" else "error"
        )
        super().__init__(
            f"Isolated worker raised {remote_type}: {remote_message}",
            terminal_reason=terminal_reason,
        )
        self.remote_type = remote_type
        self.remote_message = remote_message
        self.remote_traceback = remote_traceback


class IsolatedWorkerCrashedError(IsolatedWorkerError):
    """Raised when the child exits without returning a result payload."""

    def __init__(self, *, exitcode: int | None, memory_limit_bytes: int | None) -> None:
        terminal_reason: WorkerTerminalReason = (
            "memory_limited"
            if _exitcode_looks_memory_limited(exitcode, memory_limit_bytes)
            else "error"
        )
        super().__init__(
            f"Isolated worker exited without a result payload (exitcode={exitcode!r})",
            terminal_reason=terminal_reason,
        )
        self.exitcode = exitcode


class IsolatedWorkerTimeoutError(IsolatedWorkerError):
    """Raised when the parent terminates a worker that exceeded its timeout."""

    def __init__(self, *, timeout_seconds: float) -> None:
        super().__init__(
            f"Isolated worker exceeded timeout of {timeout_seconds:g} seconds",
            terminal_reason="timed_out",
        )
        self.timeout_seconds = timeout_seconds


class IsolatedWorkerStoppedError(IsolatedWorkerError):
    """Raised when parent-side cancellation requests terminate the worker."""

    def __init__(self, *, terminal_reason: WorkerTerminalReason) -> None:
        if terminal_reason == "completed":
            raise ValueError("completed is not a valid stopped-worker reason")
        super().__init__(
            f"Isolated worker was stopped with reason {terminal_reason!r}",
            terminal_reason=terminal_reason,
        )


class IsolatedWorkerMemoryLimitUnsupportedError(IsolatedWorkerError):
    """Raised when a required child-process memory cap cannot be enforced."""

    def __init__(self, *, memory_limit_bytes: int) -> None:
        super().__init__(
            "Isolated worker memory caps require resource.RLIMIT_AS, "
            "which is not available on this platform.",
            terminal_reason="contract_error",
        )
        self.memory_limit_bytes = memory_limit_bytes


class IsolatedWorkerCleanupError(IsolatedWorkerError):
    """Raised when parent-owned cleanup fails after a worker finishes."""

    def __init__(self, errors: list[BaseException]) -> None:
        super().__init__(
            f"Isolated worker cleanup failed in {len(errors)} callback(s)",
            terminal_reason="error",
        )
        self.errors = tuple(errors)


def process_memory_caps_supported() -> bool:
    """Return whether this platform can enforce child address-space limits.

    macOS exposes ``resource.RLIMIT_AS`` and ``resource.setrlimit`` but
    the kernel does NOT actually enforce process address-space limits —
    any call to ``setrlimit(RLIMIT_AS, ...)`` with a finite value raises
    ``ValueError: current limit exceeds maximum limit`` even when the
    current limit is ``RLIM_INFINITY``. Treat macOS as unsupported so
    the dependent code path falls back to soft enforcement (best-effort
    only, with an explicit ``IsolatedWorkerMemoryLimitUnsupportedError``
    when the caller requires it).
    """
    try:
        import resource
    except ImportError:
        return False
    if not (hasattr(resource, "RLIMIT_AS") and hasattr(resource, "setrlimit")):
        return False
    if sys.platform == "darwin":
        return False
    return True


def run_isolated_worker(
    function: Callable[..., T],
    *args: Any,
    config: IsolatedWorkerConfig | None = None,
    **kwargs: Any,
) -> T:
    """Run ``function`` in a child process and return its pickled result.

    The parent owns cleanup callbacks and always runs them after the child
    reaches a terminal state. The child process is deliberately started via the
    ``spawn`` context so the worker does not inherit the host interpreter's
    large native heaps.
    """
    worker_config = config or IsolatedWorkerConfig()
    if (
        worker_config.require_memory_limit
        and worker_config.memory_limit_bytes is not None
        and not process_memory_caps_supported()
    ):
        raise IsolatedWorkerMemoryLimitUnsupportedError(
            memory_limit_bytes=worker_config.memory_limit_bytes,
        )

    ctx = mp.get_context("spawn")
    result_queue: mp.Queue[tuple[str, Any]] = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_isolated_worker_entrypoint,
        name=worker_config.process_name,
        args=(result_queue, function, args, kwargs, worker_config.memory_limit_bytes),
    )
    primary_error: IsolatedWorkerError | None = None
    result: T | None = None
    has_result = False
    try:
        try:
            process.start()
        except Exception as exc:  # pragma: no cover - depends on multiprocessing internals
            raise IsolatedWorkerStartError(
                f"Failed to start isolated worker: {exc}",
            ) from exc

        _wait_for_worker(process, worker_config)

        status, payload = _read_worker_payload(
            result_queue,
            exitcode=process.exitcode,
            memory_limit_bytes=worker_config.memory_limit_bytes,
        )
        if status == "ok":
            result = cast(T, payload)
            has_result = True
        elif status == "error":
            remote_type, remote_message, remote_traceback = cast(
                tuple[str, str, str],
                payload,
            )
            raise IsolatedWorkerRemoteError(
                remote_type=remote_type,
                remote_message=remote_message,
                remote_traceback=remote_traceback,
            )
        else:  # pragma: no cover - guarded by child entrypoint protocol
            raise IsolatedWorkerCrashedError(
                exitcode=process.exitcode,
                memory_limit_bytes=worker_config.memory_limit_bytes,
            )
    except IsolatedWorkerError as exc:
        primary_error = exc
    finally:
        try:
            result_queue.close()
            result_queue.join_thread()
        except Exception:
            pass

    cleanup_error = _run_cleanup_callbacks(worker_config.cleanup_callbacks)
    if primary_error is not None:
        if cleanup_error is not None:
            primary_error.add_note(str(cleanup_error))
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if has_result:
        return cast(T, result)
    raise IsolatedWorkerCrashedError(
        exitcode=process.exitcode,
        memory_limit_bytes=worker_config.memory_limit_bytes,
    )


def _isolated_worker_entrypoint(
    result_queue: mp.Queue[tuple[str, Any]],
    function: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    memory_limit_bytes: int | None,
) -> None:
    try:
        if memory_limit_bytes is not None and process_memory_caps_supported():
            _apply_address_space_limit(memory_limit_bytes)
        result_queue.put(("ok", function(*args, **kwargs)))
    except BaseException as exc:
        result_queue.put(
            (
                "error",
                (
                    type(exc).__name__,
                    str(exc),
                    "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                ),
            )
        )


def _apply_address_space_limit(memory_limit_bytes: int) -> None:
    # ``resource`` only exists on POSIX. Callers gate this via
    # ``process_memory_caps_supported`` so reaching the Windows branch here
    # means we hit a contract bug — fail loudly rather than silently no-op.
    if sys.platform == "win32":  # pragma: no cover - guarded by caller's support check
        raise IsolatedWorkerMemoryLimitUnsupportedError(memory_limit_bytes=memory_limit_bytes)
    import resource

    current_soft, current_hard = resource.getrlimit(resource.RLIMIT_AS)
    hard = memory_limit_bytes
    if current_hard != resource.RLIM_INFINITY:
        hard = min(hard, int(current_hard))
    soft = min(memory_limit_bytes, hard)
    if current_soft != resource.RLIM_INFINITY:
        soft = min(soft, int(current_soft))
    resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _read_worker_payload(
    result_queue: mp.Queue[tuple[str, Any]],
    *,
    exitcode: int | None,
    memory_limit_bytes: int | None,
) -> tuple[str, Any]:
    try:
        return result_queue.get(timeout=1.0)
    except queue.Empty as exc:
        raise IsolatedWorkerCrashedError(
            exitcode=exitcode,
            memory_limit_bytes=memory_limit_bytes,
        ) from exc


def _terminate_process(process: BaseProcess) -> None:
    process.terminate()
    process.join(timeout=2.0)
    if process.is_alive():
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
            process.join(timeout=2.0)


def _wait_for_worker(process: BaseProcess, config: IsolatedWorkerConfig) -> None:
    deadline = None if config.timeout_seconds is None else time.monotonic() + config.timeout_seconds
    while process.is_alive():
        if config.stop_reason is not None:
            reason = config.stop_reason()
            if reason is not None:
                _terminate_process(process)
                raise IsolatedWorkerStoppedError(terminal_reason=reason)
        wait_seconds = config.stop_poll_interval_seconds
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                raise IsolatedWorkerTimeoutError(
                    timeout_seconds=cast(float, config.timeout_seconds),
                )
            wait_seconds = min(wait_seconds, remaining)
        process.join(timeout=wait_seconds)


def _run_cleanup_callbacks(
    cleanup_callbacks: tuple[Callable[[], None], ...],
) -> IsolatedWorkerCleanupError | None:
    errors: list[BaseException] = []
    for callback in cleanup_callbacks:
        try:
            callback()
        except BaseException as exc:
            errors.append(exc)
    return IsolatedWorkerCleanupError(errors) if errors else None


def _exitcode_looks_memory_limited(
    exitcode: int | None,
    memory_limit_bytes: int | None,
) -> bool:
    if memory_limit_bytes is None or exitcode is None:
        return False
    try:
        import signal
    except ImportError:
        return False
    # ``SIGKILL`` is POSIX-only. ``getattr`` lets this module typecheck on
    # Windows where memory caps are unsupported anyway (callers gate via
    # ``process_memory_caps_supported``).
    sigkill = getattr(signal, "SIGKILL", None)
    if sigkill is None:
        return False
    return exitcode in {-int(sigkill), -int(signal.SIGABRT)}
