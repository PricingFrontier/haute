"""Value-asserting regression tests pinning ``_ExprEvaluator`` to real Polars.

``src/haute/_expression_parser.py``'s :class:`_ExprEvaluator` computes the
per-row ``result_value`` rendered in the user-facing calculation trace. For
non-window expressions that value is written straight to ``step.calculation``
and is **not** reconciled against the engine's actual Polars output (see
``src/haute/_trace_enrichment.py`` ~L1506-1522), so an evaluator that diverges
from Polars semantics silently shows a *wrong* number while older tests — which
only asserted ``result is not None`` — stay green.

A 2026-06-24 coverage audit (``notes-haute/git-history/COVERAGE_AUDIT_2026-06-24.md``
§3.1) flagged four divergences: ``str.contains``, ``round``, ``is_between`` and
``is_in``. The tests below assert the evaluator's concrete *value* and, wherever
the form is also valid Polars, cross-check it against the **identical** Polars
expression evaluated on the same one-row input — the strongest guard against
future drift.

A note on ``round``: the audit described Polars as rounding "half-away-from-zero".
That does not hold for the pinned Polars (1.39): Polars computes
``round(v * 10**n) / 10**n`` with **half-to-even** tie breaking. That differs
from Python's decimal-accurate two-arg ``round(v, n)`` on float-edge inputs
(``round(2.675, 2)`` is ``2.68`` under Polars but ``2.67`` under ``round()``),
which is the divergence the fix actually had to correct. The parity cross-checks
below are what prove the implementation matches the engine rather than the prose.
"""

from __future__ import annotations

import ast

import polars as pl
import pytest

from haute._expression_parser import _ExprEvaluator, evaluate_expression


def _trace_value(expr_text: str, row: dict) -> object:
    """``result_value`` the trace renders for ``df.with_columns(<expr>.alias("out"))``."""
    code = f'df = df.with_columns({expr_text}.alias("out"))'
    return evaluate_expression(code, "out", dict(row)).result_value


def _polars_value(expr_text: str, row: dict) -> object:
    """The engine's actual output for the *same* expression on a one-row frame."""
    # expr_text is a test-local literal (never user input), so eval is safe here.
    expr = eval(expr_text, {"pl": pl})
    return pl.DataFrame([dict(row)]).select(expr.alias("out")).item()


def _eval_node(src: str, row: dict | None = None) -> object:
    """Evaluate a bare expression string straight through ``_ExprEvaluator``."""
    node = ast.parse(src, mode="eval").body
    return _ExprEvaluator(row or {}).evaluate(node)


# ---------------------------------------------------------------------------
# round() — Polars: round(v * 10**n) / 10**n, half-to-EVEN (NOT half-away)
# ---------------------------------------------------------------------------

ROUND_CASES = [
    # Clean ties: half-to-even (the audit's "half-away" claim would give 1.0 / 3.0).
    ('pl.col("x").round(0)', {"x": 0.5}, 0.0),
    ('pl.col("x").round(0)', {"x": 1.5}, 2.0),
    ('pl.col("x").round(0)', {"x": 2.5}, 2.0),
    ('pl.col("x").round(0)', {"x": -0.5}, 0.0),
    ('pl.col("x").round(0)', {"x": -2.5}, -2.0),
    ('pl.col("x").round(1)', {"x": 1.25}, 1.2),
    ('pl.col("x").round(1)', {"x": 0.35}, 0.4),
    ('pl.col("x").round(1)', {"x": 0.45}, 0.4),
    ('pl.col("x").round(1)', {"x": 0.65}, 0.6),
    ('pl.col("x").round(2)', {"x": 0.125}, 0.12),
    # Float-edge inputs where scale-then-round diverges from round(v, n).
    ('pl.col("x").round(2)', {"x": 2.675}, 2.68),
    ('pl.col("x").round(2)', {"x": 8.235}, 8.24),
    ('pl.col("x").round(2)', {"x": 1.115}, 1.12),
    ('pl.col("x").round(2)', {"x": -2.675}, -2.68),
    # Non-tie sanity + default decimals.
    ('pl.col("x").round(2)', {"x": 3.14159}, 3.14),
    ('pl.col("x").round()', {"x": 1.5}, 2.0),
]


@pytest.mark.parametrize("expr_text,row,expected", ROUND_CASES)
def test_round_matches_polars(expr_text: str, row: dict, expected: float) -> None:
    trace = _trace_value(expr_text, row)
    polars = _polars_value(expr_text, row)
    assert trace == polars, f"{expr_text} on {row}: trace={trace!r} polars={polars!r}"
    assert trace == expected


