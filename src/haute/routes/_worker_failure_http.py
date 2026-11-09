"""The HTTP answer for each worker failure, decided once for every route that runs one.

A route that waits on a worker (the interactive pool, a one-shot isolated worker, the
dedicated solver session) answers what the worker raised through
:func:`worker_failure_http_exception`. A memory outcome is a 507 with the closed
``memory_limit`` payload, a deadline 504, a stop 409, a worker that could not start
503, a child's curated public error its own status and payload, and anything else a
logged 500 with no detail beyond the generic message. A job keeps the same failure's
``terminal_reason`` as its end state.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException

from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionMemoryLimitExceededError
from haute._logging import get_logger
from haute._native_memory_limit import NativeMemoryLimitUnsupportedError
from haute._worker_failures import (
    WorkerCrashedError,
    WorkerError,
    WorkerHostError,
    WorkerMemoryLimitError,
    WorkerMemoryLimitUnsupportedError,
    WorkerRemoteError,
    WorkerStartError,
    WorkerStoppedError,
    WorkerTimeoutError,
    worker_failure_is_memory,
    worker_memory_detail,
)
from haute.errors import SeedPlanExpiredError
from haute.routes._contract_errors import PUBLIC_CONTRACT_ERROR_TYPES, SEED_PLAN_EXPIRED_HTTP_STATUS
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

logger = get_logger(component="server.worker_failures")

#: A route's own answer for a remote exception only it knows, or None to fall through.
RemoteAnswer = Callable[[WorkerRemoteError], HTTPException | None]

_PUBLIC_REMOTE_ERROR_CODES = {
    (error_type.__module__, error_type.__name__): error_type.error_code
    for error_type in PUBLIC_CONTRACT_ERROR_TYPES
}
_MEMORY_REMOTE_ERROR_CODES = {
    (ExecutionAdmissionError.__module__, ExecutionAdmissionError.__name__): "memory_limit",
    (
        ExecutionMemoryLimitExceededError.__module__,
        ExecutionMemoryLimitExceededError.__name__,
    ): "memory_limit",
}
_NATIVE_CAP_UNAVAILABLE = (
    NativeMemoryLimitUnsupportedError.__module__,
    NativeMemoryLimitUnsupportedError.__name__,
)


def _memory_detail(operation: str, *, reason: str) -> dict[str, object]:
    return {"error_code": "memory_limit", "operation": operation, "reason": reason}


def _remote_http_exception(
    exc: WorkerRemoteError,
    *,
    operation: str,
    memory_limit_bytes: int | None,
    remote: RemoteAnswer | None,
) -> HTTPException:
    payload = exc.public_payload
    identity = (exc.remote_module, exc.remote_type)
    expected_memory_code = _MEMORY_REMOTE_ERROR_CODES.get(identity)
    if (
        payload is not None
        and expected_memory_code is not None
        and payload.get("error_code") == expected_memory_code
    ):
        return HTTPException(status_code=507, detail=payload)
    # Memory outcomes the child cannot curate: classified by exact identity and
    # answered with a parent-authored detail, never the child payload. A
    # same-named exception from any other module is not one.
    if identity == ("builtins", "MemoryError"):
        return HTTPException(
            status_code=507, detail=_memory_detail(operation, reason="worker_memory_exhausted")
        )
    if identity == _NATIVE_CAP_UNAVAILABLE:
        return HTTPException(
            status_code=507,
            detail=_memory_detail(operation, reason="native_memory_cap_unavailable"),
        )
    expected_public_code = _PUBLIC_REMOTE_ERROR_CODES.get(identity)
    if (
        payload is not None
        and expected_public_code is not None
        and payload.get("error_code") == expected_public_code
    ):
        status_code = (
            SEED_PLAN_EXPIRED_HTTP_STATUS
            if expected_public_code == SeedPlanExpiredError.error_code
            else 422
        )
        return HTTPException(status_code=status_code, detail=payload)
    if remote is not None:
        answered = remote(exc)
        if answered is not None:
            return answered
    # A one-shot worker reports no module, so its child's own MemoryError check
    # classifies it; a remote that names its module is a memory outcome only by the
    # exact identities above, so a same-named exception from elsewhere stays a 500.
    if not exc.remote_module and worker_failure_is_memory(exc):
        return HTTPException(
            status_code=507,
            detail=worker_memory_detail(
                exc, operation=operation, memory_limit_bytes=memory_limit_bytes
            ),
        )
    # The client gets a generic 500; the server log keeps what the worker raised.
    logger.error(
        "worker_remote_failure",
        operation=operation,
        remote_type=exc.remote_type,
        remote_module=exc.remote_module,
        remote_message=exc.remote_message,
        remote_traceback=exc.remote_traceback,
    )
    return HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


def worker_failure_http_exception(
    exc: WorkerError,
    *,
    operation: str,
    timed_out: str,
    memory_limit_bytes: int | None = None,
    remote: RemoteAnswer | None = None,
) -> HTTPException:
    """The response for *exc*, raised by the worker running *operation*.

    *timed_out* is the route's 504 detail; *memory_limit_bytes* the cap a hard-capped
    worker ran under, reported in a memory outcome's payload; *remote* the route's own
    answer for a remote exception only it knows.
    """
    if isinstance(exc, WorkerMemoryLimitError | WorkerMemoryLimitUnsupportedError):
        return HTTPException(
            status_code=507,
            detail=worker_memory_detail(
                exc, operation=operation, memory_limit_bytes=memory_limit_bytes
            ),
        )
    if isinstance(exc, WorkerTimeoutError):
        return HTTPException(status_code=504, detail=timed_out)
    if isinstance(exc, WorkerStoppedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, WorkerCrashedError):
        if worker_failure_is_memory(exc):
            return HTTPException(
                status_code=507,
                detail=worker_memory_detail(
                    exc, operation=operation, memory_limit_bytes=memory_limit_bytes
                ),
            )
        logger.error("worker_crashed", operation=operation, exitcode=exc.exitcode)
        return HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    if isinstance(exc, WorkerRemoteError):
        return _remote_http_exception(
            exc, operation=operation, memory_limit_bytes=memory_limit_bytes, remote=remote
        )
    if isinstance(exc, WorkerStartError | WorkerHostError):
        # No worker could run it: the host, not the request, is at fault, and the
        # next request starts a worker again.
        logger.warning("worker_unavailable", operation=operation, error=str(exc))
        return HTTPException(status_code=503, detail=str(exc))
    logger.error(
        "worker_failed",
        operation=operation,
        error_class=type(exc).__name__,
        error=str(exc),
    )
    return HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
