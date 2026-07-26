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

    References are relative to the active pipeline directory.
    """
    resolved_root = project_root.resolve()
    active_dir = (pipeline_dir or project_root).resolve()

    submodel_path = (active_dir / rel_path).resolve()
    if not submodel_path.is_relative_to(resolved_root):
        raise ValueError(f"Submodel path {rel_path!r} escapes project directory")
    return submodel_path, active_dir


def resolve_submodel_by_name(
    name: str,
    *,
    pipeline_dir: Path,
    project_root: Path,
) -> tuple[Path, Path]:
    """Resolve ``<name>.py`` relative to the active pipeline directory."""
    return resolve_submodel_reference(
        f"modules/{name}.py",
        pipeline_dir=pipeline_dir,
        project_root=project_root,
    )
