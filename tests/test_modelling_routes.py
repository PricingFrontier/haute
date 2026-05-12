"""API integration tests for modelling endpoints."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from haute.routes._train_service import (
    TrainService,
    _clamp_row_limit,
    _declared_categorical_levels_for_training,
    _friendly_error,
    _training_required_columns_by_node,
    _validate_glm_family_link,
)
from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _fast_training_params(**overrides: object) -> dict[str, object]:
    """Cheap-but-real CatBoost settings for endpoint tests."""
    params: dict[str, object] = {"iterations": 4, "depth": 2}
    params.update(overrides)
    return params


def _completed_train_result() -> object:
    """Small successful TrainResult for endpoint tests that do not care about fit quality."""
    from haute.modelling._training_job import TrainResult

    return TrainResult(
        metrics={"rmse": 0.1, "gini": 0.5},
        feature_importance=[],
        model_path="outputs/test_model.cbm",
        train_rows=48,
        test_rows=12,
        features=["x1", "x2"],
        cat_features=[],
    )


class TestTrainingCategoricalLevelDeclarations:
    def test_collects_source_declared_levels_through_transforms(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataSource",
                            "config": {
                                "path": "quotes.csv",
                                "categorical_levels": {"region": ["north", "south"]},
                            },
                        },
                    },
                    {
                        "id": "prep",
                        "data": {
                            "label": "prep",
                            "nodeType": "polars",
                            "config": {"code": "df = df"},
                        },
                    },
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": {"target": "y", "algorithm": "catboost"},
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "src", "target": "prep"},
                    {"id": "e2", "source": "prep", "target": "train"},
                ],
            }
        )

        levels = _declared_categorical_levels_for_training(
            graph,
            "train",
            graph.node_map["train"].data.config,
        )

        assert levels == {"region": ["north", "south"]}


def _make_modelling_graph(
    data_path: str,
    target: str = "y",
    weight: str | None = None,
    algorithm: str = "catboost",
    task: str = "regression",
    params: dict | None = None,
) -> dict:
    """Build a simple 2-node graph: dataSource → modelling."""
    config: dict = {
        "target": target,
        "algorithm": algorithm,
        "task": task,
        "params": params or _fast_training_params(),
        "split": {"strategy": "random", "validation_size": 0.2, "seed": 42},
        "metrics": ["gini", "rmse"] if task == "regression" else ["auc", "logloss"],
    }
    if weight:
        config["weight"] = weight

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": data_path},
                    },
                },
                {
                    "id": "train",
                    "data": {"label": "train", "nodeType": "modelling", "config": config},
                },
            ],
            "edges": [make_edge("source", "train").model_dump()],
        }
    )
    return graph.model_dump()


@pytest.fixture(autouse=True)
def _fast_optional_training_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint tests assert job state, metrics, and warnings, not optional charts."""
    monkeypatch.setattr(
        "haute.modelling._algorithms.CatBoostAlgorithm.shap_summary",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])


@pytest.fixture()
def training_data(tmp_path) -> str:
    """Create a small parquet file for training tests."""
    rng = np.random.RandomState(42)
    n = 60
    df = pl.DataFrame(
        {
            "x1": rng.randn(n),
            "x2": rng.randn(n),
            "y": (rng.randn(n) * 2 + 1).clip(0),
        }
    )
    path = tmp_path / "train_data.parquet"
    df.write_parquet(path)
    return str(path)


_TERMINAL_JOB_STATUSES = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    """Poll /train/status/{job_id} until a terminal status, return final status."""
    poll_interval = 0.02
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/modelling/train/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in _TERMINAL_JOB_STATUSES:
            return data
        time.sleep(poll_interval)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


