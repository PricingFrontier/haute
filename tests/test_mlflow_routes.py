"""API integration tests for MLflow discovery endpoints.

Covers:
  - GET /api/mlflow/experiments: success, empty list, not installed (503),
    connection error (502)
  - GET /api/mlflow/runs: cbm filter, optimiser filter, no artifacts excluded,
    empty runs, artifact list failure (graceful skip), connection error (502),
    missing experiment_id (422)
  - GET /api/mlflow/models: success, empty, no latest_versions,
    connection error (502)
  - GET /api/mlflow/model-versions: sorted descending, missing model_name (422),
    connection error (502), version with missing optional fields,
    special characters in model name, pagination page_token
"""

from __future__ import annotations

import os
import sys
import types
from math import isfinite
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing
from fastapi import HTTPException


def _mock_tracking(mlflow=None, client=None):
    """Patch ``_ensure_tracking`` to return ``(mlflow, client)``."""
    mlflow = mlflow or MagicMock()
    client = client or MagicMock()
    return patch("haute.routes.mlflow._ensure_tracking", return_value=(mlflow, client))


def _make_run(
    run_id: str = "run1",
    run_name: str = "test-run",
    start_time: int = 1000,
    metrics: dict | None = None,
    params: dict | None = None,
) -> MagicMock:
    """Build a mock MLflow Run object."""
    run = MagicMock()
    run.info.run_id = run_id
    run.info.run_name = run_name
    run.info.status = "FINISHED"
    run.info.start_time = start_time
    run.data.metrics = metrics or {}
    run.data.params = params or {}
    return run


# ---------------------------------------------------------------------------
# GET /api/mlflow/experiments
# ---------------------------------------------------------------------------


