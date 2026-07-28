from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

import haute.execution as execution
from haute._execution_context import ExecutionProfile
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


def _node(
    node_id: str,
    node_type: NodeType = NodeType.POLARS,
    config: dict[str, Any] | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=config or {}),
    )


def _edge(source: str, target: str) -> GraphEdge:
    return GraphEdge(id=f"{source}-{target}", source=source, target=target)


def _graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node("source", NodeType.DATA_INPUT),
            _node("target", NodeType.POLARS),
        ],
        edges=[_edge("source", "target")],
    )


def _chain_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node("source", NodeType.DATA_INPUT),
            _node("mid", NodeType.POLARS),
            _node("target", NodeType.POLARS),
        ],
        edges=[_edge("source", "mid"), _edge("mid", "target")],
    )


def _cache_key(
    graph: PipelineGraph,
    *,
    input_fingerprint: str,
    node_id: str = "target",
    target_node_id: str | None = "target",
) -> execution.DataFrameExecutionCacheKey:
    policy = execution.dataframe_lazy_execution_policy(
        target_node_id=target_node_id,
        source_by_node=None,
        required_columns_by_node=None,
        preserve_node_ids=None,
        enforce_contracts=False,
        preamble_ns_supplied=False,
    )
    return execution.dataframe_execution_cache_key(
        graph,
        node_id=node_id,
        namespace="execute-lazy-test",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint=input_fingerprint,
        execution_policy=policy,
    )


def _cache_request(
    cache: execution.DataFrameExecutionCache,
    key: execution.DataFrameExecutionCacheKey,
) -> Any:
    request_cls = getattr(execution, "DataFrameExecutionCacheRequest", None)
    if request_cls is None:
        pytest.fail(
            "haute.execution.DataFrameExecutionCacheRequest is the intended "
            "public API for opt-in lazy execution dataframe caching."
        )
    return request_cls(cache=cache, keys_by_node={key.node_id: key})


def _cache_request_from_facade(
    graph: PipelineGraph,
    cache: execution.DataFrameExecutionCache,
    *,
    input_fingerprint: str,
    node_id: str = "target",
    target_node_id: str | None = "target",
    required_columns_by_node: dict[str, frozenset[str]] | None = None,
) -> execution.DataFrameExecutionCacheRequest:
    return execution.build_dataframe_execution_cache_request(
        graph,
        node_ids={node_id},
        namespace="execute-lazy-test",
        source="batch",
        target_node_id=target_node_id,
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint=input_fingerprint,
        required_columns_by_node=required_columns_by_node,
        cache=cache,
    )


def _build_fn(
    calls: list[str],
    *,
    source_values: list[int] | None = None,
):
    values = source_values or [1, 2, 3]

    def build_node(node: GraphNode, **_: Any):
        calls.append(node.id)
        if node.data.nodeType == NodeType.DATA_INPUT:
            return node.id, lambda: pl.DataFrame({"x": values}).lazy(), True
        return node.id, lambda input_lf: input_lf.with_columns(y=pl.col("x") * 2), False

    return build_node


def _failing_source_build_fn(node: GraphNode, **_: Any):
    if node.data.nodeType == NodeType.DATA_INPUT:

        def fail_source() -> pl.LazyFrame:
            raise RuntimeError("source builder exploded")

        return node.id, fail_source, True
    return node.id, lambda input_lf: input_lf, False


def _chain_build_fn(calls: list[str]):
    def build_node(node: GraphNode, **_: Any):
        calls.append(node.id)
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
        if node.id == "mid":
            return node.id, lambda input_lf: input_lf.with_columns(y=pl.col("x") * 2), False
        return node.id, lambda input_lf: input_lf.with_columns(z=pl.col("y") + 1), False

    return build_node


def test_execute_lazy_dataframe_cache_materializes_requested_node_on_first_run(
    tmp_path: Path,
) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    key = _cache_key(graph, input_fingerprint="input:v1")
    calls: list[str] = []

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _build_fn(calls),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key),
    )

    entry = cache.get(key)
    assert entry is not None
    assert entry.path.exists()
    assert entry.row_count == 3
    assert calls == ["source", "target"]
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
    }


