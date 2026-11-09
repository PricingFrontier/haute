"""Optimiser frontiers: computing them in a solve and selecting a point.

A frontier-enabled solve computes its frontier, ``POST /api/optimiser/frontier/select`` selects a
point idempotently from its summary, the limited frontier payload is capped, and a frontier point's
baselines must come from its solve.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute.routes._optimiser_limits import (
    FRONTIER_POINT_LIMIT,
)
from tests import _optimiser_route_helpers
from tests._optimiser_route_helpers import (
    _expected_region_factor_tables,
    _frontier_point_summary,
    _make_optimiser_graph,
    _make_ratebook_frontier_materialisation_job,
    _poll_until_done,
    _ratebook_frontier_point_summary,
)
from tests.job_store_support import replace_job, seed_job
from tests.optimiser_fixtures import (
    SOLVE_SCENARIO_GRID,
    library_frontier_frame,
    make_input_summary,
    typed_frontier_point,
)

# Fixtures shared with the other modules split from one suite.
scored_data = _optimiser_route_helpers.scored_data


# ---------------------------------------------------------------------------
# Ratebook mode
# ---------------------------------------------------------------------------


def _append_second_ratebook_frontier_point(job: dict[str, Any]) -> None:
    """Append a second retained ratebook point together with its kept tables."""
    job["frontier_data"]["n_points"] = 2
    job["frontier_data"]["points_returned"] = 2
    job["frontier_data"]["points"].append(
        _ratebook_frontier_point_summary(
            lambda_volume=0.9,
            total_objective=240.0,
            total_volume=1.03,
            threshold_volume=1.02,
            iterations=7,
            clamp_rate=0.02,
        )
    )
    job["frontier_factor_tables"].append({"region": {"North": 1.12, "South": 0.98}})


# ---------------------------------------------------------------------------
# Frontier-in-solve and /frontier/select tests
# ---------------------------------------------------------------------------


class TestFrontierInSolve:
    """Verify that frontier data is computed as part of frontier-enabled solves."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_status_includes_frontier(self, client, scored_data):
        """After a successful solve with constraints, status includes frontier data."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed"
        # Frontier should be present in the result dict
        result = status["result"]
        assert result is not None
        assert "frontier" in result
        frontier = result["frontier"]
        assert frontier is not None
        assert frontier["n_points"] > 0
        assert len(frontier["points"]) == frontier["n_points"]
        assert "volume" in frontier["constraint_names"]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_status_frontier_response_field(self, client, scored_data):
        """The top-level 'frontier' field on the status response is populated."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed"
        assert "frontier" in status
        frontier = status["frontier"]
        assert frontier is not None
        assert frontier["status"] == "ok"
        assert frontier["n_points"] > 0

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_with_empty_constraints_completes_without_frontier(
        self,
        client,
        scored_data,
    ):
        """Pin the contract for the no-constraints case end-to-end.

        Previously this test silently returned on any non-completed
        outcome — meaning a regression that made /solve crash for empty
        constraints would still pass.  Now we assert the full happy path:
        the solve completes, no frontier is computed, and the result
        contains the baseline objective with empty constraint maps.
        """
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "objective": "expected_income",
                "constraints": {},
                "max_iter": 5,
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed", (
            f"Empty-constraints solve failed: {status.get('message')!r}"
        )
        result = status["result"]
        assert result is not None
        # No frontier should be computed when there are no constraints to vary.
        assert result.get("frontier") is None
        # Result still contains the baseline objective and empty-but-defined
        # constraint maps so the UI can render a coherent summary.
        assert isinstance(result.get("baseline_objective"), (int, float))
        assert result.get("constraints") == {}
        assert result.get("baseline_constraints") == {}
        # No lambdas because there's nothing to enforce.
        assert result.get("lambdas") == {}

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_solve_with_empty_constraints_computes_no_frontier(
        self,
        client,
        scored_data,
    ):
        """With no constraints nothing is swept, so no frontier is computed
        and no spurious ``frontier_error`` is reported."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "objective": "expected_income",
                "constraints": {},
                "frontier_ranges": {},  # no constraints → no ranges
                "max_iter": 5,
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed"
        result = status["result"]
        assert result.get("frontier") is None
        assert result.get("frontier_error") is None

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_swept_constraint_is_solved_at_its_range_start(
        self,
        client,
        scored_data,
        clean_job_store,
    ):
        """A swept constraint's hidden bound never reaches the solve: the job
        runs it at the start of its range, so the as-solved result sits on the
        frontier."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "constraints": {"volume": {"min": 1.0}},
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
                "frontier_steps": 3,
                "max_iter": 5,
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})

        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)

        assert status["status"] == "completed"
        assert clean_job_store.require_job(job_id)["config"]["constraints"] == {
            "volume": {"min": 40.0}
        }


