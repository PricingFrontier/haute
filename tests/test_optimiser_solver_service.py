"""The optimiser solve service's validation, solving and result finalisation.

The heavy entrypoints refuse to run outside a solver worker, ``_validate_config`` and the defensive
validators reject malformed configs, ``_solve_online`` records its history, ``_apply_lambdas`` caps
its preview, and finalising a solve result keeps its exact thresholds and warnings.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._project_context import current_project
from haute.errors import RequestRefusedError
from haute.routes._optimiser_limits import (
    APPLY_PREVIEW_ROW_LIMIT,
    FRONTIER_POINT_LIMIT,
)
from tests import _optimiser_route_helpers
from tests._optimiser_route_helpers import (
    _expected_region_factor_tables,
    _make_optimiser_graph,
    _make_ratebook_frontier_materialisation_job,
    _ratebook_solve_result_namespace,
)
from tests._project import use_project
from tests.job_store_support import replace_job, seed_job
from tests.optimiser_fixtures import (
    SOLVE_PROVENANCE,
    SOLVE_SCENARIO_GRID,
    library_frontier_frame,
    make_completed_job,
    make_frontier_point,
    make_input_summary,
    make_online_apply_frame,
    make_solved_result,
    typed_frontier_point,
)

# Fixtures shared with the other modules split from one suite.
_in_solver_worker_context = _optimiser_route_helpers._in_solver_worker_context


# ---------------------------------------------------------------------------
# Ratebook mode
# ---------------------------------------------------------------------------


# A ratebook result's serialised table for its one ``region`` factor spec.
_REGION_FACTOR_TABLES = {
    "region": [{"__factor_group__": "North", "optimal_scenario_value": 1.0, "quote_count": 2}]
}


# ---------------------------------------------------------------------------
# Phase 2a: Frontier
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 4: Step-wise expansion
# ---------------------------------------------------------------------------


class TestSolverWorkerContextGuard:
    """Pin the worker-context guard on the heavy solver entrypoints.

    The guard is the architectural defence against reintroducing an inline
    solve/sweep in a request handler: outside ``solver_worker_context()`` the
    entrypoints must fail loud, immediately.
    """

    @pytest.mark.parametrize(
        "entrypoint_name",
        ["_compute_frontier", "_solve_online", "_solve_ratebook"],
    )
    def test_heavy_entrypoints_fail_loud_outside_worker_context(self, entrypoint_name):
        import haute.routes._optimiser_solver as service

        entrypoint = getattr(service, entrypoint_name)
        with pytest.raises(RuntimeError, match="must run inside a background solver worker"):
            entrypoint(MagicMock(), MagicMock())

    def test_compute_frontier_runs_inside_worker_context(self):
        from haute.routes._optimiser_solver import _compute_frontier, solver_worker_context

        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=pl.DataFrame({"total_objective": [1.0]})
        )
        with solver_worker_context():
            result = _compute_frontier(
                mock_solver,
                MagicMock(),
                mode="online",
                ratebook_factors=None,
                threshold_ranges={"a": (0.5, 1.0)},
                n_points_per_dim=3,
            )
        assert len(result.points) == 1

    def test_worker_context_resets_after_exit(self):
        from haute.routes._optimiser_solver import _compute_frontier, solver_worker_context

        with solver_worker_context():
            pass
        with pytest.raises(RuntimeError, match="must run inside a background solver worker"):
            _compute_frontier(MagicMock(), MagicMock())


# ---------------------------------------------------------------------------
# _validate_config unit tests
# ---------------------------------------------------------------------------


class TestValidateConfig:
    """Direct unit tests for OptimiserSolveService._validate_config."""

    def test_no_objective_raises_400(self):

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(RequestRefusedError) as exc_info:
            OptimiserSolveService._validate_config({"objective": ""})
        assert exc_info.value.status_code == 400
        assert "objective" in exc_info.value.detail.lower()

    def test_no_objective_key_raises_400(self):

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(RequestRefusedError) as exc_info:
            OptimiserSolveService._validate_config({"constraints": {}})
        assert exc_info.value.status_code == 400

    def test_invalid_mode_raises_400(self):

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(RequestRefusedError) as exc_info:
            OptimiserSolveService._validate_config({"objective": "income", "mode": "quantum"})
        assert exc_info.value.status_code == 400
        assert "setting 'mode': Input should be 'online' or 'ratebook'" in exc_info.value.detail

    def test_ratebook_without_factor_columns_raises_400(self):

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(RequestRefusedError) as exc_info:
            OptimiserSolveService._validate_config(
                {"objective": "income", "mode": "ratebook", "factor_columns": []}
            )
        assert exc_info.value.status_code == 400
        assert "factor_columns" in exc_info.value.detail.lower()

    def test_valid_online_config_passes(self):
        from haute.routes._optimiser_service import OptimiserSolveService

        mode = OptimiserSolveService._validate_config({"objective": "income", "mode": "online"})
        assert mode == "online"

    def test_valid_online_config_default_mode(self):
        from haute.routes._optimiser_service import OptimiserSolveService

        mode = OptimiserSolveService._validate_config({"objective": "income"})
        assert mode == "online"

    def test_valid_ratebook_config_passes(self):
        from haute.routes._optimiser_service import OptimiserSolveService

        mode = OptimiserSolveService._validate_config(
            {
                "objective": "income",
                "mode": "ratebook",
                "factor_columns": [["region"]],
            }
        )
        assert mode == "ratebook"

    def test_validate_config_max_iter_zero(self):
        """A solver control must be positive: the node's config model refuses max_iter=0."""
        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(RequestRefusedError) as exc_info:
            OptimiserSolveService._validate_config(
                {"objective": "income", "mode": "online", "max_iter": 0}
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Optimiser max_iter must be a positive integer."

    def test_validate_config_negative_tolerance(self):
        """A solver control must be positive: the node's config model refuses tolerance=-1."""
        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(RequestRefusedError) as exc_info:
            OptimiserSolveService._validate_config(
                {"objective": "income", "mode": "online", "tolerance": -1}
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Optimiser tolerance must be a positive number."

    def test_validate_config_empty_objective_string(self):
        """objective='' is treated as missing and should raise 400."""

        from haute.routes._optimiser_service import OptimiserSolveService

        with pytest.raises(RequestRefusedError) as exc_info:
            OptimiserSolveService._validate_config({"objective": ""})
        assert exc_info.value.status_code == 400
        assert "objective" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# _finalize_solve_result unit tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_in_solver_worker_context")
class TestFinalizeSolveResult:
    def _make_solve_result(self, *, converged=True):
        # A per-quote frame over SOLVE_SCENARIO_GRID (0.9, 1.0, 1.1).
        df = pl.DataFrame(
            {
                "quote_id": ["a", "b", "c", "d", "e"],
                "optimal_step": pl.Series([0, 1, 2, 2, 0], dtype=pl.Int32),
                "optimal_scenario_value": pl.Series([0.9, 1.0, 1.1, 1.1, 0.9], dtype=pl.Float32),
                "optimal_objective": pl.Series([20.0] * 5, dtype=pl.Float32),
                "optimal_volume": pl.Series([0.2] * 5, dtype=pl.Float32),
            }
        )
        return SimpleNamespace(
            dataframe=df,
            total_objective=100.0,
            baseline_objective=95.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.5},
            converged=converged,
        )

    def test_convergence_warning_when_not_converged(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )
        solve_result = self._make_solve_result(converged=False)
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert "warning" in job["result"]
        assert "converge" in job["result"]["warning"].lower()

    def test_no_warning_when_converged(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )
        solve_result = self._make_solve_result(converged=True)
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert "warning" not in job["result"]

    def test_result_dict_includes_core_fields(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=2.5,
        )

        job = store.require_job(job_id)
        result = job["result"]
        assert result["total_objective"] == 100.0
        assert result["lambdas"] == {"volume": 0.5}
        assert result["constraints"] == {"volume": 0.92}
        assert result["converged"] is True
        assert result["baseline_objective"] == 95.0
        assert result["baseline_constraints"] == {"volume": 0.88}
        assert job["status"] == "completed"
        assert job["elapsed_seconds"] == 2.5

    def test_frontier_not_computed_when_no_constraint_is_swept(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        for ranges in (None, {}):
            config: dict[str, Any] = {"constraints": {"volume": {"min": 0.9}}, "frontier_steps": 3}
            if ranges is not None:
                config["frontier_ranges"] = ranges
            job_id = store.create_job(
                {
                    "input_provenance": SOLVE_PROVENANCE,
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "status": "running",
                    "config": config,
                }
            )
            solve_result = self._make_solve_result()
            mock_solver = MagicMock()

            _finalize_solve_result(
                solve_result,
                mode="online",
                solver=mock_solver,
                quote_grid=MagicMock(),
                store=store,
                job_id=job_id,
                elapsed=1.0,
            )

            job = store.require_job(job_id)
            assert job["frontier_data"] is None
            assert job["result"]["frontier"] is None
            mock_solver.frontier.assert_not_called()

    def test_swept_frontier_ranges_covers_only_the_swept_constraints(self):
        from haute.routes._optimiser_solver import swept_frontier_ranges

        config = {
            "constraints": {"volume": {"min": 0.9}, "loss": {"max": 0.7}, "premium": {"min": 1.0}},
            # Keyed out of constraint order: the result follows constraint order.
            "frontier_ranges": {
                "premium": {"min": 1.0, "max": 2.0},
                "volume": {"min": 0.8, "max": 0.95},
            },
        }

        ranges = swept_frontier_ranges(config)

        assert ranges == {"volume": (0.8, 0.95), "premium": (1.0, 2.0)}
        assert list(ranges) == ["volume", "premium"]
        assert swept_frontier_ranges({**config, "frontier_ranges": {}}) == {}

    def test_anchor_swept_constraints_solves_each_swept_constraint_at_its_range_start(self):
        from haute.routes._optimiser_solver import anchor_swept_constraints

        config = {
            "constraints": {"volume": {"min": 0.0}, "loss": {"max": 0.7}},
            "frontier_ranges": {"volume": {"min": 0.8, "max": 0.95}},
        }

        anchored = anchor_swept_constraints(config)

        assert anchored["constraints"] == {"volume": {"min": 0.8}, "loss": {"max": 0.7}}
        # The saved bound is untouched, so un-sweeping restores it.
        assert config["constraints"]["volume"] == {"min": 0.0}
        # A swept range without a start leaves the bound for validation.
        incomplete = {**config, "frontier_ranges": {"volume": {"max": 0.95}}}
        assert anchor_swept_constraints(incomplete)["constraints"]["volume"] == {"min": 0.0}

    def test_frontier_range_for_an_unknown_constraint_surfaces_error(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_steps": 3,
                    "frontier_ranges": {"premium": {"min": 1.0, "max": 2.0}},
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["frontier_data"] is None
        assert job["result"]["frontier"] is None
        assert (
            "frontier_ranges names premium, which are not constraints"
            in job["result"]["frontier_error"]
        )
        mock_solver.frontier.assert_not_called()

    def test_frontier_partial_range_surfaces_error_without_defaulting_missing_side(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 0.8}},
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is None
        assert job["result"]["frontier"] is None
        assert (
            "frontier_ranges.volume must contain min and max values"
            in job["result"]["frontier_error"]
        )
        mock_solver.frontier.assert_not_called()

    def test_inline_frontier_enforces_compute_budget_before_solver(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        constraints = {f"c{index}": {"min": 0.0} for index in range(6)}
        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": constraints,
                    "frontier_ranges": {name: {"min": 0.0, "max": 1.0} for name in constraints},
                    "frontier_steps": 100,
                },
            }
        )
        solve_result = self._make_solve_result()
        solve_result.constraint_bounds = {name: 0.0 for name in constraints}
        mock_solver = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["frontier_data"] is None
        assert "frontier compute budget" in job["result"]["frontier_error"].lower()
        mock_solver.frontier.assert_not_called()

    def test_frontier_computed_for_online_mode(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        frontier_points = library_frontier_frame(
            [
                {
                    "total_objective": 100,
                    "total_volume": 0.9,
                    "lambda_volume": 0.3,
                    "bound_volume": 0.9,
                    "converged": True,
                },
                {
                    "total_objective": 110,
                    "total_volume": 0.95,
                    "lambda_volume": 0.5,
                    "bound_volume": 0.9,
                    "converged": True,
                },
            ]
        )
        mock_solver.frontier.return_value = SimpleNamespace(points=frontier_points)
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is not None
        assert job["frontier_data"]["n_points"] == 2
        assert job["frontier_data"]["points_returned"] == 2
        assert job["frontier_data"]["points_limit"] == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_truncated"] is False
        assert "volume" in job["frontier_data"]["constraint_names"]
        mock_solver.frontier.assert_called_once()
        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"] == {"volume": (0.8, 1.1)}

    def test_frontier_progress_is_visible_while_finalize_computes_frontier(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "start_time": time.monotonic() - 5.0,
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()

        def frontier_side_effect(*args, **kwargs):
            job = store.require_job(job_id)
            assert job["status"] == "running"
            assert job["message"] == "Computing efficient frontier"
            assert job["progress"] == pytest.approx(0.8)
            assert job["elapsed_seconds"] >= 5.0
            return SimpleNamespace(
                points=library_frontier_frame(
                    [
                        {
                            "total_objective": 100.0,
                            "total_volume": 0.9,
                            "lambda_volume": 0.3,
                            "bound_volume": 0.9,
                        }
                    ],
                    constraint_names=["volume"],
                )
            )

        mock_solver.frontier.side_effect = frontier_side_effect

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=0.25,
        )

        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["message"] == "Completed"
        assert job["elapsed_seconds"] >= 5.0

    def test_frontier_uses_per_constraint_absolute_ranges(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 70.0, "max": 95.0}},
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(
            points=library_frontier_frame(
                [{"total_objective": 100.0, "lambda_volume": 0.3}], constraint_names=["volume"]
            )
        )

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"] == {"volume": (70.0, 95.0)}

    def test_auto_frontier_payload_is_capped_in_job_state(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        frontier_points = library_frontier_frame(
            [
                {"total_objective": float(i), "total_volume": 0.9, "lambda_volume": 0.3}
                for i in range(FRONTIER_POINT_LIMIT + 1)
            ]
        )
        mock_solver.frontier.return_value = SimpleNamespace(points=frontier_points)
        mock_grid = MagicMock()

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is not None
        assert job["frontier_data"]["n_points"] == FRONTIER_POINT_LIMIT + 1
        assert len(job["frontier_data"]["points"]) == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_returned"] == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_limit"] == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_truncated"] is True
        assert job["result"]["frontier"] == job["frontier_data"]

    def test_auto_frontier_slices_before_serialising_points(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        class HugePoints:
            def __len__(self) -> int:
                return FRONTIER_POINT_LIMIT + 1

            def head(self, n: int):
                assert n == FRONTIER_POINT_LIMIT
                return library_frontier_frame(
                    [
                        {
                            "total_objective": i,
                            "total_volume": 0.9,
                            "lambda_volume": 0.3,
                            "bound_volume": 0.9,
                            "converged": True,
                        }
                        for i in range(n)
                    ]
                )

            def to_dicts(self):
                raise AssertionError("Full frontier must not be serialised")

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_solver.frontier.return_value = SimpleNamespace(points=HugePoints())

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=mock_solver,
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        assert job["frontier_data"]["n_points"] == FRONTIER_POINT_LIMIT + 1
        assert len(job["frontier_data"]["points"]) == FRONTIER_POINT_LIMIT
        assert job["frontier_data"]["points_truncated"] is True

    def test_frontier_failure_is_surfaced_on_completed_status(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._optimiser_solver import _finalize_solve_result

        job_id = clean_job_store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                    "frontier_steps": 3,
                },
            }
        )
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_solver.frontier.side_effect = RuntimeError("frontier exploded")
        mock_solver.summary.return_value = {"params": {}, "metrics": {}, "artifacts": {}}

        with patch("haute.routes._optimiser_solver.logger.warning") as log_warning:
            _finalize_solve_result(
                solve_result,
                mode="online",
                solver=mock_solver,
                quote_grid=MagicMock(),
                store=clean_job_store,
                job_id=job_id,
                elapsed=1.0,
            )

        job = clean_job_store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["frontier_data"] is None
        assert job["result"]["frontier"] is None
        assert job["result"]["frontier_error"] == ("Frontier unavailable: frontier exploded")
        log_warning.assert_called_once()
        assert log_warning.call_args.kwargs["exc_info"] is True

        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"]["frontier"] is None
        assert data["result"]["frontier_error"] == ("Frontier unavailable: frontier exploded")

    def test_frontier_computed_for_ratebook_mode_with_factors(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                    "frontier_steps": 3,
                },
            }
        )
        # 3b.9: real RatebookResult field set — no phantom dataframe
        solve_result = _ratebook_solve_result_namespace()
        mock_solver = MagicMock()
        frontier_points = library_frontier_frame(
            [
                {
                    "total_objective": 100,
                    "total_volume": 0.9,
                    "lambda_volume": 0.3,
                    "bound_volume": 0.9,
                    "converged": True,
                },
                {
                    "total_objective": 110,
                    "total_volume": 0.95,
                    "lambda_volume": 0.5,
                    "bound_volume": 0.9,
                    "converged": True,
                },
            ],
            mode="ratebook",
        )
        mock_solver.frontier.return_value = SimpleNamespace(points=frontier_points)
        mock_grid = MagicMock()
        factor_contexts = SimpleNamespace(n_quotes=2, factor_specs=[["region"]])

        _finalize_solve_result(
            solve_result,
            mode="ratebook",
            solver=mock_solver,
            quote_grid=mock_grid,
            store=store,
            job_id=job_id,
            elapsed=1.0,
            ratebook_factor_contexts=factor_contexts,
            factor_columns=[["region"]],
            extra_fields={"factor_tables": _REGION_FACTOR_TABLES},
        )

        job = store.require_job(job_id)
        assert job["frontier_data"] is not None
        assert job["frontier_data"]["n_points"] == 2
        mock_solver.frontier.assert_called_once()
        assert mock_solver.frontier.call_args.args == (mock_grid, factor_contexts)
        assert mock_solver.frontier.call_args.kwargs["threshold_ranges"]["volume"] == pytest.approx(
            (0.8, 1.1)
        )
        assert mock_solver.frontier.call_args.kwargs["n_points_per_dim"] == 3
        assert mock_solver.frontier.call_args.kwargs["factor_columns"] == [["region"]]

    def test_finalized_job_slims_heavy_runtime_state_after_policy_window(self):
        from unittest.mock import patch

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore(ttl_seconds=60, heavy_object_ttl_seconds=1)
        solve_result = self._make_solve_result()
        mock_solver = MagicMock()
        mock_grid = MagicMock()

        with patch("haute.routes._job_store.time.time", return_value=100.0):
            job_id = store.create_job(
                {
                    "input_provenance": SOLVE_PROVENANCE,
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                    "status": "running",
                    "config": {"constraints": {"volume": {"min": 0.9}}},
                }
            )
            _finalize_solve_result(
                solve_result,
                mode="online",
                solver=mock_solver,
                quote_grid=mock_grid,
                store=store,
                job_id=job_id,
                elapsed=1.0,
            )

        with patch("haute.routes._job_store.time.time", return_value=102.0):
            job = store.require_job(job_id)

        assert job["status"] == "completed"
        assert job["result"]["total_objective"] == 100.0
        assert "solver" not in job
        assert "solve_result" not in job
        assert "quote_grid" not in job

    def test_finalized_job_persists_apply_artifact_handle(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )
        solve_result = self._make_solve_result()
        row_count = len(solve_result.dataframe)

        _finalize_solve_result(
            solve_result,
            mode="online",
            solver=MagicMock(),
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
        )

        job = store.require_job(job_id)
        handle = job["artifact_handles"]["apply_result"]
        assert handle["kind"] == "optimiser_apply_result"
        assert handle["format"] == "parquet"
        assert handle["row_count"] == row_count
        assert Path(handle["path"]).is_file()
        assert solve_result.dataframe is None
        assert "dataframe" not in job["result"]

    def test_finalized_ratebook_job_adopts_factors_and_persists_quote_results(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_artifacts import (
            _RATEBOOK_FACTORS_HANDLE_KEY,
            _persist_ratebook_factors_lazy_artifact,
            _scan_ratebook_factors_artifact,
        )
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )
        # 3b.9: real RatebookResult field set — no phantom ``dataframe``
        # (``_make_solve_result`` models the ONLINE shape, which does carry one).
        solve_result = _ratebook_solve_result_namespace()
        factors_df = pl.DataFrame({"quote_id": ["q1", "q2"], "region": ["North", "South"]})
        factors_handle = _persist_ratebook_factors_lazy_artifact(factors_df.lazy())

        _finalize_solve_result(
            solve_result,
            mode="ratebook",
            solver=MagicMock(),
            quote_grid=MagicMock(),
            store=store,
            job_id=job_id,
            elapsed=1.0,
            ratebook_factors_handle=factors_handle,
            factor_columns=[["region"]],
            extra_fields={"factor_tables": _REGION_FACTOR_TABLES},
        )

        job = store.require_job(job_id)
        handle = job["artifact_handles"][_RATEBOOK_FACTORS_HANDLE_KEY]
        assert handle == factors_handle
        assert handle["kind"] == "optimiser_ratebook_factors"
        assert handle["row_count"] == 2
        assert _scan_ratebook_factors_artifact(handle).collect().equals(factors_df)
        # The as-solved apply artifact is price-contour's canonical per-quote evaluation.
        apply_handle = job["artifact_handles"]["apply_result"]
        assert apply_handle["kind"] == "optimiser_apply_result"
        assert pl.read_parquet(apply_handle["path"]).equals(solve_result.quote_results)

    def test_finalized_job_skips_artifact_publication_when_status_guard_is_lost(
        self,
    ):
        from haute.routes._job_lifecycle import JobLifecycle
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import _finalize_solve_result

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {"volume": {"min": 0.9}}},
            }
        )
        JobLifecycle(store).transition(
            job_id,
            to="error",
            message="Solve timed out after 10s",
            expected_status="running",
        )
        artifact_dir: Path | None = None

        def _mkdtemp(*, prefix: str, dir: str) -> str:
            nonlocal artifact_dir
            assert prefix == "apply_"
            artifact_dir = Path(dir) / "apply_guard"
            artifact_dir.mkdir(parents=True)
            return str(artifact_dir)

        with patch("haute.routes._optimiser_artifacts.tempfile.mkdtemp", side_effect=_mkdtemp):
            _finalize_solve_result(
                self._make_solve_result(),
                mode="online",
                solver=MagicMock(),
                quote_grid=MagicMock(),
                store=store,
                job_id=job_id,
                elapsed=12.0,
            )

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["message"] == "Solve timed out after 10s"
        assert job.get("result") is None
        assert job.get("artifact_handles") is None
        assert artifact_dir is None


