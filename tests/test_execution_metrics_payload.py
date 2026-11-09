"""The execution metrics payload and the evidence it carries.

Preview-cache pins are released on failure, stages record their RSS deltas and checkpoint metrics,
memory-pressure events are bounded and JSON-safe, the payload carries projection, efficiency,
admission and input-preparation evidence, and the metrics summary rolls stage timings up in order.
"""

import json
from unittest.mock import patch

import polars as pl
import pytest

from haute._execution_admission import (
    create_admitted_execution_context,
)
from haute._execution_context import (
    ExecutionAdmission,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionMetricsRecorder,
    ExecutionProfile,
    ExecutionStageMetric,
)
from haute.graph_utils import NodeType
from haute.schemas import ExecutionMetricsPayload
from tests.conftest import (
    make_graph,
    make_source_node,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def test_preview_cache_unpins_entry_when_preview_projection_fails(tmp_path) -> None:
    from haute.executor import PreviewProjectionError, _preview_cache, execute_graph

    data_path = tmp_path / "input.parquet"
    pl.DataFrame({"a": [1, 2]}).write_parquet(data_path)
    graph = make_graph({"nodes": [make_source_node("source", str(data_path))], "edges": []})

    with pytest.raises(PreviewProjectionError):
        execute_graph(
            graph,
            target_node_id="source",
            target_preview_only=True,
            requested_preview_columns=["missing"],
        )

    assert _preview_cache.stats()["pinned_entries"] == 0


def test_execute_graph_response_shaping_fault_releases_preview_cache_pin(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import executor
    from haute.executor import _preview_cache, execute_graph

    data_path = tmp_path / "input.parquet"
    pl.DataFrame({"a": [1, 2]}).write_parquet(data_path)
    graph = make_graph({"nodes": [make_source_node("source", str(data_path))], "edges": []})

    def fail_response_shaping(**_fields: object) -> None:
        raise RuntimeError("response shaping fault")

    monkeypatch.setattr(executor, "NodeResult", fail_response_shaping)

    with pytest.raises(RuntimeError, match="response shaping fault"):
        execute_graph(
            graph,
            target_node_id="source",
            target_preview_only=True,
            execution_context=ExecutionContext(
                operation="preview",
                profile=ExecutionProfile.PREVIEW_EAGER,
            ),
        )

    assert _preview_cache.stats()["pinned_entries"] == 0


def test_bounded_eager_profile_propagates_preamble_failure_before_execution() -> None:
    from haute.executor import PreambleError, _eager_execute

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_INPUT.value,
                        "config": {},
                    },
                }
            ],
            "edges": [],
            "preamble": "broken",
        }
    )
    error = PreambleError("preamble failed")

    with (
        patch("haute.executor._compile_preamble", side_effect=error),
        pytest.raises(PreambleError) as exc_info,
    ):
        _eager_execute(
            graph,
            target_node_id="source",
            row_limit=None,
            execution_context=ExecutionContext(
                operation="training",
                profile=ExecutionProfile.TRAINING_PREP,
            ),
        )

    assert exc_info.value is error


