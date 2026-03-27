"""Tests for haute.schemas — Pydantic model validation.

Focuses on: required-field validation, nested structure, roundtrip dump.
Pure default-value assertions removed (Pydantic guarantees those).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from haute.schemas import (
    FileItem,
    Graph,
    GraphEdge,
    GraphNode,
    GraphNodeData,
    PreviewNodeRequest,
    SavePipelineRequest,
    SinkRequest,
    TraceRequest,
)


class TestValidation:
    """Required fields raise ValidationError when missing."""

    def test_graph_edge_requires_fields(self):
        with pytest.raises(ValidationError):
            GraphEdge()

    def test_sink_request_requires_node_id(self):
        with pytest.raises(ValidationError):
            SinkRequest(graph=Graph())

    def test_save_pipeline_accepts_minimal(self):
        r = SavePipelineRequest(graph=Graph())
        assert r.name == "main"

    def test_preview_node_requires_node_id(self):
        with pytest.raises(ValidationError):
            PreviewNodeRequest(graph=Graph())

    def test_trace_request_accepts_minimal(self):
        r = TraceRequest(graph=Graph())
        assert r.row_index == 0


class TestCompositeStructure:
    """Nested models compose correctly."""

    def test_graph_with_nodes_and_edges(self):
        g = Graph(
            nodes=[GraphNode(id="a"), GraphNode(id="b")],
            edges=[GraphEdge(id="e1", source="a", target="b")],
            pipeline_name="test",
        )
        assert len(g.nodes) == 2
        assert g.edges[0].target == "b"

    def test_file_item_optional_size(self):
        f_file = FileItem(name="data.parquet", path="data.parquet", type="file", size=1024)
        f_dir = FileItem(name="subdir", path="subdir", type="directory")
        assert f_file.size == 1024
        assert f_dir.size is None


class TestModelDumpRoundtrip:
    """model_dump() produces dicts that match the schema structure."""

    def test_graph_dump_preserves_config(self):
        g = Graph(
            nodes=[
                GraphNode(
                    id="src",
                    data=GraphNodeData(
                        label="Source",
                        nodeType="dataSource",
                        config={"path": "d.parquet"},
                    ),
                ),
            ],
            edges=[],
        )
        d = g.model_dump()
        assert d["nodes"][0]["id"] == "src"
        assert d["nodes"][0]["data"]["config"]["path"] == "d.parquet"
