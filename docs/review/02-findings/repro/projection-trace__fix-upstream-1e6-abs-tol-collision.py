"""Adversarial repro: _fix_upstream_values uses a FIXED 1e-6 ABSOLUTE float
tolerance to relocate an upstream source row, colliding distinct
small-magnitude factors and overwriting the displayed value with the WRONG row.

Claim under test (fix-upstream-1e6-abs-tol-collision), located at
src/haute/_trace_enrichment.py:1003-1011:

    if isinstance(known_value, float):
        matched = df.filter((pl.col(col_name) - known_value).abs() < 1e-6)
    ...
    if len(matched) > 0:
        new_row = _jsonify_row(matched.row(0, named=True))
        s.output_values[col_name] = new_row.get(col_name)

The tolerance is ABSOLUTE (1e-6) and scale-dependent. For multiplicative
rating relativities near 1.0 (e.g. 1.0000004 vs 1.0000001, |delta| = 3e-7 <
1e-6), TWO genuinely distinct factor rows both satisfy the filter. The code
then unconditionally takes `.row(0)` (no `len(matched) == 1` uniqueness guard),
so the step's displayed output_values[col] can be overwritten with a DIFFERENT
row's value than the known one -> silently wrong lineage.

ISOLATION: everything is built in memory. No real project file is read or
written. A tempdir is created defensively for the sandbox project root even
though this code path does not touch disk.

Exit 0 == bug reproduced (the displayed value was overwritten with the WRONG,
non-known factor). Exit 1 == claim could NOT be reproduced as stated.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import polars as pl

import haute._sandbox as _sandbox
from haute._trace_correlation import SchemaDiff
from haute._trace_enrichment import _fix_upstream_values
from haute.trace import TraceStep

# Two genuinely DISTINCT multiplicative relativities near 1.0.
# |1.0000004 - 1.0000001| = 3e-7  <  1e-6  (the absolute tolerance).
FACTOR_ROW0 = 1.0000004  # the WRONG row (index 0) the code will pick
FACTOR_ROW1 = 1.0000001  # the KNOWN-GOOD value (index 1) the code SHOULD pick

assert FACTOR_ROW0 != FACTOR_ROW1, "factors must be distinct floats"
assert abs(FACTOR_ROW0 - FACTOR_ROW1) < 1e-6, "delta must be below the abs tol"
# Sanity: a relative comparison would NOT collide these (rel delta ~3e-7 of 1.0
# is well above rel_tol=1e-9 used by _trace_values_match elsewhere in the code).


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="haute_repro_"))
    _sandbox.set_project_root(tmp)  # defensive; this path does not touch disk

    src_node_id = "src_node_id"
    src_node_name = "src_node"
    col = "factor"

    # Source DataFrame: row 0 is the distractor, row 1 holds the known value.
    src_df = pl.DataFrame({col: [FACTOR_ROW0, FACTOR_ROW1]})

    # The upstream step whose output_values the post-hoc correlator got WRONG:
    # output_values[col] is None (row-correlation failure state).
    src_step = TraceStep(
        node_id=src_node_id,
        node_name=src_node_name,
        node_type="formula",
        schema_diff=SchemaDiff(
            columns_added=[col],
            columns_removed=[],
            columns_modified=[],
            columns_passed=[],
        ),
        input_values={},
        output_values={col: None},
    )

    # input_sources carries the KNOWN-GOOD value from expression evaluation.
    # The correct, known factor is FACTOR_ROW1 (== src_df row index 1).
    input_sources = {
        col: {
            "node_name": src_node_name,
            "result_value": FACTOR_ROW1,
        }
    }

    eager_outputs = {src_node_id: src_df}

    # --- invoke the code under test ---
    _fix_upstream_values(input_sources, [src_step], eager_outputs)

    displayed = src_step.output_values[col]
    print(f"known_value (correct)      = {FACTOR_ROW1!r}")
    print(f"src_df rows                = {src_df[col].to_list()!r}")
    print(f"displayed output_values    = {displayed!r}")

    # The bug: the displayed value is the WRONG row (FACTOR_ROW0), not the known
    # value (FACTOR_ROW1). If the relocation were correct (or guarded by
    # uniqueness), displayed would equal FACTOR_ROW1.
    if displayed == FACTOR_ROW1:
        print(
            "\nNOT REPRODUCED: displayed value equals the known-good value; "
            "the relocation picked the correct row."
        )
        return 1
    if displayed == FACTOR_ROW0:
        print(
            "\nREPRODUCED: the 1e-6 ABSOLUTE tolerance collided two distinct "
            "small factors and `.row(0)` overwrote the displayed value with the "
            f"WRONG row ({FACTOR_ROW0!r}) instead of the known value "
            f"({FACTOR_ROW1!r}). Silently wrong lineage."
        )
        return 0
    print(f"\nUNEXPECTED: displayed value {displayed!r} is neither factor.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
