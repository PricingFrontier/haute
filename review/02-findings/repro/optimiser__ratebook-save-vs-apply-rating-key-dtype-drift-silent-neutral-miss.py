"""Adversarial repro: ratebook save-vs-apply rating-key dtype drift -> silent neutral 1.0 miss.

Claim under test
----------------
At SAVE time, ratebook factor-table level labels are canonicalised by
``normalise_rating_key`` against the BANDING-SOURCE factors frame's dtype.
A Float64 ``25.0`` banding value therefore saves the canonical level ``"25"``.

At APPLY time, ``_apply_ratebook`` -> ``_apply_rating_table`` re-canonicalises the
OPTIMISER_APPLY INPUT frame's factor column with ``_rating_key_expr`` using THAT
frame's dtype.  ``_rating_key_expr`` only collapses int-like *floats* to integer
strings; a Utf8 column is cast verbatim.  So if the same logical factor arrives at
apply time as a Utf8 column carrying the literal label ``"25.0"`` (a CSV/JSON source
distinct from the banding source, or an upstream cast), the apply key is ``"25.0"``
while the saved level is ``"25"`` -> the join MISSES.  Because the ratebook lookup
spec sets ``onMissing="neutral"`` with no defaultValue, the miss applies factor 1.0
(counted/logged) and the solver's real factor is silently dropped from the API result.

What this script proves
-----------------------
1. The save side (real functions ``_ratebook_factor_level_counts`` +
   ``_canonical_ratebook_table_level``) emits saved level ``"25"`` for a Float64 25.0
   banding source -- i.e. the artifact's stored factor key is "25", NOT "25.0".
2. Applying that artifact (via the real ``_apply_ratebook``) to a Utf8 "25.0" input
   frame yields ``age_optimised_factor == 1.0`` (NEUTRAL MISS) even though the saved
   table holds the solver's real factor 1.5 for that level.
3. CONTROL: applying the SAME artifact to a Float64 25.0 input frame yields 1.5 --
   proving the artifact is correct and the divergence is purely the dtype boundary.

A miss is also surfaced via ``rating_table_lookup_misses`` warning, but the API
result (the *_optimised_factor column) is silently wrong (1.0 instead of 1.5).

Isolation: pure in-memory Polars frames + module-level helpers. No disk I/O, no
reads/writes of rating/, src/, tests/, or any real project file.
"""

from __future__ import annotations

import polars as pl

from haute.routes._optimiser_service import (
    _canonical_ratebook_table_level,
    _ratebook_factor_level_counts,
    _ratebook_factor_table_name,
)
from haute._builders import _apply_ratebook


FACTOR = "age"
SOLVED_FACTOR = 1.5  # the relativity the solver computed for the 25.0 cohort


def _saved_factor_tables() -> dict[str, list[dict[str, object]]]:
    """Build the artifact ``factor_tables`` exactly as the SAVE path would.

    The banding source is a Float64 column carrying 25.0.  We run the real
    save-side canonicalisation to derive the stored level key, so the repro
    does not hand-assert the canonical form -- it observes it.
    """
    # Banding-source factors frame: Float64 dtype, value 25.0 (one quote).
    banding = pl.DataFrame({FACTOR: pl.Series([25.0], dtype=pl.Float64)})
    level_counts_by_table = _ratebook_factor_level_counts(banding, [[FACTOR]])
    table_name = _ratebook_factor_table_name([FACTOR])
    level_counts = level_counts_by_table[table_name]

    # price-contour emits the factor-table level from the source value's repr:
    # a Float64 25.0 arrives as the label "25.0".  The save side canonicalises it.
    solver_emitted_level = "25.0"
    saved_level = _canonical_ratebook_table_level(table_name, solver_emitted_level, level_counts)

    print(f"[save]  banding dtype=Float64 value=25.0")
    print(f"[save]  level_counts keys        = {sorted(level_counts)!r}")
    print(f"[save]  solver-emitted level     = {solver_emitted_level!r}")
    print(f"[save]  SAVED canonical level    = {saved_level!r}")
    assert saved_level == "25", (
        f"precondition: expected saved level '25' from Float64 25.0, got {saved_level!r}"
    )

    return {
        table_name: [
            {"__factor_group__": saved_level, "optimal_scenario_value": SOLVED_FACTOR},
        ]
    }


def _apply_factor(input_series: pl.Series, factor_tables: dict) -> float:
    """Run the real ratebook apply over one quote and return its optimised factor."""
    artifact = {"factor_tables": factor_tables}
    lf = pl.LazyFrame({FACTOR: input_series})
    out = _apply_ratebook(lf, artifact, version="", version_col="optimiser_version").collect()
    col = f"{FACTOR}_optimised_factor"
    return float(out[col][0])


def main() -> None:
    factor_tables = _saved_factor_tables()

    # ---- CONTROL: apply against the SAME dtype the table was saved with -------
    # Float64 25.0 -> _rating_key_expr collapses to "25" -> matches saved "25".
    control = _apply_factor(pl.Series([25.0], dtype=pl.Float64), factor_tables)
    print(f"[apply] Float64 25.0 input -> {FACTOR}_optimised_factor = {control}")
    assert control == SOLVED_FACTOR, (
        f"CONTROL FAILED: artifact is wrong independent of dtype drift "
        f"(expected {SOLVED_FACTOR}, got {control}). Repro inconclusive."
    )

    # ---- BUG: same logical factor arrives as Utf8 '25.0' at apply time --------
    # _rating_key_expr leaves a Utf8 column verbatim -> apply key "25.0" != "25".
    drifted = _apply_factor(pl.Series(["25.0"], dtype=pl.Utf8), factor_tables)
    print(f"[apply] Utf8 '25.0' input  -> {FACTOR}_optimised_factor = {drifted}")

    # The bug: a SILENT neutral-1.0 miss instead of the solver's real 1.5.
    assert drifted == 1.0, (
        f"Expected silent neutral miss (1.0) under save/apply dtype drift, got {drifted}"
    )
    assert drifted != SOLVED_FACTOR, (
        "If this fires the join matched and the claim is REFUTED: the apply side "
        "found the saved level despite the Utf8-vs-Float64 dtype divergence."
    )

    print()
    print("REPRODUCED: save-side canonical level '25' (from Float64 25.0) is MISSED by")
    print(f"the apply-side Utf8 '25.0' key -> onMissing=neutral applied factor 1.0 instead")
    print(f"of the solved {SOLVED_FACTOR}. Mispricing is silent in the API result.")


if __name__ == "__main__":
    main()
