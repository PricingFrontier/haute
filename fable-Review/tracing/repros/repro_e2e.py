"""End-to-end: relocation silently anchors the trace to the WRONG row."""
from __future__ import annotations
import sys, tempfile, os
sys.path.insert(0, r"C:\Users\prici\haute\src")
sys.path.insert(0, r"C:\Users\prici\haute")

import polars as pl
from tests.conftest import make_graph as _g, make_edge as _edge
from tests.conftest import make_source_node as _source_node, make_transform_node as _transform_node
from haute.trace import execute_trace

tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "policies.parquet")
# id distinguishes rows 1 and 2, but region+premium are IDENTICAL for them.
# The output table the user sees shows region+premium (not id).
pl.DataFrame({
    "id": [900, 111, 222],
    "region": ["south", "north", "north"],
    "base":   [5, 10, 10],
}).write_parquet(p)

graph = _g({
    "nodes": [
        _source_node("src", p),
        _transform_node("t", "df = df.with_columns(premium=pl.col('base') * 2)"),
    ],
    "edges": [_edge("src", "t")],
})

# Simulate the documented trigger: preview cache evicted, cold re-exec, row
# order changed so the clicked row_index no longer holds the clicked values.
# User clicked a (north, premium=20) row — which upstream is id=111 or id=222?
# They point at row_index=0, whose ACTUAL values are (south, premium=10):
row_values = {"region": "north", "premium": 20}
result = execute_trace(
    graph,
    row_index=0,                 # mismatches row_values -> triggers relocation
    target_node_id="t",
    column="premium",
    row_values=row_values,
)

src_step = [s for s in result.steps if s.node_id == "src"][0]
print(f"relocated row_index      : {result.row_index}")
print(f"source step 'id' shown   : {src_step.output_values.get('id')}")
print(f"correlation diagnostics  : {result.correlation_diagnostics}")
print()
print("=> Two upstream rows (id=111, id=222) are indistinguishable on the")
print("   clicked columns. Relocation picked the FIRST (id=111) with NO")
print("   diagnostic. If the user clicked the id=222 row, every upstream")
print("   value in this regulator-facing trace is silently wrong.")
