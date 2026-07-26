"""Focused tests for instant frontier switching and explicit materialisation."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl

from tests.optimiser_fixtures import (
    make_frontier_data as _frontier_data,
)
from tests.optimiser_fixtures import (
    make_frontier_point as _frontier_point,
)
from tests.optimiser_fixtures import (
    make_online_frontier_job as _online_frontier_job,
)

# ``clean_job_store`` lives in tests/conftest.py — single source of truth.


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


def test_concurrent_frontier_point_materialisations_preserve_both_handles(
    client,
    clean_job_store,
):
    """Disjoint point artifacts merge instead of overwriting one another."""
    apply_barrier = threading.Barrier(2)
    job = _online_frontier_job(quote_grid=MagicMock())
    clean_job_store.jobs["apply_points_concurrently"] = job

    def apply_from_grid(_grid, *, lambdas, constraints):
        del constraints
        apply_barrier.wait(timeout=3)
        point_value = float(lambdas["volume"])
        return SimpleNamespace(
            dataframe=pl.DataFrame(
                {
                    "quote_id": [f"q-{point_value}"],
                    "optimal_scenario_value": [point_value],
                }
            )
        )

    def request_point(point_index: int):
        return client.post(
            "/api/optimiser/apply",
            json={
                "job_id": "apply_points_concurrently",
                "point_index": point_index,
            },
        )

    with (
        patch("price_contour.apply_from_grid", side_effect=apply_from_grid),
        patch.object(clean_job_store, "clear_result_data"),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        responses = [
            future.result(timeout=5)
            for future in (pool.submit(request_point, 0), pool.submit(request_point, 1))
        ]

    assert [response.status_code for response in responses] == [200, 200]
    handles = clean_job_store.require_job("apply_points_concurrently")["artifact_handles"]
    assert set(handles) == {"frontier_apply_result:0", "frontier_apply_result:1"}
    for handle in handles.values():
        assert Path(handle["path"]).is_file()


def test_frontier_point_artifact_handles_are_capped_oldest_first():
    from haute.routes.optimiser import (
        _MAX_FRONTIER_APPLY_ARTIFACTS,
        _with_bounded_frontier_apply_handle,
    )

    existing = {
        "apply_result": {"path": "base"},
        **{
            f"frontier_apply_result:{index}": {"path": f"point-{index}"}
            for index in range(_MAX_FRONTIER_APPLY_ARTIFACTS)
        },
    }

    updated, evicted = _with_bounded_frontier_apply_handle(
        existing,
        "frontier_apply_result:99",
        {"path": "point-99"},
    )

    assert updated["apply_result"] == {"path": "base"}
    assert "frontier_apply_result:0" not in updated
    assert updated["frontier_apply_result:99"] == {"path": "point-99"}
    assert evicted == [{"path": "point-0"}]


def test_apply_explicit_ratebook_frontier_point_is_contract_error(
    client,
    clean_job_store,
):
    """Ratebook apply/detail is gated with 422 before any runtime-state or
    solver work: the real ``RatebookResult`` carries factor tables only, so
    there is no per-quote dataframe for the detail endpoint to serve."""
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

    assert resp.status_code == 422
    detail = resp.json()["detail"].lower()
    assert "ratebook" in detail
    assert "factor tables" in detail
    solver.solve.assert_not_called()


def test_select_frontier_point_returns_distinct_data_for_each_index(
    client,
    clean_job_store,
):
    """A stricter version of the basic select test.

    Uses three points with mutually-distinct field values so an off-by-one
    bug in indexing (or any index → response wiring mistake) cannot pass by
    coincidence.  Each index is exercised in turn and the full response is
    checked for value-by-value match against the corresponding point.
    """
    distinct_points = [
        _frontier_point(objective=100.0, volume=0.80, lambda_volume=0.10, converged=True),
        _frontier_point(objective=500.0, volume=0.85, lambda_volume=0.55, converged=False),
        _frontier_point(objective=900.0, volume=0.90, lambda_volume=0.99, converged=True),
    ]
    clean_job_store.jobs["select_distinct"] = _online_frontier_job(
        frontier_data=_frontier_data(distinct_points),
    )

    for index, point in enumerate(distinct_points):
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "select_distinct", "point_index": index},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["point_index"] == index, (
            f"backend echoed index {data['point_index']} for request index {index}"
        )
        assert data["total_objective"] == point["total_objective"]
        assert data["constraints"] == {"volume": point["total_volume"]}
        assert data["lambdas"] == {"volume": point["lambda_volume"]}
        assert data["converged"] is point["converged"]
        # The job's stored selected point must agree with the returned index.
        assert clean_job_store.jobs["select_distinct"]["selected_frontier_point"] == index


def test_apply_explicit_frontier_point_artifact_matches_response_preview(
    client,
    clean_job_store,
):
    """Round-trip the apply artifact: the rows persisted to disk must be the
    exact rows surfaced in the response preview.

    The previous test only confirmed ``apply_from_grid`` was *called* and the
    response preview had a row.  It did not verify that the file we will read
    back later contains the same dataframe as the response — a divergence
    bug between persistence and response shaping could pass silently.
    """
    persisted_df = pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3"],
            "optimal_scenario_value": [1.04, 0.97, 1.21],
            "expected_income": [42.0, 31.5, 88.7],
        }
    )
    apply_result = SimpleNamespace(
        total_objective=130.0,
        baseline_objective=95.0,
        total_constraints={"volume": 0.93},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.55},
        converged=False,
        dataframe=persisted_df,
    )
    quote_grid = MagicMock()
    clean_job_store.jobs["apply_round_trip"] = _online_frontier_job(quote_grid=quote_grid)

    with patch("price_contour.apply_from_grid", return_value=apply_result):
        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_round_trip", "point_index": 1},
        )

    assert resp.status_code == 200
    data = resp.json()

    # 1) The response preview must mirror what was passed to persistence.
    assert data["row_count"] == persisted_df.height
    assert data["preview_row_count"] == persisted_df.height
    response_preview = pl.DataFrame(data["preview"])
    assert response_preview.equals(persisted_df), (
        f"response preview diverges from the persisted dataframe:\n"
        f"  preview: {response_preview}\n"
        f"  persisted: {persisted_df}"
    )

    # 2) The artifact file must contain the exact same dataframe.
    job = clean_job_store.jobs["apply_round_trip"]
    handle = job["artifact_handles"]["frontier_apply_result:1"]
    artifact_path = Path(handle["path"])
    assert artifact_path.is_file()
    on_disk = pl.read_parquet(artifact_path)
    assert on_disk.equals(persisted_df), (
        f"persisted artifact diverges from the apply_result dataframe:\n"
        f"  on_disk: {on_disk}\n"
        f"  expected: {persisted_df}"
    )


def test_select_frontier_point_normalises_when_config_name_differs_from_column(
    client,
    clean_job_store,
):
    """The select endpoint normalises by the *config* constraint name, not the
    parquet column name.  Use a config name that differs from the point's
    column key so a bug that returned the raw column key would surface.
    """
    point = _frontier_point(volume=0.88, lambda_volume=0.42)
    # Backend may surface the constraint under either ``total_<name>`` or
    # the bare config name; both must be looked up by the *config* key.
    point["total_volume"] = point.pop("total_volume", 0.88)
    job = _online_frontier_job(frontier_data=_frontier_data([point]))
    job["config"]["constraints"] = {"volume": {"min": 0.9}}
    clean_job_store.jobs["select_normalise"] = job

    resp = client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": "select_normalise", "point_index": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    # Response constraints are keyed by the config name, not by the
    # ``total_<name>`` parquet column.
    assert "volume" in data["constraints"]
    assert "total_volume" not in data["constraints"]
    assert data["constraints"]["volume"] == point["total_volume"]
