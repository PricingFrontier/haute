"""The quote store's schema changes (specs/workbench): dev's planner, bringing the analyst's
SQLite store to a release's form by stable id through what its ``haute_store`` records, and the
SQL Haute writes for the people who own a hosted database to change its tables with.

Dev's planner first reconciles the record with the database's own catalogue
(:func:`reconcile`): a table or column the form has that the catalogue lacks refuses it. It then
counts the quotes holding values in each table and column a step may drop or change
(:func:`counts`), plans (:func:`plan`), and applies the plan in the one transaction it runs in,
``haute_store``'s row last, so a plan that fails part way leaves nothing changed. It drops what
the form removed and builds again what changed while holding values, naming each.

A script (:func:`offline_plan`) is made from two forms' structures, or from none, with no
database: it keeps what the form removed, which its SQL drops only in a comment, makes each
change of a type, rows or role a step its SQL notes, and refuses a table or column taking a
removed one's name. :func:`render_sql` writes it for an engine, one transaction holding nothing
of Haute's beyond the tables, for the people who own a hosted database to read, change and run.

Every step runs in the store's SQL schema, the connection's default when it names none. A plan
for a server's engine refuses a name longer than that engine keeps whole. Dev changes the type
of a column holding no value in place where the engine can (:func:`._providers.retypes_in_place`),
replacing the empty column elsewhere; a script changes it in place, converting its values.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

from alembic.ddl.impl import DefaultImpl
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Column,
    Connection,
    MetaData,
    Table,
    func,
    insert,
    inspect,
    select,
    update,
)

from haute._quote_store._providers import (
    PROVIDERS,
    Provider,
    dialect_provider,
    name_problems,
    retypes_in_place,
)
from haute._quote_store._schema import (
    ACTIVITY_TABLE,
    STORE_TABLE,
    StoreSchema,
    column_type,
    store_schema,
    structure,
)
from haute._quote_store._store import (
    DEV,
    QuoteStoreError,
    QuoteStoreMismatchError,
    _recorded,
    _refuse_old_store,
    _refuse_other,
    make_engine,
    writing,
)
from haute._workbench_form import QUOTE_DETAILS_ID, FormSpec

#: How a plan treats what the form removed and what changed while holding values: dev's drops
#: and builds again; a script keeps what was removed and notes each change in its SQL.
Mode = Literal["dev", "script"]
StepKind = Literal[
    "create table",
    "add column",
    "rename table",
    "rename column",
    "keep table",
    "keep column",
    "retype column",
    "reshape table",
    "drop table",
    "drop column",
    "rebuild table",
    "rebuild column",
    "rebuild store",
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


class MigrationRefusedError(QuoteStoreError):
    """Dev's plan refused before any DDL: each step it cannot take is in *refusals*."""

    error_code = "migration_refused"

    def __init__(self, message: str, *, refusals: Sequence[str]) -> None:
        super().__init__(message)
        self.refusals = list(refusals)


@dataclass(frozen=True, slots=True)
class Step:
    """One change a plan makes, or, in a script, one thing it keeps: its kind, the table and
    column it concerns by their names after it and by their ids, the name each had before
    when renamed, and how many quotes hold values in what it concerns (None in a script)."""

    kind: StepKind
    table: str
    table_id: str
    column: str | None = None
    column_id: str | None = None
    was: str | None = None
    quotes: int | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """A plan's steps and refusals, the structure dev's ``haute_store`` records once it is
    applied, its mode, and the record or earlier form it was made from (None for an empty
    database, or a script from none)."""

    steps: tuple[Step, ...]
    refusals: tuple[str, ...]
    recorded: dict[str, Any] = field(repr=False)
    mode: Mode = "dev"
    before: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def dropped(self) -> list[str]:
        """Each table whose quotes' rows the plan drops, dev's alone: dropped or built again."""
        names: list[str] = []
        for step in self.steps:
            if step.kind in ("drop table", "rebuild table") and step.table not in names:
                names.append(step.table)
            if step.kind == "rebuild store":
                names.extend(each for each in step.table.split(", ") if each not in names)
        return names

    @property
    def cleared(self) -> list[str]:
        """Each column, as ``table.column``, whose values the plan drops from a table it keeps,
        dev's alone: dropped or built again while quotes held values in it."""
        return [
            f"{step.table}.{step.column}"
            for step in self.steps
            if step.kind in ("drop column", "rebuild column") and step.quotes
        ]