def test_facade_cache_request_matches_lazy_execution_policy(tmp_path: Path) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    request = _cache_request_from_facade(
        graph,
        cache,
        input_fingerprint="input:v1",
        required_columns_by_node={"target": frozenset({"x", "y"})},
    )

    calls: list[str] = []
    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _build_fn(calls),
        target_node_id="target",
        source="batch",
        required_columns_by_node={"target": frozenset({"x", "y"})},
        dataframe_cache_request=request,
    )

    key = request.keys_by_node["target"]
    assert cache.get(key) is not None
    assert calls == ["source", "target"]
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
    }


def test_execute_lazy_dataframe_cache_hit_skips_upstream_builders(
    tmp_path: Path,
) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    key = _cache_key(graph, input_fingerprint="input:v1")

    execution.execute_lazy_graph(
        graph,
        _build_fn([]),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key),
    )

    def unexpected_build(node: GraphNode, **_: Any):
        raise AssertionError(f"builder should not run for cached node {node.id!r}")

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        unexpected_build,
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key),
    )

    assert set(outputs) == {"target"}
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
    }


def test_execute_lazy_dataframe_cache_changed_input_fingerprint_misses(
    tmp_path: Path,
) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    original_key = _cache_key(graph, input_fingerprint="input:v1")

    execution.execute_lazy_graph(
        graph,
        _build_fn([]),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, original_key),
    )

    changed_key = _cache_key(graph, input_fingerprint="input:v2")
    calls: list[str] = []
    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _build_fn(calls, source_values=[10, 20]),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, changed_key),
    )

    assert calls == ["source", "target"]
    assert cache.get(original_key) is not None
    assert cache.get(changed_key) is not None
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [10, 20],
        "y": [20, 40],
    }


def test_execute_lazy_dataframe_cache_hit_seeds_intermediate_and_builds_downstream(
    tmp_path: Path,
) -> None:
    graph = _chain_graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    key = _cache_key(
        graph,
        node_id="mid",
        target_node_id="target",
        input_fingerprint="input:v1",
    )

    execution.execute_lazy_graph(
        graph,
        _chain_build_fn([]),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key),
    )

    calls: list[str] = []

    def build_only_downstream(node: GraphNode, **_: Any):
        calls.append(node.id)
        if node.id != "target":
            raise AssertionError(f"upstream builder should be skipped for {node.id!r}")
        return node.id, lambda input_lf: input_lf.with_columns(z=pl.col("y") + 1), False

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        build_only_downstream,
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key),
    )

    assert calls == ["target"]
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
        "z": [3, 5, 7],
    }


def test_execute_lazy_dataframe_cache_rejects_policy_mismatch(tmp_path: Path) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    key = _cache_key(graph, target_node_id="other", input_fingerprint="input:v1")

    with pytest.raises(ValueError, match="execution policy"):
        execution.execute_lazy_graph(
            graph,
            _build_fn([]),
            target_node_id="target",
            source="batch",
            dataframe_cache_request=_cache_request(cache, key),
        )


def test_execute_lazy_dataframe_cache_rejects_narrow_key_for_broader_runtime_demand(
    tmp_path: Path,
) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    narrow_request = _cache_request_from_facade(
        graph,
        cache,
        input_fingerprint="input:v1",
        required_columns_by_node={"target": frozenset({"x"})},
    )

    execution.execute_lazy_graph(
        graph,
        _build_fn([]),
        target_node_id="target",
        source="batch",
        required_columns_by_node={"target": frozenset({"x"})},
        dataframe_cache_request=narrow_request,
    )

    with pytest.raises(ValueError, match="execution policy"):
        execution.execute_lazy_graph(
            graph,
            _build_fn([]),
            target_node_id="target",
            source="batch",
            required_columns_by_node={"target": frozenset({"x", "y"})},
            dataframe_cache_request=narrow_request,
        )


