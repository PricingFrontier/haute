"""The workbench's form: its canonical shape (specs/workbench).

The form is what the workbench edits: the schema of typed tables the quote is keyed into,
the sheets, each a stack of sections holding Tables and Collections that show those tables'
columns, Charts that plot them, Headings that name them and the Imports and Exports that
will fill them and take them out, and the sample quote typed into them while building. A
project keeps it as the files of its pipeline folder's ``workbench/`` folder, which
`_workbench_folder` reads and writes. A release ships it, and the quote store keeps it, as
one document (`render_form`) with one spelling: every field is written, defaults included.
An unknown field is refused by name.

Every form holds Haute's own input table, ``quote_details``, first: the details of a quote
(its reference, name, insured, broker, dates and currency, its stage, who it is with and
whether it is archived, which the underwriter app sets itself, whether it is a renewal, and
its option, which the app sets too). Its structure is Haute's and a form that changes it is
refused; a form read or saved without it has it added, and one holding its columns as an
earlier Haute wrote them has the newer ones added after them, the one exception to nothing
migrating, so a project written before either takes them on. Every other table leads with
the quote's identity, its reference and option, locked, added the same way to a table
without it unless a column of the table's own holds either name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Strict,
    Tag,
    model_validator,
)

from haute._explore_pivots import PIVOT_DECIMAL_PLACES_MAX
from haute._hashing import content_hash_bytes
from haute._workbench_config import WorkbenchError

#: The column types of the Quote Input's tables (``haute._api_input_schema.ColumnType``).
ColumnType = Literal["int", "float", "str", "bool", "date"]

#: How a number column shows: the Explore pivot's number formats
#: (``haute._explore_pivots.PIVOT_NUMBER_FORMATS``).
ColumnFormat = Literal[
    "general", "number", "percent", "currency_gbp", "currency_usd", "currency_eur"
]

#: How tall a Table's rows or a Collection's boxes are: Haute's compact ones, or roomier ones.
Density = Literal["compact", "comfortable"]

#: The lines a Table draws: between its rows and before each output column, between every
#: column too, or none.
TableGridlines = Literal["rows", "all", "none"]

#: How a Table's header, or a Collection, shows its labels: as written, or in capitals.
TableHeader = Literal["caps", "plain"]

#: Which way a Table's labels read: level, turned to read upwards or downwards, or their
#: letters stacked one under another.
HeaderOrientation = Literal["horizontal", "up", "down", "stacked"]

#: How heavy a Table's lines, a Collection's boxes' borders or either's frame are: 1, 2 or
#: 3 pixels.
LineWeight = Literal["thin", "medium", "thick"]

#: How a Table's gridlines, or a Collection's boxes' borders, are drawn.
LineStyle = Literal["solid", "dashed", "dotted"]

#: Where a Table column's, or a Collection field's, values or label sit across it: as its
#: type puts them, numbers on the right, or as chosen; a label while ``auto`` sits as its
#: component puts it.
ColumnAlign = Literal["auto", "left", "center", "right"]

#: The sizes a Table's or a Collection's text is in, in pixels.
FontSize = Literal[10, 11, 12, 13, 14, 16, 18]

#: How narrow and how wide a Table's column, or a Collection's boxes, can be set, in pixels,
#: as the view's properties panel takes them (``frontend/src/utils/workbenchForm.ts``).
COLUMN_WIDTH_MIN = 24
COLUMN_WIDTH_MAX = 1000

#: How little and how much of its field a Collection's label beside its box can take, in
#: percent, as the view's properties panel takes them, so the box always keeps some.
LABEL_WIDTH_MIN = 1
LABEL_WIDTH_MAX = 99

#: Where a Collection's labels sit: above its boxes, or beside them.
CollectionLabels = Literal["above", "beside"]

#: A colour the sheets are drawn in, as ``#RRGGBB`` in capitals: one spelling, as the picker
#: writes it.
Colour = Annotated[str, Field(pattern=r"^#[0-9A-F]{6}$")]

#: How a Chart draws its values: as columns, a line, a filled area, or the slices of a pie.
ChartType = Literal["column", "line", "area", "pie"]

#: Where a Chart's legend sits, or none.
ChartLegend = Literal["bottom", "top", "left", "right", "none"]

#: How large a Heading's text is: a sheet's title, a section's heading, or a label.
HeadingSize = Literal["large", "medium", "small"]

#: Where a Heading's text sits across its width.
HeadingAlign = Literal["left", "center", "right"]

#: How an Import fills a many-row table: its rows replaced, added to, or updated by the
#: table's keys, the rows no key matches added.
ImportMode = Literal["replace", "append", "update"]

#: Whether an Export's file heads each column by its name or by its label, and holds each
#: value as the quote holds it or as the sheets show it, in its column's format.
ExportHeaders = Literal["names", "labels"]
ExportValues = Literal["raw", "formatted"]

#: The furthest down a workbook's sheet an Import's header row can be, as the view's
#: properties panel takes it (``frontend/src/utils/workbenchForm.ts``).
HEADER_ROW_MAX = 100

#: A query parameter's name, as ``:name`` in an Import's query: an identifier.
PARAMETER_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"

#: What a condition tests its column's value for: a tick box ticked or not; any other
#: column filled or empty, or equal to a value or not; a Text column holding a value, at its
#: start or its end, or not; a Text or number column one of some values or none of them; a
#: number or a Date above, at least, below or at most a value.
ConditionTest = Literal[
    "ticked",
    "not_ticked",
    "filled",
    "empty",
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "one_of",
    "not_one_of",
    "above",
    "at_least",
    "below",
    "at_most",
]

#: The most days a Date's test against another Date adds to its day or takes away: a hundred
#: years, so every day both sides count stays a whole number they hold exactly.
DAYS_MAX = 36_500

#: The days added to another Date field's day before a Date is compared with it.
Days = Annotated[int, Strict(), Field(ge=-DAYS_MAX, le=DAYS_MAX)]


#: A sample cell as typed: text, or a tick. Strict, so a number or a null is refused.
Cell = Annotated[str, Strict()] | Annotated[bool, Strict()]

#: How tall a new sheet's one section is: the whole sheet until another is added, as the
#: view starts a new sheet (``frontend/src/utils/sheetGeometry.ts``).
BLANK_SECTION_HEIGHT = 560

#: How wide a new form's sheet is, as the view starts one (``frontend/src/utils/sheetGeometry.ts``).
BLANK_SHEET_WIDTH = 1280

#: The most rows a Table starts with, and columns a Collection lays its boxes in, as the
#: view's properties panel takes them (``frontend/src/utils/workbenchForm.ts``).
TABLE_ROWS_MAX = 50
COLLECTION_COLUMNS_MAX = 12


class WorkbenchFormError(WorkbenchError):
    """The form folder cannot be read as a form: a file unreadable, not JSON or misshapen,
    or the folder not the files its index names."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


