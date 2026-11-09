"""Shared graphs, data, polling helpers and fixtures for the optimiser route suites.

The scored-data builders, optimiser and ratebook graphs, job polling helpers and solver fixtures
here are used by several ``test_optimiser_*`` modules, so each module imports them from this one
place.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest
from price_contour import PerFactorRecord

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.routes._optimiser_limits import (
    FRONTIER_POINT_LIMIT,
)
from tests._project import use_project
from tests.conftest import build_test_input_snapshot, make_edge, make_graph
from tests.job_store_support import seed_job
from tests.optimiser_fixtures import (
    SOLVE_SCENARIO_GRID,
    make_input_summary,
    make_ratebook_quote_results,
    typed_frontier_point,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _optimiser_helper_graph(*sources: str) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            *[
                GraphNode(id=source, data=NodeData(label=source, nodeType="dataInput"))
                for source in sources
            ],
            GraphNode(id="opt", data=NodeData(label="opt", nodeType="optimiser")),
        ],
        edges=[GraphEdge(id=f"e_{source}_opt", source=source, target="opt") for source in sources],
    )


# ``clean_job_store`` lives in tests/conftest.py — single source of truth.


# ---------------------------------------------------------------------------
# Test data: build a scored DataFrame in the shape price-contour expects
# ---------------------------------------------------------------------------


def _make_scored_data(tmp_path, n_quotes: int = 50, n_steps: int = 5) -> str:
    """Create a scored DataFrame in long format for optimisation tests.

    Columns: quote_id, scenario_index, scenario_value, expected_income, volume
    """
    use_project(tmp_path)
    rng = np.random.RandomState(42)
    quote_ids = []
    steps = []
    mults = []
    incomes = []
    volumes = []
    scenario_values = np.linspace(0.8, 1.2, n_steps).astype(np.float32)
    for q in range(n_quotes):
        base_income = rng.uniform(100, 1000)
        base_volume = rng.uniform(0.5, 1.5)
        for s, m in enumerate(scenario_values):
            quote_ids.append(f"q_{q:04d}")
            steps.append(s)
            mults.append(float(m))
            incomes.append(float(base_income * m))
            volumes.append(float(base_volume * (2.0 - m)))
    df = pl.DataFrame(
        {
            "quote_id": quote_ids,
            "scenario_index": pl.Series(steps, dtype=pl.Int32),
            "scenario_value": pl.Series(mults, dtype=pl.Float32),
            "expected_income": pl.Series(incomes, dtype=pl.Float32),
            "volume": pl.Series(volumes, dtype=pl.Float32),
        }
    )
    path = tmp_path / "scored.parquet"
    df.write_parquet(path)
    return str(path)


@pytest.fixture()
def scored_data(tmp_path) -> str:
    return _make_scored_data(tmp_path)


def _snapshot_parquet_data_input(
    path: str,
    *,
    ready: bool = True,
    **extra: object,
) -> dict[str, object]:
    """Return a parquet input config, publishing its snapshot when requested."""
    config: dict[str, object] = {
        "input_type": "file",
        "format": "parquet",
        "mode": "scan",
        "path": path,
        "arguments": {},
        **extra,
    }
    if ready:
        build_test_input_snapshot(config)
    return config


def _make_optimiser_graph(data_path: str, config: dict | None = None) -> dict:
    """Build a 2-node graph: dataInput → optimiser."""
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
                        "config": _snapshot_parquet_data_input(data_path),
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


_TERMINAL_JOB_STATUSES = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    """Poll /solve/status/{job_id} until a terminal status."""
    poll_interval = 0.02
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in _TERMINAL_JOB_STATUSES:
            return data
        time.sleep(poll_interval)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


def _wait_for_store_job_terminal(store, job_id: str, timeout: float = 2.0) -> dict:
    """Wait for a directly inspected job-store record to leave running state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.require_job(job_id)
        if job["status"] in _TERMINAL_JOB_STATUSES:
            return job
        time.sleep(0.02)
    raise TimeoutError(f"Job {job_id} did not become terminal within {timeout}s")


@pytest.fixture()
def _in_solver_worker_context():
    """Enter the solver worker context for unit tests that call the heavy
    solver entrypoints directly (they are guarded against inline execution)."""
    from haute.routes._optimiser_solver import solver_worker_context

    with solver_worker_context():
        yield


