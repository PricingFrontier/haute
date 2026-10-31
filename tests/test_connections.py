"""connections.toml (specs/workbench): the databases the editor and ``haute app serve`` reach on
the analyst's machine, each a connection naming its provider, where its database is and the
variables its sign-in reads, whose values come from the clone's .env under the environment;
and the store of a declared environment resolved through its connection, refused naming what
to set, never a server, a database or a secret."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from haute._connections import (
    CONNECTIONS_FILE,
    Connection,
    ConnectionsError,
    project_environ,
    read_connections,
)
from haute._quote_store import QuoteStoreEnvironmentError, _providers, connected_store, store_url
from haute._quote_store._environment import connection_of
from haute._workbench_config import StoreEnvironment, WorkbenchConfig

SQLSERVER_DEV = """\
[dev]
provider = "sqlserver"
server = "tcp:pricing-dev.example.net,1433"
database = "pricing_dev"
schema = "quotes"
auth = "password"
username_env = "HAUTE_DEV_USERNAME"
password_env = "HAUTE_DEV_PASSWORD"
"""


def write(root: Path, text: str) -> None:
    (root / CONNECTIONS_FILE).write_bytes(text.encode("utf-8"))


def config(root: Path, *environments: StoreEnvironment) -> WorkbenchConfig:
    return WorkbenchConfig(
        enabled=True, project_root=root, form="rating/workbench", store=environments
    )


DEV_SQLSERVER = StoreEnvironment("dev", "sqlserver", "managed-identity")
DEV_SQLITE = StoreEnvironment("dev", "sqlite")


def test_a_project_without_the_file_has_no_connections(tmp_path: Path) -> None:
    assert read_connections(tmp_path) == {}


def test_each_connection_is_read_in_the_file_s_order(tmp_path: Path) -> None:
    write(tmp_path, SQLSERVER_DEV + '\n[local]\nprovider = "sqlite"\ndatabase = "data/q.sqlite"\n')

    read = read_connections(tmp_path)

    assert list(read) == ["dev", "local"]
    assert read["dev"] == Connection(
        name="dev",
        provider="sqlserver",
        server="tcp:pricing-dev.example.net,1433",
        database="pricing_dev",
        schema="quotes",
        auth="password",
        variables={"username_env": "HAUTE_DEV_USERNAME", "password_env": "HAUTE_DEV_PASSWORD"},
    )
    assert read["local"] == Connection(name="local", provider="sqlite", database="data/q.sqlite")


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (SQLSERVER_DEV + 'password = "hunter2"\n', "[dev].password would hold a secret's value"),
        (SQLSERVER_DEV + 'url = "mssql+pyodbc://x"\n', "name the variable that holds it (server"),
        (
            SQLSERVER_DEV.replace('auth = "password"', 'auth = "managed-identity"'),
            "managed-identity is a hosted app's sign-in",
        ),
        (SQLSERVER_DEV + 'port = "1433"\n', "unknown key(s): port"),
        (SQLSERVER_DEV.replace("sqlserver", "oracle"), "[dev].provider must be one of"),
        (
            SQLSERVER_DEV.replace('"HAUTE_DEV_PASSWORD"', '"not a name"'),
            "must name the environment",
        ),
        ('[Dev]\nprovider = "sqlite"\ndatabase = "q.sqlite"\n', "a connection's name is up to 32"),
        (
            '[dev]\nprovider = "sqlite"\ndatabase = "q.sqlite"\nauth = "password"\n',
            "takes no sign-in",
        ),
        ("[dev\n", "malformed"),
    ],
)
def test_a_connection_that_is_not_as_the_file_takes_it_is_refused_naming_why(
    tmp_path: Path, text: str, said: str
) -> None:
    write(tmp_path, text)

    with pytest.raises(ConnectionsError) as refused:
        read_connections(tmp_path)

    assert said in str(refused.value)
    assert "hunter2" not in str(refused.value)


def test_an_entra_sign_in_on_postgresql_names_the_role_it_takes(tmp_path: Path) -> None:
    entra = '[dev]\nprovider = "postgresql"\nserver = "db:5432"\ndatabase = "q"\nauth = "entra"\n'
    write(tmp_path, entra)
    with pytest.raises(ConnectionsError, match="username_env"):
        read_connections(tmp_path)

    write(tmp_path, entra + 'username_env = "HAUTE_DEV_ROLE"\n')
    assert read_connections(tmp_path)["dev"].variables == {"username_env": "HAUTE_DEV_ROLE"}


def test_the_sign_in_s_values_come_from_env_under_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_bytes(b"HAUTE_DEV_USERNAME=from-env-file\nHAUTE_DEV_PASSWORD=pw\n")
    monkeypatch.setenv("HAUTE_DEV_USERNAME", "from-the-environment")

    environ = project_environ(tmp_path)

    assert environ["HAUTE_DEV_USERNAME"] == "from-the-environment"
    assert environ["HAUTE_DEV_PASSWORD"] == "pw"


# ── An environment's store through its connection ───────────────────────


def test_a_sqlite_connection_is_the_file_at_its_path_from_the_project_root(tmp_path: Path) -> None:
    write(tmp_path, '[dev]\nprovider = "sqlite"\ndatabase = ".haute/q.sqlite"\n')

    store = connected_store(config(tmp_path, DEV_SQLITE), "dev", {})

    assert store.environment == "dev"
    assert store.url == store_url(tmp_path / ".haute" / "q.sqlite")
    assert store.sign_in is None
    assert store.named_by == "connections.toml [dev]"


def test_a_server_connection_signs_in_from_its_variables_holding_nothing_in_its_url(
    tmp_path: Path,
) -> None:
    write(tmp_path, SQLSERVER_DEV)
    environ = {"HAUTE_DEV_USERNAME": "ana", "HAUTE_DEV_PASSWORD": "s3cret!"}

    store = connected_store(config(tmp_path, DEV_SQLSERVER), "dev", environ)

    assert "pricing_dev" in store.url
    assert "s3cret!" not in store.url and "ana" not in store.url
    assert store.sql_schema == "quotes"
    assert store.sign_in is not None
    assert (store.sign_in.auth, store.sign_in.username, store.sign_in.secret) == (
        "password",
        "ana",
        "s3cret!",
    )
    assert store.named_by == (
        "connections.toml [dev] and the variables its sign-in reads "
        "(HAUTE_DEV_USERNAME, HAUTE_DEV_PASSWORD)"
    )
    assert "s3cret!" not in repr(store)


@pytest.mark.parametrize(
    ("environments", "text", "environ", "said"),
    [
        ((), SQLSERVER_DEV, {}, "haute.toml declares no dev environment"),
        ((DEV_SQLSERVER,), None, {}, "connections.toml has no [dev] connection"),
        ((DEV_SQLSERVER,), '[prod]\nprovider = "sqlite"\ndatabase = "p"\n', {}, "has no [dev]"),
        ((DEV_SQLITE,), SQLSERVER_DEV, {}, "is a sqlserver connection, but the dev environment"),
        (
            (DEV_SQLSERVER,),
            SQLSERVER_DEV.replace('"tcp:pricing-dev.example.net,1433"', '""'),
            {},
            "connections.toml [dev].server is empty: set it",
        ),
        (
            (DEV_SQLSERVER,),
            SQLSERVER_DEV,
            {"HAUTE_DEV_USERNAME": "ana"},
            "reads its password from HAUTE_DEV_PASSWORD: set it in .env",
        ),
    ],
)
def test_an_environment_its_connection_cannot_place_is_refused_naming_what_to_set(
    tmp_path: Path,
    environments: tuple[StoreEnvironment, ...],
    text: str | None,
    environ: dict[str, str],
    said: str,
) -> None:
    if text is not None:
        write(tmp_path, text)

    with pytest.raises(QuoteStoreEnvironmentError) as refused:
        connected_store(config(tmp_path, *environments), "dev", environ)

    assert said in str(refused.value)
    assert "pricing-dev.example.net" not in str(refused.value)


def test_a_connection_s_schema_is_read_without_its_sign_in(tmp_path: Path) -> None:
    write(tmp_path, SQLSERVER_DEV)

    assert connection_of(config(tmp_path, DEV_SQLSERVER), "dev").schema == "quotes"


def test_the_analyst_s_entra_sign_in_is_made_once_in_the_browser_for_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[Any] = []

    class Browser:
        def __init__(self) -> None:
            made.append(self)

        def get_token(self, *scopes: str) -> Any:
            raise AssertionError("no token is asked for here")

    import azure.identity

    monkeypatch.setattr(azure.identity, "InteractiveBrowserCredential", Browser)
    _providers._analyst_credential.cache_clear()
    try:
        first = _providers.SignIn("entra").credential()
        second = _providers.SignIn("entra", username="role").credential()
    finally:
        _providers._analyst_credential.cache_clear()

    assert first is second
    assert len(made) == 1