class TestListExperiments:
    def test_list_experiments(self, client):
        """Returns list of experiments from MLflow."""

        class FakeExp:
            experiment_id = "1"
            name = "test-exp"

        mock_mlflow = MagicMock()
        mock_mlflow.search_experiments.return_value = [FakeExp()]

        with _mock_tracking(mlflow=mock_mlflow):
            resp = client.get("/api/mlflow/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["experiment_id"] == "1"
        assert data[0]["name"] == "test-exp"

    def test_empty_experiments(self, client):
        """Returns empty list when no experiments exist."""
        mock_mlflow = MagicMock()
        mock_mlflow.search_experiments.return_value = []

        with _mock_tracking(mlflow=mock_mlflow):
            resp = client.get("/api/mlflow/experiments")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_mlflow_not_installed_503(self, client):
        """Returns 503 when mlflow is not installed."""
        with patch(
            "haute.routes.mlflow._ensure_tracking",
            side_effect=HTTPException(status_code=503, detail="not installed"),
        ):
            resp = client.get("/api/mlflow/experiments")

        assert resp.status_code == 503

    def test_connection_error_502(self, client):
        """Returns 502 when MLflow tracking server is unreachable."""
        mock_mlflow = MagicMock()
        mock_mlflow.search_experiments.side_effect = ConnectionError("refused")

        with _mock_tracking(mlflow=mock_mlflow):
            resp = client.get("/api/mlflow/experiments")

        assert resp.status_code == 502
        assert "Check the server logs" in resp.json()["detail"]

    def test_multiple_experiments(self, client):
        """Returns multiple experiments in correct structure."""

        class Exp1:
            experiment_id = "1"
            name = "pricing"

        class Exp2:
            experiment_id = "2"
            name = "scoring"

        mock_mlflow = MagicMock()
        mock_mlflow.search_experiments.return_value = [Exp1(), Exp2()]

        with _mock_tracking(mlflow=mock_mlflow):
            resp = client.get("/api/mlflow/experiments")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {d["name"] for d in data}
        assert names == {"pricing", "scoring"}


# ---------------------------------------------------------------------------
# GET /api/mlflow/runs
# ---------------------------------------------------------------------------


class TestListRuns:
    @staticmethod
    def _discovery_measurement(logs: list[dict]) -> dict:
        events = [
            record for record in logs if record.get("event") == "mlflow_run_discovery_completed"
        ]
        assert len(events) == 1
        return events[0]

    @staticmethod
    def _assert_safe_measurement(
        measurement: dict,
        *,
        forbidden_values: set[str],
    ) -> None:
        expected_fields = {
            "outcome",
            "max_results",
            "search_calls",
            "artifact_calls",
            "runs_scanned",
            "runs_returned",
            "artifact_failures",
            "search_ms",
            "artifact_ms",
            "assembly_ms",
            "total_ms",
        }
        forbidden_fields = {
            "experiment_id",
            "run_id",
            "artifact_path",
            "artifacts",
            "params",
            "metrics",
            "error",
            "exception",
        }

        assert expected_fields <= measurement.keys()
        assert not (forbidden_fields & measurement.keys())
        assert not (forbidden_values & set(measurement.values()))
        for field in {"search_ms", "artifact_ms", "assembly_ms", "total_ms"}:
            assert isinstance(measurement[field], float)
            assert isfinite(measurement[field])
            assert measurement[field] >= 0

    def test_run_discovery_measurement_for_max_cardinality_success(self, client):
        """A 100-run search emits one aggregate, payload-free measurement."""
        runs = [_make_run(run_id=f"secret-run-{index}") for index in range(100)]
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = runs
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [MagicMock(path="secret-model.cbm")]

        with (
            _mock_tracking(mlflow=mock_mlflow, client=mock_client),
            structlog.testing.capture_logs() as logs,
        ):
            resp = client.get("/api/mlflow/runs?experiment_id=secret-experiment&max_results=100")

        assert resp.status_code == 200
        measurement = self._discovery_measurement(logs)
        assert {
            field: measurement[field]
            for field in {
                "outcome",
                "max_results",
                "search_calls",
                "artifact_calls",
                "runs_scanned",
                "runs_returned",
                "artifact_failures",
            }
        } == {
            "outcome": "success",
            "max_results": 100,
            "search_calls": 1,
            "artifact_calls": 100,
            "runs_scanned": 100,
            "runs_returned": 100,
            "artifact_failures": 0,
        }
        self._assert_safe_measurement(
            measurement,
            forbidden_values={"secret-experiment", "secret-run-0", "secret-model.cbm"},
        )

    def test_run_discovery_measurement_counts_partial_artifact_failure(self, client):
        """Artifact failures are counted while the successful run is returned."""
        good_run = _make_run(run_id="secret-good-run")
        broken_run = _make_run(run_id="secret-broken-run")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [good_run, broken_run]
        mock_client = MagicMock()
        mock_client.list_artifacts.side_effect = [
            [MagicMock(path="secret-good-model.cbm")],
            RuntimeError("secret artifact failure"),
        ]

        with (
            _mock_tracking(mlflow=mock_mlflow, client=mock_client),
            structlog.testing.capture_logs() as logs,
        ):
            resp = client.get("/api/mlflow/runs?experiment_id=secret-experiment")

        assert resp.status_code == 200
        measurement = self._discovery_measurement(logs)
        assert {
            field: measurement[field]
            for field in {
                "outcome",
                "max_results",
                "search_calls",
                "artifact_calls",
                "runs_scanned",
                "runs_returned",
                "artifact_failures",
            }
        } == {
            "outcome": "success",
            "max_results": 20,
            "search_calls": 1,
            "artifact_calls": 2,
            "runs_scanned": 2,
            "runs_returned": 1,
            "artifact_failures": 1,
        }
        self._assert_safe_measurement(
            measurement,
            forbidden_values={
                "secret-experiment",
                "secret-good-run",
                "secret-broken-run",
                "secret-good-model.cbm",
                "secret artifact failure",
            },
        )

    def test_run_discovery_measurement_emitted_after_search_failure(self, client):
        """A failed search still emits exactly one safe measurement."""
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.side_effect = RuntimeError("secret search failure")

        with (
            _mock_tracking(mlflow=mock_mlflow),
            structlog.testing.capture_logs() as logs,
        ):
            resp = client.get("/api/mlflow/runs?experiment_id=secret-experiment")

        assert resp.status_code == 502
        measurement = self._discovery_measurement(logs)
        assert {
            field: measurement[field]
            for field in {
                "outcome",
                "max_results",
                "search_calls",
                "artifact_calls",
                "runs_scanned",
                "runs_returned",
                "artifact_failures",
            }
        } == {
            "outcome": "search_failed",
            "max_results": 20,
            "search_calls": 1,
            "artifact_calls": 0,
            "runs_scanned": 0,
            "runs_returned": 0,
            "artifact_failures": 0,
        }
        self._assert_safe_measurement(
            measurement,
            forbidden_values={"secret-experiment", "secret search failure"},
        )

    def test_run_discovery_measurement_emitted_after_processing_failure(self):
        """An unexpected response-build failure still closes the measurement."""

        class BrokenInfo:
            run_id = "secret-run"

            @property
            def run_name(self):
                raise RuntimeError("secret response-build failure")

            status = "FINISHED"
            start_time = 1

        broken_run = MagicMock()
        broken_run.info = BrokenInfo()
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [broken_run]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [MagicMock(path="secret-model.cbm")]

        from haute.routes.mlflow import list_runs

        with (
            _mock_tracking(mlflow=mock_mlflow, client=mock_client),
            structlog.testing.capture_logs() as logs,
            pytest.raises(RuntimeError, match="secret response-build failure"),
        ):
            list_runs("secret-experiment", 1, "model")

        measurement = self._discovery_measurement(logs)
        assert measurement["outcome"] == "processing_failed"
        assert measurement["search_calls"] == 1
        assert measurement["artifact_calls"] == 1
        assert measurement["runs_scanned"] == 1
        assert measurement["runs_returned"] == 0
        self._assert_safe_measurement(
            measurement,
            forbidden_values={
                "secret-experiment",
                "secret-run",
                "secret-model.cbm",
                "secret response-build failure",
            },
        )

    def test_list_runs_filters_cbm(self, client):
        """Only returns runs with .cbm artifacts."""
        run1 = _make_run(metrics={"rmse": 0.5}, params={"lr": "0.05"})

        cbm_art = MagicMock(path="model.cbm")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run1]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [cbm_art]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["run_id"] == "run1"
        assert "model.cbm" in data[0]["artifacts"]

    def test_runs_without_cbm_excluded(self, client):
        """Runs without .cbm artifacts are excluded."""
        run1 = _make_run(run_name="no-model")

        txt_art = MagicMock(path="readme.txt")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run1]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [txt_art]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_optimiser_artifact_filter(self, client):
        """artifact_filter=optimiser matches optimiser_result.json."""
        run1 = _make_run(run_id="opt_run", run_name="opt-run")

        opt_art = MagicMock(path="optimiser_result.json")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run1]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [opt_art]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get(
                "/api/mlflow/runs?experiment_id=1&artifact_filter=optimiser",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["run_id"] == "opt_run"
        assert "optimiser_result.json" in data[0]["artifacts"]

    def test_optimiser_filter_excludes_cbm(self, client):
        """When artifact_filter=optimiser, .cbm files are not matched."""
        run1 = _make_run()

        cbm_art = MagicMock(path="model.cbm")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run1]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [cbm_art]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get(
                "/api/mlflow/runs?experiment_id=1&artifact_filter=optimiser",
            )

        assert resp.status_code == 200
        assert resp.json() == []

    def test_empty_runs(self, client):
        """Empty experiment returns empty list."""
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = []

        with _mock_tracking(mlflow=mock_mlflow):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_artifact_list_failure_skips_run(self, client):
        """If listing artifacts fails for a run, that run is skipped."""
        run1 = _make_run(run_id="good")
        run2 = _make_run(run_id="broken")

        cbm_art = MagicMock(path="model.cbm")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run1, run2]
        mock_client = MagicMock()
        # First call succeeds, second fails
        mock_client.list_artifacts.side_effect = [
            [cbm_art],
            Exception("artifact store unavailable"),
        ]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        data = resp.json()
        # Only the good run should appear
        assert len(data) == 1
        assert data[0]["run_id"] == "good"

    def test_connection_error_502(self, client):
        """search_runs failure returns 502."""
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.side_effect = ConnectionError("timeout")

        with _mock_tracking(mlflow=mock_mlflow):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 502
        assert "Check the server logs" in resp.json()["detail"]

    def test_missing_experiment_id_422(self, client):
        """Missing required experiment_id returns 422."""
        with _mock_tracking():
            resp = client.get("/api/mlflow/runs")

        assert resp.status_code == 422

    def test_run_response_shape(self, client):
        """Verify the complete response shape of a successful run."""
        run = _make_run(
            run_id="full",
            run_name="full-run",
            start_time=1700000000,
            metrics={"rmse": 0.1, "mae": 0.05},
            params={"epochs": "100", "lr": "0.01"},
        )
        cbm = MagicMock(path="best.cbm")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [cbm]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        data = resp.json()[0]
        assert data["run_id"] == "full"
        assert data["run_name"] == "full-run"
        assert data["status"] == "FINISHED"
        assert data["start_time"] == 1700000000
        assert data["metrics"]["rmse"] == 0.1
        assert data["params"]["epochs"] == "100"
        assert data["artifacts"] == ["best.cbm"]

    def test_rsglm_artifact_included(self, client):
        """Runs with .rsglm artifacts are included (regression: was .cbm only)."""
        run = _make_run(run_id="glm_run", run_name="glm-run")

        rsglm_art = MagicMock(path="fitted.rsglm")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [rsglm_art]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["run_id"] == "glm_run"
        assert "fitted.rsglm" in data[0]["artifacts"]

    def test_model_filter_matches_cbm_and_rsglm_excludes_other(self, client):
        """Default model filter includes .cbm and .rsglm runs, excludes others.

        Regression test: the _match() helper in list_runs() must accept
        both .cbm and .rsglm extensions.  Previously only .cbm was matched.
        """
        run_cbm = _make_run(run_id="cbm_run", run_name="cbm")
        run_rsglm = _make_run(run_id="rsglm_run", run_name="rsglm")
        run_txt = _make_run(run_id="txt_run", run_name="txt")

        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run_cbm, run_rsglm, run_txt]
        mock_client = MagicMock()
        mock_client.list_artifacts.side_effect = [
            [MagicMock(path="model.cbm")],
            [MagicMock(path="model.rsglm")],
            [MagicMock(path="notes.txt")],
        ]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        data = resp.json()
        returned_ids = {d["run_id"] for d in data}
        assert returned_ids == {"cbm_run", "rsglm_run"}
        assert "txt_run" not in returned_ids

    def test_max_results_forwarded(self, client):
        """max_results query param is forwarded to search_runs."""
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = []

        with _mock_tracking(mlflow=mock_mlflow):
            resp = client.get("/api/mlflow/runs?experiment_id=1&max_results=5")

        assert resp.status_code == 200
        mock_mlflow.search_runs.assert_called_once_with(
            experiment_ids=["1"],
            filter_string="status = 'FINISHED'",
            max_results=5,
            output_format="list",
        )


