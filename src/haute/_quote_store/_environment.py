"""Where a hosted app's quote store is (specs/workbench): the environment a host or a CI job
names, among those the release declares, and the database the host's own environment says it
is at. No file a project versions says where a database is or holds a credential, so a pull
request cannot point an environment at another database; the URL resolved here is handed to
the store alone, never logged, stored or answered.

The variables are ``ENVIRONMENT_ENV``, naming the environment, and either the database's parts
(``STORE_SERVER_ENV`` and ``STORE_DATABASE_ENV``) or one whole connection URL
(``STORE_URL_ENV``), with, on a server, ``STORE_SCHEMA_ENV`` naming the SQL schema its tables
are in. A SQLite database is a file: its database variable is the file's path, and it has no
server and no schema. A server database's sign-in reads what it needs from the variables its
environment names (:class:`._providers.SignIn`), and its URL holds none of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from haute._credential_security import (
    CredentialMaterialError,
    is_credential_name,
    validate_credential_free_uri,
)
from haute._quote_store._providers import PROVIDERS, SignIn, provider_of, server_url
from haute._quote_store._store import QuoteStoreEnvironmentError, store_url
from haute._workbench_config import (
    ENVIRONMENT_ENV,
    FILE_PROVIDERS,
    STORE_DATABASE_ENV,
    STORE_SCHEMA_ENV,
    STORE_SERVER_ENV,
    STORE_URL_ENV,
    StoreEnvironment,
)

#: What each sign-in key's variable holds, as a refusal names it.
_HELD: Mapping[str, str] = {
    "username_env": "user name",
    "password_env": "password",
    "tenant_id_env": "tenant id",
    "client_id_env": "client id",
    "client_secret_env": "client secret",
}
#: ODBC attributes that would sign a connection in from its URL, which the environment's
#: sign-in does instead.
_ODBC_SIGN_IN = frozenset({"uid", "user id", "trusted_connection", "integrated security"})


@dataclass(frozen=True, slots=True)
class StoreLocation:
    """The quote store an app keeps its quotes in: its environment; its database's URL and the
    SQL schema its tables are in, the connection's default when None, neither ever shown; and
    how the app signs in, None for a file."""

    environment: str
    url: str = field(repr=False)
    sql_schema: str | None = field(default=None, repr=False)
    sign_in: SignIn | None = field(default=None, repr=False)


def _given(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _sign_in(environment: StoreEnvironment, environ: Mapping[str, str]) -> SignIn:
    """The sign-in *environment* declares, with what it needs read from the variables it
    names; refused naming a variable that holds nothing."""
    values: dict[str, str] = {}
    for key, variable in environment.variables.items():
        value = environ.get(variable, "")
        if not value.strip():
            raise QuoteStoreEnvironmentError(
                f"The {environment.name} store signs in with {environment.auth}, which reads "
                f"its {_HELD.get(key, key)} from {variable}: set it."
            )
        values[key] = value
    return SignIn(
        str(environment.auth),
        username=values.get("username_env"),
        client_id=values.get("client_id_env"),
        tenant_id=values.get("tenant_id_env"),
        secret=values.get("password_env") or values.get("client_secret_env"),
    )


def _file_location_url(environment: StoreEnvironment, url: str) -> None:
    """Refuse *url*, a file provider's whole URL, unless its engine can open it: a file's URL
    names no server, port or sign-in, so the engine's own refusal, which repeats the URL, is
    never met as the app opens its store."""
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):
        # A port that is no number is SQLAlchemy's ValueError.
        parsed = None
    if parsed is None or any((parsed.username, parsed.password, parsed.host, parsed.port)):
        raise QuoteStoreEnvironmentError(
            f"{STORE_URL_ENV} for the {environment.name} store is not a {environment.provider} "
            f"file's URL: {environment.provider}:/// and the file's path, naming no server."
        )


def _server_location_url(environment: StoreEnvironment, url: str) -> str:
    """*url*, a whole connection URL a host gives, once it holds no sign-in and names the
    driver of the provider *environment* declares; refused saying which, never showing it."""
    name = environment.name
    try:
        validate_credential_free_uri(url)
        provider = provider_of(url)
    except (CredentialMaterialError, ArgumentError, ValueError) as exc:
        if isinstance(exc, CredentialMaterialError):
            reason = "it holds a sign-in, which the environment's auth and its variables give"
        elif isinstance(exc, ArgumentError):
            # SQLAlchemy's own message would repeat the URL.
            reason = "it is not a connection URL"
        else:
            reason = str(exc)
        raise QuoteStoreEnvironmentError(
            f"{STORE_URL_ENV} for the {name} store: {reason}"
        ) from None
    if provider.name != environment.provider:
        raise QuoteStoreEnvironmentError(
            f"{STORE_URL_ENV} is not a {environment.provider} URL, the provider the {name} "
            "environment declares."
        )
    odbc = make_url(url).query.get("odbc_connect")
    for part in str(odbc or "").split(";"):
        key = part.split("=", 1)[0].strip()
        if key and (is_credential_name(key) or key.casefold() in _ODBC_SIGN_IN):
            raise QuoteStoreEnvironmentError(
                f"{STORE_URL_ENV} for the {name} store holds a sign-in ({key}), which the "
                "environment's auth and its variables give."
            )
    return url


def resolve_store(
    declared: Sequence[StoreEnvironment], name: str, environ: Mapping[str, str]
) -> StoreLocation:
    """The store of the environment *name*, one of those *declared*, at the database
    *environ*'s variables say it is at, signed in as the environment declares. Refused
    (:class:`QuoteStoreEnvironmentError`) for an environment not declared, naming those that
    are; a location given both as parts and as a URL, or not at all; a location its provider
    cannot take; and a sign-in whose variables hold nothing."""
    environment = next((each for each in declared if each.name == name), None)
    if environment is None:
        names = ", ".join(each.name for each in declared) or "none"
        raise QuoteStoreEnvironmentError(
            f"{ENVIRONMENT_ENV} names the environment {name!r}, which this release does not "
            f"declare; it declares {names} ([workbench.store.<name>] in haute.toml)."
        )
    server = _given(environ, STORE_SERVER_ENV)
    database = _given(environ, STORE_DATABASE_ENV)
    schema = _given(environ, STORE_SCHEMA_ENV)
    url = _given(environ, STORE_URL_ENV)
    if url is not None and (server, database) != (None, None):
        raise QuoteStoreEnvironmentError(
            f"Where the {name} store's database is was given twice: set {STORE_URL_ENV}, or "
            f"{STORE_SERVER_ENV} and {STORE_DATABASE_ENV}, not both."
        )
    if environment.provider in FILE_PROVIDERS:
        if server is not None or schema is not None:
            raise QuoteStoreEnvironmentError(
                f"The {name} store is a {environment.provider} file, which has no server and "
                f"no schema: set {STORE_DATABASE_ENV} to its path, or {STORE_URL_ENV}."
            )
        if url is not None:
            if not url.startswith(f"{environment.provider}:"):
                raise QuoteStoreEnvironmentError(
                    f"{STORE_URL_ENV} is not a {environment.provider} URL, the provider the "
                    f"{name} environment declares."
                )
            _file_location_url(environment, url)
            return StoreLocation(name, url)
        if database is None:
            raise QuoteStoreEnvironmentError(
                f"Where the {name} store's database is was not given: set {STORE_DATABASE_ENV} "
                f"to its file's path, or {STORE_URL_ENV}."
            )
        return StoreLocation(name, store_url(Path(database)))
    provider = PROVIDERS.get(environment.provider)
    if provider is None:
        raise QuoteStoreEnvironmentError(
            f"The {name} environment's provider, {environment.provider}, is not one this Haute "
            "connects to."
        )
    if url is not None:
        url = _server_location_url(environment, url)
    elif server is None or database is None:
        raise QuoteStoreEnvironmentError(
            f"Where the {name} store's database is was not given: set {STORE_SERVER_ENV} and "
            f"{STORE_DATABASE_ENV}, or {STORE_URL_ENV}."
        )
    else:
        url = server_url(provider, server, database)
    return StoreLocation(name, url, sql_schema=schema, sign_in=_sign_in(environment, environ))
