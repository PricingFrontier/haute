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

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any, TypeVar, cast

import polars as pl
from polars.datatypes import DataTypeClass

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


FrameT = TypeVar("FrameT", pl.DataFrame, pl.LazyFrame)


def with_temporal_text(frame: FrameT, *, separator: str = "T") -> FrameT:
    """*frame* with each datetime, time and duration column written as its exact text
    (:func:`temporal_text`), for a caller about to read its rows into Python: the one way
    Haute's own code collects a temporal column. A struct, list or array holding one is
    written field by field and element by element, a null struct staying null. Other
    columns are left as they are."""
    schema = frame.collect_schema() if isinstance(frame, pl.LazyFrame) else frame.schema
    texts = [
        _nested_text(pl.col(name), dtype, separator).alias(name)
        for name, dtype in schema.items()
        if _holds_temporal(dtype)
    ]
    return frame.with_columns(texts) if texts else frame


def _instance(dtype: pl.DataType | DataTypeClass) -> pl.DataType:
    """*dtype* as an instance: a nested type may hold its inner type as a class."""
    return dtype() if isinstance(dtype, DataTypeClass) else dtype


def _holds_temporal(dtype: pl.DataType) -> bool:
    """Whether *dtype* is, or holds at any depth, a datetime, time or duration."""
    if column_kind(dtype) in TEMPORAL_TEXT_KINDS:
        return True
    if isinstance(dtype, pl.Struct):
        return any(_holds_temporal(_instance(field.dtype)) for field in dtype.fields)
    if isinstance(dtype, pl.List | pl.Array):
        return _holds_temporal(_instance(dtype.inner))
    return False


def _nested_text(expression: pl.Expr, dtype: pl.DataType, separator: str) -> pl.Expr:
    """*expression* with every temporal value it holds written as its exact text."""
    if column_kind(dtype) in TEMPORAL_TEXT_KINDS:
        return temporal_text(expression, dtype, separator=separator)
    if isinstance(dtype, pl.Struct):
        fields = [
            _nested_text(
                expression.struct.field(field.name), _instance(field.dtype), separator
            ).alias(field.name)
            if _holds_temporal(_instance(field.dtype))
            else expression.struct.field(field.name)
            for field in dtype.fields
        ]
        # A struct built from a null struct's fields is a struct of nulls: keep it null.
        return pl.when(expression.is_null()).then(None).otherwise(pl.struct(fields))
    if isinstance(dtype, pl.List):
        return expression.list.eval(_nested_text(pl.element(), _instance(dtype.inner), separator))
    if isinstance(dtype, pl.Array):
        return expression.arr.eval(_nested_text(pl.element(), _instance(dtype.inner), separator))
    return expression


def first_value_finer_than_microseconds(frame: pl.DataFrame) -> tuple[str, int] | None:
    """The first top-level datetime, time or duration column of *frame* holding a value
    below a microsecond, with that value's row, or ``None``: what a Python value, which
    holds microseconds, would cut short. A caller that must hand rows on as Python values
    refuses them rather than lose the digits."""
    for name, dtype in frame.schema.items():
        if column_kind(dtype) not in TEMPORAL_TEXT_KINDS:
            continue
        if column_kind(dtype) != "time" and getattr(dtype, "time_unit", None) != "ns":
            continue
        below = frame.select(
            (pl.col(name).to_physical() % _NANOSECONDS_PER_MICROSECOND != 0).arg_true().first()
        ).item()
        if below is not None:
            return name, int(below)
    return None


class TemporalTextError(ValueError):
    """Text that names no value of its column's type, or one finer than the column holds."""


