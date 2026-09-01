"""Shared Databricks credential-resolution contract."""

from __future__ import annotations

import pytest

from haute._databricks_credentials import (
    DatabricksConfigError,
    resolve_databricks_credentials,
)
from haute._databricks_io import DatabricksConfigError as LegacyDatabricksConfigError


@pytest.fixture(autouse=True)
def _clean_databricks_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_pat_precedence_and_host_projections_are_resolved_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "  https://workspace.example/  ")
    monkeypatch.setenv("DATABRICKS_TOKEN", " pat-secret-value ")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "ignored-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "ignored-secret")

    credentials = resolve_databricks_credentials()

    assert credentials.workspace_host == "https://workspace.example"
    assert credentials.server_hostname == "workspace.example"
    assert credentials.auth_mode == "pat"
    assert credentials.token == " pat-secret-value "
    assert credentials.client_id is None
    assert credentials.client_secret is None


def test_service_principal_pair_is_selected_without_a_pat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example/")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "client-value")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "secret-value")

    credentials = resolve_databricks_credentials()

    assert credentials.workspace_host == "workspace.example"
    assert credentials.server_hostname == "workspace.example"
    assert credentials.auth_mode == "service_principal"
    assert credentials.token is None
    assert credentials.client_id == "client-value"
    assert credentials.client_secret == "secret-value"


def test_error_aggregates_named_requirements_without_credential_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "exposed-client-value")

    with pytest.raises(DatabricksConfigError) as exc_info:
        resolve_databricks_credentials(
            additional_missing=("http_path on the Data Input node",),
        )

    message = str(exc_info.value)
    assert "DATABRICKS_TOKEN" in message
    assert "DATABRICKS_CLIENT_SECRET" in message
    assert "http_path on the Data Input node" in message
    assert "exposed-client-value" not in message


def test_credential_representation_redacts_all_auth_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "client-value")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "secret-value")

    rendered = repr(resolve_databricks_credentials())

    assert "service_principal" in rendered
    assert "client-value" not in rendered
    assert "secret-value" not in rendered


def test_sql_io_reexports_the_shared_error_class() -> None:
    assert LegacyDatabricksConfigError is DatabricksConfigError
