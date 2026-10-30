"""The quote store's database (specs/workbench): its engine, its errors, and its tables
checked against a form.

A database is the form's own tables and Haute's two (:mod:`._schema`), made and changed by the
planner (:mod:`._migrate`). :func:`open_engine` opens a database that holds exactly the form's
tables, as ``haute_store`` records them, what it keeps aside, and as its own catalogue holds
them, for this project and environment, and refuses any other, naming why. Nothing it does
adopts a table it did not make or touches a table not its own.

On SQLite the store takes SQLAlchemy's recipe for the pysqlite driver: the driver leaves
transactions to SQLAlchemy, which begins each with ``BEGIN``, and a write with ``BEGIN
IMMEDIATE``, so DDL is transactional and writers take turns. Foreign keys are on, the journal
is the rollback journal, never write-ahead logging, and a SQLite older than 3.35, which
cannot rename or drop a column, is refused.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Connection,
    Date,
    DateTime,
    Engine,
    Float,
    Integer,
    String,
    create_engine,
    event,
    inspect,
    select,
)
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.types import TypeDecorator, TypeEngine

from haute._quote_store._schema import STORE_TABLE, StoreSchema, live, store_schema, structure
from haute._workbench_config import DEV_ENVIRONMENT, WorkbenchError
from haute._workbench_form import QUOTE_DETAILS_ID, FormSpec

#: The table an earlier Haute's quote store kept its migrations' version in, which a database
#: of that store holds: it is refused, never read.
OLD_STORE_TABLE = "haute_quote_store_version"
#: The environment ``haute app serve`` keeps its quotes in, on the analyst's laptop.
DEV = DEV_ENVIRONMENT
#: The oldest SQLite that can rename and drop a column.
SQLITE_MINIMUM = (3, 35, 0)
#: The execution option that begins a transaction as a write: ``BEGIN IMMEDIATE`` on SQLite.
_WRITE = "haute_write"


class QuoteStoreError(WorkbenchError):
    """The quote store cannot do what was asked: the message says why."""

    error_code = "quote_store_invalid"


class QuoteNotFoundError(QuoteStoreError):
    """The store holds no such quote."""

    error_code = "quote_not_found"


class StaleQuoteError(QuoteStoreError):
    """A change named a version of the quote it has moved on from; nothing was written."""

    error_code = "stale_quote"


class QuoteLockedError(QuoteStoreError):
    """The quote does not take this change as it stands, final or archived: the message says
    what would let it."""

    error_code = "quote_locked"


class QuoteNotReadyError(QuoteStoreError):
    """A quote cannot be marked final yet: each required field without a value is named in
    *missing*, or the message names the release its price is not."""

    error_code = "quote_not_ready"

    def __init__(self, message: str, *, missing: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.missing = list(missing)


class WriteSupersededError(QuoteStoreError):
    """A write repeated under its operation key landed, and the quote has been changed since:
    the write is done, and the quote is as *changed_by* left it at *changed_at*."""

    error_code = "write_superseded"

    def __init__(
        self, message: str, *, landed_at: datetime, changed_by: str, changed_at: datetime
    ) -> None:
        super().__init__(message)
        self.landed_at = landed_at
        self.changed_by = changed_by
        self.changed_at = changed_at


class ReferenceTakenError(QuoteStoreError):
    """A new quote's reference is one another quote holds; *label* names that quote."""

    error_code = "reference_taken"

    def __init__(self, message: str, *, reference: str, option: int, label: str) -> None:
        super().__init__(message)
        self.reference = reference
        self.option = option
        self.label = label


@dataclass(frozen=True, slots=True)
class MisfitCell:
    """A cell holding a value its column's type does not: its table, its row on the sheet
    (None in a one-row table) and its column, by their labels."""

    table: str
    row: int | None
    column: str

    def __str__(self) -> str:
        where = self.table if self.row is None else f"{self.table} row {self.row}"
        return f"{where}, {self.column}"


class QuoteValuesError(QuoteStoreError):
    """A save holding values their columns' types do not, named in *cells*; nothing was
    written."""

    error_code = "quote_values_invalid"

    def __init__(self, message: str, *, cells: Sequence[MisfitCell]) -> None:
        super().__init__(message)
        self.cells = list(cells)


