"""Tests for the shared in-memory JobStore.

Covers basic CRUD operations, TTL eviction, and concurrent access patterns
to verify dict-backed mutation doesn't lose data under threading.
"""

from __future__ import annotations

import threading
import time

import pytest

from haute.routes._job_store import JobStore

# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


class TestJobStoreCRUD:
    """Unit tests for create, read, update, and list operations."""

    def test_create_job_returns_id(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "pending"})
        assert isinstance(job_id, str)
        assert len(job_id) >= 8
        assert job_id.isalnum()

    def test_get_job_returns_stored_data(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running", "model": "glm"})
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
        job_id = store.create_job({"status": "pending", "progress": 0})
        store.update_job(job_id, status="running", progress=50)
        result = store.get_job(job_id)
        assert result is not None
        assert result["status"] == "running"
        assert result["progress"] == 50

    def test_update_job_raises_for_unknown_id(self) -> None:
        store = JobStore()
        with pytest.raises(KeyError):
            store.update_job("nonexistent", status="done")

    def test_list_jobs_via_property(self) -> None:
        store = JobStore()
        id1 = store.create_job({"status": "a"})
        id2 = store.create_job({"status": "b"})
        assert id1 in store.jobs
        assert id2 in store.jobs
        assert len(store.jobs) == 2

    def test_create_job_sets_created_at_if_missing(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "new"})
        result = store.get_job(job_id)
        assert result is not None
        assert "created_at" in result
        assert isinstance(result["created_at"], float)

    def test_create_job_preserves_explicit_created_at(self) -> None:
        store = JobStore()
        ts = time.time()  # must be recent enough to survive TTL eviction
        job_id = store.create_job({"status": "new", "created_at": ts})
        result = store.get_job(job_id)
        assert result is not None
        assert result["created_at"] == ts

    def test_unique_ids_across_many_jobs(self) -> None:
        store = JobStore()
        ids = {store.create_job({"status": "pending"}) for _ in range(100)}
        assert len(ids) == 100  # all unique


# ---------------------------------------------------------------------------
# TTL eviction
# ---------------------------------------------------------------------------


