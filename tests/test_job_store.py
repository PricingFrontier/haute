"""Tests for the shared in-memory JobStore.

Covers basic CRUD operations, TTL eviction, and concurrent access patterns
to verify dict-backed mutation doesn't lose data under threading.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from haute.routes._job_lifecycle import JobLifecycle, TerminalReason
from haute.routes._job_store import (
    _DEFAULT_HEAVY_OBJECT_TTL_SECONDS,
    _DEFAULT_TTL_SECONDS,
    JOB_STATUSES,
    JobStore,
    RunningJobFields,
    get_job_store,
    register_artifact_cleaner,
)


def _manual_timer_factory(timers: list[object]) -> type:
    class ManualTimer:
        def __init__(self, delay: float, callback) -> None:
            self.delay = delay
            self.callback = callback
            self.daemon = False
            self.started = False
            self.cancelled = False

        def start(self) -> None:
            self.started = True
            timers.append(self)

        def cancel(self) -> None:
            self.cancelled = True

        def fire(self) -> None:
            if not getattr(self, "cancelled", False):
                self.callback()

        def force_fire(self) -> None:
            self.callback()

    return ManualTimer


def _job_store_without_cleanup_threads(**kwargs) -> JobStore:
    return JobStore(heavy_object_timer_factory=_manual_timer_factory([]), **kwargs)


def _stored_job(store: JobStore, job_id: str) -> dict[str, Any]:
    """Inspect backing state only in tests of private cleanup/timer mechanics."""
    return store._jobs[job_id]  # noqa: SLF001


def _create_job(store: JobStore, initial: dict[str, Any]) -> str:
    """Build test records through the same valid lifecycle as production.

    Historical tests created terminal dictionaries directly. Keeping their
    payload setup concise is useful, but terminal state must now pass through
    the public transition boundary so timers, timestamps, and activity state
    are exercised rather than bypassed.
    """
    record = dict(initial)
    status = record.pop("status", None)
    if status not in JOB_STATUSES:
        raise ValueError(f"Test record uses unknown job status: {status!r}")
    terminal_reason = record.pop("terminal_reason", None)
    ended_at = record.pop("ended_at", None)
    completed_at = record.pop("completed_at", None)
    running = cast(RunningJobFields, {"status": "running", **record})
    job_id = JobStore.create_job(store, running)
    if status == "running":
        return job_id
    if terminal_reason is not None and terminal_reason != status:
        raise ValueError("Test terminal_reason must match status")
    timestamp = completed_at if status == "completed" else ended_at
    transitioned = JobLifecycle(store).transition(
        job_id,
        to=cast(TerminalReason, status),
        now=time.time() if timestamp is None else float(timestamp),
    )
    assert transitioned is not None
    return job_id


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


class TestJobStoreCRUD:
    """Unit tests for create, read, update, and list operations."""

    def test_create_job_returns_id(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        assert isinstance(job_id, str)
        assert len(job_id) == 12
        assert job_id.isalnum()

    def test_get_job_returns_stored_data(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "model": "glm"})
        result = store.get_job(job_id)
        assert result is not None
        assert result["status"] == "running"
        assert result["model"] == "glm"
        assert "created_at" in result

    def test_get_job_returns_none_for_unknown_id(self) -> None:
        store = JobStore()
        assert store.get_job("nonexistent") is None

    def test_update_job_merges_fields(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0})
        store.update_job(job_id, progress=50)
        result = store.get_job(job_id)
        assert result is not None
        assert result["status"] == "running"
        assert result["progress"] == 50

    def test_update_job_raises_for_unknown_id(self) -> None:
        store = JobStore()
        with pytest.raises(KeyError):
            store.update_job("nonexistent", progress=50)

    def test_list_jobs_via_property(self) -> None:
        store = JobStore()
        id1 = _create_job(store, {"status": "running"})
        id2 = _create_job(store, {"status": "running"})
        jobs = store.list_jobs()
        assert id1 in jobs
        assert id2 in jobs
        assert len(jobs) == 2
        with pytest.raises(TypeError):
            cast(dict[str, Any], jobs)["other"] = jobs[id1]

    @pytest.mark.parametrize("status", [None, "pending", "completed", "zzz"])
    def test_create_job_rejects_non_running_status(self, status: object) -> None:
        store = JobStore()

        with pytest.raises(ValueError, match="status 'running'"):
            store.create_job(cast(RunningJobFields, {"status": status}))

        assert dict(store.list_jobs()) == {}

    def test_create_job_accepts_equal_non_interned_running_status(self) -> None:
        store = JobStore()
        running_status = "".join(["run", "ning"])

        job_id = store.create_job(cast(RunningJobFields, {"status": running_status}))

        assert store.require_job(job_id)["status"] == "running"
        assert job_id in store._running_activity_at  # noqa: SLF001

    @pytest.mark.parametrize("created_at", [True, -1, float("nan"), float("inf"), "1"])
    def test_create_job_rejects_invalid_created_at(self, created_at: object) -> None:
        store = JobStore()

        with pytest.raises(ValueError, match="created_at"):
            store.create_job(
                cast(RunningJobFields, {"status": "running", "created_at": created_at})
            )

        assert dict(store.list_jobs()) == {}

    @pytest.mark.parametrize("field", ["terminal_reason", "ended_at", "completed_at"])
    def test_create_job_rejects_terminal_metadata(self, field: str) -> None:
        store = JobStore()
        initial: dict[str, Any] = {"status": "running", field: "completed"}

        with pytest.raises(ValueError, match="terminal metadata"):
            store.create_job(cast(RunningJobFields, initial))

    def test_snapshot_is_top_level_immutable_and_nested_values_are_detached(self) -> None:
        store = JobStore()
        payload = {"values": [1], "labels": {"original"}}
        job_id = _create_job(
            store,
            {"status": "running", "payload": payload},
        )

        snapshot = store.require_job(job_id)
        with pytest.raises(TypeError):
            cast(dict[str, Any], snapshot)["progress"] = 1.0
        snapshot["payload"]["values"].append(2)
        snapshot["payload"]["labels"].add("snapshot-only")

        persisted = store.require_job(job_id)
        assert persisted["payload"] == {"values": [1], "labels": {"original"}}

    def test_snapshot_dataclass_enforces_immutable_slot_and_mapping_contracts(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})

        first = store.require_job(job_id)
        second = store.require_job(job_id)

        assert not hasattr(first, "__dict__")
        assert first == second
        assert first == dict(first)
        with pytest.raises(FrozenInstanceError):
            first._record = {}  # type: ignore[misc]  # noqa: SLF001

    def test_create_and_update_take_ownership_of_nested_builtin_values(self) -> None:
        store = JobStore()
        initial_values = [1]
        job_id = _create_job(
            store,
            {"status": "running", "payload": {"values": initial_values}},
        )
        initial_values.append(2)

        update_values = [3]
        store.atomic_update(job_id, {"other": {"values": update_values}})
        update_values.append(4)

        job = store.require_job(job_id)
        assert job["payload"] == {"values": [1]}
        assert job["other"] == {"values": [3]}

    def test_snapshot_detachment_preserves_cycles_and_opaque_identity(self) -> None:
        store = JobStore()
        cycle: list[Any] = []
        cycle.append(cycle)
        opaque = object()
        nested_opaque = object()
        job_id = _create_job(
            store,
            {
                "status": "running",
                "cycle": cycle,
                "opaque": opaque,
                "nested": [nested_opaque],
            },
        )

        snapshot = store.require_job(job_id)
        detached_cycle = snapshot["cycle"]
        assert detached_cycle is detached_cycle[0]
        assert snapshot["opaque"] is opaque
        assert snapshot["nested"][0] is nested_opaque
        detached_cycle.append("snapshot-only")

        persisted_cycle = store.require_job(job_id)["cycle"]
        assert persisted_cycle is persisted_cycle[0]
        assert len(persisted_cycle) == 1

    @pytest.mark.parametrize(
        "field",
        ["status", "terminal_reason", "created_at", "ended_at", "completed_at"],
    )
    def test_generic_updates_reject_lifecycle_fields_without_mutation(self, field: str) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.25})
        before = store.require_job(job_id)

        with pytest.raises(ValueError, match="lifecycle fields"):
            store.atomic_update(job_id, {field: "invalid"})

        assert store.require_job(job_id) == before

    def test_read_fails_loudly_for_corrupt_backing_status(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        store._jobs[job_id] = {**store._jobs[job_id], "status": "unknown"}  # noqa: SLF001

        with pytest.raises(ValueError, match="invalid status"):
            store.get_job(job_id)

    @pytest.mark.parametrize(
        ("status", "terminal_reason"),
        [("error", "superseded"), ("superseded", "error")],
    )
    def test_read_rejects_mismatched_terminal_reason_regardless_of_lexical_order(
        self,
        status: str,
        terminal_reason: str,
    ) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        store._jobs[job_id] = {  # noqa: SLF001 - corrupt-record boundary witness
            "status": status,
            "terminal_reason": terminal_reason,
            "created_at": time.time(),
            "ended_at": time.time(),
        }

        with pytest.raises(ValueError, match="reason must match"):
            store.get_job(job_id)

    def test_read_accepts_distinct_equal_terminal_status_and_reason_strings(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        status = "".join(["err", "or"])
        terminal_reason = "".join(["er", "ror"])
        assert status == terminal_reason and status is not terminal_reason
        store._jobs[job_id] = {  # noqa: SLF001 - restored-record boundary witness
            "status": status,
            "terminal_reason": terminal_reason,
            "created_at": time.time(),
            "ended_at": time.time(),
        }

        assert store.require_job(job_id)["status"] == "error"

    def test_read_rejects_dynamic_completed_record_without_completed_timestamp(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        completed_status = "".join(["com", "pleted"])
        store._jobs[job_id] = {  # noqa: SLF001 - corrupt-record boundary witness
            "status": completed_status,
            "terminal_reason": completed_status,
            "created_at": time.time(),
            "ended_at": time.time(),
        }

        with pytest.raises(ValueError, match="missing completed_at"):
            store.get_job(job_id)

    def test_create_job_sets_created_at_if_missing(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        result = store.get_job(job_id)
        assert result is not None
        assert "created_at" in result
        assert isinstance(result["created_at"], float)

    def test_create_job_preserves_explicit_created_at(self) -> None:
        store = JobStore()
        ts = time.time()  # must be recent enough to survive TTL eviction
        job_id = _create_job(store, {"status": "running", "created_at": ts})
        result = store.get_job(job_id)
        assert result is not None
        assert result["created_at"] == ts

    def test_unique_ids_across_many_jobs(self) -> None:
        store = JobStore()
        ids = {_create_job(store, {"status": "running"}) for _ in range(100)}
        assert len(ids) == 100  # all unique

    def test_default_ttl_is_24_hours(self) -> None:
        store = JobStore()
        assert _DEFAULT_TTL_SECONDS == 24 * 60 * 60
        assert store._ttl_seconds == _DEFAULT_TTL_SECONDS

    def test_invalid_ttl_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            JobStore(ttl_seconds=-1)

    def test_zero_ttl_is_valid(self) -> None:
        store = _job_store_without_cleanup_threads(ttl_seconds=0)

        assert store._ttl_seconds == 0  # noqa: SLF001

    def test_clear_all_removes_orphaned_auxiliary_state(self) -> None:
        timers: list[object] = []
        timer_type = _manual_timer_factory(timers)
        store = JobStore(heavy_object_timer_factory=timer_type)
        live_id = _create_job(store, {"status": "running"})
        job_id = _create_job(store, {"status": "running"})
        orphan_timer = timer_type(10.0, lambda: None)
        with store._write_lock:  # noqa: SLF001 - deliberate orphan-state regression
            store._jobs.pop(job_id)  # noqa: SLF001
            store._heavy_object_timers[job_id] = orphan_timer  # noqa: SLF001

        store.clear_all()

        assert live_id not in store.list_jobs()
        assert store._running_activity_at == {}  # noqa: SLF001
        assert store._heavy_object_timers == {}  # noqa: SLF001
        assert orphan_timer.cancelled is True


# ---------------------------------------------------------------------------
# TTL eviction
# ---------------------------------------------------------------------------


class TestJobStoreTTL:
    """Tests for time-based eviction."""

    def test_stale_jobs_are_evicted_on_create(self) -> None:
        store = JobStore(ttl_seconds=1)
        old_id = _create_job(store, {"status": "completed", "created_at": time.time() - 10})
        # Creating a new job triggers eviction
        _new_id = _create_job(store, {"status": "running"})
        assert store.get_job(old_id) is None

    def test_stale_jobs_are_evicted_on_get(self) -> None:
        store = JobStore(ttl_seconds=1)
        old_id = _create_job(store, {"status": "completed", "created_at": time.time() - 10})
        # get_job triggers eviction
        assert store.get_job(old_id) is None

    def test_fresh_jobs_survive_eviction(self) -> None:
        store = JobStore(ttl_seconds=60)
        job_id = _create_job(store, {"status": "running"})
        _trigger = _create_job(store, {"status": "running"})
        assert store.get_job(job_id) is not None

    def test_job_at_exact_ttl_boundary_is_still_live(self) -> None:
        store = JobStore(ttl_seconds=10)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(store, {"status": "completed", "created_at": 90.0})

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job = store.get_job(job_id)

        assert job is not None
        assert job["status"] == "completed"

    @pytest.mark.parametrize(
        ("status", "expected_timestamp", "keeps_activity"),
        [
            ("".join(["run", "ning"]), 20.0, True),
            ("completed", 10.0, False),
            ("superseded", 10.0, False),
        ],
    )
    def test_eviction_timestamp_uses_exact_status_value_equality(
        self,
        status: str,
        expected_timestamp: float,
        keeps_activity: bool,
    ) -> None:
        store = JobStore(ttl_seconds=10)
        job_id = "job-id"
        store._running_activity_at[job_id] = 20.0  # noqa: SLF001
        job: dict[str, Any] = {"status": status, "created_at": 10.0}

        timestamp = store._job_eviction_timestamp_locked(job_id, job)  # noqa: SLF001

        assert timestamp == expected_timestamp
        assert (job_id in store._running_activity_at) is keeps_activity  # noqa: SLF001

    def test_mixed_stale_and_fresh(self) -> None:
        store = JobStore(ttl_seconds=5)
        stale_id = _create_job(store, {"status": "completed", "created_at": time.time() - 100})
        fresh_id = _create_job(store, {"status": "running"})
        # Trigger eviction via a new create
        _create_job(store, {"status": "running"})
        assert store.get_job(stale_id) is None
        assert store.get_job(fresh_id) is not None

    def test_stale_job_artifacts_are_removed_on_eviction(self, tmp_path: Path) -> None:
        store = JobStore(ttl_seconds=1)
        kind = "test_job_store_cleanup_artifact"
        artifact_dir = tmp_path / "haute_opt_apply_test"
        artifact_dir.mkdir()
        artifact_path = artifact_dir / "result.parquet"
        artifact_path.write_bytes(b"artifact")
        cleaned: list[str] = []

        def cleaner(handle: dict) -> None:
            cleaned.append(handle["path"])
            shutil.rmtree(handle["directory"])

        register_artifact_cleaner(kind, cleaner)

        job_id = _create_job(
            store,
            {
                "status": "completed",
                "created_at": time.time() - 10,
                "artifact_handles": {
                    "apply_result": {
                        "kind": kind,
                        "version": 1,
                        "format": "parquet",
                        "path": str(artifact_path),
                        "directory": str(artifact_dir),
                    }
                },
            },
        )

        assert artifact_path.exists()
        assert store.get_job(job_id) is None
        assert cleaned == [str(artifact_path)]
        assert not artifact_dir.exists()

    def test_artifact_cleanup_runs_after_evicted_job_state_is_detached(self) -> None:
        timers: list[object] = []
        store = JobStore(
            ttl_seconds=1,
            heavy_object_ttl_seconds=100,
            heavy_object_timer_factory=_manual_timer_factory(timers),
        )
        kind = "test_job_store_cleanup_outside_write_lock"
        cleanup_started = threading.Event()
        concurrent_access_finished = threading.Event()
        cleanup_observations: list[tuple[bool, bool, bool]] = []
        job_id = ""

        def cleaner(_handle: dict) -> None:
            cleanup_started.set()
            cleanup_observations.append(
                (
                    job_id not in store.list_jobs(),
                    job_id not in store._heavy_object_timers,
                    concurrent_access_finished.wait(timeout=5),
                )
            )

        register_artifact_cleaner(kind, cleaner)
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "created_at": time.time() - 10,
                "solver": object(),
                "artifact_handles": {
                    "result": {
                        "kind": kind,
                        "version": 1,
                        "path": "unused",
                    }
                },
            },
        )
        assert len(timers) == 1

        eviction = threading.Thread(target=store.get_job, args=(job_id,))
        eviction.start()
        assert cleanup_started.wait(timeout=1)

        def access_store() -> None:
            _create_job(store, {"status": "running"})
            concurrent_access_finished.set()

        concurrent_access = threading.Thread(target=access_store)
        concurrent_access.start()
        eviction.join(timeout=6)
        concurrent_access.join(timeout=6)

        assert not eviction.is_alive()
        assert not concurrent_access.is_alive()
        assert cleanup_observations == [(True, True, True)]
        assert timers[0].cancelled is True

    def test_nested_store_operation_defers_cleanup_until_outer_lock_releases(self) -> None:
        store = _job_store_without_cleanup_threads(ttl_seconds=1)
        kind = "test_job_store_nested_cleanup_outside_write_lock"
        cleanup_started = threading.Event()
        concurrent_access_finished = threading.Event()
        cleanup_observations: list[bool] = []
        results: list[bool] = []

        def cleaner(_handle: dict) -> None:
            cleanup_started.set()
            cleanup_observations.append(concurrent_access_finished.wait(timeout=5))

        register_artifact_cleaner(kind, cleaner)
        _create_job(store, {"status": "running"})
        stale_id = _create_job(
            store,
            {
                "status": "completed",
                "artifact_handles": {
                    "result": {
                        "kind": kind,
                        "version": 1,
                        "path": "unused",
                    }
                },
            },
        )

        def nested_predicate(_job: dict[str, Any]) -> bool:
            # Deliberate white-box ageing while the predicate already holds the
            # store lock; public snapshots must not permit this mutation.
            store._jobs[stale_id] = {  # noqa: SLF001
                **store._jobs[stale_id],  # noqa: SLF001
                "created_at": time.time() - 10,
            }
            assert store.get_job(stale_id) is None
            return True

        outer_operation = threading.Thread(
            target=lambda: results.append(store.has_job_matching(nested_predicate))
        )
        outer_operation.start()
        assert cleanup_started.wait(timeout=5)

        def access_store() -> None:
            _create_job(store, {"status": "running"})
            concurrent_access_finished.set()

        concurrent_access = threading.Thread(target=access_store)
        concurrent_access.start()
        outer_operation.join(timeout=7)
        concurrent_access.join(timeout=7)

        assert not outer_operation.is_alive()
        assert not concurrent_access.is_alive()
        assert results == [True]
        assert cleanup_observations == [True]

    @pytest.mark.parametrize("operation", ["matching_predicate", "create", "touch"])
    def test_detached_artifacts_are_cleaned_when_post_eviction_work_raises(
        self,
        operation: str,
    ) -> None:
        store = _job_store_without_cleanup_threads(ttl_seconds=1)
        kind = f"test_job_store_cleanup_after_{operation}_failure"
        cleaned: list[str] = []

        def cleaner(handle: dict) -> None:
            cleaned.append(handle["path"])

        register_artifact_cleaner(kind, cleaner)
        if operation == "touch":
            target_id = _create_job(store, {"status": "completed", "payload": object()})
        else:
            target_id = _create_job(store, {"status": "running"})
        stale_id = _create_job(
            store,
            {
                "status": "completed",
                "created_at": time.time() - 10,
                "artifact_handles": {
                    "result": {
                        "kind": kind,
                        "version": 1,
                        "path": operation,
                    }
                },
            },
        )

        if operation == "matching_predicate":

            def raise_from_predicate(_job: dict[str, Any]) -> bool:
                raise RuntimeError("predicate failed")

            with pytest.raises(RuntimeError, match="predicate failed"):
                store.has_job_matching(raise_from_predicate)
        elif operation == "create":
            with pytest.raises(TypeError):
                _create_job(store, cast(dict[str, Any], None))
        else:

            class FailingRequiredKeys:
                def __iter__(self):
                    raise ValueError("required-key iteration failed")

            with pytest.raises(ValueError, match="required-key iteration failed"):
                store.touch_heavy_objects(
                    target_id,
                    required_keys=cast(tuple[str, ...], FailingRequiredKeys()),
                )

        assert stale_id not in store.list_jobs()
        assert cleaned == [operation]

    def test_detached_artifacts_are_cleaned_if_timer_cancellation_raises(self) -> None:
        class FailingCancelTimer:
            def __init__(self, _delay: float, callback) -> None:
                self.callback = callback
                self.daemon = False

            def start(self) -> None:
                return None

            def cancel(self) -> None:
                raise RuntimeError("timer cancellation failed")

        store = JobStore(
            ttl_seconds=1,
            heavy_object_ttl_seconds=100,
            heavy_object_timer_factory=FailingCancelTimer,
        )
        kind = "test_job_store_cleanup_after_timer_cancel_failure"
        cleaned: list[str] = []

        def cleaner(handle: dict) -> None:
            cleaned.append(handle["path"])

        register_artifact_cleaner(kind, cleaner)
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "created_at": time.time() - 10,
                "solver": object(),
                "artifact_handles": {
                    "result": {
                        "kind": kind,
                        "version": 1,
                        "path": "result",
                    }
                },
            },
        )

        with pytest.raises(RuntimeError, match="timer cancellation failed"):
            store.get_job(job_id)

        assert job_id not in store.list_jobs()
        assert job_id not in store._heavy_object_timers
        assert cleaned == ["result"]

    def test_distinct_equal_artifact_cleaners_are_rejected(self) -> None:
        kind = "test_job_store_equal_cleaner_identity"

        class EqualCleaner:
            def __call__(self, _handle: dict) -> None:
                return None

            def __eq__(self, _other: object) -> bool:
                return True

        register_artifact_cleaner(kind, EqualCleaner())

        with pytest.raises(RuntimeError, match="already registered"):
            register_artifact_cleaner(kind, EqualCleaner())

    def test_stale_job_path_only_artifacts_are_not_deleted_without_registered_cleaner(
        self,
        tmp_path: Path,
    ) -> None:
        store = JobStore(ttl_seconds=1)
        artifact_path = tmp_path / "apply_result.parquet"
        artifact_path.write_bytes(b"artifact")

        job_id = _create_job(
            store,
            {
                "status": "completed",
                "created_at": time.time() - 10,
                "artifact_handles": {
                    "apply_result": {
                        "kind": "unregistered_test_artifact",
                        "version": 1,
                        "format": "parquet",
                        "path": str(artifact_path),
                    }
                },
            },
        )

        with patch("haute.routes._job_store.logger.warning") as log_warning:
            assert store.get_job(job_id) is None

        assert artifact_path.exists()
        log_warning.assert_called_once()
        assert log_warning.call_args.args == ("job_artifact_cleanup_unknown_handle_kind",)
        assert log_warning.call_args.kwargs["job_id"] == job_id

    def test_unknown_artifact_handle_does_not_skip_later_registered_cleanup(self) -> None:
        cleaned: list[str] = []
        known_kind = "test_job_store_cleanup_after_unknown_handle"

        def cleaner(handle: dict[str, Any]) -> None:
            cleaned.append(cast(str, handle["path"]))

        register_artifact_cleaner(known_kind, cleaner)
        handles = (
            {"kind": "unregistered_handle_before_known"},
            {"kind": known_kind, "path": "later-result"},
        )

        with patch("haute.routes._job_store.logger.warning") as log_warning:
            JobStore._cleanup_artifact_handles("job-id", handles)  # noqa: SLF001

        log_warning.assert_called_once()
        assert cleaned == ["later-result"]

    def test_stale_job_artifact_cleanup_failure_is_observable(
        self,
        tmp_path: Path,
    ) -> None:
        store = JobStore(ttl_seconds=1)
        kind = "test_job_store_cleanup_failure"
        artifact_dir = tmp_path / "locked_apply_result"
        artifact_dir.mkdir()
        artifact_path = artifact_dir / "result.parquet"
        artifact_path.write_bytes(b"artifact")

        def cleaner(_handle: dict) -> None:
            raise OSError("locked")

        register_artifact_cleaner(kind, cleaner)

        job_id = _create_job(
            store,
            {
                "status": "completed",
                "created_at": time.time() - 10,
                "artifact_handles": {
                    "apply_result": {
                        "kind": kind,
                        "version": 1,
                        "format": "parquet",
                        "path": str(artifact_path),
                        "directory": str(artifact_dir),
                    }
                },
            },
        )

        with patch("haute.routes._job_store.logger.warning") as log_warning:
            assert store.get_job(job_id) is None

        log_warning.assert_called_once()
        assert log_warning.call_args.args == ("job_artifact_cleanup_failed",)
        assert log_warning.call_args.kwargs["job_id"] == job_id
        assert log_warning.call_args.kwargs["path"] == str(artifact_dir)
        assert log_warning.call_args.kwargs["kind"] == kind
        assert log_warning.call_args.kwargs["exc_info"] is True

    def test_stale_job_path_cleanup_failure_is_observable_through_registered_cleaner(
        self,
        tmp_path: Path,
    ) -> None:
        store = JobStore(ttl_seconds=1)
        kind = "test_job_store_path_cleanup_failure"
        artifact_path = tmp_path / "locked_apply_result.parquet"
        artifact_path.write_bytes(b"artifact")

        def cleaner(_handle: dict) -> None:
            raise OSError("locked")

        register_artifact_cleaner(kind, cleaner)

        job_id = _create_job(
            store,
            {
                "status": "completed",
                "created_at": time.time() - 10,
                "artifact_handles": {
                    "apply_result": {
                        "kind": kind,
                        "version": 1,
                        "format": "parquet",
                        "path": str(artifact_path),
                    }
                },
            },
        )

        with patch("haute.routes._job_store.logger.warning") as log_warning:
            assert store.get_job(job_id) is None

        log_warning.assert_called_once()
        assert log_warning.call_args.args == ("job_artifact_cleanup_failed",)
        assert log_warning.call_args.kwargs["job_id"] == job_id
        assert log_warning.call_args.kwargs["path"] == str(artifact_path)
        assert log_warning.call_args.kwargs["kind"] == kind
        assert log_warning.call_args.kwargs["error"] == "locked"
        assert log_warning.call_args.kwargs["exc_info"] is True


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestJobStoreConcurrency:
    """Concurrency tests using threading to verify dict mutation safety."""

    def test_concurrent_creates_all_tracked(self) -> None:
        """Submit 10 jobs simultaneously and verify all are tracked."""
        store = JobStore()
        job_ids: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(10)

        def create_one(idx: int) -> None:
            barrier.wait()  # all threads start together
            jid = _create_job(store, {"status": "running", "index": idx})
            with lock:
                job_ids.append(jid)

        threads = [threading.Thread(target=create_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(job_ids) == 10
        assert len(set(job_ids)) == 10  # all unique

        # Every job should be retrievable
        for jid in job_ids:
            result = store.get_job(jid)
            assert result is not None
            assert result["status"] == "running"

    def test_concurrent_updates_no_data_loss(self) -> None:
        """Concurrent status updates to different jobs don't cause data loss."""
        store = JobStore()
        n_jobs = 10
        ids = [_create_job(store, {"status": "running", "counter": 0}) for _ in range(n_jobs)]
        barrier = threading.Barrier(n_jobs)

        def update_one(job_id: str, value: int) -> None:
            barrier.wait()
            store.update_job(job_id, counter=value)

        threads = [threading.Thread(target=update_one, args=(ids[i], i + 1)) for i in range(n_jobs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All jobs should have been updated
        for i, jid in enumerate(ids):
            result = store.get_job(jid)
            assert result is not None
            assert result["status"] == "running"
            assert result["counter"] == i + 1

    def test_concurrent_updates_to_same_job(self) -> None:
        """Multiple threads updating the same job's fields concurrently.

        Each thread increments a different field, so no updates should be lost.
        """
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        n_threads = 10
        barrier = threading.Barrier(n_threads)

        def update_field(idx: int) -> None:
            barrier.wait()
            store.update_job(job_id, **{f"field_{idx}": idx})

        threads = [threading.Thread(target=update_field, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        result = store.get_job(job_id)
        assert result is not None
        for i in range(n_threads):
            assert result[f"field_{i}"] == i

    def test_concurrent_create_and_read(self) -> None:
        """Interleave creates and reads without errors."""
        store = JobStore()
        n_ops = 20
        created_ids: list[str] = []
        read_results: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(n_ops)

        def create_and_read(idx: int) -> None:
            barrier.wait()
            if idx % 2 == 0:
                jid = _create_job(store, {"status": "running", "idx": idx})
                with lock:
                    created_ids.append(jid)
            else:
                # Read a job that may or may not exist yet
                with lock:
                    target = created_ids[-1] if created_ids else "nonexistent"
                result = store.get_job(target)
                with lock:
                    read_results.append(result is not None or target == "nonexistent")

        threads = [threading.Thread(target=create_and_read, args=(i,)) for i in range(n_ops)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # No exceptions raised, all created jobs are accessible after threads finish
        for jid in created_ids:
            assert store.get_job(jid) is not None

    def test_concurrent_creates_with_eviction(self) -> None:
        """Concurrent creates with a short TTL trigger eviction under contention."""
        store = JobStore(ttl_seconds=1)
        # Pre-populate with stale jobs that will be evicted
        for _ in range(5):
            _create_job(store, {"status": "completed", "created_at": time.time() - 10})

        n_threads = 10
        new_ids: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(n_threads)

        def create_with_eviction(idx: int) -> None:
            barrier.wait()
            jid = _create_job(store, {"status": "running", "idx": idx})
            with lock:
                new_ids.append(jid)

        threads = [
            threading.Thread(target=create_with_eviction, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All new jobs should be present; stale ones evicted
        assert len(new_ids) == n_threads
        for jid in new_ids:
            result = store.get_job(jid)
            assert result is not None
            assert result["status"] == "running"

    def test_atomic_update_if_heavy_present_serialises_against_ttl_eviction(self) -> None:
        """The route handlers (``/apply``, ``/select``, ``/save``,
        ``/mlflow/log``) call ``atomic_update_if_heavy_present`` to commit
        a result while a timer thread may simultaneously be expiring the
        same job's heavy state.  Both paths take the write lock — but the
        guarantee we rely on is stronger than "no torn writes": the
        observed end state must be self-consistent, i.e. either:

        - The atomic update wins and the merged job has the heavy keys
          AND the new fields, OR
        - The eviction wins and the route sees ``None`` (because
          ``required_keys`` are missing) so the user gets a clean 400.

        A regression that re-orders the lock acquisition or splits the
        check-and-write would let a reader observe a job that has the
        new fields but has lost its heavy state — which would surface
        downstream as an opaque ``None`` access.

        We run real threads under a barrier and assert the end state is
        valid in both possible orderings.
        """
        n_runs = 30  # Repeat to shake out scheduling-order variation.
        for run in range(n_runs):
            store = _job_store_without_cleanup_threads()
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "completed_at": time.time(),
                    # Heavy objects the timer is about to clear.
                    "solver": object(),
                    "quote_grid": object(),
                    # Force expiry: ``_clear_expired_heavy_objects`` will
                    # consider this job's heavy state due now.
                    "heavy_objects_expires_at": time.time() - 1.0,
                },
            )

            barrier = threading.Barrier(2)
            results: dict[str, object] = {}
            errors: list[BaseException] = []

            def updater() -> None:
                try:
                    barrier.wait()
                    results["update"] = store.atomic_update_if_heavy_present(
                        job_id,
                        {
                            "result": {"total_objective": 42.0},
                            "selected_frontier_point": 0,
                        },
                        required_keys=("solver", "quote_grid"),
                        expected_status="completed",
                    )
                except BaseException as exc:  # noqa: BLE001 - report thread failures
                    errors.append(exc)

            def evictor() -> None:
                try:
                    barrier.wait()
                    store._clear_expired_heavy_objects(job_id=None, timer=None)
                    results["evict"] = "done"
                except BaseException as exc:  # noqa: BLE001 - report thread failures
                    errors.append(exc)

            threads = [
                threading.Thread(target=updater),
                threading.Thread(target=evictor),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert errors == [], f"Race threads raised: {errors}"
            assert all(not t.is_alive() for t in threads), f"Race threads hung on run {run}"
            assert results.get("evict") == "done"
            assert "update" in results
            update_outcome = results.get("update")
            final = store.get_job(job_id)
            assert final is not None, f"run {run}: job evicted entirely"

            if update_outcome is None:
                # The evictor won the race.  The route would respond 400.
                # The job survived but heavy keys are gone — and crucially
                # the new ``result`` field was NOT merged.
                assert "solver" not in final, (
                    f"run {run}: update reported failure but heavy state survived"
                )
                assert "quote_grid" not in final
                assert "result" not in final, (
                    f"run {run}: update reported failure but partial fields were "
                    f"persisted: {final.get('result')!r}"
                )
            else:
                # The updater won — heavy keys are still present AND the
                # new fields are merged.
                assert final["solver"] is not None
                assert final["quote_grid"] is not None
                assert final["result"] == {"total_objective": 42.0}
                assert final["selected_frontier_point"] == 0
                # The state returned to the caller matches the persisted state.
                assert update_outcome["result"] == final["result"]
                assert update_outcome["selected_frontier_point"] == 0

    def test_terminal_transition_is_optimistic_lock(self) -> None:
        """The lifecycle transition must behave as an optimistic lock under
        real concurrency: when N threads simultaneously try to complete the
        same running job, exactly one wins and the rest receive ``None``.

        This is the exact race the route handlers rely on for /apply,
        /select, /save and /mlflow/log.  A regression that drops the
        compare-and-swap semantics — for example, a refactor that always
        merges fields — would corrupt user state in production.  The
        prior tests only stubbed atomic_update with ``return_value=None``;
        this test runs real threads and verifies the lock invariant under
        contention.
        """
        store = JobStore()
        lifecycle = JobLifecycle(store)
        job_id = _create_job(store, {"status": "running", "phase": "init"})

        n_threads = 12
        barrier = threading.Barrier(n_threads)
        lock = threading.Lock()
        winners: list[tuple[int, dict]] = []
        losers: list[int] = []
        errors: list[BaseException] = []

        def race(idx: int) -> None:
            try:
                barrier.wait()
                # Each thread tries to transition running → completed and
                # stamps its identifier on the result so we can identify
                # the winner unambiguously.
                outcome = lifecycle.transition(
                    job_id,
                    to="completed",
                    fields={"winner": idx, "phase": f"done-{idx}"},
                )
                with lock:
                    if outcome is None:
                        losers.append(idx)
                    else:
                        winners.append((idx, outcome))
            except BaseException as exc:  # noqa: BLE001 — record any exception
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=race, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # No thread crashed.
        assert errors == [], f"Race threads raised: {errors}"
        # Exactly one thread won the compare-and-swap.
        assert len(winners) == 1, (
            f"Expected exactly 1 winner, got {len(winners)}: {[w[0] for w in winners]}"
        )
        # Every other thread saw the conflict signal (None).
        assert len(losers) == n_threads - 1, f"Expected {n_threads - 1} losers, got {len(losers)}"
        # The winner's identity matches the persisted state.
        winner_idx, winner_state = winners[0]
        assert winner_state["winner"] == winner_idx
        assert winner_state["phase"] == f"done-{winner_idx}"

        # The store reflects the winner — no loser ever wrote.
        final = store.get_job(job_id)
        assert final is not None
        assert final["status"] == "completed"
        assert final["winner"] == winner_idx
        assert final["phase"] == f"done-{winner_idx}"


# ---------------------------------------------------------------------------
# require_job
# ---------------------------------------------------------------------------


class TestRequireJob:
    """Tests for require_job — raises HTTP 404 for missing jobs."""

    def test_returns_existing_job(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.5})
        job = store.require_job(job_id)
        assert job["status"] == "running"
        assert job["progress"] == 0.5

    def test_raises_404_for_missing_job(self) -> None:
        from fastapi import HTTPException

        store = JobStore()
        with pytest.raises(HTTPException) as exc_info:
            store.require_job("nonexistent_id")
        assert exc_info.value.status_code == 404
        assert "nonexistent_id" in exc_info.value.detail

    def test_raises_404_for_evicted_job(self) -> None:
        from fastapi import HTTPException

        store = JobStore(ttl_seconds=1)
        job_id = _create_job(store, {"status": "completed", "created_at": time.time() - 10})
        with pytest.raises(HTTPException) as exc_info:
            store.require_job(job_id)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# require_completed_job
# ---------------------------------------------------------------------------


class TestRequireCompletedJob:
    """Tests for require_completed_job — fetch + status check in one call."""

    def test_returns_completed_job(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "completed", "result": {"score": 0.95}})
        job = store.require_completed_job(job_id)
        assert job["status"] == "completed"
        assert job["result"] == {"score": 0.95}

    def test_raises_404_for_missing_job(self) -> None:
        from fastapi import HTTPException

        store = JobStore()
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job("nonexistent_id")
        assert exc_info.value.status_code == 404
        assert "nonexistent_id" in exc_info.value.detail

    def test_raises_400_for_running_job(self) -> None:
        from fastapi import HTTPException

        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400
        assert "not completed" in exc_info.value.detail
        assert "running" in exc_info.value.detail

    def test_raises_400_for_cancelled_job(self) -> None:
        from fastapi import HTTPException

        store = JobStore()
        job_id = _create_job(store, {"status": "cancelled"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400
        assert "not completed" in exc_info.value.detail
        assert "cancelled" in exc_info.value.detail

    def test_raises_400_for_error_job(self) -> None:
        from fastapi import HTTPException

        store = JobStore()
        job_id = _create_job(store, {"status": "error", "message": "boom"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400
        assert "not completed" in exc_info.value.detail
        assert "error" in exc_info.value.detail

    def test_create_rejects_missing_status(self) -> None:
        """A record without the required running status fails at creation."""
        store = JobStore()
        with pytest.raises(ValueError, match="status 'running'"):
            store.create_job(cast(RunningJobFields, {"progress": 0.0}))

    def test_detail_includes_job_id(self) -> None:
        """Error messages should include the job ID for debuggability."""
        from fastapi import HTTPException

        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert job_id in exc_info.value.detail

    def test_raises_404_for_evicted_job(self) -> None:
        """An evicted (stale) job should raise 404, not 400."""
        from fastapi import HTTPException

        store = JobStore(ttl_seconds=1)
        job_id = _create_job(store, {"status": "completed", "created_at": time.time() - 10})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        # Should be 404 (not found due to eviction), not 400 (not completed)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# P7: atomic_update — thread-safe dict replacement
# ---------------------------------------------------------------------------


class TestAtomicUpdate:
    """Tests for atomic_update — replaces dict instead of mutating in-place."""

    def test_atomic_update_merges_fields(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.0})
        store.atomic_update(job_id, {"progress": 1.0})
        result = store.get_job(job_id)
        assert result is not None
        assert result["status"] == "running"
        assert result["progress"] == 1.0

    @pytest.mark.parametrize("reason", ["completed", "superseded"])
    def test_terminal_transition_clears_running_activity(self, reason: str) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.0})

        assert job_id in store._running_activity_at

        JobLifecycle(store).transition(
            job_id,
            to=cast(TerminalReason, reason),
            fields={"progress": 1.0},
        )

        assert job_id not in store._running_activity_at

    def test_atomic_update_preserves_existing_keys(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "config": {"x": 1}})
        store.atomic_update(job_id, {"progress": 0.5})
        result = store.get_job(job_id)
        assert result is not None
        assert result["config"] == {"x": 1}
        assert result["progress"] == 0.5

    def test_atomic_update_creates_new_dict(self) -> None:
        """The old dict reference should no longer be the stored one."""
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        old_dict = store.get_job(job_id)
        store.atomic_update(job_id, {"phase": "prepared"})
        new_dict = store.get_job(job_id)
        # The new snapshot is distinct and the previous snapshot stays stable.
        assert old_dict is not new_dict
        assert old_dict is not None and "phase" not in old_dict
        assert new_dict is not None and new_dict["phase"] == "prepared"

    def test_atomic_update_raises_for_unknown_id(self) -> None:
        store = JobStore()
        with pytest.raises(KeyError):
            store.atomic_update("nonexistent", {"progress": 50})

    def test_atomic_update_thread_safety(self) -> None:
        """Concurrent atomic updates should not corrupt the dict.

        NOTE: ``atomic_update`` uses a read-modify-write pattern
        (``old = d[k]; d[k] = {**old, **fields}``) which is NOT
        linearisable under concurrency -- concurrent writers to
        *different* keys can lose each other's updates.  This is
        acceptable for the real workload where only ONE background
        thread writes to a given job and the main thread only reads.

        This test verifies that no exception is raised and the dict
        remains structurally intact.
        """
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})
        n_threads = 20
        barrier = threading.Barrier(n_threads)

        def update_field(idx: int) -> None:
            barrier.wait()
            store.atomic_update(job_id, {f"field_{idx}": idx})

        threads = [threading.Thread(target=update_field, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        result = store.get_job(job_id)
        assert result is not None
        # The dict should still be valid and contain at least the
        # original keys plus whatever the last writer included.
        assert result["status"] == "running"
        # At least one field_N key must be present (the last writer's)
        field_keys = [k for k in result if k.startswith("field_")]
        assert len(field_keys) >= 1

    def test_atomic_update_vs_reader_no_partial_state(self) -> None:
        """A reader should never see a half-updated state.

        We simulate the pattern: a background thread updates phase + progress
        atomically, while main thread reads the job dict repeatedly.
        """
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.0})

        partial_states_seen: list[bool] = []
        stop_event = threading.Event()

        def reader() -> None:
            while not stop_event.is_set():
                job = store.get_job(job_id)
                if job is None:
                    continue
                phase = job.get("phase", "running")
                progress = job.get("progress")
                # A partial state would be: phase is "completed" but
                # progress is still 0.0, or vice versa.
                if phase == "completed" and progress != 1.0:
                    partial_states_seen.append(True)
                if phase == "running" and progress == 1.0:
                    partial_states_seen.append(True)

        def writer() -> None:
            for _ in range(100):
                store.atomic_update(
                    job_id,
                    {
                        "phase": "completed",
                        "progress": 1.0,
                    },
                )
                store.atomic_update(
                    job_id,
                    {
                        "phase": "running",
                        "progress": 0.0,
                    },
                )

        reader_thread = threading.Thread(target=reader, daemon=True)
        writer_thread = threading.Thread(target=writer)
        reader_thread.start()
        writer_thread.start()
        writer_thread.join(timeout=5)
        stop_event.set()
        reader_thread.join(timeout=2)

        assert not partial_states_seen, "Reader saw a partially-updated job dict"


# ---------------------------------------------------------------------------
# P5: clear_result_data — strip heavy objects after consumption
# ---------------------------------------------------------------------------


class TestClearResultData:
    """Tests for clear_result_data — memory cleanup for completed jobs."""

    def test_clears_default_heavy_keys(self) -> None:
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": "heavy_solver_object",
                "solve_result": "heavy_result_object",
                "quote_grid": "heavy_grid_object",
                "config": {"objective": "income"},
                "result": {"converged": True},
            },
        )
        store.clear_result_data(job_id)
        job = store.get_job(job_id)
        assert job is not None
        # Heavy keys should be gone
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job
        # Lightweight keys should remain
        assert job["status"] == "completed"
        assert job["config"] == {"objective": "income"}
        assert job["result"] == {"converged": True}

    def test_clear_default_heavy_keys_removes_expiry_marker(self) -> None:
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": "heavy_solver_object",
                "solve_result": "heavy_result_object",
                "quote_grid": "heavy_grid_object",
                "result": {"converged": True},
            },
        )

        assert "heavy_objects_expires_at" in _stored_job(store, job_id)

        store.clear_result_data(job_id)

        job = store.get_job(job_id)
        assert job is not None
        assert "heavy_objects_expires_at" not in job
        assert "solver" not in job
        assert job["result"] == {"converged": True}

    def test_clears_custom_keys(self) -> None:
        store = JobStore()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "big_thing": "data",
                "another": "thing",
                "keep": "this",
            },
        )
        store.clear_result_data(job_id, keys=("big_thing", "another"))
        job = store.get_job(job_id)
        assert job is not None
        assert "big_thing" not in job
        assert "another" not in job
        assert job["keep"] == "this"

    def test_custom_clear_preserves_expiry_marker_when_heavy_objects_remain(self) -> None:
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": "heavy_solver_object",
                "trace_blob": "large-debug-payload",
                "result": {"converged": True},
            },
        )
        original_expires_at = _stored_job(store, job_id)["heavy_objects_expires_at"]

        store.clear_result_data(job_id, keys=("trace_blob",))

        job = store.get_job(job_id)
        assert job is not None
        assert job["solver"] == "heavy_solver_object"
        assert "trace_blob" not in job
        assert job["heavy_objects_expires_at"] == original_expires_at

    def test_noop_for_missing_job(self) -> None:
        """Should not raise for a nonexistent job ID."""
        store = JobStore()
        store.clear_result_data("nonexistent")  # no exception

    def test_noop_when_keys_already_absent(self) -> None:
        """If heavy keys were never stored, the method is a no-op."""
        store = JobStore()
        job_id = _create_job(store, {"status": "completed", "result": {"ok": True}})
        store.clear_result_data(job_id)
        job = store.get_job(job_id)
        assert job is not None
        assert job["status"] == "completed"
        assert job["result"] == {"ok": True}

    def test_idempotent(self) -> None:
        """Calling clear_result_data twice should not error or change state."""
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": "heavy",
                "solve_result": "heavy",
            },
        )
        store.clear_result_data(job_id)
        store.clear_result_data(job_id)  # second call
        job = store.get_job(job_id)
        assert job is not None
        assert "solver" not in job
        assert job["status"] == "completed"

    def test_clear_uses_atomic_replacement(self) -> None:
        """The old dict reference should not be mutated."""
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": "heavy",
            },
        )
        old_dict = store.get_job(job_id)
        store.clear_result_data(job_id)
        new_dict = store.get_job(job_id)
        # Old dict should still have solver
        assert "solver" in old_dict
        # New dict should not
        assert "solver" not in new_dict
        assert old_dict is not new_dict


