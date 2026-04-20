"""Project root and pipeline file discovery for Haute.

A Haute project is a directory containing ``haute.toml`` that lives inside
a git repository. :func:`get_project_root` walks upward from a starting
path to locate it; :func:`is_haute_project` is the exact-path predicate.

:func:`resolve_pipeline_file` is the canonical pipeline-file resolver used
by every CLI command and any programmatic caller — it maps a user-facing
path (``None``, a directory, or a file) to the absolute path of a concrete
``.py`` pipeline file.

All functions fail loudly: missing ``haute.toml``, absent git setup, or a
non-existent pipeline file raise an explicit exception rather than
silently falling back to a default.
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
            "start path does not exist; check the path or cd to a Haute project",
            start=str(start),
        )

    current = start.resolve()
    # First haute.toml walking up wins — nested projects inner-win.
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


def resolve_pipeline_file(path: Path | None) -> Path:
    """Resolve a user-supplied pipeline path to a concrete absolute file.

    The canonical pipeline-file resolver — every CLI command and every
    programmatic caller funnels through this function so resolution drift
    is impossible.

    Resolution rules:

    * ``None`` → ``<cwd>/main.py`` (the project-wide default).
    * A directory → ``<dir>/main.py`` inside that directory.
    * An existing file → resolved to its absolute path.
    * A non-existent path → :class:`FileNotFoundError` naming the missing
      path so the user can fix it.

    Relative paths are resolved against :func:`pathlib.Path.cwd` via
    :meth:`pathlib.Path.resolve`.

    Parameters
    ----------
    path:
        The user-supplied path, or ``None`` to use the project default.

    Returns
    -------
    Path
        Absolute path to an existing ``.py`` pipeline file.

    Raises
    ------
    FileNotFoundError
        When *path* (or the resolved ``main.py`` inside a directory, or the
        default ``main.py`` when *path* is ``None``) does not exist.  The
        message always names the missing path so users can diagnose and
        fix their invocation.
    """
    if path is None:
        candidate = Path.cwd() / "main.py"
        if not candidate.exists():
            raise FileNotFoundError(
                f"Pipeline file not found: {candidate}. "
                "Pass a path explicitly, cd to a directory with main.py, "
                "or run 'haute init' to scaffold a project."
            )
        return candidate.resolve()

    if path.is_dir():
        candidate = path / "main.py"
        if not candidate.exists():
            raise FileNotFoundError(
                f"No main.py found in directory: {path}. "
                "Pass the pipeline file directly or add a main.py to the directory."
            )
        return candidate.resolve()

    if not path.exists():
        raise FileNotFoundError(
            f"Pipeline file not found: {path}. "
            "Check the path or pass a different file."
        )

    return path.resolve()
