"""Adversarial repro for candidate V007.

CLAIM: `_referenced_polars_columns` (src/haute/projection.py:789-802) only
recognises ``pl.col('name')`` as a column reference and bails (returns None,
forcing the safe full-width boundary) ONLY for a dynamic/unknown ``.col(...)``.
Polars top-level aggregation SHORTCUTS that name a column by string argument --
``pl.sum('a')``, ``pl.mean('a')``, ``pl.min/max/first/last/std/var/...('a')`` --
are NOT ``pl.col`` calls, so the function silently returns an EMPTY set for them
instead of bailing. The empty set propagates through ``_select_output_to_input``
(:829) and ``_unordered_expression_demands`` (:1204) into
``SingleParentPolarsExpressionRule.parent_demands`` (:1277), producing a parent
demand that OMITS the referenced column. Because these shortcuts are
row-preserving inside ``with_columns`` (the column aggregate broadcasts across
all rows), the code is a valid single-parent shape that legitimately reaches the
demand walk; the narrowed parent scan then fails at execution with
ColumnNotFoundError on valid user code.

This script ASSERTS on the specific WRONG VALUES (expected vs actual) at three
layers, and pins down the contrast (the ``pl.col('a').sum()`` form IS captured),
so the gap is unambiguously attributable to the string-argument shortcut.

It also confirms the Polars ground truth: the shortcut requires column ``a`` to
exist (ColumnNotFoundError when absent) and is row-preserving when present.

ISOLATION: pure in-memory synthetic AST + tiny in-memory Polars frames. No disk
I/O, no project files, no rating/ src/ tests/ access.

Run:
  uv run python review/04-exhaustive/repro/V007.py
"""

from __future__ import annotations

import ast

import polars as pl

from haute.graph_utils import GraphNode, NodeData
from haute.projection import (
    SingleParentPolarsExpressionRule,
    _referenced_polars_columns,
    _select_output_to_input,
    _single_parent_polars_expression_demands,
    projection_contract,
)


def _expr(src: str) -> ast.AST:
    return ast.parse(src, mode="eval").body


def _select_call(src: str) -> ast.Call:
    call = ast.parse(src).body[0].value
    assert isinstance(call, ast.Call)
    return call


