"""Adversarial verification of NEWBUG-02.

CLAIM (paraphrased): _ExprEvaluator._boolop implements Python and/or short-circuit
semantics (returns the last truthy operand VALUE), while a when-condition written
with bitwise & / | (BinOp BitAnd/BitOr -> operator.and_/operator.or_) is a *bitwise*
op on ints. For non-bool integer factor columns combined with & (a "common Polars
idiom for masks"), `2 & 1 == 0` (falsey) "whereas Polars boolean-context treats the
predicate differently", so the *branch-taken decision* (taken_branch / dimmed_branches
shown in the trace) can DIVERGE from the executor -- i.e. the waterfall highlights a
DIFFERENT when-clause than the one Polars actually evaluated.

This script tests that claim against the REAL evaluator and the REAL Polars executor.
It is fully isolated: synthetic in-memory data only; no disk writes; no rating/ src/
tests/ project files touched (it only IMPORTS from src, read-only).

Method: for each scenario, ask
  (E) what branch does Polars' executor actually take? (ground truth)
  (T) what branch does haute's _BranchTrackingEvaluator report (taken_branch_index)?
and check whether (T) != (E) is a *reachable* divergence (i.e. requires an expression
Polars will actually execute).
"""

import ast
import sys

import polars as pl

from haute._expression_parser import (
    _BranchTrackingEvaluator,
    _ExprEvaluator,
)

PASS = []
def check(name, cond):
    PASS.append(cond)
    print(("PASS " if cond else "FAIL ") + name)


def polars_branch(when_predicate_expr, df):
    """Ground truth: which branch (0=then, 1=otherwise) does Polars take?
    Returns ('then'|'otherwise', value) or ('ERROR', exc)."""
    try:
        out = df.select(
            pl.when(when_predicate_expr)
            .then(pl.lit("THEN"))
            .otherwise(pl.lit("OTHERWISE"))
            .alias("b")
        )["b"].to_list()[0]
        return ("then" if out == "THEN" else "otherwise", out)
    except Exception as e:  # noqa: BLE001  -- ground-truth probe
        return ("ERROR", e)


def trace_branch(cond_src, row_values):
    """What branch does the haute trace evaluator pick for the same when()?
    Build the AST for pl.when(<cond>).then('THEN').otherwise('OTHERWISE')."""
    code = f"pl.when({cond_src}).then('THEN').otherwise('OTHERWISE')"
    tree = ast.parse(code, mode="eval").body
    ev = _BranchTrackingEvaluator(row_values)
    result = ev.evaluate(tree)
    return ev.taken_branch_index, ev.taken_branch, result


print("=" * 70)
print("Polars version:", pl.__version__)
print("=" * 70)

# ---------------------------------------------------------------------------
# SCENARIO A: the EXACT claimed scenario -- non-bool INTEGER columns + `&`.
#   row a=2, b=1.  Evaluator: 2 & 1 == 0 (falsey) -> OTHERWISE.
#   Claim says Polars "treats the predicate differently" and would take THEN.
# ---------------------------------------------------------------------------
print("\n--- SCENARIO A: integer columns, `pl.col('a') & pl.col('b')` (a=2,b=1) ---")
df_int = pl.DataFrame({"a": [2], "b": [1]})

# (E) ground truth from the real executor
e_branch, e_val = polars_branch(pl.col("a") & pl.col("b"), df_int)
print(f"  [executor] branch = {e_branch!r}  value = {e_val!r}")

# (T) the trace evaluator
t_idx, t_branch, t_res = trace_branch("pl.col('a') & pl.col('b')", {"a": 2, "b": 1})
print(f"  [trace]    taken_branch_index = {t_idx}  taken_branch = {t_branch!r}  result = {t_res!r}")

# The load-bearing fact: Polars REFUSES this expression (i64 is not Boolean),
# so there is NO executor branch to diverge from. The claimed "divergence in
# branch selection" is unreachable because the executor errors out.
check("A1 executor RAISES on non-bool i64 when-predicate (no branch exists)",
      e_branch == "ERROR" and isinstance(e_val, Exception))
check("A2 the executor error is the dtype/SchemaError (expected Boolean, got i64)",
      e_branch == "ERROR" and ("Boolean" in str(e_val) or "boolean" in str(e_val).lower()))