def test_execute_lazy_dataframe_cache_rejects_stale_graph_key(tmp_path: Path) -> None:
    graph = _graph()
    changed_graph = PipelineGraph(
        nodes=[
            _node("source", NodeType.DATA_INPUT),
            _node("target", NodeType.POLARS, {"selected_columns": ["x"]}),
        ],
        edges=[_edge("source", "target")],
    )
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    stale_key = _cache_key(graph, input_fingerprint="input:v1")

    with pytest.raises(ValueError, match="current lazy execution graph"):
        execution.execute_lazy_graph(
            changed_graph,
            _build_fn([]),
            target_node_id="target",
            source="batch",
            dataframe_cache_request=_cache_request(cache, stale_key),
        )


def test_execute_lazy_dataframe_cache_can_warm_broader_required_columns(
    tmp_path: Path,
) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    cache_request = _cache_request_from_facade(
        graph,
        cache,
        input_fingerprint="input:v1",
        required_columns_by_node={"target": frozenset({"x", "y"})},
    )
    first_calls: list[str] = []

    execution.execute_lazy_graph(
        graph,
        _build_fn(first_calls),
        target_node_id="target",
        source="batch",
        required_columns_by_node={"target": frozenset({"x"})},
        dataframe_cache_request=cache_request,
    )

    key = cache_request.keys_by_node["target"]
    assert cache.get(key) is not None
    assert first_calls == ["source", "target"]

    def unexpected_build(node: GraphNode, **_: Any):
        raise AssertionError(f"builder should not run for cached node {node.id!r}")

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        unexpected_build,
        target_node_id="target",
        source="batch",
        required_columns_by_node={"target": frozenset({"x", "y"})},
        dataframe_cache_request=cache_request,
    )

    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
    }


def test_execute_lazy_dataframe_cache_skips_broader_key_when_columns_missing(
    tmp_path: Path,
) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    cache_request = _cache_request_from_facade(
        graph,
        cache,
        input_fingerprint="input:v1",
        required_columns_by_node={"target": frozenset({"missing_solver_column", "x"})},
    )
    calls: list[str] = []

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _build_fn(calls),
        target_node_id="target",
        source="batch",
        required_columns_by_node={"target": frozenset({"x"})},
        dataframe_cache_request=cache_request,
    )

    key = cache_request.keys_by_node["target"]
    assert calls == ["source", "target"]
    assert cache.get(key) is None
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
    }


def test_execute_lazy_dataframe_cache_oversized_artifact_skips_cache_write(
    tmp_path: Path,
) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(root=tmp_path, max_entries=4, max_bytes=1)
    key = _cache_key(graph, input_fingerprint="input:v1")
    calls: list[str] = []

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _build_fn(calls),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key),
    )

    assert calls == ["source", "target"]
    assert cache.get(key) is None
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
    }


def test_execute_lazy_dataframe_cache_miss_propagates_builder_failure(
    tmp_path: Path,
) -> None:
    graph = _graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    key = _cache_key(graph, input_fingerprint="input:v1")

    with pytest.raises(RuntimeError, match="source builder exploded"):
        execution.execute_lazy_graph(
            graph,
            _failing_source_build_fn,
            target_node_id="target",
            source="batch",
            dataframe_cache_request=_cache_request(cache, key),
        )

    assert cache.get(key) is None


def test_execute_lazy_dataframe_cache_missing_artifact_degrades_to_miss(
    tmp_path: Path,
) -> None:
    """If the cache artifact is externally deleted between calls, the
    next execution must fall through to a miss with a warning log, not
    crash the whole pipeline."""

    graph = _chain_graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    key = _cache_key(
        graph,
        node_id="mid",
        target_node_id="target",
        input_fingerprint="input:v1",
    )

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _chain_build_fn([]),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key),
    )
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
        "z": [3, 5, 7],
    }
    del outputs

    entry = cache.get(key)
    assert entry is not None
    entry.path.unlink()  # simulate external cleanup of the temp artifact

    rebuild_calls: list[str] = []
    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _chain_build_fn(rebuild_calls),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key),
    )

    # The full graph must be rebuilt: cache lookup did not blow up.
    assert "source" in rebuild_calls
    assert "mid" in rebuild_calls
    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
        "z": [3, 5, 7],
    }


