"""An Export run (specs/workbench, Control flow 38): an Excel workbook of cells, made in
memory from what Preview's last pricing gave.

The workbook is Haute's own: a sheet for each sheet name the Export's cells give, in the
order first given, each cell holding its column's value as the quote holds it, typed by
its column, text always as text, and a number in its column's format as the sheets show
it. Nothing is written to disk.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from haute._json_safe import MAX_SAFE_INTEGER, to_json_safe
from haute._json_values import JsonValueError, json_value
from haute._workbench_config import WorkbenchError
from haute._workbench_form import (
    QUOTE_DETAILS_NAME,
    ExcelCellsExport,
    ExportCell,
    ExportWidget,
    FormSpec,
    SchemaColumn,
    SchemaTable,
    with_quote_details,
)
from haute._workbench_input import with_stand_in
from haute._workbench_tables import COLUMN_DTYPES, input_tables, sample_quote

#: What a workbook is answered as.
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The last column of a sheet, XFD, by number, and its last row.
_LAST_COLUMN = 16_384
_LAST_ROW = 1_048_576
#: An address as Excel writes one: a column's letters, then a row without leading zeros.
_ADDRESS = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6})")
#: The longest name Excel gives a sheet, and the characters it refuses in one.
SHEET_NAME_LIMIT = 31
_SHEET_NAME_REFUSED = "[]:*?/\\"
#: A ``{name}`` in a file name, as ``fileNameTokens`` in the view finds it.
_TOKEN = re.compile(r"\{([^{}]*)\}")
#: What a file name cannot hold on Windows, which is the strictest system it is saved on.
_FILE_NAME_REFUSED = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
#: A currency's sign, quoted as an Excel format holds text.
_CURRENCY_SIGNS = {"currency_gbp": '"£"', "currency_usd": '"US$"', "currency_eur": '"€"'}
#: The most decimal places an Excel format holds.
_PLACES_LIMIT = 30
#: A whole number as text, as ``to_json_safe`` writes one past what JSON holds exactly.
_WHOLE_TEXT = re.compile(r"-?[0-9]+")


class ExportRefusedError(WorkbenchError):
    """An export that cannot be written as asked: the route answers 422 with its message."""


class StaleExportError(WorkbenchError):
    """An export of a sample other than the one the form gives now: the route answers 409."""


def cell_address(address: str) -> tuple[int, int] | None:
    """*address*'s column, by number from 1, and its row; None for one that is not a cell of
    a sheet: a column from ``A`` to ``XFD`` in capitals, then a row from 1 to 1,048,576."""
    found = _ADDRESS.fullmatch(address)
    if found is None:
        return None
    letters, digits = found.groups()
    column = 0
    for letter in letters:
        column = column * 26 + ord(letter) - ord("A") + 1
    row = int(digits)
    return (column, row) if column <= _LAST_COLUMN and row <= _LAST_ROW else None


def sheet_name_problem(name: str) -> str | None:
    """Why Excel refuses *name* for a sheet, said of it, or None for a name it takes. A blank
    name is a problem of its own (:func:`cells_problems`)."""
    if len(name) > SHEET_NAME_LIMIT:
        return f"is longer than {SHEET_NAME_LIMIT} characters"
    refused = next((char for char in name if char in _SHEET_NAME_REFUSED), None)
    if refused is not None:
        return f"holds {refused}"
    if name.startswith("'") or name.endswith("'"):
        return "starts or ends with '"
    if name.lower() == "history":
        return "is a name Excel keeps for itself"
    return None


def _own_column(spec: FormSpec, cell: ExportCell) -> tuple[SchemaTable, SchemaColumn] | None:
    """The table and column *cell* names, among the columns a sheet shows, or None."""
    table = next((held for held in spec.data_schema.tables if held.name == cell.table), None)
    if table is None:
        return None
    column = next(
        (held for held in [*table.own_inputs, *table.own_outputs] if held.name == cell.column),
        None,
    )
    return None if column is None else (table, column)


def _file_name_problems(spec: FormSpec, file_name: str) -> list[str]:
    """A file name's problems, as ``exportProblems`` in the view words them."""
    problems = [] if file_name.strip() else ["has no file name"]
    details = next(
        (table for table in spec.data_schema.tables if table.name == QUOTE_DETAILS_NAME), None
    )
    held = set() if details is None else {column.name for column in details.columns}
    for token in dict.fromkeys(_TOKEN.findall(file_name)):
        if token not in held:
            problems.append(
                f"names its file by {{{token}}}, which is not a column of quote_details"
            )
    return problems


