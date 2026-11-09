"""The one family of worker failures every worker mechanism raises.

The one-shot isolated worker (:func:`haute._worker_isolation.run_isolated_worker`),
the warm interactive pool (:class:`haute._interactive_workers.InteractiveWorkerPool`),
the dedicated worker it leases (:mod:`haute._dedicated_workers`) and the versioned job
transport (:mod:`haute._worker_protocol`) all fail through these classes. Each carries
its job terminal reason, so a job's terminal state is read from the failure itself;
:mod:`haute.routes._worker_failure_http` answers for each class once over HTTP.
"""

from __future__ import annotations

from typing import Literal

from haute._file_ops import PathTooLongError, path_too_long_error

WorkerTerminalReason = Literal[
    "completed",
    "superseded",
    "timed_out",
    "cancelled",
    "memory_limited",
    "contract_error",
    "error",
]


class WorkerError(RuntimeError):
    """A worker did not return its result; ``terminal_reason`` is the job's end state."""

    terminal_reason: WorkerTerminalReason

    def __init__(self, message: str, *, terminal_reason: WorkerTerminalReason = "error") -> None:
        super().__init__(message)
        self.terminal_reason = terminal_reason


class WorkerStartError(WorkerError):
    """The parent could not start a worker, or a started one never reported ready."""


class WorkerHostError(WorkerError):
    """The host's process machinery is unusable, so no worker can start.

    Distinct from a worker that ran and failed: nothing ran at all, and
    the cause is the environment rather than the job. Specifically,
    CPython's ``multiprocessing`` resource tracker — a helper process that
    must be alive before any queue or lock can be created — has died and
    could not be replaced. Named so the failure reads as the host problem
    it is instead of an unexplained internal error.
    """


def remote_failure_terminal_reason(remote_type: str, *, is_memory: bool) -> WorkerTerminalReason:
    """The terminal reason for an exception a child reported: one rule for every parent.

    *is_memory* is the child's own ``isinstance(exc, MemoryError)``, so every subclass
    (an admission refusal, a sampled checkpoint over budget) counts whatever its name.
    A native cap the child could not install is a contract error.
    """
    if remote_type.startswith("NativeMemoryLimit"):
        return "contract_error"
    return "memory_limited" if is_memory else "error"


class WorkerRemoteError(WorkerError):
    """The child raised a Python exception, reported by type, module, message and traceback.

    *public_payload* is the child's curated, user-safe payload when the exception
    carries one; *remote_module* names where its type is defined, so a parent
    classifies it by exact identity rather than by a name any module could reuse.
    """

    def __init__(
        self,
        *,
        remote_type: str,
        remote_message: str,
        remote_traceback: str,
        is_memory: bool,
        remote_module: str = "",
        public_payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            f"The worker raised {remote_type}: {remote_message}",
            terminal_reason=remote_failure_terminal_reason(remote_type, is_memory=is_memory),
        )
        self.remote_type = remote_type
        self.remote_module = remote_module
        self.remote_message = remote_message
        self.remote_traceback = remote_traceback
        self.public_payload = public_payload
        self.is_memory = is_memory


# ``STATUS_STACK_BUFFER_OVERRUN``: the exit status of a Windows fail-fast abort.
_WINDOWS_FAIL_FAST_EXITCODE = 0xC0000409
# ``STATUS_STACK_OVERFLOW``: under a Job Object commit cap, a thread whose next
# stack page the cap refuses to commit dies with this status, not an allocation
# failure — a 10M-row preview join capture ended exactly this way.
_WINDOWS_STACK_OVERFLOW_EXITCODE = 0xC00000FD


