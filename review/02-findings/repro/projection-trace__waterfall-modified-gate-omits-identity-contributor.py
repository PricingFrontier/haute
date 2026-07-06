"""Adversarial repro: waterfall omits a real rating step that is identity
(factor 1.0) for the traced row.

Mechanism under test
--------------------
* ``_compute_schema_diff`` (src/haute/_trace_correlation.py:149-158) marks a
  column ``modified`` ONLY if ``in_val != out_val`` on the single traced row.
* ``build_waterfall_from_steps`` (src/haute/_trace_waterfall.py:483) emits a
  contribution for a non-base step ONLY if ``column in diff.columns_modified``.

So a rating step that genuinely multiplies the column but evaluates to factor
1.0 for THIS row (e.g. ``factor = when(region=='X').then(1.2).otherwise(1.0)``
and the row is not region X) leaves the cell numerically unchanged. The column
is classified ``passed`` -> the step is dropped from the waterfall entirely.
Because the value did not change, the final cumulative still reconciles, so no
WaterfallReconciliationError fires. The omission is SILENT.

This script asserts on the SPECIFIC behaviour:
  - the identity-for-this-row step IS present in ``steps`` and classified
    ``passed`` (not ``modified``) by the real schema-diff function;
  - the returned waterfall is NOT an error payload (no error fired);
  - the identity step's label is ABSENT from the waterfall entries while the
    two genuinely-changing steps ARE present;
  - the waterfall total still equals the traced output value (correct number).

No disk I/O, no project root, no real rating/ files — pure in-memory TraceSteps.
"""

from __future__ import annotations

import sys

from haute._trace_correlation import _compute_schema_diff
from haute._trace_waterfall import build_waterfall_from_steps
from haute.trace import TraceStep

COLUMN = "premium"


def _step(
    node_id: str,
    node_name: str,
    input_row: dict | None,
    output_row: dict,
    *,
    expression_text: str | None = None,
) -> TraceStep:
    """Build a TraceStep whose schema_diff comes from the REAL production
    classifier, so the gating decision is value-derived exactly as in prod."""
    diff = _compute_schema_diff(input_row, output_row)
    expression = None
    if expression_text is not None:
        expression = {
            "target_column": COLUMN,
            "expression_text": expression_text,
        }
    return TraceStep(
        node_id=node_id,
        node_name=node_name,
        node_type="ratingStep",
        schema_diff=diff,
        input_values=input_row if input_row is not None else {},
        output_values=output_row,
        expression=expression,
    )


def main() -> int:
    # A 4-step chain that all genuinely act on `premium`:
    #   source      -> premium = 100.0           (base)
    #   region_reli -> x 1.2 GENERALLY, but 1.0 for THIS row (not region X)
    #   base_rate   -> x 2.0                       (100 -> 200)
    #   loading     -> x 1.5                       (200 -> 300)
    #
    # The region relativity is a real multiply node, but the traced row is not
    # in region X, so its factor evaluates to 1.0 -> premium unchanged 100->100.
    source = _step("src", "policies", None, {"region": "Y", COLUMN: 100.0})

    region = _step(
        "region_reli",
        "region_relativity",
        {"region": "Y", COLUMN: 100.0},
        {"region": "Y", COLUMN: 100.0},  # identity for THIS row
        expression_text="premium * 1.2",  # real multiply in general
    )

    base = _step(
        "base_rate",
        "base_rate",
        {"region": "Y", COLUMN: 100.0},
        {"region": "Y", COLUMN: 200.0},
        expression_text="premium * 2.0",
    )

    loading = _step(
        "loading",
        "expense_loading",
        {"region": "Y", COLUMN: 200.0},
        {"region": "Y", COLUMN: 300.0},
        expression_text="premium * 1.5",
    )

    steps = [source, region, base, loading]

    parents_of = {
        "src": [],
        "region_reli": ["src"],
        "base_rate": ["region_reli"],
        "loading": ["base_rate"],
    }

    # --- Pre-condition checks: the region step really is a no-op-for-this-row
    # rating step, classified `passed` (not `modified`) by the real classifier.
    assert COLUMN in region.schema_diff.columns_passed, (
        f"setup invalid: expected {COLUMN!r} in columns_passed for the identity "
        f"step, got passed={region.schema_diff.columns_passed} "
        f"modified={region.schema_diff.columns_modified}"
    )
    assert COLUMN not in region.schema_diff.columns_modified, (
        "setup invalid: identity step should NOT be classified modified"
    )
    # And the genuinely-changing steps ARE classified modified.
    assert COLUMN in base.schema_diff.columns_modified
    assert COLUMN in loading.schema_diff.columns_modified

    result = build_waterfall_from_steps(
        steps,
        COLUMN,
        target_node_id="loading",
        final_output_value=300.0,
        parents_of=parents_of,
        node_map=None,
    )

    print(f"result type: {type(result).__name__}")
    print(f"result: {result}")

    # The call must succeed (not None, not an error payload). No reconciliation
    # error fires because the omitted step contributed factor 1.0.
    assert result is not None, "BUG-SETUP: waterfall returned None (feature N/A)"
    assert isinstance(result, list), (
        f"expected a list of entries (no error), got {result!r}"
    )
    assert not (isinstance(result, dict) and "error" in result), (
        f"expected NO error payload, but reconciliation fired: {result!r}"
    )

    labels = [e["label"] for e in result]
    print(f"waterfall labels: {labels}")

    # The genuinely-changing steps are present.
    assert "base_rate" in labels, f"base_rate missing from {labels}"
    assert "expense_loading" in labels, f"expense_loading missing from {labels}"

    # The waterfall total still equals the traced output value (correct number).
    final_cumulative = result[-1]["cumulative"]
    print(f"final cumulative: {final_cumulative} (traced output value: 300.0)")
    assert abs(final_cumulative - 300.0) < 1e-9, (
        f"total should reconcile to 300.0, got {final_cumulative}"
    )

    # THE BUG: the real rating step that was identity for THIS row is silently
    # absent from the explanation, even though it is present in `steps`.
    region_present_in_steps = any(s.node_name == "region_relativity" for s in steps)
    region_in_waterfall = "region_relativity" in labels
    print(
        f"region_relativity present in steps: {region_present_in_steps}; "
        f"present in waterfall: {region_in_waterfall}"
    )

    if region_present_in_steps and not region_in_waterfall:
        print(
            "\nREPRODUCED: a real rating step (region_relativity) that evaluated "
            "to factor 1.0 for THIS row is present in the trace steps but SILENTLY "
            "OMITTED from the waterfall explanation. No error payload was produced "
            "and the total is correct (300.0). The actuary cannot see the node was "
            "applied."
        )
        return 0

    print(
        "\nNOT REPRODUCED: the identity-for-this-row step appeared in the "
        "waterfall (or an error fired); claimed completeness gap does not hold."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
