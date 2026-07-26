"""Pipeline file discovery - shared by CLI and server."""

from __future__ import annotations

from pathlib import Path

from haute._project import _looks_like_pipeline_file, _toml_configured_pipeline
from haute.errors import ConfigError

_SKIP = {"__init__.py", "setup.py", "conftest.py"}


def _configured_pipeline(root: Path) -> Path | None:
    """Return the canonical resolver's configured pipeline candidate."""
    return _toml_configured_pipeline(root)


def discover_pipelines(root: Path | None = None) -> list[Path]:
    """Find ``.py`` files in *root* that contain ``haute.Pipeline``.

    Resolution order:

    1. The pipeline configured in ``haute.toml`` (``[project].pipeline``),
       if it exists and contains ``haute.Pipeline``.
    2. Root-level ``*.py`` files that contain ``haute.Pipeline``.

    Parameters
    ----------
    root:
        Directory to scan.  Defaults to ``Path.cwd()``.
    Returns
    -------
    list[Path]
        Sorted list of matching files.

    Raises
    ------
    ConfigError
        When a candidate file cannot be read.
    """
    if root is None:
        root = Path.cwd()

    found: list[Path] = []
    seen: set[Path] = set()

    # 1. Check the configured pipeline path from haute.toml
    configured = _configured_pipeline(root)
    if configured is not None:
        if not configured.exists():
            raise FileNotFoundError(
                "Pipeline file configured in haute.toml [project].pipeline "
                f"does not exist: {configured}. Fix the path in haute.toml or create the file."
            )
        if not _looks_like_pipeline_file(configured):
            raise FileNotFoundError(
                "Pipeline file configured in haute.toml [project].pipeline "
                f"does not look like a Haute pipeline: {configured}. "
                "Point [project].pipeline at a .py file containing 'haute.Pipeline'."
            )
        try:
            text = configured.read_text(errors="replace")
        except OSError as exc:
            raise ConfigError(
                "Could not read configured pipeline",
                path=str(configured),
                error_type=type(exc).__name__,
                reason=str(exc),
            ) from exc
        else:
            if "haute.Pipeline" in text:
                found.append(configured)
                seen.add(configured.resolve())

    # 2. Fall back to root-level *.py glob when no configured file matched.
    for f in sorted(root.glob("*.py")):
        if f.name in _SKIP:
            continue
        if f.resolve() in seen:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError as exc:
            raise ConfigError(
                "Could not read candidate pipeline file",
                path=str(f),
                error_type=type(exc).__name__,
                reason=str(exc),
            ) from exc
        if "haute.Pipeline" in text:
            found.append(f)

    return found
