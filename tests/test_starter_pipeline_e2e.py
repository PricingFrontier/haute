"""Tests for Phase 5 Wave 10A #112 — starter pipeline end-to-end.

These tests pin the real promise of ``haute init``: the scaffolded
project is *immediately runnable*. A new user types

    uv init my-pricing
    cd my-pricing
    uv add haute
    haute init
    haute run rating/main.py

and gets actual output — not an empty ``Pipeline`` object and not a
"no nodes found" error. Without that, the "5-minute onboarding"
promise of the tool is fiction.

The existing starter test (``tests/test_pipeline.py`` scaffolded by
``haute init``) only checks that the file *parses* as Python. That's
a tautology — the scaffold writes literal Python. This file closes
the gap by parsing it through the Haute parser AND executing the
resulting graph through the Haute executor, from a fresh ``tmp_path``
scaffold, end to end.

No network. No external data files required — if the scaffolded
pipeline declares file-based sources, the test generates the
matching files in ``tmp_path`` so the pipeline can run under
hermetic conditions.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from click.testing import CliRunner, Result

from haute.cli import cli
from haute.executor import execute_graph
from haute.graph_utils import NodeType, PipelineGraph
from haute.parser import parse_pipeline_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


STARTER_PIPELINE_REL = Path("rating") / "main.py"


@dataclass(frozen=True)
class _StarterProject:
    project_root: Path
    pipeline_file: Path


@dataclass(frozen=True)
class _ExecutedStarterProject:
    project_root: Path
    pipeline_file: Path
    graph: PipelineGraph
    results: dict[str, Any]


@contextmanager
def _pushd(path: Path):
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


def _scaffold_project(runner: CliRunner, tmp_path: Path) -> Path:
    """Run ``haute init`` in ``tmp_path`` and return the pipeline file path.

    Fails the test loudly if ``haute init`` itself errors — a broken
    scaffold is a different bug class from the runtime checks these
    tests target.
    """
    # ``haute init`` requires a git repo to be a first-class Haute
    # project (for certain discovery operations).  Stubbing the .git
    # directory is enough — no commits needed.
    (tmp_path / ".git").mkdir(exist_ok=True)
    result = runner.invoke(cli, ["init"], catch_exceptions=False)
    assert result.exit_code == 0, f"haute init failed: {result.output}"
    pipeline_file = tmp_path / STARTER_PIPELINE_REL
    assert pipeline_file.exists(), (
        f"scaffold did not produce {pipeline_file}; init output: {result.output}"
    )
    return pipeline_file


def _materialise_referenced_data_files(pipeline_file: Path, project_root: Path) -> list[Path]:
    """Ensure any data files referenced by the starter pipeline exist.

    The scaffolded starter *may* reference sample data paths
    (``data/*.parquet`` etc.). When it does, real users follow the
    docs and drop a file into ``data/``; in a test we synthesise one
    so the pipeline can run hermetically.

    Returns the list of files we created so the caller can inspect /
    clean them.  If the starter uses synthetic in-memory data and
    references no files, this is a no-op.
    """
    created: list[Path] = []
    graph = parse_pipeline_file(pipeline_file)
    for node in graph.nodes:
        cfg = node.data.config or {}
        path_str = cfg.get("path")
        if not isinstance(path_str, str) or not path_str:
            continue
        target = Path(path_str)
        if not target.is_absolute():
            target = pipeline_file.parent / target
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write something plausible for each known format so the
        # executor can open the file.  The schemas are intentionally
        # tiny — we are not trying to fake a realistic dataset, only
        # to satisfy "file must exist and be parseable".
        suffix = target.suffix.lower()
        sample = pl.DataFrame({"id": [1, 2, 3], "value": [10.0, 20.0, 30.0]})
        if suffix == ".parquet":
            sample.write_parquet(target)
        elif suffix == ".csv":
            sample.write_csv(target)
        elif suffix == ".json":
            # JSON sources in Haute are typically lists of records.
            target.write_text(
                '[{"id": 1, "value": 10.0}, {"id": 2, "value": 20.0}]',
                encoding="utf-8",
            )
        else:
            # Unknown format — dump an empty byte so at least the
            # existence check passes. If the node needs richer content,
            # the test will fail downstream with a clear error pointing
            # at the format mismatch rather than a cryptic OSError.
            target.write_bytes(b"")
        created.append(target)
    return created


# ---------------------------------------------------------------------------
# #112.1 — Parsed graph has real structure
# ---------------------------------------------------------------------------


class TestStarterPipelineParses:
    """The scaffold must produce a real, runnable pipeline graph.

    The pre-10A starter just instantiated ``haute.Pipeline(...)`` with
    no decorated nodes at all — ``parse_pipeline_file`` returned an
    empty graph that could not be executed, linted, or run. Every
    test in this class asserts the opposite: *there are nodes*, and
    the structure is plausible.
    """

    @pytest.fixture(scope="class")
    def parsed_starter(self, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, PipelineGraph]:
        project_root = tmp_path_factory.mktemp("starter-parses")
        runner = CliRunner()
        with _pushd(project_root):
            pipeline_file = _scaffold_project(runner, project_root)
        graph = parse_pipeline_file(pipeline_file)
        return pipeline_file, graph

    def test_init_scaffolds_pipeline_file(
        self,
        parsed_starter: tuple[Path, PipelineGraph],
    ) -> None:
        """``haute init`` writes ``rating/main.py`` as the starter."""
        pipeline_file, _ = parsed_starter
        assert pipeline_file.is_file()
        # The file must be valid Python — a broken scaffold would
        # never make it past compile().
        compile(
            pipeline_file.read_text(encoding="utf-8"),
            str(pipeline_file),
            "exec",
        )

    def test_parsed_graph_has_nodes(
        self,
        parsed_starter: tuple[Path, PipelineGraph],
    ) -> None:
        """Parsed starter pipeline has at least one decorated node.

        The empty-starter regression: a user ran ``haute init`` then
        ``haute run`` and got "No pipeline nodes found". Treat it as
        the unit-test equivalent of a smoke test for the scaffold
        promise.
        """
        _, graph = parsed_starter
        assert graph.nodes, (
            "Starter pipeline must contain at least one node so "
            "`haute run` / `haute lint` work out of the box; got an "
            "empty graph (regression to the pre-Wave-10A starter)."
        )

    def test_parsed_graph_has_source_and_output(
        self,
        parsed_starter: tuple[Path, PipelineGraph],
    ) -> None:
        """Starter has at least one source node and one output-like node.

        Any runnable pipeline must have *data in* and *data out*.
        Asserting this shape — rather than specific node names —
        leaves the dev free to pick whatever makes sense in the
        starter while keeping the invariant that the pipeline can
        actually compute something.
        """
        _, graph = parsed_starter

        source_types = {
            NodeType.DATA_SOURCE,
            NodeType.API_INPUT,
        }
        sink_types = {
            NodeType.OUTPUT,
            NodeType.DATA_SINK,
        }

        node_types = {n.data.nodeType for n in graph.nodes}
        assert source_types & node_types, (
            "Starter pipeline must have a dataSource or apiInput node so "
            "data can enter the pipeline; node types present: "
            f"{sorted(str(nt) for nt in node_types)}"
        )
        assert sink_types & node_types, (
            "Starter pipeline must have an output or dataSink node so the "
            "computed result is produced somewhere; node types present: "
            f"{sorted(str(nt) for nt in node_types)}"
        )


# ---------------------------------------------------------------------------
# #112.2 — Executed graph produces rows
# ---------------------------------------------------------------------------


class TestStarterPipelineExecutes:
    """``execute_graph`` on the scaffolded pipeline returns non-empty rows.

    The old placeholder test (``test_pipeline_parses``) only checked
    that the file parsed as Python. That leaves bugs like "nodes
    exist but the first source has a wrong path" invisible. These
    tests actually drive the graph through the executor.
    """

    @pytest.fixture(scope="class")
    def executed_starter(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> _ExecutedStarterProject:
        project_root = tmp_path_factory.mktemp("starter-exec")
        runner = CliRunner()
        with _pushd(project_root):
            pipeline_file = _scaffold_project(runner, project_root)
        _materialise_referenced_data_files(pipeline_file, project_root)
        graph = parse_pipeline_file(pipeline_file)
        results = execute_graph(graph)
        return _ExecutedStarterProject(
            project_root=project_root,
            pipeline_file=pipeline_file,
            graph=graph,
            results=results,
        )

    def test_all_nodes_execute_ok(
        self,
        executed_starter: _ExecutedStarterProject,
    ) -> None:
        """Every node in the scaffolded graph returns ``status == "ok"``."""
        results = executed_starter.results

        assert results, (
            "execute_graph returned an empty dict — the graph has no "
            "executable nodes. Starter pipeline is a no-op."
        )
        failures = {nid: res.error for nid, res in results.items() if res.status != "ok"}
        assert not failures, (
            f"Starter pipeline must execute cleanly end-to-end; failures: {failures}"
        )

    def test_terminal_node_has_nonempty_output(
        self,
        executed_starter: _ExecutedStarterProject,
    ) -> None:
        """The output/sink node produces at least one row with at least one column.

        The strongest readable invariant for "the starter does something":
        rows > 0 and columns > 0 on the terminal node.
        """
        graph = executed_starter.graph
        results = executed_starter.results

        terminals = [
            n for n in graph.nodes if n.data.nodeType in (NodeType.OUTPUT, NodeType.DATA_SINK)
        ]
        assert terminals, (
            "Pre-condition: starter must declare an output or dataSink node "
            "(see TestStarterPipelineParses.test_parsed_graph_has_source_and_output)"
        )
        terminal = terminals[0]
        terminal_result = results[terminal.id]
        assert terminal_result.status == "ok", (
            f"Terminal node '{terminal.id}' failed: {terminal_result.error}"
        )
        assert terminal_result.row_count > 0, (
            f"Terminal node '{terminal.id}' produced zero rows — starter "
            "pipeline has no data flowing through it."
        )
        assert terminal_result.column_count > 0, (
            f"Terminal node '{terminal.id}' produced zero columns — starter "
            "pipeline output schema is empty."
        )

    def test_terminal_node_preview_contains_rows(
        self,
        executed_starter: _ExecutedStarterProject,
    ) -> None:
        """The terminal node's preview payload has at least one row.

        ``NodeResult.preview`` is what the GUI displays, the CLI
        prints, and what downstream tools (impact analysis, smoke
        tests) read. A zero-length preview would break all of those
        even if ``row_count > 0`` was advertised.
        """
        graph = executed_starter.graph
        results = executed_starter.results

        terminals = [
            n for n in graph.nodes if n.data.nodeType in (NodeType.OUTPUT, NodeType.DATA_SINK)
        ]
        assert terminals, "starter must declare a terminal node"
        terminal = terminals[0]
        preview = results[terminal.id].preview
        assert preview, (
            "Terminal node's preview payload is empty — the row_count "
            "advertised by the executor is a lie, and every downstream "
            "consumer (GUI, smoke, impact) will see nothing."
        )


# ---------------------------------------------------------------------------
# #112.3 — `haute lint` passes on a fresh scaffold
# ---------------------------------------------------------------------------


class TestHauteLintOnScaffold:
    """``haute lint`` on a freshly-scaffolded project must succeed.

    Lint is the gate both ``haute init`` docs and the generated CI
    workflows rely on — if the fresh scaffold fails lint, the
    generated GitHub Action fails on the user's first commit. That
    breaks the golden-path onboarding that ``haute init`` is
    advertising.
    """

    @pytest.fixture(scope="class")
    def lint_result(self, tmp_path_factory: pytest.TempPathFactory) -> Result:
        project_root = tmp_path_factory.mktemp("starter-lint")
        runner = CliRunner()
        with _pushd(project_root):
            pipeline_file = _scaffold_project(runner, project_root)
            return runner.invoke(
                cli,
                ["lint", str(pipeline_file)],
                catch_exceptions=False,
            )

    def test_lint_zero_exit_on_fresh_scaffold(
        self,
        lint_result: Result,
    ) -> None:
        """``haute lint rating/main.py`` returns exit code 0."""
        assert lint_result.exit_code == 0, (
            "haute lint must pass on the fresh scaffold — the generated "
            "CI pipeline runs `haute lint` on every PR. Lint output:\n"
            f"{lint_result.output}"
        )

    def test_lint_reports_no_issues(
        self,
        lint_result: Result,
    ) -> None:
        """The lint output body must confirm "No structural issues found".

        The exit-code check alone can't distinguish "clean run" from
        "lint silently skipped because no nodes" — pre-10A scaffold
        actually hit the latter path when parse_pipeline_file returned
        an empty graph. Exit codes have been set correctly by
        ``_lint.py`` only since then; a payload assertion adds a
        second line of defence.
        """
        assert "No structural issues found" in lint_result.output, (
            f"lint did not print the clean-run confirmation; got:\n{lint_result.output}"
        )


# ---------------------------------------------------------------------------
# #112.4 — `haute run` executes the scaffold successfully
# ---------------------------------------------------------------------------


class TestHauteRunOnScaffold:
    """``haute run`` on the scaffold returns exit 0 and prints output.

    ``haute run`` is the headline "try the pipeline" command — the
    tutorial for brand-new users goes: ``haute init`` → ``haute run``.
    If ``haute run`` exits non-zero on the fresh scaffold, the first
    impression is a broken tool.
    """

    @pytest.fixture(scope="class")
    def run_result(self, tmp_path_factory: pytest.TempPathFactory) -> Result:
        project_root = tmp_path_factory.mktemp("starter-run")
        runner = CliRunner()
        with _pushd(project_root):
            pipeline_file = _scaffold_project(runner, project_root)
        _materialise_referenced_data_files(pipeline_file, project_root)
        with _pushd(project_root):
            return runner.invoke(
                cli,
                ["run", str(pipeline_file)],
                catch_exceptions=False,
            )

    def test_run_zero_exit_on_fresh_scaffold(
        self,
        run_result: Result,
    ) -> None:
        """``haute run rating/main.py`` exits 0 after ``haute init``."""
        assert run_result.exit_code == 0, (
            "haute run failed on fresh scaffold — the core user-facing "
            "promise (init → run) is broken. Output:\n"
            f"{run_result.output}"
        )

    def test_run_output_reports_per_node_rows(
        self,
        run_result: Result,
    ) -> None:
        """Run output shows per-node ``rows x cols`` — proves real execution.

        ``haute run`` on a pipeline with no nodes prints only the
        "Running pipeline:" banner and then an error — a naive banner
        check could pass even for that failure path. The per-node
        row count is printed by ``_run.py`` ONLY when a node
        succeeded, so asserting on the ``rows`` / ``cols`` substrings
        proves the executor actually produced results.
        """
        # ``_run.py`` prints "rows x cols" only on successful node
        # execution; the banner alone doesn't guarantee anything ran.
        assert "rows" in run_result.output and "cols" in run_result.output, (
            "haute run did not print the per-node 'rows x cols' summary "
            "that `_run.py` emits on success — the command likely "
            "early-exited with an error. Full output:\n"
            f"{run_result.output}"
        )


# ---------------------------------------------------------------------------
# #112.5 — Replacement of the tautological starter test
# ---------------------------------------------------------------------------


class TestScaffoldedStarterTestIsMeaningful:
    """The scaffolded ``tests/test_pipeline.py`` must be more than a
    compile-check.

    Pre-Wave-10A the starter test was::

        def test_pipeline_parses():
            source = pipeline_path.read_text()
            compile(source, str(pipeline_path), "exec")
            assert "haute.Pipeline" in source

    That passes for *any* file containing the literal string
    ``haute.Pipeline`` — including a file with zero decorators, which
    is exactly what the pre-10A scaffold produced. The scaffold now
    ships a starter test that actually runs the pipeline, so users
    see ``pytest`` fail when they break their pipeline — the point of
    a test at all.
    """

    @pytest.fixture(scope="class")
    def starter_project(self, tmp_path_factory: pytest.TempPathFactory) -> _StarterProject:
        project_root = tmp_path_factory.mktemp("starter-test")
        runner = CliRunner()
        with _pushd(project_root):
            pipeline_file = _scaffold_project(runner, project_root)
        return _StarterProject(project_root=project_root, pipeline_file=pipeline_file)

    @pytest.fixture(scope="class")
    def starter_test_content(self, starter_project: _StarterProject) -> tuple[Path, str]:
        starter_test = starter_project.project_root / "tests" / "test_pipeline.py"
        assert starter_test.exists(), "scaffold must create tests/test_pipeline.py"
        return starter_test, starter_test.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def starter_test_pytest_result(
        self,
        starter_project: _StarterProject,
    ) -> subprocess.CompletedProcess[str]:
        import sys

        _materialise_referenced_data_files(
            starter_project.pipeline_file,
            starter_project.project_root,
        )
        starter_test = starter_project.project_root / "tests" / "test_pipeline.py"
        return subprocess.run(
            [sys.executable, "-m", "pytest", str(starter_test), "-q"],
            cwd=str(starter_project.project_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def test_scaffolded_starter_test_imports_pipeline(
        self,
        starter_test_content: tuple[Path, str],
    ) -> None:
        """Starter test file imports the pipeline module (not just reads bytes).

        An import forces the starter pipeline to be a valid, runnable
        module — vs ``read_text + compile``, which passes even for
        nonsense that happens to be syntactically valid Python.
        """
        starter_test, content = starter_test_content
        # Either it imports the pipeline module directly, or it runs
        # the pipeline through the haute machinery (parse or run).
        is_import_based = (
            "from rating" in content or "import rating" in content or "importlib" in content
        )
        is_run_based = (
            "parse_pipeline_file" in content
            or "execute_graph" in content
            or "pipeline.run(" in content
            or "haute run" in content
        )
        assert is_import_based or is_run_based, (
            "Scaffolded tests/test_pipeline.py is a tautology — it reads "
            "the pipeline file as bytes instead of importing or executing "
            "it. A failing pipeline would still pass this test. Starter "
            "test content:\n"
            f"{content}"
        )

    def test_scaffolded_starter_test_not_merely_string_match(
        self,
        starter_test_content: tuple[Path, str],
    ) -> None:
        """Starter test must NOT rely on substring matching ``"haute.Pipeline"``.

        A substring match on source is the pre-10A anti-pattern —
        the presence of the literal characters in the file proves
        nothing about runtime behaviour.
        """
        _, content = starter_test_content
        assert '"haute.Pipeline" in source' not in content, (
            "Scaffolded starter test still uses the pre-10A substring "
            "match — replace it with a real import or execute call."
        )

    def test_scaffolded_starter_test_runs_via_subprocess_pytest(
        self,
        starter_test_pytest_result: subprocess.CompletedProcess[str],
    ) -> None:
        """``pytest`` can actually run the scaffolded starter test.

        Wraps the whole onboarding promise in one assertion: ``haute
        init`` → ``pytest`` → green bar. We shell out in a
        subprocess (with ``sys.executable``) so the scaffolded test
        runs in a clean process with ``tmp_path`` as its rootdir,
        exactly matching what a real user's CI does.
        """
        assert starter_test_pytest_result.returncode == 0, (
            "Scaffolded starter test failed under pytest — the "
            "init → pytest onboarding promise is broken.\n"
            f"STDOUT:\n{starter_test_pytest_result.stdout}\n"
            f"STDERR:\n{starter_test_pytest_result.stderr}"
        )
