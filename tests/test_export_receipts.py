"""Export receipts, idempotent MLflow logging, and single-flight logs (MLF-E06)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from haute.modelling._feature_contract import build_contract, save_contract
from haute.schemas import TrainResponse
from tests.training_artifacts_support import publish_trained_job

_JOB = "receipt_job"


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
def trained(tmp_path: Path, training_artifact_root: Path, monkeypatch: pytest.MonkeyPatch):
    from haute.routes.modelling import _store

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("haute.routes.modelling._get_project_root", lambda: project)
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
        _JOB,
        root=training_artifact_root,
        model_file=model_path,
        result=TrainResponse(
            status="completed",
            diagnostic_metrics={"rmse": 0.1},
            development_rows=10,
            diagnostics_set="development",
            evaluation=_evaluation(),
        ),
        config={"algorithm": "catboost"},
        node_label="freq",
    )
    yield project
    _store.delete_job(_JOB)


def _logged(run_id: str, tracking_uri: str = "file:///C:/proj/mlruns") -> SimpleNamespace:
    return SimpleNamespace(
        backend="local",
        experiment_name="freq",
        run_id=run_id,
        run_url=None,
        tracking_uri=tracking_uri,
    )


def _receipts(client: Any) -> dict[str, list[dict[str, Any]]]:
    response = client.get(f"/api/modelling/train/status/{_JOB}")
    assert response.status_code == 200, response.text
    return response.json()["export_receipts"]


class TestMlflowReceipts:
    def test_a_log_is_recorded_on_the_job_and_returned_by_status(
        self, client: Any, trained: Path
    ) -> None:
        assert _receipts(client) == {"mlflow": [], "model_files": []}
        with patch("haute.modelling._mlflow_log.log_experiment", return_value=_logged("run-1")):
            response = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": _JOB, "destination": "", "operation_id": "op-1"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["operation_id"] == "op-1" and body["run_id"] == "run-1"
        [receipt] = _receipts(client)["mlflow"]
        assert receipt["operation_id"] == "op-1"
        assert receipt["destination"] == ""
        assert receipt["experiment_name"] == "freq"
        assert receipt["run_id"] == "run-1"
        assert receipt["logged_at"] == body["logged_at"]

    def test_a_retry_of_the_same_operation_returns_the_recorded_run(
        self, client: Any, trained: Path
    ) -> None:
        body = {"job_id": _JOB, "operation_id": "op-retry"}
        with patch(
            "haute.modelling._mlflow_log.log_experiment", return_value=_logged("run-1")
        ) as log:
            first = client.post("/api/modelling/mlflow/log", json=body).json()
            retried = client.post("/api/modelling/mlflow/log", json=body).json()
        assert log.call_count == 1
        assert retried == first
        assert len(_receipts(client)["mlflow"]) == 1

    def test_a_new_operation_logs_again(self, client: Any, trained: Path) -> None:
        with patch(
            "haute.modelling._mlflow_log.log_experiment",
            side_effect=[_logged("run-1"), _logged("run-2")],
        ):
            client.post("/api/modelling/mlflow/log", json={"job_id": _JOB, "operation_id": "a"})
            client.post("/api/modelling/mlflow/log", json={"job_id": _JOB, "operation_id": "b"})
        assert [r["run_id"] for r in _receipts(client)["mlflow"]] == ["run-1", "run-2"]

    def test_a_second_log_of_the_same_job_while_one_runs_is_refused(
        self, client: Any, trained: Path
    ) -> None:
        from haute.routes._export_receipts import single_flight_mlflow_log

        with (
            single_flight_mlflow_log(_JOB),
            patch("haute.modelling._mlflow_log.log_experiment") as log,
        ):
            response = client.post(
                "/api/modelling/mlflow/log", json={"job_id": _JOB, "operation_id": "late"}
            )
        assert response.status_code == 409
        assert response.json()["detail"]["error_code"] == "mlflow_log_in_progress"
        log.assert_not_called()
        # The slot is released once the running log finishes.
        with patch("haute.modelling._mlflow_log.log_experiment", return_value=_logged("run-9")):
            again = client.post(
                "/api/modelling/mlflow/log", json={"job_id": _JOB, "operation_id": "late"}
            )
        assert again.status_code == 200

    def test_a_failed_log_records_nothing_and_frees_the_slot(
        self, client: Any, trained: Path
    ) -> None:
        with patch("haute.modelling._mlflow_log.log_experiment", side_effect=RuntimeError("x")):
            failed = client.post(
                "/api/modelling/mlflow/log", json={"job_id": _JOB, "operation_id": "op"}
            )
        assert failed.status_code == 500
        assert _receipts(client)["mlflow"] == []
        with patch("haute.modelling._mlflow_log.log_experiment", return_value=_logged("run-3")):
            retried = client.post(
                "/api/modelling/mlflow/log", json={"job_id": _JOB, "operation_id": "op"}
            )
        assert retried.status_code == 200 and retried.json()["run_id"] == "run-3"

    def test_receipts_never_carry_uri_credentials(self, client: Any, trained: Path) -> None:
        secret_uri = "https://user:synthetic-secret@mlflow.example.test"
        with patch(
            "haute.modelling._mlflow_log.log_experiment",
            return_value=_logged("run-1", tracking_uri=secret_uri),
        ):
            client.post("/api/modelling/mlflow/log", json={"job_id": _JOB, "operation_id": "s"})
        assert "synthetic-secret" not in str(_receipts(client))

    def test_malformed_operation_ids_are_rejected(self, client: Any, trained: Path) -> None:
        response = client.post(
            "/api/modelling/mlflow/log", json={"job_id": _JOB, "operation_id": "a b/c"}
        )
        assert response.status_code == 422


class TestModelFileReceipts:
    def test_a_save_is_recorded_on_the_job(self, client: Any, trained: Path) -> None:
        response = client.post(
            "/api/modelling/save", json={"job_id": _JOB, "output_path": "frequency"}
        )
        assert response.status_code == 200, response.text
        [receipt] = _receipts(client)["model_files"]
        assert receipt["path"] == "models/frequency.cbm"
        assert receipt["feature_contract_path"] == "models/frequency.feature_contract.json"
        assert receipt["saved_at"]

    def test_receipts_keep_only_the_newest(self, client: Any, trained: Path) -> None:
        from haute.routes._export_receipts import MAX_RECEIPTS_PER_KIND, record_receipt
        from haute.routes.modelling import _store

        for index in range(MAX_RECEIPTS_PER_KIND + 3):
            record_receipt(
                _store,
                _JOB,
                "model_files",
                {"path": f"models/{index}.cbm", "feature_contract_path": "c", "saved_at": "t"},
            )
        paths = [r["path"] for r in _receipts(client)["model_files"]]
        assert len(paths) == MAX_RECEIPTS_PER_KIND
        assert paths[-1] == f"models/{MAX_RECEIPTS_PER_KIND + 2}.cbm"