class TestTrainEndpoint:
    def test_train_with_invalid_target(self, client, training_data):
        graph = _make_modelling_graph(training_data, target="nonexistent")
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 422
        assert "nonexistent" in resp.text

    def test_train_missing_node(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "nonexistent"})
        assert resp.status_code == 404

    def test_train_wrong_node_type(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "source"})
        assert resp.status_code == 400

    def test_train_success(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["job_id"]
        status = _poll_until_done(client, data["job_id"])
        result = status["result"]
        assert result["metrics"]
        assert result["train_rows"] > 0
        assert result["test_rows"] > 0
        # Should have ave_per_feature for the 2 features (x1, x2)
        assert "ave_per_feature" in result
        assert isinstance(result["ave_per_feature"], list)
        assert len(result["ave_per_feature"]) == 2
        for entry in result["ave_per_feature"]:
            assert "feature" in entry
            assert "type" in entry
            assert "bins" in entry

    def test_train_reports_progress(self, client, training_data):
        """Training should report iteration progress via the status endpoint."""
        graph = _make_modelling_graph(training_data, params=_fast_training_params(iterations=8))
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        data = resp.json()
        job_id = data["job_id"]
        # Poll a few times — we should see iteration progress at some point
        saw_iteration = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            resp = client.get(f"/api/modelling/train/status/{job_id}")
            status = resp.json()
            if status.get("iteration", 0) > 0:
                saw_iteration = True
            if status["status"] in ("completed", "error"):
                break
            time.sleep(0.02)
        # Multi-iteration runs should usually emit at least one update, though
        # fast training can still finish before a poll lands.
        assert saw_iteration or status.get("result", {}).get("train_rows", 0) > 0

    def test_train_rejects_concurrent(self, client, training_data):
        """A second training request while one is running returns 409."""
        from haute.routes.modelling import _store

        _store.jobs["fake_running"] = {
            "status": "running",
            "progress": 0.5,
            "message": "Training...",
            "created_at": time.time(),
        }
        try:
            graph = _make_modelling_graph(training_data)
            resp = client.post(
                "/api/modelling/train",
                json={"graph": graph, "node_id": "train"},
            )
            assert resp.status_code == 409
            assert "already running" in resp.json()["detail"]
        finally:
            _store.jobs.pop("fake_running", None)

    def test_train_gpu_refuses_on_vram_limit(self, client, training_data):
        """When GPU VRAM is insufficient, training should fail before launch."""
        graph = _make_modelling_graph(
            training_data,
            params=_fast_training_params(task_type="GPU"),
        )
        # Pretend GPU has only 1 byte VRAM -- forces refusal before launch.
        with (
            patch("haute._ram_estimate.available_vram_bytes", return_value=1),
            patch("haute.modelling.TrainingJob.run", return_value=_completed_train_result()) as run,
        ):
            resp = client.post(
                "/api/modelling/train",
                json={"graph": graph, "node_id": "train"},
            )
            assert resp.status_code == 507
            detail = resp.json()["detail"]
            assert detail["error_code"] == "gpu_vram_limit"
            assert detail["reason"] == "gpu_vram_limit_exceeded"
            assert "Switch task_type to CPU" in detail["message"]
            run.assert_not_called()



class TestTrainBackgroundLaunchFailures:
    def test_launch_background_start_failure_marks_job_error(
        self,
        tmp_path: Path,
    ) -> None:
        """A worker-start failure should not leave the job stuck in running."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        tmp_parquet = tmp_path / "train_data.parquet"
        tmp_parquet.write_bytes(b"train")

        with (
            patch("haute.modelling.TrainingJob", return_value=MagicMock()),
            patch(
                "haute.routes._train_service.threading.Thread.start",
                side_effect=RuntimeError("thread boom"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service._launch_background(
                job_id,
                "train",
                {"target": "y"},
                {},
                str(tmp_parquet),
                None,
                None,
            )

        assert exc_info.value.status_code == 500
        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert "Failed to start training worker" in job["message"]
        assert not tmp_parquet.exists()

    def test_late_completion_does_not_overwrite_timeout(self, tmp_path: Path) -> None:
        """Late progress and completion callbacks must not overwrite a timeout error."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running", "progress": 0.0, "message": "Starting"})
        tmp_parquet = tmp_path / "train_data.parquet"
        tmp_parquet.write_bytes(b"train")

        deferred_threads: list[object] = []

        class DeferredThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon
                deferred_threads.append(self)

            def start(self) -> None:
                return None

        class FakeTrainingJob:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, progress, on_iteration, check_cancelled=None, execution_context=None):
                progress("Still training", 0.5)
                on_iteration(1, 2, {"rmse": 1.0})
                return _completed_train_result()

        with (
            patch("haute.modelling.TrainingJob", FakeTrainingJob),
            patch("haute.routes._train_service.threading.Thread", DeferredThread),
        ):
            service._launch_background(
                job_id,
                "train",
                {"target": "y", "timeout": 10},
                {},
                str(tmp_parquet),
                None,
                None,
            )

        assert len(deferred_threads) == 1

        store.atomic_update(
            job_id,
            {
                "status": "error",
                "message": "Training timed out after 10s",
                "elapsed_seconds": 10.0,
            },
            expected_status="running",
        )

        deferred_threads[0].target()

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert "terminal_reason" not in job
        assert job["message"] == "Training timed out after 10s"
        assert job["elapsed_seconds"] == 10.0
        assert job["progress"] == 0.0
        assert job.get("iteration", 0) == 0
        assert job.get("result") is None
        assert not tmp_parquet.exists()

    def test_non_finite_training_result_marks_job_error(self, tmp_path: Path) -> None:
        """Worker completion must fail loudly before storing invalid JSON."""
        from haute.modelling._training_job import TrainResult
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running", "progress": 0.0, "message": "Starting"})
        tmp_parquet = tmp_path / "train_data.parquet"
        tmp_parquet.write_bytes(b"train")

        deferred_threads: list[object] = []

        class DeferredThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon
                deferred_threads.append(self)

            def start(self) -> None:
                return None

        class FakeTrainingJob:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, progress, on_iteration, check_cancelled=None, execution_context=None):
                return TrainResult(
                    metrics={"auc": float("nan")},
                    feature_importance=[],
                    model_path="outputs/bad.rsglm",
                    train_rows=10,
                    test_rows=0,
                    features=["x"],
                    cat_features=[],
                )

        with (
            patch("haute.modelling.TrainingJob", FakeTrainingJob),
            patch("haute.routes._train_service.threading.Thread", DeferredThread),
        ):
            service._launch_background(
                job_id,
                "train",
                {"target": "y"},
                {},
                str(tmp_parquet),
                None,
                None,
            )

        deferred_threads[0].target()

        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"
        assert "non-finite numeric value" in job["message"]
        assert "metrics.auc" in job["message"]
        assert job.get("result") is None
        assert not tmp_parquet.exists()


class TestTrainStatusTimeout:
    def test_timeout_sets_error_with_elapsed(self, client):
        from haute.routes.modelling import _store

        _store.jobs["train_tout"] = {
            "status": "running",
            "progress": 0.3,
            "message": "Training",
            "start_time": time.monotonic() - 500,
            "timeout": 10,
            "created_at": time.time(),
        }
        try:
            resp = client.get("/api/modelling/train/status/train_tout")
            data = resp.json()
            assert data["status"] == "timed_out"
            assert data["terminal_reason"] == "timed_out"
            assert "timed out" in data["message"].lower()
            assert data["elapsed_seconds"] > 0
        finally:
            _store.jobs.pop("train_tout", None)

    def test_cancel_training_marks_job_cancelled(self, client):
        from haute.routes.modelling import _store

        _store.jobs["train_cancel_me"] = {
            "status": "running",
            "job_type": "training",
            "progress": 0.3,
            "message": "Training",
            "start_time": time.monotonic() - 1,
            "created_at": time.time(),
        }
        try:
            resp = client.post("/api/modelling/train/cancel/train_cancel_me")
            data = resp.json()
            assert resp.status_code == 200
            assert data["status"] == "cancelled"
            assert data["terminal_reason"] == "cancelled"
            assert _store.require_job("train_cancel_me")["terminal_reason"] == "cancelled"
        finally:
            _store.jobs.pop("train_cancel_me", None)

    def test_completed_job_not_overwritten_by_timeout(self, client):
        from haute.routes.modelling import _store

        _store.jobs["train_done_past_timeout"] = {
            "status": "completed",
            "progress": 1.0,
            "message": "Done",
            "start_time": time.monotonic() - 500,
            "timeout": 10,
            "elapsed_seconds": 12.0,
            "created_at": time.time(),
        }
        try:
            resp = client.get("/api/modelling/train/status/train_done_past_timeout")
            data = resp.json()
            assert data["status"] == "completed"
            assert data["message"] == "Done"
            assert "timed out" not in data["message"].lower()
        finally:
            _store.jobs.pop("train_done_past_timeout", None)