def test_preview_cache_does_not_pin_when_store_rejects_entry(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import structlog.testing

    from haute import executor as executor_mod

    class RejectingPreviewCache:
        fingerprint = None
        most_recent_key = None

        def __init__(self) -> None:
            self.put_calls = 0
            self.pin_calls = 0
            self.unpin_calls = 0

        def get(self, _fingerprint):
            return None

        def put(self, _fingerprint, _entry):
            self.put_calls += 1
            return False

        def pin(self, _fingerprint):
            self.pin_calls += 1

        def unpin(self, _fingerprint):
            self.unpin_calls += 1

    rejecting_cache = RejectingPreviewCache()
    monkeypatch.setattr(executor_mod, "_preview_cache", rejecting_cache)
    data_path = tmp_path / "input.parquet"
    pl.DataFrame({"a": [1, 2]}).write_parquet(data_path)
    graph = make_graph({"nodes": [make_source_node("source", str(data_path))], "edges": []})

    with structlog.testing.capture_logs() as logs:
        result = executor_mod.execute_graph(
            graph,
            target_node_id="source",
            target_preview_only=True,
        )

    assert result["source"].status == "ok"
    assert rejecting_cache.put_calls == 1
    assert rejecting_cache.pin_calls == 0
    assert rejecting_cache.unpin_calls == 0
    assert any(record.get("event") == "preview_cache_store_skipped" for record in logs)


def test_preview_execution_metrics_identify_cache_miss_and_hit(tmp_path) -> None:
    from haute.executor import _preview_cache, execute_graph

    _preview_cache.clear()
    data_path = tmp_path / "input.parquet"
    pl.DataFrame({"a": [1, 2]}).write_parquet(data_path)
    graph = make_graph({"nodes": [make_source_node("source", str(data_path))], "edges": []})

    miss_context = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 1_000,
    )
    miss_result = execute_graph(
        graph,
        target_node_id="source",
        target_preview_only=True,
        execution_context=miss_context,
    )
    assert miss_result["source"].status == "ok"
    miss_stages = miss_context.metrics_payload(status="completed")["stage_elapsed_ms"]
    assert "preview_cache_lookup" in miss_stages
    assert "preview_cache_miss" in miss_stages
    assert "preview_cache_hit" not in miss_stages

    hit_context = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 1_000,
    )
    hit_result = execute_graph(
        graph,
        target_node_id="source",
        target_preview_only=True,
        execution_context=hit_context,
    )
    assert hit_result["source"].status == "ok"
    hit_stages = hit_context.metrics_payload(status="completed")["stage_elapsed_ms"]
    assert "preview_cache_lookup" in hit_stages
    assert "preview_cache_hit" in hit_stages
    assert "preview_cache_miss" not in hit_stages
    assert "eager_collect" not in hit_stages

    _preview_cache.clear()


def test_execution_context_records_stage_metric_with_rss_delta() -> None:
    samples = iter([100, 140])
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        job_id="job-1",
        memory_sampler=lambda: next(samples),
    )

    with context.stage("node", node_id="node-1"):
        pass

    metrics = context.metrics.snapshot()
    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.name == "node"
    assert metric.operation == "preview"
    assert metric.profile == ExecutionProfile.PREVIEW_EAGER
    assert metric.job_id == "job-1"
    assert metric.node_id == "node-1"
    assert metric.rss_start_bytes == 100
    assert metric.rss_end_bytes == 140
    assert metric.rss_delta_bytes == 40
    assert metric.rss_peak_bytes == 140
    assert metric.elapsed_ms >= 0


def test_stage_exit_sampler_failure_preserves_primary_error_and_restores_context() -> None:
    from haute._execution_context import current_execution_context

    samples = iter([100])
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: next(samples),
    )
    primary = RuntimeError("primary execution failure")

    with pytest.raises(RuntimeError) as exc_info:
        with context.stage("collect", node_id="node-1"):
            raise primary

    assert exc_info.value is primary
    assert current_execution_context() is None
    assert any(
        "Execution stage finalization failed: StopIteration" in note
        for note in getattr(primary, "__notes__", ())
    )


def test_stage_exit_sampler_failure_without_primary_remains_loud_and_restores_context() -> None:
    from haute._execution_context import current_execution_context

    calls = 0

    def sample_memory() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 100
        raise OSError("RSS sampling failed")

    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=sample_memory,
    )

    with pytest.raises(OSError, match="RSS sampling failed"):
        with context.stage("collect", node_id="node-1"):
            pass

    assert current_execution_context() is None


