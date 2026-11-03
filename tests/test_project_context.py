"""The project context: resolved once where a process starts, installed for each call.

The specification is ``specs/pipeline-config/high-level.md`` ("Project context") and the
``haute._project_context`` row of ``specs/pipeline-config/low-level.md``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._interactive_workers import InteractiveWorkerPool
from haute._project_context import (
    NoProjectContextError,
    ProjectContext,
    context_thread,
    current_project,
    graph_pipeline_dir,
    project_holding,
    project_scope,
    resolve_project,
    standalone_project_scope,
)
from haute._sandbox import validate_project_path
from haute._types import PipelineGraph
from haute._worker_isolation import run_isolated_worker
from haute.errors import ConfigError, PathOutsideProjectError
from haute.server import create_app
from tests._project import use_project

_PIPELINE = textwrap.dedent(
    """\
    import haute
    import polars as pl

    pipeline = haute.Pipeline("{name}")


    @pipeline.polars
    def frame() -> pl.DataFrame:
        return pl.DataFrame({{"x": [1]}})
    """
)


def _project(root: Path, *, name: str = "main", pipeline: str | None = None) -> Path:
    """A Haute project at *root*: ``haute.toml``, a git marker and one pipeline."""
    root.mkdir(parents=True, exist_ok=True)
    table = f'[project]\npipeline = "{pipeline}"\n' if pipeline else "[project]\n"
    (root / "haute.toml").write_text(table, encoding="utf-8")
    (root / ".git").mkdir(exist_ok=True)
    target = root / (pipeline or "main.py")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PIPELINE.format(name=name), encoding="utf-8")
    return root


# ── resolution ─────────────────────────────────────────────────────────────────


def test_resolve_project_walks_up_to_the_first_haute_toml_inside_git(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")
    nested = root / "deep" / "folder"
    nested.mkdir(parents=True)

    assert resolve_project(nested) == ProjectContext(root.resolve())


def test_resolve_project_refuses_a_folder_in_no_project(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not inside a Haute project"):
        resolve_project(tmp_path)


def test_resolve_project_refuses_a_haute_toml_outside_git(tmp_path: Path) -> None:
    (tmp_path / "haute.toml").write_text("[project]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not inside a git repository"):
        resolve_project(tmp_path)


def test_the_pipeline_folder_is_read_from_haute_toml_when_used(tmp_path: Path) -> None:
    context = ProjectContext.at(tmp_path)
    assert context.pipeline_dir == tmp_path.resolve()

    (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
    assert context.pipeline_dir == (tmp_path / "rating").resolve()

    (tmp_path / "haute.toml").write_text('[project]\npipeline = "../outside/main.py"\n')
    with pytest.raises(ConfigError, match="must name a file inside the project"):
        _ = context.pipeline_dir


def test_a_context_takes_an_absolute_root() -> None:
    with pytest.raises(ValueError, match="absolute root"):
        ProjectContext(Path("relative"))


# ── the accessor ──────────────────────────────────────────────────────────────


@pytest.mark.no_project
def test_with_nothing_installed_a_call_fails_and_never_reads_the_working_directory() -> None:
    with pytest.raises(NoProjectContextError, match="project_scope"):
        current_project()
    # The repository is a Haute project, and the working directory is inside it.
    assert (Path.cwd() / "haute.toml").exists() or (Path.cwd().parent / "haute.toml").exists()


def test_project_scope_nests_and_restores(tmp_path: Path) -> None:
    outer = current_project()
    inner = ProjectContext.at(tmp_path / "inner")
    with project_scope(inner):
        assert current_project() == inner
    assert current_project() == outer


def test_a_context_thread_runs_in_the_starting_project(tmp_path: Path) -> None:
    seen: list[ProjectContext | BaseException] = []

    def read() -> None:
        try:
            seen.append(current_project())
        except BaseException as exc:
            seen.append(exc)

    expected = ProjectContext.at(tmp_path / "a")
    with project_scope(expected):
        started = context_thread(read)
        plain = threading.Thread(target=read)
    started.start()
    started.join()
    plain.start()
    plain.join()

    assert seen[0] == expected
    assert isinstance(seen[1], NoProjectContextError)


def test_a_relative_source_file_is_under_the_project_root(tmp_path: Path) -> None:
    graph = PipelineGraph(source_file="rating/main.py")
    assert graph_pipeline_dir(graph) == (tmp_path / "rating").resolve()


def test_validate_project_path_anchors_a_relative_path_at_the_root(tmp_path: Path) -> None:
    (tmp_path / "model.pkl").write_bytes(b"")
    assert validate_project_path("model.pkl") == (tmp_path / "model.pkl").resolve()
    with pytest.raises(PathOutsideProjectError):
        validate_project_path("../model.pkl")


# ── standalone runs ────────────────────────────────────────────────────────────


def test_an_installed_project_wins_over_the_files(tmp_path: Path) -> None:
    other = _project(tmp_path / "other")
    with standalone_project_scope(other / "main.py") as context:
        assert context == current_project() == ProjectContext.at(tmp_path)


@pytest.mark.no_project
def test_a_standalone_call_runs_in_the_project_holding_its_file(tmp_path: Path) -> None:
    root = _project(tmp_path / "project", pipeline="rating/main.py")
    with standalone_project_scope(root / "rating" / "main.py") as context:
        assert context.root == root.resolve()
    loose = tmp_path / "loose"
    loose.mkdir()
    assert project_holding(loose) == ProjectContext.at(loose)


@pytest.mark.no_project
def test_a_standalone_run_reads_a_relative_path_from_its_files_project(tmp_path: Path) -> None:
    """``run()`` and a configured node called directly read ``data/rows.parquet`` from the
    project holding the pipeline file, whatever the working directory is."""
    root = _project(tmp_path / "project")
    (root / "data").mkdir()
    pl.DataFrame({"x": [1, 2]}).write_parquet(root / "data" / "rows.parquet")
    (root / "config" / "data_input").mkdir(parents=True)
    (root / "config" / "data_input" / "rows.json").write_text(
        '{"inputType": "file", "path": "data/rows.parquet", "format": "parquet", "mode": "scan"}\n',
        encoding="utf-8",
    )
    script = root / "standalone.py"
    script.write_text(
        textwrap.dedent(
            """\
            import haute

            pipeline = haute.Pipeline("standalone")


            @pipeline.data_input(config="config/data_input/rows.json")
            def rows():
                pass
            """
        ),
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    probe = textwrap.dedent(
        f"""\
        import runpy
        namespace = runpy.run_path({str(script)!r})
        print(namespace["rows"]().lazy().collect().height)
        print(namespace["pipeline"].run(source="live").lazy().collect().height)
        """
    )

    shown = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert shown.returncode == 0, shown.stderr
    assert shown.stdout.split() == ["2", "2"]


# ── the editor server ─────────────────────────────────────────────────────────


def test_an_app_serves_its_own_project_after_another_is_installed(tmp_path: Path) -> None:
    project_a = _project(tmp_path / "a", name="alpha")
    project_b = _project(tmp_path / "b", name="beta")
    client = TestClient(create_app(ProjectContext.at(project_a)))

    use_project(project_b)
    listed = client.get("/api/pipelines").json()

    assert [entry["name"] for entry in listed] == ["alpha"]


def test_apps_for_two_projects_in_turn_each_list_their_own(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        root = _project(tmp_path / name, name=name)
        client = TestClient(create_app(ProjectContext.at(root)))
        assert [entry["name"] for entry in client.get("/api/pipelines").json()] == [name]


@pytest.mark.no_project
def test_haute_serve_refuses_to_start_outside_a_project(tmp_path: Path) -> None:
    shown = subprocess.run(
        [sys.executable, "-c", "from haute.cli import cli; cli()", "serve", "--no-browser"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert shown.returncode == 1
    assert "not inside a Haute project" in shown.stderr
    assert "Run 'haute serve' inside a Haute project." in shown.stderr


def test_the_server_factory_refuses_without_a_resolved_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.server import PROJECT_ROOT_ENV, create_app_from_environment

    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    with pytest.raises(ConfigError, match="HAUTE_PROJECT_ROOT is not set"):
        create_app_from_environment()


# ── workers ───────────────────────────────────────────────────────────────────


def _worker_project() -> tuple[str, str]:
    """What a worker sees: its installed project's root and its own working directory."""
    return str(current_project().root), os.getcwd()