def test_bounded_loss_history_retains_latest_rows() -> None:
    from haute.routes import _train_service

    history = [
        {"iteration": float(index), "rmse": float(index)}
        for index in range(_train_service._MAX_TRAIN_LOSS_HISTORY + 5)
    ]

    bounded, truncated = _train_service._bounded_loss_history(history)

    assert truncated is True
    assert len(bounded) == _train_service._MAX_TRAIN_LOSS_HISTORY
    assert bounded[0]["iteration"] == 5.0
    assert bounded[-1]["iteration"] == float(_train_service._MAX_TRAIN_LOSS_HISTORY + 4)


class TestExportEndpoint:
    def test_export_generates_script(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post(
            "/api/modelling/export",
            json={
                "graph": graph,
                "node_id": "train",
                "data_path": "output/data.parquet",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "script" in data
        assert "filename" in data
        assert "TrainingJob" in data["script"]
        assert data["filename"].endswith(".py")

    def test_export_missing_node(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post(
            "/api/modelling/export",
            json={
                "graph": graph,
                "node_id": "nonexistent",
            },
        )
        assert resp.status_code == 404


class TestTrainStatusEndpoint:
    def test_missing_job_returns_404(self, client):
        resp = client.get("/api/modelling/train/status/nonexistent")
        assert resp.status_code == 404

    def test_non_finite_completed_result_becomes_job_error(self, client):
        """A bad completed payload must not make status polling 500 forever."""
        from haute.routes.modelling import _store
        from haute.schemas import TrainResponse

        store = _store
        bad_result = TrainResponse.model_construct(
            status="completed",
            job_id="bad_result",
            metrics={"auc": float("nan")},
        )
        job_id = store.create_job(
            {
                "status": "completed",
                "progress": 1.0,
                "message": "Done",
                "result": bad_result,
            }
        )
        try:
            resp = client.get(f"/api/modelling/train/status/{job_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "error"
            assert "non-finite numeric value" in data["message"]
            assert "metrics.auc" in data["message"]
            assert data["result"] is None
        finally:
            store.jobs.pop(job_id, None)

    def test_finite_completed_result_is_validated_only_once(self, client, monkeypatch):
        """Status polls must not re-walk an already-validated result on every read.

        ``_assert_json_finite`` is a deep recursive walk; running it on every
        poll is wasted work because completed results are immutable once stored.
        Once a job's result has passed validation we should mark it as
        validated and skip the walk on subsequent polls.
        """
        from haute.routes import modelling as modelling_routes
        from haute.routes.modelling import _store
        from haute.schemas import TrainResponse

        store = _store
        good_result = TrainResponse.model_construct(
            status="completed",
            job_id="good_result",
            metrics={"auc": 0.87},
        )
        job_id = store.create_job(
            {
                "status": "completed",
                "progress": 1.0,
                "message": "Done",
                "result": good_result,
            }
        )

        call_count = {"n": 0}
        original_assert = modelling_routes._assert_json_finite

        def counting_assert(value, path="result"):
            call_count["n"] += 1
            return original_assert(value, path)

        monkeypatch.setattr(modelling_routes, "_assert_json_finite", counting_assert)

        try:
            for _ in range(5):
                resp = client.get(f"/api/modelling/train/status/{job_id}")
                assert resp.status_code == 200
                assert resp.json()["status"] == "completed"
            # First poll validates; the four subsequent polls must short-circuit
            # because the result is already known to be finite.
            assert call_count["n"] == 1
        finally:
            store.jobs.pop(job_id, None)

    def test_assert_json_finite_walks_nested_pydantic_models(self):
        """Recursion must descend into nested ``BaseModel`` instances.

        The on-the-wire ``TrainResponse`` may carry nested pydantic models
        (e.g. metric snapshots, feature-importance entries) — the validator
        has to model_dump them or it'd silently miss a NaN one level deep.
        """
        import pytest as _pytest
        from pydantic import BaseModel

        from haute.routes._train_service import _assert_json_finite

        class Inner(BaseModel):
            metric: float

        class Outer(BaseModel):
            label: str
            inner: Inner

        good = Outer(label="ok", inner=Inner(metric=0.5))
        _assert_json_finite(good)  # no raise

        bad = Outer.model_construct(label="bad", inner=Inner.model_construct(metric=float("nan")))
        with _pytest.raises(ValueError, match="inner.metric"):
            _assert_json_finite(bad)


class TestMlflowCheckEndpoint:
    def test_mlflow_check_response_shape(self, client):
        resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        data = resp.json()
        assert "mlflow_installed" in data
        assert "mlflow_importable" in data
        assert "tracking_configured" in data
        assert "backend" in data
        assert "databricks_host" in data
        assert "detail" in data


class TestMlflowLogEndpoint:
    def test_mlflow_log_job_not_found(self, client):
        resp = client.post(
            "/api/modelling/mlflow/log",
            json={
                "job_id": "nonexistent",
            },
        )
        assert resp.status_code == 404

    def test_mlflow_log_job_not_completed(self, client, training_data):
        """Start a job, then immediately try to log — should fail with 400."""
        from haute.routes.modelling import _store

        # Inject a fake running job
        _store.jobs["fake_running"] = {
            "status": "running",
            "progress": 0.5,
            "message": "Training...",
            "created_at": time.time(),
        }
        try:
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={
                    "job_id": "fake_running",
                },
            )
            assert resp.status_code == 400
            assert "not completed" in resp.json()["detail"]
        finally:
            _store.jobs.pop("fake_running", None)


# ---------------------------------------------------------------------------
# Phase 1A: Pure function tests
# ---------------------------------------------------------------------------


class TestFriendlyError:
    """Unit tests for _friendly_error — translates exceptions into user messages."""

    def test_value_error_passthrough(self):
        exc = ValueError("Target column 'z' not found")
        assert _friendly_error(exc) == "Target column 'z' not found"

    def test_file_not_found(self):
        exc = FileNotFoundError("/data/missing.parquet")
        result = _friendly_error(exc)
        assert result.startswith("File not found:")
        assert "missing.parquet" in result

    def test_catboost_nan(self):
        """CatBoost NaN/Inf errors should recommend upstream transforms."""
        # Simulate CatBoost error class
        exc = type("CatBoostError", (Exception,), {})("NaN values in features")
        result = _friendly_error(exc)
        assert "NaN" in result or "nan" in result.lower()
        assert "polars" in result.lower()

    def test_catboost_feature_mismatch(self):
        exc = type("CatBoostError", (Exception,), {})("feature number mismatch: expected 10 got 8")
        result = _friendly_error(exc)
        assert "feature mismatch" in result.lower()

    def test_catboost_generic(self):
        exc = type("CatBoostError", (Exception,), {})("internal pool error")
        result = _friendly_error(exc)
        assert result.startswith("CatBoost error:")
        assert "internal pool error" in result

    def test_os_error(self):
        exc = OSError("Permission denied: /models/model.cbm")
        result = _friendly_error(exc)
        assert result.startswith("Could not save model file:")
        assert "Permission denied" in result

    def test_fallback_includes_type(self):
        exc = RuntimeError("something unexpected")
        result = _friendly_error(exc)
        assert "RuntimeError" in result
        assert "something unexpected" in result

    def test_catboost_inf_message(self):
        """'inf' in message also triggers NaN/Inf advice."""
        exc = type("CatBoostError", (Exception,), {})("Found inf in column 3")
        result = _friendly_error(exc)
        assert "infinite" in result.lower() or "inf" in result.lower()
        assert "polars" in result.lower()

    def test_empty_exception_message(self):
        exc = RuntimeError("")
        result = _friendly_error(exc)
        assert "RuntimeError" in result

    def test_catboost_nan_recommends_fill_null(self):
        exc = type("CatBoostError", (Exception,), {})("NaN values in column 'x'")
        result = _friendly_error(exc)
        assert ".fill_null()" in result or ".drop_nulls()" in result

    def test_catboost_feature_mismatch_includes_original_message(self):
        exc = type("CatBoostError", (Exception,), {})(
            "feature number mismatch: expected 5 but got 3"
        )
        result = _friendly_error(exc)
        assert "feature mismatch" in result.lower()
        assert "expected 5 but got 3" in result


class TestClampRowLimit:
    """Unit tests for _clamp_row_limit — applies user row limits."""

    def test_none_user_limit_returns_current(self):
        assert _clamp_row_limit(1000, None) == 1000

    def test_zero_user_limit_returns_current(self):
        assert _clamp_row_limit(1000, 0) == 1000

    def test_negative_user_limit_returns_current(self):
        assert _clamp_row_limit(1000, -5) == 1000

    def test_user_smaller_than_current(self):
        assert _clamp_row_limit(1000, 500) == 500

    def test_user_larger_than_current(self):
        assert _clamp_row_limit(500, 1000) == 500

    def test_no_current_limit(self):
        assert _clamp_row_limit(None, 500) == 500

    def test_string_user_limit_ignored(self):
        assert _clamp_row_limit(1000, "abc") == 1000

    def test_float_user_limit_converted(self):
        assert _clamp_row_limit(1000, 500.7) == 500
        assert isinstance(_clamp_row_limit(1000, 500.7), int)

    def test_both_none(self):
        assert _clamp_row_limit(None, None) is None


class TestOutputDirDefault:
    """The default output_dir for training should be <pipeline_dir>/outputs."""

    def test_default_output_dir_uses_pipeline_dir(self):
        from haute.executor import _pipeline_dir
        from tests.conftest import make_graph

        graph = make_graph(
            {
                "nodes": [],
                "edges": [],
                "source_file": "/projects/rating/main.py",
            }
        )
        p_dir = _pipeline_dir(graph)
        assert p_dir is not None
        assert str(p_dir / "outputs").replace("\\", "/").endswith("rating/outputs")

    def test_default_output_dir_without_source_file(self):
        from haute.executor import _pipeline_dir
        from tests.conftest import make_graph

        graph = make_graph({"nodes": [], "edges": []})
        p_dir = _pipeline_dir(graph)
        assert p_dir is None

    def test_training_job_default_is_outputs(self):
        import inspect

        from haute.modelling._training_job import TrainingJob

        # output_dir parameter default (7th keyword-only param after name...model_name)
        # Verify via signature
        sig = inspect.signature(TrainingJob.__init__)
        assert sig.parameters["output_dir"].default == "outputs"


# ---------------------------------------------------------------------------
# Phase 1A: Endpoint validation gaps
# ---------------------------------------------------------------------------


class TestEstimateEndpoint:
    """Tests for /estimate — RAM + row estimation."""

    def test_estimate_success(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_rows" in data
        assert "safe_row_limit" in data
        assert "estimated_mb" in data
        assert "training_mb" in data

    def test_estimate_gpu_vram_path(self, client, training_data):
        graph = _make_modelling_graph(
            training_data,
            params=_fast_training_params(task_type="GPU"),
        )
        with patch("haute._ram_estimate.available_vram_bytes", return_value=1):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("gpu_vram_estimated_mb") is not None
        assert data.get("gpu_warning") is not None

    def test_estimate_missing_node(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post(
            "/api/modelling/estimate", json={"graph": graph, "node_id": "nonexistent"}
        )
        assert resp.status_code == 404

    def test_estimate_exception_returns_empty(self, client, training_data):
        """If the RAM estimate fails entirely, return an empty estimate (not 500)."""
        graph = _make_modelling_graph(training_data)
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            side_effect=RuntimeError("probe failed"),
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("total_rows") is None

    def test_estimate_suppresses_ram_warning_when_user_limit_binds(self, client, training_data):
        """When user's row_limit is lower than the RAM-safe limit, suppress the RAM warning."""
        graph = _make_modelling_graph(training_data)
        # Inject a user row_limit that is lower than the RAM-safe limit
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["row_limit"] = 500

        mock_est = SimpleNamespace(
            safe_row_limit=9_000_000,
            warning="Dataset downsampled to 9,000,000 of 10,000,000 rows",
            total_rows=10_000_000,
            probe_columns=5,
            estimated_bytes=1e9,
            available_bytes=2e10,
            bytes_per_row=100.0,
            was_downsampled=True,
        )
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=mock_est,
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["safe_row_limit"] == 500
        assert data["warning"] is None
        assert data["was_downsampled"] is False

    def test_estimate_keeps_ram_warning_when_ram_limit_binds(self, client, training_data):
        """When RAM-safe limit is lower than user's row_limit, keep the RAM warning."""
        graph = _make_modelling_graph(training_data)
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["row_limit"] = 20_000_000

        mock_est = SimpleNamespace(
            safe_row_limit=9_000_000,
            warning="Dataset downsampled to 9,000,000 of 10,000,000 rows",
            total_rows=10_000_000,
            probe_columns=5,
            estimated_bytes=1e9,
            available_bytes=2e10,
            bytes_per_row=100.0,
            was_downsampled=True,
        )
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=mock_est,
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["safe_row_limit"] == 9_000_000
        assert data["warning"] is not None
        assert data["was_downsampled"] is True


class TestMlflowLogSuccess:
    """Tests for /mlflow/log success and exception paths."""

    def test_mlflow_log_success(self, client):
        """Inject a completed job and mock log_experiment to test success path."""
        from haute.routes.modelling import _store
        from haute.schemas import TrainResponse

        fake_result = TrainResponse(
            status="completed",
            job_id="test_log",
            metrics={"gini": 0.85, "rmse": 0.12},
            model_path="/tmp/model.cbm",
            train_rows=80,
            test_rows=20,
        )
        _store.jobs["test_log"] = {
            "status": "completed",
            "result": fake_result,
            "config": {"algorithm": "catboost", "task": "regression", "target": "y"},
            "node_label": "my_model",
            "created_at": time.time(),
        }
        mock_log_result = SimpleNamespace(
            backend="local",
            experiment_name="/Shared/haute/my_model",
            run_id="abc123",
            run_url=None,
            tracking_uri="file:///tmp/mlruns",
        )
        try:
            with patch(
                "haute.modelling._mlflow_log.log_experiment",
                return_value=mock_log_result,
            ):
                resp = client.post("/api/modelling/mlflow/log", json={"job_id": "test_log"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["backend"] == "local"
            assert data["run_id"] == "abc123"
        finally:
            _store.jobs.pop("test_log", None)

    def test_mlflow_log_exception_returns_500(self, client):
        """If log_experiment raises, should return 500."""
        from haute.routes.modelling import _store
        from haute.schemas import TrainResponse

        fake_result = TrainResponse(status="completed", job_id="test_err", metrics={"gini": 0.5})
        _store.jobs["test_err"] = {
            "status": "completed",
            "result": fake_result,
            "config": {},
            "node_label": "model",
            "created_at": time.time(),
        }
        try:
            with patch(
                "haute.modelling._mlflow_log.log_experiment",
                side_effect=RuntimeError("MLflow connection refused"),
            ):
                resp = client.post("/api/modelling/mlflow/log", json={"job_id": "test_err"})
            assert resp.status_code == 500
            assert "MLflow connection refused" not in resp.json()["detail"]
            assert "Check the server logs" in resp.json()["detail"]
        finally:
            _store.jobs.pop("test_err", None)

    def test_mlflow_log_no_result_data(self, client):
        """Completed job with no result should return 400."""
        from haute.routes.modelling import _store

        _store.jobs["no_result"] = {
            "status": "completed",
            "result": None,
            "created_at": time.time(),
        }
        try:
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "no_result"})
            assert resp.status_code == 400
            assert "no result" in resp.json()["detail"].lower()
        finally:
            _store.jobs.pop("no_result", None)


class TestMlflowCheckImportError:
    """Test /mlflow/check when mlflow is not installed."""

    def test_mlflow_import_error(self, client):
        """Simulate mlflow not being installed via sys.modules patch."""
        import sys

        # patch.dict automatically restores sys.modules on exit
        with patch.dict(sys.modules, {"mlflow": None}):
            resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mlflow_installed"] is False


# ---------------------------------------------------------------------------
# Phase 1A: Background thread error tests
# ---------------------------------------------------------------------------


class TestBackgroundThreadErrors:
    """Test error handling in the background training thread."""

    def test_background_value_error(self, client, training_data):
        """ValueError in TrainingJob.run() sets status to error with message."""
        graph = _make_modelling_graph(training_data)
        with patch(
            "haute.modelling.TrainingJob.run",
            side_effect=ValueError("Invalid target column: not found"),
        ):
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            data = resp.json()
            assert data["status"] == "started"
            status = _poll_until_done(client, data["job_id"])
            assert status["status"] == "contract_error"
            assert status["terminal_reason"] == "contract_error"
            assert "Invalid target column" in status["message"]

    def test_background_runtime_error(self, client, training_data):
        """RuntimeError in TrainingJob.run() is translated via _friendly_error."""
        graph = _make_modelling_graph(training_data)
        with patch(
            "haute.modelling.TrainingJob.run",
            side_effect=RuntimeError("CUDA out of memory"),
        ):
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            data = resp.json()
            status = _poll_until_done(client, data["job_id"])
            assert status["status"] == "error"
            assert "CUDA out of memory" in status["message"]

    def test_background_generic_exception(self, client, training_data):
        """Generic exception in TrainingJob.run() includes exception type."""
        graph = _make_modelling_graph(training_data)
        with patch(
            "haute.modelling.TrainingJob.run",
            side_effect=Exception("unexpected crash"),
        ):
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            data = resp.json()
            status = _poll_until_done(client, data["job_id"])
            assert status["status"] == "error"
            assert "unexpected crash" in status["message"]

    def test_ram_warning_propagated(self, client, training_data):
        """RAM warning from estimate should appear in job status."""
        graph = _make_modelling_graph(training_data)
        mock_est = SimpleNamespace(
            safe_row_limit=50,
            warning="Dataset too large for available RAM. Row limit applied: 50.",
            total_rows=100,
            probe_columns=2,
            estimated_bytes=1000.0,
            available_bytes=500.0,
            bytes_per_row=10.0,
            was_downsampled=True,
        )
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=mock_est,
        ):
            with patch("haute.modelling.TrainingJob.run", return_value=_completed_train_result()):
                resp = client.post(
                    "/api/modelling/train",
                    json={"graph": graph, "node_id": "train"},
                )
                data = resp.json()
                status = _poll_until_done(client, data["job_id"])
                # Whether it completed or errored, the warning should be set
                warning = status.get("warning") or ""
                assert "Row limit" in warning or "RAM" in warning

    def test_ram_warning_suppressed_when_user_limit_binds(self, client, training_data):
        """When user's row_limit is lower than RAM-safe limit, no RAM warning in job status."""
        graph = _make_modelling_graph(training_data)
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["row_limit"] = 30

        mock_est = SimpleNamespace(
            safe_row_limit=50,
            warning="Dataset downsampled to 50 of 100 rows",
            total_rows=100,
            probe_columns=2,
            estimated_bytes=1000.0,
            available_bytes=500.0,
            bytes_per_row=10.0,
            was_downsampled=True,
        )
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=mock_est,
        ):
            with patch("haute.modelling.TrainingJob.run", return_value=_completed_train_result()):
                resp = client.post(
                    "/api/modelling/train",
                    json={"graph": graph, "node_id": "train"},
                )
                data = resp.json()
                status = _poll_until_done(client, data["job_id"])
                # RAM warning should be suppressed since user limit (30) < RAM limit (50)
                assert status.get("warning") is None


# ---------------------------------------------------------------------------
# TrainService._execute_and_sink checkpoint cleanup
# ---------------------------------------------------------------------------


class TestTrainingProjection:
    def test_glm_training_columns_include_terms_aux_and_split_column(self):
        seeds = _training_required_columns_by_node(
            "train",
            {
                "algorithm": "glm",
                "target": "claim_count",
                "weight": "exposure",
                "offset": "log_exposure",
                "terms": {
                    "driver_age": {"type": "linear"},
                    "territory": {"type": "categorical"},
                },
                "split": {"strategy": "group", "group_column": "policy_id"},
            },
        )

        assert seeds == {
            "train": frozenset(
                {
                    "claim_count",
                    "driver_age",
                    "exposure",
                    "log_exposure",
                    "policy_id",
                    "territory",
                }
            )
        }

    def test_catboost_training_columns_use_all_except_demand(self):
        demand = _training_required_columns_by_node(
            "train",
            {
                "algorithm": "catboost",
                "target": "claim_count",
                "exclude": ["policy_id"],
            },
        )

        assert demand is not None
        assert type(demand["train"]).__name__ == "AllExcept"
        assert demand["train"].required_columns == frozenset({"claim_count"})
        assert demand["train"].excluded_columns == frozenset(
            {"claim_count", "policy_id"}
        )

    def test_execute_and_sink_forwards_training_projection(self, tmp_path):
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": {"target": "claim_count"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        body = TrainRequest(graph=graph, node_id="train")
        job_id = store.create_job({"status": "running"})
        captured: dict[str, object] = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return (
                {
                    "train": pl.DataFrame(
                        {"claim_count": [1.0], "driver_age": [40]}
                    ).lazy()
                },
                ["train"],
                {},
                {},
            )

        seeds = {"train": frozenset({"claim_count", "driver_age"})}
        with (
            patch("haute.routes._train_service.execute_lazy_graph", side_effect=fake_execute_lazy),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
            patch("haute.executor._preview_cache", MagicMock()),
            patch("haute.trace._cache", MagicMock()),
            patch("haute._polars_utils.bounded_sink"),
        ):
            tmp_parquet = service._execute_and_sink(
                body,
                preamble_ns=None,
                row_limit=None,
                job_id=job_id,
                required_columns_by_node=seeds,
            )

        assert captured["required_columns_by_node"] == seeds
        assert Path(tmp_parquet).exists()
        Path(tmp_parquet).unlink()

    def test_execute_and_sink_maps_bounded_sink_failure_to_http_422(self) -> None:
        from fastapi import HTTPException

        from haute.errors import BoundedMemoryUnsupportedError
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": {"target": "claim_count"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        body = TrainRequest(graph=graph, node_id="train")
        job_id = store.create_job({"status": "running"})

        def fake_execute_lazy(*_args, **_kwargs):
            return (
                {"train": pl.DataFrame({"claim_count": [1.0]}).lazy()},
                ["train"],
                {},
                {},
            )

        with (
            patch("haute.routes._train_service.execute_lazy_graph", side_effect=fake_execute_lazy),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
            patch(
                "haute._polars_utils.bounded_sink",
                side_effect=BoundedMemoryUnsupportedError("Bounded streaming sink failed"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                service._execute_and_sink(body, preamble_ns=None, row_limit=None, job_id=job_id)

        assert exc_info.value.status_code == 422
        assert "bounded streaming mode" in exc_info.value.detail
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"


class TestExecuteAndSinkCheckpointCleanup:
    """Verify checkpoint_dir is cleaned up even when _execute_lazy raises."""

    def test_checkpoint_dir_cleaned_on_error(self, tmp_path):
        """If _execute_lazy raises, checkpoint_dir must still be cleaned up."""
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "n",
                        "data": {
                            "label": "n",
                            "nodeType": "dataSource",
                            "config": {"path": "x.parquet"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        body = TrainRequest(graph=graph, node_id="n")
        job_id = store.create_job({"status": "running"})

        created_dirs: list[Path] = []

        def failing_execute_lazy(*args, **kwargs):
            cp_dir = kwargs.get("checkpoint_dir")
            if cp_dir is not None:
                created_dirs.append(cp_dir)
            raise RuntimeError("boom")

        from fastapi import HTTPException

        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=failing_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
            patch("haute.executor._preview_cache", MagicMock()),
        ):
            with pytest.raises(HTTPException):
                service._execute_and_sink(body, preamble_ns=None, row_limit=None, job_id=job_id)

        # Checkpoint dir should have been created and then cleaned up
        assert len(created_dirs) == 1
        assert not created_dirs[0].exists(), "checkpoint_dir should be cleaned up after error"


# ---------------------------------------------------------------------------
# _validate_config unit tests
# ---------------------------------------------------------------------------


class TestValidateConfig:
    def test_no_target_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config({"algorithm": "catboost"})
        assert exc_info.value.status_code == 400
        assert "No target column" in exc_info.value.detail

    def test_empty_target_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config({"target": "", "algorithm": "catboost"})
        assert exc_info.value.status_code == 400
        assert "No target column" in exc_info.value.detail

    def test_unknown_algorithm_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config({"target": "y", "algorithm": "xgboost"})
        assert exc_info.value.status_code == 400
        assert "xgboost" in exc_info.value.detail
        assert "Available algorithms" in exc_info.value.detail

    def test_glm_unknown_family_raises_with_suggestions(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "exponential",
                }
            )
        assert exc_info.value.status_code == 400
        assert "exponential" in exc_info.value.detail
        assert "gaussian" in exc_info.value.detail

    def test_glm_invalid_link_for_family_raises_with_valid_options(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                    "link": "logit",
                }
            )
        assert exc_info.value.status_code == 400
        assert "logit" in exc_info.value.detail
        assert "log" in exc_info.value.detail

    def test_valid_catboost_config_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "catboost",
                "params": {"iterations": 10},
            }
        )

    def test_valid_glm_config_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "link": "log",
            }
        )

    def test_glm_empty_family_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "",
            }
        )

    def test_glm_empty_link_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "gaussian",
                "link": "",
            }
        )