def _named(key: str) -> str:
    """A temporary name for a rename's first phase, so a swap and a change of case work."""
    return f"haute_tmp_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _by_id(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _count(counts: Mapping[Key, int] | None, key: Key) -> int | None:
    """The quotes holding values in what *key* names, None in a script. A count dev's plan
    needs and was not given is a fault, never taken for none, which would drop values unsaid."""
    if counts is None:
        return None
    if key not in counts:
        raise KeyError(f"The plan was given no count of {key}: counts follow wanted_counts.")
    return counts[key]


def wanted_counts(form: Mapping[str, Any], recorded: Mapping[str, Any]) -> set[Key]:
    """The tables and columns of *recorded* dev's plan to *form* counts the quotes of: those
    the form removed, and those whose type, rows or role it changed."""
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
    counts: Mapping[Key, int] | None,
    mode: Mode,
    *,
    provider: Provider | None = None,
) -> Plan:
    """The plan bringing a database whose ``haute_store`` *recorded*, or, in a script, an
    earlier form's structure (None for an empty one), to the structure of *form*, by stable
    id, given the *counts* of quotes holding values in each table and column
    :func:`wanted_counts` names (None in a script), on *provider*'s engine, which refuses a
    name longer than it keeps whole. Reads nothing else."""
    made = _plan(form, recorded, counts, mode)
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
    mode: Mode,
) -> Plan:
    after = json.loads(json.dumps(form))
    if recorded is None:
        created = tuple(
            Step("create table", table["name"], table["id"]) for table in form["tables"]
        )
        return Plan(created, (), after, mode=mode)
    if mode == "dev" and (recorded.get("format"), recorded.get("store_columns")) != (
        form["format"],
        form["store_columns"],
    ):
        names = ", ".join(table["name"] for table in recorded.get("tables", []))
        rebuild = (Step("rebuild store", names, ""),)
        return Plan(rebuild, (), after, mode=mode, before=dict(recorded))
    steps: list[Step] = []
    refusals: list[str] = []
    script = mode == "script"
    old = _by_id(recorded["tables"])
    new = _by_id(form["tables"])
    recorded_after: list[dict[str, Any]] = []
    # The names a script's removed tables still hold, since its SQL drops none: no table of the
    # form may take one, made or renamed.
    removed_names = {
        table["name"].casefold() for table in recorded["tables"] if table["id"] not in new
    }

    for table in form["tables"]:
        before = old.get(table["id"])
        if before is None:
            if script and table["name"].casefold() in removed_names:
                refusals.append(
                    f"the new table {table['name']} takes the name of a table the form removes, "
                    "which the SQL keeps: rename one, or drop the removed table first"
                )
                continue
            steps.append(Step("create table", table["name"], table["id"]))
            recorded_after.append(table)
            continue
        if (before["rows"], before["role"]) != (table["rows"], table["role"]):
            # A table of other rows or another role: in dev made afresh under its name, built
            # again while it holds rows; in a script a step its SQL notes, whatever it holds.
            quotes = _count(counts, (table["id"], None))
            if table["id"] == QUOTE_DETAILS_ID:
                refusals.append("quote_details takes no change of its rows or role")
                recorded_after.append(before)
            else:
                kind: StepKind = "reshape table" if script or quotes == 0 else "rebuild table"
                steps.append(
                    Step(kind, table["name"], table["id"], was=before["name"], quotes=quotes)
                )
                recorded_after.append(table)
            continue
        if script and before["name"] != table["name"] and table["name"].casefold() in removed_names:
            refusals.append(
                f"the table {before['name']}, renamed {table['name']}, takes the name of a table "
                "the form removes, which the SQL keeps: rename one, or drop the removed table "
                "first"
            )
            recorded_after.append(before)
            continue
        if before["name"] != table["name"]:
            steps.append(Step("rename table", table["name"], table["id"], was=before["name"]))
        columns_after = _plan_columns(table, before, counts, script, steps, refusals)
        recorded_after.append({**table, "columns": columns_after})

    for table in recorded["tables"]:
        if table["id"] in new:
            continue
        quotes = _count(counts, (table["id"], None))
        removed: StepKind = "keep table" if script else "drop table"
        steps.append(Step(removed, table["name"], table["id"], quotes=quotes))
    return Plan(
        tuple(steps),
        tuple(refusals),
        {**after, "tables": recorded_after},
        mode=mode,
        before=dict(recorded),
    )


