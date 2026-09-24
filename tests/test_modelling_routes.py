"""API integration tests for modelling endpoints."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._ram_estimate import RamEstimate
from haute.errors import HauteValidationError
from haute.projection import ProjectionRequest, plan
from haute.routes._train_service import (
    TrainService,
    _clamp_row_limit,
    _declared_categorical_levels_for_training,
    _friendly_error,
    _training_required_columns_by_node,
    _validate_glm_config_values,
)
from tests.conftest import (
    make_edge,
    make_file_input_config,
    make_graph,
    make_ready_file_input_config,
)
from tests.job_store_support import seed_job
from tests.training_artifacts_support import publish_trained_job

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


def _write_trained_model(directory: Path, name: str = "frequency") -> Path:
    """Write a model file and a complete feature contract as training leaves them."""
    from haute.modelling._feature_contract import build_contract, save_contract
    from haute.modelling._training_job import model_contract_filename

    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / f"{name}.cbm"
    model_path.write_bytes(b"model-bytes")
    save_contract(
        build_contract(
            features=["x1"],
            feature_types={"x1": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        ),
        directory / model_contract_filename(name),
    )
    return model_path


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


def _xgboost_gpu_graph(data_path: str) -> dict:
    """An XGBoost node set to train on the GPU (MOD-F06)."""
    graph = _make_modelling_graph(data_path, algorithm="xgboost", params={"num_boost_round": 5})
    node = next(node for node in graph["nodes"] if node["id"] == "train")
    node["data"]["config"].update({"loss_function": "RMSE", "device": "gpu"})
    return graph


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
        """Namespace cleanup also removes orphaned auxiliary activity state."""
        from haute.routes._job_store import get_job_store
        from tests.conftest import _clear_job_store_jobs

        store = get_job_store("training")
        job_id = store.create_job({"status": "running"})
        with store._write_lock:  # noqa: SLF001 - deliberate orphan-state regression
            store._jobs.pop(job_id)  # noqa: SLF001

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

        seed_job(
            _store,
            "fake_running",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Training...",
                "created_at": time.time(),
            },
        )
        try:
            graph = _make_modelling_graph(training_data)
            resp = client.post(
                "/api/modelling/train",
                json={"graph": graph, "node_id": "train"},
            )
            assert resp.status_code == 409
            assert "already running" in resp.json()["detail"]
        finally:
            _store.delete_job("fake_running")

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

    def test_train_xgboost_gpu_refuses_on_vram_limit(self, client, training_data):
        graph = _xgboost_gpu_graph(training_data)
        with (
            patch("haute._host_memory.available_vram_bytes", return_value=1),
            patch("haute.modelling.TrainingJob.run", return_value=_completed_train_result()) as run,
        ):
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            assert resp.status_code == 200
            status = _poll_until_done(client, resp.json()["job_id"])
            assert status["status"] == "memory_limited"
            assert status["error_code"] == "gpu_vram_limit"
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
        """Worker completion must fail loudly before storing invalid JSON.

        The response-model rejection is a pydantic ``ValidationError`` — a
        dependency ``ValueError``, not a ``HauteValidationError`` — so it takes
        the type-only fallback as a plain ``error``; the pydantic dump (which
        names the non-finite field) stays in the diagnostic ``error`` field.
        """
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
        assert job["status"] == "error"
        assert job["terminal_reason"] == "error"
        assert "unexpected internal error" in job["message"]
        assert "ValidationError" in job["message"]
        assert "must be a finite number" not in job["message"]
        # The wrapper text claims the "error" field; the child's raw pydantic
        # dump survives under "worker_error" (naming the non-finite field).
        assert "final_test_metrics.auc" in job["worker_error"]
        assert job.get("result") is None
        assert not tmp_parquet.exists()


class TestTrainStatusTimeout:
    def test_timeout_sets_error_with_elapsed(self, client):
        from haute.routes.modelling import _store

        seed_job(
            _store,
            "train_tout",
            {
                "status": "running",
                "progress": 0.3,
                "message": "Training",
                "start_time": time.monotonic() - 500,
                "timeout": 10,
                "created_at": time.time(),
            },
        )
        try:
            resp = client.get("/api/modelling/train/status/train_tout")
            data = resp.json()
            assert data["status"] == "timed_out"
            assert data["terminal_reason"] == "timed_out"
            assert "timed out" in data["message"].lower()
            assert data["elapsed_seconds"] > 0
        finally:
            _store.delete_job("train_tout")

    def test_cancel_training_marks_job_cancelled(self, client):
        from haute.routes.modelling import _store

        seed_job(
            _store,
            "train_cancel_me",
            {
                "status": "running",
                "job_type": "training",
                "progress": 0.3,
                "message": "Training",
                "start_time": time.monotonic() - 1,
                "created_at": time.time(),
            },
        )
        try:
            resp = client.post("/api/modelling/train/cancel/train_cancel_me")
            data = resp.json()
            assert resp.status_code == 200
            assert data["status"] == "cancelled"
            assert data["terminal_reason"] == "cancelled"
            assert _store.require_job("train_cancel_me")["terminal_reason"] == "cancelled"
        finally:
            _store.delete_job("train_cancel_me")

    def test_completed_job_not_overwritten_by_timeout(self, client):
        from haute.routes.modelling import _store

        seed_job(
            _store,
            "train_done_past_timeout",
            {
                "status": "completed",
                "progress": 1.0,
                "message": "Done",
                "start_time": time.monotonic() - 500,
                "timeout": 10,
                "elapsed_seconds": 12.0,
                "created_at": time.time(),
            },
        )
        try:
            resp = client.get("/api/modelling/train/status/train_done_past_timeout")
            data = resp.json()
            assert data["status"] == "completed"
            assert data["message"] == "Done"
            assert "timed out" not in data["message"].lower()
        finally:
            _store.delete_job("train_done_past_timeout")


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
        from haute.routes._job_lifecycle import JobLifecycle
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
                "status": "running",
                "progress": 1.0,
                "message": "Done",
                "result": bad_result,
            }
        )
        JobLifecycle(store).transition(job_id, to="completed")
        try:
            resp = client.get(f"/api/modelling/train/status/{job_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "error"
            assert "non-finite numeric value" in data["message"]
            assert "diagnostic_metrics.auc" in data["message"]
            assert data["result"] is None
        finally:
            store.delete_job(job_id)

    def test_finite_completed_result_is_validated_only_once(self, client, monkeypatch):
        """Status polls must not re-walk an already-validated result on every read.

        ``_assert_json_finite`` is a deep recursive walk; running it on every
        poll is wasted work because completed results are immutable once stored.
        Once a job's result has passed validation we should mark it as
        validated and skip the walk on subsequent polls.
        """
        from haute.routes import modelling as modelling_routes
        from haute.routes._job_lifecycle import JobLifecycle
        from haute.routes.modelling import _store

        store = _store
        good_result = _completed_train_response(
            job_id="good_result",
            diagnostic_metrics={"auc": 0.87},
            final_test_metrics={"auc": 0.87},
        )
        job_id = store.create_job(
            {
                "status": "running",
                "progress": 1.0,
                "message": "Done",
                "result": good_result,
            }
        )
        JobLifecycle(store).transition(job_id, to="completed")

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
            store.delete_job(job_id)

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
        seed_job(
            _store,
            "fake_running",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Training...",
                "created_at": time.time(),
            },
        )
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
            _store.delete_job("fake_running")


class TestSaveModelEndpoint:
    """Tests for ``POST /api/modelling/save`` and its destination preview."""

    @pytest.fixture()
    def _seeded_model(self, tmp_path, monkeypatch, training_artifact_root):
        """Publish a completed job that owns its trained model + contract."""
        from haute.routes.modelling import _store

        project_root = tmp_path / "project"
        project_root.mkdir()
        model_path = _write_trained_model(tmp_path / "trained")
        published = publish_trained_job(
            _store,
            "save_job",
            root=training_artifact_root,
            model_file=model_path,
            result=_completed_train_response(job_id="save_job", model_path=str(model_path)),
            config={},
        )
        monkeypatch.setattr("haute.routes.modelling._get_project_root", lambda: project_root)

        try:
            yield SimpleNamespace(
                model_path=published / "frequency.cbm",
                contract_path=published / "frequency.feature_contract.json",
                contract_text=(published / "frequency.feature_contract.json").read_text(),
                project_root=project_root,
                models=project_root / "models",
            )
        finally:
            _store.delete_job("save_job")

    @staticmethod
    def _save(client, output_path: str, **extra: object):
        return client.post(
            "/api/modelling/save",
            json={"job_id": "save_job", "output_path": output_path, **extra},
        )

    def test_save_model_job_not_found(self, client):
        resp = client.post(
            "/api/modelling/save",
            json={"job_id": "nonexistent", "output_path": "frequency"},
        )
        assert resp.status_code == 404

    def test_save_model_job_not_completed(self, client):
        from haute.routes.modelling import _store

        seed_job(
            _store,
            "fake_running_save",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Training...",
                "created_at": time.time(),
            },
        )
        try:
            resp = client.post(
                "/api/modelling/save",
                json={"job_id": "fake_running_save", "output_path": "frequency"},
            )
            assert resp.status_code == 400
        finally:
            _store.delete_job("fake_running_save")

    def test_bare_filename_saves_model_and_contract_under_models(self, client, _seeded_model):
        resp = self._save(client, "frequency")

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ok",
            "path": "models/frequency.cbm",
            "feature_contract_path": "models/frequency.feature_contract.json",
        }
        assert (_seeded_model.models / "frequency.cbm").read_bytes() == b"model-bytes"
        assert (
            _seeded_model.models / "frequency.feature_contract.json"
        ).read_text() == _seeded_model.contract_text
        assert _seeded_model.model_path.read_bytes() == b"model-bytes"
        assert _seeded_model.contract_path.read_text() == _seeded_model.contract_text

    def test_folder_paths_are_project_root_relative(self, client, _seeded_model):
        resp = self._save(client, "exports/severity.cbm")

        assert resp.status_code == 200
        assert resp.json()["path"] == "exports/severity.cbm"
        assert resp.json()["feature_contract_path"] == "exports/severity.feature_contract.json"
        assert (_seeded_model.project_root / "exports" / "severity.cbm").is_file()

    def test_existing_destination_requires_overwrite(self, client, _seeded_model):
        _seeded_model.models.mkdir()
        (_seeded_model.models / "frequency.cbm").write_bytes(b"stale-model")
        (_seeded_model.models / "frequency.feature_contract.json").write_text('{"stale": true}')

        refused = self._save(client, "frequency")

        assert refused.status_code == 409
        assert refused.json()["detail"] == {
            "error_code": "model_file_exists",
            "message": "Model file already exists: models/frequency.cbm",
        }
        assert (_seeded_model.models / "frequency.cbm").read_bytes() == b"stale-model"

        replaced = self._save(client, "frequency", overwrite=True)

        assert replaced.status_code == 200
        assert (_seeded_model.models / "frequency.cbm").read_bytes() == b"model-bytes"
        assert (
            _seeded_model.models / "frequency.feature_contract.json"
        ).read_text() == _seeded_model.contract_text

    def test_competing_saves_never_mix_one_jobs_model_with_anothers_contract(
        self, tmp_path, _seeded_model, training_artifact_root
    ):
        """Two saves to one destination: the second waits for the first, then refuses."""
        import threading

        from haute.routes import modelling as modelling_routes
        from haute.schemas import SaveModelRequest

        other_model = _write_trained_model(tmp_path / "trained_other")
        (tmp_path / "trained_other" / "frequency.cbm").write_bytes(b"other-model-bytes")
        publish_trained_job(
            modelling_routes._store,
            "save_job_other",
            root=training_artifact_root,
            model_file=other_model,
            result=_completed_train_response(job_id="save_job_other", model_path=str(other_model)),
            config={},
        )
        real_copy = modelling_routes.atomic_copy_files
        first_copying = threading.Event()
        release_first = threading.Event()
        second_copying = threading.Event()
        copy_calls: list[int] = []

        def gated_copy(pairs):
            copy_calls.append(len(copy_calls))
            if len(copy_calls) == 1:
                first_copying.set()
                assert release_first.wait(10), "the test never released the first save"
            else:
                second_copying.set()
            real_copy(pairs)

        outcomes: dict[str, object] = {}

        def save(job_id: str) -> None:
            try:
                outcomes[job_id] = modelling_routes.save_model(
                    SaveModelRequest(job_id=job_id, output_path="frequency")
                )
            except HTTPException as exc:
                outcomes[job_id] = exc

        first = threading.Thread(target=save, args=("save_job",))
        second = threading.Thread(target=save, args=("save_job_other",))
        try:
            with patch.object(modelling_routes, "atomic_copy_files", gated_copy):
                try:
                    first.start()
                    assert first_copying.wait(10)
                    second.start()
                    # The second save must not reach publication while the first holds it.
                    assert not second_copying.wait(0.5)
                finally:
                    release_first.set()
                    for thread in (first, second):
                        if thread.ident is not None:
                            thread.join(10)
        finally:
            modelling_routes._store.delete_job("save_job_other")

        assert not first.is_alive() and not second.is_alive()
        assert getattr(outcomes["save_job"], "status", None) == "ok"
        refused = outcomes["save_job_other"]
        assert isinstance(refused, HTTPException) and refused.status_code == 409
        assert copy_calls == [0]
        assert (_seeded_model.models / "frequency.cbm").read_bytes() == b"model-bytes"
        assert (
            _seeded_model.models / "frequency.feature_contract.json"
        ).read_text() == _seeded_model.contract_text

    def test_an_existing_contract_alone_also_requires_overwrite(self, client, _seeded_model):
        _seeded_model.models.mkdir()
        (_seeded_model.models / "frequency.feature_contract.json").write_text('{"stale": true}')

        resp = self._save(client, "frequency")

        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "model_file_exists"
        assert not (_seeded_model.models / "frequency.cbm").exists()

    def test_suffix_mismatch_is_rejected(self, client, _seeded_model):
        resp = self._save(client, "frequency.pkl")

        assert resp.status_code == 400
        assert ".cbm" in resp.json()["detail"]
        assert not _seeded_model.models.exists()

    def test_missing_training_contract_is_gone(self, client, _seeded_model):
        _seeded_model.contract_path.unlink()

        resp = self._save(client, "frequency")

        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "training_artifacts_unavailable"
        assert str(_seeded_model.contract_path.parent) not in str(resp.json()["detail"])
        assert not _seeded_model.models.exists()

    def test_released_training_artifacts_are_gone(self, client, _seeded_model):
        from haute.routes._training_artifacts import TRAINING_ARTIFACTS_HANDLE_KEY
        from haute.routes.modelling import _store

        assert _store.detach_artifact_handle("save_job", TRAINING_ARTIFACTS_HANDLE_KEY)

        resp = self._save(client, "frequency")

        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "training_artifacts_unavailable"
        assert not _seeded_model.models.exists()

    @pytest.mark.parametrize("output_path", ["../outside.cbm", "models/../../outside.cbm"])
    def test_escaping_path_is_forbidden(self, client, _seeded_model, output_path):
        resp = self._save(client, output_path)

        assert resp.status_code == 403

    def test_copy_oserror_returns_sanitized_500(self, client, _seeded_model):
        with patch(
            "haute.routes.modelling.atomic_copy_files",
            side_effect=OSError("disk full"),
        ):
            resp = self._save(client, "frequency")

        assert resp.status_code == 500
        assert "models/frequency.cbm" not in resp.json()["detail"]

    def test_empty_output_path_is_invalid(self, client, _seeded_model):
        assert self._save(client, "").status_code == 422

    def test_destination_preview_resolves_without_writing(self, client, _seeded_model):
        resp = client.post(
            "/api/modelling/save/destination",
            json={"output_path": "frequency", "algorithm": "glm"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"path": "models/frequency.rsglm", "suffix_mismatch": False}
        assert not _seeded_model.models.exists()

    def test_destination_preview_flags_a_suffix_mismatch(self, client, _seeded_model):
        resp = client.post(
            "/api/modelling/save/destination",
            json={"output_path": "models/frequency.rsglm", "algorithm": "catboost"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"path": "models/frequency.rsglm", "suffix_mismatch": True}

    def test_destination_preview_rejects_an_escape_and_unknown_algorithm(
        self, client, _seeded_model
    ):
        escaped = client.post(
            "/api/modelling/save/destination",
            json={"output_path": "../frequency", "algorithm": "catboost"},
        )
        unknown = client.post(
            "/api/modelling/save/destination",
            json={"output_path": "frequency", "algorithm": "unregistered"},
        )

        assert escaped.status_code == 403
        assert unknown.status_code == 422


# ---------------------------------------------------------------------------
# Phase 1A: Pure function tests
# ---------------------------------------------------------------------------


class TestFriendlyError:
    """Unit tests for _friendly_error — translates exceptions into user messages."""

    def test_haute_validation_error_passthrough(self):
        exc = HauteValidationError("Target column 'z' not found")
        assert _friendly_error(exc) == "Target column 'z' not found"

    def test_plain_value_error_takes_the_type_only_fallback(self):
        """Provenance is enforced by the marker type: a dependency's bare
        ``ValueError`` body is never promoted verbatim."""
        exc = ValueError("could not parse '/private/tmp/leaky' as float")
        result = _friendly_error(exc)
        assert "leaky" not in result
        assert "ValueError" in result
        assert "unexpected internal error" in result

    def test_file_not_found_does_not_quote_the_path(self):
        """A fit-stage missing file is typically an internal staged asset —
        the path stays diagnostic, never in the terminal message."""
        exc = FileNotFoundError(2, "No such file or directory", "/data/missing.parquet")
        result = _friendly_error(exc)
        assert "could not find a file it needs" in result
        assert "missing.parquet" not in result
        assert "/data" not in result

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
        # The third-party body is never interpolated.
        assert "expected 10" not in result

    def test_catboost_generic(self):
        """The generic CatBoost shape names the type, never the body."""
        exc = type("CatBoostError", (Exception,), {})("internal pool error at /tmp/pool")
        result = _friendly_error(exc)
        assert "CatBoostError" in result
        assert "internal pool error" not in result
        assert "/tmp" not in result

    def test_catboost_generic_names_training_context(self):
        exc = type("CatBoostError", (Exception,), {})("internal pool error")
        result = _friendly_error(exc, context="target 'sev' (objective 'RMSE')")
        assert "target 'sev' (objective 'RMSE')" in result
        assert "internal pool error" not in result

    def test_catboost_in_message_only_is_not_classified_catboost(self):
        """Classification keys on the exception TYPE — a non-CatBoost error
        that merely mentions catboost in its text takes the generic fallback
        and never embeds its body."""
        exc = RuntimeError("catboost cache failed at /var/lib/haute/token=abc")
        result = _friendly_error(exc)
        assert "RuntimeError" in result
        assert "CatBoost error" not in result
        assert "/var/lib" not in result
        assert "token" not in result

    def test_os_error(self):
        """The errno-derived OS reason is surfaced; the staging path is not."""
        exc = OSError(13, "Permission denied", "/models/model.cbm")
        result = _friendly_error(exc)
        assert result.startswith("Training could not save its output files")
        assert "Permission denied" in result
        assert "/models" not in result

    def test_os_error_strerror_is_rederived_from_errno(self):
        """OSError.strerror is constructor-supplied — a dependency can put
        arbitrary text there. The reason comes from os.strerror(errno)."""
        exc = OSError(5, "failed opening /tmp/secret credential=xyz")
        result = _friendly_error(exc)
        assert "/tmp/secret" not in result
        assert "credential" not in result
        assert "Input/output error" in result

    def test_os_error_without_errno_uses_generic_reason(self):
        exc = OSError("raw single-argument message")
        result = _friendly_error(exc)
        assert "file-system error" in result
        assert "raw single-argument message" not in result
        assert "OSError" not in result

    def test_fallback_includes_type_but_not_third_party_text(self):
        exc = RuntimeError("something unexpected")
        result = _friendly_error(exc)
        assert "RuntimeError" in result
        assert "something unexpected" not in result
        assert "error details" in result

    def test_fallback_names_training_context(self):
        exc = RuntimeError("boom")
        result = _friendly_error(exc, context="target 'freq' (objective 'Poisson')")
        assert result.startswith("Training of target 'freq' (objective 'Poisson') failed")

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

    def test_catboost_feature_mismatch_excludes_original_message(self):
        """Flipped from the pre-curation pin: the CatBoost body is never
        interpolated, even in the feature-mismatch shape."""
        exc = type("CatBoostError", (Exception,), {})(
            "feature number mismatch: expected 5 but got 3"
        )
        result = _friendly_error(exc)
        assert "feature mismatch" in result.lower()
        assert "expected 5 but got 3" not in result


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

    def test_estimate_gpu_unknown_vram_returns_advisory_warning(self, client, training_data):
        """Unknown VRAM surfaces as gpu_warning in the response, without the
        switch-to-CPU refusal suffix reserved for observed-insufficient VRAM."""
        graph = _make_modelling_graph(
            training_data,
            params=_fast_training_params(task_type="GPU"),
        )
        with patch("haute._host_memory.available_vram_bytes", return_value=None):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["gpu_warning"] is not None
        assert "could not be detected" in data["gpu_warning"]
        assert "Switch task_type" not in data["gpu_warning"]
        assert data["gpu_vram_available_mb"] is None
        assert data["gpu_vram_estimated_mb"] is not None

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

    @pytest.mark.parametrize(
        "terms",
        [
            {"ghost": {"type": "linear"}},
            {"x1": {"type": "linear"}, "ratio": {"type": "expression", "expr": "x1 / missing"}},
        ],
    )
    def test_evaluation_preview_ignores_unfinished_terms(self, client, training_data, terms):
        """The preview reads only the target and the evaluation key, so a GLM
        term on a column that is not upstream does not fail the estimate."""
        graph = _make_modelling_graph(training_data, algorithm="glm", params={})
        config = graph["nodes"][1]["data"]["config"]
        config.update({"family": "gaussian", "terms": terms})

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 200, resp.text
        preview = resp.json()["evaluation_preview"]
        assert preview is not None
        assert preview["development_rows"] == 60

    def test_estimate_maps_evaluation_preview_validation_failure_to_422(
        self,
        client,
        tmp_path,
    ):
        """A data-dependent preflight failure is a 422 with its reason, never a 500."""
        path = tmp_path / "null_target.parquet"
        pl.DataFrame(
            {
                "x1": [1.0, 2.0, 3.0],
                "y": pl.Series([None, None, None], dtype=pl.Float64),
            }
        ).write_parquet(path)
        graph = _make_modelling_graph(str(path))

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail.startswith("Evaluation preview failed: ")
        assert "only null values" in detail

    def test_estimate_maps_contract_mismatch_to_422(self, client, training_data):
        """A node whose output breaks its declared contract is a 422, never a 500."""
        base = _make_modelling_graph(training_data)
        train_config = next(n for n in base["nodes"] if n["id"] == "train")["data"]["config"]
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": make_ready_file_input_config(training_data),
                        },
                    },
                    {
                        "id": "prep",
                        "data": {
                            "label": "prep",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = source",
                                "contract": {
                                    "inputs": [],
                                    "outputs": ["x1", "x2", "y", "phantom"],
                                },
                            },
                        },
                    },
                    {
                        "id": "train",
                        "data": {"label": "train", "nodeType": "modelling", "config": train_config},
                    },
                ],
                "edges": [
                    make_edge("source", "prep").model_dump(),
                    make_edge("prep", "train").model_dump(),
                ],
            }
        ).model_dump()

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail.startswith("Evaluation preview failed: ")
        assert "phantom" in detail

    def test_estimate_maps_polars_missing_column_to_422(self, client, training_data):
        """Post-load code naming a column the source lacks is a 422, never a 500."""
        graph = _make_modelling_graph(training_data)
        source = next(n for n in graph["nodes"] if n["id"] == "source")
        source["data"]["config"]["code"] = "df = df.with_columns(flag = pl.col('missing'))"

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail.startswith("Evaluation preview failed: ")
        assert "missing" in detail

    def test_estimate_evaluation_preview_accepts_upstream_group_by(
        self,
        client,
        training_data,
    ):
        graph = _make_modelling_graph(training_data)
        graph["nodes"].insert(
            1,
            {
                "id": "grouped_features",
                "data": {
                    "label": "grouped_features",
                    "nodeType": "polars",
                    "config": {
                        "code": (
                            "df = source.group_by('x1').agg("
                            "pl.col('x2').mean().alias('x2'), "
                            "pl.col('y').mean().alias('y'))"
                        )
                    },
                },
            },
        )
        graph["edges"] = [
            make_edge("source", "grouped_features").model_dump(),
            make_edge("grouped_features", "train").model_dump(),
        ]

        resp = client.post(
            "/api/modelling/estimate",
            json={"graph": graph, "node_id": "train"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["evaluation_preview"] is not None

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

    def test_estimate_xgboost_gpu_vram_path(self, client, training_data):
        graph = _xgboost_gpu_graph(training_data)
        with patch("haute._host_memory.available_vram_bytes", return_value=1):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("gpu_vram_estimated_mb") is not None
        assert "Train on CPU" in data["gpu_warning"]

    @pytest.mark.parametrize(
        ("algorithm", "device", "message"),
        [
            ("lightgbm", "gpu", "LightGBM trains on CPU only"),
            ("xgboost", "cuda", 'device must be "cpu" or "gpu"'),
        ],
    )
    def test_train_refuses_a_bad_device_before_preparation_with_explicit_metrics(
        self, client, training_data, algorithm, device, message
    ):
        graph = _xgboost_gpu_graph(training_data)
        node = next(node for node in graph["nodes"] if node["id"] == "train")
        node["data"]["config"].update(
            {
                "algorithm": algorithm,
                "device": device,
                "metrics": ["rmse"],
                "params": {"num_iterations": 5}
                if algorithm == "lightgbm"
                else {"num_boost_round": 5},
            }
        )
        with patch(
            "haute.routes._training_lifecycle.TrainService._launch_training_protocol"
        ) as launch:
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 400, resp.text
        assert message in resp.json()["detail"]
        launch.assert_not_called()

    def test_estimate_missing_node(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post(
            "/api/modelling/estimate", json={"graph": graph, "node_id": "nonexistent"}
        )
        assert resp.status_code == 404

    def test_estimate_failure_is_an_error_not_an_empty_estimate(self, client, training_data):
        """An estimator exception is a failure the user sees, never the empty
        estimate that stands for a size the estimator cannot prove."""
        graph = _make_modelling_graph(training_data)
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            side_effect=RuntimeError("probe failed"),
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 500

    @pytest.mark.parametrize(
        ("unavailable", "expected_rows", "expected_reason"),
        [
            (
                RamEstimate.row_count_unprovable("explode_items", 8 * 1024**3),
                None,
                {"reason": "row_count_unprovable", "blocking_node_id": "explode_items"},
            ),
            (
                RamEstimate.schema_unresolvable(250_000, 8 * 1024**3),
                250_000,
                {"reason": "schema_unresolvable", "blocking_node_id": None},
            ),
        ],
    )
    def test_estimate_the_estimator_cannot_size_says_why_without_figures(
        self, client, training_data, unavailable, expected_rows, expected_reason
    ):
        """An unavailable estimate carries its reason and no memory figure or
        VRAM check, even with GPU training and a user row limit configured."""
        graph = _make_modelling_graph(training_data)
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["params"] = {"task_type": "GPU"}
                node["data"]["config"]["row_limit"] = 500
        with (
            patch("haute._ram_estimate.estimate_safe_training_rows", return_value=unavailable),
            patch("haute.routes.modelling._check_gpu_vram") as vram_check,
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["unavailable"] == expected_reason
        assert data["total_rows"] == expected_rows
        assert (data["estimated_mb"], data["training_mb"], data["bytes_per_row"]) == (
            None,
            None,
            None,
        )
        assert data["available_mb"] == 8192.0
        assert data["safe_row_limit"] == 500
        assert (data["was_downsampled"], data["warning"]) == (False, None)
        assert data["gpu_vram_estimated_mb"] is None
        vram_check.assert_not_called()

    def test_available_estimate_reports_figures_and_no_reason(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        available = RamEstimate(
            safe_row_limit=None,
            total_rows=1_000,
            estimated_bytes=3 * 1024**2,
            available_bytes=8 * 1024**3,
            bytes_per_row=3_145.7,
            was_downsampled=False,
            warning=None,
            probe_columns=4,
        )
        with patch("haute._ram_estimate.estimate_safe_training_rows", return_value=available):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["unavailable"] is None
        assert (data["total_rows"], data["estimated_mb"], data["training_mb"]) == (1_000, 3.0, 3.0)
        assert data["bytes_per_row"] == 3_145.7

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"estimated_mb": None}, "requires a row total and memory figures"),
            ({"total_rows": None}, "requires a row total and memory figures"),
            (
                {"unavailable": {"reason": "schema_unresolvable", "blocking_node_id": None}},
                "has no memory figures",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "was_downsampled": True,
                    "unavailable": {"reason": "schema_unresolvable", "blocking_node_id": None},
                },
                "no downsampling verdict or warning",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "gpu_vram_estimated_mb": 12.0,
                    "unavailable": {"reason": "schema_unresolvable", "blocking_node_id": None},
                },
                "no GPU VRAM check",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "unavailable": {"reason": "row_count_unprovable", "blocking_node_id": "j"},
                },
                "only a row_count_unprovable estimate lacks a row total",
            ),
            (
                {
                    "total_rows": None,
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "unavailable": {"reason": "row_count_unprovable", "blocking_node_id": None},
                },
                "names the blocking node",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "unavailable": {"reason": "schema_unresolvable", "blocking_node_id": "j"},
                },
                "names no blocking node",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "unavailable": {"reason": "cardinality", "blocking_node_id": None},
                },
                "row_count_unprovable",
            ),
        ],
    )
    def test_estimate_response_rejects_figures_that_disagree_with_availability(
        self, overrides, message
    ):
        from pydantic import ValidationError

        from haute.schemas import TrainEstimateResponse

        payload = {
            "total_rows": 100,
            "estimated_mb": 1.0,
            "training_mb": 1.0,
            "available_mb": 8192.0,
            "bytes_per_row": 10.0,
            "unavailable": None,
        }
        with pytest.raises(ValidationError, match=message):
            TrainEstimateResponse.model_validate({**payload, **overrides})

    def test_estimate_suppresses_ram_warning_when_user_limit_binds(self, client, training_data):
        """When user's row_limit is lower than the RAM-safe limit, suppress the RAM warning."""
        graph = _make_modelling_graph(training_data)
        # Inject a user row_limit that is lower than the RAM-safe limit
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["row_limit"] = 500

        mock_est = RamEstimate(
            safe_row_limit=9_000_000,
            warning="Dataset downsampled to 9,000,000 of 10,000,000 rows",
            total_rows=10_000_000,
            probe_columns=5,
            estimated_bytes=1_000_000_000,
            available_bytes=20_000_000_000,
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

        mock_est = RamEstimate(
            safe_row_limit=9_000_000,
            warning="Dataset downsampled to 9,000,000 of 10,000,000 rows",
            total_rows=10_000_000,
            probe_columns=5,
            estimated_bytes=1_000_000_000,
            available_bytes=20_000_000_000,
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

    @staticmethod
    @contextmanager
    def _published(
        job_id: str,
        root: Path,
        tmp_path: Path,
        *,
        config: dict[str, object] | None = None,
        **result_overrides: object,
    ):
        from haute.routes.modelling import _store

        model_path = _write_trained_model(tmp_path / job_id)
        publish_trained_job(
            _store,
            job_id,
            root=root,
            model_file=model_path,
            result=_completed_train_response(
                job_id=job_id, model_path=str(model_path), **result_overrides
            ),
            config=config or {"algorithm": "catboost", "task": "regression", "target": "y"},
            node_label="my_model",
        )
        try:
            yield
        finally:
            _store.delete_job(job_id)

    def test_mlflow_log_success(self, client, tmp_path, training_artifact_root):
        """A published job logs a candidate built from its own artifacts."""
        mock_log_result = SimpleNamespace(
            backend="local",
            experiment_name="my_model",
            run_id="abc123",
            run_url=None,
            tracking_uri="file:///tmp/mlruns",
        )
        with (
            self._published(
                "test_log",
                training_artifact_root,
                tmp_path,
                final_test_metrics={"gini": 0.85, "rmse": 0.12},
                diagnostic_metrics={"gini": 0.85, "rmse": 0.12},
            ),
            patch(
                "haute.modelling._mlflow_log.log_experiment",
                return_value=mock_log_result,
            ) as m_log,
        ):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "test_log"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["backend"] == "local"
        assert data["run_id"] == "abc123"
        candidate = m_log.call_args.kwargs["candidate"]
        assert candidate.metrics["final_test_gini"] == 0.85
        assert candidate.tags["haute.job_id"] == "test_log"
        assert candidate.artifacts.model.parent.parent.parent == training_artifact_root

    def test_fixed_catboost_logs_the_recorded_final_tree_count(
        self, client, tmp_path, training_artifact_root
    ):
        log_result = SimpleNamespace(
            backend="local",
            experiment_name="my_model",
            run_id="trees",
            run_url=None,
            tracking_uri="file:///tmp/mlruns",
        )
        with (
            self._published(
                "test_trees",
                training_artifact_root,
                tmp_path,
                config={
                    "algorithm": "catboost",
                    "task": "regression",
                    "target": "y",
                    "params": {"n_estimators": 20, "depth": 3, "early_stopping_rounds": 5},
                },
                final_tree_count=7,
            ),
            patch("haute.modelling._mlflow_log.log_experiment", return_value=log_result) as m_log,
        ):
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "test_trees"})
        assert resp.status_code == 200
        candidate = m_log.call_args.kwargs["candidate"]
        assert candidate.params["param_iterations"] == 7
        assert candidate.params["param_depth"] == 3
        assert "param_n_estimators" not in candidate.params
        assert "param_early_stopping_rounds" not in candidate.params

    def test_mlflow_log_exception_returns_500(self, client, tmp_path, training_artifact_root):
        """If log_experiment raises, should return 500."""
        with (
            self._published("test_err", training_artifact_root, tmp_path),
            patch(
                "haute.modelling._mlflow_log.log_experiment",
                side_effect=RuntimeError("MLflow connection refused"),
            ),
        ):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "test_err"},
            )
        assert resp.status_code == 500
        assert "MLflow connection refused" not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]

    def test_mlflow_log_no_result_data(self, client):
        """Completed job with no result should return 400."""
        from haute.routes.modelling import _store

        seed_job(
            _store,
            "no_result",
            {
                "status": "completed",
                "result": None,
                "created_at": time.time(),
            },
        )
        try:
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "no_result"})
            assert resp.status_code == 400
            assert "no evaluation report" in resp.json()["detail"].lower()
        finally:
            _store.delete_job("no_result")


