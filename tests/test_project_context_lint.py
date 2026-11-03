"""Ratchets for the one project context (``specs/pipeline-config/low-level.md``, Testing).

A test installs its project (``tests/_project.py``); it never changes directory to set
one. Production code reads the project from ``current_project()``; it never reads the
working directory to find one. Each list below names the only places allowed, with why.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from tests._source_files import REPO_ROOT, source_files

#: ``chdir`` calls a test may make, by file and enclosing scope: none sets a project.
ALLOWED_TEST_CHDIRS: dict[tuple[str, str], int] = {
    # The one way a test sets its project: it installs the context and runs in the
    # project's folder, as `haute serve` runs in its project's root for the user's code.
    ("tests/_project.py", "use_project"): 1,
    # A test proving Haute reads the installed project, never the working directory.
    ("tests/_project.py", "work_elsewhere"): 1,
    # The harness: a strict test runs in its project (and its writes stay in the sandbox),
    # and every test ends in the directory it started in.
    ("tests/conftest.py", "_haute_write_sandbox"): 1,
    ("tests/conftest.py", "_installed_project"): 1,
    # The interpreter multiprocessing exec's is a path relative to the process's own
    # working directory: the test moves it to prove the path is pinned first.
    (
        "tests/test_worker_isolation.py",
        "TestSpawnableInterpreter.test_a_relative_interpreter_is_resolved_against_the_launch_directory",
    ): 2,
    (
        "tests/test_worker_isolation.py",
        "TestSpawnableInterpreter.test_a_relative_interpreter_after_the_chdir_falls_back_to_the_kernel",
    ): 1,
    # The Databricks bootstrap changes directory itself; monkeypatch restores it.
    (
        "tests/test_hosted.py",
        "TestEnsureProject.test_unbound_boot_seeds_a_volatile_project_and_chdirs_into_it",
    ): 1,
    ("tests/test_hosted.py", "TestEnsureProject.test_a_restored_or_present_boot_never_reseeds"): 1,
    (
        "tests/test_hosted.py",
        "TestEnsureProject.test_a_failed_restore_gates_the_boot_without_seeding",
    ): 1,
}

#: Reads of the working directory production code may make, by module and scope.
ALLOWED_SOURCE_CWD_READS: dict[tuple[str, str], str] = {
    ("src/haute/_project_context.py", "standalone_project_scope"): (
        "a pipeline with no defining file is run from an interactive session, the entry point"
    ),
    ("src/haute/_workbench_app.py", "_check_output"): (
        "a release build never replaces the working directory itself"
    ),
    ("src/haute/cli/__init__.py", "cli"): (
        "the CLI is an entry point: a command works in the shell's working directory"
    ),
    ("src/haute/cli/_polars_startup.py", ""): (
        "Polars' spill folder is fixed when Polars is imported, before any command runs"
    ),
}


def _scopes(tree: ast.AST) -> dict[ast.AST, str]:
    """Each node's enclosing class/function qualname."""
    owner: dict[ast.AST, str] = {}

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f"{scope}.{child.name}" if scope else child.name
            owner[child] = inner
            visit(child, inner)

    visit(tree, "")
    return owner


def _calls(path: Path, match: object) -> Counter[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    owner = _scopes(tree)
    found: Counter[tuple[str, str]] = Counter()
    relative = path.relative_to(REPO_ROOT).as_posix()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and match(node.func):  # type: ignore[operator]
            found[(relative, owner.get(node, ""))] += 1
    return found


def _is_chdir(func: ast.expr) -> bool:
    return isinstance(func, ast.Attribute) and func.attr == "chdir"


def _is_cwd_read(func: ast.expr) -> bool:
    return isinstance(func, ast.Attribute) and (
        (func.attr == "cwd" and isinstance(func.value, ast.Name) and func.value.id.endswith("Path"))
        or (func.attr == "getcwd" and isinstance(func.value, ast.Name) and func.value.id == "os")
    )


def test_no_test_changes_directory_to_set_a_project() -> None:
    found: Counter[tuple[str, str]] = Counter()
    for path in source_files(REPO_ROOT / "tests"):
        found += _calls(path, _is_chdir)
    assert dict(found) == ALLOWED_TEST_CHDIRS, (
        "A test installs its project with tests._project.use_project (or the project "
        "fixture); it does not change directory. Update ALLOWED_TEST_CHDIRS only for a "
        "test of code that itself reads or changes the working directory."
    )


def test_production_code_never_reads_the_working_directory_for_a_project() -> None:
    found: Counter[tuple[str, str]] = Counter()
    for path in source_files(REPO_ROOT / "src" / "haute"):
        found += _calls(path, _is_cwd_read)
    assert set(found) == set(ALLOWED_SOURCE_CWD_READS), (
        "Read the project from haute._project_context.current_project(), never from the "
        "working directory."
    )
