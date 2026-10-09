"""The form's tables as Haute takes them: the Quote Input's v2 ``tables`` (specs/workbench).

The schema's input tables are the Workbench Input's and its output tables the Workbench
Output's. In each quote, and in each priced quote, a one-row table is an object under its own
name and a many-row table an array of objects under its name, ``{"policy_details": {...},
"equipment": [{...}, ...]}``, and a request is one quote or a list of them. The pipeline keeps
a copy of these tables in the workbench nodes' configs, so it runs without the form. Rules
such as required and ranges stay in the form: the tables have no place for them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from haute._workbench_form import FormSpec, SchemaTable


@dataclass(frozen=True, slots=True)
class WorkbenchTables:
    """The three things ``GET /api/workbench/tables`` serves, as the form defines them now."""

    tables: list[dict[str, Any]]
    sample: dict[str, Any]
    response_tables: list[dict[str, Any]]


def workbench_tables(spec: FormSpec) -> WorkbenchTables:
    return WorkbenchTables(input_tables(spec), sample_quote(spec), output_tables(spec))


def input_tables(spec: FormSpec) -> list[dict[str, Any]]:
    """The Workbench Input's tables for the schema's input tables, in schema order."""
    return [_table(table) for table in spec.data_schema.tables if table.role == "input"]


def output_tables(spec: FormSpec) -> list[dict[str, Any]]:
    """The Workbench Output's tables for the schema's output tables, in schema order."""
    return [_table(table) for table in spec.data_schema.tables if table.role == "output"]


def _table(table: SchemaTable) -> dict[str, Any]:
    many = table.rows == "many"
    # A one-row table's columns sit at the quote's own level, under the table's object.
    level = f"$[:].{table.name}[:]" if many else f"$[:].{table.name}"
    keys = [column.name for column in table.columns if column.key]
    columns = [
        # The index is each row's number: an Integer, with no levels.
        _column(level, column.name, "int", None)
        if column.index
        else _column(level, column.name, column.type, list(column.options) or None)
        for column in table.columns
    ]
    return {
        "path": level if many else "$[:]",
        "label": table.name,
        "emit": True,
        "row_id_column": keys[0] if many and len(keys) == 1 else None,
        "columns": columns,
    }


def _column(level: str, name: str, type_: str, levels: list[str] | None) -> dict[str, Any]:
    return {
        "name": name,
        "path": f"{level}.{name}",
        "type": type_,
        "status": "Confirmed",
        "selected": True,
        "levels": levels,
    }


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


def _typed(column_type: str, text: str) -> Any:
    """Typed text as its column's type holds it; text that isn't of that type, as typed."""
    if column_type not in ("int", "float"):
        return text
    try:
        number = float(re.sub(r"[$£€,\s]", "", text))
    except ValueError:
        return text
    if not math.isfinite(number):
        return text
    return int(number) if column_type == "int" and number.is_integer() else number