class TestFrontierSelect:
    """Tests for POST /api/optimiser/frontier/select."""

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_frontier_point(self, client, scored_data):
        """A completed solve keeps runtime state so a frontier point can be selected."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        assert status["status"] == "completed"

        frontier = status["result"]["frontier"]
        assert frontier is not None
        n_points = frontier["n_points"]
        assert n_points > 0

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": 0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["lambdas"]
        assert isinstance(data["converged"], bool)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_last_frontier_point(self, client, scored_data):
        """Selecting the final frontier point after solve succeeds."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        status = _poll_until_done(client, job_id)
        frontier = status["result"]["frontier"]
        n_points = frontier["n_points"]

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": n_points - 1,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["lambdas"]

    def test_select_out_of_range(self, client, clean_job_store):
        """Point index >= n_points returns 400."""
        seed_job(
            clean_job_store,
            "sel_oob",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "solver": MagicMock(),
                "quote_grid": MagicMock(),
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        typed_frontier_point({"total_objective": 1.0, "lambda_volume": 0.5})
                    ],
                    "n_points": 1,
                    "constraint_names": ["volume"],
                },
                "result": {},
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "sel_oob",
                "point_index": 5,
            },
        )
        assert resp.status_code == 400
        assert "out of range" in resp.json()["detail"].lower()

    def test_select_point_beyond_capped_returned_points_is_explicit(
        self,
        client,
        clean_job_store,
    ):
        seed_job(
            clean_job_store,
            "sel_capped",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "solver": MagicMock(),
                "quote_grid": MagicMock(),
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        typed_frontier_point({"total_objective": 1.0, "lambda_volume": 0.5})
                    ],
                    "n_points": 3,
                    "points_returned": 1,
                    "points_limit": 1,
                    "points_truncated": True,
                    "constraint_names": ["volume"],
                },
                "result": {},
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "sel_capped",
                "point_index": 2,
            },
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "not available" in detail
        assert "1 of 3" in detail
        assert "limit 1" in detail

    def test_select_ratebook_frontier_point_uses_factors(
        self,
        client,
        clean_job_store,
    ):
        """Ratebook frontier selection uses stored summaries without re-solving."""
        factor_contexts = SimpleNamespace(n_quotes=2, factor_specs=[["region"]])
        mock_grid = MagicMock()
        mock_solver = MagicMock()
        mock_solver.solve.return_value = SimpleNamespace(
            total_objective=120.0,
            baseline_objective=95.0,
            total_constraints={"volume": 0.97},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.3},
            converged=True,
        )
        seed_job(
            clean_job_store,
            "rb_select",
            {
                "status": "completed",
                "config": {
                    "mode": "ratebook",
                    "factor_columns": [["region"]],
                    "constraints": {"volume": {"min": 0.9}},
                },
                "solver": mock_solver,
                "quote_grid": mock_grid,
                "ratebook_factor_contexts": factor_contexts,
                "factor_columns_valid": [["region"]],
                "artifact_handles": {},
                "result": {
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
                "frontier_data": {
                    "status": "ok",
                    "n_points": 1,
                    "points_returned": 1,
                    "points_limit": FRONTIER_POINT_LIMIT,
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.3,
                            total_objective=120.0,
                            total_volume=0.97,
                        )
                    ],
                    "constraint_names": ["volume"],
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "rb_select", "point_index": 0},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["constraints"] == {"volume": 0.97}
        mock_solver.solve.assert_not_called()

    def test_select_ratebook_frontier_point_materialises_tables_in_banding_order(
        self,
        client,
        clean_job_store,
    ):
        """On-demand Rates tab materialisation keeps the stored band order."""
        mock_solver, _mock_grid, _factor_contexts = _make_ratebook_frontier_materialisation_job(
            clean_job_store, "rb_select_order"
        )
        replace_job(
            clean_job_store,
            "rb_select_order",
            lambda job: {**job, "factor_level_order": {"region": ["South", "North"]}},
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_select_order",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )

        assert resp.status_code == 200
        rows = resp.json()["factor_tables"]["region"]
        assert [row["__factor_group__"] for row in rows] == ["South", "North"]
        assert [row["quote_count"] for row in rows] == [1, 1]
        assert [row["optimal_scenario_value"] for row in rows] == [0.92, 1.08]
        mock_solver.solve.assert_not_called()

    def test_select_negative_index(self, client, clean_job_store):
        """Negative point index returns 422 (Pydantic validation via Field(ge=0))."""
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "sel_neg",
                "point_index": -1,
            },
        )
        assert resp.status_code == 422

    def test_select_no_frontier_data(self, client, clean_job_store):
        """Select when no frontier data returns 400."""
        seed_job(
            clean_job_store,
            "sel_nf",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "solver": MagicMock(),
                "quote_grid": MagicMock(),
                "frontier_data": None,
                "result": {},
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "sel_nf",
                "point_index": 0,
            },
        )
        assert resp.status_code == 400
        assert "no frontier" in resp.json()["detail"].lower()

    def test_select_missing_job(self, client):
        """Non-existent job returns 404."""
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "nonexistent",
                "point_index": 0,
            },
        )
        assert resp.status_code == 404

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_select_after_solve_persists_selected_point(self, client, scored_data):
        """Selecting after solve updates the completed job's active result."""
        graph = _make_optimiser_graph(
            scored_data,
            config={
                "frontier_ranges": {"volume": {"min": 40.0, "max": 60.0}},
            },
        )
        resp = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
        job_id = resp.json()["job_id"]
        _poll_until_done(client, job_id)

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": job_id,
                "point_index": 0,
            },
        )
        assert resp.status_code == 200

        from haute.routes.optimiser import _store

        job = _store.require_job(job_id)
        assert job["selected_frontier_point"] == 0
        assert job["result"]["selected_frontier_point"] == 0

    def test_select_does_not_touch_heavy_object_ttl_and_survives_expiry(
        self,
        client,
        clean_job_store,
    ):
        solver = MagicMock()
        seed_job(
            clean_job_store,
            "sel_touch_ttl",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "created_at": 100.0,
                "completed_at": 100.0,
                "heavy_objects_expires_at": 1000.0,
                "solver": solver,
                "solve_result": object(),
                "quote_grid": MagicMock(),
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.4,
                            total_objective=120.0,
                            total_volume=0.9,
                        ),
                        _frontier_point_summary(
                            lambda_volume=0.5,
                            total_objective=125.0,
                            total_volume=0.95,
                        ),
                    ],
                    "n_points": 2,
                    "constraint_names": ["volume"],
                },
                "result": {
                    "total_objective": 100.0,
                    "baseline_objective": 95.0,
                    "constraints": {"volume": 0.85},
                    "baseline_constraints": {"volume": 0.8},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.2},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "artifact_handles": {},
            },
        )

        with patch("haute.routes._job_store.time.time", return_value=940.0):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_touch_ttl", "point_index": 0},
            )
            job_at_940 = clean_job_store.require_job("sel_touch_ttl")
        assert resp.status_code == 200
        assert job_at_940["heavy_objects_expires_at"] == pytest.approx(1000.0)
        solver.solve.assert_not_called()

        with patch("haute.routes._job_store.time.time", return_value=1780.0):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_touch_ttl", "point_index": 0},
            )
            job_at_1780 = clean_job_store.require_job("sel_touch_ttl")
        assert resp.status_code == 200
        assert "heavy_objects_expires_at" not in job_at_1780

        with patch("haute.routes._job_store.time.time", return_value=2741.0):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_touch_ttl", "point_index": 1},
            )
            job = clean_job_store.require_job("sel_touch_ttl")

        assert resp.status_code == 200
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job
        assert job["result"]["total_objective"] == 125.0

    def test_select_does_not_reserve_heavy_objects_or_run_solver(
        self,
        client,
        clean_job_store,
    ):
        current_time = {"value": 940.0}
        solver = MagicMock()
        seed_job(
            clean_job_store,
            "sel_touch_before_work",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "created_at": 100.0,
                "completed_at": 100.0,
                "heavy_objects_expires_at": 1000.0,
                "solver": solver,
                "solve_result": object(),
                "quote_grid": MagicMock(),
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.4,
                            total_objective=120.0,
                            total_volume=0.9,
                        )
                    ],
                    "n_points": 1,
                    "constraint_names": ["volume"],
                },
                "result": {
                    "total_objective": 100.0,
                    "baseline_objective": 95.0,
                    "constraints": {"volume": 0.85},
                    "baseline_constraints": {"volume": 0.8},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.2},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "artifact_handles": {},
            },
        )

        with patch(
            "haute.routes._job_store.time.time",
            side_effect=lambda: current_time["value"],
        ):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_touch_before_work", "point_index": 0},
            )
            job = clean_job_store.get_job("sel_touch_before_work")

        assert resp.status_code == 200
        assert job is not None
        assert "solver" in job
        assert "solve_result" not in job
        assert "quote_grid" in job
        assert job["heavy_objects_expires_at"] == pytest.approx(1000.0)
        solver.solve.assert_not_called()

    def test_select_does_not_materialise_apply_artifacts(
        self,
        client,
        clean_job_store,
    ):
        solver = MagicMock()
        seed_job(
            clean_job_store,
            "sel_cleanup_primary",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "solver": solver,
                "solve_result": object(),
                "quote_grid": MagicMock(),
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.4,
                            total_objective=120.0,
                            total_volume=0.9,
                        )
                    ],
                    "n_points": 1,
                    "constraint_names": ["volume"],
                },
                "result": {
                    "total_objective": 100.0,
                    "baseline_objective": 95.0,
                    "constraints": {"volume": 0.85},
                    "baseline_constraints": {"volume": 0.8},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.2},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "artifact_handles": {},
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

        with (
            patch(
                "haute.routes._optimiser_frontier._persist_apply_frame_artifact",
                side_effect=AssertionError("selection must not persist apply artifacts"),
            ),
            patch.object(
                clean_job_store,
                "atomic_update_if_heavy_present",
                side_effect=AssertionError("selection must not reserve heavy state"),
            ),
        ):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "sel_cleanup_primary", "point_index": 0},
            )

        assert resp.status_code == 200
        solver.solve.assert_not_called()


