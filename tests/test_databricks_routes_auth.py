"""Credential resolution for the Databricks workspace-browsing routes.

The /api/databricks/* helpers must accept either a PAT (local .env) or
the service-principal OAuth pair injected into a Databricks App
container — mirroring the `_databricks_io` precedence rules.
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi import HTTPException

from haute.routes.databricks import _get_databricks_client


@pytest.fixture()
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def workspace_client_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    recorded: dict[str, object] = {}

    def fake_client(**kwargs: object) -> str:
        recorded.update(kwargs)
        return "client"

    stub = types.ModuleType("databricks.sdk")
    stub.WorkspaceClient = fake_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "databricks.sdk", stub)
    return recorded


@pytest.mark.usefixtures("_clean_env")
def test_missing_credentials_reports_both_auth_options(
    workspace_client_stub: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    with pytest.raises(HTTPException) as exc_info:
        _get_databricks_client()
    assert exc_info.value.status_code == 503
    assert "DATABRICKS_TOKEN" in exc_info.value.detail
    assert "DATABRICKS_CLIENT_SECRET" in exc_info.value.detail


@pytest.mark.usefixtures("_clean_env")
def test_token_authenticates_and_takes_precedence(
    workspace_client_stub: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "pat")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")

    assert _get_databricks_client() == "client"
    assert workspace_client_stub == {"host": "workspace.example", "token": "pat"}


@pytest.mark.usefixtures("_clean_env")
def test_service_principal_pair_authenticates_without_token(
    workspace_client_stub: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")

    assert _get_databricks_client() == "client"
    assert workspace_client_stub == {
        "host": "workspace.example",
        "client_id": "sp-client",
        "client_secret": "sp-secret",
    }
