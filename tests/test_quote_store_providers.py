"""The quote store on SQL Server and PostgreSQL (specs/workbench): where a server environment's
database is and how the app signs in to it, read from the host's variables with nothing of
either shown; each connection signed in as the release declares, an Entra ID token asked for
as it is made; the store's types pinned on each engine; names past an engine's limit refused
in the plan; the image's drivers; and, on each server the environment names
(tests/_store_servers.py), tables in a SQL
schema of their own made and changed by the SQL Haute writes, the app's check of collations
and privileges, and a release started apart from its project signing in as it declares."""

from __future__ import annotations

import json
import struct
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mssql, postgresql
from sqlalchemy.dialects.mssql.mssqlpython import MSDialect_mssqlpython
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateTable

import haute._quote_store._providers as providers_module
from haute._credential_security import validate_credential_free_uri
from haute._project_context import current_project
from haute._quote_store import (
    HOST_VARIABLES,
    QuoteStoreEnvironmentError,
    QuoteStoreMismatchError,
    QuoteStoreUnreachableError,
    SignIn,
    open_book,
    resolve_store,
)
from haute._quote_store._migrate import offline_plan, plan
from haute._quote_store._providers import (
    PROVIDERS,
    SQL_COPT_SS_ACCESS_TOKEN,
    connect_hook,
    make_server_engine,
    missing_privileges,
    server_url,
)
from haute._quote_store._rows import KEPT_KEY
from haute._quote_store._schema import store_schema, structure
from haute._quote_store._store import make_engine, reaching
from haute._workbench_app import create_app, hosted_store, read_release, store_dependencies
from haute._workbench_app_store import StoreGate
from haute._workbench_config import StoreEnvironment
from haute._workbench_form import FormSpec, with_quote_details
from tests._project import use_project
from tests._store_servers import Target, backends, configured, database
from tests.test_workbench_app_quotes import Page, _write_release

PROJECT = "motor"
TEST = "test"
#: A sign-in by password, its two variables named.
PASSWORD = {"username_env": "STORE_USER", "password_env": "STORE_PASSWORD"}


def form(*tables: dict[str, Any]) -> FormSpec:
    return with_quote_details(
        FormSpec.model_validate(
            {
                "name": PROJECT,
                "schema": {"tables": list(tables)},
                "pages": [
                    {
                        "id": "p",
                        "title": "Sheet 1",
                        "width": 1280,
                        "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
                    }
                ],
            }
        )
    )


def column(name: str, type_: str = "str") -> dict[str, Any]:
    return {"name": name, "type": type_}


def table(name: str, *columns: dict[str, Any], rows: str = "one") -> dict[str, Any]:
    """A schema table of inputs."""
    return {"name": name, "rows": rows, "inputs": list(columns)}


POLICY = table(
    "policy",
    column("region"),
    column("value", "float"),
    column("start", "date"),
    column("telematics", "bool"),
    column("drivers", "int"),
)