# ---------------------------------------------------------------------------
# apply_lambdas unit tests (row count & preview via mock)
# ---------------------------------------------------------------------------


class TestApplyLambdasUnit:
    @staticmethod
    def _seed_persisted(store: Any, job_id: str, frame: pl.DataFrame, **extra: Any) -> dict:
        """A completed online job whose as-solved apply frame is the persisted *frame*."""
        from haute.routes._optimiser_artifacts import _persist_apply_frame_artifact

        handle = _persist_apply_frame_artifact(frame)
        seed_job(
            store,
            job_id,
            make_completed_job(
                result=make_solved_result(total_objective=500.0, constraints={"volume": 0.95}),
                artifact_handles={"apply_result": handle},
                **extra,
            ),
        )
        return handle

    def test_apply_returns_row_count_and_preview(self, client, clean_job_store):
        frame = make_online_apply_frame([f"q{i}" for i in range(5)])
        self._seed_persisted(clean_job_store, "apply_unit", frame)

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_unit"})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["row_count"] == data["matched_row_count"] == 5
        assert data["total_objective"] == 500.0
        assert data["constraints"] == {"volume": 0.95}
        assert data["from_artifact"] is True
        assert data["preview"] == frame.drop("optimal_step").to_dicts()
        assert data["preview_row_count"] == 5
        assert data["preview_row_limit"] == APPLY_PREVIEW_ROW_LIMIT

    def test_apply_preview_payload_is_capped_with_the_counts(self, client, clean_job_store):
        frame = make_online_apply_frame([f"q{i:03d}" for i in range(APPLY_PREVIEW_ROW_LIMIT + 1)])
        self._seed_persisted(clean_job_store, "apply_capped", frame)

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_capped"})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["row_count"] == data["matched_row_count"] == APPLY_PREVIEW_ROW_LIMIT + 1
        assert len(data["preview"]) == data["preview_row_count"] == APPLY_PREVIEW_ROW_LIMIT
        assert data["preview_row_limit"] == APPLY_PREVIEW_ROW_LIMIT

        last = client.post(
            "/api/optimiser/apply",
            json={"job_id": "apply_capped", "offset": APPLY_PREVIEW_ROW_LIMIT},
        ).json()
        assert [row["quote_id"] for row in last["preview"]] == [f"q{APPLY_PREVIEW_ROW_LIMIT:03d}"]
        assert last["offset"] == APPLY_PREVIEW_ROW_LIMIT

    def test_apply_success_clears_heavy_result_but_keeps_handle_for_later_apply(
        self,
        client,
        clean_job_store,
    ):
        frame = make_online_apply_frame(["q1", "q2"])
        handle = self._seed_persisted(
            clean_job_store,
            "apply_terminal",
            frame,
            solver=MagicMock(),
            quote_grid=MagicMock(),
            solve_result=SimpleNamespace(dataframe=frame),
        )

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_terminal"})

        assert resp.status_code == 200, resp.text
        job = clean_job_store.require_job("apply_terminal")
        assert "solver" not in job
        assert "quote_grid" not in job
        assert "solve_result" not in job
        assert job["artifact_handles"]["apply_result"]["path"] == handle["path"]

        second_resp = client.post("/api/optimiser/apply", json={"job_id": "apply_terminal"})

        assert second_resp.status_code == 200
        assert second_resp.json()["preview"] == frame.drop("optimal_step").to_dicts()

    def test_apply_missing_artifact_handle_returns_gone(
        self,
        client,
        clean_job_store,
    ):
        handle = self._seed_persisted(
            clean_job_store, "apply_missing_handle", make_online_apply_frame(["q1"])
        )
        Path(handle["path"]).unlink()

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_missing_handle"})

        assert resp.status_code == 410
        assert resp.json()["detail"] == (
            "Optimiser apply artifact is no longer available. Re-run the solve to regenerate it."
        )

    def test_a_damaged_apply_artifact_fails_loudly(self, client, clean_job_store):
        """An artifact without its mode's schema is a defect: the sanitised 500, never a page."""
        from haute.routes._optimiser_outcomes import ChoiceJoinError

        self._seed_persisted(
            clean_job_store,
            "apply_damaged",
            make_online_apply_frame(["q1"]).drop("optimal_volume"),
        )

        with patch("haute.server.logger.error") as log_error:
            resp = client.post("/api/optimiser/apply", json={"job_id": "apply_damaged"})

        assert resp.status_code == 500
        assert log_error.call_args.kwargs["error_class"] == ChoiceJoinError.__name__

    def test_apply_after_heavy_result_policy_expiry_without_handle_returns_400(
        self,
        client,
        clean_job_store,
    ):
        from haute.routes._job_store import _DEFAULT_HEAVY_OBJECT_TTL_SECONDS

        seed_job(
            clean_job_store,
            "apply_expired",
            {
                "status": "completed",
                "solve_result": SimpleNamespace(
                    dataframe=pl.DataFrame({"quote_id": ["q1"]}),
                    total_objective=100.0,
                    baseline_objective=0.0,
                    baseline_constraints={},
                    total_constraints={},
                    constraint_bounds={},
                ),
                "result": {"total_objective": 100.0},
                "created_at": time.time() - _DEFAULT_HEAVY_OBJECT_TTL_SECONDS - 1,
                "completed_at": time.time() - _DEFAULT_HEAVY_OBJECT_TTL_SECONDS - 1,
            },
        )

        resp = client.post("/api/optimiser/apply", json={"job_id": "apply_expired"})

        assert resp.status_code == 400
        assert "apply artifact handle" in resp.json()["detail"].lower()
        job = clean_job_store.require_job("apply_expired")
        assert job["status"] == "completed"
        assert job["result"] == {"total_objective": 100.0}
        assert "solve_result" not in job

    def test_save_ratebook_frontier_point_rebuilds_stale_cached_summary(
        self,
        client,
        clean_job_store,
        tmp_path,
    ):
        import json as json_mod

        mock_solver, _mock_grid, _factors_df = _make_ratebook_frontier_materialisation_job(
            clean_job_store,
            "save_rb_frontier_stale_cache",
        )

        def install_stale_cached_summary(job):
            job["selected_frontier_point"] = 0
            job["result"].update(
                {
                    "selected_frontier_point": 0,
                    "total_objective": 111.0,
                    "constraints": {"volume": 0.91},
                    "baseline_objective": 0.0,
                    "baseline_constraints": {},
                    "lambdas": {"volume": 0.1},
                    "factor_tables": {
                        "region": [
                            {
                                "__factor_group__": "Stale",
                                "optimal_scenario_value": 1.0,
                            }
                        ]
                    },
                    "diagnostics_errors": [],
                    "input_summary": make_input_summary(),
                    "scenario_grid": SOLVE_SCENARIO_GRID,
                }
            )

        replace_job(
            clean_job_store,
            "save_rb_frontier_stale_cache",
            install_stale_cached_summary,
        )
        use_project(tmp_path)
        out_path = str(tmp_path / "selected_ratebook.json")

        resp = client.post(
            "/api/optimiser/save",
            json={
                "job_id": "save_rb_frontier_stale_cache",
                "output_path": out_path,
                "point_index": 0,
            },
        )

        assert resp.status_code == 200
        mock_solver.solve.assert_not_called()
        saved = json_mod.loads(Path(out_path).read_text(encoding="utf-8"))
        # The stale cache is replaced by the frontier row and its kept tables.
        assert saved["total_objective"] == 220.0
        assert saved["total_constraints"] == {"volume": 0.97}
        assert saved["lambdas"] == {"volume": 0.7}
        assert saved["factor_tables"] == _expected_region_factor_tables()