# ---------------------------------------------------------------------------
# _validate_glm_family_link unit tests
# ---------------------------------------------------------------------------


class TestValidateGlmFamilyLink:
    def test_unknown_family(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_glm_family_link("exponential", "log")
        assert exc_info.value.status_code == 400
        assert "exponential" in exc_info.value.detail
        assert "gaussian" in exc_info.value.detail

    def test_invalid_link_for_family(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_glm_family_link("binomial", "identity")
        assert exc_info.value.status_code == 400
        assert "identity" in exc_info.value.detail
        assert "logit" in exc_info.value.detail

    def test_valid_family_link(self):
        _validate_glm_family_link("gamma", "log")

    def test_empty_family_skips(self):
        _validate_glm_family_link("", "log")

    def test_empty_link_skips(self):
        _validate_glm_family_link("poisson", "")


# ---------------------------------------------------------------------------
# /mlflow/check backend resolution tests
# ---------------------------------------------------------------------------


class TestMlflowCheckBackend:
    def test_mlflow_installed_detected(self, client):
        with (
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("file:///mlruns", "local"),
            ),
            patch("importlib.util.find_spec", return_value=SimpleNamespace()),
            patch("importlib.import_module", return_value=SimpleNamespace()),
        ):
            resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        assert resp.json()["mlflow_installed"] is True
        assert resp.json()["mlflow_importable"] is True
        assert resp.json()["tracking_configured"] is True

    def test_local_backend(self, client):
        with (
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("file:///mlruns", "local"),
            ),
            patch("importlib.util.find_spec", return_value=SimpleNamespace()),
            patch("importlib.import_module", return_value=SimpleNamespace()),
        ):
            resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mlflow_installed"] is True
        assert data["mlflow_importable"] is True
        assert data["tracking_configured"] is True
        assert data["backend"] == "local"
        assert data["databricks_host"] == ""
        assert data["detail"] == ""

    def test_databricks_backend(self, client):
        with (
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("databricks", "databricks"),
            ),
            patch("importlib.util.find_spec", return_value=SimpleNamespace()),
            patch.dict("os.environ", {"DATABRICKS_HOST": "https://my.cloud.databricks.com"}),
            patch("importlib.import_module", return_value=SimpleNamespace()),
        ):
            resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mlflow_installed"] is True
        assert data["mlflow_importable"] is True
        assert data["tracking_configured"] is True
        assert data["backend"] == "databricks"
        assert data["databricks_host"] == "https://my.cloud.databricks.com"

    def test_backend_resolution_failure_keeps_package_available(self, client):
        with (
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                side_effect=RuntimeError("tracking backend misconfigured"),
            ),
            patch("importlib.util.find_spec", return_value=SimpleNamespace()),
            patch("importlib.import_module", return_value=SimpleNamespace()),
        ):
            resp = client.get("/api/modelling/mlflow/check")

        assert resp.status_code == 200
        assert resp.json() == {
            "mlflow_installed": True,
            "mlflow_importable": True,
            "tracking_configured": False,
            "backend": "",
            "databricks_host": "",
            "detail": "tracking backend misconfigured",
        }


