"""The engines the quote store runs on (specs/workbench).

``sqlite`` is dev's, a file on the analyst's laptop. ``sqlserver`` is SQL Server 2019 and
later, Azure SQL Database and Azure SQL Managed Instance, through pyodbc and Microsoft ODBC
Driver 18 (:data:`SQLSERVER_DRIVER`); ``postgresql`` is PostgreSQL and its hosted forms (Azure
Database for PostgreSQL, Amazon RDS and Aurora, Cloud SQL, Databricks Lakebase), through
psycopg. Each enforces keys, runs a transaction across tables and runs DDL inside one, so a
migrate that fails part way changes nothing.

A :class:`Provider` is what the store needs of one family past SQLAlchemy Core: the URL it
makes from the server and database a host names (:func:`server_url`), how an app signs in to
it (:class:`SignIn`, through :func:`make_server_engine`'s connect hook), the longest name it
keeps whole (:func:`name_problems`), whether it changes the type of a column holding no value
in place (:func:`retypes_in_place`), and the privileges a started app checks it holds
(:func:`missing_privileges`). The store's types on each engine are pinned where its tables are
derived (:mod:`._schema`), so the SQL a plan prints is the SQL the connected engine runs. A
sign-in's secret or token is handed to each connection as it is made: it is never in a URL,
and no error or log names it.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.engine import URL, make_url

if TYPE_CHECKING:
    from haute._quote_store._schema import StoreSchema

#: The ODBC driver ``sqlserver`` connects through, which a release's image installs.
SQLSERVER_DRIVER = "ODBC Driver 18 for SQL Server"
#: The pyodbc connection attribute an Entra ID access token is handed in.
SQL_COPT_SS_ACCESS_TOKEN = 1256
#: The sign-ins that go through Microsoft Entra ID.
ENTRA_SIGN_INS = frozenset({"managed-identity", "service-principal"})
#: The package an Entra ID sign-in needs besides the driver.
ENTRA_PACKAGE = "azure-identity"


class TokenCredential(Protocol):
    """What signs in through Entra ID: one of azure-identity's credentials, or a test's."""

    def get_token(self, *scopes: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class SignIn:
    """How an app signs in to a server database, as its environment declares it and its
    host's variables hold what it needs: ``managed-identity`` (a user-assigned identity's
    *client_id*, or none for the system-assigned one), ``service-principal`` (*tenant_id*,
    *client_id* and its *secret*) or ``password`` (*username* and its *secret*). *username*
    is also the role an Entra ID sign-in takes on PostgreSQL. The secret is never shown."""

    auth: str
    username: str | None = None
    client_id: str | None = None
    tenant_id: str | None = None
    secret: str | None = field(default=None, repr=False)

    @property
    def entra(self) -> bool:
        return self.auth in ENTRA_SIGN_INS

    def credential(self) -> TokenCredential:
        """The Entra ID credential this sign-in takes tokens from."""
        try:
            from azure.identity import ClientSecretCredential, ManagedIdentityCredential
        except ImportError as exc:  # pragma: no cover - the extras install it
            raise ImportError(
                f"Signing in with {self.auth} needs the {ENTRA_PACKAGE} package: install "
                "haute[sqlserver] or haute[postgresql]."
            ) from exc
        if self.auth == "service-principal":
            return ClientSecretCredential(
                str(self.tenant_id), str(self.client_id), str(self.secret)
            )
        return ManagedIdentityCredential(client_id=self.client_id)


@dataclass(frozen=True, slots=True)
class Provider:
    """One family of engines the store runs on."""

    name: str
    #: The SQLAlchemy dialect its SQL is written in, a plan's printed SQL with it.
    dialect: str
    #: The SQLAlchemy driver its URL names.
    driver: str
    #: The longest table or column name it keeps whole, None for none Haute reaches.
    name_limit: int | None
    #: Whether that limit counts UTF-8 bytes rather than characters.
    limit_in_bytes: bool
    #: What an Entra ID token is asked for, None where no sign-in takes one.
    token_scope: str | None
    #: The distributions its driver needs in a release's image, pinned as installed here.
    packages: tuple[str, ...]


PROVIDERS: Mapping[str, Provider] = {
    "sqlite": Provider("sqlite", "sqlite", "pysqlite", None, False, None, ()),
    "sqlserver": Provider(
        "sqlserver",
        "mssql",
        "pyodbc",
        128,
        False,
        "https://database.windows.net/.default",
        ("pyodbc",),
    ),
    "postgresql": Provider(
        "postgresql",
        "postgresql",
        "psycopg",
        63,
        True,
        "https://ossrdbms-aad.database.windows.net/.default",
        ("psycopg", "psycopg-binary"),
    ),
}
#: Each provider by the SQLAlchemy dialect its URLs name.
_BY_DIALECT: Mapping[str, Provider] = {each.dialect: each for each in PROVIDERS.values()}


def dialect_provider(dialect: str) -> Provider:
    """The provider whose engines speak the SQLAlchemy dialect *dialect*."""
    return _BY_DIALECT[dialect]


def provider_of(url: str) -> Provider:
    """The provider whose engine *url* names: :class:`ValueError` for a dialect the store does
    not run on or a driver its provider does not connect through."""
    parsed = make_url(url)
    provider = _BY_DIALECT.get(parsed.get_backend_name())
    if provider is None:
        raise ValueError(
            f"The quote store does not run on {parsed.get_backend_name()}: its providers are "
            + ", ".join(PROVIDERS)
            + "."
        )
    if provider.name != "sqlite" and parsed.get_driver_name() != provider.driver:
        raise ValueError(
            f"A {provider.name} store connects through {provider.driver}: the URL names "
            f"{provider.dialect}+{provider.driver}, not {parsed.drivername}."
        )
    return provider


def _odbc_value(value: str) -> str:
    """*value* as an ODBC connection string holds it, braced when it would otherwise end the
    attribute."""
    if any(mark in value for mark in ";{}=") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


def server_url(provider: Provider, server: str, database: str) -> str:
    """The URL of *provider*'s database *database* on *server*, as a host names them: the
    server as the engine's own clients take it (``tcp:name,1433`` on SQL Server, ``host:port``
    on PostgreSQL), with no sign-in in it."""
    if provider.name == "sqlserver":
        odbc = ";".join(
            [
                f"Driver={{{SQLSERVER_DRIVER}}}",
                f"Server={_odbc_value(server)}",
                f"Database={_odbc_value(database)}",
                "Encrypt=yes",
            ]
        )
        url = URL.create("mssql+pyodbc", query={"odbc_connect": odbc})
    else:
        host, port = server, None
        name, _, number = server.partition(":")
        if number.isdigit():
            host, port = name, int(number)
        url = URL.create("postgresql+psycopg", host=host, port=port, database=database)
    return url.render_as_string(hide_password=False)


def _without(odbc: str, *keys: str) -> str:
    """The ODBC connection string *odbc* without the attributes *keys*, whatever their case."""
    dropped = {key.casefold() for key in keys}
    kept = [
        part for part in odbc.split(";") if part.split("=", 1)[0].strip().casefold() not in dropped
    ]
    return ";".join(kept)


def _access_token(token: str) -> bytes:
    """*token* as SQL Server's ODBC driver takes one: its UTF-16-LE bytes after their length."""
    raw = token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


