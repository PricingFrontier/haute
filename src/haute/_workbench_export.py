"""An Export run (specs/workbench, Control flow 38): an Excel workbook of cells, made in
memory from what Preview's last pricing gave.

The workbook is Haute's own: a sheet for each sheet name the Export's cells and blocks
give, in the order first given, each cell holding its column's value as the quote holds
it, typed by its column, text always as text, and a number in its column's format as the
sheets show it, each label cell its column's label, as text, and each block its table's
rows under its header. Nothing is written to disk.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote as url_quoted

from haute._json_safe import MAX_SAFE_INTEGER, to_json_safe
from haute._json_values import JsonValueError, json_value
from haute._workbench_config import WorkbenchError
from haute._workbench_form import (
    QUOTE_DETAILS_NAME,
    ExcelCellsExport,
    ExportBlock,
    ExportCell,
    ExportWidget,
    FormSpec,
    SchemaColumn,
    SchemaTable,
    TrueFalseText,
    with_quote_details,
)
from haute._workbench_input import with_stand_in
from haute._workbench_tables import COLUMN_DTYPES, input_tables, quote_request, sample_quote

#: The custom property of a workbook the app exports naming the release that priced it.
RELEASE_PROPERTY = "Haute release"
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


def attachment(file_name: str) -> str:
    """A ``Content-Disposition`` naming *file_name*: whole in ``filename*``, and in
    ``filename`` with each character outside printable ASCII written ``_``, for a client
    that reads only that."""
    plain = "".join(char if " " <= char <= "~" else "_" for char in file_name)
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{url_quoted(file_name, safe='')}"


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


def column_label(column: SchemaColumn) -> str:
    """*column*'s label as the sheets label it: its own, else its name made readable as
    ``readableName`` in the view makes it, each run of underscores a space, the ends
    trimmed and the first character in capitals (``sum_insured`` as ``Sum insured``)."""
    if column.label:
        return column.label
    readable = re.sub(r"_+", " ", column.name).strip()
    return readable[:1].upper() + readable[1:]


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


def _block_columns(
    spec: FormSpec, block: ExportBlock
) -> tuple[SchemaTable, list[SchemaColumn]] | None:
    """The table *block* names and its columns, in its order, among the columns a sheet
    shows, or None when the schema no longer holds the table or one of them."""
    table = next((held for held in spec.data_schema.tables if held.name == block.table), None)
    if table is None:
        return None
    shown = {column.name: column for column in [*table.own_inputs, *table.own_outputs]}
    columns = [shown.get(name) for name in block.columns]
    found = [column for column in columns if column is not None]
    return (table, found) if len(found) == len(columns) else None


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


def _sheet_problems(sheet: str, sheets: dict[str, str]) -> list[str]:
    """What Excel refuses of the sheet named *sheet*, given *sheets*, each name given before
    by its spelling in lower case, which it joins."""
    if sheet.strip() == "":
        return ["writes to a sheet with no name"]
    if (reason := sheet_name_problem(sheet)) is not None:
        return [f'writes to the sheet "{sheet}", which {reason}']
    spelled = sheets.setdefault(sheet.lower(), sheet)
    if spelled != sheet:
        return [f'names the sheets "{spelled}" and "{sheet}", which Excel holds as one']
    return []


def _block_column_problems(spec: FormSpec, block: ExportBlock) -> list[str]:
    """What stops *block*'s table and columns being written: the table or a column no longer
    in the schema, no columns, or a column twice."""
    table = next((held for held in spec.data_schema.tables if held.name == block.table), None)
    if table is None:
        return [f"writes the table {block.table}, which is no longer in the schema"]
    if not block.columns:
        return [f"writes the table {block.table} with no columns"]
    shown = {column.name for column in [*table.own_inputs, *table.own_outputs]}
    problems: list[str] = []
    seen: set[str] = set()
    for name in block.columns:
        field = f"{block.table}.{name}"
        if name not in shown:
            problems.append(f"writes {field}, which is no longer in the schema")
        elif name in seen:
            problems.append(f"writes {field} more than once in its table")
        seen.add(name)
    return problems


def _block_true_false_problems(spec: FormSpec, block: ExportBlock) -> list[str]:
    """True/False text on *block* none of whose columns is True/false, while it has columns
    the schema holds: a block whose table or a column is gone has that problem alone."""
    held = None if block.true_false is None else _block_columns(spec, block)
    if held is None or not held[1] or any(column.type == "bool" for column in held[1]):
        return []
    return [
        f"writes the table {block.table} with True/False text, but none of its columns is "
        "True/false"
    ]


def cells_problems(spec: FormSpec, target: ExcelCellsExport) -> list[str]:
    """What stops an Excel workbook of cells being written, as ``cellsProblems`` in the view
    words them: its file name's problems, then its cells', then its blocks', each once, in
    their order, a cell's value's address checked before its label's, each against every
    address taken before it on its sheet, a value's or a label's, and a block, which takes
    its columns from its top-left cell's row to the sheet's last, against every value,
    label and block before it, the first it lies over named. True/False text is a problem on
    a cell whose column the schema holds but not as True/false, after that column's own, and
    on a block none of whose columns is True/false, after its columns' own."""
    problems = _file_name_problems(spec, target.file_name)
    if not target.cells and not target.blocks:
        problems.append("writes no cells or tables")
    # The addresses taken, in the order taken, by sheet in lower case.
    taken: dict[tuple[str, str], None] = {}
    sheets: dict[str, str] = {}
    for cell in target.cells:
        found = _own_column(spec, cell)
        field = f"{cell.table}.{cell.column}"
        if found is None:
            problems.append(f"writes {field}, which is no longer in the schema")
        elif found[0].rows == "many":
            problems.append(f"writes {field}, a column of a many-row table")
        if found is not None and cell.true_false is not None and found[1].type != "bool":
            problems.append(
                f"writes {field} with True/False text, but it is not a True/false column"
            )
        problems += _sheet_problems(cell.sheet, sheets)
        if cell_address(cell.cell) is None:
            problems.append(
                f'writes to "{cell.cell}" on "{cell.sheet}", which is not a cell like B3'
            )
        elif (cell.sheet.lower(), cell.cell) in taken:
            problems.append(f'writes more than once to {cell.cell} on "{cell.sheet}"')
        else:
            taken[(cell.sheet.lower(), cell.cell)] = None
        if cell.label_cell.strip() == "":
            continue
        if cell_address(cell.label_cell) is None:
            problems.append(
                f'writes the label of {field} to "{cell.label_cell}" on "{cell.sheet}", '
                "which is not a cell like B3"
            )
        elif (cell.sheet.lower(), cell.label_cell) in taken:
            problems.append(f'writes more than once to {cell.label_cell} on "{cell.sheet}"')
        else:
            taken[(cell.sheet.lower(), cell.label_cell)] = None
    # The blocks placed, each its sheet in lower case, its first and last column and its
    # top row, with its table's name and its address.
    placed: list[tuple[str, int, int, int, ExportBlock]] = []
    for block in target.blocks:
        problems += _block_column_problems(spec, block)
        problems += _block_true_false_problems(spec, block)
        problems += _sheet_problems(block.sheet, sheets)
        address = cell_address(block.cell)
        if address is None:
            problems.append(
                f'writes the table {block.table} to "{block.cell}" on "{block.sheet}", which '
                "is not a cell like B3"
            )
            continue
        first, top = address
        last = first + len(block.columns) - 1
        if last > _LAST_COLUMN:
            problems.append(
                f'writes the table {block.table} from {block.cell} on "{block.sheet}" past '
                "XFD, the last column"
            )
            continue
        if not block.columns:
            continue
        sheet = block.sheet.lower()
        under = next(
            (
                written
                for (on, written) in taken
                if on == sheet
                and (spot := cell_address(written)) is not None
                and first <= spot[0] <= last
                and spot[1] >= top
            ),
            None,
        )
        if under is not None:
            problems.append(
                f'writes the table {block.table} from {block.cell} on "{block.sheet}" over '
                f"{under}, which is written already"
            )
        else:
            over = next(
                (
                    other
                    for (on, start, end, _, other) in placed
                    if on == sheet and start <= last and first <= end
                ),
                None,
            )
            if over is not None:
                problems.append(
                    f'writes the table {block.table} from {block.cell} on "{block.sheet}" '
                    f"over the table {over.table} from {over.cell}"
                )
        placed.append((sheet, first, last, top, block))
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


