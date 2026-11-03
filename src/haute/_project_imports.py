"""The project's ``utility`` package, imported without editing ``sys.path``.

A preamble imports the project's ``utility`` package by name, and so can a helper it
defines when Polars calls that helper while it collects, long after the preamble ran.
A meta-path finder answers ``utility`` and every ``utility.*`` name from the project the
executor last compiled a preamble for, so either import resolves to that project
whenever it runs, and ``sys.path`` is never edited: Haute puts no project directory
on the import path, so a file beside the pipeline cannot shadow an installed package
and no project module but ``utility`` is imported through Haute, as only it is in a
deploy bundle. (A directory the interpreter itself puts on the path, such as the
working directory under ``python -m``, imports as in any Python process.)

The finder's loader compiles every ``utility`` module from its source and never reads
or writes bytecode: a timestamp-validated ``.pyc`` can hide an edit that keeps the
file's size and mtime. A project without a ``utility`` leaves the name to the normal
import system, which is where a deploy bundle's own copy is found (an MLflow model's
code path, a container's working directory).
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec, PathFinder, SourceFileLoader
from pathlib import Path
from types import CodeType, ModuleType

from haute._project_context import current_project

UTILITY_PACKAGE = "utility"


def utility_search_dirs(
    pipeline_dir: str | Path | None, *, project_root: str | Path | None = None
) -> list[str]:
    """Where a project's ``utility`` is found: beside the pipeline, then the project root.

    The project root is the current project's unless *project_root* names one. One
    entry when they are the same directory. The cache fingerprint and the deploy
    bundle read ``utility`` from these directories too, so each hashes or ships the
    files the finder loads.
    """
    dirs: list[str] = []
    if pipeline_dir is not None:
        dirs.append(str(Path(pipeline_dir).resolve()))
    root = current_project().root if project_root is None else Path(project_root).resolve()
    dirs.append(str(root))
    return list(dict.fromkeys(dirs))


def find_utility_spec(pipeline_dir: str | Path | None) -> ModuleSpec | None:
    """The ``utility`` an import loads, resolved without importing it.

    The finder's search of the project's directories first, then, for a project without
    a ``utility``, the rest of the import path, as the import system falls through. A
    project ``utility`` that is a namespace package (no ``__init__.py``) therefore wins
    over a regular package of that name elsewhere on the path, as it does when imported.
    """
    spec = PathFinder.find_spec(UTILITY_PACKAGE, utility_search_dirs(pipeline_dir))
    if spec is not None:
        return spec
    return PathFinder.find_spec(UTILITY_PACKAGE, sys.path)


class _SourceOnlyLoader(SourceFileLoader):
    """Loads a ``utility`` module from its source every time, never from bytecode."""

    def get_code(self, fullname: str) -> CodeType:
        path = self.get_filename(fullname)
        return self.source_to_code(self.get_data(path), path)


class _ProjectUtilityFinder(MetaPathFinder):
    """Answers ``utility`` and ``utility.*`` from the bound project's directories."""

    def __init__(self) -> None:
        self.search_dirs: tuple[str, ...] = ()

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        del target
        if fullname != UTILITY_PACKAGE and not fullname.startswith(f"{UTILITY_PACKAGE}."):
            return None
        # A submodule is looked up in its package's own path, as the import system does.
        search = self.search_dirs if fullname == UTILITY_PACKAGE else path
        if not search:
            return None
        spec = PathFinder.find_spec(fullname, list(search))
        if spec is None:
            return None
        if isinstance(spec.loader, SourceFileLoader) and spec.origin is not None:
            spec.loader = _SourceOnlyLoader(fullname, spec.origin)
            spec.cached = None
        return spec


_FINDER = _ProjectUtilityFinder()


def bind_project_utility(pipeline_dir: str | Path | None, *, project_root: str | Path) -> bool:
    """Bind the ``utility`` finder to a project; return whether the binding changed.

    The finder is installed ahead of every other finder on first use. The caller holds
    the executor's preamble lock and evicts ``utility`` (:func:`evict_utility_modules`)
    when the binding changed, so the next import reads the newly bound project.
    """
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)
    dirs = tuple(utility_search_dirs(pipeline_dir, project_root=project_root))
    changed = dirs != _FINDER.search_dirs
    _FINDER.search_dirs = dirs
    return changed


def utility_bound_to(pipeline_dir: str | Path | None, *, project_root: str | Path) -> bool:
    """Whether the finder is bound to these project directories now."""
    return _FINDER in sys.meta_path and _FINDER.search_dirs == tuple(
        utility_search_dirs(pipeline_dir, project_root=project_root)
    )


def evict_utility_modules() -> None:
    """Forget every imported ``utility`` module, so the next import loads the source again."""
    importlib.invalidate_caches()
    for name in [
        module
        for module in sys.modules
        if module == UTILITY_PACKAGE or module.startswith(f"{UTILITY_PACKAGE}.")
    ]:
        del sys.modules[name]