def connect_hook(
    provider: Provider, sign_in: SignIn, credential: TokenCredential | None = None
) -> Callable[[Any, Any, list[Any], dict[str, Any]], None]:
    """SQLAlchemy's ``do_connect`` hook signing each new connection to *provider*'s database
    in as *sign_in* says: an Entra ID token asked of *credential* (the sign-in's own unless
    given) as the connection is made, which the credential renews as it nears its end, or the
    password. On SQL Server the token goes in the driver's access-token attribute, and the
    integrated sign-in SQLAlchemy asks for when a URL names no user is taken out."""
    if sign_in.entra and credential is None:
        credential = sign_in.credential()
    scope = str(provider.token_scope)

    def secret() -> str:
        if credential is not None:
            return str(credential.get_token(scope).token)
        return str(sign_in.secret)

    def connect(_dialect: Any, _record: Any, cargs: list[Any], cparams: dict[str, Any]) -> None:
        if provider.name == "sqlserver":
            odbc = _without(str(cargs[0]), "Trusted_Connection", "UID", "PWD", "Authentication")
            if sign_in.entra:
                cparams["attrs_before"] = {SQL_COPT_SS_ACCESS_TOKEN: _access_token(secret())}
            else:
                odbc += f";UID={_odbc_value(str(sign_in.username))};PWD={_odbc_value(secret())}"
            cargs[0] = odbc
        else:
            cparams["user"] = sign_in.username
            cparams["password"] = secret()

    return connect


