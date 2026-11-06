"""The comparison of two answers of the Workbench Output, which ``haute app impact`` and the
analyst's test quotes share (specs/workbench).

An answer is each output table's rows by name, as the underwriter app's price route gives
it. Two answers are compared on fields: the output tables compared, each with its columns
and the columns its rows are matched by. ``haute app impact`` compares on the fields two
releases' forms share (:func:`shared_fields`); a test quote on the fields of the form as
saved (:func:`form_fields`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: A number has moved when it differs from the value before by more than this part of it: a
#: millionth of a percent, as the endpoint's impact counts a change.
RELATIVE_CHANGE = 1e-8


@dataclass(frozen=True, slots=True)
class SharedTable:
    """An output table compared: its name, whether it has many rows, the columns its answer
    holds that are compared or listed, and for a many-row table its index, by which its rows
    are matched whatever its name: *index* the second answer's name for it and *was_index*
    the first's. A numbered index of outputs alone (*numbered*) is Haute's numbering of the
    answer's rows. The index is never a move: matched rows hold the same value in it.
    *unmatched* says why the two answers' rows cannot be matched, their indexes getting their
    values in different ways or being of different types; None when they can."""

    name: str
    many: bool
    columns: tuple[str, ...]
    index: str | None = None
    was_index: str | None = None
    numbered: bool = False
    unmatched: str | None = None


@dataclass(frozen=True, slots=True)
class SharedFields:
    """The output fields compared, and those only one side has: a table as its name, a
    column as ``table.column``. A table one side has one row per quote and the other many is
    ``reshaped``, not compared."""

    tables: tuple[SharedTable, ...]
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    reshaped: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Move:
    """A field that moved: its table, its row (its index's value; None in a one-row table),
    its column, the value before and after. A row only one side holds is a move with no
    column, ``before`` and ``after`` saying which side holds it."""

    table: str
    row: str | None
    column: str | None
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class Comparison:
    """Two answers compared: the moves, or why they no longer compare."""

    moves: tuple[Move, ...] = ()
    why: str | None = None


# ---------------------------------------------------------------------------
# The fields compared
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FormTable:
    many: bool
    columns: tuple[str, ...]
    #: Its index's name, how the index gets its values and its type; None for one row.
    index: str | None = None
    kind: str | None = None
    type: str | None = None
    numbered: bool = False


#: An index's way of getting its values and its type, as a reason names them.
_KINDS = {"numbered": "numbered", "typed": "typed", "values": "a row for each value"}
_TYPES = {"int": "Integer", "str": "Text"}


def _output_tables(form: Mapping[str, Any]) -> dict[str, _FormTable]:
    """The output tables of a form document, by name, each with the columns its answer
    holds and its index: read from the document as it stands, so a form another Haute
    version wrote reads alike. An input table's answer holds the quote's identity, its index
    and its outputs; a table of outputs alone's its outputs, its index among them."""
    schema = form.get("schema")
    tables = schema.get("tables") if isinstance(schema, Mapping) else None
    found: dict[str, _FormTable] = {}
    for table in tables if isinstance(tables, list) else []:
        if not isinstance(table, Mapping):
            continue
        inputs = [each for each in table.get("inputs") or [] if isinstance(each, Mapping)]
        outputs = [each for each in table.get("outputs") or [] if isinstance(each, Mapping)]
        own_outputs = [each for each in outputs if each.get("name") not in _IDENTITY]
        if not own_outputs:
            continue
        many = table.get("rows", "one") == "many"
        typed = [
            each for each in inputs if each.get("name") not in _IDENTITY and not each.get("index")
        ]
        names = [str(column.get("name")) for column in outputs]
        index = next((column for column in [*inputs, *outputs] if column.get("index")), None)
        kind = index.get("index") if index is not None else None
        # An index of an earlier form, a flag, numbered its rows.
        kind = kind if isinstance(kind, str) else ("numbered" if index is not None else None)
        index_name = None if index is None or not many else str(index.get("name"))
        if typed:
            held = [name for name in _IDENTITY if any(c.get("name") == name for c in inputs)]
            names = [*held, *([index_name] if index_name else []), *names]
        found[str(table.get("name"))] = _FormTable(
            many=many,
            columns=tuple(names),
            index=index_name,
            kind=kind if index_name else None,
            type=str(index.get("type", "str")) if index is not None and index_name else None,
            numbered=bool(index_name) and kind == "numbered" and not typed,
        )
    return found


#: The quote's identity, which leads a table's first block.
_IDENTITY = ("quote_reference", "quote_option")


def _unmatched(name: str, before: _FormTable, after: _FormTable) -> str | None:
    """Why *name*'s rows in the two answers cannot be matched by their indexes: they get
    their values in different ways, or are of different types; None when they can."""
    if (before.kind, before.type) == (after.kind, after.type):
        return None
    return (
        f"{name}'s index is {_described(before)} in one release and {_described(after)} in "
        "the other, so its rows cannot be matched"
    )


def _described(table: _FormTable) -> str:
    kind = _KINDS.get(str(table.kind), str(table.kind))
    return f"{_TYPES.get(str(table.type), str(table.type))}, {kind}"


def shared_fields(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> SharedFields:
    """The output fields two forms' documents share, *baseline*'s and *candidate*'s, in the
    candidate's order, and those only one has; a many-row table's index paired with the
    other's whatever their names."""
    before = _output_tables(baseline)
    after = _output_tables(candidate)
    tables: list[SharedTable] = []
    added: list[str] = [name for name in after if name not in before]
    removed: list[str] = [name for name in before if name not in after]
    reshaped: list[str] = []
    for name, table in after.items():
        old = before.get(name)
        if old is None:
            continue
        if old.many != table.many:
            reshaped.append(name)
            continue
        old_outputs = set(old.columns) - {old.index}
        new_outputs = set(table.columns) - {table.index}
        columns = tuple(
            column for column in table.columns if column in old_outputs or column == table.index
        )
        added.extend(
            f"{name}.{column}"
            for column in table.columns
            if column != table.index and column not in old_outputs
        )
        removed.extend(
            f"{name}.{column}"
            for column in old.columns
            if column != old.index and column not in new_outputs
        )
        tables.append(
            SharedTable(
                name,
                table.many,
                columns,
                index=table.index,
                was_index=old.index,
                numbered=table.numbered,
                unmatched=_unmatched(name, old, table) if table.many else None,
            )
        )
    return SharedFields(tuple(tables), tuple(added), tuple(removed), tuple(reshaped))


def form_fields(form: Mapping[str, Any]) -> SharedFields:
    """The output fields of one form's document: each output table with its columns and,
    when it has many rows, its index."""
    return SharedFields(
        tuple(
            SharedTable(
                name,
                table.many,
                table.columns,
                index=table.index,
                was_index=table.index,
                numbered=table.numbered,
            )
            for name, table in _output_tables(form).items()
        )
    )


# ---------------------------------------------------------------------------
# One answer compared
# ---------------------------------------------------------------------------


def is_number(value: Any) -> bool:
    """Whether *value* is a number: an integer or a decimal, never true or false."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def moved(before: Any, after: Any, tolerance: float | None = None) -> bool:
    """Whether a value moved from *before* to *after*: a number by more than *tolerance* of
    the value before (a fraction), :data:`RELATIVE_CHANGE` when none is given, or from zero
    by anything; any other value when it is not the same."""
    if is_number(before) and is_number(after):
        if before == 0:
            return bool(after != 0)
        allowed = RELATIVE_CHANGE if tolerance is None else tolerance
        return bool(abs(after - before) > abs(before) * allowed)
    if is_number(before) != is_number(after):
        return True
    return bool(before != after)


def _rows(answer: Mapping[str, Any], table: str) -> list[Mapping[str, Any]]:
    rows = answer.get(table)
    if rows is None:
        return []
    if isinstance(rows, Mapping):
        return [rows]
    return [row if isinstance(row, Mapping) else {} for row in rows]


def _keyed(
    rows: Sequence[Mapping[str, Any]], index: str, side: str, table: str
) -> dict[tuple[str, Any], Mapping[str, Any]] | str:
    """*rows* by their *index* values, or why they cannot be: an index empty, or a value
    held twice."""
    keyed: dict[tuple[str, Any], Mapping[str, Any]] = {}
    for row in rows:
        value = row.get(index)
        if value is None:
            return f"a row of {table} {side} leaves its key {index} empty"
        hashable = _key_value(value)
        if hashable in keyed:
            return f"{table} {side} holds the key {_key_label(index, value)} twice"
        keyed[hashable] = row
    return keyed


def _key_value(value: Any) -> tuple[str, Any]:
    """A key value as rows are matched by it: a number by its value, so a key column that
    becomes a decimal still matches ``1`` with ``1.0``; any other value, true and false
    included, by its JSON."""
    if is_number(value):
        return ("number", value)
    return ("value", json.dumps(value, sort_keys=True))


def _key_label(index: str, value: Any) -> str:
    return f"{index}={value}"


def _row_moves(
    table: SharedTable,
    row: str | None,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    tolerance: float | None,
) -> list[Move]:
    # The index matched the rows, so it holds one value in both, whatever its name in each.
    return [
        Move(table.name, row, column, before.get(column), after.get(column))
        for column in table.columns
        if column != table.index and moved(before.get(column), after.get(column), tolerance)
    ]


def compare_quote(
    fields: SharedFields,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    sides: tuple[str, str],
    tolerance: float | None = None,
) -> Comparison:
    """Two answers, *before* and *after*, each table's rows by name, compared on *fields*:
    the moves, or why they no longer compare. *sides* names each answer where a reason says
    which (``on production``, ``in expected``); *tolerance* is how far a number may move, a
    fraction of its value before (:data:`RELATIVE_CHANGE` when None)."""
    moves: list[Move] = []
    for table in fields.tables:
        old = _rows(before, table.name)
        new = _rows(after, table.name)
        if not table.many:
            # One row a side: a second row is never left out of the comparison unseen.
            for rows, side in ((old, sides[0]), (new, sides[1])):
                if len(rows) > 1:
                    return Comparison(
                        why=f"{table.name} has {len(rows)} rows {side} though it has one row "
                        "per quote"
                    )
            moves.extend(
                _row_moves(table, None, old[0] if old else {}, new[0] if new else {}, tolerance)
            )
            continue
        if table.unmatched is not None:
            return Comparison(why=table.unmatched)
        if table.index is None or table.was_index is None:
            return Comparison(why=f"{table.name} has many rows and no index to match them by")
        old_keyed = _keyed(old, table.was_index, sides[0], table.name)
        if isinstance(old_keyed, str):
            return Comparison(why=old_keyed)
        new_keyed = _keyed(new, table.index, sides[1], table.name)
        if isinstance(new_keyed, str):
            return Comparison(why=new_keyed)
        for key, was in old_keyed.items():
            label = _key_label(table.index, was.get(table.was_index))
            if key in new_keyed:
                moves.extend(_row_moves(table, label, was, new_keyed[key], tolerance))
            else:
                moves.append(Move(table.name, label, None, True, False))
        for key, now in new_keyed.items():
            if key not in old_keyed:
                label = _key_label(table.index, now.get(table.index))
                moves.append(Move(table.name, label, None, False, True))
    return Comparison(moves=tuple(moves))
