"""Pipeline file discovery - shared by CLI and server."""

from __future__ import annotations

from pathlib import Path

from haute._logging import get_logger
from haute.errors import ConfigError

logger = get_logger(component="discovery")

_SKIP = {"__init__.py", "setup.py", "conftest.py"}


def _configured_pipeline(root: Path) -> Path | None:
    """Read ``haute.toml`` and return the configured pipeline path, if any."""
    toml_path = root / "haute.toml"
    if not toml_path.exists():
        logger.error(
            "haute_toml_missing", root=str(root), hint="Run 'haute init' to create a project"
        )
        return None
    try:
        import tomllib

        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        rel: str | None = data.get("project", {}).get("pipeline")
        if rel:
            return root / rel
    except Exception:
        logger.error("haute_toml_read_failed", path=str(toml_path), exc_info=True)
    return None


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
    if configured is not None and configured.exists():
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
