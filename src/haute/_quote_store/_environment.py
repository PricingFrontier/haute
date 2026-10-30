"""Where a hosted app's quote store is (specs/workbench): the environment a host or a CI job
names, among those the release declares, and the database the host's own environment says it
is at. No file a project versions says where a database is or holds a credential, so a pull
request cannot point an environment at another database; the URL resolved here is handed to
the store alone, never logged, stored or answered.

The variables are ``ENVIRONMENT_ENV``, naming the environment, and either the database's parts
(``STORE_SERVER_ENV``, ``STORE_DATABASE_ENV`` and, optionally, ``STORE_SCHEMA_ENV``) or one
whole connection URL (``STORE_URL_ENV``). A SQLite database is a file: its database variable
is the file's path, and it has no server and no schema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class StoreLocation:
    """The quote store an app keeps its quotes in: its environment, and its database's URL,
    which is never shown."""

    environment: str
    url: str = field(repr=False)


def _given(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def resolve_store(
    declared: Sequence[StoreEnvironment], name: str, environ: Mapping[str, str]
) -> StoreLocation:
    """The store of the environment *name*, one of those *declared*, at the database
    *environ*'s variables say it is at. Refused (:class:`QuoteStoreEnvironmentError`) for an
    environment not declared, naming those that are; a location given both as parts and as a
    URL, or not at all; and a location its provider cannot take."""
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
    if url is not None and (server, database, schema) != (None, None, None):
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
            return StoreLocation(name, url)
        if database is None:
            raise QuoteStoreEnvironmentError(
                f"Where the {name} store's database is was not given: set {STORE_DATABASE_ENV} "
                f"to its file's path, or {STORE_URL_ENV}."
            )
        return StoreLocation(name, store_url(Path(database)))
    raise QuoteStoreEnvironmentError(
        f"The {name} environment's provider, {environment.provider}, is not one this Haute "
        "connects to."
    )
