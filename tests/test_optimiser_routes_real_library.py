"""Real-price-contour integration contracts for the optimiser routes.

No solver mocks in this module.  Every test either drives the real
``price_contour`` library directly (shape contracts) or drives the HTTP
routes end-to-end with the real solver behind them.

Pinned here:

1. The real ``RatebookResult`` shape — it has NO per-quote ``dataframe``
   and NO ``iterations`` attribute.  The ``/apply`` ("Load detail") route
   therefore cannot produce per-quote detail for ratebook jobs and must
   fail with a clean 422 contract error instead of an opaque 500.
2. The real ``SolveResult.dataframe`` / ``ApplyResult.dataframe`` schema
   the online apply/detail path serves, end-to-end through solve →
   apply → artifact round-trip.
3. The frontier compute budget: the route-level limit must equal the
   library's ``max_total_points`` cap (10,000) so over-budget requests
   fail as a 422 naming both the projected grid size and the cap,
   before the solver is invoked — not as a library-internal 500.
4. ``/estimate`` cost: exactly one streaming aggregation scan on top of
   the pipeline execution (the old shape ran a separate full null-scan
   plus the aggregation scan).
"""

from __future__ import annotations

import inspect
import json
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest

from haute.routes._optimiser_limits import (
    FRONTIER_COMPUTE_LIMIT,
    FrontierComputeBudgetExceededError,
    enforce_frontier_compute_budget,
)
from tests.conftest import make_edge, make_file_input_config, make_graph
from tests.optimiser_fixtures import run_frontier_and_wait

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

# The exact per-quote schema the real library returns for a solve/apply
# with a single constraint named ``volume``.  ``optimal_<constraint>``
# columns are derived from constraint names.
REAL_APPLY_DETAIL_COLUMNS = [
    "quote_id",
    "optimal_step",
    "optimal_scenario_value",
    "optimal_objective",
    "optimal_volume",
]

_TERMINAL = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in _TERMINAL:
            return data
        time.sleep(0.02)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


def _scored_frame(
    n_quotes: int = 6,
    n_steps: int = 3,
    *,
    extra_constraint_columns: bool = False,
) -> pl.DataFrame:
    """Long-format scored frame in the shape price-contour expects."""
    rng = np.random.RandomState(7)
    quote_ids: list[str] = []
    steps: list[int] = []
    mults: list[float] = []
    incomes: list[float] = []
    volumes: list[float] = []
    scenario_values = np.linspace(0.8, 1.2, n_steps).astype(np.float32)
    for q in range(n_quotes):
        base_income = float(rng.uniform(100, 1000))
        base_volume = float(rng.uniform(0.5, 1.5))
        for s, m in enumerate(scenario_values):
            quote_ids.append(f"q_{q:04d}")
            steps.append(s)
            mults.append(float(m))
            incomes.append(base_income * float(m))
            volumes.append(base_volume * (2.0 - float(m)))
    columns = {
        "quote_id": quote_ids,
        "scenario_index": pl.Series(steps, dtype=pl.Int32),
        "scenario_value": pl.Series(mults, dtype=pl.Float32),
        "expected_income": pl.Series(incomes, dtype=pl.Float32),
        "volume": pl.Series(volumes, dtype=pl.Float32),
    }
    if extra_constraint_columns:
        columns["conversion"] = pl.Series(
            [v * 0.5 for v in volumes],
            dtype=pl.Float32,
        )
        columns["margin"] = pl.Series(
            [inc * 0.1 for inc in incomes],
            dtype=pl.Float32,
        )
    return pl.DataFrame(columns)


def _online_graph(data_path: str, config: dict | None = None) -> dict:
    default_config: dict = {
        "mode": "online",
        "objective": "expected_income",
        "constraints": {"volume": {"min": 0.90}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "max_iter": 20,
        "tolerance": 1e-4,
    }
    if config:
        default_config.update(config)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config(data_path),
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": default_config,
                    },
                },
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    )
    return graph.model_dump()