# ---------------------------------------------------------------------------
# Phase 1A: Background thread error tests
# ---------------------------------------------------------------------------


class TestBackgroundThreadErrors:
    """Test error handling in the background training thread."""

    def test_background_validation_error(self, client, training_data, monkeypatch):
        """HauteValidationError in TrainingJob.run() surfaces verbatim as contract_error."""
        graph = _make_modelling_graph(training_data)

        class FailingJob:
            def __init__(self, **_kwargs):
                pass

            def run(self, *_args, **_kwargs):
                raise HauteValidationError("Invalid target column: not found")

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

    def test_background_plain_value_error_takes_the_fallback(
        self, client, training_data, monkeypatch
    ):
        """A dependency's bare ValueError never rides the validation channel:
        it takes the type-only fallback as a plain error, with the raw text
        kept diagnostic."""
        graph = _make_modelling_graph(training_data)

        class FailingJob:
            def __init__(self, **_kwargs):
                pass

            def run(self, *_args, **_kwargs):
                raise ValueError("could not convert string to float: '/tmp/leaky'")

        service, launched = _inline_route_service(monkeypatch)
        with patch("haute.modelling.TrainingJob", FailingJob):
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
            data = resp.json()
            assert data["status"] == "started"
            service._join_preparation(data["job_id"])
            launched[0].join_and_raise(timeout=10)
            status = client.get(f"/api/modelling/train/status/{data['job_id']}").json()
            assert status["status"] == "error"
            assert status["terminal_reason"] == "error"
            assert "unexpected internal error" in status["message"]
            assert "ValueError" in status["message"]
            assert "leaky" not in status["message"]

    def test_background_runtime_error(self, client, training_data, monkeypatch):
        """RuntimeError in TrainingJob.run() surfaces the curated fallback.

        The terminal message names the exception type and training context but
        never the third-party message body; the raw text stays diagnostic.
        """
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
            # The curated fallback is promoted verbatim: the entrypoint must
            # stamp user_message, or the wrapper prefix would appear here.
            assert status["message"].startswith("Training of ")
            assert "RuntimeError" in status["message"]
            assert "CUDA out of memory" not in status["message"]
            # The raw third-party text stays in the server-side job record
            # (the public status response whitelist never exposed it).
            from haute.routes.modelling import _store

            job = _store.require_job(data["job_id"])
            assert "CUDA out of memory" in job["worker_remote_traceback"]

    def test_background_generic_exception(self, client, training_data, monkeypatch):
        """The curated fallback names the exception type and the fit's target."""
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
            assert status["message"].startswith("Training of ")
            assert "RuntimeError" in status["message"]
            assert "target" in status["message"]
            assert "unexpected crash" not in status["message"]
            from haute.routes.modelling import _store

            job = _store.require_job(data["job_id"])
            assert "unexpected crash" in job["worker_remote_traceback"]

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


