"""The quote store's database (specs/workbench): its engine, and its tables made ready for a
form and checked against it.

A database is the form's own tables and Haute's two (:mod:`._schema`). :func:`prepare_store`
makes it hold a form's tables: on a database holding none it builds them; in dev, the
analyst's laptop, where schema changes are tried, it builds again every table the form has
changed, naming each one whose rows it dropped; elsewhere it refuses a database holding
another form's tables, which :func:`haute app migrate`'s planner changes. :func:`open_engine`
opens a database that holds exactly the form's tables, as ``haute_store`` records them, for
this project and environment, and refuses any other, naming why. Nothing it does adopts a
table it did not make or touches a table not its own.

On SQLite the store takes SQLAlchemy's recipe for the pysqlite driver: the driver leaves
transactions to SQLAlchemy, which begins each with ``BEGIN``, and a write with ``BEGIN
IMMEDIATE``, so DDL is transactional and writers take turns. Foreign keys are on, the journal
is the rollback journal, never write-ahead logging, and a SQLite older than 3.35, which
cannot rename or drop a column, is refused.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Connection,
    Engine,
    MetaData,
    Table,
    create_engine,
    event,
    insert,
    inspect,
    literal,
    select,
    update,
)
from sqlalchemy.schema import DropTable

from haute._quote_store._schema import STORE_TABLE, StoreSchema, store_schema, structure_text
from haute._workbench_config import WorkbenchError
from haute._workbench_form import FormSpec

#: The table an earlier Haute's quote store kept its migrations' version in, which a database
#: of that store holds: it is refused, never read.
OLD_STORE_TABLE = "haute_quote_store_version"
#: The environment ``haute app serve`` keeps its quotes in, on the analyst's laptop.
DEV = "dev"
#: The environment a hosted app keeps its quotes in, which ``haute app migrate`` makes.
HOSTED = "hosted"
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
        raise QuoteStoreMismatchError(
            f"This database holds the quotes of {recorded.project!r} in "
            f"{recorded.environment!r}, not of {project!r} in {environment!r}: point the app "
            "at its own database."
        )


def _differences(recorded: str, wanted: str) -> list[str]:
    """Each table whose structure differs between the *recorded* and the *wanted* schema,
    by name, or the store's own columns when Haute's changed."""
    old = json.loads(recorded)
    new = json.loads(wanted)
    if old.get("format") != new.get("format") or old.get("store_columns") != new.get(
        "store_columns"
    ):
        return ["the quote store's own columns"]
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


def prepare_store(
    url: str,
    form: FormSpec,
    *,
    project: str,
    environment: str = DEV,
    haute_version: str | None = None,
    release: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> list[str]:
    """Make the database at *url* hold *form*'s tables for *project* in *environment*;
    answer the names of the tables whose rows it dropped, in dev alone.

    A database holding none of the store's tables takes them all. In dev the tables the form
    has changed, or no longer has, are built again, and when ``quote_details`` or the
    store's own columns changed, every table is. In any other environment a database holding
    another form's tables is refused, naming them: changing them is the planner's. A
    database of an earlier Haute's store, another project's or environment's, and one holding
    a table of the form's name the store did not make, are refused.
    """
    schema = store_schema(form)
    wanted = structure_text(form)
    now = (clock or (lambda: datetime.now(UTC)))()
    engine = make_engine(url)
    try:
        with writing(engine) as connection:
            names = set(inspect(connection).get_table_names())
            _refuse_old_store(names)
            recorded = _recorded(connection, schema) if STORE_TABLE in names else None
            if recorded is None:
                taken = sorted(names & {stored.table.name for stored in schema.tables})
                if taken:
                    raise QuoteStoreMismatchError(
                        "This database holds tables of the form's names that the quote store "
                        f"did not make ({', '.join(taken)}): the store adopts none; point it "
                        "at another database or schema.",
                        differences=taken,
                    )
                schema.metadata.create_all(connection)
                connection.execute(
                    insert(schema.store).values(
                        id=1,
                        project=project,
                        environment=environment,
                        release=release,
                        haute_version=haute_version,
                        migrated_at=now,
                        schema=wanted,
                    )
                )
                return []
            _refuse_other(recorded, project, environment)
            if recorded.structure == wanted:
                return []
            differences = _differences(recorded.structure, wanted)
            if environment != DEV:
                raise QuoteStoreMismatchError(
                    "This database holds another form's tables, which the quote store does "
                    "not change outside dev: " + "; ".join(differences) + ".",
                    differences=differences,
                )
            dropped = _rebuild(connection, schema, recorded.structure, wanted)
            connection.execute(
                update(schema.store)
                .where(schema.store.c.id == 1)
                .values(
                    release=release, haute_version=haute_version, migrated_at=now, schema=wanted
                )
            )
            return dropped
    finally:
        engine.dispose()


def _rebuild(connection: Connection, schema: StoreSchema, recorded: str, wanted: str) -> list[str]:
    """Dev's rebuild: drop the tables the form changed or no longer has, every table when
    ``quote_details`` or the store's own columns changed, and make the form's afresh; the
    names of those whose rows went."""
    old = {table["id"]: table for table in json.loads(recorded)["tables"]}
    new = {table["id"]: table for table in json.loads(wanted)["tables"]}
    everything = json.loads(recorded).get("store_columns") != json.loads(wanted).get(
        "store_columns"
    ) or old.get("quote_details") != new.get("quote_details")
    names = set(inspect(connection).get_table_names())
    gone = [table["name"] for key, table in old.items() if everything or new.get(key) != table]
    dropped: list[str] = []
    # Children first: every table but quote_details refers to it, and so does the activity.
    order = [name for name in gone if name != "quote_details"]
    if everything:
        order.append(schema.activity.name)
        order.append("quote_details")
    for name in order:
        if name not in names:
            continue
        held = Table(name, MetaData())
        if connection.execute(select(literal(1)).select_from(held).limit(1)).first() is not None:
            dropped.append(name)
        connection.execute(DropTable(held))
    schema.metadata.create_all(connection)
    return [name for name in dropped if name != schema.activity.name]


def open_engine(
    url: str, form: FormSpec, *, project: str, environment: str = DEV
) -> tuple[Engine, StoreSchema]:
    """An engine on the database at *url* with *form*'s store schema, once the database holds
    exactly the form's tables for *project* in *environment*; refused otherwise, naming what
    differs and what resolves it."""
    schema = store_schema(form)
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            names = set(inspect(connection).get_table_names())
            _refuse_old_store(names)
            recorded = _recorded(connection, schema) if STORE_TABLE in names else None
            if recorded is None:
                raise QuoteStoreMismatchError(
                    "This database holds no quote store yet: run haute app migrate to make its "
                    "tables."
                )
            _refuse_other(recorded, project, environment)
            wanted = structure_text(form)
            if recorded.structure != wanted:
                differences = _differences(recorded.structure, wanted)
                raise QuoteStoreMismatchError(
                    "This database holds another form's tables: "
                    + "; ".join(differences)
                    + ". Run haute app migrate to bring them to this release's form.",
                    differences=differences,
                )
    except Exception:
        engine.dispose()
        raise
    return engine, schema
