"""End-to-end contract for the blank pipeline created by ``haute init``."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

from haute.cli import cli
from haute.parser import parse_pipeline_file


@contextmanager
def _pushd(path: Path):
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


def _scaffold_project(project_root: Path) -> Path:
    with _pushd(project_root):
        result = CliRunner().invoke(cli, ["init"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return project_root / "rating" / "main.py"


def test_init_scaffolds_blank_pipeline(tmp_path: Path) -> None:
    pipeline_file = _scaffold_project(tmp_path)

    source = pipeline_file.read_text(encoding="utf-8")
    compile(source, str(pipeline_file), "exec")
    graph = parse_pipeline_file(pipeline_file)

    assert graph.pipeline_name == tmp_path.name
    assert graph.nodes == []
    assert graph.edges == []
    assert "@pipeline." not in source


def test_scaffolded_starter_test_passes_for_blank_pipeline(tmp_path: Path) -> None:
    _scaffold_project(tmp_path)
    starter_test = tmp_path / "tests" / "test_pipeline.py"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(starter_test), "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
