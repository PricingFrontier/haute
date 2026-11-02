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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: A number has moved when it differs from the value before by more than this part of it: a
#: millionth of a percent, as the endpoint's impact counts a change.
RELATIVE_CHANGE = 1e-8


@dataclass(frozen=True, slots=True)
class SharedTable:
    """An output table compared: its name, whether it has many rows, the columns compared
    and the columns its rows are matched by."""

    name: str
    many: bool
    columns: tuple[str, ...]
    keys: tuple[str, ...]


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
    """A field that moved: its table, its row (its key values or its position; None in a
    one-row table), its column, the value before and after. A row only one side holds is a
    move with no column, ``before`` and ``after`` saying which side holds it."""

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
    keys: frozenset[str]


def _output_tables(form: Mapping[str, Any]) -> dict[str, _FormTable]:
    """The output tables of a form document, by name: read from the document as it stands,
    so a form another Haute version wrote reads alike."""
    schema = form.get("schema")
    tables = schema.get("tables") if isinstance(schema, Mapping) else None
    found: dict[str, _FormTable] = {}
    for table in tables if isinstance(tables, list) else []:
        if not isinstance(table, Mapping) or table.get("role", "input") != "output":
            continue
        columns = [each for each in table.get("columns") or [] if isinstance(each, Mapping)]
        found[str(table.get("name"))] = _FormTable(
            many=table.get("rows", "one") == "many",
            columns=tuple(str(column.get("name")) for column in columns),
            keys=frozenset(str(column.get("name")) for column in columns if column.get("key")),
        )
    return found


def shared_fields(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> SharedFields:
    """The output fields two forms' documents share, *baseline*'s and *candidate*'s, in the
    candidate's order, and those only one has."""
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
        columns = tuple(column for column in table.columns if column in old.columns)
        added.extend(f"{name}.{column}" for column in table.columns if column not in old.columns)
        removed.extend(f"{name}.{column}" for column in old.columns if column not in table.columns)
        keys = tuple(column for column in columns if column in table.keys and column in old.keys)
        tables.append(SharedTable(name, table.many, columns, keys if table.many else ()))
    return SharedFields(tuple(tables), tuple(added), tuple(removed), tuple(reshaped))


def form_fields(form: Mapping[str, Any]) -> SharedFields:
    """The output fields of one form's document: each output table with its columns and,
    when it has many rows, the columns it marks as its keys."""
    return SharedFields(
        tuple(
            SharedTable(
                name,
                table.many,
                table.columns,
                tuple(column for column in table.columns if column in table.keys)
                if table.many
                else (),
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
    rows: Sequence[Mapping[str, Any]], keys: tuple[str, ...], side: str, table: str
) -> dict[tuple[Any, ...], Mapping[str, Any]] | str:
    """*rows* by their key values, or why they cannot be: a key column empty, or a key held
    twice."""
    keyed: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(column) for column in keys)
        empty = [column for column, value in zip(keys, key, strict=True) if value is None]
        if empty:
            return f"a row of {table} {side} leaves its key {', '.join(empty)} empty"
        hashable = tuple(_key_value(value) for value in key)
        if hashable in keyed:
            return f"{table} {side} holds the key {_key_label(keys, key)} twice"
        keyed[hashable] = row
    return keyed


def _key_value(value: Any) -> tuple[str, Any]:
    """A key value as rows are matched by it: a number by its value, so a key column that
    becomes a decimal still matches ``1`` with ``1.0``; any other value, true and false
    included, by its JSON."""
    if is_number(value):
        return ("number", value)
    return ("value", json.dumps(value, sort_keys=True))


def _key_label(keys: tuple[str, ...], values: Iterable[Any]) -> str:
    return ", ".join(f"{column}={value}" for column, value in zip(keys, values, strict=True))


def _row_moves(
    table: SharedTable,
    row: str | None,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    tolerance: float | None,
) -> list[Move]:
    return [
        Move(table.name, row, column, before.get(column), after.get(column))
        for column in table.columns
        if moved(before.get(column), after.get(column), tolerance)
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
        if not table.keys:
            if len(old) != len(new):
                return Comparison(
                    why=f"{table.name} has {len(old)} rows {sides[0]} and {len(new)} "
                    f"{sides[1]}, and no key matches them"
                )
            for position, (was, now) in enumerate(zip(old, new, strict=True), 1):
                moves.extend(_row_moves(table, f"row {position}", was, now, tolerance))
            continue
        old_keyed = _keyed(old, table.keys, sides[0], table.name)
        if isinstance(old_keyed, str):
            return Comparison(why=old_keyed)
        new_keyed = _keyed(new, table.keys, sides[1], table.name)
        if isinstance(new_keyed, str):
            return Comparison(why=new_keyed)
        for key, was in old_keyed.items():
            label = _key_label(table.keys, (was.get(column) for column in table.keys))
            if key in new_keyed:
                moves.extend(_row_moves(table, label, was, new_keyed[key], tolerance))
            else:
                moves.append(Move(table.name, label, None, True, False))
        for key, now in new_keyed.items():
            if key not in old_keyed:
                label = _key_label(table.keys, (now.get(column) for column in table.keys))
                moves.append(Move(table.name, label, None, False, True))
    return Comparison(moves=tuple(moves))
