"""The modelling train and train-status routes.

``/train`` launches a background job that reports progress, live and bounded loss history and its
evaluation response; launch, background-thread and timeout failures mark the job's error;
``/train/status`` and cancellation report the job's state; and the route functions behave the same
when called directly.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._ram_estimate import RamEstimate
from haute.errors import HauteValidationError, RequestRefusedError
from haute.routes._training_lifecycle import TrainService
from tests import _modelling_route_helpers
from tests._modelling_route_helpers import (
    _admitted_training_context_for_launch,
    _completed_train_response,
    _evaluation_response_payload,
    _fast_training_params,
    _make_joined_modelling_graph,
    _make_modelling_graph,
    _poll_until_done,
    _random_evaluation_config,
    _xgboost_gpu_graph,
)
from tests._training_helpers import join_training_preparation
from tests.conftest import (
    make_edge,
    make_file_input_config,
    make_graph,
)
from tests.job_store_support import seed_job

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
_fast_optional_training_diagnostics = _modelling_route_helpers._fast_optional_training_diagnostics
training_data = _modelling_route_helpers.training_data


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
                lambda payload: payload.update(plan_sha256="not-a-digest"),
                "plan_sha256",
            ),
        ],
    )
    def test_completed_response_rejects_malformed_report(self, mutate, message: str) -> None:
        # Structure only: the report's invariants are checked where its
        # artifacts are produced (tests/test_evaluation.py).
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

    @pytest.mark.parametrize(
        ("target", "feature_columns"),
        [
            pytest.param("nonexistent", None, id="missing-target"),
            pytest.param("y", ["x1", "nonexistent"], id="missing-selected-feature"),
        ],
    )
    def test_train_with_a_missing_column_names_it(
        self, client, training_data, target, feature_columns
    ):
        """The exact training demand meets the missing column at its source: a
        422 contract failure naming it, never a generic pipeline failure."""
        graph = _make_modelling_graph(training_data, target=target, feature_columns=feature_columns)
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

    @pytest.mark.parametrize(
        "feature_columns", [None, [], ["y"]], ids=["absent", "empty", "target_only"]
    )
    def test_train_refuses_a_catboost_node_without_features(
        self, client, training_data, feature_columns
    ):
        """Features are opt-in: the request is refused before any job starts, rather
        than training on every non-role column of the data."""
        from haute.routes.modelling import _store

        graph = _make_modelling_graph(training_data)
        config = graph["nodes"][1]["data"]["config"]
        if feature_columns is None:
            del config["feature_columns"]
        else:
            config["feature_columns"] = feature_columns

        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 400
        assert "Modelling config has no features" in resp.json()["detail"]
        assert not _store.list_jobs()

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

    def test_an_unproven_join_bound_trains_on_every_row_without_a_downsample_warning(
        self, client, tmp_path, monkeypatch
    ) -> None:
        """The RAM limit applies to the rows that arrive, not the join's product (MDL-01)."""
        graph = _make_joined_modelling_graph(tmp_path, 200, None)
        # Too little RAM for the 40,000-row worst case: the limit floors at 500 rows,
        # more than the 200 that really arrive.
        monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 1)

        resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 200, resp.text
        status = _poll_until_done(client, resp.json()["job_id"])
        assert status["status"] == "completed", status
        assert status["warning"] is None
        assert status["result"]["warning"] is None
        assert status["result"]["development_rows"] == 200

    @pytest.mark.parametrize("downsampled", [False, True], ids=["gpu-only", "ram-and-gpu"])
    def test_a_gpu_advisory_reaches_the_completed_result(
        self, client, tmp_path, monkeypatch, downsampled: bool
    ) -> None:
        rng = np.random.RandomState(3)
        rows = 800
        data = tmp_path / "rows.parquet"
        pl.DataFrame(
            {"x1": rng.randn(rows), "x2": rng.randn(rows), "y": rng.rand(rows) * 3}
        ).write_parquet(data)
        advisory = "GPU VRAM could not be detected (no NVIDIA GPU found)."

        def advise(service, train_params, row_limit, total_source_rows, probe_columns, job_id, **_):
            # What the VRAM check records for an unknown GPU; the fit itself runs on CPU.
            service._store.update_job(job_id, gpu_warning=advisory, warning=advisory)

        monkeypatch.setattr(TrainService, "_check_gpu_vram_before_launch", advise)
        if downsampled:
            # Too little RAM for 800 rows: the limit floors at 500 and samples the input.
            monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 1)

        resp = client.post(
            "/api/modelling/train",
            json={"graph": _make_modelling_graph(str(data)), "node_id": "train"},
        )

        assert resp.status_code == 200, resp.text
        status = _poll_until_done(client, resp.json()["job_id"])
        assert status["status"] == "completed", status
        assert status["result"]["warning"] == status["warning"]
        assert status["warning"].endswith(advisory)
        assert status["warning"].startswith("Dataset downsampled to 500") is downsampled

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
                "node_id": "train",
                "node_label": "train",
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
            pytest.raises(RequestRefusedError) as exc_info,
        ):
            service._launch_background(
                job_id,
                "train",
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "task": "regression",
                    "loss_function": "RMSE",
                    "feature_columns": ["x"],
                    "evaluation": _random_evaluation_config(),
                },
                {},
                str(tmp_parquet),
                None,
                None,
                fit_mode="in_memory",
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
                "node_id": "train",
                "node_label": "train",
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
                    "feature_columns": ["x"],
                    "evaluation": _random_evaluation_config(),
                },
                {},
                str(tmp_parquet),
                None,
                None,
                fit_mode="in_memory",
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

    def test_an_outcome_its_supervisor_could_not_record_is_answered_as_failed(
        self, client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """ROAD-WORKER-07: the job no longer stays running until its TTL."""
        from haute._worker_protocol import WorkerRequest, WorkerResultManifest
        from haute.routes._background_jobs import IsolatedJobSupervisor
        from haute.routes._job_lifecycle import JobLifecycle
        from haute.routes.modelling import _store, _train_service

        supervisor = IsolatedJobSupervisor(
            JobLifecycle(_store),
            protocol_runner=lambda *_args, **_kwargs: WorkerResultManifest(metadata={}),
        )
        monkeypatch.setattr(_train_service, "_supervisor", supervisor)
        seed_job(
            _store,
            "train_unrecorded",
            {
                "status": "running",
                "start_time": time.monotonic(),
                "timeout": 3600,
                "created_at": time.time(),
            },
        )
        try:
            # A lifecycle-owned field makes the store refuse the terminal write.
            supervisor.launch_protocol(
                "train_unrecorded",
                lambda *_args: None,
                WorkerRequest("train_unrecorded", "test", {}),
                artifact_root=tmp_path,
                artifact_kinds=frozenset(),
                max_artifact_size_bytes=0,
                completed_fields=lambda _result: {"ended_at": 1.0},
            ).join(10)
            assert _store.require_job("train_unrecorded")["status"] == "running"

            data = client.get("/api/modelling/train/status/train_unrecorded").json()

            assert data["status"] == "error"
            assert "could not record" in data["message"]
        finally:
            _store.delete_job("train_unrecorded")

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

    @pytest.mark.parametrize(
        ("status", "progress"),
        [
            (
                "running",
                {
                    "phase": "trial_fit",
                    "trial_index": 2,
                    "trial_count": 5,
                    "fold_index": 1,
                    "fold_count": 3,
                    "completed_fits": 4,
                    "total_fits": 16,
                    "best_objective": 0.0,
                },
            ),
            (
                "running",
                {
                    "phase": "planning",
                    "trial_index": None,
                    "trial_count": 5,
                    "fold_index": None,
                    "fold_count": 1,
                    "completed_fits": 0,
                    "total_fits": 6,
                    "best_objective": None,
                },
            ),
            (
                "error",
                {
                    "phase": "trial_complete",
                    "trial_index": 3,
                    "trial_count": 5,
                    "fold_index": None,
                    "fold_count": 1,
                    "completed_fits": 3,
                    "total_fits": 6,
                    "best_objective": -1.25,
                },
            ),
        ],
        ids=["trial-fit", "planning", "failed-after-a-trial"],
    )
    def test_poll_and_cancel_return_the_recorded_tuning_progress(
        self, client, status: str, progress: dict[str, object]
    ):
        from haute.routes.modelling import _store

        seed_job(
            _store,
            "train_tuning",
            {
                "status": status,
                "job_type": "training",
                "progress": 0.4,
                "message": "Tuning",
                "start_time": time.monotonic() - 1,
                "created_at": time.time(),
                **progress,
            },
        )
        try:
            polled = client.get("/api/modelling/train/status/train_tuning").json()
            assert {key: polled[key] for key in progress} == progress
            cancelled = client.post("/api/modelling/train/cancel/train_tuning").json()
            assert cancelled["status"] == ("cancelled" if status == "running" else status)
            assert {key: cancelled[key] for key in progress} == progress
        finally:
            _store.delete_job("train_tuning")

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


def test_live_loss_history_spans_its_fit_and_restarts_with_the_next() -> None:
    from haute.routes import _training_worker

    limit = _training_worker._max_train_loss_history()
    history: list[dict[str, float]] = []
    truncated = False
    peak, trough = -float("inf"), float("inf")
    for n in range(1, 1001):
        # A falling curve with a training spike at 500 and a validation dip at 700.
        train = 5.0 if n == 500 else 1.0 / n
        evaluation = 0.0001 if n == 700 else 1.0 / n + 0.1
        row = {"iteration": float(n), "train_rmse": train, "eval_rmse": evaluation}
        history, truncated = _training_worker._append_live_loss_row(history, truncated, row, 1000)
        assert len(history) <= limit
        # Every extreme so far survives thinning, so an axis drawn from the rows never shrinks.
        peak, trough = max(peak, train), min(trough, evaluation)
        assert max(entry["train_rmse"] for entry in history) == peak
        assert min(entry["eval_rmse"] for entry in history) == trough
    iterations = [row["iteration"] for row in history]
    assert truncated is True
    assert iterations[0] == 1.0 and iterations[-1] == 1000.0
    assert iterations == sorted(set(iterations))
    # Even coverage of the rounds so far: no gap is wider than two buckets.
    assert max(b - a for a, b in zip(iterations, iterations[1:])) <= 2 * 1000 / (limit - 2)

    # The next fit's first row starts its own history, even with the same values.
    first = {"iteration": 1.0, "train_rmse": 1.0, "eval_rmse": 1.1}
    history, truncated = _training_worker._append_live_loss_row(history, truncated, first, 1000)
    assert history == [first]
    assert truncated is False

    # A fit that outruns its stated budget still stays within the limit and spans
    # every round from its first.
    for n in range(2, 501):
        row = {"iteration": float(n), "train_rmse": 1.0 / n}
        history, truncated = _training_worker._append_live_loss_row(history, truncated, row, 10)
        assert len(history) <= limit
    assert history[0] == first and history[-1]["iteration"] == 500.0
    gaps = [b["iteration"] - a["iteration"] for a, b in zip(history, history[1:])]
    assert max(gaps) <= 2 * 500 / (limit - 6)
    assert truncated is True


def test_live_loss_history_updates_a_repeated_round_in_place() -> None:
    from haute.routes import _training_worker

    history: list[dict[str, float]] = []
    for row in (
        {"iteration": 1.0, "train_deviance": 0.5},
        {"iteration": 2.0, "train_deviance": 0.4},
        # A bagged fit reports its furthest round again as slower bags catch up.
        {"iteration": 2.0, "train_deviance": 0.45},
    ):
        history, truncated = _training_worker._append_live_loss_row(history, False, row, 10)
    assert history == [
        {"iteration": 1.0, "train_deviance": 0.5},
        {"iteration": 2.0, "train_deviance": 0.45},
    ]
    assert truncated is False


def test_live_loss_history_refuses_a_limit_too_small_for_its_extremes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import _training_worker

    # Two values need six rows: first, newest, and each value's lowest and highest.
    monkeypatch.setenv("HAUTE_TRAIN_LOSS_HISTORY_LIMIT", "5")
    history: list[dict[str, float]] = []
    truncated = False
    for n in range(1, 6):
        row = {"iteration": float(n), "train_rmse": 1.0 / n, "eval_rmse": 1.0 / n}
        history, truncated = _training_worker._append_live_loss_row(history, truncated, row, 10)
    with pytest.raises(RuntimeError, match="needs at least 6 rows"):
        _training_worker._append_live_loss_row(
            history, truncated, {"iteration": 6.0, "train_rmse": 0.1, "eval_rmse": 0.1}, 10
        )


def test_bounded_loss_history_thins_the_whole_fit_around_its_best_iteration() -> None:
    from haute.routes import _training_worker

    # Rows count iterations from one; best_iteration is zero-based.
    history = [{"iteration": float(n), "train_rmse": 1.0 / n} for n in range(1, 1001)]

    bounded, truncated = _training_worker._bounded_loss_history(history, best_iteration=660)

    assert truncated is True
    assert len(bounded) == _training_worker._max_train_loss_history() == 200
    iterations = [row["iteration"] for row in bounded]
    assert iterations == sorted(set(iterations))
    assert {1.0, 661.0, 1000.0} <= set(iterations)
    # An even stride: no gap between kept rows is more than twice the average.
    gaps = [later - earlier for earlier, later in zip(iterations, iterations[1:], strict=False)]
    assert max(gaps) <= 2 * (999 / 199)


def test_bounded_loss_history_keeps_a_short_history_whole() -> None:
    from haute.routes import _training_worker

    history = [{"iteration": float(n), "train_rmse": 1.0} for n in range(1, 201)]

    assert _training_worker._bounded_loss_history(history, best_iteration=None) == (history, False)


class TestTrainStatusEndpoint:
    def test_missing_job_returns_404(self, client):
        resp = client.get("/api/modelling/train/status/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "remote_traceback",
        ["Traceback (most recent call last):\n  ImportError: /opt/lib/engine.so\n", None],
    )
    @pytest.mark.parametrize(
        ("job_type", "route"),
        [("training", "train/status"), ("dispersion_estimate", "dispersion/status")],
    )
    def test_a_failed_job_returns_its_workers_traceback(
        self, client, remote_traceback, job_type, route
    ):
        """The failure message says the full error is in the job's error details: the
        traceback the supervisor recorded is those details."""
        from haute.routes._job_lifecycle import JobLifecycle
        from haute.routes.modelling import _store

        job_id = _store.create_job({"status": "running", "job_type": job_type})
        fields = {} if remote_traceback is None else {"worker_remote_traceback": remote_traceback}
        JobLifecycle(_store).transition(
            job_id, to="error", message="Training failed", fields=fields
        )
        try:
            resp = client.get(f"/api/modelling/{route}/{job_id}")
            assert resp.status_code == 200
            assert resp.json()["worker_remote_traceback"] == remote_traceback
        finally:
            _store.delete_job(job_id)

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

        from haute.routes._training_worker import _assert_json_finite

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
            join_training_preparation(data["job_id"])
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
            join_training_preparation(data["job_id"])
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
            join_training_preparation(data["job_id"])
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
            join_training_preparation(data["job_id"])
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
            unbounded_join_node_ids=(),
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
                join_training_preparation(data["job_id"])
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
            unbounded_join_node_ids=(),
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
                join_training_preparation(data["job_id"])
                launched[0].join_and_raise(timeout=10)
                status = client.get(f"/api/modelling/train/status/{data['job_id']}").json()
                # RAM warning should be suppressed since user limit (30) < RAM limit (50)
                assert status.get("warning") is None


def test_downsampling_warning_comes_from_the_rows_training_prepared(tmp_path) -> None:
    """The RAM limit warns only when it removed rows, and never claims a join's product."""
    from haute._types import PipelineGraph
    from haute.routes._training_lifecycle import _downsampling_warning

    graph = PipelineGraph.model_validate(
        _make_joined_modelling_graph(tmp_path, rows=10, validate=None)
    )

    def estimate(unbounded: tuple[str, ...]) -> RamEstimate:
        return RamEstimate(
            safe_row_limit=500,
            total_rows=40_000,
            estimated_bytes=10**9,
            available_bytes=2 * 1024**3,
            bytes_per_row=100.0,
            was_downsampled=not unbounded,
            warning=None if unbounded else "Dataset downsampled to 500 of 40,000 rows",
            unbounded_join_node_ids=unbounded,
        )

    unproven = estimate(("competitor_join",))
    # Fewer rows than the limit arrived: nothing was removed.
    assert _downsampling_warning(graph, unproven, 200) is None
    assert _downsampling_warning(graph, unproven, 500) == (
        "Dataset downsampled to 500 rows to fit in available RAM (2.0 GB). Its row count is "
        "not proven: 'competitor_join' has no key contract, so up to 40,000 rows could arrive."
    )
    assert _downsampling_warning(graph, estimate(()), 500) == (
        "Dataset downsampled to 500 of 40,000 rows"
    )


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

        with pytest.raises(RequestRefusedError) as exc_info:
            await train_status("nonexistent_job_id")
        assert exc_info.value.status_code == 404