# ---------------------------------------------------------------------------
# GET /api/mlflow/models
# ---------------------------------------------------------------------------


class TestListModels:
    def test_list_models(self, client):
        """Returns list of registered models."""
        model = MagicMock()
        model.name = "my-model"
        v = MagicMock(version="1", status="READY", run_id="run1")
        model.latest_versions = [v]

        mock_client = MagicMock()
        mock_client.search_registered_models.return_value = [model]

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/models")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "my-model"
        assert data[0]["latest_versions"][0]["version"] == "1"

    def test_empty_models(self, client):
        """Returns empty list when no registered models exist."""
        mock_client = MagicMock()
        mock_client.search_registered_models.return_value = []

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/models")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_model_without_versions(self, client):
        """Model with no latest_versions returns empty array."""
        model = MagicMock()
        model.name = "empty-model"
        model.latest_versions = None

        mock_client = MagicMock()
        mock_client.search_registered_models.return_value = [model]

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/models")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "empty-model"
        assert data[0]["latest_versions"] == []

    def test_connection_error_502(self, client):
        """search_registered_models failure returns 502."""
        mock_client = MagicMock()
        mock_client.search_registered_models.side_effect = ConnectionError("timeout")

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/models")

        assert resp.status_code == 502
        assert "Check the server logs" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/mlflow/model-versions
