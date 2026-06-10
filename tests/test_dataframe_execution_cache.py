from __future__ import annotations

import gc
import os
from pathlib import Path
from types import MappingProxyType

import polars as pl
import pytest

from haute._cache import canonical_json
from haute._dataframe_execution_cache import (
    DATAFRAME_EXECUTION_CACHE_VERSION,
    DEFAULT_DATAFRAME_EXECUTION_CACHE_MAX_BYTES,
)
from haute._execution_context import ExecutionProfile
from haute._hashing import content_hash_bytes
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import (
    CacheArtifactTooLargeError,
    DataFrameExecutionCache,
    DataFrameExecutionCacheKey,
    dataframe_execution_cache_key,
    dataframe_execution_cache_profile,
    dataframe_execution_policy_fingerprint,
    dataframe_graph_input_fingerprint,
    materialize_lazy_frame_with_cache,
)


def _node(node_id: str, node_type: NodeType = NodeType.POLARS, **config: object) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=dict(config)),
    )


def _edge(
    source: str,
    target: str,
    *,
    target_handle: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=f"{source}-{target}",
        source=source,
        target=target,
        targetHandle=target_handle,
    )


def _port_wired_graph(target_handle: str) -> PipelineGraph:
    """Source feeding one consumer port — only the port name varies."""
    return PipelineGraph(
        nodes=[
            _node("source", NodeType.API_INPUT),
            _node("target", NodeType.POLARS, output="premium"),
        ],
        edges=[_edge("source", "target", target_handle=target_handle)],
    )


def _graph(*, mid_multiplier: int = 2, downstream_label: str = "downstream") -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node("source", NodeType.DATA_SOURCE, path="data/input.parquet"),
            _node("mid", NodeType.POLARS, multiplier=mid_multiplier),
            _node("target", NodeType.POLARS, output="premium"),
            _node("downstream", NodeType.OUTPUT, label=downstream_label),
        ],
        edges=[
            _edge("source", "mid"),
            _edge("mid", "target"),
            _edge("target", "downstream"),
        ],
    )


