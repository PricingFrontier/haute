"""Dtype facts shared by every column-summarising surface.

Explore's frame statistics and the assistant's value profiles ask the same
questions of a Polars column — can it be counted, can its values be encoded —
and both learned the same answers the hard way. Keeping those answers in one
dependency-light module (polars only, no routes or execution imports) is what
stops a second summariser rediscovering them as production failures.
"""

from __future__ import annotations

import math

import polars as pl

# Dtypes whose values are not hashable in Polars and therefore cannot have
# ``n_unique`` computed: it raises ``InvalidOperationError``. Pre-detected by
# dtype so no summariser invokes it on a column guaranteed to fail.
UNHASHABLE_DTYPES: tuple[type[pl.DataType], ...] = (pl.Object,)

# Reserved alias for the count side of a ``value_counts`` result. Polars
# refuses ``value_counts`` on a column already named ``count`` — the default
# would collide — so every caller names the count field explicitly instead.
CATEGORICAL_COUNT_FIELD = "__haute_categorical_count"


def is_unhashable_dtype(dtype: pl.DataType) -> bool:
    """Return True when ``n_unique`` cannot be computed for *dtype*.

    Object columns are excluded because Polars raises ``InvalidOperationError``
    when their values are hashed. All other dtypes (including Struct, Decimal,
    Datetime, List, Array, etc.) are allowed through to ``n_unique``.
    """

    return dtype.base_type() in UNHASHABLE_DTYPES


def json_safe_scalar(value: object) -> object:
    """Render one Polars scalar as a JSON-encodable value, preserving type.

    A summary that reaches a provider is serialised with the standard library
    encoder under ``allow_nan=False``, which accepts only null, bool, int,
    finite float and str. Polars returns native Python objects for temporal and
    decimal columns — ``date``, ``datetime``, ``time``, ``timedelta``,
    ``Decimal`` — and any of them raises ``TypeError`` at encode time, far from
    the column that produced it. Numbers and strings keep their JSON type so a
    numeric bound stays a number; everything else becomes its ``str()`` form,
    which is the ISO-8601 spelling for temporals and the exact digits for a
    decimal. A non-finite float is rendered the same way, because ``NaN`` and
    ``Infinity`` are not JSON and the alternative — dropping the bound — hides
    that the column is entirely missing.
    """

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)


__all__ = [
    "CATEGORICAL_COUNT_FIELD",
    "UNHASHABLE_DTYPES",
    "is_unhashable_dtype",
    "json_safe_scalar",
]
