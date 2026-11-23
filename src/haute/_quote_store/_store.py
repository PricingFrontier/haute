"""The quote store's database (specs/workbench): its engine, its errors, and its tables
checked against a form.

A database is the form's own tables and Haute's activity (:mod:`._schema`), made and changed
in SQL by the people who own it, starting from the SQL Haute writes (:mod:`._migrate`), in
every environment alike. :func:`open_engine` opens a database and names each way its own
catalogue does not hold the form's tables as the store reads and writes them
(:class:`StoreMisfit`), which the book refuses the uses that need them for, and refuses a
database it cannot reach. Nothing it does makes, adopts or changes a table.

On SQLite the store takes SQLAlchemy's recipe for the pysqlite driver: the driver leaves
transactions to SQLAlchemy, which begins each with ``BEGIN``, and a write with ``BEGIN
IMMEDIATE``, so DDL is transactional and writers take turns. Foreign keys are on, the journal
is the rollback journal, never write-ahead logging, and a SQLite older than 3.35, which
cannot rename or drop a column, is refused.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import Connection, Dialect, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from haute._logging import get_logger
from haute._quote_store._catalogue import (
    Catalogue,
    CatalogueMovedError,
    column_problems,
    read_catalogue,
    table_problems,
)
from haute._quote_store._providers import SignIn, make_server_engine, missing_privileges
from haute._quote_store._schema import StoreSchema, store_schema, with_kept_columns
from haute._workbench_config import (
    STORE_DATABASE_ENV,
    STORE_SERVER_ENV,
    STORE_URL_ENV,
    WorkbenchError,
)
from haute._workbench_form import FormSpec

#: What places a hosted app's database and signs it in, as a refusal names it.
HOST_VARIABLES = (
    f"where {STORE_SERVER_ENV} and {STORE_DATABASE_ENV}, or {STORE_URL_ENV}, say it is and the "
    "variables its sign-in reads"
)
#: The oldest SQLite that can rename and drop a column.
SQLITE_MINIMUM = (3, 35, 0)
#: The execution option that begins a transaction as a write: ``BEGIN IMMEDIATE`` on SQLite.
_WRITE = "haute_write"
#: How long a read the database ends as one to run again waits before each run again, in
#: seconds: three more at most, half a second the longest.
RERUN_WAITS = (0.05, 0.2, 0.5)

logger = get_logger(component="quote_store")
_Read = TypeVar("_Read")


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
    """The environment named, or where its database is or how to sign in to it, cannot be
    resolved. The message says which variable, file or key to set, and names no server,
    database or URL."""

    error_code = "quote_store_environment"


class QuoteStoreUnreachableError(QuoteStoreEnvironmentError):
    """A server database the store cannot reach, or one that refuses its sign-in: the message
    names what places it and signs in to it, never what the driver said."""

    error_code = "quote_store_unreachable"


class QuoteStoreChangedError(QuoteStoreError):
    """The database's tables are no longer as the store found them when it opened: a column of
    an input table added, renamed or dropped since, which the store must know of before it
    reads a quote's rows or writes them again. Raised before anything is written, for the
    store to be opened again and the work done once more."""

    error_code = "quote_store_changed"


class QuoteStoreReadOnlyError(QuoteStoreError):
    """A SQLite store the app cannot write to: its file, or the folder its rollback journal is
    made in, read-only. The message names the file."""

    error_code = "quote_store_read_only"


@dataclass(frozen=True)
class StoreMisfit:
    """One way the database does not fit the store: its *table*, its *column* when it is one,
    what is wrong (*problem*, the words that follow its name), the *privilege* the sign-in
    lacks on the table when that is what is wrong, and the whole as one *text*
    (``policy_details.is_renewal is missing``, ``SELECT on policy``)."""

    table: str
    column: str | None
    problem: str
    text: str
    privilege: str | None = None


class QuoteStoreMismatchError(QuoteStoreError):
    """The database does not hold the form's tables as the store needs them: *misfits* names
    each, ``differences`` their texts, *environment* whose database it is, and the message what
    resolves them."""

    error_code = "quote_store_mismatch"

    def __init__(
        self, message: str, *, misfits: Sequence[StoreMisfit] = (), environment: str | None = None
    ) -> None:
        super().__init__(message)
        self.misfits = tuple(misfits)
        self.differences = [misfit.text for misfit in self.misfits]
        self.environment = environment


def store_mismatch(misfits: Sequence[StoreMisfit], environment: str) -> QuoteStoreMismatchError:
    """The refusal of *environment*'s database for *misfits*: each table or column that does not
    fit and what resolves them, then each privilege its sign-in lacks."""
    tables = [misfit.text for misfit in misfits if misfit.privilege is None]
    lacking = [misfit.text for misfit in misfits if misfit.privilege is not None]
    said: list[str] = []
    if tables:
        said.append(
            "This database's tables do not fit the form: "
            + "; ".join(tables)
            + f". {_resolve(environment)}"
        )
    if lacking:
        said.append(
            "The identity the app signs in as lacks what the quote store needs: "
            + "; ".join(lacking)
            + ". Grant them to it."
        )
    return QuoteStoreMismatchError(" ".join(said), misfits=misfits, environment=environment)


def store_url(path: Path) -> str:
    """The SQLAlchemy URL of a SQLite store at *path*."""
    return f"sqlite:///{path.resolve().as_posix()}"


def make_engine(url: str, *, sign_in: SignIn | None = None) -> Engine:
    """An engine for the store at *url*: on SQLite with the store's transactions, on a server
    database signed in as *sign_in* says (:func:`._providers.make_server_engine`)."""
    if make_url(url).get_backend_name() != "sqlite":
        return make_server_engine(url, sign_in)
    engine = create_engine(url)
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


def _entra_refused(exc: BaseException) -> bool:
    """Whether *exc* is Microsoft Entra ID refusing a sign-in's token, as a connection asks for
    it: a credential that cannot sign in or a browser sign-in left or cancelled, whose text can
    name the tenant and the account. azure-identity is loaded only where a sign-in uses it."""
    try:
        from azure.core.exceptions import ClientAuthenticationError
    except ImportError:  # pragma: no cover - the server extras install it
        return False
    return isinstance(exc, ClientAuthenticationError)


def unreached(exc: BaseException) -> bool:
    """Whether *exc* is a server database's refusal of a connection, or one that never came:
    an error the driver raised before any statement, whose text names where the database is
    and who signs in to it, or Entra ID refusing the sign-in's token as the connection is
    made; or a database that stopped answering: a statement ended at its deadline
    (:func:`past_deadline`), or every connection taken past the pool's."""
    return (
        (isinstance(exc, DBAPIError) and exc.statement is None)
        or _entra_refused(exc)
        or past_deadline(exc)
        or isinstance(exc, PoolTimeoutError)
    )


