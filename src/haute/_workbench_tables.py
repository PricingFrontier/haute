"""The workbench's tables as the pipeline holds them (specs/workbench).

The schema's input tables are the Workbench Input's and its output tables the Workbench
Output's. Each node's ``tables`` config is a copy, which the editor keeps current, so the
pipeline runs without the form: a table is its name, whether a quote holds one row of it or
many, and its typed columns, ``{"name": "equipment", "rows": "many", "columns": [{"name":
"value", "type": "float"}]}``. The form's rules, labels and keys stay in the form. In a
quote, and in a priced quote, a one-row table is an object under its name and a many-row
table a list of objects, ``{"policy_details": {...}, "equipment": [{...}, ...]}``. A table
with no columns yet is not a port: there is nothing to read into or to fill.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from haute._graph_utils import is_frame_label
from haute._workbench_form import ColumnType
from haute.errors import HauteError

if TYPE_CHECKING:
    from haute._workbench_form import FormSpec, SchemaTable

#: Each column type's dtype in a frame. ``tests/test_workbench_tables.py`` holds it equal to
#: the Quote Input's, so a step reads either input's columns alike.
COLUMN_DTYPES: dict[ColumnType, pl.DataType] = {
    "int": pl.Int64(),
    "float": pl.Float64(),
    "str": pl.String(),
    "bool": pl.Boolean(),
    "date": pl.Date(),
}

_NAME_RULE = "letters, digits and underscores, not starting with a digit and not a Python keyword"


class WorkbenchTablesError(HauteError):
    """A workbench node's tables, as its config holds them, cannot be the pipeline's.

    A public contract error: the message says what to fix, and a route answers it as 422.
    """

    error_code = "workbench_tables_invalid"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkbenchColumn(_Strict):
    """A column of a table: its name in the frame and its type."""

    name: str
    type: ColumnType

    @property
    def dtype(self) -> pl.DataType:
        return COLUMN_DTYPES[self.type]


class WorkbenchTable(_Strict):
    """A table: a port of the node, one row per quote or many, and its columns."""

    name: str
    rows: Literal["one", "many"]
    columns: list[WorkbenchColumn] = Field(default_factory=list)

    @property
    def one_row(self) -> bool:
        return self.rows == "one"

    @property
    def frame_schema(self) -> pl.Schema:
        """The frame's schema: each column with its declared dtype."""
        return pl.Schema({column.name: column.dtype for column in self.columns})

    @property
    def quote_dtype(self) -> pl.DataType:
        """How a quote holds the table: an object of its columns, or a list of them."""
        struct = pl.Struct(dict(self.frame_schema))
        return struct if self.one_row else pl.List(struct)


_TABLES = TypeAdapter(list[WorkbenchTable])


def parse_workbench_tables(value: object, *, owner: str) -> tuple[WorkbenchTable, ...]:
    """*value*, a workbench node's ``tables`` config, as the tables it holds.

    *owner* names the node in a refusal. A shape the workbench does not write is refused
    naming the spot, and the names are checked as :func:`check_tables` checks them.
    """
    try:
        tables = tuple(_TABLES.validate_python(value, strict=True))
    except ValidationError as exc:
        # The first error is enough to find the problem; the rest usually follow it.
        first = exc.errors()[0]
        where = "tables" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in first["loc"]
        )
        raise WorkbenchTablesError(
            f"The {owner}'s tables are not as the workbench writes them ({where}: "
            f"{first['msg']}). Open the pipeline in the editor and save it."
        ) from exc
    check_tables(tables, owner=owner)
    return tables


def check_tables(tables: Sequence[WorkbenchTable], *, owner: str) -> None:
    """Refuse a name that cannot be a port's or a frame column's, or one used twice.

    Two tables whose names differ only in case are refused too: the editor binds a port by
    its name compared without case, as it binds a Quote Input's frames.
    """
    by_folded_name: dict[str, str] = {}
    for table in tables:
        if not is_frame_label(table.name):
            raise WorkbenchTablesError(
                f"The {owner}'s table {table.name!r} cannot be a port: a table's name is "
                f"{_NAME_RULE}."
            )
        earlier = by_folded_name.get(table.name.casefold())
        if earlier == table.name:
            raise WorkbenchTablesError(f"The {owner}'s tables name {table.name!r} twice.")
        if earlier is not None:
            raise WorkbenchTablesError(
                f"The {owner}'s tables {earlier!r} and {table.name!r} differ only in case, so "
                "they cannot both be ports."
            )
        by_folded_name[table.name.casefold()] = table.name
        names: set[str] = set()
        for column in table.columns:
            if not is_frame_label(column.name):
                raise WorkbenchTablesError(
                    f"The {owner}'s table {table.name!r} has a column {column.name!r}, which "
                    f"cannot name a frame's column: a column's name is {_NAME_RULE}."
                )
            if column.name in names:
                raise WorkbenchTablesError(
                    f"The {owner}'s table {table.name!r} names a column {column.name!r} twice."
                )
            names.add(column.name)


