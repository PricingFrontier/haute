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
from typing import TYPE_CHECKING

import pytest

from haute.routes._job_store import JobStore

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ============================================================================
# Helpers
# ============================================================================


def _inject_job(store: JobStore, status: str, **extra) -> str:
    """Create a fake job in the given store with the specified status."""
    fields = {
        "status": status,
        "progress": 1.0 if status == "completed" else 0.5,
        "message": status.capitalize(),
        "config": {},
        "node_label": "test_node",
        **extra,
    }
    return store.create_job(fields)


# ============================================================================
# 1. Double-start training — 409 Conflict
# ============================================================================


class TestDoubleStartTraining:
    """Starting a training job while one is already running must return 409."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from haute.routes.modelling import _store

        self._store = _store
        self._snapshot = dict(_store.jobs)
        yield
        _store.jobs.clear()
        _store.jobs.update(self._snapshot)

    def test_409_when_job_already_running(self, client: TestClient) -> None:
        _inject_job(self._store, "running")

        # The train endpoint requires a valid graph payload; it will check
        # for concurrent jobs before doing any heavy validation.
        # We send a minimal (invalid) graph — the concurrency check fires
        # first because it is inside _check_no_concurrent_jobs which runs
        # before pipeline execution.
        payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataSource",
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
                                "params": {"iterations": 5},
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
                            "nodeType": "dataSource",
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
                                "params": {"iterations": 5},
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
    def _setup(self):
        from haute.routes.modelling import _store

        self._store = _store
        self._snapshot = dict(_store.jobs)
        yield
        _store.jobs.clear()
        _store.jobs.update(self._snapshot)

    def test_completed_job_returns_same_status_on_repeated_polls(
        self,
        client: TestClient,
    ) -> None:
        job_id = _inject_job(
            self._store,
            "completed",
            progress=1.0,
            message="Done",
            result={"status": "completed", "metrics": {"rmse": 0.5}},
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
    def _setup(self):
        from haute.routes.modelling import _store as modelling_store
        from haute.routes.optimiser import _store as optimiser_store

        self._stores = {
            "modelling": modelling_store,
            "optimiser": optimiser_store,
        }
        self._snapshots = {name: dict(store.jobs) for name, store in self._stores.items()}
        yield
        for name, store in self._stores.items():
            store.jobs.clear()
            store.jobs.update(self._snapshots[name])

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
                            "nodeType": "dataSource",
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
                                "params": {"iterations": 100},
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
        with pytest.raises(Exception) as exc_info:
            store.require_completed_job(job_id)
        # HTTPException with status 400
        assert exc_info.value.status_code == 400  # type: ignore[attr-defined]

    def test_require_completed_on_error_raises_400(self) -> None:
        store = JobStore()
        job_id = _inject_job(store, "error")
        with pytest.raises(Exception) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400  # type: ignore[attr-defined]


# ============================================================================
# 8. Revert to non-existent SHA
# ============================================================================


class TestRevertNonExistentSHA:
    """Reverting to a SHA that doesn't exist must raise GitError."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from tests._git_helpers import git_run as _git
        from tests._git_helpers import init_repo as _init_repo

        repo = _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        # Create a user branch so revert is allowed (not on protected branch)
        _git(tmp_path, "checkout", "-b", "pricing/test-user/my-feature")
        return repo

    def test_revert_to_nonexistent_sha(self) -> None:
        from haute._git import GitError, revert_to

        with pytest.raises(GitError, match="not found"):
            revert_to("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

    def test_revert_to_garbage_string(self) -> None:
        from haute._git import GitError, revert_to

        with pytest.raises(GitError, match="not found"):
            revert_to("not_a_real_sha_at_all")

    def test_revert_via_api(self, client: TestClient) -> None:
        resp = client.post(
            "/api/git/revert",
            json={"sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"},
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()


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

    def test_delete_main_raises_guardrail(self) -> None:
        from haute._git import GitGuardrailError, delete_branch

        with pytest.raises(GitGuardrailError, match="protected"):
            delete_branch("main")

    def test_delete_master_raises_guardrail(self) -> None:
        from haute._git import GitGuardrailError, delete_branch

        with pytest.raises(GitGuardrailError, match="protected"):
            delete_branch("master")

    def test_delete_main_via_api(self, client: TestClient) -> None:
        resp = client.request("DELETE", "/api/git/branches", json={"branch": "main"})
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()

    def test_delete_develop_via_api(self, client: TestClient) -> None:
        resp = client.request("DELETE", "/api/git/branches", json={"branch": "develop"})
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()


# ============================================================================
# 10. Switch to current branch (no-op, should succeed silently)
# ============================================================================


class TestSwitchToCurrentBranch:
    """Switching to the branch you're already on should be a silent no-op."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from tests._git_helpers import init_repo as _init_repo

        repo = _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        return repo

    def test_switch_to_current_is_noop(self) -> None:
        from haute._git import _get_current_branch, switch_branch

        current = _get_current_branch()
        # Should not raise
        switch_branch(current)
        assert _get_current_branch() == current

    def test_switch_to_current_via_api(self, client: TestClient) -> None:
        status = client.get("/api/git/status").json()
        current = status["branch"]
        resp = client.post("/api/git/switch", json={"branch": current})
        assert resp.status_code == 200


# ============================================================================
# 11. Create duplicate branch
# ============================================================================


class TestCreateDuplicateBranch:
    """Creating a branch that already exists must fail with a clear error."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from tests._git_helpers import init_repo as _init_repo

        repo = _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        return repo

    def test_duplicate_branch_raises(self) -> None:
        from haute._git import GitError, create_branch

        create_branch("my feature")
        with pytest.raises(GitError, match="already exists"):
            create_branch("my feature")

    def test_duplicate_via_api(self, client: TestClient, tmp_path: Path) -> None:
        from tests._git_helpers import git_run as _git

        resp1 = client.post("/api/git/branches", json={"description": "rate update"})
        assert resp1.status_code == 200

        # Switch back to main so we can try creating the same branch again
        _git(tmp_path, "checkout", "main")

        resp2 = client.post("/api/git/branches", json={"description": "rate update"})
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["detail"].lower()


# ============================================================================
# 12. Save on protected branch
# ============================================================================


class TestSaveOnProtectedBranch:
    """Saving progress on main/master must be blocked by guardrails."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from tests._git_helpers import init_repo as _init_repo

        repo = _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        return repo

    def test_save_on_main_raises(self, tmp_path: Path) -> None:
        from haute._git import GitGuardrailError, save_progress

        # Create a change so there's something to save
        (tmp_path / "change.py").write_text("x = 1\n")
        with pytest.raises(GitGuardrailError, match="protected"):
            save_progress()

    def test_save_on_main_via_api(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / "change.py").write_text("x = 1\n")
        resp = client.post("/api/git/save")
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
        self._snapshot = dict(_store.jobs)
        yield
        _store.jobs.clear()
        _store.jobs.update(self._snapshot)

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
        assert body["status"] == "error"
        assert "timed out" in body["message"].lower()


# ============================================================================
# JobStore edge cases
# ============================================================================


class TestJobStoreEdgeCases:
    """Direct unit tests for JobStore transition invariants."""

    def test_require_job_404_for_missing_id(self) -> None:
        store = JobStore()
        with pytest.raises(Exception) as exc_info:
            store.require_job("nonexistent")
        assert exc_info.value.status_code == 404  # type: ignore[attr-defined]

    def test_atomic_update_preserves_existing_fields(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running", "progress": 0.0, "extra": "keep"})
        store.atomic_update(job_id, {"status": "completed", "progress": 1.0})
        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["extra"] == "keep"

    def test_status_transitions_running_to_completed(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        store.atomic_update(job_id, {"status": "completed"})
        job = store.require_completed_job(job_id)
        assert job["status"] == "completed"

    def test_status_transitions_running_to_error(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "running"})
        store.atomic_update(job_id, {"status": "error", "message": "boom"})
        job = store.require_job(job_id)
        assert job["status"] == "error"
        with pytest.raises(Exception) as exc_info:
            store.require_completed_job(job_id)
        assert exc_info.value.status_code == 400  # type: ignore[attr-defined]

    def test_error_job_stays_error_after_repeated_access(self) -> None:
        store = JobStore()
        job_id = store.create_job({"status": "error", "message": "fail"})
        for _ in range(10):
            job = store.require_job(job_id)
            assert job["status"] == "error"


# ============================================================================
# Git revert on protected branch (via API)
# ============================================================================


class TestRevertOnProtectedBranch:
    """Reverting on a protected branch must be blocked."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from tests._git_helpers import init_repo as _init_repo

        repo = _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        # Stay on main (protected)
        return repo

    def test_revert_on_main_via_api(self, client: TestClient, tmp_path: Path) -> None:
        from tests._git_helpers import git_run as _git

        sha = _git(tmp_path, "rev-parse", "HEAD")
        resp = client.post("/api/git/revert", json={"sha": sha})
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()
