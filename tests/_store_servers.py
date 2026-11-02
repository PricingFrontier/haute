"""The databases the quote store's tests run on (specs/workbench): SQLite always, and each
SQL Server or PostgreSQL server the environment names, as the CI job's containers do.

A server is named by ``HAUTE_TEST_SQLSERVER_URL`` or ``HAUTE_TEST_POSTGRESQL_URL``, its URL
with no sign-in in it, naming a database the login may connect to (SQL Server's ``master``,
which a pyodbc URL needs or it takes the server for a DSN, or PostgreSQL's ``postgres``), and
signed in to as the login ``HAUTE_TEST_<NAME>_USER`` and
``HAUTE_TEST_<NAME>_PASSWORD`` name, which may make and drop databases. Each test is given a
database of its own on it, dropped after the test, so its tables are its own and a plain SQL
read names them as a report would. On every backend a store's tables are made from the SQL
Haute writes, as the people who own a database make them, and it is opened in one
environment, :data:`HOSTED`, whatever the backend. A server's runs are marked
``store_servers``; with none named, a store test runs on SQLite alone and nothing is skipped.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from haute._quote_store import SignIn, offline_plan, render_sql, store_url
from haute._quote_store._migrate import PROVIDER_DIALECTS
from haute._quote_store._providers import PROVIDERS
from haute._quote_store._store import make_engine
from haute._workbench_form import FormSpec

#: The servers a store test may run on besides SQLite.
SERVERS = ("sqlserver", "postgresql")
#: The environment a test's store is opened in, on every backend.
HOSTED = "test"


def _variable(server: str, what: str) -> str:
    return f"HAUTE_TEST_{server.upper()}_{what}"


def configured() -> list[str]:
    """The servers this run's environment names."""
    return [server for server in SERVERS if os.environ.get(_variable(server, "URL"))]


def backends() -> list[Any]:
    """The databases a store test runs on: SQLite, then each server named, marked
    ``store_servers``."""
    return [
        "sqlite",
        *(pytest.param(server, marks=pytest.mark.store_servers) for server in configured()),
    ]


@dataclass(frozen=True)
class Target:
    """A database a test's store is on: its backend, its URL and how a store signs in."""

    backend: str
    url: str
    sign_in: SignIn | None = None

    @property
    def environment(self) -> str:
        """The environment a store on this database is opened in: the same on every
        backend, as every environment's database is checked alike."""
        return HOSTED

    def make(self, form: FormSpec, *, base: FormSpec | None = None) -> None:
        """The store's tables brought to *form* by running the SQL Haute writes for this
        backend from *base*'s tables, or from none, as the people who own a database run
        it."""
        self.script(self.sql(form, base))

    def sql(self, form: FormSpec, base: FormSpec | None = None, **where: Any) -> str:
        """The SQL Haute writes for this backend from *base*'s tables, or from none, to
        *form*'s, *where* naming its SQL schema."""
        plan = offline_plan(base, form, provider=PROVIDERS[self.backend])
        return render_sql(plan, form, PROVIDER_DIALECTS[self.backend], **where)

    def script(self, script: str) -> None:
        """Run *script*, SQL as the people who own a database run it, on a connection of its
        own in autocommit, the driver's error raised when a statement fails."""
        if self.backend == "sqlite":
            database = make_url(self.url).database
            assert database is not None
            connection = sqlite3.connect(database)
            try:
                connection.executescript(script)
            finally:
                connection.close()
            return
        engine = make_engine(self.url, sign_in=self.sign_in)
        raw = engine.raw_connection()
        try:
            raw.driver_connection.autocommit = True  # type: ignore[union-attr]
            cursor = raw.cursor()
            cursor.execute(script)
            if self.backend == "sqlserver":
                # pyodbc raises a statement's error once its result is reached.
                while cursor.nextset():
                    pass
        finally:
            raw.close()
            engine.dispose()

    def read(self, query: str, **params: Any) -> list[tuple[Any, ...]]:
        """The rows *query* reads, plain SQL, each value as SQLite would answer it: a day as
        its ISO text and True/false as 1 or 0, so one assertion holds on every backend."""
        engine = make_engine(self.url, sign_in=self.sign_in)
        try:
            with engine.connect() as connection:
                rows = connection.execute(text(query), params).fetchall()
        finally:
            engine.dispose()
        return [tuple(_as_sqlite(value) for value in row) for row in rows]

    def run(self, *statements: str) -> None:
        """Run each of *statements*, committed together."""
        engine = make_engine(self.url, sign_in=self.sign_in)
        try:
            with engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))
        finally:
            engine.dispose()


def _as_sqlite(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def server_sign_in(server: str) -> SignIn:
    """The password sign-in of the login the environment names for *server*."""
    return SignIn(
        "password",
        username=os.environ[_variable(server, "USER")],
        secret=os.environ[_variable(server, "PASSWORD")],
    )


@contextmanager
def database(backend: str, tmp_path: Path) -> Iterator[Target]:
    """A database of the test's own on *backend*: a SQLite file under *tmp_path*, or a
    database made on the server for the test and dropped after it."""
    if backend == "sqlite":
        yield Target("sqlite", store_url(tmp_path / "store.sqlite"))
        return
    server = make_url(os.environ[_variable(backend, "URL")])
    sign_in = server_sign_in(backend)
    name = f"haute_test_{uuid.uuid4().hex[:12]}"
    engine = make_engine(server.render_as_string(hide_password=False), sign_in=sign_in)
    admin = engine.execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {name}"))
            if backend == "sqlserver":
                # As the CI job's server runs: reads see the last commit without blocking.
                connection.execute(text(f"ALTER DATABASE {name} SET READ_COMMITTED_SNAPSHOT ON"))
        try:
            yield Target(
                backend, server.set(database=name).render_as_string(hide_password=False), sign_in
            )
        finally:
            with admin.connect() as connection:
                if backend == "sqlserver":
                    connection.execute(
                        text(f"ALTER DATABASE {name} SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
                    )
                    connection.execute(text(f"DROP DATABASE {name}"))
                else:
                    connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
    finally:
        engine.dispose()
