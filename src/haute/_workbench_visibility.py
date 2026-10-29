"""Which sheets, sections, components and fields show for values typed into the sheets, and
so which input columns a quote leaves out (specs/workbench).

A sheet, a section, a component or a field of a Table or a Collection with conditions shows
only while they hold on the values typed into the sheets, all of them, any or at least a
number, a group of them holding as one; a section on a sheet that does not show does not
show either, nor does a component in a section that does not show, nor a field of a
component that does not show. A
condition holds while it fits (``condition_fits``), its column is a field that shows of a
Table or a Collection (a Chart takes nothing typed, so its columns never count, and a
Heading has none), and the value typed into the column passes its test, read as the
sample's values are typed; a test against another field holds only while that field shows
too, and tests against the value typed into it. A condition on a list's rows holds while
each column it tests is a field that shows of a Table (for a count without tests, one of
its table's columns) and the table's typed rows passing all of its tests in the same row
do: any of them, every
one (there being one), how many, or a total of a number column over them. Everything starts
shown and anything whose conditions fail is hidden, round after round, until a round hides
nothing: hiding never shows anything, so the answer does not depend on the order of the
sheets, sections, components, fields or conditions. A condition that does not fit never
holds; it is a problem the view reports, never an error here.

The view applies the same rules in ``frontend/src/utils/sheetVisibility.ts``; both are
held to the cases in ``frontend/src/utils/__tests__/fixtures/sheetVisibility.json``.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
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
    ListCondition,
    Match,
    RowCount,
    RowsCondition,
    RowTest,
    RowTotal,
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

#: The tests any column but a True/false one takes, a Text column's of text within text, a
#: Text or number column's choices, and a number or a Date column's comparisons.
_ANY: frozenset[ConditionTest] = frozenset({"filled", "empty", "equals", "not_equals"})
_TEXT: frozenset[ConditionTest] = frozenset(
    {"contains", "not_contains", "starts_with", "ends_with"}
)
_CHOICES: frozenset[ConditionTest] = frozenset({"one_of", "not_one_of"})
_COMPARISONS: dict[ConditionTest, Callable[[Any, Any], bool]] = {
    "above": operator.gt,
    "at_least": operator.ge,
    "below": operator.lt,
    "at_most": operator.le,
}
_ORDERS = frozenset(_COMPARISONS)

#: How a count or a total compares its number with the condition's.
_COUNTS: dict[CountTest, Callable[[float, float], bool]] = {
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
    "str": _ANY | _TEXT | _CHOICES,
    "int": _ANY | _CHOICES | _ORDERS,
    "float": _ANY | _CHOICES | _ORDERS,
    "date": _ANY | _ORDERS,
}

#: The tests that take no value; the choices take one or more, and the rest one, which
#: another field may give in place of one typed.
_NO_VALUES: frozenset[ConditionTest] = frozenset({"ticked", "not_ticked", "filled", "empty"})
_ONE_VALUE: frozenset[ConditionTest] = frozenset(_ORDERS | _TEXT | {"equals", "not_equals"})
#: The tests of one value a cell passes when there is nothing to test it against.
_NEGATED: frozenset[ConditionTest] = frozenset({"not_equals", "not_contains"})

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
    #: The fields that show of the Tables and Collections that show, each a component id, a
    #: table id and a column id.
    fields: frozenset[tuple[str, str, str]]
    #: The input columns left out of the quote, each a table id and a column id: columns
    #: other than the index on at least one Table or Collection, none of which shows them
    #: as a field that shows.
    hidden: frozenset[tuple[str, str]]


def visibility(spec: FormSpec, rows: Rows) -> Visibility:
    """Which of *spec*'s sheets, sections, components and fields show for *rows*, and which
    columns hide."""
    placed = [
        (page, section, widget)
        for page in spec.pages
        for section in page.sections
        for widget in section.widgets
    ]
    entered = [
        (widget.id, field, show_when)
        for _, _, widget in placed
        for field, show_when in _entered(widget)
    ]
    pages = {page.id for page in spec.pages}
    sections = {section.id for page in spec.pages for section in page.sections}
    widgets = {widget.id for _, _, widget in placed}
    fields = {(widget_id, field.table, field.column) for widget_id, field, _ in entered}
    while True:
        # A field still shown is of a component, in a section and on a sheet, still shown:
        # each round hides a sheet's sections, a section's components and a component's
        # fields with it.
        shown = {(table, column) for _, table, column in fields}
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
        hiding_fields = {
            key
            for widget_id, field, show_when in entered
            if (key := (widget_id, field.table, field.column)) in fields
            and (widget_id in hiding_widgets or not _shows(spec, show_when, rows, shown))
        }
        if not hiding_pages and not hiding_sections and not hiding_widgets and not hiding_fields:
            break
        pages -= hiding_pages
        sections -= hiding_sections
        widgets -= hiding_widgets
        fields -= hiding_fields
    on_widgets = {
        (field.table, field.column) for _, _, widget in placed for field, _ in _entered(widget)
    }
    inputs = {
        (table.id, column.id)
        for table in spec.data_schema.tables
        if table.role == "input"
        for column in table.columns
        if not column.index
    }
    hidden = (on_widgets - shown) & inputs
    return Visibility(
        frozenset(pages),
        frozenset(sections),
        frozenset(widgets),
        frozenset(fields),
        frozenset(hidden),
    )


_ALWAYS = ShowWhen()


def _entered(widget: Widget) -> list[tuple[FieldRef, ShowWhen]]:
    """The columns a component has values typed into, each with when it shows with its
    component: a Table's and a Collection's fields, as their looks' ``show_when`` says,
    always without a look; none of a Chart's, which it only plots, and none of a Heading,
    which has no fields."""
    if not isinstance(widget, TableInputWidget | CollectionWidget):
        return []
    shows = {(look.table, look.column): look.show_when for look in widget.column_looks}
    return [(field, shows.get((field.table, field.column), _ALWAYS)) for field in widget.fields]


def condition_fits(spec: FormSpec, condition: SingleCondition) -> bool:
    """Whether *condition* can hold as written: a one-row table's condition's column in a
    one-row input table, a test the column's type takes, as many values as the test takes,
    and each value one the column could hold; a condition on a list's rows on a many-row
    input table, each of its tests of a row on a column of the table other than its index
    and fitting so, and a total's column a number column of the table other than its index,
    its value a number."""
    if isinstance(condition, Condition):
        column = _tested_column(spec, condition.table, condition.column)
        return column is not None and _fits(column.type, condition, _other_column(spec, condition))
    table = _list_table(spec, condition)
    return table is not None and _list_fits(table, condition)


def _shows(spec: FormSpec, show_when: ShowWhen, rows: Rows, shown: set[tuple[str, str]]) -> bool:
    if not show_when.conditions:
        return True
    holding = (
        _group_holds(spec, entry, rows, shown)
        if isinstance(entry, ConditionGroup)
        else _holds(spec, entry, rows, shown)
        for entry in show_when.conditions
    )
    return _matches(show_when.match, holding)


def _group_holds(
    spec: FormSpec, group: ConditionGroup, rows: Rows, shown: set[tuple[str, str]]
) -> bool:
    return _matches(
        group.match, (_holds(spec, condition, rows, shown) for condition in group.conditions)
    )


def _matches(match: Match, holding: Iterable[bool]) -> bool:
    """Whether all of *holding* hold, any, or at least *match* of them."""
    if match == "all":
        return all(holding)
    if match == "any":
        return any(holding)
    return sum(holding) >= match


def _holds(
    spec: FormSpec, condition: SingleCondition, rows: Rows, shown: set[tuple[str, str]]
) -> bool:
    if not isinstance(condition, Condition):
        return _list_holds(spec, condition, rows, shown)
    column = _tested_column(spec, condition.table, condition.column)
    if column is None or not _fits(column.type, condition, _other_column(spec, condition)):
        return False
    if (condition.table, condition.column) not in shown:
        return False
    other = None
    if condition.field is not None:
        if (condition.field.table, condition.field.column) not in shown:
            return False
        other = _first_cell(rows, condition.field.table, condition.field.column)
    return _passes(
        column.type, condition, _first_cell(rows, condition.table, condition.column), other
    )


def _first_cell(rows: Rows, table_id: str, column_id: str) -> str | bool | None:
    """The value typed into a one-row table's column: its first row's."""
    first = next(iter(rows.get(table_id, ())), None)
    return None if first is None else first.get(column_id)


def _list_holds(
    spec: FormSpec, condition: ListCondition, rows: Rows, shown: set[tuple[str, str]]
) -> bool:
    table = _list_table(spec, condition)
    if table is None or not _list_fits(table, condition):
        return False
    tested = _list_columns(condition)
    if tested:
        if not all((table.id, column_id) in shown for column_id in tested):
            return False
    elif not any((table.id, column.id) in shown for column in table.columns if not column.index):
        return False
    typed = _typed_rows(table, rows.get(table.id, ()))
    passing = _passing_rows(table, condition.where, typed)
    if isinstance(condition, RowsCondition):
        if condition.rows == "any":
            return bool(passing)
        return bool(typed) and len(passing) == len(typed)
    if isinstance(condition, RowCount):
        return _COUNTS[condition.op](len(passing), condition.count)
    total = _total(table, condition, passing)
    return total is not None and _COUNTS[condition.op](
        total, _typed("float", condition.value.strip())
    )


def _list_columns(condition: ListCondition) -> list[str]:
    """The ids of the columns a condition on a list's rows tests: its tests of a row's, those
    they test against included, and a total's."""
    tested = [
        column
        for test in condition.where
        for column in (test.column, *(() if test.field is None else (test.field,)))
    ]
    return [*tested, condition.column] if isinstance(condition, RowTotal) else tested


