"""Focused tests for instant frontier switching and explicit materialisation."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from tests.job_store_support import seed_job
from tests.optimiser_fixtures import (
    make_frontier_data as _frontier_data,
)
from tests.optimiser_fixtures import (
    make_frontier_point as _frontier_point,
)
from tests.optimiser_fixtures import (
    make_online_frontier_job as _online_frontier_job,
)
from tests.optimiser_fixtures import use_local_mlflow_store

# ``clean_job_store`` lives in tests/conftest.py — single source of truth.


def _with_as_solved_artifact(job: dict, n_quotes: int = 3) -> dict:
    """Give a seeded job its as-solved apply artifact: a point's apply is estimated from it."""
    from haute.routes._optimiser_artifacts import _persist_apply_result_artifact

    frame = pl.DataFrame(
        {
            "quote_id": [f"q{quote}" for quote in range(n_quotes)],
            "optimal_step": pl.Series([1] * n_quotes, dtype=pl.Int32),
            "optimal_scenario_value": pl.Series([1.0] * n_quotes, dtype=pl.Float32),
            "optimal_objective": pl.Series([10.0] * n_quotes, dtype=pl.Float32),
            "optimal_volume": pl.Series([0.3] * n_quotes, dtype=pl.Float32),
        }
    )
    handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=frame))
    job["artifact_handles"] = {**job.get("artifact_handles", {}), "apply_result": handle}
    return job


def test_select_frontier_point_uses_stored_summary_without_solver(
    client,
    clean_job_store,
):
    seed_job(clean_job_store, "select_instant", _online_frontier_job())

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

    job = clean_job_store.require_job("select_instant")
    assert job["selected_frontier_point"] == 1
    assert job["result"]["selected_frontier_point"] == 1
    assert job["result"]["total_objective"] == 130.0
    assert job["result"]["constraints"] == {"volume": 0.93}
    assert "solve_result" not in job


def test_save_explicit_frontier_point_without_solve_result(
    client,
    clean_job_store,
    tmp_path: Path,
):
    from haute._sandbox import _get_project_root, set_project_root

    original_root = _get_project_root()
    seed_job(clean_job_store, "save_point", _online_frontier_job())
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


