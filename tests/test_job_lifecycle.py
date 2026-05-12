"""Shared route job lifecycle contract tests."""

from __future__ import annotations

import itertools

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute.routes._background_jobs import CancellableJobRegistry
from haute.routes._job_lifecycle import (
    TERMINAL_REASON_TO_STATUS,
    JobLifecycle,
    TerminalReason,
    bind_running_execution_metrics_publisher,
)
from haute.routes._job_store import JobStore

_ORDERED_REASONS: tuple[TerminalReason, ...] = (
    "error",
    "contract_error",
    "memory_limited",
    "cancelled",
    "timed_out",
    "superseded",
)


def test_lifecycle_transition_sets_terminal_metadata() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running"})

    job = lifecycle.transition(
        job_id,
        to="memory_limited",
        message="RAM budget exceeded",
        fields={"error_code": "memory_limit"},
        elapsed_seconds=1.25,
        now=123.0,
    )

    assert job is not None
    assert job["status"] == "memory_limited"
    assert job["terminal_reason"] == "memory_limited"
    assert job["message"] == "RAM budget exceeded"
    assert job["error_code"] == "memory_limit"
    assert job["elapsed_seconds"] == 1.25
    assert job["ended_at"] == 123.0


def test_lifecycle_completed_status_is_immutable_to_error_races() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running"})

    assert lifecycle.transition(job_id, to="completed", now=1.0) is not None
    assert lifecycle.transition(job_id, to="superseded", now=2.0) is None

    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["terminal_reason"] == "completed"
    assert job["completed_at"] == 1.0


def test_lifecycle_stale_completed_cannot_overwrite_terminal_stop() -> None:
    for reason in _ORDERED_REASONS:
        store = JobStore()
        lifecycle = JobLifecycle(store)
        job_id = store.create_job({"status": "running"})

        assert lifecycle.transition(job_id, to=reason, now=1.0) is not None
        assert lifecycle.transition(job_id, to="completed", now=2.0) is None

        job = store.require_job(job_id)
        assert job["status"] == TERMINAL_REASON_TO_STATUS[reason]
        assert job["terminal_reason"] == reason
        assert "completed_at" not in job


def test_lifecycle_reason_precedence_pairwise_races() -> None:
    for lower, higher in itertools.combinations(_ORDERED_REASONS, 2):
        store = JobStore()
        lifecycle = JobLifecycle(store)
        job_id = store.create_job({"status": "running"})

        assert lifecycle.transition(job_id, to=lower, now=1.0) is not None
        assert lifecycle.transition(job_id, to=higher, now=2.0) is not None

        job = store.require_job(job_id)
        assert job["status"] == TERMINAL_REASON_TO_STATUS[higher]
        assert job["terminal_reason"] == higher


def test_lifecycle_lower_precedence_reason_cannot_overwrite_race_winner() -> None:
    for lower, higher in itertools.combinations(_ORDERED_REASONS, 2):
        store = JobStore()
        lifecycle = JobLifecycle(store)
        job_id = store.create_job({"status": "running"})

        assert lifecycle.transition(job_id, to=higher, now=1.0) is not None
        assert lifecycle.transition(job_id, to=lower, now=2.0) is None

        job = store.require_job(job_id)
        assert job["status"] == TERMINAL_REASON_TO_STATUS[higher]
        assert job["terminal_reason"] == higher


def test_running_metrics_publisher_updates_job_on_memory_pressure() -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id=job_id,
        memory_limit_bytes=100,
        memory_baseline_bytes=100,
        memory_sampler=lambda: 150,
    )
    bind_running_execution_metrics_publisher(store, job_id, context)

    context.checkpoint(label="half", node_id="model")

    job = store.require_job(job_id)
    assert job["execution_metrics"]["status"] == "running"
    assert job["execution_metrics"]["memory_pressure_event_count"] == 1
    event = job["execution_metrics"]["memory_pressure_events"][0]
    assert event["stage"] is None
    assert event["node_id"] == "model"


def test_running_metrics_publisher_ignores_terminal_job() -> None:
    store = JobStore()
    job_id = store.create_job({"status": "completed"})
    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id=job_id,
        memory_limit_bytes=100,
        memory_baseline_bytes=100,
        memory_sampler=lambda: 150,
    )
    bind_running_execution_metrics_publisher(store, job_id, context)

    context.checkpoint(label="half", node_id="model")

    assert "execution_metrics" not in store.require_job(job_id)


def test_cancellable_registry_records_registry_derived_stop_reason() -> None:
    registry = CancellableJobRegistry()

    _first, previous = registry.register_latest(("kind", "node"), "job-1")
    assert previous is None
    _second, previous = registry.register_latest(("kind", "node"), "job-2")

    assert previous == "job-1"
    assert registry.cancellation_reason("job-1") == "superseded"
    assert registry.cancellation_reason("job-2") is None

    assert registry.cancel("job-2", reason="timed_out") is True
    assert registry.cancellation_reason("job-2") == "timed_out"
