"""ISOLATED reproduction for NEWBUG-2.

Claim: _fix_upstream_values matches the step to patch by ``node_name`` (the
human label, set from node_data.label) rather than by ``node_id`` (the unique
node identifier). input_sources only records ``node_name`` (built at
_build_input_sources line 1081). When two distinct nodes/steps share a label,
the ``for s in steps: if s.node_name != src_node_name: continue ... break``
loop patches the FIRST same-named step's ``output_values[col]`` with the
known-good value that actually belongs to a DIFFERENT (same-named) sibling
step -- silently corrupting the wrong step's displayed row.

This is DISTINCT from catalogued #28 (the 1e-6 absolute float-tolerance
collision between two rows of ONE node). To keep the two failures separate,
this repro:
  * uses an EXACT integer known value (no float tolerance involved at all), and
  * makes the eager_outputs frame for the wrongly-selected step contain a row
    whose value EXACTLY equals the known value, so the patch succeeds and
    writes onto the wrong step. The float-tolerance code path (#28) is never
    the cause here -- the defect is purely target-step selection by label.

Everything is synthetic and in-memory. No rating/, no real project files, no
src/ or tests/ mutation. A tempdir is set as a defensive CWD only.

Run: uv run python review/03-simplification/repro/projection-trace__NEWBUG-2.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import polars as pl

# Defensive: run from a throwaway tempdir so nothing touches real project files.
_tmp = tempfile.mkdtemp(prefix="newbug2_")
os.chdir(_tmp)

from haute._trace_correlation import SchemaDiff
from haute._trace_enrichment import _fix_upstream_values
from haute.trace import TraceStep


def _step(node_id: str, node_name: str, output_values: dict) -> TraceStep:
    """Build a real TraceStep. node_name is the (collidable) human label."""
    return TraceStep(
        node_id=node_id,
        node_name=node_name,  # human label -- NOT unique (node_data.label)
        node_type="formula",
        schema_diff=SchemaDiff(
            columns_added=list(output_values.keys()),
            columns_removed=[],
            columns_modified=[],
            columns_passed=[],
        ),
        input_values={},
        output_values=dict(output_values),
    )


def main() -> int:
    COL = "rate"

    # Two DISTINCT nodes that happen to share the SAME human label "Factor".
    # This is exactly the duplicate-label situation catalogued in #19 (distinct
    # node_ids, shared name/label).
    #
    #   step_first  : node_id="node_a"  label="Factor"  -> output_values[rate]=None
    #                 (its OWN correlated row legitimately had no value for COL)
    #   step_source : node_id="node_b"  label="Factor"  -> output_values[rate]=42
    #                 (this is the TRUE source the known value was derived from)
    step_first = _step("node_a", "Factor", {COL: None})
    step_source = _step("node_b", "Factor", {COL: 42})
    steps = [step_first, step_source]

    # input_sources as built by _build_input_sources: it records ONLY node_name
    # (line 1081) and the result_value derived from the TRUE source (node_b).
    # The node_id of the true source is NOT carried -- that is the root defect.
    input_sources = {
        COL: {
            "node_name": "Factor",      # ambiguous label -> selects FIRST match
            "result_value": 42,         # derived from node_b (the real source)
            "expression_text": f"{COL} = lookup()",
        }
    }

    # eager_outputs keyed by node_id. node_a's OWN dataframe genuinely contains
    # an UNRELATED row that also equals 42 for COL (e.g. a coincidental other
    # row). The exact-equality filter (non-float branch) matches it, so the
    # wrong step gets "successfully" patched. Note: node_a's true displayed
    # value for this trace was None; 99 below is the value that legitimately
    # belongs to node_a's correlated row in a different column to show the row
    # identity, but COL specifically should remain None for node_a.
    eager_outputs = {
        "node_a": pl.DataFrame({COL: [42], "other": ["belongs_to_A_row"]}),
        "node_b": pl.DataFrame({COL: [42], "other": ["belongs_to_B_row"]}),
    }

    before = step_first.output_values.get(COL)
    assert before is None, f"precondition: node_a {COL} should start None, got {before!r}"

    _fix_upstream_values(input_sources, steps, eager_outputs)

    after_first = step_first.output_values.get(COL)
    after_source = step_source.output_values.get(COL)

    print(f"step_first  (node_a, label 'Factor') {COL}: before={before!r} after={after_first!r}")
    print(f"step_source (node_b, label 'Factor') {COL}: {after_source!r}")
    print()

    # THE BUG: the loop matched step_first (node_a) purely because it is the
    # FIRST step whose node_name == 'Factor', and wrote the value belonging to
    # the node_b derivation onto node_a's output_values. node_a's COL was None
    # and should have stayed None (the known value was NOT derived from node_a);
    # instead it is now 42.
    if after_first == 42:
        print(
            "BUG REPRODUCED: _fix_upstream_values patched node_a (FIRST step "
            "sharing the label 'Factor') using a known value that belongs to "
            f"node_b. node_a.output_values['{COL}'] went None -> 42, silently "
            "corrupting the wrong step's displayed row."
        )
        print(
            "  Root cause: matching `if s.node_name != src_node_name` "
            "(_trace_enrichment.py:992) uses the non-unique label, and "
            "input_sources carries only node_name (line 1081), never node_id."
        )
        # Hard assertion on the specific wrong value.
        assert after_first == 42, "expected the wrong-step patch to write 42"
        assert step_first.node_id == "node_a", "the corrupted step is node_a"

        # CONTROL: prove this is wrong-TARGET selection, not "the value matched".
        # Re-run with order reversed and node_a's own df NOT containing 42. The
        # loop still selects the FIRST label-'Factor' step and `break`s, so the
        # genuine source is never reached. Here the genuine source that NEEDS
        # the fix is node_b (None), placed FIRST; node_a (already 42, the real
        # owner) is second. The loop hits node_b first, but node_b's own df has
        # no 42, so the fixup silently fails AND never tries node_a -- the true
        # owner is skipped purely because of the label `break`.
        ctrl_source = _step("node_b", "Factor", {COL: None})   # needs fixing
        ctrl_owner = _step("node_a", "Factor", {COL: 42})      # real owner of 42
        ctrl_steps = [ctrl_source, ctrl_owner]
        ctrl_inputs = {COL: {"node_name": "Factor", "result_value": 42}}
        ctrl_eager = {
            "node_b": pl.DataFrame({COL: [7]}),    # node_b's own df: no 42
            "node_a": pl.DataFrame({COL: [42]}),   # 42 lives here, never reached
        }
        _fix_upstream_values(ctrl_inputs, ctrl_steps, ctrl_eager)
        print(
            f"  CONTROL: with node_b('Factor', None) first and node_a('Factor', 42) "
            f"second -> node_b after={ctrl_source.output_values.get(COL)!r} "
            f"(loop broke on first label match; node_a's df never consulted)."
        )
        assert ctrl_source.output_values.get(COL) is None, (
            "control: node_b stays None -- the `break` prevented falling through "
            "to any other same-named step, confirming target selection (not the "
            "float tolerance) is the defect"
        )
        return 0

    print(
        "NOT REPRODUCED: node_a was not patched (after="
        f"{after_first!r}). The bug did not fire."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
