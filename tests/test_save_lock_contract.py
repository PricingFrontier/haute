"""Contract tests for the Bundle 5.M1 shared save_lock.

Background. The OPUS race-conditions report (see handover) flagged
scenarios S1 (concurrent /pipeline/save stale-cleanup clobber) and S4
(codegen-vs-sidecar split mid-save) as P0 hazards rooted in two saves
interleaving. Bundle 5.M1 introduced ``save_lock`` (asyncio.Lock,
module-level in ``routes/_helpers.py``) acquired by the three save-shaped
endpoints:

- ``/api/pipeline/save``
- ``/api/submodel/create``
- ``/api/submodel/dissolve``

The routes run blocking save work in a threadpool while holding
``save_lock``. That keeps the event loop responsive while preserving the
single-writer contract across save-shaped operations. These tests pin both
the lock contract and the offload boundary.
"""

from __future__ import annotations

from typing import Any

import pytest

from haute.routes._helpers import save_lock


def test_save_lock_is_an_asyncio_lock() -> None:
    """``save_lock`` is a single, module-level ``asyncio.Lock``."""
    import asyncio

    assert isinstance(save_lock, asyncio.Lock), (
        f"save_lock must be an asyncio.Lock; got {type(save_lock).__name__}"
    )


def test_save_lock_is_acquired_inside_pipeline_save_route() -> None:
    """``routes/pipeline.py::save_pipeline`` opens an ``async with save_lock``.

    AST-level assertion — no need to actually invoke the route, just
    verify the route body contains the lock acquisition. Pins the
    structural contract.
    """
    import ast
    from pathlib import Path as _Path

    src = _Path("src/haute/routes/pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "save_pipeline":
            for child in ast.walk(node):
                if isinstance(child, ast.AsyncWith):
                    for item in child.items:
                        ctx = item.context_expr
                        if isinstance(ctx, ast.Name) and ctx.id == "save_lock":
                            found = True
                            break
    assert found, (
        "routes/pipeline.py::save_pipeline must use `async with save_lock:` "
        "to serialise against concurrent /api/submodel/* saves."
    )


@pytest.mark.parametrize("route_name", ["create_submodel", "dissolve_submodel"])
def test_save_lock_is_acquired_inside_submodel_routes(route_name: str) -> None:
    """Both submodel write-shaped routes open an ``async with save_lock``."""
    import ast
    from pathlib import Path as _Path

    src = _Path("src/haute/routes/submodel.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == route_name:
            for child in ast.walk(node):
                if isinstance(child, ast.AsyncWith):
                    for item in child.items:
                        ctx = item.context_expr
                        if isinstance(ctx, ast.Name) and ctx.id == "save_lock":
                            found = True
                            break
    assert found, (
        f"routes/submodel.py::{route_name} must use `async with save_lock:` "
        "to serialise against concurrent /api/pipeline/save and other "
        "submodel routes."
    )


@pytest.mark.asyncio
async def test_save_lock_holds_during_svc_save(monkeypatch: pytest.MonkeyPatch) -> None:
    """``save_lock`` is held while ``SavePipelineService.save`` runs.

    Pin that the lock is genuinely held during save, and that the save body
    runs in a worker thread rather than on the async event-loop thread.
    """
    import threading

    import httpx

    from haute.routes._save_pipeline import SavePipelineService
    from haute.schemas import SavePipelineRequest, SavePipelineResponse
    from haute.server import app

    locked_observations: list[bool] = []
    save_thread_ids: list[int] = []
    event_loop_thread_id = threading.get_ident()

    def spy_save(self: SavePipelineService, body: SavePipelineRequest) -> SavePipelineResponse:
        locked_observations.append(save_lock.locked())
        save_thread_ids.append(threading.get_ident())
        # Return a VALID response — the spy short-circuits all real save
        # behaviour so the test doesn't touch the filesystem, but it MUST
        # construct ``SavePipelineResponse`` with the real schema fields
        # (``file`` + ``pipeline_name`` required; ``status`` + ``warnings``
        # have defaults).  An invalid construction would raise pydantic
        # ValidationError inside the route's ``async with save_lock`` block
        # and surface as a 500 — masking whether the post-save half of the
        # route ever runs.  See haute.schemas.SavePipelineResponse.
        return SavePipelineResponse(
            status="saved",
            file="test.py",
            pipeline_name="test",
            source_revision="revision-test",
            warnings=[],
        )

    monkeypatch.setattr(SavePipelineService, "save", spy_save)

    # Minimal valid save payload — schema fields verified by Pydantic.
    # ``SavePipelineRequest`` uses ``name``/``description`` (not
    # ``pipeline_name``/``pipeline_description``); unknown keys are ignored
    # by the default model config, so the prior payload still parsed but
    # silently dropped the misnamed fields.  Use the real field names.
    payload: dict[str, Any] = {
        "graph": {"nodes": [], "edges": []},
        "name": "test",
        "description": "",
        "source_file": "test.py",
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/api/pipeline/save", json=payload)

    # Assert the route returned 200 — this proves the post-save half of the
    # handler executed (response_model serialisation succeeded) rather than
    # the spy's return raising mid-route and being swallowed as a 500.
    assert response.status_code == 200, (
        f"/api/pipeline/save must return 200; got {response.status_code}: {response.text}"
    )
    body_json = response.json()
    assert body_json["status"] == "saved"
    assert body_json["file"] == "test.py"
    assert body_json["pipeline_name"] == "test"

    assert locked_observations == [True], (
        f"save_lock.locked() must return True during svc.save; got {locked_observations}"
    )
    assert save_thread_ids and save_thread_ids[0] != event_loop_thread_id, (
        "SavePipelineService.save must run off the async event-loop thread "
        f"(loop={event_loop_thread_id}, save={save_thread_ids})"
    )


@pytest.mark.parametrize("route_name", ["create_submodel", "dissolve_submodel", "get_submodel"])
def test_submodel_write_routes_offload_blocking_work(route_name: str) -> None:
    """Submodel routes keep the async event loop free while respecting save_lock."""
    import inspect

    from haute.routes import submodel

    src = inspect.getsource(getattr(submodel, route_name))
    assert ("run_in_threadpool" in src) or ("to_thread" in src), (
        f"routes/submodel.py::{route_name} must offload file/parse I/O "
        "instead of running synchronously on the event loop."
    )


def test_submodel_get_route_uses_save_lock() -> None:
    """Submodel reads must not parse files while create/dissolve is mid-write."""
    import inspect

    from haute.routes import submodel

    src = inspect.getsource(submodel.get_submodel)
    assert "save_lock" in src, (
        "routes/submodel.py::get_submodel must coordinate with save_lock so "
        "it cannot read partially-written submodel files/config sidecars."
    )


def test_json_cache_infer_route_offloads_blocking_work() -> None:
    """JSON schema inference reads user data and must not block the event loop."""
    import inspect

    from haute.routes import json_cache

    src = inspect.getsource(json_cache.infer_json_cache_schema)
    assert ("run_in_threadpool" in src) or ("to_thread" in src), (
        "routes/json_cache.py::infer_json_cache_schema must offload JSON reads "
        "and schema inference from the event loop."
    )