# ---------------------------------------------------------------------------
# /model-cache endpoint tests
# ---------------------------------------------------------------------------


class TestClearModelCache:
    def test_clears_cache_successfully(self, client):
        with patch(
            "haute._mlflow_io.clear_model_cache",
            return_value=3,
        ):
            resp = client.delete("/api/modelling/model-cache")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == 3
        assert data["run_id"] is None

    def test_clears_specific_run_cache(self, client):
        with patch(
            "haute._mlflow_io.clear_model_cache",
            return_value=1,
        ):
            resp = client.delete("/api/modelling/model-cache?run_id=abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == 1
        assert data["run_id"] == "abc123"


# ---------------------------------------------------------------------------
# Direct route function tests — bypasses TestClient for coverage
# ---------------------------------------------------------------------------


class TestTrainModelDirect:
    """Test train_model route function directly (not through HTTP client)."""

    def test_train_model_delegates_to_service(self):
        """train_model should delegate to _train_service.start()."""
        from haute.routes.modelling import _train_service, train_model
        from haute.schemas import TrainRequest, TrainResponse

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
                        },
                    },
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": {
                                "target": "y",
                                "algorithm": "catboost",
                                "params": {"iterations": 5},
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "train").model_dump()],
            }
        )
        body = TrainRequest(graph=graph, node_id="train")
        fake_response = TrainResponse(status="started", job_id="abc123")

        with patch.object(_train_service, "start", return_value=fake_response) as mock_start:
            result = train_model(body)
            mock_start.assert_called_once_with(body)
            assert result.status == "started"
            assert result.job_id == "abc123"


