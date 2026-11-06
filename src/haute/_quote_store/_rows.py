"""A quote's values as the store's rows, and its rows as the sheets' values (specs/workbench).

A save's input rows come from one path: the cells, with the app's details set over them,
built into the request a pricing builds (:func:`haute._workbench_tables.quote_request_rows`:
hidden cells left out, preset rows placed, numbers typed), each value then read by the rule
a request is read by (:func:`haute._json_values.json_value`), with the quote's identity on
every row and a many-row table's rows keyed by its index, a numbered one holding each row's
number on the sheet. A value its column's type does not hold, or a typed index blank,
repeated or too long, refuses the save, naming every such cell. Output rows are the
pricing's typed document's, a value that is not finite kept as null. Read back, each value is
the cell text it reads as, a many-row table's rows in its index's order, and the output
tables are rendered as the pricing route renders them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from haute._json_values import JsonValueError, json_value
from haute._output_assembler import prune_document
from haute._quote_store._store import MisfitCell, QuoteStoreError, QuoteValuesError
from haute._workbench_form import (
    IDENTITY_NAMES,
    QUOTE_DETAILS_NAME,
    Cell,
    FormSpec,
    SchemaColumn,
    SchemaTable,
)
from haute._workbench_tables import (
    WorkbenchTable,
    holds_output,
    index_number,
    index_order,
    index_values,
    input_tables,
    numbered_value,
    output_tables,
    preset_rows,
    priced_tables,
    quote_request_rows,
    request_columns,
    starting_value,
    typed_index_problems,
)
from haute._workbench_visibility import visibility

#: The values typed into the sheets: by table name, rows keyed by column name, as typed.
Values = dict[str, list[dict[str, Cell]]]
#: A table's rows as the store holds them, by column name.
Rows = list[dict[str, Any]]
#: Each output table's rows by name, as the pricing route answers them.
PricedTables = dict[str, list[Any]]

_IDENTITY_NAMES = ("quote_reference", "quote_option")

#: The field of a row holding its values in store columns the form no longer holds, as JSON
#: text by column name (READ-05). No sheet shows it and no pricing reads it: it moves with its
#: row through inserts and deletes, and a save writes it back. ``$`` starts no column's id or
#: name, so it is never a cell.
KEPT_KEY = "$kept"
#: Each input table's kept columns, by table name, each by its name and kind.
KeptColumns = Mapping[str, Sequence[tuple[str, str]]]

#: Bound empty-row expansion independently of the number of populated schedule rows.
MAX_NUMBERED_GAPS = 10_000


def _check_numbered_gaps(table: SchemaTable, positions: Collection[int]) -> None:
    """Refuse sparse business IDs before treating them as numbered grid positions."""
    gaps = max(positions, default=0) - len(positions)
    if gaps > MAX_NUMBERED_GAPS:
        assert table.index is not None
        raise QuoteStoreError(
            f"{table.name}.{table.index.name} would create {gaps:,} empty rows in its numbered "
            f"grid (limit {MAX_NUMBERED_GAPS:,}). Remove empty rows, use a Typed index, or "
            "migrate the stored keys to consecutive row numbers."
        )


def _carries_kept(row: Mapping[str, Any]) -> bool:
    """Whether *row* carries a kept field: a row whose cells are all empty still holds the
    values in it, so a save keeps the row (only deleting it deletes them)."""
    return row.get(KEPT_KEY) is not None


def _kept_json(row: Mapping[str, Any], columns: Sequence[tuple[str, str]]) -> str | None:
    """*row*'s values in its kept *columns* as the field's JSON text, None when it holds none."""
    held: dict[str, Any] = {}
    for name, _kind in columns:
        value = row.get(name)
        if isinstance(value, datetime | date):
            value = value.isoformat()
        elif isinstance(value, float) and not math.isfinite(value):
            value = None
        if value is not None:
            held[name] = value
    return json.dumps(held, sort_keys=True, separators=(",", ":")) if held else None


def _typed_kept(value: Any, kind: str) -> Any:
    """*value* as a kept column of *kind* holds it; :class:`ValueError` when it does not fit."""
    if value is None:
        return None
    if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
        return value
    if kind == "float" and isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if kind == "string" and isinstance(value, str):
        return value
    if kind == "boolean" and isinstance(value, bool):
        return value
    if kind == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if kind == "datetime" and isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(value)


def _kept_values(
    text: Any, table: SchemaTable, columns: Sequence[tuple[str, str]], row: int | None
) -> dict[str, Any]:
    """The values the kept field *text* of *table*'s *row* holds, typed as the store holds
    them: refused (:class:`QuoteValuesError`), naming them, for a column the form holds, one
    the store does not hold, or a value its column's kind does not hold."""
    try:
        held = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        held = None
    if not isinstance(held, dict):
        raise QuoteValuesError(
            f"A row of {table.name} carries a {KEPT_KEY} field that is not an object of values, "
            "so the quote was not saved.",
            cells=[MisfitCell(table=table.name, row=row, column=KEPT_KEY)],
        )
    kinds = dict(columns)
    form_columns = {column.name for column in table.columns}
    named = sorted(name for name in held if name in form_columns or name not in kinds)
    if named:
        raise QuoteValuesError(
            f"A row of {table.name} carries values for "
            + ", ".join(named)
            + ", which are no columns the store keeps outside the form, so the quote was not "
            "saved.",
            cells=[MisfitCell(table=table.name, row=row, column=name) for name in named],
        )
    typed: dict[str, Any] = {}
    misfits: list[str] = []
    for name, value in held.items():
        try:
            typed[name] = _typed_kept(value, kinds[name])
        except ValueError:
            misfits.append(name)
    if misfits:
        raise QuoteValuesError(
            f"A row of {table.name} carries values not of their store columns' kinds: "
            + ", ".join(sorted(misfits))
            + "; the quote was not saved.",
            cells=[MisfitCell(table=table.name, row=row, column=name) for name in sorted(misfits)],
        )
    return typed


