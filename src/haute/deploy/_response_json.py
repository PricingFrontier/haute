"""A deployed pipeline's response rows as JSON, one encoding for every response mode.

The generated container answers ``/quote`` with a JSON envelope or, when the client asks
for it, NDJSON. Both carry the rows :func:`haute._output_assembler.render_output_document`
renders from the OUTPUT node's frame (null fields and empty collections left out, a
null inside an array kept), encoded here, so a client sees the same rows whichever it asks
for: changing ``Accept`` changes the framing and the row limit, never a row's content.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import polars as pl

from haute._output_assembler import render_output_document


def _encode_value(value: object) -> str:
    """A temporal value as its ISO 8601 text, the form a request carries one in, and a
    decimal as its exact digits, which no JSON number could hold without rounding."""
    if isinstance(value, date | datetime | time):
        return value.isoformat()
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


def ndjson_rows(frame: pl.DataFrame) -> bytes:
    """*frame*'s rendered rows, one JSON line each, in order."""
    return b"".join(response_json(row) + b"\n" for row in render_output_document(frame))
