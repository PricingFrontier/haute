"""Tests for haute.routes.files — file browsing and schema inspection endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(work_dir: Path) -> TestClient:
    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


# ───────────────────────────── browse_files ─────────────────────────────


class TestBrowseFilesDefaultDir:
    def test_returns_200_for_default_dir(self, client: TestClient, work_dir: Path):
        (work_dir / "data.parquet").write_bytes(b"")
        resp = client.get("/api/files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dir"] == "."

    def test_lists_matching_files(self, client: TestClient, work_dir: Path):
        (work_dir / "a.csv").write_text("x\n1\n")
        (work_dir / "b.json").write_text("{}")
        resp = client.get("/api/files")
        names = [i["name"] for i in resp.json()["items"]]
        assert "a.csv" in names
        assert "b.json" in names


class TestBrowseFilesCustomDir:
    def test_subdirectory_listing(self, client: TestClient, work_dir: Path):
        sub = work_dir / "subdir"
        sub.mkdir()
        (sub / "file.parquet").write_bytes(b"")
        resp = client.get("/api/files", params={"dir": "subdir"})
        assert resp.status_code == 200
        names = [i["name"] for i in resp.json()["items"]]
        assert "file.parquet" in names


class TestBrowseFilesExtensionFiltering:
    def test_default_extensions_include_parquet_csv_json(self, client: TestClient, work_dir: Path):
        (work_dir / "a.parquet").write_bytes(b"")
        (work_dir / "b.csv").write_text("x\n")
        (work_dir / "c.json").write_text("{}")
        (work_dir / "d.txt").write_text("hi")
        resp = client.get("/api/files")
        names = [i["name"] for i in resp.json()["items"]]
        assert "a.parquet" in names
        assert "b.csv" in names
        assert "c.json" in names
        assert "d.txt" in names

    def test_default_extensions_come_from_installed_input_capabilities(
        self, client: TestClient, work_dir: Path
    ) -> None:
        (work_dir / "events.ndjson").write_text('{"x":1}\n', encoding="utf-8")
        (work_dir / "unsupported.xml").write_text("<x />", encoding="utf-8")

        resp = client.get("/api/files")

        assert resp.status_code == 200
        names = {item["name"] for item in resp.json()["items"]}
        assert "events.ndjson" in names
        assert "unsupported.xml" not in names

    def test_extension_matching_is_case_insensitive(
        self, client: TestClient, work_dir: Path
    ) -> None:
        (work_dir / "DATA.CSV").write_text("x\n1\n", encoding="utf-8")

        resp = client.get("/api/files", params={"extensions": ".csv"})

        assert resp.status_code == 200
        assert [item["name"] for item in resp.json()["items"]] == ["DATA.CSV"]

    def test_custom_extensions_parameter(self, client: TestClient, work_dir: Path):
        (work_dir / "a.parquet").write_bytes(b"")
        (work_dir / "b.txt").write_text("hi")
        resp = client.get("/api/files", params={"extensions": ".txt"})
        names = [i["name"] for i in resp.json()["items"]]
        assert "b.txt" in names
        assert "a.parquet" not in names


class TestBrowseFilesPathTraversal:
    def test_traversal_returns_403(self, client: TestClient, work_dir: Path):
        resp = client.get("/api/files", params={"dir": "../.."})
        assert resp.status_code == 403


class TestBrowseFilesEmptyDir:
    def test_empty_directory_returns_empty_items(self, client: TestClient, work_dir: Path):
        empty = work_dir / "empty"
        empty.mkdir()
        resp = client.get("/api/files", params={"dir": "empty"})
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestBrowseFilesTypeField:
    def test_files_and_directories_distinguished(self, client: TestClient, work_dir: Path):
        (work_dir / "mydir").mkdir()
        (work_dir / "data.csv").write_text("x\n")
        resp = client.get("/api/files")
        items = {i["name"]: i["type"] for i in resp.json()["items"]}
        assert items["mydir"] == "directory"
        assert items["data.csv"] == "file"


class TestBrowseFilesHiddenFiles:
    def test_dotfiles_excluded(self, client: TestClient, work_dir: Path):
        (work_dir / ".hidden.csv").write_text("x\n")
        (work_dir / "visible.csv").write_text("x\n")
        resp = client.get("/api/files")
        names = [i["name"] for i in resp.json()["items"]]
        assert ".hidden.csv" not in names
        assert "visible.csv" in names

    def test_dotdirectories_excluded(self, client: TestClient, work_dir: Path):
        (work_dir / ".secret").mkdir()
        (work_dir / "public").mkdir()
        resp = client.get("/api/files")
        names = [i["name"] for i in resp.json()["items"]]
        assert ".secret" not in names
        assert "public" in names


class TestBrowseFilesSymlinks:
    def test_symlink_entries_are_not_advertised(
        self,
        client: TestClient,
        work_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = work_dir / "real.csv"
        target.write_text("x\n1\n", encoding="utf-8")
        link = work_dir / "linked.csv"
        link.write_text("x\n2\n", encoding="utf-8")
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == link or original_is_symlink(path),
        )

        resp = client.get("/api/files")

        assert resp.status_code == 200
        names = {item["name"] for item in resp.json()["items"]}
        assert "real.csv" in names
        assert "linked.csv" not in names


class TestBrowseFilesNonExistentDir:
    def test_returns_404(self, client: TestClient, work_dir: Path):
        resp = client.get("/api/files", params={"dir": "no_such_dir"})
        assert resp.status_code == 404


class TestBrowseFilesSize:
    def test_file_size_included(self, client: TestClient, work_dir: Path):
        content = b"hello world"
        (work_dir / "sized.csv").write_bytes(content)
        resp = client.get("/api/files")
        item = next(i for i in resp.json()["items"] if i["name"] == "sized.csv")
        assert item["size"] == len(content)

    def test_directory_size_is_none(self, client: TestClient, work_dir: Path):
        (work_dir / "adir").mkdir()
        resp = client.get("/api/files")
        item = next(i for i in resp.json()["items"] if i["name"] == "adir")
        assert item["size"] is None


class TestBrowseFilesUnresolvedCwd:
    """Regression: the file browser must resolve cwd before ``relative_to``.

    Discovered by the ci-lanes ``init-smoke`` lane: on Windows the server cwd
    can be an 8.3 short path (``C:\\Users\\RUNNER~1\\...``) while ``iterdir()``
    yields long-form entries, so an unresolved base made ``relative_to`` raise
    ``ValueError`` → HTTP 500 on ``/api/files``. POSIX ``getcwd`` resolves
    symlinks so it never bit locally; reproduced cross-platform here by
    pointing ``Path.cwd`` at a symlink whose ``resolve()`` differs.
    """

    @staticmethod
    def _symlinked_project(tmp_path: Path) -> tuple[Path, Path]:
        real = tmp_path / "real"
        (real / "data").mkdir(parents=True)
        (real / "data" / "probe.json").write_text("{}", encoding="utf-8")
        link = tmp_path / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover - privilege-gated
            pytest.skip("symlinks unavailable on this platform/privilege level")
        # The symlink path must be genuinely unresolved for the repro to bite.
        assert link.resolve() == real.resolve()
        assert link != real
        return real, link

    def test_browse_with_unresolved_cwd_lists_and_labels_correctly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _real, link = self._symlinked_project(tmp_path)
        # files.py reads ``Path.cwd()``; make it return the unresolved symlink.
        monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: link))

        from haute.server import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/files", params={"dir": "data"})

        assert resp.status_code == 200, resp.text
        item = next(i for i in resp.json()["items"] if i["name"] == "probe.json")
        # Path is relative to the resolved base, so it stays clean (no leaked
        # absolute prefix, no crash).
        assert item["path"] == str(Path("data") / "probe.json")

    def test_schema_with_unresolved_cwd_reads_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        real, link = self._symlinked_project(tmp_path)
        (real / "data" / "t.csv").write_text("x\n1\n", encoding="utf-8")
        monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: link))

        from haute.server import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/schema", params={"path": "data/t.csv"})

        assert resp.status_code == 200, resp.text
        assert [c["name"] for c in resp.json()["columns"]] == ["x"]


# ───────────────────────────── get_schema ─────────────────────────────


class TestGetSchemaParquet:
    def test_valid_parquet_returns_schema(self, client: TestClient, work_dir: Path):
        df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        df.write_parquet(work_dir / "test.parquet")
        resp = client.get("/api/schema", params={"path": "test.parquet"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "test.parquet"
        col_names = [c["name"] for c in body["columns"]]
        assert "a" in col_names
        assert "b" in col_names
        assert body["row_count"] == 3
        assert body["column_count"] == 2
        assert len(body["preview"]) == 3

    def test_empty_parquet_returns_empty_preview(self, client: TestClient, work_dir: Path):
        df = pl.DataFrame({"a": pl.Series([], dtype=pl.Int64), "b": pl.Series([], dtype=pl.Utf8)})
        df.write_parquet(work_dir / "empty.parquet")
        resp = client.get("/api/schema", params={"path": "empty.parquet"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["row_count"] == 0
        assert body["preview"] == []
        assert body["column_count"] == 2

    def test_many_columns(self, client: TestClient, work_dir: Path):
        data = {f"col_{i}": [i] for i in range(50)}
        pl.DataFrame(data).write_parquet(work_dir / "wide.parquet")
        resp = client.get("/api/schema", params={"path": "wide.parquet"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["column_count"] == 50
        assert len(body["columns"]) == 50

    def test_parquet_row_count_uses_metadata_without_len_collect(
        self,
        work_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from haute.routes.files import _read_schema_blocking

        path = work_dir / "metadata_count.parquet"
        pl.DataFrame(
            {
                "id": list(range(12)),
                "label": [f"row-{i}" for i in range(12)],
            }
        ).write_parquet(path)

        original_collect = pl.LazyFrame.collect
        collected_schemas: list[tuple[str, ...]] = []

        def guarded_collect(self: pl.LazyFrame, *args, **kwargs):
            schema_names = tuple(self.collect_schema().names())
            collected_schemas.append(schema_names)
            if schema_names == ("len",):
                raise AssertionError(
                    "Parquet row count must use file metadata, not pl.len().collect()"
                )
            return original_collect(self, *args, **kwargs)

        monkeypatch.setattr(pl.LazyFrame, "collect", guarded_collect)

        response = _read_schema_blocking("metadata_count.parquet", path)

        assert response.row_count == 12
        assert response.row_count_estimated is False
        assert response.column_count == 2
        assert len(response.preview) == 5
        assert ("len",) not in collected_schemas


class TestGetSchemaCsv:
    def test_valid_csv_returns_schema(self, client: TestClient, work_dir: Path):
        pl.DataFrame({"x": [10, 20], "y": [1.5, 2.5]}).write_csv(work_dir / "data.csv")
        resp = client.get("/api/schema", params={"path": "data.csv"})
        assert resp.status_code == 200
        body = resp.json()
        col_names = [c["name"] for c in body["columns"]]
        assert "x" in col_names
        assert "y" in col_names
        assert body["row_count"] == 2


class TestGetSchemaJsonl:
    def test_jsonl_row_count_estimated(self, client: TestClient, work_dir: Path):
        rows = [json.dumps({"a": i, "b": f"val_{i}"}) for i in range(100)]
        (work_dir / "data.jsonl").write_text("\n".join(rows) + "\n")
        resp = client.get("/api/schema", params={"path": "data.jsonl"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["row_count_estimated"] is True
        assert body["row_count"] is not None
        assert body["row_count"] > 0


class TestGetSchemaPathTraversal:
    def test_returns_403(self, client: TestClient, work_dir: Path):
        resp = client.get("/api/schema", params={"path": "../../etc/passwd"})
        assert resp.status_code == 403


class TestGetSchemaNonExistentFile:
    def test_returns_404(self, client: TestClient, work_dir: Path):
        resp = client.get("/api/schema", params={"path": "missing.parquet"})
        assert resp.status_code == 404


# ───────────────────────────── get_databricks_schema ─────────────────────────────


# ───────────────────────────── edge cases ─────────────────────────────


class TestBrowseFilesSymlinkOutsideBase:
    def test_symlink_pointing_outside_base_is_not_traversable(
        self, client: TestClient, work_dir: Path, tmp_path_factory: pytest.TempPathFactory
    ):
        outside = tmp_path_factory.mktemp("outside")
        secret = outside / "secret.csv"
        secret.write_text("password,123\n")
        link = work_dir / "escape_link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported (requires elevated privileges on Windows)")
        resp = client.get("/api/files", params={"dir": "escape_link"})
        assert resp.status_code == 403

    def test_symlink_file_outside_base_blocked_by_schema(
        self, client: TestClient, work_dir: Path, tmp_path_factory: pytest.TempPathFactory
    ):
        outside = tmp_path_factory.mktemp("outside")
        secret = outside / "secret.parquet"
        import polars as pl

        pl.DataFrame({"a": [1]}).write_parquet(secret)
        link = work_dir / "linked.parquet"
        try:
            link.symlink_to(secret)
        except OSError:
            pytest.skip("symlink creation not supported (requires elevated privileges on Windows)")
        resp = client.get("/api/schema", params={"path": "linked.parquet"})
        assert resp.status_code == 403


class TestBrowseFilesLargeDirectory:
    def test_500_plus_files_returned(self, client: TestClient, work_dir: Path):
        for i in range(510):
            (work_dir / f"file_{i:04d}.csv").write_bytes(b"x\n")
        resp = client.get("/api/files")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 510


class TestGetSchemaCorruptedParquet:
    def test_random_bytes_parquet_returns_error(self, client: TestClient, work_dir: Path):
        """Known decoder failures return a safe format-specific diagnostic.

        Parquet internals and absolute paths remain private in the structured
        server log.
        """
        (work_dir / "corrupt.parquet").write_bytes(b"\x00\x01\x02\x03\xff\xfe\xfd")
        resp = client.get("/api/schema", params={"path": "corrupt.parquet"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail == (
            "Could not decode the .parquet file. Check that it is valid and "
            "matches its file extension."
        )
        assert str(work_dir) not in detail


class TestGetSchemaUnsupportedFormat:
    def test_compound_suffix_returns_safe_actionable_400(
        self, client: TestClient, work_dir: Path
    ) -> None:
        (work_dir / "quotes.csv.gz").write_bytes(b"x\n1\n")

        resp = client.get("/api/schema", params={"path": "quotes.csv.gz"})

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert ".csv.gz" in detail
        assert "Supported" in detail
        assert str(work_dir) not in detail


class TestGetSchemaEmptyJsonl:
    def test_empty_jsonl_returns_error_or_zero_rows(self, client: TestClient, work_dir: Path):
        (work_dir / "empty.jsonl").write_bytes(b"")
        resp = client.get("/api/schema", params={"path": "empty.jsonl"})
        assert resp.status_code in (200, 400, 500)
        if resp.status_code == 200:
            body = resp.json()
            assert body["row_count"] == 0
            assert body["preview"] == []


class TestBrowseFilesNestedSubdirectories:
    def test_browse_shows_subdirectory_type(self, client: TestClient, work_dir: Path):
        nested = work_dir / "data" / "sub1" / "sub2"
        nested.mkdir(parents=True)
        pl.DataFrame({"x": [1]}).write_parquet(nested / "file.parquet")
        resp = client.get("/api/files", params={"dir": "data"})
        assert resp.status_code == 200
        items = {i["name"]: i["type"] for i in resp.json()["items"]}
        assert items["sub1"] == "directory"

    def test_browse_nested_subdirectory(self, client: TestClient, work_dir: Path):
        nested = work_dir / "data" / "sub1" / "sub2"
        nested.mkdir(parents=True)
        pl.DataFrame({"x": [1]}).write_parquet(nested / "file.parquet")
        resp = client.get("/api/files", params={"dir": "data/sub1"})
        assert resp.status_code == 200
        items = {i["name"]: i["type"] for i in resp.json()["items"]}
        assert items["sub2"] == "directory"

    def test_browse_deepest_level_shows_file(self, client: TestClient, work_dir: Path):
        nested = work_dir / "data" / "sub1" / "sub2"
        nested.mkdir(parents=True)
        pl.DataFrame({"x": [1]}).write_parquet(nested / "file.parquet")
        resp = client.get("/api/files", params={"dir": "data/sub1/sub2"})
        assert resp.status_code == 200
        names = [i["name"] for i in resp.json()["items"]]
        assert "file.parquet" in names


class TestSchemaFilePathWithSpaces:
    def test_parquet_with_spaces_in_name(self, client: TestClient, work_dir: Path):
        pl.DataFrame({"col": [1, 2, 3]}).write_parquet(work_dir / "my data file.parquet")
        resp = client.get("/api/schema", params={"path": "my data file.parquet"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "my data file.parquet"
        assert body["row_count"] == 3

    def test_browse_lists_file_with_spaces(self, client: TestClient, work_dir: Path):
        pl.DataFrame({"col": [1]}).write_parquet(work_dir / "my data file.parquet")
        resp = client.get("/api/files")
        assert resp.status_code == 200
        names = [i["name"] for i in resp.json()["items"]]
        assert "my data file.parquet" in names


class TestBrowseFilesExtensionFilterNoMatch:
    def test_no_matching_extensions_returns_empty(self, client: TestClient, work_dir: Path):
        (work_dir / "notes.txt").write_text("hello")
        (work_dir / "readme.txt").write_text("world")
        resp = client.get("/api/files", params={"extensions": ".parquet"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items == []
