"""The project's folder layout: what Haute versions and what stays local.

The pipeline folder (the folder holding ``[project].pipeline``) is versioned as a
whole. Its ``files/`` folder holds what the pipeline reads that is not code:
``files/data/``, ``files/models/`` and ``files/params/``. The project root's ``data/`` and
``outputs/`` stay on the analyst's machine.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from haute._project import _toml_configured_pipeline

PIPELINE_FILES_FOLDER = "files"
PIPELINE_FILES_SUBFOLDERS = ("data", "models", "params")
LOCAL_FOLDERS = ("data", "outputs")

#: The largest file Haute versions from the pipeline folder: GitHub refuses larger files.
VERSIONED_FILE_LIMIT_BYTES = 100 * 1024 * 1024


def versioned_name(relative: str) -> bool:
    """Whether *relative*, a ``/``-separated path, names a file the project versions through
    Haute: no part a dot-name (``.env.local``, ``.DS_Store``, an editor's lock file, the
    deploy's ``.haute_build``) or ``__pycache__``, and not bytecode. The Files panels show
    and take only such names, and a milestone sweeps in only such new files, so Commit
    never records a file the analyst could not see in Haute."""
    parts = PurePosixPath(relative).parts
    return (
        bool(parts)
        and not relative.endswith(".pyc")
        and not any(part.startswith(".") or part.casefold() == "__pycache__" for part in parts)
    )


def pipeline_folder(project_root: Path) -> str | None:
    """The configured pipeline folder, relative to *project_root* in POSIX form.

    ``None`` when no pipeline is configured or it sits at the root: there is then
    no folder to version as a whole. Malformed ``haute.toml``, or a pipeline it
    names outside the root, raises.
    """
    configured = _toml_configured_pipeline(project_root)
    if configured is None:
        return None
    root = project_root.resolve()
    folder = configured.parent.resolve()
    if folder == root:
        return None
    return folder.relative_to(root).as_posix()


def pipeline_files_folder(project_root: Path) -> str | None:
    """The pipeline folder's ``files/``, relative to *project_root*, or ``None``."""
    folder = pipeline_folder(project_root)
    if folder is None:
        return None
    return str(PurePosixPath(folder) / PIPELINE_FILES_FOLDER)


def default_model_folder(project_root: Path) -> str:
    """Where a bare model or optimiser-result filename lands, relative to the root."""
    files = pipeline_files_folder(project_root)
    if files is None:
        return "models"
    return str(PurePosixPath(files) / "models")
