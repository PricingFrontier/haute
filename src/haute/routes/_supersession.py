"""Bounded supersession for route-level preview/trace work."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from typing import TypeVar

R = TypeVar("R")
T = TypeVar("T")


class SupersededRequestError(RuntimeError):
    """Raised when a request is replaced by a newer same-key request."""


@dataclass
class _SupersessionState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    latest_generation: int = 0
    active: bool = False
    references: int = 0
    active_cancel: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class SupersessionStateSnapshot:
    """Immutable, test-facing view of one coordinator state."""

    latest_generation: int
    active: bool
    references: int


class SupersessionCoordinator:
    """Keep at most one running and one latest waiting request per key."""

    def __init__(self) -> None:
        self._states: dict[Hashable, _SupersessionState] = {}
        self._states_lock = asyncio.Lock()

    async def run_latest(
        self,
        key: Hashable,
        worker: Callable[[], Awaitable[R]],
        *,
        limiter: asyncio.Semaphore | None = None,
        operation: str | None = None,
        superseded_message: str | None = None,
        cancel_active: Callable[[], None] | None = None,
    ) -> R:
        state = await self._retain_state(key)
        generation: int | None = None
        message = superseded_message or (
            f"{operation or 'Route'} request superseded by a newer request "
            "for the same graph/source"
        )
        try:
            async with state.condition:
                state.latest_generation += 1
                generation = state.latest_generation
                if state.active and state.active_cancel is not None:
                    state.active_cancel()
                state.condition.notify_all()

                while state.active:
                    if generation != state.latest_generation:
                        raise SupersededRequestError(message)
                    await state.condition.wait()

                if generation != state.latest_generation:
                    raise SupersededRequestError(message)

            limiter_acquired = False
            active = False
            deferred_limiter_release: asyncio.Future[object] | None = None
            try:
                if limiter is not None:
                    await self._acquire_limiter_unless_superseded(
                        limiter,
                        state,
                        generation,
                        message,
                    )
                    limiter_acquired = True

                async with state.condition:
                    while state.active:
                        if generation != state.latest_generation:
                            raise SupersededRequestError(message)
                        await state.condition.wait()

                    if generation != state.latest_generation:
                        raise SupersededRequestError(message)
                    state.active = True
                    state.active_cancel = cancel_active
                    active = True

                worker_error: Exception | None = None
                result_box: list[R] = []
                try:
                    result_box.append(await worker())
                except Exception as exc:
                    worker_error = exc
                    deferred_limiter_release = _background_task_from_error(exc)
                finally:
                    if deferred_limiter_release is None:
                        async with state.condition:
                            state.active = False
                            active = False
                            if state.active_cancel is cancel_active:
                                state.active_cancel = None
                            state.condition.notify_all()
                    else:
                        active = False
                        self._clear_active_when_done(
                            deferred_limiter_release,
                            key,
                            state,
                            cancel_active,
                        )

                async with state.condition:
                    if generation != state.latest_generation:
                        raise SupersededRequestError(message)

                if worker_error is not None:
                    raise worker_error
                return result_box[0]
            finally:
                if active:
                    async with state.condition:
                        state.active = False
                        if state.active_cancel is cancel_active:
                            state.active_cancel = None
                        state.condition.notify_all()
                if limiter_acquired and limiter is not None:
                    if deferred_limiter_release is None:
                        limiter.release()
                    else:
                        _release_limiter_when_done(deferred_limiter_release, limiter)
        finally:
            await self._release_state(key, state)

    async def snapshot_for_tests(self) -> dict[Hashable, SupersessionStateSnapshot]:
        """Return a copied, immutable view of coordinator state for tests."""
        async with self._states_lock:
            snapshot: dict[Hashable, SupersessionStateSnapshot] = {}
            for key, state in self._states.items():
                async with state.condition:
                    snapshot[key] = SupersessionStateSnapshot(
                        latest_generation=state.latest_generation,
                        active=state.active,
                        references=state.references,
                    )
            return snapshot

    async def _acquire_limiter_unless_superseded(
        self,
        limiter: asyncio.Semaphore,
        state: _SupersessionState,
        generation: int,
        message: str,
    ) -> None:
        acquire_task = asyncio.create_task(limiter.acquire())
        superseded_task = asyncio.create_task(
            self._wait_until_superseded(state, generation),
        )
        acquired = False
        try:
            done, _ = await asyncio.wait(
                {acquire_task, superseded_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if acquire_task in done:
                acquire_result = acquire_task.result()
                if acquire_result is not True:
                    raise RuntimeError("Concurrency limiter acquire returned false")
                acquired = True

            if superseded_task in done:
                if acquired:
                    limiter.release()
                    acquired = False
                raise SupersededRequestError(message)

            await self._cancel_and_drain(superseded_task)
        except BaseException:
            drained_acquire_result = await self._cancel_and_drain(acquire_task)
            if drained_acquire_result is True and not acquired:
                acquired = True
            await self._cancel_and_drain(superseded_task)
            if acquired:
                limiter.release()
            raise

    @staticmethod
    async def _cancel_and_drain(task: asyncio.Task[T]) -> T | BaseException:
        if not task.done():
            task.cancel()
        (result,) = await asyncio.gather(task, return_exceptions=True)
        return result

    @staticmethod
    async def _wait_until_superseded(
        state: _SupersessionState,
        generation: int,
    ) -> None:
        async with state.condition:
            while generation == state.latest_generation:
                await state.condition.wait()

    async def _retain_state(self, key: Hashable) -> _SupersessionState:
        async with self._states_lock:
            state = self._states.get(key)
            if state is None:
                state = _SupersessionState()
                self._states[key] = state
            state.references += 1
            return state

    async def _release_state(self, key: Hashable, state: _SupersessionState) -> None:
        async with self._states_lock:
            state.references -= 1
            if state.references == 0 and not state.active and self._states.get(key) is state:
                del self._states[key]

    def _clear_active_when_done(
        self,
        future: asyncio.Future[object],
        key: Hashable,
        state: _SupersessionState,
        cancel_active: Callable[[], None] | None,
    ) -> None:
        if future.done():
            asyncio.create_task(self._clear_active_after_background(key, state, cancel_active))
            return
        future.add_done_callback(
            lambda _done: asyncio.create_task(
                self._clear_active_after_background(key, state, cancel_active)
            )
        )

    async def _clear_active_after_background(
        self,
        key: Hashable,
        state: _SupersessionState,
        cancel_active: Callable[[], None] | None,
    ) -> None:
        async with state.condition:
            state.active = False
            if state.active_cancel is cancel_active:
                state.active_cancel = None
            state.condition.notify_all()
        async with self._states_lock:
            if state.references == 0 and not state.active and self._states.get(key) is state:
                del self._states[key]


def _background_task_from_error(exc: BaseException) -> asyncio.Future[object] | None:
    background_task = getattr(exc, "background_task", None)
    if isinstance(background_task, asyncio.Future):
        return background_task
    return None


def _release_limiter_when_done(
    future: asyncio.Future[object],
    limiter: asyncio.Semaphore,
) -> None:
    if future.done():
        limiter.release()
        return
    future.add_done_callback(lambda _done: limiter.release())