def _frontier_point_summary(
    *,
    lambda_volume: float,
    total_objective: float,
    total_volume: float = 0.9,
    threshold_volume: float | None = None,
    converged: bool = True,
    mode: str = "online",
    iterations: int = 3,
    **extra: Any,
) -> dict[str, Any]:
    """A typed frontier point from the row price-contour emits for it."""
    row: dict[str, Any] = {
        "threshold_volume": total_volume if threshold_volume is None else threshold_volume,
        "bound_volume": total_volume if threshold_volume is None else threshold_volume,
        "total_objective": total_objective,
        "total_volume": total_volume,
        "lambda_volume": lambda_volume,
        "iterations": iterations,
        "converged": converged,
    }
    if mode == "online":
        row.update(
            {
                "sv_mean": 1.0,
                "sv_std": 0.1,
                "sv_min": 0.8,
                "sv_p5": 0.85,
                "sv_p25": 0.95,
                "sv_median": 1.0,
                "sv_p75": 1.05,
                "sv_p95": 1.15,
                "sv_max": 1.2,
                "sv_pct_increase": 0.5,
                "sv_pct_decrease": 0.25,
            }
        )
    row.update(extra)
    return typed_frontier_point(row, mode=mode)


# ---------------------------------------------------------------------------
# Ratebook mode
# ---------------------------------------------------------------------------


def _ratebook_solve_result_namespace(
    *,
    total_objective: float = 222.0,
    baseline_objective: float = 95.0,
    total_constraints: dict[str, float] | None = None,
    baseline_constraints: dict[str, float] | None = None,
    lambdas: dict[str, float] | None = None,
    cd_iterations: int = 5,
    clamp_rate: float = 0.04,
    factor_tables: dict[str, dict[str, float]] | None = None,
    constraint_bounds: dict[str, float] | None = None,
    per_factor_results: list[PerFactorRecord] | None = None,
    quote_results: pl.DataFrame | None = None,
) -> SimpleNamespace:
    """Mock shaped like the REAL ``price_contour.RatebookResult``.

    Deliberately has NO ``dataframe`` and NO ``iterations`` attribute —
    the real result carries factor tables, aggregates and the canonical
    per-quote evaluation ``quote_results`` (pinned by
    ``tests/test_optimiser_routes_real_library.py``), which a mocked solve's
    finalize persists as the apply artifact (OPT-V09C). Its default holds two
    quotes at ``SOLVE_SCENARIO_GRID``'s middle step, one per constraint.

    ``per_factor_results`` defaults to one pass over ``region`` ending on the
    result's totals and λ, as a real solve's CD trace does.
    """
    total_constraints = total_constraints if total_constraints is not None else {"volume": 0.97}
    lambdas = lambdas if lambdas is not None else {"volume": 0.7}
    return SimpleNamespace(
        quote_results=(
            quote_results
            if quote_results is not None
            else make_ratebook_quote_results(list(total_constraints))
        ),
        total_objective=total_objective,
        baseline_objective=baseline_objective,
        total_constraints=total_constraints,
        baseline_constraints=(
            baseline_constraints if baseline_constraints is not None else {"volume": 0.88}
        ),
        lambdas=lambdas,
        constraint_bounds=constraint_bounds if constraint_bounds is not None else {"volume": 0.9},
        converged=True,
        cd_iterations=cd_iterations,
        clamp_rate=clamp_rate,
        combined_factor_bounds={"min": 0.9, "max": 1.1},
        factor_tables=(
            factor_tables
            if factor_tables is not None
            else {"region": {"North": 1.08, "South": 0.92}}
        ),
        per_factor_results=(
            per_factor_results
            if per_factor_results is not None
            else [
                _per_factor_record(
                    total_objective=total_objective,
                    total_constraints=total_constraints,
                    lambdas=lambdas,
                )
            ]
        ),
    )


def _per_factor_record(
    *,
    cd_iteration: int = 1,
    factor: str = "region",
    factor_index: int = 0,
    total_objective: float = 222.0,
    total_constraints: dict[str, float] | None = None,
    lambdas: dict[str, float] | None = None,
) -> PerFactorRecord:
    """One inner grouped solve of a ratebook coordinate descent, as price-contour records it."""
    return PerFactorRecord(
        cd_iteration=cd_iteration,
        factor=factor,
        factor_index=factor_index,
        total_objective=total_objective,
        total_constraints=total_constraints if total_constraints is not None else {"volume": 0.97},
        lambdas=lambdas if lambdas is not None else {"volume": 0.7},
        clamp_rate=0.04,
        inner_iterations=4,
        inner_converged=True,
    )


