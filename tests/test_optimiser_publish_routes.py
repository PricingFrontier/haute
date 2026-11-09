"""Publishing an optimiser result: the save artifact and the MLflow log.

``_build_artifact_payload`` and the as-solved adjustments build the pricing artifact, ``/save``
validates and writes it atomically, ``/mlflow/log`` logs frontier data, tags and artifacts, and
both publish from lightweight summaries rather than heavy job state.
"""

from __future__ import annotations

import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute._project_context import context_thread
from haute.routes._optimiser_solver import _as_solved_adjustments
from haute.routes.optimiser import _build_artifact_payload
from tests import _optimiser_route_helpers
from tests._optimiser_route_helpers import (
    _anchor_result,
    _expected_region_factor_tables,
    _frontier_point_summary,
    _make_optimiser_graph,
    _make_ratebook_frontier_materialisation_job,
    _poll_until_done,
)
from tests._project import use_project
from tests.job_store_support import replace_job, seed_job
from tests.optimiser_fixtures import (
    SOLVE_SCENARIO_GRID,
    logged_json_artifacts,
    make_frontier_point,
    make_input_summary,
    make_online_apply_frame,
    make_solved_result,
    use_local_mlflow_store,
    with_solve_summary,
)

# Fixtures shared with the other modules split from one suite.
scored_data = _optimiser_route_helpers.scored_data


# ---------------------------------------------------------------------------
# Phase 1B: Pure function tests
# ---------------------------------------------------------------------------


class TestAsSolvedAdjustments:
    """Unit tests for _as_solved_adjustments (the finalize-time report)."""

    _JOB: ClassVar[dict[str, Any]] = {
        "config": {"constraints": {}},
        "scenario_grid": SOLVE_SCENARIO_GRID,
    }

    def test_no_dataframe_attribute(self):
        """An object without .dataframe raises; finalize records it as a diagnostics error."""
        with pytest.raises(ValueError, match="no per-quote frame"):
            _as_solved_adjustments(SimpleNamespace(), self._JOB)

    def test_missing_column(self):
        """A frame without the chosen-scenario columns raises rather than omitting the report."""
        from haute.routes._optimiser_outcomes import ChoiceJoinError

        result = SimpleNamespace(dataframe=pl.DataFrame({"other_col": [1.0, 2.0, 3.0]}))
        with pytest.raises(ChoiceJoinError, match="optimal_step"):
            _as_solved_adjustments(result, self._JOB)

    def test_empty_dataframe_raises_rather_than_omitting_the_report(self):
        frame = pl.DataFrame(
            {
                "quote_id": pl.Series([], dtype=pl.String),
                "optimal_step": pl.Series([], dtype=pl.Int32),
                "optimal_scenario_value": pl.Series([], dtype=pl.Float32),
                "optimal_objective": pl.Series([], dtype=pl.Float32),
            }
        )
        with pytest.raises(ValueError, match="no quotes"):
            _as_solved_adjustments(SimpleNamespace(dataframe=frame), self._JOB)


