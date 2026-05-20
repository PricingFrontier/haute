"""API Input multi-port runtime (MULTI_FRAME_PLAN commit 4).

Exercises the v2 apiInput at runtime end-to-end through execute_graph:

- 0 emit-true tables → preview fails loud with a configuration message.
- 1 emit-true table → single-port shorthand; source emits a bare
  LazyFrame; downstream edges with null ``sourceHandle`` bind correctly.
- 2+ emit-true tables → source emits a dict[port_label, LazyFrame]; the
  executor's edge-resolution picks per edge via ``sourceHandle``.
- Cache absent → fail with the "click Cache as Parquet" hint.
- Multi-port edge with null ``sourceHandle`` → executor raises a clear
  diagnostic naming the available ports.

The test pipelines use only DATA_SOURCE + POLARS + API_INPUT to keep
the surface focused on routing, not transform semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._json_flatten import _json_cache_dir, clear_json_cache
from haute._json_shred import build_per_port_cache
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph


def _rating_records() -> list[dict[str, Any]]:
    return [
        {
            "policy_id": 1001,
            "drivers": [
                {"driver_id": 1, "age_band": "30-59"},
                {"driver_id": 2, "age_band": "60+"},
            ],
        },
        {
            "policy_id": 1002,
            "drivers": [{"driver_id": 3, "age_band": "60+"}],
        },
    ]


def _single_port_config(data_path: Path) -> dict[str, Any]:
    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[*]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[*].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
        ],
    }


def _multi_port_config(data_path: Path) -> dict[str, Any]:
    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[*]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[*].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
            {
                "path": "$[*].drivers[*]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[*].drivers[*].driver_id",
                        "type": "int",
                        "selected": True,
                    },
                    {
                        "name": "age_band",
                        "path": "$[*].drivers[*].age_band",
                        "type": "str",
                        "selected": True,
                    },
                ],
            },
        ],
    }


@pytest.fixture()
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set the sandbox project root to tmp_path so apiInput data + cache
    live in the test's isolated tree. Restore on teardown."""
    monkeypatch.chdir(tmp_path)
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.invalidate()
    yield tmp_path
    set_project_root(original)
    _preview_cache.invalidate()


def _api_input_node(node_id: str, config: dict[str, Any]) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=NodeType.API_INPUT, config=config),
    )


def _build_cache_for(tmp_path: Path, data_path: Path, config: dict[str, Any]) -> None:
    """Build the v2 per-port cache for *data_path* using *config*. Mirrors
    what the /api/json-cache/build endpoint does (commit 3)."""
    cache_dir = _json_cache_dir(data_path, "working")
    build_per_port_cache(data_path, config, cache_dir)


# ─── 1. zero-emit-true: preview fails loud ────────────────────────


def test_zero_emit_true_fails_loud(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    config["tables"][0]["emit"] = False  # turn the lone table OFF

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])
    results = execute_graph(graph, target_node_id="api")
    assert results["api"].status == "error"
    error_msg = (results["api"].error or "").lower()
    assert "emit" in error_msg
    assert "tick" in error_msg or "configure" in error_msg or "configuration" in error_msg


# ─── 2. single-port: bare frame, null-sourceHandle edge works ─────


def test_single_port_emits_bare_frame_through_passthrough_consumer(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    # apiInput → polars (passthrough, code-free → uses _passthrough_fn).
    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="downstream",
                data=NodeData(
                    label="downstream",
                    nodeType=NodeType.POLARS,
                    config={},  # no code → _passthrough_fn
                ),
            ),
        ],
        edges=[
            # Single-port: null sourceHandle (the shorthand).
            GraphEdge(id="e", source="api", target="downstream", sourceHandle=None),
        ],
    )
    results = execute_graph(graph, target_node_id="downstream")
    assert results["api"].status == "ok"
    assert results["downstream"].status == "ok", results["downstream"].error
    # Source emitted exactly one frame (the policies table).
    assert results["api"].row_count == 2
    # Downstream passed it through unchanged.
    assert results["downstream"].row_count == 2


