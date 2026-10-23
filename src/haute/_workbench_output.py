"""The Workbench Output: the response's tables, filled from the frames connected to them.

A Workbench Output's ``tables`` are a copy of the response's tables the project's
workbench supplies (specs/workbench), as :mod:`haute._workbench_tables` holds them. Each
table with a column is a port: the frame connected to it fills it, each column from the
frame's column of exactly its name, given its declared type. A column the frame lacks or
holds with a type that does not fit, and a table nothing is connected to, fill with nulls,
so the pipeline runs while it is being built. A one-row table holds the quote's one row and
a many-row table any number. The node's result is its tables; where the pipeline answers a
request they become the response for one quote, each table under its name, as a quote holds
its own tables. A table leading with the quote's reference and option holds them blank in
the node's own tables, whatever the frame holds, and takes the quote's in the response: a
pipeline never carries them through.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from haute._polars_utils import execution_collect, limited_python_scan
from haute._workbench_form import QUOTE_DETAILS_ID
from haute._workbench_tables import (
    IDENTITY_SHAPE,
    WorkbenchTable,
    carries_identity,
    parse_workbench_tables,
    port_tables,
    quote_schema,
)
from haute.errors import ExecutionError

#: The identity columns' names, in a table's order.
_IDENTITY_NAMES = [name for name, _ in IDENTITY_SHAPE]


class WorkbenchOutputError(ExecutionError):
    """A Workbench Output that cannot fill its tables from the frames connected to it."""


class WorkbenchOutputTables(dict[str, pl.LazyFrame]):
    """A Workbench Output's result: its tables' frames by name, and the tables they fill.

    A ``dict`` of frames, as a multi-frame source's result is, so a preview shows each table
    and a walk keeps them as a bundle; ``tables`` lets :func:`workbench_response` build the
    response from them wherever the pipeline answers a request.
    """

    def __init__(self, frames: Mapping[str, pl.LazyFrame], tables: tuple[WorkbenchTable, ...]):
        super().__init__(frames)
        self.tables = tables


def workbench_output_tables(config: Mapping[str, Any]) -> tuple[WorkbenchTable, ...]:
    """The tables a Workbench Output fills, read from its *config*: those with a column."""
    tables = parse_workbench_tables(config.get("tables"), owner="Workbench Output")
    if not tables:
        raise WorkbenchOutputError(
            "This Workbench Output has no tables: add output tables to the workbench's "
            "schema and save it."
        )
    ports = port_tables(tables)
    if not ports:
        raise WorkbenchOutputError(
            "This Workbench Output's tables have no columns yet: add their columns in the "
            "workbench's schema and save it."
        )
    return ports


def column_fits(declared: str, dtype: pl.DataType) -> bool:
    """Whether a frame's column of *dtype* can fill a column declared *declared*."""
    if dtype == pl.Null:
        return True
    if declared == "int":
        return dtype.is_integer()
    if declared == "float":
        return dtype.is_numeric()
    if declared == "str":
        return dtype == pl.String or isinstance(dtype, (pl.Categorical, pl.Enum))
    if declared == "bool":
        return dtype == pl.Boolean
    return dtype == pl.Date


def fill_workbench_tables(
    frames: Mapping[str, pl.LazyFrame], tables: tuple[WorkbenchTable, ...]
) -> WorkbenchOutputTables:
    """Each table filled from the frame its port has in *frames*, by name.

    Nothing is collected: a connected one-row table checks it has one row when it is read.
    """
    filled: dict[str, pl.LazyFrame] = {}
    for table in tables:
        frame = frames.get(table.name)
        if frame is None:
            filled[table.name] = _unconnected(table)
            continue
        selection = _filled(table, frame)
        filled[table.name] = _exactly_one_row(table, selection) if table.one_row else selection
    return WorkbenchOutputTables(filled, tables)


def _unconnected(table: WorkbenchTable) -> pl.LazyFrame:
    """*table* with nothing connected: one row of nulls when it is one-row, else no rows."""
    rows = [None] if table.one_row else []
    return pl.LazyFrame(
        {column.name: rows for column in table.columns},
        schema={column.name: column.dtype for column in table.columns},
    )


