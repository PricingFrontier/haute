"""JSON request values checked against a declared Polars type (specs/deploy, specs/workbench).

A value is accepted only when the declared type holds it exactly, so a request types its
columns as the sample it was built from: an integer within its type's range, never a
boolean, a numeric string or a number with a fraction, which is never truncated; a finite
float, an integer only when the float type holds its exact value; a string for a string,
Categorical or Enum column (an Enum's one of its categories); a boolean; an ISO date or
date-time string. A null is a null of any type. Nothing is coerced.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

import numpy as np
import polars as pl

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

_INTEGER_RANGES: dict[type[pl.DataType], tuple[int, int]] = {
    pl.Int8: (-(2**7), 2**7 - 1),
    pl.Int16: (-(2**15), 2**15 - 1),
    pl.Int32: (-(2**31), 2**31 - 1),
    pl.Int64: (-(2**63), 2**63 - 1),
    pl.UInt8: (0, 2**8 - 1),
    pl.UInt16: (0, 2**16 - 1),
    pl.UInt32: (0, 2**32 - 1),
    pl.UInt64: (0, 2**64 - 1),
}

#: The declared types a request value can be checked against; any other refuses the deploy.
REQUEST_DTYPES: tuple[type[pl.DataType], ...] = (
    *_INTEGER_RANGES,
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
    """A value its declared type does not hold."""


def json_value(value: object, dtype: pl.DataType) -> Any:
    """*value* as a column of *dtype* holds it, or :class:`JsonValueError`.

    *dtype* is one of :data:`REQUEST_DTYPES`. A ``date`` or ``datetime`` object is taken
    as its ISO string would be: a deploy's dry run and a pandas request carry them so.
    """
    if value is None:
        return None
    base = dtype.base_type()
    if base in _INTEGER_RANGES:
        low, high = _INTEGER_RANGES[base]
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            return value
    elif base is pl.Float64 or base is pl.Float32:
        number = _exact_float(value, base)
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
        moment = _datetime(value, zoned=dtype.time_zone is not None)  # type: ignore[attr-defined]
        if moment is not None:
            return moment
    raise JsonValueError(f"not {dtype}")


def _exact_float(value: object, base: type[pl.DataType]) -> float | None:
    """*value* as a finite float the type holds exactly when it is an integer."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if base is pl.Float32:
        with np.errstate(over="ignore"):
            narrowed = np.float32(value)
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


def _datetime(value: object, *, zoned: bool) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime) or (value.tzinfo is not None) != zoned:
        return None
    return value
