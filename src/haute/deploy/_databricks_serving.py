"""The Databricks Model Serving endpoint: its credentials, its client and its readiness.

``haute deploy`` creates or updates the endpoint, and ``haute smoke`` and ``haute
impact`` query it. All three authenticate with the serving pair,
``DATABRICKS_RATING_HOST`` and ``DATABRICKS_RATING_TOKEN``: the MLflow pair registers
the model and the data-access pair (``DATABRICKS_HOST``/``DATABRICKS_TOKEN``) reads data,
and neither reaches the endpoint. The client is built from the pair alone with
personal-access-token auth, so no other Databricks variable or profile in the
environment changes which credentials it uses.

An update is finished once the endpoint is no longer updating and serves the version
asked for. An update Databricks reports failed or cancelled fails with what Databricks
says about it: each served entity's state message and the tails of its build and
server logs, so the reason a model could not be served is in the output of the step
that waited for it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from haute.errors import DeployError

RATING_HOST_ENV = "DATABRICKS_RATING_HOST"
RATING_TOKEN_ENV = "DATABRICKS_RATING_TOKEN"
SERVING_CREDENTIAL_NAMES = (RATING_HOST_ENV, RATING_TOKEN_ENV)

#: How long deploy and smoke wait for an endpoint to serve. A first deploy builds the
#: model's environment and provisions its container, which can take Databricks well
#: over ten minutes; a generated workflow's deploy job allows longer than this.
SERVING_READY_TIMEOUT_SECONDS = 45 * 60
_POLL_SECONDS = 15.0
_LOG_TAIL_LINES = 40
_FAILED_UPDATES = frozenset({"UPDATE_FAILED", "UPDATE_CANCELED"})


class ServingEndpointError(DeployError):
    """The serving endpoint's credentials are missing, or it could not be made to serve."""


@dataclass(frozen=True)
class ServingCredentials:
    """The serving pair: the workspace that hosts the endpoint and a token for it."""

    host: str
    token: str = field(repr=False)


def missing_serving_credentials(environ: Mapping[str, str] | None = None) -> list[str]:
    """The serving pair's variables that are unset or blank in *environ* (``os.environ``)."""
    env = os.environ if environ is None else environ
    return [name for name in SERVING_CREDENTIAL_NAMES if not env.get(name, "").strip()]


def refusal_reason(exc: BaseException) -> str:
    """Databricks' message for a refused request, with the gatekeeper's decision if it made one.

    A workspace Databricks has deactivated answers every compute and serving request with
    "please try again later"; why is only in the response's ``ErrorInfo``, from
    Databricks' resource gatekeeper (``DENY_NEW_AND_EXISTING_RESOURCES``, with a
    ``denyReason`` such as ``INACTIVE``), and retrying cannot succeed until it is lifted.
    """
    message = str(exc)
    get_error_info = getattr(exc, "get_error_info", None)
    for info in get_error_info() if callable(get_error_info) else []:
        if info.domain != "resource-gatekeeper":
            continue
        deny = (info.metadata or {}).get("denyReason")
        decision = ", ".join(part for part in (info.reason, deny) if part)
        if deny == "INACTIVE":
            return (
                f"{message} Databricks has marked the workspace inactive ({decision}), so it "
                "refuses every compute and serving request until the workspace is "
                "reactivated; retrying will not help. Reactivate the workspace (its account "
                "admin or Databricks support can), then deploy again."
            )
        return f"{message} Databricks' resource gatekeeper refused it ({decision})."
    return message


def missing_serving_credentials_message(missing: list[str]) -> str:
    """Why the serving endpoint cannot be reached when *missing* variables are unset."""
    verb = "is" if len(missing) == 1 else "are"
    return (
        f"{' and '.join(missing)} {verb} not set: the Databricks serving endpoint is reached "
        f"with {RATING_HOST_ENV} and {RATING_TOKEN_ENV}. Set them in .env, or as CI secrets "
        "(on GitHub, `haute ci secrets` sets every secret the deploy needs from .env)."
    )


def serving_credentials() -> ServingCredentials:
    """The serving pair from the environment, or ``ServingEndpointError`` naming what is unset."""
    missing = missing_serving_credentials()
    if missing:
        raise ServingEndpointError(missing_serving_credentials_message(missing))
    return ServingCredentials(
        host=os.environ[RATING_HOST_ENV].strip().rstrip("/"),
        token=os.environ[RATING_TOKEN_ENV].strip(),
    )


def pat_workspace_client(host: str, token: str) -> Any:
    """A ``WorkspaceClient`` for *host* that authenticates with *token* and nothing else.

    ``auth_type="pat"`` picks the token over any other credential the environment sets
    (a service principal's ``DATABRICKS_CLIENT_ID``, say), and the empty profile name
    stops the SDK reading ``DATABRICKS_CONFIG_PROFILE`` and a config file: an ambient
    profile, or one the file does not hold, would otherwise change the client or fail it.
    """
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(host=host, token=token, auth_type="pat", profile="")


def serving_workspace_client(credentials: ServingCredentials | None = None) -> Any:
    """A ``WorkspaceClient`` authenticated with the serving pair and nothing else."""
    resolved = credentials if credentials is not None else serving_credentials()
    return pat_workspace_client(resolved.host, resolved.token)


def enum_value(value: object) -> str:
    """An SDK enum's value (``EndpointStateReady.READY`` -> ``"READY"``); ``""`` when unset.

    A value already rendered as ``"EndpointStateReady.READY"`` reads the same.
    """
    if value is None:
        return ""
    return str(getattr(value, "value", value)).rsplit(".", 1)[-1]


