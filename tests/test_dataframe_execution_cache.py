from __future__ import annotations

import gc
import os
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import polars as pl
import pytest

from haute._cache import CacheConsumer, canonical_json, checked_cache_inputs
from haute._dataframe_execution_cache import (
    DATAFRAME_EXECUTION_CACHE_VERSION,
    DEFAULT_DATAFRAME_EXECUTION_CACHE_MAX_BYTES,
)
from haute._execution_context import ExecutionProfile
from haute._hashing import content_hash_bytes
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import (
    CacheArtifactCorruptError,
    CacheArtifactTooLargeError,
    DataFrameExecutionCache,
    DataFrameExecutionCacheKey,
    dataframe_execution_cache_key,
    dataframe_execution_cache_profile,
    dataframe_execution_policy_fingerprint,
    dataframe_frame_input_fingerprint,
    dataframe_graph_input_fingerprint,
    materialize_lazy_frame_with_cache,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


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
            _node("source", NodeType.DATA_INPUT, path="data/input.parquet"),
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
    payload_digest = content_hash_bytes(
        checked_cache_inputs(CacheConsumer.DATAFRAME_EXECUTION, payload).canonical_bytes
    )
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
            _node("source", NodeType.DATA_INPUT, path="data/input.parquet"),
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


def test_dataframe_frame_fingerprint_uses_versioned_canonical_uint64_buffer() -> None:
    frame = pl.DataFrame(
        {
            "id": [1, 2, 3],
            "amount": [1.5, 2.5, 3.5],
            "group": ["a", "b", "a"],
        }
    )
    hashes = frame.hash_rows(seed=0)
    expected_bytes = hashes.to_numpy().astype("<u8", copy=False).tobytes(order="C")

    with patch.object(pl.Series, "to_list", side_effect=AssertionError("decimal conversion")):
        fingerprint = dataframe_frame_input_fingerprint(frame)

    assert fingerprint == {
        "height": 3,
        "width": 3,
        "schema": {"id": "Int64", "amount": "Float64", "group": "String"},
        "row_hash_encoding": "polars-u64-le:v1",
        "row_hash": content_hash_bytes(expected_bytes),
    }


def test_dataframe_frame_fingerprint_preserves_null_and_empty_identity() -> None:
    empty_int = pl.DataFrame(schema={"value": pl.Int64})
    empty_string = pl.DataFrame(schema={"value": pl.String})
    with_nulls = pl.DataFrame({"value": [1, None, 3], "label": [None, "a", ""]})
    changed_nulls = pl.DataFrame({"value": [1, 2, 3], "label": [None, "a", ""]})

    assert dataframe_frame_input_fingerprint(empty_int) == dataframe_frame_input_fingerprint(
        empty_int.clone()
    )
    assert dataframe_frame_input_fingerprint(empty_int) != dataframe_frame_input_fingerprint(
        empty_string
    )
    assert dataframe_frame_input_fingerprint(with_nulls) == dataframe_frame_input_fingerprint(
        with_nulls.clone()
    )
    assert dataframe_frame_input_fingerprint(with_nulls) != dataframe_frame_input_fingerprint(
        changed_nulls
    )


def test_dataframe_graph_input_fingerprint_reuses_same_stat_gate_after_content_change(
    tmp_path: Path,
) -> None:
    """A byte edit below the explicit ``(mtime_ns, size)`` gate is a cache hit.

    Runtime-path fingerprints deliberately trade detection of same-size,
    same-mtime rewrites for avoiding a full content hash on every request.
    Independent JSON shred operations retain the stricter always-hash contract.
    """
    source = tmp_path / "source.csv"
    source.write_text("a,b\n1,2\n")
    graph = PipelineGraph(
        nodes=[
            _node("source", NodeType.DATA_INPUT, path=str(source)),
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

    assert second == first


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


def test_fresh_artifact_first_consume_does_not_reopen_through_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Store + first consume bypass transient reopen validation; later hits do not."""
    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
    )
    validations = 0

    def fail_reopen(_self: DataFrameExecutionCache, _entry: object) -> None:
        nonlocal validations
        validations += 1
        raise CacheArtifactCorruptError("transient reopen failure")

    monkeypatch.setattr(DataFrameExecutionCache, "_validate_entry", fail_reopen)

    first_scan = materialize_lazy_frame_with_cache(
        pl.DataFrame({"x": [1]}).lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    artifact = next(tmp_path.glob("*.parquet"))
    assert validations == 0
    assert first_scan.collect().to_dict(as_series=False) == {"x": [1]}

    del first_scan
    gc.collect()
    assert cache.get(key) is None
    assert validations == 1
    assert not artifact.exists()


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


# ---------------------------------------------------------------------------
# W2.10 — never evict the artifact being stored.
#
# Storing a fresh artifact under byte pressure must never evict the
# artifact itself (or strand the executor that is about to read it).
# The store+first-consume window is the ``materialization_lock`` held by
# ``materialize_lazy_frame_with_cache`` across store -> first scan; while
# it is open the key is exempt from VICTIM selection only — capacity
# accounting and the eviction order of every other entry are unchanged.
# ---------------------------------------------------------------------------


def _pressure_key(fingerprint: str) -> DataFrameExecutionCacheKey:
    return dataframe_execution_cache_key(
        _graph(),
        node_id="target",
        namespace="unit",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint=fingerprint,
    )


def _pressure_frame(rows: int, *, seed: int) -> pl.DataFrame:
    """Deterministic, poorly-compressible payload so parquet sizes scale
    with row count instead of collapsing under run-length encoding."""
    return pl.DataFrame({"x": [((i + seed) * 7919) % 104729 for i in range(rows)]})


def _measured_artifact_size(root: Path, frame: pl.DataFrame, fingerprint: str) -> int:
    """Materialize *frame* into a throwaway unbounded cache and return the
    exact artifact size the byte-capped store under test will produce."""
    probe = DataFrameExecutionCache(root=root, max_entries=4)
    key = _pressure_key(fingerprint)
    scan = materialize_lazy_frame_with_cache(
        frame.lazy(),
        cache=probe,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    entry = probe.get(key)
    assert entry is not None
    size = entry.size_bytes
    del scan
    gc.collect()
    probe.clear()
    return size


def test_byte_pressure_store_never_evicts_the_artifact_being_stored(tmp_path: Path) -> None:
    """W2.10 (CODE_REVIEW MEDIUM): storing a fresh artifact under byte
    pressure while every other entry is pinned by a live scan must never
    evict the artifact being stored.

    Pre-fix, ``put``'s eviction pass skipped the scan-pinned siblings and
    removed the just-written fresh entry — the only unpinned candidate —
    unlinked its parquet, and ``store_artifact`` raised
    ``DataFrameExecutionCacheError`` ("vanished immediately"), turning a
    cache-management event into a hard run failure for the executor that
    was about to read the artifact it had just written.
    """
    frame_a = _pressure_frame(256, seed=1)
    frame_b = _pressure_frame(256, seed=2)
    size_a = _measured_artifact_size(tmp_path / "probe-a", frame_a, "input:a")
    size_b = _measured_artifact_size(tmp_path / "probe-b", frame_b, "input:b")

    max_bytes = size_a + size_b - 1
    cache = DataFrameExecutionCache(root=tmp_path / "cache", max_entries=4, max_bytes=max_bytes)

    # The "running executor": holds A's scan for the rest of the run.
    scan_a = materialize_lazy_frame_with_cache(
        frame_a.lazy(),
        cache=cache,
        key=_pressure_key("input:a"),
        profile=ExecutionProfile.LAZY_SINK,
    )

    # Storing B byte-pressures the cache; the only unpinned entry is B itself.
    scan_b = materialize_lazy_frame_with_cache(
        frame_b.lazy(),
        cache=cache,
        key=_pressure_key("input:b"),
        profile=ExecutionProfile.LAZY_SINK,
    )

    assert scan_b.collect().to_dict(as_series=False) == frame_b.to_dict(as_series=False)
    assert scan_a.collect().to_dict(as_series=False) == frame_a.to_dict(as_series=False)
    stats = cache.stats()
    assert stats["entries"] == 2
    # Pinned-overflow allowance: while both artifacts are held by live
    # scans the cache may exceed its byte budget rather than fail the run.
    assert stats["bytes"] is not None and stats["bytes"] > max_bytes

    del scan_a, scan_b
    gc.collect()

    # Once the live scans are released the deferred byte debt is settled.
    trimmed = cache.stats()
    assert trimmed["entries"] == 1
    assert trimmed["bytes"] is not None and trimmed["bytes"] <= max_bytes


def test_store_window_protection_ends_after_first_consume_is_released(
    tmp_path: Path,
) -> None:
    """The just-stored artifact is protected only for its store+first-
    consume window.  Byte pressure still evicts the LRU *other* entry at
    store time (eviction order unchanged), and once an artifact's window
    has closed and its scans are released it is a normal LRU citizen.
    """
    frame_a = _pressure_frame(64, seed=1)
    frame_b = _pressure_frame(96, seed=2)
    frame_c = _pressure_frame(128, seed=3)
    size_a = _measured_artifact_size(tmp_path / "probe-a", frame_a, "input:a")
    size_b = _measured_artifact_size(tmp_path / "probe-b", frame_b, "input:b")
    size_c = _measured_artifact_size(tmp_path / "probe-c", frame_c, "input:c")

    max_bytes = max(size_a, size_b, size_c)
    # Preconditions for the eviction arithmetic asserted below.
    assert size_a + size_b > max_bytes
    assert size_b + size_c > max_bytes

    cache = DataFrameExecutionCache(root=tmp_path / "cache", max_entries=4, max_bytes=max_bytes)

    scan_a = materialize_lazy_frame_with_cache(
        frame_a.lazy(),
        cache=cache,
        key=_pressure_key("input:a"),
        profile=ExecutionProfile.LAZY_SINK,
    )
    entry_a = cache.get(_pressure_key("input:a"))
    assert entry_a is not None
    del scan_a
    gc.collect()  # A consumed and released: its protection window is over.

    scan_b = materialize_lazy_frame_with_cache(
        frame_b.lazy(),
        cache=cache,
        key=_pressure_key("input:b"),
        profile=ExecutionProfile.LAZY_SINK,
    )

    # Pressure at B's store evicts the LRU OTHER entry (A) — never fresh B.
    assert cache.get(_pressure_key("input:a")) is None
    assert not entry_a.path.exists()
    assert scan_b.collect().to_dict(as_series=False) == frame_b.to_dict(as_series=False)

    entry_b = cache.get(_pressure_key("input:b"))
    assert entry_b is not None
    del scan_b
    gc.collect()  # B's window closed and its first consume released.

    scan_c = materialize_lazy_frame_with_cache(
        frame_c.lazy(),
        cache=cache,
        key=_pressure_key("input:c"),
        profile=ExecutionProfile.LAZY_SINK,
    )

    # B is now a normal LRU citizen: pressure at C's store evicts it.
    assert cache.get(_pressure_key("input:b")) is None
    assert not entry_b.path.exists()
    assert scan_c.collect().to_dict(as_series=False) == frame_c.to_dict(as_series=False)


def test_concurrent_stores_under_byte_pressure_each_survive_their_own_window(
    tmp_path: Path,
) -> None:
    """Two threads storing different keys under byte pressure: each fresh
    artifact survives its own store+first-consume window and eviction
    takes the LRU OTHER (released) entry, for every interleaving of the
    two store windows.

    Pre-fix the second store to complete found every other entry pinned
    (victim already evicted, first store's artifact held by a live scan)
    and evicted its own fresh artifact — one worker failed hard.
    """
    import threading

    victim_frame = _pressure_frame(384, seed=1)
    frame_one = _pressure_frame(256, seed=2)
    frame_two = _pressure_frame(256, seed=3)
    size_victim = _measured_artifact_size(tmp_path / "probe-v", victim_frame, "input:victim")
    size_one = _measured_artifact_size(tmp_path / "probe-1", frame_one, "input:one")
    size_two = _measured_artifact_size(tmp_path / "probe-2", frame_two, "input:two")

    max_bytes = size_one + size_two - 1
    # Preconditions: the victim fits alone but the first pressured store
    # must evict it (victim is strictly larger than either fresh artifact).
    assert size_victim <= max_bytes
    assert size_victim > size_one
    assert size_victim > size_two

    cache = DataFrameExecutionCache(root=tmp_path / "cache", max_entries=4, max_bytes=max_bytes)

    victim_scan = materialize_lazy_frame_with_cache(
        victim_frame.lazy(),
        cache=cache,
        key=_pressure_key("input:victim"),
        profile=ExecutionProfile.LAZY_SINK,
    )
    del victim_scan
    gc.collect()  # victim resident, unpinned, LRU.

    barrier = threading.Barrier(2)
    results: dict[str, pl.LazyFrame] = {}
    errors: list[BaseException] = []

    def _worker(name: str, frame: pl.DataFrame, fingerprint: str) -> None:
        try:
            barrier.wait(timeout=10)
            results[name] = materialize_lazy_frame_with_cache(
                frame.lazy(),
                cache=cache,
                key=_pressure_key(fingerprint),
                profile=ExecutionProfile.LAZY_SINK,
            )
        except BaseException as exc:  # noqa: BLE001 — surfaced for assert below
            errors.append(exc)

    t1 = threading.Thread(target=_worker, args=("one", frame_one, "input:one"))
    t2 = threading.Thread(target=_worker, args=("two", frame_two, "input:two"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    assert results["one"].collect().to_dict(as_series=False) == frame_one.to_dict(as_series=False)
    assert results["two"].collect().to_dict(as_series=False) == frame_two.to_dict(as_series=False)
    # The LRU OTHER entry paid for the pressure; both fresh artifacts live.
    assert cache.get(_pressure_key("input:victim")) is None
    assert cache.get(_pressure_key("input:one")) is not None
    assert cache.get(_pressure_key("input:two")) is not None

    results.clear()
    gc.collect()

    trimmed = cache.stats()
    assert trimmed["entries"] == 1
    assert trimmed["bytes"] is not None and trimmed["bytes"] <= max_bytes


def test_store_window_settle_failure_releases_materialization_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure in the window-close settle must not orphan the per-key
    materialization lock.

    The settle (``_evict_if_over_capacity`` at window exit) can unlink an
    artifact, and on Windows ``Path.unlink`` raises ``PermissionError`` on
    a sharing violation (AV scanners, indexers).  The error must propagate
    loudly, but ``lock.release()`` must still run — otherwise any thread
    already blocked in ``lock.acquire()`` for that key hangs forever (it
    holds a strong ref to the orphaned RLock, so the WeakValueDictionary
    cannot self-heal, and a later ``clear()`` would hang too).
    """
    import threading

    frame_a = _pressure_frame(256, seed=1)
    frame_b = _pressure_frame(256, seed=2)
    size_a = _measured_artifact_size(tmp_path / "probe-a", frame_a, "input:a")
    scratch_b = tmp_path / "scratch-b.parquet"
    frame_b.write_parquet(scratch_b)
    size_b = scratch_b.stat().st_size

    max_bytes = size_a + size_b - 1
    cache = DataFrameExecutionCache(root=tmp_path / "cache", max_entries=4, max_bytes=max_bytes)
    key_a = _pressure_key("input:a")
    key_b = _pressure_key("input:b")

    # A is held by a live scan, so at B's window close the settle's only
    # eviction victim is B itself (stored but never consumed).
    scan_a = materialize_lazy_frame_with_cache(
        frame_a.lazy(),
        cache=cache,
        key=key_a,
        profile=ExecutionProfile.LAZY_SINK,
    )

    real_unlink = Path.unlink
    lock_holder: list[threading.RLock] = []

    with pytest.raises(PermissionError, match="sharing violation"):
        with cache.materialization_lock(key_b):
            path_b = cache.path_for_key(key_b)
            frame_b.write_parquet(path_b)
            cache.store_artifact(
                key_b,
                path_b,
                {
                    "row_count": frame_b.height,
                    "column_count": frame_b.width,
                    "columns": {"x": "Int64"},
                    "size_bytes": path_b.stat().st_size,
                    "uncompressed_size_bytes": path_b.stat().st_size,
                },
            )
            # Hold a strong ref so the orphaned-RLock state (the bug)
            # cannot be masked by WeakValueDictionary self-healing.
            lock_holder.append(cache._materialize_locks[key_b.cache_key])

            target = path_b.resolve()

            def _failing_unlink(self: Path, missing_ok: bool = False) -> None:
                if self == target:
                    raise PermissionError(13, "sharing violation (simulated)", str(self))
                real_unlink(self, missing_ok=missing_ok)

            monkeypatch.setattr(Path, "unlink", _failing_unlink)
            # Window exit: B's store-pin is dropped, the settle evicts B
            # (the only unpinned entry), and B's unlink raises.

    monkeypatch.setattr(Path, "unlink", real_unlink)

    # The per-key lock must have been released despite the settle failure:
    # a second thread can acquire it.  The timeout makes a regression FAIL
    # fast instead of hanging the suite.
    acquired: list[bool] = []

    def _try_acquire() -> None:
        ok = lock_holder[0].acquire(timeout=2)
        acquired.append(ok)
        if ok:
            lock_holder[0].release()

    thread = threading.Thread(target=_try_acquire)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert acquired == [True]

    # No leaked store-pin, and the pinned sibling is intact.
    assert cache._store_pins == {}
    assert scan_a.collect().to_dict(as_series=False) == frame_a.to_dict(as_series=False)


def test_oversized_artifact_policy_unchanged_and_no_stale_window_state(
    tmp_path: Path,
) -> None:
    """An artifact larger than the whole budget is still rejected with
    ``CacheArtifactTooLargeError`` (callers continue uncached) and the
    failed store leaves no protection state behind: the same key stores a
    fitting artifact normally afterwards."""
    small_frame = _pressure_frame(64, seed=1)
    big_frame = _pressure_frame(2048, seed=2)
    size_small = _measured_artifact_size(tmp_path / "probe-s", small_frame, "input:v1")

    cache = DataFrameExecutionCache(
        root=tmp_path / "cache",
        max_entries=4,
        max_bytes=size_small,
    )
    key = _pressure_key("input:v1")

    with pytest.raises(CacheArtifactTooLargeError, match="exceeds"):
        materialize_lazy_frame_with_cache(
            big_frame.lazy(),
            cache=cache,
            key=key,
            profile=ExecutionProfile.LAZY_SINK,
        )

    assert cache.get(key) is None
    assert list((tmp_path / "cache").rglob("*.parquet")) == []
    assert cache.stats()["pinned_entries"] == 0

    scan = materialize_lazy_frame_with_cache(
        small_frame.lazy(),
        cache=cache,
        key=key,
        profile=ExecutionProfile.LAZY_SINK,
    )
    assert scan.collect().to_dict(as_series=False) == small_frame.to_dict(as_series=False)
    assert cache.get(key) is not None


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


def test_same_key_waiter_does_not_block_unrelated_materialization_lock(
    tmp_path: Path,
) -> None:
    """A thread waiting for key A must not hold the global lock registry
    guard and block an unrelated key B materialization.

    Pre-fix, ``materialization_lock`` acquired the per-key lock while still
    holding ``_materialize_locks_guard``. A same-key waiter therefore
    serialized every other key behind key A.
    """

    import threading

    class BlockingLock:
        __slots__ = ("_locked", "_released", "_state_lock", "blocked", "__weakref__")

        def __init__(self) -> None:
            self._locked = False
            self._released = threading.Event()
            self._state_lock = threading.Lock()
            self.blocked = threading.Event()

        def acquire(self) -> bool:
            with self._state_lock:
                if not self._locked:
                    self._locked = True
                    return True
                self.blocked.set()
            if not self._released.wait(timeout=5):
                raise TimeoutError("same-key waiter did not unblock")
            with self._state_lock:
                self._locked = True
            return True

        def release(self) -> None:
            with self._state_lock:
                self._locked = False
                self._released.set()

    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key_a = _pressure_key("input:a")
    key_b = _pressure_key("input:b")
    lock_a = BlockingLock()
    lock_a.acquire()
    cache._materialize_locks[key_a.cache_key] = lock_a  # noqa: SLF001 - lock contract test

    errors: list[BaseException] = []
    same_key_entered = threading.Event()
    unrelated_entered = threading.Event()

    def _same_key_waiter() -> None:
        try:
            with cache.materialization_lock(key_a):
                same_key_entered.set()
        except BaseException as exc:  # noqa: BLE001 - surfaced for assert below
            errors.append(exc)

    def _unrelated_key_worker() -> None:
        try:
            with cache.materialization_lock(key_b):
                unrelated_entered.set()
        except BaseException as exc:  # noqa: BLE001 - surfaced for assert below
            errors.append(exc)

    same_key_thread = threading.Thread(target=_same_key_waiter)
    same_key_thread.start()
    assert lock_a.blocked.wait(timeout=5), "same-key waiter never blocked on key A"

    unrelated_thread = threading.Thread(target=_unrelated_key_worker)
    unrelated_thread.start()

    try:
        assert unrelated_entered.wait(timeout=1), (
            "same-key waiter held the global materialization-lock guard and blocked unrelated key B"
        )
    finally:
        lock_a.release()
        same_key_thread.join(timeout=5)
        unrelated_thread.join(timeout=5)

    assert not same_key_thread.is_alive()
    assert not unrelated_thread.is_alive()
    assert same_key_entered.is_set()
    assert not errors, errors


def test_clear_keeps_in_flight_materialization_locks_discoverable(
    tmp_path: Path,
) -> None:
    """A clear between lock lookup and acquisition must not split one key
    across two locks.

    The registry is weak, but an in-flight materializer owns a strong local
    reference.  ``clear()`` must leave that active lock discoverable so a
    later same-key materializer waits on it instead of creating a second lock.
    """

    import threading

    class PausableLock:
        __slots__ = (
            "_condition",
            "_locked",
            "allow_first_acquire",
            "first_waiting",
            "__weakref__",
        )

        def __init__(self) -> None:
            self._condition = threading.Condition()
            self._locked = False
            self.allow_first_acquire = threading.Event()
            self.first_waiting = threading.Event()

        def acquire(self) -> bool:
            if threading.current_thread().name == "first-materializer":
                self.first_waiting.set()
                if not self.allow_first_acquire.wait(timeout=5):
                    raise TimeoutError("first materializer never resumed")
            with self._condition:
                while self._locked:
                    self._condition.wait(timeout=5)
                self._locked = True
            return True

        def release(self) -> None:
            with self._condition:
                self._locked = False
                self._condition.notify_all()

    cache = DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=10_000_000)
    key = _pressure_key("input:a")
    pausable_lock = PausableLock()
    cache._materialize_locks[key.cache_key] = pausable_lock  # noqa: SLF001 - lock race test

    errors: list[BaseException] = []
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def _first_materializer() -> None:
        try:
            with cache.materialization_lock(key):
                first_entered.set()
                release_first.wait(timeout=5)
        except BaseException as exc:  # noqa: BLE001 - surfaced for assert below
            errors.append(exc)

    def _second_materializer() -> None:
        try:
            with cache.materialization_lock(key):
                second_entered.set()
        except BaseException as exc:  # noqa: BLE001 - surfaced for assert below
            errors.append(exc)

    first = threading.Thread(target=_first_materializer, name="first-materializer")
    first.start()
    assert pausable_lock.first_waiting.wait(timeout=5), "first materializer never looked up lock"

    cache.clear()

    pausable_lock.allow_first_acquire.set()
    assert first_entered.wait(timeout=5), "first materializer did not enter"

    second = threading.Thread(target=_second_materializer, name="second-materializer")
    second.start()
    try:
        assert not second_entered.wait(timeout=0.25), (
            "clear() removed an in-flight same-key lock from the registry, "
            "allowing a second materializer to enter concurrently"
        )
    finally:
        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_entered.is_set()
    assert not errors, errors
