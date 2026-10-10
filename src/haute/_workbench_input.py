"""The Workbench Input: a quote's tables, read as dataframes (specs/workbench).

Its ``tables`` are a copy of the workbench's input tables and its ``sample`` the sample quote
typed while building, as a request holds one: each table under its name, an object for a
one-row table and a list of objects for a many-row one. It reads no file and walks no JSON
path: the quote's shape is the tables'. :func:`read_quote` gives one frame per port, each
column typed as declared, and refuses a part of the quote that is not of its table's shape
or a value that is not of its column's type, naming it. Without a request, in editor runs
and previews and in generated code, :func:`workbench_table_frames` reads the sample as a
request is read, so a sample prices as the quote it stands for would when deployed; while
there is no sample, every table is one row of nulls. A deployed pipeline reads the request
through :func:`workbench_request_frames`, from the records as they were sent.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import polars as pl

from haute._workbench_tables import (
    WorkbenchColumn,
    WorkbenchTable,
    WorkbenchTablesError,
    parse_workbench_tables,
    port_tables,
)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class WorkbenchInputError(WorkbenchTablesError):
    """A Workbench Input that cannot give its frames: no port, or a quote that does not fit.

    A public contract error of its own code: a preview's worker reports it by its class.
    """

    error_code = "workbench_input_invalid"


def workbench_input_tables(config: Mapping[str, Any]) -> tuple[WorkbenchTable, ...]:
    """A Workbench Input's ports, read from its *config*: its tables with columns, in order.

    Read from the tables alone, never the sample, so a sample that does not fit stops
    nothing that only needs the ports.
    """
    tables = parse_workbench_tables(config.get("tables"), owner="Workbench Input")
    if not tables:
        raise WorkbenchInputError(
            "This Workbench Input has no tables: add input tables to the workbench's schema "
            "and save it."
        )
    ports = port_tables(tables)
    if not ports:
        raise WorkbenchInputError(
            "This Workbench Input's tables have no columns yet: add their columns in the "
            "workbench's schema and save it."
        )
    return ports


def workbench_table_labels(config: Mapping[str, Any]) -> tuple[str, ...]:
    """A Workbench Input's ports' labels: its tables' names, in schema order."""
    return tuple(table.name for table in workbench_input_tables(config))


def read_quote(
    tables: Sequence[WorkbenchTable], quote: object, *, what: str = "the quote"
) -> dict[str, pl.DataFrame]:
    """Each of *tables* as the frame *quote* gives it, typed as declared.

    A one-row table is its one row, of nulls when the quote does not hold it; a many-row
    table is the rows the quote holds, none when it does not. A part that is not of its
    table's shape, or a value that is not of its column's type, raises
    :class:`WorkbenchInputError` naming it, with *what* naming the quote itself; a key the
    tables do not name is not read, and a column a row does not hold is null.
    """
    if not isinstance(quote, dict):
        raise WorkbenchInputError(f"{what} is {_kind(quote)}, not an object")
    return {table.name: _frame(table, _rows(table, quote.get(table.name))) for table in tables}


def _rows(table: WorkbenchTable, part: object) -> list[Mapping[str, Any]]:
    if part is None:
        return [{}] if table.one_row else []
    if table.one_row:
        if not isinstance(part, dict):
            raise WorkbenchInputError(f"{table.name} is {_kind(part)}, not an object")
        return [part]
    if not isinstance(part, list):
        raise WorkbenchInputError(f"{table.name} is {_kind(part)}, not a list")
    for index, row in enumerate(part):
        if not isinstance(row, dict):
            raise WorkbenchInputError(f"{table.name}[{index}] is {_kind(row)}, not a row")
    return part


def _frame(table: WorkbenchTable, rows: Sequence[Mapping[str, Any]]) -> pl.DataFrame:
    columns = {
        column.name: [
            _value(column, row.get(column.name), _spot(table, column, index))
            for index, row in enumerate(rows)
        ]
        for column in table.columns
    }
    return pl.DataFrame(columns, schema=table.frame_schema)


def _spot(table: WorkbenchTable, column: WorkbenchColumn, index: int) -> str:
    """Where a value sits in a quote: ``policy.limit``, or ``items[2].value`` in a list."""
    row = "" if table.one_row else f"[{index}]"
    return f"{table.name}{row}.{column.name}"


