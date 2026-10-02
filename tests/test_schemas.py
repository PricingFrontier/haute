"""Tests for haute.schemas — Pydantic model validation.

Focuses on: required-field validation, nested structure, roundtrip dump.
Pure default-value assertions removed (Pydantic guarantees those).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from pydantic import ValidationError

from haute.schemas import (
    ExecutionCacheProofPayload,
    FileItem,
    Graph,
    GraphEdge,
    GraphNode,
    GraphNodeData,
    PipelineSettingsValues,
    PreviewNodeRequest,
    PreviewSeedPlanEntry,
    SavePipelineRequest,
    TraceRequest,
    WriteOutputRequest,
)

_LIST_UNBUILT_MODELS = textwrap.dedent(
    """
    import haute.server
    from pydantic import BaseModel

    def models(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from models(sub)

    for model in set(models(BaseModel)):
        if model.__module__.startswith("haute") and not model.__pydantic_complete__:
            print(f"{model.__module__}.{model.__qualname__}")
    """
)


def test_every_haute_model_is_built_at_import() -> None:
    # A model left for pydantic to build lazily is completed by the first request
    # that uses it, and two request threads doing so at once can leave it without
    # a validator. A fresh interpreter, because any earlier use in this process
    # would already have built the model.
    result = subprocess.run(
        [sys.executable, "-c", _LIST_UNBUILT_MODELS],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.split() == []


@pytest.mark.parametrize("created_at", ["2026-09-22T12:00:00", "2026-09-22T12:00:00+01:00"])
def test_preview_seed_plan_rejects_timestamps_without_utc_offset(created_at: str) -> None:
    with pytest.raises(ValidationError, match="created_at must include a UTC offset"):
        PreviewSeedPlanEntry(
            node_id="join",
            node_label="Join",
            identity_digest="a" * 64,
            generation_id="generation",
            columns=None,
            created_at=created_at,
            kind="seeded",
        )


def test_execution_cache_proof_rejects_an_incoherent_miss_total() -> None:
    with pytest.raises(ValidationError, match="closed reason-count total"):
        ExecutionCacheProofPayload.model_validate(
            {
                "hits": 0,
                "misses": 1,
                "direct_fallbacks": 0,
                "miss_reason_counts": {
                    "metadata_source_mismatch": 0,
                    "artifact_integrity_schema_failure": 0,
                    "unreadable_artifact": 0,
                    "proof_unavailable": 0,
                },
            }
        )


def test_execution_cache_proof_requires_every_counter() -> None:
    with pytest.raises(ValidationError):
        ExecutionCacheProofPayload.model_validate({"misses": 0})
    with pytest.raises(ValidationError):
        ExecutionCacheProofPayload.model_validate(
            {
                "hits": 0,
                "misses": 0,
                "direct_fallbacks": 0,
                "miss_reason_counts": {"proof_unavailable": 0},
            }
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
        r = SavePipelineRequest(graph=Graph(), base_revision=None)
        assert r.name == "main"

    def test_preview_node_requires_node_id(self):
        with pytest.raises(ValidationError):
            PreviewNodeRequest(graph=Graph())

    def test_trace_request_accepts_minimal(self):
        r = TraceRequest(seed_plan=[], graph=Graph())
        assert r.row_index == 0

    def test_trace_request_requires_its_previews_seed_plan(self):
        # A trace names the generations its preview read, even when none.
        with pytest.raises(ValidationError, match="seed_plan"):
            TraceRequest(graph=Graph())


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
            TraceRequest(seed_plan=[], graph=Graph(), row_index=-1)

    def test_row_index_zero_succeeds(self):
        r = TraceRequest(seed_plan=[], graph=Graph(), row_index=0)
        assert r.row_index == 0

    def test_row_limit_zero_fails(self):
        with pytest.raises(ValidationError):
            TraceRequest(seed_plan=[], graph=Graph(), row_limit=0)

    def test_row_limit_above_max_fails(self):
        with pytest.raises(ValidationError):
            TraceRequest(seed_plan=[], graph=Graph(), row_limit=10001)

    def test_row_limit_min_boundary(self):
        r = TraceRequest(seed_plan=[], graph=Graph(), row_limit=1)
        assert r.row_limit == 1


class TestSavePipelineRequestDefaults:
    def test_name_defaults_to_main(self):
        r = SavePipelineRequest(base_revision=None)
        assert r.name == "main"

    def test_graph_defaults_to_empty(self):
        r = SavePipelineRequest(base_revision=None)
        assert isinstance(r.graph, Graph)
        assert r.graph.nodes == []
        assert r.graph.edges == []

    def test_explicit_name_overrides_default(self):
        r = SavePipelineRequest(name="custom", base_revision=None)
        assert r.name == "custom"

    def test_omitting_base_revision_raises_validation_error(self):
        with pytest.raises(ValidationError):
            SavePipelineRequest()


class TestPipelineSettingsValues:
    """The PATCH body of ``/api/pipeline-settings``: the file's keys, each optional."""

    def test_every_key_is_optional_and_null_is_distinct_from_absent(self):
        assert PipelineSettingsValues().model_dump(exclude_unset=True) == {}
        body = PipelineSettingsValues.model_validate({"preview_memory_gb": None})
        assert body.model_dump(exclude_unset=True) == {"preview_memory_gb": None}

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("chunk_rows", 1),
            ("chunk_rows", 10_000_000),
            ("caching", False),
            ("cache_size_gb", 0.5),
            ("preview_memory_gb", 12),
            ("kept_free_gb", 0),
            ("pipeline_time_limit_minutes", 10_080),
            ("modelling_time_limit_minutes", 0.25),
            ("optimisation_time_limit_minutes", 90),
        ],
    )
    def test_accepts_values_in_range(self, key, value):
        assert getattr(PipelineSettingsValues.model_validate({key: value}), key) == value

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("chunk_rows", 0),
            ("chunk_rows", 10_000_001),
            ("chunk_rows", True),
            ("chunk_rows", "big"),
            ("caching", "yes"),
            ("caching", 1),
            ("cache_size_gb", 0),
            ("cache_size_gb", False),
            ("preview_memory_gb", -1),
            ("kept_free_gb", -0.5),
            ("kept_free_gb", True),
            ("pipeline_time_limit_minutes", 0),
            ("pipeline_time_limit_minutes", 10_081),
            ("optimisation_time_limit_minutes", True),
        ],
    )
    def test_rejects_values_out_of_range_and_bools_for_numbers(self, key, value):
        with pytest.raises(ValidationError):
            PipelineSettingsValues.model_validate({key: value})

    def test_rejects_an_unknown_key(self):
        with pytest.raises(ValidationError):
            PipelineSettingsValues.model_validate({"chunk_row": 1000})


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