class QuoteStoreEnvironmentError(QuoteStoreError):
    """The store is not the environment's: the environment named or where its database is
    cannot be resolved, or the database holds another project's or environment's quotes. The
    message says which variable to set, and names no server, database or URL."""

    error_code = "quote_store_environment"


class QuoteStoreMismatchError(QuoteStoreError):
    """The database does not hold the form's tables as the store needs them: *differences*
    names each, and the message what resolves them."""

    error_code = "quote_store_mismatch"

    def __init__(self, message: str, *, differences: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.differences = list(differences)


def store_url(path: Path) -> str:
    """The SQLAlchemy URL of a SQLite store at *path*."""
    return f"sqlite:///{path.resolve().as_posix()}"


def make_engine(url: str) -> Engine:
    """An engine for the store at *url*, on SQLite with the store's transactions."""
    engine = create_engine(url)
    if engine.dialect.name != "sqlite":
        return engine
    if sqlite3.sqlite_version_info < SQLITE_MINIMUM:
        engine.dispose()
        raise QuoteStoreError(
            f"The quote store needs SQLite {'.'.join(map(str, SQLITE_MINIMUM))} or later, "
            f"which can rename and drop a column; this Python has {sqlite3.sqlite_version}."
        )

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection: Any, _record: Any) -> None:
        # SQLAlchemy begins every transaction itself, so DDL is transactional too.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            try:
                mode = cursor.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            except sqlite3.OperationalError as exc:
                mode = str(exc)
        finally:
            cursor.close()
        if str(mode).lower() != "delete":
            raise QuoteStoreError(
                f"The quote store's database is in {mode!r} mode and could not be put back to a "
                "rollback journal: close whatever else has it open and try again."
            )

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        writes = connection.get_execution_options().get(_WRITE, False)
        connection.exec_driver_sql("BEGIN IMMEDIATE" if writes else "BEGIN")

    return engine


@contextmanager
def writing(engine: Engine) -> Iterator[Connection]:
    """A write transaction on *engine*: committed when the block ends, rolled back when it
    raises. On SQLite it holds the database's write lock from its start."""
    with engine.execution_options(**{_WRITE: True}).begin() as connection:
        yield connection


@dataclass(frozen=True, slots=True)
class Recorded:
    """What a database's ``haute_store`` row says it holds."""

    project: str
    environment: str
    structure: str


def _recorded(connection: Connection, schema: StoreSchema) -> Recorded | None:
    row = connection.execute(select(schema.store).where(schema.store.c.id == 1)).one_or_none()
    if row is None:
        return None
    return Recorded(project=row.project, environment=row.environment, structure=row.schema)


def _refuse_old_store(names: set[str]) -> None:
    if OLD_STORE_TABLE in names:
        held = sorted(name for name in names if name.startswith("haute_quote"))
        raise QuoteStoreMismatchError(
            "This database holds the quote store an earlier Haute kept ("
            + ", ".join(held)
            + "), which this Haute does not read: delete those tables, or the file, and the "
            "store is made afresh."
        )


def _refuse_other(recorded: Recorded, project: str, environment: str) -> None:
    if recorded.project != project or recorded.environment != environment:
        raise QuoteStoreEnvironmentError(
            f"This database holds the quotes of {recorded.project!r} in "
            f"{recorded.environment!r}, not of {project!r} in {environment!r}: point the app "
            "at its own database."
        )


def _differences(recorded: dict[str, Any], wanted: dict[str, Any]) -> list[str]:
    """Each table whose structure differs between the *recorded* schema, what it keeps aside,
    and the *wanted* one, by name, or the store's own columns when Haute's changed."""
    old = recorded
    new = wanted
    if old.get("format") != new.get("format") or old.get("store_columns") != new.get(
        "store_columns"
    ):
        return ["the quote store's own columns"]
    old = live(old)
    before = {table["id"]: table for table in old["tables"]}
    after = {table["id"]: table for table in new["tables"]}
    differences = [
        f"{table['name']} is no longer the form's"
        for key, table in before.items()
        if key not in after
    ]
    differences.extend(
        f"{table['name']} is new" if key not in before else f"{table['name']} has changed"
        for key, table in after.items()
        if before.get(key) != table
    )
    return differences


#: The kinds a column's SQL type is told apart by, the first one it is wins: what the store
#: reads and writes it as, whatever each engine names it.
_KINDS: tuple[type[TypeEngine[Any]], ...] = (Boolean, Integer, Float, DateTime, Date, String)