def _ratebook_fixture_paths(tmp_path, n_quotes: int = 9, n_steps: int = 3) -> tuple[str, str]:
    """Write scored + per-quote banding parquets for a real ratebook solve."""
    scored = _scored_frame(n_quotes=n_quotes, n_steps=n_steps)
    scored_path = tmp_path / "rb_scored.parquet"
    scored.write_parquet(scored_path)

    regions = ["North", "South", "East"]
    banding = pl.DataFrame(
        {
            "quote_id": [f"q_{q:04d}" for q in range(n_quotes)],
            "region": [regions[q % len(regions)] for q in range(n_quotes)],
        }
    )
    banding_path = tmp_path / "rb_banding.parquet"
    banding.write_parquet(banding_path)
    return str(scored_path), str(banding_path)


def _ratebook_graph(scored_path: str, banding_path: str) -> dict:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config(scored_path),
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "dataInput",
                        "config": make_file_input_config(banding_path),
                    },
                },
                {
                    "id": "opt",
                    "data": {
                        "label": "optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "ratebook",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.90}},
                            "quote_id": "quote_id",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "max_iter": 20,
                            "tolerance": 1e-4,
                            "max_cd_iterations": 3,
                            "cd_tolerance": 1e-3,
                            "factor_columns": [["region"]],
                            "banding_source": "banding",
                            "data_input": "source",
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "opt").model_dump(),
                make_edge("banding", "opt").model_dump(),
            ],
        }
    )
    return graph.model_dump()


def _solve_completed(client: TestClient, graph: dict) -> str:
    resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    status = _poll_until_done(client, job_id)
    assert status["status"] == "completed", f"Solve failed: {status.get('message')}"
    return job_id


# ---------------------------------------------------------------------------
# 1. Library result-shape contracts (direct, no HTTP)
# ---------------------------------------------------------------------------


class TestRealLibraryShapeContracts:
    """Pin the real price-contour result shapes the routes consume."""

    def test_ratebook_result_has_no_per_quote_dataframe(self) -> None:
        """The real ``RatebookResult`` carries factor tables and aggregates
        only — no ``dataframe`` and no ``iterations``.  The apply/detail
        route logic must never assume otherwise."""
        from price_contour import RatebookOptimiser

        df = _scored_frame(n_quotes=6, n_steps=3)
        factors = pl.DataFrame(
            {
                "quote_id": [f"q_{q:04d}" for q in range(6)],
                "region": ["N", "S", "N", "S", "N", "S"],
            }
        )
        solver = RatebookOptimiser(
            objective="expected_income",
            constraints={"volume": {"min": 0.90}},
            factor_columns=[["region"]],
            max_cd_iterations=2,
            max_iter=10,
        )
        result = solver.solve(df, factors)

        assert not hasattr(result, "dataframe"), (
            "RatebookResult grew a .dataframe attribute — the /apply route's "
            "ratebook 422 gate can now be revisited."
        )
        assert not hasattr(result, "iterations")
        assert set(vars(result).keys()) == {
            "factor_tables",
            "lambdas",
            "total_objective",
            "total_constraints",
            "baseline_objective",
            "baseline_constraints",
            "cd_iterations",
            "converged",
            "clamp_rate",
            "per_factor_results",
        }
        assert isinstance(result.factor_tables, dict)
        assert set(result.factor_tables) == {"region"}
        assert set(result.factor_tables["region"]) == {"N", "S"}
        assert isinstance(result.cd_iterations, int)
        assert isinstance(result.clamp_rate, float)
        assert isinstance(result.converged, bool)

    def test_online_solve_and_apply_dataframes_share_pinned_schema(self) -> None:
        """The online per-quote detail schema served by ``/apply``."""
        from price_contour import OnlineOptimiser, apply_from_grid

        df = _scored_frame(n_quotes=6, n_steps=3)
        solver = OnlineOptimiser(
            objective="expected_income",
            constraints={"volume": {"min": 0.90}},
            max_iter=20,
            tolerance=1e-4,
        )
        solve_result = solver.solve(df)
        assert solve_result.dataframe.columns == REAL_APPLY_DETAIL_COLUMNS
        assert solve_result.dataframe.height == 6

        apply_result = apply_from_grid(
            solve_result.grid,
            lambdas=solve_result.lambdas,
            constraints={"volume": {"min": 0.90}},
        )
        assert apply_result.dataframe.columns == REAL_APPLY_DETAIL_COLUMNS
        assert apply_result.dataframe.height == 6

    def test_route_budget_equals_library_frontier_cap(self) -> None:
        """The route-level compute budget must equal the library's
        ``max_total_points`` default for BOTH frontier implementations.

        The routes call ``solver.frontier(...)`` without overriding
        ``max_total_points``, so any route-level allowance above the
        library default is a region where requests pass the route gate
        and then explode inside the library as an opaque 500.
        """
        from price_contour import OnlineOptimiser, RatebookOptimiser

        online_cap = (
            inspect.signature(OnlineOptimiser.frontier).parameters["max_total_points"].default
        )
        ratebook_cap = (
            inspect.signature(RatebookOptimiser.frontier).parameters["max_total_points"].default
        )
        assert online_cap == ratebook_cap == FRONTIER_COMPUTE_LIMIT, (
            f"Route budget {FRONTIER_COMPUTE_LIMIT:,} diverges from the "
            f"price-contour caps (online={online_cap:,}, ratebook={ratebook_cap:,}); "
            "requests between the smaller and larger value become opaque 500s."
        )