#: The longest name a sheet's title makes.
SHEET_NAME_MAX = 64


def sheet_name(title: str) -> str:
    """The name a sheet's title makes, its file's name: lower case, each run of other
    characters than ASCII letters and digits one ``_``, none at either end, 64 at most."""
    name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return name[:SHEET_NAME_MAX].rstrip("_")


class _Placed(_Strict):
    """A widget's name and its place in its section, in pixels from the section's top-left
    corner. The name is the builder's, which the editor gives it as it adds or copies it and
    changes in its panel: it names the component's file and places it in its sheet's file,
    and the folder holds it to an identifier no other component has whatever its case
    (``haute._workbench_folder.folder_name_problems``)."""

    id: str
    name: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class FieldRef(_Strict):
    """A schema column a widget shows, by its table's name and its own (READ-23)."""

    table: str
    column: str


class Condition(_Strict):
    """A test of the value in a one-row table's column, the column by table and column name.

    The shape is all the file holds a condition to. Whether its column, test and values
    fit one another is ``haute._workbench_visibility.condition_fits``'s question, asked
    where conditions are tested: retyping a column can unfit a condition the form holds.
    """

    table: str
    column: str
    op: ConditionTest
    #: As typed: none for the ticks, ``filled`` and ``empty``, one or more for ``one_of``
    #: and ``not_one_of``, else one, or none while ``field`` gives it.
    values: list[Annotated[str, Strict()]] = Field(default_factory=list)
    #: Another one-row input table's column of the same type, whose value a test of one
    #: value tests against in place of one typed.
    field: FieldRef | None = None
    #: Added to that field's day when the column is a Date.
    days: Days = 0


class RowTest(_Strict):
    """A test a row of a many-row table must pass: a ``Condition``'s column, test and values,
    its table the condition's that holds it, and another column of the same row, by name,
    to test against."""

    column: str
    op: ConditionTest
    values: list[Annotated[str, Strict()]] = Field(default_factory=list)
    field: str | None = None
    days: Days = 0


class RowsCondition(_Strict):
    """A many-row table's rows, by table name, passing all of its tests in the same row: any
    of them, or every one, as ``rows`` says."""

    table: str
    rows: Literal["any", "every"]
    where: list[RowTest] = Field(min_length=1)


#: What a count or a total tests its number for.
CountTest = Literal["equals", "not_equals", "above", "at_least", "below", "at_most"]


class RowCount(_Strict):
    """How many of a many-row table's rows, by table name, pass all of its tests in the same
    row: every row while it has none."""

    table: str
    where: list[RowTest]
    op: CountTest
    count: Annotated[int, Strict(), Field(ge=0)]


#: Which total of a number column's values a total takes.
TotalKind = Literal["sum", "min", "max", "average"]


class RowTotal(_Strict):
    """A total of a number column's values in the rows of a many-row table, by table name,
    passing all of its tests in the same row, every row while it has none, tested against
    a number typed as text."""

    table: str
    total: TotalKind
    column: str
    where: list[RowTest]
    op: CountTest
    value: Annotated[str, Strict()]


def _condition_kind(value: Any) -> str:
    """Which entry of a ``show_when`` *value* is, by its fields: a group holds
    ``conditions``, a total ``total``, a count ``count``, a condition in any or every row
    ``rows``; anything else is read as one, which then refuses it."""
    fields = value if isinstance(value, dict) else getattr(type(value), "model_fields", {})
    if "conditions" in fields:
        return "group"
    if "total" in fields:
        return "total"
    if "count" in fields:
        return "count"
    return "rows" if "rows" in fields else "one"


_ONE_CONDITION = (
    Annotated[Condition, Tag("one")]
    | Annotated[RowsCondition, Tag("rows")]
    | Annotated[RowCount, Tag("count")]
    | Annotated[RowTotal, Tag("total")]
)

#: One condition: of a one-row table's column, or of a many-row table's rows.
SingleCondition = Annotated[_ONE_CONDITION, Discriminator(_condition_kind)]

#: A condition on a many-row table's rows.
ListCondition = RowsCondition | RowCount | RowTotal


#: How many of a ``show_when``'s or a group's conditions must hold: all, any, or at least a
#: number of them.
Match = Literal["all", "any"] | Annotated[int, Strict(), Field(ge=1)]


class ConditionGroup(_Strict):
    """Conditions that count as one: holding while all of them hold, any, or at least a
    number of them, as ``match`` says. A group holds no group."""

    match: Match
    conditions: list[SingleCondition] = Field(min_length=1)


#: An entry of a ``show_when``: one condition or a group of them.
ShowWhenEntry = Annotated[
    _ONE_CONDITION | Annotated[ConditionGroup, Tag("group")], Discriminator(_condition_kind)
]


