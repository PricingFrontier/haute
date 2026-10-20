"""Which sheets, sections and components show for values typed into the sheets, and so
which input columns a quote leaves out (specs/workbench).

A sheet, a section or a component with conditions shows only while they hold on the values
typed into the sheets, a group of them holding as one; a section on a sheet that does not
show does not show either, nor does a component in a section that does not show. A
condition holds while it fits (``condition_fits``), its column is on a Table or a
Collection that shows (a Chart takes nothing typed, so its columns never count, and a
Heading has none), and the value typed into the column passes its test, read as the
sample's values are typed: a one-row table's one value, or a many-row table's in any of
its typed rows or in every one, there being one; a count holds while one of its table's
columns is on a Table that shows and its number of typed rows passes. Everything starts
shown and anything whose conditions fail is hidden, round after round, until a round hides
nothing: hiding never shows anything, so the answer does not depend on the order of the
sheets, sections, components or conditions. A condition that does not fit never holds; it
is a problem the view reports, never an error here.

The view applies the same rules in ``frontend/src/utils/sheetVisibility.ts``; both are
held to the cases in ``frontend/src/utils/__tests__/fixtures/sheetVisibility.json``.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from haute._workbench_form import (
    CollectionWidget,
    ColumnType,
    Condition,
    ConditionGroup,
    ConditionTest,
    CountTest,
    FieldRef,
    FormSpec,
    RowCount,
    RowsCondition,
    SchemaColumn,
    SchemaTable,
    ShowWhen,
    SingleCondition,
    TableInputWidget,
    Widget,
)
from haute._workbench_tables import _filled, _typed, preset_rows

#: The values typed into the sheets: rows by table id, each a cell (text, or a tick) by
#: column id, as the sample holds them.
Rows = Mapping[str, Sequence[Mapping[str, str | bool]]]

#: The tests any column but a True/false one takes, a Text or number column's choices, and
#: a number or a Date column's comparisons.
_ANY: frozenset[ConditionTest] = frozenset({"filled", "empty", "equals", "not_equals"})
_CHOICES: frozenset[ConditionTest] = frozenset({"one_of", "not_one_of"})
_COMPARISONS: dict[ConditionTest, Callable[[Any, Any], bool]] = {
    "above": operator.gt,
    "at_least": operator.ge,
    "below": operator.lt,
    "at_most": operator.le,
}
_ORDERS = frozenset(_COMPARISONS)

#: How a count compares a table's number of rows with its own.
_COUNTS: dict[CountTest, Callable[[int, int], bool]] = {
    "equals": operator.eq,
    "not_equals": operator.ne,
    "above": operator.gt,
    "at_least": operator.ge,
    "below": operator.lt,
    "at_most": operator.le,
}

#: The tests each column type takes.
_TESTS: dict[ColumnType, frozenset[ConditionTest]] = {
    "bool": frozenset({"ticked", "not_ticked"}),
    "str": _ANY | _CHOICES,
    "int": _ANY | _CHOICES | _ORDERS,
    "float": _ANY | _CHOICES | _ORDERS,
    "date": _ANY | _ORDERS,
}

#: The tests that take no value; the choices take one or more, and the rest one.
_NO_VALUES: frozenset[ConditionTest] = frozenset({"ticked", "not_ticked", "filled", "empty"})

# ``[0-9]``, not ``\d``, which matches digits outside ASCII too.
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True, slots=True)
class Visibility:
    """What shows for some values typed into the sheets."""

    #: The ids of the sheets that show.
    pages: frozenset[str]
    #: The ids of the sections that show, each on a sheet that shows.
    sections: frozenset[str]
    #: The ids of the components that show, each in a section that shows.
    widgets: frozenset[str]
    #: The input columns left out of the quote, each a table id and a column id: columns
    #: other than the index on at least one Table or Collection, none of which shows.
    hidden: frozenset[tuple[str, str]]


def visibility(spec: FormSpec, rows: Rows) -> Visibility:
    """Which of *spec*'s sheets, sections and components show for *rows*, and which columns
    hide."""
    placed = [
        (page, section, widget)
        for page in spec.pages
        for section in page.sections
        for widget in section.widgets
    ]
    pages = {page.id for page in spec.pages}
    sections = {section.id for page in spec.pages for section in page.sections}
    widgets = {widget.id for _, _, widget in placed}
    while True:
        # A component still shown is in a section and on a sheet still shown: each round
        # hides a sheet's sections and a section's components with it.
        shown = {
            (field.table, field.column)
            for _, _, widget in placed
            if widget.id in widgets
            for field in _entered(widget)
        }
        hiding_pages = {
            page.id
            for page in spec.pages
            if page.id in pages and not _shows(spec, page.show_when, rows, shown)
        }
        hiding_sections = {
            section.id
            for page in spec.pages
            for section in page.sections
            if section.id in sections
            and (page.id in hiding_pages or not _shows(spec, section.show_when, rows, shown))
        }
        hiding_widgets = {
            widget.id
            for _, section, widget in placed
            if widget.id in widgets
            and (section.id in hiding_sections or not _shows(spec, widget.show_when, rows, shown))
        }
        if not hiding_pages and not hiding_sections and not hiding_widgets:
            break
        pages -= hiding_pages
        sections -= hiding_sections
        widgets -= hiding_widgets
    on_widgets = {
        (field.table, field.column) for _, _, widget in placed for field in _entered(widget)
    }
    inputs = {
        (table.id, column.id)
        for table in spec.data_schema.tables
        if table.role == "input"
        for column in table.columns
        if not column.index
    }
    hidden = (on_widgets - shown) & inputs
    return Visibility(frozenset(pages), frozenset(sections), frozenset(widgets), frozenset(hidden))


def _entered(widget: Widget) -> list[FieldRef]:
    """The columns a component has values typed into: a Table's and a Collection's fields,
    none of a Chart's, which it only plots, and none of a Heading, which has no fields."""
    return widget.fields if isinstance(widget, TableInputWidget | CollectionWidget) else []


