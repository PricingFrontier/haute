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


SV_STATS: dict[str, float] = {
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


def make_frontier_point(
    *,
    objective: float = 123.0,
    volume: float = 0.91,
    lambda_volume: float = 0.42,
    threshold_volume: float = 0.9,
    converged: bool = True,
    iterations: int = 7,
    mode: str = "online",
    **extra: Any,
) -> dict[str, Any]:
    """One typed ``frontier_data['points']`` row (``OptimiserFrontierPoint``).

    A single ``volume`` constraint by default; pass ``thresholds``, ``bounds``,
    ``totals`` and ``lambdas`` in ``extra`` for other constraint sets. An
    online point carries the ``sv_*`` statistics and its solver path; a
    ratebook point carries its clamp diagnostics instead.
    """
    point: dict[str, Any] = {
        "mode": mode,
        "total_objective": objective,
        "thresholds": {"volume": threshold_volume},
        # price-contour's absolute bound; equal to the threshold for a min/max constraint.
        "bounds": {"volume": threshold_volume},
        "totals": {"volume": volume},
        "lambdas": {"volume": lambda_volume},
        "iterations": iterations,
        "converged": converged,
    }
    if mode == "online":
        point.update(
            {
                "solver_path": "bisection",
                "non_convergence_reason": None if converged else "bracket_exhausted",
                **SV_STATS,
            }
        )
    else:
        point.update({"clamp_rate": 0.1, "n_quotes_clamped_low": 0, "n_quotes_clamped_high": 0})
    point.update(extra)
    return point


def library_frontier_frame(
    rows: list[dict[str, Any]],
    *,
    mode: str = "online",
    constraint_names: list[str] | None = None,
) -> pl.DataFrame:
    """A price-contour ``FrontierResult.points`` frame in the exact library schema.

    Each row gives only the columns a test cares about; every other column of
    ``frontier_points_schema(mode, constraint_names)`` takes a deterministic
    test value (a constraint's bound defaults to its threshold, its threshold
    to its total).
    """
    import price_contour

    names = constraint_names if constraint_names is not None else ["volume"]
    schema = price_contour.frontier_points_schema(mode, names)
    defaults: dict[str, Any] = {"total_objective": 100.0, "iterations": 7, "converged": True}
    if mode == "online":
        defaults.update({"solver_path": "bisection", "non_convergence_reason": None, **SV_STATS})
    else:
        defaults.update({"clamp_rate": 0.1, "n_quotes_clamped_low": 0, "n_quotes_clamped_high": 0})
    full_rows = []
    for row in rows:
        full = dict(defaults)
        for name in names:
            total = row.get(f"total_{name}", 1.0)
            threshold = row.get(f"threshold_{name}", total)
            full.update(
                {
                    f"total_{name}": total,
                    f"threshold_{name}": threshold,
                    f"bound_{name}": row.get(f"bound_{name}", threshold),
                    f"lambda_{name}": row.get(f"lambda_{name}", 0.0),
                }
            )
        full.update(row)
        full_rows.append({column: full[column] for column in schema})
    return pl.DataFrame(full_rows, schema=schema)


def typed_frontier_point(
    row: dict[str, Any],
    *,
    mode: str = "online",
    constraint_names: list[str] | None = None,
) -> dict[str, Any]:
    """A typed frontier point from a partial flat library row (see ``library_frontier_frame``).

    ``constraint_names`` defaults to the constraints the row names in its
    ``total_<c>``/``lambda_<c>`` columns.
    """
    from haute.routes._frontier_point_summary import frontier_point_rows

    names = constraint_names
    if names is None:
        names = []
        for key in row:
            for prefix in ("total_", "lambda_", "threshold_", "bound_"):
                name = key.removeprefix(prefix)
                if key.startswith(prefix) and key != "total_objective" and name not in names:
                    names.append(name)
    frame = library_frontier_frame([row], mode=mode, constraint_names=names)
    return frontier_point_rows(frame, mode=mode, constraint_names=names)[0]


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
    from haute.routes._frontier_point_summary import frontier_point_summary

    names = constraint_names if constraint_names is not None else ["volume"]
    kinds = {name: "min" for name in names}
    data: dict[str, Any] = {
        "status": "ok",
        "points": actual_points,
        # The server's summary of each point, as ``limited_frontier_payload`` builds it.
        "point_summaries": [frontier_point_summary(point, kinds) for point in actual_points],
        "n_points": len(actual_points),
        "points_returned": len(actual_points),
        "points_limit": points_limit,
        "points_truncated": False,
        "constraint_names": names,
        "swept_axes": list(names),
        "frontier_generation": 0,
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Result dicts (the cached "result" payload on a completed job)
# ---------------------------------------------------------------------------


# The ``input_provenance`` a solve job records when it is created.
SOLVE_PROVENANCE: dict[str, str | None] = {
    "node_id": "opt",
    "data_source": "batch",
    "source_file": "main.py",
    "graph_fingerprint": "graph-fingerprint",
}


def make_scenario_grid(n_steps: int = 3) -> list[dict[str, Any]]:
    """A strictly increasing ``scenario_grid`` of *n_steps* steps from 0.9 by 0.1."""
    return [
        {"optimal_step": step, "scenario_value": round(0.9 + 0.1 * step, 10)}
        for step in range(n_steps)
    ]


# The ``scenario_grid`` a solve's setup records on the job before the solve.
SOLVE_SCENARIO_GRID: list[dict[str, Any]] = make_scenario_grid()


def make_input_summary(**overrides: Any) -> dict[str, Any]:
    """A solve result's ``input_summary``: the job's provenance and solver settings."""
    summary: dict[str, Any] = {
        "node_id": "opt",
        "data_source": "batch",
        "source_file": "main.py",
        "graph_fingerprint": "graph-fingerprint",
        "solver_settings": {
            "max_iter": 50,
            "tolerance": 1e-6,
            "chunk_size": None,
        },
    }
    summary.update(overrides)
    return summary


def with_solve_summary(job: dict[str, Any]) -> dict[str, Any]:
    """*job* whose solve result carries the ``input_summary`` its solve would have built.

    The summary comes from the job's own config (and ``input_provenance``,
    else ``SOLVE_PROVENANCE``) through the production builder, so a test that
    varies the config sees the settings that config produces.
    """
    from haute.routes._optimiser_solver import solve_input_summary

    provenance = job.get("input_provenance", SOLVE_PROVENANCE)
    summary = solve_input_summary({**job, "input_provenance": provenance})
    key = "base_result" if "base_result" in job else "result"
    job[key] = {**job.get(key, {}), "input_summary": summary}
    return job


def make_solved_result(
    *,
    mode: str = "online",
    total_objective: float = 95.0,
    baseline_objective: float = 90.0,
    constraints: dict[str, float] | None = None,
    baseline_constraints: dict[str, float] | None = None,
    lambdas: dict[str, float] | None = None,
    effective_bounds: dict[str, dict[str, Any]] | None = None,
    converged: bool = True,
    constraint_names: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """The base ``result`` dict an optimiser job persists post-solve.

    ``constraint_names`` keys every constraint map by those names (a ``min``
    bound of 0.9 each) instead of the default single ``volume`` constraint.
    """
    if constraint_names is not None:
        constraints = constraints or {name: 0.85 for name in constraint_names}
        baseline_constraints = baseline_constraints or {name: 0.85 for name in constraint_names}
        lambdas = lambdas or {name: 0.0 for name in constraint_names}
        effective_bounds = effective_bounds or {
            name: {"kind": "min", "bound": 0.9} for name in constraint_names
        }
    result: dict[str, Any] = {
        "mode": mode,
        "total_objective": total_objective,
        "baseline_objective": baseline_objective,
        "constraints": constraints if constraints is not None else {"volume": 0.85},
        "baseline_constraints": (
            baseline_constraints if baseline_constraints is not None else {"volume": 0.85}
        ),
        "effective_bounds": (
            effective_bounds
            if effective_bounds is not None
            else {"volume": {"kind": "min", "bound": 0.9}}
        ),
        "lambdas": lambdas if lambdas is not None else {"volume": 0.0},
        "converged": converged,
        "frontier_generation": 0,
        "input_summary": make_input_summary(),
        "diagnostics_errors": [],
        "scenario_grid": make_scenario_grid(extra.get("n_steps") or 3),
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
    constraint_bounds: dict[str, float] | None = None,
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
        constraint_bounds=(constraint_bounds if constraint_bounds is not None else {"volume": 0.9}),
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
                ),
                make_frontier_point(
                    objective=130.0,
                    volume=0.93,
                    lambda_volume=0.55,
                    threshold_volume=0.93,
                    converged=False,
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


def use_local_mlflow_store(tmp_path: Any, monkeypatch: Any) -> Any:
    """Point MLflow logging at a local store under *tmp_path*; return a client to read it.

    The optimiser's MLflow log resolves the empty destination to the project's
    local folder, so the project root moves to *tmp_path* and every Databricks,
    server and environment tracking setting is cleared.
    """
    from mlflow.tracking import MlflowClient

    from haute._sandbox import set_project_root

    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_MLFLOW_HOST",
        "DATABRICKS_MLFLOW_TOKEN",
        "MLFLOW_TRACKING_URI",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    return MlflowClient(tracking_uri=(tmp_path / "mlruns").as_uri())


def logged_json_artifacts(store: Any, run_id: str, destination: Any) -> dict[str, Any]:
    """Every top-level JSON artifact of *run_id*, downloaded under *destination*."""
    import json
    from pathlib import Path

    destination.mkdir(parents=True, exist_ok=True)
    return {
        artifact.path: json.loads(
            Path(store.download_artifacts(run_id, artifact.path, str(destination))).read_text(
                encoding="utf-8"
            )
        )
        for artifact in store.list_artifacts(run_id)
        if artifact.path.endswith(".json")
    }


def setup_grid_stub(grid: object | None = None) -> Any:
    """What a stubbed ``_build_grid`` returns: *grid* and no analysis table."""
    from haute.routes._optimiser_service import SetupGrid

    return SetupGrid(grid=object() if grid is None else grid, quote_analysis_handle=None)