# ---------------------------------------------------------------------------
# Phase 6: automatic heavy-object lifecycle policy
# ---------------------------------------------------------------------------


class TestHeavyObjectLifecyclePolicy:
    """Completed jobs keep summaries, but heavy runtime objects age out sooner."""

    @staticmethod
    def _manual_timer_factory(timers: list) -> object:
        return _manual_timer_factory(timers)

    def test_default_heavy_object_retention_is_shorter_than_job_ttl(self) -> None:
        store = JobStore()

        assert _DEFAULT_HEAVY_OBJECT_TTL_SECONDS == 15 * 60
        assert _DEFAULT_HEAVY_OBJECT_TTL_SECONDS < _DEFAULT_TTL_SECONDS
        assert store._heavy_object_ttl_seconds == _DEFAULT_HEAVY_OBJECT_TTL_SECONDS

    def test_zero_heavy_object_retention_is_valid_and_due_immediately(self) -> None:
        store = _job_store_without_cleanup_threads(heavy_object_ttl_seconds=0)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "quote_grid": object(),
                },
            )

        assert _stored_job(store, job_id)["heavy_objects_expires_at"] == pytest.approx(100.0)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job = store.get_job(job_id)

        assert job is not None
        assert "solver" not in job
        assert "quote_grid" not in job
        assert job["heavy_objects_retention_seconds"] == 0

    def test_completed_status_checks_use_value_equality_for_heavy_policy(self) -> None:
        timers: list[object] = []
        store = JobStore(
            heavy_object_ttl_seconds=10,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )
        completed_status = "".join(["com", "pleted"])

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": completed_status,
                    "solver": object(),
                },
            )

        assert _stored_job(store, job_id)["heavy_objects_expires_at"] == pytest.approx(110.0)
        assert len(timers) == 1
        assert timers[0].started is True

    def test_completed_job_heavy_objects_are_slimmed_after_policy_window(self) -> None:
        timers: list = []
        store = JobStore(
            ttl_seconds=60,
            heavy_object_ttl_seconds=1,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(store, {"status": "running", "config": {"objective": "loss"}})
            JobLifecycle(store).transition(
                job_id,
                to="completed",
                fields={
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                    "factors_df": object(),
                    "result": {"total_objective": 123.0},
                    "frontier_data": {"n_points": 0},
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=100.5):
            job = store.get_job(job_id)
        assert job is not None
        assert "solver" in job
        assert "solve_result" in job
        assert "quote_grid" in job
        assert "factors_df" in job

        with patch("haute.routes._job_store.time.time", return_value=102.0):
            job = store.get_job(job_id)

        assert job is not None
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job
        assert "factors_df" not in job
        assert job["status"] == "completed"
        assert job["config"] == {"objective": "loss"}
        assert job["result"] == {"total_objective": 123.0}
        assert job["frontier_data"] == {"n_points": 0}
        assert job["heavy_objects_cleared_at"] == 102.0
        assert job["heavy_objects_retention_seconds"] == 1

    def test_completed_job_schedules_active_heavy_object_cleanup(self) -> None:
        timers: list = []
        store = JobStore(
            ttl_seconds=60,
            heavy_object_ttl_seconds=1,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(store, {"status": "running"})
            JobLifecycle(store).transition(
                job_id,
                to="completed",
                fields={
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        assert len(timers) == 1
        assert timers[0].started is True
        assert timers[0].daemon is True
        assert timers[0].delay == pytest.approx(1.0)
        assert store._heavy_object_timers[job_id] is timers[0]
        assert "solver" in _stored_job(store, job_id)

        with patch("haute.routes._job_store.time.time", return_value=102.0):
            timers[0].fire()
            job = store.get_job(job_id)
        assert job is not None
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job
        assert job_id not in store._heavy_object_timers

    def test_touch_heavy_objects_replaces_stale_cleanup_timer(self) -> None:
        timers: list = []
        store = JobStore(
            ttl_seconds=1000,
            heavy_object_ttl_seconds=10,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        assert len(timers) == 1
        first_timer = timers[0]

        with patch("haute.routes._job_store.time.time", return_value=105.0):
            assert store.touch_heavy_objects(job_id) is True

        assert len(timers) == 2
        second_timer = timers[1]
        assert first_timer.cancelled is True
        assert second_timer.cancelled is False
        assert store._heavy_object_timers[job_id] is second_timer

        with patch("haute.routes._job_store.time.time", return_value=111.0):
            first_timer.force_fire()
        assert "solver" in _stored_job(store, job_id)
        assert store._heavy_object_timers[job_id] is second_timer

        with patch("haute.routes._job_store.time.time", return_value=116.0):
            second_timer.fire()
        assert "solver" not in _stored_job(store, job_id)
        assert job_id not in store._heavy_object_timers

    def test_clear_result_data_cancels_pending_heavy_object_timer(self) -> None:
        timers: list = []
        store = JobStore(
            ttl_seconds=1000,
            heavy_object_ttl_seconds=10,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        assert len(timers) == 1
        store.clear_result_data(job_id)

        assert timers[0].cancelled is True
        assert job_id not in store._heavy_object_timers
        assert "heavy_objects_expires_at" not in _stored_job(store, job_id)

    def test_existing_heavy_object_expiry_does_not_schedule_duplicate_cleanup(self) -> None:
        timers: list[object] = []
        store = JobStore(
            ttl_seconds=1000,
            heavy_object_ttl_seconds=10,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "result": {"step": 1},
                },
            )

        assert len(timers) == 1
        first_timer = timers[0]

        with patch("haute.routes._job_store.time.time", return_value=105.0):
            store.atomic_update(job_id, {"result": {"step": 2}})

        assert len(timers) == 1
        assert store._heavy_object_timers[job_id] is first_timer
        assert first_timer.cancelled is False

    def test_completed_at_is_stamped_only_on_first_completion_transition(self) -> None:
        store = _job_store_without_cleanup_threads()

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(store, {"status": "running"})

        with patch("haute.routes._job_store.time.time", return_value=150.0):
            first = JobLifecycle(store).transition(job_id, to="completed")

        assert first is not None
        assert first["completed_at"] == 150.0

        with patch("haute.routes._job_store.time.time", return_value=200.0):
            second = store.atomic_update(job_id, {"result": {"ok": True}})

        assert second is not None
        assert second["completed_at"] == 150.0
        assert _stored_job(store, job_id)["completed_at"] == 150.0

    def test_dynamic_completed_transition_is_stamped_by_value(self) -> None:
        store = _job_store_without_cleanup_threads()
        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(store, {"status": "running"})

        completed_status = "".join(["com", "pleted"])
        with patch("haute.routes._job_store.time.time", return_value=125.0):
            updated = JobLifecycle(store).transition(
                job_id,
                to=cast(TerminalReason, completed_status),
            )

        assert updated is not None
        assert updated["completed_at"] == 125.0

    def test_status_change_away_from_completed_cancels_heavy_cleanup_timer(self) -> None:
        timers: list[object] = []
        store = JobStore(
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": object(),
                "quote_grid": object(),
            },
        )
        assert len(timers) == 1

        JobLifecycle(store).transition(
            job_id,
            to="error",
            expected_status="completed",
            message="failed",
        )

        assert timers[0].cancelled is True
        assert job_id not in store._heavy_object_timers
        assert _stored_job(store, job_id)["status"] == "error"

    def test_dynamic_completed_status_preserves_existing_heavy_cleanup_timer(self) -> None:
        timers: list[object] = []
        store = JobStore(
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )
        completed_status = "".join(["com", "pleted"])

        job_id = _create_job(
            store,
            {
                "status": completed_status,
                "solver": object(),
                "quote_grid": object(),
            },
        )
        assert len(timers) == 1
        first_timer = timers[0]

        store.atomic_update(job_id, {"result": {"ok": True}})

        assert first_timer.cancelled is False
        assert store._heavy_object_timers[job_id] is first_timer

    def test_non_completed_update_cancels_orphaned_heavy_cleanup_timer(self) -> None:
        timers: list[object] = []
        timer_type = self._manual_timer_factory(timers)
        store = JobStore(heavy_object_timer_factory=timer_type)
        job_id = _create_job(store, {"status": "cancelled"})
        orphan_timer = timer_type(10.0, lambda: None)
        store._heavy_object_timers[job_id] = orphan_timer  # noqa: SLF001

        store.atomic_update(job_id, {"detail": "still cancelled"})

        assert orphan_timer.cancelled is True
        assert job_id not in store._heavy_object_timers  # noqa: SLF001

    def test_generic_status_change_is_rejected_without_affecting_cleanup_timer(self) -> None:
        timers: list[object] = []
        store = JobStore(
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": object(),
                "quote_grid": object(),
            },
        )
        assert len(timers) == 1

        with pytest.raises(ValueError, match="lifecycle fields"):
            store.atomic_update(job_id, {"status": "archived"})

        assert timers[0].cancelled is False
        assert store._heavy_object_timers[job_id] is timers[0]
        assert _stored_job(store, job_id)["status"] == "completed"

    def test_stale_job_eviction_cancels_pending_heavy_object_timer(self) -> None:
        timers: list = []
        store = JobStore(
            ttl_seconds=1,
            heavy_object_ttl_seconds=100,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        assert len(timers) == 1
        with patch("haute.routes._job_store.time.time", return_value=102.0):
            assert store.get_job(job_id) is None

        assert timers[0].cancelled is True
        assert job_id not in store._heavy_object_timers

    @pytest.mark.parametrize("status", ["running", "cancelled"])
    def test_non_completed_jobs_are_not_slimmed_by_heavy_object_policy(self, status: str) -> None:
        timers: list = []
        store = JobStore(
            ttl_seconds=60,
            heavy_object_ttl_seconds=1,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": status,
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=102.0):
            job = store.get_job(job_id)

        assert job is not None
        assert "solver" in job
        assert "solve_result" in job
        assert "quote_grid" in job
        assert timers == []

    def test_invalid_heavy_object_ttl_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="heavy_object_ttl_seconds"):
            JobStore(heavy_object_ttl_seconds=-1)

    def test_heavy_object_cleanup_contract_requires_expiry(self) -> None:
        store = JobStore()

        with pytest.raises(RuntimeError, match="without an expiry"):
            store._schedule_heavy_object_cleanup_if_needed("job-id", True, None)

    def test_touch_heavy_objects_returns_false_for_running_job(self) -> None:
        store = JobStore(ttl_seconds=3600, heavy_object_ttl_seconds=900)
        job_id = _create_job(
            store,
            {
                "status": "running",
                "solver": object(),
                "solve_result": object(),
                "quote_grid": object(),
            },
        )

        assert store.touch_heavy_objects(job_id) is False
        assert "heavy_objects_expires_at" not in _stored_job(store, job_id)

    def test_touch_heavy_objects_extends_successful_access_window(self) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=940.0):
            assert store.touch_heavy_objects(job_id) is True

        job = _stored_job(store, job_id)
        assert job["heavy_objects_expires_at"] == pytest.approx(1840.0)

        with patch("haute.routes._job_store.time.time", return_value=1839.0):
            job = store.get_job(job_id)
        assert job is not None
        assert "solver" in job
        assert "solve_result" in job
        assert "quote_grid" in job

        with patch("haute.routes._job_store.time.time", return_value=1841.0):
            job = store.get_job(job_id)
        assert job is not None
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job

    def test_touch_heavy_objects_keeps_current_window_when_already_fresh(self) -> None:
        timers: list[object] = []
        store = JobStore(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "created_at": 100.0,
                    "heavy_objects_expires_at": 1000.0,
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        assert timers == []

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            assert store.touch_heavy_objects(job_id) is True

        assert timers == []
        assert _stored_job(store, job_id)["heavy_objects_expires_at"] == pytest.approx(1000.0)

    def test_touch_heavy_objects_does_not_shrink_longer_current_window(self) -> None:
        timers: list[object] = []
        store = JobStore(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
            heavy_object_timer_factory=self._manual_timer_factory(timers),
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "created_at": 100.0,
                    "heavy_objects_expires_at": 2000.0,
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            assert store.touch_heavy_objects(job_id) is True

        assert timers == []
        assert _stored_job(store, job_id)["heavy_objects_expires_at"] == pytest.approx(2000.0)

    @pytest.mark.parametrize("status", ["error", "cancelled"])
    def test_touch_heavy_objects_refuses_non_completed_status(self, status: str) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
        )
        job_id = _create_job(
            store,
            {
                "status": status,
                "solver": object(),
                "solve_result": object(),
                "quote_grid": object(),
            },
        )

        assert store.touch_heavy_objects(job_id) is False
        assert "heavy_objects_expires_at" not in _stored_job(store, job_id)

    def test_touch_heavy_objects_uses_value_equality_for_completed_status(self) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
        )
        completed_status = "".join(["com", "pleted"])

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": completed_status,
                    "created_at": 100.0,
                    "heavy_objects_expires_at": 500.0,
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=200.0):
            assert store.touch_heavy_objects(job_id) is True

        assert _stored_job(store, job_id)["heavy_objects_expires_at"] == pytest.approx(1100.0)

    def test_touch_heavy_objects_is_capped_by_job_metadata_ttl(self) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=1000,
            heavy_object_ttl_seconds=900,
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=950.0):
            assert store.touch_heavy_objects(job_id) is True

        assert _stored_job(store, job_id)["heavy_objects_expires_at"] == pytest.approx(1100.0)

    def test_touch_heavy_objects_does_not_fabricate_missing_runtime_state(self) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=1001.0):
            job = store.get_job(job_id)
        assert job is not None
        assert "solver" not in job

        with patch("haute.routes._job_store.time.time", return_value=1002.0):
            assert store.touch_heavy_objects(job_id) is False

        job = _stored_job(store, job_id)
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job

    def test_touch_heavy_objects_returns_false_for_evicted_job(self) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=1,
            heavy_object_ttl_seconds=900,
        )

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "completed",
                    "solver": object(),
                    "solve_result": object(),
                    "quote_grid": object(),
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=102.0):
            assert store.touch_heavy_objects(job_id) is False

        assert job_id not in store.list_jobs()

    def test_guarded_atomic_update_refuses_slimmed_runtime_state(self) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
        )
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": object(),
                "quote_grid": object(),
                "result": {"total_objective": 100.0},
            },
        )

        store.clear_result_data(job_id)

        updated = store.atomic_update_if_heavy_present(
            job_id,
            {"result": {"total_objective": 200.0}},
            required_keys=("solver", "quote_grid"),
            expected_status="completed",
        )

        assert updated is None
        assert _stored_job(store, job_id)["result"] == {"total_objective": 100.0}

    def test_guarded_atomic_update_refuses_none_required_runtime_state(self) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
        )
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": None,
                "quote_grid": object(),
                "result": {"total_objective": 100.0},
            },
        )

        updated = store.atomic_update_if_heavy_present(
            job_id,
            {"result": {"total_objective": 200.0}},
            required_keys=("solver", "quote_grid"),
            expected_status="completed",
        )

        assert updated is None
        assert _stored_job(store, job_id)["result"] == {"total_objective": 100.0}

    def test_guarded_atomic_update_honours_custom_required_runtime_keys(self) -> None:
        store = _job_store_without_cleanup_threads(
            ttl_seconds=3600,
            heavy_object_ttl_seconds=900,
        )
        factors_df = object()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "factors_df": factors_df,
                "result": {"rows": 10},
            },
        )

        updated = store.atomic_update_if_heavy_present(
            job_id,
            {"result": {"rows": 20}},
            required_keys=("factors_df",),
            expected_status="completed",
        )

        assert updated is not None
        assert updated["factors_df"] is factors_df
        assert updated["result"] == {"rows": 20}

    def test_guarded_atomic_update_refuses_unexpected_status(self) -> None:
        store = JobStore(ttl_seconds=3600, heavy_object_ttl_seconds=900)
        job_id = _create_job(
            store,
            {
                "status": "running",
                "solver": object(),
                "quote_grid": object(),
                "result": {"total_objective": 100.0},
            },
        )

        updated = store.atomic_update_if_heavy_present(
            job_id,
            {"result": {"total_objective": 200.0}},
            required_keys=("solver", "quote_grid"),
            expected_status="completed",
        )

        assert updated is None
        assert _stored_job(store, job_id)["status"] == "running"
        assert _stored_job(store, job_id)["result"] == {"total_objective": 100.0}

    def test_guarded_heavy_update_refuses_lexically_lower_unexpected_status(self) -> None:
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "cancelled",
                "solver": object(),
                "quote_grid": object(),
                "result": {"total_objective": 100.0},
            },
        )

        updated = store.atomic_update_if_heavy_present(
            job_id,
            {"result": {"total_objective": 200.0}},
            required_keys=("solver", "quote_grid"),
            expected_status="completed",
        )

        assert updated is None
        assert _stored_job(store, job_id)["result"] == {"total_objective": 100.0}

    def test_guarded_atomic_update_rejects_unknown_expected_status(
        self,
    ) -> None:
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "running",
                "solver": object(),
                "quote_grid": object(),
                "result": {"total_objective": 100.0},
            },
        )

        with pytest.raises(ValueError, match="Unknown expected job status"):
            store.atomic_update_if_heavy_present(
                job_id,
                {"result": {"total_objective": 200.0}},
                required_keys=("solver", "quote_grid"),
                expected_status="zzz",
            )

        assert _stored_job(store, job_id)["status"] == "running"
        assert _stored_job(store, job_id)["result"] == {"total_objective": 100.0}

    def test_guarded_atomic_update_rejects_lifecycle_payload(
        self,
    ) -> None:
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "running",
                "solver": object(),
                "quote_grid": object(),
                "result": {"total_objective": 100.0},
            },
        )

        with pytest.raises(ValueError, match="created_at"):
            store.atomic_update_if_heavy_present(
                job_id,
                {"created_at": 0.0},
                required_keys=("solver", "quote_grid"),
                expected_status="running",
            )

        assert _stored_job(store, job_id)["status"] == "running"
        assert _stored_job(store, job_id)["result"] == {"total_objective": 100.0}

    def test_guarded_atomic_update_expected_status_uses_value_equality(self) -> None:
        store = _job_store_without_cleanup_threads()
        running_status = "".join(["run", "ning"])
        expected_status = "".join(["run", "ning"])
        job_id = _create_job(
            store,
            {
                "status": running_status,
                "solver": object(),
                "quote_grid": object(),
                "result": {"total_objective": 100.0},
            },
        )

        updated = store.atomic_update_if_heavy_present(
            job_id,
            {"result": {"total_objective": 200.0}},
            required_keys=("solver", "quote_grid"),
            expected_status=expected_status,
        )

        assert updated is not None
        assert updated["result"] == {"total_objective": 200.0}


