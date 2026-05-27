"""API integration tests for JSON cache endpoints (routes/json_cache.py).

The route is v2-only (per-port shred). v1 symbols (build_json_cache,
json_cache_info, read_json_flat, _json_cache_path, JsonCacheCancelledError,
flatten_progress, cancel_json_cache) were removed with the v1 codec. Tests
that exercised v1-only behaviour have been deleted.

Covers:
  - POST /api/json-cache/build: 422 without schema source, 404 missing file,
    missing-path 422, timeout 504
  - GET /api/json-cache/progress: always returns active=False (v2 stub),
    missing-path 422
  - GET /api/json-cache/status: missing-path 422
  - POST /api/json-cache/status: 422 without schema source
  - DELETE /api/json-cache: success (clear_json_cache called), missing-path 422
  - POST /api/json-cache/cancel: always returns cancelled=False (v2 stub),
    missing-path 422
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with cwd set to a temp directory."""
    monkeypatch.chdir(tmp_path)
    from haute.server import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/json-cache/build
# ---------------------------------------------------------------------------


class TestBuildJsonCache:
    def test_build_missing_path_returns_422(self, client: TestClient) -> None:
        """Missing required 'path' field returns 422."""
        resp = client.post("/api/json-cache/build", json={})
        assert resp.status_code == 422

    def test_build_no_schema_source_returns_422(self, client: TestClient) -> None:
        """Path without volatile_schema or config_path returns 422 (no v2 schema source)."""
        resp = client.post("/api/json-cache/build", json={"path": "data.jsonl"})
        assert resp.status_code == 422
        body = resp.json()
        assert body.get("type") == "ApiInputSchemaError"

    def test_build_timeout_returns_504(self, client: TestClient, tmp_path: Path) -> None:
        """Build exceeding timeout returns 504."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"a":1}\n', encoding="utf-8")
        minimal_schema = {
            "tables": [
                {
                    "label": "root",
                    "path": "$",
                    "emit": True,
                    "columns": [{"name": "a", "path": "$.a", "type": "int", "selected": True}],
                }
            ]
        }

        async def _never_finishes(*_args: object, **_kwargs: object) -> None:
            await asyncio.sleep(60)

        with (
            patch("haute.routes._timeouts.asyncio.to_thread", _never_finishes),
            patch("haute.routes.json_cache._BUILD_TIMEOUT", 0.001),
        ):
            resp = client.post(
                "/api/json-cache/build",
                json={"path": str(data_file), "volatile_schema": minimal_schema},
            )

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]

    def test_build_missing_file_returns_404(self, client: TestClient, tmp_path: Path) -> None:
        """Non-existent data file returns 404 (requires a valid haute.toml project)."""
        # Route path resolution requires a project root (haute.toml).
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "test"\npipeline = "pipeline.py"\n',
            encoding="utf-8",
        )
        minimal_schema = {
            "tables": [
                {
                    "label": "root",
                    "path": "$",
                    "emit": True,
                    "columns": [{"name": "a", "path": "$.a", "type": "int", "selected": True}],
                }
            ]
        }
        resp = client.post(
            "/api/json-cache/build",
            json={"path": "does_not_exist.jsonl", "volatile_schema": minimal_schema},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/json-cache/progress
# ---------------------------------------------------------------------------


class TestJsonCacheProgress:
    def test_progress_always_inactive(self, client: TestClient) -> None:
        """v2 progress endpoint is a stub that always returns active=False."""
        resp = client.get("/api/json-cache/progress", params={"path": "data.jsonl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False

    def test_missing_path_returns_422(self, client: TestClient) -> None:
        """Missing required 'path' query param returns 422."""
        resp = client.get("/api/json-cache/progress")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/json-cache/status
# ---------------------------------------------------------------------------


class TestJsonCacheStatus:
    def test_missing_path_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/json-cache/status")
        assert resp.status_code == 422

    def test_no_schema_source_post_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        """POST status without volatile_schema or config_path returns 422."""
        resp = client.post(
            "/api/json-cache/status",
            json={"path": "data.jsonl"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body.get("type") == "ApiInputSchemaError"

    def test_get_status_missing_config_returns_uncached(self, client: TestClient) -> None:
        """GET status for a file with no on-disk v2 config returns cached=False."""
        resp = client.get("/api/json-cache/status", params={"path": "data.jsonl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["data_path"] == "data.jsonl"


# ---------------------------------------------------------------------------
# DELETE /api/json-cache
# ---------------------------------------------------------------------------


class TestDeleteJsonCache:
    def test_delete_success(self, client: TestClient) -> None:
        """Deleting an existing cache returns cached=False."""
        with patch("haute._json_flatten.clear_json_cache"):
            resp = client.delete("/api/json-cache", params={"path": "data.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["data_path"] == "data.jsonl"

    def test_delete_already_missing(self, client: TestClient) -> None:
        """Deleting a nonexistent cache still returns 200 with cached=False."""
        with patch("haute._json_flatten.clear_json_cache"):
            resp = client.delete("/api/json-cache", params={"path": "no_such.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False

    def test_delete_missing_path_returns_422(self, client: TestClient) -> None:
        resp = client.delete("/api/json-cache")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/json-cache/cancel
# ---------------------------------------------------------------------------


class TestCancelJsonCache:
    def test_cancel_always_returns_false(self, client: TestClient) -> None:
        """v2 cancel endpoint is a stub that always returns cancelled=False."""
        resp = client.post("/api/json-cache/cancel", json={"path": "data.jsonl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is False
        assert data["data_path"] == "data.jsonl"

    def test_cancel_missing_path_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/json-cache/cancel", json={})
        assert resp.status_code == 422