def test_round_diverges_from_naive_python_round() -> None:
    # The old implementation used round(val, n), which is decimal-accurate and
    # yields 2.67; Polars (scale, round-half-even, unscale) yields 2.68. The
    # trace must follow Polars, the engine the user actually runs.
    assert round(2.675, 2) == 2.67
    assert _trace_value('pl.col("x").round(2)', {"x": 2.675}) == 2.68
    assert _polars_value('pl.col("x").round(2)', {"x": 2.675}) == 2.68


def test_round_no_decimals_defaults_to_zero() -> None:
    assert _eval_node('pl.col("x").round()', {"x": 2.5}) == 2.0


def test_round_with_null_decimals_returns_value_unchanged() -> None:
    # A literal None decimals can't be rounded; the value passes through.
    assert _eval_node('pl.col("x").round(None)', {"x": 1.5}) == 1.5


def test_round_overflowing_scale_falls_back_to_value() -> None:
    # 10**400 overflows the float scale factor; the guard returns the input.
    assert _trace_value('pl.col("x").round(400)', {"x": 1.5}) == 1.5


# ---------------------------------------------------------------------------
# str.contains() — Polars: REGEX by default, substring only when literal=True
# ---------------------------------------------------------------------------

CONTAINS_CASES = [
    ('pl.col("s").str.contains("a.c")', {"s": "abc"}, True),
    ('pl.col("s").str.contains("a.c")', {"s": "abxc"}, False),
    ('pl.col("s").str.contains("a.c", literal=True)', {"s": "abc"}, False),
    ('pl.col("s").str.contains("a.c", literal=True)', {"s": "a.c"}, True),
    ('pl.col("s").str.contains("^hello")', {"s": "hello world"}, True),
    ('pl.col("s").str.contains("^hello")', {"s": "say hello"}, False),
    ('pl.col("s").str.contains("world$")', {"s": "hello world"}, True),
    ('pl.col("s").str.contains("[0-9]+")', {"s": "abc123"}, True),
    ('pl.col("s").str.contains("[0-9]+")', {"s": "abc"}, False),
    ('pl.col("s").str.contains("")', {"s": "anything"}, True),
]


@pytest.mark.parametrize("expr_text,row,expected", CONTAINS_CASES)
def test_contains_matches_polars(expr_text: str, row: dict, expected: bool) -> None:
    trace = _trace_value(expr_text, row)
    polars = _polars_value(expr_text, row)
    assert trace == polars, f"{expr_text} on {row}: trace={trace!r} polars={polars!r}"
    assert trace is expected


def test_contains_defaults_to_regex_not_substring() -> None:
    # "a.c" is a regex; "." matches any char, so "abc" matches even though
    # "a.c" is not a literal substring of "abc". The old impl returned False.
    assert _trace_value('pl.col("s").str.contains("a.c")', {"s": "abc"}) is True
    assert _trace_value('pl.col("s").str.contains("a.c", literal=True)', {"s": "abc"}) is False


def test_contains_literal_positional_arg() -> None:
    # Polars rejects a positional `literal`, but the evaluator still honours it
    # for robustness — a positional truthy value means substring matching.
    assert _eval_node('pl.col("s").str.contains("a.c", True)', {"s": "abc"}) is False
    assert _eval_node('pl.col("s").str.contains("a.c", False)', {"s": "abc"}) is True


def test_contains_invalid_regex_returns_none() -> None:
    # An unterminated character class is an invalid regex; the trace degrades to
    # None rather than crashing (Polars would raise at execution time).
    assert _trace_value('pl.col("s").str.contains("[")', {"s": "abc"}) is None


def test_contains_null_pattern_returns_none() -> None:
    assert _eval_node('pl.col("s").str.contains(pl.col("p"))', {"s": "abc", "p": None}) is None


# ---------------------------------------------------------------------------
# is_between() — Polars honours closed=: both | left | right | none
# ---------------------------------------------------------------------------

