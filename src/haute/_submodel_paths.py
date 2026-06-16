"""Submodel path resolution shared by parser and routes."""

from __future__ import annotations

from pathlib import Path


def resolve_submodel_reference(
    rel_path: str,
    *,
    pipeline_dir: Path | None,
    project_root: Path,
) -> tuple[Path, Path]:
    """Resolve a submodel reference and the config base it should use.

    ``modules/<name>.py`` is pipeline-local for configured nested projects,
    with a project-root fallback for legacy projects. Explicit
    project-root-prefixed paths that still point under the active pipeline
    keep the active pipeline as their config base.
    """
    resolved_root = project_root.resolve()
    active_dir = (pipeline_dir or project_root).resolve()

    local_path = (active_dir / rel_path).resolve()
    project_path = (resolved_root / rel_path).resolve()
    for candidate in (local_path, project_path):
        if not candidate.is_relative_to(resolved_root):
            raise ValueError(f"Submodel path {rel_path!r} escapes project directory")

    if local_path.is_file():
        return local_path, active_dir
    if project_path.is_relative_to(active_dir):
        return project_path, active_dir
    return project_path, resolved_root


def resolve_submodel_by_name(
    name: str,
    *,
    pipeline_dir: Path,
    project_root: Path,
) -> tuple[Path, Path]:
    """Resolve ``<name>.py`` using the same preference as parser imports."""
    return resolve_submodel_reference(
        f"modules/{name}.py",
        pipeline_dir=pipeline_dir,
        project_root=project_root,
    )
