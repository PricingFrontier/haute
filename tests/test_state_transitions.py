"""Adversarial tests for invalid state machine transitions.

Validates that the system correctly rejects or handles:
- Double-start of training/optimiser jobs
- Operations on wrong-status jobs (running, error, completed)
- Git guardrail violations (protected branches, bad SHAs, duplicates)

Uses the in-memory JobStore directly (no real training/solving) so tests
are fast and deterministic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import HTTPException

from haute.routes._job_lifecycle import JobLifecycle
from haute.routes._job_store import TERMINAL_REASONS, JobStore, TerminalReason

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ============================================================================
# Helpers
# ============================================================================


def _inject_job(store: JobStore, status: str, **extra) -> str:
    """Create a fake job and reach terminal states through the lifecycle API."""
    fields = {
        "progress": 1.0 if status == "completed" else 0.5,
        "message": status.capitalize(),
        "config": {},
        "node_label": "test_node",
        **extra,
    }
    job_id = store.create_job({"status": "running", **fields})
    if status != "running":
        if status not in TERMINAL_REASONS:
            raise ValueError(f"Unsupported injected job status: {status!r}")
        transitioned = JobLifecycle(store).transition(
            job_id,
            to=cast(TerminalReason, status),
        )
        if transitioned is None:
            raise AssertionError(f"Failed to prepare terminal test job: {status!r}")
    return job_id


def _random_single_evaluation() -> dict:
    return {
        "schema_version": 1,
        "strategy": "random",
        "seed": 42,
        "validation": {"method": "single", "size": 0.2},
    }


def _completed_train_result() -> dict:
    return {
        "status": "completed",
        "diagnostic_metrics": {"rmse": 0.5},
        "final_test_metrics": {},
        "development_rows": 8,
        "final_test_rows": 0,
        "diagnostics_set": "development",
        "evaluation": {
            "schema_version": 1,
            "strategy": "random",
            "validation_method": "none",
            "validation_fit_count": 0,
            "fit_count": 1,
            "development_rows": 8,
            "final_test_rows": 0,
            "selection_fits": [],
            "selection_metrics": {},
            "plan_sha256": "0" * 64,
            "results_sha256": "1" * 64,
            "plan_path": "evaluation-plan.json",
            "results_path": "evaluation-results.json",
            "report_path": "evaluation-report.json",
            "summary": {"development_rows": 8, "test_rows": 0, "validation_fit_count": 0},
        },
    }


# ============================================================================
# 1. Double-start training — 409 Conflict
# ============================================================================


class TestDoubleStartTraining:
    """Starting a training job while one is already running must return 409."""

    @pytest.fixture(autouse=True)
    def _setup(self, clean_training_job_store):
        self._store = clean_training_job_store

    def test_409_when_job_already_running(self, client: TestClient) -> None:
        _inject_job(self._store, "running")

        # The train endpoint receives a valid, lightweight graph; the 409
        # concurrency check still takes precedence over pipeline execution.
        payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataInput",
                            "config": {"path": "fake.parquet"},
                        },
                    },
                    {
                        "id": "m1",
                        "data": {
                            "label": "model",
                            "nodeType": "modelling",
                            "config": {
                                "target": "y",
                                "algorithm": "catboost",
                                "loss_function": "RMSE",
                                "params": {"iterations": 5},
                                "evaluation": _random_single_evaluation(),
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "m1"}],
            },
            "node_id": "m1",
        }
        resp = client.post("/api/modelling/train", json=payload)
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        assert "already running" in resp.json()["detail"].lower()

    def test_allows_start_when_previous_completed(self, client: TestClient) -> None:
        """A completed job should not block a new start (no 409)."""
        _inject_job(self._store, "completed")
        # The request may fail for other reasons (missing file etc.) but
        # it must NOT be 409.
        payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataInput",
                            "config": {"path": "fake.parquet"},
                        },
                    },
                    {
                        "id": "m1",
                        "data": {
                            "label": "model",
                            "nodeType": "modelling",
                            "config": {
                                "target": "y",
                                "algorithm": "catboost",
                                "loss_function": "RMSE",
                                "params": {"iterations": 5},
                                "evaluation": _random_single_evaluation(),
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "m1"}],
            },
            "node_id": "m1",
        }
        resp = client.post("/api/modelling/train", json=payload)
        assert resp.status_code != 409, "Completed job should not block new training"


# ============================================================================
# 2. Poll completed job — stable results
# ============================================================================


class TestPollCompletedJob:
    """Polling a completed training job should return stable, consistent results."""

    @pytest.fixture(autouse=True)
    def _setup(self, clean_training_job_store):
        self._store = clean_training_job_store

    def test_completed_job_returns_same_status_on_repeated_polls(
        self,
        client: TestClient,
    ) -> None:
        job_id = _inject_job(
            self._store,
            "completed",
            progress=1.0,
            message="Done",
            result=_completed_train_result(),
            elapsed_seconds=10.0,
        )

        responses = []
        for _ in range(3):
            resp = client.get(f"/api/modelling/train/status/{job_id}")
            assert resp.status_code == 200
            responses.append(resp.json())

        # All three polls should return identical data
        for r in responses:
            assert r["status"] == "completed"
            assert r["progress"] == 1.0

        assert responses[0] == responses[1] == responses[2]

    def test_poll_nonexistent_job_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/modelling/train/status/does_not_exist")
        assert resp.status_code == 404


# ============================================================================
# 3. Reject non-completed job — 400 (parametrized)
# ============================================================================


def _make_payload(job_id: str, url: str) -> dict:
    """Build the minimal JSON payload for the given endpoint, inserting job_id."""
    if url == "/api/optimiser/frontier/select":
        return {"job_id": job_id, "point_index": 0}
    if url == "/api/optimiser/save":
        return {"job_id": job_id, "output_path": "output/test.json"}
    # Default: endpoints that only need a job_id
    return {"job_id": job_id}


class TestRejectNonCompletedJob:
    """Every endpoint that requires a completed job must return 400
    with 'not completed' in the detail when given a running or error job."""

    _CASES = [
        ("optimiser", "error", "/api/optimiser/frontier/select"),
        ("optimiser", "running", "/api/optimiser/frontier/select"),
        ("modelling", "running", "/api/modelling/mlflow/log"),
        ("modelling", "error", "/api/modelling/mlflow/log"),
        ("optimiser", "running", "/api/optimiser/apply"),
        ("optimiser", "error", "/api/optimiser/apply"),
        ("optimiser", "running", "/api/optimiser/save"),
        ("optimiser", "error", "/api/optimiser/save"),
        ("optimiser", "running", "/api/optimiser/mlflow/log"),
    ]

    @pytest.fixture(autouse=True)
    def _setup(self, clean_training_job_store, clean_job_store):
        self._stores = {
            "modelling": clean_training_job_store,
            "optimiser": clean_job_store,
        }

    @pytest.mark.parametrize("store_name,status,url", _CASES)
    def test_rejects_non_completed_job(
        self,
        client: TestClient,
        store_name: str,
        status: str,
        url: str,
    ) -> None:
        store = self._stores[store_name]
        extra = {"message": "Injected failure"} if status == "error" else {}
        job_id = _inject_job(store, status, **extra)

        resp = client.post(url, json=_make_payload(job_id, url))
        assert resp.status_code == 400, (
            f"Expected 400 for {url} with status={status}, got {resp.status_code}: {resp.text}"
        )
        assert "not completed" in resp.json()["detail"].lower()


# ============================================================================
# 5. Export on error job — should still work (export uses graph, not job state)
#    However, export requires a valid graph node. We test that export does
#    NOT depend on job state at all (it reads the graph directly).
# ============================================================================


class TestExportScript:
    """Export generates a script from the graph config, not from job state.

    This test verifies that export works regardless of job status because
    it reads the node config from the submitted graph payload.
    """

    def test_export_works_with_valid_graph(self, client: TestClient) -> None:
        """Export only needs a valid graph with a modelling node."""
        payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataInput",
                            "config": {"path": "data.parquet"},
                        },
                    },
                    {
                        "id": "m1",
                        "data": {
                            "label": "my_model",
                            "nodeType": "modelling",
                            "config": {
                                "target": "y",
                                "algorithm": "catboost",
                                "loss_function": "RMSE",
                                "params": {"iterations": 100},
                                "evaluation": _random_single_evaluation(),
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "m1"}],
            },
            "node_id": "m1",
        }
        resp = client.post("/api/modelling/export", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert "script" in body
        assert "filename" in body


# ============================================================================
# 7. Cancel completed training — require_job returns stable "completed"
#    (There is no cancel endpoint — verify the completed state is immutable)
# ============================================================================


class TestCompletedJobImmutability:
    """A completed job's status must not change on repeated access."""

    def test_completed_job_status_is_stable(self) -> None:
        store = JobStore()
        job_id = _inject_job(store, "completed", result={"metrics": {"rmse": 0.1}})

        # Simulate repeated access
        for _ in range(5):
            job = store.require_job(job_id)
            assert job["status"] == "completed"

    def test_require_completed_on_completed_succeeds(self) -> None:
        store = JobStore()
        job_id = _inject_job(store, "completed")
        # Should not raise
        job = store.require_completed_job(job_id)
        assert job["status"] == "completed"

    def test_require_completed_on_running_raises_400(self) -> None:
        store = JobStore()
        job_id = _inject_job(store, "running")
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400

    def test_require_completed_on_error_raises_400(self) -> None:
        store = JobStore()
        job_id = _inject_job(store, "error")
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400


