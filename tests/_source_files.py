"""Walk the repository's source trees without entering bytecode caches.

A walk that enters a ``__pycache__`` directory can fail when another test
process creates or removes one mid-walk, so every source walk in the suite goes
through :func:`source_files`, which prunes caches before listing them.
:class:`SourceTreeGuard` uses the same walk to fail a session that leaves files
behind under ``src/``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# Bytecode and tool caches: never source, and created or removed by other
# processes while a walk runs.
_CACHE_DIRECTORIES = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})


def source_files(root: Path, *, suffix: str | None = ".py") -> list[Path]:
    """Every file under *root* ending in *suffix* (any file for ``None``), sorted.

    Cache directories are pruned before they are listed. Any other directory
    that cannot be listed raises, so a scan never checks less than it claims.
    """
    files: list[Path] = []
    for directory, subdirectories, names in os.walk(root, onerror=_raise):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in _CACHE_DIRECTORIES
        )
        files.extend(
            Path(directory, name)
            for name in sorted(names)
            if suffix is None or name.endswith(suffix)
        )
    return files


def _raise(error: OSError) -> NoReturn:
    raise error


def tree_snapshot(root: Path) -> frozenset[str]:
    """The relative paths of every file under *root*, cache directories excluded."""
    return frozenset(path.relative_to(root).as_posix() for path in source_files(root, suffix=None))


class SourceTreeGuard:
    """Fail a pytest session that leaves files behind under a source tree.

    The controller (or a run without xdist) snapshots *root* when the session
    starts and again when it finishes, so a file that a test on any worker
    leaves under the tree turns a passing run into a failing one and is named
    in the terminal summary. Bytecode caches are ignored: importing a module
    writes them legitimately. On an xdist worker the guard does nothing; the
    controller's comparison already covers the files its tests write.
    """

    def __init__(self, root: Path, *, label: str) -> None:
        self._root = root
        self._label = label
        self._before: frozenset[str] | None = None
        self.added: list[str] = []

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        if not hasattr(session.config, "workerinput"):
            self._before = tree_snapshot(self._root)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self._before is None:
            return
        self.added = sorted(tree_snapshot(self._root) - self._before)
        if self.added and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if not self.added:
            return
        terminalreporter.section(f"files left under {self._label}/", red=True)
        for path in self.added:
            terminalreporter.line(f"{self._label}/{path}")
        terminalreporter.line(
            f"{len(self.added)} file(s) appeared under {self._label}/ during the run; tests "
            "must write under tmp_path, never into the package tree."
        )