def _passing_rows(
    table: SchemaTable, where: Sequence[RowTest], rows: Sequence[Mapping[str, str | bool]]
) -> list[Mapping[str, str | bool]]:
    """The *rows* passing every test of *where* in the same row, whose columns fit."""
    types = {column.id: column.type for column in table.columns}
    return [
        row
        for row in rows
        if all(
            _passes(
                types[test.column],
                test,
                row.get(test.column),
                None if test.field is None else row.get(test.field),
            )
            for test in where
        )
    ]


def _total(
    table: SchemaTable, total: RowTotal, rows: Sequence[Mapping[str, str | bool]]
) -> float | None:
    """The sum, least, greatest or average of the numbers *total*'s column holds in *rows*,
    a cell blank, not a number of the column's type or too large for a float left out, to
    15 significant digits so that 0.1, 0.2 and 0.3 sum to 0.6: the sum of none is 0, and the
    others of none are None, which no test passes."""
    column_type = next(column.type for column in table.columns if column.id == total.column)
    # Each read as a float, as the view reads it, a whole number past 2**53 too: read
    # exactly, its least or greatest would round apart from the view's.
    numbers: list[float] = [
        number
        for row in rows
        if isinstance(cell := row.get(total.column), str)
        and _of_type(column_type, cell)
        and isinstance(number := _typed("float", cell.strip()), float)
    ]
    if not numbers and total.total != "sum":
        return None
    # Added in order, as the view adds them: ``sum`` compensates floats' errors from Python
    # 3.12, which the view's addition does not.
    added = 0.0
    for number in numbers:
        added += number
    if total.total == "sum":
        found = added
    elif total.total == "min":
        found = min(numbers)
    elif total.total == "max":
        found = max(numbers)
    else:
        found = added / len(numbers)
    # Rounded as the view's ``toPrecision`` rounds the exact value, a tie away from zero,
    # where formatting it would take a tie to the even digit.
    with localcontext(prec=15, rounding=ROUND_HALF_UP):
        return float(+Decimal(found))


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