def _label(column: SchemaColumn) -> str:
    return column.label or column.name


def input_rows(
    form: FormSpec,
    values: Values,
    identity: tuple[str, int],
    kept: KeptColumns | None = None,
) -> dict[str, Rows]:
    """Each input table's rows for *values*, by table name, as the store writes them: a one-row
    table its one row, nulls where nothing is typed; a many-row table its typed rows, each
    under its index's value, a numbered one's its number on the sheet. Refused
    (:class:`QuoteValuesError`), naming each cell, when a typed index is blank on a row holding
    anything, repeated or too long (:func:`typed_index_problems`), or any value is not of its
    column's type. With *kept*, the store's columns outside the form, each row also writes back
    the values its kept field carries (READ-05)."""
    problems = typed_index_problems(form, values, kept=None if kept is None else KEPT_KEY)
    if problems:
        index_labels = {
            (table.name, column.name): _label(column)
            for table in form.data_schema.tables
            for column in table.columns
        }
        raise QuoteValuesError(
            "The rows' keys are not their own, so the quote was not saved: "
            + "; ".join(problem.message for problem in problems)
            + ".",
            cells=[
                MisfitCell(
                    table=problem.table,
                    row=row,
                    column=index_labels.get((problem.table, problem.column), problem.column),
                )
                for problem in problems
                for row in problem.rows
            ],
        )
    request, numbers = quote_request_rows(
        form, values, carried=None if kept is None else _carries_kept
    )
    by_name = {table.name: table for table in input_tables(form)}
    reference, option = identity
    misfits: list[MisfitCell] = []
    rows: dict[str, Rows] = {}
    for table in form.data_schema.tables:
        if not table.has_inputs:
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
        if table.index is not None and table.index.index == "numbered":
            _check_numbered_gaps(table, sheet)
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
            numbered = table.index
            if numbered is not None and numbered.index == "numbered":
                # A numbered index is the row's number on the sheet, and keys it in the store:
                # set here too, since a row carried for its kept values gives the request none.
                row[numbered.name] = numbered_value(numbered, sheet[place])
            if kept is not None and table.name != QUOTE_DETAILS_NAME:
                # In the order the request's rows stand, an index of values' order included,
                # so each row's kept field is its own.
                typed = preset_rows(table, values.get(table.name) or [])
                index = sheet[place] - 1 if many else 0
                source = typed[index] if 0 <= index < len(typed) else {}
                # Every row names each kept column, a value or null: one insert writes them.
                row.update(dict.fromkeys(name for name, _ in kept.get(table.name, ())))
                text = source.get(KEPT_KEY)
                if text is not None:
                    row.update(
                        _kept_values(
                            text, table, kept.get(table.name, ()), sheet[place] if many else None
                        )
                    )
            written.append(row)
        rows[table.name] = written
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
    """Each output table's rows from the pricing's typed *document*, by table name: none when
    the quote is saved unpriced; a value that is not finite as null; the quote's identity on
    every row of a table leading with it; a many-row table's numbered index numbered in the
    pricing's order, any other holding the answer's values."""
    by_name = {table.name: table for table in output_tables(form)}
    reference, option = identity
    rows: dict[str, Rows] = {}
    for table in form.data_schema.tables:
        if not table.has_outputs or table.has_inputs:
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
            index = table.index
            if index is not None and index.index == "numbered":
                # The Workbench Output numbered it so; the store keys the row by it.
                row[index.name] = numbered_value(index, place)
            written.append(row)
        rows[table.name] = written
    return rows


