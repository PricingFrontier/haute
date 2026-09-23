"""Where the Export pane's "Save model to file" writes a trained model.

The rules mirror a file Data Output (``resolve_data_output_path``) with a
``models/`` folder in place of ``outputs/``: a bare filename lands under
``models/``, a missing extension gets the trained model's, and every path is
project-root-relative and must stay inside the project.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType

from haute._path_resolution import (
    RuntimePathOutsideProjectError,
    _normalise_path_text,
    _reject_reserved_device_components,
)
from haute.modelling._descriptors import DESCRIPTORS

MODEL_EXPORT_FOLDER = "models"

#: The native model file extension each algorithm trains to, from its descriptor.
MODEL_FILE_SUFFIXES: Mapping[str, str] = MappingProxyType(
    {key: descriptor.suffix for key, descriptor in DESCRIPTORS.items()}
)


@dataclass(frozen=True, slots=True)
class ModelExportDestination:
    path: Path
    """The resolved filesystem path inside the project."""
    display_path: str
    """The forward-slash spelling shown to the user, before resolution."""
    suffix_mismatch: bool
    """An explicit extension that is not the model's (ignoring case)."""


def resolve_model_export_destination(
    raw_path: str,
    *,
    model_suffix: str,
    project_root: Path,
) -> ModelExportDestination:
    """Resolve a user's model filename or path against the project root."""
    text = _normalise_path_text(raw_path)
    if "/" not in text:
        text = f"{MODEL_EXPORT_FOLDER}/{text}"
    explicit_suffix = PurePosixPath(text).suffix
    if not explicit_suffix:
        text = f"{text}{model_suffix}"

    relative = Path(text)
    _reject_reserved_device_components(raw_path, relative)
    if PureWindowsPath(text).drive and not relative.is_absolute():
        raise RuntimePathOutsideProjectError(f"Path {raw_path!r} resolves outside the project root")
    root = project_root.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise RuntimePathOutsideProjectError(f"Path {raw_path!r} resolves outside the project root")
    return ModelExportDestination(
        path=target,
        display_path=text,
        suffix_mismatch=bool(explicit_suffix)
        and explicit_suffix.casefold() != model_suffix.casefold(),
    )
