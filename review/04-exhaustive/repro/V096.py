"""Isolated reproduction for V096.

Claim: the online frontier-point /apply branch (optimiser.py:1288) calls
``_store.clear_result_data(body.job_id)`` with NO keys argument, which
defaults to the FULL ``_HEAVY_OBJECT_KEYS`` set and therefore wipes
``quote_grid`` (and ``solver``) even though the user is mid
frontier-analysis session. Every OTHER frontier-session exit in this module
preserves that heavy state via ``_clear_result_data_after_user_action``
(which retains it while ``_job_has_frontier_points`` is True). Consequence:
after applying online frontier point 0, applying a DIFFERENT point N (which
has no cached ``frontier_apply_result:N`` artifact) re-enters
``_materialise_frontier_point_apply`` and hits
``touch_heavy_objects(required_keys=('quote_grid',))`` which now returns
False, raising HTTP 400 'Quote grid is not available for this job.'

ISOLATION:
- Project root is set to a fresh tempdir via ``haute._sandbox.set_project_root``.
- No rating/, src/, tests/, or real project files are read or written.
- The frontier job is a small synthetic dict injected straight into the
  in-memory job store the route already uses.
- ``price_contour.apply_from_grid`` is monkeypatched to a trivial stub so no
  heavy compute runs; the only on-disk write is a tiny parquet under the OS
  temp dir (tempfile.gettempdir()), which the service itself owns.

This asserts on the SPECIFIC wrong behaviour: the SECOND apply of a
different valid frontier point returns HTTP 400 ("Quote grid is not
available...") when a correctly-symmetric session would return 200.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import polars as pl
from fastapi.testclient import TestClient


def _frontier_point_summary(*, lambda_volume: float, total_objective: float) -> dict:
    """Stored frontier-point summary shape emitted by price-contour."""
    return {
        "threshold_volume": 0.9,
        "total_objective": total_objective,
        "total_volume": 0.9,
        "lambda_volume": lambda_volume,
        "iterations": 3,
        "converged": True,
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


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="v096_root_")
    import os

    import haute._sandbox

    haute._sandbox.set_project_root(Path(tmp))

    import price_contour
    from haute._local_security import (
        SESSION_TOKEN_ENV,
        SESSION_TOKEN_HEADER,
        local_session_token,
    )
    from haute.routes.optimiser import _store
    from haute.server import app

    # The app enforces a local-session token + trusted host; replicate the
    # conftest setup so requests are not rejected with 400 "Invalid host
    # header" / auth before they ever reach the route under test.
    os.environ[SESSION_TOKEN_ENV] = "v096-local-session-token"
    client_headers = {
        "host": "localhost",
        SESSION_TOKEN_HEADER: local_session_token(),
    }

    job_id = "v096_online_frontier"

    base_result = {
        "mode": "online",
        "total_objective": 100.0,
        "baseline_objective": 90.0,
        "constraints": {"volume": 0.9},
        "baseline_constraints": {"volume": 0.85},
        "lambdas": {"volume": 0.3},
        "converged": True,
    }

    # Online frontier job with TWO points; solver + quote_grid present, and NO
    # cached frontier_apply_result:* handles (so each point must materialise
    # against the live quote_grid). This is exactly the shape produced after a
    # solve + frontier run, before any apply.
    _store.jobs[job_id] = {
        "status": "completed",
        "solver": SimpleNamespace(name="solver"),
        "quote_grid": SimpleNamespace(name="quote_grid"),
        "config": {"mode": "online", "constraints": {"volume": {"min": 0.9}}},
        "base_result": base_result,
        "result": dict(base_result),
        "frontier_data": {
            "status": "ok",
            "points": [
                _frontier_point_summary(lambda_volume=0.7, total_objective=200.0),
                _frontier_point_summary(lambda_volume=0.9, total_objective=240.0),
            ],
            "n_points": 2,
            "points_returned": 2,
            "constraint_names": ["volume"],
            "points_limit": 100,
            "points_truncated": False,
        },
        "artifact_handles": {},
        "created_at": time.time(),
    }

    # Trivial apply stub: returns a small dataframe so materialisation succeeds
    # without any heavy price-contour compute.
    def _fake_apply_from_grid(quote_grid, *, lambdas, constraints):  # noqa: ANN001, ANN202
        return SimpleNamespace(
            dataframe=pl.DataFrame(
                {"quote_id": ["q1"], "optimal_scenario_value": [1.23]},
            )
        )

    original_apply = price_contour.apply_from_grid
    price_contour.apply_from_grid = _fake_apply_from_grid
    try:
        client = TestClient(
            app,
            raise_server_exceptions=False,
            base_url="http://localhost",
            headers=client_headers,
        )

        # --- Apply frontier point 0 (first point of a 2-point session) ---
        first = client.post(
            "/api/optimiser/apply",
            json={"job_id": job_id, "point_index": 0},
        )
        print("first apply status:", first.status_code)
        assert first.status_code == 200, f"expected 200, got {first.status_code}: {first.text}"

        # The job is still mid frontier-analysis: it retains its 2 frontier
        # points. A correct symmetric exit would keep quote_grid available.
        job_after_first = _store.jobs[job_id]
        points_still_present = (
            isinstance(job_after_first.get("frontier_data"), dict)
            and len(job_after_first["frontier_data"].get("points", [])) == 2
        )
        quote_grid_present = "quote_grid" in job_after_first
        print("after first apply: frontier points still present =", points_still_present)
        print("after first apply: quote_grid present =", quote_grid_present)

        # BUG MECHANISM: despite being mid-session, quote_grid was full-cleared.
        assert points_still_present, "frontier points unexpectedly gone"
        assert not quote_grid_present, (
            "EXPECTED-BUG-PRECONDITION not met: quote_grid was NOT cleared, "
            "so the full-clear regression may have already been fixed."
        )

        # --- Apply a DIFFERENT frontier point (point 1) in the same session ---
        second = client.post(
            "/api/optimiser/apply",
            json={"job_id": job_id, "point_index": 1},
        )
        print("second apply status:", second.status_code)
        print("second apply body:", second.text)

        # DEMONSTRABLY WRONG OUTCOME: the second valid point cannot be applied
        # because the runtime quote_grid was destroyed by the first apply.
        assert second.status_code == 400, (
            "Bug NOT reproduced: second apply did not 400 — it returned "
            f"{second.status_code}. The session may have been preserved."
        )
        detail = second.json().get("detail", "")
        assert "Quote grid is not available" in detail, (
            f"Got a 400 but not the predicted quote-grid error. detail={detail!r}"
        )

        print()
        print("REPRODUCED: applying frontier point 0 full-cleared quote_grid; a")
        print("subsequent apply of frontier point 1 (same live session) failed")
        print("with HTTP 400:", repr(detail))
        print("Expected behaviour: HTTP 200 (point 1 materialised against the")
        print("still-live quote_grid), matching the ratebook frontier-apply")
        print("symmetry contract.")
    finally:
        price_contour.apply_from_grid = original_apply
        _store.jobs.pop(job_id, None)


if __name__ == "__main__":
    main()