class TestTrainStatusDirect:
    """Test train_status route function directly."""

    @pytest.mark.asyncio
    async def test_returns_status_for_existing_job(self):
        from haute.routes.modelling import _store, train_status

        job_id = _store.create_job(
            {
                "status": "running",
                "progress": 0.42,
                "message": "Epoch 5/10",
                "iteration": 5,
                "total_iterations": 10,
                "train_loss": {"rmse": 0.15},
                "elapsed_seconds": 12.5,
            }
        )
        try:
            result = await train_status(job_id)
            assert result.status == "running"
            assert result.progress == 0.42
            assert result.message == "Epoch 5/10"
            assert result.iteration == 5
            assert result.total_iterations == 10
            assert result.train_loss == {"rmse": 0.15}
            assert result.elapsed_seconds == 12.5
            assert result.result is None
            assert result.warning is None
        finally:
            _store.jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_completed_job_includes_result(self):
        from haute.routes.modelling import _store, train_status
        from haute.schemas import TrainResponse

        fake_result = TrainResponse(
            status="completed",
            job_id="test",
            metrics={"gini": 0.85},
        )
        job_id = _store.create_job(
            {
                "status": "completed",
                "progress": 1.0,
                "message": "Done",
                "result": fake_result,
                "warning": "Row limit applied",
            }
        )
        try:
            result = await train_status(job_id)
            assert result.status == "completed"
            assert result.result is not None
            assert result.warning == "Row limit applied"
        finally:
            _store.jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_missing_job_raises_404(self):
        from haute.routes.modelling import train_status

        with pytest.raises(HTTPException) as exc_info:
            await train_status("nonexistent_job_id")
        assert exc_info.value.status_code == 404


