"""The quote store on SQL Server and PostgreSQL (specs/workbench): where a server environment's
database is and how the app signs in to it, read from the host's variables with nothing of
either shown; each connection signed in as the release declares, an Entra ID token asked for
as it is made; the store's types pinned on each engine; names past an engine's limit refused
in the plan; an empty column's type changed in place where the engine can; the image's
drivers; and, on each server the environment names (tests/_store_servers.py), the planner in a
SQL schema of the store's own, the startup check of collations and privileges, and a release
started apart from its project signing in as it declares."""

from __future__ import annotations

import struct
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mssql, postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateTable

from haute._credential_security import validate_credential_free_uri
from haute._quote_store import (
    QuoteStoreEnvironmentError,
    QuoteStoreMismatchError,
    SignIn,
    offline_plan,
    open_book,
    plan_store,
    prepare_store,
    render_sql,
    resolve_store,
)
from haute._quote_store._migrate import plan
from haute._quote_store._providers import (
    PROVIDERS,
    SQL_COPT_SS_ACCESS_TOKEN,
    connect_hook,
    make_server_engine,
    missing_privileges,
    retypes_in_place,
)
from haute._quote_store._schema import store_schema, structure
from haute._sandbox import _get_project_root, set_project_root
from haute._workbench_app import create_app, hosted_store, store_dependencies
from haute._workbench_config import StoreEnvironment
from haute._workbench_form import FormSpec, with_quote_details
from haute.cli import cli
from tests._store_servers import Target, configured, database
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
    script = render_sql(
        made,
        retyped,
        "mssql",
        project=PROJECT,
        environment=TEST,
        release="r1",
        haute_version="1",
        restore_point="backup-1",
        sql_schema="pricing",
    )

    # A float SQL Server converts in place; a date it cannot, so the empty column is replaced.
    assert "ALTER TABLE pricing.policy ALTER COLUMN value BIGINT NULL" in script
    assert "ALTER TABLE pricing.policy DROP COLUMN start" in script
    assert "EXEC sp_rename 'pricing.policy.haute_tmp_" in script
    assert script.startswith("-- Steps only a restore reverses: take the restore point backup-1")


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


def test_a_server_plan_is_shown_as_the_sql_that_engine_runs(server: Target) -> None:
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

    planned = plan_store(server.url, retyped, project=PROJECT, environment=TEST, **where)
    script = render_sql(
        planned,
        retyped,
        "postgresql" if server.backend == "postgresql" else "mssql",
        project=PROJECT,
        environment=TEST,
        release="r2",
        haute_version="1",
        restore_point="backup-2",
        sql_schema="pricing",
    )
    if server.backend == "postgresql":
        assert (
            "ALTER TABLE pricing.policy ALTER COLUMN note TYPE BIGINT USING NULL::BIGINT" in script
        )
    else:
        assert "ALTER TABLE pricing.policy DROP COLUMN start" in script

    prepare_store(
        server.url, retyped, project=PROJECT, environment=TEST, restore_point="backup-2", **where
    )
    with open_book(
        server.url, retyped, project=PROJECT, environment=TEST, haute_version="1", **where
    ) as book:
        made = book.create(
            operation="operation-0001",
            actor="ana",
            values={"t_policy": [{f"c_{moved}": "7"}]},
            at=book.now(),
            reference="A-1",
        )
        assert made.values["t_policy"][0][f"c_{moved}"] == "7"


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
            server.run(f"DROP LOGIN {reader}")
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
