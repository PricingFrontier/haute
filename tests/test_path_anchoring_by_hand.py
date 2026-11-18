"""No code outside the anchoring module decides which folder a relative path is read from.

``haute._path_resolution.resolve_node_input_path`` is the one rule for a relative path in
a node's settings: the project root's file first, then the one in the pipeline folder.
Deploy used to keep a second rule beside it, the pipeline folder first, by passing a
preference to the shared resolver, so with a file under both the bundle shipped one copy
and the preview read the other (specs/deploy, "One anchoring rule for every deploy
reader"). The resolver takes no preference now. This scan finds, in ``src/haute`` outside
that module, a function that:

- joins a configured path (anything but a literal) onto the pipeline directory, as in
  ``pipeline_dir / raw``, ``base_dir.joinpath(raw)`` or ``Path(source_file).parent / raw``,
  which a second rule needs whichever order it tries the folders in;
- passes a ``prefer`` to a call, the shape the old deploy rule took;
- imports a private resolver of ``haute._path_resolution`` (any private name but its
  separator and device-name checks).

A join onto the project root alone is not reported: it names the file the rule names
whenever that file exists and fails loud when only the pipeline folder holds it, so it
cannot read another copy in silence. A place that anchors on the pipeline directory by
design, such as a node's config sidecar or a SQLite URI, is on :data:`_ANCHORED_BY_HAND`
with its reason; the list may only shrink.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._source_files import REPO_ROOT, source_files

pytestmark = pytest.mark.meta

_SOURCE_ROOT = REPO_ROOT / "src" / "haute"
#: The one module that anchors a relative path.
_ANCHORING_MODULE = "src/haute/_path_resolution.py"
#: Its private names that only check a path's spelling, and decide no folder.
_SPELLING_CHECKS = frozenset({"_normalise_path_text", "_reject_reserved_device_components"})

#: Names a function gives the pipeline directory and the project root.
_PIPELINE_NAMES = frozenset(
    {"pipeline_dir", "pipeline_root", "pipeline_folder", "base_dir", "base", "pdir", "source_dir"}
)
_PROJECT_NAMES = frozenset({"root", "project_root", "project_dir"})
#: A ``.parent`` of one of these is the folder a pipeline file sits in.
_PIPELINE_FILE_WORDS = ("source_file", "pipeline_file", "root_path")

_SIDECAR = (
    "a node's config sidecar (config/<type>/<name>.json), which lives beside the pipeline "
    "file that names it (specs/pipeline-config)"
)

#: ``path::scope`` -> why the place anchors a relative path on the pipeline directory.
#: The scope is the enclosing function, ``Class.method`` for a method.
_ANCHORED_BY_HAND: dict[str, str] = {
    "src/haute/_config_io.py::load_node_config": _SIDECAR,
    "src/haute/_model_scorer.py::score_from_config": _SIDECAR,
    "src/haute/_pipeline_repair.py::_resolve_config_reference": _SIDECAR,
    "src/haute/_recovery_sources.py::read_raw_node_settings": _SIDECAR,
    "src/haute/assistant/_application.py::_prove_steps_survive_save": (
        _SIDECAR + ", written to a scratch folder to prove the steps survive a save"
    ),
    "src/haute/routes/_save_pipeline.py::SavePipelineService._write_config_files": (
        _SIDECAR + ", written by a save"
    ),
    "src/haute/routes/_save_pipeline.py::SavePipelineService._remove_stale_config_files": (
        _SIDECAR + ", removed by a save when its node goes"
    ),
    "src/haute/_parser_submodels.py::_validate_pipeline_dir": (
        "a submodel module's own file, checked against the pipeline that registers it"
    ),
    "src/haute/_database_io.py::resolve_sqlite_path": (
        "a SQLite URI, relative to the pipeline directory by SQLAlchemy's convention; the "
        "preview and deploy read it through this one function (specs/io-layer)"
    ),
    "src/haute/executor.py::_contain_output_path": (
        "a Data Output's write target: a run writes it and a deployment drops the node; a "
        "file target arrives here already anchored on the project root"
    ),
    "src/haute/_sandbox.py::contained_path": (
        "the one containment check, joining a path onto whatever folder its caller names"
    ),
}


@dataclass(frozen=True)
class Finding:
    scope: str
    line: int
    text: str


def _anchor(expr: ast.expr) -> str | None:
    """``"pipeline"`` or ``"project"`` for an expression naming that folder, else ``None``."""
    if isinstance(expr, ast.Name):
        name = expr.id.lstrip("_")
    elif isinstance(expr, ast.Attribute):
        if expr.attr == "parent":
            owner = ast.unparse(expr.value)
            return "pipeline" if any(word in owner for word in _PIPELINE_FILE_WORDS) else None
        name = expr.attr.lstrip("_")
    elif isinstance(expr, ast.Call):
        func = expr.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if called == "graph_pipeline_dir":
            return "pipeline"
        if called in {"Path", "PurePath"} and len(expr.args) == 1:
            return _anchor(expr.args[0])
        if called in {"resolve", "absolute"} and isinstance(func, ast.Attribute):
            return _anchor(func.value)
        return None
    elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div) and _literal(expr.right):
        return _anchor(expr.left)
    else:
        return None
    if name in _PIPELINE_NAMES:
        return "pipeline"
    return "project" if name in _PROJECT_NAMES else None


def _literal(expr: ast.expr) -> bool:
    """Whether *expr* is a path the code spells itself: a string, or a constant's name."""
    if isinstance(expr, ast.Constant | ast.JoinedStr):
        return True
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return _literal(expr.left) and _literal(expr.right)
    if isinstance(expr, ast.Name):
        return expr.id.lstrip("_").isupper()
    if isinstance(expr, ast.Attribute):
        return expr.attr.lstrip("_").isupper()
    return False


