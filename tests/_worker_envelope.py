"""A child's failure as a spawned worker reports it to its parent."""

from __future__ import annotations

from haute._worker_failures import WorkerRemoteError


def remote_failure(exc: BaseException) -> WorkerRemoteError:
    """*exc*, raised in a child, as its parent receives it: through the child's error
    envelope, its answer classified where it was raised
    (``haute._failure_answers.failure_payload``)."""
    from haute._worker_isolation import _worker_error_envelope

    _status, (remote_type, message, remote_traceback, is_memory, payload) = _worker_error_envelope(
        exc
    )
    return WorkerRemoteError(
        remote_type=remote_type,
        remote_message=message,
        remote_traceback=remote_traceback,
        is_memory=is_memory,
        public_payload=payload,
    )