# ---------------------------------------------------------------------------
# Factory allow-list
# ---------------------------------------------------------------------------


class TestJobStoreFactoryAllowList:
    """Factory prefixes are deliberately closed to keep singleton storage bounded."""

    def test_known_prefixes_return_cached_singletons(self) -> None:
        get_job_store.cache_clear()
        try:
            first_training = get_job_store("training")
            second_training = get_job_store("training")
            optimiser = get_job_store("optimiser")

            assert first_training is second_training
            assert optimiser is not first_training
        finally:
            get_job_store.cache_clear()

    def test_explore_prefix_returns_distinct_store(self) -> None:
        """`_KNOWN_PREFIXES` comment requires each new prefix to be verified distinct."""
        get_job_store.cache_clear()
        try:
            training = get_job_store("training")
            optimiser = get_job_store("optimiser")
            explore = get_job_store("explore")

            assert explore is get_job_store("explore")  # singleton per prefix
            assert explore is not training
            assert explore is not optimiser
        finally:
            get_job_store.cache_clear()

    def test_unknown_prefix_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="Unknown JobStore prefix 'pipeline'"):
            get_job_store("pipeline")


# ---------------------------------------------------------------------------
# GAP 1: update_job (non-atomic) race condition — reader sees partial state
# ---------------------------------------------------------------------------


