"""Dtype facts shared by every column-summarising surface.

Explore's frame statistics and the assistant's value profiles ask the same
questions of a Polars column — can it be counted, can its values be encoded —
and both learned the same answers the hard way. Keeping those answers in one
dependency-light module (polars and the stdlib-only JSON-safe encoder, no
routes or execution imports) is what stops a second summariser rediscovering
them as production failures.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta

import polars as pl

from haute._json_safe import non_finite_float_sentinel

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
    finite float and str. Polars returns native Python objects for date and
    decimal columns — ``date``, ``Decimal`` — and either raises ``TypeError`` at
    encode time, far from the column that produced it. Numbers and strings keep
    their JSON type so a numeric bound stays a number; a date or a decimal
    becomes its ``str()`` form, the ISO-8601 spelling and the exact digits.
    A ``datetime``, ``time`` or ``timedelta`` is refused: Python holds it to the
    microsecond, so a nanosecond value read into one has lost three digits, and
    a summariser writes such a value's exact text in Polars instead
    (:func:`haute._temporal_text.temporal_text`). ``NaN`` and ``Infinity`` are
    not JSON either, and dropping the bound would hide that the column holds
    them, so a non-finite float becomes the tagged sentinel every Haute payload
    uses for one (:func:`haute._json_safe.non_finite_float_sentinel`) — never
    the bare text ``"inf"``, which a string column could hold too.
    """

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else non_finite_float_sentinel(value)
    if isinstance(value, (datetime, time, timedelta)):
        raise TypeError(
            f"A {type(value).__name__} reaches a summary only as its exact text, written in "
            "Polars by haute._temporal_text.temporal_text: Python holds it to the microsecond."
        )
    return str(value)


__all__ = [
    "CATEGORICAL_COUNT_FIELD",
    "UNHASHABLE_DTYPES",
    "is_unhashable_dtype",
    "json_safe_scalar",
]
