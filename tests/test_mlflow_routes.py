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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from haute.server import app


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
