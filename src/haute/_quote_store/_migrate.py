"""The quote store's schema changes (specs/workbench): one planner bringing a database's tables
to a release's form, comparing them by stable id through what ``haute_store`` records.

A migrate first reconciles that record with the database's own catalogue (:func:`reconcile`):
a kept table or column the SQL side dropped is forgotten, so its name is free, and one the form
still has that the catalogue lacks refuses the migrate. It then counts the quotes holding values
in each table and column a step may keep, drop or change (:func:`counts`), plans
(:func:`plan`), and applies the plan in the one transaction it runs in, ``haute_store``'s row
last, so a migrate that fails part way leaves nothing changed.

A plan is made from the form's structure, the reconciled record and the counts alone. In
``hosted`` mode, a test or prod environment's, it drops nothing: a table or column the form
removed is kept, listed with how many quotes hold values in it and the DROP statement for the
DBA; a type, rows or role change of a table or column holding values is refused, as is a new
table or column taking a kept one's name. In ``dev`` mode, the analyst's laptop's, it drops what
the form removed and builds again what changed while holding values, naming each. Offline (no
counts), each step that depends on data is conditional: made if the object holds no value, else
refused. Each step says what reverses it: nothing, a migrate back to the previous release, or
only a restore, which a hosted plan holding one needs a restore point for.

Every step runs in the store's SQL schema, the connection's default when its environment names
none. A plan for a server's engine refuses a name longer than that engine keeps whole, and
changes the type of a column holding no value in place where the engine can
(:func:`._providers.retypes_in_place`), replacing the empty column elsewhere.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

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
    literal,
    select,
    update,
)

from haute._quote_store._providers import (
    PROVIDERS,
    Provider,
    SignIn,
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
    reaching,
    writing,
)
from haute._workbench_form import QUOTE_DETAILS_ID, FormSpec

#: How a migrate treats what the form removed and what changed while holding values: a hosted
#: environment's keeps and refuses, dev's drops and builds again.
Mode = Literal["dev", "hosted"]
#: What reverses a step: nothing, a migrate back to the previous release, or only a restore.
Rollback = Literal["nothing", "a migrate back", "a restore"]
StepKind = Literal[
    "create table",
    "add column",
    "rename table",
    "rename column",
    "keep table",
    "keep column",
    "bring back table",
    "bring back column",
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
#: The SQL dialect each provider's offline plan is printed in.
PROVIDER_DIALECTS: Mapping[str, str] = {name: each.dialect for name, each in PROVIDERS.items()}


class MigrationRefusedError(QuoteStoreError):
    """A migrate refused before any DDL: each step it cannot take is in *refusals*."""

    error_code = "migration_refused"

    def __init__(self, message: str, *, refusals: Sequence[str]) -> None:
        super().__init__(message)
        self.refusals = list(refusals)


@dataclass(frozen=True, slots=True)
class Step:
    """One change a plan makes, or one thing it keeps: its kind, the table and column it
    concerns by their names after it and by their ids, the name each had before when renamed,
    how many quotes hold values in what it concerns (None unknown, offline), what reverses it,
    and whether it is made only if the object holds no value (offline)."""

    kind: StepKind
    table: str
    table_id: str
    column: str | None = None
    column_id: str | None = None
    was: str | None = None
    quotes: int | None = None
    rollback: Rollback = "a migrate back"
    conditional: bool = False


@dataclass(frozen=True, slots=True)
class Plan:
    """A plan's steps and refusals, the structure ``haute_store`` records once it is applied,
    whether it was made with no database (provisional), and the record it was made from."""

    steps: tuple[Step, ...]
    refusals: tuple[str, ...]
    recorded: dict[str, Any] = field(repr=False)
    provisional: bool = False
    #: What ``haute_store`` recorded, reconciled, the plan was made from; None for an empty
    #: database.
    before: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def restoring(self) -> tuple[Step, ...]:
        """The steps only a restore reverses."""
        return tuple(step for step in self.steps if step.rollback == "a restore")

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


def _quotes(count: int | None) -> str:
    return "1 quote" if count == 1 else f"{count} quotes"


def _by_id(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _kept(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "kept": True}


def _count(counts: Mapping[Key, int] | None, key: Key) -> int | None:
    return None if counts is None else counts.get(key, 0)


def wanted_counts(form: Mapping[str, Any], recorded: Mapping[str, Any]) -> set[Key]:
    """The tables and columns of *recorded* a plan to *form* counts the quotes of: those the
    form removed or kept already, and those whose type, rows or role it changed."""
    new = _by_id(form["tables"])
    wanted: set[Key] = set()
    for table in recorded["tables"]:
        after = new.get(table["id"])
        if after is None or table["kept"]:
            # A table removed, or kept already, is counted whole.
            wanted.add((table["id"], None))
            continue
        if (table["rows"], table["role"]) != (after["rows"], after["role"]):
            wanted.add((table["id"], None))
        columns = _by_id(after["columns"])
        for column in table["columns"]:
            changed = columns.get(column["id"])
            if changed is None or column["kept"] or changed["type"] != column["type"]:
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
    """The plan bringing a database whose ``haute_store`` *recorded* (None for an empty one)
    to the structure of *form*, by stable id, given the *counts* of quotes holding values in
    each table and column :func:`wanted_counts` names (None offline), on *provider*'s engine,
    which refuses a name longer than it keeps whole. Reads nothing else."""
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
        return Plan(created, (), after, provisional=counts is None)
    if (recorded.get("format"), recorded.get("store_columns")) != (
        form["format"],
        form["store_columns"],
    ):
        if mode == "dev":
            names = ", ".join(table["name"] for table in recorded.get("tables", []))
            return Plan(
                (Step("rebuild store", names, "", rollback="nothing"),),
                (),
                after,
                counts is None,
                dict(recorded),
            )
        return Plan(
            (),
            (
                "the database was made by a Haute whose quote store keeps other columns of its "
                "own, which a migrate does not change: make its store afresh",
            ),
            dict(recorded),
            counts is None,
            dict(recorded),
        )
    steps: list[Step] = []
    refusals: list[str] = []
    hosted = mode == "hosted"
    offline = counts is None
    old = _by_id(recorded["tables"])
    new = _by_id(form["tables"])
    recorded_after: list[dict[str, Any]] = []
    kept_names = {table["name"].casefold() for table in recorded["tables"] if table["kept"]}

    for table in form["tables"]:
        before = old.get(table["id"])
        if before is None:
            if hosted and table["name"].casefold() in kept_names:
                refusals.append(
                    f"the new table {table['name']} takes the name of a table the store keeps: "
                    "rename one, or drop the kept table on the SQL side first"
                )
                continue
            steps.append(Step("create table", table["name"], table["id"]))
            recorded_after.append(table)
            continue
        if (before["rows"], before["role"]) != (table["rows"], table["role"]):
            # A table of other rows or another role is made afresh under its name, when it holds
            # nothing or in dev; a hosted one holding rows keeps them as they are.
            quotes = _count(counts, (table["id"], None))
            if table["id"] == QUOTE_DETAILS_ID:
                refusals.append("quote_details takes no change of its rows or role")
                recorded_after.append(before)
            elif quotes == 0 or (offline and hosted):
                steps.append(
                    Step(
                        "reshape table",
                        table["name"],
                        table["id"],
                        was=before["name"],
                        quotes=quotes,
                        rollback="a restore",
                        conditional=offline,
                    )
                )
                recorded_after.append(table)
            elif hosted:
                refusals.append(
                    f"{table['name']} holds rows of {_quotes(quotes)}, so its rows or role do not "
                    "change: make a new table for them, or empty it first"
                )
                recorded_after.append(before)
            else:
                steps.append(
                    Step(
                        "rebuild table",
                        table["name"],
                        table["id"],
                        was=before["name"],
                        quotes=quotes,
                        rollback="nothing",
                    )
                )
                recorded_after.append(table)
            continue
        if before["kept"]:
            steps.append(Step("bring back table", table["name"], table["id"], was=before["name"]))
        elif before["name"] != table["name"]:
            steps.append(Step("rename table", table["name"], table["id"], was=before["name"]))
        columns_after = _plan_columns(table, before, counts, hosted, offline, steps, refusals)
        recorded_after.append({**table, "columns": columns_after})

    for table in recorded["tables"]:
        if table["id"] in new:
            continue
        quotes = _count(counts, (table["id"], None))
        if hosted:
            steps.append(
                Step("keep table", table["name"], table["id"], quotes=quotes, rollback="nothing")
            )
            recorded_after.append(_kept(table))
        else:
            steps.append(
                Step("drop table", table["name"], table["id"], quotes=quotes, rollback="nothing")
            )
    return Plan(
        tuple(steps),
        tuple(refusals),
        {**after, "tables": recorded_after},
        provisional=offline,
        before=dict(recorded),
    )


def _plan_columns(
    table: dict[str, Any],
    before: dict[str, Any],
    counts: Mapping[Key, int] | None,
    hosted: bool,
    offline: bool,
    steps: list[Step],
    refusals: list[str],
) -> list[dict[str, Any]]:
    """The steps of one table both structures hold, column by column; the columns its record
    holds after them."""
    old = _by_id(before["columns"])
    new = _by_id(table["columns"])
    kept_names = {
        column["name"].casefold()
        for column in before["columns"]
        if column["kept"] and column["id"] not in new
    }
    after: list[dict[str, Any]] = []
    name = table["name"]
    for column in table["columns"]:
        was = old.get(column["id"])
        key: Key = (table["id"], column["id"])
        if was is None:
            if column["name"].casefold() in kept_names and hosted:
                refusals.append(
                    f"the new column {name}.{column['name']} takes the name of a column the store "
                    "keeps: rename one, or drop the kept column on the SQL side first"
                )
                continue
            steps.append(Step("add column", name, table["id"], column["name"], column["id"]))
            after.append(column)
            continue
        if was["kept"]:
            steps.append(
                Step(
                    "bring back column",
                    name,
                    table["id"],
                    column["name"],
                    column["id"],
                    was=was["name"],
                )
            )
        elif was["name"] != column["name"]:
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
            elif quotes == 0 or (offline and hosted):
                steps.append(
                    Step(
                        "retype column",
                        name,
                        table["id"],
                        column["name"],
                        column["id"],
                        quotes=quotes,
                        rollback="a restore",
                        conditional=offline,
                    )
                )
            elif hosted:
                refusals.append(
                    f"{name}.{column['name']} holds values in {_quotes(quotes)}, so its type does "
                    "not change: add a column of the new type, or empty it first"
                )
                after.append(was)
                continue
            else:
                steps.append(
                    Step(
                        "rebuild column",
                        name,
                        table["id"],
                        column["name"],
                        column["id"],
                        quotes=quotes,
                        rollback="nothing",
                    )
                )
        after.append(column)
    for column in before["columns"]:
        if column["id"] in new:
            continue
        quotes = _count(counts, (table["id"], column["id"]))
        if hosted:
            steps.append(
                Step(
                    "keep column",
                    name,
                    table["id"],
                    column["name"],
                    column["id"],
                    quotes=quotes,
                    rollback="nothing",
                )
            )
            after.append(_kept(column))
        else:
            steps.append(
                Step(
                    "drop column",
                    name,
                    table["id"],
                    column["name"],
                    column["id"],
                    quotes=quotes,
                    rollback="nothing",
                )
            )
    return after


# ── The database's side ─────────────────────────────────────────────────


def reconcile(
    recorded: Mapping[str, Any],
    form: Mapping[str, Any],
    catalogue: Any,
    sql_schema: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """*recorded* as the database's catalogue (a SQLAlchemy ``Inspector``) holds it in the SQL
    schema *sql_schema*: a kept table or column the SQL side dropped forgotten, so its name is
    free; one the form still has that the catalogue lacks refused, naming it; what neither
    records left alone."""
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
    """The DDL of *plan* through *operations*, live or as offline SQL: drops first, then every
    rename in two phases through temporary names (tables, then columns), then what is made,
    each in the store's SQL schema."""
    before = plan.before
    old = {} if before is None else _by_id(before["tables"])
    steps = plan.steps
    owner = schema.metadata.schema
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
        operations.impl.create_table(schema.store)
        operations.impl.create_table(schema.activity)
        return
    # What is dropped goes first, under the names the database holds before any rename, so a
    # name it frees is free for what follows.
    for step in steps:
        if step.kind == "drop table" or step.kind in ("rebuild table", "reshape table"):
            operations.drop_table(old[step.table_id]["name"], schema=owner)
        elif step.kind in ("drop column", "rebuild column"):
            operations.drop_column(old[step.table_id]["name"], _was_name(old, step), schema=owner)
    renames = [
        step
        for step in steps
        if step.kind in ("rename table", "bring back table") and step.was != step.table
    ]
    for step in renames:
        operations.rename_table(str(step.was), _named(step.table_id), schema=owner)
    for step in renames:
        operations.rename_table(_named(step.table_id), step.table, schema=owner)
    columns = [
        step
        for step in steps
        if step.kind in ("rename column", "bring back column") and step.was != step.column
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
        if step.kind in ("create table", "rebuild table", "reshape table"):
            operations.impl.create_table(schema.by_id(step.table_id).table)
        elif step.kind in ("add column", "rebuild column"):
            operations.add_column(step.table, _column(schema, step), schema=owner)
        elif step.kind == "retype column":
            _retype(operations, provider, plan, schema, step)
    if before is None:
        # A database holding none of the store takes Haute's two tables with the form's.
        operations.impl.create_table(schema.store)
        operations.impl.create_table(schema.activity)


def _type_of(structure: Mapping[str, Any] | None, step: Step) -> str:
    """The type *structure* gives the column *step* concerns, by its ids."""
    for table in (structure or {}).get("tables", []):
        if table["id"] == step.table_id:
            for column in table["columns"]:
                if column["id"] == step.column_id:
                    return str(column["type"])
    raise KeyError((step.table_id, step.column_id))


def _retype(
    operations: Operations, provider: Provider, plan: Plan, schema: StoreSchema, step: Step
) -> None:
    """Change the empty column *step* names to the store's type for it: in place where
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
    environment: str,
    release: str | None,
    haute_version: str | None,
    restore_point: str | None,
    at: Any,
) -> None:
    values = {
        "release": release,
        "haute_version": haute_version,
        "migrated_at": at,
        "schema": json.dumps(plan.recorded, sort_keys=True, ensure_ascii=False),
        "restore_point": restore_point,
    }
    if fresh:
        operations.execute(
            insert(schema.store).values(id=1, project=project, environment=environment, **values)
        )
    else:
        operations.execute(update(schema.store).where(schema.store.c.id == 1).values(**values))


def _refuse(plan: Plan, restore_point: str | None, mode: Mode) -> None:
    if plan.refusals:
        raise MigrationRefusedError(
            "The migrate was refused, with nothing changed: " + "; ".join(plan.refusals) + ".",
            refusals=plan.refusals,
        )
    if mode == "hosted" and plan.restoring and not restore_point:
        steps = [
            f"{step.kind} {step.table}{'.' + step.column if step.column else ''}"
            for step in plan.restoring
        ]
        raise MigrationRefusedError(
            "The plan holds steps only a restore reverses ("
            + "; ".join(steps)
            + "): give the backup or point in time the database can be returned to as "
            "--restore-point NAME.",
            refusals=steps,
        )


def plan_store(
    url: str,
    form: FormSpec,
    *,
    project: str,
    environment: str = DEV,
    sql_schema: str | None = None,
    sign_in: SignIn | None = None,
) -> Plan:
    """The plan bringing the database at *url*, signed in as *sign_in* says, to *form* for
    *project* in *environment*, its tables in the SQL schema *sql_schema*, with nothing
    applied."""
    schema = store_schema(form, sql_schema=sql_schema)
    engine = make_engine(url, sign_in=sign_in)
    try:
        with reaching(url, environment):
            with engine.connect() as connection:
                return _planned(connection, schema, form, project, environment)
    finally:
        engine.dispose()


def _planned(
    connection: Connection, schema: StoreSchema, form: FormSpec, project: str, environment: str
) -> Plan:
    """The plan for the database on *connection*."""
    owner = schema.metadata.schema
    provider = dialect_provider(connection.dialect.name)
    catalogue = inspect(connection)
    names = set(catalogue.get_table_names(schema=owner))
    _refuse_old_store(names)
    wanted = structure(form)
    held = _recorded(connection, schema) if STORE_TABLE in names else None
    mode: Mode = "dev" if environment == DEV else "hosted"
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
        return plan(wanted, None, {}, mode, provider=provider)
    _refuse_other(held, project, environment)
    recorded = json.loads(held.structure)
    if (recorded.get("format"), recorded.get("store_columns")) != (
        wanted["format"],
        wanted["store_columns"],
    ):
        return plan(wanted, recorded, {}, mode, provider=provider)
    reconciled, refusals = reconcile(recorded, wanted, catalogue, owner)
    found = counts(connection, reconciled, wanted_counts(wanted, reconciled), owner)
    made = plan(wanted, reconciled, found, mode, provider=provider)
    if refusals:
        made = Plan(made.steps, (*refusals, *made.refusals), made.recorded, before=made.before)
    return made


def prepare_store(
    url: str,
    form: FormSpec,
    *,
    project: str,
    environment: str = DEV,
    haute_version: str | None = None,
    release: str | None = None,
    restore_point: str | None = None,
    clock: Callable[[], datetime] | None = None,
    sql_schema: str | None = None,
    sign_in: SignIn | None = None,
) -> Plan:
    """Make the database at *url*, signed in as *sign_in* says, hold *form*'s tables for
    *project* in *environment* in the SQL schema *sql_schema*, and answer the plan applied:
    dev's mode on the analyst's laptop, a hosted environment's elsewhere, in one transaction.
    Refused (:class:`MigrationRefusedError`), with nothing changed, when the plan refuses a
    step or, hosted, holds one only a restore reverses and is given no *restore_point*. A
    database of an earlier Haute's store, of another project's or environment's, or holding
    tables of the form's names the store did not make, is refused, nothing adopted."""
    schema = store_schema(form, sql_schema=sql_schema)
    now = (clock or (lambda: datetime.now(UTC)))()
    mode: Mode = "dev" if environment == DEV else "hosted"
    engine = make_engine(url, sign_in=sign_in)
    try:
        with reaching(url, environment):
            with writing(engine) as connection:
                made = _planned(connection, schema, form, project, environment)
                _refuse(made, restore_point, mode)
                operations = Operations(MigrationContext.configure(connection))
                _emit(operations, made, schema)
                rebuilt = any(step.kind == "rebuild store" for step in made.steps)
                _record(
                    operations,
                    schema,
                    made,
                    fresh=made.before is None or rebuilt,
                    project=project,
                    environment=environment,
                    release=release,
                    haute_version=haute_version,
                    restore_point=restore_point if mode == "hosted" else None,
                    at=now,
                )
                return made
    finally:
        engine.dispose()


def offline_plan(base: FormSpec, form: FormSpec, *, provider: Provider | None = None) -> Plan:
    """The plan from *base*'s tables to *form*'s with no database, as a hosted migrate makes
    it on *provider*'s engine: provisional, each step that depends on the data conditional."""
    return plan(structure(form), structure(base), None, "hosted", provider=provider)


# ── Saying a plan ───────────────────────────────────────────────────────


def _what(step: Step) -> str:
    where = step.table if step.column is None else f"{step.table}.{step.column}"
    if step.kind in (
        "rename table",
        "rename column",
        "bring back table",
        "bring back column",
    ) and step.was not in (None, step.column, step.table):
        where = f"{where} (was {step.was})"
    held = "" if step.quotes is None else f", {_quotes(step.quotes)} holding values"
    made = " if it holds no value, else refused" if step.conditional else ""
    return f"{step.kind} {where}{held}{made}; reversed by {step.rollback}"


def render_text(plan: Plan) -> str:
    """*plan* as ``haute app migrate --plan`` prints it: each step with what reverses it, each
    kept object with its DROP for the DBA, and each refusal."""
    lines: list[str] = []
    if plan.provisional:
        lines.append(
            "Provisional plan, made with no database: each step marked so is made only if what it "
            "changes holds no value."
        )
    for step in plan.steps:
        lines.append(f"  - {_what(step)}")
        if step.kind == "keep table":
            lines.append(f"      to drop it on the SQL side: DROP TABLE {step.table};")
        if step.kind == "keep column":
            lines.append(
                f"      to drop it on the SQL side: ALTER TABLE {step.table} DROP COLUMN "
                f"{step.column};"
            )
    if not plan.steps:
        lines.append("  Nothing to change.")
    for refusal in plan.refusals:
        lines.append(f"  ! refused: {refusal}")
    if plan.restoring:
        lines.append(
            "  A restore is the only way back from the steps reversed by a restore: the migrate "
            "needs --restore-point NAME."
        )
    return "\n".join(lines)


def render_sql(
    plan: Plan,
    form: FormSpec,
    dialect: str,
    *,
    project: str,
    environment: str,
    release: str | None,
    haute_version: str | None,
    restore_point: str | None,
    sql_schema: str | None = None,
) -> str:
    """*plan* as the SQL a DBA runs in its place, Alembic's offline rendering for *dialect*,
    each name in the SQL schema *sql_schema* when one is named, ``haute_store``'s row last, a
    restore point noted where the plan needs one."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name=dialect, opts={"as_sql": True, "output_buffer": buffer}
    )
    operations = Operations(context)
    schema = store_schema(form, sql_schema=sql_schema)
    if plan.restoring:
        buffer.write(
            "-- Steps only a restore reverses: take the restore point "
            f"{restore_point or '<NAME>'} before running this.\n\n"
        )
    _emit(operations, plan, schema)
    _record(
        operations,
        schema,
        plan,
        fresh=plan.before is None,
        project=project,
        environment=environment,
        release=release,
        haute_version=haute_version,
        restore_point=restore_point,
        at=literal(datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")),
    )
    return buffer.getvalue()
