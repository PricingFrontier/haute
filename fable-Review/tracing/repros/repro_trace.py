"""Empirical repros for trace backend-core review findings."""
from __future__ import annotations
import sys, os
sys.path.insert(0, r"C:\Users\prici\haute\src")

import polars as pl
import math

print("=" * 70)
print("REPRO 1 — FR-06: _find_target_row_index returns first of ambiguous")
print("=" * 70)
from haute.trace import _find_target_row_index
from haute._trace_correlation import _find_matching_row

# Two rows identical in VISIBLE columns (region, premium) but different
# hidden upstream identity (policy_id). row_values carries only visible cols.
df = pl.DataFrame({
    "policy_id": [101, 202],
    "region": ["north", "north"],
    "premium": [50.0, 50.0],
})
row_values = {"region": "north", "premium": 50.0}
idx = _find_target_row_index(df, row_values)
print(f"_find_target_row_index -> {idx}  (silently picks first; the click "
      f"could have been policy_id={df['policy_id'][idx]} OR the other)")

# Contrast: the deeper correlation matcher REFUSES the same ambiguity.
diags: list = []
row, cidx = _find_matching_row(df, row_values, diagnostics=diags)
print(f"_find_matching_row     -> (row is None: {row is None}, idx={cidx}), "
      f"diagnostics={[d['reason'] for d in diags]}")
print("=> entry-point relocation is NOT fail-loud; deeper matcher IS. Asymmetric.\n")


print("=" * 70)
print("REPRO 2 — FR-05: the two correlation paths disagree on float equality")
print("=" * 70)
from haute._trace_correlation import _trace_values_match, _build_value_match_expr

child = 1234.5678
parent = 1234.567800617284   # drift ~6e-7 relative ~5e-10, inside isclose window
print(f"drift = {abs(child-parent):.3e}  rel = {abs(child-parent)/abs(child):.3e}")

# Fast/positional path predicate:
fast = _trace_values_match(parent, child)
print(f"_trace_values_match(parent, child)          -> {fast}")

# Vectorised value-match path predicate (what _find_matching_row uses):
pdf = pl.DataFrame({"v": [parent]})
expr = _build_value_match_expr("v", child, pdf.schema["v"])
matched = pdf.select(expr.alias("m"))["m"][0]
print(f"_build_value_match_expr(v == {child}) on parent -> {matched}")
print(f"=> fast path says MATCH, value-match path says {matched}. "
      f"Which one a user hits depends on incidental row-count equality.\n")


print("=" * 70)
print("REPRO 3 — FR-07: _jsonify_row renders Datetime/List unlike the preview")
print("=" * 70)
from datetime import datetime
from haute._trace_correlation import _jsonify_row
from haute._json_safe import to_json_safe

raw = {"ts": datetime(2020, 1, 2, 3, 4, 5), "tags": [1, 2, 3]}
# Emulate how polars hands these back in a row(named=True):
pdf = pl.DataFrame({"ts": [datetime(2020,1,2,3,4,5)], "tags": [[1,2,3]]})
raw_row = pdf.row(0, named=True)
trace_side = _jsonify_row(raw_row)
preview_side = {k: to_json_safe(v) for k, v in raw_row.items()}
print(f"trace   _jsonify_row : {trace_side}")
print(f"preview to_json_safe : {preview_side}")
print(f"=> ts differs: {trace_side['ts']!r} vs {preview_side['ts']!r}")
print(f"=> tags differs: {trace_side['tags']!r} (str) vs {preview_side['tags']!r} (array)\n")


print("=" * 70)
print("REPRO 4 — _assemble_steps: multi-parent merge keeps FIRST parent bare")
print("=" * 70)
from haute.trace import _assemble_steps

# Two parents both carry column 'shared' with DIFFERENT values feeding a join child.
order = ["pA", "pB", "child"]
source_ids = {"pA", "pB"}
parents_of = {"child": ["pA", "pB"], "pA": [], "pB": []}

class _D:
    def __init__(self, label, nodeType):
        self.label = label; self.nodeType = nodeType; self.config = {}
class _N:
    def __init__(self, label, nodeType):
        self.data = _D(label, nodeType)
node_map = {
    "pA": _N("pA", "dataSource"),
    "pB": _N("pB", "dataSource"),
    "child": _N("child", "polars"),
}
cached_rows = {
    "pA": {"shared": "A_value", "a_only": 1},
    "pB": {"shared": "B_value", "b_only": 2},
    "child": {"shared": "A_value", "a_only": 1, "b_only": 2},
}
steps = _assemble_steps(order=order, source_ids=source_ids, node_map=node_map,
                        parents_of=parents_of, cached_rows=cached_rows)
child_step = [s for s in steps if s.node_id == "child"][0]
print(f"child input_values = {child_step.input_values}")
print(f"=> 'shared' is bare (=pA's {child_step.input_values.get('shared')!r}); "
      f"pB's copy is under 'pB.shared'={child_step.input_values.get('pB.shared')!r}")
print(f"=> schema_diff.modified = {child_step.schema_diff.columns_modified}, "
      f"removed = {child_step.schema_diff.columns_removed}")
print("=> asymmetric/order-dependent provenance for join inputs.\n")
