"""The project's pipelines: the one list, shared by the CLI and the server."""

from __future__ import annotations

from pathlib import Path

from haute._project import _is_python_file, configured_pipeline_file
from haute._project_context import current_project
from haute.errors import ConfigError


def discover_pipelines(root: Path | None = None) -> list[Path]:
    """Every pipeline of the project at *root*: the one list of a project's pipelines.

    In this order, each file once:

    1. The pipeline configured in ``haute.toml`` (``[project].pipeline``), checked as the
       resolver checks it: a file that is missing, unreadable or holds no pipeline raises
       :class:`~haute._project.PipelineFileError`.
    2. The other ``.py`` files holding ``haute.Pipeline`` in the configured pipeline's
       folder: the pipelines beside it, which import the same ``modules/`` and ``utility/``.
    3. The ``.py`` files holding ``haute.Pipeline`` in the project folder itself.

    The editor's picker and every check that asks what else uses a file (a submodel's
    definition, a utility module, the module import index, a save's notice) read this list.

    Parameters
    ----------
    root:
        The project folder. Defaults to the current project's root.

    Raises
    ------
    ConfigError
        When a candidate file cannot be read, or the settings parse refuses ``haute.toml``.
    """
    if root is None:
        root = current_project().root

    found: list[Path] = []
    seen: set[Path] = set()

    def keep(candidate: Path) -> None:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            found.append(candidate)

    configured = configured_pipeline_file(root)
    if configured is not None:
        keep(configured)
        if configured.parent != root.resolve():
            for candidate in _pipelines_in(configured.parent):
                keep(candidate)
    for candidate in _pipelines_in(root):
        keep(candidate)
    return found


def _pipelines_in(folder: Path) -> list[Path]:
    """The ``.py`` files directly in *folder* holding ``haute.Pipeline``, sorted."""
    pipelines: list[Path] = []
    for candidate in sorted(folder.glob("*.py")):
        if not _is_python_file(candidate):
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ConfigError(
                "Could not read candidate pipeline file",
                path=str(candidate),
                error_type=type(exc).__name__,
                reason=str(exc),
            ) from exc
        if "haute.Pipeline" in text:
            pipelines.append(candidate)
    return pipelines
