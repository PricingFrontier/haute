"""A preview whose client goes away (the browser's Stop) stops its server work.

The pool-level half — a worker whose ``stop_reason`` answers stops — is covered
by ``test_interactive_worker_pool.py``; these tests pin that the preview route
feeds its own cancellation into that seam, stops a thread-mode execution at its
checkpoints, and never starts a request that was still queued.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request

from haute.routes import pipeline as pipeline_routes
from haute.schemas import PreviewNodeRequest
from tests.conftest import make_ready_file_input_config


class _Client:
    """A request whose client disconnects once ``leave`` is set."""

    def __init__(self) -> None:
        self.leave = threading.Event()

    async def is_disconnected(self) -> bool:
        return self.leave.is_set()


def _body() -> PreviewNodeRequest:
    return PreviewNodeRequest.model_validate(
        {
            "graph": {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": make_ready_file_input_config(
                                "tests/fixtures/data/policies.parquet"
                            ),
                        },
                    }
                ],
                "edges": [],
            },
            "node_id": "source",
            "row_limit": 2,
        }
    )


async def _wait_for(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 30)


async def test_thread_mode_preview_stops_at_a_checkpoint_when_its_client_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "thread")
    running = threading.Event()
    stopped: list[str] = []

    def slow_execute_graph(*_args: Any, execution_context: Any, **_kwargs: Any) -> dict[str, Any]:
        running.set()
        deadline = time.monotonic() + 30
        while True:
            assert time.monotonic() < deadline
            # The executor's own checkpoint: raises once the preview is cancelled.
            try:
                execution_context.checkpoint(label="before_node")
            except Exception as exc:
                stopped.append(type(exc).__name__)
                raise
            time.sleep(0.01)

    monkeypatch.setattr(pipeline_routes, "execute_graph", slow_execute_graph)
    client = _Client()
    preview = asyncio.ensure_future(
        pipeline_routes._preview_canonical_graph(_body(), cast(Request, client))
    )
    await _wait_for(running)
    client.leave.set()

    with pytest.raises(HTTPException) as raised:
        await preview

    assert raised.value.status_code == 499
    assert stopped == ["ExecutionCancelledError"]


async def test_process_mode_preview_tells_its_worker_to_stop_when_its_client_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    running = threading.Event()
    reasons: list[str] = []

    async def worker(*_args: Any, stop_reason: Any, **_kwargs: Any) -> Any:
        running.set()
        deadline = time.monotonic() + 30
        while (reason := stop_reason()) is None:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        reasons.append(reason)
        raise RuntimeError("worker stopped")

    monkeypatch.setattr(pipeline_routes, "run_in_interactive_worker", worker)
    client = _Client()
    preview = asyncio.ensure_future(
        pipeline_routes._preview_canonical_graph(_body(), cast(Request, client))
    )
    await _wait_for(running)
    client.leave.set()

    with pytest.raises(HTTPException) as raised:
        await preview

    assert raised.value.status_code == 499
    assert reasons == ["superseded"]


async def test_a_preview_still_queued_for_a_slot_never_runs_once_its_client_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "thread")
    executed: list[str] = []

    def execute_graph(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        executed.append("ran")
        raise AssertionError("a queued preview whose client left must not run")

    monkeypatch.setattr(pipeline_routes, "execute_graph", execute_graph)
    # Every work slot is taken, so the request queues.
    monkeypatch.setattr(pipeline_routes, "_preview_work_slots", asyncio.Semaphore(0))
    client = _Client()
    client.leave.set()

    started_at = time.monotonic()
    with pytest.raises(HTTPException) as raised:
        await asyncio.wait_for(
            pipeline_routes._preview_canonical_graph(_body(), cast(Request, client)),
            timeout=30,
        )

    assert raised.value.status_code == 499
    # Answered at once: it does not wait for a slot it no longer needs.
    assert time.monotonic() - started_at < 5
    assert executed == []
