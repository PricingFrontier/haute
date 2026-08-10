"""Opt-in CACHE-PERF-01 evidence; measurements are observational, not budgets."""

from __future__ import annotations

import platform
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from haute._cache import LineageCacheKeyRequest, lineage_cache_key, selected_live_switch_path
from haute._lru_cache import LRUCache
from haute._stat_gated_cache import StatGatedCache
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import dataframe_frame_input_fingerprint
from haute.projection import prepare_graph

pytestmark = pytest.mark.perf

_WARMUPS = 2
_SAMPLES = 7


def _median_ns(operation: Callable[[], object], *, iterations: int = 1) -> int:
    for _ in range(_WARMUPS):
        for _ in range(iterations):
            operation()
    samples = []
    for _ in range(_SAMPLES):
        started = time.perf_counter_ns()
        for _ in range(iterations):
            operation()
        samples.append((time.perf_counter_ns() - started) // iterations)
    return int(statistics.median(samples))


def _record(request: pytest.FixtureRequest, **evidence: object) -> None:
    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "polars": pl.__version__,
                },
                "materiality_threshold": 0.20,
                "report_artifact": ".cache/perf/perf-report.json",
                **evidence,
            },
        ),
    )


def test_row_hash_bytes_encoding_evidence(request: pytest.FixtureRequest) -> None:
    """Compare only encodings after Polars has already calculated UInt64 hashes."""
    rows = 100_000
    frame = pl.DataFrame(
        {
            "id": range(rows),
            "amount": [float(index % 997) / 10 for index in range(rows)],
            "group": [f"group-{index % 17}" for index in range(rows)],
            "active": [index % 3 != 0 for index in range(rows)],
        },
    )
    hashes = frame.hash_rows(seed=0)
    expected = hashes.to_list()

    def decimal() -> bytes:
        return ",".join(str(value) for value in expected).encode()

    def contiguous() -> bytes:
        return hashes.to_numpy().astype("<u8", copy=False).tobytes(order="C")

    assert list(np.frombuffer(contiguous(), dtype="<u8")) == expected
    production = dataframe_frame_input_fingerprint(frame)
    assert production["row_hash_encoding"] == "polars-u64-le:v1"
    decimal_ns = _median_ns(decimal)
    contiguous_ns = _median_ns(contiguous)
    improvement = 1.0 - (contiguous_ns / decimal_ns)
    assert improvement >= 0.20
    _record(
        request,
        workload=(
            "100k-row mixed Polars DataFrame: encode already-computed hash_rows(seed=0) UInt64s"
        ),
        artifact_paths=["src/haute/execution.py:dataframe_frame_input_fingerprint"],
        measured_medians_ns={"decimal_csv": decimal_ns, "contiguous_uint64_bytes": contiguous_ns},
        speedup=decimal_ns / contiguous_ns if contiguous_ns else None,
        improvement_fraction=improvement,
        decision="implemented",
        decision_reason="The canonical little-endian UInt64 buffer clears the 20% median gate.",
    )


