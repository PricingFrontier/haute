"""The quote store on SQL Server and PostgreSQL (specs/workbench): where a server environment's
database is and how the app signs in to it, read from the host's variables with nothing of
either shown; each connection signed in as the release declares, an Entra ID token asked for
as it is made; the store's types pinned on each engine; names past an engine's limit refused
in the plan; an empty column's type changed in place where the engine can; the image's
drivers; and, on each server the environment names (tests/_store_servers.py), the planner in a
SQL schema of the store's own, the startup check of collations and privileges, and a release
started apart from its project signing in as it declares."""

from __future__ import annotations

import io
import sqlite3
import struct
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from click.testing import CliRunner
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mssql, postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.schema import CreateTable

from haute._credential_security import validate_credential_free_uri
from haute._quote_store import (
    QuoteStoreEnvironmentError,
    QuoteStoreMismatchError,
    SignIn,
    _migrate,
    offline_plan,
    open_book,
    plan_store,
    prepare_store,
    render_sql,
    resolve_store,
)
from haute._quote_store._migrate import MigrationRefusedError, _emit, plan
from haute._quote_store._providers import (
    PROVIDERS,
    SQL_COPT_SS_ACCESS_TOKEN,
    connect_hook,
    make_server_engine,
    missing_privileges,
    retypes_in_place,
)
from haute._quote_store._schema import store_schema, structure
from haute._quote_store._store import make_engine, reaching
from haute._sandbox import _get_project_root, set_project_root
from haute._workbench_app import create_app, hosted_store, store_dependencies
from haute._workbench_config import StoreEnvironment
from haute._workbench_form import FormSpec, with_quote_details
from haute.cli import cli
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


def column(name: str, type_: str = "str", id_: str | None = None) -> dict[str, Any]:
    return {"id": id_ or f"c_{name}", "name": name, "type": type_}


def table(name: str, *columns: dict[str, Any], rows: str = "one") -> dict[str, Any]:
    return {"id": f"t_{name}", "name": name, "rows": rows, "columns": list(columns)}


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

    # The server as SQL Server's own clients take it, the driver, encryption, and no sign-in.
    assert make_url(store.url).query["odbc_connect"] == (
        "Driver={ODBC Driver 18 for SQL Server};"
        "Server=tcp:hidden-sql.database.windows.net,1433;Database=hidden-quotes;Encrypt=yes"
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
        {"HAUTE_STORE_URL": "mssql+pyodbc://s/d?driver=x", "T": "t", "C": "c", "S": "s3"},
    )
    assert signed.sign_in == SignIn("service-principal", client_id="c", tenant_id="t", secret="s3")