def test_save_without_point_saves_the_anchor_not_the_selected_point(
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
    seed_job(
        clean_job_store,
        "save_selected",
        _online_frontier_job(
            solve_result=stale_solve_result,
            selected_frontier_point=1,
        ),
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

    # No point index is the job's own solve, from its summary: neither the
    # server-selected point nor the (stale) heavy solve result.
    assert resp.status_code == 200
    saved = json.loads(out_path.read_text())
    assert saved["total_objective"] == 99.0
    assert saved["total_constraints"] == {"volume": 0.88}
    assert saved["lambdas"] == {"volume": 0.1}
    assert "frontier_selection" not in saved


def test_mlflow_log_explicit_frontier_point_without_solver_or_solve_result(
    client,
    clean_job_store,
    tmp_path,
    monkeypatch,
):
    seed_job(clean_job_store, "mlflow_point", _online_frontier_job())
    store = use_local_mlflow_store(tmp_path, monkeypatch)

    resp = client.post(
        "/api/optimiser/mlflow/log",
        json={"job_id": "mlflow_point", "point_index": 0},
    )

    assert resp.status_code == 200
    run = store.get_run(resp.json()["run_id"])
    assert run.data.tags["frontier.selected_point_index"] == "0"
    assert run.data.metrics["total_objective"] == 123.0
    assert run.data.metrics["constraint.volume"] == 0.91


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
    solver = MagicMock()
    seed_job(
        clean_job_store,
        "apply_point",
        _with_as_solved_artifact(
            _online_frontier_job(
                quote_grid=quote_grid,
                solver=solver,
                solve_result=SimpleNamespace(),
            )
        ),
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

    job = clean_job_store.require_job("apply_point")
    assert job["selected_frontier_point"] == 1
    handle = job["artifact_handles"]["frontier_apply_result:1"]
    assert Path(handle["path"]).is_file()
    # Only the solve result is released, so other points stay inspectable.
    assert "solve_result" not in job
    assert job["quote_grid"] is quote_grid
    assert job["solver"] is solver


def test_concurrent_frontier_point_materialisations_run_one_at_a_time_and_keep_both_handles(
    client,
    clean_job_store,
):
    """Two points requested together are applied one after the other; both handles merge."""
    lock = threading.Lock()
    running: list[float] = []
    overlap: list[int] = []
    job = _with_as_solved_artifact(_online_frontier_job(quote_grid=MagicMock()))
    seed_job(clean_job_store, "apply_points_concurrently", job)

    def apply_from_grid(_grid, *, lambdas, constraints):
        del constraints
        point_value = float(lambdas["volume"])
        with lock:
            running.append(point_value)
            overlap.append(len(running))
        time.sleep(0.05)
        with lock:
            running.remove(point_value)
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
            future.result(timeout=10)
            for future in (pool.submit(request_point, 0), pool.submit(request_point, 1))
        ]

    assert [response.status_code for response in responses] == [200, 200]
    assert max(overlap) == 1
    handles = clean_job_store.require_job("apply_points_concurrently")["artifact_handles"]
    assert {key for key in handles if key.startswith("frontier_apply_result:")} == {
        "frontier_apply_result:0",
        "frontier_apply_result:1",
    }
    for handle in handles.values():
        assert Path(handle["path"]).is_file()


def test_frontier_point_materialisation_survives_store_copy_of_frontier_payload(
    client,
    clean_job_store,
):
    """A store may copy nested payloads without representing a recompute."""
    job_id = "apply_point_after_store_copy"
    seed_job(
        clean_job_store,
        job_id,
        _with_as_solved_artifact(
            _online_frontier_job(
                quote_grid=MagicMock(),
                frontier_generation=7,
            )
        ),
    )

    def apply_from_grid(_grid, *, lambdas, constraints):
        del lambdas, constraints
        current = clean_job_store.require_job(job_id)
        clean_job_store.atomic_update(
            job_id,
            {"frontier_data": dict(current["frontier_data"])},
            expected_status="completed",
        )
        return SimpleNamespace(
            dataframe=pl.DataFrame(
                {
                    "quote_id": ["q-store-copy"],
                    "optimal_scenario_value": [1.0],
                }
            )
        )

    with patch("price_contour.apply_from_grid", side_effect=apply_from_grid):
        response = client.post(
            "/api/optimiser/apply",
            json={"job_id": job_id, "point_index": 0},
        )

    assert response.status_code == 200, response.text
    assert clean_job_store.require_job(job_id)["frontier_generation"] == 7


def test_frontier_point_artifact_handles_are_capped_oldest_first():
    from haute.routes._optimiser_frontier import (
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
    seed_job(
        clean_job_store,
        "select_distinct",
        _online_frontier_job(
            frontier_data=_frontier_data(distinct_points),
        ),
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
        assert data["constraints"] == point["totals"]
        assert data["lambdas"] == point["lambdas"]
        assert data["converged"] is point["converged"]
        # The job's stored selected point must agree with the returned index.
        assert clean_job_store.require_job("select_distinct")["selected_frontier_point"] == index


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
    seed_job(
        clean_job_store,
        "apply_round_trip",
        _with_as_solved_artifact(_online_frontier_job(quote_grid=quote_grid)),
    )

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
    job = clean_job_store.require_job("apply_round_trip")
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
    seed_job(clean_job_store, "select_normalise", job)

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


def test_frontier_work_on_one_solve_never_waits_for_another(clean_job_store) -> None:
    """Each parent solve has its own frontier lock: slow work on one leaves the rest free."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeoutError

    from haute.routes._optimiser_frontier import OptimiserFrontierService

    service = OptimiserFrontierService(clean_job_store)
    for sweep, parent in (("sweep_a", "parent_a"), ("sweep_b", "parent_b")):
        seed_job(
            clean_job_store,
            sweep,
            {
                "status": "running",
                "job_type": "frontier_recompute",
                "parent_job_id": parent,
                "start_time": time.monotonic(),
                "timeout": None,
                "progress": 0.0,
                "message": "Computing efficient frontier",
                "created_at": time.time(),
            },
        )
    held = threading.Event()
    release = threading.Event()

    def hold_parent_a() -> None:
        with service.parent_lock("parent_a"):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold_parent_a, daemon=True)
    holder.start()
    assert held.wait(5)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            other = executor.submit(service.sweep_status, "sweep_b")
            same = executor.submit(service.sweep_status, "sweep_a")
            try:
                assert other.result(timeout=5)["status"] == "running"
                with pytest.raises(FutureTimeoutError):
                    same.result(timeout=0.2)
            finally:
                release.set()
            assert same.result(timeout=5)["status"] == "running"
    finally:
        release.set()
        holder.join(5)


# ---------------------------------------------------------------------------
# OPT-V09B: one point materialisation per job, latest wins, admission first
# ---------------------------------------------------------------------------


def _stepping_job(n_points: int, *, quote_grid: object | None = None) -> dict:
    points = [
        _frontier_point(objective=100.0 + index, lambda_volume=0.1 * (index + 1))
        for index in range(n_points)
    ]
    return _with_as_solved_artifact(
        _online_frontier_job(
            frontier_data=_frontier_data(points),
            quote_grid=MagicMock() if quote_grid is None else quote_grid,
        )
    )


def _point_of(lambdas: dict) -> int:
    return round(float(lambdas["volume"]) / 0.1) - 1


class _BlockingApply:
    """A fake ``apply_from_grid`` that records which point it applied and can hold one."""

    def __init__(self, hold: set[int] | None = None) -> None:
        self.hold = hold or set()
        self.applied: list[int] = []
        self.started = {index: threading.Event() for index in range(16)}
        self.release = threading.Event()
        self._lock = threading.Lock()
        self._running = 0
        self.max_running = 0

    def __call__(self, _grid, *, lambdas, constraints):
        del constraints
        point = _point_of(lambdas)
        with self._lock:
            self.applied.append(point)
            self._running += 1
            self.max_running = max(self.max_running, self._running)
        self.started[point].set()
        try:
            if point in self.hold:
                assert self.release.wait(10)
            return SimpleNamespace(
                dataframe=pl.DataFrame(
                    {
                        "quote_id": ["q0", "q1", "q2"],
                        "optimal_step": pl.Series([point % 3] * 3, dtype=pl.Int32),
                        "optimal_scenario_value": pl.Series([1.0] * 3, dtype=pl.Float32),
                        "optimal_objective": pl.Series([float(point)] * 3, dtype=pl.Float32),
                        "optimal_volume": pl.Series([0.3] * 3, dtype=pl.Float32),
                    }
                )
            )
        finally:
            with self._lock:
                self._running -= 1


def _apply(client, job_id: str, point_index: int):
    return client.post("/api/optimiser/apply", json={"job_id": job_id, "point_index": point_index})


def test_rapid_stepping_applies_a_then_c_and_replaces_b(clean_job_store):
    from fastapi import HTTPException

    from haute.routes.optimiser import _frontier_service

    seed_job(clean_job_store, "stepping", _stepping_job(3))
    fake = _BlockingApply(hold={0})
    with patch("price_contour.apply_from_grid", side_effect=fake):
        ticket_a = _frontier_service.request_point_apply("stepping", 0)
        assert fake.started[0].wait(10)
        ticket_b = _frontier_service.request_point_apply("stepping", 1)
        ticket_c = _frontier_service.request_point_apply("stepping", 2)

        with pytest.raises(HTTPException) as replaced:
            ticket_b.wait()
        assert replaced.value.status_code == 409
        assert replaced.value.detail["error_code"] == "frontier_point_apply_replaced"
        assert not fake.started[2].is_set()

        fake.release.set()
        ticket_a.wait()
        ticket_c.wait()

    assert fake.applied == [0, 2]
    assert fake.max_running == 1
    handles = clean_job_store.require_job("stepping")["artifact_handles"]
    assert {"frontier_apply_result:0", "frontier_apply_result:2"} <= set(handles)
    assert "frontier_apply_result:1" not in handles


def test_a_disconnecting_subscriber_detaches_only_itself(clean_job_store):
    from haute._execution_context import ExecutionCancellationToken, ExecutionCancelledError
    from haute.routes.optimiser import _frontier_service

    seed_job(clean_job_store, "shared_point", _stepping_job(2))
    fake = _BlockingApply(hold={1})
    token = ExecutionCancellationToken()
    outcome: dict = {}
    with patch("price_contour.apply_from_grid", side_effect=fake):
        leaving = _frontier_service.request_point_apply("shared_point", 1)
        staying = _frontier_service.request_point_apply("shared_point", 1)
        assert fake.started[1].wait(10)

        def wait_leaving() -> None:
            try:
                leaving.wait(token)
            except ExecutionCancelledError as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=wait_leaving, daemon=True)
        thread.start()
        token.cancel()
        thread.join(10)
        assert isinstance(outcome.get("error"), ExecutionCancelledError)

        fake.release.set()
        staying.wait()

    assert fake.applied == [1]
    assert (
        "frontier_apply_result:1" in clean_job_store.require_job("shared_point")["artifact_handles"]
    )


async def test_a_client_that_leaves_an_apply_request_leaves_the_apply_to_finish(clean_job_store):
    import asyncio
    from typing import cast

    from fastapi import HTTPException, Request

    from haute.routes.optimiser import apply_lambdas
    from haute.schemas import OptimiserApplyRequest

    seed_job(clean_job_store, "left_point", _stepping_job(2))
    fake = _BlockingApply(hold={1})

    class _LeavingRequest:
        async def is_disconnected(self) -> bool:
            return fake.started[1].is_set()

    with patch("price_contour.apply_from_grid", side_effect=fake):
        with pytest.raises(HTTPException) as left:
            await apply_lambdas(
                OptimiserApplyRequest(job_id="left_point", point_index=1),
                cast(Request, _LeavingRequest()),
            )
        assert left.value.status_code == 499
        fake.release.set()
        for _ in range(200):
            handles = clean_job_store.require_job("left_point")["artifact_handles"]
            if "frontier_apply_result:1" in handles:
                break
            await asyncio.sleep(0.02)

    # The apply ran to completion and its artifact is kept for the next request.
    assert "frontier_apply_result:1" in handles
    assert fake.applied == [1]
    assert clean_job_store.require_job("left_point").get("selected_frontier_point") is None


def test_an_over_budget_point_apply_is_refused_before_apply_from_grid(
    client, clean_job_store, monkeypatch
):
    seed_job(clean_job_store, "admit_point", _stepping_job(2))
    monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_BYTES", str(32 * 1024 * 1024))
    with patch("price_contour.apply_from_grid") as apply_from_grid:
        response = _apply(client, "admit_point", 1)

    assert response.status_code == 507, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "memory_limit"
    assert "point apply" in detail["reason"]
    apply_from_grid.assert_not_called()
    assert (
        "frontier_apply_result:1"
        not in clean_job_store.require_job("admit_point")["artifact_handles"]
    )


def test_a_retained_point_stays_available_after_the_grid_has_gone(client, clean_job_store):
    seed_job(clean_job_store, "retained_point", _stepping_job(2))
    fake = _BlockingApply()
    with patch("price_contour.apply_from_grid", side_effect=fake):
        assert _apply(client, "retained_point", 1).status_code == 200
        clean_job_store.clear_result_data("retained_point")
        again = _apply(client, "retained_point", 1)

    assert again.status_code == 200, again.text
    assert again.json()["from_artifact"] is True
    assert fake.applied == [1]


def test_an_unmaterialised_point_after_heavy_state_expiry_is_a_named_410(client, clean_job_store):
    seed_job(clean_job_store, "expired_point", _stepping_job(2))
    clean_job_store.clear_result_data("expired_point")
    with patch("price_contour.apply_from_grid") as apply_from_grid:
        response = _apply(client, "expired_point", 1)

    assert response.status_code == 410, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "frontier_point_unavailable"
    assert "re-run" in detail["message"].lower()
    apply_from_grid.assert_not_called()


def test_a_point_evicted_as_the_ninth_artifact_is_recomputed_while_the_grid_lives(
    client, clean_job_store
):
    seed_job(clean_job_store, "evicted_point", _stepping_job(10))
    fake = _BlockingApply()
    with patch("price_contour.apply_from_grid", side_effect=fake):
        first_path = None
        for point in range(9):
            assert _apply(client, "evicted_point", point).status_code == 200
            if point == 0:
                first_path = clean_job_store.require_job("evicted_point")["artifact_handles"][
                    "frontier_apply_result:0"
                ]["path"]
        handles = clean_job_store.require_job("evicted_point")["artifact_handles"]
        assert "frontier_apply_result:0" not in handles
        assert first_path is not None and not Path(first_path).exists()

        # The grid is alive: the evicted point is applied again.
        again = _apply(client, "evicted_point", 0)
        assert again.status_code == 200, again.text
        assert again.json()["from_artifact"] is False
        assert fake.applied == [*range(9), 0]

        # That evicted point 1; once the grid has gone it is a named 410, and a
        # retained point still answers from its artifact.
        clean_job_store.clear_result_data("evicted_point")
        gone = _apply(client, "evicted_point", 1)
        kept = _apply(client, "evicted_point", 8)

    assert gone.status_code == 410
    assert gone.json()["detail"]["error_code"] == "frontier_point_unavailable"
    assert kept.status_code == 200
    assert kept.json()["from_artifact"] is True


def test_an_eviction_during_a_read_waits_for_the_reader(client, clean_job_store):
    seed_job(clean_job_store, "read_evicted", _stepping_job(9))
    fake = _BlockingApply()
    with patch("price_contour.apply_from_grid", side_effect=fake):
        for point in range(8):
            assert _apply(client, "read_evicted", point).status_code == 200
        with clean_job_store.lease("read_evicted", "frontier_apply_result:0") as handle:
            assert _apply(client, "read_evicted", 8).status_code == 200
            handles = clean_job_store.require_job("read_evicted")["artifact_handles"]
            # Evicted from the job, but the reader still holds the file.
            assert "frontier_apply_result:0" not in handles
            assert pl.read_parquet(handle["path"]).height == 3
        assert not Path(handle["directory"]).exists()
