"""Small process-isolation primitive for heavy execution workers."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing.process import BaseProcess
from typing import Any, Literal, TypeVar, cast

from haute._logging import get_logger

logger = get_logger(component="worker_isolation")

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
WorkerMemoryEnforcement = Literal["best_effort", "required"]
_WORKER_MEMORY_ENFORCEMENT_ENV = "HAUTE_WORKER_MEMORY_ENFORCEMENT"


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
        if self.require_memory_limit and self.memory_limit_bytes is None:
            raise ValueError("required memory enforcement needs a configured memory limit")
        if self.stop_poll_interval_seconds <= 0:
            raise ValueError("stop_poll_interval_seconds must be positive")


def resolve_worker_memory_enforcement() -> WorkerMemoryEnforcement:
    """Return the explicit hard-cap policy for isolated workers."""
    raw = os.environ.get(_WORKER_MEMORY_ENFORCEMENT_ENV, "best_effort")
    policy = raw.strip().lower()
    if policy not in {"best_effort", "required"}:
        raise RuntimeError(f"{_WORKER_MEMORY_ENFORCEMENT_ENV} must be 'best_effort' or 'required'")
    return cast(WorkerMemoryEnforcement, policy)


def worker_config_for_memory_policy(
    *,
    memory_limit_bytes: int | None,
    timeout_seconds: float | None = None,
    cleanup_callbacks: tuple[Callable[[], None], ...] = (),
    stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
    stop_poll_interval_seconds: float = 0.1,
    process_name: str = "haute-isolated-worker",
) -> IsolatedWorkerConfig:
    """Build worker controls without implying a hard cap on unsupported hosts."""
    enforcement = resolve_worker_memory_enforcement()
    if enforcement == "required" and memory_limit_bytes is None:
        raise RuntimeError(
            f"{_WORKER_MEMORY_ENFORCEMENT_ENV}='required' requires a configured memory limit"
        )
    return IsolatedWorkerConfig(
        timeout_seconds=timeout_seconds,
        memory_limit_bytes=memory_limit_bytes,
        require_memory_limit=enforcement == "required",
        cleanup_callbacks=cleanup_callbacks,
        stop_reason=stop_reason,
        stop_poll_interval_seconds=stop_poll_interval_seconds,
        process_name=process_name,
    )


class IsolatedWorkerError(RuntimeError):
    """Base class for typed isolated-worker failures."""

    terminal_reason: WorkerTerminalReason

    def __init__(self, message: str, *, terminal_reason: WorkerTerminalReason = "error") -> None:
        super().__init__(message)
        self.terminal_reason = terminal_reason


class IsolatedWorkerStartError(IsolatedWorkerError):
    """Raised when the parent cannot start the worker process."""


class IsolatedWorkerHostError(IsolatedWorkerError):
    """The host's process machinery is unusable, so no worker can start.

    Distinct from a worker that ran and failed: nothing ran at all, and
    the cause is the environment rather than the job. Specifically,
    CPython's ``multiprocessing`` resource tracker — a helper process that
    must be alive before any queue or lock can be created — has died and
    could not be replaced. Named so the failure reads as the host problem
    it is instead of an unexplained internal error.
    """


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
    """Raised when the child exits without returning a result payload.

    The message is parent-authored — a crashed child left no payload to
    curate — so it is written directly for the user rather than in
    supervisor jargon. The exit code stays on the exception (and in the
    job's ``worker_exitcode`` field) for diagnostics.
    """

    def __init__(self, *, exitcode: int | None, memory_limit_bytes: int | None) -> None:
        memory_limited = _exitcode_looks_memory_limited(exitcode, memory_limit_bytes)
        exit_detail = "" if exitcode is None else f" (exit code {exitcode})"
        if memory_limited:
            message = (
                f"The background process ran out of memory and was stopped{exit_detail}. "
                "Reduce the data size, or run on a server with more memory, then try again."
            )
        else:
            message = (
                "The background process stopped unexpectedly before returning a result"
                f"{exit_detail}. This usually means it crashed or was stopped by the "
                "operating system — check the server logs, then try again."
            )
        super().__init__(
            message,
            terminal_reason="memory_limited" if memory_limited else "error",
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


class IsolatedWorkerTerminationError(IsolatedWorkerError):
    """Raised when a child remains alive after terminate and kill attempts."""

    def __init__(self) -> None:
        super().__init__(
            "Isolated worker remained alive after terminate and kill attempts",
            terminal_reason="error",
        )


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


# ---------------------------------------------------------------------------
# Host process machinery
# ---------------------------------------------------------------------------
#
# Before any worker can start, CPython creates a multiprocessing queue, which
# creates a semaphore, which must be registered with the `resource_tracker` —
# a helper process spawned on first use whose job is to clean up leaked
# semaphores. Writing to its pipe raises BrokenPipeError once that helper is
# dead. `ensure_running()` re-spawns a tracker it has already probed and found
# dead, but it does NOT probe one it has just spawned, so a tracker that dies
# immediately on spawn surfaces as a broken pipe on the very next write.
#
# Measured on Databricks Apps (5 August 2026): three occurrences, each on the
# first queue of a training job, while ordinary in-process execution kept
# working. The diagnostics below exist to answer WHY the tracker died there —
# the recovery is bounded and loud precisely so a silent retry cannot hide it.

#: Errors that mean the tracker's pipe is gone rather than the job being bad.
_DEAD_TRACKER_ERRORS = (BrokenPipeError, ConnectionResetError)


#: Set once ``ensure_spawnable_interpreter`` has had its say.
_interpreter_checked = False


def ensure_spawnable_interpreter() -> str | None:
    """Pin multiprocessing's interpreter to an absolute, runnable path.

    Databricks Apps launches the app with a RELATIVE ``sys.executable``
    (measured: ``.venv/bin/python``). multiprocessing spawns every helper
    — the resource tracker, and each worker — by exec'ing that path, so
    the moment anything changes the working directory those spawns
    exec-fail with status 255. The first symptom is a broken pipe when
    the resource tracker is registered, which reads as an unrelated
    internal error; the real cause is a path that stopped resolving.

    Resolving it once removes the dependency on the working directory
    entirely. Called before the hosted boot changes directory (where a
    relative path still resolves), and again as a safety net at first
    worker use, where ``/proc/self/exe`` names the running interpreter
    directly for a cwd that has already moved.

    Returns the path now in force, or ``None`` when nothing was changed.
    """
    global _interpreter_checked

    from multiprocessing import spawn

    _interpreter_checked = True
    current = spawn.get_executable()
    if isinstance(current, bytes):
        current = os.fsdecode(current)
    if current and os.path.isabs(current) and os.access(current, os.X_OK):
        return None  # Already absolute and runnable — the ordinary case.

    candidate = os.path.abspath(current) if current else ""
    if not (candidate and os.access(candidate, os.X_OK)):
        # A relative path cannot be resolved once the working directory has
        # moved. The kernel still knows which binary is running.
        try:
            candidate = os.readlink("/proc/self/exe")
        except OSError:  # pragma: no cover - non-Linux hosts
            logger.warning("worker_interpreter_unresolved", executable=current)
            return None
    if not os.access(candidate, os.X_OK):  # pragma: no cover - defensive
        logger.warning("worker_interpreter_unresolved", executable=current)
        return None

    spawn.set_executable(candidate)
    logger.info("worker_interpreter_resolved", was=current, now=candidate)
    return candidate


def _resource_tracker_diagnostics() -> dict[str, Any]:
    """Facts about the host that explain a dead resource tracker.

    Every probe is individually guarded: this runs on a failure path and
    must never replace the original error with one of its own.
    """
    info: dict[str, Any] = {}
    try:
        from multiprocessing import resource_tracker

        tracker = resource_tracker._resource_tracker
        pid = getattr(tracker, "_pid", None)
        info["tracker_pid"] = pid
        info["tracker_fd"] = getattr(tracker, "_fd", None)
        if pid is not None:
            try:
                # Reaps the tracker if it has exited, which is what
                # ensure_running would do anyway, and reports how it died.
                reaped, status = os.waitpid(pid, os.WNOHANG)
                info["tracker_reaped"] = reaped
                info["tracker_exit_status"] = status
            except OSError as exc:
                info["tracker_waitpid_error"] = str(exc)
    except Exception as exc:  # pragma: no cover - private-API shape guard
        info["tracker_probe_error"] = str(exc)

    # A tracker is spawned as `sys.executable -c ...`; an interpreter that has
    # gone missing (a replaced virtualenv, say) would exec-fail instantly.
    info["executable"] = sys.executable
    try:
        info["executable_usable"] = bool(sys.executable) and os.access(sys.executable, os.X_OK)
    except OSError:  # pragma: no cover - defensive
        info["executable_usable"] = False

    try:
        import resource as _resource

        for name in ("RLIMIT_NPROC", "RLIMIT_NOFILE", "RLIMIT_AS"):
            limit = getattr(_resource, name, None)
            if limit is not None:
                info[name.lower()] = _resource.getrlimit(limit)
    except Exception:  # pragma: no cover - POSIX-only, best effort
        pass

    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(("Threads:", "VmRSS:", "VmSize:")):
                    key, _, value = line.partition(":")
                    info[f"proc_{key.strip().lower()}"] = value.strip()
    except OSError:  # pragma: no cover - Linux-only, best effort
        pass
    return info


def _reset_resource_tracker() -> None:
    """Forget a dead tracker so the next use spawns a replacement.

    ``ensure_running`` only re-spawns when its own probe fails; clearing
    the handle makes the next call take the spawn path unconditionally.
    """
    from multiprocessing import resource_tracker

    # Private CPython state by necessity: the stdlib exposes no supported way
    # to say "this tracker is gone, start another". Every attribute is read
    # defensively so a future layout change degrades to no recovery rather
    # than to a new crash on the failure path.
    tracker: Any = resource_tracker._resource_tracker
    lock = getattr(tracker, "_lock", None)
    if lock is None:  # pragma: no cover - shape guard for a future CPython
        return
    with lock:
        fd = getattr(tracker, "_fd", None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        tracker._fd = None
        tracker._pid = None


def create_worker_queue(ctx: Any, maxsize: int) -> Any:
    """Create a worker queue, surviving one dead host resource tracker.

    The retry is deliberately bounded at one attempt and logs both times:
    a dead tracker is a host fault worth seeing, not something to paper
    over. If the replacement tracker also fails, the failure is raised as
    :class:`IsolatedWorkerHostError` so the user is told the host cannot
    start workers rather than being handed an unexplained internal error.
    """
    if not _interpreter_checked:
        # Cheap, once per process, and it removes by far the likeliest
        # cause of the failure handled below.
        ensure_spawnable_interpreter()
    try:
        return ctx.Queue(maxsize=maxsize)
    except _DEAD_TRACKER_ERRORS as exc:
        logger.warning(
            "worker_resource_tracker_dead",
            error=str(exc),
            error_type=type(exc).__name__,
            **_resource_tracker_diagnostics(),
        )
        _reset_resource_tracker()
        try:
            return ctx.Queue(maxsize=maxsize)
        except _DEAD_TRACKER_ERRORS as retry_exc:
            logger.error(
                "worker_resource_tracker_unrecoverable",
                error=str(retry_exc),
                error_type=type(retry_exc).__name__,
                **_resource_tracker_diagnostics(),
            )
            raise IsolatedWorkerHostError(
                "This server cannot start background work at the moment — the process "
                "tracker it relies on has stopped. Restart the app and try again."
            ) from retry_exc


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
    result_queue: mp.Queue[tuple[str, Any]] = create_worker_queue(ctx, 1)
    process = ctx.Process(
        target=_isolated_worker_entrypoint,
        name=worker_config.process_name,
        args=(result_queue, function, args, kwargs, worker_config.memory_limit_bytes),
    )
    primary_error: BaseException | None = None
    result: T | None = None
    has_result = False
    queued_payload: tuple[str, Any] | None = None
    process_started = False
    try:
        try:
            process.start()
            process_started = True
        except Exception as exc:  # pragma: no cover - depends on multiprocessing internals
            raise IsolatedWorkerStartError(
                f"Failed to start isolated worker: {exc}",
            ) from exc

        queued_payload = _wait_for_worker(process, worker_config, result_queue)

        status, payload = (
            queued_payload
            if queued_payload is not None
            else _read_worker_payload(
                result_queue,
                exitcode=process.exitcode,
                memory_limit_bytes=worker_config.memory_limit_bytes,
            )
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
    except BaseException as exc:
        primary_error = exc
    finally:

        def record_finalization_error(exc: BaseException, *, step: str) -> None:
            nonlocal primary_error
            if primary_error is None:
                primary_error = exc
            else:
                primary_error.add_note(f"{step} failed: {exc}")

        process_alive = False
        if process_started:
            try:
                process_alive = process.is_alive()
            except BaseException as exc:
                record_finalization_error(exc, step="process liveness check")
                process_alive = True
        if process_alive:
            try:
                _terminate_process(process)
            except BaseException as exc:
                record_finalization_error(exc, step="process termination")
        if process_started:
            try:
                process.join(timeout=2.0)
            except BaseException as exc:
                record_finalization_error(exc, step="process join")
        try:
            result_queue.close()
        except BaseException as exc:
            record_finalization_error(exc, step="result queue close")
        try:
            result_queue.join_thread()
        except BaseException as exc:
            record_finalization_error(exc, step="result queue feeder join")

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
    if process.is_alive():
        raise IsolatedWorkerTerminationError()


def _wait_for_worker(
    process: BaseProcess,
    config: IsolatedWorkerConfig,
    result_queue: mp.Queue[tuple[str, Any]],
) -> tuple[str, Any] | None:
    deadline = None if config.timeout_seconds is None else time.monotonic() + config.timeout_seconds
    queued_payload: tuple[str, Any] | None = None
    while process.is_alive():
        if queued_payload is None:
            try:
                queued_payload = result_queue.get_nowait()
            except queue.Empty:
                pass
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
    return queued_payload


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
