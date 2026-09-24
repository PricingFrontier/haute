"""Walk the repository's source trees without entering bytecode caches.

A walk that enters a ``__pycache__`` directory can fail when another test
process creates or removes one mid-walk, so every source walk in the suite goes
through :func:`source_files`, which prunes caches before listing them.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BYTECODE_CACHE = "__pycache__"


def source_files(root: Path, *, suffix: str | None = ".py") -> list[Path]:
    """Every file under *root* ending in *suffix* (any file for ``None``), sorted."""
    files: list[Path] = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(name for name in subdirectories if name != _BYTECODE_CACHE)
        files.extend(
            Path(directory, name)
            for name in sorted(names)
            if suffix is None or name.endswith(suffix)
        )
    return files


def source_tree_snapshot() -> frozenset[str]:
    """The relative paths of every file under ``src/``, bytecode caches excluded."""
    src = REPO_ROOT / "src"
    return frozenset(path.relative_to(src).as_posix() for path in source_files(src, suffix=None))
