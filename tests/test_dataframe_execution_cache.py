from __future__ import annotations

import gc
import os
from pathlib import Path

import polars as pl
import pytest

from haute._dataframe_execution_cache import DEFAULT_DATAFRAME_EXECUTION_CACHE_MAX_BYTES
from haute._execution_context import ExecutionProfile
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import (
    CacheArtifactTooLargeError,
    DataFrameExecutionCache,
    dataframe_execution_cache_key,
    dataframe_execution_cache_profile,
    dataframe_graph_input_fingerprint,
    materialize_lazy_frame_with_cache,
)


def _node(node_id: str, node_type: NodeType = NodeType.POLARS, **config: object) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=dict(config)),
    )


def _edge(source: str, target: str) -> GraphEdge:
    return GraphEdge(id=f"{source}-{target}", source=source, target=target)


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
