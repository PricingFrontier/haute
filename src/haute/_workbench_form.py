"""The workbench's form file: its canonical shape, read whole and written atomically
(specs/workbench).

``forms/form.json`` is the file the workbench edits: the schema of typed tables the quote is
keyed into, the sheets, each a stack of sections holding Tables and Collections that show
those tables' columns, Charts that plot them and Headings that name them, and the sample
quote typed into them while building. It is one JSON
document with one spelling: every field is written, defaults included, and an unknown
field is refused by name. Nothing migrates an older spelling: a sheet holding its
components itself, as sheets did before sections, is refused with what replaced it.

A file that does not exist is the blank form that has never been saved. A file's revision
is its content hash, which a save quotes so a form that changed on disk since it was read
is never overwritten.

Every form holds Haute's own input table, ``quote_details``, first: the details of a quote
(its reference, name, insured, broker, dates and currency, its stage, who it is with and
whether it is archived, which the underwriter app sets itself, and whether it is a renewal).
Its structure is Haute's and a form that changes it is refused; a form read or saved without
it has it added, and one holding its columns as an earlier Haute wrote them has the newer
ones added after them, the one exception to nothing migrating, so a project written before
either takes them on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, ValidationError, model_validator

from haute._explore_pivots import PIVOT_DECIMAL_PLACES_MAX
from haute._file_ops import atomic_write_bytes
from haute._hashing import content_hash_bytes
from haute._workbench_config import WorkbenchConfig, WorkbenchError

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

#: How a Table's header shows its labels: as written, or in capitals.
TableHeader = Literal["caps", "plain"]

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

#: What a condition tests its column's value for: a tick box ticked or not; any other
#: column filled or empty, or equal to a value or not; a Text or number column one of some
#: values or none of them; a number or a Date above, at least, below or at most a value.
ConditionTest = Literal[
    "ticked",
    "not_ticked",
    "filled",
    "empty",
    "equals",
    "not_equals",
    "one_of",
    "not_one_of",
    "above",
    "at_least",
    "below",
    "at_most",
]


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
    """The form file cannot be read as a form: unreadable, not JSON or misshapen."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Placed(_Strict):
    """A widget's place in its section, in pixels from the section's top-left corner."""

    id: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class FieldRef(_Strict):
    """A schema column a widget shows, by table and column id, so a rename keeps it."""

    table: str
    column: str


class Condition(_Strict):
    """A test of a schema column's value, the column by table and column id.

    The shape is all the file holds a condition to. Whether its column, test and values
    fit one another is ``haute._workbench_visibility.condition_fits``'s question, asked
    where conditions are tested: retyping a column can unfit a condition the form holds.
    """

    table: str
    column: str
    op: ConditionTest
    #: As typed: none for the ticks, ``filled`` and ``empty``, one or more for ``one_of``
    #: and ``not_one_of``, else one.
    values: list[Annotated[str, Strict()]] = Field(default_factory=list)


class ShowWhen(_Strict):
    """When a sheet, a section or a widget shows: always, without conditions; else while all
    of them hold, or any, as ``match`` says."""

    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(default_factory=list)