class Credential:
    """A stand-in for azure-identity's credentials: a new token each time one is asked for."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, ...]] = []

    def get_token(self, *scopes: str) -> Any:
        self.asked.append(scopes)
        return SimpleNamespace(token=f"token-{len(self.asked)}")


def _packed(token: str) -> bytes:
    raw = token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


# ── Where a server's database is, and how the app signs in ───────────────


def test_a_server_location_is_made_from_its_parts_holding_no_sign_in() -> None:
    declared = (StoreEnvironment("prod", "sqlserver", "password", PASSWORD),)
    environ = {
        "HAUTE_STORE_SERVER": "tcp:hidden-sql.database.windows.net,1433",
        "HAUTE_STORE_DATABASE": "hidden-quotes",
        "HAUTE_STORE_SCHEMA": "hidden_pricing",
        "STORE_USER": "quotes_app",
        "STORE_PASSWORD": "hidden;secret}",
    }

    store = resolve_store(declared, "prod", environ)

    # The server as SQL Server's own clients take it, encryption, and no sign-in, as the
    # connection string mssql-python is handed.
    parsed = make_url(store.url)
    assert parsed.drivername == "mssql+mssqlpython"
    assert MSDialect_mssqlpython().create_connect_args(parsed) == (
        ("Server=hidden-sql.database.windows.net,1433;Database=hidden-quotes;Encrypt=yes",),
        {},
    )
    assert validate_credential_free_uri(store.url) == store.url
    assert store.sql_schema == "hidden_pricing"
    assert store.sign_in == SignIn("password", username="quotes_app", secret="hidden;secret}")
    # Nothing of where it is or how it signs in is shown.
    assert "hidden" not in repr(store)

    pg = resolve_store(
        (StoreEnvironment("prod", "postgresql", "password", PASSWORD),),
        "prod",
        {**environ, "HAUTE_STORE_SERVER": "hidden-pg.example.com:6432"},
    )
    parsed = make_url(pg.url)
    assert (parsed.drivername, parsed.host, parsed.port, parsed.database) == (
        "postgresql+psycopg",
        "hidden-pg.example.com",
        6432,
        "hidden-quotes",
    )


@pytest.mark.parametrize(
    ("server", "says"),
    [
        ("tcp:pricing.database.windows.net,1433", "Server=pricing.database.windows.net,1433"),
        ("TCP: pricing.database.windows.net , 1433", "Server=pricing.database.windows.net,1433"),
        ("pricing.database.windows.net", "Server=pricing.database.windows.net"),
        ("localhost,14330", "Server=localhost,14330"),
    ],
)
def test_a_sqlserver_server_is_taken_as_its_own_clients_write_it(server: str, says: str) -> None:
    url = make_url(server_url(PROVIDERS["sqlserver"], server, "quotes"))

    (connection_string,), _ = MSDialect_mssqlpython().create_connect_args(url)

    assert connection_string == f"{says};Database=quotes;Encrypt=yes"


def test_an_entra_sign_in_reads_only_what_its_identity_needs() -> None:
    system = resolve_store(
        (StoreEnvironment("prod", "sqlserver", "managed-identity"),),
        "prod",
        {"HAUTE_STORE_SERVER": "s", "HAUTE_STORE_DATABASE": "d"},
    )
    assert system.sign_in == SignIn("managed-identity")
    principal = StoreEnvironment(
        "prod",
        "sqlserver",
        "service-principal",
        {"tenant_id_env": "T", "client_id_env": "C", "client_secret_env": "S"},
    )
    signed = resolve_store(
        (principal,),
        "prod",
        {"HAUTE_STORE_URL": "mssql+mssqlpython://s/d", "T": "t", "C": "c", "S": "s3"},
    )
    assert signed.sign_in == SignIn("service-principal", client_id="c", tenant_id="t", secret="s3")


@pytest.mark.parametrize(
    ("provider", "environ", "says"),
    [
        ("sqlserver", {"HAUTE_STORE_SERVER": "hidden-a"}, "set HAUTE_STORE_SERVER and"),
        (
            "sqlserver",
            {"HAUTE_STORE_URL": "mssql+mssqlpython://hidden-u:hidden-p@hidden-a/hidden-b"},
            "holds a sign-in",
        ),
        (
            "sqlserver",
            {"HAUTE_STORE_URL": "mssql+mssqlpython://hidden-a/hidden-b?Password=hidden-p"},
            "holds a sign-in",
        ),
        (
            "sqlserver",
            {"HAUTE_STORE_URL": "mssql+mssqlpython://hidden-a/hidden-b?UID=hidden"},
            "holds a sign-in (UID)",
        ),
        (
            "sqlserver",
            {"HAUTE_STORE_URL": "mssql+mssqlpython://hidden-a/b?Authentication=hidden"},
            "holds a sign-in",
        ),
        (
            "sqlserver",
            {
                "HAUTE_STORE_URL": "mssql+mssqlpython:///?odbc_connect=Server%3Dhidden-a%3BUID%3Dhidden"
            },
            "holds a sign-in (UID)",
        ),
        (
            "sqlserver",
            {"HAUTE_STORE_URL": "mssql+pyodbc://hidden-a/hidden-b"},
            "through mssqlpython",
        ),
        ("sqlserver", {"HAUTE_STORE_URL": "postgresql+psycopg://hidden-a/b"}, "not a sqlserver"),
        ("postgresql", {"HAUTE_STORE_URL": "oracle://hidden-a/hidden-b"}, "does not run on"),
    ],
    ids=[
        "no database",
        "user in the URL",
        "password asked of the URL",
        "user attribute",
        "authentication attribute",
        "ODBC user",
        "driver",
        "provider",
        "engine",
    ],
)
def test_a_server_location_unfit_or_holding_a_sign_in_is_refused_naming_neither(
    provider: str, environ: dict[str, str], says: str
) -> None:
    declared = (StoreEnvironment("prod", provider, "managed-identity", {"username_env": "R"}),)

    with pytest.raises(QuoteStoreEnvironmentError) as refused:
        resolve_store(declared, "prod", {**environ, "R": "role"})

    assert says in str(refused.value)
    assert "hidden" not in str(refused.value)


def test_a_sign_in_whose_variable_holds_nothing_is_refused_naming_the_variable() -> None:
    declared = (StoreEnvironment("prod", "postgresql", "password", PASSWORD),)
    environ = {"HAUTE_STORE_SERVER": "s", "HAUTE_STORE_DATABASE": "d", "STORE_USER": "app"}

    with pytest.raises(QuoteStoreEnvironmentError, match="its password from STORE_PASSWORD"):
        resolve_store(declared, "prod", environ)
    with pytest.raises(QuoteStoreEnvironmentError, match="STORE_PASSWORD"):
        resolve_store(declared, "prod", {**environ, "STORE_PASSWORD": "  "})


def test_a_url_that_does_not_parse_is_refused_without_repeating_it() -> None:
    declared = (StoreEnvironment("prod", "sqlserver", "managed-identity"),)

    with pytest.raises(QuoteStoreEnvironmentError, match="is not a connection URL") as refused:
        resolve_store(declared, "prod", {"HAUTE_STORE_URL": "hidden-server hidden-database"})
    assert "hidden" not in str(refused.value)


def test_a_connection_the_server_refuses_is_said_without_the_drivers_words() -> None:
    # psycopg's and mssql-python's own text names the server, its port and who signs in.
    said = 'connection to server at "hidden-host", port 5432 failed: for user "hidden-user"'
    url = "postgresql+psycopg://hidden-host/quotes"

    with pytest.raises(QuoteStoreEnvironmentError) as refused, reaching(url, "prod"):
        raise OperationalError(None, None, Exception(said))

    # Named by what places the database and signs in to it: the host's variables, unless a
    # connection names it.
    assert str(refused.value) == (
        "The prod quote store's database could not be reached, or it refused the sign-in: "
        f"check {HOST_VARIABLES}."
    )
    assert "hidden" not in str(refused.value)
    assert refused.value.__suppress_context__ and refused.value.__cause__ is None
    with (
        pytest.raises(QuoteStoreEnvironmentError) as connected,
        reaching(url, "dev", "connections.toml [dev]"),
    ):
        raise OperationalError(None, None, Exception(said))
    assert str(connected.value).endswith("refused the sign-in: check connections.toml [dev].")
    # A statement's failure, and anything on a SQLite file, is the store's own to say.
    statement = OperationalError("SELECT 1", {}, Exception("deadlock"))
    with pytest.raises(OperationalError), reaching(url, "prod"):
        raise statement
    with pytest.raises(OperationalError), reaching("sqlite:///store.sqlite", "dev"):
        raise OperationalError(None, None, Exception("unable to open database file"))


# ── Each connection signed in as it is made ──────────────────────────────


def test_a_sqlserver_connection_takes_a_fresh_entra_token_and_no_integrated_sign_in() -> None:
    credential = Credential()
    hook = connect_hook(PROVIDERS["sqlserver"], SignIn("managed-identity"), credential)
    first: list[Any] = ["Server=s;Database=d;Encrypt=yes;Authentication=ActiveDirectoryIntegrated"]
    params: dict[str, Any] = {}

    hook(None, None, first, params)

    assert first == ["Server=s;Database=d;Encrypt=yes"]
    assert params == {"attrs_before": {SQL_COPT_SS_ACCESS_TOKEN: _packed("token-1")}}
    assert credential.asked == [("https://database.windows.net/.default",)]
    # The next connection asks again, so a token past its life is never handed on.
    again: dict[str, Any] = {}
    hook(None, None, ["Server=s"], again)
    assert again["attrs_before"][SQL_COPT_SS_ACCESS_TOKEN] == _packed("token-2")


def test_a_password_sign_in_is_handed_to_each_connection_never_put_in_the_url() -> None:
    hook = connect_hook(PROVIDERS["sqlserver"], SignIn("password", username="app", secret="p;w}d"))
    odbc: list[Any] = ["Server=s;Trusted_Connection=Yes;UID=other"]
    hook(None, None, odbc, {})
    assert odbc == ["Server=s;UID=app;PWD={p;w}}d}"]

    credential = Credential()
    pg = connect_hook(
        PROVIDERS["postgresql"], SignIn("managed-identity", username="role"), credential
    )
    params: dict[str, Any] = {}
    pg(None, None, [], params)
    assert params == {"user": "role", "password": "token-1"}
    assert credential.asked == [("https://ossrdbms-aad.database.windows.net/.default",)]


def test_a_server_engine_signs_in_through_its_hook_and_checks_a_connection_before_use() -> None:
    sign_in = SignIn("password", username="app", secret="hidden")
    engine = make_server_engine("postgresql+psycopg://localhost:1/quotes", sign_in)
    try:
        assert engine.pool._pre_ping
        assert len(engine.dialect.dispatch.do_connect) == 1
        assert "hidden" not in repr(engine)
    finally:
        engine.dispose()


# ── The store's types on each engine ──────────────────────────────────────


def test_the_store_types_are_pinned_on_sql_server_the_identity_exact_in_every_table() -> None:
    schema = store_schema(form(POLICY), sql_schema="pricing")

    def ddl(table: Any, dialect: Any) -> str:
        return str(CreateTable(table).compile(dialect=dialect))

    policy = ddl(schema.by_name("policy").table, mssql.dialect())
    assert "CREATE TABLE pricing.policy" in policy
    assert "quote_reference NVARCHAR(80) COLLATE Latin1_General_100_BIN2 NOT NULL" in policy
    for pinned in (
        "region NVARCHAR(max) NULL",
        "value FLOAT(53) NULL",
        "start DATE NULL",
        "telematics BIT NULL",
        "drivers BIGINT NULL",
    ):
        assert pinned in policy
    assert "REFERENCES pricing.quote_details (quote_reference, quote_option)" in policy
    details = ddl(schema.details.table, mssql.dialect())
    assert "saved_at DATETIME2(6) NOT NULL" in details
    assert "saved_by NVARCHAR(640) COLLATE Latin1_General_100_BIN2 NOT NULL" in details
    activity = ddl(schema.activity, mssql.dialect())
    assert "quote_reference NVARCHAR(80) COLLATE Latin1_General_100_BIN2 NOT NULL" in activity
    assert "activity_id INTEGER NOT NULL IDENTITY" in activity

    on_postgresql = ddl(schema.by_name("policy").table, postgresql.dialect())
    for pinned in (
        "quote_reference VARCHAR(40) NOT NULL",
        "region TEXT",
        "value DOUBLE PRECISION",
        "start DATE",
        "telematics BOOLEAN",
    ):
        assert pinned in on_postgresql
    assert "GENERATED BY DEFAULT AS IDENTITY" in ddl(schema.activity, postgresql.dialect())


# ── Names and drivers ──────────────────────────────────────────────────────


def test_a_name_too_long_for_postgresql_is_refused_in_the_plan() -> None:
    long = "a" * 64
    wanted = structure(form(table("policy", column("region"))))
    # Past the form's own 63-character rule, as a structure made elsewhere could be.
    wanted["tables"][-1]["columns"][0]["name"] = long

    refused = plan(wanted, None, provider=PROVIDERS["postgresql"])

    assert refused.refusals == (
        f"the name {long} is 64 bytes long, past the 63 postgresql keeps whole: shorten it",
    )
    assert plan(wanted, None, provider=PROVIDERS["sqlserver"]).refusals == ()


def test_a_release_image_takes_each_declared_providers_driver() -> None:
    assert store_dependencies(()) == ("sqlalchemy", "alembic")
    assert store_dependencies(
        (
            StoreEnvironment("prod", "sqlserver", "managed-identity"),
            StoreEnvironment("dev2", "sqlite"),
        )
    ) == ("sqlalchemy", "alembic", "mssql-python", "azure-identity")
    assert store_dependencies((StoreEnvironment("prod", "postgresql", "password", PASSWORD),)) == (
        "sqlalchemy",
        "alembic",
        "psycopg",
        "psycopg-binary",
    )


def test_a_started_app_names_each_privilege_its_sign_in_lacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = store_schema(form(POLICY), sql_schema="pri]cing")
    asked: list[list[tuple[str, str]]] = []

    def execute(_statement: Any, params: dict[str, str]) -> Any:
        pairs = [(params[f"n{at}"], params[f"k{at}"]) for at in range(len(params) // 2)]
        asked.append(pairs)
        held = [pair != ("[pri]]cing].[policy]", "DELETE") for pair in pairs]
        return SimpleNamespace(one=lambda: tuple(1 if each else 0 for each in held))

    connection = SimpleNamespace(dialect=SimpleNamespace(name="mssql"), execute=execute)
    assert missing_privileges(connection, schema) == ["DELETE on policy"]  # type: ignore[arg-type]
    # Every privilege asked in one statement, a round trip to the server.
    assert len(asked) == 1
    asked_at_once = asked[0]
    kinds = set(asked_at_once)
    assert ("[pri]]cing].[quote_details]", "UPDATE") in kinds
    assert ("[pri]]cing].[haute_activity]", "INSERT") in kinds
    # More than a statement may bind are asked a few to a statement, answering the same.
    monkeypatch.setattr(providers_module, "_ASKED_AT_ONCE", 2)
    asked.clear()
    assert missing_privileges(connection, schema) == ["DELETE on policy"]  # type: ignore[arg-type]
    assert len(asked) > 1
    assert all(len(each) <= 2 for each in asked)
    assert [pair for each in asked for pair in each] == asked_at_once
    sqlite = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    assert missing_privileges(sqlite, schema) == []  # type: ignore[arg-type]


# ── On each server the environment names ─────────────────────────────────


@pytest.fixture(
    params=[pytest.param(name, marks=pytest.mark.store_servers) for name in configured()]
)
def server(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    """A database of the test's own on each server the run has."""
    with database(request.param, tmp_path) as target:
        yield target


