"""Cancellation-safe asyncio bridge for one-shot isolated workers."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, TypeVar

from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerStoppedError,
    run_isolated_worker,
)

T = TypeVar("T")


class WorkerCancellationGate:
    """Linearize request cancellation against an irreversible parent commit."""

    def __init__(self) -> None:
        self._requested = threading.Event()
        self._publication_lock = threading.Lock()

    def is_set(self) -> bool:
        """Return whether cancellation won before a publication section began."""
        return self._requested.is_set()

    def request(self) -> None:
        """Record cancellation at the same serialization point as publication."""
        with self._publication_lock:
            self._requested.set()

    @contextmanager
    def publication_guard(self) -> Iterator[None]:
        """Reject a late commit, or serialize cancellation after this commit."""
        with self._publication_lock:
            if self._requested.is_set():
                raise IsolatedWorkerStoppedError(terminal_reason="cancelled")
            yield


async def run_cancellable_worker_transaction(
    transaction: Callable[[WorkerCancellationGate], T],
    *,
    task_name: str,
) -> T:
    """Run a parent supervisor transaction and drain it before cancellation."""
    cancellation_requested = WorkerCancellationGate()
    task = asyncio.create_task(
        asyncio.to_thread(transaction, cancellation_requested),
        name=task_name,
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        cancellation_requested.request()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            task.result()
        except IsolatedWorkerStoppedError as exc:
            if exc.terminal_reason != "cancelled":
                cancellation.add_note(f"isolated worker stopped as {exc.terminal_reason}")
        except BaseException as exc:
            cancellation.add_note(
                f"isolated worker finalization after request cancellation failed: {exc}"
            )
        raise


async def run_isolated_worker_async(
    function: Callable[..., T],
    *args: Any,
    config: IsolatedWorkerConfig,
    **kwargs: Any,
) -> T:
    """Run a spawn worker off-loop and join it before propagating cancellation."""
    configured_stop_reason = config.stop_reason

    def transaction(cancellation_requested: WorkerCancellationGate) -> T:
        def stop_reason() -> Any:
            if cancellation_requested.is_set():
                return "cancelled"
            if configured_stop_reason is None:
                return None
            return configured_stop_reason()

        effective_config = replace(config, stop_reason=stop_reason)
        return run_isolated_worker(
            function,
            *args,
            config=effective_config,
            **kwargs,
        )

    return await run_cancellable_worker_transaction(
        transaction,
        task_name=f"{config.process_name}-supervisor",
    )
