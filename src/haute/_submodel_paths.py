"""Submodel path resolution shared by parser and routes."""

from __future__ import annotations

from pathlib import Path


class SubmodelPathError(ValueError):
    """Base class for user-authored submodel path failures."""


class MalformedSubmodelPathError(SubmodelPathError):
    """A route path segment cannot represent one submodel definition id."""


class SubmodelPathOutsideProjectError(SubmodelPathError):
    """A submodel reference resolves outside the project root."""


def validate_submodel_definition_id(definition_id: str) -> None:
    """Validate a route definition identity before registry lookup."""
    if (
        not definition_id
        or "\x00" in definition_id
        or "/" in definition_id
        or "\\" in definition_id
    ):
        raise MalformedSubmodelPathError(
            "Submodel definition id must be one non-empty path segment.",
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
