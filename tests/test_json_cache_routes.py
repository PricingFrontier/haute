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
from fastapi import HTTPException
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
    def test_cache_identity_matches_for_relative_and_absolute_paths(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Relative and absolute paths to the same JSON file share one cache."""
        from haute._json_flatten import build_json_cache, json_cache_info

        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data" / "quotes.jsonl"
        data_file.parent.mkdir()
        data_file.write_text('{"quote_id":"q-1","premium":123.45}\n', encoding="utf-8")

        result = build_json_cache("data/quotes.jsonl")
        info = json_cache_info(data_file)

        assert info is not None
        assert info["path"] == result["path"]
        assert info["row_count"] == 1

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
            schema=None,
            config_path=str(tmp_path / "config" / "quote_input" / "my_api.json"),
        )

    def test_build_with_inline_schema(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Inline flatten schemas are forwarded to cache builds."""
        schema = {"x": "int"}
        fake_result = {
            "path": ".haute_cache/json_data_jsonl.parquet",
            "data_path": "data.jsonl",
            "row_count": 10,
            "column_count": 1,
            "columns": {"x": "Int64"},
            "size_bytes": 512,
            "cached_at": time.time(),
            "cache_seconds": 0.5,
        }

        with patch("haute._json_flatten.build_json_cache", return_value=fake_result) as mock_build:
            resp = client.post(
                "/api/json-cache/build",
                json={"path": "data.jsonl", "flatten_schema": schema},
            )

        assert resp.status_code == 200
        mock_build.assert_called_once_with(
            data_path=str(tmp_path / "data.jsonl"),
            schema=schema,
            config_path=None,
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

    def test_build_preserves_explicit_http_exception(self, client: TestClient) -> None:
        """Deliberate route errors must not be wrapped as generic 500s."""
        with patch(
            "haute._json_flatten.build_json_cache",
            side_effect=HTTPException(status_code=409, detail="cache locked"),
        ):
            resp = client.post("/api/json-cache/build", json={"path": "data.jsonl"})

        assert resp.status_code == 409
        assert resp.json()["detail"] == "cache locked"

    def test_build_malformed_jsonl_returns_400(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Malformed JSONL is client data error, not an internal server error."""
        data_file = tmp_path / "bad.jsonl"
        data_file.write_text('{"a":1}\nnot json\n', encoding="utf-8")

        resp = client.post("/api/json-cache/build", json={"path": "bad.jsonl"})

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "Invalid JSONL file" in detail
        assert "line 2" in detail
        assert "Check the server logs" not in detail

    def test_build_malformed_json_returns_400(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Malformed JSON files are client data errors, not internal failures."""
        data_file = tmp_path / "bad.json"
        data_file.write_text('{"a":1', encoding="utf-8")

        resp = client.post("/api/json-cache/build", json={"path": str(data_file)})

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "Invalid JSON file" in detail
        assert "Check the server logs" not in detail

    def test_build_scalar_json_root_returns_400(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Scalar JSON roots cannot be flattened into a tabular cache."""
        data_file = tmp_path / "scalar.json"
        data_file.write_text('"not a table"', encoding="utf-8")

        resp = client.post("/api/json-cache/build", json={"path": str(data_file)})

        assert resp.status_code == 400
        assert "JSON root must be an object or array" in resp.json()["detail"]

    def test_build_mixed_json_array_returns_400_without_partial_cache(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Mixed JSON arrays fail loudly instead of silently dropping rows."""
        from haute._json_flatten import json_cache_info

        data_file = tmp_path / "mixed.json"
        data_file.write_text('[{"a":1}, 2, {"a":3}]', encoding="utf-8")

        resp = client.post("/api/json-cache/build", json={"path": str(data_file)})

        assert resp.status_code == 400
        assert "JSON array items must be objects" in resp.json()["detail"]
        assert json_cache_info(data_file) is None

    def test_build_invalid_flatten_schema_returns_422(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Invalid flatten schemas are returned as explicit validation errors."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"a":{"b":1}}\n', encoding="utf-8")

        resp = client.post(
            "/api/json-cache/build",
            json={"path": "data.jsonl", "flatten_schema": {"a.b": "int"}},
        )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "Invalid flatten schema" in detail
        assert "Unsupported JSON object key" in detail
        assert "Check the server logs" not in detail

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

    def test_post_status_rejects_schema_incompatible_cache(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Schema-aware status must not report a cache built for a different schema."""
        from haute._json_flatten import build_json_cache

        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x":1,"y":2}\n', encoding="utf-8")
        build_json_cache(str(data_file), schema={"x": "int"})

        resp = client.post(
            "/api/json-cache/status",
            json={
                "path": str(data_file),
                "flatten_schema": {"x": "int", "y": "int"},
            },
        )

        assert resp.status_code == 200
        assert resp.json()["cached"] is False

    def test_post_status_accepts_schema_compatible_cache(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Schema-aware status reports metadata when the fingerprint matches."""
        from haute._json_flatten import build_json_cache

        schema = {"x": "int"}
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x":1,"y":2}\n', encoding="utf-8")
        build_json_cache(str(data_file), schema=schema)

        resp = client.post(
            "/api/json-cache/status",
            json={
                "path": str(data_file),
                "flatten_schema": schema,
            },
        )

        data = resp.json()
        assert resp.status_code == 200
        assert data["cached"] is True
        assert data["row_count"] == 1
        assert data["columns"] == {"x": "int64"}

    def test_post_status_resolves_config_schema_for_existing_cache(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Config-backed status resolves schema only after an existing cache is found."""
        from haute._json_flatten import build_json_cache

        data_file = tmp_path / "data.jsonl"
        config_file = tmp_path / "config" / "api_input.json"
        data_file.write_text('{"x":1,"y":2}\n', encoding="utf-8")
        config_file.parent.mkdir()
        config_file.write_text('{"flattenSchema":{"x":"int"}}', encoding="utf-8")
        build_json_cache(str(data_file), config_path=str(config_file))

        resp = client.post(
            "/api/json-cache/status",
            json={"path": str(data_file), "config_path": str(config_file)},
        )

        data = resp.json()
        assert resp.status_code == 200
        assert data["cached"] is True
        assert data["row_count"] == 1
        assert data["columns"] == {"x": "int64"}

    def test_post_status_rejects_explicit_cache_when_config_cache_is_required(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Config-backed status mirrors read_json_flat cache mode checks."""
        from haute._json_flatten import build_json_cache

        data_file = tmp_path / "data.jsonl"
        config_file = tmp_path / "config" / "api_input.json"
        data_file.write_text('{"x":1,"y":2}\n', encoding="utf-8")
        config_file.parent.mkdir()
        config_file.write_text('{"flattenSchema":{"x":"int"}}', encoding="utf-8")
        build_json_cache(str(data_file), schema={"x": "int"})

        resp = client.post(
            "/api/json-cache/status",
            json={"path": str(data_file), "config_path": str(config_file)},
        )

        data = resp.json()
        assert resp.status_code == 200
        assert data["cached"] is False

    def test_post_status_rejects_config_cache_when_config_changed(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Config-backed status includes config freshness, not only data freshness."""
        import os

        from haute._json_flatten import build_json_cache

        data_file = tmp_path / "data.jsonl"
        config_file = tmp_path / "config" / "api_input.json"
        data_file.write_text('{"x":1,"y":2}\n', encoding="utf-8")
        config_file.parent.mkdir()
        config_file.write_text('{"flattenSchema":{"x":"int"}}', encoding="utf-8")
        result = build_json_cache(str(data_file), config_path=str(config_file))

        cache_mtime = Path(result["path"]).stat().st_mtime
        config_file.write_text('{"flattenSchema":{"x":"int","y":"int"}}', encoding="utf-8")
        os.utime(config_file, (cache_mtime + 5, cache_mtime + 5))

        resp = client.post(
            "/api/json-cache/status",
            json={"path": str(data_file), "config_path": str(config_file)},
        )

        data = resp.json()
        assert resp.status_code == 200
        assert data["cached"] is False

    def test_post_status_invalid_inline_schema_returns_422(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Schema-aware status surfaces invalid schemas as validation errors."""
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"a":{"b":1}}\n', encoding="utf-8")

        resp = client.post(
            "/api/json-cache/status",
            json={"path": str(data_file), "flatten_schema": {"a.b": "int"}},
        )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "Invalid flatten schema" in detail
        assert "Unsupported JSON object key" in detail

    def test_post_status_missing_file_with_config_returns_uncached(
        self,
        client: TestClient,
    ) -> None:
        """Schema-aware status checks should not infer schema for an uncached file."""
        resp = client.post(
            "/api/json-cache/status",
            json={"path": "missing.jsonl", "config_path": "missing_config.json"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["path"] is None
        assert data["data_path"] == "missing.jsonl"

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
