"""API integration tests for JSON cache endpoints (routes/json_cache.py).

The route is v2-only (per-port shred). v1 symbols (build_json_cache,
json_cache_info, read_json_flat, _json_cache_path, JsonCacheCancelledError,
flatten_progress, cancel_json_cache) were removed with the v1 codec. Tests
that exercised v1-only behaviour have been deleted.

Covers:
  - POST /api/json-cache/build: 422 without schema source, 404 missing file,
    missing-path 422, timeout 504
  - GET /api/json-cache/progress: inactive with no build, active while
    worker builds, missing-path 422
  - GET /api/json-cache/status: missing-path 422
  - POST /api/json-cache/status: 422 without schema source
  - DELETE /api/json-cache: success (clear_json_cache called), missing-path 422
  - POST /api/json-cache/cancel: always returns cancelled=False (v2 stub),
    missing-path 422
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response


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
        started = threading.Event()

        def _slow_build(*, data_path: str, v2_config: dict, cache_dir: Path) -> dict:
            started.set()
            time.sleep(0.05)
            return {
                "schema_mode": "v2",
                "schema_fingerprint": "fake",
                "tables": [],
                "data_file": {},
                "skipped": {"records": 0, "rows_by_table": {}},
                "cache_dir": str(cache_dir),
            }

        with (
            patch("haute._json_shred.build_per_port_cache", _slow_build),
            patch("haute.routes.json_cache._BUILD_TIMEOUT", 0.001),
        ):
            resp = client.post(
                "/api/json-cache/build",
                json={"path": str(data_file), "volatile_schema": minimal_schema},
            )

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]
        assert started.wait(timeout=5), "build worker did not start"

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
        """With no active build, progress reports inactive."""
        resp = client.get("/api/json-cache/progress", params={"path": "data.jsonl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False

    def test_progress_reports_active_while_v2_build_running(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")
        schema = {
            "tables": [
                {
                    "label": "root",
                    "path": "$[*]",
                    "emit": True,
                    "columns": [{"name": "a", "path": "$[*].a", "type": "int", "selected": True}],
                }
            ]
        }
        started = threading.Event()
        release = threading.Event()
        response_by_thread: dict[str, Response] = {}

        def _slow_build(*, data_path: str, v2_config: dict, cache_dir: Path) -> dict:
            started.set()
            assert release.wait(timeout=5), "test build was not released"
            return {
                "schema_mode": "v2",
                "schema_fingerprint": "fake",
                "tables": [],
                "data_file": {},
                "skipped": {"records": 0, "rows_by_table": {}},
                "cache_dir": str(cache_dir),
            }

        def _post_build() -> None:
            response_by_thread["response"] = client.post(
                "/api/json-cache/build",
                json={"path": "data.json", "volatile_schema": schema},
            )

        with patch("haute._json_shred.build_per_port_cache", _slow_build):
            worker = threading.Thread(target=_post_build)
            worker.start()
            try:
                assert started.wait(timeout=5), "build did not start"
                resp = client.get("/api/json-cache/progress", params={"path": "data.json"})
            finally:
                release.set()
                worker.join(timeout=5)

        assert not worker.is_alive()
        build_resp = response_by_thread["response"]
        assert build_resp.status_code == 200
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["phase"] == "building"
        assert data["elapsed"] >= 0

    @pytest.mark.asyncio
    async def test_progress_stays_active_after_504_until_worker_finishes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import HTTPException

        from haute.routes.json_cache import build_json_cache, get_json_cache_progress
        from haute.schemas import JsonCacheBuildRequest

        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")
        schema = {
            "tables": [
                {
                    "label": "root",
                    "path": "$[*]",
                    "emit": True,
                    "columns": [{"name": "a", "path": "$[*].a", "type": "int", "selected": True}],
                }
            ]
        }
        started = threading.Event()
        release = threading.Event()

        def _slow_build(*, data_path: str, v2_config: dict, cache_dir: Path) -> dict:
            started.set()
            assert release.wait(timeout=5), "timed-out build worker was not released"
            return {
                "schema_mode": "v2",
                "schema_fingerprint": "fake",
                "tables": [],
                "data_file": {},
                "skipped": {"records": 0, "rows_by_table": {}},
                "cache_dir": str(cache_dir),
            }

        with (
            patch("haute._json_shred.build_per_port_cache", _slow_build),
            patch("haute.routes.json_cache._BUILD_TIMEOUT", 0.001),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await build_json_cache(
                    JsonCacheBuildRequest(path="data.json", volatile_schema=schema)
                )
            assert exc_info.value.status_code == 504
            assert started.wait(timeout=5), "build worker did not start"

            progress = await get_json_cache_progress("data.json")
            assert progress.active is True

            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                progress = await get_json_cache_progress("data.json")
                if progress.active is False:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("progress stayed active after the timed-out worker finished")

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
