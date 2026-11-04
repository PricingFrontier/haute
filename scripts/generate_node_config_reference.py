"""Write each node reference page's config-keys table from the node type's config model.

Every page in ``docs/building-models/nodes/`` documenting a node type carries, inside
its "In the pipeline file" note, a table of every key its config may hold: the key
(a nested one under its parent's path, ``factors[].banding``), its type and its
description, the model's attribute docstring. The table sits between
``<!-- config-keys:start -->`` and ``<!-- config-keys:end -->`` and is regenerated
from ``haute._node_config_models``; ``--check`` fails when a page's table is stale.
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, TypeAdapter
from typing_extensions import is_typeddict

from haute._config_validation import FORM_GIVEN_KEYS
from haute._node_config_models import (
    NODE_CONFIG_MODELS,
    NodeConfigBase,
    shape_variants,
)
from haute._types import NodeType

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_REFERENCE = REPO_ROOT / "docs" / "building-models" / "nodes"

#: Every node type a user can add, and its one reference page.
NODE_REFERENCE_PAGES: dict[NodeType, str] = {
    NodeType.API_INPUT: "quote-input.md",
    NodeType.WORKBENCH_INPUT: "workbench-input.md",
    NodeType.WORKBENCH_OUTPUT: "workbench-output.md",
    NodeType.DATA_INPUT: "data-input.md",
    NodeType.DATA_OUTPUT: "data-output.md",
    NodeType.CONSTANT: "constant.md",
    NodeType.POLARS: "transform.md",
    NodeType.EDGE_JOIN: "edge-join.md",
    NodeType.BANDING: "banding.md",
    NodeType.RATING_STEP: "rating-step.md",
    NodeType.SCENARIO_EXPANDER: "scenario-expander.md",
    NodeType.LIVE_SWITCH: "source-switch.md",
    NodeType.MODELLING: "model-training.md",
    NodeType.MODEL_SCORE: "model-score.md",
    NodeType.EXTERNAL_FILE: "external-file.md",
    NodeType.OPTIMISER: "optimiser.md",
    NodeType.OPTIMISER_APPLY: "optimiser-apply.md",
    NodeType.OUTPUT: "output.md",
    NodeType.EXPLORE: "explore.md",
    NodeType.SUBMODEL: "submodel.md",
}

START = "<!-- config-keys:start -->"
END = "<!-- config-keys:end -->"
_NOTE = re.compile(r'^\?\?\? note "In the pipeline file"\n((?:(?:    .*)?\n)+)', re.M)
_INDENT = "    "


def type_text(annotation: Any) -> str:
    """A key's type in words."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is str:
        return "text"
    if annotation is bool:
        return "`true` or `false`"
    if annotation is int:
        return "whole number"
    if annotation is float:
        return "number"
    if annotation is Any:
        return "any JSON value"
    if annotation is type(None):
        return "`null`"
    if origin is Literal:
        return " or ".join(
            f"`{value!r}`" if not isinstance(value, str) else f'`"{value}"`' for value in args
        )
    if origin in (Union, types.UnionType):
        # A whole number is a number: ``int | float`` reads "number" once.
        members = [arg for arg in args if not (arg is int and float in args)]
        return " or ".join(type_text(arg) for arg in members)
    if origin is list:
        return f"list of {type_text(args[0])}" if args else "list"
    if origin is dict:
        return f"map of text to {type_text(args[1])}" if len(args) > 1 else "object"
    if is_typeddict(annotation) or (
        isinstance(annotation, type) and issubclass(annotation, BaseModel)
    ):
        return "object"
    return "any JSON value"


def _nested_shape(annotation: Any) -> tuple[Any, str] | None:
    """The object shape *annotation* holds and the path suffix that reaches it."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if is_typeddict(annotation):
        return annotation, ""
    if origin is list and args and is_typeddict(args[0]):
        return args[0], "[]"
    if origin in (Union, types.UnionType):
        shapes = [arg for arg in args if arg is not type(None)]
        if len(shapes) == 1:
            return _nested_shape(shapes[0])
    return None


@functools.cache
def _descriptions(shape: Any) -> dict[str, str]:
    """Each key's description in *shape*: its attribute docstring, as the model reads it."""
    properties = TypeAdapter(shape).json_schema().get("properties", {})
    return {
        key: " ".join(str(value.get("description", "")).split())
        for key, value in properties.items()
    }


