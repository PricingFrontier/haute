"""Shared route job lifecycle contract tests."""

from __future__ import annotations

import itertools
import threading
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute.routes._background_jobs import (
    BackgroundJobStoppedError,
    CancellableJobRegistry,
    SingleFlightConflictError,
    SingleFlightCoordinator,
    SingleFlightHandle,
)
from haute.routes._job_lifecycle import (
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


def test_lifecycle_rejects_invalid_terminal_reason_without_mutating_job() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running", "progress": 0.25})
    before = dict(store.require_job(job_id))

    with pytest.raises(ValueError, match="Unsupported terminal reason.*bogus"):
        lifecycle.transition(
            job_id,
            to=cast(TerminalReason, "bogus"),
            fields={"progress": 1.0},
            now=123.0,
        )

    assert store.require_job(job_id) == before


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


def test_lifecycle_explicit_completed_publication_correction_is_coherent() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running"})
    assert lifecycle.transition(
        job_id,
        to="completed",
        fields={"result": {"value": "invalid"}},
        now=1.0,
    )

    corrected = lifecycle.transition(
        job_id,
        to="error",
        expected_status="completed",
        fields={"result": None},
        message="Result could not be published",
        now=2.0,
    )

    assert corrected is not None
    assert corrected["status"] == "error"
    assert corrected["terminal_reason"] == "error"
    assert corrected["result"] is None
    assert corrected["message"] == "Result could not be published"


def test_lifecycle_rejects_other_direct_terminal_corrections() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running"})
    assert lifecycle.transition(job_id, to="completed") is not None

    with pytest.raises(ValueError, match="only be corrected to 'error'"):
        lifecycle.transition(
            job_id,
            to="cancelled",
            expected_status="completed",
        )


def test_lifecycle_stale_completed_cannot_overwrite_terminal_stop() -> None:
    for reason in _ORDERED_REASONS:
        store = JobStore()
        lifecycle = JobLifecycle(store)
        job_id = store.create_job({"status": "running"})

        assert lifecycle.transition(job_id, to=reason, now=1.0) is not None
        assert lifecycle.transition(job_id, to="completed", now=2.0) is None

        job = store.require_job(job_id)
        assert job["status"] == reason
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
        assert job["status"] == higher
        assert job["terminal_reason"] == higher


def test_lifecycle_lower_precedence_reason_cannot_overwrite_race_winner() -> None:
    for lower, higher in itertools.combinations(_ORDERED_REASONS, 2):
        store = JobStore()
        lifecycle = JobLifecycle(store)
        job_id = store.create_job({"status": "running"})

        assert lifecycle.transition(job_id, to=higher, now=1.0) is not None
        assert lifecycle.transition(job_id, to=lower, now=2.0) is None

        job = store.require_job(job_id)
        assert job["status"] == higher
        assert job["terminal_reason"] == higher


@pytest.mark.parametrize(
    "fault_point",
    [
        "terminal_transition_before_write",
        "terminal_transition_before_cleanup_schedule",
    ],
)
def test_lifecycle_exposes_terminal_transition_fault_points(fault_point: str) -> None:
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    seen: list[str] = []

    def inject(point: str) -> None:
        seen.append(point)
        if point == fault_point:
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=fault_point):
        JobLifecycle(store, fault_injector=inject).transition(job_id, to="error")

    if fault_point == "terminal_transition_before_write":
        assert store.require_job(job_id)["status"] == "running"
    else:
        assert store.require_job(job_id)["status"] == "error"
    assert seen[0] == "terminal_transition_before_write"


def test_publish_completion_is_paired_and_suppresses_lost_claim_callback() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running"})
    assert lifecycle.transition(job_id, to="cancelled") is not None
    called = False

    def publish() -> dict[str, object]:
        nonlocal called
        called = True
        return {"result": {"value": 1}}

    assert lifecycle.publish_completion(job_id, publish=publish) is None
    assert called is False


def test_publish_completion_failure_leaves_running_record_unchanged() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running", "progress": 0.25})
    before = store.require_job(job_id)

    def publish() -> dict[str, object]:
        raise RuntimeError("artifact publication failed")

    with pytest.raises(RuntimeError, match="artifact publication failed"):
        lifecycle.publish_completion(job_id, publish=publish)

    assert store.require_job(job_id) == before


def test_publish_completion_timestamps_after_publisher_returns() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running"})
    clock = [100.0]

    def publish() -> dict[str, object]:
        clock[0] = 200.0
        return {"result": {"value": 1}}

    with patch("haute.routes._job_store.time.time", side_effect=lambda: clock[0]):
        completed = lifecycle.publish_completion(job_id, publish=publish)

    assert completed is not None
    assert completed["ended_at"] == 200.0
    assert completed["completed_at"] == 200.0


def test_publish_completion_publishes_before_competing_cancellation() -> None:
    store = JobStore()
    lifecycle = JobLifecycle(store)
    job_id = store.create_job({"status": "running"})
    publisher_entered = threading.Event()
    allow_publisher_return = threading.Event()
    published: list[object] = []

    def publish() -> dict[str, object]:
        publisher_entered.set()
        assert allow_publisher_return.wait(timeout=2)
        return {"result": {"value": 1}}

    completion = threading.Thread(
        target=lambda: published.append(lifecycle.publish_completion(job_id, publish=publish))
    )
    completion.start()
    assert publisher_entered.wait(timeout=2)
    cancellation = threading.Thread(target=lambda: lifecycle.transition(job_id, to="cancelled"))
    cancellation.start()
    allow_publisher_return.set()
    completion.join(timeout=2)
    cancellation.join(timeout=2)

    assert all(not thread.is_alive() for thread in (completion, cancellation))
    assert published[0] is not None
    job = store.require_job(job_id)
    assert job["status"] == "completed"
    assert job["terminal_reason"] == "completed"
    assert job["result"] == {"value": 1}


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
    job_id = store.create_job({"status": "running"})
    assert JobLifecycle(store).transition(job_id, to="completed") is not None
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


