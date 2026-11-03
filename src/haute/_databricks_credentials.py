"""Shared Databricks credential resolution with redaction-safe errors.

Four forms sign haute in to a workspace, every one selected in the environment
(the project's ``.env``), never in the pipeline:

* ``DATABRICKS_HOST`` + ``DATABRICKS_TOKEN`` — a personal access token.
* ``DATABRICKS_HOST`` + ``DATABRICKS_CLIENT_ID``/``DATABRICKS_CLIENT_SECRET`` — a
  service principal (a Databricks App injects this pair).
* ``DATABRICKS_HOST`` + ``DATABRICKS_AUTH_TYPE=databricks-cli`` — the user's own
  sign-in, held by the Databricks CLI after ``databricks auth login``.
* ``DATABRICKS_CONFIG_PROFILE`` — a profile of the CLI's config file, which names
  the host and one of the forms above (the CLI's sign-in, a token, or a pair).

The user sign-ins are ruled in (2026-10-26): a workspace that issues no personal
access tokens, and a user with no service principal, otherwise has no way in,
while a user sign-in gives the user their own Unity Catalog grants with no
secret on disk. Every form is explicit — haute never falls through to whatever
credentials happen to be on the machine — so the identity haute acts as is
always the one named here; a profile therefore has its sign-in pinned at
resolution rather than left to the SDK's default chain, which would try an
Azure CLI login or ``DATABRICKS_USERNAME``/``DATABRICKS_PASSWORD`` first.

The SDK's own browser sign-in (``external-browser``) is not offered: the SDK
waits for the browser's redirect with no timeout and no way to cancel from
outside, so an abandoned sign-in would hang a request for good and keep its port.
A browser sign-in is still available through ``databricks auth login``, which
opens the browser in a process the user controls; haute then uses it as the CLI
form or a profile.
"""

from __future__ import annotations

import configparser
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from haute.errors import HauteError

AuthMode = Literal["pat", "service_principal", "cli_login", "profile"]

#: The SDK auth types ``DATABRICKS_AUTH_TYPE`` may name, and the mode each selects.
USER_SIGN_IN_AUTH_TYPES: Mapping[str, AuthMode] = {"databricks-cli": "cli_login"}

_USER_SIGN_IN_MODES = frozenset({"cli_login", "profile"})

#: The SDK auth type haute refuses wherever it appears (see the module docstring).
_BROWSER_AUTH_TYPE = "external-browser"

#: The sign-ins a profile may be pinned to: each one's material lives in the profile
#: (or, for the CLI's, in the keychain the CLI holds), never in the ambient environment.
_PROFILE_AUTH_TYPES = frozenset({"databricks-cli", "pat", "oauth-m2m"})

_SIGN_IN_ALTERNATIVES = (
    "or a user sign-in: DATABRICKS_AUTH_TYPE=databricks-cli with DATABRICKS_HOST, "
    "or DATABRICKS_CONFIG_PROFILE=<profile>"
)

#: ``configparser`` folds a ``[DEFAULT]`` section into every other section; the SDK
#: does not (it reads each profile's own keys, and ``DEFAULT`` is a profile of its
#: own). Naming an impossible default section makes ``[DEFAULT]`` ordinary here too.
_NO_DEFAULT_SECTION = "\x00no-default-section"


class DatabricksConfigError(HauteError):
    """Raised when Databricks configuration is incomplete or unavailable."""


class DatabricksSignInError(DatabricksConfigError):
    """A user sign-in the SDK could not complete: an expired CLI login, or a profile
    whose credentials the workspace refused.

    ``problem`` and ``remedy`` are haute's own words, so a caller can set the problem
    in its own sentence ("..., so the catalogs could not be listed"). The SDK's text,
    which can name hosts and account ids, travels only as ``__cause__`` for the log.
    """

    def __init__(self, problem: str, remedy: str) -> None:
        super().__init__(f"{problem}: {remedy}.")
        self.problem = problem
        self.remedy = remedy