def with_outputs(
    form: FormSpec, rows: Mapping[str, Rows], document: Mapping[str, Any] | None
) -> dict[str, Rows]:
    """*rows*, each input table's rows of its inputs, given its outputs from the pricing's
    typed *document*: a one-row table's one row the document's row of it; a many-row
    table's each row those of the document's row holding its key value, matched as typed;
    every output null while the quote is unpriced (no *document*) or the document gives that
    row none; a value that is not finite null. A document row whose key no input row holds,
    or one key on two rows, is refused with nothing written: a pricing that passed
    ``output_misfit`` never gives one."""
    sides = {table.name: table for table in output_tables(form)}
    given: dict[str, Rows] = dict(rows)
    for table in form.data_schema.tables:
        if not (table.has_inputs and table.has_outputs):
            continue
        side = sides[table.name]
        filled = [column.name for column in side.columns if column.name not in _OUTPUT_ASIDE]
        filled = [name for name in filled if name != side.key]
        part = None if document is None else document.get(table.name)
        held = [dict(row) for row in given.get(table.name, [])]
        if side.key is None:
            answered = part if isinstance(part, Mapping) else {}
            for row in held:
                row.update({name: _finite(answered.get(name)) for name in filled})
            given[table.name] = held
            continue
        records = part if isinstance(part, list) else []
        by_key: dict[Any, Mapping[str, Any]] = {}
        typed = {row.get(side.key) for row in held}
        for record in records:
            value = record.get(side.key)
            if value in by_key or value not in typed:
                raise QuoteValuesError(
                    f"The pricing gives {table.name} a row of {side.key} {value!r}, which is "
                    "on no row of the quote or on two, so the quote was not saved.",
                    cells=[],
                )
            by_key[value] = record
        for row in held:
            record = by_key.get(row.get(side.key), {})
            row.update({name: _finite(record.get(name)) for name in filled})
        given[table.name] = held
    return given


#: A response row's columns the store keeps from the request, not from the pricing.
_OUTPUT_ASIDE = frozenset({"quote_reference", "quote_option"})


def rendered_outputs(form: FormSpec, rows: Mapping[str, Rows]) -> PricedTables:
    """The outputs held in *rows* (by table name) rendered as the pricing rendered them:
    each output table's rows by name, pruned and JSON-safe. An input table gives its output
    side's columns of each stored row holding an output value, a one-row table its one
    row's; a table of outputs alone its rows in its index's order, the index among them."""
    sides = {table.name: table for table in output_tables(form)}
    document: dict[str, Any] = {}
    for table in form.data_schema.tables:
        if not table.has_outputs:
            continue
        held = rows.get(table.name, [])
        index = table.index
        if index is not None:
            order = index_order(index, [row.get(index.name) for row in held])
            held = [held[place] for place in order]
        if table.has_inputs:
            side = sides[table.name]
            names = [column.name for column in side.columns]
            outputs = [column.name for column in table.own_outputs]
            if table.rows == "many":
                held = [row for row in held if any(holds_output(row.get(n)) for n in outputs)]
            records = [{name: row.get(name) for name in names} for row in held]
        else:
            records = [dict(row) for row in held]
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


def _row_cells(
    table: SchemaTable, row: Mapping[str, Any], kept: Sequence[tuple[str, str]] = ()
) -> dict[str, Cell]:
    details = table.name == QUOTE_DETAILS_NAME
    cells: dict[str, Cell] = {}
    for column in table.inputs:
        if column.index == "numbered" or (not details and column.name in IDENTITY_NAMES):
            continue
        cell = _cell(row.get(column.name))
        if cell is not None:
            cells[column.name] = cell
    carried = _kept_json(row, kept)
    if carried is not None:
        cells[KEPT_KEY] = carried
    return cells


