"""Value-asserting regression tests pinning the trace expression-evaluator to
Polars semantics.

The single-row ``_ExprEvaluator`` computes the ``result_value`` shown in the
user-facing calculation trace; for non-window expressions it is written straight
to ``step.calculation`` and is NOT reconciled against the engine. A coverage
audit (2026-06-24) found four methods where the evaluator silently diverged
from Polars — so these tests cross-check the evaluator's value against the
actual Polars result on the same one-row input, not just ``is not None``:

- ``str.contains`` defaults to REGEX (``literal=True`` switches to substring);
- ``is_between`` honours ``closed=`` ("both"/"left"/"right"/"none");
- ``is_in([...])`` resolves a list-literal argument;
- ``round`` matches Polars by quantising the *decimal* value (banker's), where
  Python's ``round`` rounds the float representation (2.675 → 2.67 not 2.68).
"""

from __future__ import annotations

from typing import Any

import polars as pl

from haute._expression_parser import evaluate_expression


def _both(expr: str, row: dict[str, Any]) -> tuple[Any, Any]:
    """Return (evaluator_value, polars_value) for ``expr`` on a one-row frame."""
    code = f'df = df.with_columns(({expr}).alias("r"))'
    evaluated = evaluate_expression(code, "r", row).result_value
    df = pl.DataFrame({k: [v] for k, v in row.items()})
    polars_value = df.with_columns(eval(expr, {"pl": pl}).alias("r"))["r"][0]
    return evaluated, polars_value


class TestStrContainsRegex:
    def test_regex_metachar_matches_like_polars(self):
        # "a.c" is a regex: the "." matches any char, so "aXc" matches.
        ev, pv = _both('pl.col("s").str.contains("a.c")', {"s": "aXc"})
        assert ev == pv is True

    def test_literal_does_not_treat_dot_as_wildcard(self):
        ev, pv = _both('pl.col("s").str.contains("a.c", literal=True)', {"s": "aXc"})
        assert ev == pv is False

    def test_literal_true_exact_substring(self):
        ev, pv = _both('pl.col("s").str.contains("a.c", literal=True)', {"s": "a.c"})
        assert ev == pv is True

    def test_empty_pattern_matches(self):
        ev, pv = _both('pl.col("s").str.contains("")', {"s": "abc"})
        assert ev == pv is True

    def test_non_match(self):
        ev, pv = _both('pl.col("s").str.contains("z+")', {"s": "abc"})
        assert ev == pv is False


class TestIsBetweenClosed:
    def test_default_both_inclusive(self):
        for v in (1, 2, 3):
            ev, pv = _both('pl.col("v").is_between(1, 3)', {"v": v})
            assert ev == pv is True

    def test_left_excludes_upper(self):
        ev, pv = _both('pl.col("v").is_between(1, 3, closed="left")', {"v": 3})
        assert ev == pv is False
        ev, pv = _both('pl.col("v").is_between(1, 3, closed="left")', {"v": 1})
        assert ev == pv is True

    def test_right_excludes_lower(self):
        ev, pv = _both('pl.col("v").is_between(1, 3, closed="right")', {"v": 1})
        assert ev == pv is False
        ev, pv = _both('pl.col("v").is_between(1, 3, closed="right")', {"v": 3})
        assert ev == pv is True

    def test_none_excludes_both(self):
        ev, pv = _both('pl.col("v").is_between(1, 3, closed="none")', {"v": 1})
        assert ev == pv is False
        ev, pv = _both('pl.col("v").is_between(1, 3, closed="none")', {"v": 2})
        assert ev == pv is True


class TestIsInListLiteral:
    def test_member_true(self):
        ev, pv = _both('pl.col("v").is_in([1, 2, 3])', {"v": 2})
        assert ev == pv is True

    def test_member_false(self):
        ev, pv = _both('pl.col("v").is_in([1, 2, 3])', {"v": 5})
        assert ev == pv is False


class TestRoundMatchesPolars:
    def test_round_matches_polars_including_decimal_half_edges(self):
        # 2.675/0.125-style half edges are where Python's float-repr round and
        # Polars' decimal round diverge; the evaluator must track Polars.
        for x, n in [(0.5, 0), (1.5, 0), (2.5, 0), (-2.5, 0), (2.675, 2), (0.125, 2), (2.665, 2)]:
            ev, pv = _both(f'pl.col("x").round({n})', {"x": x})
            assert ev == pv, f"round({x}, {n}): evaluator={ev} polars={pv}"