def _tested_column(spec: FormSpec, table_id: str, column_id: str) -> SchemaColumn | None:
    """A one-row table's column, while it is in a one-row input table: a condition's, or the
    field one tests against."""
    return next(
        (
            column
            for table in spec.data_schema.tables
            if table.id == table_id and table.role == "input" and table.rows == "one"
            for column in table.columns
            if column.id == column_id
        ),
        None,
    )


def _other_column(spec: FormSpec, condition: Condition) -> SchemaColumn | None:
    """The field a one-row table's condition tests against, while it names one in a one-row
    input table."""
    field = condition.field
    return None if field is None else _tested_column(spec, field.table, field.column)


def _list_table(spec: FormSpec, condition: ListCondition) -> SchemaTable | None:
    """A condition on a list's rows' table, while it is a many-row input table."""
    return next(
        (
            table
            for table in spec.data_schema.tables
            if table.id == condition.table and table.role == "input" and table.rows == "many"
        ),
        None,
    )


def _list_fits(table: SchemaTable, condition: ListCondition) -> bool:
    """Whether a condition on *table*'s rows can hold as written: each test of a row on a
    column of the table other than its index, fitting as a one-row table's condition fits,
    and a total's column a number column of the table other than its index, its value a
    number."""
    for test in condition.where:
        column = _row_column(table, test.column)
        other = None if test.field is None else _row_column(table, test.field)
        if column is None or not _fits(column.type, test, other):
            return False
    if not isinstance(condition, RowTotal):
        return True
    column = _row_column(table, condition.column)
    return (
        column is not None
        and column.type in ("int", "float")
        and _of_type("float", condition.value)
    )


