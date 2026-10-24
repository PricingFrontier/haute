"""One Python for a project: local development, CI, and the deployed model.

``haute init`` writes ``.python-version`` when the project has none and pins every CI
job and a container's base image to it; deploy refuses an interpreter or a ``python``
base image that differs, and a Databricks model's conda environment takes the same
version (``haute._project_python``).
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

from haute._project_python import (
    DEFAULT_PYTHON,
    ProjectPythonError,
    base_image_python_version,
    container_base_image,
    declared_python_version,
    deploy_python_mismatch,
    deployed_python_version,
    python_version_matches,
)
from haute.cli import cli
from haute.deploy._config import DeployConfig, _validate_python_versions
from haute.errors import DeployError

RUNNING_MINOR = f"{sys.version_info.major}.{sys.version_info.minor}"
OTHER_MINOR = "3.11" if RUNNING_MINOR != "3.11" else "3.12"


def _project(tmp_path: Path, *, pin: str | None = None, requires: str | None = None) -> Path:
    if pin is not None:
        (tmp_path / ".python-version").write_text(f"{pin}\n", encoding="utf-8")
    if requires is not None:
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "p"\nrequires-python = "{requires}"\n', encoding="utf-8"
        )
    return tmp_path


@pytest.mark.parametrize(
    ("pin", "requires", "declared"),
    [
        (None, None, None),
        (None, ">=3.13", "3.13"),
        (None, ">=3.9", "3.11"),
        ("3.12", ">=3.11", "3.12"),
        ("3.13.11", None, "3.13.11"),
        ("3.13.6", ">=3.13.5", "3.13.6"),
        # A range admitting part of a minor names its lowest release, so no runtime
        # taking another patch of the minor can fall outside it.
        (None, "==3.12.8", "3.12.8"),
        (None, ">=3.12.8,<3.12.10", "3.12.8"),
        (None, ">=3.13.5", "3.13.5"),
    ],
)
def test_the_declared_python(
    tmp_path: Path, pin: str | None, requires: str | None, declared: str | None
) -> None:
    assert declared_python_version(_project(tmp_path, pin=pin, requires=requires)) == declared


@pytest.mark.parametrize(
    ("pin", "requires", "says"),
    [
        ("3.10", None, "Haute runs on Python 3.11 or newer"),
        ("3.13.1garbage", None, "holds '3.13.1garbage'"),
        ("pypy@3.11", None, "holds 'pypy@3.11'"),
        ("", None, "holds ''"),
        (None, "not-a-specifier", "is not a version specifier"),
        (None, "<3.11", "admits no Python Haute runs on"),
        ("3.12", ">=3.13", "excludes some of its releases"),
        ("3.13", ">=3.13.5", "excludes some of its releases; pin a patch"),
        ("3.13.1", ">=3.13.5", "excludes it."),
    ],
)
def test_an_unreadable_or_contradictory_declaration_is_refused(
    tmp_path: Path, pin: str | None, requires: str | None, says: str
) -> None:
    project = _project(tmp_path, pin=pin, requires=requires)

    with pytest.raises(ProjectPythonError, match=re.escape(says)):
        declared_python_version(project)
    with pytest.raises(ProjectPythonError, match=re.escape(says)):
        deploy_python_mismatch(project)


def test_a_requires_python_that_is_not_a_string_is_refused(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\nrequires-python = 3.11\n', encoding="utf-8"
    )

    with pytest.raises(ProjectPythonError, match="is not a version specifier"):
        declared_python_version(tmp_path)


def test_versions_match_at_the_declared_precision() -> None:
    assert python_version_matches("3.13", "3.13.11")
    assert python_version_matches("3.13.11", "3.13.11")
    assert not python_version_matches("3.13.11", "3.13.1")
    assert not python_version_matches("3.13", "3.12.8")


def test_a_container_image_runs_the_projects_python() -> None:
    assert container_base_image("3.13") == "python:3.13.1-slim"
    assert container_base_image("3.13.11") == "python:3.13.11-slim"
    assert base_image_python_version("docker.io/library/python:3.12.8-slim") == "3.12.8"
    assert base_image_python_version("myregistry/rating-base:1.2.3") is None


def test_the_deployed_python_is_the_pin_else_the_running_one(tmp_path: Path) -> None:
    assert deployed_python_version(_project(tmp_path, requires=">=3.11")) == RUNNING_MINOR
    assert deployed_python_version(_project(tmp_path, pin="3.13")) == "3.13"


def test_an_unpinned_deploy_keeps_its_exact_release_when_the_range_splits_a_minor(
    tmp_path: Path,
) -> None:
    # Other releases of the running minor fall outside the range, so the model's
    # environment must not float to them.
    running = ".".join(str(part) for part in sys.version_info[:3])

    assert deployed_python_version(_project(tmp_path, requires=f"=={running}")) == running


@pytest.mark.parametrize(
    ("pin", "requires", "says"),
    [
        (RUNNING_MINOR, None, None),
        (None, f">={RUNNING_MINOR}", None),
        (OTHER_MINOR, None, "pins Python"),
        (None, f">{RUNNING_MINOR}.99", "excludes"),
    ],
)
def test_deploy_refuses_an_interpreter_the_project_does_not_run_on(
    tmp_path: Path, pin: str | None, requires: str | None, says: str | None
) -> None:
    mismatch = deploy_python_mismatch(_project(tmp_path, pin=pin, requires=requires))

    assert mismatch is None if says is None else says in (mismatch or "")


def _container_config(project: Path, base_image: str) -> DeployConfig:
    config = DeployConfig(pipeline_file=project / "main.py", model_name="m", project_dir=project)
    config.target = "container"
    config.container.base_image = base_image
    return config


def test_deploy_refuses_a_base_image_on_another_python(tmp_path: Path) -> None:
    project = _project(tmp_path, pin=RUNNING_MINOR)

    _validate_python_versions(_container_config(project, f"python:{RUNNING_MINOR}.4-slim"), project)
    # An image Haute cannot read a version from is the project's to keep in step.
    _validate_python_versions(
        _container_config(project, "registry/base@sha256:" + "a" * 64), project
    )
    with pytest.raises(DeployError, match=f"runs Python {OTHER_MINOR}.9"):
        _validate_python_versions(
            _container_config(project, f"python:{OTHER_MINOR}.9-slim"), project
        )


def test_deploy_refuses_a_base_image_requires_python_excludes(tmp_path: Path) -> None:
    running = ".".join(str(part) for part in sys.version_info[:3])
    project = _project(tmp_path, requires=f">={running}")

    _validate_python_versions(_container_config(project, f"python:{running}-slim"), project)
    with pytest.raises(DeployError, match="requires-python"):
        _validate_python_versions(_container_config(project, "python:3.11.0-slim"), project)
    if sys.version_info.micro > 0:
        # A minor-only tag can float to a release before the range's floor.
        with pytest.raises(DeployError, match="requires-python"):
            _validate_python_versions(
                _container_config(project, f"python:{RUNNING_MINOR}-slim"), project
            )


@pytest.mark.parametrize("target", ["databricks", "container"])
def test_init_gives_local_ci_and_deploy_one_python(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, requires=">=3.13")

    result = runner.invoke(cli, ["init", "--target", target, "--ci", "github"])

    assert result.exit_code == 0, result.output
    # Local: uv reads .python-version.
    assert (tmp_path / ".python-version").read_text(encoding="utf-8") == "3.13\n"
    # CI: every job.
    workflow = (tmp_path / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.13"' in workflow and DEFAULT_PYTHON + '"' not in workflow
    # Deploy: the model's Python, and a container's base image.
    assert deployed_python_version(tmp_path) == "3.13"
    toml = tomllib.loads((tmp_path / "haute.toml").read_text(encoding="utf-8"))
    if target == "container":
        assert toml["deploy"]["container"]["base_image"] == "python:3.13.1-slim"


def test_init_pins_the_lowest_release_a_range_splitting_a_minor_admits(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, requires=">=3.13.5")

    result = runner.invoke(cli, ["init", "--target", "container", "--ci", "github"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".python-version").read_text(encoding="utf-8") == "3.13.5\n"
    workflow = (tmp_path / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.13.5"' in workflow
    toml = tomllib.loads((tmp_path / "haute.toml").read_text(encoding="utf-8"))
    assert toml["deploy"]["container"]["base_image"] == "python:3.13.5-slim"


def test_init_refuses_an_unreadable_python_version_and_writes_nothing(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, pin="3.13.1garbage")

    result = runner.invoke(cli, ["init", "--ci", "github"])

    assert result.exit_code != 0
    assert "3.13.1garbage" in result.output
    assert not (tmp_path / ".github").exists()
    assert (tmp_path / ".python-version").read_text(encoding="utf-8") == "3.13.1garbage\n"


def test_init_keeps_an_existing_python_version(
    runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, pin="3.12.8", requires=">=3.11")

    result = runner.invoke(cli, ["init", "--ci", "github"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".python-version").read_text(encoding="utf-8") == "3.12.8\n"
    workflow = (tmp_path / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.12.8"' in workflow


def test_an_unpinned_projects_base_image_is_its_own(tmp_path: Path) -> None:
    """A project that pins no Python (one older than this check) keeps its base image."""
    project = _project(tmp_path, requires=">=3.11")

    _validate_python_versions(_container_config(project, f"python:{OTHER_MINOR}.9-slim"), project)


def test_an_unparseable_pyproject_is_refused(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")

    with pytest.raises(ProjectPythonError, match="pyproject.toml is not valid TOML"):
        declared_python_version(tmp_path)


def test_deploy_refuses_to_run_on_another_python(tmp_path: Path) -> None:
    project = _project(tmp_path, pin=OTHER_MINOR)

    with pytest.raises(DeployError, match=f"pins Python {OTHER_MINOR}"):
        _validate_python_versions(_container_config(project, "python:3.12.8-slim"), project)


def test_a_pyproject_without_requires_python_declares_none(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")

    assert declared_python_version(tmp_path) is None