@contextmanager
def _training_prep_context():
    """Yield an admitted TRAINING_PREP context for direct child-core calls."""
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile

    context = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    try:
        yield context
    finally:
        context.release_admission(preserve_primary_error=True)


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

    def test_catboost_feature_menu_exclusions_project_before_api_input_loading(self):
        config = {
            "algorithm": "catboost",
            "target": "target",
            "weight": "weight",
            "offset": "offset",
            "fold_column": "fold",
            "id_columns": ["id"],
            "evaluation": {
                "schema_version": 1,
                "strategy": "group",
                "group_column": "group",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "exclude": ["excluded_feature"],
        }
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "api",
                        "data": {
                            "label": "api",
                            "nodeType": "apiInput",
                            "config": {
                                "tables": [
                                    {
                                        "label": "rows",
                                        "emit": True,
                                        "columns": [
                                            {"name": column, "selected": True}
                                            for column in (
                                                "feature_a",
                                                "feature_b",
                                                "excluded_feature",
                                                "target",
                                                "weight",
                                                "offset",
                                                "fold",
                                                "id",
                                                "group",
                                            )
                                        ],
                                    }
                                ]
                            },
                        },
                    },
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": config,
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "api-train",
                        "source": "api",
                        "target": "train",
                        "sourceHandle": "rows",
                    }
                ],
            }
        )
        required = _training_required_columns_by_node("train", config)
        projection = plan(
            ProjectionRequest(
                graph=graph,
                target_node_id="train",
                profile=ExecutionProfile.TRAINING_PREP,
                required_columns_by_node=required,
            )
        )
        expected = frozenset(
            {
                "feature_a",
                "feature_b",
                "target",
                "weight",
                "offset",
                "fold",
                "id",
                "group",
            }
        )

        assert projection.needed_by_node["train"] == expected
        assert projection.demand_for_edge(graph.edges[0]) == expected
        assert projection.diagnostics.node_reasons["train"].rule == "schema_all_except"

    def test_prepare_training_data_forwards_training_projection(self, tmp_path):
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

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
        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config={"target": "claim_count"},
            project_root=str(tmp_path),
            required_columns_by_node=seeds,
        )
        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
            patch("haute.executor._preview_cache", MagicMock()),
            patch("haute.trace._cache", MagicMock()),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        assert outcome.failure is None
        assert captured["required_columns_by_node"] == seeds
        # The run executes under a seed plan planned for the same demand.
        plan = captured["snapshot_plan"]
        assert plan is not None
        assert captured["prepare_inputs"] is False
        assert plan.decision.target_node_id == "train"
        assert seeds["train"] <= set(plan.decision.planning_required_columns["train"])
        assert outcome.parquet_path == parquet_path
        assert Path(parquet_path).exists()
        assert outcome.feature_selection is not None
        assert outcome.execution_metrics is not None
        assert pl.read_parquet(parquet_path)["claim_count"].to_list() == [1.0]

    def test_prepare_training_data_maps_bounded_sink_failure_to_contract_failure(
        self,
        tmp_path,
    ) -> None:
        from haute.errors import BoundedMemoryUnsupportedError
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

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

        def fake_execute_lazy(*_args, **_kwargs):
            # Filtered so the frame is not sliceable: training's writer slices a
            # frame it can slice and would never reach the native sink this test
            # is about.
            frame = (
                pl.DataFrame({"claim_count": [1.0], "driver_age": [40]})
                .lazy()
                .filter(pl.col("claim_count") > 0)
            )
            return ({"train": frame}, ["train"], {}, {})

        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config={"target": "claim_count"},
            project_root=str(tmp_path),
        )
        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
            patch(
                "haute._chunked_writes.bounded_sink",
                side_effect=BoundedMemoryUnsupportedError("Bounded streaming sink failed"),
            ),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        assert outcome.parquet_path is None
        failure = outcome.failure
        assert failure is not None
        assert failure.terminal_reason == "contract_error"
        assert failure.http_status_code == 422
        assert "bounded streaming mode" in failure.message
        assert not Path(parquet_path).exists()

    def test_prepare_training_data_gate_failure_removes_parquet(self, tmp_path) -> None:
        """A target/task mismatch fails as a contract failure with no artifact."""
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

        config = {
            "target": "claim_count",
            "task": "classification",
            "metrics": ["auc"],
        }
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": config,
                        },
                    }
                ],
                "edges": [],
            }
        )

        def fake_execute_lazy(*_args, **_kwargs):
            return (
                {
                    "train": pl.DataFrame(
                        {"claim_count": [0.5, 1.25, 2.75], "driver_age": [40, 41, 42]}
                    ).lazy()
                },
                ["train"],
                {},
                {},
            )

        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config=config,
            project_root=str(tmp_path),
        )
        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        failure = outcome.failure
        assert failure is not None
        assert failure.terminal_reason == "contract_error"
        assert failure.http_status_code == 422
        assert not Path(parquet_path).exists()

    def test_prepare_training_data_maps_memory_failure_to_memory_outcome(self, tmp_path) -> None:
        from haute._execution_context import ExecutionMemoryLimitExceededError
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

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
        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config={"target": "claim_count"},
            project_root=str(tmp_path),
        )

        def raise_memory(*_args, **_kwargs):
            raise ExecutionMemoryLimitExceededError(
                "training_pipeline",
                rss_bytes=2048,
                limit_bytes=1024,
            )

        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=raise_memory,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        failure = outcome.failure
        assert failure is not None
        assert failure.terminal_reason == "memory_limited"
        assert failure.http_status_code == 507
        assert failure.fields["error_code"] == "memory_limit"
        assert not Path(parquet_path).exists()


