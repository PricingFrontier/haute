"""A quote's values as the store's rows, and its rows as the sheets' values (specs/workbench).

A save's input rows come from one path: the cells, with the app's details set over them,
built into the request a pricing builds (:func:`haute._workbench_tables.quote_request_rows`:
hidden cells left out, preset rows placed, numbers typed), each value then read by the rule
a request is read by (:func:`haute._json_values.json_value`), with the quote's identity on
every row and a many-row table's rows numbered as the sheet numbers them. A value its
column's type does not hold refuses the save, naming every such cell. Output rows are the
pricing's typed document's, a value that is not finite kept as null. Read back, each value is
the cell text it reads as, a many-row table's rows in their places on the sheet, and the
output tables are rendered as the pricing route renders them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from typing import Any

from haute._json_values import JsonValueError, json_value
from haute._output_assembler import prune_document
from haute._quote_store._store import MisfitCell, QuoteValuesError
from haute._workbench_form import (
    IDENTITY_IDS,
    QUOTE_DETAILS_ID,
    Cell,
    FormSpec,
    SchemaColumn,
    SchemaTable,
)
from haute._workbench_tables import (
    QUOTE_ROW,
    WorkbenchTable,
    input_tables,
    output_tables,
    preset_column,
    priced_tables,
    quote_request_rows,
    request_columns,
)

#: The values typed into the sheets: by table id, rows keyed by column id, as typed.
Values = dict[str, list[dict[str, Cell]]]
#: A table's rows as the store holds them, by column name.
Rows = list[dict[str, Any]]
#: Each output table's rows by name, as the pricing route answers them.
PricedTables = dict[str, list[Any]]

_IDENTITY_NAMES = ("quote_reference", "quote_option")


def _label(column: SchemaColumn) -> str:
    return column.label or column.name


def input_rows(form: FormSpec, values: Values, identity: tuple[str, int]) -> dict[str, Rows]:
    """Each input table's rows for *values*, by table id, as the store writes them: a one-row
    table its one row, nulls where nothing is typed; a many-row table its typed rows, each
    under its number on the sheet. Refused (:class:`QuoteValuesError`), naming each cell, when
    any value is not of its column's type."""
    request, numbers = quote_request_rows(form, values)
    by_name = {table.name: table for table in input_tables(form)}
    reference, option = identity
    misfits: list[MisfitCell] = []
    rows: dict[str, Rows] = {}
    for table in form.data_schema.tables:
        if table.role != "input":
            continue
        workbench = by_name[table.name]
        labels = {column.name: _label(column) for column in table.columns}
        part = request.get(table.name)
        many = table.rows == "many"
        records: list[Mapping[str, Any]]
        if many:
            records = part if isinstance(part, list) else []
        else:
            records = [part if isinstance(part, dict) else {}]
        sheet = numbers.get(table.name, [])
        written: Rows = []
        for place, record in enumerate(records):
            row: dict[str, Any] = {}
            for column in request_columns(workbench):
                try:
                    row[column.name] = json_value(record.get(column.name), column.dtype)
                except JsonValueError:
                    misfits.append(
                        MisfitCell(
                            table=table.name,
                            row=sheet[place] if many else None,
                            column=labels.get(column.name, column.name),
                        )
                    )
            row["quote_reference"] = reference
            row["quote_option"] = option
            if many:
                row[QUOTE_ROW] = sheet[place]
            written.append(row)
        rows[table.id] = written
    if misfits:
        named = "; ".join(str(cell) for cell in misfits)
        raise QuoteValuesError(
            f"These values are not of their columns' types, so the quote was not saved: {named}.",
            cells=misfits,
        )
    return rows


def _finite(value: Any) -> Any:
    """*value*, a float that is not finite as null."""
    return None if isinstance(value, float) and not math.isfinite(value) else value


def output_rows(
    form: FormSpec, document: Mapping[str, Any] | None, identity: tuple[str, int]
) -> dict[str, Rows]:
    """Each output table's rows from the pricing's typed *document*, by table id: none when
    the quote is saved unpriced; a value that is not finite as null; the quote's identity on
    every row of a table leading with it; a many-row table's rows numbered in the pricing's
    order."""
    by_name = {table.name: table for table in output_tables(form)}
    reference, option = identity
    rows: dict[str, Rows] = {}
    for table in form.data_schema.tables:
        if table.role != "output":
            continue
        workbench: WorkbenchTable = by_name[table.name]
        part = None if document is None else document.get(table.name)
        records = part if isinstance(part, list) else ([part] if isinstance(part, dict) else [])
        written: Rows = []
        for place, record in enumerate(records, 1):
            row = {column.name: _finite(record.get(column.name)) for column in workbench.columns}
            if "quote_reference" in row:
                row["quote_reference"] = reference
                row["quote_option"] = option
            else:
                row.update(quote_reference=reference, quote_option=option)
            if table.rows == "many":
                row[QUOTE_ROW] = place
            written.append(row)
        rows[table.id] = written
    return rows


def rendered_outputs(form: FormSpec, rows: Mapping[str, Rows]) -> PricedTables:
    """The output tables held in *rows* (by table id) rendered as the pricing rendered them:
    each table's rows by name, without the store's row numbers, pruned and JSON-safe."""
    document: dict[str, Any] = {}
    for table in form.data_schema.tables:
        if table.role != "output":
            continue
        held = sorted(rows.get(table.id, []), key=lambda row: row.get(QUOTE_ROW, 0))
        records = [{key: value for key, value in row.items() if key != QUOTE_ROW} for row in held]
        if table.rows == "many":
            document[table.name] = records
        elif records:
            document[table.name] = records[0]
    return priced_tables(prune_document(document))


def _cell(value: Any) -> Cell | None:
    """A stored value as the cell text it reads as: an Integer its digits, a Decimal the
    shortest text that reads back as it (``1000`` for 1000.0), a Date its ISO text, a tick for
    true; None, no cell, for false and null."""
    if value is None or value is False:
        return None
    if value is True:
        return True
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value).removesuffix(".0")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _row_cells(table: SchemaTable, row: Mapping[str, Any]) -> dict[str, Cell]:
    details = table.id == QUOTE_DETAILS_ID
    cells: dict[str, Cell] = {}
    for column in table.columns:
        if column.index or (not details and column.id in IDENTITY_IDS):
            continue
        cell = _cell(row.get(column.name))
        if cell is not None:
            cells[column.id] = cell
    return cells


def cells(form: FormSpec, rows: Mapping[str, Rows]) -> tuple[Values, list[str]]:
    """The values *rows* (by table id) read back as on the sheets, and each row that no longer
    fits them: a many-row table's rows in their places by ``quote_row``, a gap a blank row;
    a preset row whose value is no longer allowed left out and named as ``table row N
    (value)``."""
    values: Values = {}
    dropped: list[str] = []
    for table in form.data_schema.tables:
        if table.role != "input":
            continue
        held = rows.get(table.id, [])
        if table.rows == "one":
            row = _row_cells(table, held[0]) if held else {}
            if row:
                values[table.id] = [row]
            continue
        given = preset_column(table)
        placed: dict[int, dict[str, Cell]] = {}
        for row in held:
            number = int(row[QUOTE_ROW])
            if given is not None:
                value = row.get(given.name)
                if value is not None and value not in given.options:
                    dropped.append(f"{table.name} row {number} ({value})")
                    continue
            placed[number] = _row_cells(table, row)
        if placed:
            values[table.id] = [placed.get(number, {}) for number in range(1, max(placed) + 1)]
    return values, dropped
