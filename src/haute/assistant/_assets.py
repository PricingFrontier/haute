"""Load the pricing assistant's packaged authoring knowledge.

The exemplar files are package data, not importable pipeline modules.  Keeping
them as text is important: importing an exemplar would execute user-shaped
pipeline code and would make the examples depend on the project's optional
runtime environment.  The parser is used only when an example is requested,
so the graph returned to the assistant is produced by the same code path as a
saved pipeline.
"""

from __future__ import annotations

import ast
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

_ASSET_PACKAGE = "haute.assistant"
_ASSET_DIR = "assets"
_EXAMPLES_DIR = "examples"
_GUIDE_NAME = "authoring_guide.md"


def _asset_root() -> Traversable:
    """Return the resource directory containing the assistant assets."""

    return resources.files(_ASSET_PACKAGE).joinpath(_ASSET_DIR)


def _examples_root() -> Traversable:
    """Return the resource directory containing exemplar pipeline sources."""

    return _asset_root().joinpath(_EXAMPLES_DIR)


@cache
def _example_resources() -> tuple[tuple[str, Traversable], ...]:
    """Return all exemplar resources in stable, source-file order."""

    examples_root = _examples_root()
    examples = tuple(
        sorted(
            (
                Path(resource.name).stem,
                resource,
            )
            for resource in examples_root.iterdir()
            if resource.is_file() and resource.name.endswith(".py")
        )
    )
    if not examples:
        raise RuntimeError("No assistant exemplar pipeline assets were found.")
    return examples


def _read_resource(resource: Traversable) -> str:
    """Read a UTF-8 package resource as text."""

    return resource.read_text(encoding="utf-8")


def _module_notes(source: str, *, resource_name: str) -> str:
    """Extract and validate an exemplar's complete module docstring."""

    try:
        tree = ast.parse(source, filename=resource_name)
    except SyntaxError as exc:
        raise RuntimeError(f"Assistant exemplar {resource_name!r} is not valid Python.") from exc

    notes = ast.get_docstring(tree)
    if notes is None or not notes.strip():
        raise RuntimeError(f"Assistant exemplar {resource_name!r} must have a module docstring.")
    if not notes.splitlines()[0].strip():
        raise RuntimeError(
            f"Assistant exemplar {resource_name!r} must start its module docstring with a summary."
        )
    return notes


def _resource_for_name(name: str) -> Traversable | None:
    """Find an exemplar by its filename stem."""

    return dict(_example_resources()).get(name)


@cache
def authoring_guide() -> str:
    """Return the packaged Haute authoring guide.

    A missing or empty guide is a packaging defect and therefore raises a
    clear error instead of silently weakening every assistant turn.
    """

    resource = _asset_root().joinpath(_GUIDE_NAME)
    try:
        guide = _read_resource(resource)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"Assistant authoring guide is missing: {_GUIDE_NAME}") from exc
    if not guide.strip():
        raise RuntimeError(f"Assistant authoring guide is empty: {_GUIDE_NAME}")
    return guide


@cache
def example_index() -> list[tuple[str, str]]:
    """Return ``(example_name, one_line_summary)`` pairs for all exemplars."""

    index: list[tuple[str, str]] = []
    for name, resource in _example_resources():
        notes = _module_notes(_read_resource(resource), resource_name=resource.name)
        index.append((name, notes.splitlines()[0].strip()))
    return index


def _unknown_example_error(name: str) -> dict[str, object]:
    """Build the structured error passed back to the model for an unknown name."""

    valid_names = [example_name for example_name, _summary in example_index()]
    message = f"Unknown assistant example {name!r}. Choose one of: {', '.join(valid_names)}."
    return {
        "error": {
            "code": "unknown_example",
            "message": message,
            "name": name,
            "valid_names": valid_names,
        }
    }


def load_example(name: str) -> dict[str, object]:
    """Return an exemplar's notes and parser-produced graph rendering.

    Exemplars are parsed as source files and never imported.  ``as_file`` also
    makes the parser work when package resources are supplied by a zip-backed
    importer rather than a normal filesystem installation.
    """

    resource = _resource_for_name(name)
    if resource is None:
        return _unknown_example_error(name)

    source = _read_resource(resource)
    notes = _module_notes(source, resource_name=resource.name)

    from haute.routes._helpers import parse_pipeline_to_graph

    with resources.as_file(resource) as example_path:
        graph = parse_pipeline_to_graph(Path(example_path))

    # Import lazily because _tools owns the shared graph rendering function
    # and imports this module for the example dispatcher.
    from haute.assistant._tools import render_pipeline_graph

    return {
        "name": name,
        "narrative": notes,
        "graph": render_pipeline_graph(graph),
    }


__all__ = ["authoring_guide", "example_index", "load_example"]
