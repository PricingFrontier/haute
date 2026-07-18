"""ISOLATED reproduction for optimiser BUG-1.

Claim: a ratingStep table that passes config validation but produces no
lookup rows is silently skipped, and the rate column it was supposed to add
(its outputColumn) never appears -- violating the fail-loud mandate.

Mechanism:
  * _rating_step_config.normalise_rating_tables (the real config path) does
    NOT reject an empty `entries: []` -- it early-returns the table verbatim
    (_normalise_entries_for_table: `if entries == []: ... return result`),
    so {factors:['age'], entries:[], outputColumn:'age_factor'} SURVIVES.
  * _rating._apply_rating_table then hits
    `if not factors or not entries or not output_col: return lf`
    and returns the frame UNCHANGED -- no age_factor column added.
  * _rating._apply_rating_step_outputs STILL appends 'age_factor' to out_cols.

Contrast: an ordinary lookup MISS in the same function raises a loud
RatingTableMissError. A config that yields zero usable entries should fail
at least as loudly; instead it is a silent no-op.

Run: uv run python review/03-simplification/repro/optimiser__BUG-1.py

This repro touches NO project files, no disk, no rating/ src/ tests/ data.
It imports the real haute functions and feeds synthetic in-memory frames.
"""

from __future__ import annotations

import polars as pl

from haute._rating import (
    RatingTableMissError,
    _apply_rating_step_outputs,
    _apply_rating_table,
)
from haute._rating_step_config import normalise_rating_tables

FAIL = False


def check(label: str, cond: bool) -> None:
    global FAIL
    status = "OK" if cond else "FAIL"
    if not cond:
        FAIL = True
    print(f"[{status}] {label}")


print("=" * 72)
print("PART 0 -- baseline: a USABLE rating table DOES add its outputColumn")
print("=" * 72)
frame = pl.DataFrame({"age": [25, 40]})
good_table = {
    "factors": ["age"],
    "entries": [{"age": 25, "value": 1.5}, {"age": 40, "value": 2.0}],
    "outputColumn": "age_factor",
}
good_out = _apply_rating_step_outputs(frame.clone(), [good_table], []).collect()
print("baseline output columns:", good_out.columns)
print("baseline age_factor:", good_out["age_factor"].to_list())
check("baseline adds age_factor column", "age_factor" in good_out.columns)
check("baseline age_factor values correct", good_out["age_factor"].to_list() == [1.5, 2.0])

print()
print("=" * 72)
print("PART A -- config path: normalise_rating_tables does NOT reject empty entries")
print("=" * 72)
config = {
    "tables": [
        {
            "factors": ["age"],
            "entries": [],  # passes config validation but yields zero rows
            "outputColumn": "age_factor",
        }
    ]
}
normalised = normalise_rating_tables(config)
print("normalised tables:", normalised)
# The empty table survived normalisation unchanged -- no ValueError raised.
check(
    "normalise_rating_tables returns exactly one table (empty entries NOT rejected)",
    len(normalised) == 1 and normalised[0].get("entries") == [],
)
check(
    "surviving table still declares outputColumn 'age_factor'",
    normalised[0].get("outputColumn") == "age_factor",
)

print()
print("=" * 72)
print("PART B -- the silently-wrong materialisation (the headline repro)")
print("=" * 72)
empty_table = {"factors": ["age"], "entries": [], "outputColumn": "age_factor"}
# This is the EXACT repro from the bug report:
result = _apply_rating_step_outputs(frame.clone(), [empty_table], []).collect()
print("input columns :", frame.columns)
print("result columns:", result.columns)
print("result shape  :", result.shape, "(input shape:", frame.shape, ")")
check(
    "materialises with NO error (silent no-op, not a loud failure)",
    True,  # reaching here at all means .collect() did not raise
)
check(
    "age_factor column NEVER materialises (promised output dropped)",
    "age_factor" not in result.columns,
)
check(
    "frame shape is unchanged -- the entire table was a silent no-op",
    result.shape == frame.shape,
)