def _joins(body: list[ast.AST]) -> Iterator[tuple[ast.expr, ast.expr, ast.AST]]:
    """Each path join in *body*: ``base / part``, ``base.joinpath(part)``, ``os.path.join``."""
    for node in body:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            yield node.left, node.right, node
        elif isinstance(node, ast.Call) and len(node.args) >= 1:
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "joinpath":
                yield func.value, node.args[0], node
            elif len(node.args) >= 2 and (
                (isinstance(func, ast.Attribute) and ast.unparse(func) == "os.path.join")
                or (isinstance(func, ast.Name) and func.id in {"Path", "PurePath"})
            ):
                yield node.args[0], node.args[1], node


def _own_nodes(function: ast.AST) -> list[ast.AST]:
    """The nodes of *function*'s body, not descending into a nested function or class."""
    found: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        found.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return found


def _function_findings(scope: str, function: ast.AST) -> list[Finding]:
    body = _own_nodes(function)
    joins = [
        Finding(scope, getattr(join, "lineno", 0), ast.unparse(join))
        for base, part, join in _joins(body)
        if _anchor(base) == "pipeline" and not _literal(part)
    ]
    preferences = [
        Finding(scope, node.lineno, ast.unparse(node))
        for node in body
        if isinstance(node, ast.Call) and any(keyword.arg == "prefer" for keyword in node.keywords)
    ]
    return joins + preferences


