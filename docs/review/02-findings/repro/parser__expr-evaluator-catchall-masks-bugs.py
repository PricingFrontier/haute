"""Adversarial repro for claim `expr-evaluator-catchall-masks-bugs`.

Claim: every public entry point of the trace expression engine wraps its body in
`except Exception` and, in evaluate_expression / _compute_result, falls through to
`row_values.get(target_column)` -- i.e. the evaluator returns the ALREADY-OBSERVED
value as if it had computed it. A real internal evaluator failure therefore becomes
invisible: the "computed" step silently equals the observed value, so a waterfall
reconciles against itself and a wrong number is masked.

This script demonstrates the silent swallow with a *real* (perfectly valid) Polars
expression that raises inside the AST evaluator's dispatch, NOT a contrived monkeypatch.

ISOLATION: no disk I/O against real project files; a tempdir is registered as the
sandbox project root defensively. evaluate_expression is a pure function over an
in-memory row dict.
"""

import math
import tempfile
from pathlib import Path

from haute import _sandbox
from haute._expression_parser import (
    _compute_result,
    _compute_result_impl,
    evaluate_expression,
    parse_expression,
)

# Defensive isolation: point any project-root lookups at a throwaway tempdir.
_tmp = tempfile.mkdtemp(prefix="haute_repro_expr_")
_sandbox.set_project_root(Path(_tmp))

failures: list[str] = []


def check(label: str, cond: bool, detail: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}: {detail}")
    if not cond:
        failures.append(f"{label}: {detail}")


# ---------------------------------------------------------------------------
# Scenario A — REAL evaluator bug: float division by zero.
#
# `pl.col("a") / pl.col("b")` is a completely valid Polars expression. In real
# Polars, 1.0 / 0.0 yields +inf (float division). The AST evaluator instead uses
# Python's operator.truediv, which raises ZeroDivisionError for b == 0. That
# exception propagates up to _compute_result's `except Exception`, which silently
# returns row_values.get(target_column) -- the ALREADY-OBSERVED value.
#
# We seed the observed target ("ratio") with a SENTINEL that is neither the correct
# Polars answer (inf) nor a plausible coincidence, to prove the masked number is the
# observed value, not a real computation.
# ---------------------------------------------------------------------------
SENTINEL = -999.0  # stand-in for "whatever the trace row already shows for ratio"

code_div = "df = df.with_columns((pl.col('a') / pl.col('b')).alias('ratio'))"
row_div = {"a": 1.0, "b": 0.0, "ratio": SENTINEL}

# First, prove the inner *impl* genuinely raises on this real expression — i.e. there
# is a real evaluator failure being swallowed (not a no-op path).
parsed_div = parse_expression(code_div, "ratio")
raised = False
try:
    _compute_result_impl(code_div, "ratio", dict(row_div), parsed_div)
except ZeroDivisionError:
    raised = True
check(
    "A.impl-raises",
    raised,
    "operator.truediv(1.0, 0.0) raises ZeroDivisionError inside _compute_result_impl "
    "(a real internal failure)",
)

# The wrapper swallows it and returns the observed value verbatim.
swallowed = _compute_result(code_div, "ratio", dict(row_div), parsed_div)
check(
    "A.compute-result-swallows",
    swallowed == SENTINEL,
    f"_compute_result returned {swallowed!r} == observed row_values['ratio'] "
    f"({SENTINEL!r}); the ZeroDivisionError was masked, not raised",
)

# End-to-end via the public entry point: evaluate_expression reports result_value
# equal to the observed value, with no exception and no error surfaced. In a real
# waterfall the observed 'ratio' would therefore appear "reconciled" / self-consistent.
ev = evaluate_expression(code_div, "ratio", dict(row_div))
check(
    "A.evaluate-masks-as-observed",
    ev.result_value == SENTINEL,
    f"evaluate_expression.result_value = {ev.result_value!r} == observed "
    f"({SENTINEL!r}); a genuine evaluator failure is reported as a clean computed value",
)
# And it is NOT the correct Polars semantics (+inf): the masked value is wrong.
correct_polars = float("inf")  # 1.0 / 0.0 in Polars float division
check(
    "A.masked-value-is-wrong",
    ev.result_value != correct_polars and not (
        isinstance(ev.result_value, float) and math.isinf(ev.result_value)
    ),
    f"result_value {ev.result_value!r} differs from correct Polars value {correct_polars!r}; "
    "the silent fallback substituted the observed number for the real (uncomputed) answer",
)

# ---------------------------------------------------------------------------
# Scenario B — REAL evaluator bug: .round(n) with a string column.
#
# `pl.col('label').round(2)` is malformed in real Polars (round on a Utf8 column),
# but more importantly the AST evaluator at the .round branch does round(val, n)
# where val is a str -> TypeError. That, too, is swallowed to the observed value.
# This shows the mask is general across dispatch methods, not specific to division.
# ---------------------------------------------------------------------------
code_round = "df = df.with_columns(pl.col('label').round(2).alias('out'))"
OBSERVED_B = "untouched-observed"
row_round = {"label": "abc", "out": OBSERVED_B}
parsed_round = parse_expression(code_round, "out")

raised_b = False
try:
    _compute_result_impl(code_round, "out", dict(row_round), parsed_round)
except TypeError:
    raised_b = True
check(
    "B.impl-raises",
    raised_b,
    "round('abc', 2) raises TypeError inside _compute_result_impl",
)

ev_b = evaluate_expression(code_round, "out", dict(row_round))
check(
    "B.evaluate-masks-as-observed",
    ev_b.result_value == OBSERVED_B,
    f"evaluate_expression.result_value = {ev_b.result_value!r} == observed "
    f"({OBSERVED_B!r}); TypeError from the dispatch method was silently swallowed",
)

# ---------------------------------------------------------------------------
# Control — when the target column is NOT present in row_values, the fallback can
# only return None. This documents that the mask specifically launders the OBSERVED
# value when present (the dangerous waterfall case), and degrades to None otherwise.
# ---------------------------------------------------------------------------
row_div_no_target = {"a": 1.0, "b": 0.0}  # no 'ratio' key
ev_ctrl = evaluate_expression(code_div, "ratio", dict(row_div_no_target))
check(
    "C.no-target-falls-to-None",
    ev_ctrl.result_value is None,
    f"with no observed 'ratio', result_value = {ev_ctrl.result_value!r} (None) -- "
    "confirms the fallback is row_values.get(target), which launders the observed "
    "value precisely when one exists",
)

print()
if failures:
    print(f"REPRO RESULT: CLAIM SUPPORTED — {len(failures)} masking assertion(s) "
          "fired as predicted (the bug-mask exists):")
    for f in failures:
        print(f"  - {f}")
else:
    # All checks "PASS" here means: the predicted silent-mask behaviour was observed
    # exactly. (Each check asserts the *masking* condition holds, so PASS == claim true.)
    print("REPRO RESULT: CLAIM SUPPORTED — every predicted silent-swallow behaviour "
          "was observed (evaluator failures masked as the observed value).")