# Bonus: the LEAF bitwise value itself AGREES between evaluator and Polars
#        (2 & 1 == 0 in both), so even the leaf isn't a divergence here.
leaf = df_int.select((pl.col("a") & pl.col("b")).alias("x"))["x"].to_list()[0]
ev_leaf = _ExprEvaluator({"a": 2, "b": 1}).evaluate(
    ast.parse("pl.col('a') & pl.col('b')", mode="eval").body
)
print(f"  [leaf] polars 2&1 = {leaf!r} ; evaluator 2&1 = {ev_leaf!r}")
check("A3 leaf bitwise value AGREES (polars and evaluator both give 0)",
      leaf == 0 and ev_leaf == 0)


# ---------------------------------------------------------------------------
# SCENARIO B: the BoolOp path the claim is *named* after -- `a and b`.
#   Claim worries _boolop returns "the last truthy operand VALUE". But Polars
#   cannot even BUILD `pl.col('a') and pl.col('b')` -- Expr.__bool__ raises.
#   So no Polars source ever contains this; the _boolop path is unreachable
#   from any executable Polars expression.
# ---------------------------------------------------------------------------
print("\n--- SCENARIO B: BoolOp `pl.col('a') and pl.col('b')` (the named mechanism) ---")
boolop_raises = False
try:
    _ = pl.col("a") and pl.col("b")
except Exception as e:  # noqa: BLE001
    boolop_raises = True
    print(f"  [executor] constructing `a and b` RAISES: {type(e).__name__}: {str(e)[:60]}")
check("B1 Polars `pl.col('a') and pl.col('b')` raises at construction (unreachable)",
      boolop_raises)

# And confirm the evaluator's _boolop really does the value-return thing the claim
# describes -- so we are not refuting on the mechanism, only on its reachability.
# `2 and 3` style: BoolOp(And) over two truthy -> returns last operand (3), not True.
bo = _ExprEvaluator({}).evaluate(ast.parse("2 and 3", mode="eval").body)
print(f"  [trace] _boolop('2 and 3') = {bo!r} (claim: returns last operand value 3, not bool)")
check("B2 evaluator _boolop returns last-operand VALUE (mechanism exists as claimed)",
      bo == 3)


# ---------------------------------------------------------------------------
# SCENARIO C: the ACTUAL legitimate Polars idiom -- BOOLEAN columns + `&`.
#   This is what real pipelines write: pl.col('flag_a') & pl.col('flag_b').
#   Does the evaluator's BITWISE operator.and_ agree with Polars' boolean & for
#   the branch decision?  Test the truth table incl. the tricky 0/1 ints case.
# ---------------------------------------------------------------------------
print("\n--- SCENARIO C: boolean columns, `pl.col('fa') & pl.col('fb')` (real idiom) ---")
all_ok = True
for fa in (True, False):
    for fb in (True, False):
        df_b = pl.DataFrame({"fa": [fa], "fb": [fb]})
        eb, ev_ = polars_branch(pl.col("fa") & pl.col("fb"), df_b)
        ti, tb, _ = trace_branch("pl.col('fa') & pl.col('fb')", {"fa": fa, "fb": fb})
        agree = (eb == tb)
        all_ok = all_ok and agree
        print(f"  fa={fa!s:5} fb={fb!s:5} executor={eb!r:11} trace={tb!r:11} agree={agree}")
check("C1 boolean-column `&` branch selection AGREES across full truth table",
      all_ok)

# Also test `|` on booleans the same way.
all_ok_or = True
for fa in (True, False):
    for fb in (True, False):
        df_b = pl.DataFrame({"fa": [fa], "fb": [fb]})
        eb, _ = polars_branch(pl.col("fa") | pl.col("fb"), df_b)
        ti, tb, _ = trace_branch("pl.col('fa') | pl.col('fb')", {"fa": fa, "fb": fb})
        all_ok_or = all_ok_or and (eb == tb)
check("C2 boolean-column `|` branch selection AGREES across full truth table",
      all_ok_or)


# ---------------------------------------------------------------------------
# VERDICT
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
real_divergence_found = False  # set True only if we ever saw trace != executor
                               # on an expression the executor ACTUALLY runs.
print("Summary of whether a REACHABLE branch-selection divergence was found:")
print(f"  A (int & int): executor cannot run it (SchemaError) -> no divergence reachable")
print(f"  B (a and b)  : executor cannot build it (TypeError) -> _boolop unreachable")
print(f"  C (bool & / |): executor runs it; trace AGREES on every branch -> no divergence")
print(f"  => reachable branch-selection divergence found: {real_divergence_found}")
print("=" * 70)

if all(PASS) and not real_divergence_found:
    print("\nRESULT: claim REFUTED -- no reachable branch-SELECTION divergence exists.")
    sys.exit(0)
else:
    print(f"\nRESULT: assertions={PASS} -- review needed.")
    sys.exit(1)
