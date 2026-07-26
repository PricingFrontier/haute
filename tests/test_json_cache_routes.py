"""API integration tests for per-port JSON cache endpoints.

Covers:
  - POST /api/json-cache/build: 422 without schema source, 404 missing file,
    missing-path 422, timeout 504
  - GET /api/json-cache/progress: inactive with no build, active while
    worker builds, missing-path 422
  - GET /api/json-cache/status: missing-path 422
  - POST /api/json-cache/status: 422 without schema source
  - DELETE /api/json-cache: success (clear_json_cache called), missing-path 422
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any
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


def _minimal_root_schema() -> dict[str, Any]:
    return {
        "tables": [
            {
                "label": "root",
                "path": "$[:]",
                "emit": True,
                "columns": [
                    {
                        "name": "a",
                        "path": "$[:].a",
                        "type": "int",
                        "status": "Inferred",
                        "selected": True,
                    }
                ],
            }
        ]
    }


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
                    "path": "$[:]",
                    "emit": True,
                    "columns": [
                        {
                            "name": "a",
                            "path": "$[:].a",
                            "type": "int",
                            "selected": True,
                        }
                    ],
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
            patch.dict(os.environ, {"HAUTE_BUILD_TIMEOUT": "0.001"}),
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
                    "path": "$[:]",
                    "emit": True,
                    "columns": [
                        {
                            "name": "a",
                            "path": "$[:].a",
                            "type": "int",
                            "selected": True,
                        }
                    ],
                }
            ]
        }
        resp = client.post(
            "/api/json-cache/build",
            json={"path": "does_not_exist.jsonl", "volatile_schema": minimal_schema},
        )
        assert resp.status_code == 404

    def test_build_file_removed_during_worker_returns_404(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """FileNotFoundError raised after dispatch still maps to a user-visible 404."""
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")

        with patch(
            "haute._json_shred.build_per_port_cache",
            side_effect=FileNotFoundError("data disappeared"),
        ):
            resp = client.post(
                "/api/json-cache/build",
                json={"path": "data.json", "volatile_schema": _minimal_root_schema()},
            )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Data file not found"


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
                    "path": "$[:]",
                    "emit": True,
                    "columns": [{"name": "a", "path": "$[:].a", "type": "int", "selected": True}],
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
        # F440: no producer ever updates `rows`; the response defaults it to 0.
        assert data["rows"] == 0

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
                    "path": "$[:]",
                    "emit": True,
                    "columns": [{"name": "a", "path": "$[:].a", "type": "int", "selected": True}],
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
            patch.dict(os.environ, {"HAUTE_BUILD_TIMEOUT": "0.001"}),
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

    def test_progress_counter_tracks_overlapping_builds_and_unknown_finish(
        self, tmp_path: Path
    ) -> None:
        """Progress bookkeeping is reference-counted across overlapping builds."""
        from haute.routes import json_cache

        data_file = tmp_path / "data.json"
        data_file.write_text("[]", encoding="utf-8")
        data_path = str(data_file)

        with json_cache._build_progress_lock:
            json_cache._build_progress.clear()
        try:
            json_cache._finish_build_progress(data_path)
            assert json_cache._get_build_progress(data_path).active is False

            json_cache._start_build_progress(data_path)
            json_cache._start_build_progress(data_path)

            with json_cache._build_progress_lock:
                key = json_cache._progress_key(data_path)
                assert json_cache._build_progress[key]["active_count"] == 2

            json_cache._finish_build_progress(data_path)

            with json_cache._build_progress_lock:
                assert json_cache._build_progress[key]["active_count"] == 1
            assert json_cache._get_build_progress(data_path).active is True

            json_cache._finish_build_progress(data_path)
            assert json_cache._get_build_progress(data_path).active is False
        finally:
            with json_cache._build_progress_lock:
                json_cache._build_progress.clear()


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

    def test_post_status_valid_cache_without_meta_returns_uncached(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A cache directory that loses meta during status polling is treated as uncached."""
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")

        with (
            patch("haute._json_shred.is_per_port_cache_valid", return_value=True),
            patch("haute._json_shred.read_per_port_cache_meta", return_value=None),
        ):
            resp = client.post(
                "/api/json-cache/status",
                json={"path": "data.json", "volatile_schema": _minimal_root_schema()},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["data_path"] == "data.json"

    def test_post_status_schema_error_returns_structured_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Status preserves the ApiInputSchemaError discriminator from cache validation."""
        from haute._api_input_schema import ApiInputSchemaError

        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")

        with patch(
            "haute._json_shred.is_per_port_cache_valid",
            side_effect=ApiInputSchemaError("status schema mismatch"),
        ):
            resp = client.post(
                "/api/json-cache/status",
                json={"path": "data.json", "volatile_schema": _minimal_root_schema()},
            )

        assert resp.status_code == 422
        body = resp.json()
        assert body["type"] == "ApiInputSchemaError"
        assert "status schema mismatch" in body["detail"]


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


class TestStatusPathValidatesSchema:
    """F053: the status/validity path must enforce the SAME v2 invariants
    as ``build_per_port_cache`` (which runs ``validate_v2_schema`` first).

    Without validation the status path evaluates an invalid schema (e.g.
    duplicate table labels, a B1 illegal column type) and reports
    ``cached=False`` — silently accepting a schema the build would loudly
    reject. POST /status must surface the structured 422; GET /status (a
    read-only poll) reports ``cached=False`` truthfully, mirroring how it
    already treats a corrupt on-disk config.
    """

    @staticmethod
    def _duplicate_label_schema() -> dict[str, Any]:
        # v2-shaped (has `tables`) so it reaches the status/validity path,
        # but validate_v2_schema rejects the repeated label.
        return {
            "tables": [
                {"label": "root", "path": "$[:]", "emit": True, "columns": []},
                {"label": "root", "path": "$[:]", "emit": True, "columns": []},
            ]
        }

    def test_post_status_invalid_schema_returns_structured_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")

        resp = client.post(
            "/api/json-cache/status",
            json={"path": "data.json", "volatile_schema": self._duplicate_label_schema()},
        )

        assert resp.status_code == 422
        body = resp.json()
        assert body["type"] == "ApiInputSchemaError"
        assert "root" in body["detail"]

    def test_get_status_invalid_config_returns_uncached(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        data_file = tmp_path / "data.json"
        data_file.write_text('[{"a":1}]', encoding="utf-8")
        config_file = tmp_path / "config.json"
        import json as _json

        config_file.write_text(_json.dumps(self._duplicate_label_schema()), encoding="utf-8")

        resp = client.get(
            "/api/json-cache/status",
            params={"path": "data.json", "config_path": "config.json"},
        )

        # Read-only poll: an invalid schema means no valid cache can exist.
        assert resp.status_code == 200
        assert resp.json()["cached"] is False


class TestReadV2ConfigRejectsDuplicateKeys:
    """F009: the cache-build read funnel (``_read_v2_config``) must reject
    duplicate JSON keys, exactly as the parser load funnel
    (``_config_io._load_json_object``) does — otherwise the two funnels
    disagree on the same file (one silently keeps the last value, the
    other fails loud).
    """

    def test_read_v2_config_raises_on_duplicate_key(self, tmp_path: Path) -> None:
        from haute._api_input_schema import ApiInputSchemaError
        from haute.routes.json_cache import _read_v2_config

        config_file = tmp_path / "config.json"
        config_file.write_text(
            '{"tables": [{"label": "root", "path": "$", "emit": true, "columns": []}],'
            ' "foo": 1, "foo": 2}',
            encoding="utf-8",
        )

        with pytest.raises(ApiInputSchemaError):
            _read_v2_config(str(config_file))


class TestResolveConfigPathNullByte:
    """F439: an embedded-null-byte path must map to HTTP 400 on the config
    funnel just as it does on the data funnel (``_resolve_data_path``) —
    it's a malformed request, not a forbidden traversal (403).
    """

    def test_config_path_null_byte_returns_400(self, tmp_path: Path) -> None:
        from fastapi import HTTPException

        from haute.routes.json_cache import _resolve_config_path

        with pytest.raises(HTTPException) as exc_info:
            _resolve_config_path("bad\x00config.json")
        assert exc_info.value.status_code == 400


class TestBuildStatusAggregateEquality:
    """Witness: build (POST) and status (GET/POST) collapse the SAME
    per-port ``tables[]`` into EQUAL per-port aggregates.

    ``_aggregate_v2_build_response`` (json_cache.py:255) and
    ``_aggregate_v2_status_response`` (json_cache.py:300) are
    copy-paste-near-identical helpers. For the same inputs (same
    ``tables[]``, same on-disk parquets) every per-port aggregate they
    compute — row_count, column_count, the ``{label}.colN`` columns map,
    size_bytes, cached_at, skipped_records, skipped_rows — must match.
    The two helpers will drift (a fix in one forgotten in the other); the
    equality is the actual contract and is otherwise untested (every
    existing status assertion is on the cached=False / error path).

    JsonCacheBuildResponse and JsonCacheStatusResponse are *different*
    Pydantic models (schemas.py:703 vs :733), so this asserts the shared
    per-port FIELDS equal, not whole objects. The intentional
    non-aggregate differences — ``cache_seconds`` (build-only) and
    ``cached=True`` (status-only) — are deliberately NOT asserted equal.
    """

    def test_build_and_status_per_port_aggregates_match(self, tmp_path: Path) -> None:
        from haute.routes.json_cache import (
            _aggregate_v2_build_response,
            _aggregate_v2_status_response,
        )

        cache_dir = tmp_path
        # Two emit-true tables: 10+4 rows, 3+2 columns.
        tables = [
            {
                "label": "policies",
                "parquet": "policies.parquet",
                "row_count": 10,
                "column_count": 3,
                "columns": {"id": "Int64", "name": "String", "premium": "Float64"},
            },
            {
                "label": "drivers",
                "parquet": "drivers.parquet",
                "row_count": 4,
                "column_count": 2,
                "columns": {"id": "Int64", "age": "Int64"},
            },
        ]
        # Create identical stub parquet files so size_bytes > 0 and
        # cached_at > 0 are asserted equal NON-trivially (not both 0).
        (cache_dir / "policies.parquet").write_bytes(b"PAR1" * 8)
        (cache_dir / "drivers.parquet").write_bytes(b"PAR1" * 8)

        skipped = {"records": 1, "rows_by_table": {"drivers": 2}}
        summary = {"tables": tables, "skipped": skipped}
        meta = {"tables": tables, "skipped": skipped}

        build_resp = _aggregate_v2_build_response(
            summary, cache_dir, "data.json", elapsed_seconds=0.1
        )
        status_resp = _aggregate_v2_status_response(cache_dir, "data.json", meta)

        # Per-port aggregates must be equal across both responses.
        assert build_resp.row_count == status_resp.row_count == 14
        assert build_resp.column_count == status_resp.column_count == 5
        assert build_resp.columns == status_resp.columns
        assert len(build_resp.columns) == 5
        assert build_resp.size_bytes == status_resp.size_bytes
        assert build_resp.size_bytes > 0
        assert build_resp.cached_at == status_resp.cached_at
        assert build_resp.cached_at > 0
        assert build_resp.skipped_records == status_resp.skipped_records == 1
        assert build_resp.skipped_rows == status_resp.skipped_rows == {"drivers": 2}

        # Intentional non-aggregate differences (NOT asserted equal):
        #   build carries cache_seconds; status carries cached=True.
        assert build_resp.cache_seconds == 0.1
        assert status_resp.cached is True

    def test_aggregate_uses_only_canonical_metadata_columns(self, tmp_path: Path) -> None:
        """Aggregate responses derive columns only from canonical metadata."""
        from haute.routes.json_cache import _aggregate_v2_tables

        (tmp_path / "root.parquet").write_bytes(b"not a parquet file")
        _rows, _count, columns, _size, _cached_at = _aggregate_v2_tables(
            tmp_path,
            [
                {
                    "label": "root",
                    "parquet": "root.parquet",
                    "row_count": 1,
                    "column_count": 1,
                }
            ],
        )

        assert columns == {}
