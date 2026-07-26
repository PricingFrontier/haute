"""Shared timeout helpers for blocking route work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from haute._logging import get_logger

R = TypeVar("R")

logger = get_logger(component="server.timeouts")


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
    blocking_task = asyncio.create_task(
        asyncio.to_thread(func, *args, **kwargs),
        name=f"haute-route-{operation}",
    )
    try:
        return await asyncio.wait_for(asyncio.shield(blocking_task), timeout=timeout)
    except TimeoutError:
        blocking_task.add_done_callback(_drain_background_future_result)
        logger.warning("route_work_response_timeout", operation=operation, timeout_s=timeout)
        raise BlockingWorkTimeoutError(operation, timeout, blocking_task) from None
    except asyncio.CancelledError:
        while not blocking_task.done():
            try:
                await asyncio.shield(blocking_task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        _drain_cancelled_future_result(blocking_task)
        raise


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
