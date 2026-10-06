"""MLflow failure classification and the log routes' HTTP outcomes (MLF-E02)."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from mlflow.exceptions import MlflowException, RestException

from haute._mlflow_errors import (
    MLFLOW_LOG_FAILURE_MESSAGES,
    MlflowRemoteError,
    classify_mlflow_error,
)
from haute.modelling._feature_contract import build_contract, save_contract
from haute.schemas import TrainResponse
from tests.job_store_support import seed_job
from tests.optimiser_fixtures import make_solved_result, use_local_mlflow_store
from tests.training_artifacts_support import publish_trained_job


def _http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status}", response=response)


def _wrapped(cause: BaseException) -> MlflowException:
    wrapper = MlflowException("API request to https://host/api failed")
    wrapper.__cause__ = cause
    return wrapper


class TestClassifyMlflowError:
    @pytest.mark.parametrize(
        ("error_code", "category"),
        [
            ("UNAUTHENTICATED", "authentication"),
            ("PERMISSION_DENIED", "permission"),
            ("RESOURCE_DOES_NOT_EXIST", "missing_resource"),
            ("INVALID_PARAMETER_VALUE", "unknown"),
        ],
    )
    def test_rest_error_codes(self, error_code: str, category: str) -> None:
        exc = RestException({"error_code": error_code, "message": "denied token=dapi-secret"})
        assert classify_mlflow_error(exc) == category

    @pytest.mark.parametrize(
        ("status", "category"),
        [(401, "authentication"), (403, "permission"), (404, "missing_resource"), (500, "unknown")],
    )
    def test_http_statuses_behind_an_mlflow_wrapper(self, status: int, category: str) -> None:
        assert classify_mlflow_error(_wrapped(_http_error(status))) == category

    @pytest.mark.parametrize(
        "transport",
        [
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.ReadTimeout("slow"),
            ConnectionRefusedError("refused"),
            TimeoutError("deadline"),
            socket.gaierror("name resolution"),
        ],
    )
    def test_transport_failures_are_connectivity(self, transport: BaseException) -> None:
        assert classify_mlflow_error(transport) == "connectivity"
        assert classify_mlflow_error(_wrapped(transport)) == "connectivity"

    @pytest.mark.parametrize(
        "local",
        [PermissionError("mlruns is read-only"), FileNotFoundError("mlruns"), OSError("disk full")],
    )
    def test_a_local_disk_failure_is_never_connectivity(self, local: BaseException) -> None:
        assert classify_mlflow_error(local) == "unknown"
        assert classify_mlflow_error(_wrapped(local)) == "unknown"

    def test_a_classified_haute_error_keeps_its_category(self) -> None:
        assert classify_mlflow_error(MlflowRemoteError("permission", "no")) == "permission"

    def test_unrecognised_failures_are_unknown(self) -> None:
        assert classify_mlflow_error(RuntimeError("boom")) == "unknown"
        assert classify_mlflow_error(MlflowException("bare")) == "unknown"

    def test_classifies_without_raising_when_mlflow_is_a_stub(self) -> None:
        """An error handler must not fail because ``mlflow.exceptions`` cannot import."""
        import sys

        with patch.dict(sys.modules, {"mlflow": MagicMock()}):
            sys.modules.pop("mlflow.exceptions", None)
            assert classify_mlflow_error(RuntimeError("summary boom")) == "unknown"
            assert classify_mlflow_error(ConnectionRefusedError("refused")) == "connectivity"
            assert classify_mlflow_error(_http_error(403)) == "permission"

    def test_classifies_without_requests(self) -> None:
        import sys

        with patch.dict(sys.modules, {"requests": None}):
            assert classify_mlflow_error(TimeoutError("deadline")) == "connectivity"
            assert classify_mlflow_error(RestException({"error_code": "PERMISSION_DENIED"})) == (
                "permission"
            )


# ---------------------------------------------------------------------------
# Log routes
# ---------------------------------------------------------------------------


def _evaluation() -> dict[str, object]:
    return {
        "schema_version": 1,
        "strategy": "random",
        "validation_method": "none",
        "validation_fit_count": 0,
        "fit_count": 1,
        "development_rows": 10,
        "final_test_rows": 0,
        "selection_fits": [],
        "selection_metrics": {},
        "plan_sha256": "a" * 64,
        "results_sha256": "b" * 64,
        "plan_path": "plan.json",
        "results_path": "results.json",
        "report_path": "report.json",
        "summary": {"development_rows": 10, "test_rows": 0, "validation_fit_count": 0},
    }


@pytest.fixture()
def published_training_job(tmp_path: Path, training_artifact_root: Path):
    """A completed training job whose snapshot names an old experiment."""
    from haute.routes.modelling import _store

    model_path = tmp_path / "trained" / "freq.cbm"
    model_path.parent.mkdir()
    model_path.write_bytes(b"model")
    save_contract(
        build_contract(
            features=["x"],
            feature_types={"x": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        ),
        model_path.with_name("freq.feature_contract.json"),
    )
    publish_trained_job(
        _store,
        "log_job",
        root=training_artifact_root,
        model_file=model_path,
        result=TrainResponse(
            status="completed",
            diagnostic_metrics={"rmse": 0.1},
            development_rows=10,
            diagnostics_set="development",
            evaluation=_evaluation(),
        ),
        config={"algorithm": "catboost", "mlflow_experiment": "/snapshot/at/training"},
        node_label="freq",
    )
    yield "log_job"
    _store.delete_job("log_job")


def _log_training(client, body: dict[str, object], **patch_kwargs: object):
    with patch("haute.modelling._mlflow_log.log_experiment", **patch_kwargs) as log:
        response = client.post("/api/modelling/mlflow/log", json=body)
    return response, log


class TestModellingLogRoute:
    def test_a_cleared_experiment_logs_to_the_default_not_the_training_snapshot(
        self, client, published_training_job: str
    ) -> None:
        result = SimpleNamespace(
            backend="local",
            experiment_name="freq",
            run_id="r",
            run_url=None,
            tracking_uri="file:/x",
        )
        response, log = _log_training(
            client,
            {"job_id": published_training_job, "experiment_name": None, "destination": "local"},
            return_value=result,
        )
        assert response.status_code == 200, response.text
        assert log.call_args.kwargs["experiment_name"] == "freq"

    @pytest.mark.parametrize(
        ("failure", "category"),
        [
            (_wrapped(requests.exceptions.ConnectionError("refused dapi-secret")), "connectivity"),
            (
                RestException({"error_code": "UNAUTHENTICATED", "message": "dapi-secret"}),
                "authentication",
            ),
            (
                RestException({"error_code": "PERMISSION_DENIED", "message": "dapi-secret"}),
                "permission",
            ),
            (
                RestException({"error_code": "RESOURCE_DOES_NOT_EXIST", "message": "x"}),
                "missing_resource",
            ),
        ],
    )
    def test_classified_remote_failures_are_502_with_a_code_and_write_copy(
        self, client, published_training_job: str, failure: BaseException, category: str
    ) -> None:
        with patch("haute.routes._mlflow_log_errors.logger.error") as log_error:
            response, _ = _log_training(
                client,
                {"job_id": published_training_job, "destination": "local"},
                side_effect=failure,
            )
        assert response.status_code == 502
        assert response.json()["detail"] == {
            "error_code": f"mlflow_{category}",
            "message": MLFLOW_LOG_FAILURE_MESSAGES[category],
        }
        assert "dapi-secret" not in response.text
        assert log_error.call_args.kwargs["category"] == category
        assert "dapi-secret" not in str(log_error.call_args)

    def test_a_folder_refusal_reports_haute_s_own_message(
        self, client, published_training_job: str
    ) -> None:
        refusal = MlflowRemoteError("permission", "MLflow denied permission to create /Shared/x.")
        response, _ = _log_training(
            client, {"job_id": published_training_job, "destination": "local"}, side_effect=refusal
        )
        assert response.status_code == 502
        assert response.json()["detail"] == {
            "error_code": "mlflow_permission",
            "message": "MLflow denied permission to create /Shared/x.",
        }

    def test_a_local_disk_failure_is_the_generic_500(
        self, client, published_training_job: str
    ) -> None:
        response, _ = _log_training(
            client,
            {"job_id": published_training_job, "destination": "local"},
            side_effect=PermissionError("C:/private/mlruns is read-only"),
        )
        assert response.status_code == 500
        assert "private" not in response.text
        assert "connect" not in response.text.lower()

    def test_an_unloadable_model_is_a_400_before_any_run(
        self, client, published_training_job: str
    ) -> None:
        from haute.errors import HauteValidationError

        response, _ = _log_training(
            client,
            {"job_id": published_training_job, "destination": "local"},
            side_effect=HauteValidationError("The trained CatBoost model file could not be loaded"),
        )
        assert response.status_code == 400
        assert "could not be loaded" in response.json()["detail"]

    def test_missing_mlflow_is_the_shared_503(self, client, published_training_job: str) -> None:
        with patch.dict("sys.modules", {"mlflow": None}):
            response = client.post(
                "/api/modelling/mlflow/log", json={"job_id": published_training_job}
            )
        assert response.status_code == 503
        assert response.json()["detail"] == (
            "MLflow is not installed. Install it with: pip install mlflow"
        )


class TestOptimiserLogRoute:
    @staticmethod
    def _seed(store) -> None:
        seed_job(
            store,
            "opt_job",
            {
                "status": "completed",
                "result": make_solved_result(
                    total_objective=1.0, baseline_objective=1.0, constraint_names=[]
                ),
                "publish_summary": {"params": {}, "metrics": {}, "artifacts": {}},
                "config": {"mode": "online", "mlflow_experiment": "/snapshot/at/solve"},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

    def test_a_cleared_experiment_logs_to_the_default_not_the_solve_snapshot(
        self, client, clean_job_store, tmp_path, monkeypatch
    ) -> None:
        self._seed(clean_job_store)
        store = use_local_mlflow_store(tmp_path, monkeypatch)
        with patch("haute.routes.optimiser._build_artifact_payload", return_value={}):
            response = client.post(
                "/api/optimiser/mlflow/log", json={"job_id": "opt_job", "experiment_name": ""}
            )
        assert response.status_code == 200, response.text
        assert response.json()["experiment_name"] == "opt"
        run = store.get_run(response.json()["run_id"])
        assert store.get_experiment(run.info.experiment_id).name == "opt"
        assert store.get_experiment_by_name("/snapshot/at/solve") is None

    def test_a_connection_failure_is_classified_like_the_training_route(
        self, client, clean_job_store
    ) -> None:
        self._seed(clean_job_store)
        with (
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("http://mlflow.invalid", "server"),
            ),
            patch(
                "haute.routes.optimiser.ensure_experiment",
                side_effect=_wrapped(requests.exceptions.ConnectionError("refused")),
            ),
        ):
            response = client.post("/api/optimiser/mlflow/log", json={"job_id": "opt_job"})
        assert response.status_code == 502
        assert response.json()["detail"]["error_code"] == "mlflow_connectivity"