def _value(column: WorkbenchColumn, value: object, spot: str) -> Any:
    """*value* as the column's type holds it, or a refusal naming *spot* and the type."""
    if value is None:
        return None
    kind = column.type
    if kind == "str":
        fits = isinstance(value, str)
    elif kind == "bool":
        fits = isinstance(value, bool)
    elif kind == "int":
        fits = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "float":
        fits = isinstance(value, int | float) and not isinstance(value, bool)
        if fits:
            value = float(value)  # type: ignore[arg-type]
            fits = math.isfinite(value)
    else:
        fits = isinstance(value, str) and _ISO_DATE.fullmatch(value) is not None
        if fits:
            try:
                value = date.fromisoformat(value)  # type: ignore[arg-type]
            except ValueError:
                fits = False
    if not fits:
        raise WorkbenchInputError(f"{spot} is {_shown(value)}, not {column.type!r}")
    return value


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, list):
        return "a list"
    return "a value"


def _shown(value: object) -> str:
    """*value* as a refusal shows it: its repr, cut short."""
    text = repr(value)
    return text if len(text) <= 40 else text[:39] + "…"


def null_quote(tables: Sequence[WorkbenchTable]) -> dict[str, Any]:
    """One quote holding *tables* with nothing filled in: one row of nulls each.

    What the previews run on while the workbench has supplied no sample, and what deploy's
    dry run reads, so it checks the pipeline on those rows.
    """
    return {
        table.name: (
            {column.name: None for column in table.columns}
            if table.one_row
            else [{column.name: None for column in table.columns}]
        )
        for table in tables
    }


def workbench_table_frames(
    config: Mapping[str, Any],
    *,
    port_columns: Mapping[str, frozenset[str] | set[str] | None] | None = None,
) -> dict[str, pl.LazyFrame]:
    """A Workbench Input's frames without a request: its sample's rows, typed as declared.

    The sample, the copy of the quote the workbench supplies, is read as a request is, so
    the sample prices as the quote it stands for would when deployed: a many-row table it
    does not hold has no rows, and a one-row table its one row of nulls. While there is no
    sample (none, or nothing typed in it), every table is one row of nulls, the
    :func:`null_quote`, so the pipeline previews before the workbench supplies values. The
    sample is read whole, every port and every column whatever *port_columns* asks for, and
    only then cut to it, so every reader finds the same misfit.
    """
    tables = workbench_input_tables(config)
    sample = config.get("sample")
    try:
        read = read_quote(
            tables,
            null_quote(tables) if sample is None or sample == {} else sample,
            what="the sample",
        )
    except WorkbenchInputError as exc:
        raise WorkbenchInputError(
            f"The workbench's sample does not fit this Workbench Input's tables: {exc}. "
            "Correct it in the workbench and save it."
        ) from exc
    return {
        table.name: read[table.name].select(columns).lazy()
        for table, columns in _demanded(tables, port_columns)
    }


def workbench_request_frames(
    config: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> dict[str, pl.DataFrame]:
    """A deployed pipeline's frames: the one quote in *records*, read through the tables.

    *records* are the request's quotes as they were sent, one object each, untyped: the
    reader types each value as its column holds it and refuses a misfit naming the spot,
    where a frame built from them by inference would refuse it as Polars does. A request of
    several quotes is refused, as a Workbench Output answers one quote per request.
    """
    tables = workbench_input_tables(config)
    if len(records) != 1:
        raise WorkbenchInputError(
            f"A Workbench Input reads one quote per request, and this request holds {len(records)}."
        )
    try:
        return read_quote(tables, records[0], what="the request")
    except WorkbenchInputError as exc:
        raise WorkbenchInputError(
            f"The request does not fit this Workbench Input's tables: {exc}."
        ) from exc


def _demanded(
    tables: Sequence[WorkbenchTable],
    port_columns: Mapping[str, frozenset[str] | set[str] | None] | None,
) -> list[tuple[WorkbenchTable, list[str]]]:
    """The ports asked for, each with the columns it needs in schema order; all without a demand."""
    if port_columns is None:
        return [(table, [column.name for column in table.columns]) for table in tables]
    by_name = {table.name: table for table in tables}
    demanded: list[tuple[WorkbenchTable, list[str]]] = []
    for name, requested in port_columns.items():
        table = by_name.get(name)
        if table is None:
            raise ValueError(f"port_columns requests unknown port {name!r}")
        declared = [column.name for column in table.columns]
        if requested is None:
            demanded.append((table, declared))
            continue
        missing = set(requested) - set(declared)
        if missing:
            raise ValueError(
                f"port_columns[{name!r}] requests missing declared column(s): {sorted(missing)!r}"
            )
        # An empty demand is cardinality-only: one carrier column keeps the rows.
        kept = [column for column in declared if column in requested]
        demanded.append((table, kept or declared[:1]))
    return demanded