@dataclass(frozen=True, slots=True)
class DatabricksCredentials:
    """Normalised Databricks endpoint and selected authentication material."""

    workspace_host: str
    server_hostname: str
    auth_mode: AuthMode
    token: str | None = field(repr=False)
    client_id: str | None = field(repr=False)
    client_secret: str | None = field(repr=False)
    #: The profile ``DATABRICKS_CONFIG_PROFILE`` names (mode ``profile``).
    profile: str | None = None
    #: The SDK auth type for a user sign-in: ``DATABRICKS_AUTH_TYPE``'s value, or the
    #: one pinned for the profile (declared by it, or implied by its token or pair).
    sdk_auth_type: str | None = None

    @property
    def signs_in_as_user(self) -> bool:
        """Whether the SDK performs the sign-in (the CLI's, or a profile's), rather
        than haute presenting a token or service principal it holds."""
        return self.auth_mode in _USER_SIGN_IN_MODES

    def sign_in_failure(self, cli_said: str = "") -> tuple[str, str]:
        """The problem and the remedy when this user sign-in cannot be completed.

        *cli_said* is the Databricks CLI's own message when there is one. Only a
        message that reports a missing or expired login earns the login command as
        the remedy; a locked keychain, a proxy, or a workspace that restricts the
        CLI's app would not be cured by it, so those are sent to the server log,
        where the CLI's message is kept (hosts scrubbed). The host never appears
        here: a message reaches API responses, which keep host values out, so
        ``DATABRICKS_HOST`` is named in its place.
        """
        if self.auth_mode == "profile" and self.sdk_auth_type != "databricks-cli":
            return (
                f"Databricks refused the credentials of profile '{self.profile}'",
                "check them in the Databricks CLI config file",
            )
        if self.auth_mode == "profile":
            where = f"for profile '{self.profile}'"
            expired = f"Databricks sign-in for profile '{self.profile}' failed or has expired"
            login = f"databricks auth login --profile {self.profile}"
        elif self.auth_mode == "cli_login":
            where = "for DATABRICKS_HOST"
            expired = "The Databricks CLI sign-in failed or has expired"
            login = "databricks auth login --host <your DATABRICKS_HOST>"
        else:
            raise ValueError(f"{self.auth_mode} is not a user sign-in")
        if cli_said and not _LOGIN_NEEDED_RE.search(cli_said):
            return (
                f"The Databricks CLI could not produce a token {where}",
                f"its message is in the server log; run `{login}` if it reports a missing "
                "or expired sign-in, otherwise check the keychain, the proxy and the "
                "workspace's policy for the CLI",
            )
        return expired, f"run `{login}` and try again"


#: What the Databricks CLI says when the login itself is missing or expired, the
#: only failures `databricks auth login` cures (CLI 1.10: "A new access token could
#: not be retrieved because the refresh token is invalid. To reauthenticate, run ...").
_LOGIN_NEEDED_RE = re.compile(
    r"refresh token|reauthenticate|auth login|not logged in|no (?:oauth |access )?token",
    re.IGNORECASE,
)


def _normalise_server_hostname(workspace_host: str) -> str:
    """Convert an optional-protocol workspace URL to the SQL hostname."""
    return re.sub(r"^https?://", "", workspace_host, count=1, flags=re.IGNORECASE)


def _credentials(
    workspace_host: str,
    auth_mode: AuthMode,
    *,
    token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    profile: str | None = None,
    sdk_auth_type: str | None = None,
) -> DatabricksCredentials:
    return DatabricksCredentials(
        workspace_host=workspace_host,
        server_hostname=_normalise_server_hostname(workspace_host),
        auth_mode=auth_mode,
        token=token,
        client_id=client_id,
        client_secret=client_secret,
        profile=profile,
        sdk_auth_type=sdk_auth_type,
    )


def _missing(names: Sequence[str]) -> DatabricksConfigError:
    return DatabricksConfigError("Missing Databricks configuration:\n  " + "\n  ".join(names))


def _refuse_beside(selector: str, others: Mapping[str, str]) -> None:
    """Refuse *selector* when any of *others* is also set, naming them.

    The SDK merges the environment over a profile, and an auth type over a token,
    so a second selector would quietly change which workspace or identity haute
    uses; refusing keeps the identity haute acts as the one that is named.
    """
    also_set = [name for name, value in others.items() if value.strip()]
    if also_set:
        raise DatabricksConfigError(
            f"{selector} selects the Databricks sign-in on its own: unset "
            f"{', '.join(also_set)}, or unset {selector}."
        )


