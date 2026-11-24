"""Haute's temporary folder is the user's own and private (SBS-03).

specs/sandbox-security/high-level.md, Trust boundary, Haute's temporary folder.
"""

from __future__ import annotations

import ast
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import pytest
from fastapi.testclient import TestClient

from haute import _file_ops, executor
from haute._project_mutation_lock import ProjectMutationLock
from haute._temp_folder import TemporaryFolderError, haute_temp_root
from haute._workbench_folder import form_folder_lock
from haute.cli import cli
from haute.cli._helpers import _write_incident
from haute.routes import _optimiser_artifacts, _training_artifacts
from tests._project import project_app, use_project
from tests._source_files import REPO_ROOT, source_files

if TYPE_CHECKING:
    from click.testing import CliRunner

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="owner and mode are POSIX's")


@pytest.fixture()
def temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A system temporary folder of the test's own, which ``tempfile`` reads at each use."""
    folder = tmp_path / "system-tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture()
def another_users_root(temporary: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The root as another user made it first: Haute runs as a user it does not belong to."""
    other = os.getuid() + 1
    root = temporary / f"haute-{other}"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(os, "getuid", lambda: other)
    return root


@pytest.fixture()
def outside_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """A command that runs with no project installed."""
    monkeypatch.setattr("haute._project_context.installed_project", lambda: None)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@posix_only
def test_every_file_haute_keeps_there_is_under_the_users_private_root(
    temporary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    use_project(project)

    async def take_the_project_lock() -> None:
        lock = ProjectMutationLock()
        async with lock:
            pass

    anyio.run(take_the_project_lock)
    with form_folder_lock(project):
        pass
    with executor.data_output_destination_lock(project / "outputs" / "result.parquet"):
        pass
    _training_artifacts.create_training_artifact_directory()
    _optimiser_artifacts._prepare_apply_artifact_root()
    monkeypatch.setattr("haute._project_context.installed_project", lambda: None)
    try:
        raise RuntimeError("a failure outside a project")
    except RuntimeError as failure:
        assert "incidents" in _write_incident("0123abcd", failure, command="run")

    root = temporary / f"haute-{os.getuid()}"
    assert [entry.name for entry in temporary.iterdir()] == [root.name]
    assert _mode(root) == 0o700
    made = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    for expected in (
        "project-locks",
        "form-locks",
        "output-locks",
        "incidents/0123abcd.txt",
        "artifacts/v1/modelling_training",
        "artifacts/v1/optimiser_apply",
    ):
        assert any(name == expected or name.startswith(f"{expected}/") for name in made), expected
    for path in [root, *root.rglob("*")]:
        assert os.lstat(path).st_uid == os.getuid(), path
    # A process takes its writer's lock once, at its first atomic write.
    assert _file_ops._writer_locks() == root / "writers"


@posix_only
def test_a_new_root_is_private_whatever_the_umask(temporary: Path) -> None:
    previous = os.umask(0o277)
    try:
        root = haute_temp_root()
    finally:
        os.umask(previous)
    assert root == temporary / f"haute-{os.getuid()}"
    assert _mode(root) == 0o700
    assert haute_temp_root() == root


@posix_only
def test_another_users_root_is_refused_naming_it(another_users_root: Path) -> None:
    with pytest.raises(TemporaryFolderError) as refused:
        haute_temp_root()
    message = str(refused.value)
    assert str(another_users_root) in message
    assert f"belongs to user {os.lstat(another_users_root).st_uid}, not you" in message
    assert "set TMPDIR to a folder of your own" in message


@posix_only
def test_a_root_open_to_others_is_refused_and_left_as_it_is(temporary: Path) -> None:
    root = temporary / f"haute-{os.getuid()}"
    root.mkdir()
    root.chmod(0o755)
    with pytest.raises(TemporaryFolderError, match=rf"run `chmod 700 {root}`"):
        haute_temp_root()
    assert _mode(root) == 0o755


@posix_only
@pytest.mark.parametrize("kind", ["link", "file"])
def test_a_root_that_is_not_a_folder_is_refused(temporary: Path, tmp_path: Path, kind: str) -> None:
    root = temporary / f"haute-{os.getuid()}"
    if kind == "link":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        root.symlink_to(elsewhere, target_is_directory=True)
    else:
        root.write_text("", encoding="utf-8")
    with pytest.raises(TemporaryFolderError, match=rf"{root}, which is not a folder"):
        haute_temp_root()


@posix_only
def test_another_users_root_stops_the_server_starting(
    another_users_root: Path, tmp_path: Path
) -> None:
    use_project(tmp_path)
    with pytest.raises(TemporaryFolderError, match=str(another_users_root)):
        with TestClient(project_app()):
            pass


@posix_only
def test_another_users_root_ends_haute_serve_before_it_starts(
    another_users_root: Path, tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "haute.toml").write_text("[project]\n", encoding="utf-8")
    use_project(tmp_path)
    monkeypatch.setattr(
        "haute.cli._serve.handle_serve", lambda _config: pytest.fail("the server started")
    )
    result = runner.invoke(cli, ["serve", "--no-browser"])
    assert result.exit_code == 1
    assert f"Error: Haute keeps its temporary files in {another_users_root}" in result.output


@posix_only
def test_an_incident_outside_a_project_says_why_it_could_not_be_written(
    another_users_root: Path, outside_a_project: None
) -> None:
    try:
        raise RuntimeError("a failure outside a project")
    except RuntimeError as failure:
        said = _write_incident("0123abcd", failure, command="run")
    assert said.startswith(
        f"Its details could not be written (Haute keeps its temporary files in {another_users_root}"
    )
    assert "a failure outside a project" in said


def test_windows_keeps_its_name_and_checks_no_owner(
    temporary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(os, "getuid", raising=False)
    assert haute_temp_root() == temporary / "haute"
    assert (temporary / "haute").is_dir()


def _gettempdir_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == "gettempdir")
        or (isinstance(node, ast.Name) and node.id == "gettempdir")
        or (
            isinstance(node, ast.ImportFrom)
            and node.module == "tempfile"
            and any(alias.name == "gettempdir" for alias in node.names)
        )
    ]


@pytest.mark.meta
def test_the_temporary_folder_is_read_only_through_haute_temp_root() -> None:
    """A fixed name directly under the shared temporary folder is the defect SBS-03 closed:
    every file Haute keeps there under a fixed name goes through ``haute_temp_root``.
    ``mkdtemp`` and ``TemporaryDirectory`` make private folders of unique names and
    need not."""
    src = REPO_ROOT / "src" / "haute"
    owner = src / "_temp_folder.py"
    found = {
        f"{path.relative_to(REPO_ROOT).as_posix()}:{line}"
        for path in source_files(src)
        if path != owner
        for line in _gettempdir_calls(path)
    }
    assert not found, f"Read the temporary folder through haute_temp_root: {sorted(found)}"
    assert _gettempdir_calls(owner), "the scan no longer recognises the one real call"
