import asyncio
import json
import threading
import time
from unittest.mock import patch

import polars as pl
import pytest

from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
    execution_budget_for_profile,
)
from haute._execution_context import (
    ExecutionAdmission,
    ExecutionCancellationToken,
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionMetricsRecorder,
    ExecutionProfile,
    ExecutionStageMetric,
)
from haute.graph_utils import NodeType, _execute_eager_core, _execute_lazy
from haute.schemas import ExecutionMetricsPayload
from tests.conftest import make_edge, make_graph, make_output_config, make_source_node


def _clear_execution_memory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove memory-budget env vars so tests exercise default policy."""
    from haute import _execution_admission as admission_mod

    for profile in ExecutionProfile:
        for key, _multiplier in admission_mod._memory_env_candidates(profile):
            monkeypatch.delenv(key, raising=False)
        for key, _multiplier in admission_mod._process_rss_env_candidates(profile):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("HAUTE_EXECUTION_MEMORY_POLICY", raising=False)
    monkeypatch.delenv("HAUTE_EXECUTION_OS_RESERVE_BYTES", raising=False)
    monkeypatch.delenv("HAUTE_EXECUTION_OS_RESERVE_MB", raising=False)
    admission_mod._clear_in_flight_reservations_for_tests()


def test_windows_current_rss_bytes_returns_none_when_windll_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_context as context_mod

    monkeypatch.setattr(context_mod.os, "name", "nt")
    monkeypatch.delattr(context_mod.ctypes, "WinDLL", raising=False)

    assert context_mod._windows_current_rss_bytes() is None


class _ImmediateThread:
    """Run a background target inline while preserving the Thread constructor API."""

    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self) -> None:
        self.target()


def test_execution_metrics_payload_includes_memory_budget() -> None:
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=123_456,
        memory_sampler=lambda: 1_000,
    )

    with context.stage("collect", node_id="node-1"):
        pass

    payload = context.metrics_payload(status="completed")

    assert payload["memory_limit_bytes"] == 123_456


def test_execution_admission_uses_profile_specific_memory_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import admit_execution

    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_LIMIT_BYTES", "100")
    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_BYTES", "250")

    context = admit_execution(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 10,
    )

    assert context.profile == ExecutionProfile.PREVIEW_EAGER
    assert context.memory_limit_bytes == 250


def test_execution_admission_policy_covers_every_engine_profile() -> None:
    from haute import _execution_admission as admission

    expected_profiles = set(ExecutionProfile)

    assert set(admission._ADAPTIVE_MEMORY_POLICY) == expected_profiles
    assert set(admission._PROFILE_MEMORY_ENV) == expected_profiles
    assert set(admission._PROFILE_PROCESS_RSS_ENV) == expected_profiles
    assert ExecutionProfile.DEPLOY_LIVE not in admission._ADAPTIVE_LOCAL_PROFILES


def test_default_memory_budgets_adapt_to_available_ram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 20 * gib)

    for profile in (
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
    ):
        budget = execution_budget_for_profile(profile)
        assert budget.config_key == f"adaptive:{profile.value}"
        assert budget.budget_policy == "adaptive_local"
        assert budget.available_ram_bytes == 20 * gib
        assert budget.os_reserve_bytes == 2 * gib
        assert budget.memory_limit_bytes > 0

    live_budget = execution_budget_for_profile(ExecutionProfile.DEPLOY_LIVE)
    assert live_budget.memory_limit_bytes == gib
    assert live_budget.config_key == "default:deploy_live"
    assert live_budget.budget_policy == "fixed_default"


def test_adaptive_default_memory_budgets_never_exceed_available_ram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_execution_memory_env(monkeypatch)
    available = 384 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: available)

    for profile in set(ExecutionProfile) - {ExecutionProfile.DEPLOY_LIVE}:
        budget = execution_budget_for_profile(profile)
        assert 0 < budget.memory_limit_bytes <= available


def test_adaptive_default_memory_budgets_honor_configured_os_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 20 * gib)

    default_budget = execution_budget_for_profile(ExecutionProfile.AUTO_RANGE)
    monkeypatch.setenv("HAUTE_EXECUTION_OS_RESERVE_MB", str(6 * 1024))
    reserved_budget = execution_budget_for_profile(ExecutionProfile.AUTO_RANGE)

    assert default_budget.os_reserve_bytes == 2 * gib
    assert reserved_budget.os_reserve_bytes == 6 * gib
    assert reserved_budget.memory_limit_bytes < default_budget.memory_limit_bytes


def test_explicit_profile_memory_cap_remains_hard_when_default_would_be_larger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 64 * gib)
    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", "1024")

    budget = execution_budget_for_profile(ExecutionProfile.TRAINING_PREP)

    assert budget.memory_limit_bytes == gib
    assert budget.config_key == "HAUTE_TRAINING_MEMORY_LIMIT_MB"


def test_explicit_global_memory_cap_remains_hard_for_all_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 64 * gib)
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_LIMIT_MB", "768")

    for profile in ExecutionProfile:
        budget = execution_budget_for_profile(profile)
        assert budget.memory_limit_bytes == 768 * 1024 * 1024
        assert budget.config_key == "HAUTE_EXECUTION_MEMORY_LIMIT_MB"


def test_execution_admission_rejects_invalid_memory_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import admit_execution

    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_BYTES", "0")

    with pytest.raises(RuntimeError, match="HAUTE_SINK_MEMORY_LIMIT_BYTES"):
        admit_execution(operation="pipeline_sink", profile=ExecutionProfile.LAZY_SINK)


def test_default_execution_budget_is_adaptive_across_local_engine_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 16 * gib)

    heavy_profiles = {
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
    }
    for profile in heavy_profiles:
        budget = admission_mod.execution_budget_for_profile(profile)
        assert budget.budget_policy == "adaptive_local"
        assert budget.config_key == f"adaptive:{profile.value}"
        assert budget.available_ram_bytes == 16 * gib
        assert budget.memory_limit_bytes > admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[profile]

    preview_budget = admission_mod.execution_budget_for_profile(ExecutionProfile.PREVIEW_EAGER)
    assert preview_budget.budget_policy == "adaptive_local"
    assert (
        preview_budget.memory_limit_bytes
        > admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[ExecutionProfile.PREVIEW_EAGER]
    )

    deploy_live_budget = admission_mod.execution_budget_for_profile(ExecutionProfile.DEPLOY_LIVE)
    assert deploy_live_budget.budget_policy == "fixed_default"
    assert (
        deploy_live_budget.memory_limit_bytes
        == admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[ExecutionProfile.DEPLOY_LIVE]
    )


def test_explicit_memory_limit_env_still_overrides_adaptive_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 64 * 1024**3)
    monkeypatch.setenv("HAUTE_AUTO_RANGE_MEMORY_LIMIT_MB", "512")

    budget = admission_mod.execution_budget_for_profile(ExecutionProfile.AUTO_RANGE)

    assert budget.memory_limit_bytes == 512 * 1024 * 1024
    assert budget.config_key == "HAUTE_AUTO_RANGE_MEMORY_LIMIT_MB"
    assert budget.budget_policy == "explicit_env"
    assert budget.available_ram_bytes is None


def test_heavy_execution_admission_counts_in_flight_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 10 * gib)

    first = create_admitted_execution_context(
        operation="optimiser_setup_a",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        memory_sampler=lambda: 100,
    )
    try:
        with pytest.raises(ExecutionAdmissionError) as exc_info:
            create_admitted_execution_context(
                operation="optimiser_setup_b",
                profile=ExecutionProfile.OPTIMISER_SETUP,
                memory_sampler=lambda: 100,
            )
        assert exc_info.value.reason == "in_flight_memory_budget_exceeded"

        first.release_admission()

        second = create_admitted_execution_context(
            operation="optimiser_setup_b",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            memory_sampler=lambda: 100,
        )
        second.release_admission()
    finally:
        first.release_admission()


def test_preview_execution_admission_does_not_reserve_heavy_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 10 * gib)

    previews = [
        create_admitted_execution_context(
            operation=f"preview_{idx}",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_sampler=lambda: 100,
        )
        for idx in range(4)
    ]
    for context in previews:
        context.release_admission()


def test_fixed_memory_policy_keeps_legacy_profile_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 64 * 1024**3)
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_POLICY", "fixed")

    budget = admission_mod.execution_budget_for_profile(ExecutionProfile.AUTO_RANGE)

    assert (
        budget.memory_limit_bytes
        == admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[ExecutionProfile.AUTO_RANGE]
    )
    assert budget.config_key == "default:auto_range"
    assert budget.budget_policy == "fixed_default"
    assert budget.available_ram_bytes is None


def test_strict_server_memory_policy_keeps_legacy_profile_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 64 * 1024**3)
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_POLICY", "strict_server")

    budget = admission_mod.execution_budget_for_profile(ExecutionProfile.AUTO_RANGE)

    assert (
        budget.memory_limit_bytes
        == admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[ExecutionProfile.AUTO_RANGE]
    )
    assert budget.config_key == "default:auto_range"
    assert budget.budget_policy == "fixed_default"
    assert budget.available_ram_bytes is None


def test_adaptive_budget_still_respects_process_rss_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_execution_memory_env(monkeypatch)
    mib = 1024 * 1024
    gib = 1024 * mib
    monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 16 * gib)
    monkeypatch.setenv("HAUTE_AUTO_RANGE_PROCESS_RSS_LIMIT_MB", "2048")
    samples = iter([1500 * mib, 2100 * mib])

    context = create_admitted_execution_context(
        operation="frontier_auto_range",
        profile=ExecutionProfile.AUTO_RANGE,
        memory_sampler=lambda: next(samples),
    )

    assert context.admission is not None
    assert context.admission.budget_policy == "adaptive_local"
    assert context.rss_limit_bytes == 2048 * mib
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="over-process-cap")
    assert exc_info.value.reason == "process_rss_limit_exceeded"


def test_execution_context_checkpoint_raises_when_cancelled() -> None:
    token = ExecutionCancellationToken()
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        job_id="job-1",
        cancellation_token=token,
    )

    token.cancel()

    with pytest.raises(ExecutionCancelledError) as exc_info:
        context.checkpoint(label="before-node", node_id="node-1")

    assert exc_info.value.operation == "preview"
    assert exc_info.value.job_id == "job-1"


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


def test_preview_execution_metrics_identify_cache_miss_and_hit(tmp_path) -> None:
    from haute.executor import _preview_cache, execute_graph

    _preview_cache.invalidate()
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

    _preview_cache.invalidate()


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
        profile=ExecutionProfile.AUTO_RANGE,
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
        profile=ExecutionProfile.AUTO_RANGE,
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
        ProjectionPlan,
        ProjectionReason,
    )

    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    context.projection_plan = ProjectionPlan(
        needed_by_node={"train": None},
        edge_demands={("source", "train"): frozenset({"target"})},
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
                ("source", "train"): ProjectionReason(
                    rule="runtime_inferred_streaming",
                    message="runtime parent projection",
                )
            },
        ),
    )

    payload = context.metrics_payload(status="completed")

    assert payload["projection_plan_diagnostics"] == {
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
    ExecutionMetricsPayload.model_validate(payload)


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
        profile=ExecutionProfile.AUTO_RANGE,
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


def test_admitted_execution_context_uses_profile_specific_memory_limit(monkeypatch) -> None:
    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "512")

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 128 * 1024 * 1024,
    )

    assert context.memory_limit_bytes == 512 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.admitted
    assert context.admission.profile == ExecutionProfile.PREVIEW_EAGER
    assert context.admission.memory_limit_bytes == 512 * 1024 * 1024
    assert context.admission.rss_at_admission_bytes == 128 * 1024 * 1024
    assert context.memory_baseline_bytes == 128 * 1024 * 1024
    assert context.rss_limit_bytes == 640 * 1024 * 1024
    assert context.admission.rss_limit_bytes == 640 * 1024 * 1024
    assert context.admission.headroom_bytes == 512 * 1024 * 1024
    assert context.admission.config_key == "HAUTE_PREVIEW_MEMORY_LIMIT_MB"


def test_admitted_execution_context_allows_warm_process_above_operation_budget(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "512")
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    samples = iter([2 * gib, 2 * gib + 511 * mib, 2 * gib + 513 * mib])

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: next(samples),
    )

    assert context.memory_baseline_bytes == 2 * gib
    assert context.memory_limit_bytes == 512 * mib
    assert context.rss_limit_bytes == 2 * gib + 512 * mib
    context.checkpoint(label="within-operation-growth-budget")
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="over-operation-growth-budget")
    assert exc_info.value.limit_bytes == 512 * mib
    assert exc_info.value.baseline_rss_bytes == 2 * gib
    assert exc_info.value.rss_limit_bytes == 2 * gib + 512 * mib


def test_admitted_execution_context_runtime_failure_reports_process_rss_cap(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "512")
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB", "2304")
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    samples = iter([2 * gib, 2 * gib + 305 * mib])

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: next(samples),
    )

    assert context.rss_limit_bytes == 2304 * mib
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="over-process-cap")
    assert exc_info.value.reason == "process_rss_limit_exceeded"
    assert exc_info.value.limit_bytes == 512 * mib
    assert exc_info.value.rss_limit_bytes == 2304 * mib


def test_process_rss_cap_catches_cumulative_warm_process_ratcheting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process cap bounds total RSS even when each operation gets fresh headroom."""
    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "512")
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB", "1024")
    mib = 1024 * 1024
    samples = iter([900 * mib, 1030 * mib])

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: next(samples),
    )

    assert context.memory_baseline_bytes == 900 * mib
    assert context.memory_limit_bytes == 512 * mib
    assert context.rss_limit_bytes == 1024 * mib
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="cumulative-rss-ratchet")
    assert exc_info.value.reason == "process_rss_limit_exceeded"
    assert exc_info.value.rss_limit_bytes == 1024 * mib


