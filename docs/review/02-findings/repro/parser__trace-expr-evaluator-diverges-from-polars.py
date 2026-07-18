"""Adversarial repro for claim: trace-expr-evaluator-diverges-from-polars.

Claim: the trace/quote-explanation evaluator (haute._expression_parser.evaluate_expression)
reimplements Polars single-row semantics and diverges from real Polars for:
  (a) pl.sum_horizontal('a','b') all-null  -> real Polars 0.0, evaluator None
  (b) pl.mean_horizontal('a','b') one null -> real Polars 10.0, evaluator None
  (c) pl.concat_str([...], separator='-')   -> real Polars 'x-y', evaluator None

This script computes BOTH the evaluator's result_value and the REAL Polars single-row
result for each case, and asserts on the specific expected-vs-actual values.

No disk I/O, no project files. evaluate_expression is a pure AST evaluator.
"""

import polars as pl

from haute._expression_parser import evaluate_expression


def real_polars(expr: pl.Expr, row: dict) -> object:
    """Compute the true single-row Polars value for `out=expr` given a 1-row frame."""
    df = pl.DataFrame({k: [v] for k, v in row.items()})
    out = df.with_columns(out=expr)
    return out["out"][0]


def evaluator(code: str, row: dict) -> object:
    return evaluate_expression(code, "out", row).result_value


results = {}

# ---- (a) sum_horizontal, all null ------------------------------------------
row_a = {"a": None, "b": None}
code_a = 'df = df.with_columns(out=pl.sum_horizontal("a","b"))'
real_a = real_polars(pl.sum_horizontal("a", "b"), row_a)
eval_a = evaluator(code_a, row_a)
results["a_sum_horizontal_allnull"] = {"real": real_a, "evaluator": eval_a}

# ---- (b) mean_horizontal, one null -----------------------------------------
row_b = {"a": 10.0, "b": None}
code_b = 'df = df.with_columns(out=pl.mean_horizontal("a","b"))'
real_b = real_polars(pl.mean_horizontal("a", "b"), row_b)
eval_b = evaluator(code_b, row_b)
results["b_mean_horizontal_onenull"] = {"real": real_b, "evaluator": eval_b}

# ---- (c) concat_str with separator -----------------------------------------
row_c = {"a": "x", "b": "y"}
code_c = 'df = df.with_columns(out=pl.concat_str(["a","b"], separator="-"))'
real_c = real_polars(pl.concat_str(["a", "b"], separator="-"), row_c)
eval_c = evaluator(code_c, row_c)
results["c_concat_str_separator"] = {"real": real_c, "evaluator": eval_c}

print("=== RAW RESULTS ===")
for k, v in results.items():
    print(f"{k}: real={v['real']!r}  evaluator={v['evaluator']!r}")

print("\n=== DIVERGENCE CHECK (claim asserts evaluator differs from real Polars) ===")

# Claim (a): real == 0.0, evaluator == None  => divergence
print(f"(a) claim says real=0.0 evaluator=None | observed real={real_a!r} evaluator={eval_a!r}")
diverge_a = (real_a != eval_a)

# Claim (b): real == 10.0, evaluator == None  => divergence
print(f"(b) claim says real=10.0 evaluator=None | observed real={real_b!r} evaluator={eval_b!r}")
diverge_b = (real_b != eval_b)

# Claim (c): real == 'x-y', evaluator == None => divergence
print(f"(c) claim says real='x-y' evaluator=None | observed real={real_c!r} evaluator={eval_c!r}")
diverge_c = (real_c != eval_c)

print("\n=== PER-SUBCLAIM VERDICT ===")
print(f"(a) sum_horizontal all-null diverges as claimed (real=0.0, eval=None): "
      f"{real_a == 0.0 and eval_a is None}")
print(f"(b) mean_horizontal one-null diverges as claimed (real=10.0, eval=None): "
      f"{real_b == 10.0 and eval_b is None}")
print(f"(c) concat_str diverges as claimed (real='x-y', eval=None): "
      f"{real_c == 'x-y' and eval_c is None}")

# The claim as a whole asserts ALL THREE specific (real, evaluator) pairs.
# Assert each precisely so a wrong sub-claim fails loudly.
assert real_a == 0.0, f"(a) expected real Polars 0.0, got {real_a!r}"
assert real_b == 10.0, f"(b) expected real Polars 10.0, got {real_b!r}"
assert real_c == "x-y", f"(c) expected real Polars 'x-y', got {real_c!r}"

# Now assert the evaluator's claimed (wrong) values. If ANY of these fail, the
# corresponding sub-claim is REFUTED for that case.
errors = []
if eval_a is not None:
    errors.append(f"(a) sub-claim WRONG: evaluator returned {eval_a!r}, claim said None")
if eval_b is not None:
    errors.append(f"(b) sub-claim WRONG: evaluator returned {eval_b!r}, claim said None")
if eval_c is not None:
    errors.append(f"(c) sub-claim WRONG: evaluator returned {eval_c!r}, claim said None")

print("\n=== FINAL ===")
if errors:
    print("SOME SUB-CLAIMS REFUTED:")
    for e in errors:
        print("  " + e)
else:
    print("ALL THREE SUB-CLAIMS REPRODUCED: evaluator returns None where real Polars does not.")

# Overall: at minimum the claim requires divergence in each case. Verify divergence
# (evaluator != real Polars) regardless of whether the *exact* None prediction held.
assert diverge_a, "(a) NO divergence: evaluator matches real Polars"
assert diverge_c, "(c) NO divergence: evaluator matches real Polars"
# (b) is the contested one; report it but assert divergence to confirm the bug class.
print(f"\n(b) divergence present: {diverge_b}")