# ---------------------------------------------------------------------------


class TestListModelVersions:
    def test_list_model_versions(self, client):
        """Returns sorted versions of a model."""
        v1 = MagicMock(
            version="1", run_id="r1", status="READY", creation_timestamp=100, description="first"
        )
        v2 = MagicMock(
            version="2", run_id="r2", status="READY", creation_timestamp=200, description="second"
        )

        mock_client = MagicMock()
        mock_client.search_model_versions.return_value = [v1, v2]

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=my-model")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Should be sorted descending by version
        assert data[0]["version"] == "2"
        assert data[1]["version"] == "1"

    def test_list_model_versions_includes_backing_run_params(self, client):
        """Model versions include run params so optimiser mode can be discovered."""
        v = MagicMock(
            version="1",
            run_id="ratebook-run",
            status="READY",
            creation_timestamp=100,
            description="ratebook optimiser",
        )

        mock_client = MagicMock()
        mock_client.search_model_versions.return_value = [v]
        mock_client.get_run.return_value = _make_run(
            run_id="ratebook-run",
            params={"mode": "ratebook"},
        )

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=my-model")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["params"] == {"mode": "ratebook"}
        mock_client.get_run.assert_called_once_with("ratebook-run")

    def test_list_model_versions_keeps_versions_when_run_params_unavailable(self, client):
        """A params lookup failure should not hide registered model versions."""
        v = MagicMock(
            version="1",
            run_id="orphaned-run",
            status="READY",
            creation_timestamp=100,
            description="version with inaccessible run",
        )

        mock_client = MagicMock()
        mock_client.search_model_versions.return_value = [v]
        mock_client.get_run.side_effect = RuntimeError("run metadata unavailable")

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=my-model")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["version"] == "1"
        assert data[0]["params"] == {}
        mock_client.get_run.assert_called_once_with("orphaned-run")

    def test_sorting_with_many_versions(self, client):
        """Versions 1, 3, 2, 10 should sort as 10, 3, 2, 1."""
        versions = [
            MagicMock(
                version=str(n),
                run_id=f"r{n}",
                status="READY",
                creation_timestamp=n * 100,
                description="",
            )
            for n in [1, 3, 2, 10]
        ]

        mock_client = MagicMock()
        mock_client.search_model_versions.return_value = versions

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=my-model")

        assert resp.status_code == 200
        data = resp.json()
        assert [d["version"] for d in data] == ["10", "3", "2", "1"]

    def test_missing_model_name_422(self, client):
        """Returns 422 when model_name query param is missing."""
        resp = client.get("/api/mlflow/model-versions")
        assert resp.status_code == 422

    def test_connection_error_502(self, client):
        """search_model_versions failure returns 502."""
        mock_client = MagicMock()
        mock_client.search_model_versions.side_effect = ConnectionError("timeout")

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=my-model")

        assert resp.status_code == 502
        assert "Check the server logs" in resp.json()["detail"]

    def test_version_missing_optional_fields(self, client):
        """Versions with missing optional fields default gracefully."""
        v = MagicMock()
        v.version = "1"
        v.run_id = None  # No run_id
        v.status = "PENDING_REGISTRATION"
        v.creation_timestamp = None
        # Simulate missing description attribute
        del v.description

        mock_client = MagicMock()
        mock_client.search_model_versions.return_value = [v]

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=test-model")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["version"] == "1"
        assert data[0]["run_id"] == ""  # Defaults to empty string
        assert data[0]["description"] == ""  # getattr default

    def test_special_characters_in_model_name(self, client):
        """Model names with special characters are properly escaped in the query."""
        mock_client = MagicMock()
        mock_client.search_model_versions.return_value = []

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=my-model's")

        assert resp.status_code == 200
        # Verify the escaped query was sent
        mock_client.search_model_versions.assert_called_once_with(
            "name='my-model\\'s'",
        )

    def test_pagination_page_token(self, client):
        """page_token is forwarded to MLflow client."""
        mock_client = MagicMock()
        mock_client.search_registered_models.return_value = []

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/models?max_results=10&page_token=abc123")

        assert resp.status_code == 200
        mock_client.search_registered_models.assert_called_once_with(
            max_results=10,
            page_token="abc123",
        )


