"""Every command, and the editor's load, finds and reads a project by one rule
(pipeline-config high-level, "Finding the project" and "The settings file"; CLI high-level,
the table of what each command needs).

Each command runs on a table of projects: started from a folder inside the project; with a
byte-order mark; with a misspelt key; with a misspelt table; with a backslash in a path;
with no git repository; with no ``haute.toml``; and with a second pipeline beside the
configured one. Commands that need a project give the same reading or the same refusal, in
the same words; a command that runs without a project or a ``haute.toml`` still does. A
command without a row in :data:`COMMANDS` fails here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from haute._project_context import ProjectContext, current_project
from haute.cli import cli
from haute.cli._helpers import HauteCommand, command_settings
from haute.discovery import discover_pipelines
from tests._project import work_elsewhere
from tests.conftest import write_data_input_config

pytestmark = pytest.mark.no_project

#: Every command: what it needs of the project (the CLI specification's table) and the
#: arguments it is invoked with here.
COMMANDS: dict[tuple[str, ...], tuple[str, tuple[str, ...]]] = {
    ("init",): ("none", ()),
    ("gpu-setup",): ("none", ()),
    ("run",): ("pipeline_file", ("--node", "doubled")),
    ("lint",): ("pipeline_file", ()),
    ("status",): ("project_if_any", ()),
    ("deploy",): ("project_if_any", ()),
    ("train",): ("project_if_any", ("train.py",)),
    ("serve",): ("project", ()),
    ("smoke",): ("project", ()),
    ("impact",): ("project", ()),
    ("ci", "secrets"): ("project", ()),
    ("ci", "update"): ("project", ()),
    ("ci", "registry-host"): ("project", ()),
    ("app", "build"): ("project", ()),
    ("app", "rollout"): ("project", ("dev",)),
    ("app", "impact"): ("project", ()),
    ("app", "serve"): ("project", ()),
}

_PIPELINE = '''\
import haute
import polars as pl

pipeline = haute.Pipeline("{name}")


@pipeline.data_input(config="{config}")
def source():
    """Read data."""


@pipeline.polars
def doubled(source: pl.DataFrame) -> pl.DataFrame:
    return source.with_columns(y=pl.col("x") * 2)


pipeline.connect("source", "doubled")
'''


@dataclass(frozen=True)
class Project:
    """A row of the table: where commands start, and what they should find."""

    start: Path
    #: The project root a command that finds one installs; ``None`` where none is found.
    root: Path | None
    #: Words every command that reads the settings refuses in; ``None`` where it reads them.
    refusal: str | None = None
    #: The pipelines the one list holds, by path from the root.
    pipelines: tuple[str, ...] = ("rating/main.py",)


def _pipeline(folder: Path, data_root: Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    config = write_data_input_config(folder, name, "data/x.csv")
    (folder / f"{name}.py").write_text(_PIPELINE.format(name=name, config=config), encoding="utf-8")
    (data_root / "data").mkdir(exist_ok=True)
    (data_root / "data" / "x.csv").write_text("x\n1\n2\n", encoding="utf-8")


def _project(
    root: Path, toml: bytes = b'[project]\nname = "motor"\npipeline = "rating/main.py"\n'
) -> Path:
    root.mkdir()
    (root / "haute.toml").write_bytes(toml)
    (root / ".git").mkdir()
    _pipeline(root / "rating", root, "main")
    return root


def _inside(tmp: Path) -> Project:
    root = _project(tmp / "inside")
    return Project(start=root / "rating", root=root)


def _bom(tmp: Path) -> Project:
    root = _project(
        tmp / "bom", b'\xef\xbb\xbf[project]\nname = "motor"\npipeline = "rating/main.py"\n'
    )
    return Project(start=root, root=root)


def _misspelt_key(tmp: Path) -> Project:
    root = _project(tmp / "key", b'[project]\nname = "motor"\npipline = "rating/main.py"\n')
    return Project(start=root, root=root, refusal="[project] unknown key 'pipline'")


def _misspelt_table(tmp: Path) -> Project:
    root = _project(tmp / "table", b'[projct]\nname = "motor"\npipeline = "rating/main.py"\n')
    return Project(start=root, root=root, refusal="unknown table [projct]")


def _backslash(tmp: Path) -> Project:
    root = _project(tmp / "backslash", b"[project]\nname = 'motor'\npipeline = 'rating\\main.py'\n")
    return Project(start=root, root=root)


def _no_git(tmp: Path) -> Project:
    root = _project(tmp / "nogit")
    (root / ".git").rmdir()
    return Project(
        start=root, root=None, refusal="which is not inside a git repository: run 'git init'"
    )


def _no_toml(tmp: Path) -> Project:
    folder = tmp / "bare"
    folder.mkdir()
    _pipeline(folder, folder, "main")
    return Project(start=folder, root=None, pipelines=())


def _second_pipeline(tmp: Path) -> Project:
    root = _project(tmp / "second")
    _pipeline(root / "rating", root, "renewal")
    return Project(start=root, root=root, pipelines=("rating/main.py", "rating/renewal.py"))


PROJECTS: dict[str, Callable[[Path], Project]] = {
    "inside": _inside,
    "bom": _bom,
    "misspelt_key": _misspelt_key,
    "misspelt_table": _misspelt_table,
    "backslash": _backslash,
    "no_git": _no_git,
    "no_toml": _no_toml,
    "second_pipeline": _second_pipeline,
}

_NO_PROJECT = (
    "or any folder above it: run this inside a Haute project, or make one with 'haute init'"
)


def _leaves(
    group: click.Group, prefix: tuple[str, ...] = ()
) -> dict[tuple[str, ...], click.Command]:
    found: dict[tuple[str, ...], click.Command] = {}
    for name, command in group.commands.items():
        path = (*prefix, name)
        if isinstance(command, click.Group):
            found.update(_leaves(command, path))
        else:
            found[path] = command
    return found


def test_every_command_has_a_row_and_states_its_need() -> None:
    leaves = _leaves(cli)

    assert set(leaves) == set(COMMANDS), "a command without a row here, or a row without one"
    for path, command in leaves.items():
        assert isinstance(command, HauteCommand), " ".join(path)
        assert command.need == COMMANDS[path][0], " ".join(path)


@dataclass
class _Reading:
    root: Path
    pipeline: Path | None
    pipelines: tuple[str, ...]


def _probe(seen: list[_Reading], need: str) -> Callable[..., None]:
    """A command body that records what the command found, in place of its own work: a
    command that needs no project reads no settings."""

    def body(**_params: Any) -> None:
        root = current_project().root
        settings = None if need == "none" else command_settings()
        pipelines: tuple[str, ...] = ()
        if settings is not None:
            pipelines = tuple(
                path.resolve().relative_to(root).as_posix() for path in discover_pipelines(root)
            )
        seen.append(
            _Reading(root, None if settings is None else settings.configured_pipeline, pipelines)
        )

    return body


def _said(output: str) -> str:
    """The command's one failure line, as the ``haute`` group says it."""
    lines = [line for line in output.splitlines() if line.startswith("Error:")]
    assert len(lines) == 1, output
    return lines[0]


