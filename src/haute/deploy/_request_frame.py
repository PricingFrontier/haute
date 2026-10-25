"""A flat Quote Input's served request, framed by the manifest's input schema (specs/deploy).

The request is typed by the columns and types the deploy recorded from the sample, never by
inference from the JSON values, so it types its columns as the dry run and the test quotes
did. A key the schema does not name is ignored; a column a quote leaves out is a typed null.
A value its declared type does not hold is refused naming the column and the quote
(:func:`haute._json_values.json_value` has the rules).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import polars as pl

from haute._json_values import REQUEST_DTYPES, JsonValueError, json_value
from haute._polars_dtypes import parse_rendered_dtype
from haute.errors import DeployError, QuoteInputError


def request_frame_schema(input_schema: Mapping[str, str]) -> dict[str, pl.DataType]:
    """The manifest's rendered *input_schema* as dtypes a request can be framed by.

    Raises:
        DeployError: a column's type is not one a JSON request can carry.
    """
    schema: dict[str, pl.DataType] = {}
    for column, rendered in input_schema.items():
        dtype = parse_rendered_dtype(rendered, column=column)
        if dtype.base_type() not in REQUEST_DTYPES:
            raise DeployError(
                f"The request input's column {column!r} is {rendered}, which a JSON request "
                "cannot carry. Give the input's sample file that column as a number, string, "
                "boolean, date or date-time.",
                column=column,
                dtype=rendered,
            )
        schema[column] = dtype
    return schema


def frame_rows(
    rows: Sequence[Mapping[str, Any]], schema: Mapping[str, pl.DataType]
) -> pl.DataFrame:
    """*rows*, a request's quotes, as one frame of *schema*'s columns and types.

    Raises:
        QuoteInputError: a quote is not an object, or a value does not fit its column.
    """
    columns: dict[str, list[Any]] = {name: [] for name in schema}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise QuoteInputError(f"Quote {index} is not an object.", column=None, quote=index)
        for name, dtype in schema.items():
            value = row.get(name)
            try:
                columns[name].append(json_value(value, dtype))
            except JsonValueError:
                raise QuoteInputError(
                    f"Quote {index}'s {name!r} is {_shown(value)}, which its {dtype} column "
                    "does not hold.",
                    column=name,
                    quote=index,
                ) from None
    return pl.DataFrame(columns, schema=dict(schema))


def _shown(value: object) -> str:
    """*value* as a refusal shows it: its JSON spelling's repr, cut short."""
    text = repr(value)
    return text if len(text) <= 40 else text[:39] + "…"