CELL_TEXT_LIMIT = 32_767
"""The most characters an Excel cell holds; openpyxl cuts longer text there silently."""


def _refuse_long_text(text: str, what: str, where: str) -> None:
    """Refuse *text*, *what* is, longer than a cell holds, rather than let openpyxl cut it:
    the download would lose its tail without a word."""
    if len(text) > CELL_TEXT_LIMIT:
        raise ExportRefusedError(
            f"{what} is {len(text):,} characters long; an Excel cell holds at most "
            f"{CELL_TEXT_LIMIT:,}: shorten it in {where}."
        )


def _write_true_false(cell: Any, text: TrueFalseText, value: Any, field: str) -> None:
    """*text*'s True text for a ticked *value*, its False text for one unticked or empty,
    into the openpyxl *cell* as text; a blank text leaves the cell empty."""
    # Imported here: only an export loads openpyxl.
    from openpyxl.utils.exceptions import IllegalCharacterError

    written = text.true if value is True else text.false
    if written.strip() == "":
        return
    what = f"The True/False text of {field}"
    _refuse_long_text(written, what, "the Export's settings")
    try:
        cell.value = written
    except IllegalCharacterError:
        raise ExportRefusedError(
            f"{what} holds a character Excel cannot hold in a cell, such as a control "
            "character: take it out of the Export's settings."
        ) from None
    cell.data_type = "s"