def past_deadline(exc: BaseException) -> bool:
    """Whether *exc* is a statement the database or its driver ended at its deadline
    (:data:`._providers.STATEMENT_TIMEOUT_SECONDS`): PostgreSQL's cancelled statement,
    SQLSTATE 57014, or SQL Server's driver's query timeout, which mssql-python names alone."""
    if not isinstance(exc, DBAPIError) or exc.statement is None:
        return False
    original = exc.orig
    return getattr(original, "sqlstate", None) == "57014" or (
        getattr(original, "driver_error", None) == "Timeout expired"
    )


def rerun_asked(exc: BaseException) -> bool:
    """Whether *exc* is the database ending a statement's transaction as one it asks to be run
    again: a deadlock's victim or a serialization failure, SQLSTATE 40001, or PostgreSQL's
    deadlock, 40P01. SQL Server makes a read of its catalogue a deadlock's victim beside DDL
    its owners run. psycopg keeps the SQLSTATE; mssql-python keeps its name for it alone."""
    if not isinstance(exc, DBAPIError) or exc.statement is None:
        return False
    original = exc.orig
    return getattr(original, "sqlstate", None) in ("40001", "40P01") or (
        getattr(original, "driver_error", None) == "Serialization failure"
    )


def read_store(engine: Engine, read: Callable[[Connection], _Read]) -> _Read:
    """*read*, which writes nothing, on a connection of *engine*: run again on a fresh one
    after each of :data:`RERUN_WAITS` while the database ends it as one to run again
    (:func:`rerun_asked`) or the catalogue moves under it (:class:`CatalogueMovedError`), the
    last run's error raised, so DDL run beside a reading app is neither a failed request nor
    a refusal; any other error raised at once."""
    waits = list(RERUN_WAITS)
    while True:
        try:
            with engine.connect() as connection:
                return read(connection)
        except Exception as exc:
            if not waits or not (isinstance(exc, CatalogueMovedError) or rerun_asked(exc)):
                raise
            logger.info("quote_store_read_rerun", runs_left=len(waits))
        time.sleep(waits.pop(0))


@contextmanager
def reaching(url: str, environment: str, named_by: str = HOST_VARIABLES) -> Iterator[None]:
    """A block that connects to the server database at *url*: a connection refused or never
    made said as a :class:`QuoteStoreUnreachableError` naming *named_by*, what places the
    database and signs in to it, never what the driver said of where it is or who signs in."""
    try:
        yield
    except Exception as exc:
        if not unreached(exc) or make_url(url).get_backend_name() == "sqlite":
            raise
        raise QuoteStoreUnreachableError(
            f"The {environment} quote store's database could not be reached, or it refused the "
            f"sign-in: check {named_by}."
        ) from None


