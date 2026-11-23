"""The exact text of a datetime, time or duration value, written by Polars, and back.

Python's ``datetime``, ``time`` and ``timedelta`` hold microseconds, while a Polars
``Datetime("ns")``, a ``Duration("ns")`` and every ``Time`` hold nanoseconds, so a
value collected into Python loses what lies below a microsecond and two values that
differ only there become one. Polars' own ``cast(pl.String)`` is no answer: it drops
a ``Time``'s whole fraction and refuses a ``Duration``. :func:`temporal_text` writes
the text inside the query instead, at the column's own precision, in Python's
spelling extended to nanoseconds: no fraction for a whole second, six digits for a
whole microsecond and nine otherwise; an aware datetime carries its offset, and a
duration reads as ``str(timedelta)`` does.

This module is the one way such a value reaches Haute's own Python code
(:func:`exact_text`, :func:`exact_rows`, :func:`exact_row`) and the one way its text
becomes a value of its column again (:func:`temporal_physical`,
:func:`temporal_literal`), refused when the column cannot hold it (specs/io-layer).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal, TypeVar, cast

import polars as pl
from polars.datatypes import DataTypeClass

from haute._polars_dtypes import ColumnKind, column_kind
from haute.errors import SchemaMismatchError

#: The column kinds whose values Python cannot hold exactly, so reach it as text.
TEMPORAL_TEXT_KINDS: frozenset[ColumnKind] = frozenset({"datetime", "time", "duration"})

_MICROSECONDS_PER_DAY = 86_400_000_000
_MICROSECONDS_PER_SECOND = 1_000_000
_NANOSECONDS_PER_MICROSECOND = 1_000
_NANOSECONDS_PER_SECOND = 1_000_000_000
_NANOSECONDS_PER_DAY = 86_400 * _NANOSECONDS_PER_SECOND
_UNITS_PER_SECOND = {"ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}
_NAIVE_EPOCH = datetime(1970, 1, 1)
_AWARE_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

FrameT = TypeVar("FrameT", pl.DataFrame, pl.LazyFrame)


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


# ---------------------------------------------------------------------------
# Reading a frame's values into Python
# ---------------------------------------------------------------------------


def is_temporal(dtype: pl.DataType) -> bool:
    """Whether a column of *dtype* is a datetime, time or duration, whose values reach Python
    only as their exact text."""
    return column_kind(dtype) in TEMPORAL_TEXT_KINDS


def holds_temporal(dtype: pl.DataType) -> bool:
    """Whether a column of *dtype* holds a datetime, time or duration value, at its top level
    or inside a struct, list or array."""
    return _exact_expression(pl.col("_"), dtype) is not None


def _exact_expression(expression: pl.Expr, held: pl.DataType | DataTypeClass) -> pl.Expr | None:
    """*expression*, of type *held*, with every temporal value in it written as its exact
    text; ``None`` when it holds none, so it is left as it is."""
    # A nested type's inner types may be classes: ``pl.List(pl.Time)`` holds ``Time``.
    dtype = held() if isinstance(held, type) else held
    if column_kind(dtype) in TEMPORAL_TEXT_KINDS:
        return temporal_text(expression, dtype)
    if isinstance(dtype, pl.Struct):
        fields = [
            (field.name, _exact_expression(expression.struct.field(field.name), field.dtype))
            for field in dtype.fields
        ]
        if all(written is None for _name, written in fields):
            return None
        rebuilt = pl.struct(
            [
                (expression.struct.field(name) if written is None else written).alias(name)
                for name, written in fields
            ]
        )
        # A null struct stays null rather than becoming a struct of nulls.
        return pl.when(expression.is_null()).then(None).otherwise(rebuilt)
    if isinstance(dtype, pl.List):
        inner = _exact_expression(pl.element(), dtype.inner)
        return None if inner is None else expression.list.eval(inner)
    if isinstance(dtype, pl.Array):
        inner = _exact_expression(pl.element(), dtype.inner)
        return None if inner is None else expression.arr.eval(inner)
    return None


def exact_text_expression(expression: pl.Expr, dtype: pl.DataType) -> pl.Expr:
    """*expression*, of *dtype*, with every temporal value in it written as its exact text,
    nested ones included; *expression* itself when it holds none."""
    written = _exact_expression(expression, dtype)
    return expression if written is None else written


def exact_text_dtype(dtype: pl.DataType) -> pl.DataType:
    """The type :func:`exact_text_expression` gives a column of *dtype*: each temporal type
    in it a ``String``."""
    written = pl.LazyFrame(schema={"value": dtype}).select(
        exact_text_expression(pl.col("value"), dtype)
    )
    return written.collect_schema()["value"]


def exact_text(frame: FrameT) -> FrameT:
    """*frame* with every datetime, time and duration value written as its exact text
    (separator ``"T"``), at its top level or inside a struct, list or array; every other
    column as it is. Nothing is collected."""
    schema = frame.collect_schema()
    written = {
        name: expression
        for name, dtype in schema.items()
        if (expression := _exact_expression(pl.col(name), dtype)) is not None
    }
    if not written:
        return frame
    return frame.with_columns(expression.alias(name) for name, expression in written.items())


def exact_rows(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """*frame*'s rows by column name, each temporal value its exact text."""
    return exact_text(frame).to_dicts()