def _kind(sql_type: TypeEngine[Any]) -> str:
    """The kind of *sql_type*, a type decorator's its implementation's."""
    if isinstance(sql_type, TypeDecorator):
        sql_type = sql_type.impl_instance
    for kind in _KINDS:
        if isinstance(sql_type, kind):
            return kind.__name__.lower()
    return type(sql_type).__name__.lower()


def _resolve(environment: str) -> str:
    """What brings the database to the release's tables in *environment*."""
    if environment == DEV:
        return "Start haute app serve again, which builds dev's tables for the form."
    return f"Run haute app migrate --environment {environment} to bring it to this release's form."


def _catalogue_differences(catalogue: Inspector, schema: StoreSchema) -> list[str]:
    """Each way the database's own catalogue differs from the tables *schema* derives, which
    every write and read of the store takes as they are: a table or a column missing, of
    another kind, taking a null where the store needs a value or none where it writes one,
    keyed otherwise or not referring to its quote; and a column of those tables the store does
    not write that takes no null, so no row could be inserted."""
    present = set(catalogue.get_table_names())
    details = schema.details.table.name
    differences: list[str] = []
    tables = [(stored.table, stored.form.id != QUOTE_DETAILS_ID) for stored in schema.tables]
    tables.extend([(schema.store, False), (schema.activity, False)])
    for table, refers in tables:
        if table.name not in present:
            differences.append(f"the table {table.name} is missing")
            continue
        reflected = {column["name"]: column for column in catalogue.get_columns(table.name)}
        for column in table.columns:
            found = reflected.pop(column.name, None)
            if found is None:
                differences.append(f"{table.name}.{column.name} is missing")
                continue
            if _kind(found["type"]) != _kind(column.type):
                differences.append(
                    f"{table.name}.{column.name} holds {_kind(found['type'])}, not "
                    f"{_kind(column.type)}"
                )
            if bool(found["nullable"]) != bool(column.nullable):
                takes = "takes a null" if found["nullable"] else "takes no null"
                differences.append(f"{table.name}.{column.name} {takes}, unlike the store's")
        for name, extra in reflected.items():
            if not extra["nullable"]:
                differences.append(
                    f"{table.name}.{name} is no column of the release's and takes no null, so "
                    "no row of it could be saved"
                )
        keyed = list(catalogue.get_pk_constraint(table.name).get("constrained_columns") or [])
        wanted = [column.name for column in table.primary_key.columns]
        if keyed != wanted:
            differences.append(
                f"{table.name} is keyed by {', '.join(keyed) or 'nothing'}, not {', '.join(wanted)}"
            )
        if refers and not any(
            foreign.get("referred_table") == details
            and list(foreign.get("constrained_columns") or [])
            == ["quote_reference", "quote_option"]
            for foreign in catalogue.get_foreign_keys(table.name)
        ):
            differences.append(f"{table.name} does not refer to its quote's {details} row")
    return differences


def open_engine(
    url: str, form: FormSpec, *, project: str, environment: str = DEV
) -> tuple[Engine, StoreSchema]:
    """An engine on the database at *url* with *form*'s store schema, once the database holds
    exactly the form's tables for *project* in *environment*, as ``haute_store`` records them
    and as its own catalogue holds them; refused otherwise, naming each difference and what
    resolves it."""
    schema = store_schema(form)
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            catalogue = inspect(connection)
            names = set(catalogue.get_table_names())
            _refuse_old_store(names)
            recorded = _recorded(connection, schema) if STORE_TABLE in names else None
            if recorded is None:
                raise QuoteStoreMismatchError(
                    f"This database holds no quote store yet. {_resolve(environment)}"
                )
            _refuse_other(recorded, project, environment)
            wanted = structure(form)
            held = json.loads(recorded.structure)
            if held.get("format") != wanted["format"] or live(held) != wanted:
                differences = _differences(held, wanted)
                raise QuoteStoreMismatchError(
                    "This database holds another form's tables: "
                    + "; ".join(differences)
                    + f". {_resolve(environment)}",
                    differences=differences,
                )
            differences = _catalogue_differences(catalogue, schema)
            if differences:
                raise QuoteStoreMismatchError(
                    "This database's tables are not as the store made them: "
                    + "; ".join(differences)
                    + f". {_resolve(environment)}",
                    differences=differences,
                )
    except Exception:
        engine.dispose()
        raise
    return engine, schema
