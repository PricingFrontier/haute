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

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute.routes._train_service import (
    TrainService,
    _clamp_row_limit,
    _declared_categorical_levels_for_training,
    _friendly_error,
    _training_required_columns_by_node,
    _validate_glm_family_link,
)
from tests.conftest import (
    make_edge,
    make_file_input_config,
    make_graph,
    make_ready_file_input_config,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _admitted_training_context_for_launch(job_id: str | None = None) -> ExecutionContext:
    """Build an admitted-like context for direct ``_launch_background`` calls."""
    return ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id=job_id,
        memory_limit_bytes=1_000,
        memory_baseline_bytes=500,
        rss_limit_bytes=1_500,
        memory_sampler=lambda: 600,
        admission_release=lambda: None,
    )


if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _fast_training_params(**overrides: object) -> dict[str, object]:
    """Cheap-but-real CatBoost settings for endpoint tests."""
    params: dict[str, object] = {"iterations": 4, "depth": 2}
    params.update(overrides)
    return params


def _random_evaluation_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "strategy": "random",
        "seed": 42,
        "validation": {"method": "single", "size": 0.2},
    }


def _completed_train_result() -> object:
    """Small successful TrainResult for endpoint tests that do not care about fit quality."""
    from haute.modelling._training_job import TrainResult

    return TrainResult(
        metrics={"rmse": 0.1, "gini": 0.5},
        feature_importance=[],
        model_path="outputs/test_model.cbm",
        train_rows=48,
        validation_rows=12,
        features=["x1", "x2"],
        cat_features=[],
    )


def _evaluation_response_payload() -> dict[str, object]:
    selection_fits = [
        {
            "schema_version": 1,
            "fit_index": 0,
            "train_rows": 8,
            "validation_rows": 2,
            "metrics": {"rmse": 1.0},
        },
        {
            "schema_version": 1,
            "fit_index": 1,
            "train_rows": 8,
            "validation_rows": 2,
            "metrics": {"rmse": 3.0},
        },
    ]
    return {
        "schema_version": 1,
        "strategy": "random",
        "validation_method": "cross_validation",
        "validation_fit_count": 2,
        "fit_count": 3,
        "development_rows": 10,
        "final_test_rows": 2,
        "selection_fits": selection_fits,
        "selection_metrics": {
            "rmse": {
                "mean": 2.0,
                "stddev": 1.0,
                "min": 1.0,
                "max": 3.0,
                "fit_count": 2,
                "validation_rows": 4,
            }
        },
        "plan_sha256": "a" * 64,
        "results_sha256": "b" * 64,
        "plan_path": "outputs/model.evaluation-plan.json",
        "results_path": "outputs/model.evaluation-results.json",
        "report_path": "outputs/model.evaluation-report.json",
        "summary": {
            "development_rows": 10,
            "test_rows": 2,
            "validation_fit_count": 2,
        },
    }


def _completed_train_response(**overrides: object):
    from haute.schemas import TrainResponse

    values: dict[str, object] = {
        "status": "completed",
        "job_id": "test",
        "diagnostic_metrics": {"rmse": 0.12},
        "final_test_metrics": {"rmse": 0.12},
        "development_rows": 10,
        "final_test_rows": 2,
        "diagnostics_set": "final_test",
        "evaluation": _evaluation_response_payload(),
    }
    values.update(overrides)
    return TrainResponse(**values)


class TestEvaluationResponseContract:
    def test_completed_response_accepts_bounded_report(self) -> None:
        from haute.schemas import TrainResponse

        response = TrainResponse(
            status="completed",
            diagnostic_metrics={"rmse": 0.12},
            final_test_metrics={"rmse": 0.12},
            development_rows=10,
            final_test_rows=2,
            diagnostics_set="final_test",
            evaluation=_evaluation_response_payload(),
        )

        assert response.evaluation is not None
        assert response.evaluation.fit_count == 3
        assert [fit.fit_index for fit in response.evaluation.selection_fits] == [0, 1]

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (
                lambda payload: payload.update(validation_fit_count=11),
                "less than or equal to 10",
            ),
            (
                lambda payload: payload["selection_fits"].reverse(),
                "ascending",
            ),
            (
                lambda payload: payload["selection_metrics"]["rmse"].update(validation_rows=5),
                "validation_rows",
            ),
            (
                lambda payload: payload["selection_fits"][0]["metrics"].update(mae=1.0),
                "metric names",
            ),
            (
                lambda payload: payload.update(plan_sha256="not-a-digest"),
                "plan_sha256",
            ),
        ],
    )
    def test_completed_response_rejects_malformed_report(self, mutate, message: str) -> None:
        from haute.schemas import TrainResponse

        payload = _evaluation_response_payload()
        mutate(payload)

        with pytest.raises(ValueError, match=message):
            TrainResponse(
                status="completed",
                diagnostic_metrics={"rmse": 0.12},
                final_test_metrics={"rmse": 0.12},
                development_rows=10,
                final_test_rows=2,
                diagnostics_set="final_test",
                evaluation=payload,
            )