def test_small_lru_and_stat_gated_cache_evidence(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    cache: LRUCache[str, int] = LRUCache(max_size=2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("b") is None and cache.get("a") == 1 and len(cache) == 2
    lru_ns = _median_ns(lambda: (cache.get("a"), cache.put("a", 1)), iterations=200)

    path = tmp_path / "unchanged.artifact"
    path.write_text("stable")
    stat_cache: StatGatedCache[str, str] = StatGatedCache(
        artifact_kind="perf",
        max_entries=8,
    )
    loads = 0

    def loader() -> str:
        nonlocal loads
        loads += 1
        return path.read_text()

    assert stat_cache.get_or_load("artifact", str(path), loader) == "stable"
    assert stat_cache.get_or_load("artifact", str(path), loader) == "stable"
    assert loads == 1
    stat_hit_ns = _median_ns(
        lambda: stat_cache.get_or_load("artifact", str(path), loader), iterations=100
    )
    assert loads == 1
    _record(
        request,
        workload="bounded LRU operations and unchanged-file StatGatedCache hits",
        artifact_paths=["src/haute/_lru_cache.py", "src/haute/_stat_gated_cache.py"],
        measured_medians_ns={
            "lru_hit_insert_eviction_ns_per_op": lru_ns,
            "stat_hit_ns_per_op": stat_hit_ns,
        },
        loader_calls=loads,
        decision="no_change",
        decision_reason=(
            "No semantics-preserving comparative candidate demonstrated a 20% improvement; "
            "retention is bounded independently of this hot-path decision."
        ),
    )


def _lineage_graph() -> PipelineGraph:
    nodes = [
        GraphNode(
            id="n0",
            data=NodeData(
                label="n0", nodeType=NodeType.DATA_INPUT, config={"inputType": "dataframe"}
            ),
        )
    ]
    nodes.extend(
        GraphNode(
            id=f"n{index}",
            data=NodeData(
                label=f"n{index}",
                nodeType=NodeType.POLARS,
                config={"code": (f"df = n{index - 1}.with_columns(c{index}=pl.lit({index}))")},
            ),
        )
        for index in range(1, 100)
    )
    return PipelineGraph(
        nodes=nodes,
        edges=[
            GraphEdge(id=f"e{index}", source=f"n{index}", target=f"n{index + 1}")
            for index in range(99)
        ],
        preamble="import polars as pl",
        source_file="performance/cache_identity.py",
    )


def _lineage_request(graph: PipelineGraph) -> LineageCacheKeyRequest:
    prepared = prepare_graph(graph, "n99", source="live")
    return LineageCacheKeyRequest(
        graph=graph,
        prepared=prepared,
        target_node_id="n99",
        source="live",
        requested_columns=("c99",),
        initial_column_limit=None,
        row_limit=100,
        port_label=None,
        contract_fingerprint="contract:v1",
        selected_live_switch_path=selected_live_switch_path(prepared),
        runtime_input_fingerprint="runtime:v1",
        execution_semantics_version="preview:v1",
    )


def test_canonical_lineage_identity_evidence(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _lineage_graph()
    identity_request = _lineage_request(graph)
    baseline = lineage_cache_key(identity_request)
    assert lineage_cache_key(identity_request) == baseline
    changed_nodes = list(graph.nodes)
    changed_nodes[50] = changed_nodes[50].model_copy(
        update={
            "data": changed_nodes[50].data.model_copy(
                update={"config": {"code": "df = n49.with_columns(c50=pl.lit(500))"}}
            )
        }
    )
    assert (
        lineage_cache_key(_lineage_request(graph.model_copy(update={"nodes": changed_nodes})))
        != baseline
    )

    import haute._cache as cache_module

    original = cache_module.canonical_json
    canonical_elapsed_ns = 0

    def timed_canonical(value: Any) -> str:
        nonlocal canonical_elapsed_ns
        started = time.perf_counter_ns()
        try:
            return original(value)
        finally:
            canonical_elapsed_ns += time.perf_counter_ns() - started

    monkeypatch.setattr(cache_module, "canonical_json", timed_canonical)
    total_samples: list[int] = []
    canonical_samples: list[int] = []
    for sample_index in range(_WARMUPS + _SAMPLES):
        canonical_elapsed_ns = 0
        started = time.perf_counter_ns()
        for _ in range(10):
            lineage_cache_key(identity_request)
        total_elapsed_ns = time.perf_counter_ns() - started
        if sample_index >= _WARMUPS:
            total_samples.append(total_elapsed_ns // 10)
            canonical_samples.append(canonical_elapsed_ns // 10)
    total_ns = int(statistics.median(total_samples))
    canonical_ns = int(statistics.median(canonical_samples))
    assert canonical_ns <= total_ns
    _record(
        request,
        workload="100-node linear PipelineGraph repeated preview lineage cache-key construction",
        artifact_paths=[
            "src/haute/_cache.py:lineage_cache_key",
            "src/haute/_cache.py:canonical_json",
        ],
        measured_medians_ns={
            "lineage_key_total": total_ns,
            "canonical_serialization": canonical_ns,
        },
        canonical_share=canonical_ns / total_ns if total_ns else None,
        decision="no_change",
        decision_reason=(
            "Serialization share alone does not demonstrate a safe 20% end-to-end improvement; "
            "a cross-request lookup must first identify changed graph/runtime inputs and would "
            "add retention and invalidation state."
        ),
    )