def _worker_reads_a_relative_file(name: str) -> str:
    return validate_project_path(name).read_text(encoding="utf-8")


def test_an_isolated_worker_runs_in_the_submitting_project(tmp_path: Path) -> None:
    project = ProjectContext.at(tmp_path / "submitted")
    (tmp_path / "submitted").mkdir()
    (tmp_path / "submitted" / "note.txt").write_text("submitted", encoding="utf-8")

    with project_scope(project):
        root, working_dir = run_isolated_worker(_worker_project)
        note = run_isolated_worker(_worker_reads_a_relative_file, "note.txt")

    assert root == str(project.root)
    assert Path(working_dir).resolve() != project.root
    assert note == "submitted"


def test_one_warm_worker_serves_two_projects_each_its_own(tmp_path: Path) -> None:
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        root.mkdir()
        (root / "note.txt").write_text(root.name, encoding="utf-8")
    pool = InteractiveWorkerPool(size=1, polars_threads=1, poll_interval_seconds=0.01)
    try:
        seen = []
        for root in [*roots, roots[0]]:
            with project_scope(ProjectContext.at(root)):
                seen.append(
                    pool.run(
                        _worker_reads_a_relative_file,
                        "note.txt",
                        affinity_key="one",
                        timeout_seconds=60,
                    )
                )
        with project_scope(ProjectContext.at(roots[1])):
            root, _working_dir = pool.run(_worker_project, affinity_key="one", timeout_seconds=60)
    finally:
        pool.close()

    assert seen == ["a", "b", "a"]
    assert root == str(roots[1].resolve())