class TestUpdateJobRaceCondition:
    """Demonstrate that update_job can expose partially-updated dicts.

    Production failure: The train service uses ``update_job`` for progress
    callbacks from background threads (``_progress``, ``_on_iteration``).
    Meanwhile the main thread polls ``get_job`` to serve status responses.
    Because ``dict.update()`` mutates in-place and is NOT atomic for
    multi-key updates, a reader can observe a dict where some keys are
    from the old state and others from the new state.

    ``atomic_update`` avoids this by swapping in a new dict; ``update_job``
    does not.
    """

    def test_update_job_can_expose_inconsistent_multi_key_state(self) -> None:
        """Stress test: writer calls update_job with coupled fields,
        reader checks whether the fields are always consistent.

        With update_job (non-atomic), there is a window where status and
        progress disagree.  With atomic_update, they never disagree.

        This test documents the hazard — it may not fail deterministically
        on every run (due to GIL timing), but the assertion captures the
        structural difference between the two APIs.
        """
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.0, "step": 0})

        inconsistencies: list[dict] = []
        stop = threading.Event()

        def reader() -> None:
            """Poll the job and look for step/progress mismatches."""
            while not stop.is_set():
                job = store.get_job(job_id)
                if job is None:
                    continue
                step = job.get("step", 0)
                progress = job.get("progress", 0.0)
                # Convention: progress == step / 100.  If they disagree
                # by more than one step, the reader saw a partial update.
                expected_progress = step / 100.0
                if abs(progress - expected_progress) > 0.015:
                    inconsistencies.append({"step": step, "progress": progress})

        def writer_non_atomic() -> None:
            """Update coupled fields with the non-atomic update_job."""
            for i in range(1, 101):
                store.update_job(job_id, step=i, progress=i / 100.0)

        reader_t = threading.Thread(target=reader, daemon=True)
        writer_t = threading.Thread(target=writer_non_atomic)
        reader_t.start()
        writer_t.start()
        writer_t.join(timeout=5)
        stop.set()
        reader_t.join(timeout=2)

        # Record whether inconsistencies were seen — either way, the test
        # passes.  The purpose is to document the hazard.  The real
        # assertion is structural: atomic_update NEVER shows them.
        # (Inconsistencies may or may not appear depending on GIL timing.)

        # Now verify that atomic_update never shows inconsistencies.
        store2 = JobStore()
        job_id2 = _create_job(store2, {"status": "running", "progress": 0.0, "step": 0})
        atomic_inconsistencies: list[dict] = []
        stop2 = threading.Event()

        def reader2() -> None:
            while not stop2.is_set():
                job = store2.get_job(job_id2)
                if job is None:
                    continue
                step = job.get("step", 0)
                progress = job.get("progress", 0.0)
                expected = step / 100.0
                if abs(progress - expected) > 0.015:
                    atomic_inconsistencies.append({"step": step, "progress": progress})

        def writer_atomic() -> None:
            for i in range(1, 101):
                store2.atomic_update(job_id2, {"step": i, "progress": i / 100.0})

        reader_t2 = threading.Thread(target=reader2, daemon=True)
        writer_t2 = threading.Thread(target=writer_atomic)
        reader_t2.start()
        writer_t2.start()
        writer_t2.join(timeout=5)
        stop2.set()
        reader_t2.join(timeout=2)

        # atomic_update must never show partial state
        assert not atomic_inconsistencies, (
            "atomic_update exposed partial state — this should be impossible"
        )


