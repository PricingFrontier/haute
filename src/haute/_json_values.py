"""JSON request values checked against a declared Polars type (specs/deploy, specs/workbench).

A value is accepted only when the declared type holds it exactly, so a request types its
columns as the sample it was built from: an integer within its type's range, never a
boolean, a numeric string or a number with a fraction, which is never truncated; a finite
float, an integer only when the float type holds its exact value; a string for a string,
Categorical or Enum column (an Enum's one of its categories); a boolean; an ISO date or
date-time string, a date-time read whole and refused when it is finer than its column holds.
A null is a null of any type. Nothing is coerced.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

import numpy as np
import polars as pl

from haute._polars_dtypes import column_kind, integer_range, numpy_float_type
from haute._temporal_text import TemporalTextError, temporal_physical

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: The declared types a request value can be checked against; any other refuses the deploy,
#: naming its type. A 128-bit integer and a half-precision float are not among them: a
#: deployed model's MLflow signature has no type for either.
REQUEST_DTYPES: tuple[type[pl.DataType], ...] = (
    pl.Int8,
    pl.Int16,
    pl.Int32,
    pl.Int64,
    pl.UInt8,
    pl.UInt16,
    pl.UInt32,
    pl.UInt64,
    pl.Float32,
    pl.Float64,
    pl.String,
    pl.Categorical,
    pl.Enum,
    pl.Boolean,
    pl.Date,
    pl.Datetime,
    pl.Null,
)


class JsonValueError(ValueError):
    """A value its declared type does not hold; *reason*, when there is one past the type,
    says why (a date-time finer than its column holds), for a refusal to show."""

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


def json_value(value: object, dtype: pl.DataType) -> Any:
    """*value* as a column of *dtype* holds it, or :class:`JsonValueError`.

    *dtype* is one of :data:`REQUEST_DTYPES`. A ``date`` or ``datetime`` object is taken
    as its ISO string would be: a deploy's dry run and a pandas request carry them so. A
    date-time is returned as its column's physical integer, which a frame of *dtype* holds
    exactly: a Python ``datetime`` holds microseconds, and would cut a nanosecond request
    short.
    """
    if value is None:
        return None
    base = dtype.base_type()
    kind = column_kind(dtype)
    if kind == "integer":
        low, high = integer_range(dtype)
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            return value
    elif kind == "float":
        number = _exact_float(value, dtype)
        if number is not None:
            return number
    elif base is pl.String or base is pl.Categorical:
        if isinstance(value, str):
            return value
    elif base is pl.Enum:
        if isinstance(value, str) and value in dtype.categories:  # type: ignore[attr-defined]
            return value
    elif base is pl.Boolean:
        if isinstance(value, bool):
            return value
    elif base is pl.Date:
        day = _date(value)
        if day is not None:
            return day
    elif base is pl.Datetime:
        return _datetime(value, dtype)
    raise JsonValueError(f"not {dtype}")


def _exact_float(value: object, dtype: pl.DataType) -> float | None:
    """*value* as a finite float the type holds exactly when it is an integer."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    width = numpy_float_type(dtype)
    if width is not np.float64:
        with np.errstate(over="ignore"):
            narrowed = width(value)
        if not np.isfinite(narrowed) or (isinstance(value, int) and int(narrowed) != value):
            return None
        return float(narrowed) if isinstance(value, int) else float(value)
    try:
        number = float(value)
    except OverflowError:
        return None
    # A Python int and float compare exactly: 2**53 + 1 is not its float.
    if not math.isfinite(number) or (isinstance(value, int) and number != value):
        return None
    return number


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        # A pandas Timestamp for a date column: midnight, without a zone.
        midnight = value.tzinfo is None and value.time() == datetime.min.time()
        return value.date() if midnight else None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _datetime(value: object, dtype: pl.DataType) -> int:
    """*value*, an ISO date-time or a ``datetime``, as *dtype*'s physical integer, read by
    :func:`~haute._temporal_text.temporal_physical`: every digit, never through a Python
    ``datetime``. A zone the column lacks, or a missing one it needs, and a part-second
    finer than it holds are refused."""
    if isinstance(value, datetime):
        value = value.isoformat()
    if not isinstance(value, str):
        raise JsonValueError(f"not {dtype}")
    try:
        return temporal_physical(value, dtype)
    except TemporalTextError as exc:
        raise JsonValueError(str(exc), reason=str(exc)) from None