class TestBuildArtifactPayload:
    """Unit tests for _build_artifact_payload."""

    def test_online_mode_basic(self):
        """Online mode produces a payload with expected keys."""
        job = with_solve_summary(
            {
                "node_label": "my_opt",
                "config": {
                    "mode": "online",
                    "constraints": {"volume": {"min": 0.9}},
                    "objective": "income",
                },
            }
        )
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=1000.0,
            baseline_objective=950.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["mode"] == "online"
        assert payload["lambdas"] == {"volume": 0.5}
        assert payload["converged"] is True
        assert "factor_tables" not in payload  # only for ratebook

    def test_missing_chunk_size_is_not_serialized_as_default(self):
        """Saved artifacts only carry chunk_size when the user set it explicitly."""
        job = with_solve_summary(
            {
                "node_label": "my_opt",
                "config": {
                    "mode": "online",
                    "constraints": {"volume": {"min": 0.9}},
                    "objective": "income",
                },
            }
        )
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=1000.0,
            baseline_objective=950.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
        )

        payload = _build_artifact_payload(job, solve_result)

        assert "chunk_size" not in payload

    def test_setup_chunking_provenance_is_serialized(self):
        """Saved artifacts preserve the physical chunking provenance used by setup."""
        setup_chunking = {
            "optimiser_grid": {
                "policy": "pipeline_setting",
                "chunk_size": 500_000,
                "source": "optimiser_grid",
            }
        }
        job = with_solve_summary(
            {
                "node_label": "my_opt",
                "config": {
                    "mode": "online",
                    "constraints": {"volume": {"min": 0.9}},
                    "objective": "income",
                },
                "setup_chunking": setup_chunking,
            }
        )
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=1000.0,
            baseline_objective=950.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
        )

        payload = _build_artifact_payload(job, solve_result)

        assert payload["setup_chunking"] == setup_chunking
        assert "chunk_size" not in payload

    def test_ratebook_mode_includes_factor_tables(self):
        """Ratebook mode includes factor_tables and clamp_rate."""
        job = with_solve_summary(
            {
                "node_label": "rb_opt",
                "config": {"mode": "ratebook", "constraints": {}, "objective": "income"},
                "result": {
                    "factor_tables": {
                        "region": [{"__factor_group__": "North", "optimal_scenario_value": 1.1}]
                    },
                    "combined_factor_bounds": {"min": 0.1, "max": 10.0},
                    "factor_dtypes": {
                        "region": [{"column": "region", "dtype": {"kind": "String"}}]
                    },
                },
            }
        )
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=1000.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={},
            constraint_bounds={},
            converged=True,
            clamp_rate=0.05,
            combined_factor_bounds={"min": 0.9, "max": 1.1},
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["mode"] == "ratebook"
        assert "factor_tables" in payload
        assert payload["factor_dtypes"] == {
            "region": [{"column": "region", "dtype": {"kind": "String"}}]
        }
        assert payload["clamp_rate"] == 0.05
        assert payload["combined_factor_bounds"] == {"min": 0.9, "max": 1.1}

    def test_version_override(self):
        """User-specified version overrides auto-generated one."""
        job = with_solve_summary({"node_label": "opt", "config": {"mode": "online"}})
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={},
            constraint_bounds={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result, version_override="v2.0")
        assert payload["version"] == "v2.0"

    @staticmethod
    def _frontier_job() -> dict:
        return with_solve_summary(
            {
                "node_label": "my_opt",
                "config": {
                    "mode": "online",
                    "objective": "income",
                    "constraints": {"volume": {"min": 0.9}},
                },
                "selected_frontier_point": 1,
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        make_frontier_point(objective=100.0, threshold_volume=0.85),
                        make_frontier_point(objective=110.0, threshold_volume=0.9),
                        make_frontier_point(objective=120.0, threshold_volume=0.95),
                    ],
                    "n_points": 3,
                    "constraint_names": ["volume"],
                },
            }
        )

    def test_payload_includes_frontier_selection(self):
        """T3: A frontier-point target records its frontier_selection and thresholds."""
        job = self._frontier_job()
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=120.0,
            baseline_objective=100.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
        )
        payload = _build_artifact_payload(job, solve_result, point_index=2)
        assert "frontier_selection" in payload
        fs = payload["frontier_selection"]
        assert fs["selected_from_frontier"] is True
        assert fs["point_index"] == 2
        assert fs["n_frontier_points"] == 3
        # The point's own threshold is the effective one; the configured
        # specs OPTIMISER_APPLY reads stay unchanged.
        assert payload["effective_constraints"] == {"volume": {"min": 0.95}}
        assert payload["constraints"] == {"volume": {"min": 0.9}}

    def test_anchor_target_ignores_the_server_selected_point(self):
        """No point index is the job's own solve, whatever point the server has selected."""
        job = self._frontier_job()
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=105.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={"volume": 0.91},
            constraint_bounds={"volume": 0.9},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" not in payload
        assert payload["effective_constraints"] == {"volume": {"min": 0.9}}
        assert payload["stale_at_publish"] is False

    def test_payload_records_the_audit_trail(self):
        """Solver settings, input provenance and staleness are recorded for audit."""
        job = with_solve_summary(
            {
                "node_label": "rb",
                "config": {
                    "mode": "ratebook",
                    "objective": "income",
                    "constraints": {"volume": {"min": 0.9}},
                    "max_iter": 80,
                    "cd_tolerance": 0.01,
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.0}},
                },
                "base_result": {"n_quotes": 12, "n_steps": 3},
                "result": {"n_quotes": 99},
                "input_provenance": {
                    "node_id": "opt",
                    "data_source": "batch",
                    "source_file": "main.py",
                    "graph_fingerprint": "abc123",
                },
            }
        )
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=105.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={"volume": 0.91},
            constraint_bounds={"volume": 0.9},
            converged=True,
            factor_tables={},
            factor_dtypes={},
            clamp_rate=0.1,
            combined_factor_bounds={"min": 0.9, "max": 1.1},
        )
        payload = _build_artifact_payload(job, solve_result, stale_at_publish=True)
        assert payload["solver_settings"] == {
            "max_iter": 80,
            "tolerance": 1e-6,
            "max_cd_iterations": 10,
            "cd_tolerance": 0.01,
            "frontier_steps": 15,
            "frontier_ranges": {"volume": {"min": 0.8, "max": 1.0}},
        }
        assert payload["input_summary"] == {
            "n_quotes": 12,
            "n_steps": 3,
            "node_id": "opt",
            "data_source": "batch",
            "source_file": "main.py",
            "graph_fingerprint": "abc123",
        }
        assert payload["stale_at_publish"] is True

    def test_online_solver_settings_without_frontier(self):
        job = with_solve_summary({"node_label": "o", "config": {"mode": "online"}})
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=1.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={},
            constraint_bounds={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["solver_settings"] == {
            "max_iter": 50,
            "tolerance": 1e-6,
        }
        assert payload["input_summary"]["n_quotes"] is None

    def test_payload_no_frontier_selection_when_none(self):
        """T3: When no frontier point is selected, payload has no frontier_selection key."""
        job = with_solve_summary(
            {
                "node_label": "my_opt",
                "config": {"mode": "online", "objective": "income", "constraints": {}},
                # No selected_frontier_point key
            }
        )
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=100.0,
            baseline_objective=95.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" not in payload


# ---------------------------------------------------------------------------
# Phase 1B: MLflow log endpoint tests
# ---------------------------------------------------------------------------


class TestOptimiserMlflowLog:
    """Tests for /mlflow/log endpoint."""

    def test_mlflow_log_missing_job(self, client):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "nonexistent",
                "experiment_name": "/test",
            },
        )
        assert resp.status_code == 404

    def test_mlflow_log_not_completed(self, client, clean_job_store):
        seed_job(
            clean_job_store,
            "running_job",
            {
                "status": "running",
                "progress": 0.5,
                "created_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "running_job",
                "experiment_name": "/test",
            },
        )
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]

    def test_mlflow_log_without_publish_summary_asks_for_a_re_run(self, client, clean_job_store):
        seed_job(
            clean_job_store,
            "no_summary",
            {
                "status": "completed",
                "result": {
                    "lambdas": {},
                    "total_objective": 1.0,
                    "constraints": {},
                    "baseline_objective": 0.0,
                    "baseline_constraints": {},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "publish_summary": None,
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "no_summary",
                "experiment_name": "/test",
            },
        )
        assert resp.status_code == 400
        assert "re-run the solve" in resp.json()["detail"].lower()

    def test_mlflow_log_import_error(self, client, clean_job_store):
        """If mlflow is not installed, return the shared 503 every MLflow route uses."""
        mock_solver = MagicMock()
        mock_solve = MagicMock(
            lambdas={},
            total_objective=0,
            total_constraints={},
            constraint_bounds={},
            converged=True,
        )
        seed_job(
            clean_job_store,
            "import_err",
            {
                "result": make_solved_result(),
                "status": "completed",
                "solver": mock_solver,
                "solve_result": mock_solve,
                "config": {"mode": "online"},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

        with patch.dict("sys.modules", {"mlflow": None}):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={
                    "job_id": "import_err",
                    "experiment_name": "/test",
                },
            )
        assert resp.status_code == 503
        assert (
            resp.json()["detail"] == "MLflow is not installed. Install it with: pip install mlflow"
        )

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_mlflow_log_after_real_solve(self, client, scored_data, tmp_path, monkeypatch):
        """A completed solve keeps the solver available for later MLflow logging."""
        graph = _make_optimiser_graph(scored_data)
        resp = client.post(
            "/api/optimiser/solve",
            json={"graph": graph, "node_id": "opt"},
        )
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"

        store = use_local_mlflow_store(tmp_path, monkeypatch)
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": job_id, "experiment_name": "optimiser"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        run = store.get_run(data["run_id"])
        assert run.info.status == "FINISHED"
        assert run.data.metrics

    @staticmethod
    def _seed_opt_job(job_store, job_id, config_extra=None):
        # Publishing reads only the completion summaries; no heavy state is seeded.
        config = {"mode": "online", **(config_extra or {})}
        seed_job(
            job_store,
            job_id,
            {
                "status": "completed",
                "result": {
                    "mode": "online",
                    "lambdas": {"m": 0.1},
                    "total_objective": 100.0,
                    "constraints": {},
                    "baseline_objective": 0.0,
                    "baseline_constraints": {},
                    "converged": True,
                    "iterations": 10,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "publish_summary": {
                    "params": {"mode": "online"},
                    "metrics": {"total_objective": 100.0},
                    "artifacts": {},
                },
                "config": config,
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

    @pytest.mark.parametrize(
        ("result_overrides", "summary_artifacts", "named"),
        [
            pytest.param({"lambdas": {"m": float("nan")}}, {}, "lambdas.m", id="nan-lambda"),
            pytest.param(
                {}, {"lambdas": {"m": float("inf")}}, "lambdas.m", id="non-finite-summary"
            ),
        ],
    )
    def test_a_non_finite_result_is_refused_before_any_run_exists(
        self, client, clean_job_store, monkeypatch, result_overrides, summary_artifacts, named
    ):
        """OPT-R01: the log gates its artifacts as the save does (400 naming the
        values), before a run is created, so nothing non-finite is ever logged."""
        from mlflow.tracking import MlflowClient

        def no_run(*_args, **_kwargs):
            raise AssertionError("a run was created for a refused result")

        monkeypatch.setattr(MlflowClient, "create_run", no_run)
        self._seed_opt_job(clean_job_store, "opt_nan")

        def poison(job: dict) -> None:
            job["result"].update(result_overrides)
            job["publish_summary"]["artifacts"] = summary_artifacts

        replace_job(clean_job_store, "opt_nan", poison)

        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "opt_nan", "destination": "local", "experiment_name": "opt_exp"},
        )

        assert resp.status_code == 400, resp.text
        assert named in resp.json()["detail"]

    def test_omitted_and_empty_go_to_auto_and_key_goes_to_its_own(
        self, client, clean_job_store, tmp_path, monkeypatch
    ):
        from mlflow.tracking import MlflowClient

        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        use_project(tmp_path)

        # 1) omitted destination -> 200, backend == "local", tracking_uri startswith file:
        self._seed_opt_job(clean_job_store, "opt_dest_omitted", {"mlflow_destination": "server"})
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "opt_dest_omitted", "experiment_name": "opt_exp_test"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        assert data["tracking_uri"].startswith("file:")

        # 2) destination "" -> same
        self._seed_opt_job(clean_job_store, "opt_dest_empty", {"mlflow_destination": "server"})
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "opt_dest_empty", "destination": "", "experiment_name": "opt_exp_test"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        assert data["tracking_uri"].startswith("file:")

        # 3) destination "local" -> same
        self._seed_opt_job(clean_job_store, "opt_dest_local", {"mlflow_destination": "server"})
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "opt_dest_local",
                "destination": "local",
                "experiment_name": "opt_exp_test",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        assert data["tracking_uri"].startswith("file:")

        # Count runs before unconfigured server attempt
        client_mlflow = MlflowClient(tracking_uri=(tmp_path / "mlruns").as_uri())
        exp = client_mlflow.get_experiment_by_name("opt_exp_test")
        assert exp is not None
        runs_before = len(client_mlflow.search_runs([exp.experiment_id]))

        # 4) destination "server" (unconfigured) -> 400,
        # "MLflow server is not configured" in detail,
        # and the local store gained no new run.
        self._seed_opt_job(clean_job_store, "opt_dest_server", {"mlflow_destination": "server"})
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "opt_dest_server",
                "destination": "server",
                "experiment_name": "opt_exp_test",
            },
        )
        assert resp.status_code == 400, resp.text
        assert "MLflow server is not configured" in resp.json()["detail"]
        runs_after = len(client_mlflow.search_runs([exp.experiment_id]))
        assert runs_after == runs_before

    def test_second_destination_is_distinct(self, client, clean_job_store, tmp_path, monkeypatch):
        from mlflow.tracking import MlflowClient

        from haute.modelling._mlflow_settings import TrackingConfig

        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        use_project(tmp_path)

        server_runs_dir = tmp_path / "server-runs"
        server_config = TrackingConfig(
            "server", server_runs_dir.as_uri(), "http://stub:5000", "toml"
        )
        monkeypatch.setattr(
            "haute.modelling._mlflow_settings._resolve_server",
            lambda stored=None: server_config,
        )

        self._seed_opt_job(clean_job_store, "opt_srv_distinct")
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "opt_srv_distinct",
                "destination": "server",
                "experiment_name": "opt_srv_exp",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "server"
        assert data["tracking_uri"] == server_runs_dir.as_uri()

        server_client = MlflowClient(tracking_uri=server_runs_dir.as_uri())
        exp = server_client.get_experiment_by_name("opt_srv_exp")
        assert exp is not None
        assert len(server_client.search_runs([exp.experiment_id])) == 1

        self._seed_opt_job(clean_job_store, "opt_loc_distinct")
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "opt_loc_distinct",
                "destination": "local",
                "experiment_name": "opt_loc_exp",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        assert data["tracking_uri"] == (tmp_path / "mlruns").as_uri()

        local_client = MlflowClient(tracking_uri=(tmp_path / "mlruns").as_uri())
        loc_exp = local_client.get_experiment_by_name("opt_loc_exp")
        assert loc_exp is not None
        assert len(local_client.search_runs([loc_exp.experiment_id])) == 1

    def test_unknown_destination_is_422_before_any_write(self, client):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "x", "destination": "managed"},
        )
        assert resp.status_code == 422

    def test_chosen_databricks_fails_loudly_when_rejected_but_unchosen_logs_locally(
        self, client, clean_job_store, tmp_path, monkeypatch
    ):
        from mlflow.tracking import MlflowClient

        use_project(tmp_path)
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("MLFLOW_ENABLE_DB_SDK", "true")

        self._seed_opt_job(clean_job_store, "opt_dest_sdk")
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "opt_dest_sdk", "destination": "databricks"},
        )
        assert resp.status_code == 400, resp.text
        assert "MLFLOW_ENABLE_DB_SDK" in resp.json()["detail"]

        self._seed_opt_job(clean_job_store, "opt_dest_sdk_local")
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "opt_dest_sdk_local",
                "destination": "",
                "experiment_name": "opt_sdk_local_exp",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"

        client_mlflow = MlflowClient(tracking_uri=(tmp_path / "mlruns").as_uri())
        exp = client_mlflow.get_experiment_by_name("opt_sdk_local_exp")
        assert exp is not None
        assert len(client_mlflow.search_runs([exp.experiment_id])) == 1


