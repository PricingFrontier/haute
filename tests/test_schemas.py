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
    OptimiserEstimateRequest,
    OptimiserFrontierAutoRangeRequest,
    OptimiserFrontierRequest,
    OptimiserSolveRequest,
    PreviewNodeRequest,
    SavePipelineRequest,
    TraceRequest,
    TrainRequest,
    WriteOutputRequest,
)


class TestValidation:
    """Required fields raise ValidationError when missing."""

    def test_graph_edge_requires_fields(self):
        with pytest.raises(ValidationError):
            GraphEdge()

    def test_sink_request_requires_node_id(self):
        with pytest.raises(ValidationError):
            WriteOutputRequest(graph=Graph())

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
                        nodeType="dataInput",
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


_SCHEMA_CASES_WITH_NODE_ID = [
    PreviewNodeRequest,
    TraceRequest,
    WriteOutputRequest,
    TrainRequest,
    OptimiserSolveRequest,
    OptimiserEstimateRequest,
    OptimiserFrontierAutoRangeRequest,
    OptimiserFrontierRequest,
]


def _kwargs_for(schema_cls: type) -> dict:
    if schema_cls is TraceRequest:
        return {"graph": Graph()}
    if schema_cls is OptimiserFrontierRequest:
        return {"job_id": "j"}
    return {"graph": Graph(), "node_id": "n"}


class TestStreamingChunkSizeField:
    """The optional ``streaming_chunk_size`` field on request schemas."""

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    def test_default_is_none(self, schema_cls):
        r = schema_cls(**_kwargs_for(schema_cls))
        assert r.streaming_chunk_size is None

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    def test_accepts_positive_int(self, schema_cls):
        r = schema_cls(**_kwargs_for(schema_cls), streaming_chunk_size=12345)
        assert r.streaming_chunk_size == 12345

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    def test_accepts_lower_boundary(self, schema_cls):
        r = schema_cls(**_kwargs_for(schema_cls), streaming_chunk_size=1)
        assert r.streaming_chunk_size == 1

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    def test_accepts_upper_boundary(self, schema_cls):
        r = schema_cls(**_kwargs_for(schema_cls), streaming_chunk_size=10_000_000)
        assert r.streaming_chunk_size == 10_000_000

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    def test_rejects_zero(self, schema_cls):
        with pytest.raises(ValidationError):
            schema_cls(**_kwargs_for(schema_cls), streaming_chunk_size=0)

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    def test_rejects_negative(self, schema_cls):
        with pytest.raises(ValidationError):
            schema_cls(**_kwargs_for(schema_cls), streaming_chunk_size=-1)

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    def test_rejects_above_upper_boundary(self, schema_cls):
        with pytest.raises(ValidationError):
            schema_cls(**_kwargs_for(schema_cls), streaming_chunk_size=10_000_001)

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    def test_rejects_non_int(self, schema_cls):
        with pytest.raises(ValidationError):
            schema_cls(**_kwargs_for(schema_cls), streaming_chunk_size="big")

    @pytest.mark.parametrize("schema_cls", _SCHEMA_CASES_WITH_NODE_ID)
    @pytest.mark.parametrize("bool_value", [True, False])
    def test_rejects_bool(self, schema_cls, bool_value):
        with pytest.raises(ValidationError):
            schema_cls(**_kwargs_for(schema_cls), streaming_chunk_size=bool_value)


class TestAssistantMessageRequest:
    def test_accepts_session_and_message_only(self):
        from haute.schemas import AssistantMessageRequest

        request = AssistantMessageRequest(
            session_id="session-1",
            message="Author this pipeline",
        )
        assert request.session_id == "session-1"
        assert request.message == "Author this pipeline"

    @pytest.mark.parametrize(
        "payload",
        [
            {"session_id": "s", "message": "m", "unknown": True},
            {"session_id": "s", "message": "m", "confirmation": {"plan_hash": "a" * 64}},
        ],
    )
    def test_request_is_closed(self, payload):
        from haute.schemas import AssistantMessageRequest

        with pytest.raises(ValidationError):
            AssistantMessageRequest.model_validate(payload)


class TestAssistantStatus:
    def test_configured_status_requires_complete_egress_identity(self):
        from haute.schemas import AssistantStatusResponse

        with pytest.raises(ValidationError):
            AssistantStatusResponse(
                configured=True,
                reason=None,
                provider="openai",
                model="m",
                endpoint_host=None,
                trust=None,
                max_sensitivity=None,
                mutations_enabled=True,
                mutations_reason=None,
            )