# ---------------------------------------------------------------------------
# The solve-time frontier payload
# ---------------------------------------------------------------------------


def _volume_points(count: int) -> Any:
    return library_frontier_frame(
        [
            {
                "total_objective": float(i),
                "total_volume": 0.9,
                "lambda_volume": i / 100 + 0.01,
                "bound_volume": 0.9,
                "converged": True,
            }
            for i in range(count)
        ]
    )


class TestLimitedFrontierPayload:
    _KINDS: ClassVar[dict[str, Any]] = {"volume": "min"}

    def _payload(self, points: Any) -> dict[str, Any]:
        from haute.routes._optimiser_limits import limited_frontier_payload

        return limited_frontier_payload(
            points, mode="online", constraint_kinds=self._KINDS, swept_axes=["volume"]
        )

    def test_payload_is_capped_with_total_point_metadata(self):
        data = self._payload(_volume_points(FRONTIER_POINT_LIMIT + 1))

        assert data["n_points"] == FRONTIER_POINT_LIMIT + 1
        assert len(data["points"]) == FRONTIER_POINT_LIMIT
        assert len(data["point_summaries"]) == FRONTIER_POINT_LIMIT
        assert data["points_returned"] == FRONTIER_POINT_LIMIT
        assert data["points_limit"] == FRONTIER_POINT_LIMIT
        assert data["points_truncated"] is True
        assert data["constraint_names"] == ["volume"]
        assert data["swept_axes"] == ["volume"]

    def test_payload_slices_before_serialising_points(self):
        class HugePoints:
            def __len__(self) -> int:
                return FRONTIER_POINT_LIMIT + 1

            def head(self, n: int) -> Any:
                assert n == FRONTIER_POINT_LIMIT
                return _volume_points(n)

            def to_dicts(self) -> Any:
                raise AssertionError("Full frontier must not be serialised")

        data = self._payload(HugePoints())

        assert data["n_points"] == FRONTIER_POINT_LIMIT + 1
        assert len(data["points"]) == FRONTIER_POINT_LIMIT
        assert data["points_truncated"] is True

    def test_lambda_zero_and_precision_round_trip(self):
        points = library_frontier_frame(
            [
                {
                    "total_objective": 50.0,
                    "total_volume": 0.8,
                    "lambda_volume": 0.0,
                    "bound_volume": 0.9,
                    "converged": True,
                },
                {
                    "total_objective": 60.0,
                    "total_volume": 0.95,
                    "lambda_volume": 0.7128,
                    "bound_volume": 0.9,
                    "converged": True,
                },
            ],
            constraint_names=["volume"],
        )

        from haute.schemas import OptimiserFrontierResponse

        data = OptimiserFrontierResponse(**self._payload(points)).model_dump()

        assert data["points"][0]["lambdas"]["volume"] == 0.0
        assert data["points"][1]["lambdas"]["volume"] == pytest.approx(0.7128, rel=1e-6)

    def test_a_pinned_range_is_a_valid_sweep(self):
        from haute.routes._optimiser_solver import swept_frontier_ranges

        config = {
            "constraints": {"volume": {"min": 0.9}},
            "frontier_ranges": {"volume": [0.9, 0.9]},
        }

        assert swept_frontier_ranges(config) == {"volume": (0.9, 0.9)}


