"""The SQL Haute writes for the people who own a quote store's database to change its tables
with (specs/workbench): a plan from one structure of the store's tables to a form's, by stable
id, and the SQL that carries it out.

A plan (:func:`plan`) is made from what the database holds, as an earlier form's structure
(:func:`offline_plan`) or as its own catalogue read into one (:mod:`._compare`), or from none,
for a database that holds no store yet. It keeps what the form removed, which its SQL drops
only in a comment, makes each change of a type, rows or role a step its SQL notes, and refuses
a table or column taking a removed one's name. Given the quotes holding values in each table
and column (from a database's catalogue), each note on one says how many. :func:`render_sql`
writes it for an engine, one transaction holding nothing of Haute's beyond the tables, for the
people who own the database to read, change and run. Nothing here connects to a database.

Every step runs in the store's SQL schema, the connection's default when it names none. A plan
for a server's engine refuses a name longer than that engine keeps whole. A column changes its
type in place, the engine converting its values.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from alembic.ddl.impl import DefaultImpl
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import TypeEngine

from haute._quote_store._providers import PROVIDERS, Provider, name_problems
from haute._quote_store._schema import StoreSchema, column_type, store_schema, structure
from haute._workbench_form import QUOTE_DETAILS_ID, FormSpec

StepKind = Literal[
    "create table",
    "add column",
    "rename table",
    "rename column",
    "keep table",
    "keep column",
    "retype column",
    "reshape table",
]
#: A table's or a column's count, keyed by the table's id and the column's, None for the table.
Key = tuple[str, str | None]
#: The SQL dialect each provider's script is written in.
PROVIDER_DIALECTS: Mapping[str, str] = {name: each.dialect for name, each in PROVIDERS.items()}
#: How a script's notes name a table's rows.
_ROWS: Mapping[str, str] = {"one": "one row per quote", "many": "many rows per quote"}
#: How each dialect's script opens and closes the one transaction that holds all of it.
#: SQL Server rolls back at an error that ends the batch (XACT_ABORT), and its TRY ... CATCH
#: at one a procedure raises and goes on from, as sp_rename does, raising it again;
#: PostgreSQL turns the COMMIT of a failed transaction into a rollback; and SQLite's shell
#: stops before the COMMIT with -bail.
_SCRIPT_TRANSACTIONS: Mapping[str, tuple[str, str]] = {
    "mssql": (
        "SET XACT_ABORT ON;\nBEGIN TRANSACTION;\nBEGIN TRY\n\n",
        "COMMIT TRANSACTION;\nEND TRY\nBEGIN CATCH\n"
        "    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;\n    THROW;\nEND CATCH;\n",
    ),
    "postgresql": ("BEGIN;\n\n", "COMMIT;\n"),
    "sqlite": (
        "-- Run with sqlite3 -bail, which stops at a failing statement before the COMMIT.\n"
        "BEGIN;\n\n",
        "COMMIT;\n",
    ),
}


@dataclass(frozen=True, slots=True)
class Step:
    """One change a plan makes, or one thing it keeps: its kind, the table and column it
    concerns by their names after it and by their ids, the name each had before when renamed,
    and how many quotes hold values in what it concerns (None when not counted)."""

    kind: StepKind
    table: str
    table_id: str
    column: str | None = None
    column_id: str | None = None
    was: str | None = None
    quotes: int | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """A plan's steps and refusals, the structure the tables have once it is carried out, the
    structure it was made from (None for a database holding no store), whether it makes
    ``haute_activity``, which a database holding no store lacks, and the notes its SQL heads
    with: the ways the tables differ that no statement of it changes."""

    steps: tuple[Step, ...]
    refusals: tuple[str, ...]
    recorded: dict[str, Any] = field(repr=False)
    before: dict[str, Any] | None = field(default=None, repr=False)
    activity: bool = False
    notes: tuple[str, ...] = ()


def _named(key: str) -> str:
    """A temporary name for a rename's first phase, so a swap and a change of case work."""
    return f"haute_tmp_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _by_id(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _count(counts: Mapping[Key, int] | None, key: Key) -> int | None:
    """The quotes holding values in what *key* names, None when nothing was counted. A count
    a counted plan needs and was not given is a fault, never taken for none."""
    if counts is None:
        return None
    if key not in counts:
        raise KeyError(f"The plan was given no count of {key}: counts follow wanted_counts.")
    return counts[key]


