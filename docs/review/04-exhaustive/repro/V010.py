"""Reproduction for V010.

Claim: ``_BranchTrackingEvaluator._eval_clauses`` (src/haute/_expression_parser.py)
sets ``self._is_outer = False`` on the THEN path (line ~2094) BEFORE recursing,
but the OTHERWISE path (lines ~2107-2113) sets taken_branch / taken_branch_index /
dimmed_branches WITHOUT clearing ``self._is_outer``. Consequently, when the outer
``.otherwise()`` value itself contains a nested when-chain, the nested chain
re-enters ``_eval_clauses`` with ``is_outer`` STILL True and OVERWRITES the
outer's just-set branch metadata with its own. The trace therefore reports the
outer THEN clause as taken when in fact the outer OTHERWISE fired.

This metadata flows verbatim into ``EvaluatedExpression.taken_branch /
taken_branch_index / dimmed_branches`` (evaluate_expression -> branch_info, lines
~1393-1398) and is then serialized into ``step.calculation`` in
``_trace_enrichment.py`` (dataclasses.asdict + explicit taken_branch copy, lines
~1523-1528), driving which clause the explainability UI highlights/dims.

Strategy (ISOLATED, no disk I/O, no project files): drive the *real* public
``evaluate_expression`` with small code strings + a concrete row dict. We assert
on the specific WRONG values:

  * the computed ``result_value`` is CORRECT (outer-otherwise -> inner-then -> 1),
    proving the expression genuinely evaluates the OTHERWISE branch; while
  * the reported ``taken_branch`` metadata wrongly names the outer THEN branch
    (taken_branch == "then", index 0, dimmed == [1]) instead of the truthful
    outer OTHERWISE (index 1, dimmed [0]).

A control case (nested chain inside the outer THEN, where _is_outer IS cleared)
is shown to be reported CORRECTLY, isolating the missing ``_is_outer = False`` on
the otherwise path as the root cause.

We assert on demonstrably wrong VALUES, not merely that "something raised".
"""

from __future__ import annotations

import polars as pl  # noqa: F401  (referenced by the expression code strings)

from haute._expression_parser import evaluate_expression

failures: list[str] = []


def show(label: str, ev) -> None:
    print(f"  [{label}]")
    print(f"      expression_type   = {ev.expression_type!r}")
    print(f"      result_value      = {ev.result_value!r}")
    print(f"      taken_branch      = {ev.taken_branch!r}")
    print(f"      taken_branch_index= {ev.taken_branch_index!r}")
    print(f"      dimmed_branches   = {ev.dimmed_branches!r}")
    print(f"      nested_branches   = {ev.nested_branches!r}")


# ---------------------------------------------------------------------------
# (A) THE BUG: outer .otherwise() whose value is a nested when-chain.
#     out = pl.when(a > 0).then(99).otherwise( pl.when(b > 0).then(1).otherwise(2) )
#     Row a = -1, b = 1.
#     Truth: outer cond (a>0) is FALSE -> outer OTHERWISE fires (index 1);
#            inner cond (b>0) is TRUE  -> inner THEN -> value 1.
#     So result MUST be 1 and the OUTER taken_branch MUST be "otherwise"/index 1.
# ---------------------------------------------------------------------------
code_a = (
    "df = df.with_columns("
    "pl.when(pl.col('a') > 0).then(99)"
    ".otherwise(pl.when(pl.col('b') > 0).then(1).otherwise(2))"
    ".alias('out'))"
)
ev_a = evaluate_expression(code_a, "out", {"a": -1, "b": 1})
print("Case A (outer-otherwise wraps a nested when):")
show("A", ev_a)

# Sanity: the engine genuinely took the OTHERWISE branch (value computed by the
# nested then -> 1). If this is not 1 the test setup is wrong, not the bug.
if ev_a.result_value != 1:
    failures.append(
        f"A: setup invalid -- result_value={ev_a.result_value!r}, expected 1 "
        f"(outer-otherwise -> inner-then). Not the predicted bug."
    )
else:
    # The expression provably evaluated the OUTER OTHERWISE (because the value 1
    # can ONLY come from the inner chain reached via the otherwise branch; the
    # outer THEN would have produced 99). Yet the reported metadata claims THEN.
    bug_present = (
        ev_a.taken_branch == "then"
        and ev_a.taken_branch_index == 0
        and ev_a.dimmed_branches == [1]
    )
    truthful = (
        ev_a.taken_branch == "otherwise"
        and ev_a.taken_branch_index == 1
        and ev_a.dimmed_branches == [0]
    )
    if bug_present:
        print(
            "      -> BUG CONFIRMED: result_value=1 proves the OUTER OTHERWISE "
            "fired, but taken_branch reports the OUTER THEN (value 99, index 0)."
        )
    elif truthful:
        failures.append(
            "A: metadata is CORRECT (taken_branch='otherwise', index 1, "
            "dimmed [0]) -- bug NOT reproduced; the otherwise path was tracked "
            "properly."
        )
    else:
        failures.append(
            f"A: unexpected metadata taken_branch={ev_a.taken_branch!r}, "
            f"index={ev_a.taken_branch_index!r}, dimmed={ev_a.dimmed_branches!r}; "
            f"neither the predicted buggy nor the truthful value."
        )

# ---------------------------------------------------------------------------
# (B) CONTROL: nested when-chain inside the outer THEN (the _is_outer = False
#     path). Same shape but the nesting is under .then():
#     out = pl.when(a > 0).then( pl.when(b > 0).then(1).otherwise(2) ).otherwise(99)
#     Row a = 1, b = 1 -> outer THEN fires (index 0); inner THEN -> value 1.
#     Here the THEN path DOES clear _is_outer before recursing, so the outer
#     metadata must survive: taken_branch == "then", index 0, dimmed [1], and the
#     inner branch is captured under nested_branches. This shows the asymmetry:
#     the otherwise path is the only one that fails.
# ---------------------------------------------------------------------------
code_b = (
    "df = df.with_columns("
    "pl.when(pl.col('a') > 0)"
    ".then(pl.when(pl.col('b') > 0).then(1).otherwise(2))"
    ".otherwise(99)"
    ".alias('out'))"
)
ev_b = evaluate_expression(code_b, "out", {"a": 1, "b": 1})
print("\nCase B (CONTROL: nested when inside outer THEN):")
show("B", ev_b)
if ev_b.result_value != 1:
    failures.append(
        f"B: setup invalid -- result_value={ev_b.result_value!r}, expected 1."
    )
elif not (ev_b.taken_branch == "then" and ev_b.taken_branch_index == 0):
    failures.append(
        f"B: control regressed -- expected outer THEN (index 0) to be reported "
        f"correctly, got taken_branch={ev_b.taken_branch!r}, "
        f"index={ev_b.taken_branch_index!r}. (The THEN path is supposed to clear "
        f"_is_outer and preserve outer metadata.)"
    )
else:
    print(
        "      -> CONTROL OK: the THEN path correctly preserves the outer "
        "metadata (index 0), confirming the otherwise path is the defective one."
    )

# ---------------------------------------------------------------------------
print()
if failures:
    print("REPRO RESULT: NOT reproduced as predicted -- discrepancies:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
else:
    print("REPRO RESULT: REPRODUCED.")
    print(
        "  The outer .otherwise() that wraps a nested when reports taken_branch="
        "'then'/index 0/dimmed [1] (the OUTER THEN), even though result_value=1 "
        "proves the OUTER OTHERWISE branch fired. The nested chain clobbered the "
        "outer metadata because the otherwise path never sets _is_outer = False."
    )
