"""Opt-in CACHE-S17 evidence: what a preview's planning costs as its lineage grows.

A resolution signs every node of the lineage. Its work per node must stay flat:
the graph is canonicalised once, and each runtime input's entry is read once,
whatever the lineage's length. The counts are asserted; the times are evidence.
"""

from __future__ import annotations

import platform
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import haute.execution as execution
import haute.executor as executor
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from tests._project import use_project

pytestmark = pytest.mark.perf

_SAMPLES = 3
_INPUTS = ("quotes", "claims")


def _graph(project: Path, length: int) -> PipelineGraph:
    """Two Parquet inputs joined, then *length* row-local transforms."""

    def data_input(node_id: str, file: str) -> GraphNode:
        return GraphNode(
            id=node_id,
            data=NodeData(
                label=node_id,
                nodeType=NodeType.DATA_INPUT,
                config={"inputType": "file", "format": "parquet", "mode": "scan", "path": file},
            ),
        )

    nodes = [
        data_input("quotes", "quotes.parquet"),
        data_input("claims", "claims.parquet"),
        GraphNode(
            id="joined",
            data=NodeData(
                label="joined",
                nodeType=NodeType.POLARS,
                # One claim per id, declared so admission can estimate the join
                # on a runner with no hard memory cap.
                config={"code": "df = quotes.join(claims, on='id', how='left', validate='m:1')"},
            ),
        ),
    ]
    edges = [
        GraphEdge(id="quotes-joined", source="quotes", target="joined"),
        GraphEdge(id="claims-joined", source="claims", target="joined"),
    ]
    previous = "joined"
    for index in range(length):
        node_id = f"step{index}"
        nodes.append(
            GraphNode(
                id=node_id,
                data=NodeData(
                    label=node_id,
                    nodeType=NodeType.POLARS,
                    config={"code": f"df = {previous}.with_columns(a=pl.col('a') + {index})"},
                ),
            )
        )
        edges.append(GraphEdge(id=f"{previous}-{node_id}", source=previous, target=node_id))
        previous = node_id
    return PipelineGraph(
        nodes=nodes,
        edges=edges,
        preamble="import polars as pl",
        # Its own pipeline file, so no other length's capture seeds it.
        source_file=str(project / f"main_{length}.py"),
    )


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    use_project(haute_scratch)
    rows = 10_000
    pl.DataFrame({"id": range(rows), "a": range(rows)}).write_parquet(
        haute_scratch / "quotes.parquet"
    )
    pl.DataFrame({"id": range(rows), "d": [0.5] * rows}).write_parquet(
        haute_scratch / "claims.parquet"
    )
    return haute_scratch


def test_preview_planning_reads_inputs_a_fixed_number_of_times_however_long_the_lineage(
    request: pytest.FixtureRequest,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts: Counter[str] = Counter()
    canonicalise = execution._canonical_dataframe_execution_graph
    read_entry = execution._runtime_input_fingerprint_entry

    def counted_canonical(graph: PipelineGraph) -> PipelineGraph:
        counts["canonical"] += 1
        return canonicalise(graph)

    def counted_entry(graph: PipelineGraph, node: GraphNode) -> Any:
        counts["entries"] += 1
        return read_entry(graph, node)

    monkeypatch.setattr(execution, "_canonical_dataframe_execution_graph", counted_canonical)
    monkeypatch.setattr(execution, "_runtime_input_fingerprint_entry", counted_entry)

    def preview(graph: PipelineGraph, target: str) -> float:
        executor._preview_cache.clear()
        counts.clear()
        started = time.perf_counter()
        result = executor.execute_graph(
            graph,
            target_node_id=target,
            row_limit=100,
            target_preview_only=True,
            shared_snapshots=True,
        )
        elapsed = time.perf_counter() - started
        assert result[target].status == "ok"
        return elapsed

    evidence: list[dict[str, object]] = []
    for length in (10, 40, 80):
        (project / f"main_{length}.py").write_text("# pipeline\n", encoding="utf-8")
        graph = _graph(project, length)
        target = f"step{length - 1}"
        # The first preview captures the join and resolves again after it
        # ran; the rest seed from that capture.
        cold = preview(graph, target)
        cold_counts = dict(counts)
        warm = [preview(graph, target) for _ in range(_SAMPLES)]
        evidence.append(
            {
                "lineage_nodes": length + len(_INPUTS) + 1,
                "cold_preview_seconds": round(cold, 4),
                "warm_preview_median_seconds": round(statistics.median(warm), 4),
                "cold_counts": cold_counts,
                "warm_counts": dict(counts),
            }
        )

    # Signing every node reads the graph's inputs no more often for a long
    # lineage than for a short one: each resolution canonicalises its graph
    # once and reads each input's entry once.
    assert len({(str(item["cold_counts"]), str(item["warm_counts"])) for item in evidence}) == 1
    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "polars": pl.__version__,
                },
                "inputs": len(_INPUTS),
                "previews": evidence,
            },
        )
    )