def wanted_counts(form: Mapping[str, Any], recorded: Mapping[str, Any]) -> set[Key]:
    """The tables and columns of *recorded* a plan to *form* says the quotes of: those the
    form removed, and those whose type, rows or role it changed."""
    new = _by_id(form["tables"])
    wanted: set[Key] = set()
    for table in recorded["tables"]:
        after = new.get(table["id"])
        if after is None:
            # A table removed is counted whole.
            wanted.add((table["id"], None))
            continue
        if (table["rows"], table["role"]) != (after["rows"], after["role"]):
            wanted.add((table["id"], None))
        columns = _by_id(after["columns"])
        for column in table["columns"]:
            changed = columns.get(column["id"])
            if changed is None or changed["type"] != column["type"]:
                wanted.add((table["id"], column["id"]))
    return wanted


def plan(
    form: Mapping[str, Any],
    recorded: Mapping[str, Any] | None,
    counts: Mapping[Key, int] | None = None,
    *,
    provider: Provider | None = None,
) -> Plan:
    """The plan bringing a database whose tables have the structure *recorded* (None for one
    holding no store) to the structure of *form*, by stable id, given the *counts* of quotes
    holding values in each table and column :func:`wanted_counts` names (None when nothing
    was counted), on *provider*'s engine, which refuses a name longer than it keeps whole.
    Reads nothing else."""
    made = _plan(form, recorded, counts)
    names = [
        name
        for table in form["tables"]
        for name in (table["name"], *(column["name"] for column in table["columns"]))
    ]
    problems = [] if provider is None else name_problems(provider, names)
    return replace(made, refusals=(*problems, *made.refusals)) if problems else made


def _plan(
    form: Mapping[str, Any],
    recorded: Mapping[str, Any] | None,
    counts: Mapping[Key, int] | None,
) -> Plan:
    after = json.loads(json.dumps(form))
    if recorded is None:
        created = tuple(
            Step("create table", table["name"], table["id"]) for table in form["tables"]
        )
        return Plan(created, (), after, activity=True)
    steps: list[Step] = []
    refusals: list[str] = []
    old = _by_id(recorded["tables"])
    new = _by_id(form["tables"])
    recorded_after: list[dict[str, Any]] = []
    # The names the removed tables still hold, since the SQL drops none: no table of the form
    # may take one, made or renamed.
    removed_names = {
        table["name"].casefold() for table in recorded["tables"] if table["id"] not in new
    }

    for table in form["tables"]:
        before = old.get(table["id"])
        if before is None:
            if table["name"].casefold() in removed_names:
                refusals.append(
                    f"the new table {table['name']} takes the name of a table the form removes, "
                    "which the SQL keeps: rename one, or drop the removed table first"
                )
                continue
            steps.append(Step("create table", table["name"], table["id"]))
            recorded_after.append(table)
            continue
        if (before["rows"], before["role"]) != (table["rows"], table["role"]):
            # A table of other rows or another role: a step the SQL notes, whatever it holds.
            quotes = _count(counts, (table["id"], None))
            if table["id"] == QUOTE_DETAILS_ID:
                refusals.append("quote_details takes no change of its rows or role")
                recorded_after.append(before)
            else:
                steps.append(
                    Step(
                        "reshape table",
                        table["name"],
                        table["id"],
                        was=before["name"],
                        quotes=quotes,
                    )
                )
                recorded_after.append(table)
            continue
        if before["name"] != table["name"] and table["name"].casefold() in removed_names:
            refusals.append(
                f"the table {before['name']}, renamed {table['name']}, takes the name of a table "
                "the form removes, which the SQL keeps: rename one, or drop the removed table "
                "first"
            )
            recorded_after.append(before)
            continue
        if before["name"] != table["name"]:
            steps.append(Step("rename table", table["name"], table["id"], was=before["name"]))
        columns_after = _plan_columns(table, before, counts, steps, refusals)
        recorded_after.append({**table, "columns": columns_after})

    for table in recorded["tables"]:
        if table["id"] in new:
            continue
        quotes = _count(counts, (table["id"], None))
        steps.append(Step("keep table", table["name"], table["id"], quotes=quotes))
    return Plan(
        tuple(steps),
        tuple(refusals),
        {**after, "tables": recorded_after},
        before=dict(recorded),
    )