def cells(
    form: FormSpec, rows: Mapping[str, Rows], kept: KeptColumns | None = None
) -> tuple[Values, list[str]]:
    """The values *rows* (by table name) read back as on the sheets, and each row that no longer
    fits them: a many-row table's rows in its index's order (:func:`index_order`), a numbered
    index's in their places, a gap a blank row and a row whose index is no number after them;
    a row of an index of values whose value is no longer allowed left out and named as
    ``table row N (value)``; each empty cell the sheets hide in a one-row table starting at its
    default (:func:`_hidden_defaults`). With *kept*, each row carries its values in the store's
    columns outside the form in its kept field (:data:`KEPT_KEY`)."""
    kept = kept or {}
    values: Values = {}
    dropped: list[str] = []
    for table in form.data_schema.tables:
        if not table.has_inputs:
            continue
        held = rows.get(table.name, [])
        if table.rows == "one":
            row = _row_cells(table, held[0], kept.get(table.name, ())) if held else {}
            if row:
                values[table.name] = [row]
            continue
        index = table.index
        if index is None:
            raise ValueError(f"{table.name} has many rows and no index")
        stored = [row.get(index.name) for row in held]
        ordered = [held[place] for place in index_order(index, stored)]
        columns = kept.get(table.name, ())
        if index.index == "numbered":
            placed: dict[int, dict[str, Cell]] = {}
            after: list[dict[str, Cell]] = []
            for row in ordered:
                number = _row_number(row.get(index.name))
                cells_of = _row_cells(table, row, columns)
                if number is None or number in placed:
                    after.append(cells_of)
                else:
                    placed[number] = cells_of
            _check_numbered_gaps(table, placed.keys())
            listed = [placed.get(number, {}) for number in range(1, max(placed, default=0) + 1)]
            listed.extend(after)
        else:
            allowed = set(index_values(index)) if index.index == "values" else None
            listed = []
            for place, row in enumerate(ordered, 1):
                value = row.get(index.name)
                if allowed is not None and value not in allowed:
                    dropped.append(f"{table.name} row {place} ({value})")
                    continue
                listed.append(_row_cells(table, row, columns))
        if listed:
            values[table.name] = listed
    return _hidden_defaults(form, values, rows), dropped


def _row_number(value: Any) -> int | None:
    """A numbered index's stored *value* as the row's number on the sheet, from 1, written as
    Haute writes one; None for any other value, as one saved before the index was numbered
    holds (``"01"`` or ``"Leeds"``), so a row stored as ``"1"`` keeps place 1 whatever else
    reads as 1."""
    number = index_number(value)
    return number if number is not None and number >= 1 else None


def _hidden_defaults(form: FormSpec, values: Values, rows: Mapping[str, Rows]) -> Values:
    """*values* with each cell of a one-row input table that the sheets hide for them, and
    that the store holds null, holding the default a new quote starts its column with: the
    store keeps nothing of a hidden cell, so a sheet hidden when the quote was saved shows its
    defaults once it shows. A box held unticked is false, not null, and stays unticked, and
    ``quote_details`` is stored whole, its cells read as saved. A default can show what holds
    it, as a box ticked by default showing the sheet it is on, which would show and price what
    the quote was not saved with: the defaults are filled together when together they change
    nothing that shows, else one at a time in the schema's order, each that would change it
    left out."""
    shown = visibility(form, values)
    starts = [
        (table.name, column.name, start)
        for table in form.data_schema.tables
        if table.name != QUOTE_DETAILS_NAME
        for column in table.columns
        if (start := starting_value(table, column)) is not None
        and (table.name, column.name) in shown.hidden
        and _stored(rows, table.name, column.name) is None
    ]
    if not starts:
        return values
    every = _with_cells(values, starts)
    if visibility(form, every) == shown:
        return every
    filled = values
    for fill in starts:
        tried = _with_cells(filled, [fill])
        if visibility(form, tried) == shown:
            filled = tried
    return filled


def _stored(rows: Mapping[str, Rows], table: str, column: str) -> Any:
    """What the store holds in a one-row *table*'s *column*: None while it holds no row."""
    held = rows.get(table, [])
    return held[0].get(column) if held else None


def _with_cells(values: Values, fills: Sequence[tuple[str, str, Cell]]) -> Values:
    """*values* with each of *fills*, a one-row table's column and its value, set; *values*
    left as they are."""
    filled = dict(values)
    for table, column, value in fills:
        [row] = filled.get(table, [{}])
        filled[table] = [{**row, column: value}]
    return filled
