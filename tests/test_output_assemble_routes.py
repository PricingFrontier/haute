"""Dry-run route for the OUTPUT editor (MULTI_FRAME_PLAN piece 8).

``POST /api/output-assemble/dry-run`` validates an in-progress (volatile)
``outputMapping``, swaps it into the target OUTPUT node, runs the graph up to
that node, and returns the rendered response document. Exercised against the
canonical data-model example so the route's assembled output matches the
``test_output_nested_roundtrip`` end-to-end proof.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haute._json_flatten import _json_cache_dir
from haute._json_shred import build_per_port_cache
from haute._sandbox import _get_project_root, set_project_root
from haute._types import NodeType
from haute.executor import _preview_cache
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
    _preview_cache.invalidate()

    from haute.server import app

    data_path = tmp_path / "data" / "data_model_example.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(_FIXTURE.read_text())
    yield TestClient(app), data_path
    set_project_root(original)
    _preview_cache.invalidate()


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
