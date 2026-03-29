"""Tests for Databricks browsing API endpoints (mocked — no real Databricks connection).

Covers:
  - GET /api/databricks/warehouses: success, empty list, exception, missing creds
  - GET /api/databricks/catalogs: success
  - GET /api/databricks/schemas: success, missing catalog param
  - GET /api/databricks/tables: success, full_name fallback construction,
    missing params, tables with None name skipped
  - POST /api/databricks/fetch: success, timeout (504), ImportError (400),
    generic exception (500)
  - GET /api/databricks/fetch/progress: active, not active
  - GET /api/databricks/cache: cached, not cached
  - DELETE /api/databricks/cache: success, already missing
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABRICKS_HOST", "https://test.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")
    (tmp_path / "main.py").write_text("")
    from haute.server import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/databricks/warehouses
# ---------------------------------------------------------------------------


class TestListWarehouses:
    def test_returns_warehouse_list(self, client: TestClient) -> None:
        from databricks.sdk.service.sql import EndpointInfo, State

        wh = EndpointInfo(
            id="abc123",
            name="Starter Warehouse",
            state=State.RUNNING,
            cluster_size="Small",
        )

        mock_ws = MagicMock()
        mock_ws.warehouses.list.return_value = [wh]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/warehouses")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["warehouses"]) == 1
        wh = data["warehouses"][0]
        assert wh["id"] == "abc123"
        assert wh["name"] == "Starter Warehouse"
        assert wh["http_path"] == "/sql/1.0/warehouses/abc123"
        assert wh["state"] == "RUNNING"
        assert wh["size"] == "Small"

    def test_empty_warehouse_list(self, client: TestClient) -> None:
        """Returns empty warehouses list when none exist."""
        mock_ws = MagicMock()
        mock_ws.warehouses.list.return_value = []

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/warehouses")

        assert resp.status_code == 200
        data = resp.json()
        assert data["warehouses"] == []

    def test_exception_returns_500(self, client: TestClient) -> None:
        """Unexpected exception from Databricks SDK returns 500 without leaking details."""
        mock_ws = MagicMock()
        mock_ws.warehouses.list.side_effect = RuntimeError("network issue")

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/warehouses")

        assert resp.status_code == 500
        assert "network issue" not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]

    def test_warehouse_without_state(self, client: TestClient) -> None:
        """Warehouse with state=None returns UNKNOWN."""
        from databricks.sdk.service.sql import EndpointInfo

        wh = EndpointInfo(
            id="xyz",
            name="No State WH",
            state=None,
            cluster_size=None,
        )

        mock_ws = MagicMock()
        mock_ws.warehouses.list.return_value = [wh]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/warehouses")

        assert resp.status_code == 200
        wh_data = resp.json()["warehouses"][0]
        assert wh_data["state"] == "UNKNOWN"
        assert wh_data["size"] == ""

    def test_missing_credentials_returns_400(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        from haute.server import app

        c = TestClient(app)
        resp = c.get("/api/databricks/warehouses")
        assert resp.status_code == 503
        assert "DATABRICKS_HOST" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/databricks/catalogs
# ---------------------------------------------------------------------------


class TestListCatalogs:
    def test_returns_catalog_list(self, client: TestClient) -> None:
        from databricks.sdk.service.catalog import CatalogInfo

        cat = CatalogInfo(name="main", comment="Default catalog")

        mock_ws = MagicMock()
        mock_ws.catalogs.list.return_value = [cat]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/catalogs")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["catalogs"]) == 1
        assert data["catalogs"][0]["name"] == "main"
        assert data["catalogs"][0]["comment"] == "Default catalog"


# ---------------------------------------------------------------------------
# GET /api/databricks/schemas
# ---------------------------------------------------------------------------


class TestListSchemas:
    def test_returns_schema_list(self, client: TestClient) -> None:
        from databricks.sdk.service.catalog import SchemaInfo

        sch = SchemaInfo(name="pricing", comment="")

        mock_ws = MagicMock()
        mock_ws.schemas.list.return_value = [sch]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/schemas", params={"catalog": "main"})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["schemas"]) == 1
        assert data["schemas"][0]["name"] == "pricing"

    def test_missing_catalog_param_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/databricks/schemas")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/databricks/tables
# ---------------------------------------------------------------------------


class TestListTables:
    def test_returns_table_list(self, client: TestClient) -> None:
        from databricks.sdk.service.catalog import TableInfo, TableType

        tbl = TableInfo(
            name="policies",
            full_name="main.pricing.policies",
            table_type=TableType.MANAGED,
            comment="Policy data",
        )

        mock_ws = MagicMock()
        mock_ws.tables.list.return_value = [tbl]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get(
                "/api/databricks/tables",
                params={"catalog": "main", "schema": "pricing"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tables"]) == 1
        tbl = data["tables"][0]
        assert tbl["name"] == "policies"
        assert tbl["full_name"] == "main.pricing.policies"
        assert tbl["table_type"] == "MANAGED"
        assert tbl["comment"] == "Policy data"

    def test_missing_params_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/databricks/tables", params={"catalog": "main"})
        assert resp.status_code == 422

    def test_full_name_fallback_construction(self, client: TestClient) -> None:
        """When full_name is None, it is constructed from catalog.schema.name."""
        from databricks.sdk.service.catalog import TableInfo, TableType

        tbl = TableInfo(
            name="claims",
            full_name=None,
            table_type=TableType.EXTERNAL,
            comment="",
        )

        mock_ws = MagicMock()
        mock_ws.tables.list.return_value = [tbl]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get(
                "/api/databricks/tables",
                params={"catalog": "prod", "schema": "insurance"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["tables"][0]["full_name"] == "prod.insurance.claims"

    def test_tables_with_none_name_skipped(self, client: TestClient) -> None:
        """Tables where name is None are filtered out."""
        from databricks.sdk.service.catalog import TableInfo, TableType

        valid = TableInfo(
            name="valid_tbl",
            full_name="cat.sch.valid_tbl",
            table_type=TableType.MANAGED,
            comment="",
        )
        invalid = TableInfo(name=None, full_name=None, table_type=None, comment="")

        mock_ws = MagicMock()
        mock_ws.tables.list.return_value = [valid, invalid]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get(
                "/api/databricks/tables",
                params={"catalog": "cat", "schema": "sch"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tables"]) == 1
        assert data["tables"][0]["name"] == "valid_tbl"

    def test_exception_returns_500(self, client: TestClient) -> None:
        """Unexpected error from tables.list returns 500 without leaking details."""
        mock_ws = MagicMock()
        mock_ws.tables.list.side_effect = RuntimeError("quota exceeded")

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get(
                "/api/databricks/tables",
                params={"catalog": "cat", "schema": "sch"},
            )

        assert resp.status_code == 500
        assert "quota exceeded" not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/databricks/cache
# ---------------------------------------------------------------------------


class TestCacheStatus:
    def test_not_cached(self, client: TestClient) -> None:
        resp = client.get("/api/databricks/cache", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["table"] == "cat.sch.tbl"

    def test_cached_after_write(self, client: TestClient) -> None:
        import polars as pl

        from haute._databricks_io import _cache_path_for

        p = _cache_path_for("cat.sch.tbl")  # uses Path.cwd() set by client fixture
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        resp = client.get("/api/databricks/cache", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is True
        assert data["row_count"] == 3
        assert data["column_count"] == 1
        assert data["size_bytes"] > 0

    def test_delete_cache(self, client: TestClient) -> None:
        import polars as pl

        from haute._databricks_io import _cache_path_for

        p = _cache_path_for("cat.sch.tbl")
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1]}).write_parquet(p)
        assert p.exists()

        resp = client.delete("/api/databricks/cache", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert not p.exists()

    def test_delete_cache_noop_when_missing(self, client: TestClient) -> None:
        resp = client.delete("/api/databricks/cache", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 200
        assert resp.json()["cached"] is False


# ---------------------------------------------------------------------------
# GET /api/databricks/fetch/progress
# ---------------------------------------------------------------------------


class TestFetchProgress:
    @pytest.fixture(autouse=True)
    def _cleanup_progress(self):
        yield
        from haute._databricks_io import _clear_fetch_progress

        _clear_fetch_progress()

    def test_no_active_fetch(self, client: TestClient) -> None:
        resp = client.get("/api/databricks/fetch/progress", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_active_fetch(self, client: TestClient) -> None:
        from haute._databricks_io import _set_fetch_progress

        _set_fetch_progress("cat.sch.tbl", {"rows": 200_000, "batches": 2, "elapsed": 3.5})

        resp = client.get("/api/databricks/fetch/progress", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["rows"] == 200_000
        assert data["batches"] == 2
        assert data["elapsed"] == 3.5


# ---------------------------------------------------------------------------
# POST /api/databricks/fetch
# ---------------------------------------------------------------------------


class TestFetchTable:
    def test_fetch_success(self, client: TestClient) -> None:
        from haute._databricks_io import _cache_path_for

        fake_result = {
            "path": str(_cache_path_for("cat.sch.tbl")),
            "table": "cat.sch.tbl",
            "row_count": 100,
            "column_count": 3,
            "columns": {"a": "Int64", "b": "Utf8", "c": "Float64"},
            "size_bytes": 4096,
            "fetched_at": 1700000000.0,
            "fetch_seconds": 1.5,
        }

        with patch("haute._databricks_io.fetch_and_cache", return_value=fake_result):
            resp = client.post(
                "/api/databricks/fetch",
                json={
                    "table": "cat.sch.tbl",
                    "http_path": "/sql/wh",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["table"] == "cat.sch.tbl"
        assert data["row_count"] == 100
        assert data["column_count"] == 3
        assert data["size_bytes"] == 4096
        assert data["fetch_seconds"] == 1.5

    def test_fetch_missing_connector_returns_400(self, client: TestClient) -> None:
        with patch("haute._databricks_io.fetch_and_cache", side_effect=ImportError("no module")):
            resp = client.post("/api/databricks/fetch", json={"table": "cat.sch.tbl"})
        assert resp.status_code == 400
        assert "databricks-sql-connector" in resp.json()["detail"]

    def test_fetch_timeout_returns_504(self, client: TestClient) -> None:
        """Fetch exceeding timeout returns 504."""
        with patch("asyncio.wait_for", side_effect=TimeoutError("timed out")):
            resp = client.post(
                "/api/databricks/fetch",
                json={
                    "table": "cat.sch.big_table",
                    "http_path": "/sql/wh",
                },
            )

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]

    def test_fetch_generic_exception_returns_500(self, client: TestClient) -> None:
        """Unexpected error during fetch returns 500 without leaking details."""
        with patch("haute._databricks_io.fetch_and_cache", side_effect=RuntimeError("disk full")):
            resp = client.post(
                "/api/databricks/fetch",
                json={
                    "table": "cat.sch.tbl",
                },
            )

        assert resp.status_code == 500
        assert "disk full" not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]

    def test_fetch_with_custom_query(self, client: TestClient) -> None:
        """Custom SQL query is forwarded to fetch_and_cache."""
        from haute._databricks_io import _cache_path_for

        fake_result = {
            "path": str(_cache_path_for("cat.sch.tbl")),
            "table": "cat.sch.tbl",
            "row_count": 10,
            "column_count": 2,
            "columns": {"a": "Int64", "b": "Utf8"},
            "size_bytes": 512,
            "fetched_at": 1700000000.0,
            "fetch_seconds": 0.5,
        }

        with patch("haute._databricks_io.fetch_and_cache", return_value=fake_result) as mock_fetch:
            resp = client.post(
                "/api/databricks/fetch",
                json={
                    "table": "cat.sch.tbl",
                    "http_path": "/sql/wh",
                    "query": "SELECT a, b FROM cat.sch.tbl WHERE a > 10",
                },
            )

        assert resp.status_code == 200
        mock_fetch.assert_called_once_with(
            table="cat.sch.tbl",
            http_path="/sql/wh",
            query="SELECT a, b FROM cat.sch.tbl WHERE a > 10",
        )

    def test_fetch_missing_table_returns_422(self, client: TestClient) -> None:
        """Missing required 'table' field returns 422."""
        resp = client.post("/api/databricks/fetch", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/schema/databricks
# ---------------------------------------------------------------------------


class TestDatabricksSchema:
    def test_not_cached_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/schema/databricks", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 404

    def test_cached_returns_schema(self, client: TestClient) -> None:
        import polars as pl

        from haute._databricks_io import _cache_path_for

        p = _cache_path_for("cat.sch.tbl")  # uses Path.cwd() set by client fixture
        p.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]}).write_parquet(p)

        resp = client.get("/api/schema/databricks", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 3
        assert data["column_count"] == 2
        assert len(data["preview"]) <= 5


# ---------------------------------------------------------------------------
# Table name validation on route endpoints
# ---------------------------------------------------------------------------


class TestRouteTableValidation:
    """Verify that cache/progress endpoints reject invalid table names."""

    def test_cache_status_rejects_invalid_table(self, client: TestClient) -> None:
        resp = client.get("/api/databricks/cache", params={"table": "DROP TABLE foo"})
        assert resp.status_code == 400
        assert "Invalid table name" in resp.json()["detail"]

    def test_delete_cache_rejects_invalid_table(self, client: TestClient) -> None:
        resp = client.delete("/api/databricks/cache", params={"table": "../../../etc/passwd"})
        assert resp.status_code == 400
        assert "Invalid table name" in resp.json()["detail"]

    def test_fetch_progress_rejects_invalid_table(self, client: TestClient) -> None:
        resp = client.get(
            "/api/databricks/fetch/progress",
            params={"table": "just_a_table"},
        )
        assert resp.status_code == 400
        assert "Invalid table name" in resp.json()["detail"]

    def test_cache_status_accepts_valid_table(self, client: TestClient) -> None:
        resp = client.get("/api/databricks/cache", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 200

    def test_fetch_progress_accepts_valid_table(self, client: TestClient) -> None:
        resp = client.get(
            "/api/databricks/fetch/progress",
            params={"table": "cat.sch.tbl"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _get_databricks_client — error paths
# ---------------------------------------------------------------------------


class TestGetDatabricksClient:
    def test_sdk_not_installed_returns_503(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When databricks-sdk is not installed, returns 503."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.py").write_text("")
        from haute.server import app

        c = TestClient(app)

        with patch(
            "haute.routes.databricks._get_databricks_client",
            side_effect=HTTPException(
                status_code=503, detail="databricks-sdk is not installed"
            ),
        ):
            resp = c.get("/api/databricks/warehouses")
        assert resp.status_code == 503
        assert "databricks-sdk" in resp.json()["detail"]

    def test_missing_host_returns_503(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When DATABRICKS_HOST is missing, returns 503."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABRICKS_TOKEN", "some_token")
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        (tmp_path / "main.py").write_text("")
        from haute.server import app

        c = TestClient(app)
        resp = c.get("/api/databricks/warehouses")
        assert resp.status_code == 503

    def test_missing_token_returns_503(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When DATABRICKS_TOKEN is missing, returns 503."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABRICKS_HOST", "https://test.cloud.databricks.com")
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        (tmp_path / "main.py").write_text("")
        from haute.server import app

        c = TestClient(app)
        resp = c.get("/api/databricks/warehouses")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Catalog / Schema exceptions
# ---------------------------------------------------------------------------


class TestCatalogExceptions:
    def test_catalogs_exception_returns_500(self, client: TestClient) -> None:
        """Unexpected error from catalogs.list returns 500."""
        mock_ws = MagicMock()
        mock_ws.catalogs.list.side_effect = RuntimeError("API quota exceeded")

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/catalogs")

        assert resp.status_code == 500
        assert "API quota exceeded" not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]

    def test_catalogs_empty_list(self, client: TestClient) -> None:
        """Empty catalog list returns empty array."""
        mock_ws = MagicMock()
        mock_ws.catalogs.list.return_value = []

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/catalogs")

        assert resp.status_code == 200
        assert resp.json()["catalogs"] == []

    def test_catalogs_with_none_name_skipped(self, client: TestClient) -> None:
        """Catalogs where name is None are filtered out."""
        from databricks.sdk.service.catalog import CatalogInfo

        valid = CatalogInfo(name="main", comment="Main catalog")
        invalid = CatalogInfo(name=None, comment="No name")

        mock_ws = MagicMock()
        mock_ws.catalogs.list.return_value = [valid, invalid]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/catalogs")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["catalogs"]) == 1
        assert data["catalogs"][0]["name"] == "main"

    def test_catalog_with_none_comment(self, client: TestClient) -> None:
        """Catalog with comment=None defaults to empty string."""
        from databricks.sdk.service.catalog import CatalogInfo

        cat = CatalogInfo(name="test_cat", comment=None)

        mock_ws = MagicMock()
        mock_ws.catalogs.list.return_value = [cat]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/catalogs")

        assert resp.status_code == 200
        assert resp.json()["catalogs"][0]["comment"] == ""


class TestSchemaExceptions:
    def test_schemas_exception_returns_500(self, client: TestClient) -> None:
        """Unexpected error from schemas.list returns 500."""
        mock_ws = MagicMock()
        mock_ws.schemas.list.side_effect = RuntimeError("network timeout")

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/schemas", params={"catalog": "main"})

        assert resp.status_code == 500
        assert "network timeout" not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]

    def test_schemas_empty_list(self, client: TestClient) -> None:
        """Empty schema list returns empty array."""
        mock_ws = MagicMock()
        mock_ws.schemas.list.return_value = []

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/schemas", params={"catalog": "main"})

        assert resp.status_code == 200
        assert resp.json()["schemas"] == []

    def test_schemas_with_none_name_skipped(self, client: TestClient) -> None:
        """Schemas where name is None are filtered out."""
        from databricks.sdk.service.catalog import SchemaInfo

        valid = SchemaInfo(name="pricing", comment="Pricing data")
        invalid = SchemaInfo(name=None, comment="No name")

        mock_ws = MagicMock()
        mock_ws.schemas.list.return_value = [valid, invalid]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/schemas", params={"catalog": "main"})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["schemas"]) == 1
        assert data["schemas"][0]["name"] == "pricing"

    def test_schema_with_none_comment(self, client: TestClient) -> None:
        """Schema with comment=None defaults to empty string."""
        from databricks.sdk.service.catalog import SchemaInfo

        sch = SchemaInfo(name="test_schema", comment=None)

        mock_ws = MagicMock()
        mock_ws.schemas.list.return_value = [sch]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/schemas", params={"catalog": "main"})

        assert resp.status_code == 200
        assert resp.json()["schemas"][0]["comment"] == ""


# ---------------------------------------------------------------------------
# Fetch endpoint — additional edge cases
# ---------------------------------------------------------------------------


class TestFetchTableAdditional:
    def test_fetch_reraises_http_exception(self, client: TestClient) -> None:
        """HTTPException from deeper layers is re-raised, not wrapped in 500."""
        with patch(
            "haute._databricks_io.fetch_and_cache",
            side_effect=HTTPException(status_code=409, detail="conflict"),
        ):
            resp = client.post(
                "/api/databricks/fetch",
                json={"table": "cat.sch.tbl", "http_path": "/sql/wh"},
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "conflict"

    def test_fetch_without_http_path(self, client: TestClient) -> None:
        """Fetch without http_path is accepted (http_path is optional)."""
        from haute._databricks_io import _cache_path_for

        fake_result = {
            "path": str(_cache_path_for("cat.sch.tbl")),
            "table": "cat.sch.tbl",
            "row_count": 5,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": 512,
            "fetched_at": 1700000000.0,
            "fetch_seconds": 0.1,
        }

        with patch("haute._databricks_io.fetch_and_cache", return_value=fake_result) as mock_fn:
            resp = client.post(
                "/api/databricks/fetch",
                json={"table": "cat.sch.tbl"},
            )

        assert resp.status_code == 200
        mock_fn.assert_called_once_with(
            table="cat.sch.tbl",
            http_path=None,
            query=None,
        )


# ---------------------------------------------------------------------------
# Warehouse — additional edge cases
# ---------------------------------------------------------------------------


class TestWarehouseAdditional:
    def test_warehouse_without_id_or_name_skipped(self, client: TestClient) -> None:
        """Warehouses without id or name are filtered out."""
        from databricks.sdk.service.sql import EndpointInfo, State

        valid_wh = EndpointInfo(
            id="abc",
            name="Good WH",
            state=State.RUNNING,
            cluster_size="Small",
        )
        no_id = EndpointInfo(
            id=None,
            name="No ID",
            state=State.RUNNING,
            cluster_size="Small",
        )
        no_name = EndpointInfo(
            id="def",
            name=None,
            state=State.RUNNING,
            cluster_size="Small",
        )

        mock_ws = MagicMock()
        mock_ws.warehouses.list.return_value = [valid_wh, no_id, no_name]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/warehouses")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["warehouses"]) == 1
        assert data["warehouses"][0]["id"] == "abc"

    def test_multiple_warehouses(self, client: TestClient) -> None:
        """Multiple warehouses are returned correctly."""
        from databricks.sdk.service.sql import EndpointInfo, State

        wh1 = EndpointInfo(
            id="wh1", name="Warehouse A", state=State.RUNNING, cluster_size="Small"
        )
        wh2 = EndpointInfo(
            id="wh2", name="Warehouse B", state=State.STOPPED, cluster_size="Medium"
        )

        mock_ws = MagicMock()
        mock_ws.warehouses.list.return_value = [wh1, wh2]

        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/warehouses")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["warehouses"]) == 2
        names = {w["name"] for w in data["warehouses"]}
        assert names == {"Warehouse A", "Warehouse B"}


# ---------------------------------------------------------------------------
# Table validation — additional patterns
# ---------------------------------------------------------------------------


class TestTableValidationAdditional:
    def test_two_part_name_rejected(self, client: TestClient) -> None:
        """Two-part names (catalog.table) are rejected."""
        resp = client.get("/api/databricks/cache", params={"table": "catalog.table"})
        assert resp.status_code == 400

    def test_four_part_name_rejected(self, client: TestClient) -> None:
        """Four-part names are rejected."""
        resp = client.get("/api/databricks/cache", params={"table": "a.b.c.d"})
        assert resp.status_code == 400
