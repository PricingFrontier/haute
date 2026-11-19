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

import errno
from pathlib import Path
from typing import Any, Literal

from haute._project_settings import has_settings, read_project_settings
from haute.errors import ConfigError

PipelineFileReason = Literal["missing", "not_a_pipeline", "unreadable"]


class PipelineFileError(FileNotFoundError):
    """``haute.toml``'s ``[project].pipeline`` names a file that is not there (``missing``),
    that the system would not let Haute read (``unreadable``, with the system's *detail*), or
    that was read and holds no pipeline (``not_a_pipeline``).

    Still a ``FileNotFoundError``, so every caller that catches one keeps doing so. It carries
    the file, so each edge names it as it names files (relative to the project), and its
    message the remedy.
    """

    def __init__(
        self, path: str, *, reason: PipelineFileReason = "missing", detail: str = ""
    ) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(errno.ENOENT, self.words(path), path)

    def words(self, named: str) -> str:
        """The refusal, naming the file as *named*."""
        if self.reason == "missing":
            return (
                f"Pipeline file configured in haute.toml [project].pipeline does not exist: "
                f"{named}. Fix the path in haute.toml or create the file."
            )
        if self.reason == "unreadable":
            return (
                f"Pipeline file configured in haute.toml [project].pipeline could not be read: "
                f"{named} ({self.detail}). Close the program holding it, or check the file's "
                "permissions, and try again."
            )
        return (
            f"Pipeline file configured in haute.toml [project].pipeline does not look like a "
            f"Haute pipeline: {named}. Point [project].pipeline at a .py file containing "
            "'haute.Pipeline'."
        )

    def __str__(self) -> str:
        return str(self.strerror)

    def __reduce__(self) -> tuple[Any, ...]:
        return (
            _restore_pipeline_file_error,
            (type(self), str(self.filename), self.reason, self.detail),
        )


def _restore_pipeline_file_error(
    error_type: type[PipelineFileError],
    path: str,
    reason: PipelineFileReason,
    detail: str,
) -> PipelineFileError:
    """A :class:`PipelineFileError` as a worker sent it."""
    return error_type(path, reason=reason, detail=detail)


class NoProjectError(ConfigError):
    """No ``haute.toml`` at or above the folder a command or call starts from."""

    def __init__(self, start: Path) -> None:
        self.start = start
        super().__init__(
            f"No haute.toml in {start} or any folder above it: run this inside a Haute "
            "project, or make one with 'haute init'."
        )

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.start,))