# A datetime's or time's text: its whole seconds (a datetime's day may be all of it), a
# part-second of up to nine digits, and an offset ("Z", "+05:30", "+05:21:10").
# The day and clock hold no fraction mark, so a part-second is always the fraction group's,
# after a full stop or a comma (both ISO 8601's), and its offset may be written with
# colons or without ("+00:00", "+0000"). Text of any other shape matches nothing and is
# refused: a part-second never reaches fromisoformat, which would cut it to six digits.
_TEMPORAL_PARTS = re.compile(
    r"(?P<whole>[^.,]*?(?:[0-9]{2}:?[0-9]{2}(?::?[0-9]{2})?)?)(?:[.,](?P<fraction>[0-9]+))?"
    r"(?P<offset>Z|[+-][0-9]{2}(?::?[0-9]{2}(?::?[0-9]{2})?)?)?"
)
# A duration's text as temporal_text writes it, str(timedelta)'s layout to nanoseconds.
_DURATION_PARTS = re.compile(
    r"(?:(?P<days>-?[0-9]+) days?, )?(?P<hours>[0-9]+):(?P<minutes>[0-9]{2}):"
    r"(?P<seconds>[0-9]{2})(?:\.(?P<fraction>[0-9]+))?"
)
_NAIVE_EPOCH = datetime(1970, 1, 1)
_AWARE_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_UNITS_PER_SECOND = {"ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}
_NANOSECONDS_PER_SECOND = 1_000_000_000


def temporal_physical(text: str, dtype: pl.DataType) -> int:
    """The physical integer of the value *text* names in a column of type *dtype*, a
    datetime, time or duration: exactly, never through a Python ``datetime``, ``time`` or
    ``timedelta``, which hold microseconds.

    *text* is the column's value as :func:`temporal_text` writes it, or as ISO 8601 writes
    a datetime or time (``T`` or a space between day and time; seconds and a part-second of
    up to nine digits optional). An aware column's value names its instant through its
    offset, so it needs one, and a naive column's has none. Raises
    :class:`TemporalTextError` for text that names no such value, and for a part-second
    finer than the column holds (``Datetime("us")`` holds six digits), which reading it
    would cut short.
    """
    kind = column_kind(dtype)
    if kind == "duration":
        return _duration_physical(text, cast(pl.Duration, dtype).time_unit)
    if kind not in ("datetime", "time"):
        raise TemporalTextError(f"{dtype} is not a datetime, time or duration type.")
    parts = _TEMPORAL_PARTS.fullmatch(text.strip())
    if parts is None or not parts["whole"]:
        raise TemporalTextError(f"{text!r} is not a {kind}.")
    digits = parts["fraction"] or ""
    unit = "ns" if kind == "time" else cast(pl.Datetime, dtype).time_unit
    per_second = _UNITS_PER_SECOND[unit]
    if len(digits.rstrip("0")) > len(str(per_second)) - 1:
        raise TemporalTextError(
            f"{text!r} is finer than a {unit} {kind} holds: reading it would cut it short."
        )
    fraction = _fraction_units(digits, per_second)
    try:
        if kind == "time":
            if parts["offset"]:
                raise TemporalTextError(f"{text!r} has a time zone, which a time has not.")
            clock = time.fromisoformat(parts["whole"])
            seconds = (clock.hour * 60 + clock.minute) * 60 + clock.second
            return seconds * per_second + fraction
        moment = datetime.fromisoformat(parts["whole"] + (parts["offset"] or ""))
    except ValueError as exc:
        if isinstance(exc, TemporalTextError):
            raise
        raise TemporalTextError(f"{text!r} is not a {kind}: {exc}.") from None
    aware_column = cast(pl.Datetime, dtype).time_zone is not None
    if (moment.tzinfo is not None) != aware_column:
        raise TemporalTextError(
            f"{text!r} {'has no' if aware_column else 'has a'} time zone, which the column "
            f"{'has' if aware_column else 'has not'}."
        )
    # An aware value names its instant through its offset, so both occurrences of a local
    # time the clocks repeat keep their own: the physical value is UTC.
    epoch = _AWARE_EPOCH if aware_column else _NAIVE_EPOCH
    return ((moment - epoch) // timedelta(seconds=1)) * per_second + fraction


def _duration_physical(text: str, unit: str) -> int:
    parts = _DURATION_PARTS.fullmatch(text.strip())
    if parts is None:
        raise TemporalTextError(f"{text!r} is not a duration.")
    per_second = _UNITS_PER_SECOND[unit]
    digits = parts["fraction"] or ""
    if len(digits.rstrip("0")) > len(str(per_second)) - 1:
        raise TemporalTextError(
            f"{text!r} is finer than a {unit} duration holds: reading it would cut it short."
        )
    fraction = _fraction_units(digits, per_second)
    seconds = (
        _matched_digits(parts["days"] or "0") * 86_400
        + _matched_digits(parts["hours"]) * 3_600
        + _matched_digits(parts["minutes"]) * 60
        + _matched_digits(parts["seconds"])
    )
    return seconds * per_second + fraction


def _fraction_units(digits: str, per_second: int) -> int:
    """A part-second's digits as whole units of a column holding *per_second* a second."""
    width = len(str(per_second)) - 1
    return _matched_digits(digits.ljust(width, "0")[:width] or "0")


def _matched_digits(digits: str) -> int:
    """Digits, a sign allowed, that a pattern of this module matched in a value's text."""
    return int(digits)


def temporal_literal(text: str, dtype: pl.DataType) -> pl.Expr:
    """The value *text* names as a literal of *dtype*, exactly (:func:`temporal_physical`):
    for a filter or a match on a temporal column."""
    return pl.lit(temporal_physical(text, dtype), dtype=pl.Int64).cast(dtype)


def temporal_series(name: str, texts: Sequence[str | None], dtype: pl.DataType) -> pl.Series:
    """The values *texts* name as a column of *dtype*, exactly (:func:`temporal_physical`);
    ``None`` is a null."""
    physical = [None if text is None else temporal_physical(text, dtype) for text in texts]
    return pl.Series(name, physical, dtype=pl.Int64).cast(dtype)


def temporal_frame(rows: Sequence[Mapping[str, Any]], schema: pl.Schema) -> pl.DataFrame:
    """*rows* as a frame of *schema*, each datetime, time and duration value, at any depth,
    read from its exact text (:func:`with_temporal_text`'s) as its column's own value
    (:func:`temporal_physical`): the way back for rows read as text. A key a row or an object
    leaves out is a null, as Polars reads such rows; any other value is taken as it is."""
    temporal = {name: dtype for name, dtype in schema.items() if _holds_temporal(dtype)}
    if not temporal:
        return pl.DataFrame(rows, schema=schema)
    physical_rows = [
        {
            name: _physical_value(value, temporal[name]) if name in temporal else value
            for name, value in row.items()
        }
        for row in rows
    ]
    physical_schema = {
        name: _physical_dtype(dtype) if name in temporal else dtype
        for name, dtype in schema.items()
    }
    return pl.DataFrame(physical_rows, schema=physical_schema).cast(temporal)  # type: ignore[arg-type]


def _physical_dtype(dtype: pl.DataType) -> pl.DataType:
    """*dtype* with each datetime, time and duration it holds as its physical ``Int64``."""
    if column_kind(dtype) in TEMPORAL_TEXT_KINDS:
        return pl.Int64()
    if isinstance(dtype, pl.Struct):
        return pl.Struct(
            {field.name: _physical_dtype(_instance(field.dtype)) for field in dtype.fields}
        )
    if isinstance(dtype, pl.List):
        return pl.List(_physical_dtype(_instance(dtype.inner)))
    if isinstance(dtype, pl.Array):
        # The inner type holds the dimensions past the first: give the first alone.
        return pl.Array(_physical_dtype(_instance(dtype.inner)), dtype.size)
    return dtype


def _physical_value(value: Any, dtype: pl.DataType) -> Any:
    """*value*, of *dtype*, with each temporal text it holds as that value's physical integer."""
    if value is None:
        return None
    if column_kind(dtype) in TEMPORAL_TEXT_KINDS:
        return temporal_physical(value, dtype)
    if isinstance(dtype, pl.Struct) and isinstance(value, Mapping):
        fields = {field.name: _instance(field.dtype) for field in dtype.fields}
        return {
            key: _physical_value(item, fields[key]) if key in fields else item
            for key, item in value.items()
        }
    if isinstance(dtype, pl.List | pl.Array) and isinstance(value, list | tuple):
        return [_physical_value(item, _instance(dtype.inner)) for item in value]
    return value


__all__ = [
    "TEMPORAL_TEXT_KINDS",
    "TemporalTextError",
    "first_value_finer_than_microseconds",
    "temporal_frame",
    "temporal_literal",
    "temporal_physical",
    "temporal_series",
    "temporal_text",
    "with_temporal_text",
]