def endpoint_update_state(endpoint: Any) -> str:
    """The endpoint's config-update state: ``NOT_UPDATING``, ``IN_PROGRESS``, … or ``""``."""
    state = getattr(endpoint, "state", None)
    return enum_value(getattr(state, "config_update", None))


def _served_entities(config: Any) -> list[Any]:
    return list(getattr(config, "served_entities", None) or [])


def _describe(endpoint: Any, version: str | None) -> str:
    """One line on where the endpoint's update has got to."""
    state = getattr(endpoint, "state", None)
    ready = enum_value(getattr(state, "ready", None)) or "UNKNOWN"
    update = enum_value(getattr(state, "config_update", None)) or "UNKNOWN"
    parts = [f"ready={ready}", f"config_update={update}"]
    for entity in _served_entities(getattr(endpoint, "pending_config", None)):
        entity_state = getattr(entity, "state", None)
        deployment = enum_value(getattr(entity_state, "deployment", None))
        message = getattr(entity_state, "deployment_state_message", None) or ""
        detail = f"{deployment}: {message}" if message else deployment
        parts.append(f"{entity.name} {detail}".strip())
    serving = [
        str(entity.entity_version) for entity in _served_entities(getattr(endpoint, "config", None))
    ]
    if serving:
        parts.append(f"serving version {', '.join(serving)}")
    if version is not None and version not in serving:
        parts.append(f"waiting for version {version}")
    return "; ".join(parts)


def _log_tail(fetch: Callable[[], Any], what: str) -> list[str]:
    """The last lines of a log Databricks returns, or why it could not be read."""
    try:
        logs = getattr(fetch(), "logs", None) or ""
    except Exception as exc:  # noqa: BLE001 - the failure being reported matters more
        return [f"({what} unavailable: {exc})"]
    lines = [line for line in str(logs).splitlines() if line.strip()]
    if not lines:
        return []
    shown = lines[-_LOG_TAIL_LINES:]
    return [f"{what}, last {len(shown)} lines:", *(f"  {line}" for line in shown)]


def _failure_message(ws: Any, endpoint: Any, name: str, update: str, version: str | None) -> str:
    """What Databricks says about a failed or cancelled update of endpoint *name*."""
    entities = _served_entities(getattr(endpoint, "pending_config", None)) or _served_entities(
        getattr(endpoint, "config", None)
    )
    if version is not None:
        entities = [e for e in entities if str(e.entity_version) == version] or entities
    lines = [f"Databricks could not serve endpoint '{name}': its update is {update}."]
    for entity in entities:
        entity_state = getattr(entity, "state", None)
        deployment = enum_value(getattr(entity_state, "deployment", None))
        message = getattr(entity_state, "deployment_state_message", None) or ""
        lines.append(
            f"Served entity {entity.name} (version {entity.entity_version}): "
            f"{deployment} {message}".rstrip()
        )
        for fetch, what in (
            (ws.serving_endpoints.build_logs, "Build log"),
            (ws.serving_endpoints.logs, "Server log"),
        ):
            tail = _log_tail(partial(fetch, name=name, served_model_name=entity.name), what)
            lines.extend(f"  {line}" for line in tail)
    return "\n".join(lines)


def wait_for_endpoint(
    ws: Any,
    name: str,
    *,
    version: str | None = None,
    progress: Callable[[str], None],
    timeout_seconds: float = SERVING_READY_TIMEOUT_SECONDS,
    poll_seconds: float = _POLL_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Return once endpoint *name* is ready, not updating, and serving *version* when given.

    *progress* hears each change of state. An update Databricks reports failed or
    cancelled raises :class:`ServingEndpointError` with its state messages and log
    tails, and so does polling for *timeout_seconds*, with the last state seen. The
    time waited is counted in polls of *poll_seconds* (each poll's request adds to the
    real time). *sleep* defaults to ``time.sleep`` as it is when called. The SDK's
    ``NotFound`` for an endpoint that does not exist propagates.
    """
    pause = time.sleep if sleep is None else sleep
    waited = 0.0
    last_status = ""
    while True:
        endpoint = ws.serving_endpoints.get(name)
        state = getattr(endpoint, "state", None)
        update = enum_value(getattr(state, "config_update", None))
        if update in _FAILED_UPDATES:
            raise ServingEndpointError(_failure_message(ws, endpoint, name, update, version))
        serving = {
            str(entity.entity_version)
            for entity in _served_entities(getattr(endpoint, "config", None))
        }
        ready = enum_value(getattr(state, "ready", None)) == "READY"
        if ready and update in ("", "NOT_UPDATING") and (version is None or version in serving):
            return
        status = _describe(endpoint, version)
        if status != last_status:
            progress(f"Endpoint '{name}': {status}")
            last_status = status
        if waited >= timeout_seconds:
            minutes = round(timeout_seconds / 60)
            raise ServingEndpointError(
                f"Endpoint '{name}' was not serving "
                f"{f'version {version}' if version is not None else 'a ready version'} "
                f"after {minutes} minute{'' if minutes == 1 else 's'} ({status}). Its progress "
                "is on the endpoint's page under Serving in the Databricks workspace."
            )
        pause(poll_seconds)
        waited += poll_seconds


def workspace_user(host: str, token: str) -> str:
    """The user a personal access token authenticates as on workspace *host*.

    Raises whatever the workspace answers for a host or token it refuses.
    """
    me = pat_workspace_client(host, token).current_user.me()
    return str(me.user_name or me.display_name or me.id or "")
