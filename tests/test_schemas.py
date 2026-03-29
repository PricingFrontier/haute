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


class TestPreviewNodeRequestBoundaries:
    def test_row_limit_zero_fails(self):
        with pytest.raises(ValidationError):
            PreviewNodeRequest(graph=Graph(), node_id="n", row_limit=0)

    def test_row_limit_above_max_fails(self):
        with pytest.raises(ValidationError):
            PreviewNodeRequest(graph=Graph(), node_id="n", row_limit=10001)

    def test_row_limit_min_boundary(self):
        r = PreviewNodeRequest(graph=Graph(), node_id="n", row_limit=1)
        assert r.row_limit == 1

    def test_row_limit_max_boundary(self):
        r = PreviewNodeRequest(graph=Graph(), node_id="n", row_limit=10000)
        assert r.row_limit == 10000


class TestTraceRequestBoundaries:
    def test_row_index_negative_fails(self):
        with pytest.raises(ValidationError):
            TraceRequest(graph=Graph(), row_index=-1)

    def test_row_index_zero_succeeds(self):
        r = TraceRequest(graph=Graph(), row_index=0)
        assert r.row_index == 0

    def test_row_limit_zero_fails(self):
        with pytest.raises(ValidationError):
            TraceRequest(graph=Graph(), row_limit=0)

    def test_row_limit_above_max_fails(self):
        with pytest.raises(ValidationError):
            TraceRequest(graph=Graph(), row_limit=10001)

    def test_row_limit_min_boundary(self):
        r = TraceRequest(graph=Graph(), row_limit=1)
        assert r.row_limit == 1


class TestSavePipelineRequestDefaults:
    def test_name_defaults_to_main(self):
        r = SavePipelineRequest()
        assert r.name == "main"

    def test_graph_defaults_to_empty(self):
        r = SavePipelineRequest()
        assert isinstance(r.graph, Graph)
        assert r.graph.nodes == []
        assert r.graph.edges == []

    def test_explicit_name_overrides_default(self):
        r = SavePipelineRequest(name="custom")
        assert r.name == "custom"