def test_admitted_execution_context_rejects_when_process_rss_cap_exceeded(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "64")
    monkeypatch.setenv("HAUTE_SINK_PROCESS_RSS_LIMIT_MB", "128")

    with pytest.raises(ExecutionAdmissionError) as exc_info:
        create_admitted_execution_context(
            operation="pipeline_sink",
            profile=ExecutionProfile.LAZY_SINK,
            memory_sampler=lambda: 129 * 1024 * 1024,
        )

    assert exc_info.value.operation == "pipeline_sink"
    assert exc_info.value.profile == ExecutionProfile.LAZY_SINK
    assert exc_info.value.memory_limit_bytes == 64 * 1024 * 1024
    assert exc_info.value.rss_at_admission_bytes == 129 * 1024 * 1024
    assert exc_info.value.process_rss_limit_bytes == 128 * 1024 * 1024
    assert exc_info.value.to_payload() == {
        "error_code": "memory_limit",
        "operation": "pipeline_sink",
        "profile": "lazy_sink",
        "memory_limit_bytes": 64 * 1024 * 1024,
        "rss_at_admission_bytes": 129 * 1024 * 1024,
        "rss_limit_bytes": None,
        "process_rss_limit_bytes": 128 * 1024 * 1024,
        "headroom_bytes": -1 * 1024 * 1024,
        "reason": "process_rss_limit_exceeded",
    }


def test_execution_metrics_payload_includes_admission_metadata(monkeypatch) -> None:
    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "256")
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
        "config_key": "HAUTE_PREVIEW_MEMORY_LIMIT_MB",
        "budget_policy": "explicit_env",
        "available_ram_bytes": None,
        "os_reserve_bytes": None,
        "reason": "within_memory_budget",
    }
    validated = ExecutionMetricsPayload.model_validate(payload).model_dump(mode="json")
    assert validated["admission"]["budget_policy"] == "explicit_env"
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

    assert recorder.by_node_elapsed_ms() == {"a": 3.75}


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
                profile=ExecutionProfile.AUTO_RANGE,
                node_id=f"node-{index}",
            )
        )

    summary = recorder.summary(
        operation="auto_range",
        profile=ExecutionProfile.AUTO_RANGE,
        job_id="job-1",
    ).to_dict()

    assert summary["stage_count"] == 4
    assert summary["retained_stage_count"] == 2
    assert summary["truncated_stage_count"] == 2
    assert [stage["node_id"] for stage in summary["stages"]] == ["node-0", "node-1"]
    assert summary["stage_elapsed_ms"] == {"chunk": 10.0}


def test_background_job_registry_cancels_execution_token_for_superseded_job() -> None:
    from haute.routes._background_jobs import CancellableJobRegistry

    registry = CancellableJobRegistry()
    first_token, previous = registry.register_latest(("auto-range", "graph"), "job-1")
    assert previous is None

    context = ExecutionContext(
        operation="frontier_auto_range",
        profile=ExecutionProfile.AUTO_RANGE,
        job_id="job-1",
        cancellation_token=first_token.execution_token,
    )

    _second_token, previous = registry.register_latest(("auto-range", "graph"), "job-2")

    assert previous == "job-1"
    with pytest.raises(ExecutionCancelledError):
        context.checkpoint(label="before-heavy-stage")


def test_background_job_registry_uses_caller_execution_token() -> None:
    from haute.routes._background_jobs import CancellableJobRegistry

    registry = CancellableJobRegistry()
    supplied_token = ExecutionCancellationToken()

    token, previous = registry.register_latest(
        ("auto-range", "graph"),
        "job-1",
        execution_token=supplied_token,
    )

    assert previous is None
    assert token.execution_token is supplied_token
    registry.cancel("job-1")
    assert supplied_token.cancelled