def _write(
    cell: Any,
    column: SchemaColumn,
    value: Any,
    field: str,
    true_false: TrueFalseText | None = None,
    *,
    formatted: bool = True,
) -> None:
    """*value*, of *column*'s type, into the openpyxl *cell*: text held as text, so neither a
    leading ``=`` nor an error value is read; a whole number Excel cannot hold exactly (past
    2**53, as its numbers are doubles) written as its digits, as text, so none is lost; a
    date shown ``yyyy-mm-dd``; a number in its column's format while *formatted*, else in
    Excel's General format, as it is; a True/false value given
    *true_false* that text (:func:`_write_true_false`), even when it is none; none else
    leaving the cell empty."""
    # Imported here: only an export loads openpyxl.
    from openpyxl.utils.exceptions import IllegalCharacterError

    if true_false is not None and column.type == "bool":
        _write_true_false(cell, true_false, value, field)
        return
    if value is None:
        return
    if column.type == "int" and abs(value) > MAX_SAFE_INTEGER:
        cell.value = str(value)
        cell.data_type = "s"
        return
    if column.type == "str":
        _refuse_long_text(str(value), field, "the sample")
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
    elif column.type in ("int", "float") and formatted:
        cell.number_format = number_format(column, value)


def _write_label(cell: Any, label: str, field: str) -> None:
    """*label*, the label or the name of the column *field*, into the openpyxl *cell*, held
    as text, so neither a leading ``=`` nor an error value is read."""
    # Imported here: only an export loads openpyxl.
    from openpyxl.utils.exceptions import IllegalCharacterError

    _refuse_long_text(label, f"The label of {field}", "the column's label in the schema")
    try:
        cell.value = label
    except IllegalCharacterError:
        raise ExportRefusedError(
            f"The label of {field} holds a character Excel cannot hold in a cell, such as a "
            "control character: take it out of the column's label in the schema."
        ) from None
    cell.data_type = "s"


