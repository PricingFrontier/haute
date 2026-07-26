"""Flat (CSV/parquet) apiInput wired straight into an OUTPUT node.

Regression for the eager-path column-contract failure: a FLAT apiInput is a
single-frame source, so the executor stores its columns under
``column_cache[(source, None)]``. An OUTPUT-editor edge names its
``source_port``, so the edge carries a non-null ``sourceHandle`` and the
consumer's input-contract check looked the cache up under
``(source, sourceHandle)`` — a miss — and collapsed the upstream column set to
``frozenset()``. The dry-run route surfaced that as::

    ContractMismatchError: Input columns required by the node's contract are
    missing from the upstream frame. (... missing=['customer_id','premium'],
    upstream_columns=[])

The fix makes the eager input-contract check fall back to the actual input
frame's schema on a cache-key miss — the same fallback the lazy path already
uses. This is independent of the flat-apiInput path-anchoring class: the tests
here use an ABSOLUTE data path, so path resolution is never in play.

The project uses the canonical nested layout (``haute.toml`` →
``rating/main.py``, ``data/`` at the project root), launched with cwd at the
project root, matching how a real haute install runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionProfile
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph

_CSV = "customer_id,premium\n1,100.0\n2,200.0\n"
_EXPECTED_DOCUMENT = [
    {"customer_id": 1, "premium": 100.0},
    {"customer_id": 2, "premium": 200.0},
]
_OUTPUT_MAPPING = [
    {
        "source_port": "src",
        "source_column": "customer_id",
        "output_path": "$[:].customer_id",
    },
    {
        "source_port": "src",
        "source_column": "premium",
        "output_path": "$[:].premium",
    },
]


@pytest.fixture()
def nested_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Canonical nested layout: haute.toml → rating/main.py, data/ at root.

    cwd is the project root, mirroring a real launch. The apiInput data file
    is written with an ABSOLUTE path so the flat-apiInput path-anchoring class
    (a separate bug) can never interfere with this contract-boundary check.
    """
    (tmp_path / "haute.toml").write_text('[project]\nname = "main"\npipeline = "rating/main.py"\n')
    (tmp_path / "rating").mkdir()
    (tmp_path / "rating" / "main.py").write_text("# pipeline entry\n")
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.clear()
    yield tmp_path
    set_project_root(original)
    _preview_cache.clear()


def _flat_api_node(csv_path: Path) -> GraphNode:
    return GraphNode(
        id="src",
        data=NodeData(
            label="src",
            nodeType=NodeType.API_INPUT,
            config={"sourceType": "flat_file", "path": str(csv_path)},
        ),
    )


def _output_node() -> GraphNode:
    return GraphNode(
        id="out",
        data=NodeData(
            label="out",
            nodeType=NodeType.OUTPUT,
            config={"outputMapping": _OUTPUT_MAPPING, "outputFormat": "json"},
        ),
    )


def _flat_to_output_graph(csv_path: Path) -> PipelineGraph:
    return PipelineGraph(
        nodes=[_flat_api_node(csv_path), _output_node()],
        # The OUTPUT editor names its source_port, so the edge carries a
        # non-null sourceHandle — the exact shape that triggered the miss.
        edges=[GraphEdge(id="e", source="src", target="out", sourceHandle="src")],
    )


def test_flat_apiinput_to_output_executes_via_execute_graph(nested_project: Path) -> None:
    """Executor-level regression: a flat apiInput reached through a non-null
    ``sourceHandle`` edge no longer fails the OUTPUT input-contract check.

    Pins the general class (single-frame source + named-port edge), not just
    the route. Pre-fix this raised ``ContractMismatchError`` with
    ``upstream_columns=[]``.
    """
    csv_path = nested_project / "data" / "customers.csv"
    csv_path.write_text(_CSV)

    graph = _flat_to_output_graph(csv_path)
    context = create_admitted_execution_context(
        operation="flat_output_regression",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    results = execute_graph(
        graph,
        target_node_id="out",
        target_preview_only=True,
        execution_context=context,
    )
    assert results["out"].status == "ok", results["out"].error
    assert results["out"].preview == _EXPECTED_DOCUMENT
    assert results["out"].row_count == 2


def test_flat_apiinput_to_output_dry_run_route(nested_project: Path) -> None:
    """End-to-end through ``POST /api/output-assemble/dry-run`` — the exact
    route the OUTPUT editor calls, and the one that surfaced the failure as a
    422. The volatile mapping is supplied on the request; the graph's OUTPUT
    config is left empty so the route's config-swap is exercised too.
    """
    from haute.server import app

    csv_path = nested_project / "data" / "customers.csv"
    csv_path.write_text(_CSV)

    graph = {
        "nodes": [
            {
                "id": "src",
                "data": {
                    "label": "src",
                    "nodeType": NodeType.API_INPUT.value,
                    "config": {"sourceType": "flat_file", "path": str(csv_path)},
                },
            },
            {
                "id": "out",
                "data": {"label": "out", "nodeType": NodeType.OUTPUT.value, "config": {}},
            },
        ],
        "edges": [{"id": "e", "source": "src", "target": "out", "sourceHandle": "src"}],
    }

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/output-assemble/dry-run",
        json={"graph": graph, "node_id": "out", "output_mapping": _OUTPUT_MAPPING},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "ok", payload.get("error")
    assert payload["document"] == _EXPECTED_DOCUMENT
    assert payload["row_count"] == 2
