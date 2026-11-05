"""Every file ``haute init`` writes has LF line endings, kept in every checkout (READ-03).

Spec: specs/cli/high-level.md, ``haute init``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from haute._scaffold_files import GITATTRIBUTES_ENTRIES, ensure_gitattributes
from haute.cli import cli
from tests._project import use_project


def _init(project: Path) -> None:
    use_project(project)
    result = CliRunner().invoke(cli, ["init"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_no_file_haute_init_writes_holds_a_carriage_return(tmp_path: Path) -> None:
    _init(tmp_path)

    written = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert tmp_path / ".githooks" / "pre-commit" in written
    assert tmp_path / ".gitattributes" in written
    assert [path for path in written if b"\r" in path.read_bytes()] == []


def test_the_gitattributes_pins_lf_and_marks_the_lock_file_generated(tmp_path: Path) -> None:
    _init(tmp_path)

    lines = (tmp_path / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert lines == list(GITATTRIBUTES_ENTRIES)
    assert "uv.lock linguist-generated=true" in lines
    assert not any(line.startswith("*.csv") for line in lines)


def test_a_teams_gitattributes_keeps_its_lines(tmp_path: Path) -> None:
    (tmp_path / ".gitattributes").write_bytes(b"*.parquet binary\n*.py text eol=lf\n")

    added = ensure_gitattributes(tmp_path)

    assert "*.py text eol=lf" not in added
    text = (tmp_path / ".gitattributes").read_bytes()
    assert text.startswith(b"*.parquet binary\n*.py text eol=lf\n\n# Haute\n")
    assert ensure_gitattributes(tmp_path) == []
    assert (tmp_path / ".gitattributes").read_bytes() == text


def test_a_clone_with_autocrlf_checks_out_lf(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _init(project)
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "scaffold")

    clone = tmp_path / "clone"
    _git(tmp_path, "-c", "core.autocrlf=true", "clone", "-q", str(project), str(clone))

    checked = [
        clone / ".githooks" / "pre-commit",
        clone / "rating" / "main.py",
        clone / "haute.toml",
        clone / "pyproject.toml",
        *clone.glob(".github/workflows/*.yml"),
    ]
    assert len(checked) > 4
    assert [path for path in checked if b"\r" in path.read_bytes()] == []
