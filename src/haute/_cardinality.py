"""Finite row-cardinality bounds shared by planning and lineage analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

JoinHow = Literal["inner", "left", "right", "full", "semi", "anti", "cross"]
JoinValidation = Literal["m:m", "1:1", "1:m", "m:1"]

_JOIN_HOW_VALUES = frozenset({"inner", "left", "right", "full", "semi", "anti", "cross"})
_JOIN_VALIDATION_VALUES = frozenset({"m:m", "1:1", "1:m", "m:1"})


@dataclass(frozen=True, slots=True)
class JoinCardinalityBound:
    """One safe join-output row bound and the formula that established it."""

    max_rows: int
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_rows, int)
            or isinstance(self.max_rows, bool)
            or self.max_rows < 0
        ):
            raise ValueError("join cardinality max_rows must be a non-negative integer")
        if not self.evidence or any(
            not isinstance(item, str) or not item for item in self.evidence
        ):
            raise ValueError("join cardinality evidence must contain non-empty strings")


def _row_bound(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _join_how(value: str) -> JoinHow:
    if not isinstance(value, str):
        raise TypeError("join how must be a string")
    if value not in _JOIN_HOW_VALUES:
        raise ValueError(f"unsupported join how {value!r}")
    return value  # type: ignore[return-value]


def normalise_join_validation(value: str | None) -> JoinValidation:
    """Return the closed Polars uniqueness contract, defaulting to many-to-many."""
    if value is None or value == "":
        return "m:m"
    if not isinstance(value, str):
        raise TypeError("join validate must be a string")
    if value not in _JOIN_VALIDATION_VALUES:
        raise ValueError(f"unsupported join validate contract {value!r}")
    return value  # type: ignore[return-value]


def join_cardinality_upper_bound(
    left_rows: int,
    right_rows: int,
    *,
    how: str,
    validate: str | None = None,
) -> JoinCardinalityBound:
    """Return a mathematical upper bound for one Polars join.

    Uniqueness contracts are runtime assertions: if their evidence is false,
    Polars rejects the join rather than producing a frame outside this bound.
    Python integers keep repeated products exact instead of overflowing.
    """

    left = _row_bound(left_rows, "left_rows")
    right = _row_bound(right_rows, "right_rows")
    strategy = _join_how(how)
    validation = normalise_join_validation(validate)
    left_unique = validation in {"1:1", "1:m"}
    right_unique = validation in {"1:1", "m:1"}

    formula: str
    if strategy == "cross":
        bound = left * right
        formula = "left_rows*right_rows"
    elif strategy in {"semi", "anti"}:
        bound = left
        formula = "left_rows"
    elif strategy == "inner":
        if left_unique and right_unique:
            bound = min(left, right)
            formula = "min(left_rows,right_rows):both_keys_unique"
        elif right_unique:
            bound = left
            formula = "left_rows:right_key_unique"
        elif left_unique:
            bound = right
            formula = "right_rows:left_key_unique"
        else:
            bound = left * right
            formula = "left_rows*right_rows"
    elif strategy == "left":
        preservation_bound = left * max(right, 1)
        if right_unique:
            bound = left
            formula = "left_rows:right_key_unique"
        elif left_unique:
            bound = left if right == 0 else left + right - 1
            formula = "left_rows+right_rows-1:left_key_unique_and_right_nonempty"
        else:
            bound = preservation_bound
            formula = "left_rows*max(right_rows,1)"
    elif strategy == "right":
        preservation_bound = right * max(left, 1)
        if left_unique:
            bound = right
            formula = "right_rows:left_key_unique"
        elif right_unique:
            bound = right if left == 0 else left + right - 1
            formula = "left_rows+right_rows-1:right_key_unique_and_left_nonempty"
        else:
            bound = preservation_bound
            formula = "right_rows*max(left_rows,1)"
    else:
        assert strategy == "full"
        if left == 0:
            bound = right
            formula = "right_rows:left_empty"
        elif right == 0:
            bound = left
            formula = "left_rows:right_empty"
        elif left_unique or right_unique:
            bound = left + right
            formula = "left_rows+right_rows:at_least_one_key_unique"
        else:
            bound = max(left * right, left + right)
            formula = "max(left_rows*right_rows,left_rows+right_rows)"

    return JoinCardinalityBound(
        max_rows=bound,
        evidence=(
            f"join_how={strategy}",
            f"join_validate={validation}",
            f"join_cardinality_formula={formula}",
            f"join_cardinality_upper_bound={bound}",
        ),
    )


__all__ = [
    "JoinCardinalityBound",
    "JoinHow",
    "JoinValidation",
    "join_cardinality_upper_bound",
    "normalise_join_validation",
]