# ---------------------------------------------------------------------------
# 2. Ratebook apply / "Load detail" contract (HTTP, real solver)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestRatebookApplyDetailContract:
    """The UI's "Load detail" button posts ``/apply``.  For ratebook jobs
    the real library has no per-quote detail to serve, so the backend
    must answer with an explicit 422 contract error — never a 500 and
    never silently-wrong online-style output."""

    def test_apply_without_point_is_clean_contract_error(self, client, tmp_path):
        scored_path, banding_path = _ratebook_fixture_paths(tmp_path)
        job_id = _solve_completed(client, _ratebook_graph(scored_path, banding_path))

        resp = client.post("/api/optimiser/apply", json={"job_id": job_id})

        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "ratebook" in detail.lower()
        assert "factor tables" in detail.lower()

    def test_apply_frontier_point_is_clean_contract_error(
        self,
        client,
        tmp_path,
        clean_job_store,
    ):
        scored_path, banding_path = _ratebook_fixture_paths(tmp_path)
        job_id = _solve_completed(client, _ratebook_graph(scored_path, banding_path))

        frontier_status = run_frontier_and_wait(
            client,
            {
                "job_id": job_id,
                "threshold_ranges": {"volume": [4.0, 6.0]},
                "n_points_per_dim": 2,
            },
        )
        assert frontier_status["status"] == "completed", frontier_status.get("message", "")
        assert frontier_status["result"]["n_points"] == 2

        resp = client.post(
            "/api/optimiser/apply",
            json={"job_id": job_id, "point_index": 0},
        )

        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "ratebook" in detail.lower()
        assert "factor tables" in detail.lower()
        # The gate fires before any materialisation work: no frontier apply
        # artifact appears and no frontier point gets selected as a side
        # effect of the rejected detail request.
        job = clean_job_store.jobs[job_id]
        assert "frontier_apply_result:0" not in job.get("artifact_handles", {})
        assert job.get("selected_frontier_point") is None

    def test_select_and_save_work_against_real_ratebook_result(
        self,
        client,
        tmp_path,
    ):
        """Save and frontier-point selection (the supported ratebook
        actions) must keep working against the REAL ``RatebookResult``
        shape — the factor-table flow is the ratebook detail surface."""
        scored_path, banding_path = _ratebook_fixture_paths(tmp_path)
        job_id = _solve_completed(client, _ratebook_graph(scored_path, banding_path))

        frontier_status = run_frontier_and_wait(
            client,
            {
                "job_id": job_id,
                "threshold_ranges": {"volume": [4.0, 6.0]},
                "n_points_per_dim": 2,
            },
        )
        assert frontier_status["status"] == "completed", frontier_status.get("message", "")

        select_resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )
        assert select_resp.status_code == 200, select_resp.text
        tables = select_resp.json()["factor_tables"]
        assert set(tables) == {"region"}
        rows = tables["region"]
        assert {row["__factor_group__"] for row in rows} == {"North", "South", "East"}
        for row in rows:
            assert set(row) == {"__factor_group__", "optimal_scenario_value", "quote_count"}
            assert isinstance(row["optimal_scenario_value"], float)
            assert row["quote_count"] == 3
        assert isinstance(select_resp.json()["cd_iterations"], int)
        assert isinstance(select_resp.json()["clamp_rate"], float)

        out_path = tmp_path / "rb_selected.json"
        save_resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": job_id,
                "output_path": str(out_path),
                "point_index": 0,
            },
        )
        assert save_resp.status_code == 200, save_resp.text
        saved = json.loads(out_path.read_text())
        assert saved["mode"] == "ratebook"
        assert isinstance(saved["lambdas"], dict)
        assert isinstance(saved["converged"], bool)
        # The real ratebook frontier exposes its CD count under the
        # online-compat ``iterations`` points column, so a point-save
        # carries an integer here (the base-result save pins None).
        assert isinstance(saved["iterations"], int)
        assert isinstance(saved["cd_iterations"], int)
        assert saved["frontier_selection"]["point_index"] == 0
        saved_rows = saved["factor_tables"]["region"]
        assert {row["__factor_group__"] for row in saved_rows} == {"North", "South", "East"}

    def test_save_without_point_pins_real_artifact_shape(self, client, tmp_path):
        scored_path, banding_path = _ratebook_fixture_paths(tmp_path)
        job_id = _solve_completed(client, _ratebook_graph(scored_path, banding_path))

        out_path = tmp_path / "rb_base.json"
        save_resp = client.post(
            "/api/optimiser/save",
            json={"job_id": job_id, "output_path": str(out_path)},
        )
        assert save_resp.status_code == 200, save_resp.text
        saved = json.loads(out_path.read_text())
        assert saved["mode"] == "ratebook"
        assert isinstance(saved["cd_iterations"], int)
        assert saved["iterations"] is None
        assert isinstance(saved["clamp_rate"], float)
        rows = saved["factor_tables"]["region"]
        assert {row["__factor_group__"] for row in rows} == {"North", "South", "East"}
        for row in rows:
            assert set(row) == {"__factor_group__", "optimal_scenario_value", "quote_count"}


