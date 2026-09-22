"""Which banding rule claims each row (RAT-B01).

The Banding editor needs per-rule match counts that agree with execution, and
grouping the output column cannot give them: several rules may share an
assignment and the default may equal one of them. These tests pin the claim
expression against hand-calculated vectors, and then pin the property that
matters — a claim is exactly the rule whose assignment execution writes.
"""

from __future__ import annotations

import math
import random
from typing import Any

import polars as pl
import pytest

from haute._rating import _apply_banding, banding_rule_claim_expr


def _claims(
    values: list[Any],
    dtype: pl.DataType,
    mode: str,
    rules: list[dict[str, Any]],
    *,
    right_closed: bool = True,
) -> tuple[list[int], int]:
    """Return ``rule_counts`` aligned to *rules* and the unmatched row count."""
    frame = pl.DataFrame({"x": pl.Series(values, dtype=dtype)})
    claim = frame.select(
        banding_rule_claim_expr(pl.col("x"), dtype, mode, rules, right_closed)
    ).to_series()
    counts = [0] * len(rules)
    unmatched = 0
    for index in claim.to_list():
        if index is None:
            unmatched += 1
        else:
            counts[index] += 1
    return counts, unmatched


BREAKPOINT_RULES = [
    {"boundary": "10", "label": "low"},
    {"boundary": "", "label": "high"},
    {"boundary": "5", "label": "low"},
]
BREAKPOINT_VALUES = [1, 5, 10, 10.5, 20, None, math.nan, math.inf]


def test_breakpoint_claims_follow_the_users_rule_order_not_the_sorted_intervals() -> None:
    # Execution sorts the boundaries and evaluates the open-ended one last, so
    # the claim has to name the breakpoint the user wrote, not the interval.
    assert _claims(BREAKPOINT_VALUES, pl.Float64, "breakpoints", BREAKPOINT_RULES) == (
        [1, 2, 2],
        3,
    )


def test_breakpoint_claims_follow_the_closure_the_factor_declares() -> None:
    assert _claims(
        BREAKPOINT_VALUES, pl.Float64, "breakpoints", BREAKPOINT_RULES, right_closed=False
    ) == ([1, 3, 1], 3)


def test_a_repeated_label_and_a_default_equal_to_it_keep_their_own_counts() -> None:
    # Both "low" rules and the "low" default would be one group in the output
    # column; the claim keeps them apart.
    counts, unmatched = _claims(BREAKPOINT_VALUES, pl.Float64, "breakpoints", BREAKPOINT_RULES)
    assert counts[0] == 1 and counts[2] == 2
    assert unmatched == 3


def test_categorical_claims_are_last_wins_on_the_column_cast_to_text() -> None:
    rules = [
        {"value": "1.0", "assignment": "A"},
        {"value": "NaN", "assignment": "B"},
        {"value": "1", "assignment": "A"},
        {"value": "1.0", "assignment": "C"},
        {"value": "inf", "assignment": ""},
    ]
    # A Float64 1.0 is "1.0", never "1"; the repeated "1.0" is claimed by its
    # last rule; a rule with no assignment claims nothing.
    assert _claims([1.0, 2.0, math.nan, None, 1.0, math.inf], pl.Float64, "categorical", rules) == (
        [0, 1, 0, 2, 0],
        3,
    )


@pytest.mark.parametrize(
    ("values", "dtype", "value"),
    [
        ([1, 2, None], pl.Int64, "1"),
        ([True, False, None], pl.Boolean, "true"),
    ],
)
def test_categorical_claims_match_the_text_each_dtype_casts_to(
    values: list[Any], dtype: pl.DataType, value: str
) -> None:
    assert _claims(values, dtype, "categorical", [{"value": value, "assignment": "T"}]) == (
        [1],
        2,
    )


def test_a_continuous_rule_with_no_assignment_claims_nothing() -> None:
    rules = [
        {"op1": ">", "val1": "0", "op2": "<=", "val2": "10", "assignment": ""},
        {"op1": ">", "val1": "5", "assignment": "GT5"},
    ]
    # Execution never writes an empty assignment, so the row it would have
    # covered is defaulted and unclaimed rather than claimed by that rule.
    assert _claims([1, 7, 12], pl.Float64, "continuous", rules) == ([0, 2], 1)


def test_rules_execution_rejects_are_rejected_with_execution_s_message() -> None:
    with pytest.raises(ValueError, match="unsupported operator"):
        banding_rule_claim_expr(
            pl.col("x"), pl.Float64, "continuous", [{"op1": "~", "val1": "1", "assignment": "A"}]
        )
    with pytest.raises(ValueError, match="non-numeric threshold"):
        banding_rule_claim_expr(
            pl.col("x"), pl.Float64, "continuous", [{"op1": ">", "val1": "x", "assignment": "A"}]
        )
    with pytest.raises(ValueError, match="Duplicate breakpoint boundary"):
        banding_rule_claim_expr(
            pl.col("x"),
            pl.Float64,
            "breakpoints",
            [{"boundary": "5", "label": "a"}, {"boundary": "5", "label": "b"}],
        )
    with pytest.raises(ValueError, match="has no usable categorical rule"):
        banding_rule_claim_expr(
            pl.col("x"),
            pl.Float64,
            "categorical",
            [{"value": "", "assignment": ""}],
            output_column="band",
        )
    with pytest.raises(ValueError, match="has no usable continuous rule"):
        banding_rule_claim_expr(
            pl.col("x"),
            pl.Float64,
            "continuous",
            [{"op1": ">", "val1": "1", "assignment": ""}],
            output_column="band",
        )
    with pytest.raises(ValueError, match="unsupported banding type"):
        banding_rule_claim_expr(pl.col("x"), pl.Float64, "sideways", [{"value": "1"}])