def test_stage_exit_reports_every_internal_finalization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage cleanup is fail-loud and preserves later failures as notes."""
    from haute import _execution_context as context_mod

    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 100,
    )

    class BrokenContextVar:
        def set(self, value: object) -> object:
            assert value is context
            return object()

        def reset(self, _token: object) -> None:
            raise LookupError("context reset failed")

    def fail_record(_metric: ExecutionStageMetric) -> None:
        raise ValueError("metric recording failed")

    monkeypatch.setattr(context_mod, "_CURRENT_EXECUTION_CONTEXT", BrokenContextVar())
    monkeypatch.setattr(context.metrics, "record", fail_record)

    with pytest.raises(RuntimeError, match="stage stack is unbalanced") as exc_info:
        with context.stage("collect", node_id="node-1"):
            context._open_stages.stack.clear()

    notes = getattr(exc_info.value, "__notes__", ())
    assert any("LookupError: context reset failed" in note for note in notes)
    assert any("ValueError: metric recording failed" in note for note in notes)


def test_execution_context_stage_tracks_peak_rss_from_checkpoints() -> None:
    samples = iter([100, 160, 120])
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        job_id="job-1",
        memory_sampler=lambda: next(samples),
    )

    with context.stage("collect", node_id="node-1"):
        context.checkpoint(label="inside_collect", node_id="node-1")

    metric = context.metrics.snapshot()[0]
    assert metric.rss_start_bytes == 100
    assert metric.rss_end_bytes == 120
    assert metric.rss_delta_bytes == 20
    assert metric.rss_peak_bytes == 160


def test_execution_context_stage_records_checkpoint_metrics() -> None:
    samples = iter([100, 110, 120, 130])
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: next(samples),
    )

    with context.stage("collect", node_id="node-1"):
        context.checkpoint(label="before_inner_collect", node_id="node-1")
        context.checkpoint(label="after_inner_collect", node_id="node-1")

    metric = context.metrics.snapshot()[0]
    assert metric.n_checkpoints == 2
    assert metric.to_summary().to_dict()["n_checkpoints"] == 2


def test_execution_context_records_non_terminal_memory_pressure_events() -> None:
    samples = iter([400, 500, 760, 901, 950])
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        job_id="job-1",
        memory_limit_bytes=1_000,
        memory_baseline_bytes=0,
        admission=ExecutionAdmission(
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=1_000,
            rss_at_admission_bytes=400,
            rss_limit_bytes=None,
            headroom_bytes=600,
            config_key="adaptive:preview_eager",
            budget_policy="adaptive_local",
            available_ram_bytes=8_000,
            os_reserve_bytes=2_000,
        ),
        memory_sampler=lambda: next(samples),
    )

    with context.stage("collect", node_id="node-1"):
        context.checkpoint(label="half", node_id="node-1")
        context.checkpoint(label="three-quarter", node_id="node-1")
        context.checkpoint(label="ninety", node_id="node-1")

    payload = context.metrics_payload(status="completed")

    assert payload["memory_pressure_event_count"] == 3
    assert payload["retained_memory_pressure_event_count"] == 3
    assert payload["truncated_memory_pressure_event_count"] == 0
    assert payload["memory_pressure_events_truncated"] is False
    assert [event["threshold_percent"] for event in payload["memory_pressure_events"]] == [
        50,
        75,
        90,
    ]
    assert [event["rss_bytes"] for event in payload["memory_pressure_events"]] == [
        500,
        760,
        901,
    ]
    assert all(
        event["event"] == "memory_pressure"
        and event["operation"] == "preview"
        and event["profile"] == "preview_eager"
        and event["job_id"] == "job-1"
        and event["stage"] == "collect"
        and event["node_id"] == "node-1"
        and event["rss_limit_bytes"] == 1_000
        for event in payload["memory_pressure_events"]
    )
    final_event = payload["memory_pressure_events"][-1]
    assert final_event["rss_peak_bytes"] == 901
    assert final_event["headroom_bytes"] == 99
    assert final_event["baseline_rss_bytes"] == 0
    assert final_event["budget_policy"] == "adaptive_local"
    assert final_event["config_key"] == "adaptive:preview_eager"
    assert final_event["available_ram_bytes"] == 8_000
    assert final_event["os_reserve_bytes"] == 2_000


def test_execution_context_memory_pressure_events_are_bounded() -> None:
    samples = iter([10, 50, 75, 90, 95])
    context = ExecutionContext(
        operation="auto_range",
        profile=ExecutionProfile.OPTIMISER_SOLVE,
        memory_limit_bytes=100,
        metrics=ExecutionMetricsRecorder(max_memory_pressure_events=2),
        memory_sampler=lambda: next(samples),
    )

    with context.stage("chunk", node_id="node-1"):
        context.checkpoint(label="half", node_id="node-1")
        context.checkpoint(label="three-quarter", node_id="node-1")
        context.checkpoint(label="ninety", node_id="node-1")

    payload = context.metrics_payload(status="completed")

    assert payload["memory_pressure_event_count"] == 3
    assert payload["retained_memory_pressure_event_count"] == 2
    assert payload["truncated_memory_pressure_event_count"] == 1
    assert payload["memory_pressure_events_truncated"] is True
    assert [event["threshold_percent"] for event in payload["memory_pressure_events"]] == [50, 75]
    assert all(
        "frame" not in event and "traceback" not in event
        for event in payload["memory_pressure_events"]
    )


def test_execution_context_memory_pressure_uses_growth_budget_when_baselined() -> None:
    samples = iter([1_000, 1_049, 1_050, 1_075, 1_090, 1_099])
    context = ExecutionContext(
        operation="auto_range",
        profile=ExecutionProfile.OPTIMISER_SOLVE,
        memory_limit_bytes=100,
        memory_baseline_bytes=1_000,
        memory_sampler=lambda: next(samples),
    )

    with context.stage("range_build", node_id="ratebook_optimiser"):
        context.checkpoint(label="below-half", node_id="ratebook_optimiser")
        context.checkpoint(label="half", node_id="ratebook_optimiser")
        context.checkpoint(label="three-quarter", node_id="ratebook_optimiser")
        context.checkpoint(label="ninety", node_id="ratebook_optimiser")

    payload = context.metrics_payload(status="completed")

    assert [event["threshold_percent"] for event in payload["memory_pressure_events"]] == [
        50,
        75,
        90,
    ]
    assert [event["rss_bytes"] for event in payload["memory_pressure_events"]] == [
        1_050,
        1_075,
        1_090,
    ]
    assert [event["headroom_used_bytes"] for event in payload["memory_pressure_events"]] == [
        50,
        75,
        90,
    ]
    assert [event["headroom_bytes"] for event in payload["memory_pressure_events"]] == [
        50,
        25,
        10,
    ]


def test_execution_context_memory_pressure_events_survive_memory_failure() -> None:
    samples = iter([40, 76, 101, 101])
    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id="job-1",
        memory_limit_bytes=100,
        memory_baseline_bytes=0,
        memory_sampler=lambda: next(samples),
    )

    with pytest.raises(ExecutionMemoryLimitExceededError):
        with context.stage("fit", node_id="model"):
            context.checkpoint(label="pressure", node_id="model")
            context.checkpoint(label="over-limit", node_id="model")

    payload = context.metrics_payload(
        status="memory_limited",
        terminal_reason="memory_limited",
    )

    assert payload["status"] == "memory_limited"
    assert payload["memory_pressure_event_count"] == 3
    assert [event["threshold_percent"] for event in payload["memory_pressure_events"]] == [
        50,
        75,
        90,
    ]
    terminal_event = payload["memory_pressure_events"][-1]
    assert terminal_event["rss_bytes"] == 101
    assert terminal_event["headroom_bytes"] == -1
    assert terminal_event["stage"] == "fit"
    assert terminal_event["node_id"] == "model"


def test_execution_metrics_payload_includes_projection_diagnostics() -> None:
    from haute.projection import (
        ProjectionDiagnostics,
        ProjectionEdgeKey,
        ProjectionPlan,
        ProjectionReason,
    )

    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    edge_key = ProjectionEdgeKey(edge_id="e_source_train", source="source", target="train")
    plan = ProjectionPlan(
        needed_by_node={"train": None},
        edge_demands={edge_key: frozenset({"target"})},
        opaque_boundaries=frozenset({"train"}),
        diagnostics=ProjectionDiagnostics(
            opaque_reasons={
                "train": ProjectionReason(
                    rule="schema_all_except",
                    message="schema-derived all-except demand",
                    details={"keep": ("target",)},
                )
            },
            node_reasons={
                "train": ProjectionReason(
                    rule="schema_all_except",
                    message="schema-derived all-except demand",
                )
            },
            edge_reasons={
                edge_key: ProjectionReason(
                    rule="runtime_inferred_streaming",
                    message="runtime parent projection",
                )
            },
        ),
    )

    context.projection_plan = plan

    assert plan.diagnostics_payload(profile=context.profile.value) == {
        "opaque_reasons": {
            "train": {
                "rule": "schema_all_except",
                "message": "schema-derived all-except demand",
                "details": {"keep": ("target",)},
            }
        },
        "node_reasons": {
            "train": {
                "rule": "schema_all_except",
                "message": "schema-derived all-except demand",
                "details": {},
            }
        },
        "edge_reasons": {
            "source->train": {
                "rule": "runtime_inferred_streaming",
                "message": "runtime parent projection",
                "details": {},
            }
        },
        "strategy_summary": {
            "profile": "training_prep",
            "node_strategy_counts": {"schema_all_except": 1},
            "opaque_boundary_count": 1,
            "materialisation_boundary_count": 0,
            "node_strategy_count": 1,
            "retained_node_strategy_count": 1,
            "truncated_node_strategy_count": 0,
            "node_strategies_truncated": False,
            "node_strategies": [
                {
                    "node_id": "train",
                    "strategy": "schema_all_except",
                    "reason_rule": "schema_all_except",
                }
            ],
        },
    }


def test_execution_metrics_payload_bounds_width_evidence_and_keeps_unavailable_counters_null() -> (
    None
):
    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_limit_bytes=1_000,
        memory_baseline_bytes=0,
        memory_sampler=lambda: 256,
    )
    for index in reversed(range(130)):
        context.record_column_widths(
            node_id=f"node-{index:03d}",
            input_width=index,
            output_width=index + 1,
            requested_width=2,
            physically_scanned_width=3,
        )
    context.record_bytes_written(4096)
    context.record_chunk()
    context.checkpoint(label="after_chunk")

    payload = context.metrics_payload(status="completed")

    assert "bytes_read" not in payload
    assert payload["bytes_written"] == 4096
    assert payload["estimated_bytes"] is None
    assert payload["checkpoint_count"] == 1
    assert payload["chunk_count"] == 1
    assert payload["observed_peak_rss_bytes"] == 256
    assert payload["column_widths"]["state"] == "truncated"
    assert payload["column_widths"]["total_count"] == 130
    assert len(payload["column_widths"]["items"]) == 128
    assert payload["column_widths"]["items"][0]["node_id"] == "node-000"
    assert payload["column_widths"]["items"][-1]["node_id"] == "node-127"
    assert ExecutionMetricsPayload.model_validate(payload).model_dump(mode="json") == payload
    ExecutionMetricsPayload.model_validate(payload)


def test_execution_efficiency_evidence_counts_direct_fallbacks_and_aggregate_widths() -> None:
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    context.record_column_widths(
        node_id="a",
        requested_width=2,
        physically_scanned_width=3,
    )
    context.record_column_widths(
        node_id="b",
        requested_width=5,
        physically_scanned_width=8,
    )
    context.record_cache_direct_fallback()

    payload = context.metrics_payload(status="completed")

    assert payload["requested_column_width_total"] == 7
    assert payload["physically_scanned_column_width_total"] == 11
    assert payload["cache_direct_fallbacks"] == 1
    assert "cache_proof" not in payload
    assert ExecutionMetricsPayload.model_validate(payload).model_dump(mode="json") == payload


def test_execution_efficiency_width_totals_fail_closed_on_partial_evidence() -> None:
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    context.record_column_widths(
        node_id="known",
        requested_width=2,
        physically_scanned_width=3,
    )
    context.record_column_widths(
        node_id="partial",
        requested_width=None,
        physically_scanned_width=5,
    )

    payload = context.metrics_payload(status="completed")

    assert payload["requested_column_width_total"] is None
    assert payload["physically_scanned_column_width_total"] == 8


def test_projection_strategy_summary_is_bounded() -> None:
    from haute.projection import ProjectionPlan

    plan = ProjectionPlan(
        needed_by_node={f"node_{idx:03d}": frozenset({"value"}) for idx in range(105)},
        edge_demands={},
    )

    payload = plan.diagnostics_payload(profile="lazy_sink")["strategy_summary"]

    assert payload["profile"] == "lazy_sink"
    assert payload["node_strategy_counts"] == {"projected": 105}
    assert payload["node_strategy_count"] == 105
    assert payload["retained_node_strategy_count"] == 100
    assert payload["truncated_node_strategy_count"] == 5
    assert payload["node_strategies_truncated"] is True
    assert len(payload["node_strategies"]) == 100


def test_execution_context_memory_pressure_payload_is_json_safe() -> None:
    samples = iter([40, 76, 101, 101])
    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id="job-1",
        memory_limit_bytes=100,
        memory_sampler=lambda: next(samples),
    )

    with pytest.raises(ExecutionMemoryLimitExceededError):
        with context.stage("fit", node_id="model"):
            context.checkpoint(label="pressure", node_id="model")
            context.checkpoint(label="over-limit", node_id="model")

    payload = context.metrics_payload(
        status="memory_limited",
        terminal_reason="memory_limited",
    )
    round_tripped = ExecutionMetricsPayload.model_validate(payload).model_dump(mode="json")

    json.dumps(round_tripped)
    assert round_tripped["memory_pressure_events"][-1]["stage"] == "fit"
    assert round_tripped["memory_pressure_events"][-1]["node_id"] == "model"


def test_execution_context_without_memory_limit_records_no_pressure_events() -> None:
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 10_000,
    )

    with context.stage("collect", node_id="node-1"):
        context.checkpoint(label="inside_collect", node_id="node-1")

    payload = context.metrics_payload(status="completed")

    assert payload["memory_pressure_event_count"] == 0
    assert payload["retained_memory_pressure_event_count"] == 0
    assert payload["truncated_memory_pressure_event_count"] == 0
    assert payload["memory_pressure_events_truncated"] is False
    assert payload["memory_pressure_events"] == []


def test_execution_context_enforces_memory_budget_at_checkpoint() -> None:
    context = ExecutionContext(
        operation="auto-range",
        profile=ExecutionProfile.OPTIMISER_SOLVE,
        job_id="job-1",
        memory_limit_bytes=99,
        memory_sampler=lambda: 100,
    )

    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="before-collect")

    assert exc_info.value.operation == "auto-range"
    assert exc_info.value.job_id == "job-1"
    assert exc_info.value.rss_bytes == 100
    assert exc_info.value.limit_bytes == 99


def test_execution_context_records_stage_metric_when_memory_limit_fails_at_entry() -> None:
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        job_id="job-1",
        memory_limit_bytes=10,
        memory_baseline_bytes=100,
        memory_sampler=lambda: 111,
    )

    with pytest.raises(ExecutionMemoryLimitExceededError):
        with context.stage("collect", node_id="node-1"):
            raise AssertionError("stage body should not execute")

    metrics = context.metrics.snapshot()
    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.name == "collect"
    assert metric.node_id == "node-1"
    assert metric.rss_start_bytes == 111
    assert metric.rss_end_bytes == 111
    assert metric.rss_peak_bytes == 111


def test_execution_metrics_payload_includes_admission_metadata(
    monkeypatch, pipeline_settings
) -> None:
    pipeline_settings(preview_memory_gb=256 / 1024)
    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 32 * 1024 * 1024,
    )

    payload = context.metrics_payload(status="completed")

    assert payload["admission"] == {
        "admitted": True,
        "operation": "pipeline_preview",
        "profile": "preview_eager",
        "memory_limit_bytes": 256 * 1024 * 1024,
        "rss_at_admission_bytes": 32 * 1024 * 1024,
        "rss_limit_bytes": 288 * 1024 * 1024,
        "process_rss_limit_bytes": None,
        "headroom_bytes": 256 * 1024 * 1024,
        "config_key": "preview_memory_gb",
        "budget_policy": "pipeline_settings",
        "available_ram_bytes": None,
        "os_reserve_bytes": None,
        "reason": "within_memory_budget",
    }
    validated = ExecutionMetricsPayload.model_validate(payload).model_dump(mode="json")
    assert validated["admission"]["budget_policy"] == "pipeline_settings"
    assert validated["admission"]["available_ram_bytes"] is None
    assert validated["admission"]["os_reserve_bytes"] is None


def test_execution_metrics_recorder_sums_node_stage_timings() -> None:
    recorder = ExecutionMetricsRecorder()

    recorder.record(
        ExecutionStageMetric(
            name="read",
            elapsed_ms=1.25,
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            node_id="a",
        )
    )
    recorder.record(
        ExecutionStageMetric(
            name="collect",
            elapsed_ms=2.5,
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            node_id="a",
        )
    )
    recorder.record(
        ExecutionStageMetric(
            name="setup",
            elapsed_ms=10.0,
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            node_id=None,
        )
    )

    summary = recorder.summary(operation="preview", profile=ExecutionProfile.PREVIEW_EAGER)
    assert summary.node_elapsed_ms == {"a": 3.75}


def test_execution_metrics_summary_preserves_order_and_rolls_up_totals() -> None:
    recorder = ExecutionMetricsRecorder()
    recorder.record(
        ExecutionStageMetric(
            name="read",
            elapsed_ms=1.25,
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            node_id="a",
            rss_start_bytes=100,
            rss_end_bytes=140,
            rss_peak_bytes=155,
        )
    )
    recorder.record(
        ExecutionStageMetric(
            name="collect",
            elapsed_ms=2.5,
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            node_id="a",
            rss_start_bytes=140,
            rss_end_bytes=125,
            rss_peak_bytes=160,
        )
    )
    recorder.record(
        ExecutionStageMetric(
            name="setup",
            elapsed_ms=10.0,
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            node_id=None,
            rss_start_bytes=90,
            rss_end_bytes=95,
            rss_peak_bytes=100,
        )
    )

    summary = recorder.summary(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        job_id="job-1",
        max_stages=2,
    )
    payload = summary.to_dict()

    assert payload["schema_version"] == 1
    assert payload["operation"] == "preview"
    assert payload["profile"] == "preview_eager"
    assert payload["job_id"] == "job-1"
    assert payload["stage_count"] == 3
    assert payload["retained_stage_count"] == 2
    assert payload["truncated_stage_count"] == 1
    assert payload["total_elapsed_ms"] == 13.75
    assert payload["rss_peak_bytes"] == 160
    assert payload["rss_delta_bytes"] == -5
    assert [stage["name"] for stage in payload["stages"]] == ["read", "collect"]
    assert payload["node_elapsed_ms"] == {"a": 3.75}
    assert payload["stage_elapsed_ms"] == {"read": 1.25, "collect": 2.5, "setup": 10.0}


def test_execution_metrics_summary_caps_stage_payload_without_dropping_rollups() -> None:
    recorder = ExecutionMetricsRecorder(max_stages=2)

    for index in range(4):
        recorder.record(
            ExecutionStageMetric(
                name="chunk",
                elapsed_ms=float(index + 1),
                operation="auto_range",
                profile=ExecutionProfile.OPTIMISER_SOLVE,
                node_id=f"node-{index}",
            )
        )

    summary = recorder.summary(
        operation="auto_range",
        profile=ExecutionProfile.OPTIMISER_SOLVE,
        job_id="job-1",
    ).to_dict()

    assert summary["stage_count"] == 4
    assert summary["retained_stage_count"] == 2
    assert summary["truncated_stage_count"] == 2
    assert [stage["node_id"] for stage in summary["stages"]] == ["node-0", "node-1"]
    assert summary["stage_elapsed_ms"] == {"chunk": 10.0}


def test_conservative_strategy_reports_materialising_streamability() -> None:
    """A warned conservative run still materialises at its group-by boundary."""
    from types import SimpleNamespace

    from haute.execution import (
        BoundedDiagnosticCollection,
        ExecutionBoundedness,
        ExecutionStrategy,
        ExecutionStrategyDiagnostic,
    )

    context = ExecutionContext(operation="sink", profile=ExecutionProfile.LAZY_SINK)
    diagnostic = ExecutionStrategyDiagnostic.create(
        strategy=ExecutionStrategy.FULL_WIDTH_CONSERVATIVE,
        profile=ExecutionProfile.LAZY_SINK,
        boundedness=ExecutionBoundedness.UNBOUNDED,
        reason_code="materialisation_estimate_unavailable_conservative",
        boundaries=BoundedDiagnosticCollection.available([]),
        reasons=BoundedDiagnosticCollection.available([]),
        provenance=BoundedDiagnosticCollection.available([]),
    )
    context.projection_plan = SimpleNamespace(diagnostic=diagnostic)  # type: ignore[assignment]

    payload = context.metrics_payload(status="completed")

    assert payload["execution_strategy"]["status"] == "warned"
    assert payload["streamability"] == "materialising"
    assert (
        "materialisation_estimate_unavailable_conservative"
        in (payload["streamability_evidence"]["items"])
    )


def test_input_preparation_records_reach_the_metrics_payload() -> None:
    from haute._execution_schemas import ExecutionMetricsPayload
    from haute._input_preparation import InputPreparationRecord

    context = ExecutionContext(operation="metrics", profile=ExecutionProfile.LAZY_SINK)
    assert context.metrics_payload()["input_preparation"] == []

    context.record_input_preparation(
        InputPreparationRecord(
            node_id="input",
            identity_digest="b" * 64,
            action="refreshed",
            build_class="bounded",
            execution="worker",
            memory_limit_bytes=1024,
            elapsed_seconds=0.5,
            row_count=3,
            size_bytes=64,
            generation_id="8f0d4a2c-1c3b-4f5a-9c2d-0e1f2a3b4c5d",
            warning_code="eager_read_mode_scanned",
        )
    )
    payload = context.metrics_payload()

    assert payload["input_preparation"] == [
        {
            "node_id": "input",
            "identity_digest": "b" * 64,
            "action": "refreshed",
            "build_class": "bounded",
            "execution": "worker",
            "memory_limit_bytes": 1024,
            "elapsed_seconds": 0.5,
            "row_count": 3,
            "size_bytes": 64,
            "generation_id": "8f0d4a2c-1c3b-4f5a-9c2d-0e1f2a3b4c5d",
            "warning_code": "eager_read_mode_scanned",
        }
    ]
    validated = ExecutionMetricsPayload.model_validate(payload)
    assert validated.input_preparation[0].action == "refreshed"


def test_worker_metrics_carry_the_parents_evidence_ahead_of_their_own() -> None:
    from haute._execution_schemas import ExecutionMetricsPayload
    from haute._input_preparation import InputPreparationRecord
    from haute._node_snapshots import NodeSnapshotColumns
    from haute._seed_plans import (
        CaptureKind,
        SharedSnapshotCaptureRecord,
        SharedSnapshotSeedRecord,
    )

    parent = ExecutionContext(operation="parent", profile=ExecutionProfile.TRAINING_PREP)
    parent.record_input_preparation(
        InputPreparationRecord(
            node_id="src",
            identity_digest="a" * 64,
            action="reused",
            build_class="bounded",
            execution="in_process",
            memory_limit_bytes=None,
            elapsed_seconds=0.0,
            row_count=3,
            size_bytes=64,
            generation_id="8f0d4a2c-1c3b-4f5a-9c2d-0e1f2a3b4c5d",
            warning_code="source_unavailable",
        )
    )
    parent.record_execution_warning("snapshot_capture_superseded", node_id="A")
    worker = ExecutionContext(operation="worker", profile=ExecutionProfile.TRAINING_PREP)
    worker.record_shared_snapshot_seed(
        SharedSnapshotSeedRecord("B", "b" * 64, "gen-b", NodeSnapshotColumns.all())
    )
    worker.record_shared_snapshot_capture(
        SharedSnapshotCaptureRecord(
            "C", "c" * 64, CaptureKind.CONSUMED, "superseded", None, NodeSnapshotColumns.all()
        )
    )
    worker.record_execution_warning("snapshot_capture_superseded", node_id="C")

    merged = parent.metrics_with_worker_evidence(worker.metrics_payload())

    assert merged["operation"] == "worker"
    assert [record["node_id"] for record in merged["input_preparation"]] == ["src"]
    assert merged["input_preparation"][0]["warning_code"] == "source_unavailable"
    assert [seed["node_id"] for seed in merged["shared_snapshot_seeds"]] == ["B"]
    assert [capture["node_id"] for capture in merged["shared_snapshot_captures"]] == ["C"]
    assert [(warning["code"], warning["node_id"]) for warning in merged["warnings"]] == [
        ("snapshot_capture_superseded", "A"),
        ("snapshot_capture_superseded", "C"),
    ]
    # The parent now holds the worker's evidence, so a later worker's metrics
    # carry both processes' evidence.
    assert parent.worker_evidence()["shared_snapshot_seeds"] == merged["shared_snapshot_seeds"]
    ExecutionMetricsPayload.model_validate(merged)


def test_adopted_worker_input_preparation_is_copied_into_parent_evidence() -> None:
    from haute._input_preparation import InputPreparationRecord

    parent = ExecutionContext(operation="parent", profile=ExecutionProfile.TRAINING_PREP)
    worker = ExecutionContext(operation="worker", profile=ExecutionProfile.TRAINING_PREP)
    worker.record_input_preparation(
        InputPreparationRecord(
            node_id="source",
            identity_digest="a" * 64,
            action="built",
            build_class="bounded",
            execution="in_process",
            memory_limit_bytes=None,
            elapsed_seconds=0.1,
            row_count=4,
            size_bytes=16,
            generation_id="8f0d4a2c-1c3b-4f5a-9c2d-0e1f2a3b4c5d",
            warning_code=None,
        )
    )
    payload = worker.metrics_payload()

    parent.adopt_worker_evidence(payload)
    payload["input_preparation"][0]["node_id"] = "mutated"  # type: ignore[index]

    evidence = parent.worker_evidence()
    assert evidence["input_preparation"][0]["node_id"] == "source"
    assert parent.metrics_payload()["input_preparation"][0]["node_id"] == "source"