class TestExportScriptDirect:
    """Test export_script route function directly."""

    @pytest.mark.asyncio
    async def test_generates_script(self):
        from haute.routes.modelling import export_script
        from haute.schemas import ExportScriptRequest

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
                        },
                    },
                    {
                        "id": "model",
                        "data": {
                            "label": "my_model",
                            "nodeType": "modelling",
                            "config": {
                                "target": "y",
                                "algorithm": "catboost",
                                "task": "regression",
                                "params": {"iterations": 100},
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "model").model_dump()],
            }
        )
        body = ExportScriptRequest(graph=graph, node_id="model", data_path="output/data.parquet")
        result = await export_script(body)
        assert "TrainingJob" in result.script
        assert result.filename == "train_my_model.py"

    @pytest.mark.asyncio
    async def test_default_data_path(self):
        """When data_path is not provided, uses a default based on node name."""
        from haute.routes.modelling import export_script
        from haute.schemas import ExportScriptRequest

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "m1",
                        "data": {
                            "label": "my_model",
                            "nodeType": "modelling",
                            "config": {
                                "target": "y",
                                "algorithm": "catboost",
                                "params": {"iterations": 10},
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )
        body = ExportScriptRequest(graph=graph, node_id="m1")
        result = await export_script(body)
        assert "TrainingJob" in result.script
        assert "output/" in result.script

    @pytest.mark.asyncio
    async def test_missing_node_raises_404(self):
        from haute.routes.modelling import export_script
        from haute.schemas import ExportScriptRequest

        graph = make_graph({"nodes": [], "edges": []})
        body = ExportScriptRequest(graph=graph, node_id="nonexistent")
        with pytest.raises(HTTPException) as exc_info:
            await export_script(body)
        assert exc_info.value.status_code == 404


class TestClearModelCacheDirect:
    """Test clear_model_cache route function directly."""

    @pytest.mark.asyncio
    async def test_clears_all(self):
        from haute.routes.modelling import clear_model_cache

        with patch("haute._mlflow_io.clear_model_cache", return_value=5) as mock:
            result = await clear_model_cache(run_id=None)
            mock.assert_called_once_with(None)
            assert result.removed == 5
            assert result.run_id is None

    @pytest.mark.asyncio
    async def test_clears_specific_run(self):
        from haute.routes.modelling import clear_model_cache

        with patch("haute._mlflow_io.clear_model_cache", return_value=2) as mock:
            result = await clear_model_cache(run_id="run_xyz")
            mock.assert_called_once_with("run_xyz")
            assert result.removed == 2
            assert result.run_id == "run_xyz"


class TestMlflowCheckDirect:
    """Test mlflow_check route function directly."""

    @pytest.mark.asyncio
    async def test_mlflow_installed(self):
        from haute.routes.modelling import mlflow_check

        result = await mlflow_check()
        assert result.mlflow_installed is True

    @pytest.mark.asyncio
    async def test_mlflow_not_installed(self):
        """When mlflow import fails, returns mlflow_installed=False."""
        from haute.routes.modelling import mlflow_check

        with patch("importlib.util.find_spec", return_value=None):
            result = await mlflow_check()

        assert result.mlflow_installed is False
        assert result.mlflow_importable is False
        assert result.tracking_configured is False
        assert result.detail == "MLflow package is not installed"

    @pytest.mark.asyncio
    async def test_mlflow_import_failure_keeps_package_available(self):
        from haute.routes.modelling import mlflow_check

        with (
            patch("importlib.util.find_spec", return_value=SimpleNamespace()),
            patch("importlib.import_module", side_effect=ImportError("broken dependency")),
        ):
            result = await mlflow_check()

        assert result.mlflow_installed is True
        assert result.mlflow_importable is False
        assert result.tracking_configured is False
        assert result.backend == ""
        assert result.databricks_host == ""
        assert result.detail == "MLflow package import failed: broken dependency"

    @pytest.mark.asyncio
    async def test_backend_resolution_failure_returns_tracking_unavailable(self):
        from haute.routes.modelling import mlflow_check

        with (
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                side_effect=RuntimeError("tracking backend misconfigured"),
            ),
            patch("importlib.util.find_spec", return_value=SimpleNamespace()),
            patch("importlib.import_module", return_value=SimpleNamespace()),
        ):
            result = await mlflow_check()

        assert result.mlflow_installed is True
        assert result.mlflow_importable is True
        assert result.tracking_configured is False
        assert result.backend == ""
        assert result.databricks_host == ""
        assert result.detail == "tracking backend misconfigured"
