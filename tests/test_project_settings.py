"""The one parse of ``haute.toml`` (``haute._project_settings``; pipeline-config, "The settings
file"): its encoding, its tables and keys, its paths, and that each call reads the file afresh."""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from haute._project_settings import (
    ProjectSettingsError,
    read_project_settings,
    write_settings_table,
    write_settings_text,
)

PIPELINE_TOML = b'[project]\nname = "motor"\npipeline = "rating/main.py"\n'


def _settings_file(folder: Path, raw: bytes) -> Path:
    path = folder / "haute.toml"
    path.write_bytes(raw)
    return path


def _refusal(folder: Path, raw: bytes) -> ProjectSettingsError:
    _settings_file(folder, raw)
    with pytest.raises(ProjectSettingsError) as caught:
        read_project_settings(folder)
    return caught.value


def test_no_file_is_no_settings(tmp_path: Path) -> None:
    assert read_project_settings(tmp_path) is None


class TestEncoding:
    def test_a_byte_order_mark_is_read_through(self, tmp_path: Path) -> None:
        _settings_file(tmp_path, b"\xef\xbb\xbf" + PIPELINE_TOML)
        settings = read_project_settings(tmp_path)

        assert settings is not None
        assert settings.table("project") == {"name": "motor", "pipeline": "rating/main.py"}
        assert not settings.text.startswith("\ufeff")

    def test_a_byte_that_is_not_utf8_is_named_by_position(self, tmp_path: Path) -> None:
        error = _refusal(tmp_path, b"[project]\nname = 'caf\xe9'\n")

        assert "is not UTF-8 text: the byte at position 21 is not UTF-8" in str(error)
        assert str(tmp_path / "haute.toml") in str(error)
        assert error.line == 2

    def test_malformed_toml_names_its_line_and_column(self, tmp_path: Path) -> None:
        error = _refusal(tmp_path, b"[project\n")

        assert "is malformed and could not be parsed" in str(error)
        assert (error.line, error.column) == (1, 9)


class TestTablesAndKeys:
    @pytest.mark.parametrize(
        ("raw", "named"),
        [
            (b"[project]\npipline = 'rating/main.py'\n", "[project] unknown key 'pipline'"),
            (b"[projct]\npipeline = 'rating/main.py'\n", "unknown table [projct]"),
            (b"[workbnch]\nenabled = true\n", "unknown table [workbnch]"),
            (b"[deploy.databricks]\ncatalg = 'main'\n", "[deploy.databricks] unknown key 'catalg'"),
            (b"[assistant.egress]\ntrus = 'x'\n", "[assistant.egress] unknown key 'trus'"),
            (b"[mlflow]\nmode = 'local'\n", "[mlflow] unknown key 'mode'"),
            (b"[deploy.databrick]\ncatalog = 'main'\n", "[deploy] unknown key 'databrick'"),
            (b"[deploy.unknown]\nx = 1\n", "[deploy] unknown key 'unknown'"),
            (b"[workbench]\nenabled = false\nfrm = 1\n", "[workbench] unknown key 'frm'"),
        ],
    )
    def test_an_unknown_key_or_table_is_named_where_it_is(
        self, tmp_path: Path, raw: bytes, named: str
    ) -> None:
        assert named in str(_refusal(tmp_path, raw))

    def test_the_refusal_names_what_the_place_takes(self, tmp_path: Path) -> None:
        error = _refusal(tmp_path, b"[project]\npipline = 'x.py'\n")

        assert "(the keys are name and pipeline)" in str(error)

    def test_every_problem_is_named_at_once(self, tmp_path: Path) -> None:
        error = str(_refusal(tmp_path, b"[projct]\n[server]\nhots = 'x'\n"))

        assert "unknown table [projct]" in error
        assert "[server] unknown key 'hots'" in error

    def test_a_retired_key_is_refused_in_its_own_words(self, tmp_path: Path) -> None:
        error = _refusal(tmp_path, b"[workbench]\nenabled = true\nform = 'x.json'\n")

        assert "[workbench] form was removed" in str(error)

    @pytest.mark.parametrize(
        ("raw", "named"),
        [
            (b"project = 'x'\n", "[project] must be a table"),
            (b"[deploy]\ncontainer = 'x'\n", "[deploy.container] must be a table"),
        ],
    )
    def test_a_table_that_is_not_one_is_refused(
        self, tmp_path: Path, raw: bytes, named: str
    ) -> None:
        assert named in str(_refusal(tmp_path, raw))

    def test_every_table_haute_writes_is_known(self, tmp_path: Path) -> None:
        from haute._scaffold import TARGETS, haute_toml, workbench_haute_toml

        for target in TARGETS:
            write_settings_text(tmp_path, haute_toml("motor", target, "github"))
            assert read_project_settings(tmp_path) is not None
        write_settings_text(
            tmp_path,
            workbench_haute_toml(
                "motor",
                "github",
                host="azure-container-apps",
                store="sqlserver",
                python_version="3.12",
            ),
        )
        assert read_project_settings(tmp_path) is not None


