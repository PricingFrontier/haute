"""The quote store's database (specs/workbench): its engine, its errors, and its tables
checked against a form.

A database is the form's own tables and Haute's activity (:mod:`._schema`), made and changed
in SQL by the people who own it, starting from the SQL Haute writes (:mod:`._migrate`), in
every environment alike. :func:`open_engine` opens a database whose own catalogue holds the
form's tables as the store reads and writes them, and refuses any other, naming why. Nothing
it does makes, adopts or changes a table.

On SQLite the store takes SQLAlchemy's recipe for the pysqlite driver: the driver leaves
transactions to SQLAlchemy, which begins each with ``BEGIN``, and a write with ``BEGIN
IMMEDIATE``, so DDL is transactional and writers take turns. Foreign keys are on, the journal
is the rollback journal, never write-ahead logging, and a SQLite older than 3.35, which
cannot rename or drop a column, is refused.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Dialect, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from haute._quote_store._catalogue import (
    Catalogue,
    column_problems,
    read_catalogue,
    table_problems,
)
from haute._quote_store._providers import SignIn, make_server_engine, missing_privileges
from haute._quote_store._schema import StoreSchema, store_schema
from haute._workbench_config import (
    STORE_DATABASE_ENV,
    STORE_SERVER_ENV,
    STORE_URL_ENV,
    WorkbenchError,
)
from haute._workbench_form import QUOTE_DETAILS_ID, FormSpec

#: What places a hosted app's database and signs it in, as a refusal names it.
HOST_VARIABLES = (
    f"where {STORE_SERVER_ENV} and {STORE_DATABASE_ENV}, or {STORE_URL_ENV}, say it is and the "
    "variables its sign-in reads"
)
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
    """The environment named, or where its database is or how to sign in to it, cannot be
    resolved. The message says which variable, file or key to set, and names no server,
    database or URL."""

    error_code = "quote_store_environment"


class QuoteStoreUnreachableError(QuoteStoreEnvironmentError):
    """A server database the store cannot reach, or one that refuses its sign-in: the message
    names what places it and signs in to it, never what the driver said."""

    error_code = "quote_store_unreachable"


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
    made."""
    return (isinstance(exc, DBAPIError) and exc.statement is None) or _entra_refused(exc)


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


def _catalogue_differences(
    catalogue: Catalogue, schema: StoreSchema, dialect: Dialect
) -> list[str]:
    """Each way the database's own *catalogue* differs from the tables *schema* derives, which
    every write and read of the store takes as they are: a table or a column missing, of
    another kind, taking a null where the store needs a value or none where it writes one,
    keyed otherwise or not referring to its quote; a text the store compares exactly under
    another collation, on SQL Server (:func:`._catalogue.column_problems` and
    :func:`._catalogue.table_problems`); and a column of those tables the store does not
    write that takes no null, so no row could be inserted."""
    differences: list[str] = []
    tables = [(stored.table, stored.form.id != QUOTE_DETAILS_ID) for stored in schema.tables]
    tables.append((schema.activity, False))
    for table, refers in tables:
        found = catalogue.table(table.name)
        if found is None:
            differences.append(f"the table {table.name} is missing")
            continue
        for column in table.columns:
            held = found.column(column.name)
            if held is None:
                differences.append(f"{table.name}.{column.name} is missing")
                continue
            differences.extend(
                f"{table.name}.{column.name} {said}"
                for _, said in column_problems(column, held, dialect)
            )
        for extra in found.columns:
            if extra.name not in table.columns and not extra.nullable:
                differences.append(
                    f"{table.name}.{extra.name} is no column of the release's and takes no "
                    "null, so no row of it could be saved"
                )
        differences.extend(
            f"{table.name} {said}" for _, said in table_problems(table, found, refers)
        )
    return differences


def open_engine(
    url: str,
    form: FormSpec,
    *,
    environment: str,
    sql_schema: str | None = None,
    sign_in: SignIn | None = None,
    named_by: str = HOST_VARIABLES,
) -> tuple[Engine, StoreSchema]:
    """An engine on the database at *url*, signed in as *sign_in* says, with *form*'s store
    schema in the SQL schema *sql_schema*, once the database's own catalogue holds the form's
    tables as the store reads and writes them and the sign-in holds every privilege the app
    needs on them; refused otherwise, naming each difference and what resolves it, and a
    database not reached naming *named_by*. A database is judged by its catalogue alone:
    nothing in it says which environment it is or what made its tables."""
    schema = store_schema(form, sql_schema=sql_schema)
    engine = make_engine(url, sign_in=sign_in)
    try:
        with reaching(url, environment, named_by):
            with engine.connect() as connection:
                names = [stored.table.name for stored in schema.tables]
                catalogue = read_catalogue(connection, sql_schema, [*names, schema.activity.name])
                differences = _catalogue_differences(catalogue, schema, connection.dialect)
                if differences:
                    raise QuoteStoreMismatchError(
                        "This database's tables do not fit the form: "
                        + "; ".join(differences)
                        + f". {_resolve(environment)}",
                        differences=differences,
                    )
                lacking = missing_privileges(connection, schema)
                if lacking:
                    raise QuoteStoreMismatchError(
                        "The identity the app signs in as lacks what the quote store needs: "
                        + "; ".join(lacking)
                        + ". Grant them to it.",
                        differences=lacking,
                    )
    except Exception:
        engine.dispose()
        raise
    return engine, schema