def condition_fits(spec: FormSpec, condition: SingleCondition) -> bool:
    """Whether *condition* can hold as written: a count's table a many-row input table; else
    its column in an input table with the rows it tests, one or many, a test the column's
    type takes, as many values as the test takes, and each value one the column could
    hold."""
    if isinstance(condition, RowCount):
        return _counted_table(spec, condition) is not None
    column = _tested_column(spec, condition)
    return column is not None and _fits(column.type, condition)


def _shows(spec: FormSpec, show_when: ShowWhen, rows: Rows, shown: set[tuple[str, str]]) -> bool:
    if not show_when.conditions:
        return True
    holding = (
        _group_holds(spec, entry, rows, shown)
        if isinstance(entry, ConditionGroup)
        else _holds(spec, entry, rows, shown)
        for entry in show_when.conditions
    )
    return all(holding) if show_when.match == "all" else any(holding)


def _group_holds(
    spec: FormSpec, group: ConditionGroup, rows: Rows, shown: set[tuple[str, str]]
) -> bool:
    holding = (_holds(spec, condition, rows, shown) for condition in group.conditions)
    return all(holding) if group.match == "all" else any(holding)


def _holds(
    spec: FormSpec, condition: SingleCondition, rows: Rows, shown: set[tuple[str, str]]
) -> bool:
    if isinstance(condition, RowCount):
        counted = _counted_table(spec, condition)
        if counted is None or not any(
            (counted.id, column.id) in shown for column in counted.columns if not column.index
        ):
            return False
        typed = _typed_rows(counted, rows.get(counted.id, ()))
        return _COUNTS[condition.op](len(typed), condition.count)
    column = _tested_column(spec, condition)
    if column is None or not _fits(column.type, condition):
        return False
    if (condition.table, condition.column) not in shown:
        return False
    if isinstance(condition, Condition):
        first = next(iter(rows.get(condition.table, ())), None)
        cell = None if first is None else first.get(condition.column)
        return _passes(column.type, condition, cell)
    # A column that fits is in a many-row input table.
    table = next(table for table in spec.data_schema.tables if table.id == condition.table)
    cells = [row.get(column.id) for row in _typed_rows(table, rows.get(table.id, ()))]
    if condition.rows == "any":
        return any(_passes(column.type, condition, cell) for cell in cells)
    return bool(cells) and all(_passes(column.type, condition, cell) for cell in cells)


