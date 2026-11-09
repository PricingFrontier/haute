"""The execution context's cleanups, checkpoints, cancellation and telemetry.

Registered cleanups run once and never hide the primary error, checkpoints enforce cancellation and
memory limits, cancellation callbacks run once outside the token lock, fault injection reports
checkpoints in order, terminal telemetry is bounded, redacted and emitted once, and the background
job registry cancels a superseded job's token.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionEvidence,
    ExecutionLease,
    ExecutionMemoryBudget,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
    ExecutionTelemetry,
    ExecutionTelemetryEvent,
    _bounded_telemetry_attributes,
)
from haute._project_context import context_thread
from haute.schemas import ExecutionMetricsPayload
from tests._execution_context_helpers import (
    _clear_execution_memory_env,
)
from tests._execution_faults import ExecutionFaultPoint, FaultInjectingExecutionContext

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def test_execution_context_releases_registered_cleanups_once() -> None:
    context = ExecutionContext(
        operation="resource-cleanup",
        profile=ExecutionProfile.LAZY_SINK,
    )
    released: list[str] = []
    context.add_cleanup(lambda: released.append("first"))
    context.add_cleanup(lambda: released.append("second"))

    context.release_admission()
    context.release_admission()

    assert released == ["second", "first"]
    with pytest.raises(RuntimeError, match="released execution context"):
        context.add_cleanup(lambda: None)


def test_execution_context_cleanup_only_failure_runs_every_release_and_raises_first() -> None:
    released: list[str] = []

    def cleanup_first() -> None:
        released.append("first")
        raise RuntimeError("first cleanup failed")

    def cleanup_second() -> None:
        released.append("second")
        raise ValueError("second cleanup failed")

    def release_admission() -> None:
        released.append("admission")
        raise OSError("admission release failed")

    context = ExecutionContext(
        operation="cleanup-failure",
        profile=ExecutionProfile.LAZY_SINK,
        admission_release=release_admission,
    )
    context.add_cleanup(cleanup_first)
    context.add_cleanup(cleanup_second)

    with pytest.raises(ValueError, match="second cleanup failed") as exc_info:
        context.release_admission()

    assert released == ["second", "first", "admission"]
    assert any("RuntimeError" in note for note in exc_info.value.__notes__)
    assert any("OSError" in note for note in exc_info.value.__notes__)
    context.release_admission()
    assert released == ["second", "first", "admission"]


def test_execution_context_cleanup_failures_do_not_replace_primary_error() -> None:
    released: list[str] = []
    context = ExecutionContext(
        operation="primary-failure",
        profile=ExecutionProfile.LAZY_SINK,
        admission_release=lambda: released.append("admission"),
    )

    def cleanup() -> None:
        released.append("cleanup")
        raise RuntimeError("cleanup failed")

    context.add_cleanup(cleanup)

    with pytest.raises(KeyError, match="primary failed") as exc_info:
        try:
            raise KeyError("primary failed")
        finally:
            context.release_admission(preserve_primary_error=True)

    assert released == ["cleanup", "admission"]
    assert any("RuntimeError" in note for note in exc_info.value.__notes__)


def test_cleanup_failure_is_not_silently_attached_to_an_unrelated_caught_error() -> None:
    context = ExecutionContext(operation="cleanup-failure", profile=ExecutionProfile.LAZY_SINK)
    context.add_cleanup(lambda: (_ for _ in ()).throw(RuntimeError("cleanup failed")))
    caught_error: KeyError | None = None

    try:
        raise KeyError("unrelated")
    except KeyError as caught:
        caught_error = caught
        with pytest.raises(RuntimeError, match="cleanup failed"):
            context.release_admission()

    assert caught_error is not None
    assert not getattr(caught_error, "__notes__", [])


def test_heavy_admission_releases_reservation_when_finalizer_registration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr(admission_mod, "available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 10 * gib)

    def fail_finalize(*_args, **_kwargs):
        raise RuntimeError("finalizer registration failed")

    monkeypatch.setattr(admission_mod.weakref, "finalize", fail_finalize)
    with pytest.raises(RuntimeError, match="finalizer registration failed"):
        admission_mod.create_admitted_execution_context(
            operation="optimiser_setup_failed",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            memory_sampler=lambda: 100,
        )

    assert admission_mod._IN_FLIGHT_RESERVATIONS == {}


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


def test_memory_budget_fails_loudly_when_sampler_disappears_mid_run() -> None:
    samples = iter([100, None])
    sample_count = 0

    def sample_memory() -> int | None:
        nonlocal sample_count
        sample_count += 1
        return next(samples)

    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id="job-1",
        memory_limit_bytes=1_000,
        memory_baseline_bytes=0,
        memory_sampler=sample_memory,
    )

    context.checkpoint(label="sample-available")
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="sample-unavailable")

    assert exc_info.value.reason == "memory_sampler_unavailable"
    assert exc_info.value.rss_bytes is None
    assert sample_count == 2


def test_memory_limit_error_rejects_missing_rss_for_other_reasons() -> None:
    with pytest.raises(ValueError, match="rss_bytes may be None only"):
        ExecutionMemoryLimitExceededError(
            "training",
            rss_bytes=None,
            limit_bytes=1_000,
        )


def test_checkpoint_enforces_rss_limit_without_memory_growth_budget() -> None:
    samples = iter([999, 1_001])
    sample_count = 0

    def sample_memory() -> int:
        nonlocal sample_count
        sample_count += 1
        return next(samples)

    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        rss_limit_bytes=1_000,
        memory_sampler=sample_memory,
    )

    context.checkpoint(label="under-rss-cap")
    assert sample_count == 1

    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="over-rss-cap")

    assert sample_count == 2
    assert exc_info.value.rss_bytes == 1_001
    assert exc_info.value.rss_limit_bytes == 1_000
    assert exc_info.value.reason == "rss_exceeds_memory_limit"


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_cancellation_latency_uses_first_request_and_meets_controlled_checkpoint_budget(
    profile: ExecutionProfile,
) -> None:
    checkpoint_observation_budget_ms = 50.0
    now = [10.0]
    token = ExecutionCancellationToken(monotonic_clock=lambda: now[0])
    context = ExecutionContext(
        operation="training",
        profile=profile,
        cancellation_token=token,
    )

    token.cancel()
    now[0] = 10.010
    token.cancel()
    now[0] = 10.025

    with pytest.raises(ExecutionCancelledError) as exc_info:
        context.checkpoint(label="before-native")

    latency_ms = exc_info.value.cancellation_latency_ms
    assert latency_ms is not None
    assert latency_ms == pytest.approx(25.0)
    assert latency_ms <= checkpoint_observation_budget_ms
    payload = context.metrics_payload(
        status="cancelled",
        terminal_reason="cancelled",
    )
    assert payload["cancellation_latency_ms"] == pytest.approx(25.0)
    assert ExecutionMetricsPayload.model_validate(payload).cancellation_latency_ms == pytest.approx(
        25.0
    )


def test_execution_contexts_carry_no_fault_injection() -> None:
    from haute import _execution_context as context_mod

    assert not hasattr(context_mod, "ExecutionFaultPoint")
    assert not hasattr(ExecutionContext, "fault_point")
    with pytest.raises(TypeError, match="fault_injector"):
        ExecutionContext(
            operation="fault-test",
            profile=ExecutionProfile.LAZY_SINK,
            fault_injector=print,  # type: ignore[call-arg]
        )


def _crossed_thresholds(budget: ExecutionMemoryBudget, rss_bytes: int) -> list[int]:
    events = budget.pressure_events(
        rss_bytes,
        operation="budget-only",
        profile=ExecutionProfile.LAZY_SINK,
        job_id=None,
        stage=None,
        node_id=None,
        label="probe",
    )
    return [event.threshold_percent for event in events]


def test_budget_and_lease_work_without_either_recorder() -> None:
    budget = ExecutionMemoryBudget(memory_limit_bytes=100, memory_baseline_bytes=0)

    budget.enforce(50, operation="budget-only", job_id=None)
    assert _crossed_thresholds(budget, 95) == [50, 75, 90]
    assert _crossed_thresholds(budget, 99) == []
    # A process cap at or below the baseline leaves no window to measure pressure in.
    capped = ExecutionMemoryBudget(
        memory_limit_bytes=100, memory_baseline_bytes=100, rss_limit_bytes=50
    )
    assert _crossed_thresholds(capped, 60) == []
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        budget.enforce(101, operation="budget-only", job_id="job-1")
    assert exc_info.value.reason == "rss_exceeds_memory_limit"

    released: list[str] = []
    lease = ExecutionLease(lambda: released.append("admission"))
    lease.add_cleanup(lambda: released.append("first"))
    lease.add_cleanup(lambda: released.append("second"))
    lease.release()
    lease.release()
    assert released == ["second", "first", "admission"]
    assert lease.released


def test_context_reads_its_limits_admission_and_sampler_through_its_budget() -> None:
    def sampler() -> int:
        return 10

    context = ExecutionContext(
        operation="read-through",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=100,
        rss_limit_bytes=150,
        memory_sampler=sampler,
    )

    assert context.memory_limit_bytes == context.budget.memory_limit_bytes == 100
    assert context.rss_limit_bytes == context.budget.rss_limit_bytes == 150
    assert context.admission is context.budget.admission is None
    assert context.memory_sampler is context.budget.sampler is sampler

    def replacement() -> int:
        return 20

    context.memory_sampler = replacement
    assert context.budget.sampler is replacement


def test_evidence_rejects_invalid_widths_and_byte_counts() -> None:
    evidence = ExecutionEvidence()

    with pytest.raises(ValueError, match="non-empty node_id"):
        evidence.record_column_widths(node_id="")
    with pytest.raises(ValueError, match="input_width must be a non-negative integer"):
        evidence.record_column_widths(node_id="node-1", input_width=-1)
    with pytest.raises(ValueError, match="output_width must be a non-negative integer"):
        evidence.record_column_widths(node_id="node-1", output_width=True)
    with pytest.raises(ValueError, match="non-negative integers"):
        evidence.record_bytes_written(-1)


def test_cancellation_latency_keeps_the_first_observed_value() -> None:
    clock = iter([10.0, 10.025, 10.5])
    token = ExecutionCancellationToken(monotonic_clock=lambda: next(clock))
    context = ExecutionContext(
        operation="cancel", profile=ExecutionProfile.LAZY_SINK, cancellation_token=token
    )

    token.cancel()
    for _ in range(2):
        with pytest.raises(ExecutionCancelledError):
            context.checkpoint(label="after-cancel")

    assert context.metrics_payload()["cancellation_latency_ms"] == pytest.approx(25.0)


def test_on_cancel_runs_each_callback_once_when_the_token_is_cancelled() -> None:
    token = ExecutionCancellationToken()
    calls: list[str] = []
    token.on_cancel(lambda: calls.append("first"))
    token.on_cancel(lambda: calls.append("second"))
    assert calls == []

    token.cancel()
    token.cancel()

    assert calls == ["first", "second"]


def test_on_cancel_runs_at_once_on_an_already_cancelled_token() -> None:
    token = ExecutionCancellationToken()
    token.cancel()
    calls: list[str] = []

    token.on_cancel(lambda: calls.append("late"))

    assert calls == ["late"]


def test_on_cancel_callbacks_run_outside_the_token_lock() -> None:
    token = ExecutionCancellationToken()
    seen: list[bool] = []

    def reads_the_token_from_another_thread() -> None:
        # A callback that needs the token (here, from another thread) must not
        # deadlock on the lock cancel() holds while setting the flag.
        thread = context_thread(lambda: seen.append(token.cancelled))
        thread.start()
        thread.join(5)

    token.on_cancel(reads_the_token_from_another_thread)
    token.cancel()

    assert seen == [True]


def test_terminal_telemetry_skips_live_statuses_and_logs_without_a_sink() -> None:
    telemetry = ExecutionTelemetry(enabled=True)

    with patch("haute._execution_context.logger") as log:
        telemetry.emit_terminal({"status": "running"})
        log.info.assert_not_called()
        telemetry.emit_terminal({"status": "completed"})
        telemetry.emit_terminal({"status": "completed"})

    log.info.assert_called_once()
    assert log.info.call_args.args == ("execution_terminal",)


@pytest.mark.meta
def test_no_class_in_the_execution_context_module_exceeds_300_lines() -> None:
    """The execution-engine specification caps every class in the module at 300 lines."""
    import ast

    from haute import _execution_context as context_mod

    tree = ast.parse(Path(context_mod.__file__).read_text(encoding="utf-8"))
    sizes = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.end_lineno is not None
    }
    assert "ExecutionContext" in sizes
    assert {name: size for name, size in sizes.items() if size > 300} == {}


def test_fault_injecting_context_reports_checkpoints_in_order() -> None:
    points: list[ExecutionFaultPoint] = []
    context = FaultInjectingExecutionContext(
        operation="fault-test",
        profile=ExecutionProfile.LAZY_SINK,
        fault_injector=points.append,
    )

    context.checkpoint(label="before-node", node_id="node-1")
    context.checkpoint(label="after-node")

    assert points == [
        ExecutionFaultPoint(
            name="before-node",
            operation="fault-test",
            node_id="node-1",
            sequence=1,
        ),
        ExecutionFaultPoint(
            name="after-node",
            operation="fault-test",
            node_id=None,
            sequence=2,
        ),
    ]


def test_execution_fault_injector_failure_propagates_before_checkpoint_work() -> None:
    def inject(point: ExecutionFaultPoint) -> None:
        assert point.name == "before-native"
        raise RuntimeError("deterministic fault")

    context = FaultInjectingExecutionContext(
        operation="fault-test",
        profile=ExecutionProfile.LAZY_SINK,
        fault_injector=inject,
    )

    with pytest.raises(RuntimeError, match="deterministic fault"):
        context.checkpoint(label="before-native", node_id="node-1")

    assert context.metrics_payload()["checkpoint_count"] == 0


def test_execution_telemetry_disabled_mode_never_calls_sink() -> None:
    events: list[ExecutionTelemetryEvent] = []
    context = ExecutionContext(
        operation="secret-operation",
        profile=ExecutionProfile.LAZY_SINK,
        job_id="secret-job",
        telemetry_enabled=False,
        telemetry_sink=events.append,
    )

    with patch(
        "haute._execution_context._terminal_telemetry_attributes",
        side_effect=AssertionError("disabled telemetry assembled attributes"),
    ):
        context.metrics_payload(status="completed")

    assert events == []


def test_execution_telemetry_is_bounded_redacted_and_emitted_once_per_terminal_state() -> None:
    events: list[ExecutionTelemetryEvent] = []
    context = ExecutionContext(
        operation="secret-operation",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id="secret-job",
        telemetry_enabled=True,
        telemetry_sink=events.append,
    )
    context.record_bytes_written(512)
    context.record_chunk()
    context.record_column_widths(
        node_id="source",
        requested_width=2,
        physically_scanned_width=3,
    )
    context.record_cache_direct_fallback()

    terminal_reason = "bounded_reason_" + ("x" * 256)
    context.metrics_payload(status="completed", terminal_reason=terminal_reason)
    context.metrics_payload(status="completed", terminal_reason=terminal_reason)

    assert len(events) == 1
    event = events[0]
    assert event.schema_version == 1
    assert event.event == "execution_terminal"
    assert len(event.attributes) <= 48
    assert all(
        not isinstance(value, str) or len(value) <= 128 for value in event.attributes.values()
    )
    assert event.attributes["profile"] == "training_prep"
    # Nothing records bytes read or cache proofs, so no attribute claims a count of them.
    assert not {"bytes_read", "cache_proof_hits", "cache_proof_misses"} & set(event.attributes)
    assert event.attributes["bytes_written"] == 512
    assert event.attributes["chunk_count"] == 1
    assert "rss_start_bytes" in event.attributes
    assert "strategy_status" in event.attributes
    assert "admission_headroom_bytes" in event.attributes
    assert "column_widths_state" in event.attributes
    assert event.attributes["requested_column_width_total"] == 2
    assert event.attributes["physically_scanned_column_width_total"] == 3
    assert event.attributes["cache_direct_fallbacks"] == 1
    serialised = json.dumps(event.to_dict())
    assert "secret-operation" not in serialised
    assert "secret-job" not in serialised
    assert not {"operation", "job_id", "node_id", "path", "columns"} & set(event.attributes)


def test_execution_telemetry_sink_failure_does_not_change_metrics_result() -> None:
    def fail_sink(_event: ExecutionTelemetryEvent) -> None:
        raise RuntimeError("telemetry unavailable")

    context = ExecutionContext(
        operation="telemetry-failure",
        profile=ExecutionProfile.LAZY_SINK,
        telemetry_enabled=True,
        telemetry_sink=fail_sink,
    )

    payload = context.metrics_payload(status="completed")

    assert payload["status"] == "completed"


def test_execution_telemetry_environment_rejects_ambiguous_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_context import configure_execution_telemetry

    monkeypatch.setenv("HAUTE_EXECUTION_TELEMETRY", "sometimes")

    with pytest.raises(ValueError, match="HAUTE_EXECUTION_TELEMETRY"):
        configure_execution_telemetry()


def test_execution_telemetry_environment_is_parsed_once_across_default_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_context as context_mod

    context_mod._execution_telemetry_enabled.cache_clear()
    monkeypatch.setenv("HAUTE_EXECUTION_TELEMETRY", "true")
    ExecutionContext(operation="one", profile=ExecutionProfile.LAZY_SINK)
    ExecutionContext(operation="two", profile=ExecutionProfile.LAZY_SINK)
    assert context_mod._execution_telemetry_enabled.cache_info().misses == 1
    context_mod._execution_telemetry_enabled.cache_clear()


def test_bounded_telemetry_attributes_rejects_overflow_instead_of_slicing() -> None:
    attributes = {f"key_{index}": index for index in range(48)}
    assert _bounded_telemetry_attributes(attributes) == attributes
    with pytest.raises(ValueError, match="48"):
        _bounded_telemetry_attributes({**attributes, "overflow": 49})


def test_telemetry_attribute_failure_does_not_change_metrics_payload() -> None:
    context = ExecutionContext(
        operation="telemetry", profile=ExecutionProfile.LAZY_SINK, telemetry_enabled=True
    )
    with patch(
        "haute._execution_context._terminal_telemetry_attributes",
        side_effect=RuntimeError("bad telemetry"),
    ):
        assert context.metrics_payload(status="completed")["status"] == "completed"


def test_background_job_registry_cancels_execution_token_for_superseded_job() -> None:
    from haute.routes._background_jobs import CancellableJobRegistry

    registry = CancellableJobRegistry()
    first_token, previous = registry.register_latest(("auto-range", "graph"), "job-1")
    assert previous is None

    context = ExecutionContext(
        operation="frontier_auto_range",
        profile=ExecutionProfile.OPTIMISER_SOLVE,
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
