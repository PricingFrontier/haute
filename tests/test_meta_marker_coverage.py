"""Every test that scans the repository carries the ``meta`` marker.

A test that walks the repository's own files, or runs the full-corpus debt and
write-sandbox scans, reads files rather than exercising behaviour: its result
cannot differ by interpreter, and when it fails it fails identically on every
compatibility shard. The ``meta`` marker keeps such tests out of the
compatibility lanes (``-m "not perf and not meta"`` in .github/workflows/ci.yml);
this module holds every one to carrying it.

A scan is a call of the shared repository walker ``source_files`` or of a
corpus scanner, made by the test or by a helper in its module. A test that
builds the tree it walks (``tmp_path``, ``pytester``) exercises the walker
instead and is not a scan, and ``BEHAVIOURAL_WALKS`` names, with the reason,
each helper or test whose walk is part of the behaviour under test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._source_files import REPO_ROOT, source_files

pytestmark = pytest.mark.meta

CORPUS_SCANS = frozenset(
    {
        "source_files",
        "_scan_debt_sites",
        "_scan_frontend_debt_sites",
        "_test_health_summary",
        "scan_tests",
    }
)
_OWN_TREE_FIXTURES = frozenset({"tmp_path", "tmp_path_factory", "haute_scratch", "pytester"})

#: ``path::function`` (a helper, or a test as pytest names it) whose walk belongs
#: to the behaviour under test, with the reason. An entry no longer needed fails.
BEHAVIOURAL_WALKS = {
    "tests/test_workbench_folder.py::on_disk": (
        "lists the files a save left in the test's own form folder"
    ),
    "tests/test_assistant_replay.py::test_the_fixture_projects_are_not_modified_by_replay": (
        "checks what the replays of the same run left in the checked-in projects, which "
        "each interpreter's run must keep clean too"
    ),
}


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        if isinstance(call.func, ast.Name):
            names.add(call.func.id)
        elif isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
    return names


def _is_meta_mark(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(target, ast.Attribute) and target.attr == "meta"


def _module_is_meta(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            values = (
                node.value.elts if isinstance(node.value, ast.List | ast.Tuple) else [node.value]
            )
            if any(_is_meta_mark(value) for value in values):
                return True
    return False


def _scanning_functions(tree: ast.Module, exempt: frozenset[str]) -> set[str]:
    """Names of the module's functions that scan, directly or through each other."""
    functions = {
        node.name: _called_names(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name not in exempt
    }
    scanning = {name for name, calls in functions.items() if calls & CORPUS_SCANS}
    while True:
        grown = {name for name, calls in functions.items() if calls & scanning} | scanning
        if grown == scanning:
            return scanning
        scanning = grown


def unmarked_scans(path: Path, source: str, exempt: frozenset[str] = frozenset()) -> list[str]:
    """``path::test`` for each test in *source* that scans without the marker.

    *exempt* holds the module's helpers and tests (as ``path::name``) that
    ``BEHAVIOURAL_WALKS`` excuses.
    """
    tree = ast.parse(source)
    if _module_is_meta(tree):
        return []
    prefix_of_path = f"{path.as_posix()}::"
    exempt_names = frozenset(
        name.removeprefix(prefix_of_path) for name in exempt if name.startswith(prefix_of_path)
    )
    scanning = _scanning_functions(tree, exempt_names) | CORPUS_SCANS
    found: list[str] = []

    def visit(body: list[ast.stmt], prefix: str, class_meta: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                meta = class_meta or any(_is_meta_mark(d) for d in node.decorator_list)
                visit(node.body, f"{prefix}{node.name}::", meta)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test"
            ):
                parameters = {argument.arg for argument in node.args.args}
                if parameters & _OWN_TREE_FIXTURES or not _called_names(node) & scanning:
                    continue
                if class_meta or any(_is_meta_mark(d) for d in node.decorator_list):
                    continue
                if f"{prefix}{node.name}" in exempt_names:
                    continue
                found.append(f"{path.as_posix()}::{prefix}{node.name}")

    visit(tree.body, "", False)
    return found


def test_every_repository_scan_carries_the_meta_marker() -> None:
    exempt = frozenset(BEHAVIOURAL_WALKS)
    unmarked: list[str] = []
    needed: set[str] = set()
    for path in source_files(REPO_ROOT / "tests"):
        if not path.name.startswith("test_"):
            continue
        relative = path.relative_to(REPO_ROOT)
        source = path.read_text(encoding="utf-8")
        found = unmarked_scans(relative, source, exempt)
        unmarked.extend(found)
        for entry in exempt:
            # An entry is needed while dropping it would flag a test.
            if entry.startswith(f"{relative.as_posix()}::") and (
                unmarked_scans(relative, source, exempt - {entry}) != found
            ):
                needed.add(entry)

    assert unmarked == [], (
        "These tests scan the repository's files; mark them @pytest.mark.meta so the "
        "compatibility lanes leave them to the coverage lane:\n" + "\n".join(unmarked)
    )
    assert needed == exempt, (
        f"BEHAVIOURAL_WALKS entries that excuse nothing; remove them: {sorted(exempt - needed)}"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from tests._source_files import source_files\n"
            "def test_walks():\n    assert source_files(ROOT)\n",
            ["tests/test_probe.py::test_walks"],
        ),
        (
            "def _modules():\n    return source_files(SRC)\n"
            "class TestShape:\n    def test_through_a_helper(self):\n        _modules()\n",
            ["tests/test_probe.py::TestShape::test_through_a_helper"],
        ),
        (
            "import pytest\n@pytest.mark.meta\ndef test_marked():\n    _scan_debt_sites()\n",
            [],
        ),
        (
            "import pytest\npytestmark = [pytest.mark.meta]\ndef test_module_marked():\n"
            "    scan_tests()\n",
            [],
        ),
        ("def test_own_tree(tmp_path):\n    source_files(tmp_path)\n", []),
        ("def test_behaviour():\n    assert 1 + 1 == 2\n", []),
    ],
    ids=["direct", "through-helper", "marked", "module-marked", "own-tree", "no-scan"],
)
def test_a_scan_is_found_directly_or_through_a_helper(source: str, expected: list[str]) -> None:
    assert unmarked_scans(Path("tests/test_probe.py"), source) == expected