def exact_row(frame: pl.DataFrame, index: int) -> dict[str, Any]:
    """*frame*'s row *index* by column name, each temporal value its exact text."""
    return exact_text(frame.slice(index, 1)).row(0, named=True)


def _physical_dtype(held: pl.DataType | DataTypeClass) -> pl.DataType:
    """*held* with each temporal type in it, nested ones included, its physical ``Int64``."""
    dtype = held() if isinstance(held, type) else held
    if column_kind(dtype) in TEMPORAL_TEXT_KINDS:
        return pl.Int64()
    if isinstance(dtype, pl.Struct):
        return pl.Struct({field.name: _physical_dtype(field.dtype) for field in dtype.fields})
    if isinstance(dtype, pl.List):
        return pl.List(_physical_dtype(dtype.inner))
    if isinstance(dtype, pl.Array):
        # The inner type holds the dimensions past the first, so it takes the outer width.
        return pl.Array(_physical_dtype(dtype.inner), dtype.size)
    return dtype


def _physical_value(value: Any, held: pl.DataType | DataTypeClass) -> Any:
    """*value*, of a column of type *held* as :func:`exact_rows` reads it, with each temporal
    value in it, nested ones included, its physical integer (:func:`temporal_physical`)."""
    dtype = held() if isinstance(held, type) else held
    if value is None:
        return None
    if column_kind(dtype) in TEMPORAL_TEXT_KINDS:
        if not isinstance(value, str):
            raise TypeError(f"A {dtype} value read as exact text is a string, not {value!r}.")
        return temporal_physical(value, dtype)
    if isinstance(dtype, pl.Struct):
        return {
            field.name: _physical_value(value.get(field.name), field.dtype)
            for field in dtype.fields
        }
    if isinstance(dtype, pl.List):
        return [_physical_value(item, dtype.inner) for item in value]
    if isinstance(dtype, pl.Array):
        return [_physical_value(item, dtype.inner) for item in value]
    return value


def frame_from_exact_rows(
    rows: Sequence[Mapping[str, Any]], schema: Mapping[str, pl.DataType]
) -> pl.DataFrame:
    """*rows*, read as :func:`exact_rows` reads a frame's (each temporal value its exact text),
    back into a frame of *schema*, each temporal value exactly the one its text names:
    through its physical integer, never a Python ``datetime``, ``time`` or ``timedelta``."""
    temporal = [name for name, dtype in schema.items() if holds_temporal(dtype)]
    if not temporal:
        return pl.DataFrame(rows, schema=dict(schema))
    physical = [
        {
            **row,
            **{name: _physical_value(row.get(name), schema[name]) for name in temporal},
        }
        for row in rows
    ]
    physical_schema = {name: _physical_dtype(dtype) for name, dtype in schema.items()}
    # Not strict: every integer temporal_physical gives is a value of its type (a datetime
    # or duration any Int64, a time one inside its day), so nothing is nulled, and Polars'
    # strict cast refuses a multidimensional array holding a null row it would cast whole.
    return pl.DataFrame(physical, schema=physical_schema).cast(
        pl.Schema(dict(schema)), strict=False
    )


# ---------------------------------------------------------------------------
# Text back to a value of its column
# ---------------------------------------------------------------------------

Refusal = Literal["malformed", "finer", "zone", "range"]


class TemporalTextError(ValueError):
    """Text that is no value of its column: ``reason`` says why."""

    def __init__(self, message: str, *, reason: Refusal) -> None:
        super().__init__(message)
        self.reason: Refusal = reason