def port_tables(tables: Iterable[WorkbenchTable]) -> tuple[WorkbenchTable, ...]:
    """The tables that are ports: those with a column."""
    return tuple(table for table in tables if table.columns)


def quote_schema(tables: Iterable[WorkbenchTable]) -> pl.Schema:
    """The schema of one quote holding *tables*: each under its name, as a quote holds it."""
    return pl.Schema({table.name: table.quote_dtype for table in tables})


@dataclass(frozen=True, slots=True)
class WorkbenchTables:
    """The three things ``GET /api/workbench/tables`` serves, as the form defines them now."""

    tables: tuple[WorkbenchTable, ...]
    sample: dict[str, Any]
    response_tables: tuple[WorkbenchTable, ...]


def workbench_tables(spec: FormSpec) -> WorkbenchTables:
    return WorkbenchTables(input_tables(spec), sample_quote(spec), output_tables(spec))


def input_tables(spec: FormSpec) -> tuple[WorkbenchTable, ...]:
    """The Workbench Input's tables: the schema's input tables, in schema order, checked."""
    return _tables(spec, "input", owner="Workbench Input")


def output_tables(spec: FormSpec) -> tuple[WorkbenchTable, ...]:
    """The Workbench Output's tables: the schema's output tables, in schema order, checked."""
    return _tables(spec, "output", owner="Workbench Output")


def _tables(spec: FormSpec, role: str, *, owner: str) -> tuple[WorkbenchTable, ...]:
    tables = tuple(_table(table) for table in spec.data_schema.tables if table.role == role)
    check_tables(tables, owner=owner)
    return tables


def _table(table: SchemaTable) -> WorkbenchTable:
    return WorkbenchTable(
        name=table.name,
        rows=table.rows,
        # The index is each row's number: an Integer, whatever the form holds for it.
        columns=[
            WorkbenchColumn(name=column.name, type="int" if column.index else column.type)
            for column in table.columns
        ],
    )


def sample_quote(spec: FormSpec) -> dict[str, Any]:
    """The sample quote typed while building, as a request holds it, for the Workbench Input.

    An input table with values is an object under its name when it has one row per quote, and
    a list of objects when it has many; rows with nothing typed are left out, and so is a table
    with none. Values take their column's type: a number typed with a currency sign or
    separators becomes a number, and an unticked box in a filled row is false. A value that
    isn't of its column's type stays as typed, for the Workbench Input to report.
    """
    sample: dict[str, Any] = {}
    for table in spec.data_schema.tables:
        if table.role != "input":
            continue
        rows = spec.sample.get(table.id, [])
        # Numbered as the rows stand, from 1, so deleting a row renumbers those after it.
        records = [
            record
            for number, row in enumerate(rows if table.rows == "many" else rows[:1], 1)
            if (record := _record(table, row, number))
        ]
        if records:
            sample[table.name] = records if table.rows == "many" else records[0]
    return sample


def _record(table: SchemaTable, row: dict[str, str | bool], number: int) -> dict[str, Any]:
    """Row *number*'s values by column name, or nothing when nothing was typed in it."""
    if not any(_filled(row.get(column.id)) for column in table.columns if not column.index):
        return {}
    record: dict[str, Any] = {}
    for column in table.columns:
        raw = row.get(column.id)
        if column.index:
            record[column.name] = number
        elif isinstance(raw, str) and raw.strip():
            # Typed text, even in a box: a column whose type changed keeps what was typed.
            record[column.name] = _typed(column.type, raw.strip())
        elif raw is True:
            # A ticked box, even in a column that is no longer True/false.
            record[column.name] = True
        elif column.type == "bool":
            record[column.name] = False
    return record


def _filled(raw: str | bool | None) -> bool:
    return raw is True or (isinstance(raw, str) and raw.strip() != "")


#: A decimal number, once a currency sign, separators and spaces are gone: the one rule the
#: view's ``parseTypedNumber`` (``frontend/src/utils/sheetValues.ts``) applies too, so a cell
#: the view marks is one the server would keep as typed.
_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _typed(column_type: str, text: str) -> Any:
    """Typed text as its column's type holds it; text that isn't of that type, as typed."""
    if column_type not in ("int", "float"):
        return text
    cleaned = re.sub(r"[$£€,\s]", "", text)
    if _NUMBER.fullmatch(cleaned) is None:
        return text
    number = float(cleaned)
    if not math.isfinite(number):
        return text
    return int(number) if column_type == "int" and number.is_integer() else number