def test_eager_graph_execution_records_collect_stages() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
                {
                    "id": "derived",
                    "data": {
                        "label": "derived",
                        "nodeType": NodeType.POLARS.value,
                        "config": {},
                    },
                },
            ],
            "edges": [make_edge("source", "derived").model_dump()],
        }
    )
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 1_000,
    )

    def build_node_fn(node, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1, 2]}).lazy(), True
        return (
            node.id,
            lambda df: df.with_columns((pl.col("a") + 1).alias("b")),
            False,
        )

    result = _execute_eager_core(
        graph,
        build_node_fn,
        target_node_id="derived",
        execution_context=context,
    )

    assert result.outputs["derived"]["b"].to_list() == [2, 3]
    metrics = context.metrics.snapshot()
    assert [metric.node_id for metric in metrics] == ["source", "derived"]
    assert {metric.name for metric in metrics} == {"eager_collect"}


def test_eager_graph_execution_does_not_swallow_memory_budget_failures() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    samples = iter([50, 50, 100])
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=90,
        memory_sampler=lambda: next(samples),
    )

    def build_node_fn(node, **_kwargs):
        return node.id, lambda: pl.DataFrame({"a": [1, 2]}).lazy(), True

    with pytest.raises(ExecutionMemoryLimitExceededError):
        _execute_eager_core(
            graph,
            build_node_fn,
            target_node_id="source",
            swallow_errors=True,
            execution_context=context,
        )


def test_eager_graph_execution_does_not_swallow_cancellation() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    context.cancel()

    def build_node_fn(node, **_kwargs):
        return node.id, lambda: pl.DataFrame({"a": [1, 2]}).lazy(), True

    with pytest.raises(ExecutionCancelledError):
        _execute_eager_core(
            graph,
            build_node_fn,
            target_node_id="source",
            swallow_errors=True,
            execution_context=context,
        )


def test_lazy_graph_execution_checks_cancellation_before_node_work() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(operation="sink", profile=ExecutionProfile.LAZY_SINK)
    context.cancel()

    def build_node_fn(_node, **_kwargs):
        raise AssertionError("cancelled execution should not build node functions")

    with pytest.raises(ExecutionCancelledError):
        _execute_lazy(graph, build_node_fn, execution_context=context)


def test_lazy_graph_execution_records_build_and_checkpoint_stages(tmp_path) -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
                {
                    "id": "mid",
                    "data": {
                        "label": "mid",
                        "nodeType": NodeType.POLARS.value,
                        "config": {},
                    },
                },
                {
                    "id": "left",
                    "data": {
                        "label": "left",
                        "nodeType": NodeType.POLARS.value,
                        "config": {},
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": NodeType.POLARS.value,
                        "config": {},
                    },
                },
            ],
            "edges": [
                make_edge("source", "mid").model_dump(),
                make_edge("mid", "left").model_dump(),
                make_edge("mid", "right").model_dump(),
            ],
        }
    )
    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1_000,
    )

    def build_node_fn(node, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1, 2]}).lazy(), True
        if node.id == "mid":
            return node.id, lambda df: df.with_columns((pl.col("a") + 1).alias("b")), False
        return node.id, lambda df: df.select("b"), False

    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        checkpoint_dir=tmp_path,
        execution_context=context,
    )

    assert outputs["left"].collect()["b"].to_list() == [2, 3]
    metrics = context.metrics.snapshot()
    assert [metric.node_id for metric in metrics if metric.name == "lazy_build"] == [
        "source",
        "mid",
        "left",
        "right",
    ]
    assert any(
        metric.name == "lazy_checkpoint_parquet" and metric.node_id == "mid" for metric in metrics
    )


def test_execute_sink_forwards_execution_context_to_lazy_executor(tmp_path) -> None:
    from haute.executor import execute_sink

    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {"path": str(output_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1_000,
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        return {"sink": pl.DataFrame({"a": [1]}).lazy()}, ["sink"], {}, {}

    with patch("haute.executor._execute_lazy", side_effect=fake_execute_lazy):
        result = execute_sink(graph, "sink", execution_context=context)

    assert result.status == "ok"
    assert captured["execution_context"] is context
    assert any(metric.name == "sink_write" for metric in context.metrics.snapshot())
    assert result.execution_metrics is not None
    assert result.execution_metrics.profile == ExecutionProfile.LAZY_SINK.value


@pytest.mark.asyncio
async def test_sink_route_creates_lazy_sink_execution_context(monkeypatch, tmp_path) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import SinkRequest, SinkResponse

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 128 * 1024 * 1024,
    )
    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {"path": str(output_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [],
        }
    )
    captured = {}

    def fake_execute_sink(*_args, **kwargs):
        captured.update(kwargs)
        return SinkResponse(status="ok")

    with patch.object(pipeline_route, "execute_sink", side_effect=fake_execute_sink):
        response = await pipeline_route.execute_sink_node(
            SinkRequest(graph=graph, node_id="sink", source="batch")
        )

    assert response.status == "ok"
    assert captured["execution_context"].profile == ExecutionProfile.LAZY_SINK
    assert captured["execution_context"].memory_limit_bytes == 512 * 1024 * 1024
    assert captured["execution_context"].admission is not None
    assert captured["execution_context"].admission.rss_at_admission_bytes == 128 * 1024 * 1024


@pytest.mark.asyncio
async def test_sink_route_allows_sink_without_configured_output_path(monkeypatch, tmp_path) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import SinkRequest, SinkResponse

    monkeypatch.chdir(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    def fake_execute_sink(*_args, **_kwargs):
        return SinkResponse(status="ok")

    with (
        patch.object(
            pipeline_route,
            "resolve_sink_output_path",
            side_effect=AssertionError("empty sink paths should not be resolved"),
        ),
        patch.object(pipeline_route, "execute_sink", side_effect=fake_execute_sink),
    ):
        response = await pipeline_route.execute_sink_node(
            SinkRequest(graph=graph, node_id="sink", source="batch")
        )

    assert response.status == "ok"


@pytest.mark.asyncio
async def test_get_pipeline_falls_back_after_indexed_and_scanned_parse_failures(
    monkeypatch,
    tmp_path,
) -> None:
    from haute.routes import pipeline as pipeline_route

    indexed = tmp_path / "indexed.py"
    scanned_bad = tmp_path / "scanned_bad.py"
    scanned_match = tmp_path / "scanned_match.py"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
            "pipeline_name": "rating",
        }
    )
    parsed_paths: list[str] = []

    def parse(path):
        parsed_paths.append(path.name)
        if path in {indexed, scanned_bad}:
            raise RuntimeError(f"{path.name} is temporarily unparseable")
        return graph

    monkeypatch.setattr(pipeline_route, "lookup_pipeline_by_name", lambda _name: indexed)
    monkeypatch.setattr(pipeline_route, "discover_pipelines", lambda: [scanned_bad, scanned_match])
    monkeypatch.setattr(pipeline_route, "parse_pipeline_to_graph", parse)

    result = await pipeline_route.get_pipeline("rating")

    assert result is graph
    assert parsed_paths == ["indexed.py", "scanned_bad.py", "scanned_match.py"]


