"""Adversarial repro for claim:
'frontier-point-reconstruction-prefix-collision-and-blind-lambda-parse'

Thesis: _frontier_point_constraint_value(point, name) extracts the constraint
value by the string prefix key f"total_{name}" on a FLAT point dict that the
library *always* populates with a reserved 'total_objective' key (the OBJECTIVE
total). So a constraint named 'objective' has its reconstructed constraint value
silently aliased to the objective total. Symmetrically, _frontier_point_lambdas
collects every 'lambda_*' key, so 'lambda_objective' is folded into the lambdas
dict keyed 'objective'.

This script asserts on the SPECIFIC WRONG VALUE (objective total vs a distinct
constraint value), not merely that something raised.

It also probes whether a constraint literally named 'objective' is reachable
(no config-level guard) and whether the upstream library DataFrame emitter
tolerates such a name (reachability / severity evidence).

Isolation: pure in-memory dicts. No disk I/O, no project files touched.
"""

from __future__ import annotations

import sys
import traceback

from haute.routes.optimiser import (
    _frontier_point_constraint_value,
    _frontier_point_lambdas,
    _frontier_point_result_dict,
)

failures: list[str] = []
notes: list[str] = []


# ---------------------------------------------------------------------------
# Part 1: _frontier_point_constraint_value aliases a constraint named
# 'objective' to the reserved 'total_objective' (objective total) key.
# ---------------------------------------------------------------------------
# Construct a flat frontier point exactly as the library emits it:
#   total_objective = the OBJECTIVE total (always present, reserved key)
#   total_objective is ALSO what f"total_{name}" resolves to when name=='objective'
# We give the objective total a value distinct from what a constraint-named-
# 'objective' field would carry, to show the alias.
OBJECTIVE_TOTAL = 100.0
CONSTRAINT_OWN_VALUE = 42.0  # the value the constraint *should* report

point = {
    "total_objective": OBJECTIVE_TOTAL,  # reserved: objective total
    "lambda_objective": 0.5,             # lambda for constraint 'objective'
    "threshold_objective": 1.0,
    "converged": True,
}

got = _frontier_point_constraint_value(point, "objective")
print(f"[part1] _frontier_point_constraint_value(point, 'objective') -> {got!r}")
print(f"[part1] objective total in point['total_objective']        -> {OBJECTIVE_TOTAL!r}")

# The bug: the function returns the OBJECTIVE total, because total_key=='total_objective'
# collides with the reserved objective-total key. It can NEVER return a distinct
# constraint value for a constraint named 'objective'.
if got == OBJECTIVE_TOTAL:
    notes.append(
        "CONFIRMED part1: constraint-value lookup for name='objective' returned "
        f"the objective total {OBJECTIVE_TOTAL} (point['total_objective']) via the "
        "f'total_{name}' prefix collision, NOT a distinct constraint value."
    )
else:
    failures.append(
        f"part1 NOT reproduced: expected the objective total {OBJECTIVE_TOTAL} to be "
        f"returned via prefix collision, got {got!r}"
    )

# Demonstrate that there is literally no way for this function to distinguish a
# constraint's own value from the objective total when the constraint is named
# 'objective': even if the point carried a *separate* notion, total_objective is
# consumed first (line 470 short-circuits). We show the precedence: total_key
# wins over point.get('constraints') and over a bare point[name].
point_with_constraints_block = {
    "total_objective": OBJECTIVE_TOTAL,
    "constraints": {"objective": CONSTRAINT_OWN_VALUE},  # a "true" constraint value
    "lambda_objective": 0.5,
    "converged": True,
}
got2 = _frontier_point_constraint_value(point_with_constraints_block, "objective")
print(
    f"[part1b] with constraints={{'objective': {CONSTRAINT_OWN_VALUE}}} present, "
    f"function still returns -> {got2!r}"
)
if got2 == OBJECTIVE_TOTAL and got2 != CONSTRAINT_OWN_VALUE:
    notes.append(
        "CONFIRMED part1b: even when a distinct constraints['objective']="
        f"{CONSTRAINT_OWN_VALUE} is present, the 'total_objective' prefix key "
        f"short-circuits and the objective total {OBJECTIVE_TOTAL} is returned "
        "(the authoritative constraint value is shadowed)."
    )
else:
    failures.append(
        "part1b NOT reproduced: expected objective total to shadow "
        f"constraints['objective']; got {got2!r}"
    )