def cells_problems(spec: FormSpec, target: ExcelCellsExport) -> list[str]:
    """What stops an Excel workbook of cells being written, as ``cellsProblems`` in the view
    words them: its file name's problems, then its cells', each once, in the cells' order."""
    problems = _file_name_problems(spec, target.file_name)
    if not target.cells:
        problems.append("writes no cells")
    taken: set[tuple[str, str]] = set()
    sheets: dict[str, str] = {}
    for cell in target.cells:
        found = _own_column(spec, cell)
        field = f"{cell.table}.{cell.column}"
        if found is None:
            problems.append(f"writes {field}, which is no longer in the schema")
        elif found[0].rows == "many":
            problems.append(f"writes {field}, a column of a many-row table")
        if cell.sheet.strip() == "":
            problems.append("writes to a sheet with no name")
        elif (reason := sheet_name_problem(cell.sheet)) is not None:
            problems.append(f'writes to the sheet "{cell.sheet}", which {reason}')
        else:
            spelled = sheets.setdefault(cell.sheet.lower(), cell.sheet)
            if spelled != cell.sheet:
                problems.append(
                    f'names the sheets "{spelled}" and "{cell.sheet}", which Excel holds as one'
                )
        if cell_address(cell.cell) is None:
            problems.append(
                f'writes to "{cell.cell}" on "{cell.sheet}", which is not a cell like B3'
            )
        elif (cell.sheet.lower(), cell.cell) in taken:
            problems.append(f'writes more than one field to {cell.cell} on "{cell.sheet}"')
        else:
            taken.add((cell.sheet.lower(), cell.cell))
    return list(dict.fromkeys(problems))


def _places(value: Decimal) -> int:
    """How many decimal places *value* shows with no trailing zero."""
    exponent = value.normalize().as_tuple().exponent
    return min(_PLACES_LIMIT, max(0, -exponent)) if isinstance(exponent, int) else 0