@pytest.mark.asyncio
async def test_get_first_pipeline_keeps_first_empty_graph_when_later_files_fail(
    monkeypatch,
    tmp_path,
) -> None:
    from haute.routes import pipeline as pipeline_route

    monkeypatch.chdir(tmp_path)
    empty_first = tmp_path / "empty_first.py"
    empty_second = tmp_path / "empty_second.py"
    broken = tmp_path / "broken.py"
    first_graph = make_graph({"nodes": [], "edges": [], "pipeline_name": "empty_first"})
    second_graph = make_graph({"nodes": [], "edges": [], "pipeline_name": "empty_second"})

    def parse(path):
        if path == empty_first:
            return first_graph
        if path == empty_second:
            return second_graph
        raise RuntimeError("broken pipeline")

    monkeypatch.setattr(
        pipeline_route,
        "discover_pipelines",
        lambda: [empty_first, empty_second, broken],
    )
    monkeypatch.setattr(pipeline_route, "parse_pipeline_to_graph", parse)

    result = await pipeline_route.get_first_pipeline()

    assert result is first_graph
    assert result.source_file == "empty_first.py"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_status", "expected_detail"),
    [
        (
            "Target node 'missing' not found in graph",
            404,
            "Target node 'missing' not found in graph",
        ),
        (
            "unexpected trace failure",
            500,
            "Operation failed. Check the server logs for details.",
        ),
    ],
)
async def test_trace_route_maps_target_not_found_and_unknown_value_errors(
    monkeypatch,
    message: str,
    expected_status: int,
    expected_detail: str,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import TraceRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    def raise_value_error(*_args, **_kwargs):
        raise ValueError(message)

    monkeypatch.setattr(pipeline_route, "execute_trace", raise_value_error)

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.trace_row(
            TraceRequest(graph=graph, row_index=0, target_node_id="source")
        )

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.detail == expected_detail


@pytest.mark.asyncio
async def test_trace_route_maps_contract_mismatch_to_http_422(monkeypatch) -> None:
    from fastapi import HTTPException

    from haute.errors import ContractMismatchError
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import TraceRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    def raise_contract_mismatch(*_args, **_kwargs):
        raise ContractMismatchError("bad contract", node_id="source")

    monkeypatch.setattr(pipeline_route, "execute_trace", raise_contract_mismatch)

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.trace_row(TraceRequest(graph=graph, row_index=0))

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "bad contract (node_id=source)"


@pytest.mark.asyncio
async def test_read_json_file_maps_unexpected_read_failure_to_internal_error(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import ReadJsonRequest

    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pipeline_route, "_get_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        pipeline_route,
        "read_user_text",
        lambda _path: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.read_json_file(ReadJsonRequest(path="payload.json"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Operation failed. Check the server logs for details."


@pytest.mark.asyncio
async def test_preview_route_creates_admitted_preview_execution_context(monkeypatch) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import ColumnInfo, NodeResult, PreviewNodeRequest

    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "384")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 96 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    captured = {}

    def fake_execute_graph(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "source": NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                columns=[ColumnInfo(name="a", dtype="Int64")],
                available_columns=[ColumnInfo(name="a", dtype="Int64")],
                preview=[{"a": 1}],
                preview_columns=["a"],
                preview_row_count=1,
                preview_row_limit=100,
            )
        }

    with patch.object(pipeline_route, "execute_graph", side_effect=fake_execute_graph):
        response = await pipeline_route.preview_node(
            PreviewNodeRequest(graph=graph, node_id="source")
        )

    assert response.status == "ok"
    assert captured["execution_context"].profile == ExecutionProfile.PREVIEW_EAGER
    assert captured["execution_context"].memory_limit_bytes == 384 * 1024 * 1024
    assert response.execution_metrics is not None
    assert response.execution_metrics.admission is not None
    assert response.execution_metrics.admission.profile == "preview_eager"
    assert response.execution_metrics.admission.memory_limit_bytes == 384 * 1024 * 1024


@pytest.mark.asyncio
async def test_preview_route_admits_when_warm_process_rss_exceeds_operation_budget(
    monkeypatch,
) -> None:
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import ColumnInfo, NodeResult, PreviewNodeRequest

    gib = 1024 * 1024 * 1024
    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "384")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 2 * gib,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    captured = {}

    def fake_execute_graph(*_args, **kwargs):
        captured["execution_context"] = kwargs["execution_context"]
        return {
            "source": NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                columns=[ColumnInfo(name="a", dtype="int")],
                available_columns=[ColumnInfo(name="a", dtype="int")],
                preview=[{"a": 1}],
                preview_columns=["a"],
                preview_row_count=1,
                preview_row_limit=100,
            )
        }

    with patch.object(pipeline_route, "execute_graph", side_effect=fake_execute_graph):
        response = await pipeline_route.preview_node(
            PreviewNodeRequest(graph=graph, node_id="source")
        )

    assert response.status == "ok"
    context = captured["execution_context"]
    assert context.memory_baseline_bytes == 2 * gib
    assert context.rss_limit_bytes == 2 * gib + 384 * 1024 * 1024


@pytest.mark.asyncio
async def test_preview_route_maps_admission_failure_to_http_507(monkeypatch) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import PreviewNodeRequest

    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "64")
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB", "64")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 65 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.preview_node(PreviewNodeRequest(graph=graph, node_id="source"))

    assert exc_info.value.status_code == 507
    assert exc_info.value.detail["error_code"] == "memory_limit"
    assert exc_info.value.detail["profile"] == "preview_eager"
    assert exc_info.value.detail["reason"] == "process_rss_limit_exceeded"


@pytest.mark.asyncio
async def test_preview_route_cancels_execution_context_on_timeout(monkeypatch) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import NodeResult, PreviewNodeRequest

    monkeypatch.setenv("HAUTE_PREVIEW_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    monkeypatch.setattr(pipeline_route, "_PREVIEW_TIMEOUT", 0.05)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    started = threading.Event()
    cancel_seen = threading.Event()

    def slow_execute_graph(*_args, **kwargs):
        context = kwargs["execution_context"]
        target = kwargs["target_node_id"]
        started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not context.cancellation_token.cancelled:
            time.sleep(0.005)
        if context.cancellation_token.cancelled:
            cancel_seen.set()
        return {
            target: NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                preview=[{"a": 1}],
            )
        }

    with patch.object(pipeline_route, "execute_graph", side_effect=slow_execute_graph):
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.preview_node(PreviewNodeRequest(graph=graph, node_id="source"))

    assert exc_info.value.status_code == 504
    assert started.wait(2)
    assert cancel_seen.wait(2)