class TestExecuteAndSinkPlanCleanup:
    """Verify the seed plan is closed even when _execute_lazy raises."""

    def test_seed_plan_closed_on_error(self, tmp_path):
        """If _execute_lazy raises, the plan's leases and staging are released."""
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

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
        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="n",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config={"target": "claim_count"},
            project_root=str(tmp_path),
        )

        plans: list[Any] = []

        def failing_execute_lazy(*args, **kwargs):
            plans.append(kwargs["snapshot_plan"])
            raise RuntimeError("boom")

        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=failing_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
            patch("haute.executor._preview_cache", MagicMock()),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        assert outcome.failure is not None
        assert outcome.failure.terminal_reason == "error"
        assert outcome.failure.http_status_code == 500
        # The plan was opened for the run and closed when it failed.
        assert len(plans) == 1
        assert plans[0]._closed


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
            TrainService._validate_config({"target": "y", "algorithm": "unregistered"})
        assert exc_info.value.status_code == 400
        assert "unregistered" in exc_info.value.detail
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
                "terms": {"age": {"type": "linear"}},
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
                "terms": {"age": {"type": "linear"}},
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

    def test_glm_empty_terms_raises_400(self):
        """An empty term set must not silently auto-term over every column."""
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                }
            )
        assert exc_info.value.status_code == 400
        assert "add a term to at least one feature" in exc_info.value.detail.lower()

    def test_glm_tweedie_without_variance_power_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "tweedie",
                    "terms": {"age": {"type": "linear"}},
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
                    "terms": {"age": {"type": "linear"}},
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
                "terms": {"age": {"type": "linear"}},
                "evaluation": _random_evaluation_config(),
            }
        )