def make_server_engine(
    url: str, sign_in: SignIn | None, *, credential: TokenCredential | None = None
) -> Engine:
    """An engine on the server database at *url*, each connection signed in as *sign_in* says
    and checked before it is handed out, since a hosted database closes idle ones."""
    provider = provider_of(url)
    engine = create_engine(url, pool_pre_ping=True)
    if sign_in is not None:
        event.listen(engine, "do_connect", connect_hook(provider, sign_in, credential))
    return engine


def name_problems(provider: Provider, names: Iterable[str]) -> list[str]:
    """Each of *names* longer than *provider* keeps whole, which it would refuse or shorten."""
    if provider.name_limit is None:
        return []
    unit = "bytes" if provider.limit_in_bytes else "characters"
    problems: list[str] = []
    for name in dict.fromkeys(names):
        size = len(name.encode("utf-8")) if provider.limit_in_bytes else len(name)
        if size > provider.name_limit:
            problems.append(
                f"the name {name} is {size} {unit} long, past the {provider.name_limit} "
                f"{provider.name} keeps whole: shorten it"
            )
    return problems


#: The column types SQL Server cannot convert a date to or from, even with no value.
_NOT_FROM_DATE = frozenset({"int", "float", "bool"})


def retypes_in_place(provider: Provider, was: str, now: str) -> bool:
    """Whether *provider* changes a column holding no value from the type *was* to *now* in
    place: PostgreSQL always, taking every value as null; SQL Server where it converts the
    pair, never between a date and a number or True/false; SQLite never, its column replaced."""
    if provider.name == "postgresql":
        return True
    if provider.name == "sqlserver":
        return not ({was, now} & {"date"} and {was, now} & _NOT_FROM_DATE)
    return False


def _privileges(schema: StoreSchema) -> list[tuple[str, str]]:
    """Each table of *schema* with each privilege a started app needs on it: quote_details read,
    written and changed, every other table of the form's read, written and emptied, the
    activity read and written, and haute_store read."""
    needed: list[tuple[str, str]] = []
    for stored in schema.tables:
        kinds = (
            ("SELECT", "INSERT", "UPDATE")
            if stored is schema.details
            else (
                "SELECT",
                "INSERT",
                "DELETE",
            )
        )
        needed.extend((stored.table.name, kind) for kind in kinds)
    needed.extend((schema.activity.name, kind) for kind in ("SELECT", "INSERT"))
    needed.append((schema.store.name, "SELECT"))
    return needed


def _quoted(name: str, open_: str, close: str) -> str:
    return open_ + name.replace(close, close + close) + close


def missing_privileges(connection: Connection, schema: StoreSchema) -> list[str]:
    """Each privilege the sign-in on *connection* lacks that a started app needs on *schema*'s
    tables, as ``SELECT on policy``; none on SQLite, a file whose reader may do anything."""
    dialect = connection.dialect.name
    if dialect not in ("mssql", "postgresql"):
        return []
    open_, close = ("[", "]") if dialect == "mssql" else ('"', '"')
    probe = text(
        "SELECT HAS_PERMS_BY_NAME(:name, 'OBJECT', :kind)"
        if dialect == "mssql"
        else "SELECT has_table_privilege(:name, :kind)"
    )
    owner = schema.metadata.schema
    lacking: list[str] = []
    for table, kind in _privileges(schema):
        name = _quoted(table, open_, close)
        if owner:
            name = _quoted(owner, open_, close) + "." + name
        if not connection.execute(probe, {"name": name, "kind": kind}).scalar():
            lacking.append(f"{kind} on {table}")
    return lacking