# ---------------------------------------------------------------------------
# 3. Online apply / "Load detail" against the real result schema
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestOnlineApplyDetailRealSchema:
    def test_apply_detail_serves_real_result_schema_and_artifact_roundtrip(
        self,
        client,
        tmp_path,
    ):
        """solve → apply (live solve_result) → apply again (parquet artifact).

        Both responses must carry the pinned real per-quote schema and
        identical rows; the second response must come from the persisted
        artifact after heavy state is cleared by the first apply.
        """
        df = _scored_frame(n_quotes=7, n_steps=3)
        path = tmp_path / "online_scored.parquet"
        df.write_parquet(path)
        job_id = _solve_completed(client, _online_graph(str(path)))

        first = client.post("/api/optimiser/apply", json={"job_id": job_id})
        assert first.status_code == 200, first.text
        data = first.json()
        assert data["status"] == "ok"
        assert data["from_artifact"] is False
        assert data["row_count"] == 7
        assert data["preview_row_count"] == 7
        assert list(data["preview"][0].keys()) == REAL_APPLY_DETAIL_COLUMNS

        second = client.post("/api/optimiser/apply", json={"job_id": job_id})
        assert second.status_code == 200, second.text
        replay = second.json()
        assert replay["from_artifact"] is True
        assert replay["preview"] == data["preview"]
        assert replay["total_objective"] == pytest.approx(data["total_objective"])
        assert replay["constraints"] == data["constraints"]

    def test_frontier_point_apply_real_artifact_roundtrip(self, client, tmp_path):
        """Real frontier sweep → frontier-point apply → cached artifact."""
        df = _scored_frame(n_quotes=5, n_steps=3)
        path = tmp_path / "online_frontier_scored.parquet"
        df.write_parquet(path)
        job_id = _solve_completed(client, _online_graph(str(path)))

        frontier_status = run_frontier_and_wait(
            client,
            {
                "job_id": job_id,
                "threshold_ranges": {"volume": [4.0, 6.0]},
                "n_points_per_dim": 3,
            },
        )
        assert frontier_status["status"] == "completed", frontier_status.get("message", "")
        assert frontier_status["result"]["n_points"] == 3

        first = client.post(
            "/api/optimiser/apply",
            json={"job_id": job_id, "point_index": 1},
        )
        assert first.status_code == 200, first.text
        data = first.json()
        assert data["from_artifact"] is False
        assert data["row_count"] == 5
        assert list(data["preview"][0].keys()) == REAL_APPLY_DETAIL_COLUMNS

        second = client.post(
            "/api/optimiser/apply",
            json={"job_id": job_id, "point_index": 1},
        )
        assert second.status_code == 200, second.text
        replay = second.json()
        assert replay["from_artifact"] is True
        assert replay["preview"] == data["preview"]