BETWEEN_CASES = [
    ('pl.col("v").is_between(1, 5)', {"v": 1}, True),
    ('pl.col("v").is_between(1, 5)', {"v": 5}, True),
    ('pl.col("v").is_between(1, 5)', {"v": 3}, True),
    ('pl.col("v").is_between(1, 5)', {"v": 6}, False),
    ('pl.col("v").is_between(1, 5, closed="both")', {"v": 1}, True),
    ('pl.col("v").is_between(1, 5, closed="both")', {"v": 5}, True),
    ('pl.col("v").is_between(1, 5, closed="left")', {"v": 1}, True),
    ('pl.col("v").is_between(1, 5, closed="left")', {"v": 5}, False),
    ('pl.col("v").is_between(1, 5, closed="right")', {"v": 1}, False),
    ('pl.col("v").is_between(1, 5, closed="right")', {"v": 5}, True),
    ('pl.col("v").is_between(1, 5, closed="none")', {"v": 1}, False),
    ('pl.col("v").is_between(1, 5, closed="none")', {"v": 5}, False),
    ('pl.col("v").is_between(1, 5, closed="none")', {"v": 3}, True),
    # closed passed positionally (the 3rd positional arg).
    ('pl.col("v").is_between(1, 5, "left")', {"v": 5}, False),
]


@pytest.mark.parametrize("expr_text,row,expected", BETWEEN_CASES)
def test_is_between_matches_polars(expr_text: str, row: dict, expected: bool) -> None:
    trace = _trace_value(expr_text, row)
    polars = _polars_value(expr_text, row)
    assert trace == polars, f"{expr_text} on {row}: trace={trace!r} polars={polars!r}"
    assert trace is expected


def test_is_between_closed_bound_was_previously_ignored() -> None:
    # Before the fix the closed= bound was dropped, so the boundary always
    # counted as inside. v=5 with closed="left" must now be excluded.
    assert _trace_value('pl.col("v").is_between(1, 5, closed="left")', {"v": 5}) is False
    assert _trace_value('pl.col("v").is_between(1, 5, closed="right")', {"v": 1}) is False


def test_is_between_null_bound_returns_none() -> None:
    assert _eval_node('pl.col("v").is_between(pl.col("lo"), 5)', {"v": 3, "lo": None}) is None


# ---------------------------------------------------------------------------
# is_in() — requires ast.List/Set/Tuple handling in evaluate()
# ---------------------------------------------------------------------------

IS_IN_CASES = [
    ('pl.col("v").is_in([1, 2, 3])', {"v": 2}, True),
    ('pl.col("v").is_in([1, 2, 3])', {"v": 7}, False),
    ('pl.col("v").is_in([10, 20])', {"v": 10}, True),
    ('pl.col("v").is_in((1, 2, 3))', {"v": 2}, True),
    ('pl.col("v").is_in((1, 2, 3))', {"v": 7}, False),
    ('pl.col("v").is_in({1, 2, 3})', {"v": 3}, True),
]


@pytest.mark.parametrize("expr_text,row,expected", IS_IN_CASES)
def test_is_in_matches_polars(expr_text: str, row: dict, expected: bool) -> None:
    trace = _trace_value(expr_text, row)
    polars = _polars_value(expr_text, row)
    assert trace == polars, f"{expr_text} on {row}: trace={trace!r} polars={polars!r}"
    assert trace is expected


def test_is_in_string_membership_matches_polars() -> None:
    expr = 'pl.col("s").is_in(["a", "b", "c"])'
    assert _trace_value(expr, {"s": "b"}) is True
    assert _polars_value(expr, {"s": "b"}) is True
    assert _trace_value(expr, {"s": "z"}) is False


def test_is_in_was_previously_unevaluable() -> None:
    # The evaluate() dispatch had no ast.List case, so is_in always returned
    # None regardless of membership. It now evaluates the literal collection.
    assert _trace_value('pl.col("v").is_in([1, 2, 3])', {"v": 2}) is True


def test_is_in_non_collection_argument_returns_none() -> None:
    assert _eval_node('pl.col("v").is_in(5)', {"v": 5}) is None


def test_list_tuple_set_literals_evaluate() -> None:
    assert _eval_node("[1, 2, 3]") == [1, 2, 3]
    assert _eval_node("(1, 2, 3)") == (1, 2, 3)
    assert _eval_node("{1, 2, 3}") == {1, 2, 3}


# ---------------------------------------------------------------------------
# Conditional (when/then/otherwise) chains — first matching branch wins
# ---------------------------------------------------------------------------

