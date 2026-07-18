"""Adversarial repro for claim:
ratebook-frontier-point-resolve-diverges-from-sweep-optimum

CLAIM: _materialise_ratebook_frontier_point re-solves coordinate descent from
the stored point's lambdas (a DIFFERENT warm-start than the sweep's
predictor-corrector trajectory). Because ratebook CD is not guaranteed to land
on a unique global optimum, the re-solved factor tables / total_objective /
constraints can DISAGREE with the displayed frontier-chart row the user clicked
and that disagreement (beyond cd_tolerance) is what gets persisted to the
deploy artifact.

This script exercises the ALGORITHMIC core directly against the real compiled
price_contour.RatebookOptimiser (the divergence, if any, lives entirely in the
solver; haute's route is a thin pass-through that calls
`solver.solve(grid, factors, factor_columns=..., lambdas=point.lambdas,
_constraints_override={name: {thr_key: thr_at_point}})` — exactly replicated
below).

It asserts the claim's positive statement: that for at least one frontier point
the re-solve-from-stored-lambdas result DIFFERS from the sweep's recorded
optimum by MORE than cd_tolerance (in total_objective, a total_constraint, or a
factor-table value). If every point round-trips within tolerance, the claim is
REFUTED and the script raises AssertionError saying so.

No haute imports, no disk I/O outside tempfile, no project files touched.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import polars as pl

from price_contour import RatebookOptimiser
from price_contour.ratebook import RatebookResult


# ---------------------------------------------------------------------------
# Synthetic scored grid + factors.
#
# Two factor columns ("seg", "band") whose per-group income peaks trade off
# against a single SUM constraint ("cost") so that tightening the cost ceiling
# forces the factors down. A non-trivial CD landscape with two interacting
# factor axes — exactly the "two factor columns whose levels trade off against
# one constraint" the claim's repro strategy asks for.
# ---------------------------------------------------------------------------

SEP = "\x1f"


def _build_data() -> tuple[pl.DataFrame, pl.DataFrame, list[list[str]]]:
    # group income peaks (where expected_income is maximised over scenario_value)
    seg_band_peak = {
        ("A", "lo"): 1.30,
        ("A", "hi"): 0.75,
        ("B", "lo"): 0.80,
        ("B", "hi"): 1.28,
    }
    quotes: list[tuple[str, str, str]] = []
    qi = 0
    for (seg, band) in seg_band_peak:
        for _ in range(3):  # 3 quotes per group -> some mass
            quotes.append((f"q{qi}", seg, band))
            qi += 1

    scenario_values = (0.70, 0.85, 1.00, 1.15, 1.30, 1.40)
    rows: list[dict[str, Any]] = []
    for quote_id, seg, band in quotes:
        peak = seg_band_peak[(seg, band)]
        for step, sv in enumerate(scenario_values):
            income = 100.0 - 180.0 * (sv - peak) ** 2
            # "cost" grows with the scenario value (higher relativity = more
            # cost), so a max-cost ceiling pushes factors DOWN. Make the cost
            # slope group-dependent so the two factor axes interact with the
            # single constraint.
            cost_slope = 40.0 if seg == "A" else 55.0
            cost = cost_slope * sv
            rows.append(
                {
                    "quote_id": quote_id,
                    "scenario_index": step,
                    "scenario_value": sv,
                    "expected_income": income,
                    "cost": cost,
                }
            )

    scored = pl.DataFrame(rows).with_columns(
        pl.col("scenario_index").cast(pl.Int32),
        pl.col("scenario_value").cast(pl.Float32),
        pl.col("expected_income").cast(pl.Float32),
        pl.col("cost").cast(pl.Float32),
    )
    factors_df = pl.DataFrame(
        {
            "quote_id": [q[0] for q in quotes],
            "seg": [q[1] for q in quotes],
            "band": [q[2] for q in quotes],
        }
    )
    factor_columns = [["seg"], ["band"]]
    return scored, factors_df, factor_columns


def _make_solver(constraints: dict[str, dict[str, float]]) -> RatebookOptimiser:
    return RatebookOptimiser(
        objective="expected_income",
        constraints=constraints,
        factor_columns=[["seg"], ["band"]],
        candidate_min=0.70,
        candidate_max=1.40,
        candidate_steps=15,
        max_cd_iterations=8,
        cd_tolerance=1e-3,
        max_iter=60,
        tolerance=1e-6,
    )


def _baseline_cost(scored: pl.DataFrame) -> float:
    base = scored.filter(pl.col("scenario_value") == pl.col("scenario_value").min())
    # baseline = scenario_value closest to 1.0; use exact 1.0 rows
    base = scored.filter((pl.col("scenario_value") - 1.0).abs() < 1e-6)
    return float(base["cost"].sum())


def main() -> int:
    scored, factors_df, factor_columns = _build_data()
    cd_tol = 1e-3

    base_cost = _baseline_cost(scored)
    # Sweep a max-cost ceiling from tight to loose around the baseline cost.
    lo = 0.80 * base_cost
    hi = 1.20 * base_cost
    constraints = {"cost": {"max": hi}}  # constructor threshold (gets swept)

    solver = _make_solver(constraints)

    # --- Capture the sweep's EXACT per-point RatebookResult objects. ---
    # The frontier DataFrame only stores total_objective / total_<name> /
    # lambda_<name>; it discards factor_tables. To compare factor tables we
    # capture the real result object the sweep computed at each point by
    # wrapping self.solve and recording every call. The frontier visits points
    # in nearest-neighbour order; we key captured results by the (rounded)
    # threshold so we can line them up with the public frontier rows.
    captured: list[tuple[dict[str, float], RatebookResult]] = []
    real_solve = solver.solve

    def _recording_solve(*args: Any, **kwargs: Any) -> RatebookResult:
        res = real_solve(*args, **kwargs)
        override = kwargs.get("_constraints_override")
        thr = None
        if isinstance(override, dict) and "cost" in override:
            spec = override["cost"]
            thr = {k: float(v) for k, v in spec.items() if k in ("max", "min")}
        captured.append((thr or {}, res))
        return res

    solver.solve = _recording_solve  # type: ignore[method-assign]
    frontier = solver.frontier(
        scored,
        factors_df,
        threshold_ranges={"cost": (lo, hi)},
        n_points_per_dim=6,
        factor_columns=factor_columns,
    )
    solver.solve = real_solve  # type: ignore[method-assign]

    points_df = frontier.points
    print("Frontier rows (threshold_cost, total_objective, total_cost, lambda_cost, converged):")
    for _r in points_df.iter_rows(named=True):
        print(
            f"  thr={float(_r['threshold_cost']):.4f} "
            f"obj={float(_r['total_objective']):.6f} "
            f"cost={float(_r['total_cost']):.4f} "
            f"lam={float(_r['lambda_cost']):.6f} "
            f"conv={_r['converged']}"
        )

    # Map captured sweep results by rounded threshold for lookup.
    def _key(thr_max: float) -> int:
        return round(float(thr_max) * 1e6)

    swept_by_thr: dict[int, RatebookResult] = {}
    for thr, res in captured:
        if "max" in thr:
            swept_by_thr[_key(thr["max"])] = res

    # --- Now replicate haute's materialise re-solve for each frontier row. ---
    # haute (_materialise_ratebook_frontier_point) calls a FRESH solver.solve
    # warm-started from the stored point's lambdas, with the per-point
    # threshold override. We use a clean solver instance (no recording) — this
    # is precisely the "different warm-start trajectory" the claim describes,
    # because the sweep reached this point via predictor-corrector from
    # adjacent points, whereas the re-solve starts from the point's own
    # recorded converged lambdas.
    resolver = _make_solver(constraints)

    n = points_df.height
    worst_obj = 0.0
    worst_cost = 0.0
    worst_factor = 0.0
    worst_row: dict[str, Any] | None = None
    diverged_rows: list[int] = []

    for i in range(n):
        row = points_df.row(i, named=True)
        thr_cost = float(row["threshold_cost"])
        swept_obj = float(row["total_objective"])
        swept_total_cost = float(row["total_cost"])
        point_lambdas = {"cost": float(row["lambda_cost"])}

        override = {"cost": {"max": thr_cost}}
        re = resolver.solve(
            scored,
            factors_df,
            factor_columns=factor_columns,
            lambdas=point_lambdas,
            _constraints_override=override,
        )

        d_obj = abs(re.total_objective - swept_obj)
        d_cost = abs(float(re.total_constraints.get("cost", 0.0)) - swept_total_cost)

        # Factor-table divergence vs the captured sweep result for this point.
        swept_res = swept_by_thr.get(_key(thr_cost))
        d_factor = 0.0
        factor_detail = "no-captured-match"
        if swept_res is not None:
            factor_detail = ""
            for name, table in re.factor_tables.items():
                sweep_table = swept_res.factor_tables.get(name, {})
                for level, val in table.items():
                    sv = float(sweep_table.get(level, float("nan")))
                    dd = abs(float(val) - sv)
                    if dd > d_factor:
                        d_factor = dd
                        factor_detail = f"{name}[{level!r}] sweep={sv:.6f} resolve={float(val):.6f}"

        is_div = (d_obj > cd_tol) or (d_cost > 10 * cd_tol) or (d_factor > cd_tol)
        if is_div:
            diverged_rows.append(i)
        if d_obj > worst_obj:
            worst_obj = d_obj
        if d_cost > worst_cost:
            worst_cost = d_cost
        if d_factor > worst_factor:
            worst_factor = d_factor
            worst_row = {
                "i": i,
                "threshold_cost": thr_cost,
                "swept_obj": swept_obj,
                "resolve_obj": re.total_objective,
                "d_obj": d_obj,
                "swept_total_cost": swept_total_cost,
                "resolve_total_cost": float(re.total_constraints.get("cost", 0.0)),
                "d_cost": d_cost,
                "factor_detail": factor_detail,
                "d_factor": d_factor,
            }

        print(
            f"point {i}: thr={thr_cost:.4f} "
            f"d_obj={d_obj:.3e} d_cost={d_cost:.3e} d_factor={d_factor:.3e} "
            f"{'<-- DIVERGES' if is_div else ''} {factor_detail}"
        )

    print("\n==== SUMMARY ====")
    print(f"cd_tolerance = {cd_tol}")
    print(f"worst |d total_objective| over points = {worst_obj:.6e}")
    print(f"worst |d total_cost|       over points = {worst_cost:.6e}")
    print(f"worst |d factor value|     over points = {worst_factor:.6e}")
    print(f"diverged rows = {diverged_rows}")
    if worst_row is not None:
        print(f"worst factor-table divergence detail: {worst_row}")

    # CLAIM ASSERTION: the saved (re-solved) result must DIFFER from the
    # displayed sweep point by more than tolerance for at least one point.
    claim_holds = bool(diverged_rows)
    assert claim_holds, (
        "REFUTED: every frontier point's re-solve-from-stored-lambdas "
        "reproduced the sweep's recorded optimum within cd_tolerance "
        f"(worst d_obj={worst_obj:.3e}, worst d_cost={worst_cost:.3e}, "
        f"worst d_factor={worst_factor:.3e}). The materialise re-solve does "
        "NOT diverge from the displayed frontier point on this landscape."
    )
    print("\nCLAIM REPRODUCED: at least one frontier point's saved re-solve "
          "diverges from the displayed sweep optimum beyond tolerance.")
    return 0


if __name__ == "__main__":
    # Ensure any incidental temp artefacts stay in a sandbox dir.
    with tempfile.TemporaryDirectory() as _tmp:
        _ = Path(_tmp)
        sys.exit(main())
