"""Tests for haute.schemas — Pydantic model validation.

Focuses on: required-field validation, nested structure, roundtrip dump, and
the response models that share fields through a common base.
Pure default-value assertions removed (Pydantic guarantees those).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from pydantic import ValidationError

from haute.schemas import (
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
    def test_accepts_session_message_and_source_file(self):
        from haute.schemas import AssistantMessageRequest

        request = AssistantMessageRequest(
            session_id="session-1",
            message="Author this pipeline",
            source_file="main.py",
        )
        assert request.session_id == "session-1"
        assert request.message == "Author this pipeline"
        assert request.source_file == "main.py"

    @pytest.mark.parametrize(
        "payload",
        [
            {"session_id": "s", "message": "m"},
            {"session_id": "s", "message": "m", "source_file": ""},
            {"session_id": "s", "message": "m", "source_file": "main.py", "unknown": True},
            {
                "session_id": "s",
                "message": "m",
                "source_file": "main.py",
                "confirmation": {"plan_hash": "a" * 64},
            },
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


class TestAssistantTurnOutcome:
    @pytest.mark.parametrize(
        "payload",
        [
            {"kind": "applied", "detail": None, "changes": ["a" * 64, "b" * 64]},
            {"kind": "answered", "detail": None, "changes": []},
            {"kind": "needs_input", "detail": "Which column?", "changes": []},
            {"kind": "needs_input", "detail": "Which band edges?", "changes": ["a" * 64]},
            {"kind": "blocked", "detail": "The file is missing.", "changes": ["a" * 64]},
            {"kind": "committed_unverified", "detail": "Verification failed.", "changes": []},
            {
                "kind": "incomplete",
                "detail": "A dry-run validated a plan that was never applied.",
                "changes": [],
            },
        ],
    )
    def test_valid_outcomes(self, payload):
        from haute.schemas import AssistantTurnOutcome

        assert AssistantTurnOutcome.model_validate(payload).model_dump() == payload

    @pytest.mark.parametrize(
        "payload",
        [
            {"kind": "applied", "detail": "extra", "changes": ["a" * 64]},
            {"kind": "needs_input", "detail": None, "changes": []},
            {"kind": "blocked", "detail": "  ", "changes": []},
            {"kind": "committed_unverified", "changes": []},
            {"kind": "incomplete", "detail": None, "changes": []},
            {"kind": "finished", "detail": None, "changes": []},
            {"kind": "applied", "detail": None, "changes": []},
            {"kind": "answered", "detail": None, "changes": ["a" * 64]},
            {"kind": "blocked", "detail": "The file is missing."},
        ],
    )
    def test_detail_must_match_the_kind(self, payload):
        from haute.schemas import AssistantTurnOutcome

        with pytest.raises(ValidationError):
            AssistantTurnOutcome.model_validate(payload)


# ──────────────────────────────────────────────────────────────────────
# D7: Pydantic-native git result types (item #74)
# ──────────────────────────────────────────────────────────────────────
#
# The ``_git`` module returns response models such as
# ``GitBranchListResponse`` directly — the former dataclass → Pydantic shim was
# dead code and has been removed.
# Dedicated tests for the contract live in ``tests/test_git_routes_pydantic.py``.


# ──────────────────────────────────────────────────────────────────────
# D12: PreviewNodeResponse inherits from NodeResult
# ──────────────────────────────────────────────────────────────────────


class TestPreviewNodeResponseInheritance:
    """D12: PreviewNodeResponse should inherit all NodeResult fields."""

    def test_is_subclass(self) -> None:
        from haute.schemas import NodeResult, PreviewNodeResponse

        assert issubclass(PreviewNodeResponse, NodeResult)

    def test_inherits_all_node_result_fields(self) -> None:
        from haute.schemas import NodeResult, PreviewNodeResponse

        node_fields = set(NodeResult.model_fields.keys())
        preview_fields = set(PreviewNodeResponse.model_fields.keys())
        assert node_fields.issubset(preview_fields), (
            f"Missing fields: {node_fields - preview_fields}"
        )

    def test_serialization_includes_inherited_and_own_fields(self) -> None:
        from haute.schemas import ColumnInfo, PreviewNodeResponse

        resp = PreviewNodeResponse(
            node_id="n1",
            status="ok",
            row_count=42,
            column_count=3,
            columns=[ColumnInfo(name="a", dtype="Int64")],
            preview=[{"a": 1}],
            error=None,
            timing_ms=12.5,
            memory_bytes=1024,
            timings=[],
            memory=[],
            node_statuses={"n1": "ok", "n2": "ok"},
        )
        d = resp.model_dump()
        # Inherited from NodeResult
        assert d["status"] == "ok"
        assert d["row_count"] == 42
        assert d["column_count"] == 3
        assert d["columns"] == [{"name": "a", "dtype": "Int64"}]
        assert d["timing_ms"] == 12.5
        assert d["memory_bytes"] == 1024
        assert d["error"] is None
        # Own fields
        assert d["node_id"] == "n1"
        assert d["node_statuses"] == {"n1": "ok", "n2": "ok"}

    def test_deserialization_from_dict(self) -> None:
        from haute.schemas import PreviewNodeResponse

        data = {
            "node_id": "n2",
            "status": "error",
            "error": "bad column",
            "error_line": 5,
            "row_count": 0,
        }
        resp = PreviewNodeResponse.model_validate(data)
        assert resp.node_id == "n2"
        assert resp.status == "error"
        assert resp.error == "bad column"
        assert resp.error_line == 5
        assert resp.row_count == 0

    def test_defaults_from_node_result(self) -> None:
        """Fields with defaults in NodeResult should have same defaults."""
        from haute.schemas import PreviewNodeResponse

        resp = PreviewNodeResponse(node_id="n1", status="ok")
        assert resp.row_count == 0
        assert resp.column_count == 0
        assert resp.columns == []
        assert resp.preview == []
        assert resp.error is None
        assert resp.timing_ms == 0
        assert resp.memory_bytes == 0
        assert resp.schema_warnings == []

    def test_node_result_standalone_still_works(self) -> None:
        """NodeResult should still work independently (not broken by inheritance)."""
        from haute.schemas import NodeResult

        nr = NodeResult(status="ok", row_count=10, column_count=2)
        d = nr.model_dump()
        assert d["status"] == "ok"
        assert d["row_count"] == 10
        assert "node_id" not in d  # Not in NodeResult


# ──────────────────────────────────────────────────────────────────────
# D13: MlflowLogResponse shared base
# ──────────────────────────────────────────────────────────────────────


def _without_receipt_fields(dumped: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in dumped.items() if key not in {"operation_id", "logged_at"}}


class TestMlflowLogResponseSharedBase:
    """D13: LogExperimentResponse and OptimiserMlflowLogResponse share a base."""

    def test_both_subclass_base(self) -> None:
        from haute.schemas import (
            LogExperimentResponse,
            MlflowLogResponse,
            OptimiserMlflowLogResponse,
        )

        assert issubclass(LogExperimentResponse, MlflowLogResponse)
        assert issubclass(OptimiserMlflowLogResponse, MlflowLogResponse)

    def test_shared_fields_come_from_the_base(self) -> None:
        from haute.schemas import (
            LogExperimentResponse,
            MlflowLogResponse,
            OptimiserMlflowLogResponse,
        )

        base_fields = set(MlflowLogResponse.model_fields.keys())
        opt_fields = set(OptimiserMlflowLogResponse.model_fields.keys())
        log_fields = set(LogExperimentResponse.model_fields.keys())
        assert opt_fields == base_fields
        # A training-result log also repeats its export receipt identity.
        assert log_fields - base_fields == {"operation_id", "logged_at"}

    def test_identical_serialization(self) -> None:
        from haute.schemas import LogExperimentResponse, OptimiserMlflowLogResponse

        kwargs = {
            "status": "ok",
            "backend": "databricks",
            "experiment_name": "/my/experiment",
            "run_id": "abc123",
            "run_url": "https://example.com/run/abc123",
            "tracking_uri": "databricks",
        }
        log_resp = LogExperimentResponse(**kwargs)
        opt_resp = OptimiserMlflowLogResponse(**kwargs)
        assert _without_receipt_fields(log_resp.model_dump()) == opt_resp.model_dump()

    def test_error_case_serialization(self) -> None:
        from haute.schemas import LogExperimentResponse, OptimiserMlflowLogResponse

        kwargs = {
            "status": "error",
            "error": "MLflow server unreachable",
        }
        log_resp = LogExperimentResponse(**kwargs)
        opt_resp = OptimiserMlflowLogResponse(**kwargs)
        assert _without_receipt_fields(log_resp.model_dump()) == opt_resp.model_dump()
        assert log_resp.error == "MLflow server unreachable"

    def test_defaults(self) -> None:
        from haute.schemas import MlflowLogResponse

        resp = MlflowLogResponse(status="ok")
        assert resp.backend == ""
        assert resp.experiment_name == ""
        assert resp.run_id is None
        assert resp.run_url is None
        assert resp.tracking_uri == ""
        assert resp.error is None

    def test_run_id_accepts_string_and_none(self) -> None:
        from haute.schemas import LogExperimentResponse, OptimiserMlflowLogResponse

        # String run_id
        r1 = LogExperimentResponse(status="ok", run_id="run123")
        assert r1.run_id == "run123"

        # None run_id (default)
        r2 = OptimiserMlflowLogResponse(status="ok")
        assert r2.run_id is None

    def test_base_model_dump_matches_children(self) -> None:
        """Base class should produce same JSON shape as children."""
        from haute.schemas import (
            LogExperimentResponse,
            MlflowLogResponse,
            OptimiserMlflowLogResponse,
        )

        kwargs = {"status": "ok", "backend": "local", "run_id": "x"}
        base = MlflowLogResponse(**kwargs)
        child1 = LogExperimentResponse(**kwargs)
        child2 = OptimiserMlflowLogResponse(**kwargs)
        assert (
            base.model_dump() == _without_receipt_fields(child1.model_dump()) == child2.model_dump()
        )