@pytest.mark.parametrize("name", list(PROJECTS))
def test_every_command_finds_and_reads_the_project_by_one_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    project = PROJECTS[name](tmp_path)
    leaves = _leaves(cli)
    runner = CliRunner()
    refusals: dict[tuple[str, ...], str] = {}
    readings: dict[tuple[str, ...], _Reading] = {}
    work_elsewhere(project.start)

    for path, (need, args) in COMMANDS.items():
        if need == "pipeline_file":
            # Run for real: the command itself finds the pipeline file's project.
            result = runner.invoke(cli, [*path, *args])
            if result.exit_code:
                refusals[path] = _said(result.output)
            continue
        seen: list[_Reading] = []
        monkeypatch.setattr(leaves[path], "callback", _probe(seen, need))
        result = runner.invoke(cli, [*path, *args])
        if result.exit_code:
            refusals[path] = _said(result.output)
        else:
            assert len(seen) == 1, (path, result.output)
            readings[path] = seen[0]

    reads = {path for path, (need, _args) in COMMANDS.items() if need != "none"}
    needs_one = {path for path, (need, _args) in COMMANDS.items() if need == "project"}
    # A command that needs no project runs wherever it starts, in that folder.
    for path, (need, _args) in COMMANDS.items():
        if need == "none":
            assert path not in refusals, (path, refusals.get(path))
            assert readings[path].root == project.start.resolve()

    if project.refusal is not None:
        # Every command that reads the settings refuses, in the same words.
        assert set(refusals) == reads, sorted(set(refusals) ^ reads)
        assert len(set(refusals.values())) == 1, refusals
        assert project.refusal in next(iter(refusals.values()))
        return

    if project.root is None:
        # No haute.toml: a command that needs a project refuses in the same words; every
        # other command runs in the folder it started in (run and lint on the bare file).
        assert set(refusals) == needs_one, sorted(set(refusals) ^ needs_one)
        assert len(set(refusals.values())) == 1, refusals
        assert _NO_PROJECT in next(iter(refusals.values()))
        for path in reads - needs_one:
            if path in readings:
                assert readings[path].root == project.start.resolve()
                assert readings[path].pipeline is None
        return

    assert refusals == {}, refusals
    root = project.root.resolve()
    found = {path: reading for path, reading in readings.items() if path in reads}
    assert {reading.root for reading in found.values()} == {root}
    assert {reading.pipeline for reading in found.values()} == {root / "rating" / "main.py"}
    assert {reading.pipelines for reading in found.values()} == {project.pipelines}


