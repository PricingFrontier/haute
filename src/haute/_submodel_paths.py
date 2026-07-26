"""Submodel path resolution shared by parser and routes."""

from __future__ import annotations

from pathlib import Path


class SubmodelPathError(ValueError):
    """Base class for user-authored submodel path failures."""


class MalformedSubmodelPathError(SubmodelPathError):
    """A route path segment cannot represent one submodel name."""


class SubmodelPathOutsideProjectError(SubmodelPathError):
    """A submodel reference resolves outside the project root."""


def validate_submodel_name(name: str) -> None:
    """Validate a route-level submodel name before any filesystem lookup."""
    if not name or "\x00" in name or "/" in name or "\\" in name:
        raise MalformedSubmodelPathError(
            "Submodel name must be one non-empty path segment.",
        )


def resolve_submodel_reference(
    rel_path: str,
    *,
    pipeline_dir: Path | None,
    project_root: Path,
) -> tuple[Path, Path]:
    """Resolve a submodel reference and the config base it should use.

    References are relative to the active pipeline directory.
    """
    normalised = rel_path.replace("\\", "/")
    if not rel_path or "\x00" in rel_path or any(part == ".." for part in normalised.split("/")):
        raise MalformedSubmodelPathError(
            "Submodel reference must be a non-empty path without traversal segments.",
        )
    resolved_root = project_root.resolve()
    active_dir = (pipeline_dir or project_root).resolve()

    submodel_path = (active_dir / rel_path).resolve()
    if not submodel_path.is_relative_to(resolved_root):
        raise SubmodelPathOutsideProjectError(
            f"Submodel path {rel_path!r} escapes project directory"
        )
    return submodel_path, active_dir


def resolve_submodel_by_name(
    name: str,
    *,
    pipeline_dir: Path,
    project_root: Path,
) -> tuple[Path, Path]:
    """Resolve ``<name>.py`` relative to the active pipeline directory."""
    validate_submodel_name(name)
    return resolve_submodel_reference(
        f"modules/{name}.py",
        pipeline_dir=pipeline_dir,
        project_root=project_root,
    )
