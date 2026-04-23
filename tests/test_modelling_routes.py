"""API integration tests for modelling endpoints."""

from __future__ import annotations

import time
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
    _friendly_error,
    _validate_glm_family_link,
)
from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


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
        "params": params or {"iterations": 10, "depth": 3},
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


@pytest.fixture()
def training_data(tmp_path) -> str:
    """Create a small parquet file for training tests."""
    rng = np.random.RandomState(42)
    n = 100
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


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    """Poll /train/status/{job_id} until completed or error, return final status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/modelling/train/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "error"):
            return data
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


class TestTrainEndpoint:
    def test_train_with_invalid_target(self, client, training_data):
        graph = _make_modelling_graph(training_data, target="nonexistent")
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["job_id"]
        # Poll until done — should fail with a clear message
        status = _poll_until_done(client, data["job_id"])
        assert status["status"] == "error"
        assert "nonexistent" in status["message"].lower() or "target" in status["message"].lower()

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
        # Poll until done
        status = _poll_until_done(client, data["job_id"])
        assert status["status"] == "completed"
        result = status["result"]
        assert result["metrics"]
        assert result["train_rows"] > 0
        assert result["test_rows"] > 0

    def test_train_reports_progress(self, client, training_data):
        """Training should report iteration progress via the status endpoint."""
        graph = _make_modelling_graph(training_data, params={"iterations": 20, "depth": 3})
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
            time.sleep(0.05)
        assert status["status"] == "completed"
        # With 20 iterations, we should have seen at least one iteration update
        # (though fast training might complete before we poll — check final state too)
        assert saw_iteration or status.get("result", {}).get("train_rows", 0) > 0

    def test_train_result_includes_ave(self, client, training_data):
        """After successful training, ave_per_feature should be in the result."""
        graph = _make_modelling_graph(training_data)
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        data = resp.json()
        status = _poll_until_done(client, data["job_id"])
        assert status["status"] == "completed"
        result = status["result"]
        # Should have ave_per_feature for the 2 features (x1, x2)
        assert "ave_per_feature" in result
        assert isinstance(result["ave_per_feature"], list)
        assert len(result["ave_per_feature"]) == 2
        for entry in result["ave_per_feature"]:
            assert "feature" in entry
            assert "type" in entry
            assert "bins" in entry

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

    def test_train_gpu_falls_back_to_cpu_on_vram_limit(self, client, training_data):
        """When GPU VRAM is insufficient, training should fall back to CPU."""
        graph = _make_modelling_graph(
            training_data,
            params={"iterations": 10, "depth": 3, "task_type": "GPU"},
        )
        # Pretend GPU has only 1 byte VRAM — forces fallback to CPU
        # (even 100 rows × 3 features needs more than 1 byte)
        with patch(
            "haute._ram_estimate.available_vram_bytes",
            return_value=1,
        ):
            resp = client.post(
                "/api/modelling/train",
                json={"graph": graph, "node_id": "train"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"

        # Poll until done — should succeed on CPU (not crash on GPU OOM)
        status = _poll_until_done(client, data["job_id"])
        assert status["status"] == "completed"
        # The warning should mention GPU fallback
        warning = status.get("warning") or ""
        assert "GPU" in warning or "VRAM" in warning or "CPU" in warning


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


class TestMlflowCheckEndpoint:
    def test_mlflow_check_response_shape(self, client):
        resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        data = resp.json()
        assert "mlflow_installed" in data
        assert "backend" in data
        assert "databricks_host" in data
        # mlflow is installed in test env
        assert data["mlflow_installed"] is True


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
            params={"iterations": 10, "depth": 3, "task_type": "GPU"},
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
            assert status["status"] == "error"
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
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
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
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            data = resp.json()
            status = _poll_until_done(client, data["job_id"])
            # RAM warning should be suppressed since user limit (30) < RAM limit (50)
            assert status.get("warning") is None


# ---------------------------------------------------------------------------
# TrainService._execute_and_sink checkpoint cleanup
# ---------------------------------------------------------------------------


class TestExecuteAndSinkCheckpointCleanup:
    """Verify checkpoint_dir is cleaned up even when _execute_lazy raises."""

    def test_checkpoint_dir_cleaned_on_error(self, tmp_path):
        """If _execute_lazy raises, checkpoint_dir must still be cleaned up."""
        from pathlib import Path

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
            patch("haute._execute_lazy._execute_lazy", side_effect=failing_execute_lazy),
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
        resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        assert resp.json()["mlflow_installed"] is True

    def test_local_backend(self, client):
        with patch(
            "haute.modelling._mlflow_log.resolve_tracking_backend",
            return_value=("file:///mlruns", "local"),
        ):
            resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "local"
        assert data["databricks_host"] == ""

    def test_databricks_backend(self, client):
        with (
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("databricks", "databricks"),
            ),
            patch.dict("os.environ", {"DATABRICKS_HOST": "https://my.cloud.databricks.com"}),
        ):
            resp = client.get("/api/modelling/mlflow/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "databricks"
        assert data["databricks_host"] == "https://my.cloud.databricks.com"


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
        import builtins
        import sys

        from haute.routes.modelling import mlflow_check

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "mlflow":
                raise ImportError("No module named 'mlflow'")
            return real_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {"mlflow": None}):
            with patch("builtins.__import__", side_effect=mock_import):
                result = await mlflow_check()
                assert result.mlflow_installed is False