@pytest.mark.asyncio
async def test_preview_route_releases_admission_after_timed_out_worker_finishes(
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import NodeResult, PreviewNodeRequest

    monkeypatch.setattr(pipeline_route, "_PREVIEW_TIMEOUT", 0.05)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    release_calls = 0
    release_lock = threading.Lock()
    worker_started = threading.Event()
    release_worker = threading.Event()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    preview_context = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission_release=release_admission,
    )

    def create_context(*_args, **_kwargs) -> ExecutionContext:
        return preview_context

    def slow_execute_graph(*_args, **kwargs):
        target = kwargs["target_node_id"]
        worker_started.set()
        assert release_worker.wait(2), "preview worker was not released"
        return {
            target: NodeResult(
                status="ok",
                row_count=1,
                column_count=1,
                preview=[{"a": 1}],
            )
        }

    monkeypatch.setattr(
        pipeline_route,
        "create_admitted_execution_context",
        create_context,
    )
    monkeypatch.setattr(pipeline_route, "execute_graph", slow_execute_graph)

    try:
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.preview_node(PreviewNodeRequest(graph=graph, node_id="source"))

        assert exc_info.value.status_code == 504
        assert worker_started.wait(2)
        with release_lock:
            assert release_calls == 0

        release_worker.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with release_lock:
                if release_calls == 1:
                    break
            await asyncio.sleep(0.005)
    finally:
        release_worker.set()

    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_preview_route_releases_admission_when_timeout_task_already_finished(
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.routes._timeouts import BlockingWorkTimeoutError
    from haute.schemas import PreviewNodeRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    release_calls = 0
    release_lock = threading.Lock()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    preview_context = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission_release=release_admission,
    )

    async def raise_finished_timeout(*_args, **_kwargs):
        background_task = asyncio.get_running_loop().create_future()
        background_task.set_result(None)
        raise BlockingWorkTimeoutError("pipeline_preview", 0.01, background_task)

    monkeypatch.setattr(
        pipeline_route,
        "create_admitted_execution_context",
        lambda *_args, **_kwargs: preview_context,
    )
    monkeypatch.setattr(
        pipeline_route,
        "run_blocking_with_response_timeout",
        raise_finished_timeout,
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.preview_node(PreviewNodeRequest(graph=graph, node_id="source"))

    assert exc_info.value.status_code == 504
    with release_lock:
        assert release_calls == 0

    await asyncio.sleep(0)

    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_preview_route_maps_timeout_without_execution_context_to_http_504(
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.routes._timeouts import BlockingWorkTimeoutError
    from haute.schemas import PreviewNodeRequest

    class TimeoutBeforeWorker:
        async def run_latest(self, *_args, **_kwargs):
            background_task = asyncio.get_running_loop().create_future()
            raise BlockingWorkTimeoutError("pipeline_preview", 0.01, background_task)

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    monkeypatch.setattr(pipeline_route, "_preview_supersession", TimeoutBeforeWorker())

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.preview_node(PreviewNodeRequest(graph=graph, node_id="source"))

    assert exc_info.value.status_code == 504
    assert "Preview execution timed out" in exc_info.value.detail


@pytest.mark.asyncio
async def test_preview_route_returns_error_response_for_contract_mismatch(monkeypatch) -> None:
    from haute.errors import ContractMismatchError
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import PreviewNodeRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    def raise_contract_mismatch(*_args, **_kwargs):
        raise ContractMismatchError("bad preview contract", node_id="source")

    monkeypatch.setattr(pipeline_route, "execute_graph", raise_contract_mismatch)

    response = await pipeline_route.preview_node(PreviewNodeRequest(graph=graph, node_id="source"))

    assert response.node_id == "source"
    assert response.status == "error"
    assert response.error == "bad preview contract (node_id=source)"


@pytest.mark.asyncio
async def test_sink_route_maps_admission_failure_to_http_507(monkeypatch, tmp_path) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import SinkRequest

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "64")
    monkeypatch.setenv("HAUTE_SINK_PROCESS_RSS_LIMIT_MB", "64")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 65 * 1024 * 1024,
    )
    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {"path": str(output_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.execute_sink_node(
            SinkRequest(graph=graph, node_id="sink", source="batch")
        )

    assert exc_info.value.status_code == 507
    assert exc_info.value.detail["error_code"] == "memory_limit"
    assert exc_info.value.detail["profile"] == "lazy_sink"
    assert exc_info.value.detail["reason"] == "process_rss_limit_exceeded"


@pytest.mark.asyncio
async def test_sink_route_maps_execution_memory_budget_failure_to_http_507(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import SinkRequest

    monkeypatch.chdir(tmp_path)
    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {"path": str(output_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [],
        }
    )
    memory_error = ExecutionMemoryLimitExceededError(
        "pipeline_sink",
        rss_bytes=150,
        limit_bytes=100,
        baseline_rss_bytes=0,
        rss_limit_bytes=100,
        reason="process_rss_limit_exceeded",
    )

    def raise_memory_budget(*_args, **_kwargs):
        raise memory_error

    monkeypatch.setattr(pipeline_route, "execute_sink", raise_memory_budget)

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.execute_sink_node(
            SinkRequest(graph=graph, node_id="sink", source="batch")
        )

    assert exc_info.value.status_code == 507
    assert exc_info.value.detail["error_code"] == "memory_limit"
    assert exc_info.value.detail["operation"] == "pipeline_sink"
    assert exc_info.value.detail["reason"] == "process_rss_limit_exceeded"


@pytest.mark.asyncio
async def test_sink_route_maps_bounded_streaming_failure_to_http_422(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.errors import BoundedMemoryUnsupportedError
    from haute.routes import pipeline as pipeline_route
    from haute.schemas import SinkRequest

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {"path": str(output_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [],
        }
    )

    def unsupported_sink(*_args, **_kwargs):
        raise BoundedMemoryUnsupportedError("Bounded streaming sink failed", path="sink")

    with patch.object(pipeline_route, "execute_sink", side_effect=unsupported_sink):
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.execute_sink_node(
                SinkRequest(graph=graph, node_id="sink", source="batch")
            )

    assert exc_info.value.status_code == 422
    assert "Bounded streaming sink failed" in exc_info.value.detail


@pytest.mark.asyncio
async def test_preview_route_maps_execution_memory_budget_failure_to_http_507(
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import PreviewNodeRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    memory_error = ExecutionMemoryLimitExceededError(
        "pipeline_preview",
        rss_bytes=150,
        limit_bytes=100,
        baseline_rss_bytes=0,
        rss_limit_bytes=100,
        reason="process_rss_limit_exceeded",
    )

    def raise_memory_budget(*_args, **_kwargs):
        raise memory_error

    monkeypatch.setattr(pipeline_route, "execute_graph", raise_memory_budget)

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.preview_node(PreviewNodeRequest(graph=graph, node_id="source"))

    assert exc_info.value.status_code == 507
    assert exc_info.value.detail["error_code"] == "memory_limit"
    assert exc_info.value.detail["operation"] == "pipeline_preview"
    assert exc_info.value.detail["reason"] == "process_rss_limit_exceeded"


@pytest.mark.asyncio
async def test_sink_route_maps_timeout_before_execution_context_to_http_504(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import SinkRequest

    monkeypatch.chdir(tmp_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )

    def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("execution slot timed out")

    monkeypatch.setattr(pipeline_route, "create_admitted_execution_context", raise_timeout)

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.execute_sink_node(
            SinkRequest(graph=graph, node_id="sink", source="batch")
        )

    assert exc_info.value.status_code == 504
    assert "Sink execution timed out" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sink_route_releases_admission_after_timeout_background_task_finishes(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.routes._timeouts import BlockingWorkTimeoutError
    from haute.schemas import SinkRequest

    monkeypatch.chdir(tmp_path)
    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {"path": str(output_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [],
        }
    )
    release_calls = 0
    release_lock = threading.Lock()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    sink_context = ExecutionContext(
        operation="pipeline_sink",
        profile=ExecutionProfile.LAZY_SINK,
        admission_release=release_admission,
    )
    background_task: asyncio.Future[None] | None = None

    async def raise_timeout(*_args, **_kwargs):
        nonlocal background_task
        background_task = asyncio.get_running_loop().create_future()
        raise BlockingWorkTimeoutError("pipeline_sink", 0.01, background_task)

    monkeypatch.setattr(
        pipeline_route,
        "create_admitted_execution_context",
        lambda *_args, **_kwargs: sink_context,
    )
    monkeypatch.setattr(
        pipeline_route,
        "run_blocking_with_response_timeout",
        raise_timeout,
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.execute_sink_node(
            SinkRequest(graph=graph, node_id="sink", source="batch")
        )

    assert exc_info.value.status_code == 504
    with release_lock:
        assert release_calls == 0

    assert background_task is not None
    background_task.set_result(None)
    await asyncio.sleep(0)

    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_sink_route_releases_admission_when_timeout_task_already_finished(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.routes._timeouts import BlockingWorkTimeoutError
    from haute.schemas import SinkRequest

    monkeypatch.chdir(tmp_path)
    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {"path": str(output_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [],
        }
    )
    release_calls = 0
    release_lock = threading.Lock()

    def release_admission() -> None:
        nonlocal release_calls
        with release_lock:
            release_calls += 1

    sink_context = ExecutionContext(
        operation="pipeline_sink",
        profile=ExecutionProfile.LAZY_SINK,
        admission_release=release_admission,
    )

    async def raise_finished_timeout(*_args, **_kwargs):
        background_task = asyncio.get_running_loop().create_future()
        background_task.set_result(None)
        raise BlockingWorkTimeoutError("pipeline_sink", 0.01, background_task)

    monkeypatch.setattr(
        pipeline_route,
        "create_admitted_execution_context",
        lambda *_args, **_kwargs: sink_context,
    )
    monkeypatch.setattr(
        pipeline_route,
        "run_blocking_with_response_timeout",
        raise_finished_timeout,
    )

    with pytest.raises(HTTPException) as exc_info:
        await pipeline_route.execute_sink_node(
            SinkRequest(graph=graph, node_id="sink", source="batch")
        )

    assert exc_info.value.status_code == 504
    with release_lock:
        assert release_calls == 0

    await asyncio.sleep(0)

    with release_lock:
        assert release_calls == 1


@pytest.mark.asyncio
async def test_sink_route_cancels_execution_context_on_timeout(monkeypatch, tmp_path) -> None:
    from fastapi import HTTPException

    from haute.routes import pipeline as pipeline_route
    from haute.schemas import SinkRequest, SinkResponse

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    monkeypatch.setattr(pipeline_route, "_SINK_TIMEOUT", 0.05)
    output_path = tmp_path / "sink.parquet"
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": NodeType.DATA_SINK.value,
                        "config": {"path": str(output_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [],
        }
    )
    started = threading.Event()
    cancel_seen = threading.Event()

    def slow_execute_sink(*_args, **kwargs):
        context = kwargs["execution_context"]
        started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not context.cancellation_token.cancelled:
            time.sleep(0.005)
        if context.cancellation_token.cancelled:
            cancel_seen.set()
        return SinkResponse(status="ok")

    with patch.object(pipeline_route, "execute_sink", side_effect=slow_execute_sink):
        with pytest.raises(HTTPException) as exc_info:
            await pipeline_route.execute_sink_node(
                SinkRequest(graph=graph, node_id="sink", source="batch")
            )

    assert exc_info.value.status_code == 504
    assert started.wait(2)
    assert cancel_seen.wait(2)


def test_optimiser_execute_pipeline_forwards_execution_context(tmp_path) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserSolveRequest(graph=graph, node_id="opt")
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running"})
    context = ExecutionContext(
        operation="optimiser",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        job_id=job_id,
        memory_sampler=lambda: 1_000,
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        return {"opt": pl.DataFrame({"a": [1]}).lazy()}, ["opt"], {}, {}

    with (
        patch("haute.routes._optimiser_service.execute_lazy_graph", side_effect=fake_execute_lazy),
        patch("haute.executor._resolve_batch_scenario", return_value="batch"),
        patch("haute.executor._compile_preamble", return_value={}),
    ):
        service._execute_pipeline(
            body,
            job_id,
            tmp_path,
            execution_context=context,
        )

    assert captured["execution_context"] is context
    stored_metrics = store.require_job(job_id)["execution_metrics"]
    assert stored_metrics["operation"] == "optimiser"
    assert stored_metrics["job_id"] == job_id


def test_optimiser_auto_range_entry_points_create_admitted_contexts(monkeypatch) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import (
        OptimiserFrontierAutoRangeRequest,
        OptimiserFrontierAutoRangeResponse,
    )

    monkeypatch.setenv("HAUTE_AUTO_RANGE_MEMORY_LIMIT_MB", "320")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 7_000)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "profit", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    service = OptimiserSolveService(JobStore())
    prepared = {
        "node": graph.node_map["opt"],
        "config": {"objective": "profit", "constraints": {}},
        "mode": "online",
        "chunk_size": 10,
        "partition_count": 2,
        "timeout": 30,
        "required_columns_by_node": None,
        "streaming_plan": None,
    }
    response = OptimiserFrontierAutoRangeResponse(status="ok", ranges={})

    with (
        patch.object(service, "_prepare_frontier_auto_range", return_value=prepared),
        patch.object(service, "_run_frontier_auto_range_job", return_value=response) as run_job,
    ):
        assert service.estimate_frontier_auto_range(body).status == "ok"

    sync_context = run_job.call_args.kwargs["execution_context"]
    assert sync_context.profile == ExecutionProfile.AUTO_RANGE
    assert sync_context.memory_limit_bytes == 320 * 1024 * 1024
    assert sync_context.admission is not None

    with (
        patch.object(service, "_prepare_frontier_auto_range", return_value=prepared),
        patch.object(service, "_launch_frontier_auto_range_background") as launch,
    ):
        started = service.start_frontier_auto_range(body)

    assert started.status == "started"
    background_context = launch.call_args.kwargs["execution_context"]
    assert background_context.profile == ExecutionProfile.AUTO_RANGE
    assert background_context.memory_limit_bytes == 320 * 1024 * 1024
    assert background_context.admission is not None


def test_train_execute_and_sink_forwards_execution_context(tmp_path) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._train_service import TrainService
    from haute.schemas import TrainRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": NodeType.MODELLING.value,
                        "config": {},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = TrainRequest(graph=graph, node_id="model")
    service = TrainService(JobStore())
    job_id = service._store.create_job({"status": "running"})
    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id=job_id,
        memory_sampler=lambda: 1_000,
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        return {"model": pl.DataFrame({"target": [1.0]}).lazy()}, ["model"], {}, {}

    with patch("haute.routes._train_service.execute_lazy_graph", side_effect=fake_execute_lazy):
        tmp_parquet = service._execute_and_sink(
            body,
            preamble_ns=None,
            row_limit=None,
            job_id=job_id,
            execution_context=context,
        )

    assert captured["execution_context"] is context
    assert any(metric.name == "training_sink_write" for metric in context.metrics.snapshot())
    stored_metrics = service._store.require_job(job_id)["execution_metrics"]
    assert stored_metrics["operation"] == "training"
    assert stored_metrics["job_id"] == job_id
    assert pl.read_parquet(tmp_parquet)["target"].to_list() == [1.0]


def test_deploy_score_graph_forwards_execution_context_to_lazy_executor() -> None:
    from haute.deploy._scorer import score_graph

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_LIVE,
        memory_sampler=lambda: 1_000,
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        return {"output": pl.DataFrame({"score": [0.25]}).lazy()}, ["output"], {}, {}

    with patch("haute.deploy._scorer.execute_lazy_graph", side_effect=fake_execute_lazy):
        result = score_graph(
            graph,
            pl.DataFrame({"feature": [1]}),
            input_node_ids=[],
            output_node_id="output",
            execution_context=context,
        )

    assert result["score"].to_list() == [0.25]
    assert captured["execution_context"] is context
    assert captured["source"] == "live"
    assert any(metric.name == "deploy_collect" for metric in context.metrics.snapshot())


def test_deploy_batch_graph_routing_stays_live_for_source_switch(tmp_path) -> None:
    from haute.deploy._scorer import score_graph

    batch_path = tmp_path / "batch.parquet"
    pl.DataFrame({"origin": ["batch"], "value": [999]}).write_parquet(batch_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "live_src",
                    "data": {
                        "label": "live_src",
                        "nodeType": NodeType.API_INPUT.value,
                        "config": {"path": ""},
                    },
                },
                {
                    "id": "batch_src",
                    "data": {
                        "label": "batch_src",
                        "nodeType": NodeType.DATA_SOURCE.value,
                        "config": {"path": "batch.parquet"},
                    },
                },
                {
                    "id": "switch",
                    "data": {
                        "label": "switch",
                        "nodeType": NodeType.LIVE_SWITCH.value,
                        "config": {
                            "input_scenario_map": {
                                "live_src": "live",
                                "batch_src": ExecutionProfile.DEPLOY_BATCH.value,
                            }
                        },
                    },
                },
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config(["origin", "value"]),
                    },
                },
            ],
            "edges": [
                make_edge("live_src", "switch").model_dump(),
                make_edge("batch_src", "switch").model_dump(),
                make_edge("switch", "output").model_dump(),
            ],
        }
    )
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_BATCH,
        memory_sampler=lambda: 1_000,
    )

    result = score_graph(
        graph,
        pl.DataFrame({"origin": ["live", "live"], "value": [1, 2]}),
        input_node_ids=["live_src"],
        output_node_id="output",
        artifact_paths={"batch_src__batch.parquet": str(batch_path)},
        execution_context=context,
    )

    assert result["origin"].to_list() == ["live", "live"]
    assert result["value"].to_list() == [1, 2]


def test_deploy_score_graph_final_collect_uses_streaming_engine() -> None:
    from haute.deploy._scorer import score_graph

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_LIVE,
        memory_sampler=lambda: 1_000,
    )

    class CollectingLazy:
        collect_kwargs = None

        def collect(self, **kwargs):
            self.collect_kwargs = kwargs
            return pl.DataFrame({"score": [0.25]})

    output_lf = CollectingLazy()

    def fake_execute_lazy(*_args, **kwargs):
        assert kwargs["execution_context"] is context
        return {"output": output_lf}, ["output"], {}, {}

    with patch("haute.deploy._scorer.execute_lazy_graph", side_effect=fake_execute_lazy):
        result = score_graph(
            graph,
            pl.DataFrame({"feature": [1]}),
            input_node_ids=[],
            output_node_id="output",
            execution_context=context,
        )

    assert result["score"].to_list() == [0.25]
    assert output_lf.collect_kwargs == {"engine": "streaming"}
    assert any(metric.name == "deploy_collect" for metric in context.metrics.snapshot())


def test_deploy_score_graph_final_collect_preserves_execution_context_memory_error() -> None:
    from haute.deploy._scorer import score_graph

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_LIVE,
        job_id="job-1",
        memory_sampler=lambda: 1_000,
    )
    memory_error = ExecutionMemoryLimitExceededError(
        "deploy",
        job_id="job-1",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    class FailingLazy:
        collect_kwargs = None

        def collect(self, **kwargs):
            self.collect_kwargs = kwargs
            raise memory_error

    output_lf = FailingLazy()

    def fake_execute_lazy(*_args, **_kwargs):
        return {"output": output_lf}, ["output"], {}, {}

    with patch("haute.deploy._scorer.execute_lazy_graph", side_effect=fake_execute_lazy):
        with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
            score_graph(
                graph,
                pl.DataFrame({"feature": [1]}),
                input_node_ids=[],
                output_node_id="output",
                execution_context=context,
            )

    assert exc_info.value is memory_error
    assert output_lf.collect_kwargs == {"engine": "streaming"}
    metrics = context.metrics.snapshot()
    assert [metric.name for metric in metrics] == ["deploy_collect"]
    assert metrics[0].node_id == "output"


def test_deploy_score_graph_creates_admitted_context_when_omitted(monkeypatch) -> None:
    from haute.deploy._scorer import score_graph

    monkeypatch.setenv("HAUTE_DEPLOY_LIVE_MEMORY_LIMIT_MB", "96")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 13_000)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "output",
                    "data": {
                        "label": "output",
                        "nodeType": NodeType.OUTPUT.value,
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [],
        }
    )
    captured = {}

    def fake_execute_lazy(*_args, **kwargs):
        captured.update(kwargs)
        return {"output": pl.DataFrame({"score": [0.25]}).lazy()}, ["output"], {}, {}

    with patch("haute.deploy._scorer.execute_lazy_graph", side_effect=fake_execute_lazy):
        result = score_graph(
            graph,
            pl.DataFrame({"feature": [1]}),
            input_node_ids=[],
            output_node_id="output",
        )

    assert result["score"].to_list() == [0.25]
    context = captured["execution_context"]
    assert context.profile == ExecutionProfile.DEPLOY_LIVE
    assert context.memory_limit_bytes == 96 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.rss_at_admission_bytes == 13_000