def number_format(column: SchemaColumn, value: float) -> str:
    """The Excel format of a number cell of *column* holding *value*, as the sheets show the
    value (``formatPivotNumber`` in the view): its decimal places when set, else a
    currency's two, a percent's up to two and a number's own, Excel's formats being unable
    to trim trailing zeros as the sheets do; thousands grouped unless the column says not;
    a percent's sign after it and a currency's before it."""
    kind = column.number_format
    places = column.decimal_places
    if kind == "general" and places is None:
        return "General"
    if places is None:
        exact = Decimal(repr(value))
        if kind in _CURRENCY_SIGNS:
            places = 2
        elif kind == "percent":
            # Rounded half away from zero, as the sheets round.
            places = _places((exact * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        else:
            places = _places(exact)
    body = ("#,##0" if column.use_grouping else "0") + ("." + "0" * places if places else "")
    if kind == "percent":
        body += "%"
    return _CURRENCY_SIGNS.get(kind, "") + body


def _text(value: Any) -> str:
    """A ``quote_details`` value as a file name says it: a whole number without a point, a
    date as ``yyyy-mm-dd``, text as it is, nothing for none."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def export_file_name(file_name: str, details: Mapping[str, Any]) -> str:
    """*file_name* with each ``{name}`` the value of that column of *details*, each
    character a file name cannot hold written ``_``, and ``.xlsx``. A name left blank is
    refused: the file would be named by its extension alone."""
    named = _FILE_NAME_REFUSED.sub(
        "_", _TOKEN.sub(lambda found: _text(details.get(found[1])), file_name)
    )
    if named.strip() == "":
        raise ExportRefusedError(
            f'The file name "{file_name}" is blank for this quote: fill in what it names, or '
            "give it text of its own."
        )
    return f"{named}.xlsx"


def _write(cell: Any, column: SchemaColumn, value: Any, field: str) -> None:
    """*value*, of *column*'s type, into the openpyxl *cell*: text held as text, so neither a
    leading ``=`` nor an error value is read; a whole number Excel cannot hold exactly (past
    2**53, as its numbers are doubles) written as its digits, as text, so none is lost; a
    date shown ``yyyy-mm-dd``; a number in its column's format; none leaving the cell
    empty."""
    # Imported here: only an export loads openpyxl.
    from openpyxl.utils.exceptions import IllegalCharacterError

    if value is None:
        return
    if column.type == "int" and abs(value) > MAX_SAFE_INTEGER:
        cell.value = str(value)
        cell.data_type = "s"
        return
    if column.type == "str":
        try:
            cell.value = value
        except IllegalCharacterError:
            raise ExportRefusedError(
                f"{field} holds a character Excel cannot hold in a cell, such as a control "
                "character: take it out of the sample."
            ) from None
        cell.data_type = "s"
        return
    cell.value = value
    if column.type == "date":
        cell.number_format = "yyyy-mm-dd"
    elif column.type in ("int", "float"):
        cell.number_format = number_format(column, value)


def cells_workbook(
    spec: FormSpec, target: ExcelCellsExport, value_of: Callable[[ExportCell], Any]
) -> bytes:
    """The workbook of *target*'s cells, each holding ``value_of`` it, its sheets in the order
    their names are first given; *target* has no :func:`cells_problems`."""
    # Imported here: only an export loads openpyxl.
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.properties.creator = "Haute"
    sheets: dict[str, Any] = {}
    for cell in target.cells:
        sheet = sheets.get(cell.sheet)
        if sheet is None:
            sheet = workbook.active if not sheets else workbook.create_sheet()
            sheet.title = cell.sheet
            sheets[cell.sheet] = sheet
        found = _own_column(spec, cell)
        address = cell_address(cell.cell)
        if found is None or address is None:
            raise AssertionError(f"{cell!r} has a problem cells_problems names")
        column_number, row = address
        _write(
            sheet.cell(row=row, column=column_number),
            found[1],
            value_of(cell),
            f"{cell.table}.{cell.column}",
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _export_of(spec: FormSpec, component: str) -> tuple[ExportWidget, ExcelCellsExport]:
    """The Export of id *component* with its workbook of cells, else a refusal naming why."""
    widget = next(
        (
            held
            for page in spec.pages
            for section in page.sections
            for held in section.widgets
            if held.id == component
        ),
        None,
    )
    if widget is None:
        raise ExportRefusedError(f"The form has no component {component!r} to export.")
    if not isinstance(widget, ExportWidget) or not isinstance(widget.target, ExcelCellsExport):
        raise ExportRefusedError(
            f"{widget.name} is not an Export to an Excel workbook of cells, the only export "
            "Preview runs."
        )
    return widget, widget.target


def preview_export(
    form: FormSpec,
    component: str,
    sample: Mapping[str, Any],
    tables: Mapping[str, list[Any]],
) -> tuple[str, bytes]:
    """The file name and the bytes of the Export of id *component* of *form*, run on what
    Preview's last pricing gave: *sample*, the server's typing of the sample it priced
    (``sample_quote``), and *tables*, the priced rows by table.

    Refuses (:class:`ExportRefusedError`) a component that is not an Export to an Excel
    workbook of cells, one with problems, each named, and a value not of its column's type;
    refuses (:class:`StaleExportError`) a *sample* the form does not give now, so a
    workbook never holds one sample's outputs beside another's inputs.
    """
    spec = with_quote_details(form)
    widget, target = _export_of(spec, component)
    problems = cells_problems(spec, target)
    if problems:
        name = widget.label.strip() or widget.name
        raise ExportRefusedError(
            f'Export "{name}" cannot be written: it ' + "; it ".join(problems) + "."
        )
    typed = sample_quote(spec)
    if to_json_safe(typed) != to_json_safe(dict(sample)):
        raise StaleExportError(
            "The sample changed after it was priced: export once it is priced again."
        )
    # The server's own typing of the sample priced, a whole number past what JSON holds
    # exactly kept whole, rather than the text the wire carries it as.
    quote = with_stand_in(input_tables(spec), typed)

    def value_of(cell: ExportCell) -> Any:
        found = _own_column(spec, cell)
        assert found is not None
        table, column = found
        if table.is_output(column.name):
            rows = tables.get(table.name) or [{}]
            held = rows[0].get(column.name) if isinstance(rows[0], dict) else None
            if column.type == "int" and isinstance(held, str) and _WHOLE_TEXT.fullmatch(held):
                # A whole number past what JSON holds exactly, as the wire carries it.
                held = int(held)
        else:
            row = quote.get(table.name)
            held = row.get(column.name) if isinstance(row, dict) else None
        try:
            return json_value(held, COLUMN_DTYPES[column.type])
        except JsonValueError:
            raise ExportRefusedError(
                f"{cell.table}.{cell.column} is {held!r}, not {column.type!r}: price the "
                "sample again."
            ) from None

    data = cells_workbook(spec, target, value_of)
    details = quote.get(QUOTE_DETAILS_NAME)
    return export_file_name(target.file_name, details if isinstance(details, dict) else {}), data
