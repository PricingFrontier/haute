"""The refusal for each worker failure, decided once for every route that runs one.

A route that waits on a worker (the interactive pool, a one-shot isolated worker, the
dedicated solver session) raises what the worker raised through
:func:`worker_failure_refusal`. It adds what only the route knows to
:func:`haute._failure_answers.answer_failure`: its own words for a deadline, the cap a
hard-capped worker ran under, and the remote exceptions it maps itself. Everything else,
a memory outcome, a stop, a worker that could not start, a child's curated public error or
any failure the child put into words, is answered by the module; a failure nothing names
is an incident whose record holds what the worker raised. A job keeps the same failure's
``terminal_reason`` as its end state.
"""

from __future__ import annotations

from collections.abc import Callable

from haute._failure_answers import answer_failure, answer_from_remote, worker_memory_answer
from haute._worker_failures import (
    WorkerError,
    WorkerRemoteError,
    WorkerTimeoutError,
    worker_failure_is_cap_unavailable,
    worker_failure_is_memory,
)
from haute.errors import RequestRefusedError

#: A route's own answer for a remote exception only it knows, or None to fall through.
RemoteAnswer = Callable[[WorkerRemoteError], RequestRefusedError | None]


def worker_failure_refusal(
    exc: WorkerError,
    *,
    operation: str,
    timed_out: str,
    memory_limit_bytes: int | None = None,
    remote: RemoteAnswer | None = None,
) -> RequestRefusedError:
    """The refusal for *exc*, raised by the worker running *operation*.

    *timed_out* is the route's 504 detail; *memory_limit_bytes* the cap a hard-capped
    worker ran under, reported in a memory outcome's payload; *remote* the route's own
    answer for a remote exception only it knows.
    """
    if isinstance(exc, WorkerTimeoutError):
        return RequestRefusedError(timed_out, status_code=504)
    if isinstance(exc, WorkerRemoteError):
        # The route's own exceptions first: a trace's refusals are answered by their type,
        # which the module would call a refused request.
        answered = None if remote is None else remote(exc)
        if answered is not None:
            return answered
        named = answer_from_remote(exc, operation=operation)
        if named is not None:
            return named.refusal()
    # A one-shot worker reports no module, so its child's own MemoryError check classifies
    # it; a remote that names its module is a memory outcome only by its exact identity,
    # which the module decides.
    unnamed_remote = isinstance(exc, WorkerRemoteError) and not exc.remote_module
    if (not isinstance(exc, WorkerRemoteError) or unnamed_remote) and (
        worker_failure_is_cap_unavailable(exc) or worker_failure_is_memory(exc)
    ):
        return worker_memory_answer(
            exc, operation=operation, memory_limit_bytes=memory_limit_bytes
        ).refusal()
    return answer_failure(exc, operation=operation).refusal()