def _profile_host_and_auth_type(profile: str) -> tuple[str, str]:
    """The host of *profile* in the Databricks CLI's config file, and its pinned sign-in.

    Read as the SDK reads it: ``DATABRICKS_CONFIG_FILE`` or ``~/.databrickscfg``, an
    INI file in which each profile has only its own keys (a ``[DEFAULT]`` section is
    the profile named ``DEFAULT``, inherited by none). Reading the file here, with the
    standard library, keeps resolution free of the SDK and of any sign-in side
    effect, so a status check can never run the CLI.

    The sign-in is pinned so the SDK never walks its default chain for a profile,
    and only to a form whose material the profile itself holds, so the SDK never
    merges ambient credentials under a name the profile gave: ``databricks-cli``;
    ``pat`` with a ``token`` in the section; ``oauth-m2m`` with a ``client_id`` and
    ``client_secret`` in the section. A declared type outside those three
    (``external-browser``, ``basic``, ``azure-cli``, ...) is refused, as is a
    declared ``pat`` or ``oauth-m2m`` without its material, and a profile that
    names only a host.
    """
    path = Path(os.getenv("DATABRICKS_CONFIG_FILE", "").strip() or "~/.databrickscfg").expanduser()
    login = f"run `databricks auth login --host <workspace URL> --profile {profile}`"
    parser = configparser.ConfigParser(interpolation=None, default_section=_NO_DEFAULT_SECTION)
    if not path.is_file():
        raise DatabricksConfigError(
            f"DATABRICKS_CONFIG_PROFILE names profile '{profile}', but there is no "
            f"Databricks CLI config file at {path}: {login} to create it."
        )
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        # The parser's message quotes the offending line, which may be a token.
        raise DatabricksConfigError(
            f"The Databricks CLI config file {path} could not be read as a profiles file "
            f"(line {getattr(exc, 'lineno', '?')}): repair it, or {login} to rewrite it."
        ) from None
    if profile not in parser:
        raise DatabricksConfigError(
            f"DATABRICKS_CONFIG_PROFILE names profile '{profile}', which {path} does not "
            f"define: {login} to create it."
        )
    section = parser[profile]
    host = section.get("host", "").strip().rstrip("/")
    if not host:
        raise DatabricksConfigError(
            f"Profile '{profile}' in {path} has no host: add `host = <workspace URL>` to it, "
            f"or sign in again with `databricks auth login --host <workspace URL> "
            f"--profile {profile}`."
        )
    declared = section.get("auth_type", "").strip()
    has_token = bool(section.get("token", "").strip())
    has_pair = bool(section.get("client_id", "").strip()) and bool(
        section.get("client_secret", "").strip()
    )
    if declared == _BROWSER_AUTH_TYPE:
        raise DatabricksConfigError(
            f"Profile '{profile}' in {path} declares auth_type = external-browser, which "
            f"Haute does not run (the SDK's browser sign-in cannot be timed out or "
            f"cancelled): sign in with {login}, which writes auth_type = databricks-cli."
        )
    if declared and declared not in _PROFILE_AUTH_TYPES:
        raise DatabricksConfigError(
            f"Profile '{profile}' in {path} declares auth_type = {declared}, which Haute does "
            f"not use for data access: a profile signs in as the Databricks CLI (auth_type = "
            f"databricks-cli, which {login} writes), with a token, or with a client_id and "
            f"client_secret."
        )
    if declared == "pat" and not has_token:
        raise DatabricksConfigError(
            f"Profile '{profile}' in {path} declares auth_type = pat but holds no token: add "
            f"`token = <personal access token>` to it, or {login}."
        )
    if declared == "oauth-m2m" and not has_pair:
        raise DatabricksConfigError(
            f"Profile '{profile}' in {path} declares auth_type = oauth-m2m but lacks "
            f"client_id or client_secret: add both to it, or {login}."
        )
    if declared:
        return host, declared
    if has_token:
        return host, "pat"
    if has_pair:
        return host, "oauth-m2m"
    raise DatabricksConfigError(
        f"Profile '{profile}' in {path} names only a host, so it does not say how to sign "
        f"in: {login}, which writes auth_type = databricks-cli, or add a token or a "
        f"client_id and client_secret to it."
    )