def _inline_route_service(monkeypatch: pytest.MonkeyPatch):
    """Install an inline-protocol route service and retain launched supervisors."""
    from haute.routes.modelling import _store
    from tests.test_training_worker_protocol import _inline_protocol_runner

    service = TrainService(_store, protocol_runner=_inline_protocol_runner)
    launched = []
    launch_protocol = service._supervisor.launch_protocol

    def capture_launch(*args, **kwargs):
        thread = launch_protocol(*args, **kwargs)
        launched.append(thread)
        return thread

    monkeypatch.setattr(service._supervisor, "launch_protocol", capture_launch)
    monkeypatch.setattr("haute.routes.modelling._train_service", service)
    return service, launched


class TestTrainingCategoricalLevelDeclarations:
    def test_collects_source_declared_levels_through_transforms(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataInput",
                            "config": make_file_input_config(
                                "quotes.csv",
                                categorical_levels={"region": ["north", "south"]},
                            ),
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
    evaluation: dict | None = None,
) -> dict:
    """Build a simple 2-node graph: dataInput → modelling."""
    config: dict = {
        "target": target,
        "algorithm": algorithm,
        "task": task,
        "params": params or _fast_training_params(),
        "evaluation": evaluation
        or {
            "schema_version": 1,
            "strategy": "random",
            "seed": 42,
            "validation": {"method": "single", "size": 0.2},
        },
        "metrics": ["gini", "rmse"] if task == "regression" else ["auc", "logloss"],
    }
    if algorithm == "catboost":
        config["loss_function"] = "RMSE" if task == "regression" else "Logloss"
    if weight:
        config["weight"] = weight

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(data_path),
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
    def test_training_job_store_test_isolation_clears_running_jobs(self):
        """The shared route store must not carry one test's running job into the next."""
        from haute.routes.modelling import _store
        from tests.conftest import _clear_training_route_job_store_for_tests

        job_id = _store.create_job({"status": "running"})

        assert _store.has_job_with_status("running")

        _clear_training_route_job_store_for_tests()

        assert not _store.has_job_with_status("running")
        assert job_id not in _store._running_activity_at

    def test_training_job_store_test_isolation_clears_cached_factory_store(self):
        """Direct factory access should be cleaned even when route cleanup is not enough."""
        from haute.routes._job_store import get_job_store
        from tests.conftest import _clear_training_route_job_store_for_tests

        store = get_job_store("training")
        job_id = store.create_job({"status": "running"})

        _clear_training_route_job_store_for_tests()

        assert not store.has_job_with_status("running")
        assert job_id not in store._running_activity_at

    def test_job_store_cleanup_clears_orphaned_running_activity(self):
        """Cleanup should repair tests that mutated ``jobs`` directly."""
        from haute.routes._job_store import get_job_store
        from tests.conftest import _clear_job_store_jobs

        store = get_job_store("training")
        job_id = store.create_job({"status": "running"})
        store.jobs.pop(job_id)

        assert job_id in store._running_activity_at

        _clear_job_store_jobs(store)

        assert job_id not in store._running_activity_at

    def test_train_with_invalid_target(self, client, training_data):
        graph = _make_modelling_graph(training_data, target="nonexistent")
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        status = _poll_until_done(client, resp.json()["job_id"])
        assert status["status"] == "contract_error"
        assert status["http_status_code"] == 422
        assert "nonexistent" in status["message"]

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
        assert result["diagnostic_metrics"]
        assert result["final_test_metrics"] == {}
        assert result["development_rows"] > 0
        assert result["final_test_rows"] == 0
        assert result["evaluation"]["validation_fit_count"] == 1
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
        # Pretend GPU has only 1 byte VRAM -- forces async refusal before fit.
        with (
            patch("haute._host_memory.available_vram_bytes", return_value=1),
            patch("haute.modelling.TrainingJob.run", return_value=_completed_train_result()) as run,
        ):
            resp = client.post(
                "/api/modelling/train",
                json={"graph": graph, "node_id": "train"},
            )
            assert resp.status_code == 200
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "memory_limited"
            assert status["http_status_code"] == 507
            assert status["error_code"] == "gpu_vram_limit"
            detail = status["error_detail"]
            assert detail["reason"] == "gpu_vram_limit_exceeded"
            assert "Select CPU and retry" in detail["message"]
            run.assert_not_called()


