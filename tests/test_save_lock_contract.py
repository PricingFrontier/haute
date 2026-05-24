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

Caveat. The current routes call ``SavePipelineService.save()``
synchronously inside async handlers, so the event loop is blocked
during save — concurrent in-process saves are *already* serialised by
event-loop-blocking even without the lock. The lock is defence-in-depth
against any future refactor that moves save work to ``asyncio.to_thread``
or threadpool, OR any new endpoint that adds explicit ``await`` mid-save.
These tests pin the *contract* (lock is exercised), not the *behaviour*
(serialisation) — the latter is structurally guaranteed by the current
architecture but can't be reliably tested without instrumenting save with
async yields.
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

    src = _Path(
        "src/haute/routes/pipeline.py"
    ).read_text(encoding="utf-8")
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

    src = _Path(
        "src/haute/routes/submodel.py"
    ).read_text(encoding="utf-8")
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

    The route handler body is ``async with save_lock: svc.save(body)`` —
    pin the contract that the lock is genuinely held during the
    synchronous save call (not just acquired and immediately released).
    """
    import httpx

    from haute.routes._save_pipeline import SavePipelineService
    from haute.schemas import SavePipelineRequest, SavePipelineResponse
    from haute.server import app

    locked_observations: list[bool] = []

    def spy_save(self: SavePipelineService, body: SavePipelineRequest) -> SavePipelineResponse:
        locked_observations.append(save_lock.locked())
        # Return a minimal valid response — the spy short-circuits all
        # real save behaviour so the test doesn't touch the filesystem.
        return SavePipelineResponse(
            status="ok",
            pipeline_file="test.py",
            warnings=[],
            sidecar_warnings=[],
        )

    monkeypatch.setattr(SavePipelineService, "save", spy_save)

    # Minimal valid save payload — schema fields verified by Pydantic.
    payload: dict[str, Any] = {
        "graph": {"nodes": [], "edges": []},
        "pipeline_name": "test",
        "pipeline_description": "",
        "source_file": "test.py",
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post("/api/pipeline/save", json=payload)

    assert locked_observations == [True], (
        "save_lock.locked() must return True during svc.save; "
        f"got {locked_observations}"
    )