CONDITIONAL_CASES = [
    ('pl.when(pl.col("x") > 0).then(1).otherwise(-1)', {"x": 5}, 1),
    ('pl.when(pl.col("x") > 0).then(1).otherwise(-1)', {"x": -5}, -1),
    (
        'pl.when(pl.col("x") > 10).then(2).when(pl.col("x") > 0).then(1).otherwise(0)',
        {"x": 50},
        2,
    ),
    (
        'pl.when(pl.col("x") > 10).then(2).when(pl.col("x") > 0).then(1).otherwise(0)',
        {"x": 5},
        1,
    ),
    (
        'pl.when(pl.col("x") > 10).then(2).when(pl.col("x") > 0).then(1).otherwise(0)',
        {"x": -5},
        0,
    ),
]


@pytest.mark.parametrize("expr_text,row,expected", CONDITIONAL_CASES)
def test_when_then_otherwise_matches_polars(expr_text: str, row: dict, expected: int) -> None:
    trace = _trace_value(expr_text, row)
    polars = _polars_value(expr_text, row)
    assert trace == polars, f"{expr_text} on {row}: trace={trace!r} polars={polars!r}"
    assert trace == expected


# ---------------------------------------------------------------------------
# Graceful-degradation / defensive branches.
#
# These exercise the guard clauses added alongside the four fixes: the evaluator
# must return None (or fall back to a sensible default) rather than crash on
# malformed, unknown, or partial expressions. They assert through the raw
# evaluator because most of these forms are not valid Polars (so there is no
# engine value to cross-check against) — the contract is "never raise into the
# trace".
# ---------------------------------------------------------------------------


def test_contains_with_no_pattern_returns_none() -> None:
    assert _eval_node('pl.col("s").str.contains()', {"s": "abc"}) is None


def test_contains_positional_literal_none_falls_back_to_regex() -> None:
    # A None positional `literal` leaves the default regex behaviour in place.
    assert _eval_node('pl.col("s").str.contains("a.c", None)', {"s": "abc"}) is True


def test_contains_ignores_unknown_keyword() -> None:
    # Polars accepts a `strict=` kwarg; the evaluator ignores anything that
    # is not `literal`, so matching stays regex-based.
    assert _eval_node('pl.col("s").str.contains("a.c", strict=True)', {"s": "abc"}) is True


def test_contains_literal_none_keyword_falls_back_to_regex() -> None:
    assert _eval_node('pl.col("s").str.contains("a.c", literal=None)', {"s": "abc"}) is True


def test_unhandled_str_method_returns_none() -> None:
    # A str-namespace method the evaluator does not model degrades to None.
    assert _eval_node('pl.col("s").str.ends_with("c")', {"s": "abc"}) is None


def test_is_between_non_string_positional_closed_uses_default() -> None:
    # A non-string positional `closed` is ignored; the default "both" applies.
    assert _eval_node('pl.col("v").is_between(1, 5, 99)', {"v": 3}) is True


def test_is_between_ignores_unknown_keyword() -> None:
    assert _eval_node('pl.col("v").is_between(1, 5, junk=1)', {"v": 3}) is True


def test_is_between_non_string_closed_keyword_uses_default() -> None:
    assert _eval_node('pl.col("v").is_between(1, 5, closed=99)', {"v": 3}) is True


def test_bare_when_without_then_returns_none() -> None:
    assert _eval_node('pl.when(pl.col("c"))', {"c": True}) is None


def test_chained_when_method_returns_none() -> None:
    assert _eval_node('pl.col("x").when(pl.col("c"))', {"x": 1, "c": True}) is None


def test_then_without_when_returns_none() -> None:
    assert _eval_node('pl.col("x").then(1)', {"x": 1}) is None


def test_when_chain_with_alias_is_walked() -> None:
    # An .alias() embedded mid-chain is transparently walked through.
    assert _eval_node('pl.when(pl.col("c")).then(1).alias("z").otherwise(0)', {"c": True}) == 1


def test_call_on_call_returns_none() -> None:
    assert _eval_node("f()()") is None


def test_alias_outer_method_returns_receiver_value() -> None:
    assert _eval_node('pl.col("x").alias("y")', {"x": 5}) == 5


def test_attribute_and_unsupported_nodes_return_none() -> None:
    assert _eval_node("pl.Float64") is None
    assert _eval_node("foo.bar") is None
    assert _eval_node("lambda: 1") is None


def test_sqrt_and_clip_match_polars() -> None:
    assert _trace_value('pl.col("x").sqrt()', {"x": 9.0}) == 3.0
    assert _polars_value('pl.col("x").sqrt()', {"x": 9.0}) == 3.0
    assert _trace_value('pl.col("x").clip(0, 10)', {"x": 15}) == 10
    assert _polars_value('pl.col("x").clip(0, 10)', {"x": 15}) == 10
