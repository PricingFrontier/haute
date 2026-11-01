"""Shared Databricks credential resolution with redaction-safe errors.

Five forms sign haute in to a workspace, every one selected in the environment
(the project's ``.env``), never in the pipeline:

* ``DATABRICKS_HOST`` + ``DATABRICKS_TOKEN`` — a personal access token.
* ``DATABRICKS_HOST`` + ``DATABRICKS_CLIENT_ID``/``DATABRICKS_CLIENT_SECRET`` — a
  service principal (a Databricks App injects this pair).
* ``DATABRICKS_HOST`` + ``DATABRICKS_AUTH_TYPE=databricks-cli`` — the user's own
  sign-in, held by the Databricks CLI after ``databricks auth login``.
* ``DATABRICKS_HOST`` + ``DATABRICKS_AUTH_TYPE=external-browser`` — the user's own
  sign-in through a browser the SDK opens itself.
* ``DATABRICKS_CONFIG_PROFILE`` — a profile of the CLI's config file, which names
  the host and one of the forms above.

The last three are the user sign-ins, ruled in on 2026-10-26: a workspace that
issues no personal access tokens, and a user with no service principal, otherwise
has no way in, while a user sign-in gives the user their own Unity Catalog grants
with no secret on disk. Every form is explicit — haute never falls through to
whatever credentials happen to be on the machine — so the identity haute acts as
is always the one named here.
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

AuthMode = Literal["pat", "service_principal", "cli_login", "browser_login", "profile"]

#: The SDK auth types ``DATABRICKS_AUTH_TYPE`` may name, and the mode each selects.
USER_SIGN_IN_AUTH_TYPES: Mapping[str, AuthMode] = {
    "databricks-cli": "cli_login",
    "external-browser": "browser_login",
}

_USER_SIGN_IN_MODES = frozenset({"cli_login", "browser_login", "profile"})

_SIGN_IN_ALTERNATIVES = (
    "or a user sign-in: DATABRICKS_AUTH_TYPE=databricks-cli or external-browser "
    "with DATABRICKS_HOST, or DATABRICKS_CONFIG_PROFILE=<profile>"
)


class DatabricksConfigError(HauteError):
    """Raised when Databricks configuration is incomplete or unavailable."""


class DatabricksSignInError(DatabricksConfigError):
    """A user sign-in the SDK could not complete: an expired CLI login, an abandoned
    browser flow, a profile whose credentials the workspace refused.

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
    #: The SDK auth type: ``DATABRICKS_AUTH_TYPE``'s value, or the one the profile
    #: declares (a profile need not declare one).
    sdk_auth_type: str | None = None

    @property
    def signs_in_as_user(self) -> bool:
        """Whether the SDK signs the user in (CLI, browser, or a profile), rather
        than haute presenting a token or service principal it holds."""
        return self.auth_mode in _USER_SIGN_IN_MODES

    def sign_in_failure(self) -> tuple[str, str]:
        """The problem and the remedy when this user sign-in cannot be completed.

        The host never appears: a message reaches API responses, which keep host
        values out; ``DATABRICKS_HOST`` is named in its place.
        """
        if self.auth_mode == "profile":
            subject = f"Databricks sign-in for profile '{self.profile}'"
            if self.sdk_auth_type == "external-browser":
                return (
                    f"{subject} did not complete",
                    "finish signing in in the browser and try again",
                )
            if self.sdk_auth_type in (None, "", "databricks-cli"):
                return (
                    f"{subject} failed or has expired",
                    f"run `databricks auth login --profile {self.profile}` and try again",
                )
            return (
                f"Databricks refused the credentials of profile '{self.profile}'",
                "check them in the Databricks CLI config file",
            )
        if self.auth_mode == "cli_login":
            return (
                "The Databricks CLI sign-in failed or has expired",
                "run `databricks auth login --host <your DATABRICKS_HOST>` and try again",
            )
        if self.auth_mode == "browser_login":
            return (
                "The Databricks browser sign-in did not complete",
                "finish signing in in the browser and try again",
            )
        raise ValueError(f"{self.auth_mode} is not a user sign-in")


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
    """The host and declared auth type of *profile* in the Databricks CLI's config file.

    Read as the SDK reads it: ``DATABRICKS_CONFIG_FILE`` or ``~/.databrickscfg``, an
    INI file whose ``DEFAULT`` section every profile inherits. Reading the file here,
    with the standard library, keeps resolution free of the SDK and of any sign-in
    side effect, so a status check can never open a browser.
    """
    path = Path(os.getenv("DATABRICKS_CONFIG_FILE", "").strip() or "~/.databrickscfg").expanduser()
    login = f"run `databricks auth login --host <workspace URL> --profile {profile}` to create it"
    parser = configparser.ConfigParser(interpolation=None)
    if not path.is_file():
        raise DatabricksConfigError(
            f"DATABRICKS_CONFIG_PROFILE names profile '{profile}', but there is no "
            f"Databricks CLI config file at {path}: {login}."
        )
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        # The parser's message quotes the offending line, which may be a token.
        raise DatabricksConfigError(
            f"The Databricks CLI config file {path} could not be read as a profiles file "
            f"(line {getattr(exc, 'lineno', '?')}): repair it, or {login}."
        ) from None
    if profile not in parser:
        raise DatabricksConfigError(
            f"DATABRICKS_CONFIG_PROFILE names profile '{profile}', which {path} does not "
            f"define: {login}."
        )
    section = parser[profile]
    host = section.get("host", "").strip().rstrip("/")
    if not host:
        raise DatabricksConfigError(
            f"Profile '{profile}' in {path} has no host: add `host = <workspace URL>` to it, "
            f"or sign in again with `databricks auth login --host <workspace URL> "
            f"--profile {profile}`."
        )
    return host, section.get("auth_type", "").strip()


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
        host, declared = _profile_host_and_auth_type(profile)
        if requirements:
            raise _missing(requirements)
        return _credentials(host, "profile", profile=profile, sdk_auth_type=declared or None)

    if auth_type:
        mode = USER_SIGN_IN_AUTH_TYPES.get(auth_type)
        if mode is None:
            raise DatabricksConfigError(
                "DATABRICKS_AUTH_TYPE must be databricks-cli (the sign-in the Databricks CLI "
                "holds) or external-browser (a browser sign-in the SDK opens)."
            )
        _refuse_beside(
            "DATABRICKS_AUTH_TYPE",
            {
                "DATABRICKS_TOKEN": token,
                "DATABRICKS_CLIENT_ID": client_id,
                "DATABRICKS_CLIENT_SECRET": client_secret,
            },
        )
        missing = ["DATABRICKS_HOST"] if not workspace_host else []
        missing.extend(requirements)
        if missing:
            raise _missing(missing)
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
    try:
        from databricks.sdk.errors import Unauthenticated
    except ImportError:  # pragma: no cover - the SDK raised exc, so it is installed
        return False
    if isinstance(exc, Unauthenticated):
        return True
    return isinstance(exc, ValueError) and str(exc).partition(":")[0] in _REFUSED_CREDENTIAL_CODES