class TableInputWidget(_Placed):
    """A Table: a grid showing columns of many-row schema tables."""

    type: Literal["tableInput"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    rows: int = Field(default=3, ge=1, le=TABLE_ROWS_MAX)
    # How the grid looks, whether its rows are a set structure with no Add row and no
    # delete, whether it is transposed, its fields down its side and its rows across, and
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


class CollectionWidget(_Placed):
    """A Collection: boxes showing columns of one-row schema tables, ``columns`` across, with
    a gap across it wherever one of its ``spacers`` sits among them."""

    type: Literal["collection"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    columns: int = Field(default=3, ge=1, le=COLLECTION_COLUMNS_MAX)
    # How its boxes look: by default labelled above, comfortable and framed.
    labels: CollectionLabels = "above"
    density: Density = "comfortable"
    frame: bool = True
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


Widget = Annotated[
    TableInputWidget | CollectionWidget | ChartWidget | HeadingWidget, Field(discriminator="type")
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
    id: str
    name: str
    type: ColumnType = "str"
    #: What widgets call it; empty means the name, made readable.
    label: str = ""
    #: The group it is in, a name it shares with the columns grouped with it; empty for none.
    group: str = ""
    #: In a many-row table, part of what says which row is which.
    key: bool = False
    # The rules an input column's value must meet. Output tables ignore them.
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
    id: str
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
    """The form: what the workbench edits and ``forms/form.json`` stores."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    version: Literal[1] = 1
    name: str
    theme: Theme = Field(default_factory=Theme)
    #: ``schema`` in the file; ``BaseModel`` already has a ``schema`` attribute.
    data_schema: FormSchema = Field(default_factory=FormSchema, alias="schema")
    pages: list[Page] = Field(min_length=1)
    #: The sample quote typed while building: rows by schema table id, each keyed by column
    #: id, each value as typed (text, or a tick). The Workbench Input's previews run on it.
    #: Held as typed, strictly: a number or a null for a cell is not a form the view writes.
    sample: dict[str, list[dict[str, Cell]]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _brings_quote_details_up_to_date(cls, data: Any) -> Any:
        """A form saved by an earlier Haute holds ``quote_details`` with the columns it had
        then: the first ones (``QUOTE_DETAILS_FIRST``), or more of them in order. The newer
        ones are added after them as Haute makes them, the caller's data left as it was."""
        schema = data.get("schema") if isinstance(data, dict) else None
        if not isinstance(schema, dict) or not isinstance(schema.get("tables"), list):
            return data
        tables = schema["tables"]
        for index, table in enumerate(tables):
            if not isinstance(table, dict) or table.get("id") != QUOTE_DETAILS_ID:
                continue
            columns = table.get("columns")
            if not isinstance(columns, list):
                return data
            count = len(columns)
            if not len(QUOTE_DETAILS_FIRST) <= count < len(QUOTE_DETAILS_COLUMNS):
                return data
            if _column_shapes(columns) != list(QUOTE_DETAILS_COLUMNS[:count]):
                return data
            newer = [
                {"id": column_id, "name": name, "type": column_type}
                for column_id, name, column_type in QUOTE_DETAILS_COLUMNS[count:]
            ]
            held = [*tables[:index], {**table, "columns": [*columns, *newer]}, *tables[index + 1 :]]
            return {**data, "schema": {**schema, "tables": held}}
        return data

    @model_validator(mode="after")
    def _keeps_quote_details_as_hautes(self) -> FormSpec:
        for table in self.data_schema.tables:
            if table.id == QUOTE_DETAILS_ID or table.name == QUOTE_DETAILS_ID:
                _check_quote_details(table)
        return self

    @model_validator(mode="after")
    def _gives_each_its_own_id(self) -> FormSpec:
        """Refuse an id given twice: the editor finds, moves and removes a sheet, a section,
        a component, a table or a column by its id, so a block copied by hand or by a merge
        would be two things it takes for one."""
        sections = [section for page in self.pages for section in page.sections]
        _check_ids("sheets", [page.id for page in self.pages])
        _check_ids("sections", [section.id for section in sections])
        _check_ids("components", [w.id for section in sections for w in section.widgets])
        _check_ids("tables", [table.id for table in self.data_schema.tables])
        for table in self.data_schema.tables:
            _check_ids(f"columns of {table.name}", [column.id for column in table.columns])
        return self


def _check_ids(things: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for given in ids:
        if given in seen:
            raise ValueError(f"Two {things} have the id {given!r}: each needs one of its own")
        seen.add(given)


#: Haute's table of a quote's details: its id, also its name.
QUOTE_DETAILS_ID = "quote_details"
#: Why another table under its name is refused, in the words the view's schema editor uses
#: too (``frontend/src/utils/workbenchForm.ts``).
QUOTE_DETAILS_REFUSAL = (
    f"{QUOTE_DETAILS_ID} is Haute's own table of a quote's details: name yours otherwise"
)
#: Its columns, in order: each one's id, name and type.
QUOTE_DETAILS_COLUMNS: tuple[tuple[str, str, ColumnType], ...] = (
    ("qd_reference", "reference", "str"),
    ("qd_quote_name", "quote_name", "str"),
    ("qd_insured", "insured", "str"),
    ("qd_broker", "broker", "str"),
    ("qd_inception_date", "inception_date", "date"),
    ("qd_expiry_date", "expiry_date", "date"),
    ("qd_valid_until", "valid_until", "date"),
    ("qd_currency", "currency", "str"),
    ("qd_stage", "stage", "str"),
    ("qd_assignee", "assignee", "str"),
    ("qd_archived", "archived", "bool"),
    ("qd_is_renewal", "is_renewal", "bool"),
)
#: The columns it had first, before ``is_renewal``: a form holding these, or more of its
#: columns after them in order, takes on the rest.
QUOTE_DETAILS_FIRST = QUOTE_DETAILS_COLUMNS[:11]
#: The columns the underwriter app sets itself, whatever a page sends: read only on its
#: sheets, and taking no rules.
QUOTE_DETAILS_MANAGED: frozenset[str] = frozenset(
    {"qd_reference", "qd_stage", "qd_assignee", "qd_archived"}
)


def _column_shapes(columns: list[Any]) -> list[tuple[Any, Any, Any]]:
    """Each column's id, name and type as a form's raw data holds them, its type ``str`` when
    left out, as a column's is."""
    return [
        (column.get("id"), column.get("name"), column.get("type", "str"))
        if isinstance(column, dict)
        else (None, None, None)
        for column in columns
    ]


def quote_details_table() -> SchemaTable:
    """Haute's table of a quote's details, as a form holds it when nothing of it is set."""
    return SchemaTable(
        id=QUOTE_DETAILS_ID,
        name=QUOTE_DETAILS_ID,
        role="input",
        rows="one",
        columns=[
            SchemaColumn(id=column_id, name=name, type=column_type)
            for column_id, name, column_type in QUOTE_DETAILS_COLUMNS
        ],
    )


def _check_quote_details(table: SchemaTable) -> None:
    """Refuse a ``quote_details`` table that is not Haute's: another id under its name, or its
    structure changed. Its columns' labels, groups, help and placeholders are the project's,
    as are the rules and defaults of the columns the app does not set."""
    if table.id != QUOTE_DETAILS_ID or table.name != QUOTE_DETAILS_ID:
        raise ValueError(QUOTE_DETAILS_REFUSAL)
    shape = [(column.id, column.name, column.type) for column in table.columns]
    if (
        table.role != "input"
        or table.rows != "one"
        or shape != list(QUOTE_DETAILS_COLUMNS)
        or any(column.key or column.index or column.row_per_value for column in table.columns)
    ):
        raise ValueError(
            f"{QUOTE_DETAILS_ID} is Haute's: a one-row input table of the columns "
            f"{', '.join(name for _, name, _ in QUOTE_DETAILS_COLUMNS)}, in that order, whose "
            "structure is not changed"
        )
    for column in table.columns:
        if column.id in QUOTE_DETAILS_MANAGED and (
            column.required
            or column.options
            or column.options_source is not None
            or column.min is not None
            or column.max is not None
            or column.default_value not in ("", False)
        ):
            raise ValueError(
                f"{QUOTE_DETAILS_ID}.{column.name} is set by the underwriter app and takes no "
                "rules or default"
            )


def with_quote_details(spec: FormSpec) -> FormSpec:
    """*spec* holding Haute's ``quote_details`` table, first, added when it has none."""
    if any(table.id == QUOTE_DETAILS_ID for table in spec.data_schema.tables):
        return spec
    tables = [quote_details_table(), *spec.data_schema.tables]
    return spec.model_copy(
        update={"data_schema": spec.data_schema.model_copy(update={"tables": tables})}
    )


@dataclass(frozen=True, slots=True)
class FormDocument:
    """The form as its file holds it now, with the file's revision."""

    spec: FormSpec
    #: The file's content hash, or None while the form has never been saved: the file
    #: does not exist and ``spec`` is the blank form.
    revision: str | None
    #: What reading the file grew to open it (`_grown_to_hold`), each said; the file is as
    #: it was until the form is saved.
    repairs: tuple[str, ...] = ()


def blank_form(name: str) -> FormSpec:
    """The form a project starts with: one blank sheet holding one section, Haute's
    ``quote_details`` table alone, no sample."""
    section = Section(id="section_1", title="Section 1", height=BLANK_SECTION_HEIGHT)
    page = Page(id="page_1", title="Sheet 1", width=BLANK_SHEET_WIDTH, sections=[section])
    return with_quote_details(FormSpec(name=name, pages=[page]))


def render_form(spec: FormSpec) -> str:
    """The file's text: every field, defaults included, indented, ending in a newline."""
    # Non-ASCII text as typed, a label or an allowed value, as the node configs keep it.
    return json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def form_revision(data: bytes) -> str:
    """The revision of a form file holding *data*: its content hash."""
    return content_hash_bytes(data)


def write_form(path: Path, spec: FormSpec) -> str:
    """Write *spec* to *path* atomically, making its folder, and return the file's revision."""
    data = render_form(spec).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, data)
    return form_revision(data)


def _read_form_bytes(config: WorkbenchConfig) -> bytes | None:
    """The form file's bytes, or None when it does not exist."""
    try:
        return config.form_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkbenchFormError(
            f"{config.form} could not be read: {exc}", path=str(config.form_path)
        ) from exc


def form_file_revision(config: WorkbenchConfig) -> str | None:
    """The form file's revision as it is now, or None when the file does not exist.

    Raises :class:`WorkbenchFormError` when the file cannot be read.
    """
    data = _read_form_bytes(config)
    return None if data is None else form_revision(data)


def _refuse_page_widgets(config: WorkbenchConfig, parsed: object) -> None:
    """Refuse a sheet holding widgets of its own, as sheets did before sections, naming
    what replaced them rather than reporting an unknown field."""
    pages = parsed.get("pages") if isinstance(parsed, dict) else None
    for index, page in enumerate(pages if isinstance(pages, list) else []):
        if isinstance(page, dict) and "widgets" in page:
            raise WorkbenchFormError(
                f"{config.form} is not a workbench form: pages.{index}.widgets was removed; "
                f"a sheet's components now sit in its sections (pages.{index}.sections[].widgets)",
                path=str(config.form_path),
            )


#: Each kind of component as the editor names it.
_KINDS = {"tableInput": "Table", "collection": "Collection", "chart": "Chart", "heading": "Heading"}


def _component_named(widget: dict[str, Any]) -> str:
    """A component as a repair names it: its kind and its title, a Heading's text."""
    kind = _KINDS.get(str(widget.get("type")), "component")
    said = widget.get("text") if widget.get("type") == "heading" else widget.get("title")
    return f'the {kind} "{said.strip()}"' if isinstance(said, str) and said.strip() else f"a {kind}"


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


def read_form_document(config: WorkbenchConfig) -> FormDocument:
    """The form at ``config.form_path``, read whole and checked, with the file's revision.

    A file that does not exist is the blank form, named after the project, with no
    revision. A section or sheet too small for a component in it is grown to hold it, and
    said in ``repairs`` (`_grown_to_hold`). Raises :class:`WorkbenchFormError`, naming the
    file as ``haute.toml`` names it and what is wrong, when the file cannot be read, is not
    UTF-8 text, is not JSON or does not fit the shape, a sheet holding widgets of its own
    named with what replaced them.
    """
    path = config.form_path
    data = _read_form_bytes(config)
    if data is None:
        return FormDocument(blank_form(config.project_root.name), None)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkbenchFormError(f"{config.form} is not UTF-8 text: {exc}", path=str(path)) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkbenchFormError(f"{config.form} is not JSON: {exc}", path=str(path)) from exc
    _refuse_page_widgets(config, parsed)
    repairs = _grown_to_hold(parsed)
    try:
        spec = FormSpec.model_validate(parsed)
    except ValidationError as exc:
        # The first error is enough to find the file's problem; the rest usually follow it.
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the document"
        raise WorkbenchFormError(
            f"{config.form} is not a workbench form: {where}: {first['msg']}", path=str(path)
        ) from exc
    return FormDocument(with_quote_details(spec), form_revision(data), tuple(repairs))
