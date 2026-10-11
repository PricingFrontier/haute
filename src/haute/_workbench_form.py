"""The workbench's form file: its canonical shape, read whole and written atomically
(specs/workbench).

``forms/form.json`` is the file the workbench edits: the schema of typed tables the quote is
keyed into, the sheets of Tables and Collections that show those tables' columns, of Charts
that plot them and of Headings that name their sections, and the sample quote typed into
them while building. It is one JSON
document with one spelling: every field is written, defaults included, and an unknown
field is refused by name. Nothing migrates an older spelling.

A file that does not exist is the blank form that has never been saved. A file's revision
is its content hash, which a save quotes so a form that changed on disk since it was read
is never overwritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, ValidationError

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

#: How tall a Table's rows are: Haute's compact rows, or roomier ones.
TableDensity = Literal["compact", "comfortable"]

#: The lines a Table draws: between its rows and before each output column, between every
#: column too, or none.
TableGridlines = Literal["rows", "all", "none"]

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


class WorkbenchFormError(WorkbenchError):
    """The form file cannot be read as a form: unreadable, not JSON or misshapen."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Placed(_Strict):
    """A widget's place on its sheet, in pixels."""

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
    """When a sheet or a widget shows: always, without conditions; else while all of them
    hold, or any, as ``match`` says."""

    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(default_factory=list)


class TableInputWidget(_Placed):
    """A Table: a grid of at least ``rows`` rows showing columns of many-row schema tables."""

    type: Literal["tableInput"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    rows: int = Field(default=3, ge=1, le=50)
    # How the grid looks, and whether its rows are a set structure with no Add row and no
    # delete: by default numbered, compact, lined between rows, striped and not fixed.
    row_numbers: bool = True
    density: TableDensity = "compact"
    gridlines: TableGridlines = "rows"
    striped: bool = True
    fixed_rows: bool = False


class CollectionWidget(_Placed):
    """A Collection: boxes showing columns of one-row schema tables, ``columns`` across."""

    type: Literal["collection"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    columns: int = Field(default=3, ge=1, le=12)


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


class Page(_Strict):
    """A sheet: when it shows, and its widgets, each placed freely on the canvas."""

    id: str
    title: str
    show_when: ShowWhen = Field(default_factory=ShowWhen)
    widgets: list[Widget] = Field(default_factory=list)


class SchemaColumn(_Strict):
    id: str
    name: str
    type: ColumnType = "str"
    #: What widgets call it; empty means the name, made readable.
    label: str = ""
    #: In a many-row table, part of what says which row is which.
    key: bool = False
    # The rules an input column's value must meet. Output tables ignore them.
    required: bool = False
    min: float | None = None
    max: float | None = None
    options: list[str] = Field(default_factory=list)
    #: The table's index: each row's number, from 1, as the rows stand. Nothing is typed
    #: into it, and it is an Integer.
    index: bool = False
    #: Shown beside its label when the pointer rests on it; empty shows nothing.
    help: str = ""
    #: What an empty input cell shows, in place of the blank.
    placeholder: str = ""
    # How a number shows wherever the sheets show it, as an Explore pivot's values show:
    # a percent is the value times 100, a currency's sign before it. ``general`` with no
    # decimal places leaves an input as typed and an output as the data preview shows a
    # value. Columns of other types ignore them.
    number_format: ColumnFormat = "general"
    decimal_places: int | None = Field(default=None, ge=0, le=10)
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


class FormSpec(_Strict):
    """The form: what the workbench edits and ``forms/form.json`` stores."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    version: Literal[1] = 1
    name: str
    #: ``schema`` in the file; ``BaseModel`` already has a ``schema`` attribute.
    data_schema: FormSchema = Field(default_factory=FormSchema, alias="schema")
    pages: list[Page] = Field(min_length=1)
    #: The sample quote typed while building: rows by schema table id, each keyed by column
    #: id, each value as typed (text, or a tick). The Workbench Input's previews run on it.
    #: Held as typed, strictly: a number or a null for a cell is not a form the view writes.
    sample: dict[str, list[dict[str, Cell]]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FormDocument:
    """The form as its file holds it now, with the file's revision."""

    spec: FormSpec
    #: The file's content hash, or None while the form has never been saved: the file
    #: does not exist and ``spec`` is the blank form.
    revision: str | None


def blank_form(name: str) -> FormSpec:
    """The form a project starts with: one blank sheet, no tables, no sample."""
    return FormSpec(name=name, pages=[Page(id="page_1", title="Sheet 1")])


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


def read_form_document(config: WorkbenchConfig) -> FormDocument:
    """The form at ``config.form_path``, read whole and checked, with the file's revision.

    A file that does not exist is the blank form, named after the project, with no
    revision. Raises :class:`WorkbenchFormError`, naming the file as ``haute.toml`` names it
    and what is wrong, when the file cannot be read, is not UTF-8 text, is not JSON or does
    not fit the shape.
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
    try:
        spec = FormSpec.model_validate(parsed)
    except ValidationError as exc:
        # The first error is enough to find the file's problem; the rest usually follow it.
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the document"
        raise WorkbenchFormError(
            f"{config.form} is not a workbench form: {where}: {first['msg']}", path=str(path)
        ) from exc
    return FormDocument(spec, form_revision(data))
