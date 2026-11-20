"""A deployed pipeline's response rows as JSON, one encoding for every response mode.

The generated container answers ``/quote`` with a JSON envelope or, when the client asks
for it, NDJSON. Both carry the rows :func:`haute._output_assembler.render_output_document`
renders from the OUTPUT node's frame (null fields and empty collections left out, a
null inside an array kept), encoded here, so a client sees the same rows whichever it asks
for: changing ``Accept`` changes the framing and the row limit, never a row's content.
Every response row is rendered by :func:`response_rows`, which refuses a value JSON cannot
carry by its column and quote before anything is encoded.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import polars as pl
from polars.datatypes import DataTypeClass

from haute._logging import get_logger
from haute._output_assembler import render_output_document
from haute.errors import NonFiniteOutputError

logger = get_logger(component="deploy.response")


def served_temporal(value: object) -> object:
    """*value* as the response's JSON carries it when it is a date, a date-time or a time:
    its ISO 8601 text, the form a request carries one in. Any other value is returned as
    it is."""
    if isinstance(value, date | datetime | time):
        return value.isoformat()
    return value


def _encode_value(value: object) -> str:
    """A temporal value as its ISO 8601 text (:func:`served_temporal`), and a decimal as
    its exact digits, which no JSON number could hold without rounding."""
    if isinstance(value, date | datetime | time):
        return str(served_temporal(value))
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def response_json(content: Any) -> bytes:
    """*content* as compact UTF-8 JSON; a NaN or an infinity is refused, never sent."""
    return json.dumps(
        content,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=_encode_value,
    ).encode("utf-8")


def refuse_non_finite(frame: pl.DataFrame, *, first_quote: int | None = 0) -> None:
    """Refuse a NaN or an infinity in a deployment's answer *frame*, by column and quote.

    Raised as :class:`~haute.errors.NonFiniteOutputError` and logged with the same, before
    any byte is encoded: a JSON encoder would refuse it too, but naming neither.
    *first_quote* is the quote of the frame's first row when each row answers a quote, in
    request order (a batch, or one chunk of it); ``None`` when every row answers the one
    quote sent.
    """
    found = first_non_finite(frame)
    if found is None:
        return
    row, column, value = found
    quote = 0 if first_quote is None else first_quote + row
    logger.warning("output_not_finite", column=column, quote=quote, value=value)
    raise NonFiniteOutputError(column=column, quote=quote, value=value)


def response_rows(frame: pl.DataFrame, *, first_quote: int | None = 0) -> list[Any]:
    """*frame*'s rows as a response carries them, each value one JSON can hold
    (:func:`refuse_non_finite`)."""
    refuse_non_finite(frame, first_quote=first_quote)
    return render_output_document(frame)


def ndjson_rows(frame: pl.DataFrame, *, first_quote: int | None = 0) -> bytes:
    """*frame*'s response rows (:func:`response_rows`), one JSON line each, in order."""
    return b"".join(
        response_json(row) + b"\n" for row in response_rows(frame, first_quote=first_quote)
    )


def _holds_float(dtype: pl.DataType | DataTypeClass) -> bool:
    if dtype.is_float():
        return True
    if isinstance(dtype, pl.Struct):
        return any(_holds_float(field.dtype) for field in dtype.fields)
    if isinstance(dtype, pl.List | pl.Array):
        return _holds_float(dtype.inner)
    return False


def first_non_finite(frame: pl.DataFrame) -> tuple[int, str, str] | None:
    """The first row holding a NaN or an infinity, its column's path to it, and the value.

    A flat float column is checked by Polars; a struct or a list holding floats is walked.
    """
    first: tuple[int, str, str] | None = None
    for name, dtype in frame.schema.items():
        if not _holds_float(dtype):
            continue
        column = frame.get_column(name)
        found: tuple[int, str, str] | None
        if dtype.is_float():
            bad = (column.is_nan() | column.is_infinite()).fill_null(False).arg_true()
            found = None if bad.is_empty() else (int(bad[0]), name, str(column[int(bad[0])]))
        else:
            found = _first_nested_non_finite(column.to_list(), name)
        if found is not None and (first is None or found[0] < first[0]):
            first = found
    return first


def _first_nested_non_finite(values: list[Any], name: str) -> tuple[int, str, str] | None:
    for row, value in enumerate(values):
        path = _non_finite_path(value)
        if path is not None:
            where, number = path
            return row, name + where, str(number)
    return None


def _non_finite_path(value: object) -> tuple[str, float] | None:
    """The path into *value* to its first NaN or infinity, and that number."""
    if isinstance(value, float):
        return None if math.isfinite(value) else ("", value)
    if isinstance(value, dict):
        for key, item in value.items():
            found = _non_finite_path(item)
            if found is not None:
                return f".{key}{found[0]}", found[1]
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _non_finite_path(item)
            if found is not None:
                return f"[{index}]{found[0]}", found[1]
    return None