def _row_column(table: SchemaTable, column_id: str) -> SchemaColumn | None:
    """*table*'s column by id, other than its index, which nothing is typed into."""
    return next(
        (column for column in table.columns if column.id == column_id and not column.index),
        None,
    )


def _fits(
    column_type: ColumnType, condition: Condition | RowTest, other: SchemaColumn | None
) -> bool:
    """Whether a test of a column of *column_type* can hold as written: a test the type
    takes with the values it takes, each one the column could hold; or, against another field,
    *other* (None while it names none, or one that is not there), a test of one value with
    none typed and a field of the column's type, days added only to a Date's."""
    if condition.op not in _TESTS[column_type]:
        return False
    if condition.field is not None:
        return (
            condition.op in _ONE_VALUE
            and not condition.values
            and other is not None
            and other.type == column_type
            and (condition.days == 0 or column_type == "date")
        )
    if condition.days != 0:
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
    column_type: ColumnType,
    condition: Condition | RowTest,
    cell: str | bool | None,
    other: str | bool | None = None,
) -> bool:
    """Whether a fitting test passes *cell*, against the values typed or, for a test against
    another field, *other*, the value typed into that field."""
    op = condition.op
    if op == "ticked":
        return cell is True
    if op == "not_ticked":
        return cell is not True
    if op == "filled":
        return _filled(cell)
    if op == "empty":
        return not _filled(cell)
    if op == "one_of":
        return _equals_any(column_type, cell, condition.values)
    if op == "not_one_of":
        return not _equals_any(column_type, cell, condition.values)
    if condition.field is None:
        value = condition.values[0]
    elif isinstance(other, str) and _of_type(column_type, other):
        value = other
        if column_type == "date":
            return _day_passes(op, cell, value, condition.days)
    else:
        # The other field holds no value of the type: nothing equals, holds or orders
        # against nothing.
        return op in _NEGATED
    if op == "equals":
        return _equals_any(column_type, cell, (value,))
    if op == "not_equals":
        return not _equals_any(column_type, cell, (value,))
    if op in _TEXT:
        return _text_passes(op, cell, value)
    # A comparison: only a value of the column's type compares. A tick is checked apart,
    # since Python counts True as the number 1.
    if not isinstance(cell, str) or not _of_type(column_type, cell):
        return False
    compare = _COMPARISONS[op]
    return compare(_typed(column_type, cell.strip()), _typed(column_type, value.strip()))


def _text_passes(op: ConditionTest, cell: str | bool | None, value: str) -> bool:
    """Whether *cell* holds *value*, at its start or its end, or does not, as *op* asks, both
    without the spaces around them and compared exactly, as ``equals`` compares text: an
    empty cell, or a tick, holds nothing."""
    if not isinstance(cell, str) or not _filled(cell):
        return op == "not_contains"
    text, part = cell.strip(), value.strip()
    if op == "contains":
        return part in text
    if op == "not_contains":
        return part not in text
    if op == "starts_with":
        return text.startswith(part)
    return text.endswith(part)


def _day_passes(op: ConditionTest, cell: str | bool | None, day: str, days: int) -> bool:
    """Whether a Date's *cell* passes *op* against the real *day* with *days* added, each
    compared as its day number, so no day past the calendar's ends is ever made; a cell that
    is not a real day passes ``not_equals`` alone."""
    if not isinstance(cell, str) or not _is_day(cell.strip()):
        return op == "not_equals"
    found, wanted = _day_number(cell.strip()), _day_number(day.strip()) + days
    if op == "equals":
        return found == wanted
    if op == "not_equals":
        return found != wanted
    return _COMPARISONS[op](found, wanted)


def _day_number(day: str) -> int:
    """A real ``YYYY-MM-DD`` day's number, as ``date.toordinal`` counts it (``dayNumber`` in
    the view counts from another day, which no comparison sees)."""
    return date(int(day[:4]), int(day[5:7]), int(day[8:])).toordinal()


def _equals_any(column_type: ColumnType, cell: str | bool | None, values: Sequence[str]) -> bool:
    """Whether *cell* is filled text equal to any of *values*, both typed by the column's
    type: numbers as numbers, text and days as text. A tick never equals a value."""
    if not isinstance(cell, str) or not _filled(cell):
        return False
    typed = _typed(column_type, cell.strip())
    return any(typed == _typed(column_type, value.strip()) for value in values)
