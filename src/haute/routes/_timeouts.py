"""Shared timeout helpers for blocking route work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from haute._logging import get_logger

R = TypeVar("R")

logger = get_logger(component="server.timeouts")


async def run_blocking_with_response_timeout(
    func: Callable[..., R],
    *args: Any,
    timeout: float,
    operation: str,
    **kwargs: Any,
) -> R:
    """Run blocking work in a thread while bounding HTTP response latency.

    The timeout cancels the awaiting coroutine and lets the API return a
    504 promptly. It cannot forcibly terminate a Python worker thread
    that has already started; long-running callables should expose their
    own cooperative cancellation if execution cancellation is required.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args, **kwargs),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning("route_work_response_timeout", operation=operation, timeout_s=timeout)
        raise