def main() -> None:
    failures: list[str] = []

    # ------------------------------------------------------------------ #
    # Layer 1: _referenced_polars_columns.
    # The dynamic pl.col(x) BAILS (None). The string shortcut UNDER-DEMANDS
    # (empty set) instead of bailing. The pl.col('a') form is captured.
    # ------------------------------------------------------------------ #
    refs_shortcut = _referenced_polars_columns(_expr("pl.sum('a').alias('s')"))
    refs_plcol = _referenced_polars_columns(_expr("pl.col('a').sum().alias('s')"))
    refs_dynamic = _referenced_polars_columns(_expr("pl.col(x)"))

    print("=== Layer 1: _referenced_polars_columns ===")
    print(f"  pl.sum('a').alias('s')       -> {refs_shortcut!r}  (BUG: should be {{'a'}} or None)")
    print(f"  pl.col('a').sum().alias('s') -> {refs_plcol!r}")
    print(f"  pl.col(x)  [dynamic]         -> {refs_dynamic!r}")

    # The bug: shortcut returns a set that does NOT contain 'a'.
    if refs_shortcut is None or "a" in refs_shortcut:
        failures.append(
            f"[L1] EXPECTED string-shortcut to silently drop 'a' (empty set), "
            f"but got {refs_shortcut!r}. Bug not present at this layer."
        )
    # Contrast: pl.col form correctly captures 'a'.
    if refs_plcol != {"a"}:
        failures.append(
            f"[L1] EXPECTED pl.col('a').sum() to capture {{'a'}} but got "
            f"{refs_plcol!r}; gap not isolated to the shortcut."
        )
    # Contrast: dynamic pl.col bails (proves the safe path exists and is NOT
    # taken for the shortcut).
    if refs_dynamic is not None:
        failures.append(
            f"[L1] EXPECTED dynamic pl.col(x) to bail (None) but got "
            f"{refs_dynamic!r}; the safe full-width path is mischaracterised."
        )

    # ------------------------------------------------------------------ #
    # Layer 2: _select_output_to_input.
    # df.select(pl.sum('a').alias('s')) should map output 's' -> input {'a'};
    # the bug yields {'s': set()}.
    # ------------------------------------------------------------------ #
    sel_shortcut = _select_output_to_input(_select_call("df.select(pl.sum('a').alias('s'))"))
    sel_plcol = _select_output_to_input(_select_call("df.select(pl.col('a').sum().alias('s'))"))

    print("=== Layer 2: _select_output_to_input ===")
    print(f"  select(pl.sum('a').alias('s'))       -> {sel_shortcut!r}  (BUG: should be {{'s': {{'a'}}}})")
    print(f"  select(pl.col('a').sum().alias('s')) -> {sel_plcol!r}")

    if sel_shortcut != {"s": set()}:
        failures.append(
            f"[L2] EXPECTED under-demand {{'s': set()}} but got {sel_shortcut!r}."
        )
    if sel_plcol != {"s": {"a"}}:
        failures.append(
            f"[L2] EXPECTED pl.col form {{'s': {{'a'}}}} but got {sel_plcol!r}."
        )

    # ------------------------------------------------------------------ #
    # Layer 3: full demand inference for a row-preserving with_columns.
    # df = df.with_columns(pl.sum('a').alias('s')), downstream needs {'s'}.
    # Correct parent demand is {'a'}; the bug yields the empty set.
    # ------------------------------------------------------------------ #
    code_shortcut = "df = df.with_columns(pl.sum('a').alias('s'))"
    code_plcol = "df = df.with_columns(pl.col('a').sum().alias('s'))"
    demand_shortcut = _single_parent_polars_expression_demands(code_shortcut, {"s"})
    demand_plcol = _single_parent_polars_expression_demands(code_plcol, {"s"})

    print("=== Layer 3: _single_parent_polars_expression_demands (my_needed={'s'}) ===")
    print(f"  {code_shortcut!r} -> {demand_shortcut!r}  (BUG: should be {{'a'}})")
    print(f"  {code_plcol!r} -> {demand_plcol!r}")

    if demand_shortcut != set():
        failures.append(
            f"[L3] EXPECTED parent demand to OMIT 'a' (empty set) but got "
            f"{demand_shortcut!r}."
        )
    if demand_plcol != {"a"}:
        failures.append(
            f"[L3] EXPECTED pl.col form to demand {{'a'}} but got {demand_plcol!r}."
        )

    # ------------------------------------------------------------------ #
    # Layer 4 (rule level, matching the finding's stated evidence):
    # SingleParentPolarsExpressionRule().parent_demands(...) returns
    # by_parent={'p1': set()} -- 'a' MISSING. Confirm the contract is opaque
    # (None, None) so the rule does NOT bail early, and the aliased with_columns
    # guard at :1145 does NOT bail (expression is aliased).
    # ------------------------------------------------------------------ #
    node = GraphNode(
        id="n1",
        data=NodeData(label="n1", nodeType="polars", config={"code": code_shortcut}),
    )
    contract_tuple = projection_contract(node).to_tuple()
    result = SingleParentPolarsExpressionRule().parent_demands(
        node=node, parent_ids=["p1"], my_needed={"s"}
    )
    by_parent = None if result is None else result.by_parent

    print("=== Layer 4: SingleParentPolarsExpressionRule.parent_demands ===")
    print(f"  projection_contract.to_tuple() = {contract_tuple!r}")
    print(f"  by_parent = {by_parent!r}  (BUG: should be {{'p1': {{'a'}}}})")

    if contract_tuple != (None, None):
        failures.append(
            f"[L4] EXPECTED opaque contract (None, None) so the rule engages, "
            f"but got {contract_tuple!r}."
        )
    if by_parent != {"p1": set()}:
        failures.append(
            f"[L4] EXPECTED rule to under-demand by_parent={{'p1': set()}} but "
            f"got {by_parent!r}."
        )

    # ------------------------------------------------------------------ #
    # Layer 5: Polars ground truth. The shortcut genuinely REQUIRES column 'a'
    # (so the omission above is a real contract violation, not a harmless
    # over-narrowing), AND it is row-preserving when 'a' is present (so the
    # code is a valid single-parent shape that legitimately reaches the walk).
    # ------------------------------------------------------------------ #
    df_without_a = pl.DataFrame({"b": [1, 2, 3]})
    df_with_a = pl.DataFrame({"a": [10, 20, 30]})

    wc_raises = sel_raises = False
    try:
        df_without_a.with_columns(pl.sum("a").alias("s"))
    except pl.exceptions.ColumnNotFoundError:
        wc_raises = True
    try:
        df_without_a.select(pl.sum("a").alias("s"))
    except pl.exceptions.ColumnNotFoundError:
        sel_raises = True

    out = df_with_a.with_columns(pl.sum("a").alias("s"))
    row_preserving = out.height == df_with_a.height and out["s"].to_list() == [60, 60, 60]

    print("=== Layer 5: Polars ground truth ===")
    print(f"  with_columns(pl.sum('a')) on frame lacking 'a' raises ColumnNotFound? {wc_raises}")
    print(f"  select(pl.sum('a')) on frame lacking 'a' raises ColumnNotFound?       {sel_raises}")
    print(f"  with_columns(pl.sum('a')) row-preserving when 'a' present?            {row_preserving} (s={out['s'].to_list()})")

    if not wc_raises:
        failures.append(
            "[L5] EXPECTED with_columns(pl.sum('a')) to require 'a' "
            "(ColumnNotFoundError) but it did not raise; omission would be harmless."
        )
    if not sel_raises:
        failures.append(
            "[L5] EXPECTED select(pl.sum('a')) to require 'a' "
            "(ColumnNotFoundError) but it did not raise."
        )
    if not row_preserving:
        failures.append(
            "[L5] EXPECTED with_columns(pl.sum('a')) to broadcast row-preservingly "
            f"when 'a' present, but got height={out.height}, s={out['s'].to_list()}."
        )

    # ------------------------------------------------------------------ #
    print()
    if failures:
        print("REPRO RESULT: NOT REPRODUCED (claim not substantiated)")
        for line in failures:
            print("  " + line)
        raise SystemExit(1)

    print("REPRO RESULT: REPRODUCED")
    print(
        "  _referenced_polars_columns silently returns an EMPTY set for the "
        "string-argument aggregation shortcut pl.sum('a') (instead of bailing "
        "like the dynamic pl.col(x) path), so the projection rule's parent "
        "demand OMITS column 'a' (by_parent={'p1': set()}). Polars confirms the "
        "shortcut requires 'a' (ColumnNotFoundError when absent) and is "
        "row-preserving when present, so narrowing the parent scan to drop 'a' "
        "breaks valid single-parent user code at execution. The pl.col('a').sum() "
        "form is captured correctly ({'a'}), isolating the gap to the shortcut."
    )


if __name__ == "__main__":
    main()
