"""Focused tests for instant frontier switching and explicit materialisation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest


@pytest.fixture()
def clean_job_store():
    """Isolate direct optimiser job-store mutations in this focused module."""
    from haute.routes.optimiser import _store

    snapshot = dict(_store.jobs)
    yield _store
    _store.jobs.clear()
    _store.jobs.update(snapshot)


def _frontier_point(
    *,
    objective: float = 123.0,
    volume: float = 0.91,
    lambda_volume: float = 0.42,
    converged: bool = True,
) -> dict[str, object]:
    return {
        "threshold_volume": 0.9,
        "total_objective": objective,
        "total_volume": volume,
        "lambda_volume": lambda_volume,
        "iterations": 7,
        "converged": converged,
        "sv_mean": 1.02,
        "sv_std": 0.03,
        "sv_min": 0.95,
        "sv_p5": 0.96,
        "sv_p25": 1.0,
        "sv_median": 1.02,
        "sv_p75": 1.04,
        "sv_p95": 1.08,
        "sv_max": 1.1,
        "sv_pct_increase": 0.7,
        "sv_pct_decrease": 0.2,
    }


def _frontier_data(points: list[dict[str, object]] | None = None) -> dict[str, object]:
    selected_points = points or [
        _frontier_point(objective=123.0, volume=0.91, lambda_volume=0.42),
        _frontier_point(objective=130.0, volume=0.93, lambda_volume=0.55, converged=False),
    ]
    return {
        "status": "ok",
        "points": selected_points,
        "n_points": len(selected_points),
        "points_returned": len(selected_points),
        "points_limit": 2000,
        "points_truncated": False,
        "constraint_names": ["volume"],
    }


def _online_frontier_job(
    *,
    frontier_data: dict[str, object] | None = None,
    solve_result: object | None = None,
    solver: object | None = None,
    quote_grid: object | None = None,
    selected_frontier_point: int | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "mode": "online",
        "total_objective": 99.0,
        "baseline_objective": 95.0,
        "constraints": {"volume": 0.88},
        "baseline_constraints": {"volume": 0.85},
        "lambdas": {"volume": 0.1},
        "converged": True,
        "n_quotes": 10,
        "n_steps": 3,
        "frontier": frontier_data or _frontier_data(),
    }
    if selected_frontier_point is not None:
        result["selected_frontier_point"] = selected_frontier_point

    job: dict[str, object] = {
        "status": "completed",
        "config": {
            "mode": "online",
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        },
        "node_label": "frontier_opt",
        "frontier_data": frontier_data or _frontier_data(),
        "result": result,
        "artifact_handles": {},
        "created_at": time.time(),
    }
    if solve_result is not None:
        job["solve_result"] = solve_result
    if solver is not None:
        job["solver"] = solver
    if quote_grid is not None:
        job["quote_grid"] = quote_grid
    if selected_frontier_point is not None:
        job["selected_frontier_point"] = selected_frontier_point
    return job


def _mlflow_mock() -> MagicMock:
    mock_mlflow = MagicMock()
    mock_run = MagicMock()
    mock_run.info.run_id = "run-frontier"
    mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=mock_run)
    mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)
    return mock_mlflow


def test_select_frontier_point_uses_stored_summary_without_solver(
    client,
    clean_job_store,
):
    clean_job_store.jobs["select_instant"] = _online_frontier_job()

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_instant", "point_index": 1},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["point_index"] == 1
    assert data["total_objective"] == 130.0
    assert data["constraints"] == {"volume": 0.93}
    assert data["lambdas"] == {"volume": 0.55}
    assert data["converged"] is False

    job = clean_job_store.jobs["select_instant"]
    assert job["selected_frontier_point"] == 1
    assert job["result"]["selected_frontier_point"] == 1
    assert job["result"]["total_objective"] == 130.0
    assert job["result"]["constraints"] == {"volume": 0.93}
    assert "solve_result" not in job


def test_select_frontier_point_rejects_malformed_missing_constraint_total(
    client,
    clean_job_store,
):
    malformed = _frontier_point()
    malformed.pop("total_volume")
    solver = MagicMock()
    clean_job_store.jobs["select_malformed"] = _online_frontier_job(
        frontier_data=_frontier_data([malformed]),
        solver=solver,
        quote_grid=MagicMock(),
    )

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_malformed", "point_index": 0},
    )

    assert resp.status_code == 500
    assert "total_volume" in resp.json()["detail"]
    solver.solve.assert_not_called()


def test_select_frontier_point_accepts_raw_price_contour_constraint_column(
    client,
    clean_job_store,
):
    point = _frontier_point(objective=123.0, volume=0.91, lambda_volume=0.42)
    point["volume"] = point.pop("total_volume")
    clean_job_store.jobs["select_raw_constraint"] = _online_frontier_job(
        frontier_data=_frontier_data([point]),
        solver=MagicMock(),
        quote_grid=MagicMock(),
    )

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_raw_constraint", "point_index": 0},
    )

    assert resp.status_code == 200
    assert resp.json()["constraints"] == {"volume": 0.91}


def test_select_frontier_point_rejects_partial_scenario_stats(
    client,
    clean_job_store,
):
    malformed = _frontier_point()
    malformed.pop("sv_std")
    clean_job_store.jobs["select_partial_stats"] = _online_frontier_job(
        frontier_data=_frontier_data([malformed]),
    )

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_partial_stats", "point_index": 0},
    )

    assert resp.status_code == 500
    assert "sv_std" in resp.json()["detail"]


def test_save_explicit_frontier_point_without_solve_result(
    client,
    clean_job_store,
    tmp_path: Path,
):
    from haute._sandbox import _get_project_root, set_project_root

    original_root = _get_project_root()
    clean_job_store.jobs["save_point"] = _online_frontier_job()
    out_path = tmp_path / "selected.json"

    try:
        set_project_root(tmp_path)
        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_point",
                "output_path": str(out_path),
                "point_index": 0,
            },
        )
    finally:
        set_project_root(original_root)

    assert resp.status_code == 200
    saved = json.loads(out_path.read_text())
    assert saved["total_objective"] == 123.0
    assert saved["total_constraints"] == {"volume": 0.91}
    assert saved["lambdas"] == {"volume": 0.42}
    assert saved["frontier_selection"]["point_index"] == 0


def test_save_selected_frontier_point_does_not_use_stale_solve_result(
    client,
    clean_job_store,
    tmp_path: Path,
):
    from haute._sandbox import _get_project_root, set_project_root

    stale_solve_result = SimpleNamespace(
        lambdas={"volume": 99.0},
        total_objective=999.0,
        total_constraints={"volume": 9.99},
        baseline_constraints={"volume": 0.85},
        baseline_objective=95.0,
        converged=True,
    )
    original_root = _get_project_root()
    clean_job_store.jobs["save_selected"] = _online_frontier_job(
        solve_result=stale_solve_result,
        selected_frontier_point=1,
    )
    out_path = tmp_path / "selected-default.json"

    try:
        set_project_root(tmp_path)
        resp = client.post(
            "/api/optimiser/save",
            json={"job_id": "save_selected", "output_path": str(out_path)},
        )
    finally:
        set_project_root(original_root)

    assert resp.status_code == 200
    saved = json.loads(out_path.read_text())
    assert saved["total_objective"] == 130.0
    assert saved["total_constraints"] == {"volume": 0.93}
    assert saved["lambdas"] == {"volume": 0.55}


def test_mlflow_log_explicit_frontier_point_without_solver_or_solve_result(
    client,
    clean_job_store,
):
    clean_job_store.jobs["mlflow_point"] = _online_frontier_job()
    mock_mlflow = _mlflow_mock()

    with (
        patch.dict("sys.modules", {"mlflow": mock_mlflow}),
        patch(
            "haute.modelling._mlflow_log.configure_mlflow_tracking",
            return_value=("http://localhost:5000", "local"),
        ),
        patch(
            "haute.modelling._mlflow_log.resolve_experiment_name",
            return_value="/frontier",
        ),
        patch(
            "haute.modelling._mlflow_log.build_run_url",
            return_value="http://localhost:5000/run-frontier",
        ),
    ):
        resp = client.post(
            "/api/optimiser/mlflow/log",
            json={"job_id": "mlflow_point", "point_index": 0},
        )

    assert resp.status_code == 200
    assert resp.json()["run_id"] == "run-frontier"
    mock_mlflow.set_tag.assert_any_call("frontier.selected_point_index", "0")
    logged_metrics = mock_mlflow.log_metrics.call_args.args[0]
    assert logged_metrics["total_objective"] == 123.0
    assert logged_metrics["constraint.volume"] == 0.91


def test_apply_explicit_frontier_point_materialises_online_result_to_disk(
    client,
    clean_job_store,
):
    apply_result = SimpleNamespace(
        total_objective=130.0,
        baseline_objective=95.0,
        total_constraints={"volume": 0.93},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.55},
        converged=False,
        dataframe=pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [1.04]}),
    )
    quote_grid = MagicMock()
    clean_job_store.jobs["apply_point"] = _online_frontier_job(
        quote_grid=quote_grid,
    )

    with patch("price_contour.apply_from_grid", return_value=apply_result) as apply_from_grid:
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_point", "point_index": 1},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_objective"] == 130.0
    assert data["constraints"] == {"volume": 0.93}
    assert data["preview"][0]["optimal_scenario_value"] == 1.04
    apply_from_grid.assert_called_once_with(
        quote_grid,
        lambdas={"volume": 0.55},
        constraints={"volume": {"min": 0.9}},
    )

    job = clean_job_store.jobs["apply_point"]
    assert job["selected_frontier_point"] == 1
    handle = job["artifact_handles"]["frontier_apply_result:1"]
    assert Path(handle["path"]).is_file()
    assert "solve_result" not in job
    assert "quote_grid" not in job


def test_apply_explicit_ratebook_frontier_point_requires_runtime_state(
    client,
    clean_job_store,
):
    solver = MagicMock()
    clean_job_store.jobs["apply_ratebook_point"] = _online_frontier_job(
        solver=solver,
        quote_grid=MagicMock(),
    )
    clean_job_store.jobs["apply_ratebook_point"]["config"]["mode"] = "ratebook"
    clean_job_store.jobs["apply_ratebook_point"]["result"]["mode"] = "ratebook"

    resp = client.post(
        "/api/optimiser/apply",
        json={"job_id": "apply_ratebook_point", "point_index": 0},
    )

    assert resp.status_code == 400
    assert "ratebook runtime state is not available" in resp.json()["detail"].lower()
    solver.solve.assert_not_called()
