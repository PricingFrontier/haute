"""Dry-run route for the OUTPUT editor (MULTI_FRAME_PLAN piece 8).

``POST /api/output-assemble/dry-run`` validates an in-progress (volatile)
``outputMapping``, swaps it into the target OUTPUT node, runs the graph up to
that node, and returns the rendered response document. Exercised against the
canonical data-model example so the route's assembled output matches the
``test_output_nested_roundtrip`` end-to-end proof.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionProfile
from haute._json_flatten import _json_cache_dir
from haute._json_shred import build_per_port_cache
from haute._sandbox import _get_project_root, set_project_root
from haute._types import NodeType
from haute.executor import _preview_cache
from haute.routes._timeouts import BlockingWorkTimeoutError
from haute.schemas import NodeResult
from tests.test_output_nested_roundtrip import (
    _FIXTURE,
    _api_input_config,
    _expected_document,
    _output_mapping,
)

_PORTS = ["policies", "drivers", "licenses", "vehicles"]


def _graph_json(api_config: dict[str, Any]) -> dict[str, Any]:
    """React-Flow ``Graph`` shape: apiInput → OUTPUT over four ports. The OUTPUT
    config is deliberately empty — the dry-run supplies the (volatile) mapping."""
    return {
        "nodes": [
            {
                "id": "api",
                "data": {
                    "label": "api",
                    "nodeType": NodeType.API_INPUT.value,
                    "config": api_config,
                },
            },
            {
                "id": "out",
                "data": {"label": "out", "nodeType": NodeType.OUTPUT.value, "config": {}},
            },
        ],
        "edges": [
            {"id": f"e_{p}", "source": "api", "target": "out", "sourceHandle": p} for p in _PORTS
        ],
    }


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Path]]:
    monkeypatch.chdir(tmp_path)
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.clear()

    from haute.server import app

    data_path = tmp_path / "data" / "data_model_example.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(_FIXTURE.read_text())
    yield TestClient(app), data_path
    set_project_root(original)
    _preview_cache.clear()


def test_dry_run_assembles_nested_document(project) -> None:
    client, data_path = project
    config = _api_input_config(data_path)
    build_per_port_cache(data_path, config, _json_cache_dir(data_path, "working"))

    resp = client.post(
        "/api/output-assemble/dry-run",
        json={
            "graph": _graph_json(config),
            "node_id": "out",
            "output_mapping": _output_mapping(),
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "ok", payload.get("error")
    assert payload["document"] == _expected_document()
    assert payload["row_count"] == 2


def test_dry_run_rejects_invalid_mapping_with_422(project) -> None:
    client, data_path = project
    config = _api_input_config(data_path)
    # An indexed selector (``$[0]``) is outside the accepted ``[:]``-only subset.
    bad = [
        {
            "source_port": "policies",
            "source_column": "policy_id",
            "output_path": "$[0].policy_id",
            "enabled": True,
        }
    ]
    resp = client.post(
        "/api/output-assemble/dry-run",
        json={"graph": _graph_json(config), "node_id": "out", "output_mapping": bad},
    )
    assert resp.status_code == 422, resp.text


def test_dry_run_rejects_non_array_root_with_422(project) -> None:
    """The §3 ``$[:]`` root gate fires through the real route, not just in-unit.

    ``$.values[:].a`` has a ``[:]`` but NOT at the root — it was accepted before
    OUTPUT enforced the gate. The editor refuses it client-side now, but the
    server is the authority, so the route must reject it 422 too.
    """
    client, data_path = project
    config = _api_input_config(data_path)
    bad = [
        {
            "source_port": "policies",
            "source_column": "policy_id",
            "output_path": "$.values[:].policy_id",
            "enabled": True,
        }
    ]
    resp = client.post(
        "/api/output-assemble/dry-run",
        json={"graph": _graph_json(config), "node_id": "out", "output_mapping": bad},
    )
    assert resp.status_code == 422, resp.text
    assert "must start with '$[:]'" in resp.text


def test_dry_run_unknown_node_returns_404(project) -> None:
    client, data_path = project
    config = _api_input_config(data_path)
    resp = client.post(
        "/api/output-assemble/dry-run",
        json={
            "graph": _graph_json(config),
            "node_id": "nope",
            "output_mapping": _output_mapping(),
        },
    )
    assert resp.status_code == 404, resp.text


def test_dry_run_non_output_node_returns_400(project) -> None:
    client, data_path = project
    config = _api_input_config(data_path)
    resp = client.post(
        "/api/output-assemble/dry-run",
        json={
            "graph": _graph_json(config),
            "node_id": "api",  # the apiInput, not an OUTPUT node
            "output_mapping": _output_mapping(),
        },
    )
    assert resp.status_code == 400, resp.text


def test_dry_run_admission_refusal_returns_structured_507(project) -> None:
    client, data_path = project
    error = ExecutionAdmissionError(
        "output_assemble_dry_run",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=1024,
        rss_at_admission_bytes=2048,
        reason="memory budget exhausted",
    )

    with patch(
        "haute.routes.output_assemble.create_admitted_execution_context",
        side_effect=error,
    ):
        resp = client.post(
            "/api/output-assemble/dry-run",
            json={
                "graph": _graph_json(_api_input_config(data_path)),
                "node_id": "out",
                "output_mapping": [],
            },
        )

    assert resp.status_code == 507
    assert resp.json() == {"detail": error.to_payload()}


def test_dry_run_releases_admission_after_success(project) -> None:
    client, data_path = project
    release_calls: list[bool] = []

    class _Context:
        def release_admission(self, *, preserve_primary_error: bool = False) -> None:
            release_calls.append(preserve_primary_error)

    with (
        patch(
            "haute.routes.output_assemble.create_admitted_execution_context",
            return_value=_Context(),
        ),
        patch(
            "haute.routes.output_assemble.execute_graph",
            return_value={"out": NodeResult(status="ok", preview=[], row_count=0)},
        ),
    ):
        resp = client.post(
            "/api/output-assemble/dry-run",
            json={
                "graph": _graph_json(_api_input_config(data_path)),
                "node_id": "out",
                "output_mapping": [],
            },
        )

    assert resp.status_code == 200
    assert release_calls == [True]


@pytest.mark.asyncio
async def test_dry_run_timeout_defers_admission_release_until_worker_finishes(project) -> None:
    _, data_path = project
    release_calls: list[bool] = []
    execution_contexts: list[object | None] = []
    queued_work: list[Callable[[], dict[str, Any]]] = []
    background_task: asyncio.Future[object] = asyncio.get_running_loop().create_future()

    class _Context:
        def release_admission(self, *, preserve_primary_error: bool = False) -> None:
            release_calls.append(preserve_primary_error)

    context = _Context()

    def _execute_graph(*args, execution_context=None, **kwargs):
        del args, kwargs
        execution_contexts.append(execution_context)
        return {}

    async def _raise_timeout(work, **kwargs):
        del kwargs
        queued_work.append(work)
        raise BlockingWorkTimeoutError(
            "output_assemble_dry_run",
            1,
            background_task,
        )

    from haute.server import app

    with (
        patch(
            "haute.routes.output_assemble.create_admitted_execution_context",
            return_value=context,
        ),
        patch("haute.routes.output_assemble.execute_graph", _execute_graph),
        patch(
            "haute.routes.output_assemble.run_blocking_with_response_timeout",
            _raise_timeout,
        ),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/api/output-assemble/dry-run",
                json={
                    "graph": _graph_json(_api_input_config(data_path)),
                    "node_id": "out",
                    "output_mapping": [],
                },
            )

        assert resp.status_code == 504
        assert release_calls == []
        assert len(queued_work) == 1

        queued_work[0]()
        assert execution_contexts == [context]

    background_task.set_result(None)
    await asyncio.sleep(0)
    assert release_calls == [False]