# ---------------------------------------------------------------------------
# Part 2: _frontier_point_lambdas blindly collects every 'lambda_*' key.
# Show a non-constraint 'lambda_*' key is folded into the reconstructed lambdas.
# ---------------------------------------------------------------------------
point_lambdas = {
    "lambda_volume": 0.25,            # legit constraint lambda
    "lambda_objective": 0.5,          # collides with a constraint named 'objective'
    "lambda_internal_diagnostic": 9.0,  # hypothetical future non-constraint lambda_* key
    "converged": True,
}
recon = _frontier_point_lambdas(point_lambdas)
print(f"[part2] _frontier_point_lambdas(...) -> {recon!r}")
# The function trusts the 'lambda_' prefix, NOT the constraint_names list, so any
# lambda_* key (including a non-constraint one) is included.
if recon.get("internal_diagnostic") == 9.0 and recon.get("objective") == 0.5:
    notes.append(
        "CONFIRMED part2: _frontier_point_lambdas folded a non-constraint "
        "'lambda_internal_diagnostic' key and a 'lambda_objective' key into the "
        "reconstructed lambdas; extraction is by prefix, not by constraint_names."
    )
else:
    failures.append(f"part2 NOT reproduced: recon={recon!r}")


# ---------------------------------------------------------------------------
# Part 3 (end-to-end): _frontier_point_result_dict on a job whose frontier
# declares constraint_names=['objective'] -> the reconstructed constraints dict
# reports the objective total as the value of constraint 'objective'.
# ---------------------------------------------------------------------------
job = {
    "frontier_data": {
        "points": [point],
        "n_points": 1,
        "points_returned": 1,
        "constraint_names": ["objective"],
    },
    "base_result": {
        "baseline_objective": 7.0,
        "baseline_constraints": {"objective": 3.0},
    },
}
try:
    result = _frontier_point_result_dict(job, 0)
    recon_constraints = result.get("constraints", {})
    recon_total_obj = result.get("total_objective")
    print(
        f"[part3] result['constraints'] -> {recon_constraints!r}; "
        f"result['total_objective'] -> {recon_total_obj!r}"
    )
    if (
        recon_constraints.get("objective") == OBJECTIVE_TOTAL
        and recon_total_obj == OBJECTIVE_TOTAL
    ):
        notes.append(
            "CONFIRMED part3 (end-to-end): _frontier_point_result_dict reported "
            f"constraints['objective']={OBJECTIVE_TOTAL} == total_objective, i.e. the "
            "saved/selected constraint value for 'objective' is the objective total."
        )
    else:
        failures.append(
            "part3 NOT reproduced: expected constraints['objective']==total_objective=="
            f"{OBJECTIVE_TOTAL}; got constraints={recon_constraints!r}, "
            f"total_objective={recon_total_obj!r}"
        )
except Exception as exc:  # noqa: BLE001
    failures.append(f"part3 raised unexpectedly: {exc!r}")
    traceback.print_exc()


# ---------------------------------------------------------------------------
# Reachability probe: is a constraint named 'objective' rejected anywhere, and
# does the upstream library DataFrame emitter tolerate it (duplicate
# total_objective column)? This is severity evidence, not a pass/fail gate.
# ---------------------------------------------------------------------------
try:
    import polars as pl  # noqa: F401
    from price_contour._frontier_helpers import _build_points_dataframe

    rows = [
        {
            "threshold_objective": 1.0,
            "total_objective": OBJECTIVE_TOTAL,  # objective total (reserved)
            "lambda_objective": 0.5,
            "iterations": 3,
            "converged": True,
            "sv_mean": 1.0, "sv_std": 0.0, "sv_min": 1.0, "sv_p5": 1.0,
            "sv_p25": 1.0, "sv_median": 1.0, "sv_p75": 1.0, "sv_p95": 1.0,
            "sv_max": 1.0, "sv_pct_increase": 0.0, "sv_pct_decrease": 0.0,
        }
    ]
    try:
        df = _build_points_dataframe(rows, ["objective"])
        notes.append(
            "REACHABILITY: library _build_points_dataframe(constraint_names=['objective']) "
            f"did NOT error; resulting columns={df.columns!r} (duplicate total_objective "
            "collapses to a single column -> constraint-vs-objective genuinely indistinguishable)."
        )
    except Exception as exc:  # noqa: BLE001
        notes.append(
            "REACHABILITY: library _build_points_dataframe raised for constraint "
            f"named 'objective': {type(exc).__name__}: {exc}. This means a flat point "
            "with that duplicate key may not arise via the normal Rust/Python emitter, "
            "lowering in-practice reachability (the haute function bug stays latent)."
        )
except Exception as exc:  # noqa: BLE001
    notes.append(f"REACHABILITY probe skipped (import error): {exc!r}")


# ---------------------------------------------------------------------------
print("\n==== NOTES ====")
for n in notes:
    print("  -", n)

if failures:
    print("\n==== FAILURES (claim NOT cleanly reproduced) ====")
    for f in failures:
        print("  -", f)
    sys.exit(1)

print("\nALL CORE ASSERTIONS REPRODUCED: prefix-collision + blind-lambda-parse confirmed.")
sys.exit(0)