def test_admit_deploy_execution_rejects_negative_row_count() -> None:
    from haute.deploy._scorer import admit_deploy_execution

    with pytest.raises(ValueError, match="row_count must be non-negative"):
        admit_deploy_execution(operation="deploy_quote", row_count=-1)


def test_optimiser_start_creates_admitted_setup_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_MEMORY_LIMIT_MB", "768")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 128 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserSolveRequest(graph=graph, node_id="opt")
    scored_lf = pl.LazyFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
            "volume": [1.0],
        }
    )
    service = OptimiserSolveService(JobStore())
    captured = {}

    def fake_execute_pipeline(*_args, **kwargs):
        captured["execution_context"] = kwargs["execution_context"]
        return {"opt": scored_lf}

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", side_effect=fake_execute_pipeline),
        patch.object(service, "_validate_and_project", return_value=([], scored_lf)) as validate,
        patch.object(service, "_extract_factors", return_value=None) as extract,
        patch.object(service, "_build_grid", return_value=object()) as build,
        patch.object(service, "_launch_background") as launch,
    ):
        response = service.start(body)

    assert response.status == "started"
    context = captured["execution_context"]
    assert context.profile == ExecutionProfile.OPTIMISER_SETUP
    assert context.memory_limit_bytes == 768 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.rss_at_admission_bytes == 128 * 1024 * 1024
    validate.assert_called_once()
    assert validate.call_args.kwargs["execution_context"] is context
    extract.assert_called_once()
    assert extract.call_args.kwargs["execution_context"] is context
    build.assert_called_once()
    assert build.call_args.kwargs["execution_context"] is context
    ctx_arg = launch.call_args.args[0]
    assert ctx_arg.execution_context is context
    assert ctx_arg.registration_already_active is True