def _diamond_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node("source", NodeType.DATA_INPUT),
            _node("left", NodeType.POLARS),
            _node("right", NodeType.POLARS),
            _node("sink", NodeType.POLARS),
        ],
        edges=[
            _edge("source", "left"),
            _edge("source", "right"),
            _edge("left", "sink"),
            _edge("right", "sink"),
        ],
    )


def test_execute_lazy_dataframe_cache_diamond_partial_cache_rebuilds_other_branch(
    tmp_path: Path,
) -> None:
    """In a diamond, caching one branch must not stop the other branch
    from rebuilding, and the source must still feed the uncached branch."""

    graph = _diamond_graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    key_left = _cache_key(
        graph,
        node_id="left",
        target_node_id="sink",
        input_fingerprint="input:v1",
    )

    def build(node: GraphNode, **_: Any):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
        if node.id == "left":
            return node.id, lambda input_lf: input_lf.with_columns(left=pl.col("x") * 10), False
        if node.id == "right":
            return node.id, lambda input_lf: input_lf.with_columns(right=pl.col("x") + 100), False
        return (
            node.id,
            lambda left_lf, right_lf: left_lf.join(
                right_lf.select("x", "right"), on="x", how="inner"
            ),
            False,
        )

    # Warm just the left branch.
    execution.execute_lazy_graph(
        graph,
        build,
        target_node_id="sink",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key_left),
    )
    assert cache.get(key_left) is not None

    calls: list[str] = []

    def build_tracked(node: GraphNode, **_: Any):
        calls.append(node.id)
        return build(node)

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        build_tracked,
        target_node_id="sink",
        source="batch",
        dataframe_cache_request=_cache_request(cache, key_left),
    )

    # Source feeds the uncached "right" branch and must rebuild; "left"
    # is seeded from cache; "right" and "sink" rebuild.
    assert "left" not in calls
    assert "right" in calls
    assert "sink" in calls
    assert "source" in calls
    result = outputs["sink"].collect().sort("x").to_dict(as_series=False)
    assert result["x"] == [1, 2, 3]
    assert result["left"] == [10, 20, 30]
    assert result["right"] == [101, 102, 103]


def test_execute_lazy_dataframe_cache_preserved_node_built_when_covered(
    tmp_path: Path,
) -> None:
    """A preserved node must be built even when the cache fully covers
    its downstream — preservation overrides skip-cache-covered."""

    graph = _chain_graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    # Preserve set is part of the policy fingerprint, so the warming run
    # must use the same value as the test run.
    policy = execution.dataframe_lazy_execution_policy(
        target_node_id="target",
        source_by_node=None,
        required_columns_by_node=None,
        preserve_node_ids={"source"},
        enforce_contracts=False,
        preamble_ns_supplied=False,
    )
    key = execution.dataframe_execution_cache_key(
        graph,
        node_id="mid",
        namespace="execute-lazy-test",
        source="batch",
        profile=ExecutionProfile.LAZY_SINK,
        input_fingerprint="input:v1",
        execution_policy=policy,
    )

    # Warm the cache for "mid" with the same preserve_node_ids.
    execution.execute_lazy_graph(
        graph,
        _chain_build_fn([]),
        target_node_id="target",
        source="batch",
        preserve_node_ids={"source"},
        dataframe_cache_request=_cache_request(cache, key),
    )

    calls: list[str] = []
    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _chain_build_fn(calls),
        target_node_id="target",
        source="batch",
        preserve_node_ids={"source"},
        dataframe_cache_request=_cache_request(cache, key),
    )

    # "mid" is seeded from cache; "source" is preserved so it must build;
    # "target" builds because it's the new downstream.
    assert "source" in calls
    assert "mid" not in calls
    assert "target" in calls
    assert "source" in outputs


def test_execute_lazy_dataframe_cache_graph_config_change_invalidates(
    tmp_path: Path,
) -> None:
    """Editing upstream node config produces a different cache key."""

    graph_a = _chain_graph()
    graph_b = PipelineGraph(
        nodes=[
            _node(
                "source",
                NodeType.DATA_INPUT,
                config={
                    "inputType": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": "changed.parquet",
                    "arguments": {},
                },
            ),
            _node("mid", NodeType.POLARS),
            _node("target", NodeType.POLARS),
        ],
        edges=[_edge("source", "mid"), _edge("mid", "target")],
    )

    key_a = _cache_key(graph_a, input_fingerprint="input:v1")
    key_b = _cache_key(graph_b, input_fingerprint="input:v1")
    assert key_a.cache_key != key_b.cache_key
    assert key_a.lineage_fingerprint != key_b.lineage_fingerprint