class TestTrainBackgroundLaunchFailures:
    def test_launch_background_start_failure_marks_job_error(
        self,
        tmp_path: Path,
    ) -> None:
        """A worker-start failure should not leave the job stuck in running."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        from tests.test_training_worker_protocol import _inline_protocol_runner

        service = TrainService(store, protocol_runner=_inline_protocol_runner)
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "training",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )
        tmp_parquet = tmp_path / "train_data.parquet"
        tmp_parquet.write_bytes(b"train")

        with (
            patch("haute.modelling.TrainingJob", return_value=MagicMock()),
            patch(
                "haute.routes._background_jobs.IsolatedSupervisorThread.start",
                side_effect=RuntimeError("thread boom"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service._launch_background(
                job_id,
                "train",
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "task": "regression",
                    "loss_function": "RMSE",
                    "evaluation": _random_evaluation_config(),
                },
                {},
                str(tmp_parquet),
                None,
                None,
                execution_context=_admitted_training_context_for_launch(job_id),
            )

        assert exc_info.value.status_code == 500
        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert "Failed to start isolated supervisor" in job["message"]
        assert not tmp_parquet.exists()

    def test_non_finite_training_result_marks_job_error(self, tmp_path: Path) -> None:
        """Worker completion must fail loudly before storing invalid JSON."""
        from haute.modelling._training_job import TrainResult
        from haute.routes._job_store import JobStore

        store = JobStore()
        from tests.test_training_worker_protocol import _inline_protocol_runner

        service = TrainService(store, protocol_runner=_inline_protocol_runner)
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "training",
                "progress": 0.0,
                "message": "Starting",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )
        tmp_parquet = tmp_path / "train_data.parquet"
        tmp_parquet.write_bytes(b"train")

        class FakeTrainingJob:
            def __init__(self, *args, **kwargs):
                from haute.modelling._training_job import model_contract_filename

                self.output_dir = Path(kwargs["output_dir"])
                self.name = str(kwargs.get("name", "model"))
                self.model_contract_filename = model_contract_filename

            def run(
                self,
                progress,
                on_iteration,
                check_cancelled=None,
                execution_context=None,
                on_tuning_progress=None,
            ):
                self.output_dir.mkdir(parents=True, exist_ok=True)
                model_path = self.output_dir / f"{self.name}.cbm"
                model_path.write_bytes(b"model")
                (self.output_dir / self.model_contract_filename(self.name)).write_text(
                    '{"schema_version": 1}', encoding="utf-8"
                )
                evaluation = _evaluation_response_payload()
                for field, filename in {
                    "plan_path": f"{self.name}.evaluation-plan.json",
                    "results_path": f"{self.name}.evaluation-results.json",
                    "report_path": f"{self.name}.evaluation-report.json",
                }.items():
                    artifact_path = self.output_dir / filename
                    artifact_path.write_text("{}", encoding="utf-8")
                    evaluation[field] = str(artifact_path)
                return TrainResult(
                    metrics={"rmse": 0.1},
                    feature_importance=[],
                    model_path=str(model_path),
                    train_rows=8,
                    validation_rows=2,
                    features=["x"],
                    cat_features=[],
                    diagnostics_set="final_test",
                    development_rows=10,
                    final_test_rows=2,
                    final_test_metrics={"auc": float("nan")},
                    evaluation=evaluation,
                )

        with patch("haute.modelling.TrainingJob", FakeTrainingJob):
            thread = service._launch_background(
                job_id,
                "train",
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "task": "regression",
                    "loss_function": "RMSE",
                    "evaluation": _random_evaluation_config(),
                },
                {},
                str(tmp_parquet),
                None,
                None,
                execution_context=_admitted_training_context_for_launch(job_id),
            )
            assert thread is not None
            thread.join_and_raise(timeout=10)

        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["terminal_reason"] == "contract_error"
        assert "must be a finite number" in job["message"]
        assert "final_test_metrics.auc" in job["message"]
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
        for index in range(_train_service._max_train_loss_history() + 5)
    ]

    bounded, truncated = _train_service._bounded_loss_history(history)

    assert truncated is True
    assert len(bounded) == _train_service._max_train_loss_history()
    assert bounded[0]["iteration"] == 5.0
    assert bounded[-1]["iteration"] == float(_train_service._max_train_loss_history() + 4)


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

    def test_export_missing_target_returns_sanitized_400(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        graph["nodes"][1]["data"]["config"].pop("target")
        resp = client.post(
            "/api/modelling/export",
            json={
                "graph": graph,
                "node_id": "train",
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "target column" in detail
        assert "config panel" in detail
        assert "Traceback" not in detail
        assert "ValueError" not in detail


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
            diagnostic_metrics={"auc": float("nan")},
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
            assert "diagnostic_metrics.auc" in data["message"]
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

        store = _store
        good_result = _completed_train_response(
            job_id="good_result",
            diagnostic_metrics={"auc": 0.87},
            final_test_metrics={"auc": 0.87},
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
        with pytest.raises(ValueError, match="inner.metric"):
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

    def test_estimate_includes_exact_bounded_evaluation_preview(
        self,
        client,
        training_data,
    ):
        graph = _make_modelling_graph(
            training_data,
            evaluation={
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "test": {"size": 0.2},
                "validation": {"method": "single", "size": 0.2},
            },
        )

        resp = client.post(
            "/api/modelling/estimate",
            json={"graph": graph, "node_id": "train"},
        )

        assert resp.status_code == 200
        assert resp.json()["evaluation_preview"] == {
            "schema_version": 1,
            "strategy": "random",
            "validation_method": "single",
            "development_rows": 48,
            "final_test_rows": 12,
            "validation_fit_count": 1,
            "min_selection_train_rows": 36,
            "max_selection_train_rows": 36,
            "min_selection_validation_rows": 12,
            "max_selection_validation_rows": 12,
        }

    def test_estimate_preview_includes_group_counts(
        self,
        client,
        tmp_path,
    ):
        path = tmp_path / "group-preview.parquet"
        pl.DataFrame(
            {
                "entity": ["a", "a", "b", "b", "c", "c", "d", "d"],
                "x": list(range(8)),
                "y": [float(value) for value in range(8)],
            }
        ).write_parquet(path)
        graph = _make_modelling_graph(
            str(path),
            evaluation={
                "schema_version": 1,
                "strategy": "group",
                "group_column": "entity",
                "seed": 42,
                "test": {"size": 0.25},
                "validation": {
                    "method": "cross_validation",
                    "fold_count": 2,
                },
            },
        )

        resp = client.post(
            "/api/modelling/estimate",
            json={"graph": graph, "node_id": "train"},
        )

        assert resp.status_code == 200
        preview = resp.json()["evaluation_preview"]
        assert preview["development_rows"] == 6
        assert preview["final_test_rows"] == 2
        assert preview["development_group_count"] == 3
        assert preview["final_test_group_count"] == 1
        assert preview["min_selection_validation_rows"] == 2
        assert preview["max_selection_validation_rows"] == 4

    def test_estimate_preview_includes_temporal_date_ranges(
        self,
        client,
        tmp_path,
    ):
        path = tmp_path / "temporal-preview.parquet"
        pl.DataFrame(
            {
                "month": pl.date_range(
                    pl.date(2024, 1, 1),
                    pl.date(2024, 6, 1),
                    interval="1mo",
                    eager=True,
                ),
                "x": list(range(6)),
                "y": [float(value) for value in range(6)],
            }
        ).write_parquet(path)
        graph = _make_modelling_graph(
            str(path),
            evaluation={
                "schema_version": 1,
                "strategy": "temporal",
                "date_column": "month",
                "test": {"start": "2024-05-01"},
                "validation": {
                    "method": "single",
                    "start": "2024-03-01",
                },
            },
        )

        resp = client.post(
            "/api/modelling/estimate",
            json={"graph": graph, "node_id": "train"},
        )

        assert resp.status_code == 200
        preview = resp.json()["evaluation_preview"]
        assert preview["development_date_range"] == {
            "start": "2024-01-01",
            "end": "2024-04-01",
        }
        assert preview["final_test_date_range"] == {
            "start": "2024-05-01",
            "end": "2024-06-01",
        }

    def test_estimate_gpu_vram_path(self, client, training_data):
        graph = _make_modelling_graph(
            training_data,
            params=_fast_training_params(task_type="GPU"),
        )
        with patch("haute._host_memory.available_vram_bytes", return_value=1):
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

        fake_result = _completed_train_response(
            job_id="test_log",
            diagnostic_metrics={"gini": 0.85, "rmse": 0.12},
            final_test_metrics={"gini": 0.85, "rmse": 0.12},
            model_path="/tmp/model.cbm",
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

        fake_result = _completed_train_response(
            job_id="test_err",
            diagnostic_metrics={"gini": 0.5},
            final_test_metrics={"gini": 0.5},
        )
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

    def test_background_value_error(self, client, training_data, monkeypatch):
        """ValueError in TrainingJob.run() sets status to error with message."""
        graph = _make_modelling_graph(training_data)

        class FailingJob:
            def __init__(self, **_kwargs):
                pass

            def run(self, *_args, **_kwargs):
                raise ValueError("Invalid target column: not found")

        service, launched = _inline_route_service(monkeypatch)
        with patch("haute.modelling.TrainingJob", FailingJob):
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            data = resp.json()
            assert data["status"] == "started"
            service._join_preparation(data["job_id"])
            launched[0].join_and_raise(timeout=10)
            status = client.get(f"/api/modelling/train/status/{data['job_id']}").json()
            assert status["status"] == "contract_error"
            assert status["terminal_reason"] == "contract_error"
            assert "Invalid target column" in status["message"]

    def test_background_runtime_error(self, client, training_data, monkeypatch):
        """RuntimeError in TrainingJob.run() is translated via _friendly_error."""
        graph = _make_modelling_graph(training_data)

        class FailingJob:
            def __init__(self, **_kwargs):
                pass

            def run(self, *_args, **_kwargs):
                raise RuntimeError("CUDA out of memory")

        service, launched = _inline_route_service(monkeypatch)
        with patch("haute.modelling.TrainingJob", FailingJob):
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            data = resp.json()
            service._join_preparation(data["job_id"])
            launched[0].join_and_raise(timeout=10)
            status = client.get(f"/api/modelling/train/status/{data['job_id']}").json()
            assert status["status"] == "error"
            assert "CUDA out of memory" in status["message"]

    def test_background_generic_exception(self, client, training_data, monkeypatch):
        """Generic exception in TrainingJob.run() includes exception type."""
        graph = _make_modelling_graph(training_data)

        class FailingJob:
            def __init__(self, **_kwargs):
                pass

            def run(self, *_args, **_kwargs):
                raise RuntimeError("unexpected crash")

        service, launched = _inline_route_service(monkeypatch)
        with patch("haute.modelling.TrainingJob", FailingJob):
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            data = resp.json()
            service._join_preparation(data["job_id"])
            launched[0].join_and_raise(timeout=10)
            status = client.get(f"/api/modelling/train/status/{data['job_id']}").json()
            assert status["status"] == "error"
            assert "unexpected crash" in status["message"]

    def test_ram_warning_propagated(self, client, training_data, monkeypatch):
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
        from tests.test_training_worker_protocol import _SuccessfulTrainingJob

        service, launched = _inline_route_service(monkeypatch)
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=mock_est,
        ):
            with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
                resp = client.post(
                    "/api/modelling/train",
                    json={"graph": graph, "node_id": "train"},
                )
                data = resp.json()
                service._join_preparation(data["job_id"])
                launched[0].join_and_raise(timeout=10)
                status = client.get(f"/api/modelling/train/status/{data['job_id']}").json()
                # Whether it completed or errored, the warning should be set
                warning = status.get("warning") or ""
                assert "Row limit" in warning or "RAM" in warning

    def test_ram_warning_suppressed_when_user_limit_binds(self, client, training_data, monkeypatch):
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
        from tests.test_training_worker_protocol import _SuccessfulTrainingJob

        service, launched = _inline_route_service(monkeypatch)
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=mock_est,
        ):
            with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
                resp = client.post(
                    "/api/modelling/train",
                    json={"graph": graph, "node_id": "train"},
                )
                data = resp.json()
                service._join_preparation(data["job_id"])
                launched[0].join_and_raise(timeout=10)
                status = client.get(f"/api/modelling/train/status/{data['job_id']}").json()
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
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "group",
                    "group_column": "policy_id",
                    "seed": 42,
                    "validation": {"method": "single", "size": 0.2},
                },
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
        assert demand["train"].excluded_columns == frozenset({"claim_count", "policy_id"})

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
                {"train": pl.DataFrame({"claim_count": [1.0], "driver_age": [40]}).lazy()},
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
        cache_request = captured["dataframe_cache_request"]
        assert cache_request is not None
        assert set(cache_request.keys_by_node) == {"train"}
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
                {"train": pl.DataFrame({"claim_count": [1.0], "driver_age": [40]}).lazy()},
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
                            "nodeType": "dataInput",
                            "config": make_file_input_config("x.parquet"),
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
                "loss_function": "RMSE",
                "params": {"iterations": 10},
                "evaluation": _random_evaluation_config(),
            }
        )

    @pytest.mark.parametrize(
        "cross_validation",
        [
            {"schema_version": 2, "strategy": "random", "fold_count": 3, "seed": 7},
            {"schema_version": 1, "strategy": "random", "fold_count": True, "seed": 7},
            {
                "schema_version": 1,
                "strategy": "group",
                "fold_count": 3,
                "seed": 7,
            },
        ],
    )
    def test_invalid_cross_validation_fails_before_job_creation(
        self, cross_validation: dict[str, object]
    ) -> None:
        with pytest.raises(HTTPException) as raised:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "task": "regression",
                    "loss_function": "RMSE",
                    "cross_validation": cross_validation,
                }
            )

        assert raised.value.status_code == 400
        assert "cross_validation" in str(raised.value.detail)

    def test_valid_glm_config_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "link": "log",
                "all_factors": True,
                "evaluation": _random_evaluation_config(),
            }
        )

    def test_glm_validation_uses_canonical_top_level_family_and_link(self):
        """Nested params are CatBoost config and cannot override GLM fields."""
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "link": "log",
                "all_factors": True,
                "params": {"family": "binomial", "link": "identity"},
                "evaluation": _random_evaluation_config(),
            }
        )

    def test_catboost_missing_loss_raises_400(self):
        """Unset loss must not silently train under CatBoost's RMSE default."""
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "params": {"iterations": 10},
                }
            )
        assert exc_info.value.status_code == 400
        assert "loss function" in exc_info.value.detail.lower()

    def test_catboost_loss_invalid_for_task_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "task": "classification",
                    "loss_function": "Poisson",
                }
            )
        assert exc_info.value.status_code == 400
        assert "Poisson" in exc_info.value.detail

    def test_glm_empty_family_raises_400(self):
        """Unset family must not silently train a gaussian GLM."""
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "",
                }
            )
        assert exc_info.value.status_code == 400
        assert "family" in exc_info.value.detail.lower()

    def test_glm_empty_factors_without_all_raises_400(self):
        """An empty factor set must not silently auto-term over every column."""
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                }
            )
        assert exc_info.value.status_code == 400
        assert "factor" in exc_info.value.detail.lower()

    def test_glm_tweedie_without_variance_power_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "tweedie",
                    "all_factors": True,
                }
            )
        assert exc_info.value.status_code == 400
        assert "variance power" in exc_info.value.detail.lower()

    def test_glm_elastic_net_without_l1_ratio_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                    "all_factors": True,
                    "regularization": "elastic_net",
                }
            )
        assert exc_info.value.status_code == 400
        assert "l1 ratio" in exc_info.value.detail.lower()

    def test_catboost_tweedie_without_variance_power_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "loss_function": "Tweedie",
                }
            )
        assert exc_info.value.status_code == 400
        assert "variance power" in exc_info.value.detail.lower()

    def test_glm_empty_link_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "gaussian",
                "link": "",
                "all_factors": True,
                "evaluation": _random_evaluation_config(),
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

    def test_quasipoisson_accepted(self):
        """Quasi-Poisson estimates its dispersion (no user parameter), so the
        route validates it — RustyStats accepts only log/identity, no sqrt."""
        _validate_glm_family_link("quasipoisson", "log")
        _validate_glm_family_link("quasipoisson", "identity")
        _validate_glm_family_link("quasipoisson", "")  # canonical link

    def test_quasipoisson_rejects_bad_link(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_glm_family_link("quasipoisson", "logit")
        assert exc_info.value.status_code == 400
        assert "logit" in exc_info.value.detail

    def test_negbinomial_accepted(self):
        """Neg. Binomial is offered now its theta gate exists: the training
        objective requires an explicit theta (training_objective_issue), so
        the silent theta=1.0 failover that held it out of #86 cannot fire.
        RustyStats accepts only log/identity — no sqrt."""
        _validate_glm_family_link("negbinomial", "log")
        _validate_glm_family_link("negbinomial", "identity")
        _validate_glm_family_link("negbinomial", "")  # canonical link

    def test_negbinomial_rejects_bad_link(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_glm_family_link("negbinomial", "sqrt")
        assert exc_info.value.status_code == 400
        assert "sqrt" in exc_info.value.detail

    def test_empty_family_raises(self):
        """The old early-return here was the silent gaussian-default channel."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_glm_family_link("", "log")
        assert exc_info.value.status_code == 400
        assert "family" in exc_info.value.detail.lower()

    def test_empty_link_skips(self):
        _validate_glm_family_link("poisson", "")


# ---------------------------------------------------------------------------
# /dispersion/estimate endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def nb_training_data(tmp_path) -> str:
    """Overdispersed count parquet (gamma-Poisson mixture, true theta 2.0)."""
    rng = np.random.default_rng(42)
    n = 400
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    mu = np.exp(0.5 + 0.4 * x1 - 0.3 * x2)
    lam = rng.gamma(2.0, mu / 2.0)
    df = pl.DataFrame({"x1": x1, "x2": x2, "y": rng.poisson(lam).astype(float)})
    path = tmp_path / "nb_data.parquet"
    df.write_parquet(path)
    return str(path)


def _make_negbinomial_graph(data_path: str, **config_overrides: object) -> dict:
    config: dict = {
        "target": "y",
        "algorithm": "glm",
        "task": "regression",
        "family": "negbinomial",
        "terms": {"x1": {"type": "linear"}, "x2": {"type": "linear"}},
        "params": {},
        "evaluation": _random_evaluation_config(),
        **config_overrides,
    }
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(data_path),
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


def _poll_dispersion_until_done(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/modelling/dispersion/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in _TERMINAL_JOB_STATUSES:
            return data
        time.sleep(0.02)
    raise TimeoutError(f"Dispersion job {job_id} did not finish within {timeout}s")


class TestDispersionEstimateEndpoint:
    def test_theta_estimate_completes_with_profile_mle(self, client, nb_training_data):
        """End-to-end: pipeline execution → profile likelihood → value in the
        status payload. The golden value 2.4487 is cross-validated against
        statsmodels NB2 (1/alpha) on this exact draw."""
        graph = _make_negbinomial_graph(nb_training_data)
        resp = client.post(
            "/api/modelling/dispersion/estimate",
            json={"graph": graph, "node_id": "train", "param": "theta"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        final = _poll_dispersion_until_done(client, resp.json()["job_id"])
        assert final["status"] == "completed"
        assert final["param"] == "theta"
        assert final["value"] == pytest.approx(2.4487, abs=0.01)
        assert final["n_fits"] > 0

    def test_train_negbinomial_without_theta_rejected_400(self, client, nb_training_data):
        """The re-enabled family keeps the failover closed: an unset theta
        gates at the route, never falls through to RustyStats' theta=1.0."""
        graph = _make_negbinomial_graph(nb_training_data)
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 400
        assert "theta" in resp.json()["detail"]

    def test_train_negbinomial_with_theta_trains(self, client, nb_training_data, tmp_path):
        """A set theta round-trips to a completed NB fit through /train."""
        graph = _make_negbinomial_graph(
            nb_training_data,
            theta=2.45,
            metrics=["gini"],
            output_dir=str(tmp_path / "outputs"),
        )
        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        final = _poll_until_done(client, resp.json()["job_id"])
        assert final["status"] == "completed"
        assert final["result"]["model_path"].endswith(".rsglm")

    def test_estimate_rejected_for_catboost_node(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post(
            "/api/modelling/dispersion/estimate",
            json={"graph": graph, "node_id": "train", "param": "theta"},
        )
        assert resp.status_code == 400
        assert "GLM" in resp.json()["detail"]

    def test_estimate_rejected_for_family_param_mismatch(self, client, nb_training_data):
        graph = _make_negbinomial_graph(nb_training_data, family="poisson")
        resp = client.post(
            "/api/modelling/dispersion/estimate",
            json={"graph": graph, "node_id": "train", "param": "theta"},
        )
        assert resp.status_code == 400
        assert "negbinomial" in resp.json()["detail"]

    def test_estimate_rejected_when_rest_of_objective_incomplete(self, client, nb_training_data):
        """The profile is conditional on the design, so the factor gate still
        applies — only the parameter being estimated is stubbed."""
        graph = _make_negbinomial_graph(nb_training_data, terms=None)
        resp = client.post(
            "/api/modelling/dispersion/estimate",
            json={"graph": graph, "node_id": "train", "param": "theta"},
        )
        assert resp.status_code == 400
        assert "factor" in resp.json()["detail"].lower()

    def test_estimate_rejected_for_unknown_param(self, client, nb_training_data):
        graph = _make_negbinomial_graph(nb_training_data)
        resp = client.post(
            "/api/modelling/dispersion/estimate",
            json={"graph": graph, "node_id": "train", "param": "alpha"},
        )
        # Pydantic Literal["theta", "var_power"] rejects at parse time.
        assert resp.status_code == 422

    def test_status_unknown_job_404(self, client):
        resp = client.get("/api/modelling/dispersion/status/nonexistent")
        assert resp.status_code == 404

    def test_status_rejects_training_job_ids(self, client):
        """Job types are disjoint: a training job id is not a dispersion job."""
        from haute.routes.modelling import _store

        job_id = _store.create_job({"status": "completed", "job_type": "training"})
        resp = client.get(f"/api/modelling/dispersion/status/{job_id}")
        assert resp.status_code == 404


_NB_ESTIMATION_CONFIG: dict = {
    "target": "y",
    "algorithm": "glm",
    "family": "negbinomial",
    "terms": {"x1": {"type": "linear"}},
    "params": {},
    "evaluation": _random_evaluation_config(),
}


class TestDispersionErrorPaths:
    """The dispersion job's error/cleanup branches must not strand a job in a
    wrong state or orphan the training-prep parquet (same critical-coverage
    rationale as the training worker's)."""

    def _service(self):
        from haute.routes._job_store import JobStore
        from tests.test_training_worker_protocol import _inline_protocol_runner

        store = JobStore()
        return store, TrainService(store, protocol_runner=_inline_protocol_runner)

    def _launch(self, service, store, tmp_path: Path, *, config=None) -> tuple[str, Path, object]:
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "dispersion_estimate",
                "param": "theta",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )
        tmp_parquet = tmp_path / "estimate_data.parquet"
        tmp_parquet.write_bytes(b"parquet")
        thread = service._launch_dispersion_background(
            job_id,
            "train",
            config or dict(_NB_ESTIMATION_CONFIG),
            "theta",
            str(tmp_parquet),
            execution_context=_admitted_training_context_for_launch(job_id),
        )
        assert thread is not None
        return job_id, tmp_parquet, thread

    def test_start_maps_execute_http_error_to_contract_error(self, nb_training_data):
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(nb_training_data)
        store, service = self._service()
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        with (
            patch.object(
                TrainService,
                "_execute_and_sink",
                side_effect=HTTPException(status_code=422, detail="missing column"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.start_dispersion_estimate(body)
        assert exc_info.value.status_code == 422
        (job_id,) = store.jobs
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert "missing column" in job["message"]

    def test_start_preserves_explicit_feature_that_is_also_excluded(
        self,
        nb_training_data,
    ):
        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(
            nb_training_data,
            feature_columns=["x1"],
            exclude=["x1"],
        )
        store, service = self._service()
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        captured: dict[str, object] = {}

        def capture_sink(*_args, keep_columns, **_kwargs):
            captured["keep_columns"] = keep_columns
            return "prepared.parquet"

        context = ExecutionContext(
            operation="dispersion_estimate",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", return_value=(None, None, 100, 3)),
            patch(
                "haute.routes._train_service.create_admitted_execution_context",
                return_value=context,
            ),
            patch.object(service, "_execute_and_sink", side_effect=capture_sink),
            patch.object(service, "_launch_dispersion_background", return_value=object()),
        ):
            response = service.start_dispersion_estimate(body)

        assert response.status == "started"
        assert "x1" in captured["keep_columns"]

    def test_start_maps_unexpected_exception_to_error(self, nb_training_data):
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(nb_training_data)
        store, service = self._service()
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        with (
            patch.object(
                TrainService,
                "_execute_and_sink",
                side_effect=RuntimeError("sink exploded"),
            ),
            pytest.raises(RuntimeError),
        ):
            service.start_dispersion_estimate(body)
        (job_id,) = store.jobs
        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert "sink exploded" in job["message"]

    def test_start_maps_memory_limit_to_507(self, nb_training_data):
        from haute._execution_admission import ExecutionAdmissionError
        from haute._execution_context import ExecutionProfile
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(nb_training_data)
        store, service = self._service()
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        admission_error = ExecutionAdmissionError(
            "dispersion_estimate",
            profile=ExecutionProfile.TRAINING_PREP,
            memory_limit_bytes=1_000,
            rss_at_admission_bytes=2_000,
            reason="over budget",
        )
        with (
            patch.object(TrainService, "_execute_and_sink", side_effect=admission_error),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.start_dispersion_estimate(body)
        assert exc_info.value.status_code == 507
        (job_id,) = store.jobs
        assert store.require_job(job_id)["status"] == "memory_limited"

    def test_cancel_dispersion_running_then_terminal_noop(self):
        store, service = self._service()
        job_id = store.create_job(
            {"status": "running", "job_type": "dispersion_estimate", "param": "theta"}
        )

        cancelled = service.cancel_dispersion(job_id)
        assert cancelled["status"] == "cancelled"

        # A second cancel is a no-op on the now-terminal job.
        again = service.cancel_dispersion(job_id)
        assert again["status"] == "cancelled"

    def test_validate_rejects_unknown_param_directly(self):
        _, service = self._service()
        with pytest.raises(HTTPException) as exc_info:
            service._validate_dispersion_config(dict(_NB_ESTIMATION_CONFIG), "alpha")
        assert exc_info.value.status_code == 400
        assert "Unknown dispersion parameter" in exc_info.value.detail

    def test_validate_rejects_missing_target(self):
        _, service = self._service()
        config = {**_NB_ESTIMATION_CONFIG, "target": ""}
        with pytest.raises(HTTPException) as exc_info:
            service._validate_dispersion_config(config, "theta")
        assert exc_info.value.status_code == 400
        assert "target column" in exc_info.value.detail

    def test_validate_uses_canonical_top_level_glm_fields(self):
        _, service = self._service()
        service._validate_dispersion_config(
            {
                **_NB_ESTIMATION_CONFIG,
                "params": {"family": "poisson", "link": "identity"},
            },
            "theta",
        )

    def test_worker_missing_term_columns_is_contract_error(self, tmp_path: Path):
        """Terms referencing absent columns must fail actionably, not reach
        RustyStats as a phantom design."""

        class FakeJob:
            def __init__(self, *args, **kwargs):
                pass

            def _prepare_data(self, _report, *, execution_context=None):
                return SimpleNamespace(
                    data_path="unused.parquet",
                    owns_tmp=False,
                    features=["other_column"],
                    cat_features=[],
                )

        store, service = self._service()
        with patch("haute.modelling.TrainingJob", FakeJob):
            job_id, tmp_parquet, thread = self._launch(service, store, tmp_path)
            thread.join_and_raise(timeout=10)

        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert "x1" in job["message"]
        assert not tmp_parquet.exists()

    def test_worker_execution_cancelled_marks_cancelled(self, tmp_path: Path):
        from haute._execution_context import ExecutionCancelledError

        class FakeJob:
            def __init__(self, *args, **kwargs):
                pass

            def _prepare_data(self, _report, *, execution_context=None):
                raise ExecutionCancelledError("cancelled mid-prep")

        store, service = self._service()
        with patch("haute.modelling.TrainingJob", FakeJob):
            job_id, tmp_parquet, thread = self._launch(service, store, tmp_path)
            thread.join_and_raise(timeout=10)

        job = store.require_job(job_id)
        assert job["status"] == "cancelled"
        assert job["terminal_reason"] == "cancelled"
        assert not tmp_parquet.exists()

    def test_worker_unexpected_exception_marks_error(self, tmp_path: Path):
        class FakeJob:
            def __init__(self, *args, **kwargs):
                pass

            def _prepare_data(self, _report, *, execution_context=None):
                raise RuntimeError("estimator exploded")

        store, service = self._service()
        with patch("haute.modelling.TrainingJob", FakeJob):
            job_id, tmp_parquet, thread = self._launch(service, store, tmp_path)
            thread.join_and_raise(timeout=10)

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert not tmp_parquet.exists()

    def test_worker_thread_start_failure_maps_to_500(self, tmp_path: Path):
        store, service = self._service()
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "dispersion_estimate",
                "param": "theta",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )
        tmp_parquet = tmp_path / "estimate_data.parquet"
        tmp_parquet.write_bytes(b"parquet")

        with (
            patch("haute.modelling.TrainingJob", return_value=MagicMock()),
            patch(
                "haute.routes._background_jobs.IsolatedSupervisorThread.start",
                side_effect=RuntimeError("thread boom"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service._launch_dispersion_background(
                job_id,
                "train",
                dict(_NB_ESTIMATION_CONFIG),
                "theta",
                str(tmp_parquet),
                execution_context=_admitted_training_context_for_launch(job_id),
            )

        assert exc_info.value.status_code == 500
        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert "Failed to start isolated supervisor" in job["message"]
        assert not tmp_parquet.exists()


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
                            "nodeType": "dataInput",
                            "config": make_file_input_config("data.parquet"),
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

        fake_result = _completed_train_response(
            job_id="test",
            diagnostic_metrics={"gini": 0.85},
            final_test_metrics={"gini": 0.85},
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
                            "nodeType": "dataInput",
                            "config": make_file_input_config("data.parquet"),
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
                                "loss_function": "RMSE",
                                "params": {"iterations": 100},
                                "evaluation": _random_evaluation_config(),
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
                                "loss_function": "RMSE",
                                "params": {"iterations": 10},
                                "evaluation": _random_evaluation_config(),
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
