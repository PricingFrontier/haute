"""The checks a quote must pass to be marked final (specs/workbench).

One check for each rule a schema column holds, in the schema's order: Required, on an input
or an output, that the column holds a value; a range, that a value held is within it; allowed
values, that a value held is one of them; and a validation, a True/false output the pipeline
fills, that it holds true. A check passes when a one-row table's one row passes it, or every
row of a many-row table does, a table without rows passing; a failing check of a many-row
table names its rows by their index. An empty value passes a range and allowed values, which
are Required's to ask for. The rules of an input the sheets hide for the quote's values are
not checked: nobody could put it right. An output's are
always checked, as the pipeline fills it whatever the sheets show.

The checks read the quote as the store holds it: each table's rows, its inputs and outputs
together, typed as their columns. The view makes the same checks on what the sheets hold in
``frontend/src/utils/quoteChecks.ts``; both are held to the cases in
``frontend/src/utils/__tests__/fixtures/quoteChecks.json``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from haute._workbench_form import FormSpec, SchemaColumn
from haute._workbench_tables import allowed_values

#: Which of a column's rules a check is of.
CheckKind = Literal["required", "range", "allowed", "validation"]


@dataclass(frozen=True, slots=True)
class Check:
    """One rule of one column, and whether the quote passes it."""

    kind: CheckKind
    table: str
    column: str
    passed: bool
    #: The rows of a many-row table failing it, each by its index's value as text, in the
    #: rows' order; none for a one-row table, whose one row is the check's.
    rows: tuple[str, ...] = ()


def _blank(value: Any) -> bool:
    """Whether *value* is no value: null, or text of spaces alone. Zero and false are values."""
    return value is None or (isinstance(value, str) and not value.strip())


def _kinds(column: SchemaColumn, *, hidden: bool) -> list[CheckKind]:
    """The checks *column* gives, in their order: none of an index, which is always filled,
    and none of the rules of an input the sheets hide."""
    if column.index is not None:
        return []
    kinds: list[CheckKind] = []
    if not hidden:
        if column.required:
            kinds.append("required")
        if column.min is not None or column.max is not None:
            kinds.append("range")
        if column.options:
            kinds.append("allowed")
    if column.validation:
        kinds.append("validation")
    return kinds


def _passes(kind: CheckKind, column: SchemaColumn, value: Any) -> bool:
    if kind == "required":
        return not _blank(value)
    if kind == "validation":
        return value is True
    if _blank(value):
        return True
    if kind == "allowed":
        return value in allowed_values(column)
    # A range is a number column's, and the store holds nothing but numbers in one.
    return (column.min is None or value >= column.min) and (
        column.max is None or value <= column.max
    )


def quote_checks(
    form: FormSpec,
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    hidden: Collection[tuple[str, str]] = frozenset(),
) -> list[Check]:
    """Every check of *form* on a quote's *rows* as the store holds them, by table name,
    passed or not; *hidden* are the input columns the sheets hide for the quote's values
    (``haute._workbench_visibility.visibility``), each a table's name and a column's."""
    checks: list[Check] = []
    for table in form.data_schema.tables:
        held = list(rows.get(table.name, []))
        # A one-row table is one row whether or not the store holds it yet.
        tested = held if table.rows == "many" else [held[0] if held else {}]
        # A many-row table's rows are named by its index; a one-row table has none.
        index = table.index
        for column in table.columns:
            is_input = not table.is_output(column.name)
            off = is_input and (table.name, column.name) in hidden
            for kind in _kinds(column, hidden=off):
                failing = [row for row in tested if not _passes(kind, column, row.get(column.name))]
                checks.append(
                    Check(
                        kind=kind,
                        table=table.name,
                        column=column.name,
                        passed=not failing,
                        rows=()
                        if index is None
                        else tuple(str(row.get(index.name)) for row in failing),
                    )
                )
    return checks


def _number(value: float) -> str:
    """A range's bound as a message says it: a whole number without a point."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def check_text(form: FormSpec, check: Check) -> str:
    """What a failing *check* of *form* asks for, said by the table's name and the column's
    label: ``policy.Age needs a value``, ``drivers row 2.Age must be from 17 to 99``; a
    validation its message when it has one."""
    table = next(table for table in form.data_schema.tables if table.name == check.table)
    column = next(column for column in table.columns if column.name == check.column)
    where = table.name
    if check.rows:
        where += " row " if len(check.rows) == 1 else " rows "
        where += ", ".join(check.rows)
    named = f"{where}.{column.label or column.name}"
    if check.kind == "required":
        return f"{named} needs a value"
    if check.kind == "allowed":
        return f"{named} must be one of its allowed values"
    if check.kind == "validation":
        return (
            f"{named}: {column.validation_message}"
            if column.validation_message
            else (f"{named} does not pass")
        )
    low = "" if column.min is None else _number(column.min)
    high = "" if column.max is None else _number(column.max)
    if low and high:
        return f"{named} must be from {low} to {high}"
    return f"{named} must be at least {low}" if low else f"{named} must be at most {high}"