def _typed_rows(
    table: SchemaTable, rows: Sequence[Mapping[str, str | bool]]
) -> list[Mapping[str, str | bool]]:
    """A many-row table's rows as a grid holds them (``preset_rows``), each with anything
    but the index typed in it, whatever the sheets hide: counting only what shows would let
    hiding a column drop a row and show something again (``typedRows`` in the view)."""
    return [
        row
        for row in preset_rows(table, rows)
        if any(_filled(row.get(column.id)) for column in table.columns if not column.index)
    ]


def _tested_column(spec: FormSpec, condition: Condition | RowsCondition) -> SchemaColumn | None:
    """The condition's column, while it is in an input table with the rows it tests: one for
    a ``Condition``, many for a ``RowsCondition``."""
    rows = "one" if isinstance(condition, Condition) else "many"
    return next(
        (
            column
            for table in spec.data_schema.tables
            if table.id == condition.table and table.role == "input" and table.rows == rows
            for column in table.columns
            if column.id == condition.column
        ),
        None,
    )


def _counted_table(spec: FormSpec, count: RowCount) -> SchemaTable | None:
    """The count's table, while it is a many-row input table."""
    return next(
        (
            table
            for table in spec.data_schema.tables
            if table.id == count.table and table.role == "input" and table.rows == "many"
        ),
        None,
    )


def _fits(column_type: ColumnType, condition: Condition | RowsCondition) -> bool:
    if condition.op not in _TESTS[column_type]:
        return False
    count = len(condition.values)
    if condition.op in _NO_VALUES:
        counted = count == 0
    elif condition.op in _CHOICES:
        counted = count >= 1
    else:
        counted = count == 1
    return counted and all(_of_type(column_type, value) for value in condition.values)


def _of_type(column_type: ColumnType, text: str) -> bool:
    """Whether typed *text* is a value a column of the type holds: a real day for a Date, a
    number for a Decimal, a whole one for an Integer, else text other than blank."""
    text = text.strip()
    if column_type == "date":
        return _is_day(text)
    if column_type in ("int", "float"):
        # ``_typed`` gives an int only for a whole number in an Integer column.
        return isinstance(_typed(column_type, text), int if column_type == "int" else float)
    return text != ""


def _is_day(text: str) -> bool:
    if _DAY.fullmatch(text) is None:
        return False
    try:
        date(int(text[:4]), int(text[5:7]), int(text[8:]))
    except ValueError:
        # Not a fallback: ``date`` refusing it is what says 2026-02-30 or the year 0 is no day.
        return False
    return True


def _passes(
    column_type: ColumnType, condition: Condition | RowsCondition, cell: str | bool | None
) -> bool:
    op = condition.op
    if op == "ticked":
        return cell is True
    if op == "not_ticked":
        return cell is not True
    if op == "filled":
        return _filled(cell)
    if op == "empty":
        return not _filled(cell)
    if op in ("equals", "one_of"):
        return _equals_any(column_type, cell, condition.values)
    if op in ("not_equals", "not_one_of"):
        return not _equals_any(column_type, cell, condition.values)
    # A comparison: only a value of the column's type compares. A tick is checked apart,
    # since Python counts True as the number 1.
    if not isinstance(cell, str) or not _of_type(column_type, cell):
        return False
    compare = _COMPARISONS[op]
    return compare(
        _typed(column_type, cell.strip()), _typed(column_type, condition.values[0].strip())
    )


def _equals_any(column_type: ColumnType, cell: str | bool | None, values: Sequence[str]) -> bool:
    """Whether *cell* is filled text equal to any of *values*, both typed by the column's
    type: numbers as numbers, text and days as text. A tick never equals a value."""
    if not isinstance(cell, str) or not _filled(cell):
        return False
    typed = _typed(column_type, cell.strip())
    return any(typed == _typed(column_type, value.strip()) for value in values)
