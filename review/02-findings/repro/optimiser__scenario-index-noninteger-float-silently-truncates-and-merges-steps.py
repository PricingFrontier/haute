"""Adversarial repro for claim:
'A non-integer Float scenario_index passes the finite-value contract but is
silently truncated when cast to Int32, merging distinct scenario steps.'

Strategy
--------
We exercise the *actual* validation helpers from haute.routes._optimiser_service
(no reimplementation) plus the exact cast logic copied verbatim from
`_validate_and_project` (lines 4458-4479). We prove three things:

  (A) A Float64 scenario_index carrying [2.0, 2.5] for one quote_id passes the
      value-contract validation: `_non_finite_detail_from_counts(...)` is None
      (no 400). The contract only flags NaN/inf, not fractional values.

  (B) The cast map built exactly as in `_validate_and_project`
      (cast_map[step_col] = pl.Int32(); cast_to_float32 = {objective, mult, *constraints})
      truncates 2.5 -> 2, collapsing the two distinct scenario rows onto the
      SAME (quote_id, scenario_index) key.  This is the silent corruption.

  (C) Control: the SAME helpers DO raise for a NaN objective, proving the
      validation machinery works for what it covers -- the integrality gap is
      specific and not a setup error.

If (A) shows "no error" AND (B) shows the merge to a single Int32 step, the
claim is reproduced (a demonstrably wrong value: 2.5 silently becomes 2 and two
distinct scenario steps share one grid key).

ISOLATION: no disk I/O, no project files, only in-memory polars + the module's
own pure helper functions.
"""

from __future__ import annotations

import polars as pl

from haute.routes._optimiser_service import (
    _non_finite_check_columns,
    _non_finite_detail_from_counts,
    _value_contract_validation_exprs,
)

# Column names as the optimiser config would resolve them (defaults).
QID = "quote_id"
STEP = "scenario_index"
MULT = "scenario_value"
OBJ = "expected_income"

# Mirror exactly how `_validate_and_project` configures the contract check:
#   finite_columns          = [objective, mult_col, step_col, *constraint_cols]
#   cast_to_float32_columns = {objective, mult_col, *constraint_cols}      (NO step_col)
FINITE_COLUMNS = [OBJ, MULT, STEP]            # no constraints in this minimal case
CAST_TO_FLOAT32 = {OBJ, MULT}                 # step_col deliberately absent (as in prod)


def run_value_contract(df: pl.DataFrame) -> str | None:
    """Run the real value-contract validation exactly as the service does.

    Returns the non-finite contract detail string (what becomes a 400), or None
    when the data passes.
    """
    schema = df.schema
    non_finite_cols = _non_finite_check_columns(schema, FINITE_COLUMNS)
    exprs = _value_contract_validation_exprs(
        quote_id_col=QID,
        validate_quote_id_nulls=False,
        non_finite_check_cols=non_finite_cols,
        cast_to_float32_cols=CAST_TO_FLOAT32,
    )
    if not exprs:
        return None
    counts = df.lazy().select(exprs).collect()
    return _non_finite_detail_from_counts(counts, non_finite_cols)


def project_like_service(df: pl.DataFrame) -> pl.DataFrame:
    """Apply the EXACT cast logic from `_validate_and_project` (4468-4479)."""
    cast_map: dict[str, pl.DataType] = {
        STEP: pl.Int32(),
        MULT: pl.Float32(),
        OBJ: pl.Float32(),
    }
    cast_exprs = [pl.col(c).cast(t) for c, t in cast_map.items()]
    return df.lazy().with_columns(cast_exprs).collect()