# ---------------------------------------------------------------------------
# GAP 2: 24+ hour job evicted mid-execution
# ---------------------------------------------------------------------------


class TestLongRunningJobEviction:
    """Running jobs survive by activity, not by being immortal."""

    def test_running_job_updated_recently_survives_eviction(self) -> None:
        """A running job older than TTL is retained when activity is recent."""
        store = JobStore(ttl_seconds=10)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "running",
                    "progress": 0.5,
                    "created_at": 0.0,
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=105.0):
            store.update_job(job_id, progress=0.6, message="Still training...")
            assert store.get_job(job_id) is not None

        with patch("haute.routes._job_store.time.time", return_value=112.0):
            _create_job(store, {"status": "running"})

        with patch("haute.routes._job_store.time.time", return_value=112.0):
            job = store.get_job(job_id)

        assert job is not None
        assert job["progress"] == 0.6

    def test_stuck_running_job_without_recent_activity_is_evicted(self) -> None:
        store = JobStore(ttl_seconds=10)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(store, {"status": "running", "progress": 0.5})

        with patch("haute.routes._job_store.time.time", return_value=111.0):
            _create_job(store, {"status": "running"})

        assert store.get_job(job_id) is None

    def test_stuck_running_job_artifacts_are_removed_on_eviction(
        self,
        tmp_path: Path,
    ) -> None:
        store = JobStore(ttl_seconds=10)
        kind = "test_stuck_running_job_cleanup_artifact"
        artifact_dir = tmp_path / "stuck_running_job"
        artifact_dir.mkdir()
        artifact_path = artifact_dir / "progress.json"
        artifact_path.write_bytes(b"{}")
        cleaned: list[str] = []

        def cleaner(handle: dict) -> None:
            cleaned.append(handle["path"])
            shutil.rmtree(handle["directory"])

        register_artifact_cleaner(kind, cleaner)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(
                store,
                {
                    "status": "running",
                    "artifact_handles": {
                        "progress": {
                            "kind": kind,
                            "version": 1,
                            "format": "json",
                            "path": str(artifact_path),
                            "directory": str(artifact_dir),
                        }
                    },
                },
            )

        with patch("haute.routes._job_store.time.time", return_value=111.0):
            assert store.get_job(job_id) is None

        assert cleaned == [str(artifact_path)]
        assert not artifact_dir.exists()

    def test_completed_job_evicted_after_ttl(self) -> None:
        """A completed job older than TTL IS evicted normally."""
        store = JobStore(ttl_seconds=1)
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "created_at": time.time() - 100,
            },
        )
        _create_job(store, {"status": "running"})
        assert store.get_job(job_id) is None

    def test_running_job_safe_during_concurrent_access(self) -> None:
        """Recent running activity survives even when concurrent eviction triggers."""
        store = JobStore(ttl_seconds=2)

        job_id = _create_job(
            store,
            {
                "status": "running",
                "progress": 0.0,
                "created_at": time.time() - 5,
            },
        )

        barrier = threading.Barrier(2)

        def background_worker() -> None:
            barrier.wait()
            store.update_job(job_id, progress=0.75, message="Training epoch 3")

        def main_thread_poller() -> None:
            barrier.wait()
            store.get_job("some_other_id")

        bg = threading.Thread(target=background_worker)
        main = threading.Thread(target=main_thread_poller)
        bg.start()
        main.start()
        main.join(timeout=5)
        bg.join(timeout=5)

        assert store.get_job(job_id) is not None