def exitcode_looks_memory_limited(exitcode: int | None, memory_limit_bytes: int | None) -> bool:
    """Whether a worker's exit code is the signature of a memory cap stopping it."""
    # SIGABRT is deliberately classified alongside SIGKILL, but only when a
    # memory cap was configured: under RLIMIT_AS a native allocator that
    # cannot allocate raises bad_alloc and aborts, so SIGABRT is the cap's
    # PRIMARY out-of-memory signature (SIGKILL covers the kernel OOM killer).
    # Narrowing to SIGKILL-only would misroute exactly the failures the cap
    # exists to catch. The residual misdiagnosis vector — a native assertion
    # or heap-corruption abort under a cap — is accepted: the crash wording
    # hedges ("may have run out of memory") and the exit code stays on the
    # exception and in ``worker_exitcode``, at the cost of the UI showing
    # memory guidance for such an abort.
    #
    # ``_WINDOWS_FAIL_FAST_EXITCODE`` is SIGABRT's Windows counterpart: a
    # native allocation the Job Object cap refuses aborts through the
    # fail-fast path (Polars prints ``memory allocation of N bytes failed``),
    # and a panic or assertion abort exits the same way. A refused stack commit
    # exits as ``_WINDOWS_STACK_OVERFLOW_EXITCODE``; a genuine deep-recursion
    # overflow under a cap reads the same, which the hedged wording accepts.
    if memory_limit_bytes is None or exitcode is None:
        return False
    try:
        import signal
    except ImportError:
        return False
    # ``SIGKILL`` is not exported by Python's Windows signal module. Keep the
    # conventional POSIX value for decoded/synthetic worker status records so
    # their classification is platform-independent; live Windows RSS breaches
    # are reported directly by the parent watchdog rather than this heuristic.
    sigkill_number = int(getattr(signal, "SIGKILL", 9))
    return exitcode in {
        -sigkill_number,
        -int(signal.SIGABRT),
        _WINDOWS_FAIL_FAST_EXITCODE,
        _WINDOWS_STACK_OVERFLOW_EXITCODE,
    }


class WorkerCrashedError(WorkerError):
    """The child exited without returning a result.

    The message is parent-authored — a crashed child left no payload to
    curate — so it is written directly for the user rather than in
    supervisor jargon. *memory_limited* is the caller's evidence that a memory
    cap stopped it (:func:`exitcode_looks_memory_limited`, or the host's own
    limit notification); the exit code stays on the exception (and in the
    job's ``worker_exitcode`` field) for diagnostics.
    """

    def __init__(self, exitcode: int | None, *, memory_limited: bool = False) -> None:
        exit_detail = "" if exitcode is None else f" (exit code {exitcode})"
        if memory_limited:
            # The exit-code heuristic is indicative, not proof (a native abort
            # or an external kill can look the same) — the wording hedges
            # rather than asserting an out-of-memory condition as fact.
            message = (
                f"The background process was stopped and may have run out of memory"
                f"{exit_detail}. Reduce the data size, or run on a server with more "
                "memory, then try again."
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


class WorkerTimeoutError(WorkerError):
    """The parent stopped a worker that exceeded its time limit.

    Like the crash, the message is parent-authored. The limit stays on
    ``timeout_seconds`` for diagnostics.
    """

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            f"The background process was stopped after exceeding its time limit "
            f"of {timeout_seconds:g} seconds. Try again with less data, or "
            "increase the configured timeout.",
            terminal_reason="timed_out",
        )
        self.timeout_seconds = timeout_seconds


class WorkerStoppedError(WorkerError):
    """The parent stopped the worker: a cancellation, a newer request, or a shutdown."""

    def __init__(self, terminal_reason: WorkerTerminalReason) -> None:
        if terminal_reason == "completed":
            raise ValueError("completed is not a valid stopped-worker reason")
        super().__init__(
            f"The worker was stopped with reason {terminal_reason!r}",
            terminal_reason=terminal_reason,
        )


class WorkerMemoryLimitError(WorkerError, MemoryError):
    """The parent's watchdog saw the worker's resident memory above its limit."""

    def __init__(self, *, rss_bytes: int, rss_limit_bytes: int) -> None:
        super().__init__(
            f"The worker exceeded its RSS watchdog limit: {rss_bytes} bytes used > "
            f"{rss_limit_bytes} bytes allowed",
            terminal_reason="memory_limited",
        )
        self.rss_bytes = rss_bytes
        self.rss_limit_bytes = rss_limit_bytes

    def to_payload(self) -> dict[str, object]:
        return {
            "error_code": "memory_limit",
            "rss_bytes": self.rss_bytes,
            "rss_limit_bytes": self.rss_limit_bytes,
            "reason": "worker_rss_limit_exceeded",
        }


class WorkerMemoryLimitUnsupportedError(WorkerError):
    """A required child-process memory cap cannot be enforced on this host."""

    def __init__(self, *, memory_limit_bytes: int) -> None:
        super().__init__(
            "Worker memory caps require a supported native kernel limit on this host.",
            terminal_reason="contract_error",
        )
        self.memory_limit_bytes = memory_limit_bytes


