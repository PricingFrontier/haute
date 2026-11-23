"""A deployment's request, read as its request input reads it (specs/deploy).

:class:`RequestReader` is the one reading of a request's quotes: the served app's, its
batch worker's, the Databricks model's and the dry run's, so a test quote is refused or
priced as the service refuses or prices it. A Workbench Input, and a Quote Input with
tables, read the quotes as their records; a flat Quote Input's are framed by
:func:`frame_rows`.

A flat Quote Input's served request is framed by the manifest's input schema.

The request is typed by the columns and types the deploy recorded from the sample, never by
inference from the JSON values, so it types its columns as the dry run and the test quotes
did. A key the schema does not name is ignored, and an explicit null is a typed null. A
column a quote leaves out is refused, naming the quote and the column, unless it is an
exposure (the manifest's ``omittable_inputs``): a quote without one is priced for a full
policy term. A value its declared type does not hold is refused naming the column and the
quote (:func:`haute._json_values.json_value` has the rules).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl

from haute._json_values import REQUEST_DTYPES, JsonValueError, json_value
from haute._polars_dtypes import parse_rendered_dtype
from haute.errors import DeployError, QuoteInputError

if TYPE_CHECKING:
    from haute._types import PipelineGraph
    from haute.deploy._scorer import DeployInput


@dataclass(frozen=True)
class RequestReader:
    """How a deployment's request input reads a request's quotes.

    *records* is whether it reads them as sent (a Workbench Input, a Quote Input with
    tables), each value typed by its reader; otherwise they are framed by *schema*, a
    quote leaving out only the *omittable* columns (:func:`frame_rows`).
    """

    records: bool
    schema: Mapping[str, pl.DataType]
    omittable: frozenset[str] = frozenset()

    @classmethod
    def of(
        cls,
        graph: PipelineGraph,
        input_node_ids: Sequence[str],
        recorded: Mapping[str, Any],
    ) -> RequestReader:
        """The reader of the deployment of *graph*. A flat Quote Input's frame is typed by
        *recorded*'s ``input_schema`` and may leave out its ``omittable_inputs``, as the
        manifest records them; a reader of records reads neither."""
        from haute.deploy._scorer import reads_records

        if reads_records(graph, list(input_node_ids)):
            return cls(records=True, schema={})
        omittable: Iterable[str] = recorded["omittable_inputs"]
        return cls(
            records=False,
            schema=request_frame_schema(recorded["input_schema"]),
            omittable=frozenset(omittable),
        )

    def read(self, rows: Sequence[Mapping[str, Any]], *, first: int = 0) -> DeployInput:
        """*rows*, a request's quotes, as the scorer takes them; a refusal numbers them
        from *first*, the first one's place among the quotes its caller counts.

        Raises:
            QuoteInputError: a flat Quote Input's quote that does not fit its schema.
        """
        from haute.deploy._scorer import QuoteRequest

        if self.records:
            return QuoteRequest(tuple(rows))
        return frame_rows(rows, self.schema, omittable=self.omittable, first=first)


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
    rows: Sequence[Mapping[str, Any]],
    schema: Mapping[str, pl.DataType],
    *,
    omittable: frozenset[str],
    first: int = 0,
) -> pl.DataFrame:
    """*rows*, a request's quotes, as one frame of *schema*'s columns and types.

    A quote may leave out only the *omittable* columns (exposures). A refusal names a
    quote by its place, counted from *first*.

    Raises:
        QuoteInputError: a quote is not an object, leaves out a column it must give, or
            holds a value that does not fit its column.
    """
    columns: dict[str, list[Any]] = {name: [] for name in schema}
    for index, row in enumerate(rows, start=first):
        if not isinstance(row, Mapping):
            raise QuoteInputError(f"Quote {index} is not an object.", column=None, quote=index)
        for name, dtype in schema.items():
            if name not in row and name not in omittable:
                raise QuoteInputError(
                    f"Quote {index} has no {name!r}. Every quote gives each column the input "
                    "names (null for a value it does not have); only an exposure may be left "
                    "out, priced as a full policy term.",
                    column=name,
                    quote=index,
                )
            value = row.get(name)
            try:
                columns[name].append(json_value(value, dtype))
            except JsonValueError as refused:
                held = (
                    f"finer than its {dtype} column holds"
                    if refused.finer
                    else f"which its {dtype} column does not hold"
                )
                raise QuoteInputError(
                    f"Quote {index}'s {name!r} is {_shown(value)}, {held}.",
                    column=name,
                    quote=index,
                ) from None
    # A date-time arrives as its column's physical integer, cast to the column exactly.
    physical = {
        name: pl.Int64() if dtype.base_type() is pl.Datetime else dtype
        for name, dtype in schema.items()
    }
    return pl.DataFrame(columns, schema=physical).cast(pl.Schema(dict(schema)))


def _shown(value: object) -> str:
    """*value* as a refusal shows it: its JSON spelling's repr, cut short."""
    text = repr(value)
    return text if len(text) <= 40 else text[:39] + "…"