#: The fraction ending a clock, ``.`` or ``,`` and its digits: Python reads it as a fraction
#: of a second wherever the clock stops (``09:30.5`` is 09:30:00.5).
_CLOCK_FRACTION = re.compile(r"[.,](?P<digits>[0-9]+)\Z")
#: Where a time's offset begins: ``Z`` or a sign, neither of which a clock holds.
_OFFSET_START = re.compile(r"[Zz+-]")
#: A duration as :func:`temporal_text` writes one.
_DURATION = re.compile(
    r"(?:(?P<days>-?[0-9]+) days?, )?(?P<hours>[0-9]+):(?P<minutes>[0-5][0-9]):"
    r"(?P<seconds>[0-5][0-9])(?:\.(?P<digits>[0-9]{6}(?:[0-9]{3})?))?"
)


def _below_a_microsecond(text: str, kind: str, *, clock_start: int) -> tuple[str, int]:
    """*text* with its clock's fraction cut to the six digits Python reads, and the
    nanoseconds the digits past them add.

    The clock runs from *clock_start* (the character after a date-time's ``T`` or space, the
    start of a time) to its offset, if any, so a fraction inside an offset
    (``+01:00:00.5``) is the offset's, read by Python as it stands. A fraction of more than
    nine digits is finer than any column holds.
    """
    if clock_start >= len(text):
        return text, 0
    offset = _OFFSET_START.search(text, clock_start)
    clock_end = len(text) if offset is None else offset.start()
    fraction = _CLOCK_FRACTION.search(text[clock_start:clock_end])
    if fraction is None:
        return text, 0
    digits = fraction["digits"]
    if len(digits) > 9:
        raise TemporalTextError(
            f"{text!r} is finer than a nanosecond, which no {kind} holds.", reason="malformed"
        )
    if len(digits) <= 6:
        return text, 0
    cut = clock_start + fraction.start("digits") + 6
    return text[:cut] + text[clock_end:], int(digits[6:].ljust(3, "0"))


_DIGITS = frozenset("0123456789")


def _date_length(text: str) -> int:
    """How many characters of a date-time *text* its date takes, found as
    ``datetime.fromisoformat`` finds it (CPython's ``_find_isoformat_datetime_separator``):
    any one character may separate the date from the clock (``T``, ``t``, a space, ``_``),
    and the date is ``YYYY-MM-DD``, ``YYYYMMDD`` or a week date (``YYYY-Www-D``, ``YYYYWwwD``)."""
    if len(text) <= 7:
        return len(text)
    if text[4] == "-":
        if text[5] != "W":
            return 10
        if len(text) > 8 and text[8] == "-":
            # YYYY-Www-D, unless a digit at 10 says the hyphen at 8 is the separator.
            return 8 if len(text) > 10 and text[10] in _DIGITS else 10
        return 8
    if text[4] == "W":
        end = 7
        while end < len(text) and text[end] in _DIGITS:
            end += 1
        if end < 9:
            return end
        return 7 if end % 2 == 0 else 8
    return 8


def _date_time_parts(text: str) -> tuple[datetime, int]:
    """*text*, a date or date-time in any spelling ``datetime.fromisoformat`` accepts, as the
    moment Python reads to the microsecond and the nanoseconds below it."""
    whole, below = _below_a_microsecond(text, "date-time", clock_start=_date_length(text) + 1)
    try:
        return datetime.fromisoformat(whole), below
    except ValueError:
        raise TemporalTextError(f"{text!r} is not a date-time.", reason="malformed") from None


def _nanoseconds_from(moment: datetime, below: int) -> int:
    """*moment*'s nanoseconds from 1970, its instant in UTC when it is aware, its wall clock
    when it is naive, with *below* added: counted in whole microseconds, exactly."""
    epoch = _NAIVE_EPOCH if moment.tzinfo is None else _AWARE_EPOCH
    microseconds = (moment - epoch) // timedelta(microseconds=1)
    return microseconds * _NANOSECONDS_PER_MICROSECOND + below


def _finer(text: str, dtype: pl.DataType, unit: str) -> TemporalTextError:
    return TemporalTextError(
        f"{text!r} is finer than a {dtype} column holds ({unit}).", reason="finer"
    )