def _worker_preamble_value(pipeline_dir: str) -> int:
    """A cached preamble whose helper imports ``utility`` only when it runs."""
    from haute.executor import _compile_preamble

    namespace = _compile_preamble(
        "def value():\n    import utility\n    return utility.VALUE\n", pipeline_dir=pipeline_dir
    )
    return int(namespace["value"]())


def test_a_warm_worker_imports_each_projects_utility_in_turn(tmp_path: Path) -> None:
    """Projects A, B, then A on one warm worker, the preamble cached for A: A's helper
    imports A's ``utility`` the second time too."""
    roots = {}
    for name, value in (("a", 1), ("b", 2)):
        root = tmp_path / name
        (root / "utility").mkdir(parents=True)
        (root / "utility" / "__init__.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        roots[name] = root
    pool = InteractiveWorkerPool(size=1, polars_threads=1, poll_interval_seconds=0.01)
    try:
        values = []
        for name in ("a", "b", "a"):
            with project_scope(ProjectContext.at(roots[name])):
                values.append(
                    pool.run(
                        _worker_preamble_value,
                        str(roots[name]),
                        affinity_key="one",
                        timeout_seconds=60,
                    )
                )
    finally:
        pool.close()

    assert values == [1, 2, 1]


def _worker_mlflow_destination() -> str:
    from haute.modelling._mlflow_settings import load_mlflow_settings

    settings = load_mlflow_settings()
    return "" if settings is None else settings.tracking_uri


def test_a_worker_request_after_a_settings_update_reads_the_new_destination(
    tmp_path: Path,
) -> None:
    (tmp_path / "haute.toml").write_text("[project]\n", encoding="utf-8")
    client = TestClient(create_app(current_project()))
    pool = InteractiveWorkerPool(size=1, polars_threads=1, poll_interval_seconds=0.01)
    try:
        before = pool.run(_worker_mlflow_destination, affinity_key="one", timeout_seconds=60)
        response = client.put(
            "/api/mlflow/settings", json={"tracking_uri": "http://localhost:5000"}
        )
        assert response.status_code == 200, response.text
        after = pool.run(_worker_mlflow_destination, affinity_key="one", timeout_seconds=60)
    finally:
        pool.close()

    assert (before, after) == ("", "http://localhost:5000")
