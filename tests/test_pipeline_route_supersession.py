"""Supersession tests for rapid preview / trace route requests."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Hashable
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from typing import TypeAlias

import httpx
import pytest

from haute.routes._supersession import SupersessionCoordinator, SupersessionStateSnapshot
from haute.schemas import NodeResult
from haute.trace import TraceResult
from tests.conftest import make_graph, make_transform_node

_Snapshot: TypeAlias = dict[Hashable, SupersessionStateSnapshot]


def _single_node_graph() -> dict:
    graph = make_graph({"nodes": [make_transform_node("target")], "edges": []})
    return graph.model_dump()


def _two_node_graph() -> dict:
    graph = make_graph(
        {
            "nodes": [make_transform_node("target_a"), make_transform_node("target_b")],
            "edges": [],
        }
    )
    return graph.model_dump()


async def _post_many_same_key(
    post: Callable[[], Awaitable[httpx.Response]],
    *,
    first_started: threading.Event,
    count: int = 5,
) -> list[httpx.Response]:
    first = asyncio.create_task(post())
    await asyncio.to_thread(first_started.wait, 2)
    rest = [asyncio.create_task(post()) for _ in range(count - 1)]
    return await asyncio.gather(first, *rest)


async def _wait_for_thread_event(event: threading.Event, label: str) -> None:
    assert await asyncio.to_thread(event.wait, 2), f"timed out waiting for {label}"


async def _thread_event_was_set(event: threading.Event, timeout: float) -> bool:
    return await asyncio.to_thread(event.wait, timeout)


async def _wait_for_coordinator_snapshot(
    coordinator: SupersessionCoordinator,
    predicate: Callable[[_Snapshot], bool],
    label: str,
    *,
    timeout: float = 2,
    interval: float = 0.005,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_snapshot: _Snapshot = {}
    while True:
        last_snapshot = await coordinator.snapshot_for_tests()
        if predicate(last_snapshot):
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            pytest.fail(f"timed out waiting for {label}; last snapshot={last_snapshot!r}")
        await asyncio.sleep(min(interval, remaining))


def _snapshot_has_source(
    snapshot: _Snapshot,
    source: str,
    *,
    min_generation: int = 1,
) -> bool:
    return any(
        isinstance(key, tuple)
        and len(key) >= 3
        and key[2] == source
        and state.latest_generation >= min_generation
        for key, state in snapshot.items()
    )


@pytest.mark.asyncio
async def test_supersession_snapshot_for_tests_is_copied_and_immutable() -> None:
    coordinator = SupersessionCoordinator()
    worker_started = asyncio.Event()
    release_worker = asyncio.Event()

    async def worker() -> str:
        worker_started.set()
        await release_worker.wait()
        return "ok"

    task = asyncio.create_task(coordinator.run_latest("key", worker))
    try:
        await asyncio.wait_for(worker_started.wait(), timeout=1)

        snapshot = await coordinator.snapshot_for_tests()
        assert set(snapshot) == {"key"}
        assert snapshot["key"].latest_generation == 1
        assert snapshot["key"].active is True
        assert snapshot["key"].references == 1

        with pytest.raises(FrozenInstanceError):
            snapshot["key"].active = False
        snapshot.clear()
        assert set(await coordinator.snapshot_for_tests()) == {"key"}
    finally:
        release_worker.set()

    assert await task == "ok"
    assert await coordinator.snapshot_for_tests() == {}


@pytest.mark.asyncio
async def test_preview_supersedes_obsolete_same_key_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the first running and latest pending preview should execute."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    first_started = threading.Event()
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, call_count, max_active
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
        first_started.set()
        try:
            time.sleep(0.2)
            return {
                "target": NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"x": call_count}],
                )
            }
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post() -> httpx.Response:
            return await ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
            )

        responses = await _post_many_same_key(post, first_started=first_started)

    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1
    assert statuses.count(409) == 4
    assert call_count == 2
    assert max_active == 1
    assert all(
        "superseded" in r.json()["detail"].lower() for r in responses if r.status_code == 409
    )


@pytest.mark.asyncio
async def test_preview_supersession_cancels_active_execution_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer same-key preview should request cooperative cancellation."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    first_started = threading.Event()
    cancel_seen = threading.Event()
    call_count = 0
    state_lock = threading.Lock()

    def cancellable_preview(*args, **kwargs) -> dict[str, NodeResult]:
        del args
        nonlocal call_count
        target = kwargs["target_node_id"]
        execution_context = kwargs["execution_context"]
        with state_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_started.set()
            while not execution_context.cancellation_token.cancelled:
                time.sleep(0.005)
            cancel_seen.set()
            execution_context.checkpoint(label="superseded", node_id=target)
        return {
            target: NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                preview=[{"call": current_call}],
            )
        }

    monkeypatch.setattr(route_mod, "execute_graph", cancellable_preview)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        first = asyncio.create_task(
            ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
            )
        )
        await _wait_for_thread_event(first_started, "first preview worker")
        second = asyncio.create_task(
            ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
            )
        )

        first_response, second_response = await asyncio.gather(first, second)

    assert first_response.status_code == 409
    assert second_response.status_code == 200
    assert cancel_seen.is_set()
    assert second_response.json()["preview"] == [{"call": 2}]


@pytest.mark.asyncio
async def test_preview_returns_404_when_executor_omits_target_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.routes.pipeline as route_mod
    from haute.server import app

    monkeypatch.setattr(route_mod, "execute_graph", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        route_mod,
        "temporary_streaming_chunk_size",
        lambda _chunk_size: nullcontext(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/api/pipeline/preview",
            json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Node 'target' not found in results"


@pytest.mark.asyncio
async def test_preview_admission_failure_still_cancels_active_same_key_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer request must cancel active work before its own admission check fails."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "64")
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB", "64")
    rss_calls = 0

    def admission_rss() -> int:
        nonlocal rss_calls
        rss_calls += 1
        if rss_calls == 1:
            return 1 * 1024 * 1024
        return 65 * 1024 * 1024

    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", admission_rss)

    first_started = threading.Event()
    cancel_seen = threading.Event()
    call_count = 0
    state_lock = threading.Lock()

    def cancellable_preview(*args, **kwargs) -> dict[str, NodeResult]:
        del args
        nonlocal call_count
        target = kwargs["target_node_id"]
        execution_context = kwargs["execution_context"]
        with state_lock:
            call_count += 1
            current_call = call_count
        first_started.set()
        while not execution_context.cancellation_token.cancelled:
            time.sleep(0.005)
        cancel_seen.set()
        return {
            target: NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                preview=[{"call": current_call}],
            )
        }

    monkeypatch.setattr(route_mod, "execute_graph", cancellable_preview)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        first = asyncio.create_task(
            ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
            )
        )
        await _wait_for_thread_event(first_started, "first preview worker")
        second = asyncio.create_task(
            ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
            )
        )

        first_response, second_response = await asyncio.gather(first, second)

    assert first_response.status_code == 409
    assert second_response.status_code == 507
    assert second_response.json()["detail"]["error_code"] == "memory_limit"
    assert cancel_seen.is_set()
    assert call_count == 1


@pytest.mark.asyncio
async def test_preview_limits_blocking_workers_across_distinct_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different preview keys should queue behind the configured worker limit."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    monkeypatch.setattr(route_mod, "_preview_work_slots", asyncio.Semaphore(2))
    monkeypatch.setattr(
        route_mod,
        "temporary_streaming_chunk_size",
        lambda _chunk_size: nullcontext(),
    )

    limit_reached = threading.Event()
    extra_worker_started_before_release = threading.Event()
    release_workers = threading.Event()
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, call_count, max_active
        target = kwargs["target_node_id"]
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
            if call_count == 2:
                limit_reached.set()
            elif call_count > 2 and not release_workers.is_set():
                extra_worker_started_before_release.set()
        try:
            release_workers.wait(2)
            return {
                target: NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"source": kwargs["source"]}],
                )
            }
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post(source: str) -> httpx.Response:
            return await ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": source},
            )

        tasks = [asyncio.create_task(post(f"live-{idx}")) for idx in range(3)]
        await _wait_for_thread_event(limit_reached, "two preview workers")
        assert not await _thread_event_was_set(extra_worker_started_before_release, 0.05)

        with state_lock:
            assert call_count == 2
            assert max_active == 2

        release_workers.set()
        responses = await asyncio.gather(*tasks)

    assert [r.status_code for r in responses] == [200, 200, 200]
    with state_lock:
        assert call_count == 3
        assert max_active == 2


@pytest.mark.asyncio
async def test_preview_targets_use_distinct_supersession_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different preview targets on the same graph/source should not 409 each other."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    monkeypatch.setattr(route_mod, "_preview_work_slots", asyncio.Semaphore(1))

    first_started = threading.Event()
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, call_count, max_active
        target = kwargs["target_node_id"]
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
        first_started.set()
        try:
            time.sleep(0.2)
            return {
                target: NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"x": call_count}],
                )
            }
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post_a() -> httpx.Response:
            return await ac.post(
                "/api/pipeline/preview",
                json={"graph": _two_node_graph(), "node_id": "target_a", "source": "live"},
            )

        async def post_b() -> httpx.Response:
            return await ac.post(
                "/api/pipeline/preview",
                json={"graph": _two_node_graph(), "node_id": "target_b", "source": "live"},
            )

        first = asyncio.create_task(post_a())
        await asyncio.to_thread(first_started.wait, 2)
        second = asyncio.create_task(post_b())
        responses = await asyncio.gather(first, second)

    assert [r.status_code for r in responses] == [200, 200]
    assert call_count == 2
    assert max_active == 1


@pytest.mark.asyncio
async def test_preview_requested_columns_use_distinct_supersession_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different same-node preview projections should both be allowed to complete."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    monkeypatch.setattr(route_mod, "_preview_work_slots", asyncio.Semaphore(1))

    first_started = threading.Event()
    state_lock = threading.Lock()
    requested_columns_seen: list[tuple[str, ...]] = []
    active = 0
    max_active = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, max_active
        requested = tuple(kwargs["requested_preview_columns"] or ())
        with state_lock:
            requested_columns_seen.append(requested)
            active += 1
            max_active = max(max_active, active)
        first_started.set()
        try:
            time.sleep(0.2)
            return {
                "target": NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"projection": ",".join(requested)}],
                )
            }
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post(columns: list[str]) -> httpx.Response:
            return await ac.post(
                "/api/pipeline/preview",
                json={
                    "graph": _single_node_graph(),
                    "node_id": "target",
                    "source": "live",
                    "requested_preview_columns": columns,
                },
            )

        first = asyncio.create_task(post(["quote_id"]))
        await asyncio.to_thread(first_started.wait, 2)
        second = asyncio.create_task(post(["price"]))
        responses = await asyncio.gather(first, second)

    assert [r.status_code for r in responses] == [200, 200]
    with state_lock:
        assert requested_columns_seen == [("quote_id",), ("price",)]
        assert max_active == 1


@pytest.mark.asyncio
async def test_preview_supersession_wins_over_obsolete_worker_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old preview failure should not surface after a newer preview exists."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    first_started = threading.Event()
    state_lock = threading.Lock()
    call_count = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal call_count
        target = kwargs["target_node_id"]
        with state_lock:
            call_count += 1
            call_number = call_count
        first_started.set()
        time.sleep(0.2)
        if call_number == 1:
            raise RuntimeError("obsolete preview failed")
        return {
            target: NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                preview=[{"x": call_number}],
            )
        }

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post() -> httpx.Response:
            return await ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
            )

        first = asyncio.create_task(post())
        await asyncio.to_thread(first_started.wait, 2)
        second = asyncio.create_task(post())
        responses = await asyncio.gather(first, second)

    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1
    assert statuses.count(409) == 1
    assert call_count == 2
    assert all("obsolete preview failed" not in r.text for r in responses)


@pytest.mark.asyncio
async def test_preview_worker_limit_serializes_different_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preview worker cap applies even when requests have different keys."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, call_count, max_active
        target = kwargs["target_node_id"]
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.2)
            return {
                target: NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"x": call_count}],
                )
            }
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)
    monkeypatch.setattr(route_mod, "_preview_work_slots", asyncio.Semaphore(1))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post(source: str) -> httpx.Response:
            return await ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": source},
            )

        responses = await asyncio.gather(post("live"), post("batch"))

    assert [r.status_code for r in responses] == [200, 200]
    assert call_count == 2
    assert max_active == 1


@pytest.mark.asyncio
async def test_preview_supersedes_request_waiting_for_worker_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview superseded before getting a worker slot must not execute."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    monkeypatch.setattr(route_mod, "_preview_work_slots", asyncio.Semaphore(1))
    coordinator = SupersessionCoordinator()
    monkeypatch.setattr(route_mod, "_preview_supersession", coordinator)

    holder_started = threading.Event()
    release_holder = threading.Event()
    state_lock = threading.Lock()
    live_call_count = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal live_call_count
        target = kwargs["target_node_id"]
        source = kwargs["source"]
        if source == "holder":
            holder_started.set()
            release_holder.wait(2)
        else:
            with state_lock:
                live_call_count += 1
        return {
            target: NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                preview=[{"source": source}],
            )
        }

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post(source: str) -> httpx.Response:
            return await ac.post(
                "/api/pipeline/preview",
                json={"graph": _single_node_graph(), "node_id": "target", "source": source},
            )

        holder = asyncio.create_task(post("holder"))
        await _wait_for_thread_event(holder_started, "holder preview worker")

        obsolete = asyncio.create_task(post("live"))
        await _wait_for_coordinator_snapshot(
            coordinator,
            lambda snapshot: _snapshot_has_source(snapshot, "live"),
            "obsolete live preview to wait for worker slot",
        )
        latest = asyncio.create_task(post("live"))
        await _wait_for_coordinator_snapshot(
            coordinator,
            lambda snapshot: _snapshot_has_source(snapshot, "live", min_generation=2),
            "latest live preview to supersede obsolete queued preview",
        )

        release_holder.set()
        responses = await asyncio.gather(holder, obsolete, latest)

    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 2
    assert statuses.count(409) == 1
    with state_lock:
        assert live_call_count == 1


@pytest.mark.asyncio
async def test_repeated_aborted_preview_requests_do_not_start_worker_storm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelled preview requests must not release the cap before workers finish."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    limiter = asyncio.Semaphore(1)
    coordinator = SupersessionCoordinator()
    monkeypatch.setattr(route_mod, "_preview_work_slots", limiter)
    monkeypatch.setattr(route_mod, "_preview_supersession", coordinator)

    aborted_sources = ["first", "queued"] + [f"queued-{idx}" for idx in range(6)]
    sources = aborted_sources + ["success"]
    started = {source: threading.Event() for source in sources}
    release = {source: threading.Event() for source in sources}
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, call_count, max_active
        target = kwargs["target_node_id"]
        source = kwargs["source"]
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
        started[source].set()
        try:
            release[source].wait(2)
            return {
                target: NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"source": source}],
                )
            }
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

            async def post(source: str) -> httpx.Response:
                return await ac.post(
                    "/api/pipeline/preview",
                    json={"graph": _single_node_graph(), "node_id": "target", "source": source},
                )

            first = asyncio.create_task(post("first"))
            await _wait_for_thread_event(started["first"], "first preview worker")

            first.cancel()
            queued = asyncio.create_task(post("queued"))
            await _wait_for_coordinator_snapshot(
                coordinator,
                lambda snapshot: _snapshot_has_source(snapshot, "queued"),
                "queued preview request to wait for worker slot",
            )
            assert not await _thread_event_was_set(started["queued"], 0.05)
            queued.cancel()

            queued_aborts = []
            for source in aborted_sources[2:]:
                task = asyncio.create_task(post(source))
                await _wait_for_coordinator_snapshot(
                    coordinator,
                    lambda snapshot, source=source: _snapshot_has_source(snapshot, source),
                    f"{source} preview request to wait for worker slot",
                )
                task.cancel()
                queued_aborts.append(task)

            assert all(not started[source].is_set() for source in aborted_sources[1:])
            release["first"].set()

            cancelled_results = await asyncio.gather(
                first,
                queued,
                *queued_aborts,
                return_exceptions=True,
            )
            assert all(isinstance(result, asyncio.CancelledError) for result in cancelled_results)

            with state_lock:
                assert call_count == 1
                assert max_active == 1
                assert active == 0
            assert await coordinator.snapshot_for_tests() == {}

            success = asyncio.create_task(post("success"))
            await _wait_for_thread_event(started["success"], "success preview worker")
            release["success"].set()
            response = await success

        assert response.status_code == 200
        assert await coordinator.snapshot_for_tests() == {}
        await asyncio.wait_for(limiter.acquire(), timeout=0.1)
        limiter.release()
    finally:
        for event in release.values():
            event.set()


@pytest.mark.asyncio
async def test_timed_out_preview_requests_hold_slot_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 504 response must not free the preview slot while its thread still runs."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    limiter = asyncio.Semaphore(1)
    coordinator = SupersessionCoordinator()
    monkeypatch.setenv("HAUTE_PREVIEW_TIMEOUT", "0.05")
    monkeypatch.setattr(route_mod, "_preview_work_slots", limiter)
    monkeypatch.setattr(route_mod, "_preview_supersession", coordinator)

    sources = ("first", "second", "third")
    started = {source: threading.Event() for source in sources}
    finished = {source: threading.Event() for source in sources}
    release = {source: threading.Event() for source in sources}
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, call_count, max_active
        target = kwargs["target_node_id"]
        source = kwargs["source"]
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
        started[source].set()
        try:
            release[source].wait(2)
            return {
                target: NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"source": source}],
                )
            }
        finally:
            with state_lock:
                active -= 1
            finished[source].set()

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

            async def post(source: str) -> httpx.Response:
                return await ac.post(
                    "/api/pipeline/preview",
                    json={"graph": _single_node_graph(), "node_id": "target", "source": source},
                )

            first = asyncio.create_task(post("first"))
            await _wait_for_thread_event(started["first"], "first preview worker")

            second = asyncio.create_task(post("second"))
            first_response = await first
            assert first_response.status_code == 504
            assert not await _thread_event_was_set(started["second"], 0.05)
            with state_lock:
                assert active == 1

            release["first"].set()
            await _wait_for_thread_event(started["second"], "second preview worker")

            third = asyncio.create_task(post("third"))
            second_response = await second
            assert second_response.status_code == 504
            assert not await _thread_event_was_set(started["third"], 0.05)
            with state_lock:
                assert active == 1

            release["second"].set()
            await _wait_for_thread_event(started["third"], "third preview worker")

            third_response = await third
            assert third_response.status_code == 504
            with state_lock:
                assert active == 1

            release["third"].set()
            await _wait_for_thread_event(finished["third"], "third preview completion")

        await _wait_for_coordinator_snapshot(
            coordinator,
            lambda snapshot: snapshot == {},
            "timed-out preview worker cleanup",
        )
        await asyncio.wait_for(limiter.acquire(), timeout=0.1)
        limiter.release()
        with state_lock:
            assert call_count == 3
            assert max_active == 1
            assert active == 0
    finally:
        for event in release.values():
            event.set()


@pytest.mark.asyncio
async def test_timed_out_same_key_preview_stays_active_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-key previews must not overlap after a response timeout."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    coordinator = SupersessionCoordinator()
    monkeypatch.setenv("HAUTE_PREVIEW_TIMEOUT", "0.05")
    monkeypatch.setattr(route_mod, "_preview_work_slots", asyncio.Semaphore(2))
    monkeypatch.setattr(route_mod, "_preview_supersession", coordinator)

    started = {1: threading.Event(), 2: threading.Event()}
    finished = {1: threading.Event(), 2: threading.Event()}
    release = {1: threading.Event(), 2: threading.Event()}
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, call_count, max_active
        target = kwargs["target_node_id"]
        with state_lock:
            call_count += 1
            call_number = call_count
            active += 1
            max_active = max(max_active, active)
        started[call_number].set()
        try:
            release[call_number].wait(2)
            return {
                target: NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"call": call_number}],
                )
            }
        finally:
            with state_lock:
                active -= 1
            finished[call_number].set()

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

            async def post() -> httpx.Response:
                return await ac.post(
                    "/api/pipeline/preview",
                    json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
                )

            first = asyncio.create_task(post())
            await _wait_for_thread_event(started[1], "first same-key preview worker")
            first_response = await first
            assert first_response.status_code == 504

            second = asyncio.create_task(post())
            assert not await _thread_event_was_set(started[2], 0.05)
            with state_lock:
                assert active == 1

            release[1].set()
            await _wait_for_thread_event(finished[1], "first same-key preview completion")
            await _wait_for_thread_event(started[2], "second same-key preview worker")
            second_response = await second
            assert second_response.status_code == 504

            release[2].set()
            await _wait_for_thread_event(finished[2], "second same-key preview completion")

        await _wait_for_coordinator_snapshot(
            coordinator,
            lambda snapshot: snapshot == {},
            "timed-out same-key preview cleanup",
        )
        with state_lock:
            assert call_count == 2
            assert max_active == 1
            assert active == 0
    finally:
        for event in release.values():
            event.set()


@pytest.mark.asyncio
async def test_superseded_timed_out_preview_holds_slot_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Supersession must preserve timeout ownership until the old worker exits."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    limiter = asyncio.Semaphore(1)
    coordinator = SupersessionCoordinator()
    monkeypatch.setenv("HAUTE_PREVIEW_TIMEOUT", "0.05")
    monkeypatch.setattr(route_mod, "_preview_work_slots", limiter)
    monkeypatch.setattr(route_mod, "_preview_supersession", coordinator)

    started = {1: threading.Event(), 2: threading.Event()}
    finished = {1: threading.Event(), 2: threading.Event()}
    release = {1: threading.Event(), 2: threading.Event()}
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0
    contexts: list[_TrackedExecutionContext] = []

    class _TrackedExecutionContext:
        def __init__(self) -> None:
            self.release_calls = 0

        def release_admission(self, *, preserve_primary_error: bool = False) -> None:
            del preserve_primary_error
            self.release_calls += 1

    def _create_context(**kwargs) -> _TrackedExecutionContext:
        del kwargs
        context = _TrackedExecutionContext()
        contexts.append(context)
        return context

    def slow_preview(*args, **kwargs) -> dict[str, NodeResult]:
        nonlocal active, call_count, max_active
        target = kwargs["target_node_id"]
        with state_lock:
            call_count += 1
            call_number = call_count
            active += 1
            max_active = max(max_active, active)
        started[call_number].set()
        try:
            release[call_number].wait(2)
            return {
                target: NodeResult(
                    status="ok",
                    row_count=1,
                    column_count=1,
                    preview=[{"call": call_number}],
                )
            }
        finally:
            with state_lock:
                active -= 1
            finished[call_number].set()

    monkeypatch.setattr(route_mod, "execute_graph", slow_preview)
    monkeypatch.setattr(route_mod, "create_admitted_execution_context", _create_context)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

            async def post() -> httpx.Response:
                return await ac.post(
                    "/api/pipeline/preview",
                    json={"graph": _single_node_graph(), "node_id": "target", "source": "live"},
                )

            first = asyncio.create_task(post())
            await _wait_for_thread_event(started[1], "first same-key preview worker")

            second = asyncio.create_task(post())
            await _wait_for_coordinator_snapshot(
                coordinator,
                lambda snapshot: any(state.latest_generation >= 2 for state in snapshot.values()),
                "second same-key preview to enter supersession queue",
            )
            first_response = await first
            assert first_response.status_code == 504
            assert contexts[0].release_calls == 0
            assert not await _thread_event_was_set(started[2], 0.05)
            with state_lock:
                assert active == 1

            release[1].set()
            await _wait_for_thread_event(started[2], "second same-key preview worker")
            for _ in range(100):
                if contexts[0].release_calls == 1:
                    break
                await asyncio.sleep(0.005)
            assert contexts[0].release_calls == 1

            second_response = await second
            assert second_response.status_code == 504
            assert contexts[1].release_calls == 0
            with state_lock:
                assert active == 1

            release[2].set()
            await _wait_for_thread_event(finished[2], "second preview completion")
            for _ in range(100):
                if contexts[1].release_calls == 1:
                    break
                await asyncio.sleep(0.005)
            assert contexts[1].release_calls == 1

        await _wait_for_coordinator_snapshot(
            coordinator,
            lambda snapshot: snapshot == {},
            "superseded timed-out preview cleanup",
        )
        await asyncio.wait_for(limiter.acquire(), timeout=0.1)
        limiter.release()
        with state_lock:
            assert call_count == 2
            assert max_active == 1
            assert active == 0
    finally:
        for event in release.values():
            event.set()


@pytest.mark.asyncio
async def test_trace_supersedes_obsolete_same_key_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the first running and latest pending trace should execute."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    first_started = threading.Event()
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_trace(*args, **kwargs) -> TraceResult:
        nonlocal active, call_count, max_active
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
        first_started.set()
        try:
            time.sleep(0.2)
            return TraceResult(
                target_node_id="target",
                row_index=0,
                column=None,
                output_value={"x": call_count},
                steps=[],
                total_nodes_in_pipeline=1,
                nodes_in_trace=0,
            )
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_trace", slow_trace)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post() -> httpx.Response:
            return await ac.post(
                "/api/pipeline/trace",
                json={
                    "graph": _single_node_graph(),
                    "target_node_id": "target",
                    "row_index": 0,
                    "source": "live",
                },
            )

        responses = await _post_many_same_key(post, first_started=first_started)

    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1
    assert statuses.count(409) == 4
    assert call_count == 2
    assert max_active == 1
    assert all(
        "superseded" in r.json()["detail"].lower() for r in responses if r.status_code == 409
    )


@pytest.mark.asyncio
async def test_trace_targets_use_distinct_supersession_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different trace targets on the same graph/source should not 409 each other."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    monkeypatch.setattr(route_mod, "_trace_work_slots", asyncio.Semaphore(1))

    first_started = threading.Event()
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_trace(*args, **kwargs) -> TraceResult:
        nonlocal active, call_count, max_active
        target = kwargs["target_node_id"]
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
        first_started.set()
        try:
            time.sleep(0.2)
            return TraceResult(
                target_node_id=target,
                row_index=0,
                column=None,
                output_value={"x": call_count},
                steps=[],
                total_nodes_in_pipeline=2,
                nodes_in_trace=0,
            )
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_trace", slow_trace)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post(target: str) -> httpx.Response:
            return await ac.post(
                "/api/pipeline/trace",
                json={
                    "graph": _two_node_graph(),
                    "target_node_id": target,
                    "row_index": 0,
                    "source": "live",
                },
            )

        first = asyncio.create_task(post("target_a"))
        await asyncio.to_thread(first_started.wait, 2)
        second = asyncio.create_task(post("target_b"))
        responses = await asyncio.gather(first, second)

    assert [r.status_code for r in responses] == [200, 200]
    assert call_count == 2
    assert max_active == 1


@pytest.mark.asyncio
async def test_trace_limits_blocking_workers_across_distinct_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different trace keys should queue behind the configured worker limit."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    monkeypatch.setattr(route_mod, "_trace_work_slots", asyncio.Semaphore(2))
    monkeypatch.setattr(
        route_mod,
        "temporary_streaming_chunk_size",
        lambda _chunk_size: nullcontext(),
    )

    limit_reached = threading.Event()
    extra_worker_started_before_release = threading.Event()
    release_workers = threading.Event()
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_trace(*args, **kwargs) -> TraceResult:
        nonlocal active, call_count, max_active
        with state_lock:
            call_count += 1
            call_number = call_count
            active += 1
            max_active = max(max_active, active)
            if call_count == 2:
                limit_reached.set()
            elif call_count > 2 and not release_workers.is_set():
                extra_worker_started_before_release.set()
        try:
            release_workers.wait(2)
            return TraceResult(
                target_node_id="target",
                row_index=0,
                column=None,
                output_value={"x": call_number},
                steps=[],
                total_nodes_in_pipeline=1,
                nodes_in_trace=0,
            )
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_trace", slow_trace)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post(source: str) -> httpx.Response:
            return await ac.post(
                "/api/pipeline/trace",
                json={
                    "graph": _single_node_graph(),
                    "target_node_id": "target",
                    "row_index": 0,
                    "source": source,
                },
            )

        tasks = [asyncio.create_task(post(f"live-{idx}")) for idx in range(3)]
        await _wait_for_thread_event(limit_reached, "two trace workers")
        assert not await _thread_event_was_set(extra_worker_started_before_release, 0.05)

        with state_lock:
            assert call_count == 2
            assert max_active == 2

        release_workers.set()
        responses = await asyncio.gather(*tasks)

    assert [r.status_code for r in responses] == [200, 200, 200]
    with state_lock:
        assert call_count == 3
        assert max_active == 2


@pytest.mark.asyncio
async def test_aborted_trace_request_holds_limiter_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace cancellation uses the same bounded-worker lifecycle as preview."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    limiter = asyncio.Semaphore(1)
    coordinator = SupersessionCoordinator()
    monkeypatch.setattr(route_mod, "_trace_work_slots", limiter)
    monkeypatch.setattr(route_mod, "_trace_supersession", coordinator)

    started = {source: threading.Event() for source in ("first", "queued", "success")}
    release = {source: threading.Event() for source in ("first", "queued", "success")}
    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_trace(*args, **kwargs) -> TraceResult:
        nonlocal active, call_count, max_active
        source = kwargs["source"]
        with state_lock:
            call_count += 1
            call_number = call_count
            active += 1
            max_active = max(max_active, active)
        started[source].set()
        try:
            release[source].wait(2)
            return TraceResult(
                target_node_id="target",
                row_index=0,
                column=None,
                output_value={"x": call_number},
                steps=[],
                total_nodes_in_pipeline=1,
                nodes_in_trace=0,
            )
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_trace", slow_trace)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

            async def post(source: str) -> httpx.Response:
                return await ac.post(
                    "/api/pipeline/trace",
                    json={
                        "graph": _single_node_graph(),
                        "target_node_id": "target",
                        "row_index": 0,
                        "source": source,
                    },
                )

            first = asyncio.create_task(post("first"))
            await _wait_for_thread_event(started["first"], "first trace worker")

            first.cancel()
            queued = asyncio.create_task(post("queued"))
            await _wait_for_coordinator_snapshot(
                coordinator,
                lambda snapshot: _snapshot_has_source(snapshot, "queued"),
                "queued trace request to wait for worker slot",
            )
            assert not await _thread_event_was_set(started["queued"], 0.05)
            queued.cancel()
            release["first"].set()

            cancelled_results = await asyncio.gather(first, queued, return_exceptions=True)
            assert all(isinstance(result, asyncio.CancelledError) for result in cancelled_results)

            with state_lock:
                assert call_count == 1
                assert max_active == 1
                assert active == 0
            assert await coordinator.snapshot_for_tests() == {}

            success = asyncio.create_task(post("success"))
            await _wait_for_thread_event(started["success"], "success trace worker")
            release["success"].set()
            response = await success

        assert response.status_code == 200
        assert await coordinator.snapshot_for_tests() == {}
        await asyncio.wait_for(limiter.acquire(), timeout=0.1)
        limiter.release()
    finally:
        for event in release.values():
            event.set()


def test_preview_trace_concurrency_limit_env_must_be_positive_integer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.routes.pipeline as route_mod

    monkeypatch.setenv("HAUTE_PREVIEW_MAX_CONCURRENCY", "3")
    assert route_mod._positive_int_from_env("HAUTE_PREVIEW_MAX_CONCURRENCY", 2) == 3

    for raw in ("0", "-1", "not-an-int"):
        monkeypatch.setenv("HAUTE_PREVIEW_MAX_CONCURRENCY", raw)
        with pytest.raises(RuntimeError, match="HAUTE_PREVIEW_MAX_CONCURRENCY"):
            route_mod._positive_int_from_env("HAUTE_PREVIEW_MAX_CONCURRENCY", 2)


def test_trace_supersession_key_shared_memo_pins_memoless_key(tmp_path) -> None:
    """Pin: a request-scoped fingerprint memo must not change the key.

    The trace route computes its supersession key with a
    ``GraphFingerprintMemo`` that is then shared with ``execute_trace``.
    The memo is a pure read-cache — the key it produces must equal the
    memoless key on the same graph, including when the preamble imports
    the project ``utility`` module (the path where the memo actually
    caches file hashes).
    """
    from haute._cache import GraphFingerprintMemo
    from haute.routes.pipeline import _trace_supersession_key

    (tmp_path / "utility.py").write_text("X = 1\n")
    graph = make_graph(
        {
            "nodes": [make_transform_node("target")],
            "edges": [],
            "preamble": "import utility\n",
            "source_file": str(tmp_path / "pipeline.py"),
        }
    )

    key_args = (graph, "live", "target", 0, None, 100, {"a": 1})
    memoless = _trace_supersession_key(*key_args)
    memo = GraphFingerprintMemo()
    first_with_memo = _trace_supersession_key(*key_args, memo=memo)
    # Second call with the same memo hits the memoised utility hashes —
    # the key must still be byte-identical.
    second_with_memo = _trace_supersession_key(*key_args, memo=memo)

    assert first_with_memo == memoless
    assert second_with_memo == memoless
    assert memo.utility_file_hashes, "expected the utility hash memo to be populated"


@pytest.mark.asyncio
async def test_trace_worker_limit_serializes_different_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trace worker cap applies even when requests have different keys."""
    import haute.routes.pipeline as route_mod
    from haute.server import app

    state_lock = threading.Lock()
    call_count = 0
    active = 0
    max_active = 0

    def slow_trace(*args, **kwargs) -> TraceResult:
        nonlocal active, call_count, max_active
        with state_lock:
            call_count += 1
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.2)
            return TraceResult(
                target_node_id="target",
                row_index=0,
                column=None,
                output_value={"x": call_count},
                steps=[],
                total_nodes_in_pipeline=1,
                nodes_in_trace=0,
            )
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(route_mod, "execute_trace", slow_trace)
    monkeypatch.setattr(route_mod, "_trace_work_slots", asyncio.Semaphore(1))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:

        async def post(source: str) -> httpx.Response:
            return await ac.post(
                "/api/pipeline/trace",
                json={
                    "graph": _single_node_graph(),
                    "target_node_id": "target",
                    "row_index": 0,
                    "source": source,
                },
            )

        responses = await asyncio.gather(post("live"), post("batch"))

    assert [r.status_code for r in responses] == [200, 200]
    assert call_count == 2
    assert max_active == 1