def main() -> None:
    failures: list[str] = []

    # ---- The hazardous input: Float64 scenario_index with a fractional value.
    # Two DISTINCT scenario steps for the same quote: 2.0 and 2.5.
    bad = pl.DataFrame(
        {
            QID: ["q1", "q1"],
            STEP: [2.0, 2.5],          # Float64, fractional -> truncates on Int32 cast
            MULT: [1.00, 1.05],
            OBJ: [100.0, 110.0],
        }
    )
    assert bad.schema[STEP] == pl.Float64, f"setup: STEP must be Float64, got {bad.schema[STEP]}"

    # (A) Does the value contract reject the fractional scenario_index? It must
    #     NOT (the bug): only NaN/inf are checked, and STEP is float-typed so it
    #     is checked -- but 2.5 is finite, so it passes.
    detail = run_value_contract(bad)
    print(f"[A] value-contract detail for fractional STEP -> {detail!r}")
    if detail is not None:
        failures.append(
            "EXPECTED bug (contract passes fractional STEP) but contract RAISED: "
            f"{detail!r} -- claim would be REFUTED."
        )

    # Sanity: STEP must actually be in the set of checked (float) columns, else
    # the 'pass' is trivially because nobody looked at it. We want to show it IS
    # looked at and STILL passes.
    checked = _non_finite_check_columns(bad.schema, FINITE_COLUMNS)
    print(f"[A'] columns the contract actually NaN/inf-checks -> {checked}")
    if STEP not in checked:
        failures.append(
            f"setup expectation: STEP should be among checked float cols {checked}; "
            "if it weren't, the gap would be a different (looser) one."
        )

    # (B) Apply the real cast. 2.5 must truncate to Int32 2, MERGING the two
    #     distinct steps onto a single (quote_id, scenario_index) key.
    projected = project_like_service(bad)
    print(f"[B] projected scenario_index dtype -> {projected.schema[STEP]}")
    steps = projected[STEP].to_list()
    print(f"[B] projected rows (as dict) -> {projected.to_dict(as_series=False)}")
    assert projected.schema[STEP] == pl.Int32, projected.schema[STEP]
    if steps != [2, 2]:
        failures.append(
            f"EXPECTED truncation [2.0, 2.5] -> Int32 [2, 2]; got {steps}. "
            "If not [2,2] the merge claim would be REFUTED."
        )
    else:
        # Confirm the corruption: distinct (q,step) input -> duplicate key output.
        dup = (
            projected.group_by([QID, STEP])
            .len()
            .filter(pl.col("len") > 1)
            .height
        )
        print(f"[B] duplicate (quote_id, scenario_index) groups after cast -> {dup}")
        if dup != 1:
            failures.append(
                f"EXPECTED exactly one duplicated (quote_id, step) group; got {dup}."
            )
        else:
            print(
                "[B] CONFIRMED: two distinct scenario steps (2.0, 2.5) collapsed onto "
                "scenario_index Int32 2 for q1 with NO contract error."
            )

    # (C) Control: a NaN objective MUST be rejected by the same helpers, proving
    #     the machinery works and (A) is a real gap, not a dead code path.
    nan_obj = pl.DataFrame(
        {
            QID: ["q1", "q1"],
            STEP: [0, 1],
            MULT: [1.0, 1.05],
            OBJ: [100.0, float("nan")],
        }
    )
    ctrl = run_value_contract(nan_obj)
    print(f"[C] control: value-contract detail for NaN objective -> {ctrl!r}")
    if ctrl is None or "expected_income" not in ctrl:
        failures.append(
            "CONTROL FAILED: NaN objective was NOT rejected -> the validation path "
            "may be inert, so (A) would not prove a real gap."
        )

    # ---- Verdict ----------------------------------------------------------
    print("\n================ REPRO VERDICT ================")
    if failures:
        for f in failures:
            print("FAIL:", f)
        raise SystemExit("REPRO did NOT cleanly reproduce the claim (see FAILs above).")
    print(
        "REPRODUCED: Float64 scenario_index 2.5 passes the finite contract "
        "(no 400) and is silently truncated to Int32 2, merging two distinct "
        "scenario steps onto one (quote_id, scenario_index) key. Control proves "
        "the validation otherwise works."
    )


if __name__ == "__main__":
    main()