class WorkerTerminationError(WorkerError):
    """A worker stayed alive after terminate and kill attempts."""

    def __init__(self) -> None:
        super().__init__(
            "The worker remained alive after terminate and kill attempts",
            terminal_reason="error",
        )


class WorkerProtocolError(WorkerError):
    """A worker broke the transport contract: a malformed, stale or unexpected message."""

    def __init__(self, message: str) -> None:
        super().__init__(message, terminal_reason="contract_error")


class WorkerBusyError(WorkerError):
    """A pre-emptible request was refused rather than waiting for its pool slot.

    The slot was held, its worker was starting or not running, or an
    interactive request was already waiting for it.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"The interactive worker is busy: {reason}", terminal_reason="superseded")


class WorkerPreemptedError(WorkerError):
    """A pre-emptible request was stopped because an interactive request needed its slot.

    Raised only after its worker was terminated, confirmed dead and replaced,
    so the waiting request runs next on the replacement.
    """

    def __init__(self) -> None:
        super().__init__(
            "Interactive execution was stopped because an editor request needed its worker",
            terminal_reason="superseded",
        )


def worker_failure_is_memory(exc: BaseException) -> bool:
    """Return whether a worker failure is a memory outcome.

    An RSS breach and an unsupported native cap always are; a crashed child
    is memory-bound only when its exit-code heuristic said so; a remote
    exception is when the child's exception was a ``MemoryError`` or its native cap
    could not be installed.
    """
    if isinstance(exc, WorkerMemoryLimitError | WorkerMemoryLimitUnsupportedError):
        return True
    if isinstance(exc, WorkerCrashedError):
        return exc.terminal_reason == "memory_limited"
    if isinstance(exc, WorkerRemoteError):
        return exc.is_memory or exc.remote_type == "NativeMemoryLimitUnsupportedError"
    return False


# The child's own exceptions when it could not install, or release, its native cap.
_CAP_UNAVAILABLE_REMOTE_TYPES = frozenset(
    {"NativeMemoryLimitUnsupportedError", "NativeMemoryLimitCleanupError"}
)


def worker_failure_is_cap_unavailable(exc: BaseException) -> bool:
    """Return whether a worker failed because this host cannot install its required cap.

    The spawn refuses a required cap a host cannot install before any child
    exists, and a child reports one it could not install or release. Decide
    this before :func:`worker_failure_is_memory`, which counts both as
    memory outcomes although nothing ran out of memory.
    """
    if isinstance(exc, WorkerMemoryLimitUnsupportedError):
        return True
    return isinstance(exc, WorkerRemoteError) and (exc.remote_type in _CAP_UNAVAILABLE_REMOTE_TYPES)


def worker_failure_is_path_too_long(exc: BaseException) -> bool:
    """Return whether a worker failure is a path Windows refused for its length.

    A child reports the :class:`~haute._file_ops.PathTooLongError` behind its failure;
    a failure raised in this process is read directly.
    """
    if isinstance(exc, WorkerRemoteError):
        return exc.remote_type == PathTooLongError.__name__
    return path_too_long_error(exc) is not None


def worker_memory_detail(
    exc: BaseException,
    *,
    operation: str,
    memory_limit_bytes: int | None,
) -> dict[str, object]:
    """Return the typed ``memory_limit`` payload for a worker memory outcome.

    Shared by every surface that supervises a hard-capped worker so a worker
    RSS breach, an unsupported native cap, and a crash that looks memory-bound
    are reported with the same closed reason codes.
    """
    payload: dict[str, object] = {
        "error_code": "memory_limit",
        "operation": operation,
        "reason": "worker_memory_limit",
    }
    if memory_limit_bytes is not None:
        payload["memory_limit_bytes"] = memory_limit_bytes
    if isinstance(exc, WorkerMemoryLimitError):
        payload.update(
            rss_bytes=exc.rss_bytes,
            rss_limit_bytes=exc.rss_limit_bytes,
            reason="worker_rss_limit_exceeded",
        )
    elif isinstance(exc, WorkerMemoryLimitUnsupportedError) or (
        isinstance(exc, WorkerRemoteError)
        and exc.remote_type == "NativeMemoryLimitUnsupportedError"
    ):
        payload["reason"] = "native_memory_cap_unavailable"
    elif isinstance(exc, WorkerCrashedError):
        payload["reason"] = "worker_may_have_exceeded_memory_limit"
    return payload