def _scopes(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """Every function and method in *tree*, with its qualified name."""
    pending: list[tuple[str, ast.AST]] = [("", tree)]
    while pending:
        prefix, node = pending.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                yield f"{prefix}{child.name}", child
                pending.append((f"{prefix}{child.name}.", child))
            elif isinstance(child, ast.ClassDef):
                pending.append((f"{prefix}{child.name}.", child))
            else:
                pending.append((prefix, child))


def anchoring_by_hand(source: str) -> list[Finding]:
    """Each place in *source* that anchors a relative path itself."""
    tree = ast.parse(source)
    findings = [
        Finding("<import>", node.lineno, f"from haute._path_resolution import {alias.name}")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "haute._path_resolution"
        for alias in node.names
        if alias.name.startswith("_") and alias.name not in _SPELLING_CHECKS
    ]
    for scope, function in _scopes(tree):
        findings.extend(_function_findings(scope, function))
    return findings


def _repository_findings() -> dict[str, list[Finding]]:
    found: dict[str, list[Finding]] = {}
    for path in source_files(_SOURCE_ROOT):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == _ANCHORING_MODULE:
            continue
        for finding in anchoring_by_hand(path.read_text(encoding="utf-8")):
            found.setdefault(f"{relative}::{finding.scope}", []).append(finding)
    return found


def test_no_source_outside_the_anchoring_module_anchors_a_path_on_the_pipeline_dir() -> None:
    found = _repository_findings()
    unexplained = {
        key: [f"line {finding.line}: {finding.text}" for finding in findings]
        for key, findings in found.items()
        if key not in _ANCHORED_BY_HAND
    }
    assert not unexplained, (
        "Resolve a node's relative path with haute._path_resolution.resolve_node_input_path, "
        "the one anchoring rule, not by joining it onto the pipeline directory. A place that "
        "must anchor on the pipeline directory by design goes on _ANCHORED_BY_HAND with its "
        f"reason: {unexplained}"
    )


def test_every_listed_place_still_anchors_a_path() -> None:
    stale = sorted(set(_ANCHORED_BY_HAND) - set(_repository_findings()))
    assert not stale, f"_ANCHORED_BY_HAND entries that no longer anchor a path: {stale}"


@pytest.mark.parametrize(
    "source",
    [
        "def read(raw):\n    return pipeline_dir / raw",
        "def read(raw):\n    return root / raw if (root / raw).exists() else pipeline_dir / raw",
        "def read(raw):\n    a = root / raw\n    b = self.pipeline_root / raw\n    return a, b",
        "def read(config):\n    return Path(graph.source_file).parent / config['path']",
        "def read(raw):\n    return os.path.join(base_dir, raw)",
        "def read(raw):\n    return self._pipeline_root.joinpath(raw)",
        "def read(raw):\n    return pipeline_dir / 'files' / raw",
        "def read(raw):\n    return Path(base_dir).resolve() / raw",
        "def read(graph, raw):\n    return graph_pipeline_dir(graph) / raw",
        "class Reader:\n    def read(self, raw):\n        return self.pipeline_dir / raw",
        "def read(raw):\n    return resolve_node_input_path(raw, prefer='pipeline')",
        "from haute._path_resolution import _anchored_candidates",
    ],
)
def test_the_scan_finds_a_path_anchored_by_hand(source: str) -> None:
    assert anchoring_by_hand(source), source


def test_the_scan_finds_a_path_tried_against_both_folders() -> None:
    """The shape the old deploy rule took when written out: one path, two folders."""
    source = (
        "def read(raw):\n"
        "    first = project_root / raw\n"
        "    return first if first.exists() else Path(graph.source_file).parent / raw\n"
    )

    [finding] = anchoring_by_hand(source)

    assert finding.scope == "read"


@pytest.mark.parametrize(
    "source",
    [
        "def read(raw):\n    return root / raw",
        "def read():\n    return pipeline_dir / 'config' / 'rates.json'",
        "def read(name):\n    return pipeline_dir / f'{name}.json'",
        "def read():\n    return pipeline_dir / CONFIG_FOLDER",
        "def read(raw):\n    return resolve_node_input_path(raw, pipeline_dir=pipeline_dir)",
        "def ratio(total, count):\n    return total / count",
        "from haute._path_resolution import _normalise_path_text, resolve_node_input_path",
    ],
)
def test_the_scan_leaves_the_rule_the_root_and_literal_names(source: str) -> None:
    assert not anchoring_by_hand(source), source


def test_the_anchoring_module_exists_where_the_scan_exempts_it() -> None:
    module = REPO_ROOT / _ANCHORING_MODULE
    assert module.is_file()
    assert "def resolve_node_input_path(" in module.read_text(encoding="utf-8")
    assert Path(_ANCHORING_MODULE).name == "_path_resolution.py"
