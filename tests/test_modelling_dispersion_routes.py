"""The modelling ``/dispersion/estimate`` route.

A negative-binomial dispersion estimate runs as a background job, and its error, cancel and timeout
branches never strand the job in a wrong state or orphan its training data.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._ram_estimate import RamEstimate
from haute.errors import RequestRefusedError, UnprocessableError
from haute.routes._training_lifecycle import TrainService
from tests import _modelling_route_helpers
from tests._modelling_route_helpers import (
    _TERMINAL_JOB_STATUSES,
    _admitted_training_context_for_launch,
    _make_modelling_graph,
    _poll_until_done,
    _random_evaluation_config,
)
from tests._training_helpers import join_training_preparation
from tests.conftest import (
    make_edge,
    make_graph,
    make_ready_file_input_config,
)
from tests.job_store_support import seed_job

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
_fast_optional_training_diagnostics = _modelling_route_helpers._fast_optional_training_diagnostics
training_data = _modelling_route_helpers.training_data


if TYPE_CHECKING:
    from fastapi.testclient import TestClient


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
                "node_id": "train",
                "node_label": "train",
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
                "node_id": "train",
                "node_label": "train",
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
        with patch.object(
            TrainService,
            "_execute_and_sink",
            side_effect=UnprocessableError("missing column"),
        ):
            response = service.start_dispersion_estimate(body)
            join_training_preparation(response.job_id)
        job = store.require_job(response.job_id)
        assert job["status"] == "contract_error"
        assert job["http_status_code"] == 422
        assert "missing column" in job["message"]

    def test_start_keeps_only_role_columns_because_glm_ignores_catboost_levers(
        self,
        nb_training_data,
    ):
        """feature_columns is a tree-model lever: a GLM's sink keeps its role
        columns, projects nothing away, and reads its term columns through
        projection demand."""
        from haute._execution_context import ExecutionContext, ExecutionProfile
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(
            nb_training_data,
            feature_columns=["x1"],
            theta=1.5,
        )
        store, service = self._service()
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        captured: dict[str, object] = {}

        def capture_sink(*_args, project_to_keep_columns, keep_columns, **_kwargs):
            captured["project_to_keep_columns"] = project_to_keep_columns
            captured["keep_columns"] = keep_columns
            return "prepared.parquet"

        context = ExecutionContext(
            operation="dispersion_estimate",
            profile=ExecutionProfile.TRAINING_PREP,
        )
        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(
                service,
                "_estimate_ram",
                return_value=RamEstimate(
                    safe_row_limit=None,
                    total_rows=100,
                    estimated_bytes=1_000,
                    available_bytes=10**9,
                    bytes_per_row=10.0,
                    was_downsampled=False,
                    warning=None,
                    probe_columns=3,
                ),
            ),
            patch(
                "haute.routes._training_lifecycle.create_admitted_execution_context",
                return_value=context,
            ),
            patch.object(service, "_execute_and_sink", side_effect=capture_sink),
            patch.object(service, "_launch_dispersion_background", return_value=object()),
        ):
            response = service.start_dispersion_estimate(body)
            join_training_preparation(response.job_id)

        assert response.status == "started"
        assert captured["project_to_keep_columns"] is False
        assert captured["keep_columns"] == ["y"]

    def test_start_maps_unexpected_exception_to_error(self, nb_training_data):
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(nb_training_data)
        store, service = self._service()
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        with patch.object(
            TrainService,
            "_execute_and_sink",
            side_effect=RuntimeError("sink exploded"),
        ):
            response = service.start_dispersion_estimate(body)
            join_training_preparation(response.job_id)
        job = store.require_job(response.job_id)
        assert job["status"] == "error"
        # Curated as training's are: the raw text stays in the diagnostic field.
        assert job["message"].startswith("Dispersion estimation of ")
        assert "RuntimeError" in job["message"]
        assert "sink exploded" not in job["message"]
        assert "sink exploded" in job["error"]

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
        with patch.object(TrainService, "_execute_and_sink", side_effect=admission_error):
            response = service.start_dispersion_estimate(body)
            join_training_preparation(response.job_id)
        job = store.require_job(response.job_id)
        assert job["status"] == "memory_limited"
        assert job["http_status_code"] == 507

    def test_a_failure_before_admission_is_curated_and_frees_the_job(self, nb_training_data):
        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(nb_training_data)
        store, service = self._service()
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        from haute.routes import _training_lifecycle

        admitted: list[str] = []
        admit = _training_lifecycle.create_admitted_execution_context

        def recording(**kwargs: Any) -> Any:
            admitted.append(kwargs["operation"])
            return admit(**kwargs)

        with (
            patch.object(service, "_estimate_ram", side_effect=RuntimeError("probe exploded")),
            patch.object(_training_lifecycle, "create_admitted_execution_context", recording),
        ):
            response = service.start_dispersion_estimate(body)
            join_training_preparation(response.job_id)

        job = store.require_job(response.job_id)
        assert job["status"] == "error"
        assert job["message"].startswith("Dispersion estimation of ")
        assert "probe exploded" in job["error"]
        assert "dispersion_estimate" not in admitted
        assert service._training_jobs.cancel(response.job_id) is False

    def test_start_returns_the_job_before_preparation_and_cancel_reaches_it(self, nb_training_data):
        """The id comes back at once; cancelling stops preparation and frees its slot."""
        import threading

        from haute.schemas import DispersionEstimateRequest

        graph = _make_negbinomial_graph(nb_training_data)
        store, service = self._service()
        body = DispersionEstimateRequest.model_validate(
            {"graph": graph, "node_id": "train", "param": "theta"}
        )
        released: list[bool] = []
        tokens: list[object] = []

        def admitted(**kwargs: object) -> ExecutionContext:
            if kwargs["operation"] != "dispersion_estimate":
                # The synchronous input-schema gate admits its own context.
                return ExecutionContext(
                    operation=str(kwargs["operation"]), profile=ExecutionProfile.TRAINING_PREP
                )
            tokens.append(kwargs["cancellation_token"])
            return ExecutionContext(
                operation="dispersion_estimate",
                profile=ExecutionProfile.TRAINING_PREP,
                cancellation_token=kwargs["cancellation_token"],  # type: ignore[arg-type]
                admission_release=lambda: released.append(True),
            )

        preparation_entered = threading.Event()
        release_preparation = threading.Event()

        def blocked_sink(*_args: object, execution_context: ExecutionContext, **_kwargs: object):
            preparation_entered.set()
            assert release_preparation.wait(timeout=5)
            execution_context.checkpoint(label="dispersion_materialisation")
            raise AssertionError("cancelled materialisation continued")

        with (
            patch.object(service, "_estimate_ram", return_value=_ram_estimate()),
            patch(
                "haute.routes._training_lifecycle.create_admitted_execution_context",
                side_effect=admitted,
            ),
            patch("haute.routes._training_lifecycle.bind_running_execution_metrics_publisher"),
            patch.object(service, "_execute_and_sink", side_effect=blocked_sink),
        ):
            response = service.start_dispersion_estimate(body)
            assert response.status == "started"
            assert preparation_entered.wait(timeout=5)
            assert service.cancel_dispersion(response.job_id)["status"] == "cancelled"
            assert tokens and tokens[0].cancelled  # type: ignore[attr-defined]
            release_preparation.set()
            join_training_preparation(response.job_id)

        assert store.require_job(response.job_id)["status"] == "cancelled"
        assert released == [True]
        assert service._training_jobs.cancel(response.job_id) is False

    def test_dispersion_timeout_marks_the_job_timed_out_and_late_completion_cannot_revive_it(
        self, client, nb_training_data, monkeypatch
    ):
        from haute._worker_failures import WorkerTimeoutError
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
            raise WorkerTimeoutError(timeout_seconds=60.0)

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
            join_training_preparation(resp.job_id)

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
        with pytest.raises(RequestRefusedError) as exc_info:
            service._validate_dispersion_config(dict(_NB_ESTIMATION_CONFIG), "alpha")
        assert exc_info.value.status_code == 400
        assert "Unknown dispersion parameter" in exc_info.value.detail

    def test_validate_rejects_missing_target(self):
        _, service = self._service()
        config = {**_NB_ESTIMATION_CONFIG, "target": ""}
        with pytest.raises(RequestRefusedError) as exc_info:
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
                "node_id": "train",
                "node_label": "train",
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
            pytest.raises(RequestRefusedError) as exc_info,
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
# Direct route function tests — bypasses TestClient for coverage
# ---------------------------------------------------------------------------


def _ram_estimate() -> RamEstimate:
    """A RAM estimate that limits nothing."""
    return RamEstimate(
        safe_row_limit=None,
        total_rows=100,
        estimated_bytes=1_000,
        available_bytes=10**9,
        bytes_per_row=10.0,
        was_downsampled=False,
        warning=None,
        probe_columns=3,
    )
