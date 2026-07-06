"""Isolated reproduction for V006.

Claim: the *unordered* demand walk in projection.py omits Polars
keyword-constraint filter columns, e.g. ``df.filter(segment='A')`` where the
keyword name ``segment`` IS a required parent column. The *ordered* walk
already handles this exact case (projection.py:1124-1125), so the two demand
walks are inconsistent and a pure ``df.filter(segment='A')`` node (no
rename/select/derived-reference) is routed to the unordered walk, which
under-demands the parent.

This repro performs NO disk I/O, needs NO project root, and asserts on the
specific WRONG VALUE (a parent-demand set that is missing 'segment'), not
merely that "something raised". It also independently confirms via Polars
that the column is genuinely required.
"""

from __future__ import annotations

from haute._types import GraphNode, NodeData, NodeType
from haute.projection import (
    SingleParentPolarsExpressionRule,
    _ordered_expression_demands,
    _single_parent_polars_expression_demands,
    _unordered_expression_demands,
)

import ast


def _demands_for(code: str, needed: set[str]) -> set[str] | None:
    return _single_parent_polars_expression_demands(code, needed)


def main() -> None:
    # ------------------------------------------------------------------
    # Case 1: pure keyword-constraint filter, the headline shape.
    #   df = df.filter(segment='A') with downstream needing {'premium'}.
    #   'segment' is a required parent column (sugar for
    #   pl.col('segment') == 'A'); the parent demand MUST include it.
    # ------------------------------------------------------------------
    code = "df = df.filter(segment='A')"
    needed = {"premium"}

    tree = ast.parse(code)
    ordered = _ordered_expression_demands(tree, set(needed))
    unordered = _unordered_expression_demands(ast.parse(code), set(needed))
    routed = _demands_for(code, set(needed))

    print(f"[case1] ordered   demand = {sorted(ordered) if ordered else ordered}")
    print(f"[case1] unordered demand = {sorted(unordered) if unordered else unordered}")
    print(f"[case1] routed    demand = {sorted(routed) if routed else routed}")

    expected = {"premium", "segment"}

    # The ordered branch is the reference for correctness: it includes segment.
    assert ordered == expected, (
        f"sanity: ordered branch should demand {expected}, got {ordered}"
    )

    # The bug: the unordered branch drops the keyword-named column 'segment'.
    assert unordered == {"premium"}, (
        f"expected the (buggy) unordered branch to drop 'segment' and return "
        f"{{'premium'}}, got {unordered}"
    )
    assert "segment" not in unordered, "unordered branch unexpectedly kept 'segment'"

    # The router sends pure filter-keyword code to the unordered walk, so the
    # demand actually produced by the engine omits 'segment' -> WRONG VALUE.
    assert routed == {"premium"}, (
        f"expected routed demand to (wrongly) omit 'segment' and equal "
        f"{{'premium'}}, got {routed}"
    )
    assert routed != expected, (
        "BUG NOT REPRODUCED: routed demand already matches the correct "
        f"ordered demand {expected}; the asymmetry may have been fixed."
    )

    # ------------------------------------------------------------------
    # Case 2: full rule contract (exactly as cited in the V006 evidence).
    #   SingleParentPolarsExpressionRule().parent_demands(...).by_parent
    #   must contain 'segment' for p1; it does not.
    # ------------------------------------------------------------------
    node = GraphNode(
        id="n1",
        data=NodeData(nodeType=NodeType.POLARS, config={"code": code}),
    )
    result = SingleParentPolarsExpressionRule().parent_demands(
        node=node,
        parent_ids=["p1"],
        my_needed={"premium"},
    )
    assert result is not None, "rule unexpectedly declined to produce a demand"
    by_parent_p1 = result.by_parent["p1"]
    print(f"[case2] rule by_parent['p1'] = {sorted(by_parent_p1)}")
    assert by_parent_p1 == {"premium"}, (
        f"expected rule to (wrongly) demand only {{'premium'}} from p1, got "
        f"{by_parent_p1}"
    )
    assert "segment" not in by_parent_p1, (
        "rule.by_parent unexpectedly already includes 'segment'"
    )

    # ------------------------------------------------------------------
    # Case 3: mixed shape from the evidence.
    #   with_columns(derive y) then filter(seg='B'); needed={'y'}.
    #   'seg' is required but the unordered router drops it.
    # ------------------------------------------------------------------
    mixed = (
        "df = df.with_columns((pl.col('x') + 1).alias('y'))\n"
        "df = df.filter(seg='B')"
    )
    mixed_routed = _demands_for(mixed, {"y"})
    print(f"[case3] mixed routed demand = {sorted(mixed_routed) if mixed_routed else mixed_routed}")
    assert mixed_routed == {"x"}, (
        f"expected mixed routed demand to (wrongly) be {{'x'}} (dropping "
        f"'seg'), got {mixed_routed}"
    )
    assert "seg" not in mixed_routed, "mixed routed unexpectedly kept 'seg'"

    # ------------------------------------------------------------------
    # Case 4: independent ground truth from Polars itself -- the keyword
    #   filter genuinely REQUIRES the column. A frame lacking 'segment'
    #   raises when the filter executes, proving narrowing the parent scan
    #   to exclude 'segment' is incorrect (it would lose a required input /
    #   fail at execution).
    # ------------------------------------------------------------------
    import polars as pl

    frame_with = pl.DataFrame({"premium": [1.0, 2.0], "segment": ["A", "B"]})
    kept = frame_with.filter(segment="A")
    assert kept.height == 1, f"polars sanity: expected 1 row kept, got {kept.height}"

    raised = False
    try:
        # Frame projected to the (buggy) demand {'premium'} only -- no segment.
        pl.DataFrame({"premium": [1.0, 2.0]}).filter(segment="A")
    except pl.exceptions.ColumnNotFoundError as exc:
        raised = True
        print(f"[case4] polars confirms column required: {type(exc).__name__}")
    assert raised, (
        "polars did NOT raise on filter(segment='A') over a frame lacking "
        "'segment'; the column may not actually be required -> claim weakened"
    )

    print("\nV006 REPRODUCED: unordered demand walk drops filter keyword "
          "columns; parent is under-demanded for df.filter(segment='A').")


if __name__ == "__main__":
    main()