# ---------------------------------------------------------------------------
# 4. Frontier compute budget — RED (opaque 500) → GREEN (422, both numbers)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestFrontierComputeBudgetContract:
    def test_frontier_above_library_cap_is_422_naming_both_numbers(
        self,
        client,
        tmp_path,
    ):
        """22³ = 10,648 grid points sits above the library cap (10,000)
        but below the old route budget (100,000).  Before the fix this
        request passed the route gate and died inside price-contour as an
        opaque 500; it must now be a 422 naming the projected grid size
        and the cap, raised before the solver is invoked."""
        df = _scored_frame(n_quotes=5, n_steps=3, extra_constraint_columns=True)
        path = tmp_path / "budget_scored.parquet"
        df.write_parquet(path)
        graph = _online_graph(
            str(path),
            config={
                "constraints": {
                    "volume": {"min": 0.1},
                    "conversion": {"min": 0.1},
                    "margin": {"min": 0.1},
                },
            },
        )
        job_id = _solve_completed(client, graph)

        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": job_id,
                "threshold_ranges": {
                    "volume": [4.0, 6.0],
                    "conversion": [2.0, 3.0],
                    "margin": [100.0, 200.0],
                },
                "n_points_per_dim": 22,
            },
        )

        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "10,648" in detail
        assert "10,000" in detail
        assert "frontier compute budget" in detail.lower()

        # The job must remain a healthy completed job — the rejection is a
        # request-level contract error, not a job failure.
        status = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "completed"

    def test_small_real_frontier_still_passes_the_gate(self, client, tmp_path):
        """A within-budget sweep runs the real solver end-to-end."""
        df = _scored_frame(n_quotes=5, n_steps=3)
        path = tmp_path / "budget_ok_scored.parquet"
        df.write_parquet(path)
        job_id = _solve_completed(client, _online_graph(str(path)))

        status = run_frontier_and_wait(
            client,
            {
                "job_id": job_id,
                "threshold_ranges": {"volume": [4.0, 6.0]},
                "n_points_per_dim": 3,
            },
        )
        assert status["status"] == "completed", status.get("message", "")
        assert status["result"]["n_points"] == 3

    def test_budget_error_names_exact_projection_when_computable(self) -> None:
        with pytest.raises(FrontierComputeBudgetExceededError) as exc:
            enforce_frontier_compute_budget(n_points_per_dim=22, n_constraints=3)
        message = str(exc.value)
        assert "10,648" in message
        assert "10,000" in message
        assert "at least" not in message

    def test_budget_error_reports_lower_bound_on_early_overflow(self) -> None:
        """When an intermediate product already exceeds the cap, the
        message reports the partial product as a lower bound instead of
        materialising an astronomically large exact count."""
        with pytest.raises(FrontierComputeBudgetExceededError) as exc:
            enforce_frontier_compute_budget(n_points_per_dim=200, n_constraints=3)
        message = str(exc.value)
        assert "at least 40,000" in message
        assert "10,000" in message

    def test_budget_boundary_is_inclusive_like_the_library(self) -> None:
        """The library only rejects > max_total_points, so exactly-at-cap
        requests must pass the route gate too."""
        enforce_frontier_compute_budget(
            n_points_per_dim=FRONTIER_COMPUTE_LIMIT,
            n_constraints=1,
        )
        with pytest.raises(FrontierComputeBudgetExceededError):
            enforce_frontier_compute_budget(
                n_points_per_dim=FRONTIER_COMPUTE_LIMIT + 1,
                n_constraints=1,
            )


