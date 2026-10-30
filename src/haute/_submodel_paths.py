"""Submodel path resolution shared by parser and routes."""

from __future__ import annotations

import builtins
import keyword
from os.path import normcase
from pathlib import Path

from haute._sandbox import contained_path
from haute.errors import PathOutsideProjectError

#: The package beside a pipeline that holds its submodel modules, one per definition:
#: ``modules/<name>.py``, imported by the pipeline as ``from modules import <name>``.
SUBMODEL_PACKAGE = "modules"


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


def submodel_module_file(name: str) -> str:
    """The file, relative to the pipeline, that ``from modules import <name>`` imports."""
    return f"{SUBMODEL_PACKAGE}/{name}.py"


def submodel_module_name(rel_path: str) -> str:
    """The name ``from modules import <name>`` imports the definition file *rel_path* by.

    A definition is a module directly in the pipeline's ``modules/`` folder, named as a
    node is (a canonical identifier, as Create Submodel names it), and not a keyword, a
    dunder name or a Python built-in. Anything else raises
    ``MalformedSubmodelPathError``, saying where the file belongs or what to rename.
    """
    from haute._graph_utils import _sanitize_func_name

    parts = [part for part in rel_path.replace("\\", "/").split("/") if part not in ("", ".")]
    name = parts[-1].removesuffix(".py") if parts else ""
    if len(parts) != 2 or parts[0] != SUBMODEL_PACKAGE or not parts[1].endswith(".py"):
        raise MalformedSubmodelPathError(
            f"Submodel file {rel_path!r} is not a module in the pipeline's "
            f"{SUBMODEL_PACKAGE}/ folder: a submodel is defined in "
            f"{SUBMODEL_PACKAGE}/<name>.py, imported by the pipeline as "
            f"`from {SUBMODEL_PACKAGE} import <name>`."
        )
    if (
        not name
        or _sanitize_func_name(name) != name
        or keyword.iskeyword(name)
        or name.startswith("__")
    ):
        raise MalformedSubmodelPathError(
            f"Submodel file {rel_path!r} cannot be imported by its name: a submodel module "
            "is named as a node is, with ASCII letters, digits and underscores, not a "
            "keyword, and not starting with '__'."
        )
    if hasattr(builtins, name):
        raise MalformedSubmodelPathError(
            f"Submodel file {rel_path!r} would import as {name!r}, which hides Python's "
            "built-in of that name in the pipeline file: rename the submodel."
        )
    return name


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
    active_dir = (pipeline_dir or project_root).resolve()
    try:
        submodel_path = contained_path(project_root, (active_dir / rel_path).resolve())
    except PathOutsideProjectError:
        raise SubmodelPathOutsideProjectError(
            f"Submodel path {rel_path!r} escapes project directory"
        ) from None
    return submodel_path, active_dir


def submodel_source_key(path: Path) -> str:
    """The file a resolved registration names: registrations with one key share a definition.

    Equivalent spellings (dot segments, and casing where the filesystem folds it)
    give one key.
    """
    return normcase(str(path.resolve()))


def definition_pipeline_dir(rel_path: str) -> str:
    """The owning pipeline's directory, relative to a definition file registered at *rel_path*.

    A registration path never climbs (``..`` is rejected), so the answer is one
    ``..`` per directory the file sits below the pipeline, or ``.`` beside it.
    Dot and empty segments (``./x.py``, ``a//x.py``) are not directories.
    """
    segments = [part for part in rel_path.replace("\\", "/").split("/") if part not in ("", ".")]
    return "/".join([".."] * (len(segments) - 1)) or "."


def is_pipeline_dir(value: object) -> bool:
    """Whether *value* is a canonical ``pipeline_dir``: ``.`` or ``..`` segments."""
    return isinstance(value, str) and (
        value == "." or (bool(value) and all(part == ".." for part in value.split("/")))
    )