def _plan_columns(
    table: dict[str, Any],
    before: dict[str, Any],
    counts: Mapping[Key, int] | None,
    script: bool,
    steps: list[Step],
    refusals: list[str],
) -> list[dict[str, Any]]:
    """The steps of one table both structures hold, column by column; the columns its record
    holds after them."""
    old = _by_id(before["columns"])
    new = _by_id(table["columns"])
    # The names this table's removed columns still hold in a script, whose SQL drops none: no
    # column of the form may take one, added or renamed.
    removed_names = {
        column["name"].casefold() for column in before["columns"] if column["id"] not in new
    }
    after: list[dict[str, Any]] = []
    name = table["name"]
    for column in table["columns"]:
        was = old.get(column["id"])
        key: Key = (table["id"], column["id"])
        if was is None:
            if script and column["name"].casefold() in removed_names:
                refusals.append(
                    f"the new column {name}.{column['name']} takes the name of a column the form "
                    "removes, which the SQL keeps: rename one, or drop the removed column first"
                )
                continue
            steps.append(Step("add column", name, table["id"], column["name"], column["id"]))
            after.append(column)
            continue
        if script and was["name"] != column["name"] and column["name"].casefold() in removed_names:
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
                kind: StepKind = "retype column" if script or quotes == 0 else "rebuild column"
                steps.append(
                    Step(kind, name, table["id"], column["name"], column["id"], quotes=quotes)
                )
        after.append(column)
    for column in before["columns"]:
        if column["id"] in new:
            continue
        quotes = _count(counts, (table["id"], column["id"]))
        removed: StepKind = "keep column" if script else "drop column"
        steps.append(Step(removed, name, table["id"], column["name"], column["id"], quotes=quotes))
    return after


# ── The database's side ─────────────────────────────────────────────────