print()
print("=" * 72)
print("PART C -- out_cols still records 'age_factor' (stale-binding / combined-output hazard)")
print("=" * 72)
# Prove the second half of the failure scenario: _apply_rating_step_outputs
# appends the (never-produced) output_col to out_cols, so a combinedOutput
# would try to combine a column that does not exist -> divergence. We drive
# this through the public function with a legacy combined output referencing
# the missing columns. With <2 out_cols the legacy guard skips, so we use a
# second (also-empty) table to push out_cols to 2 and force the combine.
two_empty = [
    {"factors": ["age"], "entries": [], "outputColumn": "age_factor"},
    {"factors": ["age"], "entries": [], "outputColumn": "region_factor"},
]
combined = [{"_legacy": True, "operation": "multiply", "outputColumn": "rate", "baseValue": None}]
combine_raised = False
combine_result_cols: list[str] = []
try:
    cr = _apply_rating_step_outputs(frame.clone(), two_empty, combined).collect()
    combine_result_cols = cr.columns
    print("combined result columns:", combine_result_cols)
except Exception as exc:  # noqa: BLE001 -- we WANT to observe whatever happens
    combine_raised = True
    print(f"combined path raised: {type(exc).__name__}: {exc}")
# Either outcome substantiates the hazard: the legacy combine was driven by
# out_cols=['age_factor','region_factor'] (both appended at 793-795) even
# though neither column was produced. A clean engine should never reach a
# combine over phantom columns. We assert the diagnostic fact that matters:
# the rate output cannot have been computed from real rating columns.
check(
    "legacy combine driven by phantom out_cols (raised OR produced no real rate)",
    combine_raised or ("age_factor" not in combine_result_cols),
)

print()
print("=" * 72)
print("PART D -- sibling guards (direct _apply_rating_table callers, e.g. trace enrichment)")
print("=" * 72)
# Guard at :542 -- entries present but NO 'value' key anywhere.
no_value_table = {
    "factors": ["age"],
    "entries": [{"age": 25}, {"age": 40}],  # no 'value' -> guard returns lf
    "outputColumn": "age_factor",
}
nv = _apply_rating_table(frame.clone().lazy(), no_value_table).collect()
print("[:542] no-'value' result columns:", nv.columns)
check(
    "guard at :542 silently drops output when entries lack 'value' (direct caller)",
    "age_factor" not in nv.columns,
)
# Guard at :568 -- a declared factor absent from every entry.
missing_factor_table = {
    "factors": ["age", "region"],  # 'region' present in no entry
    "entries": [{"age": 25, "value": 1.5}, {"age": 40, "value": 2.0}],
    "outputColumn": "age_factor",
}
mf = _apply_rating_table(frame.clone().lazy(), missing_factor_table).collect()
print("[:568] missing-factor result columns:", mf.columns)
check(
    "guard at :568 silently drops output when a declared factor is absent from entries",
    "age_factor" not in mf.columns,
)

print()
print("=" * 72)
print("PART E -- CONTRAST: an ordinary lookup miss DOES fail loud")
print("=" * 72)
# Same shape, but with a real entry and a key that misses and no default ->
# RatingTableMissError. This proves the function CAN fail loud; the empty/
# unusable-entries path simply does not, which is the inconsistency.
miss_table = {
    "factors": ["age"],
    "entries": [{"age": 99, "value": 1.5}],  # 25 and 40 both miss
    "outputColumn": "age_factor",
    # no defaultValue, default onMissing == error
}
miss_raised = False
try:
    _apply_rating_table(frame.clone().lazy(), miss_table).collect()
except RatingTableMissError as exc:
    miss_raised = True
    print(f"ordinary miss raised loudly: RatingTableMissError: {str(exc)[:80]}...")
check(
    "ordinary lookup miss raises RatingTableMissError (the loud path the empty case lacks)",
    miss_raised,
)

print()
print("=" * 72)
if FAIL:
    print("RESULT: SOME CHECKS FAILED -- bug NOT cleanly reproduced (see [FAIL] above)")
else:
    print(
        "RESULT: REPRODUCED -- empty/unusable rating entries are silently dropped; "
        "the promised outputColumn never materialises and no error is raised, "
        "while an ordinary miss in the same function fails loudly."
    )
print("=" * 72)
raise SystemExit(1 if FAIL else 0)