def resolve_databricks_credentials(
    *, additional_missing: Sequence[str] = ()
) -> DatabricksCredentials:
    """Resolve the selected Databricks authentication form from the environment.

    A named profile, else an auth type, else a non-empty PAT, else the
    service-principal pair; a profile or an auth type beside another selector is
    refused (:func:`_refuse_beside`), while a PAT beside the pair simply wins, as a
    Databricks App's injected pair must be overridable. Error messages name
    configuration and profiles, never the values read from the environment.
    """
    workspace_host = os.getenv("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.getenv("DATABRICKS_TOKEN", "")
    client_id = os.getenv("DATABRICKS_CLIENT_ID", "")
    client_secret = os.getenv("DATABRICKS_CLIENT_SECRET", "")
    profile = os.getenv("DATABRICKS_CONFIG_PROFILE", "").strip()
    auth_type = os.getenv("DATABRICKS_AUTH_TYPE", "").strip()
    requirements = [requirement for requirement in additional_missing if requirement]

    if profile:
        _refuse_beside(
            "DATABRICKS_CONFIG_PROFILE",
            {
                "DATABRICKS_HOST": workspace_host,
                "DATABRICKS_TOKEN": token,
                "DATABRICKS_CLIENT_ID": client_id,
                "DATABRICKS_CLIENT_SECRET": client_secret,
                "DATABRICKS_AUTH_TYPE": auth_type,
            },
        )
        host, pinned = _profile_host_and_auth_type(profile)
        if requirements:
            raise _missing(requirements)
        return _credentials(host, "profile", profile=profile, sdk_auth_type=pinned)

    if auth_type:
        mode = USER_SIGN_IN_AUTH_TYPES.get(auth_type)
        if mode is None:
            raise DatabricksConfigError(
                "DATABRICKS_AUTH_TYPE must be databricks-cli, the sign-in the Databricks CLI "
                "holds after `databricks auth login`. (The SDK's external-browser sign-in is "
                "not offered: it cannot be timed out or cancelled.)"
            )
        _refuse_beside(
            "DATABRICKS_AUTH_TYPE",
            {
                "DATABRICKS_TOKEN": token,
                "DATABRICKS_CLIENT_ID": client_id,
                "DATABRICKS_CLIENT_SECRET": client_secret,
            },
        )
        unmet = (["DATABRICKS_HOST"] if not workspace_host else []) + requirements
        if unmet:
            raise _missing(unmet)
        return _credentials(workspace_host, mode, sdk_auth_type=auth_type)

    missing: list[str] = []
    if not workspace_host:
        missing.append("DATABRICKS_HOST")

    if token:
        auth_mode: AuthMode = "pat"
    elif client_id and client_secret:
        auth_mode = "service_principal"
    else:
        missing.append("DATABRICKS_TOKEN")
        if not client_id:
            missing.append("DATABRICKS_CLIENT_ID")
        if not client_secret:
            missing.append("DATABRICKS_CLIENT_SECRET")
        missing.append(_SIGN_IN_ALTERNATIVES)

    missing.extend(requirements)
    if missing:
        raise _missing(missing)

    if auth_mode == "pat":
        return _credentials(workspace_host, auth_mode, token=token)
    return _credentials(workspace_host, auth_mode, client_id=client_id, client_secret=client_secret)


#: The OAuth token endpoint's error codes (RFC 6749, section 5.2) that refuse the
#: client or its grant: the credentials themselves are wrong.
_REFUSED_CREDENTIAL_CODES = frozenset({"invalid_client", "invalid_grant", "unauthorized_client"})


def credentials_rejected(exc: BaseException) -> bool:
    """Whether *exc* is the workspace refusing the credentials themselves.

    A user sign-in the SDK could not complete is :class:`DatabricksSignInError`. A
    refused personal access token is the SDK's ``Unauthenticated``. A refused
    service principal never reaches the API: the SDK's OAuth token exchange raises
    a plain ``ValueError("<code>: <description>")`` from the token endpoint's
    answer, which counts when its code refuses the client or its grant. Any other
    ``ValueError`` is not a credential failure.
    """
    if isinstance(exc, DatabricksSignInError):
        return True
    if cli_refused_a_refresh(exc):
        return True
    try:
        from databricks.sdk.errors import Unauthenticated
    except ImportError:  # pragma: no cover - the SDK raised exc, so it is installed
        return False
    if isinstance(exc, Unauthenticated):
        return True
    return isinstance(exc, ValueError) and str(exc).partition(":")[0] in _REFUSED_CREDENTIAL_CODES


#: What the SDK's CLI token source raises when `databricks auth token` fails after
#: the client was built, on a refresh mid-session: ``IOError("cannot get access
#: token: <the CLI's message>")`` (databricks-sdk 0.88, ``CliTokenSource.refresh``).
_CLI_REFRESH_FAILURE = "cannot get access token"


def cli_refused_a_refresh(exc: BaseException) -> bool:
    """Whether *exc* is the Databricks CLI refusing to refresh a token mid-session.

    A sign-in that fails as the client is built is a ``ValueError`` the SDK wraps and
    :func:`databricks_sdk_config` words; one that fails later, when a request asks
    for a fresh token, is the token source's plain ``OSError`` and must be read as
    the same refused credential, not a server error.
    """
    return isinstance(exc, OSError) and str(exc).startswith(_CLI_REFRESH_FAILURE)


_CLI_NOT_FOUND = (
    "The Databricks CLI was not found by the server",
    "install it and make sure `databricks` is on the PATH of the shell that starts "
    "Haute, or set DATABRICKS_CLI_PATH to it",
)


def _cli_not_found(exc: BaseException) -> bool:
    """Whether the SDK failed because there is no ``databricks`` executable.

    A path ``DATABRICKS_CLI_PATH`` names that does not exist surfaces as a
    ``FileNotFoundError`` in the cause chain; a binary missing from the PATH makes
    the SDK's CLI provider decline, and the SDK then reports that no credentials
    could be configured.
    """
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, FileNotFoundError):
            return True
        seen = seen.__cause__ or seen.__context__
    return "cannot configure default credentials" in str(exc)