# ---------------------------------------------------------------------------
# save_result unit tests
# ---------------------------------------------------------------------------


class TestSaveResultUnit:
    def test_save_path_traversal_blocked_unit(self, client, clean_job_store, tmp_path):

        seed_job(
            clean_job_store,
            "save_trav",
            {
                "status": "completed",
                "result": _anchor_result(),
                "config": {"mode": "online"},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        use_project(tmp_path)
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_trav",
                "output_path": "../../etc/passwd",
            },
        )
        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"].lower()

    def test_save_completed_job_writes_json(self, client, clean_job_store, tmp_path):
        import json as json_mod

        mock_solve_result = MagicMock()
        seed_job(
            clean_job_store,
            "save_ok",
            {
                "status": "completed",
                "result": _anchor_result(),
                "solve_result": mock_solve_result,
                "solver": MagicMock(),
                "quote_grid": MagicMock(),
                "config": {
                    "mode": "online",
                    "objective": "income",
                    "constraints": {"volume": {"min": 0.9}},
                },
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        use_project(tmp_path)
        out_path = str(tmp_path / "out.json")
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_ok",
                "output_path": out_path,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["apply_path"] == "out.json"

        saved = json_mod.loads(Path(out_path).read_text(encoding="utf-8"))
        assert saved["lambdas"] == {"volume": 0.5}
        assert saved["total_objective"] == 100.0
        assert saved["converged"] is True
        # Publishing neither reads nor releases the heavy runtime state.
        job = clean_job_store.require_job("save_ok")
        assert job["solve_result"] is mock_solve_result
        assert "solver" in job
        assert "quote_grid" in job
        assert not mock_solve_result.mock_calls

    def test_save_bare_filename_lands_in_the_pipeline_folders_files_models(
        self, client, clean_job_store, tmp_path
    ):
        """A bare filename goes where Commit versions it with the pipeline."""

        seed_job(
            clean_job_store,
            "save_bare",
            {
                "status": "completed",
                "result": _anchor_result(),
                "solve_result": MagicMock(),
                "solver": MagicMock(),
                "quote_grid": MagicMock(),
                "config": {"mode": "online", "objective": "income"},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
        use_project(tmp_path)

        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": "save_bare", "output_path": "optimiser_opt_1.json"},
        )

        assert resp.status_code == 200
        assert resp.json()["apply_path"] == "rating/files/models/optimiser_opt_1.json"
        assert (tmp_path / "rating" / "files" / "models" / "optimiser_opt_1.json").is_file()

    def test_save_ratebook_frontier_point_materialises_selected_factor_tables(
        self,
        client,
        clean_job_store,
        tmp_path,
    ):
        import json as json_mod

        mock_solver, _mock_grid, _factor_contexts = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "save_rb_frontier",
        )
        use_project(tmp_path)
        out_path = str(tmp_path / "selected_ratebook.json")

        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_rb_frontier",
                "output_path": out_path,
                "point_index": 0,
            },
        )

        assert resp.status_code == 200
        saved = json_mod.loads(Path(out_path).read_text(encoding="utf-8"))
        # The frontier row's totals and the tables the frontier kept for it.
        assert saved["total_objective"] == 220.0
        assert saved["total_constraints"] == {"volume": 0.97}
        assert saved["lambdas"] == {"volume": 0.7}
        assert saved["factor_tables"] == _expected_region_factor_tables()
        assert saved["frontier_selection"]["point_index"] == 0
        mock_solver.solve.assert_not_called()