@pytest.mark.parametrize(
    ("provider", "environ", "says"),
    [
        ("sqlserver", {"HAUTE_STORE_SERVER": "hidden-a"}, "set HAUTE_STORE_SERVER and"),
        (
            "sqlserver",
            {"HAUTE_STORE_URL": "mssql+pyodbc://hidden-u:hidden-p@hidden-a/hidden-b"},
            "holds a sign-in",
        ),
        (
            "sqlserver",
            {"HAUTE_STORE_URL": "mssql+pyodbc://hidden-a/hidden-b?Password=hidden-p"},
            "holds a sign-in",
        ),
        (
            "sqlserver",
            {"HAUTE_STORE_URL": "mssql+pyodbc:///?odbc_connect=Server%3Dhidden-a%3BUID%3Dhidden"},
            "holds a sign-in (UID)",
        ),
        ("sqlserver", {"HAUTE_STORE_URL": "mssql+pymssql://hidden-a/hidden-b"}, "through pyodbc"),
        ("sqlserver", {"HAUTE_STORE_URL": "postgresql+psycopg://hidden-a/b"}, "not a sqlserver"),
        ("postgresql", {"HAUTE_STORE_URL": "oracle://hidden-a/hidden-b"}, "does not run on"),
    ],
    ids=[
        "no database",
        "user in the URL",
        "password asked of the URL",
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
    # psycopg's and pyodbc's own text names the server, its port and who signs in.
    said = 'connection to server at "hidden-host", port 5432 failed: for user "hidden-user"'
    url = "postgresql+psycopg://hidden-host/quotes"

    with pytest.raises(QuoteStoreEnvironmentError) as refused, reaching(url, "prod"):
        raise OperationalError(None, None, Exception(said))

    assert "The prod quote store's database could not be reached" in str(refused.value)
    assert "hidden" not in str(refused.value)
    assert refused.value.__suppress_context__ and refused.value.__cause__ is None
    # A statement's failure, and anything on dev's SQLite file, is the store's own to say.
    statement = OperationalError("SELECT 1", {}, Exception("deadlock"))
    with pytest.raises(OperationalError), reaching(url, "prod"):
        raise statement
    with pytest.raises(OperationalError), reaching("sqlite:///store.sqlite", "dev"):
        raise OperationalError(None, None, Exception("unable to open database file"))


# ── Each connection signed in as it is made ──────────────────────────────


def test_a_sqlserver_connection_takes_a_fresh_entra_token_and_no_integrated_sign_in() -> None:
    credential = Credential()
    hook = connect_hook(PROVIDERS["sqlserver"], SignIn("managed-identity"), credential)
    first: list[Any] = ["Driver={ODBC Driver 18 for SQL Server};Server=s;Trusted_Connection=Yes"]
    params: dict[str, Any] = {}

    hook(None, None, first, params)

    assert first == ["Driver={ODBC Driver 18 for SQL Server};Server=s"]
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

    policy = ddl(schema.by_id("t_policy").table, mssql.dialect())
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

    on_postgresql = ddl(schema.by_id("t_policy").table, postgresql.dialect())
    for pinned in (
        "quote_reference VARCHAR(40) NOT NULL",
        "region TEXT",
        "value DOUBLE PRECISION",
        "start DATE",
        "telematics BOOLEAN",
    ):
        assert pinned in on_postgresql
    assert "GENERATED BY DEFAULT AS IDENTITY" in ddl(schema.activity, postgresql.dialect())


# ── Names, retypes and drivers ─────────────────────────────────────────────


def test_a_name_too_long_for_postgresql_is_refused_in_the_plan() -> None:
    long = "a" * 64
    wanted = structure(form(table("policy", column("region"))))
    # Past the form's own 63-character rule, as a structure made elsewhere could be.
    wanted["tables"][-1]["columns"][0]["name"] = long

    refused = plan(wanted, None, None, "hosted", provider=PROVIDERS["postgresql"])

    assert refused.refusals == (
        f"the name {long} is 64 bytes long, past the 63 postgresql keeps whole: shorten it",
    )
    assert plan(wanted, None, None, "hosted", provider=PROVIDERS["sqlserver"]).refusals == ()


def test_an_empty_column_changes_type_in_place_where_the_engine_converts_the_pair() -> None:
    sqlserver, pg, sqlite = (PROVIDERS[name] for name in ("sqlserver", "postgresql", "sqlite"))
    assert retypes_in_place(sqlserver, "float", "int")
    assert retypes_in_place(sqlserver, "str", "date")
    assert not retypes_in_place(sqlserver, "date", "int")
    assert not retypes_in_place(sqlserver, "bool", "date")
    assert retypes_in_place(pg, "date", "int")
    assert not retypes_in_place(sqlite, "float", "int")

    base = form(POLICY)
    retyped = form(
        table(
            "policy",
            column("region"),
            column("value", "int"),
            column("start", "int"),
            column("telematics", "bool"),
            column("drivers", "int"),
        )
    )
    made = offline_plan(base, retyped, provider=sqlserver)
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mssql", opts={"as_sql": True, "output_buffer": buffer}
    )
    _emit(Operations(context), made, store_schema(retyped, sql_schema="pricing"))
    ddl = buffer.getvalue()

    # A float SQL Server converts in place; a date it cannot, so the empty column is replaced.
    assert "ALTER TABLE pricing.policy ALTER COLUMN value BIGINT NULL" in ddl
    assert "ALTER TABLE pricing.policy DROP COLUMN start" in ddl
    assert "EXEC sp_rename 'pricing.policy.haute_tmp_" in ddl
    # A migrate applies these, checking each column empty; no script prints them.
    with pytest.raises(MigrationRefusedError, match="haute app migrate checks"):
        render_sql(
            made,
            retyped,
            "mssql",
            project=PROJECT,
            environment=TEST,
            release="r1",
            haute_version="1",
            restore_point="backup-1",
        )


def test_a_release_image_takes_each_declared_providers_driver() -> None:
    assert store_dependencies(()) == ("sqlalchemy", "alembic")
    assert store_dependencies(
        (
            StoreEnvironment("prod", "sqlserver", "managed-identity"),
            StoreEnvironment("dev2", "sqlite"),
        )
    ) == ("sqlalchemy", "alembic", "pyodbc", "azure-identity")
    assert store_dependencies((StoreEnvironment("prod", "postgresql", "password", PASSWORD),)) == (
        "sqlalchemy",
        "alembic",
        "psycopg",
        "psycopg-binary",
    )


def test_a_started_app_names_each_privilege_its_sign_in_lacks() -> None:
    schema = store_schema(form(POLICY), sql_schema="pri]cing")
    asked: list[dict[str, str]] = []

    def execute(_statement: Any, params: dict[str, str]) -> Any:
        asked.append(params)
        held = (params["name"], params["kind"]) != ("[pri]]cing].[policy]", "DELETE")
        return SimpleNamespace(scalar=lambda: 1 if held else 0)

    connection = SimpleNamespace(dialect=SimpleNamespace(name="mssql"), execute=execute)
    assert missing_privileges(connection, schema) == ["DELETE on policy"]  # type: ignore[arg-type]
    kinds = {(each["name"], each["kind"]) for each in asked}
    assert ("[pri]]cing].[quote_details]", "UPDATE") in kinds
    assert ("[pri]]cing].[haute_activity]", "INSERT") in kinds
    assert ("[pri]]cing].[haute_store]", "SELECT") in kinds
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


def test_the_planner_brings_a_server_store_in_its_own_schema_to_each_release(
    server: Target,
) -> None:
    _schema_named(server, "pricing")
    where = {"sql_schema": "pricing", "sign_in": server.sign_in}
    prepare_store(server.url, form(POLICY), project=PROJECT, environment=TEST, **where)
    with open_book(
        server.url, form(POLICY), project=PROJECT, environment=TEST, haute_version="1", **where
    ) as book:
        book.create(
            operation="operation-0001",
            actor="ana",
            values={"t_policy": [{"c_region": "north", "c_value": "1.5"}]},
            at=book.now(),
            reference="A-1",
        )

    # A rename of case only, a column added, one removed kept, and an empty one retyped.
    changed = form(
        table(
            "policy",
            column("Region", id_="c_region"),
            column("value", "float"),
            column("start", "int"),
            column("seats", "int"),
        )
    )
    made = prepare_store(
        server.url,
        changed,
        project=PROJECT,
        environment=TEST,
        restore_point="backup-1",
        **where,
    )

    assert {(step.kind, step.column) for step in made.steps} >= {
        ("rename column", "Region"),
        ("add column", "seats"),
        ("keep column", "telematics"),
        ("retype column", "start"),
    }
    # Every value kept, read with plain SQL under the names the release gives them.
    assert server.read(
        'SELECT quote_reference, "Region", value, start, seats, telematics FROM pricing.policy'
    ) == [("A-1", "north", 1.5, None, None, 0)]
    # The started app opens it as it is, in its schema, with every privilege it needs.
    with open_book(
        server.url, changed, project=PROJECT, environment=TEST, haute_version="1", **where
    ) as book:
        assert book.read("A-1", 1).values["t_policy"][0]["c_region"] == "north"


def test_an_empty_column_is_retyped_on_a_server_in_place_or_by_replacement(
    server: Target,
) -> None:
    _schema_named(server, "pricing")
    where = {"sql_schema": "pricing", "sign_in": server.sign_in}
    base = form(table("policy", column("note"), column("start", "date")))
    prepare_store(server.url, base, project=PROJECT, environment=TEST, **where)
    # PostgreSQL changes the empty text column in place; SQL Server replaces the empty date.
    moved = "note" if server.backend == "postgresql" else "start"
    retyped = form(
        table(
            "policy",
            column("note", "int" if moved == "note" else "str"),
            column("start", "int" if moved == "start" else "date"),
        )
    )

    made = prepare_store(
        server.url, retyped, project=PROJECT, environment=TEST, restore_point="backup-2", **where
    )

    assert [(step.kind, step.column) for step in made.steps] == [("retype column", moved)]
    # The column is the new type in the server's own catalogue, under its name.
    assert server.read(
        "SELECT data_type FROM information_schema.columns WHERE table_schema = 'pricing' "
        f"AND table_name = 'policy' AND column_name = '{moved}'"
    ) == [("bigint",)]
    with open_book(
        server.url, retyped, project=PROJECT, environment=TEST, haute_version="1", **where
    ) as book:
        made_quote = book.create(
            operation="operation-0001",
            actor="ana",
            values={"t_policy": [{f"c_{moved}": "7"}]},
            at=book.now(),
            reference="A-1",
        )
        assert made_quote.values["t_policy"][0][f"c_{moved}"] == "7"


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
                # pyodbc raises a statement's error once its result is reached.
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


def test_a_plan_exported_as_sql_runs_on_the_server_whole_or_not_at_all(server: Target) -> None:
    _schema_named(server, "pricing")
    where = {"sql_schema": "pricing", "sign_in": server.sign_in}
    base = form(table("policy", column("region")))
    prepare_store(server.url, base, project=PROJECT, environment=TEST, **where)
    renamed = form(table("policy", column("area", id_="c_region"), column("seats", "int")))
    planned = plan_store(server.url, renamed, project=PROJECT, environment=TEST, **where)
    script = render_sql(
        planned,
        renamed,
        "postgresql" if server.backend == "postgresql" else "mssql",
        project=PROJECT,
        environment=TEST,
        release="r2",
        haute_version="1",
        restore_point=None,
        sql_schema="pricing",
    )
    recorded = "SELECT release FROM pricing.haute_store"

    # A column the SQL side added since takes the rename's name: the rename fails part way,
    # after its first phase, and the script leaves nothing changed and nothing open.
    server.run("ALTER TABLE pricing.policy ADD area BIGINT")
    with _script_run(server, script) as (raw, raised):
        assert raised is not None
        assert not _left_open(server, raw)
        assert [
            name for (name,) in server.read(_columns_in_order(server, "pricing", "policy"))
        ] == ["quote_reference", "quote_option", "region", "area"]
        assert server.read(recorded) == [(None,)]
    server.run("ALTER TABLE pricing.policy DROP COLUMN area")

    # Run whole, it brings the store to the release: the app opens it.
    with _script_run(server, script) as (raw, raised):
        assert raised is None and not _left_open(server, raw)
    assert server.read(recorded) == [("r2",)]
    with open_book(
        server.url, renamed, project=PROJECT, environment=TEST, haute_version="1", **where
    ) as book:
        assert book.page(actor="ana", view="team").quotes == []


@pytest.fixture(params=backends())
def any_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    """A database of the test's own on each backend the run has, SQLite's included."""
    with database(request.param, tmp_path) as target:
        yield target


def _write_waits(target: Target, statement: str) -> bool:
    """Whether *statement*, run on a connection of its own, waited out its lock timeout
    rather than landing."""
    if target.backend == "sqlite":
        raw = sqlite3.connect(target.url.removeprefix("sqlite:///"), timeout=1)
        try:
            raw.execute(statement)
            raw.commit()
        except sqlite3.OperationalError:
            return True
        finally:
            raw.close()
        return False
    engine = make_engine(target.url, sign_in=target.sign_in)
    try:
        with engine.connect() as connection:
            try:
                with connection.begin():
                    connection.exec_driver_sql(
                        "SET lock_timeout = '2s'"
                        if target.backend == "postgresql"
                        else "SET LOCK_TIMEOUT 2000"
                    )
                    connection.exec_driver_sql(statement)
            except DBAPIError:
                return True
        return False
    finally:
        engine.dispose()


def test_a_migrate_holds_writers_off_from_its_counts_to_its_ddl(
    any_store: Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = any_store
    base = form(table("policy", column("region"), column("note")))
    prepare_store(store.url, base, project=PROJECT, environment=TEST, sign_in=store.sign_in)
    with open_book(
        store.url, base, project=PROJECT, environment=TEST, haute_version="1", sign_in=store.sign_in
    ) as book:
        book.create(
            operation="operation-0001",
            actor="ana",
            values={"t_policy": [{"c_region": "north"}]},
            at=book.now(),
            reference="A-1",
        )
    waited: list[bool] = []
    counted = _migrate.counts

    def counting(*args: Any, **kwargs: Any) -> Any:
        found = counted(*args, **kwargs)
        # A save between the counts, which found note empty, and the retype: it must wait.
        waited.append(_write_waits(store, "UPDATE policy SET note = 'kept?'"))
        return found

    monkeypatch.setattr(_migrate, "counts", counting)
    retyped = form(table("policy", column("region"), column("note", "int")))
    made = prepare_store(
        store.url,
        retyped,
        project=PROJECT,
        environment=TEST,
        restore_point="backup-1",
        sign_in=store.sign_in,
    )

    assert waited == [True]
    assert ("retype column", "note") in {(step.kind, step.column) for step in made.steps}
    assert store.read("SELECT quote_reference, region, note FROM policy") == [
        ("A-1", "north", None)
    ]


def test_a_migrate_waiting_on_another_plans_against_the_tables_that_one_left(
    any_store: Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = any_store
    sign_in = store.sign_in
    prepare_store(store.url, form(POLICY), project=PROJECT, environment=TEST, sign_in=sign_in)
    renamed = form({**POLICY, "name": "policies"})
    first_counted = threading.Event()
    go_on = threading.Event()
    counted = _migrate.counts

    def counting(*args: Any, **kwargs: Any) -> Any:
        found = counted(*args, **kwargs)
        if threading.current_thread().name == "first":
            # The first migrate holds the store while the second starts and waits for it.
            first_counted.set()
            assert go_on.wait(30)
        return found

    monkeypatch.setattr(_migrate, "counts", counting)
    made: dict[str, Any] = {}

    def migrate(name: str) -> None:
        try:
            made[name] = prepare_store(
                store.url, renamed, project=PROJECT, environment=TEST, sign_in=sign_in
            )
        except Exception as exc:  # noqa: BLE001 - the test reads what each migrate met
            made[name] = exc

    first = threading.Thread(target=migrate, args=("first",), name="first")
    first.start()
    assert first_counted.wait(30)
    second = threading.Thread(target=migrate, args=("second",), name="second")
    second.start()
    # The second waits on the first's hold of the store, whatever it read before.
    second.join(timeout=1.0)
    assert second.is_alive()
    go_on.set()
    first.join(timeout=60)
    second.join(timeout=60)

    assert [(step.kind, step.table) for step in made["first"].steps] == [
        ("rename table", "policies")
    ]
    # The second plans against the tables the first left: nothing more to do.
    assert not isinstance(made["second"], Exception), made["second"]
    assert made["second"].steps == () and made["second"].refusals == ()


def test_a_sign_in_the_server_refuses_is_said_naming_no_server_or_user(server: Target) -> None:
    wrong = SignIn("password", username="haute_nobody", secret="Wrong-p4ss-only!")
    host = str(make_url(server.url).host)

    with pytest.raises(QuoteStoreEnvironmentError) as migrating:
        prepare_store(server.url, form(POLICY), project=PROJECT, environment=TEST, sign_in=wrong)
    with pytest.raises(QuoteStoreEnvironmentError) as starting:
        open_book(
            server.url,
            form(POLICY),
            project=PROJECT,
            environment=TEST,
            haute_version="1",
            sign_in=wrong,
        )

    for refused in (migrating, starting):
        said = str(refused.value)
        assert "could not be reached, or it refused the app's sign-in" in said
        assert host not in said and "haute_nobody" not in said


def test_a_started_app_refuses_a_sign_in_lacking_a_privilege_naming_it(server: Target) -> None:
    prepare_store(
        server.url, form(POLICY), project=PROJECT, environment=TEST, sign_in=server.sign_in
    )
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
    try:
        with pytest.raises(QuoteStoreMismatchError, match="INSERT on policy") as refused:
            open_book(
                server.url,
                form(POLICY),
                project=PROJECT,
                environment=TEST,
                haute_version="1",
                sign_in=SignIn("password", username=reader, secret=secret),
            )
        assert "SELECT on" not in str(refused.value)
    finally:
        if server.backend == "sqlserver":
            server.run(f"DROP USER {reader}")
            # The ODBC driver manager may pool the refused app's connection: end it first,
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


def test_a_started_app_refuses_an_exact_text_under_another_collation(sqlserver: Target) -> None:
    server = sqlserver
    prepare_store(
        server.url, form(POLICY), project=PROJECT, environment=TEST, sign_in=server.sign_in
    )
    server.run(
        "ALTER TABLE quote_details ALTER COLUMN saved_by NVARCHAR(640) "
        "COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL"
    )

    with pytest.raises(QuoteStoreMismatchError, match="saved_by compares under SQL_Latin1"):
        open_book(
            server.url,
            form(POLICY),
            project=PROJECT,
            environment=TEST,
            haute_version="1",
            sign_in=server.sign_in,
        )


def test_a_release_started_apart_from_its_project_signs_in_as_it_declares(
    server: Target, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    declared = StoreEnvironment(TEST, server.backend, "password", PASSWORD)
    release_dir = _write_release(tmp_path / "release", environments=(declared,))
    assert server.sign_in is not None
    for name, value in {
        "HAUTE_ENVIRONMENT": TEST,
        "HAUTE_STORE_URL": server.url,
        "STORE_USER": str(server.sign_in.username),
        "STORE_PASSWORD": str(server.sign_in.secret),
    }.items():
        monkeypatch.setenv(name, value)
    try:
        migrated = CliRunner().invoke(
            cli, ["app", "migrate", "--environment", TEST, "--release", str(release_dir)]
        )
        assert migrated.exit_code == 0, migrated.output
        assert server.url not in migrated.output
        assert str(server.sign_in.secret) not in migrated.output

        with TestClient(create_app(release_dir, store=hosted_store(release_dir))) as client:
            saved = Page(client).priced()
    finally:
        set_project_root(original)

    assert server.read("SELECT quote_reference, stage FROM quote_details") == [
        (saved["reference"], "draft")
    ]