class TestPaths:
    def test_a_backslash_is_a_separator_on_every_system(self, tmp_path: Path) -> None:
        _settings_file(
            tmp_path, b"[project]\npipeline = 'rating\\main.py'\n[mlflow]\nfolder = 'a\\b'\n"
        )
        settings = read_project_settings(tmp_path)

        assert settings is not None
        assert settings.path_setting("project", "pipeline") == "rating/main.py"
        assert settings.configured_pipeline == tmp_path / "rating" / "main.py"
        assert settings.path_setting("mlflow", "folder") == "a/b"

    def test_a_path_ending_in_a_space_is_refused(self, tmp_path: Path) -> None:
        error = _refusal(tmp_path, b"[project]\npipeline = 'rating/main.py '\n")

        assert "'rating/main.py ' starts or ends with a space" in str(error)

    def test_a_path_that_is_not_text_is_refused(self, tmp_path: Path) -> None:
        error = _refusal(tmp_path, b"[test_quotes]\ndir = 3\n")

        assert "[test_quotes] dir must be a path written as a string" in str(error)

    @pytest.mark.parametrize("pipeline", ["../outside.py", "C:/elsewhere/main.py", "/etc/main.py"])
    def test_a_configured_pipeline_outside_the_project_is_refused(
        self, tmp_path: Path, pipeline: str
    ) -> None:
        error = _refusal(tmp_path, f"[project]\npipeline = '{pipeline}'\n".encode())

        assert "must name a file inside the project" in str(error)

    def test_an_empty_pipeline_configures_none(self, tmp_path: Path) -> None:
        _settings_file(tmp_path, b"[project]\npipeline = ''\n")
        settings = read_project_settings(tmp_path)

        assert settings is not None and settings.configured_pipeline is None


def test_each_call_reads_the_file_afresh(tmp_path: Path) -> None:
    _settings_file(tmp_path, PIPELINE_TOML)
    first = read_project_settings(tmp_path)
    _settings_file(tmp_path, b'[project]\npipeline = "rating/renewal.py"\n')
    second = read_project_settings(tmp_path)

    assert first is not None and second is not None
    assert first.configured_pipeline != second.configured_pipeline
    assert second.configured_pipeline == tmp_path / "rating" / "renewal.py"


def test_a_reader_cannot_change_what_another_reads(tmp_path: Path) -> None:
    _settings_file(tmp_path, PIPELINE_TOML)
    settings = read_project_settings(tmp_path)
    assert settings is not None

    table = settings.table("project")
    assert table is not None
    table["pipeline"] = "elsewhere.py"

    assert settings.path_setting("project", "pipeline") == "rating/main.py"


def test_a_refusal_crosses_a_worker_boundary(tmp_path: Path) -> None:
    error = _refusal(tmp_path, b"[project\n")
    copy = pickle.loads(pickle.dumps(error))

    assert (str(copy), copy.path, copy.line, copy.column, copy.raw) == (
        str(error),
        error.path,
        error.line,
        error.column,
        error.raw,
    )


class TestWriters:
    def test_a_table_is_replaced_and_the_rest_kept_as_written(self, tmp_path: Path) -> None:
        _settings_file(tmp_path, b'# the team\'s note\n[project]\nname = "motor"\n')
        written: list[str] = []

        def write(path: Path, text: str) -> None:
            written.append(text)
            path.write_text(text, encoding="utf-8")

        write_settings_table(tmp_path, "mlflow", {"folder": "mlruns"}, write=write)

        assert "# the team's note" in written[0]
        settings = read_project_settings(tmp_path)
        assert settings is not None and settings.table("mlflow") == {"folder": "mlruns"}

    def test_a_file_the_parse_refuses_is_not_rewritten(self, tmp_path: Path) -> None:
        path = _settings_file(tmp_path, b"[projct]\n")

        with pytest.raises(ProjectSettingsError):
            write_settings_table(tmp_path, "mlflow", {"folder": "x"}, write=_never)
        assert path.read_bytes() == b"[projct]\n"

    def test_whole_file_text_that_would_be_refused_is_not_written(self, tmp_path: Path) -> None:
        with pytest.raises(ProjectSettingsError):
            write_settings_text(tmp_path, "[projct]\n")
        assert not (tmp_path / "haute.toml").exists()


def _never(path: Path, text: str) -> None:
    raise AssertionError(f"wrote {path}")