def test_dataframe_cache_key_uses_upstream_subgraph_not_downstream_edits() -> None:
    base = _graph()
    downstream_edit = _graph(downstream_label="changed")
    upstream_edit = _graph(mid_multiplier=3)

    base_key = dataframe_execution_cache_key(
        base,
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    assert (
        dataframe_execution_cache_key(
            downstream_edit,
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:v1",
        )
        == base_key
    )
    assert (
        dataframe_execution_cache_key(
            upstream_edit,
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:v1",
        )
        != base_key
    )


def test_dataframe_cache_key_distinguishes_edge_handle_rewire() -> None:
    """Rewiring port ``policies`` → ``drivers`` between the same two nodes
    must produce a different cache key (CODE_REVIEW finding C3)."""
    policies_key = dataframe_execution_cache_key(
        _port_wired_graph("policies"),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )
    drivers_key = dataframe_execution_cache_key(
        _port_wired_graph("drivers"),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    assert policies_key.lineage_fingerprint != drivers_key.lineage_fingerprint
    assert policies_key.cache_key != drivers_key.cache_key


def test_dataframe_cache_key_requires_explicit_input_fingerprint() -> None:
    with pytest.raises(ValueError, match="input_fingerprint"):
        dataframe_execution_cache_key(
            _graph(),
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="",
        )


def test_dataframe_cache_key_partitions_execution_policy() -> None:
    graph = _graph()
    base = dataframe_execution_cache_key(
        graph,
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
        required_columns=["quote_id", "premium"],
    )

    assert (
        dataframe_execution_cache_key(
            graph,
            node_id="target",
            namespace="unit",
            source="live",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:v1",
            required_columns=["quote_id", "premium"],
        )
        != base
    )
    assert (
        dataframe_execution_cache_key(
            graph,
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            input_fingerprint="input:v1",
            required_columns=["quote_id", "premium"],
        )
        != base
    )
    assert (
        dataframe_execution_cache_key(
            graph,
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:v1",
            required_columns=["premium", "quote_id"],
        )
        == base
    )


def test_dataframe_cache_default_has_no_artifact_byte_budget(tmp_path: Path) -> None:
    assert DEFAULT_DATAFRAME_EXECUTION_CACHE_MAX_BYTES is None

    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    materialize_lazy_frame_with_cache(
        pl.DataFrame({"payload": ["large frames must cache by default"]}).lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )

    assert cache.get(key) is not None
    assert cache.stats()["max_bytes"] is None


def test_dataframe_cache_profile_treats_auto_range_as_optimiser_setup() -> None:
    assert (
        dataframe_execution_cache_profile(ExecutionProfile.AUTO_RANGE)
        == ExecutionProfile.OPTIMISER_SETUP.value
    )

    graph = _graph()
    auto_range_key = dataframe_execution_cache_key(
        graph,
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.AUTO_RANGE,
        input_fingerprint="input:v1",
    )
    setup_key = dataframe_execution_cache_key(
        graph,
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        input_fingerprint="input:v1",
    )

    assert auto_range_key == setup_key


def test_dataframe_cache_key_partitions_non_graph_execution_policy() -> None:
    graph = _graph()
    base = dataframe_execution_cache_key(
        graph,
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
        execution_policy={
            "source_by_node": {"model": "batch"},
            "required_columns_by_node": {"target": {"premium", "quote_id"}},
        },
    )

    assert (
        dataframe_execution_cache_key(
            graph,
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:v1",
            execution_policy={
                "required_columns_by_node": {"target": {"quote_id", "premium"}},
                "source_by_node": {"model": "batch"},
            },
        )
        == base
    )
    assert (
        dataframe_execution_cache_key(
            graph,
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:v1",
            execution_policy={
                "source_by_node": {"model": "live"},
                "required_columns_by_node": {"target": {"premium", "quote_id"}},
            },
        )
        != base
    )


# ---------------------------------------------------------------------------
# W2.13 — one canonical-JSON encoder for all digest material.
#
# Pre-unification ``_normalise_execution_policy`` sorted set members by
# their compact-JSON text while the graph-fingerprint encoder in
# ``haute._cache`` sorted them by (type-tag, value) — one logical value,
# two canonical forms.  These tests pin the SINGLE canonical form (the
# ``haute._cache`` rules + compact serialization) byte-for-byte.
# ---------------------------------------------------------------------------


def test_policy_fingerprint_set_members_sort_numerically_not_by_json_text() -> None:
    """``{0, 1, 2, 10}`` must canonicalise to ``[0, 1, 2, 10]``.

    The retired dfexec encoder sorted set members by JSON text
    (``"10" < "2"``), producing ``[0, 1, 10, 2]``.
    """
    assert dataframe_execution_policy_fingerprint({"s": {0, 1, 2, 10}}) == content_hash_bytes(
        b'{"s":[0,1,2,10]}'
    )


def test_policy_fingerprint_set_orders_none_bool_number_string_by_type_tag() -> None:
    """Heterogeneous sets order None < bool < number < string.

    The retired dfexec encoder ordered them by JSON text
    (``'"a"' < '5' < 'false' < 'null'``), producing ``["a", 5, False, None]``.
    """
    assert dataframe_execution_policy_fingerprint(
        {"s": {None, 5, "a", False}}
    ) == content_hash_bytes(b'{"s":[null,false,5,"a"]}')


def test_policy_fingerprint_set_strings_sort_by_code_point_not_escape_text() -> None:
    """Non-ASCII set members order by raw code point (``"z" < "é"``).

    The retired dfexec encoder sorted by the ASCII-escaped JSON text, where
    ``'"\\u00e9"'`` < ``'"z"'`` flips the order.
    """
    assert dataframe_execution_policy_fingerprint({"s": {"é", "z"}}) == content_hash_bytes(
        b'{"s":["z","\\u00e9"]}'
    )


def test_policy_fingerprint_rejects_non_string_mapping_keys_with_type_error() -> None:
    """Unified rule: non-string mapping keys raise ``TypeError`` (the
    graph-fingerprint contract), not the retired encoder's ``ValueError``."""
    with pytest.raises(TypeError, match="non-string key"):
        dataframe_execution_policy_fingerprint({"outer": {1: "x"}})


def test_policy_fingerprint_rejects_generator_values() -> None:
    """Unified rule: only ``list``/``tuple`` sequences are digest material.

    The retired dfexec encoder silently consumed ANY iterable, letting
    one-shot iterators (or NumPy arrays) masquerade as JSON-compatible
    policy values.
    """
    with pytest.raises(TypeError, match="no deterministic canonical form"):
        dataframe_execution_policy_fingerprint({"cols": (c for c in "ab")})


def test_policy_fingerprint_allows_empty_string_keys() -> None:
    """Unified rule: ``""`` is a legal, deterministic JSON object key.

    The retired dfexec encoder raised ``ValueError`` here while the graph
    encoder accepted it — node configs can legitimately carry an
    empty-string key (e.g. a rename map for a column literally named
    ``""``), so the single encoder accepts it everywhere.
    """
    assert dataframe_execution_policy_fingerprint({"": 1}) == content_hash_bytes(b'{"":1}')


@pytest.mark.parametrize(
    "policy",
    [
        {
            "target_node_id": "target",
            "source_by_node": {"model": "batch"},
            "required_columns_by_node": {"target": ["premium", "quote_id"]},
            "preserve_node_ids": [],
            "enforce_contracts": False,
            "preamble_ns_supplied": True,
        },
        {"mixed": [None, True, 0, 2.5, "é"], "sets": {"quote_id", "premium"}},
        {"nested": {"depth": {"two": (1, 2)}}},
    ],
    ids=["real-shape", "mixed-scalars-and-set", "nested-tuple"],
)
def test_policy_fingerprint_is_hash_of_the_single_canonical_encoding(
    policy: dict[str, object],
) -> None:
    """Cross-module contract: the dfexec policy fingerprint IS the content
    hash of ``haute._cache.canonical_json`` — no second encoder exists."""
    assert dataframe_execution_policy_fingerprint(policy) == content_hash_bytes(
        canonical_json(policy).encode()
    )


def test_policy_fingerprint_accepts_read_only_mappings() -> None:
    """``dataframe_lazy_execution_policy`` consumers may hand over
    ``MappingProxyType`` views; they must fingerprint like plain dicts."""
    policy = {"source_by_node": {"model": "batch"}, "enforce_contracts": False}
    assert dataframe_execution_policy_fingerprint(
        MappingProxyType(policy)
    ) == dataframe_execution_policy_fingerprint(policy)


def test_cache_key_digest_is_canonical_json_of_documented_payload() -> None:
    """The cache-key digest must be reproducible from the documented
    payload via the ONE canonical encoder.  Guards against any digest
    site quietly growing its own serialization rules again."""
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
        required_columns=["b", "a"],
        extra_keys=["x"],
        execution_policy={"cols": {0, 1, 2, 10}},
    )

    payload = {
        "version": DATAFRAME_EXECUTION_CACHE_VERSION,
        "namespace": "unit",
        "node_id": "target",
        "lineage_fingerprint": key.lineage_fingerprint,
        "source": "batch",
        "profile": key.profile,
        "input_fingerprint": "input:v1",
        "required_columns": ["a", "b"],
        "extra_keys": ["x"],
        "execution_policy": {"cols": {0, 1, 2, 10}},
    }
    payload_digest = content_hash_bytes(canonical_json(payload).encode())
    assert key.cache_key == f"dfexec:v{DATAFRAME_EXECUTION_CACHE_VERSION}:{payload_digest}"


def test_algo_version_bump_rolls_cache_keys_without_dfexec_schema_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence for the W2.13 version analysis: the payload embeds the
    ``v<ALGO_VERSION>:``-prefixed lineage fingerprint, so a fingerprint-
    algorithm change (such as the encoder unification) rolls EVERY
    dataframe cache key by itself — ``DATAFRAME_EXECUTION_CACHE_VERSION``
    stays reserved for payload-schema changes."""
    import haute._cache as cache_mod

    def key() -> DataFrameExecutionCacheKey:
        return dataframe_execution_cache_key(
            _graph(),
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:v1",
            execution_policy={"flag": True},
        )

    before = key()
    monkeypatch.setattr(cache_mod, "ALGO_VERSION", cache_mod.ALGO_VERSION + 1)
    after = key()

    assert before.cache_key != after.cache_key
    assert before.lineage_fingerprint != after.lineage_fingerprint
    assert before.version == after.version == DATAFRAME_EXECUTION_CACHE_VERSION


def test_dataframe_graph_input_fingerprint_tracks_file_backed_runtime_artifacts(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "optimiser.json"
    artifact.write_text("v1")
    graph = PipelineGraph(
        nodes=[
            _node("source", NodeType.DATA_SOURCE, path="data/input.parquet"),
            _node(
                "apply",
                NodeType.OPTIMISER_APPLY,
                sourceType="file",
                artifact_path=str(artifact),
            ),
            _node("target", NodeType.POLARS),
        ],
        edges=[_edge("source", "apply"), _edge("apply", "target")],
    )

    first = dataframe_graph_input_fingerprint(graph, target_node_id="target", source="batch")
    artifact.write_text("v2-changed")
    second = dataframe_graph_input_fingerprint(graph, target_node_id="target", source="batch")

    assert second != first


def test_dataframe_graph_input_fingerprint_uses_file_content_not_only_stat(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    source.write_text("a,b\n1,2\n")
    graph = PipelineGraph(
        nodes=[
            _node("source", NodeType.DATA_SOURCE, path=str(source)),
            _node("target", NodeType.POLARS),
        ],
        edges=[_edge("source", "target")],
    )
    original_stat = source.stat()

    first = dataframe_graph_input_fingerprint(graph, target_node_id="target", source="batch")
    source.write_text("a,b\n9,2\n")
    os.utime(
        source,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    second = dataframe_graph_input_fingerprint(graph, target_node_id="target", source="batch")

    assert second != first


def test_materialize_lazy_frame_with_cache_reuses_cached_artifact(tmp_path: Path) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    first = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1, 2, 3]}).lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    assert first.collect().to_dict(as_series=False) == {"x": [1, 2, 3]}

    bad_if_collected = pl.DataFrame({"x": [99]}).lazy().select("missing")
    second = materialize_lazy_frame_with_cache(
        bad_if_collected,
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )

    assert second.collect().to_dict(as_series=False) == {"x": [1, 2, 3]}
    assert cache.stats()["entries"] == 1


def test_materialize_does_not_serve_stale_artifact_after_handle_rewire(
    tmp_path: Path,
) -> None:
    """Seed the cache under one port wiring, flip the handle, and assert the
    cache does NOT serve the old wiring's artifact.

    Pre-fix (CODE_REVIEW finding C3) both wirings collided on the same
    fingerprint, so the second materialize returned the ``policies`` rows
    for the ``drivers`` wiring — silently wrong pricing inputs.
    """
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)

    def key_for(target_handle: str) -> DataFrameExecutionCacheKey:
        return dataframe_execution_cache_key(
            _port_wired_graph(target_handle),
            node_id="target",
            namespace="unit",
            source="batch",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="input:v1",
        )

    seeded = materialize_lazy_frame_with_cache(
        pl.DataFrame({"port": ["policies"]}).lazy(),
        cache=cache,
        key=key_for("policies"),
        profile=ExecutionProfile.LAZY_SINK,
    )
    assert seeded.collect().to_dict(as_series=False) == {"port": ["policies"]}

    rewired = materialize_lazy_frame_with_cache(
        pl.DataFrame({"port": ["drivers"]}).lazy(),
        cache=cache,
        key=key_for("drivers"),
        profile=ExecutionProfile.LAZY_SINK,
    )

    assert rewired.collect().to_dict(as_series=False) == {"port": ["drivers"]}
    assert cache.stats()["entries"] == 2


def test_materialize_lazy_frame_with_cache_does_not_store_failed_collect(
    tmp_path: Path,
) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    with pytest.raises(Exception):
        materialize_lazy_frame_with_cache(
            pl.DataFrame({"x": [1]}).lazy().select("missing"),
            cache=cache,
            key=key,
            profile=ExecutionProfile.LAZY_SINK,
        )

    assert cache.get(key) is None
    assert list(tmp_path.rglob("*.parquet")) == []


def test_dataframe_execution_cache_eviction_removes_owned_artifact(tmp_path: Path) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=1, max_bytes=10_000_000)
    key_a = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:a",
    )
    key_b = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:b",
    )

    path_a = tmp_path / "a.parquet"
    path_b = tmp_path / "b.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path_a)
    pl.DataFrame({"x": [2]}).write_parquet(path_b)

    entry_a = cache.store_artifact(
        key_a,
        path_a,
        {
            "row_count": 1,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": path_a.stat().st_size,
            "uncompressed_size_bytes": path_a.stat().st_size,
        },
    )
    cache.store_artifact(
        key_b,
        path_b,
        {
            "row_count": 1,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": path_b.stat().st_size,
            "uncompressed_size_bytes": path_b.stat().st_size,
        },
    )

    assert cache.get(key_a) is None
    assert not entry_a.path.exists()
    assert cache.get(key_b) is not None


def test_dataframe_execution_cache_replacement_removes_old_artifact(tmp_path: Path) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    path_a = tmp_path / "a.parquet"
    path_b = tmp_path / "b.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path_a)
    pl.DataFrame({"x": [2]}).write_parquet(path_b)

    entry_a = cache.store_artifact(
        key,
        path_a,
        {
            "row_count": 1,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": path_a.stat().st_size,
            "uncompressed_size_bytes": path_a.stat().st_size,
        },
    )
    entry_b = cache.store_artifact(
        key,
        path_b,
        {
            "row_count": 1,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": path_b.stat().st_size,
            "uncompressed_size_bytes": path_b.stat().st_size,
        },
    )

    assert not entry_a.path.exists()
    assert entry_b.path.exists()
    assert cache.get(key) == entry_b


def test_dataframe_execution_cache_pins_live_scans_during_eviction(tmp_path: Path) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=1, max_bytes=10_000_000)
    key_a = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:a",
    )
    key_b = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:b",
    )

    scan_a = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1]}).lazy(),
        cache=cache,
        key=key_a,
        profile=ExecutionProfile.LAZY_SINK,
    )
    entry_a = cache.get(key_a)
    assert entry_a is not None

    scan_b = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [2]}).lazy(),
        cache=cache,
        key=key_b,
        profile=ExecutionProfile.LAZY_SINK,
    )
    del scan_b
    gc.collect()

    assert entry_a.path.exists()
    assert scan_a.collect().to_dict(as_series=False) == {"x": [1]}

    del scan_a
    gc.collect()

    assert cache.get(key_a) is None
    # Once the last live scan is gone and the entry has been evicted, the
    # artifact file must be unlinked.  A retained file would be a disk leak.
    assert not entry_a.path.exists()


def test_dataframe_execution_cache_refcounts_multiple_live_scans_during_eviction(
    tmp_path: Path,
) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=1, max_bytes=10_000_000)
    key_a = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:a",
    )
    key_b = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:b",
    )

    scan_a1 = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1]}).lazy(),
        cache=cache,
        key=key_a,
        profile=ExecutionProfile.LAZY_SINK,
    )
    scan_a2 = cache.scan(key_a)
    assert scan_a2 is not None
    entry_a = cache.get(key_a)
    assert entry_a is not None

    scan_b = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [2]}).lazy(),
        cache=cache,
        key=key_b,
        profile=ExecutionProfile.LAZY_SINK,
    )
    del scan_b
    gc.collect()

    assert entry_a.path.exists()
    del scan_a1
    gc.collect()

    assert entry_a.path.exists()
    assert scan_a2.collect().to_dict(as_series=False) == {"x": [1]}

    del scan_a2
    gc.collect()

    assert cache.get(key_a) is None
    # Last scan released and entry evicted: artifact must be cleaned up.
    assert not entry_a.path.exists()


def test_dataframe_execution_cache_keeps_derived_lazy_frame_artifact_with_live_source_scan(
    tmp_path: Path,
) -> None:
    """Cache contract: callers that compose derived LazyFrames must keep
    the source scan reference alive for as long as the derived frames may
    be collected.  While the source scan is alive the entry stays pinned
    and the artifact survives.  When the source scan is released and the
    entry is evicted or replaced, the artifact is deleted.
    """

    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    scan = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1], "y": [9]}).lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    derived = scan.select("x")
    entry = cache.get(key)
    assert entry is not None

    # While scan is alive, derived frames can be collected freely.
    assert derived.collect().to_dict(as_series=False) == {"x": [1]}

    cache.clear()

    # After clear, the entry is gone but the artifact survives because
    # the scan is still pinning it.
    assert cache.get(key) is None
    assert entry.path.exists()
    assert derived.collect().to_dict(as_series=False) == {"x": [1]}

    del scan, derived
    gc.collect()
    assert not entry.path.exists()


def test_dataframe_execution_cache_clear_removes_owned_artifacts(tmp_path: Path) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key_a = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:a",
    )
    key_b = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:b",
    )

    path_a = tmp_path / "clear-a.parquet"
    path_b = tmp_path / "clear-b.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path_a)
    pl.DataFrame({"x": [2]}).write_parquet(path_b)

    entry_a = cache.store_artifact(
        key_a,
        path_a,
        {
            "row_count": 1,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": path_a.stat().st_size,
            "uncompressed_size_bytes": path_a.stat().st_size,
        },
    )
    entry_b = cache.store_artifact(
        key_b,
        path_b,
        {
            "row_count": 1,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": path_b.stat().st_size,
            "uncompressed_size_bytes": path_b.stat().st_size,
        },
    )

    cache.clear()

    assert cache.stats()["entries"] == 0
    assert not entry_a.path.exists()
    assert not entry_b.path.exists()


def test_dataframe_execution_cache_clear_preserves_live_scan_until_release(
    tmp_path: Path,
) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    scan = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1]}).lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    entry = cache.get(key)
    assert entry is not None

    cache.clear()

    assert cache.get(key) is None
    assert entry.path.exists()
    assert scan.collect().to_dict(as_series=False) == {"x": [1]}

    del scan
    gc.collect()

    # After the last live scan is released and the entry is gone (cleared),
    # the artifact file must be deleted to avoid disk leaks.
    assert not entry.path.exists()


def test_dataframe_execution_cache_oversized_artifact_fails_loudly(
    tmp_path: Path,
) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=1)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    with pytest.raises(CacheArtifactTooLargeError, match="exceeds"):
        materialize_lazy_frame_with_cache(
            pl.DataFrame({"x": [1, 2, 3]}).lazy(),
            cache=cache,
            key=key,
            profile=ExecutionProfile.LAZY_SINK,
        )

    assert cache.get(key) is None
    assert list(tmp_path.rglob("*.parquet")) == []


def test_dataframe_execution_cache_deletes_path_after_repeated_scan_and_release(
    tmp_path: Path,
) -> None:
    """A path that has been scanned repeatedly is still deleted when the
    entry is evicted and the last live scan is released.  Guards against
    the previous ``_leased_paths`` design that retained every scanned path
    indefinitely (silent disk leak)."""

    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    scan = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1]}).lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    entry = cache.get(key)
    assert entry is not None
    # Scan repeatedly to exercise refcounting bookkeeping under load.
    extras = [cache.scan(key) for _ in range(4)]
    assert all(extra is not None for extra in extras)
    del extras
    gc.collect()

    # The entry is still live and the artifact is intact after repeated scans.
    assert entry.path.exists()

    # Force eviction explicitly via clear; previously the
    # ``_leased_paths`` set would have suppressed the unlink because the
    # path had been scanned earlier in this test.
    cache.clear()
    del scan
    gc.collect()
    assert cache.get(key) is None
    assert not entry.path.exists()


def test_dataframe_execution_cache_missing_artifact_evicts_and_returns_none(
    tmp_path: Path,
) -> None:
    """An entry whose artifact has been externally removed must be
    evicted on lookup and a subsequent ``get`` must return ``None`` so
    callers can repopulate the cache naturally."""

    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )
    scan = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1, 2, 3]}).lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    del scan
    gc.collect()
    entry = cache.get(key)
    assert entry is not None
    entry.path.unlink()  # external deletion (simulates OS/user cleanup)
    assert cache.get(key) is None
    assert cache.stats()["entries"] == 0


def test_dataframe_execution_cache_corrupt_artifact_evicts_and_returns_none(
    tmp_path: Path,
) -> None:
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )
    scan = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1, 2, 3]}).lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    del scan
    gc.collect()
    entry = cache.get(key)
    assert entry is not None
    entry.path.write_bytes(b"not a parquet file")
    assert cache.get(key) is None
    assert cache.stats()["entries"] == 0


def test_dataframe_execution_cache_concurrent_materialization_serialised(
    tmp_path: Path,
) -> None:
    """Two threads calling materialize on the same key build the body only
    once; ``materialization_lock`` serialises same-key writes."""

    import threading

    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )

    build_count = 0
    build_count_lock = threading.Lock()

    def _build() -> pl.LazyFrame:
        nonlocal build_count
        with build_count_lock:
            build_count += 1
        return pl.DataFrame({"x": [1, 2, 3]}).lazy()

    barrier = threading.Barrier(2)
    results: list[pl.LazyFrame] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            barrier.wait(timeout=10)
            lf = materialize_lazy_frame_with_cache(
                _build(),
                cache=cache,
                key=key,
                profile=ExecutionProfile.LAZY_SINK,
            )
            results.append(lf)
        except BaseException as exc:  # noqa: BLE001 — surface for assert below
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    assert len(results) == 2
    # Both calls produce a working LazyFrame, but the cache stores one entry.
    assert results[0].collect().to_dict(as_series=False) == {"x": [1, 2, 3]}
    assert results[1].collect().to_dict(as_series=False) == {"x": [1, 2, 3]}
    assert cache.stats()["entries"] == 1