# ---------------------------------------------------------------------------
# Coverage expansion tests
# ---------------------------------------------------------------------------


class TestBuildArtifactPayloadExtended:
    """Additional tests for _build_artifact_payload."""

    def test_online_mode_fields(self):
        """Online mode includes objective, quote_id, scenario fields."""
        job = with_solve_summary(
            {
                "node_label": "my_opt",
                "config": {
                    "mode": "online",
                    "constraints": {"volume": {"min": 0.9}},
                    "objective": "income",
                    "quote_id": "qid",
                    "scenario_index": "step",
                    "scenario_value": "sv",
                },
            }
        )
        solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=1000.0,
            baseline_objective=950.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            converged=True,
            iterations=10,
            cd_iterations=None,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["quote_id"] == "qid"
        assert payload["scenario_index"] == "step"
        assert payload["scenario_value"] == "sv"
        assert "chunk_size" not in payload
        assert payload["iterations"] == 10
        assert payload["cd_iterations"] is None

    def test_ratebook_mode_with_factor_tables(self):
        """Ratebook mode includes factor_tables and clamp_rate."""
        job = with_solve_summary(
            {
                "node_label": "rb",
                "config": {"mode": "ratebook", "constraints": {}, "objective": "income"},
                "result": {
                    "factor_tables": {
                        "region": [
                            {"__factor_group__": "N", "optimal_scenario_value": 1.05},
                            {"__factor_group__": "S", "optimal_scenario_value": 0.95},
                        ]
                    },
                    "combined_factor_bounds": {"min": 0.1, "max": 10.0},
                    "factor_dtypes": {
                        "region": [{"column": "region", "dtype": {"kind": "String"}}]
                    },
                },
            }
        )
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=1000.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={},
            constraint_bounds={},
            converged=True,
            clamp_rate=0.03,
            combined_factor_bounds={"min": 0.9, "max": 1.1},
        )
        payload = _build_artifact_payload(job, solve_result)
        assert payload["factor_tables"] is not None
        assert payload["factor_dtypes"] == {
            "region": [{"column": "region", "dtype": {"kind": "String"}}]
        }
        assert len(payload["factor_tables"]["region"]) == 2
        assert payload["clamp_rate"] == 0.03

    def test_version_override_replaces_auto(self):
        """Version override takes precedence over auto-generated version."""
        job = with_solve_summary({"node_label": "test", "config": {"mode": "online"}})
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={},
            constraint_bounds={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result, version_override="custom_v1")
        assert payload["version"] == "custom_v1"

    def test_auto_version_when_no_override(self):
        """When no version override, auto-generated version is used."""
        job = with_solve_summary({"node_label": "My Opt", "config": {"mode": "online"}})
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={},
            constraint_bounds={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result, version_override="")
        # Auto version has the slug of the label
        assert payload["version"].startswith("my_opt_")

    def test_no_frontier_selection_when_index_none(self):
        """No frontier_selection when selected_frontier_point is None."""
        job = with_solve_summary(
            {
                "node_label": "opt",
                "config": {"mode": "online"},
                "selected_frontier_point": None,
                "frontier_data": {"n_points": 3},
            }
        )
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={},
            constraint_bounds={},
            converged=True,
        )
        # selected_idx is None so frontier_selection should not be added
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" not in payload

    def test_no_frontier_selection_when_no_frontier_data(self):
        """No frontier_selection when frontier_data is missing."""
        job = with_solve_summary(
            {
                "node_label": "opt",
                "config": {"mode": "online"},
                "selected_frontier_point": 2,
                # no frontier_data key
            }
        )
        solve_result = SimpleNamespace(
            lambdas={},
            total_objective=0.0,
            baseline_objective=0.0,
            baseline_constraints={},
            total_constraints={},
            constraint_bounds={},
            converged=True,
        )
        payload = _build_artifact_payload(job, solve_result)
        assert "frontier_selection" not in payload


