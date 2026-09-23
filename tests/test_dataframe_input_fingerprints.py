"""Runtime-input fingerprints: graph inputs and in-memory frames."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from haute._hashing import content_hash_bytes
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import dataframe_frame_input_fingerprint, dataframe_graph_input_fingerprint

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
