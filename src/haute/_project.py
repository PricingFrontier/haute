"""Project root discovery for Haute.

A Haute project is a directory containing ``haute.toml`` that lives inside
a git repository. :func:`get_project_root` walks upward from a starting
path to locate it; :func:`is_haute_project` is the exact-path predicate.

Both functions fail loudly: missing ``haute.toml`` or absent git setup
raises :class:`~haute.errors.ConfigError` rather than silently falling
back to the current working directory.
"""

from __future__ import annotations

from pathlib import Path

from haute.errors import ConfigError


def _has_git(path: Path) -> bool:
    """Return True if a ``.git`` entry (file or directory) exists at or above ``path``."""
    for candidate in [path, *path.parents]:
        git = candidate / ".git"
        if git.exists():
            return True
    return False


def get_project_root(start: Path | None = None) -> Path:
    """Walk upward from ``start`` to find the Haute project root.

    The project root is the first ancestor directory (inclusive of
    ``start``) that contains a ``haute.toml`` file *and* sits inside a
    git repository (a ``.git`` directory or worktree-style file at or
    above the haute.toml location).

    Parameters
    ----------
    start:
        Directory to start the search from. Defaults to ``Path.cwd()``
        when ``None`` or omitted.

    Returns
    -------
    Path
        Resolved absolute path to the project root.

    Raises
    ------
    ConfigError
        If no ``haute.toml`` is found walking upward, if the discovered
        ``haute.toml`` is not inside a git repo, or if ``start`` does
        not exist.
    PermissionError
        Propagated unwrapped when the filesystem refuses to stat an
        ancestor directory.
    """
    if start is None:
        start = Path.cwd()

    if not start.exists():
        raise ConfigError(
            "start path does not exist; cannot locate haute.toml",
            start=str(start),
        )

    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "haute.toml").exists():
            if not _has_git(candidate):
                raise ConfigError(
                    "haute.toml found but not inside a git repository; run 'git init'",
                    project=str(candidate),
                )
            return candidate

    raise ConfigError(
        "no haute.toml found walking up from start; not inside a Haute project",
        start=str(start),
    )


def is_haute_project(path: Path) -> bool:
    """Return True iff ``path`` is exactly a Haute project directory.

    A directory qualifies when it contains a ``haute.toml`` file and a
    ``.git`` entry (directory or worktree file) exists at or above it.
    No walk-up is performed for the ``haute.toml`` check — subdirectories
    of a project return ``False``.
    """
    return bool((path / "haute.toml").exists() and _has_git(path))