# ---------------------------------------------------------------------------
# _ensure_tracking — error paths
# ---------------------------------------------------------------------------


class TestEnsureTracking:
    def test_mlflow_not_installed_raises_503(self, client):
        """When mlflow is not installed, _ensure_tracking raises 503."""
        with patch(
            "haute.routes.mlflow._ensure_tracking",
            side_effect=HTTPException(status_code=503, detail="mlflow is not installed"),
        ):
            resp = client.get("/api/mlflow/experiments")
        assert resp.status_code == 503
        assert "mlflow" in resp.json()["detail"].lower()

    def test_tracking_backend_resolution_failure_502(self, client):
        """When tracking backend cannot be resolved, _ensure_tracking raises 502."""
        with patch(
            "haute.routes.mlflow._ensure_tracking",
            side_effect=HTTPException(status_code=502, detail="Cannot resolve tracking backend"),
        ):
            resp = client.get("/api/mlflow/experiments")
        assert resp.status_code == 502

    def test_503_propagates_to_all_endpoints(self, client):
        """503 from _ensure_tracking propagates to all MLflow endpoints."""
        side_effect = HTTPException(status_code=503, detail="not installed")

        for path in [
            "/api/mlflow/experiments",
            "/api/mlflow/runs?experiment_id=1",
            "/api/mlflow/models",
            "/api/mlflow/model-versions?model_name=test",
        ]:
            with patch("haute.routes.mlflow._ensure_tracking", side_effect=side_effect):
                resp = client.get(path)
            assert resp.status_code == 503, f"Expected 503 for {path}"