class TestMlflowLogExtended:
    """Extended tests for /mlflow/log — frontier data logging, tags, artifacts."""

    @staticmethod
    def _make_mlflow_job(
        clean_job_store, job_id, *, frontier_data=None, selected_frontier_point=None
    ):
        mock_solver = MagicMock()
        mock_solver.summary.return_value = {
            "params": {"mode": "online"},
            "metrics": {"total_objective": 100.0},
            "artifacts": {"lambdas": {"volume": 0.5}},
        }
        mock_solve_result = SimpleNamespace(
            lambdas={"volume": 0.5},
            total_objective=100.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            baseline_objective=95.0,
            converged=True,
            iterations=10,
        )
        job = {
            "status": "completed",
            "solver": mock_solver,
            "solve_result": mock_solve_result,
            "quote_grid": MagicMock(),
            "config": {
                "mode": "online",
                "objective": "income",
                "constraints": {"volume": {"min": 0.9}},
            },
            "result": {
                "mode": "online",
                "total_objective": 100.0,
                "baseline_objective": 95.0,
                "constraints": {"volume": 0.92},
                "baseline_constraints": {"volume": 0.88},
                "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                "lambdas": {"volume": 0.5},
                "converged": True,
                "diagnostics_errors": [],
                "input_summary": make_input_summary(),
                "scenario_grid": SOLVE_SCENARIO_GRID,
            },
            "publish_summary": mock_solver.summary.return_value,
            "node_label": "my_opt",
            "created_at": time.time(),
            "completed_at": time.time(),
        }
        if frontier_data is not None:
            job["frontier_data"] = frontier_data
        if selected_frontier_point is not None:
            job["selected_frontier_point"] = selected_frontier_point
        seed_job(clean_job_store, job_id, job)
        return mock_solver

    def test_mlflow_log_success_with_frontier(self, client, clean_job_store, tmp_path, monkeypatch):
        """MLflow log with frontier data logs frontier CSV and tags."""
        frontier_data = {
            "status": "ok",
            "points": [
                _frontier_point_summary(
                    total_objective=100.0,
                    lambda_volume=0.3,
                    total_volume=0.9,
                ),
                _frontier_point_summary(
                    total_objective=110.0,
                    lambda_volume=0.5,
                    total_volume=0.95,
                ),
            ],
            "n_points": 2,
            "constraint_names": ["volume"],
        }
        mock_solver = self._make_mlflow_job(
            clean_job_store,
            "mlf_ok",
            frontier_data=frontier_data,
            selected_frontier_point=0,
        )
        store = use_local_mlflow_store(tmp_path, monkeypatch)

        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "mlf_ok",
                "experiment_name": "test_exp",
                "point_index": 1,
                "stale": True,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["experiment_name"] == "test_exp"
        run = store.get_run(data["run_id"])
        assert run.info.status == "FINISHED"
        assert run.data.tags["frontier.n_points"] == "2"
        # The requested point, not the server-selected one.
        assert run.data.tags["frontier.selected_point_index"] == "1"
        assert run.data.tags["effective_constraint.volume.min"] == "0.95"
        assert run.data.tags["stale_at_publish"] == "true"
        assert run.data.params["solver_settings.max_iter"] == "50"
        logged = {artifact.path for artifact in store.list_artifacts(data["run_id"])}
        assert {"frontier.csv", "optimiser_result.json"} <= logged
        # The logged frontier is price-contour's points table: every typed point
        # written back as the library's flat row, in its schema's column order.
        import csv

        import price_contour

        csv_path = store.download_artifacts(data["run_id"], "frontier.csv", str(tmp_path))
        with open(csv_path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert list(rows[0]) == list(price_contour.frontier_points_schema("online", ["volume"]))
        assert [float(row["total_objective"]) for row in rows] == [100.0, 110.0]
        assert [float(row["lambda_volume"]) for row in rows] == [0.3, 0.5]
        assert [float(row["total_volume"]) for row in rows] == [0.9, 0.95]
        optimiser_result = logged_json_artifacts(store, data["run_id"], tmp_path / "logged")[
            "optimiser_result.json"
        ]
        assert optimiser_result["effective_constraints"] == {"volume": {"min": 0.95}}
        assert optimiser_result["constraints"] == {"volume": {"min": 0.9}}
        assert optimiser_result["stale_at_publish"] is True
        # Publishing neither needs nor releases heavy runtime state.
        job = clean_job_store.require_job("mlf_ok")
        assert {"solver", "solve_result", "quote_grid"} <= set(job)
        mock_solver.summary.assert_not_called()

    def test_mlflow_log_without_point_logs_the_anchor_not_the_selected_point(
        self, client, clean_job_store, tmp_path, monkeypatch
    ):
        frontier_data = {
            "status": "ok",
            "points": [
                _frontier_point_summary(total_objective=90.0, lambda_volume=0.3),
                _frontier_point_summary(total_objective=110.0, lambda_volume=0.5),
            ],
            "n_points": 2,
            "constraint_names": ["volume"],
        }
        self._make_mlflow_job(
            clean_job_store,
            "mlf_anchor",
            frontier_data=frontier_data,
            selected_frontier_point=1,
        )
        store = use_local_mlflow_store(tmp_path, monkeypatch)

        resp = client.post("/api/optimiser/mlflow/log", json={"job_id": "mlf_anchor"})

        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
        tags = store.get_run(run_id).data.tags
        assert "frontier.selected_point_index" not in tags
        assert tags["stale_at_publish"] == "false"
        optimiser_result = logged_json_artifacts(store, run_id, tmp_path / "logged")[
            "optimiser_result.json"
        ]
        assert optimiser_result["total_objective"] == 100.0
        assert "frontier_selection" not in optimiser_result
        assert optimiser_result["effective_constraints"] == {"volume": {"min": 0.9}}

    def test_mlflow_log_ratebook_frontier_point_materialises_selected_factor_tables(
        self,
        client,
        clean_job_store,
        tmp_path,
        monkeypatch,
    ):
        mock_solver, _mock_grid, _factor_contexts = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "mlf_rb_frontier",
        )
        store = use_local_mlflow_store(tmp_path, monkeypatch)

        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={
                "job_id": "mlf_rb_frontier",
                "experiment_name": "ratebook_exp",
                "point_index": 0,
            },
        )

        assert resp.status_code == 200
        run_id = resp.json()["run_id"]
        logged_json = logged_json_artifacts(store, run_id, tmp_path / "logged")
        optimiser_result = logged_json["optimiser_result.json"]
        assert optimiser_result["total_objective"] == 220.0
        assert optimiser_result["cd_iterations"] == 3
        assert optimiser_result["factor_tables"] == _expected_region_factor_tables()
        assert (
            logged_json["frontier_point_summary.json"]["factor_tables"]
            == (optimiser_result["factor_tables"])
        )
        mock_solver.solve.assert_not_called()
        assert store.get_run(run_id).data.tags["frontier.selected_point_index"] == "0"

    def test_mlflow_log_no_frontier(self, client, clean_job_store, tmp_path, monkeypatch):
        """MLflow log without frontier data still succeeds."""
        self._make_mlflow_job(clean_job_store, "mlf_nf")
        store = use_local_mlflow_store(tmp_path, monkeypatch)

        resp = client.post("/api/optimiser/mlflow/log", json={"job_id": "mlf_nf"})

        assert resp.status_code == 200
        # No frontier tags should be set when no frontier data
        tags = store.get_run(resp.json()["run_id"]).data.tags
        assert not [tag for tag in tags if tag.startswith("frontier.")]

    def test_mlflow_log_artifacts_skips_none(self, client, clean_job_store, tmp_path, monkeypatch):
        """Artifacts with None data are skipped during logging."""
        seed_job(
            clean_job_store,
            "mlf_skip",
            {
                "status": "completed",
                "result": _anchor_result(total_objective=50.0, constraints={}),
                "publish_summary": {
                    "params": {"mode": "online"},
                    "metrics": {"total_objective": 50.0},
                    "artifacts": {"lambdas": {"volume": 0.5}, "empty_one": None},
                },
                "config": {"mode": "online", "objective": "income", "constraints": {}},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        store = use_local_mlflow_store(tmp_path, monkeypatch)

        resp = client.post("/api/optimiser/mlflow/log", json={"job_id": "mlf_skip"})

        assert resp.status_code == 200
        # The None artifact is skipped: lambdas.json + optimiser_result.json only.
        logged = {artifact.path for artifact in store.list_artifacts(resp.json()["run_id"])}
        assert logged == {"lambdas.json", "optimiser_result.json"}

    def test_the_log_runs_while_another_writer_holds_the_fluent_state(
        self, client, clean_job_store, tmp_path, monkeypatch
    ):
        """The optimiser logs no model, so it never waits for MLflow's
        process-global fluent state, and never writes the tracking URI into
        the environment."""
        import os

        from haute._mlflow_utils import mlflow_fluent_operation

        self._make_mlflow_job(clean_job_store, "mlf_concurrent")
        store = use_local_mlflow_store(tmp_path, monkeypatch)
        held = threading.Event()
        release = threading.Event()

        def hold_fluent_state() -> None:
            with mlflow_fluent_operation():
                held.set()
                release.wait()

        holder = context_thread(hold_fluent_state, daemon=True)
        holder.start()
        assert held.wait(10)
        with ThreadPoolExecutor(max_workers=1) as executor:
            request = executor.submit(
                contextvars.copy_context().run,
                client.post,
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_concurrent", "experiment_name": "concurrent"},
            )
            try:
                resp = request.result(timeout=30)
            finally:
                # Released before the executor waits for the request, so a
                # log that does wait for the fluent state fails instead of hanging.
                release.set()
                holder.join(10)

        assert resp.status_code == 200, resp.text
        assert store.get_run(resp.json()["run_id"]).info.status == "FINISHED"
        assert "MLFLOW_TRACKING_URI" not in os.environ


def _ratebook_gate_payload(**overrides: Any) -> dict[str, Any]:
    """A ratebook artifact payload that passes the gate until *overrides* break it."""
    payload: dict[str, Any] = {
        "lambdas": {},
        "total_objective": 1.0,
        "mode": "ratebook",
        "factor_tables": {"region": []},
        "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
        "clamp_rate": 0.25,
        "combined_factor_bounds": {"min": 0.9, "max": 1.1},
    }
    payload.update(overrides)
    return payload


class TestSaveArtifactGate:
    """Pre-write validation and atomic write for the /save pricing artifact.

    The saved JSON is exactly what OPTIMISER_APPLY reads to price, and a
    successful save clears the in-memory solve result — so a poisoned or
    torn artifact is unrecoverable without a full re-solve. These tests pin
    the gate: non-finite values are rejected before any bytes hit disk, a
    failed write leaves the previous artifact intact, and a failed save
    leaves the solve result in the job store for retry.
    """

    @pytest.mark.parametrize(
        ("payload", "expected_detail"),
        [
            ({"total_objective": 1.0}, "lambda mapping"),
            ({"lambdas": {}}, "total objective"),
            (
                {
                    "lambdas": {},
                    "total_objective": 1.0,
                    "mode": "ratebook",
                    "factor_tables": {"region": []},
                },
                "factor dtype metadata",
            ),
            (
                {
                    "lambdas": {},
                    "total_objective": 1.0,
                    "mode": "ratebook",
                    "factor_tables": {"region": []},
                    "combined_factor_bounds": {"min": 0.1, "max": 10.0},
                    "factor_dtypes": {"other": [{"column": "region", "dtype": {"kind": "String"}}]},
                },
                "do not match",
            ),
            (
                {
                    "lambdas": {},
                    "total_objective": 1.0,
                    "mode": "ratebook",
                    "factor_tables": {"region": []},
                    "combined_factor_bounds": {"min": 0.1, "max": 10.0},
                    "factor_dtypes": {"region": []},
                },
                "non-empty ordered list",
            ),
            (
                {
                    "lambdas": {},
                    "total_objective": 1.0,
                    "mode": "ratebook",
                    "factor_tables": {"region": []},
                    "combined_factor_bounds": {"min": 0.1, "max": 10.0},
                    "factor_dtypes": {"region": ["not a mapping"]},
                },
                "malformed record at index 0",
            ),
            (
                {
                    "lambdas": {},
                    "total_objective": 1.0,
                    "mode": "ratebook",
                    "factor_tables": {"region": []},
                    "combined_factor_bounds": {"min": 0.1, "max": 10.0},
                    "factor_dtypes": {"region": [{"column": "region"}]},
                },
                "malformed record at index 0",
            ),
            (
                {
                    "lambdas": {f"constraint_{index}": float("nan") for index in range(6)},
                    "total_objective": 1.0,
                },
                "6 in total",
            ),
            (
                _ratebook_gate_payload(clamp_rate=None),
                "no finite clamp_rate (got None)",
            ),
            (
                _ratebook_gate_payload(clamp_rate=float("nan")),
                "no finite clamp_rate (got nan)",
            ),
            (
                _ratebook_gate_payload(combined_factor_bounds=None),
                "no valid combined-factor collar: combined_factor_bounds must be an object",
            ),
            (
                _ratebook_gate_payload(combined_factor_bounds={"min": 1.2, "max": 0.8}),
                "min 1.2 is greater than max 0.8",
            ),
            (
                _ratebook_gate_payload(combined_factor_bounds={"min": float("nan"), "max": 1.1}),
                "combined_factor_bounds.min must be finite",
            ),
            (
                _ratebook_gate_payload(combined_factor_bounds={"min": "0.9", "max": 1.1}),
                "combined_factor_bounds.min must be a number",
            ),
            (
                _ratebook_gate_payload(combined_factor_bounds={"max": 1.1}),
                "exactly the keys 'min' and 'max'",
            ),
        ],
        ids=[
            "lambdas",
            "total-objective",
            "ratebook-factor-dtypes",
            "ratebook-table-mismatch",
            "ratebook-empty-dtypes",
            "ratebook-malformed-dtype-record",
            "ratebook-incomplete-dtype-record",
            "many-non-finite-values",
            "ratebook-missing-clamp-rate",
            "ratebook-nan-clamp-rate",
            "ratebook-missing-collar",
            "ratebook-inverted-collar",
            "ratebook-nan-collar",
            "ratebook-string-collar",
            "ratebook-one-key-collar",
        ],
    )
    def test_artifact_gate_rejects_invalid_required_sections(
        self,
        payload,
        expected_detail,
    ):
        from haute.routes.optimiser import _validate_artifact_payload

        with pytest.raises(HTTPException) as exc_info:
            _validate_artifact_payload(payload)

        assert exc_info.value.status_code == 400
        assert expected_detail in exc_info.value.detail

    def test_artifact_gate_accepts_a_complete_ratebook_payload(self) -> None:
        from haute.routes.optimiser import _validate_artifact_payload

        _validate_artifact_payload(_ratebook_gate_payload())
        _validate_artifact_payload(
            _ratebook_gate_payload(combined_factor_bounds={"min": 1.0, "max": 1.0})
        )

    @staticmethod
    def _completed_job(
        store,
        job_id: str,
        *,
        lambdas: dict | None = None,
        total_objective: float = 100.0,
        config: dict | None = None,
        solve_result_extra: dict | None = None,
    ) -> None:
        config = config or {"mode": "online"}
        result = _anchor_result(
            mode=config["mode"],
            lambdas={"volume": 0.5} if lambdas is None else lambdas,
            total_objective=total_objective,
            **(solve_result_extra or {}),
        )
        seed_job(
            store,
            job_id,
            {
                "status": "completed",
                "result": result,
                "solve_result": MagicMock(),
                "solver": MagicMock(),
                "config": config,
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

    @pytest.mark.parametrize(
        ("job_kwargs", "expected_path"),
        [
            ({"lambdas": {"volume": float("nan")}}, "lambdas.volume"),
            ({"lambdas": {"volume": float("inf")}}, "lambdas.volume"),
            ({"total_objective": float("nan")}, "total_objective"),
        ],
    )
    def test_save_rejects_non_finite_values_pre_write(
        self, client, clean_job_store, tmp_path, job_kwargs, expected_path
    ):
        """A non-finite solve value is a 400 and the artifact is untouched."""
        self._completed_job(clean_job_store, "save_nonfinite", **job_kwargs)
        use_project(tmp_path)
        out_path = tmp_path / "artifact.json"
        out_path.write_text('{"version": "previous"}')

        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": "save_nonfinite", "output_path": str(out_path)},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "non-finite" in detail
        assert expected_path in detail
        assert out_path.read_text() == '{"version": "previous"}'
        job = clean_job_store.require_job("save_nonfinite")
        assert "solve_result" in job
        assert "solver" in job

    def test_save_rejects_non_finite_ratebook_factor_level(self, client, clean_job_store, tmp_path):
        """A NaN factor level in a ratebook artifact is rejected pre-write."""
        self._completed_job(
            clean_job_store,
            "save_rb_nan",
            config={"mode": "ratebook"},
            solve_result_extra={
                "factor_tables": {
                    "region": [
                        {
                            "__factor_group__": "North",
                            "optimal_scenario_value": float("nan"),
                            "quote_count": 1,
                        }
                    ]
                },
                "combined_factor_bounds": {"min": 0.1, "max": 10.0},
                "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
                "clamp_rate": 0.0,
            },
        )
        use_project(tmp_path)
        out_path = tmp_path / "ratebook.json"

        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": "save_rb_nan", "output_path": str(out_path)},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "non-finite" in detail
        assert "factor_tables" in detail
        assert not out_path.exists()
        assert "solve_result" in clean_job_store.require_job("save_rb_nan")

    def test_save_rejects_ratebook_artifact_without_factor_tables(
        self, client, clean_job_store, tmp_path
    ):
        """A ratebook artifact with no factor tables cannot price — 400."""
        self._completed_job(clean_job_store, "save_rb_none", config={"mode": "ratebook"})
        use_project(tmp_path)
        out_path = tmp_path / "ratebook.json"

        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": "save_rb_none", "output_path": str(out_path)},
        )

        assert resp.status_code == 400
        assert "factor tables" in resp.json()["detail"]
        assert not out_path.exists()
        assert "solve_result" in clean_job_store.require_job("save_rb_none")

    def test_save_write_failure_preserves_previous_artifact(
        self, client, clean_job_store, tmp_path
    ):
        """A write failure never tears the previous artifact (atomic write)."""
        self._completed_job(clean_job_store, "save_torn")
        use_project(tmp_path)
        out_path = tmp_path / "artifact.json"
        out_path.write_text('{"version": "previous"}')

        with patch("pathlib.Path.replace", side_effect=OSError("disk full")):
            resp = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_torn", "output_path": str(out_path), "overwrite": True},
            )

        assert resp.status_code == 500
        assert out_path.read_text() == '{"version": "previous"}'
        assert list(tmp_path.glob("*.tmp")) == []
        job = clean_job_store.require_job("save_torn")
        assert "solve_result" in job
        assert "solver" in job

    def test_save_failure_then_retry_succeeds_without_resolve(
        self, client, clean_job_store, tmp_path
    ):
        """After a failed save the in-memory result still saves on retry."""
        import json as json_mod

        self._completed_job(clean_job_store, "save_retry")
        use_project(tmp_path)
        out_path = tmp_path / "artifact.json"

        with patch("pathlib.Path.replace", side_effect=OSError("disk full")):
            first = client.post(
                "/api/optimiser/save",
                json={"job_id": "save_retry", "output_path": str(out_path)},
            )
        assert first.status_code == 500

        second = client.post(
            "/api/optimiser/save",
            json={"job_id": "save_retry", "output_path": str(out_path)},
        )
        assert second.status_code == 200
        saved = json_mod.loads(out_path.read_text(encoding="utf-8"))
        assert saved["lambdas"] == {"volume": 0.5}
        # Publishing never releases the heavy state.
        assert "solve_result" in clean_job_store.require_job("save_retry")


class TestMlflowLogExceptionPath:
    """Test mlflow_log generic exception path."""

    def test_mlflow_log_neither_needs_nor_reserves_the_solver(
        self,
        client,
        clean_job_store,
        tmp_path,
        monkeypatch,
    ):
        """The anchor logs its completion-time summary; the solver is never called."""
        mock_solver = MagicMock()
        seed_job(
            clean_job_store,
            "mlf_touch",
            {
                "status": "completed",
                "created_at": 100.0,
                "completed_at": 100.0,
                "heavy_objects_expires_at": 1000.0,
                "solver": mock_solver,
                "result": _anchor_result(),
                "publish_summary": {"params": {}, "metrics": {}, "artifacts": {}},
                "config": {"mode": "online"},
                "node_label": "opt",
            },
        )
        use_local_mlflow_store(tmp_path, monkeypatch)

        with patch("haute.routes._job_store.time.time", return_value=940.0):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_touch"},
            )
            job = clean_job_store.require_job("mlf_touch")

        assert resp.status_code == 200
        assert job["heavy_objects_expires_at"] == 1000.0
        assert not mock_solver.mock_calls

    def test_mlflow_log_internal_error(self, client, clean_job_store, tmp_path, monkeypatch):
        """When mlflow logging raises, endpoint returns 500."""
        seed_job(
            clean_job_store,
            "mlf_err",
            {
                "status": "completed",
                "solver": MagicMock(),
                "solve_result": MagicMock(),
                "result": _anchor_result(),
                "publish_summary": {"params": {}, "metrics": {}, "artifacts": {}},
                "config": {"mode": "online"},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        use_local_mlflow_store(tmp_path, monkeypatch)
        with (
            patch(
                "haute.routes.optimiser.ensure_experiment",
                side_effect=RuntimeError("experiment boom"),
            ),
            patch("haute.routes._mlflow_log_errors.logger.error") as log_error,
        ):
            resp = client.post(
                "/api/optimiser/mlflow/log",
                json={"job_id": "mlf_err"},
            )
        assert resp.status_code == 500
        log_error.assert_called_once()
        assert log_error.call_args.args == ("mlflow_log_failed",)
        # Category and type only: the raw text never reaches the log.
        assert log_error.call_args.kwargs == {
            "category": "unknown",
            "error_type": "RuntimeError",
            "job_id": "mlf_err",
        }
        job = clean_job_store.require_job("mlf_err")
        assert "solver" in job
        assert "solve_result" in job


# ---------------------------------------------------------------------------
# Mutation-readiness boundary tests.
#
# Each test below pins a specific constant or boundary that a subtle code
# change (off-by-one, comparison flip, constant drift, dropped clause) could
# silently invalidate.  These complement the behavioural tests above; their
# job is to catch *implementation drift* that doesn't change observable
# behaviour for the normal path.
# ---------------------------------------------------------------------------


class TestPublishWithoutHeavyState:
    """Save and MLflow log publish from lightweight summaries, never heavy state."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_save_log_save_succeed_on_a_real_solve_even_after_heavy_state_is_cleared(
        self, client, clean_job_store, scored_data, tmp_path, monkeypatch
    ):
        import json as json_mod

        graph = _make_optimiser_graph(scored_data)
        started = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = started.json()["job_id"]
        assert _poll_until_done(client, job_id)["status"] == "completed"
        job = clean_job_store.require_job(job_id)
        summary = job["publish_summary"]
        assert summary["metrics"]["total_objective"] == job["result"]["total_objective"]
        assert job["input_provenance"]["node_id"] == "opt"
        assert job["input_provenance"]["data_source"] == "batch"
        store = use_local_mlflow_store(tmp_path, monkeypatch)

        def save(overwrite: bool):
            return client.post(
                "/api/optimiser/save",
                json={
                    "job_id": job_id,
                    "output_path": "rating/opt.json",
                    "overwrite": overwrite,
                },
            )

        first = save(False)
        assert first.status_code == 200, first.text
        assert first.json()["apply_path"] == "rating/opt.json"
        logged = client.post("/api/optimiser/mlflow/log", json={"job_id": job_id})
        assert logged.status_code == 200, logged.text
        assert save(True).status_code == 200

        # Publishing never released the heavy state ...
        assert {"solver", "solve_result", "quote_grid"} <= set(clean_job_store.require_job(job_id))
        # ... and does not need it: publish again once it is gone.
        clean_job_store.clear_result_data(job_id)
        assert "solver" not in clean_job_store.require_job(job_id)
        assert save(True).status_code == 200
        relogged = client.post("/api/optimiser/mlflow/log", json={"job_id": job_id})
        assert relogged.status_code == 200, relogged.text

        saved = json_mod.loads((tmp_path / "rating" / "opt.json").read_text(encoding="utf-8"))
        assert saved["total_objective"] == job["result"]["total_objective"]
        assert saved["lambdas"] == job["result"]["lambdas"]
        assert saved["solver_settings"]["max_iter"] == 20
        assert saved["solver_settings"]["tolerance"] == 1e-4
        assert saved["input_summary"]["n_quotes"] == job["result"]["n_quotes"]
        assert saved["input_summary"]["graph_fingerprint"]
        run = store.get_run(relogged.json()["run_id"])
        assert run.data.metrics["total_objective"] == job["result"]["total_objective"]
        assert run.data.params["solver_settings.max_iter"] == "20"

    @staticmethod
    def _seed(store, job_id: str) -> None:
        seed_job(
            store,
            job_id,
            {
                "status": "completed",
                "result": _anchor_result(),
                "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
                "node_label": "opt",
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

    def test_relative_path_resolves_against_the_project_root(
        self, client, clean_job_store, tmp_path
    ):
        self._seed(clean_job_store, "rel")
        use_project(tmp_path)
        pipeline_dir = tmp_path / "pipelines"
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "pipelines/main.py"\n')
        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": "rel", "output_path": "out/nested/result.json"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["apply_path"] == "out/nested/result.json"
        assert Path(body["path"]) == (tmp_path / "out" / "nested" / "result.json").resolve()
        assert (tmp_path / "out" / "nested" / "result.json").is_file()
        assert not pipeline_dir.exists()

    def test_absolute_path_inside_the_root_answers_a_relative_apply_path(
        self, client, clean_job_store, tmp_path
    ):
        self._seed(clean_job_store, "abs")
        use_project(tmp_path)
        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": "abs", "output_path": str(tmp_path / "a" / "b.json")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["apply_path"] == "a/b.json"

    def test_existing_file_is_a_409_unless_overwrite(self, client, clean_job_store, tmp_path):
        import json as json_mod

        self._seed(clean_job_store, "exists")
        use_project(tmp_path)
        target = tmp_path / "out" / "result.json"
        target.parent.mkdir()
        previous = json_mod.dumps({"version": "previous"})
        target.write_text(previous, encoding="utf-8")

        refused = client.post(
            "/api/optimiser/save",
            json={"job_id": "exists", "output_path": "out/result.json"},
        )
        assert refused.status_code == 409
        assert refused.json()["detail"] == {
            "error_code": "optimiser_result_exists",
            "message": "Optimiser result already exists: out/result.json",
        }
        assert target.read_text(encoding="utf-8") == previous

        replaced = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "exists",
                "output_path": "out/result.json",
                "overwrite": True,
                "stale": True,
            },
        )
        assert replaced.status_code == 200, replaced.text
        saved = json_mod.loads(target.read_text(encoding="utf-8"))
        assert saved["total_objective"] == 100.0
        assert saved["stale_at_publish"] is True

    def test_anchor_apply_after_a_clear_reads_the_anchor_totals(self, client, clean_job_store):
        """With a point selected, anchor detail from the artifact reports the anchor's totals."""
        seed_job(
            clean_job_store,
            "anchor_apply",
            {
                "status": "completed",
                "base_result": _anchor_result(),
                "result": _anchor_result(total_objective=130.0, selected_frontier_point=1),
                "selected_frontier_point": 1,
                "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "artifact_handles": {"apply_result": {"kind": "stub"}},
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        frame = make_online_apply_frame(["q1"])
        with patch(
            "haute.routes._optimiser_artifacts._scan_apply_result_artifact",
            return_value=frame.lazy(),
        ) as load:
            resp = client.post("/api/optimiser/apply", json={"job_id": "anchor_apply"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["total_objective"] == 100.0
        assert resp.json()["from_artifact"] is True
        load.assert_called_once_with({"kind": "stub"})


class TestPublishSummary:
    def test_frames_become_records_and_nothing_heavy_is_kept(self):
        from haute.routes._optimiser_solver import _publish_summary

        solver = MagicMock()
        solver.summary.return_value = {
            "params": {"objective": "income"},
            "metrics": {"total_objective": 1.0},
            "artifacts": {
                "convergence": pl.DataFrame({"iteration": [0, 1]}),
                "rating_entries": {"region": pl.DataFrame({"region": ["N"], "factor": [1.1]})},
                "none": None,
            },
        }

        summary = _publish_summary(solver, SimpleNamespace(), job_id="j")

        assert summary == {
            "params": {"objective": "income"},
            "metrics": {"total_objective": 1.0},
            "artifacts": {
                "convergence": [{"iteration": 0}, {"iteration": 1}],
                "rating_entries": {"region": [{"region": "N", "factor": 1.1}]},
                "none": None,
            },
        }

    def test_a_failing_summary_does_not_fail_the_solve(self):
        from haute.routes._optimiser_solver import _publish_summary

        solver = MagicMock()
        solver.summary.side_effect = RuntimeError("no dataframe")
        with patch("haute.routes._optimiser_solver.logger.warning") as warning:
            assert _publish_summary(solver, SimpleNamespace(), job_id="j") is None
        assert warning.call_args.args == ("publish_summary_failed",)

    def test_a_non_mapping_summary_is_refused(self):
        from haute.routes._optimiser_solver import _publish_summary

        solver = MagicMock()
        solver.summary.return_value = ["not", "a", "mapping"]
        with patch("haute.routes._optimiser_solver.logger.warning") as warning:
            assert _publish_summary(solver, SimpleNamespace(), job_id="j") is None
        assert warning.call_args.args == ("publish_summary_invalid",)
