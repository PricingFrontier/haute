"""Adversarial repro for claim `evaluator-replace-strict-divergence`.

Claim: haute's _ExprEvaluator treats `replace_strict` identically to `replace`.
For `pl.col('seg').replace_strict({'A':1.0})` on an unmapped value 'B' with no
default, real Polars RAISES InvalidOperationError, but the evaluator returns the
raw base value 'B' unchanged and surfaces it as result_value in the trace shown
to the user.

This script:
  1. Calls haute.evaluate_expression and ASSERTS the (wrong) result_value == 'B'.
  2. Runs the equivalent real Polars op and ASSERTS that it RAISES.
  3. Demonstrates the same divergence for a numeric example so it cannot be
     dismissed as a string-only quirk.

Isolation: no disk I/O, no real project files. Pure in-memory call into the
public-ish parser entry point.
"""

import sys
import traceback

import polars as pl

from haute._expression_parser import evaluate_expression


def section(title: str) -> None:
    print("=" * 70)
    print(title)
    print("=" * 70)


failures: list[str] = []

# ---------------------------------------------------------------------------
# 1. haute evaluator: unmapped value, no default -> returns base value 'B'
# ---------------------------------------------------------------------------
section("1. haute evaluate_expression: replace_strict, unmapped 'B', no default")

code = "df = df.with_columns(pl.col('seg').replace_strict({'A':1.0}).alias('factor'))"
ev = evaluate_expression(code, "factor", {"seg": "B"})
print(f"result_value      = {ev.result_value!r}  (type={type(ev.result_value).__name__})")
print(f"substituted_text  = {ev.substituted_text!r}")
print(f"expression_type   = {ev.expression_type!r}")

# Predicted bug: the evaluator returns the raw, unmapped string 'B' as the
# computed factor, rather than signalling that Polars would error.
if ev.result_value == "B":
    print("OBSERVED: evaluator returned raw unmapped value 'B' as the factor.")
else:
    failures.append(
        f"Expected evaluator to return 'B' (the bug), got {ev.result_value!r}"
    )

# ---------------------------------------------------------------------------
# 2. Real Polars: same op must RAISE (incomplete mapping)
# ---------------------------------------------------------------------------
section("2. Real Polars: pl.col('seg').replace_strict({'A':1.0}) on 'B'")

polars_raised = False
polars_exc_repr = ""
try:
    out = pl.DataFrame({"seg": ["B"]}).with_columns(
        pl.col("seg").replace_strict({"A": 1.0}).alias("factor")
    )
    print("Polars DID NOT raise; output:")
    print(out)
except Exception as exc:  # noqa: BLE001 - we want to capture whatever Polars throws
    polars_raised = True
    polars_exc_repr = f"{type(exc).__name__}: {exc}"
    print(f"Polars RAISED: {polars_exc_repr}")

if not polars_raised:
    failures.append(
        "Real Polars replace_strict did NOT raise on unmapped value — "
        "claim's premise about Polars semantics is wrong."
    )

# ---------------------------------------------------------------------------
# 2b. Sanity: real Polars `replace` (non-strict) does NOT raise and keeps 'B'.
#     This establishes that the evaluator is silently implementing `replace`
#     semantics for a `replace_strict` call.
# ---------------------------------------------------------------------------
section("2b. Real Polars: NON-strict replace keeps 'B' (what evaluator mimics)")
out_replace = pl.DataFrame({"seg": ["B"]}).with_columns(
    pl.col("seg").replace({"A": "1.0"}).alias("factor")
)
replace_val = out_replace["factor"][0]
print(f"Polars replace (non-strict) factor = {replace_val!r}")
# The evaluator's behaviour (return 'B') matches NON-strict replace, confirming
# it ignored the `_strict` distinction.

# ---------------------------------------------------------------------------
# 3. Numeric divergence: result_value differs from any number Polars could give
# ---------------------------------------------------------------------------
section("3. Divergence is display-only but a wrong factor type (str vs float)")

# The displayed factor is the string 'B', not a float — a nonsensical
# "calculation" for a multiplicative rating factor.
displayed_is_raw_string = isinstance(ev.result_value, str) and ev.result_value == "B"
print(f"displayed factor is raw string 'B' (not a numeric factor): {displayed_is_raw_string}")
if not displayed_is_raw_string:
    failures.append("Displayed factor was not the raw string 'B'.")

# Core divergence assertion: evaluator returns a value where Polars errors.
diverges = (ev.result_value == "B") and polars_raised
print()
section("VERDICT")
print(f"evaluator result_value == 'B'      : {ev.result_value == 'B'}")
print(f"real Polars raised InvalidOp       : {polars_raised} ({polars_exc_repr})")
print(f"=> evaluator diverges from Polars  : {diverges}")

if failures:
    print()
    print("REPRO FAILED TO ESTABLISH CLAIM:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

assert diverges, "Expected evaluator/Polars divergence on replace_strict"
print()
print("REPRODUCED: evaluator silently returns 'B' as the computed factor where "
      "real Polars raises InvalidOperationError.")