def _resolve(environment: str) -> str:
    """What brings the database to the release's tables in *environment*."""
    return (
        f"Change the {environment} database's tables in SQL to fit this release's form: the "
        "Workbench view's schema pane writes the SQL to start from."
    )


def _column_misfit(table: str, column: str, problem: str) -> StoreMisfit:
    return StoreMisfit(table, column, problem, f"{table}.{column} {problem}")


def _catalogue_misfits(
    catalogue: Catalogue, schema: StoreSchema, dialect: Dialect
) -> list[StoreMisfit]:
    """Each way the database's own *catalogue* differs from the tables *schema* derives, the
    tables the SQL Haute writes makes, which every write and read of the store takes as they
    are: a table or a column missing, of another kind or holding less, taking a null where
    the store needs a value or none where it writes one, not numbered where the store's is,
    a text the store compares exactly under another collation on SQL Server
    (:func:`._catalogue.column_problems`); each constraint of the store's table not held
    (:func:`._catalogue.table_problems`); and a column of those tables the store does not
    write that takes no null, so no row could be inserted."""
    misfits: list[StoreMisfit] = []
    for table in [*(stored.table for stored in schema.tables), schema.activity]:
        found = catalogue.table(table.name)
        if found is None:
            misfits.append(
                StoreMisfit(table.name, None, "is missing", f"the table {table.name} is missing")
            )
            continue
        for column in table.columns:
            held = found.column(column.name)
            if held is None:
                misfits.append(_column_misfit(table.name, column.name, "is missing"))
                continue
            misfits.extend(
                _column_misfit(table.name, column.name, said)
                for _, said in column_problems(column, held, dialect)
            )
        for extra in found.columns:
            if extra.name not in table.columns and not extra.nullable:
                misfits.append(
                    _column_misfit(
                        table.name,
                        extra.name,
                        "is no column of the release's and takes no null, so no row of it "
                        "could be saved",
                    )
                )
        misfits.extend(
            StoreMisfit(table.name, None, said, f"{table.name} {said}")
            for _, said in table_problems(table, found)
        )
    return misfits


def _refuse_unwritable(engine: Engine) -> None:
    """Refuse (:class:`QuoteStoreReadOnlyError`) a SQLite store the app could read and not
    write to: the database's header written as it stands and rolled back, which needs the
    file and, for the rollback journal a save writes beside it, its folder, as a save's first
    write does. A server's sign-in is checked by its privileges instead."""
    from haute._failure_answers import classify_failure

    with engine.connect() as connection:
        version: int = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
        try:
            connection.exec_driver_sql(f"PRAGMA user_version = {int(version)}")
        except DBAPIError as exc:
            answer = classify_failure(exc)
            if answer is None or answer.code != "quote_store_read_only":
                raise
            name = Path(str(engine.url.database)).name
            raise QuoteStoreReadOnlyError(
                f"The quote store's file {name} cannot be written to: it, or the folder it is "
                "in, is read-only. Make both writable; the quotes open once they are."
            ) from None
        finally:
            connection.rollback()


def open_engine(
    url: str,
    form: FormSpec,
    *,
    environment: str,
    sql_schema: str | None = None,
    sign_in: SignIn | None = None,
    named_by: str = HOST_VARIABLES,
) -> tuple[Engine, StoreSchema, tuple[StoreMisfit, ...]]:
    """An engine on the database at *url*, signed in as *sign_in* says, with *form*'s store
    schema in the SQL schema *sql_schema*, and each way the database does not fit it: each
    table and column its own catalogue does not hold as the store reads and writes it, then
    each privilege the sign-in lacks on them, which the app refuses only the uses that need
    them for (:meth:`QuoteBook.unfit`). A database not reached is refused, naming *named_by*.
    A database is judged by its catalogue alone: nothing in it says which environment it is or
    what made its tables."""
    schema = store_schema(form, sql_schema=sql_schema)
    names = [*(stored.table.name for stored in schema.tables), schema.activity.name]

    def checked(connection: Connection) -> tuple[Catalogue, list[StoreMisfit]]:
        catalogue = read_catalogue(connection, sql_schema, names)
        misfits = _catalogue_misfits(catalogue, schema, connection.dialect)
        misfits.extend(
            StoreMisfit(
                table,
                None,
                f"needs {kind} granted to the identity the app signs in as",
                f"{kind} on {table}",
                privilege=kind,
            )
            for table, kind in missing_privileges(connection, schema)
        )
        return catalogue, misfits

    engine = make_engine(url, sign_in=sign_in)
    try:
        with reaching(url, environment, named_by):
            catalogue, misfits = read_store(engine, checked)
        if engine.dialect.name == "sqlite":
            _refuse_unwritable(engine)
    except Exception:
        engine.dispose()
        raise
    kept = with_kept_columns(
        schema,
        {
            found.name: [(column.name, column.kind) for column in found.columns]
            for found in catalogue.tables
        },
    )
    return engine, kept, tuple(misfits)
