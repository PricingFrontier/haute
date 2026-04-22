"""API integration tests for JSON cache endpoints (routes/json_cache.py).

Covers:
  - POST /api/json-cache/build: success, internal error (500), timeout (504)
  - GET /api/json-cache/progress: active build, no active build
  - GET /api/json-cache/status: cached file, uncached file
  - DELETE /api/json-cache: success, already missing
"""

from __future__ import annotations

import asyncio
import time
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
    def test_build_success(self, client: TestClient) -> None:
        """Successful build returns 200 with cache metadata."""
        fake_result = {
            "path": ".haute_cache/json_data_jsonl.parquet",
            "data_path": "data.jsonl",
            "row_count": 50,
            "column_count": 3,
            "columns": {"a": "Int64", "b": "Utf8", "c": "Float64"},
            "size_bytes": 2048,
            "cached_at": time.time(),
            "cache_seconds": 1.2,
        }

        with patch("haute._json_flatten.build_json_cache", return_value=fake_result):
            resp = client.post(
                "/api/json-cache/build",
                json={
                    "path": "data.jsonl",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data_path"] == "data.jsonl"
        assert data["row_count"] == 50
        assert data["column_count"] == 3
        assert data["size_bytes"] == 2048
        assert data["cache_seconds"] == 1.2

    def test_build_with_config_path(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Config path is forwarded to the build function."""
        fake_result = {
            "path": ".haute_cache/json_data_jsonl.parquet",
            "data_path": "data.jsonl",
            "row_count": 10,
            "column_count": 2,
            "columns": {"x": "Int64", "y": "Utf8"},
            "size_bytes": 512,
            "cached_at": time.time(),
            "cache_seconds": 0.5,
        }

        with patch("haute._json_flatten.build_json_cache", return_value=fake_result) as mock_build:
            resp = client.post(
                "/api/json-cache/build",
                json={
                    "path": "data.jsonl",
                    "config_path": "config/quote_input/my_api.json",
                },
            )

        assert resp.status_code == 200
        mock_build.assert_called_once_with(
            data_path=str(tmp_path / "data.jsonl"),
            config_path=str(tmp_path / "config" / "quote_input" / "my_api.json"),
        )

    def test_build_internal_error_returns_500(self, client: TestClient) -> None:
        """Internal build failures return 500 without leaking details."""
        with patch(
            "haute._json_flatten.build_json_cache",
            side_effect=RuntimeError("disk full"),
        ):
            resp = client.post("/api/json-cache/build", json={"path": "data.jsonl"})

        assert resp.status_code == 500
        assert "disk full" not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]

    def test_build_timeout_returns_504(self, client: TestClient) -> None:
        """Build exceeding timeout returns 504."""

        async def _never_finishes(*_args: object, **_kwargs: object) -> None:
            await asyncio.sleep(60)

        with (
            patch("haute.routes._timeouts.asyncio.to_thread", _never_finishes),
            patch(
                "haute.routes.json_cache._BUILD_TIMEOUT",
                0.001,
            ),
        ):
            resp = client.post("/api/json-cache/build", json={"path": "data.jsonl"})

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]

    def test_build_missing_path_returns_422(self, client: TestClient) -> None:
        """Missing required 'path' field returns 422."""
        resp = client.post("/api/json-cache/build", json={})
        assert resp.status_code == 422

    def test_build_cache_matches_preview_when_pipeline_is_nested(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """The cache endpoint and preview executor resolve file paths identically.

        The GUI file browser returns project-root-relative paths.  A pipeline
        may still live in a subdirectory (``rating/main.py``), so the cache
        build route must write the same cache key that the preview executor
        checks for the API input node.
        """
        import orjson

        data_path = tmp_path / "data" / "quotes" / "sample_quote.json"
        data_path.parent.mkdir(parents=True)
        data_path.write_bytes(orjson.dumps([{"quote_id": "q-1", "premium": 123.45}]))

        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        (pipeline_dir / "main.py").write_text("import haute\n", encoding="utf-8")
        (tmp_path / "haute.toml").write_text(
            '[project]\npipeline = "rating/main.py"\n',
            encoding="utf-8",
        )

        rel_path = "data/quotes/sample_quote.json"
        build_resp = client.post("/api/json-cache/build", json={"path": rel_path})
        assert build_resp.status_code == 200

        graph = {
            "source_file": "rating/main.py",
            "nodes": [
                {
                    "id": "quotes",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "quotes",
                        "nodeType": "apiInput",
                        "config": {"path": rel_path},
                    },
                },
            ],
            "edges": [],
        }
        preview_resp = client.post(
            "/api/pipeline/preview",
            json={"graph": graph, "node_id": "quotes", "row_limit": 10},
        )

        assert preview_resp.status_code == 200
        preview = preview_resp.json()
        assert preview["status"] == "ok"
        assert preview["row_count"] == 1
        assert preview["preview"][0]["quote_id"] == "q-1"


# ---------------------------------------------------------------------------
# GET /api/json-cache/progress
# ---------------------------------------------------------------------------


class TestJsonCacheProgress:
    def test_no_active_build(self, client: TestClient) -> None:
        """When no build is active, returns active=False."""
        with patch("haute._json_flatten.flatten_progress", return_value=None):
            resp = client.get("/api/json-cache/progress", params={"path": "data.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False
        # Default zero values for inactive progress
        assert data["rows"] == 0
        assert data["elapsed"] == 0.0

    def test_active_build(self, client: TestClient) -> None:
        """When a build is in progress, returns progress details."""
        progress = {"rows": 15000, "elapsed": 2.5}

        with patch("haute._json_flatten.flatten_progress", return_value=progress):
            resp = client.get("/api/json-cache/progress", params={"path": "data.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["rows"] == 15000
        assert data["elapsed"] == 2.5

    def test_missing_path_returns_422(self, client: TestClient) -> None:
        """Missing required 'path' query param returns 422."""
        resp = client.get("/api/json-cache/progress")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/json-cache/status
# ---------------------------------------------------------------------------


class TestJsonCacheStatus:
    def test_not_cached(self, client: TestClient) -> None:
        """File that hasn't been cached returns cached=False."""
        with patch("haute._json_flatten.json_cache_info", return_value=None):
            resp = client.get("/api/json-cache/status", params={"path": "data.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["data_path"] == "data.jsonl"

    def test_cached(self, client: TestClient) -> None:
        """File that has been cached returns full metadata."""
        info = {
            "path": ".haute_cache/json_data_jsonl.parquet",
            "data_path": "data.jsonl",
            "row_count": 100,
            "column_count": 5,
            "columns": {"a": "Int64", "b": "Utf8", "c": "Float64", "d": "Boolean", "e": "Date"},
            "size_bytes": 8192,
            "cached_at": 1700000000.0,
        }

        with patch("haute._json_flatten.json_cache_info", return_value=info):
            resp = client.get("/api/json-cache/status", params={"path": "data.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is True
        assert data["data_path"] == "data.jsonl"
        assert data["row_count"] == 100
        assert data["column_count"] == 5
        assert data["size_bytes"] == 8192
        assert len(data["columns"]) == 5

    def test_missing_path_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/json-cache/status")
        assert resp.status_code == 422


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
    def test_cancel_active_build(self, client: TestClient) -> None:
        """Cancelling an active build returns cancelled=True."""
        with patch("haute._json_flatten.cancel_json_cache", return_value=True):
            resp = client.post("/api/json-cache/cancel", json={"path": "data.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is True
        assert data["data_path"] == "data.jsonl"

    def test_cancel_no_active_build(self, client: TestClient) -> None:
        """Cancelling when no build is active returns cancelled=False."""
        with patch("haute._json_flatten.cancel_json_cache", return_value=False):
            resp = client.post("/api/json-cache/cancel", json={"path": "data.jsonl"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is False

    def test_cancel_missing_path_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/json-cache/cancel", json={})
        assert resp.status_code == 422

    def test_build_cancelled_returns_499(self, client: TestClient) -> None:
        """A build that gets cancelled returns 499."""
        from haute._json_flatten import JsonCacheCancelledError

        with patch(
            "haute._json_flatten.build_json_cache",
            side_effect=JsonCacheCancelledError("cancelled"),
        ):
            resp = client.post("/api/json-cache/build", json={"path": "data.jsonl"})

        assert resp.status_code == 499
        assert "cancelled" in resp.json()["detail"].lower()