def test_execute_lazy_dataframe_cache_preamble_change_invalidates(
    tmp_path: Path,
) -> None:
    """A preamble edit must produce a different cache key."""

    graph_a = _chain_graph()
    graph_b = PipelineGraph(
        nodes=list(graph_a.nodes),
        edges=list(graph_a.edges),
        preamble="x = 1",
    )

    key_a = _cache_key(graph_a, input_fingerprint="input:v1")
    key_b = _cache_key(graph_b, input_fingerprint="input:v1")
    assert key_a.cache_key != key_b.cache_key


def test_execute_lazy_dataframe_cache_byte_pressure_at_second_store_does_not_fail_run(
    tmp_path: Path,
) -> None:
    """W2.10: a run that materializes two cached nodes must survive byte
    pressure at the second store.

    The first node's artifact stays pinned by the live scan held in
    ``lazy_outputs`` for the rest of the run, so pre-fix the second
    store's eviction pass had no unpinned candidate except the artifact
    it had just written: it evicted that fresh artifact, unlinked the
    parquet, and the run failed hard with ``DataFrameExecutionCacheError``
    ("vanished immediately") instead of completing.
    """
    import gc

    graph = _chain_graph()
    key_mid = _cache_key(
        graph,
        node_id="mid",
        target_node_id="target",
        input_fingerprint="input:v1",
    )
    key_target = _cache_key(graph, input_fingerprint="input:v1")

    def request_for(
        cache: execution.DataFrameExecutionCache,
    ) -> execution.DataFrameExecutionCacheRequest:
        return execution.DataFrameExecutionCacheRequest(
            cache=cache,
            keys_by_node={"mid": key_mid, "target": key_target},
        )

    # Measure both artifact sizes with an unbounded warm-up cache so the
    # pressured budget admits each artifact but not the two together.
    warm = execution.DataFrameExecutionCache(
        root=tmp_path / "warm",
        max_entries=4,
        max_bytes=10_000_000,
    )
    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _chain_build_fn([]),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=request_for(warm),
    )
    warm_mid = warm.get(key_mid)
    warm_target = warm.get(key_target)
    assert warm_mid is not None
    assert warm_target is not None
    max_bytes = warm_mid.size_bytes + warm_target.size_bytes - 1
    del outputs
    gc.collect()
    warm.clear()

    pressured = execution.DataFrameExecutionCache(
        root=tmp_path / "pressured",
        max_entries=4,
        max_bytes=max_bytes,
    )

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _chain_build_fn([]),
        target_node_id="target",
        source="batch",
        dataframe_cache_request=request_for(pressured),
    )

    assert outputs["target"].collect().to_dict(as_series=False) == {
        "x": [1, 2, 3],
        "y": [2, 4, 6],
        "z": [3, 5, 7],
    }
    # The artifact stored under pressure is resident and readable.
    assert pressured.get(key_target) is not None


def test_execute_lazy_dataframe_cache_write_projects_to_required_columns(
    tmp_path: Path,
) -> None:
    """Cache writes must be projected to required_columns so the on-disk
    artifact does not store columns the cache key never declared."""

    graph = _chain_graph()
    cache = execution.DataFrameExecutionCache(
        root=tmp_path,
        max_entries=4,
        max_bytes=10_000_000,
    )
    request = _cache_request_from_facade(
        graph,
        cache,
        input_fingerprint="input:v1",
        node_id="target",
        required_columns_by_node={"target": frozenset({"y"})},
    )

    outputs, *_ = execution.execute_lazy_graph(
        graph,
        _chain_build_fn([]),
        target_node_id="target",
        source="batch",
        required_columns_by_node={"target": frozenset({"y"})},
        dataframe_cache_request=request,
    )
    del outputs

    entry = cache.get(request.keys_by_node["target"])
    assert entry is not None
    assert set(entry.columns) == {"y"}