class ShowWhen(_Strict):
    """When a sheet, a section, a widget or a widget's field shows: always, without
    conditions; else while all of them hold, any, or at least a number of them, as ``match``
    says, each a condition or a group of them."""

    match: Match = "all"
    conditions: list[ShowWhenEntry] = Field(default_factory=list)


class ColumnLook(_Strict):
    """How one of a Table's columns, or one of a Collection's fields, looks and when it shows,
    its field by table and column name: each colour the component's own while None, its values
    placed as its type puts them, or as the Collection places its boxes', while ``align`` is
    ``auto``, its label as its component places its labels while ``header_align`` is, and
    over a Table's column as its values sit while the Table's is too, while ``width`` is
    None, a Table's column as wide as the grid makes it and a Collection's box as wide as the
    Collection makes it, and shown with its component while ``show_when`` has no
    conditions."""

    table: str
    column: str
    #: Its label's fill and text, and its cells', or its box's, fill and text.
    header_color: Colour | None = None
    header_font_color: Colour | None = None
    header_align: ColumnAlign = "auto"
    cell_color: Colour | None = None
    font_color: Colour | None = None
    bold: bool = False
    align: ColumnAlign = "auto"
    width: int | None = Field(default=None, ge=COLUMN_WIDTH_MIN, le=COLUMN_WIDTH_MAX)
    show_when: ShowWhen = Field(default_factory=ShowWhen)


#: A column look's fields that are not how it looks: its field and when it shows.
_NOT_ITS_LOOK = {"table", "column", "show_when"}

#: A column look's look that changes nothing: with a ``show_when`` without conditions, which
#: always shows whatever its ``match``, a look no Table or Collection holds, as a column
#: without a look of its own has none.
_PLAIN_LOOK = ColumnLook(table="", column="").model_dump(exclude=_NOT_ITS_LOOK)


def _check_column_looks(widget_id: str, fields: list[FieldRef], looks: list[ColumnLook]) -> None:
    """Refuse a component's look for a column it does not show, a second look for one column,
    or a look that changes nothing."""
    shown = {(field.table, field.column) for field in fields}
    seen: set[tuple[str, str]] = set()
    for look in looks:
        at = (look.table, look.column)
        name = f"{look.table}.{look.column}"
        if at not in shown:
            raise ValueError(f"widget {widget_id} has a look for {name}, which it does not show")
        if at in seen:
            raise ValueError(f"widget {widget_id} has two looks for {name}")
        seen.add(at)
        if look.model_dump(exclude=_NOT_ITS_LOOK) == _PLAIN_LOOK and not look.show_when.conditions:
            raise ValueError(
                f"widget {widget_id}'s look for {name} changes nothing: a column without a "
                "look of its own has none"
            )