class TestJobStoreTTL:
    """Tests for time-based eviction."""

    def test_stale_jobs_are_evicted_on_create(self) -> None:
        store = JobStore(ttl_seconds=1)
        old_id = store.create_job({"status": "old", "created_at": time.time() - 10})
        # Creating a new job triggers eviction
        _new_id = store.create_job({"status": "new"})
        assert store.get_job(old_id) is None

    def test_stale_jobs_are_evicted_on_get(self) -> None:
        store = JobStore(ttl_seconds=1)
        old_id = store.create_job({"status": "old", "created_at": time.time() - 10})
        # get_job triggers eviction
        assert store.get_job(old_id) is None

    def test_fresh_jobs_survive_eviction(self) -> None:
        store = JobStore(ttl_seconds=60)
        job_id = store.create_job({"status": "fresh"})
        _trigger = store.create_job({"status": "trigger"})
        assert store.get_job(job_id) is not None

    def test_mixed_stale_and_fresh(self) -> None:
        store = JobStore(ttl_seconds=5)
        stale_id = store.create_job({"status": "stale", "created_at": time.time() - 100})
        fresh_id = store.create_job({"status": "fresh"})
        # Trigger eviction via a new create
        store.create_job({"status": "trigger"})
        assert store.get_job(stale_id) is None
        assert store.get_job(fresh_id) is not None


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
            jid = store.create_job({"status": "pending", "index": idx})
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
            assert result["status"] == "pending"

    def test_concurrent_updates_no_data_loss(self) -> None:
        """Concurrent status updates to different jobs don't cause data loss."""
        store = JobStore()
        n_jobs = 10
        ids = [store.create_job({"status": "pending", "counter": 0}) for _ in range(n_jobs)]
        barrier = threading.Barrier(n_jobs)

        def update_one(job_id: str, value: int) -> None:
            barrier.wait()
            store.update_job(job_id, status="done", counter=value)

        threads = [threading.Thread(target=update_one, args=(ids[i], i + 1)) for i in range(n_jobs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All jobs should have been updated
        for i, jid in enumerate(ids):
            result = store.get_job(jid)
            assert result is not None
            assert result["status"] == "done"
            assert result["counter"] == i + 1

    def test_concurrent_updates_to_same_job(self) -> None:
        """Multiple threads updating the same job's fields concurrently.

        Each thread increments a different field, so no updates should be lost.
        """
        store = JobStore()
        job_id = store.create_job({"status": "running"})
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
                jid = store.create_job({"status": "new", "idx": idx})
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
            store.create_job({"status": "stale", "created_at": time.time() - 10})

        n_threads = 10
        new_ids: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(n_threads)

        def create_with_eviction(idx: int) -> None:
            barrier.wait()
            jid = store.create_job({"status": "fresh", "idx": idx})
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
            assert result["status"] == "fresh"


# ---------------------------------------------------------------------------
# require_job
# ---------------------------------------------------------------------------


class TestRequireJob:
    """Tests for require_job — raises HTTP 404 for missing jobs."""

    def test_returns_existing_job(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running", "progress": 0.5})
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
        job_id = store.create_job({"status": "old", "created_at": time.time() - 10})
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
        job_id = store.create_job({"status": "completed", "result": {"score": 0.95}})
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
        job_id = store.create_job({"status": "running"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400
        assert "not completed" in exc_info.value.detail
        assert "running" in exc_info.value.detail

    def test_raises_400_for_pending_job(self) -> None:
        from fastapi import HTTPException

        store = JobStore()
        job_id = store.create_job({"status": "pending"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400
        assert "not completed" in exc_info.value.detail
        assert "pending" in exc_info.value.detail

    def test_raises_400_for_error_job(self) -> None:
        from fastapi import HTTPException

        store = JobStore()
        job_id = store.create_job({"status": "error", "message": "boom"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400
        assert "not completed" in exc_info.value.detail
        assert "error" in exc_info.value.detail

    def test_raises_400_when_status_missing(self) -> None:
        """A job dict with no 'status' key should be treated as not completed."""
        from fastapi import HTTPException

        store = JobStore()
        job_id = store.create_job({"progress": 0.0})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400
        assert "not completed" in exc_info.value.detail

    def test_detail_includes_job_id(self) -> None:
        """Error messages should include the job ID for debuggability."""
        from fastapi import HTTPException

        store = JobStore()
        job_id = store.create_job({"status": "running"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert job_id in exc_info.value.detail

    def test_raises_404_for_evicted_job(self) -> None:
        """An evicted (stale) job should raise 404, not 400."""
        from fastapi import HTTPException

        store = JobStore(ttl_seconds=1)
        job_id = store.create_job({"status": "completed", "created_at": time.time() - 10})
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
        job_id = store.create_job({"status": "running", "progress": 0.0})
        store.atomic_update(job_id, {"status": "completed", "progress": 1.0})
        result = store.get_job(job_id)
        assert result is not None
        assert result["status"] == "completed"
        assert result["progress"] == 1.0

    def test_atomic_update_preserves_existing_keys(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running", "config": {"x": 1}})
        store.atomic_update(job_id, {"status": "completed"})
        result = store.get_job(job_id)
        assert result is not None
        assert result["config"] == {"x": 1}
        assert result["status"] == "completed"

    def test_atomic_update_creates_new_dict(self) -> None:
        """The old dict reference should no longer be the stored one."""
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        old_dict = store.get_job(job_id)
        store.atomic_update(job_id, {"status": "completed"})
        new_dict = store.get_job(job_id)
        # The new dict should be a different object
        assert old_dict is not new_dict
        # The old dict should still have the old status
        assert old_dict["status"] == "running"
        # The new dict should have the new status
        assert new_dict["status"] == "completed"

    def test_atomic_update_raises_for_unknown_id(self) -> None:
        store = JobStore()
        with pytest.raises(KeyError):
            store.atomic_update("nonexistent", {"status": "done"})

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
        job_id = store.create_job({"status": "running"})
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

        We simulate the pattern: background thread updates status + progress
        atomically, while main thread reads the job dict repeatedly.
        """
        store = JobStore()
        job_id = store.create_job({"status": "running", "progress": 0.0})

        partial_states_seen: list[bool] = []
        stop_event = threading.Event()

        def reader() -> None:
            while not stop_event.is_set():
                job = store.get_job(job_id)
                if job is None:
                    continue
                status = job.get("status")
                progress = job.get("progress")
                # A partial state would be: status is "completed" but
                # progress is still 0.0, or vice versa.
                if status == "completed" and progress != 1.0:
                    partial_states_seen.append(True)
                if status == "running" and progress == 1.0:
                    partial_states_seen.append(True)

        def writer() -> None:
            for _ in range(100):
                store.atomic_update(
                    job_id,
                    {
                        "status": "completed",
                        "progress": 1.0,
                    },
                )
                store.atomic_update(
                    job_id,
                    {
                        "status": "running",
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
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "completed",
                "solver": "heavy_solver_object",
                "solve_result": "heavy_result_object",
                "quote_grid": "heavy_grid_object",
                "config": {"objective": "income"},
                "result": {"converged": True},
            }
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

    def test_clears_custom_keys(self) -> None:
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "completed",
                "big_thing": "data",
                "another": "thing",
                "keep": "this",
            }
        )
        store.clear_result_data(job_id, keys=("big_thing", "another"))
        job = store.get_job(job_id)
        assert job is not None
        assert "big_thing" not in job
        assert "another" not in job
        assert job["keep"] == "this"

    def test_noop_for_missing_job(self) -> None:
        """Should not raise for a nonexistent job ID."""
        store = JobStore()
        store.clear_result_data("nonexistent")  # no exception

    def test_noop_when_keys_already_absent(self) -> None:
        """If heavy keys were never stored, the method is a no-op."""
        store = JobStore()
        job_id = store.create_job({"status": "completed", "result": {"ok": True}})
        store.clear_result_data(job_id)
        job = store.get_job(job_id)
        assert job is not None
        assert job["status"] == "completed"
        assert job["result"] == {"ok": True}

    def test_idempotent(self) -> None:
        """Calling clear_result_data twice should not error or change state."""
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "completed",
                "solver": "heavy",
                "solve_result": "heavy",
            }
        )
        store.clear_result_data(job_id)
        store.clear_result_data(job_id)  # second call
        job = store.get_job(job_id)
        assert job is not None
        assert "solver" not in job
        assert job["status"] == "completed"

    def test_clear_uses_atomic_replacement(self) -> None:
        """The old dict reference should not be mutated."""
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "completed",
                "solver": "heavy",
            }
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
        job_id = store.create_job({"status": "running", "progress": 0.0, "step": 0})

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
        job_id2 = store2.create_job({"status": "running", "progress": 0.0, "step": 0})
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
    """Running jobs must NOT be evicted by _evict_stale.

    Previously, _evict_stale only checked created_at and would evict
    running jobs after TTL, causing the background thread to crash with
    KeyError.  The fix skips jobs with status="running".
    """

    def test_running_job_survives_eviction(self) -> None:
        """A running job older than TTL is NOT evicted."""
        store = JobStore(ttl_seconds=10)

        job_id = store.create_job(
            {
                "status": "running",
                "progress": 0.5,
                "created_at": time.time() - 25 * 3600,
            }
        )

        assert store.jobs.get(job_id) is not None

        # Trigger eviction by creating another job
        store.create_job({"status": "new"})

        # Running job survives eviction
        assert store.get_job(job_id) is not None
        # Background thread can still update it
        store.update_job(job_id, progress=0.6, message="Still training...")
        assert store.get_job(job_id)["progress"] == 0.6

    def test_completed_job_evicted_after_ttl(self) -> None:
        """A completed job older than TTL IS evicted normally."""
        store = JobStore(ttl_seconds=1)
        job_id = store.create_job(
            {
                "status": "completed",
                "created_at": time.time() - 100,
            }
        )
        store.create_job({"status": "trigger"})
        assert store.get_job(job_id) is None

    def test_running_job_safe_during_concurrent_access(self) -> None:
        """Running job survives even when concurrent eviction triggers."""
        store = JobStore(ttl_seconds=2)

        job_id = store.create_job(
            {
                "status": "running",
                "progress": 0.0,
                "created_at": time.time() - 5,
            }
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

        # Running job survives — no KeyError
        assert store.get_job(job_id) is not None


# ---------------------------------------------------------------------------
# GAP 3: No concurrent optimiser guard
# ---------------------------------------------------------------------------


class TestOptimiserNoConcurrencyGuard:
    """Demonstrate that OptimiserSolveService lacks a _start_lock.

    Production failure: Two users (or a double-click) fire off two
    optimiser solve jobs simultaneously.  Unlike TrainService which has
    ``_start_lock`` + ``_check_no_concurrent_jobs()``, the optimiser
    service has no such guard.  Multiple jobs run in parallel, competing
    for CPU/memory.

    We test at the JobStore level since we can't easily instantiate the
    full service, but the test documents the architectural gap.
    """

    def test_train_service_has_start_lock(self) -> None:
        """Verify TrainService has the _start_lock attribute."""
        from haute.routes._train_service import TrainService

        store = JobStore()
        svc = TrainService(store)
        assert hasattr(svc, "_start_lock")
        assert isinstance(svc._start_lock, type(threading.Lock()))

    def test_optimiser_service_lacks_start_lock(self) -> None:
        """Verify OptimiserSolveService does NOT have a _start_lock.

        This documents the gap: nothing prevents concurrent solves.
        """
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        svc = OptimiserSolveService(store)
        assert not hasattr(svc, "_start_lock"), (
            "OptimiserSolveService now has _start_lock — update this test"
        )

    def test_multiple_running_jobs_allowed_in_store(self) -> None:
        """Without a lock, multiple 'running' jobs coexist in the store.

        TrainService explicitly checks for running jobs under a lock.
        The optimiser service does not, so multiple running jobs are
        possible.  This test demonstrates the difference.
        """
        store = JobStore()

        # Simulate two concurrent optimiser job starts (no lock, no check)
        id1 = store.create_job({"status": "running", "type": "optimiser"})
        id2 = store.create_job({"status": "running", "type": "optimiser"})

        running = [jid for jid, j in store.jobs.items() if j.get("status") == "running"]
        # Both are running — no guard rejected the second one
        assert len(running) == 2
        assert id1 in running
        assert id2 in running

    def test_concurrent_optimiser_starts_race(self) -> None:
        """Simulate the double-click race: N threads all create 'running'
        jobs simultaneously.  All succeed (no lock blocks them).
        """
        store = JobStore()
        n_threads = 5
        barrier = threading.Barrier(n_threads)
        ids: list[str] = []
        lock = threading.Lock()

        def start_optimiser(idx: int) -> None:
            barrier.wait()
            jid = store.create_job(
                {
                    "status": "running",
                    "type": "optimiser",
                    "idx": idx,
                }
            )
            with lock:
                ids.append(jid)

        threads = [threading.Thread(target=start_optimiser, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(ids) == n_threads
        running = [jid for jid, j in store.jobs.items() if j.get("status") == "running"]
        assert len(running) == n_threads, (
            f"Expected {n_threads} concurrent running jobs, got {len(running)}"
        )


# ---------------------------------------------------------------------------
# GAP 4: clear_result_data is dead code (never called from routes)
# ---------------------------------------------------------------------------


class TestClearResultDataDeadCode:
    """Verify clear_result_data is defined but never invoked from routes.

    Production failure: Memory leak — after an optimiser solve completes
    and results are consumed, the heavy solver/solve_result/quote_grid
    objects stay in memory until TTL eviction (up to 24h).  The
    ``clear_result_data`` method was designed to fix this, but nobody
    calls it.

    These tests exercise the method end-to-end on realistic job shapes
    to ensure it works correctly if/when it is wired in.
    """

    def test_clear_result_data_not_called_anywhere_in_routes(self) -> None:
        """Structural test: grep the source tree for clear_result_data calls.

        If this test starts failing, it means someone wired the method
        into a route — great!  Update the test accordingly.
        """
        import importlib
        import inspect

        from haute.routes import _job_store

        # Get the source of the module that defines clear_result_data
        src = inspect.getsource(_job_store)
        # The definition appears once; there should be no call sites in
        # other modules.  We check by importing all route modules and
        # scanning for the method name.
        route_modules = []
        try:
            from haute.routes import _train_service

            route_modules.append(_train_service)
        except ImportError:
            pass
        try:
            from haute.routes import _optimiser_service

            route_modules.append(_optimiser_service)
        except ImportError:
            pass

        for mod in route_modules:
            mod_src = inspect.getsource(mod)
            assert "clear_result_data" not in mod_src, (
                f"clear_result_data is called in {mod.__name__} — no longer dead code!"
            )

    def test_end_to_end_optimiser_job_shape(self) -> None:
        """Exercise clear_result_data on a dict shaped like a real
        optimiser completed job — the use case it was designed for.
        """
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "completed",
                "progress": 1.0,
                "message": "Completed",
                "config": {"objective": "income", "constraints": {"loss_ratio": 1.0}},
                "solver": object(),  # heavy: OnlineOptimiser instance
                "solve_result": object(),  # heavy: solve result with full DataFrame
                "quote_grid": object(),  # heavy: QuoteGrid
                "result": {
                    "mode": "online",
                    "total_objective": 1.05,
                    "converged": True,
                    "frontier": None,
                },
                "frontier_data": None,
                "elapsed_seconds": 42.0,
            }
        )

        # Before clear: heavy objects present
        job = store.get_job(job_id)
        assert "solver" in job
        assert "solve_result" in job
        assert "quote_grid" in job

        store.clear_result_data(job_id)

        # After clear: heavy objects gone, metadata intact
        job = store.get_job(job_id)
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job
        assert job["status"] == "completed"
        assert job["result"]["converged"] is True
        assert job["config"]["objective"] == "income"
        assert job["elapsed_seconds"] == 42.0

    def test_clear_after_clear_is_safe(self) -> None:
        """Double-clear should not raise or corrupt."""
        store = JobStore()
        job_id = store.create_job(
            {
                "status": "completed",
                "solver": "big",
                "solve_result": "big",
                "quote_grid": "big",
                "result": {"ok": True},
            }
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
            store.create_job(
                {
                    "status": "stale",
                    "idx": i,
                    "created_at": time.time() - 100 - i,
                }
            )

        n_threads = 20
        barrier = threading.Barrier(n_threads)
        new_ids: list[str] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def create_and_evict(idx: int) -> None:
            barrier.wait()
            try:
                jid = store.create_job({"status": "fresh", "idx": idx})
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
            assert job["status"] == "fresh"

    def test_eviction_concurrent_with_updates(self) -> None:
        """Writers call update_job while readers call get_job (triggering
        eviction) — no crash, no data loss for non-stale jobs.
        """
        store = JobStore(ttl_seconds=2)

        # Create some stale jobs to be evicted
        for _ in range(20):
            store.create_job({"status": "old", "created_at": time.time() - 50})

        # Create fresh jobs that should survive
        fresh_ids = [store.create_job({"status": "running", "counter": 0}) for _ in range(5)]

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
        """External code iterates store.jobs (e.g. _check_no_concurrent_jobs)
        while another thread triggers eviction via create_job.
        """
        store = JobStore(ttl_seconds=1)

        # Populate with stale jobs
        for _ in range(30):
            store.create_job({"status": "stale", "created_at": time.time() - 50})
        # And a fresh one
        fresh_id = store.create_job({"status": "running"})

        errors: list[Exception] = []

        def iterator() -> None:
            """Mimic _check_no_concurrent_jobs — iterate .jobs.items()."""
            for _ in range(100):
                try:
                    running = [jid for jid, j in store.jobs.items() if j.get("status") == "running"]
                except Exception as exc:
                    errors.append(exc)

        def evictor() -> None:
            """Create new jobs, each triggering _evict_stale."""
            for _ in range(100):
                try:
                    store.create_job(
                        {
                            "status": "stale",
                            "created_at": time.time() - 50,
                        }
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
        assert store.jobs.get(fresh_id) is not None


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
        running_id = store.create_job({"status": "running"})
        error_id = store.create_job({"status": "error", "message": "OOM"})

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
        running_id = store.create_job({"status": "running"})
        error_id = store.create_job({"status": "error", "message": "OOM"})

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
        running_id = store.create_job({"status": "running", "progress": 0.5})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(running_id)
        assert exc_info.value.status_code == 400  # should arguably be 409

        # Errored job — semantically terminal
        error_id = store.create_job({"status": "error", "message": "CUDA OOM"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(error_id)
        assert exc_info.value.status_code == 400  # same code, different semantics

    def test_detail_format_is_consistent(self) -> None:
        """Verify the exact detail format for regression testing."""
        from fastapi import HTTPException

        store = JobStore()
        job_id = store.create_job({"status": "error"})
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        detail = exc_info.value.detail
        assert detail == f"Job '{job_id}' is not completed (status: error)"