def databricks_sdk_config(credentials: DatabricksCredentials) -> Any:
    """The SDK ``Config`` that performs a user sign-in (``signs_in_as_user``).

    Building it signs in: the CLI form runs ``databricks auth token``, the browser
    form may open the browser, and a profile does whatever its ``auth_type`` says,
    so this is called only where a connection is about to be made, never from a
    status check. A sign-in the SDK cannot complete is its construction-time
    ``ValueError``; it becomes a :class:`DatabricksSignInError` worded by haute.

    Raises :class:`ImportError` when the ``databricks`` extra is not installed;
    each caller words that for its own surface.
    """
    from databricks.sdk.core import Config

    if not credentials.signs_in_as_user:
        raise ValueError(f"{credentials.auth_mode} authenticates without an SDK config")
    if credentials.auth_mode == "profile":
        kwargs: dict[str, str | None] = {"profile": credentials.profile}
    else:
        kwargs = {"host": credentials.workspace_host, "auth_type": credentials.sdk_auth_type}
    try:
        return Config(**kwargs)
    except ValueError as exc:
        problem, remedy = credentials.sign_in_failure()
        raise DatabricksSignInError(problem, remedy) from exc


def databricks_workspace_client(credentials: DatabricksCredentials) -> Any:
    """A ``databricks.sdk.WorkspaceClient`` authenticated as *credentials* select.

    Raises :class:`ImportError` when the ``databricks`` extra is not installed;
    each caller words that for its own surface.
    """
    from databricks.sdk import WorkspaceClient

    if credentials.auth_mode == "pat":
        return WorkspaceClient(host=credentials.workspace_host, token=credentials.token)
    if credentials.auth_mode == "service_principal":
        return WorkspaceClient(
            host=credentials.workspace_host,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
        )
    return WorkspaceClient(config=databricks_sdk_config(credentials))