class TableInputWidget(_Placed):
    """A Table: a grid showing columns of many-row schema tables, each of its columns given
    a look of its own looking as its ``column_looks`` entry says."""

    type: Literal["tableInput"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    rows: int = Field(default=3, ge=1, le=TABLE_ROWS_MAX)
    # How the grid looks, whether its rows are a set structure, none inserted or
    # deleted, whether it is transposed, its fields down its side and its rows across, and
    # whether its fields' schema groups head their labels: by default numbered, compact,
    # lined between rows, striped, not fixed, its header as written, framed, not
    # transposed and without groups.
    row_numbers: bool = True
    density: Density = "compact"
    gridlines: TableGridlines = "rows"
    striped: bool = True
    fixed_rows: bool = False
    header: TableHeader = "plain"
    frame: bool = True
    transposed: bool = False
    groups: bool = False
    # Its text, its header, its lines and its frame, as a spreadsheet's are set: by default
    # 12px text, its header's the same size, or a pixel smaller in capitals, bold, level and
    # unwrapped, filled and coloured as the sheets' theme says, each label placed as its
    # column's look says, thin solid gridlines and a thin frame, coloured as the theme says.
    font_size: FontSize = 12
    header_font_size: FontSize | None = None
    header_bold: bool = True
    header_wrap: bool = False
    header_orientation: HeaderOrientation = "horizontal"
    header_color: Colour | None = None
    header_font_color: Colour | None = None
    header_align: ColumnAlign = "auto"
    grid_color: Colour | None = None
    grid_weight: LineWeight = "thin"
    grid_style: LineStyle = "solid"
    frame_color: Colour | None = None
    frame_weight: LineWeight = "thin"
    #: The looks of the columns given one of their own, each a column it shows, in no set
    #: order.
    column_looks: list[ColumnLook] = Field(default_factory=list)

    @model_validator(mode="after")
    def _looks_at_its_columns(self) -> TableInputWidget:
        _check_column_looks(self.id, self.fields, self.column_looks)
        return self


class CollectionWidget(_Placed):
    """A Collection: boxes showing columns of one-row schema tables, ``columns`` across, with
    a gap across it wherever one of its ``spacers`` sits among them, each of its fields given
    a look of its own looking as its ``column_looks`` entry says."""

    type: Literal["collection"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    columns: int = Field(default=3, ge=1, le=COLLECTION_COLUMNS_MAX)
    # How its boxes look: by default labelled above, comfortable and framed.
    labels: CollectionLabels = "above"
    density: Density = "comfortable"
    frame: bool = True
    # Its text, its labels, its boxes and its frame, as a Table's are set: by default 12px
    # text, its labels the same size, or a pixel smaller in capitals, as written, neither
    # bold nor wrapped, unfilled, coloured as the sheets' theme says, at their fields'
    # start and, beside their boxes, two fifths of a field wide; its boxes' borders thin and
    # solid, coloured as the theme says, their values placed as their types put them, and
    # each box as wide as its label leaves; a thin frame, coloured as the theme says.
    font_size: FontSize = 12
    header: TableHeader = "plain"
    header_font_size: FontSize | None = None
    header_bold: bool = False
    header_wrap: bool = False
    header_color: Colour | None = None
    header_font_color: Colour | None = None
    header_align: ColumnAlign = "auto"
    label_width: int | None = Field(default=None, ge=LABEL_WIDTH_MIN, le=LABEL_WIDTH_MAX)
    box_style: LineStyle = "solid"
    box_weight: LineWeight = "thin"
    box_color: Colour | None = None
    box_align: ColumnAlign = "auto"
    box_width: int | None = Field(default=None, ge=COLUMN_WIDTH_MIN, le=COLUMN_WIDTH_MAX)
    frame_color: Colour | None = None
    frame_weight: LineWeight = "thin"
    #: The looks of the fields given one of their own, each a column it shows, in no set
    #: order.
    column_looks: list[ColumnLook] = Field(default_factory=list)
    #: Each spacer the number of its fields before it, in order, two at one place a wider
    #: gap; the fields after one start a new row.
    spacers: list[Annotated[int, Field(ge=0)]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _spaces_its_fields(self) -> CollectionWidget:
        if self.spacers != sorted(self.spacers):
            raise ValueError(
                f"widget {self.id}'s spacers are out of order: each is the number of fields "
                "before it, in order"
            )
        if self.spacers and self.spacers[-1] > len(self.fields):
            raise ValueError(
                f"widget {self.id} has a spacer after {self.spacers[-1]} fields, but shows "
                f"{len(self.fields)}"
            )
        return self

    @model_validator(mode="after")
    def _looks_at_its_columns(self) -> CollectionWidget:
        _check_column_looks(self.id, self.fields, self.column_looks)
        return self


class ChartWidget(_Placed):
    """A Chart: number columns plotted, of one-row tables a point each, or of many-row tables
    whose rows line up a series each over their rows. It takes nothing typed."""

    type: Literal["chart"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    #: What names the rows of its many-row tables, as a field is named; None numbers them.
    category: FieldRef | None = None
    # How it draws: by default columns, upright, unstacked, the legend at the bottom and no
    # data labels.
    chart_type: ChartType = "column"
    orientation: Literal["vertical", "horizontal"] = "vertical"
    stacked: bool = False
    legend: ChartLegend = "bottom"
    data_labels: bool = False


class HeadingWidget(_Placed):
    """A Heading: text naming a section of the sheet, or the sheet itself, with a line under
    it saying what the section is for. It shows no schema columns and takes nothing typed."""

    type: Literal["heading"]
    text: str = ""
    #: Under the text; empty for none.
    description: str = ""
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    size: HeadingSize = "medium"
    align: HeadingAlign = "left"


def _check_once(widget_id: str, things: str, keys: list[str]) -> None:
    """Refuse a key a button's list holds twice: one name per column, one per table."""
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise ValueError(f"widget {widget_id} has {key!r} twice among its {things}")
        seen.add(key)


class CsvImport(_Strict):
    """A CSV the underwriter picks as they press the Import, its fields split by its
    ``delimiter``, found from the file while ``auto``."""

    format: Literal["csv"]
    delimiter: Literal["auto", "comma", "semicolon", "tab"]


class ExcelImport(_Strict):
    """A sheet of an Excel workbook the underwriter picks, its columns named by its
    ``header_row``, counted from 1, and its rows those under it."""

    format: Literal["excel"]
    #: The workbook's sheet by its name; empty for its first.
    sheet: str
    header_row: int = Field(ge=1, le=HEADER_ROW_MAX)


class QueryParameter(_Strict):
    """A ``:name`` in an Import's query, given the value of a column by table and column name."""

    name: Annotated[str, Field(pattern=PARAMETER_NAME)]
    table: str
    column: str


class SqlImport(_Strict):
    """A query run on a named connection, whose credentials the environment holds, never
    the form, each of its parameters given the value of the column bound to it."""

    format: Literal["sql"]
    #: The connection by name; empty while none is chosen.
    connection: str
    query: str
    parameters: list[QueryParameter]


ImportSource = Annotated[CsvImport | ExcelImport | SqlImport, Field(discriminator="format")]


class ColumnName(_Strict):
    """A column of the table an Import fills, by name, and the name of the column it is read
    from in the file or the query's answer."""

    column: str
    name: Annotated[str, Field(min_length=1, pattern=r"\S")]


class ImportWidget(_Placed):
    """An Import: a button an underwriter presses to fill an input table from a file or a
    database. It shows no schema columns and takes nothing typed; nothing runs it yet, so
    it is drawn disabled where the sheets are used."""

    type: Literal["import"]
    #: The text on the button.
    label: str
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    source: ImportSource
    #: The input table it fills, by name; None while it fills none.
    table: str | None
    mode: ImportMode
    #: The columns of its table read from a column named otherwise, in no set order; a column
    #: without one is read from the column of its own name or label, whatever its case.
    column_names: list[ColumnName]

    @model_validator(mode="after")
    def _names_each_once(self) -> ImportWidget:
        if self.column_names and self.table is None:
            raise ValueError(f"widget {self.id} names columns but fills no table")
        _check_once(self.id, "column names", [named.column for named in self.column_names])
        if isinstance(self.source, SqlImport):
            _check_once(self.id, "parameters", [given.name for given in self.source.parameters])
        return self


class _FileExport(_Strict):
    """A file an Export writes: its name, each ``{name}`` in it standing for the quote's
    value of that column of quote_details, its extension left out, how its columns are
    headed and how its values are written."""

    file_name: str
    headers: ExportHeaders
    values: ExportValues


class CsvExport(_FileExport):
    """A CSV of the one table an Export exports, its fields split by its ``delimiter``."""

    format: Literal["csv"]
    delimiter: Literal["comma", "semicolon", "tab"]


class ExcelExport(_FileExport):
    """An Excel workbook of a sheet per table an Export exports, each named by its table."""

    format: Literal["excel"]


class TableName(_Strict):
    """A table an Export writes to a database, by name, and the name of the table it is
    written to there."""

    table: str
    name: Annotated[str, Field(min_length=1, pattern=r"\S")]


class SqlExport(_Strict):
    """The tables an Export writes to a database on a named connection, each to the table
    of its own name or the name given it, the quote's rows there written again."""

    format: Literal["sql"]
    #: The connection by name; empty while none is chosen.
    connection: str
    #: The tables written to a table named otherwise, in no set order.
    table_names: list[TableName]


ExportTarget = Annotated[CsvExport | ExcelExport | SqlExport, Field(discriminator="format")]


class ExportWidget(_Placed):
    """An Export: a button an underwriter presses to take the quote's tables out to a file
    or a database. It shows no schema columns and takes nothing typed; nothing runs it yet,
    so it is drawn disabled where the sheets are used."""

    type: Literal["export"]
    #: The text on the button.
    label: str
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    target: ExportTarget
    #: The tables it exports, by name, in the order chosen.
    tables: list[str]

    @model_validator(mode="after")
    def _exports_each_table_once(self) -> ExportWidget:
        _check_once(self.id, "tables", self.tables)
        if isinstance(self.target, SqlExport):
            named = [entry.table for entry in self.target.table_names]
            _check_once(self.id, "table names", named)
            for table in named:
                if table not in self.tables:
                    raise ValueError(f"widget {self.id} names a table {table!r} it does not export")
        return self


Widget = Annotated[
    TableInputWidget | CollectionWidget | ChartWidget | HeadingWidget | ImportWidget | ExportWidget,
    Field(discriminator="type"),
]


class Section(_Strict):
    """A section of a sheet: a strip across the sheet's whole width, ``height`` pixels tall,
    under the sections before it; when it shows; and its widgets, each placed freely inside
    it."""

    id: str
    #: The builder's name for it; what an underwriter reads at its top is a Heading in it.
    title: str
    height: int = Field(gt=0)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    widgets: list[Widget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _holds_its_widgets(self) -> Section:
        for widget in self.widgets:
            if widget.y + widget.h > self.height:
                raise ValueError(
                    f"widget {widget.id} reaches {widget.y + widget.h}px down, below the "
                    f"section's height of {self.height}px"
                )
        return self


class Page(_Strict):
    """A sheet: ``width`` pixels wide, when it shows, and its sections, stacked one under
    the next."""

    id: str
    title: str
    width: int = Field(gt=0)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    sections: list[Section] = Field(min_length=1)

    @model_validator(mode="after")
    def _holds_its_widgets(self) -> Page:
        for section in self.sections:
            for widget in section.widgets:
                if widget.x + widget.w > self.width:
                    raise ValueError(
                        f"widget {widget.id} reaches {widget.x + widget.w}px across, past the "
                        f"sheet's width of {self.width}px"
                    )
        return self


class OptionsSource(_Strict):
    """A column of a CSV in the pipeline's ``files/params/`` folder: the file by its name in
    that folder, and the column by its name in the file's header."""

    file: str
    column: str


class SchemaColumn(_Strict):
    #: What identifies it in its table, no two there differing only in case (READ-23).
    name: str
    type: ColumnType = "str"
    #: What widgets call it; empty means the name, made readable.
    label: str = ""
    #: The group it is in, a name it shares with the columns grouped with it; empty for none.
    group: str = ""
    #: In a many-row table, part of what says which row is which.
    key: bool = False
    # The rules an input column's value must meet; an output column takes `required` alone,
    # which marking a quote final checks, and ignores the rest.
    required: bool = False
    min: float | None = None
    max: float | None = None
    options: list[str] = Field(default_factory=list)
    #: Where ``options`` were read from: a params file's column, its distinct values copied
    #: in when picked or refreshed, and read again when the underwriter app is built. None
    #: while they are typed.
    options_source: OptionsSource | None = None
    #: In a many-row input table, the table has a row for each of ``options``, in their
    #: order, each holding its value here (``haute._workbench_tables.preset_rows``).
    row_per_value: bool = False
    #: The table's index: each row's number, from 1, as the rows stand. Nothing is typed
    #: into it, and it is an Integer.
    index: bool = False
    #: Shown beside its label when the pointer rests on it; empty shows nothing.
    help: str = ""
    #: What an empty input cell shows, in place of the blank.
    placeholder: str = ""
    #: What a new quote starts with in it, as if typed: text, or a tick; empty, or an
    #: unticked box, for none. Only a one-row input table's columns start a quote with one.
    default_value: Cell = ""
    # How a number shows wherever the sheets show it, as an Explore pivot's values show:
    # a percent is the value times 100, a currency's sign before it. ``general`` with no
    # decimal places leaves an input as typed and an output as the data preview shows a
    # value. Columns of other types ignore them.
    number_format: ColumnFormat = "general"
    decimal_places: int | None = Field(default=None, ge=0, le=PIVOT_DECIMAL_PLACES_MAX)
    use_grouping: bool = True


class SchemaTable(_Strict):
    #: What identifies it in the form, no two differing only in case (READ-23).
    name: str
    #: Sent to the pricing engine, or returned by it.
    role: Literal["input", "output"] = "input"
    #: One row per quote, such as policy details, or many, such as an equipment schedule.
    rows: Literal["one", "many"] = "one"
    columns: list[SchemaColumn] = Field(default_factory=list)


class FormSchema(_Strict):
    """The data the sheets work with: tables of named, typed columns."""

    tables: list[SchemaTable] = Field(default_factory=list)


class Theme(_Strict):
    """The sheets' colours, wherever they show, each the editor's own while None."""

    #: The sheet behind its sections.
    sheet_color: Colour | None = None
    #: Framed components' background and their borders: frames, rules, gridlines and boxes.
    background_color: Colour | None = None
    border_color: Colour | None = None
    #: The text of Headings and of components' titles, labels and values.
    font_color: Colour | None = None
    #: The fill of input cells and boxes, and of output cells and boxes.
    input_cell_color: Colour | None = None
    output_cell_color: Colour | None = None


class FormSpec(_Strict):
    """The form: what the workbench edits and its ``workbench/`` folder stores."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    version: Literal[1] = 1
    name: str
    theme: Theme = Field(default_factory=Theme)
    #: ``schema`` in the file; ``BaseModel`` already has a ``schema`` attribute.
    data_schema: FormSchema = Field(default_factory=FormSchema, alias="schema")
    pages: list[Page] = Field(min_length=1)
    #: The sample quote typed while building: rows by schema table name, each keyed by
    #: column name, each value as typed (text, or a tick). The Workbench Input's previews run on it.
    #: Held as typed, strictly: a number or a null for a cell is not a form the view writes.
    sample: dict[str, list[dict[str, Cell]]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _gives_each_part_an_id(cls, data: Any) -> Any:
        """A sheet, a section and a component read from a file or a release hold no id: each
        is given one from what identifies it, its sheet's file name, its section's title and
        its name, so the editor can select and move it. An id a part holds, one
        the editor made, is kept. The caller's data is left as it was."""
        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list) or all(
            isinstance(page, dict) and "id" in page for page in pages
        ):
            return data
        given: list[Any] = []
        for page in pages:
            if not isinstance(page, dict):
                given.append(page)
                continue
            page_id = page.get("id") or sheet_name(str(page.get("title", "")))
            sections: list[Any] = []
            for section in page.get("sections") or []:
                if not isinstance(section, dict):
                    sections.append(section)
                    continue
                section_id = section.get("id") or f"{page_id}/{section.get('title', '')}"
                widgets = [
                    {**widget, "id": f"{section_id}/{widget.get('name')}"}
                    if isinstance(widget, dict) and "id" not in widget
                    else widget
                    for widget in section.get("widgets") or []
                ]
                sections.append({**section, "id": section_id, "widgets": widgets})
            given.append({**page, "id": page_id, "sections": sections})
        return {**data, "pages": given}

    @model_validator(mode="before")
    @classmethod
    def _brings_quote_details_up_to_date(cls, data: Any) -> Any:
        """A form saved by an earlier Haute holds ``quote_details`` with the columns it had
        then, which are brought to today's: a column under the name it had then
        (``QUOTE_DETAILS_NAMED_BEFORE``) takes today's, one Haute makes no more
        (``QUOTE_DETAILS_RETIRED``) is dropped, those it holds keep what the project gave
        them, the missing ones are added, and all of them take today's order. A column of
        another name, or one retyped, stays for the form's check to refuse. The caller's
        data is left as it was."""
        schema = data.get("schema") if isinstance(data, dict) else None
        if not isinstance(schema, dict) or not isinstance(schema.get("tables"), list):
            return data
        tables = schema["tables"]
        for index, table in enumerate(tables):
            if not isinstance(table, dict) or table.get("name") != QUOTE_DETAILS_NAME:
                continue
            columns = table.get("columns")
            if not isinstance(columns, list) or not all(
                isinstance(column, dict) for column in columns
            ):
                return data
            renamed = [
                _named_today(column)
                for column in columns
                if column.get("name") not in QUOTE_DETAILS_RETIRED
            ]
            names = [column.get("name") for column in renamed]
            if len(set(names)) != len(names):
                # Two columns under one name are the form's check to refuse.
                return data
            held = {column.get("name"): column for column in renamed}
            known = [
                held.get(name, {"name": name, "type": column_type})
                for name, column_type in QUOTE_DETAILS_COLUMNS
            ]
            others = [column for column in renamed if column.get("name") not in QUOTE_DETAILS_NAMES]
            upgraded = {**table, "columns": [*known, *others]}
            return {
                **data,
                "schema": {**schema, "tables": [*tables[:index], upgraded, *tables[index + 1 :]]},
            }
        return data

    @model_validator(mode="before")
    @classmethod
    def _gives_every_table_the_quote_identity(cls, data: Any) -> Any:
        """Every table but ``quote_details`` leads with the quote's identity
        (``IDENTITY_COLUMNS``), added at its head when it has none, the caller's data left as
        it was. A table with a column of its own under either name is left without it: the
        clash is the project's to resolve (:func:`identity_clashes`)."""
        schema = data.get("schema") if isinstance(data, dict) else None
        if not isinstance(schema, dict) or not isinstance(schema.get("tables"), list):
            return data
        held: list[Any] = []
        for table in schema["tables"]:
            columns = table.get("columns", []) if isinstance(table, dict) else None
            if (
                not isinstance(columns, list)
                or table.get("name") == QUOTE_DETAILS_NAME
                or any(
                    isinstance(column, dict) and column.get("name") in IDENTITY_NAMES
                    for column in columns
                )
            ):
                held.append(table)
                continue
            identity = [{"name": name, "type": kind} for name, kind in IDENTITY_COLUMNS]
            held.append({**table, "columns": [*identity, *columns]})
        return {**data, "schema": {**schema, "tables": held}}

    @model_validator(mode="after")
    def _keeps_quote_details_as_hautes(self) -> FormSpec:
        for table in self.data_schema.tables:
            if table.name == QUOTE_DETAILS_NAME:
                _check_quote_details(table)
        return self

    @model_validator(mode="after")
    def _gives_each_its_own_id(self) -> FormSpec:
        """Refuse an id given twice: the editor finds, moves and removes a sheet, a section
        or a component by its id, so a block copied by hand or by a merge would be two
        things it takes for one. Tables and columns are known by their names, which the
        folder holds apart (``haute._workbench_folder.folder_name_problems``)."""
        sections = [section for page in self.pages for section in page.sections]
        _check_ids("sheets", [page.id for page in self.pages])
        _check_ids("sections", [section.id for section in sections])
        _check_ids("components", [w.id for section in sections for w in section.widgets])
        return self


def _check_ids(things: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for given in ids:
        if given in seen:
            raise ValueError(f"Two {things} have the id {given!r}: each needs one of its own")
        seen.add(given)


#: Haute's table of a quote's details, by its name, which identifies it (READ-23).
QUOTE_DETAILS_NAME = "quote_details"
#: Why another table under its name is refused, in the words the view's schema editor uses
#: too (``frontend/src/utils/workbenchForm.ts``).
QUOTE_DETAILS_REFUSAL = (
    f"{QUOTE_DETAILS_NAME} is Haute's own table of a quote's details: name yours otherwise"
)
#: Its columns, in order: each one's name and type. They are a quote's administration,
#: whatever the company or line of business.
QUOTE_DETAILS_COLUMNS: tuple[tuple[str, ColumnType], ...] = (
    ("quote_reference", "str"),
    ("quote_option", "int"),
    ("quote_name", "str"),
    ("insured", "str"),
    ("broker", "str"),
    ("inception_date", "date"),
    ("expiry_date", "date"),
    ("is_renewal", "bool"),
    ("stage", "str"),
    ("assignee", "str"),
    ("archived", "bool"),
    ("created_date", "date"),
    ("created_by", "str"),
    ("modified_date", "date"),
    ("modified_by", "str"),
    ("haute_version", "str"),
)
QUOTE_DETAILS_NAMES: frozenset[str] = frozenset(name for name, _ in QUOTE_DETAILS_COLUMNS)
#: The names an earlier Haute gave its columns, by today's: a form holding one takes today's.
QUOTE_DETAILS_NAMED_BEFORE: dict[str, str] = {"quote_reference": "reference"}
#: The columns an earlier Haute made that it makes no more: a form holding one drops it.
QUOTE_DETAILS_RETIRED: frozenset[str] = frozenset({"valid_until", "currency"})
#: The columns the underwriter app sets itself, whatever a page sends: read only on its
#: sheets, taking no rules, and in every request whatever the sheets hide.
QUOTE_DETAILS_MANAGED: frozenset[str] = frozenset(
    {
        "quote_reference",
        "quote_option",
        "stage",
        "assignee",
        "archived",
        "created_date",
        "created_by",
        "modified_date",
        "modified_by",
        "haute_version",
    }
)
#: The columns of quote_details the app changes over a quote's life: the quote store keeps
#: them, but no request holds them and no frame has a column for them, so changing them never
#: leaves a stored price that its stored inputs no longer give.
QUOTE_DETAILS_STORE_ONLY: frozenset[str] = frozenset(
    {"stage", "assignee", "archived", "modified_date", "modified_by", "haute_version"}
)
#: The quote's identity at the head of every other table, each one's name and type, as
#: ``quote_details`` names them.
IDENTITY_COLUMNS: tuple[tuple[str, ColumnType], ...] = (
    ("quote_reference", "str"),
    ("quote_option", "int"),
)
IDENTITY_NAMES: frozenset[str] = frozenset(name for name, _ in IDENTITY_COLUMNS)


def _named_today(column: Any) -> Any:
    """*column* of ``quote_details`` under today's name, when it holds the one an earlier Haute
    gave it."""
    if not isinstance(column, dict):
        return column
    for today, before in QUOTE_DETAILS_NAMED_BEFORE.items():
        if column.get("name") == before:
            return {**column, "name": today}
    return column


def quote_details_table() -> SchemaTable:
    """Haute's table of a quote's details, as a form holds it when nothing of it is set."""
    return SchemaTable(
        name=QUOTE_DETAILS_NAME,
        role="input",
        rows="one",
        columns=[
            SchemaColumn(name=name, type=column_type) for name, column_type in QUOTE_DETAILS_COLUMNS
        ],
    )


def _check_quote_details(table: SchemaTable) -> None:
    """Refuse a ``quote_details`` table that is not Haute's: its structure changed. Its
    columns' labels, groups, help and placeholders are the project's, as are the rules and
    defaults of the columns the app does not set."""
    shape = [(column.name, column.type) for column in table.columns]
    if (
        table.role != "input"
        or table.rows != "one"
        or shape != list(QUOTE_DETAILS_COLUMNS)
        or any(column.key or column.index or column.row_per_value for column in table.columns)
    ):
        raise ValueError(
            f"{QUOTE_DETAILS_NAME} is Haute's: a one-row input table of the columns "
            f"{', '.join(name for name, _ in QUOTE_DETAILS_COLUMNS)}, in that order, whose "
            "structure is not changed"
        )
    for column in table.columns:
        if column.name in QUOTE_DETAILS_MANAGED and (
            column.required
            or column.options
            or column.options_source is not None
            or column.min is not None
            or column.max is not None
            or column.default_value not in ("", False)
        ):
            raise ValueError(
                f"{QUOTE_DETAILS_NAME}.{column.name} is set by the underwriter app and takes no "
                "rules or default"
            )


#: The quote's identity columns as Haute makes them, at the head of every other table.
IDENTITY_SCHEMA_COLUMNS: tuple[SchemaColumn, ...] = tuple(
    SchemaColumn(name=name, type=column_type) for name, column_type in IDENTITY_COLUMNS
)


def holds_identity(table: SchemaTable) -> bool:
    """Whether *table* leads with the quote's identity as Haute makes it (READ-23): a column
    of either name anywhere else, or changed, is the project's own, a clash
    (:func:`identity_clashes`)."""
    return (
        table.name != QUOTE_DETAILS_NAME
        and tuple(table.columns[: len(IDENTITY_SCHEMA_COLUMNS)]) == IDENTITY_SCHEMA_COLUMNS
    )


def identity_clashes(spec: FormSpec) -> list[str]:
    """The tables a column of the project's own keeps from the quote's identity: each such
    column, said with what resolves it. Building the app and deploying refuse while any
    stands; the editor names them among the schema's problems."""
    return [
        f"{table.name}.{column.name} has a name Haute keeps for the quote's identity in every "
        "table: rename it, and the table takes quote_reference and quote_option at the next "
        "save"
        for table in spec.data_schema.tables
        if table.name != QUOTE_DETAILS_NAME and not holds_identity(table)
        for column in table.columns
        if column.name in IDENTITY_NAMES
    ]


def with_quote_details(spec: FormSpec) -> FormSpec:
    """*spec* holding Haute's ``quote_details`` table, first, added when it has none."""
    if any(table.name == QUOTE_DETAILS_NAME for table in spec.data_schema.tables):
        return spec
    tables = [quote_details_table(), *spec.data_schema.tables]
    return spec.model_copy(
        update={"data_schema": spec.data_schema.model_copy(update={"tables": tables})}
    )


@dataclass(frozen=True, slots=True)
class FormDocument:
    """The form as its folder holds it now, with the folder's revision."""

    spec: FormSpec
    #: The folder's revision: the hash of every file's path and bytes as the read took them.
    revision: str
    #: What reading the folder grew to open it (`_grown_to_hold`), each said; the files are
    #: as they were until the form is saved.
    repairs: tuple[str, ...] = ()


def blank_form(name: str) -> FormSpec:
    """The form a project starts with: one blank sheet holding one section, Haute's
    ``quote_details`` table alone, no sample."""
    section = Section(id="section_0001", title="Section 1", height=BLANK_SECTION_HEIGHT)
    page = Page(id="sheet_0001", title="Sheet 1", width=BLANK_SHEET_WIDTH, sections=[section])
    return with_quote_details(FormSpec(name=name, pages=[page]))


def render_form(spec: FormSpec) -> str:
    """The form as one document's text, as a release ships it and the quote store keeps
    it: every field, defaults included, indented, ending in a newline."""
    # Non-ASCII text as typed, a label or an allowed value, as the node configs keep it. A
    # sheet's, a section's and a component's ids are the editor's, never the release's
    # (READ-24): a release gives them from the sheet's name, the title and the name.
    no_ids = {"__all__": {"id": True}}
    data = spec.model_dump(
        mode="json",
        exclude={
            "pages": {
                "__all__": {
                    "id": True,
                    "sections": {"__all__": {"id": True, "widgets": no_ids}},
                }
            }
        },
    )
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def form_revision(data: bytes) -> str:
    """The revision of a form document holding *data*: its content hash."""
    return content_hash_bytes(data)


#: Each kind of component as the editor names it.
_KINDS = {
    "tableInput": "Table",
    "collection": "Collection",
    "chart": "Chart",
    "heading": "Heading",
    "import": "Import",
    "export": "Export",
}

#: What names a component of each kind other than by its title.
_NAMED_BY = {"heading": "text", "import": "label", "export": "label"}


def _component_named(widget: dict[str, Any]) -> str:
    """A component as a repair names it: its kind and its title, a Heading's text or a
    button's label."""
    kind = _KINDS.get(str(widget.get("type")), "component")
    said = widget.get(_NAMED_BY.get(str(widget.get("type")), "title"))
    if isinstance(said, str) and said.strip():
        return f'the {kind} "{said.strip()}"'
    return f"an {kind}" if kind[0] in "AEIOU" else f"a {kind}"


def _whole(value: object) -> int | None:
    return value if type(value) is int else None


def _grown_to_hold(parsed: object) -> list[str]:
    """Grow, in *parsed*, each section too short and each sheet too narrow for a component
    in it, as a resize past an edge grows them in the editor, and say what grew: a merge or
    a hand edit can leave a component below its section or past its sheet's width, which
    the form's checks refuse, so the editor that would repair it could not open it. Nothing
    moves. A shape this cannot read, or a size that is no size (0 or less), is left for the
    checks to name."""
    repairs: list[str] = []
    pages = parsed.get("pages") if isinstance(parsed, dict) else None
    for page in pages if isinstance(pages, list) else []:
        if not isinstance(page, dict) or not isinstance(page.get("sections"), list):
            continue
        widest: tuple[int, dict[str, Any]] | None = None
        for section in page["sections"]:
            if not isinstance(section, dict) or not isinstance(section.get("widgets"), list):
                continue
            deepest: tuple[int, dict[str, Any]] | None = None
            for widget in section["widgets"]:
                if not isinstance(widget, dict):
                    continue
                x, y, w, h = (_whole(widget.get(key)) for key in ("x", "y", "w", "h"))
                if y is not None and h is not None and (deepest is None or y + h > deepest[0]):
                    deepest = (y + h, widget)
                if x is not None and w is not None and (widest is None or x + w > widest[0]):
                    widest = (x + w, widget)
            height = _whole(section.get("height"))
            if deepest is not None and height is not None and 0 < height < deepest[0]:
                section["height"] = deepest[0]
                repairs.append(
                    f'Section "{section.get("title")}" grew from {height}px to {deepest[0]}px '
                    f"tall to hold {_component_named(deepest[1])}."
                )
        width = _whole(page.get("width"))
        if widest is not None and width is not None and 0 < width < widest[0]:
            page["width"] = widest[0]
            repairs.append(
                f'Sheet "{page.get("title")}" grew from {width}px to {widest[0]}px wide to '
                f"hold {_component_named(widest[1])}."
            )
    return repairs
