"""Step progress of a running preview, from its executor to whoever reports it.

A preview's time goes into a few heavy steps — its planned captures and the
target's collection — while every other node only builds a lazy plan, so its
progress counts those steps, each with equal weight. The walker reports through
:attr:`ExecutionContext.step_progress`; in thread mode that is the route's own
reporter, and in a warm interactive worker it writes a :class:`ProgressCell`
the parent reads while it supervises the job.

The cell is shared memory guarded by its own lock. The worker, the only writer,
writes whole updates under it; the parent only ever tries the lock without
blocking and skips a poll when it is held, so a worker that dies holding it can
never stall supervision, timeouts, or Stop. Every update carries the job id and
a sequence number, so a warm worker's previous job is never read as this one's.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from multiprocessing.context import BaseContext
from typing import Any

_CELL_BYTES = 1024
_LABEL_CHARS = 120


@dataclass(frozen=True, slots=True)
class StepProgress:
    """Steps completed of the steps planned, and the step now running."""

    done: int
    total: int
    label: str


StepProgressReporter = Callable[[StepProgress], None]


class ProgressCell:
    """One worker slot's latest step progress, in shared memory."""

    def __init__(self, ctx: BaseContext) -> None:
        self._buffer: Any = ctx.RawArray("c", _CELL_BYTES)
        self._lock: Any = ctx.Lock()

    def write(self, job_id: str, sequence: int, progress: StepProgress) -> None:
        """Replace the cell with one complete update (worker side)."""
        update = {
            "job": job_id,
            "seq": sequence,
            "done": progress.done,
            "total": progress.total,
            "label": progress.label[:_LABEL_CHARS],
        }
        payload = json.dumps(update).encode("utf-8")
        if len(payload) >= _CELL_BYTES:
            # An escaped label can outgrow the cell; the counts still fit.
            payload = json.dumps({**update, "label": ""}).encode("utf-8")
        with self._lock:
            self._buffer.raw = payload.ljust(_CELL_BYTES, b"\0")

    def try_read(self, job_id: str) -> tuple[int, StepProgress] | None:
        """The latest update for *job_id*, or None; never waits for the lock (parent side)."""
        if not self._lock.acquire(block=False):
            return None
        try:
            raw = bytes(self._buffer.raw)
        finally:
            self._lock.release()
        text = raw.rstrip(b"\0")
        if not text:
            return None
        try:
            update = json.loads(text)
        except ValueError:
            return None
        if not isinstance(update, dict) or update.get("job") != job_id:
            return None
        return int(update["seq"]), StepProgress(
            done=int(update["done"]), total=int(update["total"]), label=str(update["label"])
        )


class _JobProgressReporter:
    """Writes one job's step progress to its worker slot's cell."""

    def __init__(self, cell: ProgressCell, job_id: str) -> None:
        self.cell = cell
        self.job_id = job_id
        self._sequence = 0

    def __call__(self, progress: StepProgress) -> None:
        self._sequence += 1
        self.cell.write(self.job_id, self._sequence, progress)


# The running job's reporter inside a warm worker; None outside a job.
_bound_reporter: _JobProgressReporter | None = None


@contextlib.contextmanager
def bind_job_progress(cell: ProgressCell | None, job_id: str) -> Iterator[None]:
    """Make :func:`current_job_progress_reporter` write *cell* for *job_id* while a job runs."""
    global _bound_reporter
    if cell is None:
        yield
        return
    _bound_reporter = _JobProgressReporter(cell, job_id)
    try:
        yield
    finally:
        _bound_reporter = None


def current_job_progress_reporter() -> StepProgressReporter | None:
    """The reporter of the job this worker is running, if it was given a progress cell."""
    return _bound_reporter
