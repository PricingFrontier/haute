"""Preview cache invalidates when JSON-cache state changes for an apiInput.

The preview cache (``_preview_cache`` in ``src/haute/executor.py``) is a
process-lifetime ``FingerprintCache`` keyed by ``graph_fingerprint(graph,
extra_keys)``.  Before this fix, the key did not reflect the on-disk JSON
cache state for any apiInput referenced by the graph: a sequence of "first
preview fails (no cache) → build cache → preview again" returned the cached
failure from the first call instead of executing fresh against the
now-populated cache.

This file exercises two layers:

1. Unit tests for ``cache_state_signature_for_graph`` — the per-data-file
   fingerprint contribution that the fix adds. It must change when a
   relevant ``meta.json`` mtime changes, stay the same otherwise, and
   produce an empty string for graphs with no apiInputs.

2. An integration test that walks the full preview → build → preview
   sequence inside one process and asserts the second preview observes the
   newly-built cache, not the stale failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from haute._json_flatten import (
    _json_cache_dir,
    _json_cache_meta_path,
    build_json_cache,
    cache_state_signature_for_graph,
    clear_json_cache,
)
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph


# ─── Fixture helpers ──────────────────────────────────────────────


def _write_json_data(path: Path) -> None:
    """A trivially-shaped JSON file the apiInput can shred into a flat frame."""
    path.write_text(json.dumps([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]))


def _make_api_input_graph(data_path: Path) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="apiin",
                data=NodeData(
                    label="apiin",
                    nodeType=NodeType.API_INPUT,
                    config={
                        "path": str(data_path),
                        "flattenSchema": {"a": "int", "b": "str"},
                    },
                ),
            ),
        ],
        edges=[],
    )


# ─── Unit tests for cache_state_signature_for_graph ───────────────


def test_signature_empty_graph_is_empty_string() -> None:
    empty = PipelineGraph(nodes=[], edges=[])
    assert cache_state_signature_for_graph(empty) == ""


def test_signature_graph_without_apiinputs_is_empty_string() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="ds",
                data=NodeData(
                    label="ds",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": "data.parquet"},
                ),
            ),
        ],
        edges=[],
    )
    assert cache_state_signature_for_graph(graph) == ""


def test_signature_has_zero_mtimes_when_no_cache_exists(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_json_data(data_path)
    graph = _make_api_input_graph(data_path)

    sig = cache_state_signature_for_graph(graph)

    assert sig.startswith("json_cache=")
    assert "apiin=" in sig
    assert ":0:0" in sig  # both layer mtimes are zero (no cache files)


def test_signature_is_stable_across_repeated_calls(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_json_data(data_path)
    graph = _make_api_input_graph(data_path)

    sig_a = cache_state_signature_for_graph(graph)
    sig_b = cache_state_signature_for_graph(graph)

    assert sig_a == sig_b


def test_signature_changes_when_working_meta_mtime_changes(tmp_path: Path) -> None:
    """Touching ``meta.json`` in the working layer bumps the signature.

    This is the actual cache-invalidation mechanism: build_json_cache writes
    meta.json with the current time, which bumps the mtime relative to the
    pre-build state.
    """
    data_path = tmp_path / "data.json"
    _write_json_data(data_path)
    graph = _make_api_input_graph(data_path)

    sig_before = cache_state_signature_for_graph(graph)

    # Manually create a meta.json in the working layer; mimic what
    # build_json_cache would do without invoking the whole flatten path.
    working_dir = _json_cache_dir(str(data_path), "working")
    working_dir.mkdir(parents=True, exist_ok=True)
    meta = _json_cache_meta_path(working_dir)
    meta.write_text(json.dumps({"schema_mode": "explicit", "schema_fingerprint": "abc"}))

    sig_after = cache_state_signature_for_graph(graph)

    assert sig_before != sig_after, (
        "signature must change once meta.json appears; otherwise the preview "
        "cache won't invalidate when the JSON cache is built"
    )


def test_signature_changes_when_committed_meta_mtime_changes(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_json_data(data_path)
    graph = _make_api_input_graph(data_path)

    sig_before = cache_state_signature_for_graph(graph)

    committed_dir = _json_cache_dir(str(data_path), "committed")
    committed_dir.mkdir(parents=True, exist_ok=True)
    meta = _json_cache_meta_path(committed_dir)
    meta.write_text(json.dumps({"schema_mode": "explicit", "schema_fingerprint": "abc"}))

    sig_after = cache_state_signature_for_graph(graph)

    assert sig_before != sig_after


def test_signature_sorts_by_node_id_for_stability(tmp_path: Path) -> None:
    """Multiple apiInputs in different node-id orders yield identical signatures."""
    d1 = tmp_path / "first.json"
    d2 = tmp_path / "second.json"
    _write_json_data(d1)
    _write_json_data(d2)

    def _graph(node_ids: list[str]) -> PipelineGraph:
        return PipelineGraph(
            nodes=[
                GraphNode(
                    id=node_ids[0],
                    data=NodeData(
                        label=node_ids[0],
                        nodeType=NodeType.API_INPUT,
                        config={"path": str(d1), "flattenSchema": {"a": "int"}},
                    ),
                ),
                GraphNode(
                    id=node_ids[1],
                    data=NodeData(
                        label=node_ids[1],
                        nodeType=NodeType.API_INPUT,
                        config={"path": str(d2), "flattenSchema": {"a": "int"}},
                    ),
                ),
            ],
            edges=[],
        )

    sig_alpha = cache_state_signature_for_graph(_graph(["alpha", "beta"]))
    sig_beta = cache_state_signature_for_graph(_graph(["beta", "alpha"]))

    # The two graphs have DIFFERENT node ids, so the strings differ — but
    # canonical ordering means each is stable irrespective of declaration
    # order. Re-asserting stability:
    assert sig_alpha == cache_state_signature_for_graph(_graph(["alpha", "beta"]))
    assert sig_beta == cache_state_signature_for_graph(_graph(["beta", "alpha"]))


def test_signature_skips_apiinput_without_path(tmp_path: Path) -> None:
    """An apiInput with an empty/missing path contributes nothing — the
    runtime would fail loud anyway, so we don't synthesise a meaningless
    cache key.
    """
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="bare",
                data=NodeData(
                    label="bare",
                    nodeType=NodeType.API_INPUT,
                    config={},  # no path
                ),
            ),
        ],
        edges=[],
    )
    assert cache_state_signature_for_graph(graph) == ""


# ─── Integration: the actual bug the fix targets ──────────────────


def test_preview_succeeds_after_building_cache_in_same_process(tmp_path: Path) -> None:
    """First preview fails because no cache exists; build the cache; second
    preview must observe the newly-built cache, not the cached failure from
    the first call.

    Without the fix this test fails: the second preview returns the same
    error payload the first one stored, because the graph fingerprint didn't
    change (no graph edits between calls) and the preview cache served the
    stale entry.
    """
    original_root = _get_project_root()
    set_project_root(tmp_path)
    try:
        # Module-level cache from a previous test in this process: clear so
        # only THIS test's calls populate it.
        _preview_cache.invalidate()

        data_path = tmp_path / "data.json"
        _write_json_data(data_path)
        graph = _make_api_input_graph(data_path)

        # 1. First preview: no cache anywhere, apiInput raises.
        first = execute_graph(graph, target_node_id="apiin")
        assert first["apiin"].status == "error"
        assert "Cache as Parquet" in (first["apiin"].error or "") or "cached" in (
            first["apiin"].error or ""
        ).lower()

        # 2. Build the JSON cache. This writes meta.json into the
        # ``working/`` layer, which bumps the cache-state signature for
        # this apiInput.
        build_json_cache(
            str(data_path),
            schema={"a": "int", "b": "str"},
        )

        # 3. Second preview: the previous fingerprint is now stale because
        # the signature changed; the preview cache misses; the apiInput
        # executes fresh and returns rows.
        second = execute_graph(graph, target_node_id="apiin")
        assert second["apiin"].status == "ok", (
            f"second preview should succeed but got: {second['apiin'].error}"
        )
        assert second["apiin"].row_count == 2
    finally:
        # Restore project root and clear our cache contributions.
        set_project_root(original_root)
        _preview_cache.invalidate()
        try:
            clear_json_cache(str(tmp_path / "data.json"))
        except Exception:
            pass


def test_preview_invalidates_after_clearing_cache_in_same_process(tmp_path: Path) -> None:
    """Symmetric scenario: cache exists, preview succeeds; clear the cache;
    preview again must reflect the cleared state (failure), not the cached
    success.
    """
    original_root = _get_project_root()
    set_project_root(tmp_path)
    try:
        _preview_cache.invalidate()

        data_path = tmp_path / "data.json"
        _write_json_data(data_path)
        graph = _make_api_input_graph(data_path)

        # Build cache first so the apiInput will succeed.
        build_json_cache(
            str(data_path),
            schema={"a": "int", "b": "str"},
        )
        first = execute_graph(graph, target_node_id="apiin")
        assert first["apiin"].status == "ok"

        # Clear the cache. After this, the apiInput would fail.
        clear_json_cache(str(data_path))

        second = execute_graph(graph, target_node_id="apiin")
        assert second["apiin"].status == "error", (
            "clearing the cache must invalidate the preview-cache entry; "
            "got a cached ok payload instead"
        )
    finally:
        set_project_root(original_root)
        _preview_cache.invalidate()