class ProjectOutsideGitError(ConfigError):
    """A ``haute.toml`` outside a git repository: a project is a ``haute.toml`` inside one."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        super().__init__(
            f"haute.toml found in {folder}, which is not inside a git repository: run "
            "'git init' there to make it a Haute project."
        )

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.folder,))


# Files that look like Python modules but are never pipeline entry points.
# Kept in sync with :mod:`haute.discovery` so resolver and discovery don't
# drift.
_NOT_A_PIPELINE: frozenset[str] = frozenset({"__init__.py", "setup.py", "conftest.py"})


def _has_git(path: Path) -> bool:
    """Return True if a ``.git`` entry (file or directory) exists at or above ``path``."""
    for candidate in [path, *path.parents]:
        git = candidate / ".git"
        if git.exists():
            return True
    return False


def find_project_root(start: Path) -> Path | None:
    """The project holding *start*: the one finder.

    Walks up from *start*, inclusive, to the first folder holding a ``haute.toml``, and
    answers it when it is inside a git repository (a ``.git`` directory or worktree file at
    or above it); nested projects inner-win. ``None`` when no folder at or above holds one.

    Raises
    ------
    ProjectOutsideGitError
        When the ``haute.toml`` found is not inside a git repository: every caller refuses
        it, since a project is a ``haute.toml`` inside a git repository.
    ConfigError
        When *start* does not exist.
    PermissionError
        Propagated unwrapped when the filesystem refuses to stat an ancestor directory.
    """
    if not start.exists():
        raise ConfigError(
            "start path does not exist; check the path or cd to a Haute project",
            start=str(start),
        )
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if has_settings(candidate):
            if not _has_git(candidate):
                raise ProjectOutsideGitError(candidate)
            return candidate
    return None


def get_project_root(start: Path) -> Path:
    """The project holding *start* (:func:`find_project_root`), resolved and absolute.

    Raises :class:`NoProjectError` when no folder at or above *start* holds a
    ``haute.toml``, and as :func:`find_project_root` raises otherwise.
    """
    root = find_project_root(start)
    if root is None:
        raise NoProjectError(start.resolve())
    return root


def is_haute_project(path: Path) -> bool:
    """Return True iff ``path`` is exactly a Haute project directory.

    A directory qualifies when it contains a ``haute.toml`` file and a
    ``.git`` entry (directory or worktree file) exists at or above it.
    No walk-up is performed for the ``haute.toml`` check — subdirectories
    of a project return ``False``.
    """
    return has_settings(path) and _has_git(path)


def configured_pipeline_file(root: Path) -> Path | None:
    """The pipeline *root*'s ``haute.toml`` configures, checked; ``None`` when it names none.

    The path is read by the settings parse (``haute._project_settings``), which refuses one
    outside the project or ending in a space. Raises :class:`PipelineFileError` for a file
    that is not there, that the system will not let Haute read, or that was read and holds no
    ``haute.Pipeline``, so a typo in ``haute.toml`` never falls through to discovery.
    """
    settings = read_project_settings(root)
    configured = None if settings is None else settings.configured_pipeline
    if configured is None:
        return None
    if not configured.is_file():
        raise PipelineFileError(str(configured), reason="missing")
    try:
        text = configured.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PipelineFileError(
            str(configured), reason="unreadable", detail=exc.strerror or str(exc)
        ) from exc
    if not _is_python_file(configured) or "haute.Pipeline" not in text:
        raise PipelineFileError(str(configured), reason="not_a_pipeline")
    return configured.resolve()


def _is_python_file(candidate: Path) -> bool:
    """A ``.py`` file that can be a pipeline: the extension in any letter case, as Windows
    opens it, and not one of the module files that never are."""
    return candidate.suffix.lower() == ".py" and candidate.name.lower() not in _NOT_A_PIPELINE


def _discover_root_pipelines(root: Path) -> list[Path]:
    """Find root-level ``.py`` files in *root* that contain ``haute.Pipeline``.

    Mirrors the glob-plus-content-filter used by
    :func:`haute.discovery.discover_pipelines` but without the
    TOML-configured-pipeline tier — the resolver handles that tier
    itself so it can distinguish "user pointed at a missing file" from
    "nothing discoverable here".

    I/O errors on individual files are silently skipped: a single
    unreadable file must not break resolution for the rest of the
    directory.  Users who want strict behaviour should call
    :func:`haute.discovery.discover_pipelines` directly.
    """
    found: list[Path] = []
    for candidate in sorted(root.glob("*.py")):
        if _looks_like_pipeline_file(candidate):
            found.append(candidate)
    return found


def _looks_like_pipeline_file(candidate: Path) -> bool:
    """Return True when *candidate* appears to contain a Haute pipeline."""
    if not _is_python_file(candidate):
        return False
    try:
        text = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "haute.Pipeline" in text


def _resolve_default_in(root: Path) -> Path:
    """Resolve the default pipeline inside *root* through the fallback chain.

    Resolution order (first hit wins):

    1. **TOML-configured** — ``haute.toml [project].pipeline`` is the
       project's authoritative pipeline path.  A configured path that
       doesn't point at an existing file raises
       :class:`FileNotFoundError` naming the offending path, so a typo
       in ``haute.toml`` doesn't silently fall through to discovery.
    2. **main.py convention** — when ``<root>/main.py`` exists it wins
       over any auto-discovered sibling.  Projects that mix an entry
       point with helper pipelines therefore keep predictable ``None``
       resolution; the user can still pick a sibling explicitly.
    3. **Single-match auto-discovery** — exactly one root-level ``.py``
       file containing ``haute.Pipeline``, no ``main.py`` in sight →
       pick it.  A directory with just ``motor.py`` must work without
       renaming.
    4. **No candidates** — raise :class:`FileNotFoundError` enumerating
       what was checked so the user knows the three user-facing fixes
       (write a haute.toml, add ``haute.Pipeline`` to a ``.py`` file,
       or create ``main.py``).

    **Ambiguous discovery** (multiple candidates, no ``main.py``) also
    raises so the resolver never silently picks a random file.  The
    error message names every candidate so the user can pick one with
    ``--file <path>`` or rename the intended entry point to ``main.py``.
    """
    # Tier 1 — [project].pipeline wins over everything.
    configured = configured_pipeline_file(root)
    if configured is not None:
        return configured

    discovered = _discover_root_pipelines(root)
    main_py = root / "main.py"

    # Tier 2 — main.py wins over auto-discovery. Predictable behaviour
    # when a project mixes an entry-point module with helper pipelines.
    if main_py.exists():
        return main_py.resolve()

    # Tier 3 — exactly one discoverable pipeline and no main.py → pick it.
    if len(discovered) == 1:
        return discovered[0].resolve()

    # Tier 4 — ambiguous or empty. Enumerate what was found so the user
    # has an actionable next step: either pass --file explicitly or
    # rename the intended entry point to main.py.
    if len(discovered) > 1:
        names = ", ".join(p.name for p in discovered)
        raise FileNotFoundError(
            f"Multiple pipeline files found in {root}: {names}. "
            "Pass one explicitly with --file <path>, or rename the intended "
            "entry point to main.py."
        )

    raise FileNotFoundError(
        f"No pipeline file found in {root}. "
        "Expected one of: a haute.toml with [project].pipeline set, a "
        f".py file containing 'haute.Pipeline', or a {main_py.name}. "
        "Pass a path explicitly or run 'haute init' to scaffold a project."
    )


def resolve_pipeline_file(path: Path) -> Path:
    """Resolve a user-supplied pipeline path to a concrete absolute file.

    The canonical pipeline-file resolver — every CLI command and every
    programmatic caller funnels through this function so resolution
    drift is impossible.

    Resolution rules:

    * A **directory** → run the fallback chain (TOML-configured →
      ``main.py`` → single auto-discovery → raise) scoped to that
      directory; see :func:`_resolve_default_in` for the full tier
      ordering. The CLI passes the shell's working directory when no path
      is given, so ``haute run ./rating`` is symmetric with
      ``cd rating && haute run``.
    * An **existing file** → resolved to its absolute path.  No
      discovery or TOML lookup — the user pointed at exactly this file.
    * A **non-existent file path** → :class:`FileNotFoundError` naming
      the missing path so the user can fix it.

    Relative paths are resolved against :func:`pathlib.Path.cwd` via
    :meth:`pathlib.Path.resolve`.

    Parameters
    ----------
    path:
        The user-supplied path: a pipeline file or a folder to resolve one in.

    Returns
    -------
    Path
        Absolute path to an existing ``.py`` pipeline file.

    Raises
    ------
    FileNotFoundError
        When *path* does not exist, or when the default chain terminates
        without finding a candidate.  The message always enumerates what
        was tried so users can diagnose and fix their invocation.
    """
    if path.is_dir():
        return _resolve_default_in(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Pipeline file not found: {path}. Check the path or pass a different file."
        )

    return path.resolve()