def _plan_columns(
    table: dict[str, Any],
    before: dict[str, Any],
    counts: Mapping[Key, int] | None,
    steps: list[Step],
    refusals: list[str],
) -> list[dict[str, Any]]:
    """The steps of one table both structures hold, column by column; the columns it holds
    after them."""
    old = _by_id(before["columns"])
    new = _by_id(table["columns"])
    # The names this table's removed columns still hold, since the SQL drops none: no column of
    # the form may take one, added or renamed.
    removed_names = {
        column["name"].casefold() for column in before["columns"] if column["id"] not in new
    }
    after: list[dict[str, Any]] = []
    name = table["name"]
    for column in table["columns"]:
        was = old.get(column["id"])
        key: Key = (table["id"], column["id"])
        if was is None:
            if column["name"].casefold() in removed_names:
                refusals.append(
                    f"the new column {name}.{column['name']} takes the name of a column the form "
                    "removes, which the SQL keeps: rename one, or drop the removed column first"
                )
                continue
            steps.append(Step("add column", name, table["id"], column["name"], column["id"]))
            after.append(column)
            continue
        if was["name"] != column["name"] and column["name"].casefold() in removed_names:
            refusals.append(
                f"the column {name}.{was['name']}, renamed {column['name']}, takes the name of a "
                "column the form removes, which the SQL keeps: rename one, or drop the removed "
                "column first"
            )
            after.append(was)
            continue
        if was["name"] != column["name"]:
            steps.append(
                Step(
                    "rename column",
                    name,
                    table["id"],
                    column["name"],
                    column["id"],
                    was=was["name"],
                )
            )
        if was["type"] != column["type"]:
            quotes = _count(counts, key)
            if table["id"] == QUOTE_DETAILS_ID:
                refusals.append(f"quote_details.{column['name']} takes no change of its type")
            else:
                steps.append(
                    Step(
                        "retype column",
                        name,
                        table["id"],
                        column["name"],
                        column["id"],
                        quotes=quotes,
                    )
                )
        after.append(column)
    for column in before["columns"]:
        if column["id"] in new:
            continue
        quotes = _count(counts, (table["id"], column["id"]))
        steps.append(
            Step("keep column", name, table["id"], column["name"], column["id"], quotes=quotes)
        )
    return after


def _emit(operations: Operations, plan: Plan, schema: StoreSchema) -> None:
    """The DDL of *plan* through *operations*, as offline SQL: every rename in two phases
    through temporary names (tables, then columns), then what is made, each in the store's
    SQL schema; the notes and the DROPs it keeps commented out written where their steps
    fall."""
    steps = plan.steps
    owner = schema.metadata.schema
    renames = [step for step in steps if step.kind == "rename table" and step.was != step.table]
    for step in renames:
        operations.rename_table(str(step.was), _named(step.table_id), schema=owner)
    for step in renames:
        operations.rename_table(_named(step.table_id), step.table, schema=owner)
    columns = [step for step in steps if step.kind == "rename column" and step.was != step.column]
    for step in columns:
        operations.alter_column(
            step.table,
            str(step.was),
            new_column_name=_named(f"{step.table_id}.{step.column_id}"),
            schema=owner,
        )
    for step in columns:
        operations.alter_column(
            step.table,
            _named(f"{step.table_id}.{step.column_id}"),
            new_column_name=step.column,
            schema=owner,
        )
    for step in steps:
        if step.kind == "create table":
            operations.impl.create_table(schema.by_id(step.table_id).table)
        elif step.kind == "add column":
            operations.add_column(step.table, _column(schema, step), schema=owner)
        elif step.kind == "retype column":
            _retype_in_place(operations, plan, schema, step)
        elif step.kind == "reshape table":
            _reshape_note(operations, plan, step)
        elif step.kind in ("keep table", "keep column"):
            _removed_note(operations, step, owner)
    if plan.activity:
        # A database holding none of the store takes Haute's activity with the form's tables.
        operations.impl.create_table(schema.activity)