def _filled(table: WorkbenchTable, frame: pl.LazyFrame) -> pl.LazyFrame:
    """*table*'s columns from *frame*, each from its column of the same name, cast to its
    declared type; nulls where the frame lacks it or holds it with a type that does not fit,
    and in the quote's identity, which the response gives (:func:`workbench_response`)."""
    schema = frame.collect_schema()
    blank = set(_IDENTITY_NAMES) if carries_identity(table) else set()
    columns: list[pl.Expr] = []
    for column in table.columns:
        if (
            column.name not in blank
            and column.name in schema
            and column_fits(column.type, schema[column.name])
        ):
            columns.append(pl.col(column.name).cast(column.dtype))
        else:
            columns.append(pl.lit(None, dtype=column.dtype).alias(column.name))
    # Added beside the frame's own columns, so a literal takes the frame's length (a select
    # of literals alone would be one row); then cut to the table's columns, in order.
    return frame.with_columns(columns).select([column.name for column in table.columns])


def _exactly_one_row(table: WorkbenchTable, selection: pl.LazyFrame) -> pl.LazyFrame:
    """*selection*, failing when it is read with other than exactly one row."""

    def produce(_row_limit: int | None) -> pl.DataFrame:
        rows = execution_collect(selection, engine="streaming")
        if rows.height != 1:
            held = "no rows" if rows.height == 0 else f"{rows.height} rows"
            raise WorkbenchOutputError(
                f"The Workbench Output's {table.name!r} table has one row per quote, but the "
                f"frame connected to it has {held}. A Workbench Output answers one quote per "
                "request."
            )
        return rows

    return limited_python_scan(produce, schema=selection.collect_schema())


def quote_identity(frames: object) -> pl.LazyFrame | None:
    """The quote's reference and option as the run's ``quote_details`` frame holds them, in
    a Workbench Input's frames by table (a request's, a source's or those given to score);
    None when *frames* hold no such frame."""
    if not isinstance(frames, Mapping):
        return None
    details = frames.get(QUOTE_DETAILS_ID)
    if not isinstance(details, (pl.DataFrame, pl.LazyFrame)):
        return None
    frame = details.lazy()
    if not set(_IDENTITY_NAMES) <= set(frame.collect_schema().names()):
        return None
    return frame.select(_IDENTITY_NAMES)


def workbench_response(
    filled: WorkbenchOutputTables, identity: pl.LazyFrame | None = None
) -> pl.LazyFrame:
    """The response for one quote: each table under its name, as a quote holds its tables.

    A one-row table is an object of its columns and a many-row table a list of them, every
    row of a table leading with the quote's reference and option holding *identity*'s, read
    from the run's ``quote_details`` (:func:`quote_identity`); blank without it. The schema
    is the tables' without collecting; the tables are read when the response is.
    """
    tables = filled.tables
    schema = quote_schema(tables)

    def produce(_row_limit: int | None) -> pl.DataFrame:
        named: dict[str, Any] = dict.fromkeys(_IDENTITY_NAMES)
        if identity is not None:
            held = execution_collect(identity, engine="streaming")
            if held.height != 1:
                raise WorkbenchOutputError(
                    f"The quote's {QUOTE_DETAILS_ID} has {held.height} rows; its reference and "
                    "option are read from its one row."
                )
            named = held.row(0, named=True)
        document: dict[str, Any] = {}
        for table in tables:
            rows = execution_collect(filled[table.name], engine="streaming").to_dicts()
            if carries_identity(table):
                rows = [{**row, **named} for row in rows]
            # A one-row table has been read with exactly its one row.
            document[table.name] = rows[0] if table.one_row else rows
        return pl.DataFrame([document], schema=schema)

    return limited_python_scan(produce, schema=schema)


def as_response(result: Any, identity: pl.LazyFrame | None = None) -> Any:
    """A response node's result as the frame a request is answered with.

    A Workbench Output's tables become their response, under the quote's *identity*
    (:func:`workbench_response`); a Quote Response's result is its response already.
    """
    if isinstance(result, WorkbenchOutputTables):
        return workbench_response(result, identity)
    return result