def temporal_physical(text: str, dtype: pl.DataType) -> int:
    """The physical integer a column of *dtype*, a datetime, time or duration, holds for
    *text*; :class:`TemporalTextError` when it holds none.

    A datetime is any spelling ``datetime.fromisoformat`` accepts, and a time any
    ``time.fromisoformat`` accepts, with the fraction of a second read whole to nine
    digits; a duration is the text :func:`temporal_text` writes. An aware datetime's
    physical value is its instant in UTC.
    """
    kind = column_kind(dtype)
    if kind == "datetime":
        return _datetime_physical(text, cast(pl.Datetime, dtype))
    if kind == "time":
        whole, below = _below_a_microsecond(text, "time", clock_start=0)
        try:
            clock = time.fromisoformat(whole)
        except ValueError:
            raise TemporalTextError(f"{text!r} is not a time.", reason="malformed") from None
        if clock.tzinfo is not None:
            raise TemporalTextError(
                f"{text!r} has a time zone, which a time has not.", reason="zone"
            )
        seconds = (clock.hour * 60 + clock.minute) * 60 + clock.second
        return (
            seconds * _NANOSECONDS_PER_SECOND
            + clock.microsecond * _NANOSECONDS_PER_MICROSECOND
            + below
        )
    if kind == "duration":
        return _duration_physical(text, cast(pl.Duration, dtype))
    raise SchemaMismatchError(
        f"{dtype} is not a datetime, time or duration type.", dtype=str(dtype)
    )


def _datetime_physical(text: str, dtype: pl.Datetime) -> int:
    moment, below = _date_time_parts(text)
    if moment.tzinfo is None and dtype.time_zone is not None:
        raise TemporalTextError(
            f"{text!r} has no time zone, which a {dtype} column has.", reason="zone"
        )
    if moment.tzinfo is not None and dtype.time_zone is None:
        raise TemporalTextError(
            f"{text!r} has a time zone, which a {dtype} column has not.", reason="zone"
        )
    # An aware value names its instant through its offset, so both occurrences of a
    # local time the clocks repeat keep their own: the physical value is UTC.
    nanoseconds = _nanoseconds_from(moment, below)
    step = _NANOSECONDS_PER_SECOND // _UNITS_PER_SECOND[dtype.time_unit]
    if nanoseconds % step:
        raise _finer(text, dtype, dtype.time_unit)
    return _in_range(nanoseconds // step, text, dtype)


def _in_range(physical: int, text: str, dtype: pl.DataType) -> int:
    """*physical*, refused when a column of *dtype*, 64 bits wide, cannot hold it."""
    if not -(2**63) <= physical < 2**63:
        raise TemporalTextError(
            f"{text!r} is outside the range a {dtype} column holds.", reason="range"
        )
    return physical


def _duration_physical(text: str, dtype: pl.Duration) -> int:
    match = _DURATION.fullmatch(text)
    if match is None:
        raise TemporalTextError(f"{text!r} is not a duration.", reason="malformed")
    seconds = (int(match["hours"]) * 60 + int(match["minutes"])) * 60 + int(match["seconds"])
    nanoseconds = (
        int(match["days"] or 0) * _NANOSECONDS_PER_DAY
        + seconds * _NANOSECONDS_PER_SECOND
        + int((match["digits"] or "").ljust(9, "0"))
    )
    step = _NANOSECONDS_PER_SECOND // _UNITS_PER_SECOND[dtype.time_unit]
    if nanoseconds % step:
        raise _finer(text, dtype, dtype.time_unit)
    return _in_range(nanoseconds // step, text, dtype)


def wall_clock_nanoseconds(text: str) -> int:
    """The nanoseconds from 1970 to the wall-clock time a date or date-time *text* names,
    any offset it carries left aside, as a breakpoint compares a zoned column's values;
    the fraction read whole to nine digits. :class:`TemporalTextError` for other text."""
    moment, below = _date_time_parts(text)
    return _nanoseconds_from(moment.replace(tzinfo=None), below)


def moment_nanoseconds(text: str) -> tuple[int, bool]:
    """The moment a date or date-time *text* names, in nanoseconds from 1970, and whether it
    is aware: an aware one's instant in UTC, a naive one's wall clock, the fraction read
    whole to nine digits, so two moments inside one microsecond order apart.
    :class:`TemporalTextError` for other text."""
    moment, below = _date_time_parts(text)
    return _nanoseconds_from(moment, below), moment.tzinfo is not None


def temporal_literal(text: str, dtype: pl.DataType) -> pl.Expr:
    """*text* as a literal of *dtype*, a datetime, time or duration, holding exactly the value
    it names (:func:`temporal_physical`); never through a Python ``datetime``, ``time`` or
    ``timedelta``, which would drop a nanosecond value's last three digits."""
    return pl.lit(temporal_physical(text, dtype), dtype=pl.Int64).cast(dtype)


__all__ = [
    "TEMPORAL_TEXT_KINDS",
    "TemporalTextError",
    "exact_row",
    "exact_rows",
    "exact_text",
    "exact_text_dtype",
    "exact_text_expression",
    "frame_from_exact_rows",
    "holds_temporal",
    "is_temporal",
    "moment_nanoseconds",
    "temporal_literal",
    "temporal_physical",
    "temporal_text",
    "wall_clock_nanoseconds",
]
