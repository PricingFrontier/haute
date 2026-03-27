"""Tests for the utility file CRUD endpoints (routes/utility.py)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in a temporary directory so utility/ is fresh."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute.routes.utility.pipeline_dir", lambda: tmp_path)


# ---------------------------------------------------------------------------
# GET /api/utility — list files
# ---------------------------------------------------------------------------


class TestListUtilityFiles:
    def test_empty_when_no_directory(self, client: TestClient) -> None:
        res = client.get("/api/utility")
        assert res.status_code == 200
        assert res.json()["files"] == []

    def test_lists_py_files(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "__init__.py").write_text("")
        (d / "helpers.py").write_text("x = 1\n")
        (d / "common.py").write_text("y = 2\n")
        (d / "not_python.txt").write_text("ignored")

        res = client.get("/api/utility")
        assert res.status_code == 200
        files = res.json()["files"]
        modules = [f["module"] for f in files]
        assert "helpers" in modules
        assert "common" in modules
        assert "__init__" not in modules
        assert len(files) == 2

    def test_excludes_init(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "__init__.py").write_text("")
        res = client.get("/api/utility")
        assert res.json()["files"] == []


# ---------------------------------------------------------------------------
# GET /api/utility/{module} — read file
# ---------------------------------------------------------------------------


class TestReadUtilityFile:
    def test_reads_content(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "features.py").write_text("def foo(): pass\n")
        res = client.get("/api/utility/features")
        assert res.status_code == 200
        body = res.json()
        assert body["module"] == "features"
        assert body["content"] == "def foo(): pass\n"

    def test_404_for_missing(self, client: TestClient) -> None:
        res = client.get("/api/utility/nonexistent")
        assert res.status_code == 404

    def test_rejects_dashes(self, client: TestClient) -> None:
        res = client.get("/api/utility/my-file")
        assert res.status_code == 400

    def test_rejects_dunder_names(self, client: TestClient) -> None:
        res = client.get("/api/utility/__init__")
        assert res.status_code == 400

    def test_rejects_leading_digit(self, client: TestClient) -> None:
        res = client.get("/api/utility/123bad")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/utility — create file
# ---------------------------------------------------------------------------


class TestCreateUtilityFile:
    def test_creates_file_with_default_content(self, client: TestClient, tmp_path: Path) -> None:
        res = client.post("/api/utility", json={"name": "helpers"})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["module"] == "helpers"
        assert body["import_line"] == "from utility.helpers import *"

        # File should exist on disk
        target = tmp_path / "utility" / "helpers.py"
        assert target.exists()
        content = target.read_text()
        assert "import polars" in content

    def test_creates_init_py(self, client: TestClient, tmp_path: Path) -> None:
        client.post("/api/utility", json={"name": "helpers"})
        init = tmp_path / "utility" / "__init__.py"
        assert init.exists()

    def test_creates_with_custom_content(self, client: TestClient, tmp_path: Path) -> None:
        res = client.post("/api/utility", json={
            "name": "custom",
            "content": "import numpy as np\n\ndef helper(): pass\n",
        })
        assert res.json()["status"] == "ok"
        content = (tmp_path / "utility" / "custom.py").read_text()
        assert "import numpy" in content

    def test_rejects_syntax_error(self, client: TestClient) -> None:
        res = client.post("/api/utility", json={
            "name": "bad",
            "content": "def foo(\n",
        })
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert detail["error_line"] is not None
        assert detail["error"] is not None

    def test_rejects_duplicate(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "__init__.py").write_text("")
        (d / "existing.py").write_text("x = 1\n")

        res = client.post("/api/utility", json={"name": "existing"})
        assert res.status_code == 409

    def test_rejects_invalid_name(self, client: TestClient) -> None:
        res = client.post("/api/utility", json={"name": "123bad"})
        assert res.status_code == 422

    def test_rejects_path_traversal(self, client: TestClient) -> None:
        res = client.post("/api/utility", json={"name": "../../etc/passwd"})
        assert res.status_code == 422

    def test_creates_utility_directory(self, client: TestClient, tmp_path: Path) -> None:
        """utility/ dir is created on first file create."""
        assert not (tmp_path / "utility").exists()
        res = client.post("/api/utility", json={"name": "first"})
        assert res.json()["status"] == "ok"
        assert (tmp_path / "utility").is_dir()


# ---------------------------------------------------------------------------
# PUT /api/utility/{module} — update file
# ---------------------------------------------------------------------------


class TestUpdateUtilityFile:
    def test_updates_content(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "helpers.py").write_text("x = 1\n")

        res = client.put("/api/utility/helpers", json={"content": "x = 2\ny = 3\n"})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert (d / "helpers.py").read_text() == "x = 2\ny = 3\n"

    def test_returns_syntax_error(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "helpers.py").write_text("x = 1\n")

        res = client.put("/api/utility/helpers", json={"content": "def foo(\n"})
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert detail["error_line"] is not None
        assert detail["error"] is not None
        # Original file should be unchanged
        assert (d / "helpers.py").read_text() == "x = 1\n"

    def test_404_for_missing(self, client: TestClient) -> None:
        res = client.put("/api/utility/nonexistent", json={"content": "x = 1\n"})
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/utility/{module} — delete file
# ---------------------------------------------------------------------------


class TestDeleteUtilityFile:
    def test_deletes_file(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        target = d / "helpers.py"
        target.write_text("x = 1\n")

        res = client.delete("/api/utility/helpers")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        assert not target.exists()

    def test_404_for_missing(self, client: TestClient) -> None:
        res = client.delete("/api/utility/nonexistent")
        assert res.status_code == 404

    def test_rejects_dunder_name(self, client: TestClient) -> None:
        res = client.delete("/api/utility/__init__")
        assert res.status_code == 400

    def test_rejects_dashes(self, client: TestClient) -> None:
        res = client.delete("/api/utility/my-file")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# Integration: auto-import preamble mutation
# ---------------------------------------------------------------------------


class TestAutoImportIntegration:
    """Verify the import_line returned by create is correct and consistent."""

    def test_import_line_format(self, client: TestClient) -> None:
        res = client.post("/api/utility", json={"name": "my_helpers"})
        assert res.json()["import_line"] == "from utility.my_helpers import *"

    def test_import_line_not_duplicated(self, client: TestClient) -> None:
        """Creating two different files returns different import lines."""
        r1 = client.post("/api/utility", json={"name": "aaa"})
        r2 = client.post("/api/utility", json={"name": "bbb"})
        assert r1.json()["import_line"] != r2.json()["import_line"]
        assert "aaa" in r1.json()["import_line"]
        assert "bbb" in r2.json()["import_line"]

    def test_update_also_returns_import_line(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "helpers.py").write_text("x = 1\n")

        res = client.put("/api/utility/helpers", json={"content": "x = 2\n"})
        assert res.json()["import_line"] == "from utility.helpers import *"


# ---------------------------------------------------------------------------
# Security: path traversal prevention
# ---------------------------------------------------------------------------


class TestPathTraversalSecurity:
    @pytest.mark.parametrize("name,expected_status", [
        ("__init__", 400),
        ("__main__", 400),
        ("123starts_with_digit", 422),
        ("has-dashes", 422),
        ("has spaces", 422),
        ("has.dots", 422),
    ])
    def test_create_blocked(self, client: TestClient, name: str, expected_status: int) -> None:
        res = client.post("/api/utility", json={"name": name})
        assert res.status_code == expected_status

    @pytest.mark.parametrize("module", [
        "__init__",
        "__main__",
        "has-dashes",
    ])
    def test_read_blocked(self, client: TestClient, module: str) -> None:
        res = client.get(f"/api/utility/{module}")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# E4: Syntax errors must return 400, not 200
# ---------------------------------------------------------------------------


class TestSyntaxErrorReturns400:
    """Verify syntax errors produce HTTP 400 with structured error detail."""

    def test_create_syntax_error_returns_400(self, client: TestClient, tmp_path: Path) -> None:
        res = client.post("/api/utility", json={
            "name": "broken",
            "content": "def foo(\n",
        })
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert "error" in detail
        assert "error_line" in detail
        assert detail["error_line"] is not None
        # File must NOT have been written
        assert not (tmp_path / "utility" / "broken.py").exists()

    def test_update_syntax_error_returns_400(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "mymod.py").write_text("x = 1\n")

        res = client.put("/api/utility/mymod", json={"content": "if True\n"})
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert detail["error"] is not None
        assert detail["error_line"] is not None
        # Original file must be unchanged
        assert (d / "mymod.py").read_text() == "x = 1\n"

    def test_create_valid_content_still_returns_200(self, client: TestClient) -> None:
        res = client.post("/api/utility", json={
            "name": "good",
            "content": "x = 1\n",
        })
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
