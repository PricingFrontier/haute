"""Focused coverage for critical optimiser route edge paths."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi import HTTPException


@pytest.fixture()
def clean_job_store():
    """Isolate direct optimiser job-store mutations in this focused module."""
    from haute.routes.optimiser import _store

    snapshot = dict(_store.jobs)
    yield _store
    _store.jobs.clear()
    _store.jobs.update(snapshot)


def _frontier_job(*, artifact_handles: object | None = None) -> dict:
    solver = MagicMock()
    solver.solve.return_value = SimpleNamespace(
        total_objective=200.0,
        baseline_objective=190.0,
        total_constraints={"volume": 0.95},
        baseline_constraints={"volume": 0.90},
        lambdas={"volume": 0.7},
        converged=True,
        dataframe=pl.DataFrame({"optimal_scenario_value": [0.9, 1.0]}),
    )
    job = {
        "status": "completed",
        "solver": solver,
        "quote_grid": MagicMock(),
        "heavy_objects_expires_at": time.time() + 3600,
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "threshold_volume": 0.95,
                    "total_volume": 0.95,
                    "lambda_volume": 0.7,
                    "total_objective": 200.0,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "total_objective": 100.0,
            "baseline_objective": 90.0,
            "constraints": {"volume": 0.9},
            "baseline_constraints": {"volume": 0.85},
            "lambdas": {"volume": 0.3},
            "converged": True,
        },
        "created_at": time.time(),
    }
    if artifact_handles is not None:
        job["artifact_handles"] = artifact_handles
    return job


def test_estimate_returns_input_metrics_when_metadata_lookup_fails(client, tmp_path: Path):
    from tests.conftest import make_edge, make_graph

    data_path = tmp_path / "scored.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q2", "q2"],
            "scenario_index": [0, 1, 0, 1],
            "scenario_value": [0.9, 1.1, 0.8, 1.0],
            "expected_income": [100.0, 110.0, 90.0, 95.0],
            "volume": [1.0, 0.9, 1.2, 1.1],
        }
    ).write_parquet(data_path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": str(data_path)},
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "online",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.9}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    )

    with (
        patch(
            "haute._ram_estimate._ancestor_source_metadata",
            side_effect=RuntimeError("metadata unavailable"),
        ),
        patch("haute.routes.optimiser.logger.warning") as log_warning,
    ):
        resp = client.post(
            "/api/optimiser/estimate",
            json={"graph": graph.model_dump(), "node_id": "opt"},
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "total_rows": None,
        "quote_count": 2,
        "scenarios_per_quote_min": 2,
        "scenarios_per_quote_max": 2,
        "scenarios_per_quote_mean": 2.0,
        "expanded_row_count": 4,
    }
    assert log_warning.call_count == 1
    assert log_warning.call_args_list[0].args == ("optimiser_estimate_failed",)
    assert log_warning.call_args_list[0].kwargs["error"] == "metadata unavailable"
    assert log_warning.call_args_list[0].kwargs["node_id"] == "opt"


def test_apply_rejects_non_mapping_artifact_handles(client, clean_job_store):
    clean_job_store.jobs["bad_apply_handles"] = {
        "status": "completed",
        "artifact_handles": ["not", "a", "mapping"],
        "created_at": time.time(),
    }

    resp = client.post("/api/optimiser/apply", json={"job_id": "bad_apply_handles"})

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job artifact handles are invalid"


def test_apply_rejects_missing_artifact_summary(client, clean_job_store):
    clean_job_store.jobs["missing_apply_summary"] = {
        "status": "completed",
        "artifact_handles": {"apply_result": {"path": "already-validated-by-patch"}},
        "result": "not a summary mapping",
        "created_at": time.time(),
    }

    with patch(
        "haute.routes.optimiser._load_apply_result_artifact",
        return_value=pl.DataFrame({"quote_id": ["q1"]}),
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "missing_apply_summary"},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job summary is missing"


def test_apply_rejects_incomplete_artifact_summary(client, clean_job_store):
    clean_job_store.jobs["incomplete_apply_summary"] = {
        "status": "completed",
        "artifact_handles": {"apply_result": {"path": "already-validated-by-patch"}},
        "result": {"total_objective": "not numeric", "constraints": {"volume": 0.9}},
        "created_at": time.time(),
    }

    with patch(
        "haute.routes.optimiser._load_apply_result_artifact",
        return_value=pl.DataFrame({"quote_id": ["q1"]}),
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "incomplete_apply_summary"},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job summary is incomplete"


def test_frontier_fails_if_runtime_disappears_after_touch(client, clean_job_store):
    clean_job_store.jobs["frontier_runtime_race"] = {
        "status": "completed",
        "solver": MagicMock(),
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "frontier_runtime_race",
                "threshold_ranges": {"volume": [0.85, 0.95]},
            },
        )

    assert resp.status_code == 400
    assert "re-run the solve" in resp.json()["detail"].lower()


def test_frontier_select_succeeds_when_runtime_is_absent(client, clean_job_store):
    clean_job_store.jobs["select_runtime_race"] = {
        "status": "completed",
        "frontier_data": {
            "status": "ok",
            "points": [
                {
                    "threshold_volume": 0.95,
                    "total_volume": 0.95,
                    "lambda_volume": 0.7,
                    "total_objective": 200.0,
                    "converged": True,
                }
            ],
            "n_points": 1,
            "constraint_names": ["volume"],
        },
        "result": {
            "baseline_objective": 90.0,
            "baseline_constraints": {"volume": 0.85},
        },
        "created_at": time.time(),
    }

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_runtime_race", "point_index": 0},
    )

    assert resp.status_code == 200
    assert resp.json()["constraints"] == {"volume": 0.95}


def test_frontier_apply_rejects_non_mapping_artifact_handles(client, clean_job_store):
    clean_job_store.jobs["select_bad_handles"] = _frontier_job(
        artifact_handles="not a mapping",
    )

    resp = client.post(
        "/api/optimiser/apply",
        json={"job_id": "select_bad_handles", "point_index": 0},
    )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job artifact handles are invalid"


def test_frontier_apply_rejects_invalid_existing_apply_handle(client, clean_job_store):
    clean_job_store.jobs["select_bad_apply_handle"] = _frontier_job(
        artifact_handles={"frontier_apply_result:0": "not a handle mapping"},
    )

    resp = client.post(
        "/api/optimiser/apply",
        json={"job_id": "select_bad_apply_handle", "point_index": 0},
    )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Job frontier apply artifact handle is invalid"


def test_frontier_apply_cleans_new_artifact_after_unexpected_store_failure(
    client,
    clean_job_store,
):
    from haute.routes._optimiser_service import _persist_apply_result_artifact

    orphan_handle = _persist_apply_result_artifact(
        SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [1.0]}))
    )
    assert orphan_handle is not None
    orphan_path = Path(orphan_handle["path"])
    orphan_dir = Path(orphan_handle["directory"])
    clean_job_store.jobs["select_store_failure"] = _frontier_job(artifact_handles={})
    apply_result = SimpleNamespace(
        dataframe=pl.DataFrame({"optimal_scenario_value": [1.0]}),
    )

    with (
        patch("price_contour.apply_from_grid", return_value=apply_result),
        patch(
            "haute.routes.optimiser._persist_apply_result_artifact",
            return_value=orphan_handle,
        ),
        patch.object(
            clean_job_store,
            "atomic_update_if_heavy_present",
            side_effect=RuntimeError("store write failed"),
        ),
        patch("haute.routes.optimiser.logger.error") as log_error,
    ):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "select_store_failure", "point_index": 0},
        )

    assert resp.status_code == 500
    assert not orphan_path.exists()
    assert not orphan_dir.exists()
    log_error.assert_called_once()
    assert log_error.call_args.args == ("frontier_apply_materialise_failed",)
    assert log_error.call_args.kwargs["error"] == "store write failed"
    assert log_error.call_args.kwargs["job_id"] == "select_store_failure"
    assert log_error.call_args.kwargs["exc_info"] is True


def test_save_rechecks_solve_result_after_touch(client, clean_job_store, tmp_path: Path):
    from haute._sandbox import _get_project_root, set_project_root

    original_root = _get_project_root()
    clean_job_store.jobs["save_missing_after_touch"] = {
        "status": "completed",
        "solve_result": None,
        "created_at": time.time(),
    }

    try:
        set_project_root(tmp_path)
        with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
            resp = client.post(
                "/api/optimiser/save",
                json={
                    "job_id": "save_missing_after_touch",
                    "output_path": str(tmp_path / "out.json"),
                },
            )
    finally:
        set_project_root(original_root)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Job has no solve result"


def test_save_reraises_http_exception_from_artifact_build(
    client,
    clean_job_store,
    tmp_path: Path,
):
    from haute._sandbox import _get_project_root, set_project_root

    original_root = _get_project_root()
    clean_job_store.jobs["save_artifact_http_error"] = {
        "status": "completed",
        "solve_result": SimpleNamespace(),
        "config": {"mode": "online"},
        "node_label": "opt",
        "created_at": time.time(),
    }

    try:
        set_project_root(tmp_path)
        with patch(
            "haute.routes.optimiser._build_artifact_payload",
            side_effect=HTTPException(status_code=418, detail="artifact rejected"),
        ):
            resp = client.post(
                "/api/optimiser/save",
                json={
                    "job_id": "save_artifact_http_error",
                    "output_path": str(tmp_path / "out.json"),
                },
            )
    finally:
        set_project_root(original_root)

    assert resp.status_code == 418
    assert resp.json()["detail"] == "artifact rejected"


def test_mlflow_log_rechecks_solve_result_after_touch(client, clean_job_store):
    clean_job_store.jobs["mlflow_missing_after_touch"] = {
        "status": "completed",
        "solver": MagicMock(),
        "solve_result": None,
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "mlflow_missing_after_touch"},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Job has no solve result"


def test_mlflow_log_rechecks_solver_after_touch(client, clean_job_store):
    clean_job_store.jobs["mlflow_solver_missing_after_touch"] = {
        "status": "completed",
        "solver": None,
        "solve_result": SimpleNamespace(),
        "created_at": time.time(),
    }

    with patch.object(clean_job_store, "touch_heavy_objects", return_value=True):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "mlflow_solver_missing_after_touch"},
        )

    assert resp.status_code == 400
    assert "re-run the solve" in resp.json()["detail"].lower()