# ============================================================================
# 9. Delete protected branch
# ============================================================================


class TestDeleteProtectedBranch:
    """Deleting main/master must be blocked by guardrails."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from tests._git_helpers import git_run as _git
        from tests._git_helpers import init_repo as _init_repo

        repo = _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        # Move off main so the delete attempt targets a different-than-current branch
        _git(tmp_path, "checkout", "-b", "pricing/test-user/work")
        return repo

    def test_delete_main_via_api(self, client: TestClient) -> None:
        resp = client.request("DELETE", "/api/git/branches", json={"branch": "main"})
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()

    def test_delete_develop_via_api(self, client: TestClient) -> None:
        resp = client.request("DELETE", "/api/git/branches", json={"branch": "develop"})
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()


# ============================================================================
# Optimiser timeout detection at poll time
# ============================================================================


class TestOptimiserTimeoutDetection:
    """A running optimiser job that exceeds its timeout should transition
    to error status when polled."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from haute.routes.optimiser import _store

        self._store = _store
        _store.clear_all()
        yield
        _store.clear_all()

    def test_timeout_detection_on_poll(self, client: TestClient) -> None:
        # Create a running job with start_time far in the past
        job_id = _inject_job(
            self._store,
            "running",
            start_time=time.monotonic() - 9999,  # way past any timeout
            timeout=1,  # 1-second timeout
        )

        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "timed_out"
        assert body["terminal_reason"] == "timed_out"
        assert "timed out" in body["message"].lower()


# ============================================================================
# JobStore edge cases
# ============================================================================


class TestJobStoreEdgeCases:
    """Direct unit tests for JobStore transition invariants."""

    def test_require_job_404_for_missing_id(self) -> None:
        store = JobStore()
        with pytest.raises(HTTPException) as exc_info:
            store.require_job("nonexistent")
        assert exc_info.value.status_code == 404

    def test_atomic_update_preserves_existing_fields(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running", "progress": 0.0, "extra": "keep"})
        JobLifecycle(store).transition(job_id, to="completed", fields={"progress": 1.0})
        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["extra"] == "keep"

    def test_status_transitions_running_to_completed(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        JobLifecycle(store).transition(job_id, to="completed")
        job = store.require_completed_job(job_id)
        assert job["status"] == "completed"

    def test_status_transitions_running_to_error(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        JobLifecycle(store).transition(job_id, to="error", message="boom")
        job = store.require_job(job_id)
        assert job["status"] == "error"
        with pytest.raises(HTTPException) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400

    def test_error_job_stays_error_after_repeated_access(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running", "message": "Starting"})
        JobLifecycle(store).transition(job_id, to="error", message="fail")
        for _ in range(10):
            job = store.require_job(job_id)
            assert job["status"] == "error"