# ---------------------------------------------------------------------------
# Coverage expansion tests
# ---------------------------------------------------------------------------


class TestSelectFrontierPointIdempotent:
    """Test idempotent re-selection short circuit in select_frontier_point."""

    def test_reselect_same_point_returns_cached(self, client, clean_job_store):
        """Selecting the same frontier point twice returns cached result (short circuit)."""
        seed_job(
            clean_job_store,
            "idem",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "selected_frontier_point": 2,
                "result": {
                    "total_objective": 150.0,
                    "constraints": {"volume": 0.93},
                    "baseline_objective": 140.0,
                    "baseline_constraints": {"volume": 0.87},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.6},
                    "converged": True,
                    "selected_frontier_point": 2,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "solver": MagicMock(),
                "quote_grid": MagicMock(),
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.3,
                            total_objective=130.0,
                            total_volume=0.9,
                        ),
                        _frontier_point_summary(
                            lambda_volume=0.4,
                            total_objective=140.0,
                            total_volume=0.91,
                        ),
                        _frontier_point_summary(
                            lambda_volume=0.6,
                            total_objective=150.0,
                            total_volume=0.93,
                        ),
                    ],
                    "n_points": 3,
                    "constraint_names": ["volume"],
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "idem", "point_index": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["point_index"] == 2
        assert data["total_objective"] == 150.0
        assert data["lambdas"] == {"volume": 0.6}
        assert data["converged"] is True
        # Solver should NOT have been called (short circuit)
        clean_job_store.require_job("idem")["solver"].solve.assert_not_called()

    def test_reselect_same_point_rebuilds_stale_cached_result(
        self,
        client,
        clean_job_store,
    ):
        """A stale selected job flag must not return an unrelated cached result."""
        seed_job(
            clean_job_store,
            "idem_stale",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "selected_frontier_point": 1,
                "base_result": {
                    "total_objective": 100.0,
                    "constraints": {"volume": 0.9},
                    "baseline_objective": 90.0,
                    "baseline_constraints": {"volume": 0.85},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.3},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "result": {
                    "total_objective": 999.0,
                    "constraints": {"volume": 0.01},
                    "baseline_objective": 90.0,
                    "baseline_constraints": {"volume": 0.85},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 9.0},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.3,
                            total_objective=100.0,
                            total_volume=0.9,
                        ),
                        _frontier_point_summary(
                            lambda_volume=0.7,
                            total_objective=200.0,
                            total_volume=0.95,
                        ),
                    ],
                    "n_points": 2,
                    "constraint_names": ["volume"],
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "idem_stale", "point_index": 1},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["point_index"] == 1
        assert data["total_objective"] == 200.0
        assert data["constraints"] == {"volume": 0.95}
        assert data["lambdas"] == {"volume": 0.7}
        job = clean_job_store.require_job("idem_stale")
        assert job["result"]["selected_frontier_point"] == 1
        assert job["result"]["total_objective"] == 200.0

    def test_reselect_same_point_rebuilds_stale_cached_metrics(
        self,
        client,
        clean_job_store,
    ):
        """Matching selected markers must not hide stale summary values."""
        seed_job(
            clean_job_store,
            "idem_stale_metrics",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "selected_frontier_point": 1,
                "base_result": {
                    "total_objective": 100.0,
                    "constraints": {"volume": 0.9},
                    "baseline_objective": 90.0,
                    "baseline_constraints": {"volume": 0.85},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.3},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "result": {
                    "total_objective": 999.0,
                    "constraints": {"volume": 0.01},
                    "baseline_objective": 90.0,
                    "baseline_constraints": {"volume": 0.85},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 9.0},
                    "converged": True,
                    "selected_frontier_point": 1,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.3,
                            total_objective=100.0,
                            total_volume=0.9,
                        ),
                        _frontier_point_summary(
                            lambda_volume=0.7,
                            total_objective=200.0,
                            total_volume=0.95,
                        ),
                    ],
                    "n_points": 2,
                    "constraint_names": ["volume"],
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "idem_stale_metrics", "point_index": 1},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["point_index"] == 1
        assert data["total_objective"] == 200.0
        assert data["constraints"] == {"volume": 0.95}
        assert data["lambdas"] == {"volume": 0.7}
        job = clean_job_store.require_job("idem_stale_metrics")
        assert job["result"]["selected_frontier_point"] == 1
        assert job["result"]["total_objective"] == 200.0


