"""Tests for server-side request supersession."""

from __future__ import annotations

import asyncio

import pytest

from haute.routes._supersession import SupersededRequestError, SupersessionCoordinator


@pytest.mark.asyncio
async def test_only_latest_waiting_request_runs() -> None:
    coordinator = SupersessionCoordinator()
    key = ("preview", "graph")
    started_first = asyncio.Event()
    release_first = asyncio.Event()
    started_work: list[str] = []

    async def work(name: str) -> str:
        started_work.append(name)
        if name == "first":
            started_first.set()
            await release_first.wait()
        return name

    first = asyncio.create_task(
        coordinator.run_latest(
            key,
            lambda: work("first"),
            superseded_message="superseded",
        )
    )
    await started_first.wait()

    second = asyncio.create_task(
        coordinator.run_latest(
            key,
            lambda: work("second"),
            superseded_message="superseded",
        )
    )
    await asyncio.sleep(0)

    third = asyncio.create_task(
        coordinator.run_latest(
            key,
            lambda: work("third"),
            superseded_message="superseded",
        )
    )
    await asyncio.sleep(0)

    release_first.set()

    with pytest.raises(SupersededRequestError):
        await first
    with pytest.raises(SupersededRequestError):
        await second
    assert await third == "third"
    assert started_work == ["first", "third"]


@pytest.mark.asyncio
async def test_superseded_running_worker_error_returns_superseded() -> None:
    coordinator = SupersessionCoordinator()
    key = ("preview", "graph")
    started_first = asyncio.Event()
    release_first = asyncio.Event()

    async def first_work() -> str:
        started_first.set()
        await release_first.wait()
        raise RuntimeError("old worker failed")

    async def second_work() -> str:
        return "latest"

    first = asyncio.create_task(
        coordinator.run_latest(
            key,
            first_work,
            superseded_message="superseded",
        )
    )
    await started_first.wait()

    second = asyncio.create_task(
        coordinator.run_latest(
            key,
            second_work,
            superseded_message="superseded",
        )
    )
    await asyncio.sleep(0)
    release_first.set()

    with pytest.raises(SupersededRequestError):
        await first
    assert await second == "latest"


@pytest.mark.asyncio
async def test_superseded_request_waiting_for_limiter_does_not_run() -> None:
    coordinator = SupersessionCoordinator()
    limiter = asyncio.Semaphore(1)
    await limiter.acquire()
    key = ("preview", "graph")
    started_work: list[str] = []

    async def work(name: str) -> str:
        started_work.append(name)
        return name

    obsolete = asyncio.create_task(
        coordinator.run_latest(
            key,
            lambda: work("obsolete"),
            limiter=limiter,
            superseded_message="superseded",
        )
    )
    await asyncio.sleep(0)

    latest = asyncio.create_task(
        coordinator.run_latest(
            key,
            lambda: work("latest"),
            limiter=limiter,
            superseded_message="superseded",
        )
    )
    await asyncio.sleep(0)

    limiter.release()

    with pytest.raises(SupersededRequestError):
        await obsolete
    assert await latest == "latest"
    assert started_work == ["latest"]


@pytest.mark.asyncio
async def test_superseded_limiter_acquire_race_releases_slot() -> None:
    coordinator = SupersessionCoordinator()
    key = ("preview", "graph")

    class RacingLimiter:
        def __init__(self) -> None:
            self.acquire_calls = 0
            self.releases = 0

        async def acquire(self) -> bool:
            self.acquire_calls += 1
            if self.acquire_calls == 1:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return True
            return True

        def release(self) -> None:
            self.releases += 1

    limiter = RacingLimiter()
    started_work: list[str] = []

    async def work(name: str) -> str:
        started_work.append(name)
        return name

    obsolete = asyncio.create_task(
        coordinator.run_latest(
            key,
            lambda: work("obsolete"),
            limiter=limiter,  # type: ignore[arg-type]
            superseded_message="superseded",
        )
    )
    await asyncio.sleep(0)

    latest = asyncio.create_task(
        coordinator.run_latest(
            key,
            lambda: work("latest"),
            limiter=limiter,  # type: ignore[arg-type]
            superseded_message="superseded",
        )
    )
    await asyncio.sleep(0)

    with pytest.raises(SupersededRequestError):
        await obsolete
    assert await latest == "latest"
    assert started_work == ["latest"]
    assert limiter.releases == 2


@pytest.mark.asyncio
async def test_cancelled_request_waiting_for_limiter_does_not_leak_slot() -> None:
    coordinator = SupersessionCoordinator()
    limiter = asyncio.Semaphore(1)
    await limiter.acquire()
    started_work: list[str] = []

    async def work() -> str:
        started_work.append("cancelled")
        return "cancelled"

    queued = asyncio.create_task(
        coordinator.run_latest(
            ("preview", "graph"),
            work,
            limiter=limiter,
            superseded_message="superseded",
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued

    limiter.release()
    await asyncio.sleep(0)

    await asyncio.wait_for(limiter.acquire(), timeout=0.1)
    limiter.release()
    assert started_work == []
