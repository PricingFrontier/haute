"""Adversarial reproduction for claim:
posthoc-positional-fastpath-reorder

Hypothesis under test
---------------------
`_correlate_rows_posthoc` has a positional fast path (src/haute/_trace_correlation.py:632-647).
When parent and child have the SAME row count and share NO column names, the
`if not shared:` branch (637-643) blindly accepts the parent row at the *child's
positional index* as the lineage source.

If the child node performed a transform that PRESERVES row count but REORDERS rows
(e.g. full rename `select(col('a').alias('b'))` followed by `sort('b', descending=True)`),
positional identity no longer holds, so the fast path attaches the WRONG parent row.

Scenario
--------
parent: a = [10, 20, 30]
child : b = [30, 20, 10]   # b = a.alias('b'); sort('b', descending=True)

Click child row 0 -> b == 30.
The TRUE source of b==30 is a==30 (parent positional index 2).
The fast path will instead return parent row 0 -> a==10 (WRONG).

This script asserts on the SPECIFIC wrong value (a==10) vs the correct value (a==30).

ISOLATION: pure in-memory synthetic frames; no disk I/O; no project files touched.
"""

from __future__ import annotations

import sys

import polars as pl

from haute._trace_correlation import _correlate_rows_posthoc
from haute._types import GraphNode, NodeData, NodeType


def build_inputs():
    parent_id = "parent"
    child_id = "child"

    parent_df = pl.DataFrame({"a": [10, 20, 30]})
    # child = parent.select(pl.col('a').alias('b')).sort('b', descending=True)
    # => row order reversed, NO shared column name with parent ('a' renamed to 'b').
    child_df = (
        parent_df.select(pl.col("a").alias("b")).sort("b", descending=True)
    )

    eager_outputs = {parent_id: parent_df, child_id: child_df}
    order = [parent_id, child_id]
    parents_of = {child_id: [parent_id]}

    node_map = {
        parent_id: GraphNode(
            id=parent_id,
            data=NodeData(label="parent", nodeType=NodeType.DATA_SOURCE),
        ),
        child_id: GraphNode(
            id=child_id,
            data=NodeData(
                label="child",
                nodeType=NodeType.POLARS,
                config={"code": "df.select(pl.col('a').alias('b')).sort('b', descending=True)"},
            ),
        ),
    }
    return eager_outputs, order, parents_of, child_id, node_map, child_df


def main() -> int:
    eager_outputs, order, parents_of, child_id, node_map, child_df = build_inputs()

    # Sanity: confirm child row 0 is b == 30 (what the user clicked).
    clicked = child_df.row(0, named=True)
    assert clicked["b"] == 30, f"setup error: expected clicked b==30, got {clicked}"

    result = _correlate_rows_posthoc(
        eager_outputs,
        order,
        parents_of,
        child_id,
        0,  # clicked row index in the target (child) node
        node_map=node_map,
    )

    correlated_parent = result.get("parent")
    print("clicked child row :", clicked)
    print("correlated parent :", correlated_parent)

    # The TRUE lineage source of b==30 is a==30.
    correct = {"a": 30}
    # The fast path positionally aligns: parent.row(0) == a==10.
    predicted_wrong = {"a": 10}

    if correlated_parent == correct:
        print("RESULT: NOT REPRODUCED — correlation returned the correct upstream row.")
        return 0

    if correlated_parent == predicted_wrong:
        print(
            "RESULT: REPRODUCED — fast path attached the WRONG parent row.\n"
            f"  expected (true source of b==30): {correct}\n"
            f"  actual   (positional row 0)    : {correlated_parent}"
        )
        # Assert on the specific wrong value so the script fails loudly when buggy.
        assert correlated_parent != correct, (
            "BUG CONFIRMED: correlated parent is the positionally-aligned row, "
            "not the true lineage source."
        )
        # Hard-fail to make the wrong behaviour unmistakable in the exit code.
        raise AssertionError(
            f"BUG: correlated parent={correlated_parent} (a={correlated_parent['a']}) "
            f"but the true source of clicked b=30 is a=30."
        )

    print(
        "RESULT: UNEXPECTED — correlation returned neither the correct nor the "
        f"predicted-wrong row: {correlated_parent}"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