class TestSelectFrontierPointResolve:
    """Test summary-only frontier selection, convergence warning, and scenario stats."""

    def _make_frontier_job(self, clean_job_store, *, converged=True):
        mock_solver = MagicMock()
        seed_job(
            clean_job_store,
            "fsel",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "solver": mock_solver,
                "quote_grid": MagicMock(),
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.3,
                            total_objective=100.0,
                            total_volume=0.9,
                        ),
                        _frontier_point_summary(
                            lambda_volume=0.7,
                            total_objective=200.0,
                            total_volume=0.95,
                            converged=converged,
                        ),
                    ],
                    "n_points": 2,
                    "constraint_names": ["volume"],
                },
                "result": {
                    "total_objective": 100.0,
                    "baseline_objective": 90.0,
                    "constraints": {"volume": 0.9},
                    "baseline_constraints": {"volume": 0.85},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                    "lambdas": {"volume": 0.3},
                    "converged": True,
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                },
                "artifact_handles": {},
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        return mock_solver

    def test_resolve_with_new_lambdas(self, client, clean_job_store):
        """Selecting a frontier point returns stored summary metrics."""
        mock_solver = self._make_frontier_job(clean_job_store)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_objective"] == 200.0
        assert data["lambdas"] == {"volume": 0.7}
        assert data["converged"] is True
        assert data["constraints"] == {"volume": 0.95}
        mock_solver.solve.assert_not_called()

    def test_resolve_non_converged_adds_warning(self, client, clean_job_store):
        """When re-solve doesn't converge, the result includes a warning."""
        self._make_frontier_job(clean_job_store, converged=False)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["converged"] is False
        # Check the store was updated with a warning
        job = clean_job_store.require_job("fsel")
        assert "warning" in job["result"]
        assert "converge" in job["result"]["warning"].lower()

    def test_resolve_converged_removes_warning(self, client, clean_job_store):
        """When re-solve converges, any prior warning is removed."""
        self._make_frontier_job(clean_job_store, converged=True)

        # Pre-set a warning in the result
        def add_stale_warning(job):
            job["result"]["warning"] = "old warning"

        replace_job(clean_job_store, "fsel", add_stale_warning)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        job = clean_job_store.require_job("fsel")
        assert "warning" not in job["result"]

    def test_select_removes_the_solves_adjustment_report(self, client, clean_job_store):
        """A selected point's summary carries no report: it is loaded on request."""
        self._make_frontier_job(clean_job_store)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["adjustments"] is None
        job = clean_job_store.require_job("fsel")
        assert "adjustments" not in job["result"]

    def test_select_ratebook_frontier_point_does_not_reuse_base_factor_tables(
        self,
        client,
        clean_job_store,
    ):
        """Fast point selection must not attach base ratebook diagnostics to another point."""
        mock_solver, _mock_grid, _factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "rb_select_summary",
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "rb_select_summary", "point_index": 0},
        )

        assert resp.status_code == 200
        job = clean_job_store.require_job("rb_select_summary")
        assert job["result"]["selected_frontier_point"] == 0
        assert job["result"]["total_objective"] == 220.0
        assert "factor_tables" not in job["result"]
        assert "adjustments" not in job["result"]
        mock_solver.solve.assert_not_called()

    def test_select_ratebook_frontier_point_can_materialise_factor_tables(
        self,
        client,
        clean_job_store,
    ):
        """Ratebook point selection can opt into selected-point factor tables.

        The tables are the ones the frontier kept for the point and the
        totals, λ, CD passes and clamp rate are the frontier row's own, with
        no re-solve.
        """
        mock_solver, _mock_grid, _factor_contexts = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "rb_select_rates",
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_select_rates",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        expected_tables = _expected_region_factor_tables()
        assert data["factor_tables"] == expected_tables
        assert data["total_objective"] == 220.0
        assert data["constraints"] == {"volume": 0.97}
        assert data["lambdas"] == {"volume": 0.7}
        assert data["cd_iterations"] == 3
        assert data["clamp_rate"] == 0.04
        job = clean_job_store.require_job("rb_select_rates")
        assert job["result"]["factor_tables"] == expected_tables
        assert job["result"]["total_objective"] == 220.0
        assert job["selected_frontier_point"] == 0
        mock_solver.solve.assert_not_called()

    def test_select_ratebook_frontier_point_can_switch_materialised_factor_tables(
        self,
        client,
        clean_job_store,
    ):
        """Ratebook rates can be materialised for multiple selected frontier points.

        Each selection attaches that point's own kept tables and row totals.
        """
        mock_solver, _mock_grid, _factor_contexts = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "rb_select_rates_switch",
        )
        replace_job(
            clean_job_store,
            "rb_select_rates_switch",
            _append_second_ratebook_frontier_point,
        )

        first = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_select_rates_switch",
                "point_index": 0,
                "include_ratebook_tables": True,
            },
        )
        second = client.post(
            "/api/optimiser/frontier/select",
            json={
                "job_id": "rb_select_rates_switch",
                "point_index": 1,
                "include_ratebook_tables": True,
            },
        )

        assert first.status_code == 200
        assert first.json()["factor_tables"] == _expected_region_factor_tables()
        assert first.json()["total_objective"] == 220.0
        assert first.json()["cd_iterations"] == 3
        assert second.status_code == 200, second.json()
        second_data = second.json()
        assert second_data["factor_tables"] == _expected_region_factor_tables(
            north=1.12,
            south=0.98,
        )
        assert second_data["total_objective"] == 240.0
        assert second_data["constraints"] == {"volume": 1.03}
        assert second_data["lambdas"] == {"volume": 0.9}
        assert second_data["cd_iterations"] == 7
        assert second_data["clamp_rate"] == 0.02
        job = clean_job_store.require_job("rb_select_rates_switch")
        assert job["selected_frontier_point"] == 1
        assert job["result"]["factor_tables"] == second_data["factor_tables"]
        mock_solver.solve.assert_not_called()

    def test_resolve_records_frontier_provenance(self, client, clean_job_store):
        """After selection, the selected frontier point index is stored on the job."""
        self._make_frontier_job(clean_job_store)
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )
        assert resp.status_code == 200
        job = clean_job_store.require_job("fsel")
        assert job["selected_frontier_point"] == 1
        assert job["result"]["selected_frontier_point"] == 1

    def test_resolve_succeeds_after_runtime_state_is_cleared(
        self,
        client,
        clean_job_store,
    ):
        """Summary-only selection remains available after terminal cleanup."""
        mock_solver = self._make_frontier_job(clean_job_store)
        clean_job_store.clear_result_data("fsel")

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )

        assert resp.status_code == 200
        job = clean_job_store.require_job("fsel")
        assert "solver" not in job
        assert "quote_grid" not in job
        assert "solve_result" not in job
        assert job["selected_frontier_point"] == 1
        mock_solver.solve.assert_not_called()

    def test_resolve_does_not_replace_base_apply_artifact(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_artifacts import _persist_apply_result_artifact

        self._make_frontier_job(clean_job_store)
        old_handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [9.9]}))
        )
        assert old_handle is not None
        old_artifact_dir = Path(old_handle["directory"])
        old_artifact_path = Path(old_handle["path"])
        replace_job(
            clean_job_store,
            "fsel",
            lambda job: {**job, "artifact_handles": {"apply_result": old_handle}},
        )

        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "fsel", "point_index": 1},
        )

        assert resp.status_code == 200
        job = clean_job_store.require_job("fsel")
        assert job["artifact_handles"]["apply_result"] == old_handle
        assert Path(old_artifact_path).is_file()
        assert old_artifact_dir.exists()

    def test_resolve_keeps_success_when_old_apply_artifact_cleanup_would_fail(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_artifacts import _persist_apply_result_artifact

        self._make_frontier_job(clean_job_store)
        old_handle = _persist_apply_result_artifact(
            SimpleNamespace(dataframe=pl.DataFrame({"optimal_scenario_value": [9.9]}))
        )
        assert old_handle is not None
        old_artifact_path = Path(old_handle["path"])
        replace_job(
            clean_job_store,
            "fsel",
            lambda job: {**job, "artifact_handles": {"apply_result": old_handle}},
        )

        cleanup_error = RuntimeError("cleanup denied")
        with (
            patch(
                "haute.routes._optimiser_frontier._cleanup_apply_result_artifact",
                side_effect=cleanup_error,
            ),
            patch("haute.routes.optimiser.logger.warning") as log_warning,
        ):
            resp = client.post(
                "/api/optimiser/frontier/select",
                json={"job_id": "fsel", "point_index": 1},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_objective"] == 200.0
        assert data["constraints"] == {"volume": 0.95}
        job = clean_job_store.require_job("fsel")
        assert job["artifact_handles"]["apply_result"]["path"] == str(old_artifact_path)
        assert Path(old_artifact_path).is_file()
        assert job["selected_frontier_point"] == 1
        assert job["result"]["total_objective"] == 200.0

        log_warning.assert_not_called()

    def test_select_no_solver(self, client, clean_job_store):
        """Select with no solver still works from stored frontier data."""
        seed_job(
            clean_job_store,
            "no_slv",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "solver": None,
                "quote_grid": None,
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.5,
                            total_objective=100.0,
                            total_volume=0.9,
                        )
                    ],
                    "n_points": 1,
                    "constraint_names": ["volume"],
                },
                "result": {
                    "baseline_objective": 90.0,
                    "baseline_constraints": {"volume": 0.85},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "no_slv", "point_index": 0},
        )
        assert resp.status_code == 200
        assert resp.json()["lambdas"] == {"volume": 0.5}

    def test_select_solver_exception_returns_500(self, client, clean_job_store):
        """Solver exceptions are irrelevant because selection does not solve."""
        mock_solver = MagicMock()
        mock_solver.solve.side_effect = RuntimeError("solver boom")
        seed_job(
            clean_job_store,
            "sel_err",
            {
                "status": "completed",
                "config": {"constraints": {"volume": {"min": 0.9}}},
                "solver": mock_solver,
                "quote_grid": MagicMock(),
                "frontier_data": {
                    "status": "ok",
                    "points": [
                        _frontier_point_summary(
                            lambda_volume=0.5,
                            total_objective=100.0,
                            total_volume=0.9,
                        )
                    ],
                    "n_points": 1,
                    "constraint_names": ["volume"],
                },
                "result": {
                    "baseline_objective": 90.0,
                    "baseline_constraints": {"volume": 0.85},
                    "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
                },
                "created_at": time.time(),
                "completed_at": time.time(),
            },
        )
        resp = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": "sel_err", "point_index": 0},
        )
        assert resp.status_code == 200
        mock_solver.solve.assert_not_called()