def reconcile(
    recorded: Mapping[str, Any],
    form: Mapping[str, Any],
    catalogue: Any,
    sql_schema: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """*recorded* as the database's catalogue (a SQLAlchemy ``Inspector``) holds it in the SQL
    schema *sql_schema*: a table or column the catalogue lacks forgotten, and refused, naming
    it, while the form still has it; what neither records left alone."""
    present = set(catalogue.get_table_names(schema=sql_schema))
    new = _by_id(form["tables"])
    refusals: list[str] = []
    tables: list[dict[str, Any]] = []
    for table in recorded["tables"]:
        still = new.get(table["id"])
        if table["name"] not in present:
            if still is not None:
                refusals.append(
                    f"the table {table['name']}, which the form has, is missing from the database"
                )
            continue
        held = {
            column["name"] for column in catalogue.get_columns(table["name"], schema=sql_schema)
        }
        columns = []
        for column in table["columns"]:
            if column["name"] not in held:
                if still is not None and any(
                    each["id"] == column["id"] for each in still["columns"]
                ):
                    refusals.append(
                        f"{table['name']}.{column['name']}, which the form has, is missing from "
                        "the database"
                    )
                continue
            columns.append(column)
        tables.append({**table, "columns": columns})
    return {**recorded, "tables": tables}, refusals


def counts(
    connection: Connection,
    recorded: Mapping[str, Any],
    wanted: Iterable[Key],
    sql_schema: str | None = None,
) -> dict[Key, int]:
    """The number of quotes holding values in each table (a row) and column (a value that is
    not null) of *wanted*, as *recorded* names them in the SQL schema *sql_schema*: quotes, not
    rows, in a many-row table."""
    tables = _by_id(recorded["tables"])
    found: dict[Key, int] = {}
    for table_id, column_id in wanted:
        table = tables[table_id]
        names = [column["name"] for column in table["columns"] if column["id"] == column_id]
        held = Table(
            table["name"],
            MetaData(schema=sql_schema),
            *(Column(name) for name in dict.fromkeys(["quote_reference", "quote_option", *names])),
        )
        quotes = select(held.c.quote_reference, held.c.quote_option).distinct()
        if names:
            quotes = quotes.where(held.c[names[0]].is_not(None))
        found[(table_id, column_id)] = int(
            connection.execute(select(func.count()).select_from(quotes.subquery())).scalar_one()
        )
    return found


def _emit(operations: Operations, plan: Plan, schema: StoreSchema) -> None:
    """The DDL of *plan* through *operations*, live or as offline SQL: what dev drops first,
    then every rename in two phases through temporary names (tables, then columns), then what
    is made, each in the store's SQL schema; a script's notes and the DROPs it keeps commented
    out, written where their steps fall."""
    before = plan.before
    old = {} if before is None else _by_id(before["tables"])
    steps = plan.steps
    owner = schema.metadata.schema
    script = plan.mode == "script"
    if any(step.kind == "rebuild store" for step in steps):
        # Every table the store made, Haute's own two with them: children before quote_details,
        # which every other table refers to.
        for table in before["tables"] if before else []:
            if table["id"] != QUOTE_DETAILS_ID:
                operations.drop_table(table["name"], schema=owner, if_exists=True)
        for name in (ACTIVITY_TABLE, QUOTE_DETAILS_ID, STORE_TABLE):
            operations.drop_table(name, schema=owner, if_exists=True)
        for stored in schema.tables:
            operations.impl.create_table(stored.table)
        _create_haute_tables(operations, schema)
        return
    # What dev drops goes first, under the names the database holds before any rename, so a
    # name it frees is free for what follows.
    for step in steps:
        if step.kind in ("drop table", "rebuild table") or (
            step.kind == "reshape table" and not script
        ):
            operations.drop_table(old[step.table_id]["name"], schema=owner)
        elif step.kind in ("drop column", "rebuild column"):
            operations.drop_column(old[step.table_id]["name"], _was_name(old, step), schema=owner)
    renames = [step for step in steps if step.kind == "rename table" and step.was != step.table]
    for step in renames:
        operations.rename_table(str(step.was), _named(step.table_id), schema=owner)
    for step in renames:
        operations.rename_table(_named(step.table_id), step.table, schema=owner)
    # A column dev builds again was dropped under its old name above and is added under its
    # new one below: renaming it too would name a column already gone.
    rebuilt = {(step.table_id, step.column_id) for step in steps if step.kind == "rebuild column"}
    columns = [
        step
        for step in steps
        if step.kind == "rename column"
        and step.was != step.column
        and (step.table_id, step.column_id) not in rebuilt
    ]
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
    provider = dialect_provider(operations.impl.dialect.name)
    for step in steps:
        if step.kind in ("create table", "rebuild table") or (
            step.kind == "reshape table" and not script
        ):
            operations.impl.create_table(schema.by_id(step.table_id).table)
        elif step.kind in ("add column", "rebuild column"):
            operations.add_column(step.table, _column(schema, step), schema=owner)
        elif step.kind == "retype column" and script:
            _retype_in_place(operations, plan, schema, step)
        elif step.kind == "retype column":
            _retype(operations, provider, plan, schema, step)
        elif step.kind == "reshape table":
            _reshape_note(operations, plan, step)
        elif step.kind in ("keep table", "keep column"):
            _removed_note(operations, old, step, owner)
    if before is None:
        # A database holding none of the store takes Haute's tables with the form's.
        _create_haute_tables(operations, schema)


def _create_haute_tables(operations: Operations, schema: StoreSchema) -> None:
    """Haute's own tables: dev's record, where the schema keeps one, and the activity."""
    if schema.store is not None:
        operations.impl.create_table(schema.store)
    operations.impl.create_table(schema.activity)


def _note(operations: Operations, text: str) -> None:
    """*text* as a comment in a script's SQL, where the step it concerns falls."""
    impl = operations.impl
    if not isinstance(impl, DefaultImpl):
        raise TypeError("A script's notes are written by an offline migration context alone.")
    impl.static_output("\n".join(f"-- {line}" for line in text.splitlines()))


def _structure_table(structure: Mapping[str, Any] | None, table_id: str) -> dict[str, Any]:
    """The table *structure* holds by the id *table_id*."""
    for table in (structure or {}).get("tables", []):
        if table["id"] == table_id:
            return dict(table)
    raise KeyError(table_id)


def _type_of(structure: Mapping[str, Any] | None, step: Step) -> str:
    """The type *structure* gives the column *step* concerns, by its ids."""
    for column in _structure_table(structure, step.table_id)["columns"]:
        if column["id"] == step.column_id:
            return str(column["type"])
    raise KeyError((step.table_id, step.column_id))


def _retype(
    operations: Operations, provider: Provider, plan: Plan, schema: StoreSchema, step: Step
) -> None:
    """Change dev's empty column *step* names to the store's type for it: in place where
    *provider* can, PostgreSQL taking every value as null, else by replacing it with a column
    of the new type, which loses nothing."""
    owner = schema.metadata.schema
    was = _type_of(plan.before, step)
    fresh = _column(schema, step)
    if retypes_in_place(provider, was, _type_of(plan.recorded, step)):
        using: dict[str, Any] = {}
        if provider.name == "postgresql":
            using["postgresql_using"] = (
                f"NULL::{fresh.type.compile(dialect=operations.impl.dialect)}"
            )
        operations.alter_column(
            step.table,
            str(step.column),
            type_=fresh.type,
            existing_type=column_type(was),
            existing_nullable=True,
            schema=owner,
            **using,
        )
        return
    temporary = _named(f"{step.table_id}.{step.column_id}.type")
    operations.add_column(step.table, Column(temporary, fresh.type, nullable=True), schema=owner)
    operations.drop_column(step.table, str(step.column), schema=owner)
    operations.alter_column(step.table, temporary, new_column_name=step.column, schema=owner)


def _retype_in_place(operations: Operations, plan: Plan, schema: StoreSchema, step: Step) -> None:
    """A script's change of *step*'s column to the store's type for it, in place, the engine
    converting each value the column holds, PostgreSQL through a USING cast, after a note that
    a value which does not convert stops the script; on SQLite, which cannot change a column's
    type, the note alone says what to do."""
    dialect = operations.impl.dialect
    fresh = _column(schema, step)
    was = column_type(_type_of(plan.before, step))
    old, new = was.compile(dialect=dialect), fresh.type.compile(dialect=dialect)
    where = f"{step.table}.{step.column}"
    if dialect.name == "sqlite":
        _note(
            operations,
            f"{where} changes from {old} to {new}, which SQLite cannot do in place: add a column\n"
            "of the new type, copy the values into it, drop the old column and rename the new one.",
        )
        return
    _note(
        operations,
        f"{where} changes from {old} to {new}, each value converted as it stands: one that\n"
        "does not convert stops the script, changing nothing.",
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
        f"{step.table} changes from an {was['role']} table of {_ROWS[was['rows']]} to an "
        f"{now['role']} table of {_ROWS[now['rows']]},\n"
        "which no statement here makes: make the table again as the form has it and move its\n"
        "rows into it.",
    )


def _removed_note(
    operations: Operations, old: Mapping[str, dict[str, Any]], step: Step, owner: str | None
) -> None:
    """A script's note on a table or column the form removed, with its DROP commented out: the
    app's check passes a column or table it no longer uses, so dropping it is the owners' call."""
    preparer = operations.impl.dialect.identifier_preparer
    table = preparer.format_table(Table(old[step.table_id]["name"], MetaData(schema=owner)))
    if step.kind == "keep table":
        _note(
            operations,
            f"The form no longer has {step.table}; drop it once nothing reads it:\n"
            f"DROP TABLE {table};",
        )
        return
    _note(
        operations,
        f"The form no longer has {step.table}.{step.column}; drop it once nothing reads it:\n"
        f"ALTER TABLE {table} DROP COLUMN {preparer.quote(str(step.column))};",
    )


def _was_name(old: Mapping[str, dict[str, Any]], step: Step) -> str:
    """The name of the column *step* drops, as the database holds it before the plan."""
    for column in old[step.table_id]["columns"]:
        if column["id"] == step.column_id:
            return str(column["name"])
    return str(step.column)


def _column(schema: StoreSchema, step: Step) -> Column[Any]:
    """A fresh copy of the store's column *step* adds, nullable as every column a table gains."""
    found = schema.by_id(step.table_id).table.c[str(step.column)]
    return Column(found.name, found.type, nullable=True)


def _record(
    operations: Operations,
    schema: StoreSchema,
    plan: Plan,
    *,
    fresh: bool,
    project: str,
    release: str | None,
    haute_version: str | None,
    at: Any,
) -> None:
    store = schema.store
    if store is None:
        raise ValueError("Only dev's store keeps a record of its tables.")
    values = {
        "release": release,
        "haute_version": haute_version,
        "migrated_at": at,
        "schema": json.dumps(plan.recorded, sort_keys=True, ensure_ascii=False),
    }
    if fresh:
        operations.execute(insert(store).values(id=1, project=project, environment=DEV, **values))
    else:
        operations.execute(update(store).where(store.c.id == 1).values(**values))


def _refuse(plan: Plan) -> None:
    if plan.refusals:
        raise MigrationRefusedError(
            "The plan was refused, with nothing changed: " + "; ".join(plan.refusals) + ".",
            refusals=plan.refusals,
        )


def _planned(connection: Connection, schema: StoreSchema, form: FormSpec, project: str) -> Plan:
    """Dev's plan for the store on *connection*: an earlier Haute's store, another project's or
    environment's, and tables of the form's names the store did not make refused, nothing
    adopted; then its record reconciled with the catalogue, its quotes counted, and planned."""
    owner = schema.metadata.schema
    provider = dialect_provider(connection.dialect.name)
    catalogue = inspect(connection)
    names = set(catalogue.get_table_names(schema=owner))
    _refuse_old_store(names)
    wanted = structure(form)
    held = _recorded(connection, schema) if STORE_TABLE in names else None
    if held is None:
        taken = sorted(
            names & {stored.table.name for stored in schema.tables} | names & {ACTIVITY_TABLE}
        )
        if taken:
            raise QuoteStoreMismatchError(
                "This database holds tables of the form's names that the quote store did not "
                f"make ({', '.join(taken)}): the store adopts none; point it at another database "
                "or schema.",
                differences=taken,
            )
        return plan(wanted, None, {}, "dev", provider=provider)
    _refuse_other(held, project, DEV)
    recorded = json.loads(held.structure)
    if (recorded.get("format"), recorded.get("store_columns")) != (
        wanted["format"],
        wanted["store_columns"],
    ):
        return plan(wanted, recorded, {}, "dev", provider=provider)
    reconciled, refusals = reconcile(recorded, wanted, catalogue, owner)
    found = counts(connection, reconciled, wanted_counts(wanted, reconciled), owner)
    made = plan(wanted, reconciled, found, "dev", provider=provider)
    if refusals:
        made = replace(made, refusals=(*refusals, *made.refusals))
    return made


def prepare_store(
    url: str,
    form: FormSpec,
    *,
    project: str,
    haute_version: str | None = None,
    release: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Plan:
    """Make dev's store at *url* hold *form*'s tables for *project*, and answer the plan
    applied, in one transaction. Refused (:class:`MigrationRefusedError`), with nothing
    changed, when the plan refuses a step. A database of an earlier Haute's store, of another
    project's or environment's, or holding tables of the form's names the store did not make,
    is refused, nothing adopted."""
    schema = store_schema(form)
    now = (clock or (lambda: datetime.now(UTC)))()
    engine = make_engine(url)
    try:
        with writing(engine) as connection:
            made = _planned(connection, schema, form, project)
            _refuse(made)
            operations = Operations(MigrationContext.configure(connection))
            _emit(operations, made, schema)
            rebuilt = any(step.kind == "rebuild store" for step in made.steps)
            _record(
                operations,
                schema,
                made,
                fresh=made.before is None or rebuilt,
                project=project,
                release=release,
                haute_version=haute_version,
                at=now,
            )
            return made
    finally:
        engine.dispose()


def offline_plan(
    base: FormSpec | None, form: FormSpec, *, provider: Provider | None = None
) -> Plan:
    """The script from *base*'s tables, or from none, to *form*'s, with no database, on
    *provider*'s engine."""
    return plan(
        structure(form),
        None if base is None else structure(base),
        None,
        "script",
        provider=provider,
    )


# ── Writing a script ────────────────────────────────────────────────────


def render_sql(plan: Plan, form: FormSpec, dialect: str, *, sql_schema: str | None = None) -> str:
    """*plan*, a script to *form*'s tables, as the SQL the people who own a hosted database
    read, change and run: Alembic's offline rendering for *dialect*, every value written in,
    each name in the SQL schema *sql_schema* when one is named, the whole of it one
    transaction, so a statement that fails leaves nothing changed. Each refusal heads it as a
    note, and each change of a type, rows or role and each table or column the form removed
    is noted where it falls, a removed one's DROP commented out. Nothing of Haute's is in it
    beyond the tables, and nothing reads it back."""
    if plan.mode != "script":
        raise ValueError("Only a script is written as SQL: dev's plan is applied by the planner.")
    buffer = io.StringIO()
    for refusal in plan.refusals:
        buffer.write(f"-- Not written: {refusal}.\n")
    if not plan.steps:
        if not plan.refusals:
            buffer.write("-- The tables already fit the form: nothing to change.\n")
        return buffer.getvalue()
    if plan.refusals:
        buffer.write("\n")
    options: dict[str, Any] = {"as_sql": True, "output_buffer": buffer, "literal_binds": True}
    if dialect == "mssql":
        # One batch: a GO between statements would end the transaction's hold on them.
        options["mssql_batch_separator"] = None
    context = MigrationContext.configure(dialect_name=dialect, opts=options)
    operations = Operations(context)
    schema = store_schema(form, sql_schema=sql_schema, record=False)
    begin, commit = _SCRIPT_TRANSACTIONS[dialect]
    buffer.write(begin)
    _emit(operations, plan, schema)
    buffer.write(commit)
    return buffer.getvalue()
