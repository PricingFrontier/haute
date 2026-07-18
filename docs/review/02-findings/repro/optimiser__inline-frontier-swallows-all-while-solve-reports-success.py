"""Adversarial reproduction for claim:
  inline-frontier-swallows-all-while-solve-reports-success

Claim: When config.frontier_enabled=True and constraints are set, the inline
efficient-frontier compute in `_finalize_solve_result` wraps the whole frontier
sweep in a `try/except Exception`. A genuine fault in the frontier (solver
divergence, infeasible ranges, NaN/overflow, a real library bug) is caught,
logged at WARNING, and written ONLY as result['frontier_error'] = "Frontier
unavailable: <exc>". The job STILL transitions to status='completed' with
progress=1.0 and result['frontier']=None.

This contradicts the repo's fail-loud mandate: a frontier the actuary will use
to pick a price point silently did not compute, yet the job is reported as a
fully successful solve.

This script drives the real `_finalize_solve_result` with a mock solver whose
`.frontier(...)` raises a RuntimeError (standing in for *any* genuine frontier
fault), then ASSERTS on the specific wrong outcome:
    status == "completed"  (NOT "failed")
    progress == 1.0
    result["frontier"] is None
    result["frontier_error"] is a non-empty string

ISOLATION: only in-memory JobStore + a tempdir project root. No real project
file is read or written. No src/ or tests/ file is imported except the public
service entry point under test.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl

import haute._sandbox as _sandbox
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import _finalize_solve_result


def _make_solve_result():
    """A converged, fully-successful primary solve result (mirrors the shape
    `_finalize_solve_result` expects from the online solver)."""
    df = pl.DataFrame({"optimal_scenario_value": [0.9, 1.0, 1.1, 1.2, 0.8]})
    return SimpleNamespace(
        dataframe=df,
        total_objective=100.0,
        baseline_objective=95.0,
        total_constraints={"volume": 0.92},
        baseline_constraints={"volume": 0.88},
        lambdas={"volume": 0.5},
        converged=True,
    )


def main() -> int:
    # Sandbox any incidental project-root resolution to a throwaway tempdir.
    tmp = tempfile.mkdtemp(prefix="frontier_repro_")
    _sandbox.set_project_root(Path(tmp))

    store = JobStore()
    job_id = store.create_job(
        {
            "status": "running",
            "config": {
                # constraints set  AND  frontier explicitly requested:
                "constraints": {"volume": {"min": 0.9}},
                "frontier_enabled": True,
                # a valid (min,max) range so we get past range validation and
                # actually reach the solver.frontier(...) call, which is where
                # a *genuine* frontier fault would surface.
                "frontier_ranges": {"volume": {"min": 0.8, "max": 1.1}},
                "frontier_steps": 3,
            },
        }
    )

    solve_result = _make_solve_result()

    # Stand in for a genuine frontier fault: solver divergence, infeasible
    # threshold sweep, NaN/overflow in the predictor-corrector lambdas, or a
    # real library bug. Any of these raise out of solver.frontier(...).
    mock_solver = MagicMock()
    mock_solver.frontier.side_effect = RuntimeError(
        "predictor-corrector diverged: lambda overflow (NaN)"
    )

    # Run the real finalize step. If the codebase were fail-loud here, this
    # would raise or mark the job failed.
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
    status = job.get("status")
    progress = job.get("progress")
    result = job.get("result") or {}
    frontier = result.get("frontier")
    frontier_error = result.get("frontier_error")
    frontier_data = job.get("frontier_data")

    print("=== observed outcome after a genuine frontier fault ===")
    print(f"  solver.frontier called : {mock_solver.frontier.called}")
    print(f"  job status             : {status!r}")
    print(f"  job progress           : {progress!r}")
    print(f"  result['frontier']     : {frontier!r}")
    print(f"  result['frontier_error']: {frontier_error!r}")
    print(f"  job['frontier_data']   : {frontier_data!r}")

    # --- The fault must actually have reached the swallowing try/except. ----
    assert mock_solver.frontier.called, (
        "SETUP FAILURE: solver.frontier was never called, so the frontier "
        "fault path was not exercised. Repro inconclusive."
    )

    failures: list[str] = []

    # The crux: a frontier fault is reported as a fully successful solve.
    if status != "completed":
        failures.append(
            f"EXPECTED-IF-REFUTED: status would be 'failed'/'error' after a "
            f"frontier fault, but bug-claim predicts 'completed'. Got {status!r}."
        )
    if progress != 1.0:
        failures.append(
            f"EXPECTED-IF-REFUTED: progress<1.0 to signal incompletion, but "
            f"bug-claim predicts 1.0. Got {progress!r}."
        )
    if frontier is not None:
        failures.append(
            f"frontier was computed ({frontier!r}); the fault was not swallowed."
        )
    if not (isinstance(frontier_error, str) and frontier_error):
        failures.append(
            f"frontier_error is not a non-empty string: {frontier_error!r}"
        )

    if failures:
        print("\n*** CLAIM NOT REPRODUCED ***")
        for f in failures:
            print("  - " + f)
        return 1

    # All bug-predicted wrong values observed.
    assert status == "completed"
    assert progress == 1.0
    assert frontier is None
    assert frontier_data is None
    assert isinstance(frontier_error, str) and frontier_error.startswith(
        "Frontier unavailable:"
    )
    # Confirm the *original* exception text is buried inside the soft string.
    assert "predictor-corrector diverged" in frontier_error

    print("\n*** CLAIM REPRODUCED ***")
    print(
        "  A genuine frontier fault was swallowed into a non-fatal "
        "frontier_error string;\n"
        "  the job is reported status='completed', progress=1.0, "
        "frontier=None.\n"
        "  A fail-loud design would have failed the job (or re-raised); "
        "instead the\n"
        "  failure is demoted to a soft warning the actuary can miss."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