# ---------------------------------------------------------------------------
# Mutation-readiness boundary tests.
#
# Each test below pins a specific constant or boundary that a subtle code
# change (off-by-one, comparison flip, constant drift, dropped clause) could
# silently invalidate.  These complement the behavioural tests above; their
# job is to catch *implementation drift* that doesn't change observable
# behaviour for the normal path.
# ---------------------------------------------------------------------------


class TestFrontierPointBaselinesAreRequired:
    """A frontier point's baselines come from its solve; a missing one is an error."""

    def test_a_base_result_without_baselines_fails_loudly(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_result_dict

        job = {
            "config": {"constraints": {"a": {"min": 0.9}}},
            "frontier_data": {
                "points": [
                    typed_frontier_point(
                        {
                            "converged": True,
                            "lambda_a": 0.5,
                            "total_objective": 100.0,
                            "total_a": 0.9,
                            "bound_a": 0.9,
                        }
                    )
                ],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {"baseline_constraints": {"a": 0.85}},
        }

        with pytest.raises(KeyError, match="baseline_objective"):
            _frontier_point_result_dict(job, 0)

    def test_a_frontier_point_keeps_its_solves_collar(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_result_dict

        bounds = {"min": 0.9, "max": 1.1}
        job = {
            "config": {"constraints": {"a": {"min": 0.9}}},
            "frontier_data": {
                "points": [
                    typed_frontier_point(
                        {
                            "converged": True,
                            "lambda_a": 0.5,
                            "total_objective": 100.0,
                            "total_a": 0.9,
                            "bound_a": 0.9,
                        }
                    )
                ],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {
                "mode": "ratebook",
                "baseline_objective": 90.0,
                "baseline_constraints": {"a": 0.85},
                "combined_factor_bounds": bounds,
            },
        }

        assert _frontier_point_result_dict(job, 0)["combined_factor_bounds"] == bounds
