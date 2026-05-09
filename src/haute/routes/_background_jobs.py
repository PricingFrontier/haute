"""Small cooperative lifecycle helpers for thread-backed route jobs."""

from __future__ import annotations

import threading
from collections.abc import Hashable
from dataclasses import dataclass


class BackgroundJobStoppedError(RuntimeError):
    """Raised inside workers when their job has been cancelled or superseded."""

    def __init__(self, job_id: str, status: str) -> None:
        super().__init__(f"Job {job_id!r} stopped with status {status!r}")
        self.job_id = job_id
        self.status = status


@dataclass(frozen=True, slots=True)
class JobCancellation:
    """Cancellation token for one registered background worker."""

    job_id: str
    key: Hashable
    event: threading.Event

    def cancel(self) -> None:
        self.event.set()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()


class CancellableJobRegistry:
    """Track latest-running jobs and cooperative cancellation tokens.

    The registry deliberately owns only runtime coordination. Persistent job
    metadata remains in ``JobStore`` so route handlers keep one source of truth
    for status, result payloads, and TTL cleanup.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._latest_by_key: dict[Hashable, str] = {}
        self._tokens_by_job_id: dict[str, JobCancellation] = {}

    def register_latest(self, key: Hashable, job_id: str) -> tuple[JobCancellation, str | None]:
        """Register *job_id* as latest for *key* and cancel any previous job."""
        token = JobCancellation(job_id=job_id, key=key, event=threading.Event())
        with self._lock:
            previous_job_id = self._latest_by_key.get(key)
            if previous_job_id is not None:
                previous = self._tokens_by_job_id.get(previous_job_id)
                if previous is not None:
                    previous.cancel()
            self._latest_by_key[key] = job_id
            self._tokens_by_job_id[job_id] = token
            return token, previous_job_id

    def cancel(self, job_id: str) -> bool:
        """Request cancellation for *job_id* if it still has an active token."""
        with self._lock:
            token = self._tokens_by_job_id.get(job_id)
            if token is None:
                return False
            token.cancel()
            return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            token = self._tokens_by_job_id.get(job_id)
            return bool(token and token.cancelled)

    def release(self, job_id: str) -> None:
        """Remove active coordination state for *job_id*."""
        with self._lock:
            token = self._tokens_by_job_id.pop(job_id, None)
            if token is None:
                return
            if self._latest_by_key.get(token.key) == job_id:
                del self._latest_by_key[token.key]