def test_running_metrics_publisher_propagates_missing_job() -> None:
    store = JobStore()
    context = ExecutionContext(
        operation="training",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id="missing",
        memory_limit_bytes=100,
        memory_baseline_bytes=100,
        memory_sampler=lambda: 150,
    )
    bind_running_execution_metrics_publisher(store, "missing", context)

    with pytest.raises(HTTPException, match="missing") as exc_info:
        context.checkpoint(label="half", node_id="model")
    assert exc_info.value.status_code == 404


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


def test_cancellable_registry_scopes_supersession_to_its_own_key() -> None:
    registry = CancellableJobRegistry()

    sibling_token, previous = registry.register_latest(("explore_pivot", "pivot_2"), "sibling")
    assert previous is None
    registry.register_latest(("explore_pivot", "pivot_1"), "job-1")
    _second, previous = registry.register_latest(("explore_pivot", "pivot_1"), "job-2")

    assert previous == "job-1"
    assert registry.cancellation_reason("job-1") == "superseded"
    assert registry.cancellation_reason("sibling") is None
    assert not sibling_token.event.is_set()


def test_cancellable_registry_publication_guard_rejects_a_superseded_job() -> None:
    registry = CancellableJobRegistry()

    registry.register_latest(("kind", "node"), "job-1")
    registry.register_latest(("kind", "node"), "job-2")

    with registry.latest_publication("job-1") as owns_publication:
        assert owns_publication is False
    with registry.latest_publication("job-2") as owns_publication:
        assert owns_publication is True


def test_background_job_stopped_error_has_one_canonical_reason() -> None:
    error = BackgroundJobStoppedError("job-1", "cancelled")

    assert error.job_id == "job-1"
    assert error.terminal_reason == "cancelled"
    assert not hasattr(error, "status")


def test_singleflight_same_job_fans_in_idempotently_under_contention() -> None:
    coordinator = SingleFlightCoordinator()
    barrier = threading.Barrier(12)
    handles: list[SingleFlightHandle] = []
    failures: list[BaseException] = []
    result_lock = threading.Lock()

    def acquire() -> None:
        barrier.wait()
        try:
            handle = coordinator.acquire("shared", job_id="job-1", kind="cache")
        except BaseException as exc:
            with result_lock:
                failures.append(exc)
        else:
            with result_lock:
                handles.append(handle)

    threads = [threading.Thread(target=acquire) for _ in range(barrier.parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert (
        handles
        == [SingleFlightHandle(key="shared", job_id="job-1", kind="cache")] * barrier.parties
    )
    assert coordinator.active("shared") == handles[0]


def test_singleflight_same_key_has_one_owner_and_releases_for_retry() -> None:
    coordinator = SingleFlightCoordinator()
    barrier = threading.Barrier(12)
    handles: list[SingleFlightHandle] = []
    failures: list[tuple[str, BaseException]] = []
    result_lock = threading.Lock()

    def acquire(index: int) -> None:
        job_id = f"job-{index}"
        barrier.wait()
        try:
            handle = coordinator.acquire("shared", job_id=job_id, kind="cache")
        except BaseException as exc:
            with result_lock:
                failures.append((job_id, exc))
        else:
            with result_lock:
                handles.append(handle)

    threads = [threading.Thread(target=acquire, args=(index,)) for index in range(barrier.parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(handles) == 1
    assert len(failures) == barrier.parties - 1
    owner = handles[0]
    for _job_id, failure in failures:
        assert isinstance(failure, SingleFlightConflictError)
        assert failure.key == "shared"
        assert failure.active_job_id == owner.job_id
        assert failure.active_kind == owner.kind

    contender_job_id = failures[0][0]
    coordinator.release("shared", job_id=contender_job_id)
    assert coordinator.active("shared") == owner

    coordinator.release("shared", job_id=owner.job_id)
    retry = coordinator.acquire("shared", job_id=contender_job_id, kind="optimiser")
    assert coordinator.active("shared") == retry


def test_singleflight_different_keys_acquire_independently_under_contention() -> None:
    coordinator = SingleFlightCoordinator()
    barrier = threading.Barrier(12)
    handles: list[SingleFlightHandle] = []
    failures: list[BaseException] = []
    result_lock = threading.Lock()

    def acquire(index: int) -> None:
        barrier.wait()
        try:
            handle = coordinator.acquire(
                ("source", index),
                job_id=f"job-{index}",
                kind="cache",
            )
        except BaseException as exc:
            with result_lock:
                failures.append(exc)
        else:
            with result_lock:
                handles.append(handle)

    threads = [threading.Thread(target=acquire, args=(index,)) for index in range(barrier.parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert len(handles) == barrier.parties
    for index in range(barrier.parties):
        handle = coordinator.active(("source", index))
        assert handle is not None
        assert handle.job_id == f"job-{index}"