def test_optimiser_cancel_during_setup_prevents_worker_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserSolveRequest(graph=graph, node_id="opt")
    scored_lf = pl.LazyFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
        }
    )
    store = JobStore()
    service = OptimiserSolveService(store)

    def cancel_during_grid(*_args, **_kwargs):
        job_id = next(iter(store.jobs))
        service.cancel_solve(job_id)
        return object()

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", return_value={"opt": scored_lf}),
        patch.object(service, "_validate_and_project", return_value=([], scored_lf)),
        patch.object(service, "_extract_factors", return_value=None),
        patch.object(service, "_build_grid", side_effect=cancel_during_grid),
        patch.object(service, "_launch_background") as launch,
    ):
        response = service.start(body)

    assert response.status == "started"
    launch.assert_not_called()
    job = next(iter(store.jobs.values()))
    assert job["status"] == "cancelled"
    assert job["terminal_reason"] == "cancelled"


def test_optimiser_start_maps_admission_failure_to_http_507(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_MEMORY_LIMIT_MB", "512")
    monkeypatch.setenv("HAUTE_OPTIMISER_PROCESS_RSS_LIMIT_MB", "64")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 65 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )

    store = JobStore()
    service = OptimiserSolveService(store)

    with patch.object(service, "_execute_pipeline") as execute_pipeline:
        with patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread):
            response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))

    assert response.status == "started"
    execute_pipeline.assert_not_called()
    job = next(iter(store.jobs.values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    assert job["error_detail"]["error_code"] == "memory_limit"
    assert job["error_detail"]["profile"] == "optimiser_setup"
    assert job["error_detail"]["reason"] == "process_rss_limit_exceeded"
    assert "process_rss_limit_exceeded" in job["message"]


def test_optimiser_start_maps_runtime_memory_failure_to_http_507(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_MEMORY_LIMIT_BYTES", "512")
    samples = iter([1, 1, 600])
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: next(samples))
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    service = OptimiserSolveService(JobStore())
    memory_error = ExecutionMemoryLimitExceededError(
        "optimiser_solve",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", side_effect=memory_error),
    ):
        response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))

    assert response.status == "started"
    job = next(iter(service._store.jobs.values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    assert job["error_detail"]["error_code"] == "memory_limit"
    assert job["error_detail"]["operation"] == "optimiser_solve"
    assert job["error_detail"]["reason"] == "rss_exceeds_memory_limit"


def test_optimiser_start_records_setup_stage_metrics_when_memory_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_MEMORY_LIMIT_BYTES", "512")
    samples = iter([1, 1, 600])
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: next(samples))
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    scored_lf = pl.LazyFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
        }
    )
    store = JobStore()
    service = OptimiserSolveService(store)

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", return_value={"opt": scored_lf}),
    ):
        response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))

    assert response.status == "started"
    job = next(iter(store.jobs.values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    metrics = job["execution_metrics"]
    assert metrics["status"] == "memory_limited"
    assert metrics["terminal_reason"] == "memory_limited"
    assert "optimiser_validate_and_project" in metrics["stage_elapsed_ms"]


def test_optimiser_start_preserves_typed_memory_http_exception_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserSolveRequest

    monkeypatch.setenv("HAUTE_OPTIMISER_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    scored_lf = pl.LazyFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
        }
    )
    store = JobStore()
    service = OptimiserSolveService(store)
    memory_payload = {"error_code": "memory_limit", "reason": "rss_exceeds_memory_limit"}

    with (
        patch("haute.routes._optimiser_service.threading.Thread", _ImmediateThread),
        patch.object(service, "_execute_pipeline", return_value={"opt": scored_lf}),
        patch.object(
            service,
            "_validate_and_project",
            side_effect=HTTPException(status_code=507, detail=memory_payload),
        ),
    ):
        response = service.start(OptimiserSolveRequest(graph=graph, node_id="opt"))

    assert response.status == "started"
    job = next(iter(store.jobs.values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["http_status_code"] == 507
    assert job["error_detail"] == memory_payload
    metrics = job["execution_metrics"]
    assert metrics["status"] == "memory_limited"
    assert metrics["terminal_reason"] == "memory_limited"
    assert metrics["memory_limit_bytes"] == 512 * 1024 * 1024
    assert metrics["admission"]["profile"] == ExecutionProfile.OPTIMISER_SETUP.value


def test_optimiser_extract_factors_sinks_without_projected_frame_budget() -> None:
    from haute.routes._optimiser_service import OptimiserSolveService

    context = ExecutionContext(
        operation="optimiser_solve",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        job_id="job-1",
        memory_limit_bytes=1,
        memory_baseline_bytes=0,
        rss_limit_bytes=1,
        memory_sampler=lambda: 0,
    )

    handle = OptimiserSolveService._extract_factors(
        {"band": pl.LazyFrame({"quote_id": ["q1"], "factor": ["A"]})},
        {"banding_source": "band", "factor_columns": [["factor"]]},
        "ratebook",
        execution_context=context,
    )

    assert handle["row_count"] == 1
    assert context.metrics.snapshot()[0].name == "optimiser_extract_factors"


def test_optimiser_build_grid_preserves_memory_limit_error() -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService

    service = OptimiserSolveService(JobStore())
    job_id = service._store.create_job({"status": "running"})
    memory_error = ExecutionMemoryLimitExceededError(
        "optimiser_solve",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    with patch("haute.routes._optimiser_service.bounded_sink", side_effect=memory_error):
        with pytest.raises(ExecutionMemoryLimitExceededError):
            service._build_grid(
                pl.LazyFrame({"quote_id": ["q1"], "scenario_index": [0]}),
                [],
                {"objective": "objective"},
                "opt",
                job_id,
            )


def test_auto_range_start_admits_before_registering_latest_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    monkeypatch.setenv("HAUTE_AUTO_RANGE_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 64 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    node = graph.nodes[0]
    service = OptimiserSolveService(JobStore())
    captured = {}

    with (
        patch.object(
            service,
            "_prepare_frontier_auto_range",
            return_value={
                "node": node,
                "config": {"objective": "expected_income"},
                "mode": "online",
                "chunk_size": 100,
                "partition_count": 1,
                "timeout": 10,
                "required_columns_by_node": {},
                "streaming_plan": None,
            },
        ),
        patch.object(
            service,
            "_launch_frontier_auto_range_background",
            side_effect=lambda *_args, **kwargs: captured.update(kwargs),
        ),
    ):
        response = service.start_frontier_auto_range(body)

    assert response.status == "started"
    context = captured["execution_context"]
    assert context.profile == ExecutionProfile.AUTO_RANGE
    assert context.admission is not None
    service.cancel_frontier_auto_range(response.job_id)
    assert context.cancellation_token.cancelled


def test_auto_range_duplicate_start_reuses_running_job_without_readmitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    monkeypatch.setenv("HAUTE_AUTO_RANGE_MEMORY_LIMIT_MB", "512")
    monkeypatch.setenv("HAUTE_AUTO_RANGE_PROCESS_RSS_LIMIT_MB", "1024")
    samples = iter([64 * 1024 * 1024, 2 * 1024 * 1024 * 1024])
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: next(samples),
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    node = graph.nodes[0]
    store = JobStore()
    service = OptimiserSolveService(store)
    captured_contexts = []

    with (
        patch.object(
            service,
            "_prepare_frontier_auto_range",
            return_value={
                "node": node,
                "config": {"objective": "expected_income", "constraints": {}},
                "mode": "online",
                "chunk_size": 100,
                "partition_count": 1,
                "timeout": 10,
                "required_columns_by_node": {},
                "streaming_plan": None,
            },
        ),
        patch.object(
            service,
            "_launch_frontier_auto_range_background",
            side_effect=lambda *_args, **kwargs: captured_contexts.append(
                kwargs["execution_context"]
            ),
        ),
    ):
        first = service.start_frontier_auto_range(body)
        second = service.start_frontier_auto_range(body)

    assert second.job_id == first.job_id
    assert store.require_job(first.job_id)["status"] == "running"
    assert not captured_contexts[0].cancellation_token.cancelled
    assert len(captured_contexts) == 1


def test_auto_range_background_memory_limit_status_exposes_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    monkeypatch.setenv("HAUTE_AUTO_RANGE_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    node = graph.nodes[0]
    store = JobStore()
    service = OptimiserSolveService(store)
    memory_error = ExecutionMemoryLimitExceededError(
        "frontier_auto_range",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    with (
        patch.object(
            service,
            "_prepare_frontier_auto_range",
            return_value={
                "node": node,
                "config": {"objective": "expected_income", "constraints": {}},
                "mode": "online",
                "chunk_size": 100,
                "partition_count": 1,
                "timeout": 10,
                "required_columns_by_node": {},
                "streaming_plan": None,
            },
        ),
        patch.object(service, "_execute_pipeline", side_effect=memory_error),
    ):
        started = service.start_frontier_auto_range(body)
        deadline = time.monotonic() + 2
        status = service.frontier_auto_range_status(started.job_id)
        while status.status == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            status = service.frontier_auto_range_status(started.job_id)

    assert status.status == "memory_limited"
    assert status.terminal_reason == "memory_limited"
    assert status.error_code == "memory_limit"
    assert status.http_status_code == 507
    assert status.error_detail is not None
    assert status.error_detail.error_code == "memory_limit"
    assert status.error_detail.operation == "frontier_auto_range"
    assert status.error_detail.reason == "rss_exceeds_memory_limit"
    assert status.execution_metrics is not None
    assert status.execution_metrics.terminal_reason == "memory_limited"
    job = store.require_job(started.job_id)
    assert job["error_detail"]["error_code"] == "memory_limit"
    assert job["http_status_code"] == 507


def test_auto_range_background_preserves_typed_memory_http_exception_status() -> None:
    from fastapi import HTTPException

    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_service import OptimiserSolveService
    from haute.schemas import OptimiserFrontierAutoRangeRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "opt",
                    "data": {
                        "label": "opt",
                        "nodeType": NodeType.OPTIMISER.value,
                        "config": {"objective": "expected_income", "constraints": {}},
                    },
                },
            ],
            "edges": [],
        }
    )
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
    store = JobStore()
    service = OptimiserSolveService(store)
    job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})
    payload = {
        "error_code": "memory_limit",
        "operation": "frontier_auto_range",
        "job_id": job_id,
        "memory_limit_bytes": 512,
        "rss_bytes": 600,
        "reason": "rss_exceeds_memory_limit",
    }

    with patch.object(
        service,
        "_execute_pipeline",
        side_effect=HTTPException(status_code=507, detail=payload),
    ):
        with pytest.raises(HTTPException) as exc_info:
            service._run_frontier_auto_range_job(
                body,
                job_id,
                node=graph.nodes[0],
                config={"objective": "expected_income", "constraints": {}},
                mode="online",
                chunk_size=100,
                partition_count=1,
                timeout=10,
                required_columns_by_node={},
                streaming_plan=None,
            )

    assert exc_info.value.status_code == 507
    status = service.frontier_auto_range_status(job_id)
    assert status.status == "memory_limited"
    assert status.terminal_reason == "memory_limited"
    assert status.error_code == "memory_limit"
    assert status.http_status_code == 507
    assert status.error_detail is not None
    assert status.error_detail.job_id == job_id
    assert status.error_detail.reason == "rss_exceeds_memory_limit"
    assert status.execution_metrics is not None
    assert status.execution_metrics.terminal_reason == "memory_limited"


def test_training_start_creates_admitted_training_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from haute.routes._job_store import JobStore
    from haute.routes._train_service import TrainService
    from haute.schemas import TrainRequest

    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", "1024")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 256 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": NodeType.MODELLING.value,
                        "config": {
                            "target": "target",
                            "algorithm": "catboost",
                            "loss_function": "RMSE",
                        },
                    },
                },
            ],
            "edges": [],
        }
    )
    body = TrainRequest(graph=graph, node_id="model")
    service = TrainService(JobStore())
    captured = {}
    tmp_parquet = tmp_path / "training.parquet"
    tmp_parquet.write_bytes(b"placeholder")

    def fake_execute_and_sink(*_args, **kwargs):
        captured["execution_context"] = kwargs["execution_context"]
        return str(tmp_parquet)

    with (
        patch.object(service, "_compile_preamble", return_value={}),
        patch.object(service, "_estimate_ram", return_value=(None, None, None, [])),
        patch.object(service, "_check_gpu_fallback", return_value=None),
        patch.object(service, "_execute_and_sink", side_effect=fake_execute_and_sink),
        patch.object(service, "_launch_background"),
    ):
        response = service.start(body)

    assert response.status == "started"
    context = captured["execution_context"]
    assert context.profile == ExecutionProfile.TRAINING_PREP
    assert context.memory_limit_bytes == 1024 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.rss_at_admission_bytes == 256 * 1024 * 1024