# ---------------------------------------------------------------------------
# 5. /estimate cost contract — one aggregation scan, not two
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestEstimateSingleScanContract:
    def _ragged_graph(self, tmp_path) -> dict:
        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", "q2", "q2", "q2"],
                "scenario_index": pl.Series([0, 1, 0, 1, 2], dtype=pl.Int32),
                "scenario_value": pl.Series([0.9, 1.1, 0.8, 1.0, 1.2], dtype=pl.Float32),
                "expected_income": pl.Series(
                    [100.0, 110.0, 90.0, 95.0, 98.0],
                    dtype=pl.Float32,
                ),
                "volume": pl.Series([1.0, 0.9, 1.2, 1.1, 1.0], dtype=pl.Float32),
            }
        )
        path = tmp_path / "estimate_ragged.parquet"
        df.write_parquet(path)
        return _online_graph(str(path))

    def test_estimate_executes_exactly_one_streaming_scan(self, client, tmp_path):
        """The estimate runs the pipeline once plus ONE streaming
        aggregation collect.  The old shape additionally ran the service's
        standalone null-quote_id scan — a second full pass over the input.
        """
        import haute.routes._optimiser_service as service_mod
        import haute.routes.optimiser as routes_mod

        graph = self._ragged_graph(tmp_path)
        real_collect = routes_mod.streaming_collect

        with (
            patch.object(
                routes_mod,
                "streaming_collect",
                side_effect=real_collect,
            ) as route_collect,
            patch.object(
                service_mod,
                "streaming_collect",
                side_effect=real_collect,
            ) as service_collect,
        ):
            resp = client.post(
                "/api/optimiser/estimate",
                json={"graph": graph, "node_id": "opt"},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["quote_count"] == 2
        assert data["scenarios_per_quote_min"] == 2
        assert data["scenarios_per_quote_max"] == 3
        assert data["scenarios_per_quote_mean"] == pytest.approx(2.5)
        assert data["expanded_row_count"] == 5

        assert route_collect.call_count == 1, (
            f"estimate ran {route_collect.call_count} route-side collects; "
            "the contract is a single aggregation scan"
        )
        assert service_collect.call_count == 0, (
            f"estimate ran {service_collect.call_count} service-side collects; "
            "the null-quote_id check must be folded into the single "
            "aggregation scan, not run as a separate full pass"
        )

    def test_estimate_null_quote_id_rejected_within_single_scan(self, client, tmp_path):
        """Folding the null check into the aggregation scan must not relax
        the loud null-quote_id rejection (same message contract)."""
        import haute.routes._optimiser_service as service_mod
        import haute.routes.optimiser as routes_mod

        df = pl.DataFrame(
            {
                "quote_id": ["q1", "q1", None, None, "q2", "q2"],
                "scenario_index": pl.Series([0, 1, 0, 1, 0, 1], dtype=pl.Int32),
                "scenario_value": pl.Series(
                    [0.9, 1.1, 0.9, 1.1, 0.9, 1.1],
                    dtype=pl.Float32,
                ),
                "expected_income": pl.Series(
                    [100.0, 110.0, 999.0, 999.0, 200.0, 220.0],
                    dtype=pl.Float32,
                ),
                "volume": pl.Series([0.9, 0.8, 0.1, 0.1, 0.95, 0.9], dtype=pl.Float32),
            }
        )
        path = tmp_path / "estimate_null_qid.parquet"
        df.write_parquet(path)
        graph = _online_graph(str(path))
        real_collect = routes_mod.streaming_collect

        with (
            patch.object(
                routes_mod,
                "streaming_collect",
                side_effect=real_collect,
            ) as route_collect,
            patch.object(
                service_mod,
                "streaming_collect",
                side_effect=real_collect,
            ) as service_collect,
        ):
            resp = client.post(
                "/api/optimiser/estimate",
                json={"graph": graph, "node_id": "opt"},
            )

        assert resp.status_code == 400
        assert "Null quote_id values found in optimiser input (2 rows)." in (resp.json()["detail"])
        assert route_collect.call_count + service_collect.call_count == 1
