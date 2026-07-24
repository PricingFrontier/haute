"""Parity tests for shared preview/trace/sink request guards."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute.server import app
from tests.conftest import make_edge as _edge
from tests.conftest import make_graph as _g
from tests.conftest import make_source_node as _source_node
from tests.conftest import make_transform_node as _transform_node


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(project_root: Path) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _preview_graph(data_path: Path) -> dict:
    return _g(
        {
            "nodes": [
                _source_node("src", str(data_path)),
                _transform_node("target", ""),
            ],
            "edges": [_edge("src", "target")],
        }
    ).model_dump()


def _sink_graph(data_path: Path) -> dict:
    return _g(
        {
            "nodes": [
                _source_node("src", str(data_path)),
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": "dataOutput",
                        "config": {"path": "out.parquet", "format": "parquet"},
                    },
                },
            ],
            "edges": [_edge("src", "sink")],
        }
    ).model_dump()


def _post_payload(endpoint: str, graph: dict, lookup_id: str) -> dict:
    if endpoint == "/api/pipeline/trace":
        return {
            "graph": graph,
            "row_index": 0,
            "target_node_id": lookup_id,
        }
    return {
        "graph": graph,
        "node_id": lookup_id,
    }


@pytest.mark.parametrize(
    ("endpoint", "lookup_id", "expected_detail"),
    [
        ("/api/pipeline/preview", "bad\nnode", "node_id contains control characters"),
        ("/api/pipeline/trace", "bad\nnode", "target_node_id contains control characters"),
        ("/api/pipeline/write-output", "bad\nnode", "node_id contains control characters"),
    ],
)
def test_preview_trace_sink_reject_control_char_lookup_ids_consistently(
    client: TestClient,
    project_root: Path,
    endpoint: str,
    lookup_id: str,
    expected_detail: str,
) -> None:
    data_path = project_root / "data.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(data_path)
    graph = _sink_graph(data_path) if endpoint.endswith("/sink") else _preview_graph(data_path)

    with patch(
        "haute.routes.pipeline.run_blocking_with_response_timeout",
        new_callable=AsyncMock,
    ) as run_blocking:
        resp = client.post(endpoint, json=_post_payload(endpoint, graph, lookup_id))

    assert resp.status_code == 400
    assert resp.json() == {"detail": expected_detail}
    run_blocking.assert_not_awaited()


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/pipeline/preview",
        "/api/pipeline/trace",
        "/api/pipeline/write-output",
    ],
)
def test_preview_trace_sink_reject_same_traversal_graph_consistently(
    client: TestClient,
    endpoint: str,
) -> None:
    bad_graph = (
        _g(
            {
                "nodes": [
                    _source_node("src", "../escape.parquet"),
                    {
                        "id": "sink",
                        "data": {
                            "label": "sink",
                            "nodeType": "dataOutput",
                            "config": {"path": "out.parquet", "format": "parquet"},
                        },
                    },
                ],
                "edges": [_edge("src", "sink")],
            }
        ).model_dump()
        if endpoint.endswith("/sink")
        else _g(
            {
                "nodes": [
                    _source_node("src", "../escape.parquet"),
                    _transform_node("target", ""),
                ],
                "edges": [_edge("src", "target")],
            }
        ).model_dump()
    )
    lookup_id = "sink" if endpoint.endswith("/sink") else "target"

    with patch(
        "haute.routes.pipeline.run_blocking_with_response_timeout",
        new_callable=AsyncMock,
    ) as run_blocking:
        resp = client.post(endpoint, json=_post_payload(endpoint, bad_graph, lookup_id))

    assert resp.status_code == 403
    assert set(resp.json()) == {"detail"}
    assert isinstance(resp.json()["detail"], str)
    assert "outside the project root" in resp.json()["detail"]
    run_blocking.assert_not_awaited()