def test_training_start_maps_admission_failure_to_http_507(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes._job_store import JobStore
    from haute.routes._train_service import TrainService
    from haute.schemas import TrainRequest

    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", "512")
    monkeypatch.setenv("HAUTE_TRAINING_PROCESS_RSS_LIMIT_MB", "64")
    monkeypatch.setattr(
        "haute._execution_admission.current_rss_bytes",
        lambda: 65 * 1024 * 1024,
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": NodeType.MODELLING.value,
                        "config": {
                            "target": "target",
                            "algorithm": "catboost",
                            "loss_function": "RMSE",
                        },
                    },
                },
            ],
            "edges": [],
        }
    )
    store = JobStore()
    service = TrainService(store)

    with (
        patch.object(service, "_compile_preamble", return_value={}),
        patch.object(service, "_estimate_ram", return_value=(None, None, None, [])),
        patch.object(service, "_check_gpu_fallback", return_value=None),
        patch.object(service, "_execute_and_sink") as execute_and_sink,
        pytest.raises(HTTPException) as exc_info,
    ):
        service.start(TrainRequest(graph=graph, node_id="model"))

    assert exc_info.value.status_code == 507
    assert exc_info.value.detail["error_code"] == "memory_limit"
    assert exc_info.value.detail["profile"] == "training_prep"
    assert exc_info.value.detail["reason"] == "process_rss_limit_exceeded"
    execute_and_sink.assert_not_called()
    job = next(iter(store.jobs.values()))
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert "process_rss_limit_exceeded" in job["error"]


def test_training_start_maps_runtime_memory_failure_to_http_507(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes._job_store import JobStore
    from haute.routes._train_service import TrainService
    from haute.schemas import TrainRequest

    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 1)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": NodeType.MODELLING.value,
                        "config": {
                            "target": "target",
                            "algorithm": "catboost",
                            "loss_function": "RMSE",
                        },
                    },
                },
            ],
            "edges": [],
        }
    )
    service = TrainService(JobStore())
    memory_error = ExecutionMemoryLimitExceededError(
        "training_pipeline",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
    )

    with (
        patch.object(service, "_compile_preamble", return_value={}),
        patch.object(service, "_estimate_ram", return_value=(None, None, None, [])),
        patch.object(service, "_check_gpu_fallback", return_value=None),
        patch.object(service, "_execute_and_sink", side_effect=memory_error),
        pytest.raises(HTTPException) as exc_info,
    ):
        service.start(TrainRequest(graph=graph, node_id="model"))

    assert exc_info.value.status_code == 507
    assert exc_info.value.detail["error_code"] == "memory_limit"
    assert exc_info.value.detail["operation"] == "training_pipeline"
    assert exc_info.value.detail["reason"] == "rss_exceeds_memory_limit"


def test_deploy_pyfunc_predict_creates_admitted_live_context(monkeypatch) -> None:
    import pandas as pd

    from haute.deploy._model_code import HauteModel

    monkeypatch.setenv("HAUTE_DEPLOY_LIVE_MEMORY_LIMIT_MB", "128")
    monkeypatch.setattr("haute._execution_admission.current_rss_bytes", lambda: 11_000)

    model = HauteModel()
    model._graph = make_graph({"nodes": [], "edges": []})
    model._input_node_ids = ["src"]
    model._output_node_id = "out"
    model._artifact_paths = {}
    model._output_fields = None
    expected_result = pl.DataFrame({"score": [0.25]})

    with patch("haute.deploy._scorer.score_graph", return_value=expected_result) as score:
        result = model.predict(object(), pd.DataFrame({"feature": [1.0]}))

    assert result["score"].to_list() == [0.25]
    context = score.call_args.kwargs["execution_context"]
    assert context.profile == ExecutionProfile.DEPLOY_LIVE
    assert context.memory_limit_bytes == 128 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.rss_at_admission_bytes == 11_000