# ─── 3. multi-port: dict emit, sourceHandle picks per edge ────────


def test_multi_port_routes_per_edge_via_source_handle(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    # apiInput → two downstream consumers, each wired to a different port.
    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="d_policies",
                data=NodeData(label="d_policies", nodeType=NodeType.POLARS, config={}),
            ),
            GraphNode(
                id="d_drivers",
                data=NodeData(label="d_drivers", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            GraphEdge(
                id="e_p",
                source="api",
                target="d_policies",
                sourceHandle="policies",
            ),
            GraphEdge(
                id="e_d",
                source="api",
                target="d_drivers",
                sourceHandle="drivers",
            ),
        ],
    )
    p_results = execute_graph(graph, target_node_id="d_policies")
    assert p_results["d_policies"].status == "ok", p_results["d_policies"].error
    assert p_results["d_policies"].row_count == 2  # policies: 2 records

    d_results = execute_graph(graph, target_node_id="d_drivers")
    assert d_results["d_drivers"].status == "ok", d_results["d_drivers"].error
    assert d_results["d_drivers"].row_count == 3  # drivers: 3 across both policies


# ─── 4. cache absent: clear error message ─────────────────────────


def test_runtime_fails_when_cache_missing(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    # Do NOT call _build_cache_for here — cache is absent.

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])
    results = execute_graph(graph, target_node_id="api")
    assert results["api"].status == "error"
    error_msg = (results["api"].error or "").lower()
    assert "cache" in error_msg and "parquet" in error_msg


# ─── 5. multi-port edge with null sourceHandle: loud error ────────


def test_multi_port_with_null_source_handle_raises(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="downstream",
                data=NodeData(label="downstream", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            # BAD: sourceHandle=None against a multi-port source.
            GraphEdge(id="e", source="api", target="downstream", sourceHandle=None),
        ],
    )
    results = execute_graph(graph, target_node_id="downstream")
    assert results["downstream"].status == "error"
    error_msg = results["downstream"].error or ""
    assert "no sourceHandle" in error_msg or "multi-port" in error_msg
    # The error names the available ports so the user can pick.
    assert "policies" in error_msg or "drivers" in error_msg


# ─── 6. multi-port edge with unknown sourceHandle: loud error ─────


def test_multi_port_with_unknown_source_handle_raises(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="downstream",
                data=NodeData(label="downstream", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            # BAD: sourceHandle naming a port the source doesn't emit.
            GraphEdge(
                id="e",
                source="api",
                target="downstream",
                sourceHandle="non_existent_port",
            ),
        ],
    )
    results = execute_graph(graph, target_node_id="downstream")
    assert results["downstream"].status == "error"
    error_msg = results["downstream"].error or ""
    assert "non_existent_port" in error_msg
    # Names what IS available.
    assert "policies" in error_msg or "drivers" in error_msg


# ─── 7. preview-cache invalidation interacts correctly ────────────


def test_apiinput_preview_invalidates_when_cache_rebuilt(isolated_root) -> None:
    """Bug-class regression for the commit 1 invalidation: a v2 apiInput
    whose cache gets rebuilt mid-session must show fresh data on the next
    preview, not the stale failure cached from a previous attempt.
    """
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])

    # 1. Preview before any cache build: expect error.
    first = execute_graph(graph, target_node_id="api")
    assert first["api"].status == "error"

    # 2. Build the cache out-of-band.
    _build_cache_for(isolated_root, data_path, config)

    # 3. Preview again — should now succeed (commit 1's per-data-file
    # mtime in the preview fingerprint forces a cache miss after the
    # meta.json appears).
    second = execute_graph(graph, target_node_id="api")
    assert second["api"].status == "ok", second["api"].error
    assert second["api"].row_count == 2
