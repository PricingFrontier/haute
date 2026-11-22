"""The exact text of a datetime, time or duration value, written by Polars.

Python's ``datetime``, ``time`` and ``timedelta`` hold microseconds, while a Polars
``Datetime("ns")``, a ``Duration("ns")`` and every ``Time`` hold nanoseconds, so a
value collected into Python loses what lies below a microsecond and two values that
differ only there become one. Polars' own ``cast(pl.String)`` is no answer: it drops
a ``Time``'s whole fraction and refuses a ``Duration``. :func:`temporal_text` writes
the text inside the query instead, at the column's own precision, in Python's
spelling extended to nanoseconds: no fraction for a whole second, six digits for a
whole microsecond and nine otherwise; an aware datetime carries its offset, and a
duration reads as ``str(timedelta)`` does.
"""

from __future__ import annotations

from typing import cast

import polars as pl

from haute._polars_dtypes import ColumnKind, column_kind
from haute.errors import SchemaMismatchError

#: The column kinds whose values Python cannot hold exactly, so reach it as text.
TEMPORAL_TEXT_KINDS: frozenset[ColumnKind] = frozenset({"datetime", "time", "duration"})

_MICROSECONDS_PER_DAY = 86_400_000_000
_MICROSECONDS_PER_SECOND = 1_000_000
_NANOSECONDS_PER_MICROSECOND = 1_000


def temporal_text(expression: pl.Expr, dtype: pl.DataType, *, separator: str = "T") -> pl.Expr:
    """The exact text of *expression*, a datetime, time or duration of type *dtype*.

    *separator* joins a datetime's day and time: ``"T"`` for ISO 8601, as
    ``datetime.isoformat()`` writes it, or ``" "`` as ``str(datetime)`` does. A null
    stays null.
    """
    kind = column_kind(dtype)
    if kind == "datetime":
        text = expression.dt.to_string(f"%Y-%m-%d{separator}%H:%M:%S") + _fraction(expression)
        if cast(pl.Datetime, dtype).time_zone is None:
            return text
        return text + _offset(expression)
    if kind == "time":
        return expression.dt.to_string("%H:%M:%S") + _fraction(expression)
    if kind == "duration":
        return _duration_text(expression, cast(pl.Duration, dtype).time_unit)
    raise SchemaMismatchError(
        f"{dtype} is not a datetime, time or duration type.", dtype=str(dtype)
    )


def _fraction(expression: pl.Expr) -> pl.Expr:
    """A datetime's or time's part-second: none, six digits or nine."""
    nanoseconds = expression.dt.nanosecond().cast(pl.Int64)
    return (
        pl.when(nanoseconds % _NANOSECONDS_PER_MICROSECOND != 0)
        .then("." + _padded(nanoseconds, 9))
        .when(nanoseconds != 0)
        .then("." + _padded(nanoseconds.floordiv(_NANOSECONDS_PER_MICROSECOND), 6))
        .otherwise(pl.lit(""))
    )


def _offset(expression: pl.Expr) -> pl.Expr:
    """An aware datetime's offset, with its seconds when it has any (a zone's old local mean
    time, such as Kolkata's +05:21:10 in 1903), as ``isoformat()`` writes it."""
    offset = expression.dt.base_utc_offset() + expression.dt.dst_offset()
    return (
        pl.when(offset.dt.total_seconds() % 60 != 0)
        .then(expression.dt.to_string("%::z"))
        .otherwise(expression.dt.to_string("%:z"))
    )


def _padded(value: pl.Expr, width: int) -> pl.Expr:
    return value.cast(pl.String).str.zfill(width)


def _duration_text(expression: pl.Expr, unit: str) -> pl.Expr:
    """``str(timedelta)``'s layout, with three more digits for a part-microsecond.

    Built from the physical integer: a duration's own arithmetic would round the
    nanoseconds to the other operand's unit.
    """
    physical = expression.to_physical()
    if unit == "ns":
        microseconds = physical.floordiv(_NANOSECONDS_PER_MICROSECOND)
        nanoseconds = physical - microseconds * _NANOSECONDS_PER_MICROSECOND
    else:
        microseconds = physical * (1_000 if unit == "ms" else 1)
        nanoseconds = pl.lit(0, dtype=pl.Int64)
    # Whole days are floored, so the clock is never negative: "-1 day, 23:59:59".
    days = microseconds.floordiv(_MICROSECONDS_PER_DAY)
    clock = microseconds - days * _MICROSECONDS_PER_DAY
    seconds = clock.floordiv(_MICROSECONDS_PER_SECOND)
    fraction = clock - seconds * _MICROSECONDS_PER_SECOND
    day_text = (
        pl.when(days == 0)
        .then(pl.lit(""))
        .otherwise(
            days.cast(pl.String)
            + pl.when(days.abs() == 1).then(pl.lit(" day, ")).otherwise(pl.lit(" days, "))
        )
    )
    fraction_text = (
        pl.when(nanoseconds != 0)
        .then("." + _padded(fraction, 6) + _padded(nanoseconds, 3))
        .when(fraction != 0)
        .then("." + _padded(fraction, 6))
        .otherwise(pl.lit(""))
    )
    return (
        day_text
        + seconds.floordiv(3_600).cast(pl.String)
        + ":"
        + _padded(seconds.floordiv(60) % 60, 2)
        + ":"
        + _padded(seconds % 60, 2)
        + fraction_text
    )


__all__ = ["TEMPORAL_TEXT_KINDS", "temporal_text"]
