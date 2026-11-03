"""The ``[workbench]`` table of ``haute.toml``: whether the project's workbench is enabled
(specs/workbench).

A project's workbench is a fact about the project, so it is recorded with the project's
other facts, versioned and reviewed with them::

    [workbench]
    enabled = true

    [workbench.app]
    host = "azure-container-apps"   # optional; where the underwriter app runs
    edit_role = "Quotes.Edit"        # optional; who may change quotes in it

    [workbench.store.dev]           # optional; an environment of the quote store
    provider = "sqlserver"
    auth = "managed-identity"

Without the table the project has no workbench. With it, ``enabled`` is required; ``app``,
when given, holds ``host``, one of ``APP_HOSTS``, and ``edit_role``, the role the host signs
in with which an underwriter may change quotes, every other one reading them. ``store``
declares the environments of the underwriter app's quote store (``dev``, ``staging`` and
``prod`` in a new project), each a table under its name with its database's ``provider``,
one of ``STORE_PROVIDERS``, and, for a database that takes one, how a hosted app signs in to
it (``auth``, one of ``STORE_SIGN_INS``, with the names of the variables holding what it
needs). Where each environment's database is, and every secret, comes from the host's own
environment, or on the analyst's machine from ``connections.toml`` (:mod:`haute._connections`),
never from this file. The form is
the pipeline folder's ``workbench/`` folder, the pipeline folder being the folder of the
project's pipeline file, and is never configured. The table is read from disk whenever a
workbench route needs it, so an edit reaches the next request.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from haute._project import resolve_pipeline_file
from haute._sandbox import contained_path
from haute.errors import ConfigError, HauteError, PathOutsideProjectError

#: The keys the table may hold. ``haute deploy``'s whole-file check of ``haute.toml``
#: (``haute.deploy._config``) accepts exactly these under ``[workbench]``.
WORKBENCH_TOML_KEYS: frozenset[str] = frozenset({"enabled", "app", "store"})
#: Where the underwriter app can run: the host whose sign-in fronts it.
APP_HOSTS: frozenset[str] = frozenset({"azure-container-apps"})
#: The databases an environment's quote store can be on.
STORE_PROVIDERS: frozenset[str] = frozenset({"sqlite", "sqlserver", "postgresql"})
#: The providers whose database is a file, which takes no sign-in.
FILE_PROVIDERS: frozenset[str] = frozenset({"sqlite"})
#: How a hosted app signs in to its environment's database, each with the keys naming the
#: variables that hold what it needs.
STORE_SIGN_INS: Mapping[str, tuple[str, ...]] = {
    "managed-identity": (),
    "service-principal": ("tenant_id_env", "client_id_env", "client_secret_env"),
    "password": ("username_env", "password_env"),
}
#: The keys a sign-in may name besides: a user-assigned managed identity's client id, which
#: the system-assigned identity needs none of.
_OPTIONAL_SIGN_IN_KEYS: Mapping[str, tuple[str, ...]] = {"managed-identity": ("client_id_env",)}
#: The providers on which a Microsoft Entra ID sign-in also names the role it takes, with
#: ``username_env``: PostgreSQL's, whose role an Entra identity is mapped to by name.
ROLE_NAMED: frozenset[str] = frozenset({"postgresql"})
#: The variable a host or a CI job names the environment it runs in with.
ENVIRONMENT_ENV = "HAUTE_ENVIRONMENT"
#: The variables the host's environment says where the store's database is with: a server, a
#: database and a schema, or one whole connection URL.
STORE_SERVER_ENV = "HAUTE_STORE_SERVER"
STORE_DATABASE_ENV = "HAUTE_STORE_DATABASE"
STORE_SCHEMA_ENV = "HAUTE_STORE_SCHEMA"
STORE_URL_ENV = "HAUTE_STORE_URL"
#: The keys of a hosted environment's ``[workbench.app.<name>]``: where the host runs its app.
APP_ENVIRONMENT_KEYS: tuple[str, ...] = ("resource_group", "container_app")
#: The form folder's name in the pipeline folder.
FORM_FOLDER = "workbench"

_TABLE = "workbench"
#: A hosted environment's name: lower-case letters, digits, dashes and underscores.
ENVIRONMENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
#: An environment variable's name.
VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: Keys that would put where a database is in the file, and the variable each belongs in.
_LOCATION_KEYS: Mapping[str, str] = {
    "server": STORE_SERVER_ENV,
    "database": STORE_DATABASE_ENV,
    "schema": STORE_SCHEMA_ENV,
    "url": STORE_URL_ENV,
}
#: Keys that would put a secret's value in the file, and the key naming its variable instead.
SECRET_KEYS: Mapping[str, str] = {
    "password": "password_env",
    "username": "username_env",
    "user": "username_env",
    "client_id": "client_id_env",
    "client_secret": "client_secret_env",
    "secret": "client_secret_env",
    "token": "an environment variable",
    "connection_string": STORE_URL_ENV,
}

#: Where an earlier Haute named the form's one file; refused saying what replaced it.
_REMOVED_FORM_KEY = (
    "[workbench].form was removed: the form is the pipeline folder's workbench/ folder; "
    "delete the key"
)


class WorkbenchError(HauteError):
    """A workbench problem the analyst fixes in the project: its table or its form.

    A workbench route answers one as 409 with its message (``haute.routes._error_handlers``),
    so the editor shows what to fix and the pipeline editor goes on working.
    """


class WorkbenchConfigError(WorkbenchError, ConfigError):
    """The ``[workbench]`` table, or the ``haute.toml`` holding it, cannot be read as settings."""


@dataclass(frozen=True, slots=True)
class StoreEnvironment:
    """An environment of the quote store, as a project declares it: its name, its database's
    provider and how a hosted app signs in to it, by the names of the variables holding what
    that needs, never their values."""

    name: str
    provider: str
    #: One of ``STORE_SIGN_INS``; None for a database that takes no sign-in, a file's.
    auth: str | None = None
    #: Each of the sign-in's keys and the variable it names, ``client_id_env`` to
    #: ``STORE_CLIENT_ID``.
    variables: Mapping[str, str] = field(default_factory=dict)

    def declared(self) -> dict[str, str]:
        """The environment as a release records it, the name aside."""
        return {
            "provider": self.provider,
            **({} if self.auth is None else {"auth": self.auth}),
            **self.variables,
        }


@dataclass(frozen=True, slots=True)
class AppEnvironment:
    """A hosted environment's app as ``[workbench.app.<name>]`` names it: the resource group
    and the Container App the host runs it as, each empty while the project leaves it so,
    which ``haute app rollout`` refuses."""

    name: str
    resource_group: str = ""
    container_app: str = ""


@dataclass(frozen=True, slots=True)
class WorkbenchConfig:
    """The table as read: whether the workbench is enabled, and where its form folder is."""

    enabled: bool
    project_root: Path
    #: The form folder's path from the project root, posix (``rating/workbench`` in a new
    #: project), as the status and every message name it; empty while the workbench is
    #: disabled, when nothing reads a form.
    form: str = ""
    #: Where the underwriter app runs (``[workbench.app] host``); None when unsaid.
    app_host: str | None = None
    #: The role that may change quotes in the app (``[workbench.app] edit_role``); None lets
    #: everyone signed in change them.
    app_edit_role: str | None = None
    #: The environments of the app's quote store (``[workbench.store.<name>]``), in the file's
    #: order.
    store: tuple[StoreEnvironment, ...] = ()
    #: Each hosted environment's app (``[workbench.app.<name>]``), in the file's order.
    app_environments: tuple[AppEnvironment, ...] = ()

    @property
    def form_folder(self) -> Path:
        """The form folder; a disabled workbench has none."""
        if not self.enabled:
            raise WorkbenchConfigError("This project's workbench is not enabled.")
        return self.project_root / self.form


def store_sign_in_keys(provider: str, auth: str) -> tuple[str, ...]:
    """The keys a server environment on *provider* signed in to with *auth*, one of
    ``STORE_SIGN_INS``, names, each the variable that holds what the sign-in needs."""
    keys = STORE_SIGN_INS[auth]
    if provider in ROLE_NAMED and "username_env" not in keys:
        # The role a PostgreSQL server maps the Entra identity to, by name.
        keys = (*keys, "username_env")
    return keys


def read_workbench_config(project_root: Path) -> WorkbenchConfig:
    """The ``[workbench]`` table of *project_root*'s ``haute.toml``; disabled without one.

    Raises :class:`WorkbenchConfigError` when the file cannot be read or parsed, the table is
    not as the module docstring describes, or, while enabled, the project has no pipeline
    file or its form folder resolves outside the project.
    """
    toml_path = project_root / "haute.toml"
    disabled = WorkbenchConfig(enabled=False, project_root=project_root)
    if not toml_path.is_file():
        return disabled
    try:
        with toml_path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise WorkbenchConfigError(
            f"haute.toml could not be read: {exc}", path=str(toml_path)
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise WorkbenchConfigError(
            f"haute.toml is malformed and could not be parsed: {exc}", path=str(toml_path)
        ) from exc
    except UnicodeDecodeError as exc:
        raise WorkbenchConfigError(
            f"haute.toml is not UTF-8 text: {exc}", path=str(toml_path)
        ) from exc

    table = data.get(_TABLE)
    if table is None:
        return disabled
    if not isinstance(table, dict):
        raise WorkbenchConfigError("[workbench] must be a TOML table", path=str(toml_path))
    if "form" in table:
        raise WorkbenchConfigError(_REMOVED_FORM_KEY, path=str(toml_path))
    unknown = sorted(set(table) - WORKBENCH_TOML_KEYS)
    if unknown:
        raise WorkbenchConfigError(
            f"[workbench] has unknown key(s): {', '.join(unknown)}; the keys are enabled, app "
            "and store",
            path=str(toml_path),
        )
    enabled = table.get("enabled")
    if not isinstance(enabled, bool):
        raise WorkbenchConfigError("[workbench].enabled must be true or false", path=str(toml_path))
    try:
        store = store_environments(table.get("store", {}), "[workbench.store]")
    except ValueError as exc:
        raise WorkbenchConfigError(str(exc), path=str(toml_path)) from exc
    app_host, app_edit_role, app_environments = _app_settings(
        table.get("app"), toml_path, {environment.name for environment in store}
    )
    if not enabled:
        return WorkbenchConfig(
            enabled=False,
            project_root=project_root,
            app_host=app_host,
            app_edit_role=app_edit_role,
            store=store,
            app_environments=app_environments,
        )
    return WorkbenchConfig(
        enabled=True,
        project_root=project_root,
        form=_form_folder(project_root, toml_path),
        app_host=app_host,
        app_edit_role=app_edit_role,
        store=store,
        app_environments=app_environments,
    )


def store_environments(raw: object, where: str) -> tuple[StoreEnvironment, ...]:
    """The environments *raw* declares, a table of tables by name, as *where* (the
    file's table, or a release) holds them; :class:`ValueError` naming what is wrong. A
    location or a secret's value is refused, naming the variable it belongs in, so no file a
    project versions says where a database is or holds a credential."""
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a table of environments, each a table of its own")
    environments: list[StoreEnvironment] = []
    for name, settings in raw.items():
        at = f"{where[:-1]}.{name}]" if where.endswith("]") else f"{where}.{name}"
        if not isinstance(name, str) or not ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError(
                f"{at}: an environment's name is up to 32 lower-case letters, digits, dashes "
                "and underscores, starting with a letter"
            )
        if not isinstance(settings, dict):
            raise ValueError(f"{at} must be a table")
        environments.append(_environment(name, settings, at))
    return tuple(environments)


def _environment(name: str, settings: dict[str, object], at: str) -> StoreEnvironment:
    for key in settings:
        if key in _LOCATION_KEYS:
            raise ValueError(
                f"{at}.{key}: where an environment's database is comes from the host's "
                f"environment ({_LOCATION_KEYS[key]}), never from a file"
            )
        if key in SECRET_KEYS:
            raise ValueError(
                f"{at}.{key} would hold a secret's value: name the variable that holds it "
                f"({SECRET_KEYS[key]}) instead"
            )
    provider = settings.get("provider")
    if not isinstance(provider, str) or provider not in STORE_PROVIDERS:
        raise ValueError(
            f"{at}.provider must be one of {', '.join(sorted(STORE_PROVIDERS))}; got {provider!r}"
        )
    auth = settings.get("auth")
    optional: tuple[str, ...] = ()
    if provider in FILE_PROVIDERS:
        if auth is not None:
            raise ValueError(f"{at}.auth: a {provider} database is a file, which takes no sign-in")
        keys: tuple[str, ...] = ()
    else:
        if not isinstance(auth, str) or auth not in STORE_SIGN_INS:
            raise ValueError(f"{at}.auth must be one of {', '.join(STORE_SIGN_INS)}; got {auth!r}")
        keys = store_sign_in_keys(provider, auth)
        optional = _OPTIONAL_SIGN_IN_KEYS.get(auth, ())
    known = {"provider", "auth", *keys, *optional}
    unknown = sorted(set(settings) - known)
    if unknown:
        raise ValueError(
            f"{at} has unknown key(s): {', '.join(unknown)}; the keys are "
            + ", ".join(sorted(known))
        )
    variables: dict[str, str] = {}
    for key in (*keys, *optional):
        variable = settings.get(key)
        if variable is None and key in optional:
            continue
        if not isinstance(variable, str) or not VARIABLE_NAME.fullmatch(variable):
            raise ValueError(
                f"{at}.{key} must name the environment variable that holds it, letters, "
                "digits and underscores"
            )
        variables[key] = variable
    return StoreEnvironment(
        name=name, provider=provider, auth=None if auth is None else str(auth), variables=variables
    )


def _app_settings(
    raw: object, toml_path: Path, declared: set[str]
) -> tuple[str | None, str | None, tuple[AppEnvironment, ...]]:
    """``[workbench.app]``'s ``host``, one of ``APP_HOSTS``, its ``edit_role``, a role's name,
    each None when unsaid, and a hosted environment's app for each table under it, named as
    one of the store's environments, *declared*; none of them without the table."""
    if raw is None:
        return None, None, ()
    if not isinstance(raw, dict):
        raise WorkbenchConfigError("[workbench.app] must be a TOML table", path=str(toml_path))
    settings_keys = {"host", "edit_role"}
    environments = tuple(
        _app_environment(name, settings, toml_path, declared)
        for name, settings in raw.items()
        if name not in settings_keys and isinstance(settings, dict)
    )
    unknown = sorted(
        key
        for key, value in raw.items()
        if key not in settings_keys and not isinstance(value, dict)
    )
    if unknown:
        raise WorkbenchConfigError(
            f"[workbench.app] has unknown key(s): {', '.join(unknown)}; the keys are host and "
            "edit_role, beside a table for each hosted environment",
            path=str(toml_path),
        )
    host = raw.get("host")
    if not isinstance(host, str) or host not in APP_HOSTS:
        raise WorkbenchConfigError(
            f"[workbench.app].host must be one of {', '.join(sorted(APP_HOSTS))}; got {host!r}",
            path=str(toml_path),
        )
    edit_role = raw.get("edit_role")
    if edit_role is not None and (not isinstance(edit_role, str) or not edit_role.strip()):
        raise WorkbenchConfigError(
            "[workbench.app].edit_role must be the name of a role the host signs underwriters "
            f"in with; got {edit_role!r}",
            path=str(toml_path),
        )
    return str(host), None if edit_role is None else edit_role.strip(), environments


def _app_environment(
    name: str, settings: dict[str, object], toml_path: Path, declared: set[str]
) -> AppEnvironment:
    """``[workbench.app.<name>]``: the Container App the host runs environment *name*'s app
    as, *name* one of the store's environments, *declared*."""
    at = f"[workbench.app.{name}]"
    if name not in declared:
        raise WorkbenchConfigError(
            f"{at} names no environment [workbench.store] declares; declare "
            f"[workbench.store.{name}], whose database its app keeps its quotes in",
            path=str(toml_path),
        )
    unknown = sorted(set(settings) - set(APP_ENVIRONMENT_KEYS))
    if unknown:
        raise WorkbenchConfigError(
            f"{at} has unknown key(s): {', '.join(unknown)}; the keys are "
            + " and ".join(APP_ENVIRONMENT_KEYS),
            path=str(toml_path),
        )
    values: dict[str, str] = {}
    for key in APP_ENVIRONMENT_KEYS:
        value = settings.get(key, "")
        if not isinstance(value, str):
            raise WorkbenchConfigError(f"{at}.{key} must be text", path=str(toml_path))
        values[key] = value.strip()
    return AppEnvironment(name=name, **values)


def _form_folder(project_root: Path, toml_path: Path) -> str:
    """The form folder's path from *project_root*: the pipeline folder's ``workbench/``."""
    try:
        pipeline_file = resolve_pipeline_file(project_root)
    except FileNotFoundError as exc:
        raise WorkbenchConfigError(
            f"The workbench needs the project's pipeline file to find its form: {exc}",
            path=str(toml_path),
        ) from exc
    root = project_root.resolve()
    folder = pipeline_file.resolve().parent / FORM_FOLDER
    try:
        relative = folder.relative_to(root).as_posix()
    except ValueError as exc:
        raise WorkbenchConfigError(
            "The pipeline folder is outside the project, so the workbench's form folder "
            f"would be too: {folder}",
            path=str(toml_path),
        ) from exc
    # A `workbench` folder that is a symlink or junction to somewhere outside the project is
    # refused: the workbench never reads a file outside it.
    try:
        contained_path(project_root, relative)
    except PathOutsideProjectError as exc:
        raise WorkbenchConfigError(
            f"{relative} must stay inside the project", path=str(toml_path)
        ) from exc
    return relative
