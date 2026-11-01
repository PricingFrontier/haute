"""Tests for the utility file CRUD endpoints (routes/utility.py)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from haute._git_state import write_working_branch
from tests._git_helpers import REWRITING_PRE_COMMIT_HOOK, git_run, init_repo, install_hook

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in a temporary project so utility/ is fresh."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute.routes.utility.pipeline_dir", lambda: tmp_path)


def _revision(client: TestClient, module: str) -> str:
    """The revision a read of *module* hands the editor."""
    res = client.get(f"/api/utility/{module}")
    assert res.status_code == 200, res.text
    return res.json()["revision"]


def _put(client: TestClient, module: str, content: str, revision: str | None = None):
    revision = _revision(client, module) if revision is None else revision
    return client.put(f"/api/utility/{module}", json={"content": content, "revision": revision})


def _delete(client: TestClient, module: str, revision: str | None = None):
    revision = _revision(client, module) if revision is None else revision
    return client.delete(f"/api/utility/{module}", params={"revision": revision})


def _change_outside_haute(path: Path, content: str) -> None:
    """Rewrite *path* as another editor would, with a modification time Haute cannot have read."""
    before = path.stat().st_mtime_ns
    path.write_text(content, encoding="utf-8")
    os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


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
    @pytest.mark.parametrize(
        ("name", "status", "message"),
        [
            pytest.param("class", 400, "is a Python keyword", id="hard-keyword"),
            pytest.param("NUL", 400, "reserved device name on Windows", id="device-NUL"),
            pytest.param("con", 400, "reserved device name on Windows", id="device-con"),
            pytest.param("COM1", 400, "reserved device name on Windows", id="device-COM1"),
        ],
    )
    def test_refuses_a_name_that_cannot_be_imported_or_checked_out(
        self, client: TestClient, tmp_path: Path, name: str, status: int, message: str
    ) -> None:
        res = client.post("/api/utility", json={"name": name})

        assert res.status_code == status
        assert message in res.json()["detail"]
        assert not (tmp_path / "utility" / f"{name}.py").exists()

    def test_refuses_a_name_differing_only_in_case_on_every_platform(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        utility = tmp_path / "utility"
        utility.mkdir()
        (utility / "features.py").write_text("x = 1\n", encoding="utf-8")

        res = client.post("/api/utility", json={"name": "Features"})

        assert res.status_code == 409
        assert "differing only in case" in res.json()["detail"]
        assert [p.name for p in utility.iterdir()] == ["features.py"]

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
        res = client.post(
            "/api/utility",
            json={
                "name": "custom",
                "content": "import numpy as np\n\ndef helper(): pass\n",
            },
        )
        assert res.json()["status"] == "ok"
        content = (tmp_path / "utility" / "custom.py").read_text()
        assert "import numpy" in content

    def test_rejects_syntax_error(self, client: TestClient) -> None:
        res = client.post(
            "/api/utility",
            json={
                "name": "bad",
                "content": "def foo(\n",
            },
        )
        assert res.status_code == 400
        # Item #76: detail is a flat string, not a nested dict. The line
        # number must still be embedded so the frontend can extract it
        # with a /line (\d+)/ regex.
        detail = res.json()["detail"]
        assert isinstance(detail, str)
        assert "line" in detail.lower()
        assert "1" in detail  # SyntaxError reports line 1 for "def foo(\n"

    def test_rejects_duplicate(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "__init__.py").write_text("")
        (d / "existing.py").write_text("x = 1\n")

        res = client.post("/api/utility", json={"name": "existing"})
        assert res.status_code == 409

    def test_rejects_invalid_name(self, client: TestClient) -> None:
        # Item #76: ``_validate_module_name`` raises 400 with a flat string
        # detail.  Previously Pydantic's Field(pattern=) produced a 422 with
        # a structured list body, which violated the #76 contract.
        res = client.post("/api/utility", json={"name": "123bad"})
        assert res.status_code == 400
        assert isinstance(res.json()["detail"], str)

    def test_rejects_path_traversal(self, client: TestClient) -> None:
        res = client.post("/api/utility", json={"name": "../../etc/passwd"})
        assert res.status_code == 400
        assert isinstance(res.json()["detail"], str)

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

        res = _put(client, "helpers", "x = 2\ny = 3\n")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert (d / "helpers.py").read_text() == "x = 2\ny = 3\n"

    def test_returns_syntax_error(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "helpers.py").write_text("x = 1\n")

        res = _put(client, "helpers", "def foo(\n")
        assert res.status_code == 400
        # Item #76: flat string detail with embedded line number.
        detail = res.json()["detail"]
        assert isinstance(detail, str)
        assert "line" in detail.lower()
        # Original file should be unchanged
        assert (d / "helpers.py").read_text() == "x = 1\n"

    def test_404_for_missing(self, client: TestClient) -> None:
        res = client.put("/api/utility/nonexistent", json={"content": "x = 1\n", "revision": "0-0"})
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

        res = _delete(client, "helpers")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        assert not target.exists()

    def test_404_for_missing(self, client: TestClient) -> None:
        res = _delete(client, "nonexistent", revision="0-0")
        assert res.status_code == 404

    def test_rejects_dunder_name(self, client: TestClient) -> None:
        res = _delete(client, "__init__", revision="0-0")
        assert res.status_code == 400

    def test_rejects_dashes(self, client: TestClient) -> None:
        res = _delete(client, "my-file", revision="0-0")
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

        res = _put(client, "helpers", "x = 2\n")
        assert res.json()["import_line"] == "from utility.helpers import *"


# ---------------------------------------------------------------------------
# Security: path traversal prevention
# ---------------------------------------------------------------------------


class TestPathTraversalSecurity:
    @pytest.mark.parametrize(
        "name",
        [
            "__init__",
            "__main__",
            "123starts_with_digit",
            "has-dashes",
            "has spaces",
            "has.dots",
        ],
    )
    def test_create_blocked(self, client: TestClient, name: str) -> None:
        # Item #76: all name-validation failures produce a 400 with a
        # flat string detail (handler-level validation, not Pydantic).
        res = client.post("/api/utility", json={"name": name})
        assert res.status_code == 400
        assert isinstance(res.json()["detail"], str)

    @pytest.mark.parametrize(
        "module",
        [
            "__init__",
            "__main__",
            "has-dashes",
        ],
    )
    def test_read_blocked(self, client: TestClient, module: str) -> None:
        res = client.get(f"/api/utility/{module}")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# E4: Syntax errors must return 400, not 200
# ---------------------------------------------------------------------------


class TestSyntaxErrorReturns400:
    """Verify syntax errors produce HTTP 400 with structured error detail."""

    def test_create_syntax_error_returns_400(self, client: TestClient, tmp_path: Path) -> None:
        res = client.post(
            "/api/utility",
            json={
                "name": "broken",
                "content": "def foo(\n",
            },
        )
        assert res.status_code == 400
        # Item #76: flat string detail with embedded line number.
        detail = res.json()["detail"]
        assert isinstance(detail, str)
        assert "syntax" in detail.lower()
        assert "line" in detail.lower()
        # File must NOT have been written
        assert not (tmp_path / "utility" / "broken.py").exists()

    def test_update_syntax_error_returns_400(self, client: TestClient, tmp_path: Path) -> None:
        d = tmp_path / "utility"
        d.mkdir()
        (d / "mymod.py").write_text("x = 1\n")

        res = _put(client, "mymod", "if True\n")
        assert res.status_code == 400
        # Item #76: flat string detail with embedded line number.
        detail = res.json()["detail"]
        assert isinstance(detail, str)
        assert "syntax" in detail.lower()
        assert "line" in detail.lower()
        # Original file must be unchanged
        assert (d / "mymod.py").read_text() == "x = 1\n"

    def test_create_valid_content_still_returns_200(self, client: TestClient) -> None:
        res = client.post(
            "/api/utility",
            json={
                "name": "good",
                "content": "x = 1\n",
            },
        )
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# A utility write is a project write (specs/server-api, Utility scripts)
# ---------------------------------------------------------------------------


class TestWritesAgainstARevision:
    def test_each_write_returns_the_revision_the_next_one_sends(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        target = tmp_path / "utility" / "helpers.py"
        target.parent.mkdir()
        target.write_text("x = 1\n", encoding="utf-8")

        first = _put(client, "helpers", "x = 2\n")
        second = _put(client, "helpers", "x = 3\n", revision=first.json()["revision"])

        assert second.status_code == 200, second.text
        assert target.read_text(encoding="utf-8") == "x = 3\n"
        assert second.json()["revision"] == _revision(client, "helpers")

    def test_an_update_against_a_changed_file_is_refused_and_writes_nothing(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        target = tmp_path / "utility" / "helpers.py"
        target.parent.mkdir()
        target.write_text("VALUE = 1\n", encoding="utf-8")
        opened = _revision(client, "helpers")
        _change_outside_haute(target, "VALUE = 2\n")

        res = _put(client, "helpers", "VALUE = 3\n", revision=opened)

        assert res.status_code == 412
        assert res.json()["detail"] == (
            "utility/helpers.py changed since you opened it. Open it again, then retry."
        )
        assert target.read_text(encoding="utf-8") == "VALUE = 2\n"

    def test_a_change_made_while_a_write_is_captured_is_handed_back_with_it(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another editor saving while the write is captured in the ledger, as a project
        hook does: the answer hands the editor the file as it is then, with its revision."""
        import haute.routes.utility as utility_routes

        target = tmp_path / "utility" / "helpers.py"
        target.parent.mkdir()
        target.write_text("VALUE = 1\n", encoding="utf-8")
        capture = utility_routes.capture_save_in_ledger

        def capture_while_another_editor_saves(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            _change_outside_haute(target, "VALUE = 9\n")
            return capture(*args, **kwargs)

        monkeypatch.setattr(
            utility_routes, "capture_save_in_ledger", capture_while_another_editor_saves
        )
        written = _put(client, "helpers", "VALUE = 2\n")
        monkeypatch.setattr(utility_routes, "capture_save_in_ledger", capture)

        assert written.status_code == 200, written.text
        assert written.json()["content"] == "VALUE = 9\n"
        assert written.json()["revision"] == _revision(client, "helpers")

    def test_a_delete_against_a_changed_file_is_refused_and_keeps_it(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        target = tmp_path / "utility" / "helpers.py"
        target.parent.mkdir()
        target.write_text("VALUE = 1\n", encoding="utf-8")
        opened = _revision(client, "helpers")
        _change_outside_haute(target, "VALUE = 2\n")

        res = _delete(client, "helpers", revision=opened)

        assert res.status_code == 412
        assert target.read_text(encoding="utf-8") == "VALUE = 2\n"

    def test_a_create_never_replaces_a_file_that_appeared_meanwhile(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute.routes.utility as utility_routes

        target = tmp_path / "utility" / "late.py"
        check = utility_routes._refuse_new_name_violations

        def checked_while_another_program_writes(module: str, content: str) -> None:
            check(module, content)
            target.parent.mkdir(exist_ok=True)
            target.write_text("MINE = 1\n", encoding="utf-8")

        monkeypatch.setattr(
            utility_routes, "_refuse_new_name_violations", checked_while_another_program_writes
        )

        res = client.post("/api/utility", json={"name": "late", "content": "THEIRS = 1\n"})

        assert res.status_code == 409
        assert res.json()["detail"] == "utility/late.py already exists."
        assert target.read_text(encoding="utf-8") == "MINE = 1\n"

    def test_a_reader_during_an_update_finds_the_old_file_or_the_new_one(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every write lands its first half, then the module is read, as a preview worker
        importing it would be: it must find one whole version, never part of one."""
        target = tmp_path / "utility" / "helpers.py"
        target.parent.mkdir()
        old, new = "VALUE = 1\n" * 50, "VALUE = 2\n" * 50
        target.write_text(old, encoding="utf-8")
        opened = _revision(client, "helpers")
        observed: list[str] = []

        def write_bytes_in_halves(self: Path, data: bytes) -> int:
            with open(self, "wb") as handle:
                handle.write(data[: len(data) // 2])
                handle.flush()
                observed.append(target.read_text(encoding="utf-8"))
                handle.write(data[len(data) // 2 :])
            return len(data)

        def write_text_in_halves(
            self: Path,
            data: str,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> int:
            return write_bytes_in_halves(self, data.encode(encoding or "utf-8"))

        monkeypatch.setattr(Path, "write_bytes", write_bytes_in_halves)
        monkeypatch.setattr(Path, "write_text", write_text_in_halves)

        res = _put(client, "helpers", new, revision=opened)

        assert res.status_code == 200, res.text
        assert observed, "the update wrote no bytes through Path"
        assert all(seen in (old, new) for seen in observed)
        assert target.read_text(encoding="utf-8") == new


class TestUtilityFolderContainment:
    def test_a_utility_folder_leading_outside_the_project_is_refused_by_every_route(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._file_ops import file_revision

        project = tmp_path / "project"
        outside = tmp_path / "outside"
        project.mkdir()
        outside.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setattr("haute.routes.utility.pipeline_dir", lambda: project)
        (outside / "values.py").write_text("VALUE = 1\n", encoding="utf-8")
        link = project / "utility"
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(outside), str(link))
        else:
            os.symlink(outside, link, target_is_directory=True)
        revision = file_revision((outside / "values.py").stat())

        responses = [
            client.get("/api/utility"),
            client.get("/api/utility/values"),
            client.put(
                "/api/utility/values", json={"content": "VALUE = 999\n", "revision": revision}
            ),
            client.delete("/api/utility/values", params={"revision": revision}),
            client.post("/api/utility", json={"name": "fresh"}),
        ]

        assert [res.status_code for res in responses] == [403] * 5
        assert all("VALUE" not in res.text and "values" not in res.text for res in responses)
        assert sorted(path.name for path in outside.iterdir()) == ["values.py"]
        assert (outside / "values.py").read_text(encoding="utf-8") == "VALUE = 1\n"


_WORKING = "pricing-dev"


class TestLedgerCapture:
    @pytest.fixture()
    def repository(self, tmp_path: Path) -> Path:
        init_repo(tmp_path)
        git_run(tmp_path, "checkout", "-b", _WORKING)
        write_working_branch(tmp_path, _WORKING)
        return tmp_path

    @staticmethod
    def _ledger(repository: Path) -> tuple[str, list[str]]:
        """The ledger's last commit and the files it changed."""
        ledger = f"{_WORKING}-save"
        files = git_run(repository, "show", "--name-only", "--format=", ledger).splitlines()
        return git_run(repository, "rev-parse", ledger), files

    def test_a_create_an_update_and_a_delete_are_each_captured(
        self, client: TestClient, repository: Path
    ) -> None:
        created = client.post("/api/utility", json={"name": "helpers", "content": "X = 1\n"})
        assert created.status_code == 200, created.text
        assert self._ledger(repository) == (
            created.json()["git_sha"],
            ["utility/__init__.py", "utility/helpers.py"],
        )

        updated = _put(client, "helpers", "X = 2\n", revision=created.json()["revision"])
        assert updated.status_code == 200, updated.text
        assert self._ledger(repository) == (updated.json()["git_sha"], ["utility/helpers.py"])

        deleted = _delete(client, "helpers", revision=updated.json()["revision"])
        assert deleted.status_code == 200, deleted.text
        assert self._ledger(repository) == (deleted.json()["git_sha"], ["utility/helpers.py"])
        tracked = git_run(repository, "ls-tree", "-r", "--name-only", f"{_WORKING}-save")
        assert "utility/helpers.py" not in tracked.splitlines()

    def test_a_save_a_project_hook_rewrites_answers_with_the_file_it_left(
        self, client: TestClient, repository: Path
    ) -> None:
        """The capture runs the project's pre-commit hook. The editor is handed the content and
        the revision the hook left, so its next save is not refused as a change made outside."""
        install_hook(repository, "pre-commit", REWRITING_PRE_COMMIT_HOOK)
        created = client.post("/api/utility", json={"name": "helpers", "content": "X = 1\n"})
        assert created.status_code == 200, created.text

        first = _put(client, "helpers", "X = 2\n", revision=created.json()["revision"])

        assert first.status_code == 200, first.text
        on_disk = (repository / "utility" / "helpers.py").read_text(encoding="utf-8")
        assert on_disk == "X = 2\n# formatted\n"
        assert first.json()["content"] == on_disk
        second = _put(client, "helpers", on_disk + "Y = 3\n", revision=first.json()["revision"])
        assert second.status_code == 200, second.text


class TestBoundedNames:
    def test_an_over_long_module_name_is_refused_before_anything_is_written(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        res = client.post("/api/utility", json={"name": "a" * 3000})

        assert res.status_code == 422
        assert not (tmp_path / "utility").exists()

    @pytest.mark.parametrize("method", ["get", "put", "delete"])
    def test_an_over_long_module_in_the_path_is_refused(
        self, client: TestClient, method: str
    ) -> None:
        url = f"/api/utility/{'a' * 3000}"
        res = (
            client.put(url, json={"content": "x = 1\n", "revision": "0-0"})
            if method == "put"
            else getattr(client, method)(url, params={"revision": "0-0"})
        )

        assert res.status_code == 422
