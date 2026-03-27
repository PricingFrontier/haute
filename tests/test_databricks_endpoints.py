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
            id="abc123", name="Starter Warehouse",
            state=State.RUNNING, cluster_size="Small",
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
            id="xyz", name="No State WH",
            state=None, cluster_size=None,
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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

        valid = TableInfo(name="valid_tbl", full_name="cat.sch.valid_tbl",
                          table_type=TableType.MANAGED, comment="")
        invalid = TableInfo(name=None, full_name=None,
                            table_type=None, comment="")

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
            resp = client.post("/api/databricks/fetch", json={
                "table": "cat.sch.tbl",
                "http_path": "/sql/wh",
            })

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
            resp = client.post("/api/databricks/fetch", json={
                "table": "cat.sch.big_table",
                "http_path": "/sql/wh",
            })

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]

    def test_fetch_generic_exception_returns_500(self, client: TestClient) -> None:
        """Unexpected error during fetch returns 500 without leaking details."""
        with patch("haute._databricks_io.fetch_and_cache", side_effect=RuntimeError("disk full")):
            resp = client.post("/api/databricks/fetch", json={
                "table": "cat.sch.tbl",
            })

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
            resp = client.post("/api/databricks/fetch", json={
                "table": "cat.sch.tbl",
                "http_path": "/sql/wh",
                "query": "SELECT a, b FROM cat.sch.tbl WHERE a > 10",
            })

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