def _editor(root: Path) -> TestClient:
    from haute.server import create_app

    return TestClient(create_app(ProjectContext.at(root)))


@pytest.mark.parametrize("name", ["inside", "bom", "backslash", "second_pipeline"])
def test_the_editor_loads_the_configured_pipeline(tmp_path: Path, name: str) -> None:
    project = PROJECTS[name](tmp_path)
    assert project.root is not None
    client = _editor(project.root)

    document = client.get("/api/pipeline").json()
    listed = client.get("/api/pipelines").json()

    assert document["pipeline_name"] == "main"
    assert document["load_status"] == "ready"
    assert sorted(entry["file"] for entry in listed) == sorted(project.pipelines)


@pytest.mark.parametrize("name", ["misspelt_key", "misspelt_table"])
def test_the_editor_names_what_the_parse_refuses_instead_of_an_empty_canvas(
    tmp_path: Path, name: str
) -> None:
    project = PROJECTS[name](tmp_path)
    assert project.root is not None and project.refusal is not None

    document = _editor(project.root).get("/api/pipeline").json()

    assert document["load_status"] == "source_only"
    assert document["source_file"] == "haute.toml"
    assert '"rating/main.py"' in document["source_text"]
    [issue] = document["diagnostics"]
    assert issue["code"] == "project_settings_invalid"
    assert project.refusal in issue["message"]


def test_a_setting_edited_between_two_calls_to_a_running_server_is_read_by_the_second(
    tmp_path: Path,
) -> None:
    project = _second_pipeline(tmp_path)
    assert project.root is not None
    client = _editor(project.root)
    assert client.get("/api/pipeline").json()["pipeline_name"] == "main"

    (project.root / "haute.toml").write_text(
        '[project]\nname = "motor"\npipeline = "rating/renewal.py"\n', encoding="utf-8"
    )
    assert client.get("/api/pipeline").json()["pipeline_name"] == "renewal"

    (project.root / "haute.toml").write_text('[projct]\nname = "motor"\n', encoding="utf-8")
    document = client.get("/api/pipeline").json()
    assert document["diagnostics"][0]["code"] == "project_settings_invalid"


@pytest.mark.parametrize("command", ["run", "lint"])
def test_run_and_lint_take_a_file_in_a_bare_folder(tmp_path: Path, command: str) -> None:
    folder = tmp_path / "bare"
    folder.mkdir()
    _pipeline(folder, folder, "main")
    work_elsewhere(tmp_path)

    extra = ["--node", "doubled"] if command == "run" else []
    result = CliRunner().invoke(cli, [command, str(folder / "main.py"), *extra])

    assert result.exit_code == 0, result.output


def test_run_reads_a_data_input_from_the_project_root_from_inside_the_project(
    tmp_path: Path,
) -> None:
    project = _inside(tmp_path)
    work_elsewhere(project.start)

    result = CliRunner().invoke(cli, ["run", "--node", "doubled"])

    assert result.exit_code == 0, result.output
    assert "Output - doubled (2 rows)" in result.output


def test_serve_ends_in_one_line_when_the_configured_pipeline_is_missing(tmp_path: Path) -> None:
    project = _inside(tmp_path)
    assert project.root is not None
    (project.root / "rating" / "main.py").unlink()
    work_elsewhere(project.root)

    result = CliRunner().invoke(cli, ["serve", "--no-browser"])

    assert result.exit_code == 1
    assert "[project].pipeline does not exist" in _said(result.output)
    assert "Traceback" not in result.output
