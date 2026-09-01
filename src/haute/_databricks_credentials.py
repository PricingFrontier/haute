"""Shared Databricks credential resolution with redaction-safe errors."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from haute.errors import HauteError


class DatabricksConfigError(HauteError):
    """Raised when Databricks configuration is incomplete or unavailable."""


@dataclass(frozen=True, slots=True)
class DatabricksCredentials:
    """Normalised Databricks endpoint and selected authentication material."""

    workspace_host: str
    server_hostname: str
    auth_mode: Literal["pat", "service_principal"]
    token: str | None = field(repr=False)
    client_id: str | None = field(repr=False)
    client_secret: str | None = field(repr=False)


def _normalise_server_hostname(workspace_host: str) -> str:
    """Convert an optional-protocol workspace URL to the SQL hostname."""
    return re.sub(r"^https?://", "", workspace_host, count=1, flags=re.IGNORECASE)


def resolve_databricks_credentials(
    *, additional_missing: Sequence[str] = ()
) -> DatabricksCredentials:
    """Resolve the selected Databricks authentication form from the environment.

    A non-empty PAT takes precedence over service-principal fields.  Error
    messages list only configuration names, never the values read from the
    environment.
    """
    workspace_host = os.getenv("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.getenv("DATABRICKS_TOKEN", "")
    client_id = os.getenv("DATABRICKS_CLIENT_ID", "")
    client_secret = os.getenv("DATABRICKS_CLIENT_SECRET", "")

    missing: list[str] = []
    if not workspace_host:
        missing.append("DATABRICKS_HOST")

    if token:
        auth_mode: Literal["pat", "service_principal"] = "pat"
    elif client_id and client_secret:
        auth_mode = "service_principal"
    else:
        missing.append("DATABRICKS_TOKEN")
        if not client_id:
            missing.append("DATABRICKS_CLIENT_ID")
        if not client_secret:
            missing.append("DATABRICKS_CLIENT_SECRET")

    missing.extend(requirement for requirement in additional_missing if requirement)
    if missing:
        raise DatabricksConfigError("Missing Databricks configuration:\n  " + "\n  ".join(missing))

    if auth_mode == "pat":
        return DatabricksCredentials(
            workspace_host=workspace_host,
            server_hostname=_normalise_server_hostname(workspace_host),
            auth_mode=auth_mode,
            token=token,
            client_id=None,
            client_secret=None,
        )
    return DatabricksCredentials(
        workspace_host=workspace_host,
        server_hostname=_normalise_server_hostname(workspace_host),
        auth_mode=auth_mode,
        token=None,
        client_id=client_id,
        client_secret=client_secret,
    )