@pytest.fixture(
    params=[
        pytest.param(name, marks=pytest.mark.store_servers)
        for name in configured()
        if name == "sqlserver"
    ]
)
def sqlserver(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    """A database of the test's own on SQL Server, whose default collation ignores case."""
    with database(request.param, tmp_path) as target:
        yield target


def _schema_named(target: Target, name: str) -> None:
    target.run(f"CREATE SCHEMA {name}")


def test_tables_made_from_the_sql_take_each_later_form_keeping_every_value(
    server: Target,
) -> None:
    _schema_named(server, "pricing")
    where = {"sql_schema": "pricing", "sign_in": server.sign_in}
    base = form(
        table(
            "policy",
            column("region"),
            column("code"),
            column("drivers", "int"),
            column("telematics", "bool"),
        )
    )
    server.script(server.sql(base, sql_schema="pricing"))
    with open_book(server.url, base, environment=TEST, haute_version="1", **where) as book:
        book.create(
            operation="operation-0001",
            actor="ana",
            values={"policy": [{"region": "north", "code": "42", "drivers": "2"}]},
            at=book.now(),
            reference="A-1",
        )

    # region renamed area, which is one column added beside region kept (READ-23), a Text
    # column made an Integer and an Integer a Decimal in place, a column added and one
    # removed, which the SQL keeps.
    changed = form(
        table(
            "policy",
            column("area"),
            column("code", "int"),
            column("drivers", "float"),
            column("seats", "int"),
        )
    )
    written = server.sql(changed, base, sql_schema="pricing")
    assert "-- ALTER TABLE pricing.policy DROP COLUMN region;" in written
    assert "-- ALTER TABLE pricing.policy DROP COLUMN telematics;" in written
    server.script(written)

    # Every value kept, read with plain SQL under the names the tables hold.
    assert server.read(
        "SELECT quote_reference, region, area, code, drivers, seats, telematics FROM pricing.policy"
    ) == [("A-1", "north", None, 42, 2.0, None, 0)]
    types = dict(
        server.read(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = "
            "'pricing' AND table_name = 'policy' AND column_name IN ('code', 'drivers')"
        )
    )
    assert types == (
        {"code": "bigint", "drivers": "double precision"}
        if server.backend == "postgresql"
        else {"code": "bigint", "drivers": "float"}
    )
    # The started app opens the tables as they are, in their schema, the removed column's
    # value carried with the quote.
    with open_book(server.url, changed, environment=TEST, haute_version="1", **where) as book:
        row = book.read("A-1", 1).values["policy"][0]
        assert row["code"] == "42"
        assert json.loads(row[KEPT_KEY])["region"] == "north"


@pytest.fixture(params=backends())
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    """A database of the test's own on SQLite and on each server the run has."""
    with database(request.param, tmp_path) as target:
        yield target


def _index(name: str) -> dict[str, Any]:
    return {"name": name, "type": "int", "index": "numbered"}


@pytest.mark.parametrize(
    ("before", "after", "values"),
    [
        pytest.param(
            {"name": "vehicles", "rows": "many", "inputs": [_index("row_number"), column("reg")]},
            {"name": "vehicles", "rows": "many", "inputs": [_index("vehicle_no"), column("reg")]},
            ("reg", "'AB12'", "'CD34'"),
            id="an input table's index",
        ),
        pytest.param(
            {
                "name": "vehicles",
                "rows": "many",
                "outputs": [_index("row_number"), column("premium", "float")],
            },
            {
                "name": "vehicles",
                "rows": "many",
                "outputs": [_index("vehicle_no"), column("premium", "float")],
            },
            ("premium", "10.5", "20.5"),
            id="a table of outputs alone's index",
        ),
    ],
)
def test_a_renamed_index_renames_its_column_keeping_its_values_and_the_tables_key(
    store: Target, before: dict[str, Any], after: dict[str, Any], values: tuple[str, str, str]
) -> None:
    base, renamed = form(before), form(after)
    store.script(store.sql(base))
    other, first, second = values
    _a_quote_holding(
        store,
        base,
        f"(quote_reference, quote_option, row_number, {other}) "
        f"VALUES ('A-1', 1, 1, {first}), ('A-1', 1, 2, {second})",
    )

    # Paired by what it is: renamed, never a column added beside the one keying the table.
    assert [step.kind for step in offline_plan(base, renamed).steps] == ["rename column"]
    store.script(store.sql(renamed, base))

    # Its values kept under the new name, which keys the table: the app opens it as it is.
    assert store.read("SELECT vehicle_no FROM vehicles ORDER BY vehicle_no") == [(1,), (2,)]
    open_book(
        store.url, renamed, environment=TEST, haute_version="1", sign_in=store.sign_in
    ).close()


def _a_quote_holding(store: Target, base: FormSpec, vehicles: str) -> None:
    """Quote A-1 made through the app's own book, so its quote_details row is the app's on
    every engine, then *vehicles*, its rows, written with plain SQL."""
    with open_book(
        store.url, base, environment=TEST, haute_version="1", sign_in=store.sign_in
    ) as book:
        book.create(
            operation="operation-0001", actor="ana", values={}, at=book.now(), reference="A-1"
        )
    store.run(f"INSERT INTO vehicles {vehicles}")


def _quote_with_two_vehicles(store: Target, base: FormSpec, index: str, other: str) -> None:
    _a_quote_holding(
        store,
        base,
        f"(quote_reference, quote_option, {index}, {other}) "
        "VALUES ('A-1', 1, 1, 7), ('A-1', 1, 2, 8)",
    )


def test_an_index_renamed_its_old_name_given_a_new_column_is_a_rename_and_an_addition(
    store: Target,
) -> None:
    base = form(
        {
            "name": "vehicles",
            "rows": "many",
            "inputs": [_index("row_number"), column("seats", "int")],
        }
    )
    reused = form(
        {
            "name": "vehicles",
            "rows": "many",
            "inputs": [_index("vehicle_no"), column("seats", "int"), column("row_number")],
        }
    )
    store.script(store.sql(base))
    _quote_with_two_vehicles(store, base, "row_number", "seats")

    # The index paired first: its old name is free for the new column, added after the rename.
    plan = offline_plan(base, reused)
    assert (plan.refusals, [step.kind for step in plan.steps]) == (
        (),
        ["rename column", "add column"],
    )
    store.script(store.sql(reused, base))

    assert store.read("SELECT vehicle_no, seats, row_number FROM vehicles ORDER BY vehicle_no") == [
        (1, 7, None),
        (2, 8, None),
    ]
    open_book(store.url, reused, environment=TEST, haute_version="1", sign_in=store.sign_in).close()


def test_an_index_renamed_onto_another_columns_name_is_refused_changing_nothing(
    store: Target,
) -> None:
    base = form(
        {
            "name": "vehicles",
            "rows": "many",
            "inputs": [_index("row_number"), column("amount", "float")],
        }
    )
    # The two names swapped, a Decimal's among them, and a column added beside: whose values
    # each name should hold is not the SQL's to guess, and nothing of the table changes, so no
    # column is retyped either.
    swapped = form(
        {
            "name": "vehicles",
            "rows": "many",
            "inputs": [_index("amount"), column("row_number", "float"), column("seats", "int")],
        }
    )
    store.script(store.sql(base))
    _a_quote_holding(
        store,
        base,
        "(quote_reference, quote_option, row_number, amount) "
        "VALUES ('A-1', 1, 1, 7.25), ('A-1', 1, 2, 8.5)",
    )

    plan = offline_plan(base, swapped)
    assert plan.steps == ()
    assert plan.refusals == (
        "the index of vehicles, row_number, renamed amount, takes the name of its column "
        "amount: rename that column to a name of its own first, or drop it; nothing else of "
        "the table changes until then",
    )
    store.script(store.sql(swapped, base))
    assert store.read("SELECT row_number, amount FROM vehicles ORDER BY row_number") == [
        (1, 7.25),
        (2, 8.5),
    ]


def _columns_in_order(server: Target, owner: str, table_name: str) -> str:
    """A catalogue query of *table_name*'s columns in their order."""
    if server.backend == "postgresql":
        return (
            "SELECT column_name FROM information_schema.columns WHERE table_schema = "
            f"'{owner}' AND table_name = '{table_name}' ORDER BY ordinal_position"
        )
    return (
        "SELECT c.name FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id "
        f"JOIN sys.schemas s ON t.schema_id = s.schema_id WHERE s.name = '{owner}' "
        f"AND t.name = '{table_name}' ORDER BY c.column_id"
    )


@contextmanager
def _script_run(server: Target, script: str) -> Iterator[tuple[Any, BaseException | None]]:
    """*script* run as a DBA runs it, on a connection of its own in autocommit, each
    statement's result read so a later one's error is raised; the connection, and the error
    if one was raised, given before the connection is closed, so what the script left open
    is seen as it left it."""
    engine = make_engine(server.url, sign_in=server.sign_in)
    raw = engine.raw_connection()
    try:
        raw.driver_connection.autocommit = True  # type: ignore[union-attr]
        cursor = raw.cursor()
        raised: BaseException | None = None
        try:
            cursor.execute(script)
            if server.backend == "sqlserver":
                # The driver raises a statement's error once its result is reached.
                while cursor.nextset():
                    pass
        except Exception as exc:  # noqa: BLE001 - the driver's own error is the evidence
            raised = exc
        yield raw, raised
    finally:
        raw.close()
        engine.dispose()


def _left_open(server: Target, raw: Any) -> bool:
    """Whether the script left on *raw* a transaction that a COMMIT could still commit,
    whatever of it ran: rolled back by nothing yet, only the connection's closing would undo
    it. A PostgreSQL transaction that failed can only roll back, so it is not one."""
    if server.backend == "postgresql":
        import psycopg

        status = raw.driver_connection.info.transaction_status
        return bool(status == psycopg.pq.TransactionStatus.INTRANS)
    cursor = raw.cursor()
    cursor.execute("SELECT @@TRANCOUNT")
    (open_transactions,) = cursor.fetchone()
    cursor.close()
    return bool(open_transactions)


def test_a_script_runs_on_the_server_whole_or_not_at_all(server: Target) -> None:
    _schema_named(server, "pricing")
    where = {"sql_schema": "pricing", "sign_in": server.sign_in}
    base = form(table("policy", column("region"), column("code")))
    server.script(server.sql(base, sql_schema="pricing"))
    with open_book(server.url, base, environment=TEST, haute_version="1", **where) as book:
        book.create(
            operation="operation-0001",
            actor="ana",
            values={"policy": [{"region": "north", "code": "x1"}]},
            at=book.now(),
            reference="A-1",
        )
    # code made an Integer, then area added.
    changed = form(table("policy", column("code", "int"), column("area")))
    script = server.sql(changed, base, sql_schema="pricing")
    names = _columns_in_order(server, "pricing", "policy")

    # A value that does not convert stops the retype: nothing is left changed and nothing left
    # open.
    with _script_run(server, script) as (raw, raised):
        assert raised is not None
        assert not _left_open(server, raw)
    assert [name for (name,) in server.read(names)] == [
        "quote_reference",
        "quote_option",
        "region",
        "code",
    ]

    # A column the SQL side added since takes the added column's name: the script fails part
    # way, after the retype, and leaves nothing changed and nothing open.
    server.run("UPDATE pricing.policy SET code = '7'", "ALTER TABLE pricing.policy ADD area BIGINT")
    with _script_run(server, script) as (raw, raised):
        assert raised is not None
        assert not _left_open(server, raw)
        assert [name for (name,) in server.read(names)] == [
            "quote_reference",
            "quote_option",
            "region",
            "code",
            "area",
        ]
    server.run("ALTER TABLE pricing.policy DROP COLUMN area")

    # Run whole, the tables fit the form: the app opens them, the value converted.
    with _script_run(server, script) as (raw, raised):
        assert raised is None and not _left_open(server, raw)
    with open_book(server.url, changed, environment=TEST, haute_version="1", **where) as book:
        assert book.read("A-1", 1).values["policy"][0]["code"] == "7"


def test_a_sign_in_the_server_refuses_closes_the_quotes_naming_no_server_or_user(
    server: Target,
) -> None:
    wrong = SignIn("password", username="haute_nobody", secret="Wrong-p4ss-only!")
    host = str(make_url(server.url).host)
    server.script(server.sql(form(POLICY)))

    def opened() -> Any:
        return open_book(
            server.url,
            form(POLICY),
            environment=TEST,
            haute_version="1",
            sign_in=wrong,
        )

    with pytest.raises(QuoteStoreUnreachableError) as starting:
        opened()
    said = str(starting.value)
    assert "could not be reached, or it refused the sign-in" in said
    assert host not in said and "haute_nobody" not in said
    # A started app keeps its quotes closed as unreachable, saying the same.
    state = StoreGate(opened).state()
    assert (state.open, state.error_code) == (False, "quote_store_unreachable")
    assert host not in str(state.message) and "haute_nobody" not in str(state.message)


def test_a_sign_in_lacking_a_privilege_closes_the_quotes_naming_it(server: Target) -> None:
    server.script(server.sql(form(POLICY)))
    # A login of the test's own: a login outlives the test's database on SQL Server.
    reader = f"haute_reader_{uuid.uuid4().hex[:8]}"
    secret = "Reader-p4ss-only!"
    if server.backend == "sqlserver":
        server.run(
            f"CREATE LOGIN {reader} WITH PASSWORD = '{secret}', CHECK_POLICY = OFF",
        )
        server.run(f"CREATE USER {reader} FOR LOGIN {reader}", f"GRANT SELECT TO {reader}")
    else:
        server.run(
            f"CREATE ROLE {reader} LOGIN PASSWORD '{secret}'",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {reader}",
        )

    def opened() -> Any:
        return open_book(
            server.url,
            form(POLICY),
            environment=TEST,
            haute_version="1",
            sign_in=SignIn("password", username=reader, secret=secret),
        )

    try:
        with pytest.raises(QuoteStoreMismatchError, match="INSERT on policy") as refused:
            opened()
        assert "SELECT on" not in str(refused.value)
        state = StoreGate(opened).state()
        assert (state.open, state.error_code) == (False, "quote_store_mismatch")
        assert "INSERT on policy" in state.differences
    finally:
        if server.backend == "sqlserver":
            server.run(f"DROP USER {reader}")
            # The driver may pool the refused app's connection: end it first,
            # outside a transaction, which KILL takes no part in.
            engine = make_engine(server.url, sign_in=server.sign_in)
            try:
                with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as admin:
                    admin.exec_driver_sql(
                        "DECLARE @end nvarchar(max) = N''; "
                        "SELECT @end += N'KILL ' + CAST(session_id AS nvarchar(10)) + N'; ' "
                        f"FROM sys.dm_exec_sessions WHERE login_name = N'{reader}'; "
                        "EXEC sp_executesql @end;"
                    )
                    admin.exec_driver_sql(f"DROP LOGIN {reader}")
            finally:
                engine.dispose()
        else:
            server.run(f"DROP OWNED BY {reader}", f"DROP ROLE {reader}")


def test_an_exact_text_under_another_collation_closes_the_quotes(sqlserver: Target) -> None:
    server = sqlserver
    server.script(server.sql(form(POLICY)))
    server.run(
        "ALTER TABLE quote_details ALTER COLUMN saved_by NVARCHAR(640) "
        "COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL"
    )

    with pytest.raises(QuoteStoreMismatchError, match="saved_by compares under SQL_Latin1"):
        open_book(
            server.url,
            form(POLICY),
            environment=TEST,
            haute_version="1",
            sign_in=server.sign_in,
        )


def test_a_release_started_apart_from_its_project_signs_in_as_it_declares(
    server: Target, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = current_project().root
    use_project(tmp_path)
    declared = StoreEnvironment(TEST, server.backend, "password", PASSWORD)
    release_dir = _write_release(tmp_path / "release", environments=(declared,))
    _, release_form, _ = read_release(release_dir)
    # Its tables made from the SQL Haute writes, as the people who own the database make them.
    server.script(server.sql(release_form))
    assert server.sign_in is not None
    for name, value in {
        "HAUTE_ENVIRONMENT": TEST,
        "HAUTE_STORE_URL": server.url,
        "STORE_USER": str(server.sign_in.username),
        "STORE_PASSWORD": str(server.sign_in.secret),
    }.items():
        monkeypatch.setenv(name, value)
    try:
        with TestClient(
            create_app(release_dir, store=partial(hosted_store, release_dir))
        ) as client:
            saved = Page(client).priced()
    finally:
        use_project(original)

    assert server.read("SELECT quote_reference, stage FROM quote_details") == [
        (saved["reference"], "draft")
    ]