# ---------------------------------------------------------------------------
# Optimiser concurrency guard
# ---------------------------------------------------------------------------


class TestOptimiserConcurrencyGuard:
    """Pin the shared single-running-job contract for background work."""

    def test_train_service_has_start_lock(self) -> None:
        """Verify TrainService has the _start_lock attribute."""
        from haute.routes._train_service import TrainService

        store = JobStore()
        svc = TrainService(store)
        assert hasattr(svc, "_start_lock")
        assert isinstance(svc._start_lock, type(threading.Lock()))

    def test_optimiser_service_has_start_lock(self) -> None:
        """OptimiserSolveService should guard solve starts just like training."""
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        svc = OptimiserSolveService(store)
        assert hasattr(svc, "_start_lock")
        assert isinstance(svc._start_lock, type(threading.Lock()))

    def test_has_job_with_status_detects_running_jobs(self) -> None:
        """JobStore should expose a locked status check for route guards."""
        store = JobStore()
        assert store.has_job_with_status("running") is False

        _create_job(store, {"status": "completed", "type": "optimiser"})
        _create_job(store, {"status": "running", "type": "optimiser"})

        assert store.has_job_with_status("running") is True

    def test_has_job_with_status_uses_value_equality_not_identity(self) -> None:
        store = JobStore()
        stored_status = "".join(["run", "ning"])
        requested_status = "".join(["run", "ning"])

        _create_job(store, {"status": stored_status})

        assert store.has_job_with_status(requested_status) is True

    def test_has_job_with_status_false_when_only_other_fresh_statuses_exist(self) -> None:
        """Fresh non-matching jobs should not satisfy the status guard."""
        store = JobStore(ttl_seconds=60)
        _create_job(store, {"status": "completed"})
        _create_job(store, {"status": "error"})

        assert store.has_job_with_status("running") is False

    def test_has_job_with_status_uses_exact_status_not_ordering(self) -> None:
        store = JobStore(ttl_seconds=60)
        _create_job(store, {"status": "error"})

        assert store.has_job_with_status("completed") is False

    def test_has_job_with_status_ignores_stale_non_running_jobs(self) -> None:
        """Expired finished jobs should not trip the running-job guard."""
        store = JobStore(ttl_seconds=1)
        _create_job(store, {"status": "completed", "created_at": time.time() - 10})

        assert store.has_job_with_status("running") is False

    def test_has_job_with_status_ignores_stuck_running_jobs(self) -> None:
        """Expired running jobs without activity should not trip the guard."""
        store = JobStore(ttl_seconds=10)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            _create_job(store, {"status": "running", "job_type": "solve"})

        with patch("haute.routes._job_store.time.time", return_value=111.0):
            assert store.has_job_with_status("running") is False

    def test_has_job_with_status_keeps_recently_updated_running_jobs(self) -> None:
        """Long jobs can run past created_at TTL when updates keep them active."""
        store = JobStore(ttl_seconds=10)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = _create_job(store, {"status": "running", "created_at": 0.0})

        with patch("haute.routes._job_store.time.time", return_value=105.0):
            store.atomic_update(job_id, {"progress": 0.75})

        with patch("haute.routes._job_store.time.time", return_value=112.0):
            assert store.has_job_with_status("running") is True

    def test_has_job_matching_uses_predicate_under_lock(self) -> None:
        """Route guards can match richer job metadata without raw store iteration."""
        store = JobStore()
        _create_job(store, {"status": "running", "job_type": "estimate"})
        _create_job(store, {"status": "running", "job_type": "solve"})

        assert store.has_job_matching(
            lambda job: job.get("status") == "running" and job.get("job_type") == "solve"
        )

    def test_has_job_matching_ignores_expired_jobs(self) -> None:
        """Predicate checks should share the normal stale-job eviction behaviour."""
        store = JobStore(ttl_seconds=1)
        _create_job(
            store,
            {
                "status": "completed",
                "job_type": "solve",
                "created_at": time.time() - 10,
            },
        )

        assert store.has_job_matching(lambda job: job.get("job_type") == "solve") is False

    def test_has_job_matching_ignores_stuck_running_jobs(self) -> None:
        """Predicate guards should also evict stale running activity first."""
        store = JobStore(ttl_seconds=10)

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            _create_job(store, {"status": "running", "job_type": "solve"})

        with patch("haute.routes._job_store.time.time", return_value=111.0):
            assert store.has_job_matching(lambda job: job.get("job_type") == "solve") is False


# ---------------------------------------------------------------------------
# Manual cleanup hook: clear_result_data can strip heavy runtime state
# ---------------------------------------------------------------------------


class TestClearResultDataManualCleanup:
    """Verify clear_result_data still works for explicit job cleanup.

    Optimiser jobs intentionally retain runtime objects for post-solve
    workflows such as frontier selection and MLflow logging. The helper
    remains useful as an explicit cleanup primitive, so these tests pin
    its behavior directly on realistic job shapes.
    """

    def test_clear_result_data_is_available_for_explicit_cleanup(self) -> None:
        """The helper remains callable even when routes retain runtime state."""
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(store, {"status": "completed", "solver": object()})

        store.clear_result_data(job_id, keys=("solver",))

        job = store.get_job(job_id)
        assert job is not None
        assert "solver" not in job

    def test_end_to_end_optimiser_job_shape(self) -> None:
        """Exercise clear_result_data on a dict shaped like a real
        optimiser completed job — the use case it was designed for.
        """
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "progress": 1.0,
                "message": "Completed",
                "config": {"objective": "income", "constraints": {"loss_ratio": 1.0}},
                "solver": object(),  # heavy: Optimiser instance
                "solve_result": object(),  # heavy: solve result with full DataFrame
                "quote_grid": object(),  # heavy: QuoteGrid
                "factors_df": object(),  # heavy: ratebook aligned factor frame
                "result": {
                    "mode": "ratebook",
                    "total_objective": 1.05,
                    "converged": True,
                    "frontier": None,
                },
                "frontier_data": None,
                "elapsed_seconds": 42.0,
            },
        )

        # Before clear: heavy objects present
        job = store.get_job(job_id)
        assert "solver" in job
        assert "solve_result" in job
        assert "quote_grid" in job
        assert "factors_df" in job

        store.clear_result_data(job_id)

        # After clear: heavy objects gone, metadata intact
        job = store.get_job(job_id)
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job
        assert "factors_df" not in job
        assert job["status"] == "completed"
        assert job["result"]["converged"] is True
        assert job["config"]["objective"] == "income"
        assert job["elapsed_seconds"] == 42.0

    def test_clear_after_clear_is_safe(self) -> None:
        """Double-clear should not raise or corrupt."""
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "solver": "big",
                "solve_result": "big",
                "quote_grid": "big",
                "result": {"ok": True},
            },
        )
        store.clear_result_data(job_id)
        store.clear_result_data(job_id)
        job = store.get_job(job_id)
        assert job["status"] == "completed"
        assert "solver" not in job


# ---------------------------------------------------------------------------
# GAP 5: Eviction during iteration — dict size changes
# ---------------------------------------------------------------------------


class TestEvictionDuringIteration:
    """Demonstrate that _evict_stale can race with concurrent dict mutation.

    Production failure: ``_evict_stale`` iterates ``self._jobs.items()``
    to find stale keys, then deletes them.  If another thread inserts or
    deletes a key between the iteration and the deletion, CPython may
    raise ``RuntimeError: dictionary changed size during iteration``
    (in older versions) or silently skip entries.

    In CPython 3.12+ dict iteration is more resilient, but the race is
    still architecturally unsound.
    """

    def test_eviction_concurrent_with_creates(self) -> None:
        """Hammer create_job (which calls _evict_stale) from many threads
        while the store has a mix of stale and fresh jobs.

        Catches: RuntimeError from dict mutation during iteration,
        or silent data loss where a fresh job disappears.
        """
        store = JobStore(ttl_seconds=1)

        # Pre-populate with 50 stale jobs to maximize eviction work
        for i in range(50):
            _create_job(
                store,
                {
                    "status": "completed",
                    "idx": i,
                    "created_at": time.time() - 100 - i,
                },
            )

        n_threads = 20
        barrier = threading.Barrier(n_threads)
        new_ids: list[str] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def create_and_evict(idx: int) -> None:
            barrier.wait()
            try:
                jid = _create_job(store, {"status": "running", "idx": idx})
                with lock:
                    new_ids.append(jid)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=create_and_evict, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent eviction raised: {errors}"
        assert len(new_ids) == n_threads
        # All fresh jobs must survive
        for jid in new_ids:
            job = store.get_job(jid)
            assert job is not None, f"Fresh job {jid} was lost during eviction race"
            assert job["status"] == "running"

    def test_eviction_concurrent_with_updates(self) -> None:
        """Writers call update_job while readers call get_job (triggering
        eviction) — no crash, no data loss for non-stale jobs.
        """
        store = JobStore(ttl_seconds=2)

        # Create some stale jobs to be evicted
        for _ in range(20):
            _create_job(store, {"status": "completed", "created_at": time.time() - 50})

        # Create fresh jobs that should survive
        fresh_ids = [_create_job(store, {"status": "running", "counter": 0}) for _ in range(5)]

        n_rounds = 50
        errors: list[Exception] = []

        def updater() -> None:
            for i in range(n_rounds):
                for jid in fresh_ids:
                    try:
                        store.update_job(jid, counter=i)
                    except Exception as exc:
                        errors.append(exc)

        def reader() -> None:
            for _ in range(n_rounds):
                for jid in fresh_ids:
                    try:
                        store.get_job(jid)  # triggers _evict_stale
                    except Exception as exc:
                        errors.append(exc)

        t_update = threading.Thread(target=updater)
        t_read = threading.Thread(target=reader)
        t_update.start()
        t_read.start()
        t_update.join(timeout=10)
        t_read.join(timeout=10)

        assert not errors, f"Concurrent eviction + update raised: {errors}"
        for jid in fresh_ids:
            job = store.get_job(jid)
            assert job is not None

    def test_eviction_while_iterating_jobs_property(self) -> None:
        """External code iterates store.list_jobs() (e.g. _check_no_concurrent_jobs)
        while another thread triggers eviction via create_job.
        """
        store = JobStore(ttl_seconds=1)

        # Populate with stale jobs
        for _ in range(30):
            _create_job(store, {"status": "completed", "created_at": time.time() - 50})
        # And a fresh one
        fresh_id = _create_job(store, {"status": "running"})

        errors: list[Exception] = []

        def iterator() -> None:
            """Mimic the concurrency check by iterating current records."""
            for _ in range(100):
                try:
                    # Exercise iteration — the result itself is not under test,
                    # we're confirming iteration doesn't raise during concurrent mutation.
                    [jid for jid, j in store.list_jobs().items() if j.get("status") == "running"]
                except Exception as exc:
                    errors.append(exc)

        def evictor() -> None:
            """Create new jobs, each triggering _evict_stale."""
            for _ in range(100):
                try:
                    _create_job(
                        store,
                        {
                            "status": "completed",
                            "created_at": time.time() - 50,
                        },
                    )
                except Exception as exc:
                    errors.append(exc)

        t1 = threading.Thread(target=iterator)
        t2 = threading.Thread(target=evictor)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Concurrent iteration + eviction raised: {errors}"
        # Fresh job must survive
        assert store.list_jobs().get(fresh_id) is not None


# ---------------------------------------------------------------------------
# GAP 6: require_completed_job treats "running" and "error" identically
# ---------------------------------------------------------------------------