_RATEBOOK_FRONTIER_POINT_TABLES = {"region": {"North": 1.08, "South": 0.92}}


def _ratebook_frontier_point_summary(
    *,
    clamp_rate: float = 0.04,
    iterations: int = 3,
    **kwargs: Any,
) -> dict[str, float | bool]:
    """A typed ratebook frontier point: its ``clamp_rate`` column and no ``sv_*``;
    ``iterations`` is the point's CD pass count."""
    return _frontier_point_summary(
        mode="ratebook", iterations=iterations, clamp_rate=clamp_rate, **kwargs
    )


def _make_ratebook_frontier_materialisation_job(clean_job_store, job_id: str):
    """A completed ratebook job with one frontier point and its kept tables.

    ``frontier_factor_tables`` holds the solver-side ``{factor: {level: rate}}``
    tables price-contour kept for each retained point, aligned with
    ``frontier_data["points"]``. The solver, grid and factor contexts are
    seeded only so tests can prove materialisation never touches them.
    """
    factor_contexts = SimpleNamespace(n_quotes=2, factor_specs=[["region"]])
    mock_grid = MagicMock()
    mock_solver = MagicMock()
    base_result = {
        "mode": "ratebook",
        "total_objective": 100.0,
        "baseline_objective": 95.0,
        "constraints": {"volume": 0.92},
        "baseline_constraints": {"volume": 0.88},
        "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
        "lambdas": {"volume": 0.5},
        "converged": True,
        "factor_tables": {"region": [{"__factor_group__": "Old", "optimal_scenario_value": 1.0}]},
        "combined_factor_bounds": {"min": 0.1, "max": 10.0},
        "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
        "diagnostics_errors": [],
        "input_summary": make_input_summary(),
        "scenario_grid": SOLVE_SCENARIO_GRID,
    }
    seed_job(
        clean_job_store,
        job_id,
        {
            "status": "completed",
            "config": {
                "mode": "ratebook",
                "objective": "expected_income",
                "constraints": {"volume": {"min": 0.9}},
                "factor_columns": [["region"]],
            },
            "node_label": "ratebook opt",
            "solver": mock_solver,
            "quote_grid": mock_grid,
            "ratebook_factor_contexts": factor_contexts,
            "factor_columns_valid": [["region"]],
            "factor_level_counts": {"region": {"North": 1, "South": 1}},
            "combined_factor_bounds": {"min": 0.1, "max": 10.0},
            "factor_dtypes": {"region": [{"column": "region", "dtype": {"kind": "String"}}]},
            "base_result": base_result,
            "result": dict(base_result),
            "frontier_data": {
                "status": "ok",
                "n_points": 1,
                "points_returned": 1,
                "points_limit": FRONTIER_POINT_LIMIT,
                "points_truncated": False,
                "points": [
                    _ratebook_frontier_point_summary(
                        lambda_volume=0.7,
                        total_objective=220.0,
                        total_volume=0.97,
                        threshold_volume=0.96,
                    )
                ],
                "constraint_names": ["volume"],
            },
            "frontier_factor_tables": [_RATEBOOK_FRONTIER_POINT_TABLES],
            "artifact_handles": {},
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )
    return mock_solver, mock_grid, factor_contexts


def _expected_region_factor_tables(
    *,
    north: float = 1.08,
    south: float = 0.92,
) -> dict[str, list[dict[str, object]]]:
    return {
        "region": [
            {
                "__factor_group__": "North",
                "optimal_scenario_value": north,
                "quote_count": 1,
            },
            {
                "__factor_group__": "South",
                "optimal_scenario_value": south,
                "quote_count": 1,
            },
        ]
    }


# ---------------------------------------------------------------------------
# save_result unit tests
# ---------------------------------------------------------------------------


def _anchor_result(**overrides: object) -> dict[str, object]:
    """A completed online solve's lightweight result summary."""
    return {
        "mode": "online",
        "lambdas": {"volume": 0.5},
        "total_objective": 100.0,
        "constraints": {"volume": 0.92},
        "baseline_objective": 95.0,
        "baseline_constraints": {"volume": 0.88},
        "effective_bounds": {"volume": {"kind": "min", "bound": 0.9}},
        "converged": True,
        "iterations": 10,
        "input_summary": make_input_summary(),
        "scenario_grid": SOLVE_SCENARIO_GRID,
        "diagnostics_errors": [],
        **overrides,
    }
