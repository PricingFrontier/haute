"""Tests for retained Databricks browsing endpoints."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


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


def test_get_databricks_client_failure_is_exposed_as_service_unavailable(
    client: TestClient,
) -> None:
    with patch(
        "haute.routes.databricks._get_databricks_client",
        side_effect=HTTPException(status_code=503, detail="unavailable"),
    ):
        response = client.get("/api/databricks/warehouses")
    assert response.status_code == 503
