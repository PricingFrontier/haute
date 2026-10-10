"""The Workbench Output: the response's tables, filled from the frames connected to them.

A Workbench Output's ``tables`` are a copy of the response's tables the project's
workbench supplies (specs/workbench), as :mod:`haute._workbench_tables` holds them. Each
table with a column is a port: the frame connected to it fills it, each column from the
frame's column the ``mapping`` picks or, without a pick, the frame's column of the same
name, given its declared type. A one-row table holds the quote's one row and a many-row
table any number. The node's result is its tables; where the pipeline answers a request
they become the response for one quote, each table under its name, as a quote holds its
own tables.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from haute._polars_utils import execution_collect, limited_python_scan
from haute._workbench_tables import (
    WorkbenchTable,
    parse_workbench_tables,
    port_tables,
    quote_schema,
)
from haute.errors import ExecutionError


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


def workbench_output_mapping(
    config: Mapping[str, Any], tables: tuple[WorkbenchTable, ...]
) -> dict[str, dict[str, str | None]]:
    """The frame column each table column's ``mapping`` entry picks, by table and column.

    An entry is a frame column's name, or ``None`` for none; a column without one is filled
    by name. An entry for a table or column the tables do not have fails.
    """
    mapping = config.get("mapping") or {}
    if not isinstance(mapping, dict):
        raise WorkbenchOutputError(
            "The Workbench Output's mapping must be an object of tables, each an object of columns."
        )
    columns_by_table = {table.name: {c.name for c in table.columns} for table in tables}
    picks: dict[str, dict[str, str | None]] = {}
    for label, entries in mapping.items():
        if label not in columns_by_table:
            raise WorkbenchOutputError(
                f"The Workbench Output's mapping has a {label!r} table, which is none of its "
                f"tables ({', '.join(columns_by_table)})."
            )
        if not isinstance(entries, dict):
            raise WorkbenchOutputError(
                f"The Workbench Output's mapping for its {label!r} table must be an object of "
                "columns."
            )
        for column, source in entries.items():
            if column not in columns_by_table[label]:
                raise WorkbenchOutputError(
                    f"The Workbench Output's mapping has a {column!r} column in its {label!r} "
                    "table, which has no such column."
                )
            if source is not None and (not isinstance(source, str) or not source):
                raise WorkbenchOutputError(
                    f"The Workbench Output's mapping for {label!r}.{column!r} must name a "
                    "column, or be null for none."
                )
        picks[label] = dict(entries)
    return picks


def _fits(declared: str, dtype: pl.DataType) -> bool:
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
    frames: Mapping[str, pl.LazyFrame],
    tables: tuple[WorkbenchTable, ...],
    mapping: Mapping[str, Mapping[str, str | None]],
) -> WorkbenchOutputTables:
    """Each table filled from the frame its port has in *frames*, through *mapping*.

    Nothing is collected: a one-row table checks it has one row when it is read.
    """
    filled: dict[str, pl.LazyFrame] = {}
    for table in tables:
        frame = frames.get(table.name)
        if frame is None:
            raise WorkbenchOutputError(
                f"Connect a frame to the Workbench Output's {table.name!r} table."
            )
        selection = _filled(table, frame, mapping.get(table.name, {}))
        filled[table.name] = _exactly_one_row(table, selection) if table.one_row else selection
    return WorkbenchOutputTables(filled, tables)


def _filled(
    table: WorkbenchTable, frame: pl.LazyFrame, picks: Mapping[str, str | None]
) -> pl.LazyFrame:
    """*table*'s columns from *frame*: each from its source, cast to its declared type."""
    schema = frame.collect_schema()
    columns: list[pl.Expr] = []
    for column in table.columns:
        if column.name in picks:
            source = picks[column.name]
            if source is not None and source not in schema:
                raise WorkbenchOutputError(
                    f"The Workbench Output's {table.name!r} table fills {column.name!r} from "
                    f"{source!r}, which the frame connected to it doesn't have; it has "
                    f"{', '.join(schema.names()) or 'none'}. Pick another in its mapping."
                )
        else:
            source = column.name if column.name in schema else None
        if source is None:
            # Nothing fills it: a column of nulls, as long as the frame.
            columns.append(pl.lit(None, dtype=column.dtype).alias(column.name))
            continue
        if not _fits(column.type, schema[source]):
            raise WorkbenchOutputError(
                f"The Workbench Output's {table.name!r} table declares {column.name!r} as "
                f"{column.type}, but {source!r}, which fills it, is {schema[source]}. Convert "
                "it upstream, pick another column, or change its type in the workbench."
            )
        columns.append(pl.col(source).cast(column.dtype).alias(column.name))
    # Added beside the frame's own columns, so a literal takes the frame's length and two
    # picks may swap names; then cut to the table's columns, in order.
    return frame.with_columns(columns).select([column.name for column in table.columns])


def _exactly_one_row(table: WorkbenchTable, selection: pl.LazyFrame) -> pl.LazyFrame:
    """*selection*, failing when it is read with other than exactly one row."""

    def produce(_row_limit: int | None) -> pl.DataFrame:
        rows = execution_collect(selection)
        if rows.height != 1:
            held = "no rows" if rows.height == 0 else f"{rows.height} rows"
            raise WorkbenchOutputError(
                f"The Workbench Output's {table.name!r} table has one row per quote, but the "
                f"frame connected to it has {held}. A Workbench Output answers one quote per "
                "request."
            )
        return rows

    return limited_python_scan(produce, schema=selection.collect_schema())


def workbench_response(filled: WorkbenchOutputTables) -> pl.LazyFrame:
    """The response for one quote: each table under its name, as a quote holds its tables.

    A one-row table is an object of its columns and a many-row table a list of them. The
    schema is the tables' without collecting; the tables are read when the response is.
    """
    tables = filled.tables
    schema = quote_schema(tables)

    def produce(_row_limit: int | None) -> pl.DataFrame:
        document: dict[str, Any] = {}
        for table in tables:
            rows = execution_collect(filled[table.name])
            # A one-row table has been read with exactly its one row.
            document[table.name] = rows.row(0, named=True) if table.one_row else rows.to_dicts()
        return pl.DataFrame([document], schema=schema)

    return limited_python_scan(produce, schema=schema)


def as_response(result: Any) -> Any:
    """A response node's result as the frame a request is answered with.

    A Workbench Output's tables become their response; a Quote Response's result is its
    response already.
    """
    return workbench_response(result) if isinstance(result, WorkbenchOutputTables) else result
