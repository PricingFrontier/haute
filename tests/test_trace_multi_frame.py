"""Trace through a multi-frame (≥2-table) apiInput.

Multi-frame sources store ``dict[label, DataFrame]`` in ``eager_outputs``,
but the trace correlation assumed bare DataFrames and crashed with
``AttributeError: 'dict' object has no attribute 'columns'`` — surfaced to
the user as an opaque 500. The edge's ``sourceHandle`` names the frame each
child consumes (per ``_pick_source_frame``); the correlation walk must use
the same selection.
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
from haute.executor import _preview_cache
from haute.trace import execute_trace
from tests.conftest import make_graph
from tests.test_output_nested_roundtrip import _FIXTURE, _api_input_config


def _multi_frame_graph(api_config: dict[str, Any], code: str) -> dict[str, Any]:
    """A multi-frame apiInput feeding a transform via the ``policies`` frame."""
    return {
        "nodes": [
            {
                "id": "api",
                "data": {"label": "api", "nodeType": "apiInput", "config": api_config},
            },
            {
                "id": "t",
                "data": {"label": "t", "nodeType": "polars", "config": {"code": code}},
            },
        ],
        "edges": [
            {"id": "e1", "source": "api", "target": "t", "sourceHandle": "policies"},
        ],
    }


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolated tmp project with the nested data-model fixture + built cache."""
    monkeypatch.chdir(tmp_path)
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.invalidate()

    data_path = tmp_path / "data" / "data_model_example.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(_FIXTURE.read_text())
    build_per_port_cache(
        data_path,
        _api_input_config(data_path),
        _json_cache_dir(data_path, "working"),
    )
    yield data_path
    set_project_root(original)
    _preview_cache.invalidate()


def test_trace_through_multi_frame_source_succeeds(project: Path) -> None:
    """Tracing a node downstream of a multi-frame apiInput must correlate
    through the frame the edge's sourceHandle names — not crash."""
    graph = make_graph(
        _multi_frame_graph(
            _api_input_config(project),
            "df = df.with_columns(pid2=pl.col('policy_id') * 2)",
        )
    )

    result = execute_trace(graph, row_index=0, target_node_id="t", column="pid2")

    steps = {s.node_id: s for s in result.steps}
    assert "t" in steps and "api" in steps
    t_row = steps["t"].output_values
    api_row = steps["api"].output_values
    # The api step's row comes from the policies frame and matches the
    # traced child row on the shared key.
    assert api_row.get("policy_id") == t_row.get("policy_id")
    assert t_row.get("pid2") == t_row.get("policy_id") * 2


def test_trace_target_on_multi_frame_source_is_clear_error(project: Path) -> None:
    """Tracing the bundle node itself cannot pick a frame — must be a clear
    ValueError, not an AttributeError crash."""
    graph = make_graph(
        _multi_frame_graph(
            _api_input_config(project),
            "df = df.with_columns(pid2=pl.col('policy_id') * 2)",
        )
    )

    with pytest.raises(ValueError, match="multiple frames"):
        execute_trace(graph, row_index=0, target_node_id="api", column="policy_id")


def test_trace_route_multi_frame_not_opaque_500(project: Path) -> None:
    """The trace route must not map a multi-frame trace to an opaque 500."""
    from haute.server import app

    client = TestClient(app)
    graph = _multi_frame_graph(
        _api_input_config(project),
        "df = df.with_columns(pid2=pl.col('policy_id') * 2)",
    )

    resp = client.post(
        "/api/pipeline/trace",
        json={"graph": graph, "row_index": 0, "target_node_id": "t", "column": "pid2"},
    )
    assert resp.status_code == 200, resp.text

    # Bundle-node target: a clear 4xx naming the problem, not a 500.
    resp = client.post(
        "/api/pipeline/trace",
        json={
            "graph": graph,
            "row_index": 0,
            "target_node_id": "api",
            "column": "policy_id",
        },
    )
    assert resp.status_code == 400
    assert "frame" in resp.json()["detail"]
