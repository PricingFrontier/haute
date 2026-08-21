"""Regression coverage for pure join row-cardinality bounds."""

from __future__ import annotations

import pytest

from haute._cardinality import (
    JoinCardinalityBound,
    join_cardinality_upper_bound,
    normalise_join_validation,
)


@pytest.mark.parametrize(
    ("left_rows", "right_rows", "how", "validate", "expected"),
    [
        (4, 3, "inner", "m:m", 12),
        (4, 3, "inner", "m:1", 4),
        (4, 3, "inner", "1:m", 3),
        (4, 3, "inner", "1:1", 3),
        (4, 0, "left", "m:m", 4),
        (4, 3, "left", "m:1", 4),
        (4, 3, "left", "1:1", 4),
        (4, 3, "left", "1:m", 6),
        (0, 3, "right", "m:m", 3),
        (4, 3, "right", "1:m", 3),
        (4, 3, "right", "1:1", 3),
        (4, 3, "right", "m:1", 6),
        (0, 3, "full", "m:m", 3),
        (4, 0, "full", "m:m", 4),
        (4, 3, "full", "m:m", 12),
        (4, 3, "full", "m:1", 7),
        (4, 3, "full", "1:m", 7),
        (4, 3, "full", "1:1", 7),
        (4, 3, "semi", "m:m", 4),
        (4, 3, "anti", "m:m", 4),
        (4, 3, "cross", "m:m", 12),
    ],
)
def test_join_cardinality_upper_bound_covers_supported_join_contract(
    left_rows: int,
    right_rows: int,
    how: str,
    validate: str,
    expected: int,
) -> None:
    result = join_cardinality_upper_bound(left_rows, right_rows, how=how, validate=validate)

    assert result.max_rows == expected
    assert result.evidence


@pytest.mark.parametrize("left_rows,right_rows", [(-1, 1), (1, -1), (True, 1), (1, True)])
def test_join_cardinality_upper_bound_rejects_invalid_row_bounds(
    left_rows: object, right_rows: object
) -> None:
    with pytest.raises((TypeError, ValueError)):
        join_cardinality_upper_bound(left_rows, right_rows, how="inner", validate="m:m")


@pytest.mark.parametrize(
    ("how", "validate"),
    [("outer", "m:m"), ("inner", "many_to_many")],
)
def test_join_cardinality_upper_bound_rejects_unknown_contract(
    how: str,
    validate: str,
) -> None:
    with pytest.raises(ValueError):
        join_cardinality_upper_bound(1, 1, how=how, validate=validate)


@pytest.mark.parametrize("max_rows", [-1, True, 1.5])
def test_join_cardinality_bound_rejects_non_finite_row_contract(max_rows: object) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        JoinCardinalityBound(max_rows=max_rows, evidence=("proof",))  # type: ignore[arg-type]


@pytest.mark.parametrize("evidence", [(), ("",), (1,)])
def test_join_cardinality_bound_requires_nonempty_evidence(evidence: tuple[object, ...]) -> None:
    with pytest.raises(ValueError, match="non-empty strings"):
        JoinCardinalityBound(max_rows=1, evidence=evidence)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, ""])
def test_normalise_join_validation_defaults_empty_contract_to_many_to_many(
    value: str | None,
) -> None:
    assert normalise_join_validation(value) == "m:m"


def test_join_contract_type_errors_are_not_silently_normalised() -> None:
    with pytest.raises(TypeError, match="join how"):
        join_cardinality_upper_bound(1, 1, how=1, validate="m:m")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="join validate"):
        normalise_join_validation(1)  # type: ignore[arg-type]


def test_join_cardinality_bound_dominates_every_small_valid_polars_join() -> None:
    """Differentially protect every formula against subtle unmatched-row errors."""
    from itertools import product

    import polars as pl

    strategies = ("inner", "left", "right", "full", "semi", "anti")
    validations = ("m:m", "1:1", "1:m", "m:1")
    key_domain = (0, 1, 2)
    for left_rows in range(4):
        for right_rows in range(4):
            left_assignments = product(key_domain, repeat=left_rows)
            for left_keys in left_assignments:
                for right_keys in product(key_domain, repeat=right_rows):
                    left = pl.DataFrame({"key": pl.Series("key", left_keys, dtype=pl.Int64)})
                    right = pl.DataFrame({"key": pl.Series("key", right_keys, dtype=pl.Int64)})
                    for how in strategies:
                        for validate in validations:
                            try:
                                actual = left.join(
                                    right,
                                    on="key",
                                    how=how,
                                    validate=validate,
                                ).height
                            except pl.exceptions.ComputeError:
                                # The uniqueness evidence was false, so Polars
                                # rejects rather than emitting outside the proof.
                                continue
                            bound = join_cardinality_upper_bound(
                                left_rows,
                                right_rows,
                                how=how,
                                validate=validate,
                            )
                            assert actual <= bound.max_rows, (
                                left_keys,
                                right_keys,
                                how,
                                validate,
                                actual,
                                bound,
                            )