class TestRequireCompletedJobErrorVsRunning:
    """Document that require_completed_job returns HTTP 400 for both
    "running" and "error" status, with no way to distinguish them.

    Production failure: A client polling for job completion gets 400
    for an errored job and assumes "still running, try again later"
    because the status code is the same.  The error message includes
    the status string, but the HTTP status code (400) is identical.
    A proper API would return 409 for "running" (conflict/retry) and
    400 or 422 for "error" (terminal failure).
    """

    def test_running_and_error_both_return_400(self) -> None:
        """Both non-completed statuses produce the same HTTP status code."""
        from fastapi import HTTPException

        store = JobStore()
        running_id = _create_job(store, {"status": "running"})
        error_id = _create_job(store, {"status": "error", "message": "OOM"})

        with pytest.raises(HTTPException) as running_exc:
            store.require_completed_job(running_id)
        with pytest.raises(HTTPException) as error_exc:
            store.require_completed_job(error_id)

        # Same status code — client cannot distinguish via HTTP alone
        assert running_exc.value.status_code == error_exc.value.status_code == 400

    def test_error_detail_includes_status_string(self) -> None:
        """The detail message does include the status, so a client parsing
        the message body CAN distinguish — but this is fragile.
        """
        from fastapi import HTTPException

        store = JobStore()
        running_id = _create_job(store, {"status": "running"})
        error_id = _create_job(store, {"status": "error", "message": "OOM"})

        with pytest.raises(HTTPException) as running_exc:
            store.require_completed_job(running_id)
        with pytest.raises(HTTPException) as error_exc:
            store.require_completed_job(error_id)

        assert "running" in running_exc.value.detail
        assert "error" in error_exc.value.detail
        # Both say "not completed" — same template
        assert "not completed" in running_exc.value.detail
        assert "not completed" in error_exc.value.detail

    def test_running_is_retriable_error_is_terminal(self) -> None:
        """Document the semantic difference: 'running' means try later,
        'error' means the job failed permanently.  The API conflates them.

        A well-designed API might return:
        - 202 Accepted / 409 Conflict for "running" (retriable)
        - 400 / 422 for "error" (terminal)
        """
        from fastapi import HTTPException

        store = JobStore()

        # Running job — semantically retriable
        running_id = _create_job(store, {"status": "running", "progress": 0.5})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(running_id)
        assert exc_info.value.status_code == 400  # should arguably be 409

        # Errored job — semantically terminal
        error_id = _create_job(store, {"status": "error", "message": "CUDA OOM"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(error_id)
        assert exc_info.value.status_code == 400  # same code, different semantics

    def test_detail_format_is_consistent(self) -> None:
        """Verify the exact detail format for regression testing."""
        from fastapi import HTTPException

        store = JobStore()
        job_id = _create_job(store, {"status": "error"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        detail = exc_info.value.detail
        assert detail == f"Job '{job_id}' is not completed (status: error)"

    def test_require_completed_job_accepts_equal_non_interned_completed_status(self) -> None:
        store = JobStore()
        completed_status = "".join(["com", "pleted"])
        job_id = _create_job(store, {"status": completed_status, "result": {"ok": True}})

        job = store.require_completed_job(job_id)

        assert job["status"] == "completed"
        assert job["result"] == {"ok": True}


# ---------------------------------------------------------------------------
# atomic_update with expected_status guard
# ---------------------------------------------------------------------------


class TestAtomicUpdateExpectedStatus:
    """Tests for the expected_status parameter of atomic_update.

    The guard prevents a timeout/error from overwriting a completed job.
    """

    def test_update_applied_when_status_matches(self) -> None:
        """When expected_status matches the current status, the update proceeds."""
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.5})
        result = store.atomic_update(
            job_id,
            {"progress": 1.0},
            expected_status="running",
        )
        assert result is not None
        assert result["status"] == "running"
        assert result["progress"] == 1.0
        # Verify the store reflects the update
        assert store.get_job(job_id)["progress"] == 1.0

    def test_update_skipped_when_status_does_not_match(self) -> None:
        """When expected_status does not match, the update is a no-op."""
        store = JobStore()
        job_id = _create_job(store, {"status": "completed", "progress": 1.0})
        result = store.atomic_update(
            job_id,
            {"message": "timeout"},
            expected_status="running",
        )
        assert result is None
        # Store should also be unchanged
        assert store.get_job(job_id)["status"] == "completed"

    def test_update_skips_lexically_higher_mismatched_status(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "superseded"})

        result = store.atomic_update(
            job_id,
            {"message": "late running update"},
            expected_status="running",
        )

        assert result is None
        assert "message" not in store.require_job(job_id)

    def test_no_expected_status_always_applies(self) -> None:
        """When expected_status is None (default), update always applies."""
        store = JobStore()
        job_id = _create_job(store, {"status": "completed"})
        result = store.atomic_update(job_id, {"published": True})
        assert result is not None
        assert result["status"] == "completed"
        assert result["published"] is True

    def test_expected_status_returns_none_on_mismatch(self) -> None:
        """A skipped guarded update is explicit to callers."""
        store = JobStore()
        job_id = _create_job(store, {"status": "completed", "result": {"score": 0.9}})
        result = store.atomic_update(
            job_id,
            {"result": {"score": 1.0}},
            expected_status="running",
        )
        assert result is None

    def test_expected_status_guard_prevents_timeout_overwrite(self) -> None:
        """Realistic scenario: timeout callback fires after job already completed.

        The expected_status guard ensures the timeout does not overwrite
        the completed status.
        """
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.0})

        # Background thread completes the job through the lifecycle owner.
        JobLifecycle(store).transition(job_id, to="completed", fields={"progress": 1.0})

        # A late running-only payload write is rejected by the status guard.
        result = store.atomic_update(
            job_id,
            {"message": "Training timed out"},
            expected_status="running",
        )

        # Job should still be completed, not error
        assert result is None
        job = store.get_job(job_id)
        assert job["status"] == "completed"
        assert job["progress"] == 1.0
        assert "message" not in job

    def test_unknown_expected_status_is_rejected(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running", "progress": 0.5})

        with pytest.raises(ValueError, match="Unknown expected job status"):
            store.atomic_update(
                job_id,
                {"progress": 1.0},
                expected_status="zzz",
            )

        job = store.get_job(job_id)
        assert job is not None
        assert job["status"] == "running"
        assert job["progress"] == 0.5

    def test_expected_status_uses_value_equality_not_object_identity(self) -> None:
        store = JobStore()
        running_status = "".join(["run", "ning"])
        expected_status = "".join(["run", "ning"])
        job_id = _create_job(store, {"status": running_status, "progress": 0.5})

        store.atomic_update(
            job_id,
            {"progress": 1.0},
            expected_status=expected_status,
        )

        job = store.get_job(job_id)
        assert job["status"] == "running"
        assert job["progress"] == 1.0

    def test_expected_status_is_keyword_only(self) -> None:
        store = JobStore()
        job_id = _create_job(store, {"status": "running"})

        with pytest.raises(TypeError):
            store.atomic_update(job_id, {"progress": 1.0}, "running")

    def test_raises_key_error_for_missing_job(self) -> None:
        """expected_status guard should not mask KeyError for missing jobs."""
        store = JobStore()
        with pytest.raises(KeyError):
            store.atomic_update(
                "nonexistent",
                {"message": "late update"},
                expected_status="running",
            )


# ---------------------------------------------------------------------------
# register_artifact_cleaner contract
# ---------------------------------------------------------------------------


class TestRegisterArtifactCleaner:
    """API contract: cleaner registration prevents silent overwrites and
    rejects empty kinds.  These guards exist because a registry collision
    would mean artifact cleanup runs the wrong cleaner — silent data
    corruption — so they need to fail loudly at registration time.
    """

    def test_empty_kind_rejected_loudly(self) -> None:
        """Empty kind would key cleanup behaviour against ``""`` and silently
        no-op for typed handles, masking artifact leaks."""
        from haute.routes._job_store import _ARTIFACT_CLEANERS, register_artifact_cleaner

        with pytest.raises(ValueError, match="non-empty"):
            register_artifact_cleaner("", lambda handle: None)
        # Registry is unchanged.
        assert "" not in _ARTIFACT_CLEANERS

    def test_double_registration_with_different_cleaner_rejected(self) -> None:
        """Two cleaners for the same kind would race; the second registration
        must fail rather than silently win."""
        from haute.routes._job_store import _ARTIFACT_CLEANERS, register_artifact_cleaner

        kind = f"test-double-reg-{id(object())}"

        def first(handle: dict) -> None:
            handle["cleaned_by"] = "first"

        def second(handle: dict) -> None:
            handle["cleaned_by"] = "second"

        register_artifact_cleaner(kind, first)
        try:
            with pytest.raises(RuntimeError, match=f"already registered.*{kind!r}"):
                register_artifact_cleaner(kind, second)
            # The original cleaner stays registered (no silent overwrite).
            assert _ARTIFACT_CLEANERS[kind] is first
        finally:
            _ARTIFACT_CLEANERS.pop(kind, None)

    def test_idempotent_registration_with_same_cleaner_is_allowed(self) -> None:
        """Re-registering the same callable is a harmless idempotent op —
        common when modules are reloaded in dev — so it must succeed.
        Without this branch, hot-reload workflows would crash."""
        from haute.routes._job_store import _ARTIFACT_CLEANERS, register_artifact_cleaner

        kind = f"test-idempotent-{id(object())}"

        def cleaner(handle: dict) -> None:
            handle["cleaned"] = True

        register_artifact_cleaner(kind, cleaner)
        # Same callable — idempotent, must not raise.
        register_artifact_cleaner(kind, cleaner)
        try:
            assert _ARTIFACT_CLEANERS[kind] is cleaner
        finally:
            _ARTIFACT_CLEANERS.pop(kind, None)


# ---------------------------------------------------------------------------
# delete_job: missing job is a no-op
# ---------------------------------------------------------------------------


class TestDeleteJobMissing:
    """``delete_job`` for a non-existent ID must be a silent no-op.

    Routes call this in ``finally`` blocks for ephemeral jobs (estimate,
    auto-range) — if the job was already evicted by TTL, the cleanup
    must not raise, otherwise the original error becomes hidden under
    a secondary failure.
    """

    def test_delete_unknown_job_is_no_op(self) -> None:
        store = _job_store_without_cleanup_threads()
        # Pre-condition: store empty.
        assert dict(store.list_jobs()) == {}
        # No-op — must not raise.
        store.delete_job("never-existed")
        # Post-condition: still empty, no spurious entry created.
        assert dict(store.list_jobs()) == {}

    def test_delete_unknown_job_does_not_disturb_other_jobs(self) -> None:
        store = _job_store_without_cleanup_threads()
        keep_id = _create_job(store, {"status": "running"})
        store.delete_job("never-existed")
        # Existing job survives unchanged.
        assert store.get_job(keep_id) is not None
        assert store.get_job(keep_id)["status"] == "running"

    def test_delete_running_job_clears_activity_timestamp(self) -> None:
        store = _job_store_without_cleanup_threads()
        job_id = _create_job(store, {"status": "running"})

        assert job_id in store._running_activity_at

        store.delete_job(job_id)

        assert job_id not in store._running_activity_at


# ---------------------------------------------------------------------------
# _clear_expired_heavy_objects: full-sweep branch
# ---------------------------------------------------------------------------


class TestClearExpiredHeavyObjectsSweep:
    """The timer-less sweep branch (``job_id=None``) drives the lazy backstop
    that runs whenever a job is read.  It must walk every completed job and
    only clear the ones whose heavy objects have actually expired.
    """

    def test_sweep_clears_only_expired_completed_jobs(self) -> None:
        store = _job_store_without_cleanup_threads(heavy_object_ttl_seconds=0.01)
        # An expired completed job with heavy objects.
        expired_id = _create_job(
            store,
            {
                "status": "completed",
                "completed_at": time.time() - 1,
                "solver": object(),
                "quote_grid": object(),
            },
        )
        # A still-fresh completed job with heavy objects.
        fresh_id = _create_job(
            store,
            {
                "status": "completed",
                "completed_at": time.time(),
                "heavy_objects_expires_at": time.time() + 1000,
                "solver": object(),
            },
        )
        # A running job (heavy fields preserved by status filter).
        running_id = _create_job(store, {"status": "running", "solver": object()})

        # Drive the full sweep (no job_id, no timer) — this is the path the
        # lazy backstop takes from ``_evict_stale``.
        store._clear_expired_heavy_objects(job_id=None, timer=None)

        # Expired job has its heavy objects stripped, but the dict survives
        # so status polling can still report "completed".
        cleared = store.get_job(expired_id)
        assert cleared is not None
        assert cleared["status"] == "completed"
        assert "solver" not in cleared
        assert "quote_grid" not in cleared
        assert "heavy_objects_cleared_at" in cleared

        # Fresh job is untouched.
        fresh = store.get_job(fresh_id)
        assert fresh is not None
        assert "solver" in fresh

        # Running job is untouched (status filter).
        running = store.get_job(running_id)
        assert running is not None
        assert "solver" in running

    def test_sweep_continues_past_non_completed_and_lightweight_jobs(self) -> None:
        store = _job_store_without_cleanup_threads(heavy_object_ttl_seconds=1)
        running_id = _create_job(store, {"status": "running", "solver": object()})
        lightweight_id = _create_job(store, {"status": "completed", "result": {"ok": True}})
        expired_id = _create_job(
            store,
            {
                "status": "completed",
                "completed_at": 100.0,
                "heavy_objects_expires_at": 101.0,
                "solver": object(),
                "quote_grid": object(),
            },
        )

        store._clear_expired_heavy_objects_locked(now=102.0)

        assert "solver" in _stored_job(store, running_id)
        assert _stored_job(store, lightweight_id)["result"] == {"ok": True}
        assert "solver" not in _stored_job(store, expired_id)
        assert "quote_grid" not in _stored_job(store, expired_id)

    def test_sweep_continues_past_fresh_heavy_job_to_later_expired_job(self) -> None:
        store = _job_store_without_cleanup_threads(heavy_object_ttl_seconds=1)
        with patch("haute.routes._job_store.time.time", return_value=0.0):
            fresh_id = _create_job(
                store,
                {
                    "status": "completed",
                    "heavy_objects_expires_at": 200.0,
                    "solver": object(),
                },
            )
            expired_id = _create_job(
                store,
                {
                    "status": "completed",
                    "heavy_objects_expires_at": 100.0,
                    "solver": object(),
                },
            )

        store._clear_expired_heavy_objects_locked(now=101.0)

        assert "solver" in _stored_job(store, fresh_id)
        assert "solver" not in _stored_job(store, expired_id)

    def test_sweep_completed_status_uses_value_equality(self) -> None:
        store = _job_store_without_cleanup_threads(heavy_object_ttl_seconds=1)
        completed_status = "".join(["com", "pleted"])
        job_id = _create_job(
            store,
            {
                "status": completed_status,
                "heavy_objects_expires_at": 100.0,
                "solver": object(),
            },
        )

        store._clear_expired_heavy_objects_locked(now=101.0)

        assert "solver" not in _stored_job(store, job_id)

    def test_sweep_ignores_non_completed_status(self) -> None:
        store = _job_store_without_cleanup_threads(heavy_object_ttl_seconds=1)
        job_id = _create_job(
            store,
            {
                "status": "error",
                "heavy_objects_expires_at": 100.0,
                "solver": object(),
            },
        )

        store._clear_expired_heavy_objects_locked(now=101.0)

        assert "solver" in _stored_job(store, job_id)

    def test_sweep_clears_heavy_objects_at_exact_expiry_boundary(self) -> None:
        store = _job_store_without_cleanup_threads(heavy_object_ttl_seconds=1)
        job_id = _create_job(
            store,
            {
                "status": "completed",
                "heavy_objects_expires_at": 250.0,
                "solver": object(),
            },
        )

        store._clear_expired_heavy_objects_locked(now=250.0)

        assert "solver" not in _stored_job(store, job_id)
        assert _stored_job(store, job_id)["heavy_objects_cleared_at"] == 250.0


# ---------------------------------------------------------------------------
# _schedule_heavy_object_cleanup: race-condition guards
# ---------------------------------------------------------------------------


class TestScheduleHeavyObjectCleanupRaces:
    """Race-condition guards inside ``_schedule_heavy_object_cleanup``.

    The scheduler is invoked from inside ``_store_merged_job_locked`` after
    releasing partial work back to the lock; between the decision to
    schedule and the actual scheduling, another thread can transition the
    job out from under us.  These tests pin the three escape hatches.
    """

    @pytest.mark.parametrize("status", ["running", "cancelled"])
    def test_skips_scheduling_when_job_is_no_longer_completed(self, status: str) -> None:
        """If the job transitioned away from 'completed' before the timer
        was bound, drop the scheduling silently (the next status change will
        re-schedule when appropriate)."""
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))
        expires_at = time.time() + 1.0

        # Pre-seed a non-completed job with heavy fields.
        record: dict[str, Any] = {
            "status": status,
            "solver": object(),
            "created_at": time.time(),
            "heavy_objects_expires_at": expires_at,
        }
        if status != "running":
            record.update(terminal_reason=status, ended_at=time.time())
        with store._write_lock:
            store._jobs["job-running"] = record

        # Call the scheduler directly — the public path always goes through
        # ``_store_merged_job_locked`` which only invokes this for completed
        # jobs, so we exercise the race guard via the private API.
        store._schedule_heavy_object_cleanup("job-running", expires_at)

        # No timer was registered (race guard hit at line 250).
        assert "job-running" not in store._heavy_object_timers
        # The manually-created timer was constructed but never started in
        # the registry — the guard short-circuited before ``timer.start()``.
        # The factory does record creation, but ``start()`` is what fires it.
        assert all(not getattr(t, "started", False) for t in timers)

    def test_skips_scheduling_when_job_disappeared(self) -> None:
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))

        store._schedule_heavy_object_cleanup("missing-job", time.time() + 1.0)

        assert "missing-job" not in store._heavy_object_timers
        assert all(not getattr(t, "started", False) for t in timers)

    def test_due_or_overdue_cleanup_uses_zero_delay(self) -> None:
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))

        with store._write_lock:
            store._jobs["job-due"] = {
                "status": "completed",
                "terminal_reason": "completed",
                "solver": object(),
                "created_at": 50.0,
                "ended_at": 50.0,
                "completed_at": 50.0,
                "heavy_objects_expires_at": 90.0,
            }

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            store._schedule_heavy_object_cleanup("job-due", 90.0)

        assert len(timers) == 1
        assert timers[0].delay == 0.0
        assert timers[0].started is True

    def test_future_cleanup_delay_uses_subtraction(self) -> None:
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))

        with store._write_lock:
            store._jobs["job-future"] = {
                "status": "completed",
                "terminal_reason": "completed",
                "solver": object(),
                "created_at": 50.0,
                "ended_at": 50.0,
                "completed_at": 50.0,
                "heavy_objects_expires_at": 150.0,
            }

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            store._schedule_heavy_object_cleanup("job-future", 150.0)

        assert len(timers) == 1
        assert timers[0].delay == pytest.approx(50.0)
        assert timers[0].started is True

    @pytest.mark.parametrize("status", ["archived", "zzz"])
    def test_rejects_unknown_status_before_scheduling(
        self,
        status: str,
    ) -> None:
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))

        with store._write_lock:
            store._jobs[f"job-{status}"] = {
                "status": status,
                "solver": object(),
                "created_at": 50.0,
                "heavy_objects_expires_at": 150.0,
            }

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            with pytest.raises(ValueError, match="invalid status"):
                store._schedule_heavy_object_cleanup(f"job-{status}", 150.0)

        assert f"job-{status}" not in store._heavy_object_timers
        assert all(not getattr(t, "started", False) for t in timers)

    def test_job_specific_cleanup_ignores_missing_job(self) -> None:
        store = _job_store_without_cleanup_threads()

        store._clear_expired_heavy_objects(job_id="missing-job", timer=object())

        assert store.list_jobs() == {}

    @pytest.mark.parametrize("status", ["cancelled", "error"])
    def test_job_specific_cleanup_ignores_non_completed_terminal_statuses(
        self,
        status: str,
    ) -> None:
        store = _job_store_without_cleanup_threads(heavy_object_ttl_seconds=1)
        job_id = "terminal-job"
        timer = object()
        store._jobs[job_id] = {  # noqa: SLF001 - timer race state witness
            "status": status,
            "terminal_reason": status,
            "created_at": 90.0,
            "ended_at": 90.0,
            "heavy_objects_expires_at": 100.0,
            "solver": object(),
        }
        store._heavy_object_timers[job_id] = timer  # noqa: SLF001

        with patch("haute.routes._job_store.time.time", return_value=101.0):
            store._clear_expired_heavy_objects(job_id=job_id, timer=timer)

        assert "solver" in _stored_job(store, job_id)

    def test_job_specific_cleanup_matches_timer_by_identity(self) -> None:
        class EqualTimer:
            cancelled = False

            def cancel(self) -> None:
                self.cancelled = True

            def __eq__(self, _other: object) -> bool:
                return True

        stored_timer = EqualTimer()
        callback_timer = EqualTimer()
        store = _job_store_without_cleanup_threads()
        with store._write_lock:
            store._jobs["job-fresh"] = {
                "status": "completed",
                "terminal_reason": "completed",
                "solver": object(),
                "created_at": 50.0,
                "ended_at": 50.0,
                "completed_at": 50.0,
                "heavy_objects_expires_at": 200.0,
            }
            store._heavy_object_timers["job-fresh"] = stored_timer

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            store._clear_expired_heavy_objects(job_id="job-fresh", timer=callback_timer)

        assert store._heavy_object_timers["job-fresh"] is stored_timer
        assert stored_timer.cancelled is False

    def test_job_specific_cleanup_clears_at_exact_expiry_boundary(self) -> None:
        timer = type("T", (), {"cancel": lambda self: setattr(self, "cancelled", True)})()
        store = _job_store_without_cleanup_threads()
        with store._write_lock:
            store._jobs["job-due"] = {
                "status": "completed",
                "terminal_reason": "completed",
                "solver": object(),
                "created_at": 50.0,
                "ended_at": 50.0,
                "completed_at": 50.0,
                "heavy_objects_expires_at": 100.0,
            }
            store._heavy_object_timers["job-due"] = timer

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            store._clear_expired_heavy_objects(job_id="job-due", timer=timer)

        assert "solver" not in _stored_job(store, "job-due")
        assert "job-due" not in store._heavy_object_timers

    def test_job_specific_cleanup_completed_status_uses_value_equality(self) -> None:
        timer = type("T", (), {"cancel": lambda self: setattr(self, "cancelled", True)})()
        store = _job_store_without_cleanup_threads()
        completed_status = "".join(["com", "pleted"])
        with store._write_lock:
            store._jobs["job-due"] = {
                "status": completed_status,
                "terminal_reason": completed_status,
                "solver": object(),
                "created_at": 50.0,
                "ended_at": 50.0,
                "completed_at": 50.0,
                "heavy_objects_expires_at": 100.0,
            }
            store._heavy_object_timers["job-due"] = timer

        with patch("haute.routes._job_store.time.time", return_value=101.0):
            store._clear_expired_heavy_objects(job_id="job-due", timer=timer)

        assert "solver" not in _stored_job(store, "job-due")

    def test_job_specific_cleanup_rejects_lexically_lower_unknown_status(
        self,
    ) -> None:
        timer = type("T", (), {"cancel": lambda self: setattr(self, "cancelled", True)})()
        store = _job_store_without_cleanup_threads()
        with store._write_lock:
            store._jobs["job-archived"] = {
                "status": "archived",
                "solver": object(),
                "created_at": 50.0,
                "heavy_objects_expires_at": 100.0,
            }
            store._heavy_object_timers["job-archived"] = timer

        with patch("haute.routes._job_store.time.time", return_value=101.0):
            with pytest.raises(ValueError, match="invalid status"):
                store._clear_expired_heavy_objects(job_id="job-archived", timer=timer)

        assert "solver" in _stored_job(store, "job-archived")
        assert store._heavy_object_timers["job-archived"] is timer

    def test_job_specific_cleanup_rejects_lexically_higher_unknown_status(
        self,
    ) -> None:
        timer = type("T", (), {"cancel": lambda self: setattr(self, "cancelled", True)})()
        store = _job_store_without_cleanup_threads()
        with store._write_lock:
            store._jobs["job-zzz"] = {
                "status": "zzz",
                "solver": object(),
                "created_at": 50.0,
                "heavy_objects_expires_at": 100.0,
            }
            store._heavy_object_timers["job-zzz"] = timer

        with patch("haute.routes._job_store.time.time", return_value=101.0):
            with pytest.raises(ValueError, match="invalid status"):
                store._clear_expired_heavy_objects(job_id="job-zzz", timer=timer)

        assert "solver" in _stored_job(store, "job-zzz")
        assert store._heavy_object_timers["job-zzz"] is timer

    def test_cancels_existing_timer_when_no_heavy_keys_remain(self) -> None:
        """When the cleanup timer fires for a job that no longer has heavy
        fields (e.g. another path slimmed them already), cancel any leftover
        timer so it can't fire spuriously later."""
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))

        # Set up a completed job WITHOUT heavy keys, but WITH a registered timer
        # (the race we're guarding: timer was scheduled, then keys were cleared).
        prev_timer = type("T", (), {"cancel": lambda self: setattr(self, "cancelled", True)})()
        with store._write_lock:
            store._jobs["job-cleared"] = {
                "status": "completed",
                "terminal_reason": "completed",
                "created_at": time.time(),
                "ended_at": time.time(),
                "completed_at": time.time(),
            }
            store._heavy_object_timers["job-cleared"] = prev_timer

        store._schedule_heavy_object_cleanup("job-cleared", time.time() + 1.0)

        # The previous timer was cancelled, no new timer registered.
        assert getattr(prev_timer, "cancelled", False) is True
        assert "job-cleared" not in store._heavy_object_timers

    def test_skips_scheduling_when_expires_at_was_rescheduled_later_by_another_thread(
        self,
    ) -> None:
        """If another thread updated the job's expiry between the caller
        deciding to schedule and the lock acquisition, the caller's
        ``expires_at`` is stale — the other thread will (or has already)
        scheduled the up-to-date timer."""
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))

        # Pre-seed a completed job with a DIFFERENT expires_at than what the
        # caller has — simulating an in-flight reschedule.
        actual_expires = time.time() + 60.0
        with store._write_lock:
            store._jobs["job-rescheduled"] = {
                "status": "completed",
                "terminal_reason": "completed",
                "solver": object(),
                "created_at": time.time(),
                "ended_at": time.time(),
                "completed_at": time.time(),
                "heavy_objects_expires_at": actual_expires,
            }

        stale_expires = actual_expires + 30.0  # caller has a stale value
        store._schedule_heavy_object_cleanup("job-rescheduled", stale_expires)

        # The race guard at line 254-255 fires: no timer is registered.
        assert "job-rescheduled" not in store._heavy_object_timers

    def test_skips_scheduling_when_expires_at_was_rescheduled_earlier_by_another_thread(
        self,
    ) -> None:
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))

        actual_expires = time.time() + 60.0
        with store._write_lock:
            store._jobs["job-rescheduled-earlier"] = {
                "status": "completed",
                "terminal_reason": "completed",
                "solver": object(),
                "created_at": time.time(),
                "ended_at": time.time(),
                "completed_at": time.time(),
                "heavy_objects_expires_at": actual_expires,
            }

        stale_expires = actual_expires - 30.0
        store._schedule_heavy_object_cleanup("job-rescheduled-earlier", stale_expires)

        assert "job-rescheduled-earlier" not in store._heavy_object_timers

    def test_scheduling_uses_expiry_value_equality_not_identity(self) -> None:
        timers: list[object] = []
        store = JobStore(heavy_object_timer_factory=_manual_timer_factory(timers))

        stored_expires = float("123.5")
        caller_expires = float("123.5")
        assert stored_expires == caller_expires
        assert stored_expires is not caller_expires
        with store._write_lock:
            store._jobs["job-equal-expiry"] = {
                "status": "completed",
                "terminal_reason": "completed",
                "solver": object(),
                "created_at": 50.0,
                "ended_at": 50.0,
                "completed_at": 50.0,
                "heavy_objects_expires_at": stored_expires,
            }

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            store._schedule_heavy_object_cleanup("job-equal-expiry", caller_expires)

        assert len(timers) == 1
        assert timers[0].started is True