def _description(shape: Any, key: str) -> str:
    """*key*'s description: the attribute docstring of the shape, or of the base declaring it.

    Pydantic reads a ``TypedDict``'s attribute docstrings from its own body, so an
    inherited key's description is its declaring base's.
    """
    text = _descriptions(shape).get(key, "")
    if text:
        return text
    for base in getattr(shape, "__orig_bases__", ()):
        if is_typeddict(base) and key in base.__annotations__:
            text = _description(base, key)
            if text:
                return text
    return ""


def _rows(shape: Any, prefix: str, skip: frozenset[str]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for key, annotation in get_type_hints(shape).items():
        if key in skip:
            continue
        path = f"{prefix}{key}"
        rows.append((path, type_text(annotation), _description(shape, key)))
        nested = _nested_shape(annotation)
        if nested is not None:
            child, suffix = nested
            rows.extend(_rows(child, f"{path}{suffix}.", frozenset()))
    return rows


def config_key_rows(node_type: NodeType) -> list[tuple[str, str, str]]:
    """Every key a *node_type* config may hold: its own, then the keys any node carries.

    A workbench node's form-given keys are left out: the file never holds them.
    """
    model = NODE_CONFIG_MODELS[node_type]
    # A workbench node's tables and sample are given from the form, never stored.
    universal = frozenset(NodeConfigBase.__annotations__) | FORM_GIVEN_KEYS.get(
        node_type, frozenset()
    )
    variants = shape_variants(model.shape)
    by_path: dict[str, tuple[str, str, list[str]]] = {}
    for variant in variants:
        branch = ""
        if model.discriminant is not None and len(variants) > 1:
            branch = str(get_args(get_type_hints(variant)[model.discriminant])[0])
        for path, type_, description in _rows(variant, "", universal):
            if path not in by_path:
                by_path[path] = (type_, description, [])
            if branch:
                by_path[path][2].append(branch)
    rows = []
    for path, (type_, description, branches) in by_path.items():
        if branches and len(branches) < len(variants) and path != model.discriminant:
            description = (
                f"{description} With `{model.discriminant}` "
                + ", ".join(f'`"{branch}"`' for branch in branches)
                + "."
            )
        rows.append((path, type_, description))
    rows.extend(_rows(NodeConfigBase, "", frozenset()))
    return rows


def render_table(node_type: NodeType) -> str:
    """The generated block: the markers around one table, indented into the note."""
    lines = [
        START,
        "",
        "Every key the config may hold:",
        "",
        "| Key | Type | Description |",
        "|---|---|---|",
    ]
    for path, type_, description in config_key_rows(node_type):
        cell = description.replace("|", "\\|")
        lines.append(f"| `{path}` | {type_.replace('|', chr(92) + '|')} | {cell} |")
    lines.append("")
    lines.append(END)
    return "".join(f"{_INDENT}{line}\n" if line else "\n" for line in lines)


def page_with_table(text: str, node_type: NodeType) -> str:
    """*text* with its note's generated table replaced, or added at the note's end."""
    block = render_table(node_type)
    start = text.find(f"{_INDENT}{START}")
    if start != -1:
        end = text.index(f"{_INDENT}{END}\n", start) + len(f"{_INDENT}{END}\n")
        return text[:start] + block + text[end:]
    note = _NOTE.search(text)
    if note is None:
        raise ValueError(f"no 'In the pipeline file' note for {node_type.value}")
    body = note.group(1)
    content_end = note.start(1) + len(body.rstrip("\n")) + 1
    return text[:content_end] + "\n" + block + text[content_end:]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when a page's table is stale")
    args = parser.parse_args(argv)
    stale = []
    for node_type, page in NODE_REFERENCE_PAGES.items():
        path = NODE_REFERENCE / page
        text = path.read_text(encoding="utf-8")
        expected = page_with_table(text, node_type)
        if expected == text:
            continue
        if args.check:
            stale.append(page)
        else:
            # The repository pins LF; text mode on Windows would write CRLF.
            path.write_text(expected, encoding="utf-8", newline="\n")
    if stale:
        print(
            "node reference config-keys tables are stale: "
            + ", ".join(stale)
            + "; run `uv run python scripts/generate_node_config_reference.py`",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
