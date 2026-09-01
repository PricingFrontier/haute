"""Shared timeout helpers for blocking route work."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from haute._logging import get_logger

R = TypeVar("R")

logger = get_logger(component="server.timeouts")


@dataclass(frozen=True, slots=True)
class _OccupancySnapshot:
    queued: int
    running: int
    cancellation_waiters: int


class _BlockingWorkOccupancy:
    """Constant-space process occupancy for compatibility-thread route work."""

    __slots__ = ("_cancellation_waiters", "_lock", "_queued", "_running")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queued = 0
        self._running = 0
        self._cancellation_waiters = 0

    def submitted(self) -> None:
        with self._lock:
            self._queued += 1

    def started(self) -> None:
        with self._lock:
            if self._queued <= 0:
                raise RuntimeError("Blocking-work occupancy started without a queued submission")
            self._queued -= 1
            self._running += 1

    def finished(self) -> None:
        with self._lock:
            if self._running <= 0:
                raise RuntimeError("Blocking-work occupancy finished without a running worker")
            self._running -= 1

    def abandoned_before_start(self) -> None:
        with self._lock:
            if self._queued <= 0:
                raise RuntimeError("Blocking-work occupancy abandoned without a queued submission")
            self._queued -= 1

    def cancellation_wait_started(self) -> None:
        with self._lock:
            self._cancellation_waiters += 1

    def cancellation_wait_finished(self) -> None:
        with self._lock:
            if self._cancellation_waiters <= 0:
                raise RuntimeError("Blocking-work cancellation wait finished without a waiter")
            self._cancellation_waiters -= 1

    def snapshot(self) -> _OccupancySnapshot:
        with self._lock:
            return _OccupancySnapshot(
                queued=self._queued,
                running=self._running,
                cancellation_waiters=self._cancellation_waiters,
            )


_blocking_work_occupancy = _BlockingWorkOccupancy()


@dataclass(slots=True)
class _BlockingWorkMeasurement:
    operation: str
    submitted_at: float
    started_at: float | None = None
    finished_at: float | None = None
    response_boundary_at: float | None = None
    response_snapshot: _OccupancySnapshot | None = None
    outcome: str | None = None
    _logged: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def run(self, func: Callable[..., R], args: tuple[Any, ...], kwargs: dict[str, Any]) -> R:
        """Run the callable while owning the queued→running transition."""
        started_at = time.perf_counter()
        with self._lock:
            self.started_at = started_at
        _blocking_work_occupancy.started()
        try:
            return func(*args, **kwargs)
        finally:
            finished_at = time.perf_counter()
            _blocking_work_occupancy.finished()
            with self._lock:
                self.finished_at = finished_at

    def mark_response_boundary(self, outcome: str) -> None:
        snapshot = _blocking_work_occupancy.snapshot()
        boundary_at = time.perf_counter()
        with self._lock:
            if self.response_boundary_at is not None:
                raise RuntimeError("Blocking-work response boundary recorded more than once")
            self.response_boundary_at = boundary_at
            self.response_snapshot = snapshot
            self.outcome = outcome

    def log_after_cleanup(self) -> None:
        """Log once after the task is done and occupancy has converged."""
        cleanup_snapshot = _blocking_work_occupancy.snapshot()
        with self._lock:
            if self._logged:
                return
            if self.response_boundary_at is None or self.response_snapshot is None:
                raise RuntimeError("Blocking-work measurement has no response boundary")
            if self.finished_at is None:
                if self.started_at is not None:
                    raise RuntimeError(
                        "Blocking-work task completed before its worker recorded completion"
                    )
                _blocking_work_occupancy.abandoned_before_start()
                self.finished_at = time.perf_counter()
                cleanup_snapshot = _blocking_work_occupancy.snapshot()

            started_at = self.started_at if self.started_at is not None else self.finished_at
            queue_ms = max(0.0, (started_at - self.submitted_at) * 1000)
            execution_ms = (
                max(0.0, (self.finished_at - self.started_at) * 1000)
                if self.started_at is not None
                else 0.0
            )
            response_wait_ms = max(
                0.0,
                (self.response_boundary_at - self.submitted_at) * 1000,
            )
            cleanup_ms = max(0.0, (self.finished_at - self.response_boundary_at) * 1000)
            response_snapshot = self.response_snapshot
            outcome = self.outcome
            self._logged = True

        logger.info(
            "route_blocking_work_completed",
            operation=self.operation,
            outcome=outcome,
            queue_ms=queue_ms,
            execution_ms=execution_ms,
            response_wait_ms=response_wait_ms,
            cleanup_ms=cleanup_ms,
            queued_at_response=response_snapshot.queued,
            running_at_response=response_snapshot.running,
            cancellation_waiters_at_response=response_snapshot.cancellation_waiters,
            queued_after_cleanup=cleanup_snapshot.queued,
            running_after_cleanup=cleanup_snapshot.running,
            cancellation_waiters_after_cleanup=cleanup_snapshot.cancellation_waiters,
        )


class BlockingWorkTimeoutError(TimeoutError):
    """Timeout raised while the underlying blocking task is still running."""

    def __init__(self, operation: str, timeout: float, background_task: asyncio.Future[Any]):
        super().__init__(f"{operation} timed out after {timeout:.0f}s")
        self.background_task = background_task


async def run_blocking_with_response_timeout(
    func: Callable[..., R],
    *args: Any,
    timeout: float,
    operation: str,
    **kwargs: Any,
) -> R:
    """Run blocking work in a thread while bounding HTTP response latency.

    A response timeout returns a 504 promptly and leaves already-started
    thread work to finish in the background. If the HTTP request itself
    is cancelled, this helper waits for started blocking work to finish
    before propagating cancellation so upstream concurrency limiters
    track real worker occupancy. Python cannot forcibly terminate a
    worker thread; long-running callables should expose cooperative
    cancellation if execution cancellation is required.
    """
    measurement = _BlockingWorkMeasurement(operation=operation, submitted_at=time.perf_counter())
    _blocking_work_occupancy.submitted()
    blocking_task = asyncio.create_task(
        asyncio.to_thread(measurement.run, func, args, kwargs),
        name=f"haute-route-{operation}",
    )
    try:
        result = await asyncio.wait_for(asyncio.shield(blocking_task), timeout=timeout)
    except TimeoutError:
        measurement.mark_response_boundary("response_timeout")
        blocking_task.add_done_callback(
            lambda future: _drain_measured_background_future_result(future, measurement)
        )
        logger.warning("route_work_response_timeout", operation=operation, timeout_s=timeout)
        raise BlockingWorkTimeoutError(operation, timeout, blocking_task) from None
    except asyncio.CancelledError:
        _blocking_work_occupancy.cancellation_wait_started()
        measurement.mark_response_boundary("request_cancelled")
        try:
            while not blocking_task.done():
                try:
                    await asyncio.shield(blocking_task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            _drain_cancelled_future_result(blocking_task)
        finally:
            _blocking_work_occupancy.cancellation_wait_finished()
            measurement.log_after_cleanup()
        raise
    except BaseException:
        measurement.mark_response_boundary("failed")
        measurement.log_after_cleanup()
        raise
    measurement.mark_response_boundary("completed")
    measurement.log_after_cleanup()
    return result


def _drain_measured_background_future_result(
    future: asyncio.Future[Any],
    measurement: _BlockingWorkMeasurement,
) -> None:
    try:
        _drain_background_future_result(future)
    finally:
        measurement.log_after_cleanup()


def _drain_background_future_result(future: asyncio.Future[Any]) -> None:
    try:
        future.result()
    except Exception as exc:
        logger.error(
            "route_work_background_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )


def _drain_cancelled_future_result(future: asyncio.Future[Any]) -> None:
    """Observe every worker failure without replacing request cancellation."""
    try:
        future.result()
    except BaseException as exc:
        logger.error(
            "route_work_background_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
