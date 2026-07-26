"""Composable test fixtures for optimiser routes.

Single source of truth for the job-store shapes the optimiser tests
need.  The builders compose so callers only specify what differs from
the standard shape:

    make_completed_job()                       # bare completed solve
    make_online_frontier_job()                 # + online frontier_data
    make_ratebook_frontier_job()               # + ratebook fields
    make_select_job(frontier_data=...)         # specific to /select tests
    make_frontier_point(volume=..., ...)       # one row of frontier_data["points"]
    make_frontier_data(points=...)             # the response wrapper

Each helper accepts ``**overrides`` so a test can change a single field
without restating the whole shape.  Tests that need wholly bespoke
shapes can still build a dict literal — but the common cases live here.

The intent matches Google-style test design: tests document their
*condition* (what makes this test different) by passing only the
overrides; the standard shape is the implicit baseline.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import polars as pl

# ---------------------------------------------------------------------------
# Frontier point + data shapes
# ---------------------------------------------------------------------------


def make_frontier_point(
    *,
    objective: float = 123.0,
    volume: float = 0.91,
    lambda_volume: float = 0.42,
    threshold_volume: float = 0.9,
    converged: bool = True,
    iterations: int | None = 7,
    include_scenario_stats: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """One row of ``frontier_data['points']`` with deterministic defaults.

    ``include_scenario_stats=False`` produces a slim point without
    ``sv_*`` fields, used by tests that exercise the optional-stats
    branch of ``_scenario_stats_from_frontier_point``.
    """
    point: dict[str, Any] = {
        "threshold_volume": threshold_volume,
        "total_objective": objective,
        "total_volume": volume,
        "lambda_volume": lambda_volume,
        "converged": converged,
    }
    if iterations is not None:
        point["iterations"] = iterations
    if include_scenario_stats:
        point.update(
            {
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
        )
    point.update(extra)
    return point


def make_frontier_data(
    points: list[dict[str, Any]] | None = None,
    *,
    constraint_names: list[str] | None = None,
    points_limit: int = 2_000,
    **extra: Any,
) -> dict[str, Any]:
    """Wrap a list of frontier points into the response-shaped frontier_data.

    ``points`` may be passed positionally for ergonomic test bodies
    (``make_frontier_data([p1, p2])``) or as a kwarg for clarity in
    longer test setups.
    """
    actual_points = (
        points
        if points is not None
        else [
            make_frontier_point(),
            make_frontier_point(
                objective=130.0,
                volume=0.93,
                lambda_volume=0.55,
                converged=False,
            ),
        ]
    )
    data: dict[str, Any] = {
        "status": "ok",
        "points": actual_points,
        "n_points": len(actual_points),
        "points_returned": len(actual_points),
        "points_limit": points_limit,
        "points_truncated": False,
        "constraint_names": constraint_names if constraint_names is not None else ["volume"],
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Result dicts (the cached "result" payload on a completed job)
# ---------------------------------------------------------------------------


def make_solved_result(
    *,
    mode: str = "online",
    total_objective: float = 95.0,
    baseline_objective: float = 90.0,
    constraints: dict[str, float] | None = None,
    baseline_constraints: dict[str, float] | None = None,
    lambdas: dict[str, float] | None = None,
    converged: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """The base ``result`` dict an optimiser job persists post-solve."""
    result: dict[str, Any] = {
        "mode": mode,
        "total_objective": total_objective,
        "baseline_objective": baseline_objective,
        "constraints": constraints if constraints is not None else {"volume": 0.85},
        "baseline_constraints": (
            baseline_constraints if baseline_constraints is not None else {"volume": 0.85}
        ),
        "lambdas": lambdas if lambdas is not None else {"volume": 0.0},
        "converged": converged,
    }
    result.update(extra)
    return result


# ---------------------------------------------------------------------------
# Solver/apply mocks
# ---------------------------------------------------------------------------


def make_solve_result_namespace(
    *,
    total_objective: float = 200.0,
    baseline_objective: float = 190.0,
    total_constraints: dict[str, float] | None = None,
    baseline_constraints: dict[str, float] | None = None,
    lambdas: dict[str, float] | None = None,
    converged: bool = True,
    dataframe: pl.DataFrame | None = None,
    **extra: Any,
) -> SimpleNamespace:
    """A SimpleNamespace shaped like price-contour's solve result.

    Tests that mock ``solver.solve.return_value`` use this so they don't
    have to spell out every attribute the route accesses.
    """
    return SimpleNamespace(
        total_objective=total_objective,
        baseline_objective=baseline_objective,
        total_constraints=(
            total_constraints if total_constraints is not None else {"volume": 0.95}
        ),
        baseline_constraints=(
            baseline_constraints if baseline_constraints is not None else {"volume": 0.90}
        ),
        lambdas=lambdas if lambdas is not None else {"volume": 0.7},
        converged=converged,
        dataframe=dataframe
        if dataframe is not None
        else pl.DataFrame({"optimal_scenario_value": [0.9, 1.0]}),
        **extra,
    )


# ---------------------------------------------------------------------------
# Job-store shapes
# ---------------------------------------------------------------------------


def make_completed_job(
    *,
    config: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    frontier_data: dict[str, Any] | None = None,
    artifact_handles: dict[str, Any] | None = None,
    solver: Any = None,
    quote_grid: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """Minimal completed-job dict.  Compose with the overrides you care about.

    By default this is online mode with a single-volume-constraint config.
    Pass ``solver=...``/``quote_grid=...`` only when the test exercises
    runtime-state paths; routes that just read summary data don't need
    them.
    """
    now = time.time()
    job: dict[str, Any] = {
        "status": "completed",
        "config": config
        if config is not None
        else {
            "mode": "online",
            "constraints": {"volume": {"min": 0.9}},
        },
        "result": result if result is not None else make_solved_result(),
        "artifact_handles": artifact_handles if artifact_handles is not None else {},
        "created_at": now,
        "completed_at": now,
    }
    if frontier_data is not None:
        job["frontier_data"] = frontier_data
    if solver is not None:
        job["solver"] = solver
    if quote_grid is not None:
        job["quote_grid"] = quote_grid
    job.update(extra)
    return job


def make_online_frontier_job(
    *,
    frontier_data: dict[str, Any] | None = None,
    solve_result: SimpleNamespace | None = None,
    solver: Any = None,
    quote_grid: Any = None,
    selected_frontier_point: int | None = None,
    config: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Completed online-mode job with frontier_data attached.

    Drop-in replacement for the inline ``_online_frontier_job`` shape
    used by ``test_optimiser_frontier_materialisation.py``.
    """
    fd = frontier_data if frontier_data is not None else make_frontier_data()
    cfg = (
        config
        if config is not None
        else {
            "mode": "online",
            "objective": "income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }
    )
    result = make_solved_result(
        total_objective=99.0,
        constraints={"volume": 0.88},
        baseline_constraints={"volume": 0.85},
        lambdas={"volume": 0.1},
        converged=True,
        n_quotes=10,
        n_steps=3,
        frontier=fd,
    )
    if selected_frontier_point is not None:
        result["selected_frontier_point"] = selected_frontier_point

    now = time.time()
    job: dict[str, Any] = {
        "status": "completed",
        "config": cfg,
        "node_label": "frontier_opt",
        "frontier_data": fd,
        "frontier_generation": 0,
        "result": result,
        "artifact_handles": {},
        "created_at": now,
        "completed_at": now,
    }
    if solve_result is not None:
        job["solve_result"] = solve_result
    if solver is not None:
        job["solver"] = solver
    if quote_grid is not None:
        job["quote_grid"] = quote_grid
    if selected_frontier_point is not None:
        job["selected_frontier_point"] = selected_frontier_point
    job.update(extra)
    return job


def make_ratebook_frontier_job(
    *,
    frontier_data: dict[str, Any] | None = None,
    solver: Any = None,
    quote_grid: Any = None,
    factors_df: pl.DataFrame | None = None,
    factor_columns_valid: list[list[str]] | None = None,
    factor_level_counts: dict[str, dict[str, int]] | None = None,
    selected_frontier_point: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Completed ratebook-mode job with all heavy state present."""
    fd = frontier_data if frontier_data is not None else make_frontier_data()
    job = make_online_frontier_job(
        frontier_data=fd,
        config={"mode": "ratebook", "constraints": {"volume": {"min": 0.9}}},
        solver=solver if solver is not None else MagicMock(),
        quote_grid=quote_grid if quote_grid is not None else MagicMock(),
        selected_frontier_point=selected_frontier_point,
    )
    # Override result mode to ratebook.
    job["result"]["mode"] = "ratebook"
    job["factors_df"] = (
        factors_df if factors_df is not None else pl.DataFrame({"region": ["North"]})
    )
    job["factor_columns_valid"] = (
        factor_columns_valid if factor_columns_valid is not None else [["region"]]
    )
    job["factor_level_counts"] = (
        factor_level_counts if factor_level_counts is not None else {"region": {"North": 1}}
    )
    job.update(extra)
    return job


def make_select_job(
    *,
    frontier_data: dict[str, Any] | None = None,
    base_result: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Job shape for ``frontier/select`` tests — base result + frontier."""
    fd = (
        frontier_data
        if frontier_data is not None
        else make_frontier_data(
            points=[
                make_frontier_point(
                    objective=100.0,
                    volume=0.95,
                    lambda_volume=0.1,
                    threshold_volume=0.95,
                    include_scenario_stats=False,
                    iterations=None,
                ),
                make_frontier_point(
                    objective=130.0,
                    volume=0.93,
                    lambda_volume=0.55,
                    threshold_volume=0.93,
                    converged=False,
                    include_scenario_stats=False,
                    iterations=None,
                ),
            ],
        )
    )
    return make_completed_job(
        config={"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        result=base_result
        or make_solved_result(
            total_objective=95.0,
            constraints={"volume": 0.85},
            baseline_constraints={"volume": 0.85},
            lambdas={"volume": 0.0},
        ),
        frontier_data=fd,
        **extra,
    )


# ---------------------------------------------------------------------------
# Background frontier-sweep polling
# ---------------------------------------------------------------------------

_FRONTIER_TERMINAL_STATUSES = frozenset(
    {"completed", "error", "contract_error", "memory_limited", "timed_out", "cancelled"}
)


def poll_frontier_until_done(client: Any, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
    """Poll ``/frontier/status/{job_id}`` until a terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/optimiser/frontier/status/{job_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data["status"] in _FRONTIER_TERMINAL_STATUSES:
            return data
        time.sleep(0.02)
    raise TimeoutError(f"Frontier job {job_id} did not finish within {timeout}s")


def run_frontier_and_wait(
    client: Any,
    payload: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Start a frontier sweep and poll it to a terminal state.

    Returns the terminal status payload; callers assert on ``status``,
    ``message``/``http_status_code`` (errors) or ``result`` (success).
    """
    resp = client.post("/api/optimiser/frontier", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "started"
    assert body["job_id"]
    return poll_frontier_until_done(client, body["job_id"], timeout=timeout)


def frontier_result(
    client: Any,
    payload: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run a frontier sweep to completion and return its result payload."""
    status = run_frontier_and_wait(client, payload, timeout=timeout)
    assert status["status"] == "completed", status.get("message", "")
    result = status.get("result")
    assert isinstance(result, dict)
    return result