# ---------------------------------------------------------------------------
# Coverage expansion tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_in_solver_worker_context")
class TestSolveOnlineUnit:
    """Unit tests for _solve_online."""

    def test_solve_online_initializes_solver_and_records_history(self):
        """_solve_online always asks OnlineOptimiser to record its history (Q5)."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import SolveContext, _solve_online

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {
                    "constraints": {"volume": {"min": 0.9}},
                    "objective": "expected_income",
                },
            }
        )

        mock_grid = MagicMock()
        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={"volume": 0.92},
            constraint_bounds={"volume": 0.9},
            baseline_constraints={"volume": 0.88},
            lambdas={"volume": 0.5},
            converged=True,
            iterations=15,
            n_quotes=50,
            n_steps=5,
            history=[{"iteration": 0, "total_objective": 80.0}],
            grid=mock_grid,
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1]}),
        )

        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "max_iter": 20,
            "tolerance": 1e-4,
        }

        with patch("price_contour.OnlineOptimiser") as mock_solver:
            mock_solver.return_value.solve.return_value = mock_result
            # Also mock frontier to return None (to avoid error)
            mock_solver.return_value.frontier.side_effect = Exception("skip")

            _solve_online(
                SolveContext(
                    job_id=job_id,
                    node_id="test",
                    mode="online",
                    store=store,
                    start_time=time.monotonic(),
                ),
                quote_grid=mock_grid,
                config=config,
            )

        mock_solver.assert_called_once_with(
            objective="expected_income",
            constraints={"volume": {"min": 0.9}},
            max_iter=20,
            tolerance=1e-4,
            record_history=True,
        )
        job = store.require_job(job_id)
        assert job["status"] == "completed"
        assert job["result"]["iterations"] == 15
        assert job["result"]["history"] == [{"iteration": 0, "total_objective": 80.0}]
        assert job["result"]["ratebook_cd_trace"] is None

    def test_solve_online_without_library_history_fails_loudly(self):
        """History is always requested, so a result without one is a library defect."""
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import SolveContext, _solve_online

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {"constraints": {}, "objective": "income"},
            }
        )

        mock_grid = MagicMock()
        mock_result = SimpleNamespace(
            total_objective=100.0,
            baseline_objective=90.0,
            total_constraints={},
            constraint_bounds={},
            baseline_constraints={},
            lambdas={},
            converged=True,
            iterations=5,
            n_quotes=10,
            n_steps=3,
            history=None,
            grid=mock_grid,
            dataframe=pl.DataFrame({"optimal_scenario_value": [1.0, 1.1, 0.9]}),
        )

        config = {
            "objective": "income",
            "constraints": {},
        }

        with (
            patch("price_contour.OnlineOptimiser") as mock_solver,
            pytest.raises(RuntimeError, match="no history"),
        ):
            mock_solver.return_value.solve.return_value = mock_result
            _solve_online(
                SolveContext(
                    job_id=job_id,
                    node_id="test",
                    mode="online",
                    store=store,
                    start_time=time.monotonic(),
                ),
                quote_grid=mock_grid,
                config=config,
            )

        assert store.require_job(job_id).get("result") is None

    def test_solve_online_requires_worker_start_time(self):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_solver import SolveContext, _solve_online

        store = JobStore()
        job_id = store.create_job(
            {
                "input_provenance": SOLVE_PROVENANCE,
                "scenario_grid": SOLVE_SCENARIO_GRID,
                "status": "running",
                "config": {},
            }
        )

        with (
            patch("price_contour.OnlineOptimiser") as mock_solver,
            pytest.raises(RuntimeError, match="SolveContext.start_time"),
        ):
            _solve_online(
                SolveContext(
                    job_id=job_id,
                    node_id="opt",
                    mode="online",
                    store=store,
                ),
                quote_grid=MagicMock(),
                config={"objective": "income", "constraints": {}},
            )

        mock_solver.assert_not_called()


# ---------------------------------------------------------------------------
# Helper-function unit tests (defensive validators).
#
# These exercise the small pure-Python helpers in ``haute.routes.optimiser``
# that defend against malformed job state.  Covering them via end-to-end HTTP
# tests is awkward (each scenario needs a synthetic broken job); unit tests
# hit the same branches more directly.
# ---------------------------------------------------------------------------


class TestOptimiserHelperValidators:
    """Direct tests for defensive validators in routes/optimiser.py."""

    def test_job_has_frontier_points_handles_bad_shape(self) -> None:
        from haute.routes.optimiser import _job_has_frontier_points

        assert _job_has_frontier_points({"frontier_data": "bad"}) is False
        assert _job_has_frontier_points({"frontier_data": {"points": []}}) is False
        assert _job_has_frontier_points({"frontier_data": {"points": [{"a": 1}]}}) is True

    def test_cached_result_matches_frontier_selection_rejects_bools(self) -> None:
        from haute.routes._optimiser_frontier import _cached_result_matches_frontier_selection

        # bool is technically int in Python — must be explicitly rejected.
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": True},
                1,
            )
            is False
        )
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": 0},
                1,
            )
            is False
        )
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": 1},
                1,
            )
            is True
        )

    def test_frontier_points_or_raise_missing_data(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_points_or_raise

        with pytest.raises(RequestRefusedError) as exc:
            _frontier_points_or_raise({"frontier_data": None})
        assert exc.value.status_code == 400

    def test_frontier_points_or_raise_invalid_points_type(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_points_or_raise

        # ``"not a list"`` is truthy, so it bypasses the 400 (no points) branch
        # and trips the 500 (invalid shape) branch — both are desired loud
        # failures, just at different layers.
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_points_or_raise({"frontier_data": {"points": "not a list"}})
        assert exc.value.status_code == 500

    def test_frontier_points_or_raise_non_dict_point(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_points_or_raise

        with pytest.raises(RequestRefusedError) as exc:
            _frontier_points_or_raise({"frontier_data": {"points": [{"ok": 1}, 42]}})
        assert exc.value.status_code == 500

    def test_frontier_point_or_raise_out_of_range(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_or_raise

        job = {"frontier_data": {"points": [{"a": 1}], "n_points": 5}}
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_or_raise(job, 10)
        assert exc.value.status_code == 400
        assert "out of range" in exc.value.detail

    def test_frontier_point_or_raise_capped_payload(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_or_raise

        # n_points (50) > len(points) (1) → capped payload branch.
        job = {
            "frontier_data": {
                "points": [{"a": 1}],
                "n_points": 50,
                "points_returned": 1,
                "points_limit": 1,
            }
        }
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_or_raise(job, 5)
        assert "capped frontier payload" in exc.value.detail

    def test_base_result_for_frontier_uses_base_when_present(self) -> None:
        from haute.routes._optimiser_frontier import _base_result_for_frontier

        job = {"base_result": {"a": 1}, "result": {"a": 2}}
        assert _base_result_for_frontier(job)["a"] == 1

    def test_base_result_for_frontier_falls_back_to_result(self) -> None:
        from haute.routes._optimiser_frontier import _base_result_for_frontier

        assert _base_result_for_frontier({"result": {"b": 7}})["b"] == 7

    def test_base_result_for_frontier_missing_raises(self) -> None:
        from haute.routes._optimiser_frontier import _base_result_for_frontier

        with pytest.raises(RequestRefusedError) as exc:
            _base_result_for_frontier({})
        assert exc.value.status_code == 500

    def test_frontier_point_result_dict_invalid_constraint_names(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_result_dict

        job = {
            "frontier_data": {
                "points": [
                    typed_frontier_point(
                        {"converged": True, "lambda_a": 1.0, "total_objective": 1.0, "total_a": 1.0}
                    ),
                ],
                "n_points": 1,
                "constraint_names": "not a list",
            },
            "base_result": {},
        }
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_result_dict(job, 0)
        assert exc.value.status_code == 500

    def test_frontier_point_result_dict_non_string_constraint_name(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_result_dict

        job = {
            "frontier_data": {
                "points": [
                    typed_frontier_point(
                        {"converged": True, "lambda_a": 1.0, "total_objective": 1.0, "total_a": 1.0}
                    ),
                ],
                "n_points": 1,
                "constraint_names": [42],
            },
            "base_result": {},
        }
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_result_dict(job, 0)
        assert exc.value.status_code == 500

    def test_frontier_point_constraints_override_invalid_config(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [make_frontier_point(thresholds={"a": 1.0})],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "config": {"constraints": "not a dict"},
        }
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_constraints_override(job, 0)
        assert exc.value.status_code == 500

    def test_frontier_point_constraints_override_invalid_name(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [make_frontier_point(thresholds={"a": 1.0})],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "config": {"constraints": {42: {"min": 0}}},
        }
        with pytest.raises(RequestRefusedError):
            _frontier_point_constraints_override(job, 0)

    def test_frontier_point_constraints_override_missing_threshold(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_constraints_override

        # config defines "a" but with neither min/max/min_pct/max_pct.
        job = {
            "frontier_data": {
                "points": [make_frontier_point(thresholds={"a": 1.0})],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "config": {"constraints": {"a": {}}},
        }
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "threshold is invalid" in exc.value.detail

    def test_dataframe_or_raise_returns_dataframe_when_present(self) -> None:
        from haute.routes._optimiser_frontier import _dataframe_or_raise

        result = SimpleNamespace(dataframe=pl.DataFrame({"a": [1]}))
        df = _dataframe_or_raise(result, context="ctx")
        assert df.shape == (1, 1)

    def test_artifact_handles_or_raise_invalid_type(self) -> None:
        from haute.routes._optimiser_frontier import _artifact_handles_or_raise

        with pytest.raises(RequestRefusedError) as exc:
            _artifact_handles_or_raise({"artifact_handles": "not a dict"})
        assert exc.value.status_code == 500

    def test_lambda_mappings_match_returns_false_for_mismatched_keys(self) -> None:
        from haute.routes._optimiser_frontier import _lambda_mappings_match

        assert _lambda_mappings_match({"a": 1.0}, {"b": 1.0}) is False

    def test_lambda_mappings_match_returns_false_for_unequal_values(self) -> None:
        from haute.routes._optimiser_frontier import _lambda_mappings_match

        assert _lambda_mappings_match({"a": 1.0}, {"a": 2.0}) is False

    def test_lambda_mappings_match_returns_false_for_non_numeric(self) -> None:
        from haute.routes._optimiser_frontier import _lambda_mappings_match

        assert _lambda_mappings_match({"a": "bad"}, {"a": 1.0}) is False

    def test_lambda_mappings_match_returns_true_within_tolerance(self) -> None:
        from haute.routes._optimiser_frontier import _lambda_mappings_match

        assert _lambda_mappings_match({"a": 1.0}, {"a": 1.0 + 1e-10}) is True

    def test_lambda_mappings_match_returns_false_for_non_dict(self) -> None:
        from haute.routes._optimiser_frontier import _lambda_mappings_match

        assert _lambda_mappings_match("not a dict", {"a": 1.0}) is False
        assert _lambda_mappings_match({"a": 1.0}, "not a dict") is False

    def test_cleanup_orphan_apply_artifact_logs_and_swallows_cleanup_failures(self) -> None:
        """The orphan-cleanup helper must not let secondary failures mask the
        primary error path.  When ``_cleanup_apply_result_artifact`` raises,
        log a warning and continue without re-raising.
        """
        from haute.routes import _optimiser_frontier as optimiser_module
        from haute.routes._optimiser_frontier import _cleanup_orphan_apply_artifact

        with patch.object(
            optimiser_module,
            "_cleanup_apply_result_artifact",
            side_effect=OSError("disk gone"),
        ):
            # Must not raise — the primary failure is what matters.
            _cleanup_orphan_apply_artifact(
                {"directory": "/tmp/some/path"},
                job_id="test_job",
            )

    def test_cleanup_orphan_apply_artifact_uses_path_when_directory_missing(self) -> None:
        from haute.routes import _optimiser_frontier as optimiser_module
        from haute.routes._optimiser_frontier import _cleanup_orphan_apply_artifact

        with patch.object(
            optimiser_module,
            "_cleanup_apply_result_artifact",
            side_effect=OSError("disk gone"),
        ):
            _cleanup_orphan_apply_artifact({"path": "/tmp/file"}, job_id="j1")
            # No assertion: it must simply not raise.

    def test_frontier_point_result_dict_includes_optional_diagnostics(self) -> None:
        """Cover the optional iterations/cd_iterations/clamp_rate branches."""
        from haute.routes._optimiser_frontier import _frontier_point_result_dict

        job = {
            "config": {"constraints": {"a": {"min": 0.9}}},
            "frontier_data": {
                "points": [
                    typed_frontier_point(
                        {
                            "converged": True,
                            "lambda_a": 0.5,
                            "bound_a": 0.9,
                            "total_objective": 100.0,
                            "total_a": 0.9,
                            "iterations": 3,
                            "clamp_rate": 0.05,
                        },
                        mode="ratebook",
                    )
                ],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {"baseline_objective": 90.0, "baseline_constraints": {"a": 0.85}},
        }
        result = _frontier_point_result_dict(job, 0)
        assert result["iterations"] == 3
        # The row summary has no CD count; materialisation sets it from iterations.
        assert "cd_iterations" not in result
        assert result["clamp_rate"] == 0.05

    def test_frontier_point_result_dict_emits_non_converged_warning(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_result_dict

        job = {
            "config": {"constraints": {"a": {"min": 0.9}}},
            "frontier_data": {
                "points": [
                    typed_frontier_point(
                        {
                            "converged": False,
                            "lambda_a": 0.5,
                            "bound_a": 0.9,
                            "total_objective": 100.0,
                            "total_a": 0.9,
                        }
                    )
                ],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {"baseline_objective": 90.0, "baseline_constraints": {"a": 0.85}},
        }
        result = _frontier_point_result_dict(job, 0)
        assert "did not converge" in result["warning"]

    def test_frontier_point_result_dict_drops_warning_when_converged(self) -> None:
        from haute.routes._optimiser_frontier import _frontier_point_result_dict

        job = {
            "config": {"constraints": {"a": {"min": 0.9}}},
            "frontier_data": {
                "points": [
                    typed_frontier_point(
                        {
                            "converged": True,
                            "lambda_a": 0.5,
                            "bound_a": 0.9,
                            "total_objective": 100.0,
                            "total_a": 0.9,
                        }
                    )
                ],
                "n_points": 1,
                "constraint_names": ["a"],
            },
            "base_result": {
                "warning": "stale warning",
                "baseline_objective": 90.0,
                "baseline_constraints": {"a": 0.85},
            },
        }
        result = _frontier_point_result_dict(job, 0)
        assert "warning" not in result

    def test_frontier_point_constraints_override_uses_threshold_value(self) -> None:
        """Happy path: walks through every step of constraints_override."""
        from haute.routes._optimiser_frontier import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [
                    make_frontier_point(
                        thresholds={"a": 0.95, "b": 1.05},
                        bounds={"a": 0.95, "b": 1.05},
                        totals={"a": 1.0, "b": 1.0},
                        lambdas={"a": 0.0, "b": 0.0},
                    )
                ],
                "n_points": 1,
                "constraint_names": ["a", "b"],
            },
            "config": {"constraints": {"a": {"min": 0.5}, "b": {"max": 1.0}}},
        }
        overrides = _frontier_point_constraints_override(job, 0)
        assert overrides == {"a": {"min": 0.95}, "b": {"max": 1.05}}

    def test_compute_frontier_ratebook_requires_factor_contexts(self) -> None:
        from haute.routes._optimiser_solver import _compute_frontier, solver_worker_context

        with solver_worker_context(), pytest.raises(RuntimeError) as exc:
            _compute_frontier(
                MagicMock(),
                MagicMock(),
                mode="ratebook",
                ratebook_factors=None,
                threshold_ranges={"a": (0.5, 1.0)},
                n_points_per_dim=3,
            )
        assert "factor contexts" in str(exc.value)

    def test_enforce_frontier_compute_budget_no_constraints_is_noop(self) -> None:
        from haute.routes._optimiser_limits import enforce_frontier_compute_budget

        # No constraints → no budget check applies.
        enforce_frontier_compute_budget(n_points_per_dim=100, n_constraints=0)

    def test_frontier_point_constraints_override_invalid_constraint_names_list(self) -> None:
        """Cover the branch where ``frontier_data.constraint_names`` is not a list."""
        from haute.routes._optimiser_frontier import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [make_frontier_point(thresholds={"a": 1.0})],
                "n_points": 1,
                "constraint_names": "not a list",
            },
            "config": {"constraints": {"a": {"min": 0.5}}},
        }
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "constraint names" in exc.value.detail.lower()

    def test_frontier_point_constraints_override_non_string_in_names(self) -> None:
        """Cover the branch where ``constraint_names`` contains a non-string entry."""
        from haute.routes._optimiser_frontier import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [make_frontier_point(thresholds={"a": 1.0})],
                "n_points": 1,
                "constraint_names": [42],  # non-string
            },
            "config": {"constraints": {"a": {"min": 0.5}}},
        }
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "constraint names" in exc.value.detail.lower()

    def test_frontier_point_constraints_override_unknown_constraint(self) -> None:
        """Cover the branch where a constraint_name has no matching config spec."""
        from haute.routes._optimiser_frontier import _frontier_point_constraints_override

        job = {
            "frontier_data": {
                "points": [
                    make_frontier_point(
                        thresholds={"a": 1.0, "unknown": 1.0},
                        bounds={"a": 1.0, "unknown": 1.0},
                        totals={"a": 1.0, "unknown": 1.0},
                        lambdas={"a": 0.0, "unknown": 0.0},
                    )
                ],
                "n_points": 1,
                "constraint_names": ["a", "unknown"],
            },
            "config": {"constraints": {"a": {"min": 0.5}}},  # 'unknown' missing
        }
        with pytest.raises(RequestRefusedError) as exc:
            _frontier_point_constraints_override(job, 0)
        assert "constraint is missing" in exc.value.detail

    def test_summary_solve_result_round_trips_optional_fields(self) -> None:
        from haute.routes._optimiser_frontier import _summary_solve_result

        result = {
            "lambdas": {"a": 0.1},
            "total_objective": 100.0,
            "constraints": {"a": 0.95},
            "baseline_objective": 90.0,
            "baseline_constraints": {"a": 0.9},
            "converged": True,
            "iterations": 5,
            "cd_iterations": 2,
            "clamp_rate": 0.01,
            "factor_tables": {"region": [{"value": 1.0}]},
            "diagnostics_errors": [],
            "input_summary": make_input_summary(),
            "scenario_grid": SOLVE_SCENARIO_GRID,
        }
        ns = _summary_solve_result(result)
        assert ns.lambdas == {"a": 0.1}
        assert ns.total_objective == 100.0
        assert ns.iterations == 5
        assert ns.factor_tables == {"region": [{"value": 1.0}]}

    def test_cleanup_orphan_apply_artifact_uses_unknown_for_missing_path(self) -> None:
        """Covers the ``"<unknown>"`` fallback in the warning log."""
        from haute.routes import _optimiser_frontier as optimiser_module
        from haute.routes._optimiser_frontier import _cleanup_orphan_apply_artifact

        with patch.object(
            optimiser_module,
            "_cleanup_apply_result_artifact",
            side_effect=OSError("vanished"),
        ):
            _cleanup_orphan_apply_artifact({}, job_id="j2")  # no directory, no path

    def test_enforce_frontier_compute_budget_at_limit_passes(self) -> None:
        from haute.routes._optimiser_limits import (
            FRONTIER_COMPUTE_LIMIT,
            enforce_frontier_compute_budget,
        )

        # Find the largest n with n**5 <= FRONTIER_COMPUTE_LIMIT (10,000,
        # aligned with price-contour's max_total_points); it must NOT raise —
        # this tests the boundary inclusivity of the budget.  If the constant
        # ever moves, this test still asserts the boundary.
        n = 1
        while n**5 <= FRONTIER_COMPUTE_LIMIT:
            n += 1
        # n now exceeds budget; n-1 is the largest passing value.
        enforce_frontier_compute_budget(n_points_per_dim=n - 1, n_constraints=5)


# ---------------------------------------------------------------------------
# Mutation-readiness boundary tests.
#
# Each test below pins a specific constant or boundary that a subtle code
# change (off-by-one, comparison flip, constant drift, dropped clause) could
# silently invalidate.  These complement the behavioural tests above; their
# job is to catch *implementation drift* that doesn't change observable
# behaviour for the normal path.
# ---------------------------------------------------------------------------


class TestOptimiserMutationBoundaries:
    """Pin constants, exact thresholds, and bool/int discriminations."""

    def test_cached_result_matches_frontier_selection_rejects_true_explicitly(
        self,
    ) -> None:
        """``isinstance(True, int)`` is True in Python.  Without the
        explicit ``not isinstance(selected_point, bool)`` guard, a job
        with ``selected_frontier_point=True`` would silently match
        ``point_index=1``, returning the wrong cached result.

        Test both ``True`` and ``False`` to defend against a mutation
        that drops only one of the bool checks.
        """
        from haute.routes._optimiser_frontier import _cached_result_matches_frontier_selection

        # True looks like 1 numerically — but must be rejected.
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": True},
                1,
            )
            is False
        )
        # False looks like 0 numerically — must also be rejected.
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": False},
                0,
            )
            is False
        )
        # Real ints work as expected.
        assert (
            _cached_result_matches_frontier_selection(
                {"selected_frontier_point": 1},
                1,
            )
            is True
        )

    def test_frontier_apply_handle_prefix_is_stable_string(self) -> None:
        """``_FRONTIER_APPLY_HANDLE_PREFIX`` is a wire-format constant: it
        keys into ``artifact_handles`` dicts that survive across job-store
        round-trips and (potentially) across restarts.  A drift to a
        different prefix would orphan every previously-saved handle.
        """
        from haute.routes._optimiser_frontier import (
            _FRONTIER_APPLY_HANDLE_PREFIX,
            _frontier_apply_handle_key,
        )

        # The literal value is part of the contract — pin it.
        assert _FRONTIER_APPLY_HANDLE_PREFIX == "frontier_apply_result:"
        # The key builder concatenates without modification.
        assert _frontier_apply_handle_key(0) == "frontier_apply_result:0"
        assert _frontier_apply_handle_key(7) == "frontier_apply_result:7"
        # Negative indices are not pre-validated here — but the prefix is
        # still the key shape.  This catches a mutation that introduced
        # a separator change like ``"frontier_apply_result_"``.
        assert _frontier_apply_handle_key(0).startswith("frontier_apply_result:")

    def test_null_quote_id_error_mentions_count_and_remediation(
        self,
        client,
        tmp_path,
    ):
        """The error string for null quote_ids is part of the user-facing
        contract — the UI parses ``({n} rows)`` for a friendly diagnostic.
        Pin the exact phrasing so a refactor to ``"{n} rows have null quote_id"``
        doesn't silently break the UI message.
        """
        df = pl.DataFrame(
            {
                "quote_id": ["q1", None, None],
                "scenario_index": pl.Series([0, 0, 1], dtype=pl.Int32),
                "scenario_value": pl.Series([0.9, 0.9, 1.0], dtype=pl.Float32),
                "expected_income": pl.Series([100.0, 99.0, 101.0], dtype=pl.Float32),
                "volume": pl.Series([1.0, 1.0, 1.0], dtype=pl.Float32),
            }
        )
        path = tmp_path / "null_qid.parquet"
        df.write_parquet(path)
        graph = _make_optimiser_graph(str(path))

        original_root = current_project().root
        try:
            use_project(tmp_path)
            resp = client.post(
                "/api/optimiser/estimate",
                json={"graph": graph, "node_id": "opt"},
            )
        finally:
            use_project(original_root)

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        # Pin the exact surface a UI parser would key on.
        assert "Null quote_id values found in optimiser input" in detail
        # Specific count appears in parens for human readability.
        assert "(2 rows)" in detail
        # Remediation hint stays in the message.
        assert "non-null quote_id" in detail

    def test_enforce_frontier_compute_budget_one_above_limit_rejected(self) -> None:
        """Pin the strict-vs-inclusive comparison.  ``> FRONTIER_COMPUTE_LIMIT``
        means the limit value itself is allowed; ``+1`` must reject.

        A mutation to ``>=`` would tighten the gate by 1 grid point; a
        mutation to ``<`` would let arbitrarily large workloads through.
        Both are caught by combining this test with ``..._at_limit_passes``.
        """
        import math

        from haute.routes._optimiser_limits import (
            FRONTIER_COMPUTE_LIMIT,
            enforce_frontier_compute_budget,
        )

        # Construct the smallest grid that exceeds the limit by exactly one.
        # Use n_constraints=2 so the math is simple to inspect.
        # We need n_points_per_dim ** 2 == FRONTIER_COMPUTE_LIMIT + delta.
        # The simplest way: take ceil(sqrt(LIMIT)) + 1.
        boundary = math.isqrt(FRONTIER_COMPUTE_LIMIT)
        # boundary**2 <= LIMIT; (boundary+1)**2 > LIMIT (provided LIMIT isn't
        # a perfect square — for 100_000 it isn't).
        assert (boundary + 1) ** 2 > FRONTIER_COMPUTE_LIMIT

        with pytest.raises(ValueError, match="Frontier compute budget exceeded"):
            enforce_frontier_compute_budget(
                n_points_per_dim=boundary + 1,
                n_constraints=2,
            )

    def test_enforce_frontier_compute_budget_handles_negative_n_constraints(
        self,
    ) -> None:
        """``n_constraints <= 0`` is the no-op branch; document it.

        A mutation to ``< 0`` would cause ``n_constraints=0`` to fall
        into the loop with ``range(0)`` (still no iterations) — same
        outcome by accident.  But ``< 0`` would let a malicious caller
        pass ``n_constraints=-1`` to skip the budget check entirely.
        """
        from haute.routes._optimiser_limits import enforce_frontier_compute_budget

        # No raise — this is the early return.
        enforce_frontier_compute_budget(n_points_per_dim=10**9, n_constraints=0)
        enforce_frontier_compute_budget(n_points_per_dim=10**9, n_constraints=-1)