def test_a_factor_with_no_rules_claims_nothing_rather_than_failing() -> None:
    # An unconfigured factor is a documented no-op in execution, so asking for
    # its claims answers "nothing claimed" instead of raising.
    assert _claims([1, 2], pl.Float64, "continuous", []) == ([], 2)


# --------------------------------------------------------------- property

_MODES = ("breakpoints", "categorical", "continuous")


def _generated_case(seed: int) -> tuple[str, list[Any], list[dict[str, Any]], str | None, bool]:
    """One deterministic (mode, values, rules, default, right_closed) case."""
    rng = random.Random(seed)
    mode = _MODES[seed % len(_MODES)]
    values: list[Any] = []
    for _ in range(rng.randint(1, 12)):
        pick = rng.random()
        if pick < 0.1:
            values.append(None)
        elif pick < 0.2:
            values.append(math.nan)
        elif pick < 0.25:
            values.append(math.inf)
        else:
            values.append(float(rng.randint(-5, 25)))
    labels = ["", "a", "b", "a"]
    rules: list[dict[str, Any]] = []
    for _ in range(rng.randint(1, 4)):
        if mode == "categorical":
            rules.append(
                {
                    "value": rng.choice(["1.0", "2.0", "NaN", "inf", "", "7.0"]),
                    "assignment": rng.choice(labels),
                }
            )
        elif mode == "breakpoints":
            rules.append(
                {
                    "boundary": rng.choice(["", "0", "5", "10", "20"]),
                    "label": rng.choice(labels),
                }
            )
        else:
            rule: dict[str, Any] = {
                "op1": rng.choice([">", ">=", "<", "<=", "="]),
                "val1": str(rng.randint(-5, 25)),
                "assignment": rng.choice(labels),
            }
            if rng.random() < 0.5:
                rule["op2"] = rng.choice(["<", "<="])
                rule["val2"] = str(rng.randint(-5, 25))
            rules.append(rule)
    default = rng.choice([None, "D", "a"])
    return mode, values, rules, default, rng.random() < 0.5


def test_the_generated_cases_actually_exercise_claims_and_defaults() -> None:
    """Guard the property above against drifting into proving nothing.

    Most of its value is in the rows it compares, so if the generator ever stops
    producing rules that claim anything — or stops producing rows that default —
    the property would still pass while asserting almost nothing.
    """
    compared = claimed = defaulted = 0
    for seed in range(120):
        mode, values, rules, default, right_closed = _generated_case(seed)
        frame = pl.DataFrame({"x": pl.Series(values, dtype=pl.Float64)})
        try:
            _apply_banding(
                frame.lazy(), "x", "band", mode, rules, default=default, right_closed=right_closed
            ).collect()
        except ValueError:
            continue
        claim = frame.select(
            banding_rule_claim_expr(
                pl.col("x"), pl.Float64, mode, rules, right_closed, output_column="band"
            )
        ).to_series()
        compared += 1
        claimed += sum(1 for index in claim.to_list() if index is not None)
        defaulted += claim.null_count()

    assert compared >= 60, compared
    assert claimed >= 50, claimed
    assert defaulted >= 50, defaulted


@pytest.mark.parametrize("seed", range(120))
def test_a_claim_is_the_rule_whose_assignment_execution_writes(seed: int) -> None:
    mode, values, rules, default, right_closed = _generated_case(seed)
    frame = pl.DataFrame({"x": pl.Series(values, dtype=pl.Float64)})
    # Only execution is allowed to raise here. Claims are evaluated outside the
    # handler, so rejecting rules execution accepts fails the test rather than
    # looking like the rejection case.
    try:
        banded = _apply_banding(
            frame.lazy(),
            "x",
            "band",
            mode,
            rules,
            default=default,
            right_closed=right_closed,
        ).collect()
    except ValueError as execution_error:
        with pytest.raises(ValueError) as claim_error:
            banding_rule_claim_expr(
                pl.col("x"), pl.Float64, mode, rules, right_closed, output_column="band"
            )
        # The same rejection, in the same words: the editor shows execution's
        # message for rules execution would refuse.
        assert str(claim_error.value) == str(execution_error)
        return
    claim = frame.select(
        banding_rule_claim_expr(
            pl.col("x"), pl.Float64, mode, rules, right_closed, output_column="band"
        )
    ).to_series()
    if "band" not in banded.columns:
        # No usable rule and none configured: execution adds no column, so
        # nothing is claimed either.
        assert claim.null_count() == len(values)
        return

    normalised = [{**rule, "assignment": str(rule.get("assignment", "") or "")} for rule in rules]
    for index, output in zip(claim.to_list(), banded["band"].to_list(), strict=True):
        if index is None:
            assert output == (default if default is not None else None)
        else:
            expected = normalised[index].get("assignment") or normalised[index].get("label")
            assert output == str(expected)
