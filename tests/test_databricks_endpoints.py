"""Tests for retained Databricks browsing endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing
from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Response

from tests._databricks_workspace_stub import workspace_stub


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    (tmp_path / "main.py").write_text("")
    from haute.server import app

    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "attribute", "params", "key"),
    [
        ("/api/databricks/warehouses", "warehouses.list", {}, "warehouses"),
        ("/api/databricks/catalogs", "catalogs.list", {}, "catalogs"),
        ("/api/databricks/schemas", "schemas.list", {"catalog": "main"}, "schemas"),
        (
            "/api/databricks/tables",
            "tables.list",
            {"catalog": "main", "schema": "pricing"},
            "tables",
        ),
    ],
)
def test_browsing_endpoints_return_empty_lists(
    client: TestClient, path: str, attribute: str, params: dict[str, str], key: str
) -> None:
    workspace = MagicMock()
    target = workspace
    for part in attribute.split("."):
        target = getattr(target, part)
    target.return_value = []
    with patch("haute.routes.databricks._get_databricks_client", return_value=workspace):
        response = client.get(path, params=params)
    assert response.status_code == 200
    assert response.json()[key] == []


def test_warehouses_report_serverless_compute(client: TestClient) -> None:
    workspace = MagicMock()
    workspace.warehouses.list.return_value = [
        SimpleNamespace(
            id="abc",
            name="Serverless Starter",
            state=SimpleNamespace(value="RUNNING"),
            cluster_size="2X-Small",
            enable_serverless_compute=True,
        ),
        SimpleNamespace(
            id="def",
            name="Classic",
            state=None,
            cluster_size=None,
            enable_serverless_compute=None,
        ),
    ]
    with patch("haute.routes.databricks._get_databricks_client", return_value=workspace):
        response = client.get("/api/databricks/warehouses")
    assert response.json()["warehouses"] == [
        {
            "id": "abc",
            "name": "Serverless Starter",
            "http_path": "/sql/1.0/warehouses/abc",
            "state": "RUNNING",
            "size": "2X-Small",
            "serverless": True,
        },
        {
            "id": "def",
            "name": "Classic",
            "http_path": "/sql/1.0/warehouses/def",
            "state": "UNKNOWN",
            "size": "",
            "serverless": False,
        },
    ]


def test_tables_constructs_full_name_when_sdk_omits_it(client: TestClient) -> None:
    workspace = MagicMock()
    workspace.tables.list.return_value = [
        SimpleNamespace(name="claims", full_name=None, table_type=None, comment=None)
    ]
    with patch("haute.routes.databricks._get_databricks_client", return_value=workspace):
        response = client.get(
            "/api/databricks/tables", params={"catalog": "prod", "schema": "insurance"}
        )
    assert response.json()["tables"] == [
        {"name": "claims", "full_name": "prod.insurance.claims", "table_type": "", "comment": ""}
    ]


def _failing_workspace(attribute: str, error: Exception) -> MagicMock:
    """A workspace whose *attribute* listing raises *error* once iterated, as the SDK's
    paginated listings do."""

    def listing(**_: object) -> Iterator[object]:
        raise error
        yield  # pragma: no cover - makes this a generator

    workspace = MagicMock()
    target = workspace
    for part in attribute.split("."):
        target = getattr(target, part)
    target.side_effect = listing
    return workspace


def _browse(client: TestClient, workspace: MagicMock, path: str, **params: str) -> Response:
    with patch("haute.routes.databricks._get_databricks_client", return_value=workspace):
        return client.get(path, params=params)


# BUG-44: an SDK failure reached the analyst as the sanitized "Operation failed" 500.


def test_a_missing_catalog_answers_404_naming_it(client: TestClient) -> None:
    from databricks.sdk.errors import NotFound

    workspace = _failing_workspace("schemas.list", NotFound("Catalog 'prod' does not exist."))

    response = _browse(client, workspace, "/api/databricks/schemas", catalog="prod")

    assert response.status_code == 404
    assert response.json()["detail"] == "Catalog 'prod' was not found in the Databricks workspace."


def test_a_missing_schema_answers_404_naming_it(client: TestClient) -> None:
    from databricks.sdk.errors import NotFound

    workspace = _failing_workspace("tables.list", NotFound("Schema does not exist."))

    response = _browse(
        client, workspace, "/api/databricks/tables", catalog="prod", schema="insurance"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "Schema 'prod.insurance' was not found in the Databricks workspace."
    )


def test_a_refused_token_answers_401_naming_its_variable(client: TestClient) -> None:
    from databricks.sdk.errors import Unauthenticated

    workspace = _failing_workspace("catalogs.list", Unauthenticated("Invalid access token."))

    response = _browse(client, workspace, "/api/databricks/catalogs")

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "Databricks refused DATABRICKS_TOKEN, so the catalogs could not be listed: "
        "set a current personal access token in .env."
    )


def test_a_refused_service_principal_answers_401_naming_its_pair(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real SDK's OAuth exchange refuses a service principal with a plain
    ``ValueError("invalid_client: ...")``, which no ``DatabricksError`` handler sees."""
    with workspace_stub(accepted_secret="current-secret") as workspace:
        monkeypatch.setenv("DATABRICKS_HOST", workspace.host)
        monkeypatch.delenv("DATABRICKS_TOKEN")
        monkeypatch.setenv("DATABRICKS_CLIENT_ID", "app-id")
        monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "revoked-secret")
        monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "no-profiles.cfg"))

        refused = client.get("/api/databricks/catalogs")
        monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "current-secret")
        corrected = client.get("/api/databricks/catalogs")

    assert refused.status_code == 401
    assert refused.json()["detail"] == (
        "Databricks refused DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET, so the "
        "catalogs could not be listed: check the service principal's credentials in .env."
    )
    assert corrected.status_code == 200
    assert corrected.json()["catalogs"] == [{"name": "main", "comment": ""}]
    assert workspace.token_requests[0] == "revoked-secret"
    assert workspace.token_requests[-1] == "current-secret"


def test_a_missing_grant_answers_403_naming_what_was_listed(client: TestClient) -> None:
    from databricks.sdk.errors import PermissionDenied

    workspace = _failing_workspace("tables.list", PermissionDenied("User lacks USE SCHEMA."))

    response = _browse(
        client, workspace, "/api/databricks/tables", catalog="prod", schema="insurance"
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "The Databricks credentials may not list the tables of schema 'prod.insurance': "
        "ask a workspace admin for access."
    )


def test_any_other_sdk_failure_answers_502_and_logs_its_scrubbed_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from databricks.sdk.errors import TemporarilyUnavailable

    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-secret-value")
    workspace = _failing_workspace(
        "catalogs.list", TemporarilyUnavailable("Upstream busy for dapi-secret-value.")
    )

    with structlog.testing.capture_logs() as logs:
        response = _browse(client, workspace, "/api/databricks/catalogs")

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Databricks could not list the catalogs. Check the server logs for details."
    )
    (entry,) = [log for log in logs if log["event"] == "databricks_browsing_failed"]
    assert entry["error_type"] == "TemporarilyUnavailable"
    assert "Upstream busy" in entry["error"]
    assert "dapi-secret-value" not in entry["error"]


def test_get_databricks_client_failure_is_exposed_as_service_unavailable(
    client: TestClient,
) -> None:
    with patch(
        "haute.routes.databricks._get_databricks_client",
        side_effect=HTTPException(status_code=503, detail="unavailable"),
    ):
        response = client.get("/api/databricks/warehouses")
    assert response.status_code == 503
