"""Project code a deploy bundle carries, and project imports it cannot.

The executor puts the pipeline directory and the working directory first on
``sys.path`` before it runs the preamble
(:func:`haute.executor._prioritise_preamble_import_paths`), so a module found
in either directory is project code. Deploy ships exactly one piece of it, the
project's ``utility`` package, and every other project-local import would fail
with ``ModuleNotFoundError`` in the served bundle, so validation refuses it.
"""

from __future__ import annotations

import ast
import importlib
import sys
import tokenize
from dataclasses import dataclass
from importlib.machinery import PathFinder
from pathlib import Path

from haute.errors import DeployError

UTILITY_PACKAGE = "utility"


@dataclass(frozen=True)
class ProjectModules:
    """The project modules of one resolved deploy."""

    utility: Path | None
    """The ``utility`` package directory or ``utility.py`` file to bundle, if any."""
    unbundled_imports: tuple[str, ...]
    """One validation message per project-local import the bundle does not carry."""


def resolve_project_modules(preamble: str, pipeline_dir: Path) -> ProjectModules:
    """Resolve the project modules a pipeline's preamble depends on.

    Raises:
        DeployError: ``utility`` is a namespace package spanning both project
            directories, or a bundled ``utility`` file does not parse.
    """
    # A pipeline directory that is also the working directory is one search
    # entry: listing it twice would report one namespace package as split.
    project_dirs = list(dict.fromkeys([str(pipeline_dir.resolve()), str(Path.cwd().resolve())]))
    importlib.invalidate_caches()
    utility = _project_utility(project_dirs)

    sources: list[tuple[str, str]] = [("the preamble", preamble)] if preamble.strip() else []
    if utility is not None:
        sources.extend(_utility_sources(utility))

    unbundled: list[str] = []
    for label, source in sources:
        for module, line in _absolute_import_roots(source, label=label):
            if module == UTILITY_PACKAGE or module in _NON_PROJECT_NAMES:
                continue
            if PathFinder.find_spec(module, project_dirs) is not None:
                unbundled.append(
                    f"{label} imports project module {module!r} (line {line}), which the "
                    "deploy bundle does not carry. Move it into the pipeline's utility "
                    "package or install it as a package."
                )
    return ProjectModules(utility=utility, unbundled_imports=tuple(unbundled))


_NON_PROJECT_NAMES = frozenset(sys.builtin_module_names) | frozenset(sys.stdlib_module_names)


def _project_utility(project_dirs: list[str]) -> Path | None:
    spec = PathFinder.find_spec(UTILITY_PACKAGE, project_dirs)
    if spec is None:
        return None
    locations = list(spec.submodule_search_locations or [])
    if not locations:
        assert spec.origin is not None  # a path-based module always has a file
        return Path(spec.origin).resolve()
    if len(locations) != 1:
        raise DeployError(
            "The utility package is a namespace package split across "
            f"{len(locations)} directories. Deploy bundles one utility package; add an "
            "__init__.py to the one the pipeline uses and remove the other.",
            locations=", ".join(locations),
        )
    return Path(locations[0]).resolve()


def _utility_sources(utility: Path) -> list[tuple[str, str]]:
    if utility.is_file():
        files = [(utility.name, utility)]
    else:
        files = [
            (f"{UTILITY_PACKAGE}/{path.relative_to(utility).as_posix()}", path)
            for path in sorted(utility.rglob("*.py"))
            if "__pycache__" not in path.relative_to(utility).parts
        ]
    return [(label, _read_source(path, label=label)) for label, path in files]


def _read_source(path: Path, *, label: str) -> str:
    # Decode as the import system does: a BOM or a coding declaration.
    try:
        with tokenize.open(path) as handle:
            return handle.read()
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise DeployError(
            f"{label} cannot be decoded, so deploy cannot check its imports: {exc}",
        ) from exc


def _absolute_import_roots(source: str, *, label: str) -> list[tuple[str, int]]:
    """Return each static absolute import's top-level module and line."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise DeployError(
            f"{label} does not parse, so deploy cannot check its imports: {exc}",
        ) from exc
    roots: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend((alias.name.partition(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append((node.module.partition(".")[0], node.lineno))
    return roots
