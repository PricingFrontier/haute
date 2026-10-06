"""Error-handler coverage for ``routes/json_cache.py``'s schema inference.

The route's only endpoint is ``POST /api/json-cache/infer``; these tests
exercise the paths that surface as 4xx/5xx to the frontend against real
files under ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import orjson
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Chdir into a fresh tmp dir."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(isolated_cwd: Path) -> TestClient:
    """TestClient under the same isolated cwd, for route-level tests."""
    from haute.server import app

    return TestClient(app)


class TestInferExceptions:
    def test_infer_data_file_not_found_returns_404(self, client: TestClient) -> None:
        resp = client.post("/api/json-cache/infer", json={"path": "data/nope.json"})
        assert resp.status_code == 404
        assert "Data file not found" in resp.json()["detail"]

    def test_infer_malformed_json_returns_422(self, client: TestClient, isolated_cwd: Path) -> None:
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "broken.json").write_bytes(b"{ broken")
        resp = client.post("/api/json-cache/infer", json={"path": "data/broken.json"})
        assert resp.status_code == 422
        assert "Invalid JSON in data file" in resp.json()["detail"]

    def test_infer_generic_exception_returns_500(
        self, client: TestClient, isolated_cwd: Path
    ) -> None:
        """Unexpected internal error during inference → 500 with opaque detail."""
        data_dir = isolated_cwd / "data"
        data_dir.mkdir()
        (data_dir / "ok.json").write_bytes(orjson.dumps([{"x": 1}]))
        with patch(
            "haute._json_shred._inference.infer_v2_schema_from_data",
            side_effect=RuntimeError("infer boom"),
        ):
            resp = client.post("/api/json-cache/infer", json={"path": "data/ok.json"})
        assert resp.status_code == 500
        assert "infer boom" not in resp.text