#: What a step's or note's ``quotes`` holds when a table was read for a counted comparison
#: but lacked a quote identity column, so its quotes could not be counted.
UNCOUNTED = -1


def _commented(text: str) -> str:
    """*text* as SQL comments, each of its lines one, so no name in it ends a comment."""
    return "\n".join(f"-- {line}" for line in text.splitlines())


def held(quotes: int | None, what: str = "values") -> str:
    """How many quotes hold *what* in a table or column, as a note or a mark puts it after
    the table's or column's name (:data:`UNCOUNTED` when the table lacks a quote identity
    column so its quotes could not be counted); nothing when none were counted or none hold
    any."""
    if quotes == UNCOUNTED:
        return f" (quotes holding {what} not counted: table lacks quote identity)"
    if not quotes:
        return ""
    holding = "1 quote holds" if quotes == 1 else f"{quotes} quotes hold"
    return f" ({holding} {what} in it)"


def _note(operations: Operations, text: str) -> None:
    """*text* as a comment in a script's SQL, where the step it concerns falls."""
    impl = operations.impl
    if not isinstance(impl, DefaultImpl):
        raise TypeError("A script's notes are written by an offline migration context alone.")
    impl.static_output(_commented(text))


def _structure_table(structure: Mapping[str, Any] | None, table_id: str) -> dict[str, Any]:
    """The table *structure* holds by the id *table_id*."""
    for table in (structure or {}).get("tables", []):
        if table["id"] == table_id:
            return dict(table)
    raise KeyError(table_id)


def _structure_column(structure: Mapping[str, Any] | None, step: Step) -> dict[str, Any]:
    """The column *structure* holds by *step*'s ids."""
    for column in _structure_table(structure, step.table_id)["columns"]:
        if column["id"] == step.column_id:
            return dict(column)
    raise KeyError((step.table_id, step.column_id))


def _retype_in_place(operations: Operations, plan: Plan, schema: StoreSchema, step: Step) -> None:
    """A change of *step*'s column to the store's type for it, in place, the engine converting
    each value the column holds, PostgreSQL through a USING cast, after a note that a value
    which does not convert stops the script; on SQLite, which cannot change a column's type,
    the note alone says what to do. The type before is the one the database names where it
    was read from one, else the store's for the earlier form's type."""
    dialect = operations.impl.dialect
    fresh = _column(schema, step)
    before = _structure_column(plan.before, step)
    was: TypeEngine[Any] | None = None if "sql_type" in before else column_type(str(before["type"]))
    old = str(before["sql_type"]) if was is None else str(was.compile(dialect=dialect))
    new = fresh.type.compile(dialect=dialect)
    where = f"{step.table}.{step.column}"
    if dialect.name == "sqlite":
        _note(
            operations,
            f"{where}{held(step.quotes)} changes from {old} to {new}, which SQLite cannot do\n"
            "in place: add a column of the new type, copy the values into it, drop the old column\n"
            "and rename the new one.",
        )
        return
    _note(
        operations,
        f"{where}{held(step.quotes)} changes from {old} to {new}, each value converted as it\n"
        "stands: one that does not convert stops the script, changing nothing.",
    )
    using: dict[str, Any] = {}
    if dialect.name == "postgresql":
        using["postgresql_using"] = f"{dialect.identifier_preparer.quote(str(step.column))}::{new}"
    operations.alter_column(
        step.table,
        str(step.column),
        type_=fresh.type,
        existing_type=was,
        existing_nullable=True,
        schema=schema.metadata.schema,
        **using,
    )