def cells_workbook(
    spec: FormSpec,
    target: ExcelCellsExport,
    value_of: Callable[[ExportCell], Any],
    rows_of: Callable[[ExportBlock], list[list[Any]]],
    *,
    release: str | None = None,
) -> bytes:
    """The workbook of *target*'s cells, each holding ``value_of`` it and its label cell its
    column's label, and its blocks, each its header, but for ``none``, along its top-left
    cell's row and under it ``rows_of`` it, a value for each of its columns, its sheets in
    the order their names are first given; *target* has no :func:`cells_problems`. Its
    numbers are in their columns' formats while its ``values`` are ``formatted``, and as they
    are, in General, while ``raw``. A block whose rows run past the sheet's last row is
    refused, naming it. *release*, the release that priced the quote, is the workbook's
    :data:`RELEASE_PROPERTY`."""
    # Imported here: only an export loads openpyxl.
    from openpyxl import Workbook
    from openpyxl.packaging.custom import StringProperty

    workbook = Workbook()
    workbook.properties.creator = "Haute"
    if release is not None:
        workbook.custom_doc_props.append(StringProperty(name=RELEASE_PROPERTY, value=release))
    # Raw writes each number as it is, Formatted in its column's format.
    formatted = target.values == "formatted"
    sheets: dict[str, Any] = {}

    def sheet_of(name: str) -> Any:
        sheet = sheets.get(name)
        if sheet is None:
            sheet = workbook.active if not sheets else workbook.create_sheet()
            sheet.title = name
            sheets[name] = sheet
        return sheet

    for cell in target.cells:
        sheet = sheet_of(cell.sheet)
        found = _own_column(spec, cell)
        address = cell_address(cell.cell)
        if found is None or address is None:
            raise AssertionError(f"{cell!r} has a problem cells_problems names")
        column_number, row = address
        field = f"{cell.table}.{cell.column}"
        _write(
            sheet.cell(row=row, column=column_number),
            found[1],
            value_of(cell),
            field,
            cell.true_false,
            formatted=formatted,
        )
        if cell.label_cell.strip() == "":
            continue
        label_address = cell_address(cell.label_cell)
        if label_address is None:
            raise AssertionError(f"{cell!r} has a problem cells_problems names")
        label_column, label_row = label_address
        _write_label(sheet.cell(row=label_row, column=label_column), column_label(found[1]), field)
    for block in target.blocks:
        sheet = sheet_of(block.sheet)
        held = _block_columns(spec, block)
        address = cell_address(block.cell)
        if held is None or address is None:
            raise AssertionError(f"{block!r} has a problem cells_problems names")
        table, columns = held
        first, row = address
        if block.header != "none":
            for offset, column in enumerate(columns):
                label = column_label(column) if block.header == "labels" else column.name
                at = sheet.cell(row=row, column=first + offset)
                _write_label(at, label, f"{table.name}.{column.name}")
            row += 1
        rows = rows_of(block)
        if rows and row + len(rows) - 1 > _LAST_ROW:
            raise ExportRefusedError(
                f"The table {table.name} has {len(rows)} rows, more than fit on "
                f'"{block.sheet}" from {block.cell} down: place it higher.'
            )
        for values in rows:
            for offset, (column, value) in enumerate(zip(columns, values, strict=True)):
                at = sheet.cell(row=row, column=first + offset)
                _write(
                    at,
                    column,
                    value,
                    f"{table.name}.{column.name}",
                    block.true_false,
                    formatted=formatted,
                )
            row += 1
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
            "that runs."
        )
    return widget, widget.target


