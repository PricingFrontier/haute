"""Tests for the Databricks browsing, connection and connection-test endpoints."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing
from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Response

from haute._databricks_credentials import DatabricksSignInError
from tests._databricks_workspace_stub import workspace_stub


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "token")
    for name in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    for name in ("DATABRICKS_AUTH_TYPE", "DATABRICKS_CONFIG_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    # Neither haute nor the SDK may read the developer's own ~/.databrickscfg.
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "no-profiles.cfg"))
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


# ---------------------------------------------------------------------------
# User sign-ins: an expired CLI login while browsing, the connection, the test
# ---------------------------------------------------------------------------

_EXPIRED_PROFILE = DatabricksSignInError(
    "Databricks sign-in for profile 'work' failed or has expired",
    "run `databricks auth login --profile work` and try again",
)


def _profile_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth_type: str) -> None:
    """Select profile ``work`` of a config file under *tmp_path*, and nothing else."""
    profiles = tmp_path / "databrickscfg"
    profiles.write_text(
        f"[work]\nhost = https://workspace.example\nauth_type = {auth_type}\n", encoding="utf-8"
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(profiles))
    monkeypatch.delenv("DATABRICKS_HOST")
    monkeypatch.delenv("DATABRICKS_TOKEN")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "work")


def test_an_expired_cli_sign_in_answers_401_with_the_command_while_browsing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The SDK fails a dead CLI login as the client is built, before any listing."""
    _profile_env(monkeypatch, tmp_path, "databricks-cli")

    with (
        patch("haute.routes.databricks.databricks_workspace_client", side_effect=_EXPIRED_PROFILE),
        structlog.testing.capture_logs() as logs,
    ):
        response = client.get("/api/databricks/catalogs")

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "Databricks sign-in for profile 'work' failed or has expired, so the catalogs could "
        "not be listed: run `databricks auth login --profile work` and try again."
    )
    (entry,) = [log for log in logs if log["event"] == "databricks_browsing_failed"]
    assert entry["error_type"] == "DatabricksSignInError"


def test_the_connection_names_the_form_and_never_the_host(client: TestClient) -> None:
    response = client.get("/api/databricks/connection")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "auth_mode": "pat",
        "profile": "",
        "sdk_auth_type": "",
        "detail": "",
    }
    assert "workspace.example" not in response.text


def test_the_connection_reports_a_profile_and_its_declared_auth_type(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile_env(monkeypatch, tmp_path, "databricks-cli")

    body = client.get("/api/databricks/connection").json()

    assert body["configured"] is True
    assert body["auth_mode"] == "profile"
    assert body["profile"] == "work"
    assert body["sdk_auth_type"] == "databricks-cli"
    assert "workspace.example" not in str(body)


def test_the_connection_never_signs_in(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A browser sign-in opens a browser when the SDK client is built; the connection
    answer must come from the environment alone, so a page load never does that."""
    monkeypatch.delenv("DATABRICKS_TOKEN")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "external-browser")

    with patch(
        "haute.routes.databricks.databricks_workspace_client",
        side_effect=AssertionError("the connection must not build a client"),
    ):
        body = client.get("/api/databricks/connection").json()

    assert body["configured"] is True
    assert body["auth_mode"] == "browser_login"
    assert body["sdk_auth_type"] == "external-browser"


def test_the_connection_says_what_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABRICKS_TOKEN")

    body = client.get("/api/databricks/connection").json()

    assert body["configured"] is False
    assert body["auth_mode"] == ""
    for name in ("DATABRICKS_TOKEN", "DATABRICKS_AUTH_TYPE", "DATABRICKS_CONFIG_PROFILE"):
        assert name in body["detail"]


def _signed_in_workspace(user_name: str | None, display_name: str, auth_type: object) -> MagicMock:
    workspace = MagicMock()
    workspace.current_user.me.return_value = SimpleNamespace(
        user_name=user_name, display_name=display_name
    )
    workspace.config.auth_type = auth_type
    return workspace


def test_the_connection_test_answers_who_haute_signs_in_as(client: TestClient) -> None:
    workspace = _signed_in_workspace("nick@example.com", "Nick", "pat")

    with patch("haute.routes.databricks.databricks_workspace_client", return_value=workspace):
        response = client.post("/api/databricks/test-connection")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "identity": "nick@example.com",
        "sdk_auth_type": "pat",
        "detail": "",
    }


def test_the_connection_test_falls_back_to_the_display_name_for_a_service_principal(
    client: TestClient,
) -> None:
    workspace = _signed_in_workspace(None, "haute-app", object())

    with patch("haute.routes.databricks.databricks_workspace_client", return_value=workspace):
        body = client.post("/api/databricks/test-connection").json()

    assert body["identity"] == "haute-app"
    assert body["sdk_auth_type"] == ""


def test_the_connection_test_reports_an_expired_sign_in_without_a_5xx(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile_env(monkeypatch, tmp_path, "databricks-cli")

    with (
        patch("haute.routes.databricks.databricks_workspace_client", side_effect=_EXPIRED_PROFILE),
        structlog.testing.capture_logs() as logs,
    ):
        response = client.post("/api/databricks/test-connection")

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "identity": "",
        "sdk_auth_type": "",
        "detail": (
            "Databricks sign-in for profile 'work' failed or has expired: "
            "run `databricks auth login --profile work` and try again."
        ),
    }
    (entry,) = [log for log in logs if log["event"] == "databricks_connection_test_failed"]
    assert entry["error_type"] == "DatabricksSignInError"


def test_the_connection_test_reports_a_refused_token(client: TestClient) -> None:
    from databricks.sdk.errors import Unauthenticated

    workspace = MagicMock()
    workspace.current_user.me.side_effect = Unauthenticated("Invalid access token.")

    with patch("haute.routes.databricks.databricks_workspace_client", return_value=workspace):
        body = client.post("/api/databricks/test-connection").json()

    assert body["ok"] is False
    assert body["detail"] == (
        "Databricks refused DATABRICKS_TOKEN: set a current personal access token in .env."
    )


def test_the_connection_test_reports_any_other_sdk_failure_with_its_text_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from databricks.sdk.errors import TemporarilyUnavailable

    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-secret-value")
    workspace = MagicMock()
    workspace.current_user.me.side_effect = TemporarilyUnavailable(
        "Upstream busy for dapi-secret-value."
    )

    with (
        patch("haute.routes.databricks.databricks_workspace_client", return_value=workspace),
        structlog.testing.capture_logs() as logs,
    ):
        body = client.post("/api/databricks/test-connection").json()

    assert body == {
        "ok": False,
        "identity": "",
        "sdk_auth_type": "",
        "detail": (
            "Databricks did not answer the connection test: check the server logs for details."
        ),
    }
    (entry,) = [log for log in logs if log["event"] == "databricks_connection_test_failed"]
    assert "Upstream busy" in entry["error"]
    assert "dapi-secret-value" not in entry["error"]


def test_the_connection_test_reports_missing_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABRICKS_TOKEN")

    body = client.post("/api/databricks/test-connection").json()

    assert body["ok"] is False
    assert body["detail"].startswith("Missing Databricks configuration:")


def test_the_connection_test_reports_a_missing_sdk(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "databricks.sdk", None)

    body = client.post("/api/databricks/test-connection").json()

    assert body == {
        "ok": False,
        "identity": "",
        "sdk_auth_type": "",
        "detail": "databricks-sdk is not installed. Install with: pip install haute[databricks]",
    }
