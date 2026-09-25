"""Step progress of in-flight previews, by the id the requesting client chose.

The client that sent a preview polls its own request's progress, so progress
never reaches another tab. An entry opens when the request starts (``preparing``
until its execution plan is known) and closes when the request settles; a
closed id is remembered for a while so a late report — thread-mode work that
outlives its timed-out request — cannot recreate the entry.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from haute._step_progress import StepProgress

_CLOSED_TTL_SECONDS = 300.0

ProgressPhase = Literal["preparing", "running"]


@dataclass(frozen=True, slots=True)
class PreviewProgress:
    phase: ProgressPhase
    done: int | None
    total: int | None
    label: str | None


class PreviewProgressRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._open: dict[str, PreviewProgress] = {}
        self._closed: dict[str, float] = {}

    def open(self, request_id: str) -> None:
        with self._lock:
            self._prune()
            if request_id in self._closed:
                return
            self._open[request_id] = PreviewProgress("preparing", None, None, None)

    def report(self, request_id: str, progress: StepProgress) -> None:
        """Record a step; ignored once the request has settled."""
        with self._lock:
            if request_id not in self._open:
                return
            self._open[request_id] = PreviewProgress(
                "running", progress.done, progress.total, progress.label or None
            )

    def close(self, request_id: str) -> None:
        with self._lock:
            self._open.pop(request_id, None)
            self._closed[request_id] = self._clock()

    def get(self, request_id: str) -> PreviewProgress | None:
        with self._lock:
            return self._open.get(request_id)

    def _prune(self) -> None:
        cutoff = self._clock() - _CLOSED_TTL_SECONDS
        for request_id in [key for key, closed_at in self._closed.items() if closed_at < cutoff]:
            del self._closed[request_id]


preview_progress = PreviewProgressRegistry()
