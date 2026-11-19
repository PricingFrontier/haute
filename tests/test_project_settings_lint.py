"""Ratchets for the one parse of ``haute.toml`` (pipeline-config low-level, Testing).

``haute._project_settings`` is the only module that names the file as a path, and the only
one that parses TOML, apart from the readers of other TOML files listed below. Any other
module takes its table from ``read_project_settings``; a module that opens ``haute.toml``
itself fails here. Each list names the only places allowed, with why.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._source_files import REPO_ROOT, source_files

SOURCE_ROOT = REPO_ROOT / "src" / "haute"
PARSE_MODULE = "src/haute/_project_settings.py"

#: Modules that parse a TOML file other than ``haute.toml``, each with the file it reads.
OTHER_TOML_READERS: dict[str, str] = {
    "src/haute/_project_python.py": "pyproject.toml, for the project's Python",
    "src/haute/_connections.py": "connections.toml, the team's connections",
    "src/haute/cli/_init_cmd.py": "pyproject.toml, which haute init adds Haute to",
}

#: Modules that take ``haute.toml``'s path from ``settings_path`` without reading it as
#: settings, each with what it does with the path.
SETTINGS_PATH_USERS: dict[str, str] = {
    "src/haute/_workbench_app.py": "refuses a release folder holding a project's haute.toml",
    "src/haute/assistant/_ops.py": "the file's bytes in a snapshot's content identity",
    "src/haute/assistant/_project_knowledge.py": "the file's digest as a project fact",
    "src/haute/deploy/_config.py": "names the missing file in a refusal",
}

_TOML_MODULES = {"tomllib", "tomlkit", "toml", "tomli"}


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _modules() -> list[tuple[str, ast.Module]]:
    return [
        (_relative(path), ast.parse(path.read_text(encoding="utf-8")))
        for path in source_files(SOURCE_ROOT)
    ]


def _names_the_file(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "haute.toml"


def _settings_file_references(tree: ast.Module) -> list[int]:
    """Lines naming the parse's ``SETTINGS_FILE``, imported or as an attribute: a module
    that names it builds the path the parse alone opens."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "SETTINGS_FILE":
            lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "SETTINGS_FILE":
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and any(
            alias.name == "SETTINGS_FILE" for alias in node.names
        ):
            lines.append(node.lineno)
    return lines


def _path_uses_of_the_file(tree: ast.Module) -> list[int]:
    """Lines building a path to ``haute.toml``: ``x / "haute.toml"``, ``Path("haute.toml")``,
    ``x.joinpath("haute.toml")``, ``open("haute.toml")``."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if _names_the_file(node.right) or _names_the_file(node.left):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and any(_names_the_file(arg) for arg in node.args):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"Path", "PurePath", "PurePosixPath", "joinpath", "open", "with_name"}:
                lines.append(node.lineno)
    return lines


def _toml_imports(tree: ast.Module) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in _TOML_MODULES for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.split(".")[0] in _TOML_MODULES:
                lines.append(node.lineno)
    return lines


def _calls(tree: ast.Module, name: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == name or getattr(node.func, "attr", None) == name)
    ]


def test_only_the_parse_names_the_settings_file_as_a_path() -> None:
    offenders = [
        f"{module}:{line}"
        for module, tree in _modules()
        if module != PARSE_MODULE
        for line in [*_path_uses_of_the_file(tree), *_settings_file_references(tree)]
    ]
    assert offenders == [], (
        "Only haute._project_settings opens haute.toml: take the table from "
        f"read_project_settings. Found: {offenders}"
    )


def test_only_the_parse_parses_toml_apart_from_other_toml_files() -> None:
    offenders = [
        f"{module}:{line}"
        for module, tree in _modules()
        if module != PARSE_MODULE and module not in OTHER_TOML_READERS
        for line in _toml_imports(tree)
    ]
    assert offenders == [], (
        "A module that parses TOML reads haute.toml beside the one parse, or another TOML "
        f"file that needs its row in OTHER_TOML_READERS. Found: {offenders}"
    )


def test_the_settings_path_is_taken_only_where_listed() -> None:
    users = {
        module
        for module, tree in _modules()
        if module != PARSE_MODULE and _calls(tree, "settings_path")
    }
    assert users == set(SETTINGS_PATH_USERS), (
        "settings_path names haute.toml for a use that is not reading its settings; "
        "a new one needs its row and reason, and a reader takes read_project_settings."
    )


def test_every_listed_module_still_exists() -> None:
    for module in [*OTHER_TOML_READERS, *SETTINGS_PATH_USERS]:
        assert (REPO_ROOT / module).is_file(), module


def test_the_ratchet_catches_each_way_of_opening_the_file() -> None:
    for source in (
        'root / "haute.toml"',
        'Path("haute.toml")',
        'root.joinpath("haute.toml")',
        'open("haute.toml")',
    ):
        assert _path_uses_of_the_file(ast.parse(source)), source
    for bypass in (
        "tomllib.load((root / SETTINGS_FILE).open('rb'))",
        "from haute._project_settings import SETTINGS_FILE",
        "root / _project_settings.SETTINGS_FILE",
    ):
        assert _settings_file_references(ast.parse(bypass)), bypass
    assert _calls(ast.parse("settings_path(root).read_text()"), "settings_path")
    assert _toml_imports(ast.parse("import tomllib"))
    assert _toml_imports(ast.parse("from tomlkit import parse"))
    assert not _path_uses_of_the_file(ast.parse('name == "haute.toml"'))
