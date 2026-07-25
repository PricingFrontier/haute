from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from typing import Any

import polars as pl
import pytest

from haute._rating import (
    _coerce_rating_lookup_expr,
    _rating_key_expr,
    normalise_rating_key,
)
from tests.fixtures.rating_key_cases import RATING_KEY_CASES

pytestmark = pytest.mark.perf

_REPEATS = 5
_ITERATIONS = 20
_MAX_REFERENCE_RATIO = 0.5


def _dataframe_expression_reference(value: Any, dtype: pl.DataType) -> str | None:
    """The pre-review one-row DataFrame implementation, retained as a control."""
    raw = pl.DataFrame({"__haute_rating_key__": [value]})
    source_dtype = raw.schema["__haute_rating_key__"]
    typed = raw.select(
        _coerce_rating_lookup_expr(
            "__haute_rating_key__",
            source_dtype,
            dtype,
        )
    )
    key = typed.select(_rating_key_expr("__haute_rating_key__", dtype)).item()
    return None if key is None else str(key)


def _median_seconds(operation: Callable[[], None]) -> float:
    samples: list[float] = []
    operation()
    for _ in range(_REPEATS):
        start = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def test_scalar_key_normalisation_avoids_dataframe_expression_cost(
    request: pytest.FixtureRequest,
) -> None:
    cases = [(case.value, case.dtype) for case in RATING_KEY_CASES]
    expected = [_dataframe_expression_reference(value, dtype) for value, dtype in cases]
    assert [normalise_rating_key(value, dtype) for value, dtype in cases] == expected

    def production() -> None:
        for _ in range(_ITERATIONS):
            for value, dtype in cases:
                normalise_rating_key(value, dtype)

    def reference() -> None:
        for _ in range(_ITERATIONS):
            for value, dtype in cases:
                _dataframe_expression_reference(value, dtype)

    production_seconds = _median_seconds(production)
    reference_seconds = _median_seconds(reference)
    ratio = production_seconds / reference_seconds
    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "rating_key_scalar_normalisation",
                "cases": len(cases),
                "iterations": _ITERATIONS,
                "repeats": _REPEATS,
                "production_seconds": production_seconds,
                "dataframe_reference_seconds": reference_seconds,
                "ratio": ratio,
                "maximum_reference_ratio": _MAX_REFERENCE_RATIO,
            },
        )
    )

    assert ratio <= _MAX_REFERENCE_RATIO
