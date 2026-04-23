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

import tomllib
from pathlib import Path

from haute.errors import ConfigError

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


def _toml_configured_pipeline(root: Path) -> Path | None:
    """Return ``[project].pipeline`` from *root*/haute.toml, if set.

    A thin wrapper around :mod:`tomllib` that returns :data:`None` when
    the TOML is missing, unreadable, or simply doesn't define
    ``[project].pipeline``.  The resolver uses the return value to decide
    whether to fall through to discovery.

    Note the return value is **unresolved** — the caller checks existence
    and raises :class:`FileNotFoundError` if the configured file is
    missing so a typo in ``haute.toml`` doesn't silently fall through to
    auto-discovery.
    """
    toml_path = root / "haute.toml"
    if not toml_path.exists():
        return None
    try:
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        # Malformed TOML is a user config bug; surface it elsewhere
        # (via DeployConfig.from_toml for example).  The resolver has no
        # opinion on TOML correctness — it just can't use it as a source.
        return None
    pipeline = data.get("project", {}).get("pipeline")
    if not isinstance(pipeline, str) or not pipeline:
        return None
    return root / pipeline


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
    if candidate.name in _NOT_A_PIPELINE or candidate.suffix != ".py":
        return False
    try:
        text = candidate.read_text(errors="replace")
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
    configured = _toml_configured_pipeline(root)
    if configured is not None:
        if not configured.exists():
            raise FileNotFoundError(
                f"Pipeline file configured in haute.toml [project].pipeline "
                f"does not exist: {configured}. "
                "Fix the path in haute.toml or create the file."
            )
        if not _looks_like_pipeline_file(configured):
            raise FileNotFoundError(
                f"Pipeline file configured in haute.toml [project].pipeline "
                f"does not look like a Haute pipeline: {configured}. "
                "Point [project].pipeline at a .py file containing 'haute.Pipeline'."
            )
        return configured.resolve()

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


def resolve_pipeline_file(path: Path | None) -> Path:
    """Resolve a user-supplied pipeline path to a concrete absolute file.

    The canonical pipeline-file resolver — every CLI command and every
    programmatic caller funnels through this function so resolution
    drift is impossible.

    Resolution rules:

    * ``None`` → run the fallback chain (TOML-configured → ``main.py``
      → single auto-discovery → raise) scoped to :func:`pathlib.Path.cwd`.
      See :func:`_resolve_default_in` for the full tier ordering.
    * A **directory** → same fallback chain, but scoped to that
      directory.  ``haute run ./rating`` is therefore symmetric with
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
        The user-supplied path, or ``None`` to use the project default.

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
    if path is None:
        return _resolve_default_in(Path.cwd())

    if path.is_dir():
        return _resolve_default_in(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Pipeline file not found: {path}. Check the path or pass a different file."
        )

    return path.resolve()