def _reshape_note(operations: Operations, plan: Plan, step: Step) -> None:
    """A script's note on a table whose rows or role the form changed, which no statement
    changes: what the table was and is, and what it needs."""
    was = _structure_table(plan.before, step.table_id)
    now = _structure_table(plan.recorded, step.table_id)
    _note(
        operations,
        f"{step.table}{held(step.quotes, 'rows')} changes from an {was['role']} table of "
        f"{_ROWS[was['rows']]} to an "
        f"{now['role']} table of {_ROWS[now['rows']]},\n"
        "which no statement here makes: make the table again as the form has it and move its\n"
        "rows into it.",
    )


def _removed_note(operations: Operations, step: Step, owner: str | None) -> None:
    """A script's note on a table or column the form removed, with its DROP commented out: the
    app's check passes a column or table it no longer uses, so dropping it is the owners' call.
    The DROP names the table as the script leaves it: a removed column's by its new name."""
    preparer = operations.impl.dialect.identifier_preparer
    table = preparer.format_table(Table(step.table, MetaData(schema=owner)))
    if step.kind == "keep table":
        _note(
            operations,
            f"The form does not have {step.table}{held(step.quotes, 'rows')}; drop it once "
            f"nothing reads it:\nDROP TABLE {table};",
        )
        return
    _note(
        operations,
        f"The form does not have {step.table}.{step.column}{held(step.quotes)}; drop it once "
        f"nothing reads it:\nALTER TABLE {table} DROP COLUMN {preparer.quote(str(step.column))};",
    )


def _column(schema: StoreSchema, step: Step) -> Column[Any]:
    """A fresh copy of the store's column *step* adds, nullable as every column a table gains."""
    found = schema.by_id(step.table_id).table.c[str(step.column)]
    return Column(found.name, found.type, nullable=True)


def offline_plan(
    base: FormSpec | None, form: FormSpec, *, provider: Provider | None = None
) -> Plan:
    """The plan from *base*'s tables, or from none, to *form*'s, with no database, on
    *provider*'s engine."""
    return plan(structure(form), None if base is None else structure(base), provider=provider)


# ── Writing a script ────────────────────────────────────────────────────


def render_sql(plan: Plan, form: FormSpec, dialect: str, *, sql_schema: str | None = None) -> str:
    """*plan*, to *form*'s tables, as the SQL the people who own a database read, change and
    run: Alembic's offline rendering for *dialect*, every value written in, each name in the
    SQL schema *sql_schema* when one is named, the whole of it one transaction, so a
    statement that fails leaves nothing changed. Each refusal and each of the plan's notes
    heads it, and each change of a type, rows or role and each table or column the form does
    not have is noted where it falls, the latter's DROP commented out. Nothing of Haute's is in
    it beyond the tables, and nothing reads it back."""
    buffer = io.StringIO()
    for refusal in plan.refusals:
        buffer.write(_commented(f"Not written: {refusal}.") + "\n")
    for note in plan.notes:
        buffer.write(_commented(f"Not changed here: {note}.") + "\n")
    if not plan.steps and not plan.activity:
        if not plan.refusals and not plan.notes:
            buffer.write("-- The tables already fit the form: nothing to change.\n")
        return buffer.getvalue()
    if plan.refusals or plan.notes:
        buffer.write("\n")
    options: dict[str, Any] = {"as_sql": True, "output_buffer": buffer, "literal_binds": True}
    if dialect == "mssql":
        # One batch: a GO between statements would end the transaction's hold on them.
        options["mssql_batch_separator"] = None
    context = MigrationContext.configure(dialect_name=dialect, opts=options)
    operations = Operations(context)
    schema = store_schema(form, sql_schema=sql_schema)
    begin, commit = _SCRIPT_TRANSACTIONS[dialect]
    buffer.write(begin)
    _emit(operations, plan, schema)
    buffer.write(commit)
    return buffer.getvalue()