class TestEnsureTrackingDirect:
    def _fake_mlflow_modules(self):
        mlflow_mod = types.ModuleType("mlflow")
        mlflow_mod.set_tracking_uri = MagicMock()
        tracking_mod = types.ModuleType("mlflow.tracking")
        tracking_mod.MlflowClient = MagicMock()
        return mlflow_mod, tracking_mod

    def test_backend_resolution_failure_becomes_502(self):
        from haute.routes.mlflow import _ensure_tracking

        mlflow_mod, tracking_mod = self._fake_mlflow_modules()
        with (
            patch.dict(sys.modules, {"mlflow": mlflow_mod, "mlflow.tracking": tracking_mod}),
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                side_effect=RuntimeError("tracking backend misconfigured"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_tracking()

        assert exc_info.value.status_code == 502
        assert "Check the server logs" in str(exc_info.value.detail)

    def test_mlflow_client_initialization_failure_becomes_502(self):
        from haute.routes.mlflow import _ensure_tracking

        mlflow_mod, tracking_mod = self._fake_mlflow_modules()
        tracking_mod.MlflowClient.side_effect = RuntimeError("client init failed")
        with (
            patch.dict(sys.modules, {"mlflow": mlflow_mod, "mlflow.tracking": tracking_mod}),
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("sqlite:///mlruns", "local"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_tracking()

        assert exc_info.value.status_code == 502
        assert "Check the server logs" in str(exc_info.value.detail)

    def test_local_backend_opts_into_mlflow_file_store_before_client_init(self, monkeypatch):
        from haute.routes.mlflow import _ensure_tracking

        monkeypatch.delenv("MLFLOW_ALLOW_FILE_STORE", raising=False)
        mlflow_mod, tracking_mod = self._fake_mlflow_modules()

        def _client_with_required_env(*_args, **_kwargs):
            assert os.environ["MLFLOW_ALLOW_FILE_STORE"] == "true"
            return MagicMock()

        tracking_mod.MlflowClient.side_effect = _client_with_required_env
        with (
            patch.dict(sys.modules, {"mlflow": mlflow_mod, "mlflow.tracking": tracking_mod}),
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("file:///tmp/mlruns", "local"),
            ),
        ):
            _ensure_tracking()

        assert os.environ["MLFLOW_ALLOW_FILE_STORE"] == "true"
        mlflow_mod.set_tracking_uri.assert_called_once_with("file:///tmp/mlruns")
        tracking_mod.MlflowClient.assert_called_once_with(tracking_uri="file:///tmp/mlruns")


# ---------------------------------------------------------------------------
# Runs — additional artifact filter edge cases
# ---------------------------------------------------------------------------


class TestListRunsAdditional:
    def test_run_with_no_run_name(self, client):
        """Runs with run_name=None default to empty string."""
        run = _make_run(run_id="nameless")
        run.info.run_name = None

        cbm = MagicMock(path="model.cbm")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [cbm]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["run_name"] == ""

    def test_run_with_multiple_model_artifacts(self, client):
        """A run with both .cbm and .rsglm returns both in artifacts."""
        run = _make_run(run_id="multi")
        cbm = MagicMock(path="model.cbm")
        rsglm = MagicMock(path="glm.rsglm")
        txt = MagicMock(path="readme.txt")

        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [cbm, rsglm, txt]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert set(data[0]["artifacts"]) == {"model.cbm", "glm.rsglm"}

    def test_run_with_none_metrics_and_params(self, client):
        """Runs where metrics/params are None default to empty dict."""
        run = _make_run(run_id="sparse")
        run.data.metrics = None
        run.data.params = None

        cbm = MagicMock(path="model.cbm")
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [cbm]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["metrics"] == {}
        assert data[0]["params"] == {}

    def test_optimiser_filter_does_not_match_non_exact(self, client):
        """artifact_filter=optimiser does NOT match similar-but-wrong filenames."""
        run = _make_run(run_id="wrong")
        wrong = MagicMock(path="optimiser_result.json.bak")

        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run]
        mock_client = MagicMock()
        mock_client.list_artifacts.return_value = [wrong]

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1&artifact_filter=optimiser")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_all_artifact_listings_fail_returns_empty(self, client):
        """When all artifact listings fail, the result is an empty list."""
        run1 = _make_run(run_id="r1")
        run2 = _make_run(run_id="r2")

        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.return_value = [run1, run2]
        mock_client = MagicMock()
        mock_client.list_artifacts.side_effect = Exception("storage down")

        with _mock_tracking(mlflow=mock_mlflow, client=mock_client):
            resp = client.get("/api/mlflow/runs?experiment_id=1")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Models — additional edge cases
# ---------------------------------------------------------------------------


class TestListModelsAdditional:
    def test_models_with_multiple_versions(self, client):
        """Model with multiple latest_versions returns them all."""
        model = MagicMock()
        model.name = "multi-version-model"
        v1 = MagicMock(version="1", status="READY", run_id="r1")
        v2 = MagicMock(version="2", status="READY", run_id="r2")
        model.latest_versions = [v1, v2]

        mock_client = MagicMock()
        mock_client.search_registered_models.return_value = [model]

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/models")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data[0]["latest_versions"]) == 2

    def test_page_token_none_not_forwarded(self, client):
        """When page_token is not provided, None is passed (not empty string)."""
        mock_client = MagicMock()
        mock_client.search_registered_models.return_value = []

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/models")

        assert resp.status_code == 200
        mock_client.search_registered_models.assert_called_once_with(
            max_results=100,
            page_token=None,
        )


# ---------------------------------------------------------------------------
# Model Versions — additional edge cases
# ---------------------------------------------------------------------------


class TestListModelVersionsAdditional:
    def test_empty_versions(self, client):
        """Model with no versions returns empty list."""
        mock_client = MagicMock()
        mock_client.search_model_versions.return_value = []

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=empty-model")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_versions_with_creation_timestamp(self, client):
        """Verify creation_timestamp is included in the response."""
        v = MagicMock(
            version="1",
            run_id="r1",
            status="READY",
            creation_timestamp=1700000000,
            description="v1 desc",
        )
        mock_client = MagicMock()
        mock_client.search_model_versions.return_value = [v]

        with _mock_tracking(client=mock_client):
            resp = client.get("/api/mlflow/model-versions?model_name=test")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["creation_timestamp"] == 1700000000
        assert data[0]["description"] == "v1 desc"
