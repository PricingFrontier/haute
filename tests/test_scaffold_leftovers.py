"""A new project holds no ``uv init`` placeholders and no settings at their defaults (READ-41).

Spec: specs/cli/high-level.md, ``haute init``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from haute.cli import cli
from tests._project import use_project

_UV_PACKAGE_PYPROJECT = """\
[project]
name = "motor-pricing"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.11"
dependencies = []

[project.scripts]
motor-pricing = "motor_pricing:main"

[build-system]
requires = ["uv_build>=0.8.13,<0.9.0"]
build-backend = "uv_build"
"""

_UV_PACKAGE_INIT = 'def main() -> None:\n    print("Hello from motor-pricing!")\n'


def _uv_init(project: Path) -> None:
    """What ``uv init --package motor-pricing`` writes."""
    (project / "pyproject.toml").write_bytes(_UV_PACKAGE_PYPROJECT.encode("utf-8"))
    (project / "README.md").write_bytes(b"")
    package = project / "src" / "motor_pricing"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(_UV_PACKAGE_INIT.encode("utf-8"))


def _init(project: Path, *args: str) -> str:
    use_project(project)
    result = CliRunner().invoke(cli, ["init", *args], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.parametrize(
    "args",
    [(), ("--workbench", "--store", "sqlserver", "--sign-in", "password")],
    ids=["automated", "workbench"],
)
def test_a_first_init_removes_uv_inits_leftovers_and_writes_no_defaults(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    _uv_init(tmp_path)

    output = _init(tmp_path, *args)

    assert not (tmp_path / "README.md").exists()
    assert not (tmp_path / "src").exists()
    pyproject = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert "readme" not in pyproject["project"]
    assert "description" not in pyproject["project"]
    assert "scripts" not in pyproject["project"]
    assert "build-system" not in pyproject
    assert "Removed src/motor_pricing/" in output
    haute_toml = (tmp_path / "haute.toml").read_text(encoding="utf-8")
    assert "[test_quotes]" not in haute_toml
    assert "port = 8080" not in haute_toml
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count("# Haute") == 1


def test_a_rerun_keeps_a_readme_and_package_written_by_hand(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / "README.md").write_bytes(b"# Motor pricing\n")
    (tmp_path / "src" / "motor_pricing").mkdir(parents=True)
    (tmp_path / "src" / "motor_pricing" / "__init__.py").write_bytes(_UV_PACKAGE_INIT.encode())

    _init(tmp_path)

    assert (tmp_path / "README.md").read_bytes() == b"# Motor pricing\n"
    assert (tmp_path / "src" / "motor_pricing" / "__init__.py").exists()


def test_a_first_init_keeps_a_package_something_imports(tmp_path: Path) -> None:
    _uv_init(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_motor.py").write_bytes(b"from motor_pricing import main\n")

    _init(tmp_path)

    assert (tmp_path / "src" / "motor_pricing" / "__init__.py").exists()
    pyproject = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["build-system"]["build-backend"] == "uv_build"


def test_env_example_says_what_each_azure_value_is_for(tmp_path: Path) -> None:
    _init(tmp_path, "--workbench", "--store", "sqlserver", "--sign-in", "password")
    lines = (tmp_path / ".env.example").read_text(encoding="utf-8").splitlines()
    for variable in ("AZURE_SUBSCRIPTION_ID=", "AZURE_TENANT_ID=", "AZURE_CLIENT_ID="):
        assert lines[lines.index(variable) - 1].startswith("# ")


def test_the_gitignore_heading_is_written_once_however_often_guards_are_added(
    tmp_path: Path,
) -> None:
    from haute._gitignore_guard import ensure_gitignore_guards

    (tmp_path / ".gitignore").write_bytes(b"*.pyc\n\n# Haute\n.env\n")
    ensure_gitignore_guards(tmp_path)
    ensure_gitignore_guards(tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count("# Haute") == 1
