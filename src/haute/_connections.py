"""``connections.toml``: the databases the editor and ``haute app serve`` reach on the analyst's
machine (specs/workbench).

The file sits at the project root and is committed with the project, so the team shares one
set of connections; it holds no secret. Each connection is a table under its name, as an
environment's name is::

    [dev]
    provider = "sqlserver"
    server = "tcp:pricing-dev.database.windows.net,1433"
    database = "pricing_dev"
    schema = "quotes"                 # optional; the connection's default when unsaid
    auth = "password"
    username_env = "HAUTE_DEV_USERNAME"
    password_env = "HAUTE_DEV_PASSWORD"

    [local]
    provider = "sqlite"
    database = "data/quotes.sqlite"   # the file's path from the project root

A server's connection says how the analyst signs in, ``auth``, one of
:data:`CONNECTION_SIGN_INS`, by the names of the variables that hold what it needs, whose
values are read from the clone's ``.env``, which git ignores, or from the environment, which
wins (:func:`project_environ`). ``entra`` is the analyst's own Microsoft Entra ID sign-in, made
in the browser; ``managed-identity``, a hosted app's, is refused. A key that would hold a
secret's value is refused naming the key that names its variable, and any other key by name.
A server or a database may be left empty, as ``haute init --workbench`` writes them: it is
refused as the connection is used (``haute._quote_store._environment.connected_store``).

A connection named as an environment ``[workbench.store.<name>]`` declares is that
environment's. A hosted app and a release never read this file. It is read from disk each
time it is used, so an edit reaches the next use.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from haute._workbench_config import (
    ENVIRONMENT_NAME,
    FILE_PROVIDERS,
    ROLE_NAMED,
    SECRET_KEYS,
    STORE_PROVIDERS,
    VARIABLE_NAME,
    WorkbenchConfigError,
)

#: The file, at the project root.
CONNECTIONS_FILE = "connections.toml"
#: How the analyst signs in to a server database, each with the keys naming the variables
#: that hold what it needs: a password, a service principal, or their own Entra ID sign-in.
CONNECTION_SIGN_INS: Mapping[str, tuple[str, ...]] = {
    "password": ("username_env", "password_env"),
    "service-principal": ("tenant_id_env", "client_id_env", "client_secret_env"),
    "entra": (),
}
#: The sign-ins a server's connection signs in through Microsoft Entra ID with, which on a
#: provider of ``ROLE_NAMED`` also name the role they take.
_ENTRA = frozenset({"service-principal", "entra"})
#: Where a server's connection names its database.
_LOCATION_KEYS = ("server", "database", "schema")
#: Keys that would put a secret's value, or a whole connection string, in the file, and the
#: key that names its variable or says where the database is instead.
_REFUSED_KEYS: Mapping[str, str] = {
    **SECRET_KEYS,
    "connection_string": "server and database",
    "url": "server and database",
}


class ConnectionsError(WorkbenchConfigError):
    """``connections.toml`` cannot be read as connections: the message names what to fix."""


@dataclass(frozen=True, slots=True)
class Connection:
    """A connection as ``connections.toml`` holds it: its name, its provider, where its
    database is (a server's *server*, *database* and SQL *schema*; a SQLite file's path from
    the project root as *database*), and how the analyst signs in, by the names of the
    variables holding what that needs, never their values. *server* and *database* are empty
    while the file leaves them so."""

    name: str
    provider: str
    database: str
    server: str | None = None
    schema: str | None = None
    #: One of ``CONNECTION_SIGN_INS``; None for a file, which takes no sign-in.
    auth: str | None = None
    #: Each of the sign-in's keys and the variable it names, ``password_env`` to
    #: ``HAUTE_DEV_PASSWORD``.
    variables: Mapping[str, str] = field(default_factory=dict)

    @property
    def where(self) -> str:
        """The connection as a message names it: the file and its table."""
        return f"{CONNECTIONS_FILE} [{self.name}]"


def read_connections(project_root: Path) -> dict[str, Connection]:
    """The connections *project_root*'s ``connections.toml`` holds, by name, in the file's
    order; none without the file. :class:`ConnectionsError` for a file that cannot be read or
    parsed, or a connection that is not as the module docstring describes."""
    path = project_root / CONNECTIONS_FILE
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConnectionsError(f"{CONNECTIONS_FILE} could not be read: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConnectionsError(
            f"{CONNECTIONS_FILE} is malformed and could not be parsed: {exc}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConnectionsError(f"{CONNECTIONS_FILE} is not UTF-8 text: {exc}") from exc
    connections: dict[str, Connection] = {}
    for name, settings in data.items():
        if not ENVIRONMENT_NAME.fullmatch(name):
            raise ConnectionsError(
                f"{CONNECTIONS_FILE} [{name}]: a connection's name is up to 32 lower-case "
                "letters, digits, dashes and underscores, starting with a letter"
            )
        if not isinstance(settings, dict):
            raise ConnectionsError(f"{CONNECTIONS_FILE} [{name}] must be a table")
        connections[name] = _connection(name, settings)
    return connections


def _text(settings: Mapping[str, object], key: str, at: str, *, optional: bool) -> str | None:
    value = settings.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str):
        # Never the value: one put in the wrong key may be a secret.
        raise ConnectionsError(f"{at}.{key} must be text")
    return value.strip()


def connection_sign_in_keys(provider: str, auth: str) -> tuple[str, ...]:
    """The keys a server's connection on *provider* signed in to with *auth*, one of
    ``CONNECTION_SIGN_INS``, names, each the variable that holds what the sign-in needs."""
    keys = CONNECTION_SIGN_INS[auth]
    if provider in ROLE_NAMED and auth in _ENTRA:
        # The role a PostgreSQL server maps the Entra identity to, by name.
        keys = (*keys, "username_env")
    return keys


def _connection(name: str, settings: dict[str, object]) -> Connection:
    at = f"{CONNECTIONS_FILE} [{name}]"
    for key in settings:
        if key in _REFUSED_KEYS:
            raise ConnectionsError(
                f"{at}.{key} would hold a secret's value or a connection string: name the "
                f"variable that holds it ({_REFUSED_KEYS[key]}) instead"
            )
    provider = settings.get("provider")
    if not isinstance(provider, str) or provider not in STORE_PROVIDERS:
        raise ConnectionsError(f"{at}.provider must be one of {', '.join(sorted(STORE_PROVIDERS))}")
    if provider in FILE_PROVIDERS:
        unknown = sorted(set(settings) - {"provider", "database"})
        if unknown:
            raise ConnectionsError(
                f"{at} has unknown key(s): {', '.join(unknown)}; a {provider} file's keys are "
                "provider and database, its path from the project root, and it takes no sign-in"
            )
        database = _text(settings, "database", at, optional=False)
        return Connection(name=name, provider=provider, database=str(database))
    auth = settings.get("auth")
    if auth == "managed-identity":
        raise ConnectionsError(
            f"{at}.auth: managed-identity is a hosted app's sign-in; on this machine sign in "
            f"with {', '.join(CONNECTION_SIGN_INS)}"
        )
    if not isinstance(auth, str) or auth not in CONNECTION_SIGN_INS:
        raise ConnectionsError(f"{at}.auth must be one of {', '.join(CONNECTION_SIGN_INS)}")
    keys = connection_sign_in_keys(provider, auth)
    known = {"provider", "auth", *_LOCATION_KEYS, *keys}
    unknown = sorted(set(settings) - known)
    if unknown:
        raise ConnectionsError(
            f"{at} has unknown key(s): {', '.join(unknown)}; the keys are "
            + ", ".join(sorted(known))
        )
    variables: dict[str, str] = {}
    for key in keys:
        variable = settings.get(key)
        if not isinstance(variable, str) or not VARIABLE_NAME.fullmatch(variable):
            raise ConnectionsError(
                f"{at}.{key} must name the environment variable that holds it, letters, "
                "digits and underscores"
            )
        variables[key] = variable
    schema = _text(settings, "schema", at, optional=True)
    return Connection(
        name=name,
        provider=provider,
        server=_text(settings, "server", at, optional=False),
        database=str(_text(settings, "database", at, optional=False)),
        schema=schema or None,
        auth=auth,
        variables=variables,
    )


def project_environ(project_root: Path) -> dict[str, str]:
    """The variables a connection's sign-in reads its values from: the clone's ``.env``, read
    afresh, under the process's own environment, which wins."""
    path = project_root / ".env"
    found: dict[str, str] = {}
    if path.is_file():
        from dotenv import dotenv_values

        found = {key: value for key, value in dotenv_values(path).items() if value is not None}
    return {**found, **os.environ}