# ---------------------------------------------------------------------------
# Synchronous GLM schema gate on /train and /dispersion/estimate
# ---------------------------------------------------------------------------


_GLM_COLLIDING_TERMS: dict = {
    "x": {"type": "linear"},
    "x_sq": {"type": "expression", "expr": "x ** 2"},
}


@pytest.fixture()
def glm_collision_data(tmp_path) -> str:
    """Source whose unused ``x_sq`` column collides with an expression key."""
    path = tmp_path / "glm_collision.parquet"
    pl.DataFrame(
        {"x": [1.0, 2.0, 3.0], "x_sq": [1.0, 4.0, 9.0], "y": [1.0, 2.0, 3.0]}
    ).write_parquet(path)
    return str(path)


def _glm_schema_gate_graph(data_path: str | None, config: dict):
    """Data Input → Modelling graph; ``data_path=None`` leaves the model unfed."""
    nodes: list[dict] = []
    edges: list[dict] = []
    if data_path is not None:
        nodes.append(
            {
                "id": "source",
                "data": {
                    "label": "source",
                    "nodeType": "dataInput",
                    "config": make_ready_file_input_config(data_path),
                },
            }
        )
        edges.append(make_edge("source", "train").model_dump())
    nodes.append(
        {"id": "train", "data": {"label": "train", "nodeType": "modelling", "config": config}}
    )
    return make_graph({"nodes": nodes, "edges": edges})


