"""Answer a worker failure the way a pipeline route does, for tests of that mapping."""

from __future__ import annotations

from typing import NoReturn

from haute._worker_failures import WorkerError
from haute.routes._worker_failure_http import worker_failure_http_exception
from haute.routes.pipeline import _pipeline_remote_answer


def raise_worker_http(exc: WorkerError, *, operation: str) -> NoReturn:
    """Raise the HTTP answer a pipeline route gives *exc* from the worker running *operation*."""
    raise worker_failure_http_exception(
        exc,
        operation=operation,
        timed_out="timed out",
        remote=_pipeline_remote_answer(operation),
    )
