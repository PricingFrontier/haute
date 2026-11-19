"""A command ends on a failure in one line, never a traceback.

Each case runs a command whole, through the ``haute`` group, as an analyst runs it: the
failure is said in one line naming the key, the file or the setting, and the command exits 1
(or 2 for Click's own usage errors).
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from haute.cli import cli
from tests._project import use_project
from tests._source_files import REPO_ROOT


def _invoke(args: list[str]) -> tuple[int, str]:
    result = CliRunner().invoke(cli, args)
    return result.exit_code, result.output


def _project(root: Path, toml: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "haute.toml").write_text(toml, encoding="utf-8")
    (root / ".git").mkdir(exist_ok=True)
    use_project(root)
    return root


def _said_in_one_line(output: str) -> str:
    errors = [line for line in output.splitlines() if line.startswith("Error")]
    assert "Traceback" not in output, output
    assert len(errors) == 1, output
    return errors[0]


def test_a_misspelt_key_is_named_in_one_line(tmp_path: Path) -> None:
    _project(tmp_path, '[project]\npipeline = "main.py"\n\n[deploy]\nmodle_name = "motor"\n')

    exit_code, output = _invoke(["deploy", "--dry-run"])

    assert exit_code == 1
    assert "modle_name" in output
    assert "Traceback" not in output


def test_a_haute_toml_that_is_not_toml_is_named_in_one_line(tmp_path: Path) -> None:
    _project(tmp_path, '[project]\npipeline = "main.py"\n\n[broken\n')

    exit_code, output = _invoke(["impact"])

    assert exit_code == 1
    assert "is malformed and could not be parsed" in _said_in_one_line(output)


def test_a_read_only_haute_toml_is_called_read_only(tmp_path: Path) -> None:
    root = _project(tmp_path, '[project]\npipeline = "main.py"\n')
    toml = root / "haute.toml"
    toml.chmod(stat.S_IREAD)
    try:
        exit_code, output = _invoke(["init", "--ci", "none", "--target", "container"])
    finally:
        toml.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert exit_code == 1
    assert "haute.toml is read-only" in _said_in_one_line(output)


def test_a_port_out_of_range_is_a_usage_error(tmp_path: Path) -> None:
    _project(tmp_path, "[project]\n")

    exit_code, output = _invoke(["serve", "--port", "70000", "--no-browser"])

    assert exit_code == 2
    assert "70000 is not in the range" in output
    assert "Traceback" not in output


def test_status_without_mlflow_credentials_says_what_it_was_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in list(os.environ):
        if name.startswith(("MLFLOW_", "DATABRICKS_")):
            monkeypatch.delenv(name)
    _project(tmp_path, '[project]\npipeline = "main.py"\n\n[deploy]\nmodel_name = "motor"\n')

    exit_code, output = _invoke(["status"])

    assert exit_code == 1
    line = _said_in_one_line(output)
    assert "not ready to read the model's status" in line
    assert "for deploy" not in line


def test_a_run_its_memory_cap_refuses_names_the_cap_and_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "reference"
    shutil.copytree(REPO_ROOT / "examples" / "reference", root)
    use_project(root)
    monkeypatch.setenv("HAUTE_SINK_PROCESS_RSS_LIMIT_MB", "1")

    exit_code, output = _invoke(["run", str(root / "main.py")])

    assert exit_code == 1
    line = _said_in_one_line(output)
    assert line.startswith("Error: The run was not started: Haute is already using ")
    assert "of its 1.0 MiB memory cap" in line
    assert "Raise HAUTE_SINK_PROCESS_RSS_LIMIT_MB" in line