def _glm_transform_gate_graph(data_path: str, code: str, config: dict):
    """Data Input → Polars → Modelling, with the user's own code in the middle."""
    return make_graph(
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
                    "id": "prep",
                    "data": {"label": "prep", "nodeType": "polars", "config": {"code": code}},
                },
                {
                    "id": "train",
                    "data": {"label": "train", "nodeType": "modelling", "config": config},
                },
            ],
            "edges": [
                make_edge("source", "prep").model_dump(),
                make_edge("prep", "train").model_dump(),
            ],
        }
    )


class TestGlmInputSchemaGate:
    """GLM term columns are checked against the exact unprojected input schema
    before a job is created, so a mismatch is a 422 on the request rather than
    a job that fails later during background preparation."""

    def _service(self):
        from haute.routes._job_store import JobStore

        store = JobStore()
        return store, TrainService(store)

    def test_training_route_rejects_expression_key_colliding_with_unprojected_column(
        self, glm_collision_data
    ):
        """Upstream has an unused column ``x_sq``; the model keys an expression
        ``x_sq``. Projection would drop the column, so the gate must see the
        unprojected schema."""
        from haute.schemas import TrainRequest

        body = TrainRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    "algorithm": "glm",
                    "target": "y",
                    "family": "gaussian",
                    "terms": _GLM_COLLIDING_TERMS,
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
        )
        store, service = self._service()

        with pytest.raises(HTTPException) as raised:
            service.start(body)

        assert raised.value.status_code == 422
        assert "names a column" in str(raised.value.detail)
        assert not store.list_jobs()

    def test_dispersion_route_rejects_expression_key_colliding_with_unprojected_column(
        self, glm_collision_data
    ):
        """The dispersion route materialises the same frame, so it gates the
        same way — a 422 before the estimation job exists."""
        from haute.schemas import DispersionEstimateRequest

        body = DispersionEstimateRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    "algorithm": "glm",
                    "target": "y",
                    "family": "tweedie",
                    "terms": _GLM_COLLIDING_TERMS,
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
            param="var_power",
        )
        store, service = self._service()

        with pytest.raises(HTTPException) as raised:
            service.start_dispersion_estimate(body)

        assert raised.value.status_code == 422
        assert "names a column" in str(raised.value.detail)
        assert not store.list_jobs()

    def test_training_route_returns_422_when_input_schema_cannot_be_resolved(self):
        """An unfed modelling node has no schema to check against — an explicit
        422 naming the cause, never a 500 and never the projected schema."""
        from haute.schemas import TrainRequest

        body = TrainRequest(
            graph=_glm_schema_gate_graph(
                None,
                {
                    "algorithm": "glm",
                    "target": "y",
                    "family": "gaussian",
                    "terms": {"x": {"type": "linear"}},
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
        )
        store, service = self._service()

        with pytest.raises(HTTPException) as raised:
            service.start(body)

        assert raised.value.status_code == 422
        detail = str(raised.value.detail)
        assert "Training input schema could not be resolved" in detail
        assert "No input data available" in detail
        assert not store.list_jobs()

    @pytest.mark.parametrize(
        ("code", "error_name"),
        [
            ("df = source.with_columns(x2=undefined_name)", "NameError"),
            ("", "NotImplementedError"),
        ],
    )
    def test_user_transform_error_becomes_422_on_training_and_dispersion_routes(
        self, glm_collision_data, code, error_name
    ):
        """The schema-only build runs the user's own transform code, so any
        exception that code raises is the user's to fix — a named 422, never a
        500 that reads as a Haute crash."""
        from haute.schemas import DispersionEstimateRequest, TrainRequest

        base = {
            "algorithm": "glm",
            "target": "y",
            "terms": {"x": {"type": "linear"}},
            "evaluation": _random_evaluation_config(),
        }
        store, service = self._service()

        with pytest.raises(HTTPException) as raised:
            service.start(
                TrainRequest(
                    graph=_glm_transform_gate_graph(
                        glm_collision_data, code, {**base, "family": "gaussian"}
                    ),
                    node_id="train",
                )
            )
        assert raised.value.status_code == 422
        assert "could not be resolved" in str(raised.value.detail)
        assert error_name in str(raised.value.detail)

        with pytest.raises(HTTPException) as raised:
            service.start_dispersion_estimate(
                DispersionEstimateRequest(
                    graph=_glm_transform_gate_graph(
                        glm_collision_data, code, {**base, "family": "tweedie"}
                    ),
                    node_id="train",
                    param="var_power",
                )
            )
        assert raised.value.status_code == 422
        assert "could not be resolved" in str(raised.value.detail)
        assert error_name in str(raised.value.detail)
        assert not store.list_jobs()


# ---------------------------------------------------------------------------
# GLM sink exclusions
# ---------------------------------------------------------------------------


def _inline_sink_service(monkeypatch: pytest.MonkeyPatch):
    """Inline-protocol service plus the supervisor threads it launches."""
    from haute.routes._job_store import JobStore
    from tests.test_training_worker_protocol import _inline_protocol_runner

    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    launched: list = []
    launch_protocol = service._supervisor.launch_protocol

    def capture_launch(*args, **kwargs):
        thread = launch_protocol(*args, **kwargs)
        launched.append(thread)
        return thread

    monkeypatch.setattr(service._supervisor, "launch_protocol", capture_launch)
    return service, launched


def _spy_on_sink_exclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[list[str] | None, list[str]]]:
    """Record each ``_execute_and_sink`` call's ``exclude`` and the columns the
    real sink actually wrote.

    The real sink runs; its parquet is read here because the caller deletes it
    (or hands ownership to the worker) as soon as preparation returns. Asserting
    on ``exclude`` alone would pass even if preparation never produced a frame.
    """
    captured: list[tuple[list[str] | None, list[str]]] = []
    original = TrainService._execute_and_sink

    def spy(self, body, preamble_ns, row_limit, job_id, **kwargs):
        prepared = original(self, body, preamble_ns, row_limit, job_id, **kwargs)
        captured.append((kwargs.get("exclude"), pl.read_parquet(prepared).columns))
        return prepared

    monkeypatch.setattr(TrainService, "_execute_and_sink", spy)
    return captured


_GLM_STALE_EXCLUDE_CONFIG: dict = {
    "algorithm": "glm",
    "target": "y",
    "terms": {"x": {"type": "linear"}},
    "exclude": ["x"],
}


class TestGlmSinkExclusions:
    """A GLM's column membership comes from its terms and interactions, so the
    training and dispersion sinks never drop a column by ``exclude`` — a stale
    entry left behind by a CatBoost run must not delete a live GLM term."""

    def test_training_sink_keeps_excluded_glm_term_columns(
        self, glm_collision_data, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HAUTE_MEM_LOG", str(tmp_path / "training_mem.log"))
        captured = _spy_on_sink_exclusions(monkeypatch)
        from haute.schemas import TrainRequest
        from tests.test_training_worker_protocol import _SuccessfulTrainingJob

        body = TrainRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    **_GLM_STALE_EXCLUDE_CONFIG,
                    "family": "gaussian",
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
        )
        service, launched = _inline_sink_service(monkeypatch)

        with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
            response = service.start(body)
            service._join_preparation(response.job_id)
            for thread in launched:
                thread.join(timeout=10)

        assert len(captured) == 1
        exclude, columns = captured[0]
        assert exclude is None
        # ``x`` is the live GLM term the stale ``exclude`` would have dropped.
        assert "x" in columns and "y" in columns
        job = service._store.require_job(response.job_id)
        assert job["status"] == "completed", job
        assert launched

    def test_dispersion_sink_keeps_excluded_glm_term_columns(
        self, glm_collision_data, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HAUTE_MEM_LOG", str(tmp_path / "training_mem.log"))
        captured = _spy_on_sink_exclusions(monkeypatch)
        from haute.schemas import DispersionEstimateRequest

        body = DispersionEstimateRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    **_GLM_STALE_EXCLUDE_CONFIG,
                    "family": "tweedie",
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
            param="var_power",
        )
        service, launched = _inline_sink_service(monkeypatch)

        response = service.start_dispersion_estimate(body)
        for thread in launched:
            thread.join(timeout=10)

        assert len(captured) == 1
        exclude, columns = captured[0]
        assert exclude is None
        assert "x" in columns and "y" in columns
        job = service._store.require_job(response.job_id)
        assert job["status"] == "completed", job


# ---------------------------------------------------------------------------
# _validate_glm_config_values unit tests
# ---------------------------------------------------------------------------


def _glm_values(**overrides: object) -> dict[str, object]:
    return {
        "algorithm": "glm",
        "target": "y",
        "family": "poisson",
        "terms": {"x": {"type": "linear"}},
        **overrides,
    }


class TestValidateGlmConfigValues:
    def test_unknown_family_names_the_supported_families(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_glm_config_values(_glm_values(family="exponential"))
        assert exc_info.value.status_code == 400
        assert "exponential" in exc_info.value.detail
        assert "gaussian" in exc_info.value.detail

    def test_invalid_link_names_the_family_links(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_glm_config_values(_glm_values(family="gamma", link="logit"))
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == (
            "Link 'logit' is not valid for the gamma family. Valid links: log, identity."
        )

    @pytest.mark.parametrize(
        ("family", "link"),
        [
            ("gaussian", "inverse"),
            ("binomial", "probit"),
            ("binomial", "cloglog"),
            ("poisson", "sqrt"),
            ("gamma", "inverse"),
            ("inverse_gaussian", ""),
        ],
    )
    def test_unsupported_links_and_inverse_gaussian_are_refused(self, family, link):
        with pytest.raises(HTTPException) as exc_info:
            _validate_glm_config_values(_glm_values(family=family, link=link))
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize(
        ("family", "link"),
        [
            ("quasipoisson", ""),
            ("quasipoisson", "identity"),
            ("negbinomial", "log"),
            ("quasibinomial", "logit"),
            ("binomial", "log"),
            ("gaussian", "log"),
        ],
    )
    def test_supported_family_links_pass(self, family, link):
        _validate_glm_config_values(_glm_values(family=family, link=link))

    def test_absent_family_is_left_to_the_objective_gate(self):
        _validate_glm_config_values({"algorithm": "glm", "terms": {"x": {"type": "linear"}}})


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

    def test_theta_estimate_keeps_preparation_evidence(self, client, nb_training_data):
        """The estimate worker reports last; the job keeps what preparation wrote."""
        from haute.routes import modelling

        graph = _make_negbinomial_graph(nb_training_data)
        graph["nodes"].append(
            {
                "id": "prepared",
                "data": {
                    "label": "prepared",
                    "nodeType": "polars",
                    "config": {"code": 'df = source.sort("x1")'},
                },
            }
        )
        graph["edges"] = [
            make_edge("source", "prepared").model_dump(),
            make_edge("prepared", "train").model_dump(),
        ]
        resp = client.post(
            "/api/modelling/dispersion/estimate",
            json={"graph": graph, "node_id": "train", "param": "theta"},
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]

        final = _poll_dispersion_until_done(client, job_id)
        assert final["status"] == "completed", final
        metrics = modelling._train_service._store.require_job(job_id)["execution_metrics"]
        assert [
            (capture["node_id"], capture["outcome"])
            for capture in metrics["shared_snapshot_captures"]
        ] == [("prepared", "published")]

    def test_train_negbinomial_without_theta_rejected_400(self, client, nb_training_data):
        """The re-enabled family gates early: an unset theta is a 400 at the
        route, never a RustyStats refusal from inside a training job."""
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
        """The profile is conditional on the design, so the term gate still
        applies — only the parameter being estimated is stubbed."""
        graph = _make_negbinomial_graph(nb_training_data, terms=None)
        resp = client.post(
            "/api/modelling/dispersion/estimate",
            json={"graph": graph, "node_id": "train", "param": "theta"},
        )
        assert resp.status_code == 400
        assert "add a term to at least one feature" in resp.json()["detail"].lower()

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

        job_id = _store.create_job({"status": "running", "job_type": "training"})
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

    def test_failed_estimate_keeps_preparation_evidence(self, tmp_path: Path):
        """A failed estimate worker's metrics carry what preparation recorded."""
        from tests.test_training_worker_protocol import _context_with_preparation_evidence

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

        class ExplodingJob:
            def __init__(self, **_kwargs):
                pass

            def _prepare_data(self, *_args, **_kwargs):
                raise RuntimeError("librs panic")

        with patch("haute.modelling.TrainingJob", ExplodingJob):
            thread = service._launch_dispersion_background(
                job_id,
                "train",
                dict(_NB_ESTIMATION_CONFIG),
                "theta",
                str(tmp_parquet),
                execution_context=_context_with_preparation_evidence(),
            )
            assert thread is not None
            thread.join_and_raise(timeout=10)

        job = store.require_job(job_id)
        assert job["status"] == "error"
        metrics = job["execution_metrics"]
        assert metrics["operation"] == "dispersion_estimate"
        assert [capture["node_id"] for capture in metrics["shared_snapshot_captures"]] == ["join"]
        assert [warning["code"] for warning in metrics["warnings"]] == [
            "snapshot_capture_superseded"
        ]

    def test_worker_fallback_stamps_curated_message(self, tmp_path: Path):
        """Entrypoint-level stamp pin: an unexpected in-worker exception must
        surface the curated dispersion fallback verbatim (no wrapper prefix,
        no third-party body) — removing the entrypoint's user_message stamp
        turns the terminal message into the typed wrapper and fails here."""
        store, service = self._service()

        class ExplodingJob:
            def __init__(self, **_kwargs):
                pass

            def _prepare_data(self, *_args, **_kwargs):
                raise RuntimeError("librs panic at /opt/rustystats/cache")

        with patch("haute.modelling.TrainingJob", ExplodingJob):
            job_id, _tmp_parquet, thread = self._launch(service, store, tmp_path)
            thread.join_and_raise(timeout=10)

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["message"].startswith("Dispersion estimation of ")
        assert "RuntimeError" in job["message"]
        assert "librs panic" not in job["message"]
        assert "/opt/rustystats" not in job["message"]

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
        (job_id,) = store.list_jobs()
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert "missing column" in job["message"]

    def test_start_keeps_only_role_columns_because_glm_ignores_catboost_levers(
        self,
        nb_training_data,
    ):
        """feature_columns and exclude are CatBoost levers: a GLM's sink keeps
        its role columns and reads its term columns through projection demand."""
        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(
            nb_training_data,
            feature_columns=["x1"],
            exclude=["x1"],
            theta=1.5,
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
                "haute.routes._training_lifecycle.create_admitted_execution_context",
                return_value=context,
            ),
            patch.object(service, "_execute_and_sink", side_effect=capture_sink),
            patch.object(service, "_launch_dispersion_background", return_value=object()),
        ):
            response = service.start_dispersion_estimate(body)

        assert response.status == "started"
        assert captured["keep_columns"] == ["y"]

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
        (job_id,) = store.list_jobs()
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
        (job_id,) = store.list_jobs()
        assert store.require_job(job_id)["status"] == "memory_limited"

    def test_dispersion_timeout_marks_the_job_timed_out_and_late_completion_cannot_revive_it(
        self, client, nb_training_data, monkeypatch
    ):
        from haute._worker_isolation import IsolatedWorkerTimeoutError
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(nb_training_data)
        store, service = self._service()
        monkeypatch.setattr("haute.routes.modelling._train_service", service)

        # Confirm status read has no timeout accounting (unlike /train/status)
        seed_job(
            store,
            "disp_no_status_timeout",
            {
                "status": "running",
                "job_type": "dispersion_estimate",
                "param": "theta",
                "progress": 0.2,
                "message": "Estimating",
                "start_time": time.monotonic() - 500,
                "timeout": 10,
                "created_at": time.time(),
            },
        )
        try:
            status_read_before = client.get(
                "/api/modelling/dispersion/status/disp_no_status_timeout"
            )
            assert status_read_before.status_code == 200
            assert status_read_before.json()["status"] == "running"
        finally:
            store.delete_job("disp_no_status_timeout")

        # Deadline is enforced via isolated worker timeout
        def standin_worker(*_args: object, **_kwargs: object) -> None:
            raise IsolatedWorkerTimeoutError(timeout_seconds=60.0)

        threads: list[object] = []
        orig_launch = service._launch_dispersion_background

        def capturing_launch(*args: object, **kwargs: object):
            t = orig_launch(*args, **kwargs)
            if t is not None:
                threads.append(t)
            return t

        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        with (
            patch.object(service, "_launch_dispersion_background", side_effect=capturing_launch),
            patch("haute.routes._training_lifecycle._run_dispersion_process_job", standin_worker),
        ):
            resp = service.start_dispersion_estimate(body)

        assert resp.status == "started"
        job_id = resp.job_id

        assert len(threads) == 1
        threads[0].join(timeout=5.0)
        assert not threads[0].is_alive()

        expected_message = (
            "The background process was stopped after exceeding its time limit "
            "of 60 seconds. Try again with less data, or increase the configured timeout."
        )

        job = store.require_job(job_id)
        assert job["status"] == "timed_out"
        assert job["terminal_reason"] == "timed_out"
        assert job["message"] == expected_message
        assert "timed out" in job["message"].lower() or "time limit" in job["message"].lower()
        assert "Traceback" not in job["message"]
        assert job.get("elapsed_seconds", 0) >= 0
        assert job.get("value") is None
        assert job.get("result") is None

        status_resp = client.get(f"/api/modelling/dispersion/status/{job_id}")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["status"] == "timed_out"
        assert data["terminal_reason"] == "timed_out"
        assert data["message"] == expected_message
        assert data["elapsed_seconds"] >= 0
        assert data["value"] is None

        # Late completion cannot revive the timed-out job
        late_result = service._lifecycle.transition(
            job_id,
            to="completed",
            message="Completed",
            fields={"value": 2.45, "progress": 1.0},
        )
        assert late_result is None

        status_again = client.get(f"/api/modelling/dispersion/status/{job_id}")
        assert status_again.status_code == 200
        data_again = status_again.json()
        assert data_again["status"] == "timed_out"
        assert data_again["terminal_reason"] == "timed_out"
        assert data_again["message"] == expected_message
        assert data_again["value"] is None

        stored_again = store.require_job(job_id)
        assert stored_again["status"] == "timed_out"
        assert stored_again["terminal_reason"] == "timed_out"
        assert stored_again["message"] == expected_message
        assert stored_again.get("value") is None

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
                    feature_dtypes={"other_column": "Float64"},
                )

            def _role_columns(self):
                return {"y": "target"}

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
            _store.delete_job(job_id)

    @pytest.mark.asyncio
    async def test_completed_job_includes_result(self):
        from haute.routes._job_lifecycle import JobLifecycle
        from haute.routes.modelling import _store, train_status

        fake_result = _completed_train_response(
            job_id="test",
            diagnostic_metrics={"gini": 0.85},
            final_test_metrics={"gini": 0.85},
        )
        job_id = _store.create_job(
            {
                "status": "running",
                "progress": 1.0,
                "message": "Done",
                "result": fake_result,
                "warning": "Row limit applied",
            }
        )
        JobLifecycle(_store).transition(job_id, to="completed")
        try:
            result = await train_status(job_id)
            assert result.status == "completed"
            assert result.result is not None
            assert result.warning == "Row limit applied"
        finally:
            _store.delete_job(job_id)

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
