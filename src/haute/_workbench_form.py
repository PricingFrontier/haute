"""The workbench's form file: its canonical shape, read whole and written atomically
(specs/workbench).

``forms/form.json`` is the file the workbench edits: the schema of typed tables the quote is
keyed into, the sheets of Tables and Collections that show those tables' columns, and the
sample quote typed into them while building. It is one JSON document with one spelling:
every field is written, defaults included, and an unknown field is refused by name. Nothing
migrates an older spelling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from haute._file_ops import atomic_write_text
from haute._workbench_config import WorkbenchConfig, WorkbenchError

#: The column types of the Quote Input's tables (``haute._api_input_schema.ColumnType``).
ColumnType = Literal["int", "float", "str", "bool", "date"]


class WorkbenchFormError(WorkbenchError):
    """The form file cannot be read as a form: missing, unreadable, not JSON or misshapen."""


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


class TableInputWidget(_Placed):
    """A Table: a grid of at least ``rows`` rows showing columns of many-row schema tables."""

    type: Literal["tableInput"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    rows: int = Field(default=3, ge=1, le=50)


class CollectionWidget(_Placed):
    """A Collection: boxes showing columns of one-row schema tables, ``columns`` across."""

    type: Literal["collection"]
    title: str = ""
    fields: list[FieldRef] = Field(default_factory=list)
    columns: int = Field(default=3, ge=1, le=12)


Widget = Annotated[TableInputWidget | CollectionWidget, Field(discriminator="type")]


class Page(_Strict):
    """A sheet: its widgets, each placed freely on the canvas."""

    id: str
    title: str
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


class SchemaTable(_Strict):
    id: str
    name: str
    #: Sent to the pricing engine, or returned by it.
    role: Literal["input", "output"] = "input"
    #: One row per quote, such as policy details, or many, such as an equipment schedule.
    rows: Literal["one", "many"] = "one"
    columns: list[SchemaColumn] = Field(default_factory=list)


class Schema(_Strict):
    tables: list[SchemaTable] = Field(default_factory=list)


class FormSpec(_Strict):
    """The form: what the workbench edits and ``forms/form.json`` stores."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    version: Literal[1] = 1
    name: str
    #: ``schema`` in the file; ``BaseModel`` already has a ``schema`` attribute.
    data_schema: Schema = Field(default_factory=Schema, alias="schema")
    pages: list[Page] = Field(min_length=1)
    #: The sample quote typed while building: rows by schema table id, each keyed by column
    #: id, each value as typed (text, or a tick). The Workbench Input's previews run on it.
    sample: dict[str, list[dict[str, str | bool]]] = Field(default_factory=dict)


def blank_form(name: str) -> FormSpec:
    """The form a project starts with: one blank sheet, no tables, no sample."""
    return FormSpec(name=name, pages=[Page(id="page_1", title="Sheet 1")])


def render_form(spec: FormSpec) -> str:
    """The file's text: every field, defaults included, indented, ending in a newline."""
    return json.dumps(spec.model_dump(mode="json"), indent=2) + "\n"


def write_form(path: Path, spec: FormSpec) -> None:
    """Write *spec* to *path* atomically, making its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, render_form(spec))


def read_form(config: WorkbenchConfig) -> FormSpec:
    """The form at ``config.form_path``, read whole and checked.

    Raises :class:`WorkbenchFormError`, naming the file as ``haute.toml`` names it and what
    is wrong, when the file is missing, cannot be read, is not JSON or does not fit the shape.
    """
    path = config.form_path
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkbenchFormError(
            f"{config.form} does not exist: create it, point [workbench].form at the form, "
            "or set [workbench] enabled = false in haute.toml.",
            path=str(path),
        ) from exc
    except OSError as exc:
        raise WorkbenchFormError(f"{config.form} could not be read: {exc}", path=str(path)) from exc
    except UnicodeDecodeError as exc:
        raise WorkbenchFormError(f"{config.form} is not UTF-8 text: {exc}", path=str(path)) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkbenchFormError(f"{config.form} is not JSON: {exc}", path=str(path)) from exc
    try:
        return FormSpec.model_validate(data)
    except ValidationError as exc:
        # The first error is enough to find the file's problem; the rest usually follow it.
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the document"
        raise WorkbenchFormError(
            f"{config.form} is not a workbench form: {where}: {first['msg']}", path=str(path)
        ) from exc