def databricks_sdk_config(
    credentials: DatabricksCredentials, *, retry_timeout_seconds: int | None = None
) -> Any:
    """The SDK ``Config`` that performs a user sign-in (``signs_in_as_user``).

    Building it signs in: the CLI form runs ``databricks auth token``, and a profile
    does whatever its pinned ``auth_type`` says, so this is called only where a
    connection is about to be made, never from a status check. The auth type is
    always passed, so the SDK never walks its default chain. A sign-in the SDK
    cannot complete is its construction-time ``ValueError``; it becomes a
    :class:`DatabricksSignInError` worded by haute. *retry_timeout_seconds* bounds
    how long the SDK retries an unreachable workspace (its default is 300).

    Raises :class:`ImportError` when the ``databricks`` extra is not installed;
    each caller words that for its own surface.
    """
    from databricks.sdk.core import Config

    if not credentials.signs_in_as_user:
        raise ValueError(f"{credentials.auth_mode} authenticates without an SDK config")
    try:
        if credentials.auth_mode == "profile":
            config = Config(profile=credentials.profile, auth_type=credentials.sdk_auth_type)
        else:
            config = Config(host=credentials.workspace_host, auth_type=credentials.sdk_auth_type)
    except ValueError as exc:
        if credentials.sdk_auth_type == "databricks-cli" and _cli_not_found(exc):
            raise DatabricksSignInError(*_CLI_NOT_FOUND) from exc
        problem, remedy = credentials.sign_in_failure(str(exc))
        raise DatabricksSignInError(problem, remedy) from exc
    if retry_timeout_seconds is not None:
        config.retry_timeout_seconds = retry_timeout_seconds
    return config


def databricks_workspace_client(
    credentials: DatabricksCredentials, *, retry_timeout_seconds: int | None = None
) -> Any:
    """A ``databricks.sdk.WorkspaceClient`` authenticated as *credentials* select.

    *retry_timeout_seconds* bounds how long the SDK retries an unreachable workspace
    (its default is 300). Raises :class:`ImportError` when the ``databricks`` extra
    is not installed; each caller words that for its own surface.
    """
    from databricks.sdk import WorkspaceClient

    if credentials.signs_in_as_user:
        return WorkspaceClient(
            config=databricks_sdk_config(credentials, retry_timeout_seconds=retry_timeout_seconds)
        )
    if retry_timeout_seconds is None:
        if credentials.auth_mode == "pat":
            return WorkspaceClient(host=credentials.workspace_host, token=credentials.token)
        return WorkspaceClient(
            host=credentials.workspace_host,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
        )
    # The retry deadline is a Config attribute the client reads at construction,
    # not a WorkspaceClient argument, so a bounded client is built from a Config.
    from databricks.sdk.core import Config

    if credentials.auth_mode == "pat":
        config = Config(
            host=credentials.workspace_host,
            token=credentials.token,
            retry_timeout_seconds=retry_timeout_seconds,
        )
    else:
        config = Config(
            host=credentials.workspace_host,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            retry_timeout_seconds=retry_timeout_seconds,
        )
    return WorkspaceClient(config=config)