def _table_rows(
    table: SchemaTable, quote: Mapping[str, Any], tables: Mapping[str, list[Any]]
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """*table*'s rows in the quote, each its inputs as *quote* holds them and its outputs as
    *tables* priced them: a one-row table's one row, an input table's rows as the quote holds
    them, each beside the priced row whose index holds the same value as text, so the rows
    line up by index, never by place, as ``pricedRows`` lines them up in the view; and a
    table of outputs alone its priced rows in their order."""
    priced = [row for row in tables.get(table.name) or [] if isinstance(row, dict)]
    held = quote.get(table.name)
    if table.rows == "one":
        return [(held if isinstance(held, dict) else {}, priced[0] if priced else {})]
    if not table.has_inputs:
        return [({}, row) for row in priced]
    index = table.index
    assert index is not None, f"{table.name} has many rows, so it has an index"
    by_index: dict[str, Mapping[str, Any]] = {}
    for row in priced:
        if row.get(index.name) is not None:
            by_index.setdefault(str(row[index.name]), row)
    typed = held if isinstance(held, list) else []
    return [
        (row, by_index.get(str(row.get(index.name)), {})) for row in typed if isinstance(row, dict)
    ]


def _typed_value(
    table: SchemaTable,
    column: SchemaColumn,
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> Any:
    """*column*'s value in a row of *table*, an input's from *inputs* and an output's from
    *outputs*, typed by the column; a value not of its type refused, naming it."""
    if table.is_output(column.name):
        held = outputs.get(column.name)
        if column.type == "int" and isinstance(held, str) and _WHOLE_TEXT.fullmatch(held):
            # A whole number past what JSON holds exactly, as the wire carries it.
            held = int(held)
    else:
        held = inputs.get(column.name)
    try:
        return json_value(held, COLUMN_DTYPES[column.type])
    except JsonValueError:
        raise ExportRefusedError(
            f"{table.name}.{column.name} is {held!r}, not {column.type!r}: price the sample again."
        ) from None


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
    typed = sample_quote(spec)
    if to_json_safe(typed) != to_json_safe(dict(sample)):
        raise StaleExportError(
            "The sample changed after it was priced: export once it is priced again."
        )
    # The server's own typing of the sample priced, a whole number past what JSON holds
    # exactly kept whole, rather than the text the wire carries it as.
    return _exported(spec, component, with_stand_in(input_tables(spec), typed), tables, None)


def quote_export(
    form: FormSpec,
    component: str,
    values: Mapping[str, Any],
    tables: Mapping[str, list[Any]],
    release: str,
) -> tuple[str, bytes]:
    """The file name and the bytes of the Export of id *component* of *form*, run in the
    underwriter app on a saved quote: *values*, the quote's values as the sheets hold them,
    typed as its pricing typed them (:func:`haute._workbench_tables.quote_request`), its
    identity among them, *tables* its priced rows as the store keeps them, and *release*
    the release that priced them, which the workbook names. Refused as
    :func:`preview_export` refuses an Export."""
    spec = with_quote_details(form)
    return _exported(spec, component, quote_request(spec, values), tables, release)


def _exported(
    spec: FormSpec,
    component: str,
    quote: Mapping[str, Any],
    tables: Mapping[str, list[Any]],
    release: str | None,
) -> tuple[str, bytes]:
    """The file name and bytes of the Export of id *component* of *spec* (its quote_details
    given) on *quote*, typed as a request, and *tables*, its priced rows by table: the one
    path Preview's export and the app's take."""
    widget, target = _export_of(spec, component)
    problems = cells_problems(spec, target)
    if problems:
        name = widget.label.strip() or widget.name
        raise ExportRefusedError(
            f'Export "{name}" cannot be written: it ' + "; it ".join(problems) + "."
        )

    def value_of(cell: ExportCell) -> Any:
        found = _own_column(spec, cell)
        assert found is not None
        table, column = found
        inputs, outputs = _table_rows(table, quote, tables)[0]
        return _typed_value(table, column, inputs, outputs)

    def rows_of(block: ExportBlock) -> list[list[Any]]:
        held = _block_columns(spec, block)
        assert held is not None
        table, columns = held
        return [
            [_typed_value(table, column, inputs, outputs) for column in columns]
            for inputs, outputs in _table_rows(table, quote, tables)
        ]

    data = cells_workbook(spec, target, value_of, rows_of, release=release)
    details = quote.get(QUOTE_DETAILS_NAME)
    return export_file_name(target.file_name, details if isinstance(details, dict) else {}), data
