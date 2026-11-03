"""The one printer for every JSON file Haute commits to a project (READ-10).

A node's config sidecar, a pipeline's ``.haute.json``, the global constants,
the Workbench form folder and a test quote are all written by
:func:`render_json`: UTF-8 with nothing escaped, LF line endings, a two-space
indent and a final newline. A record is shaped by its kind, never by a width
limit, so a one-character edit changes one line: the writer of each kind of
file names the records it writes on one line (a rating entry, a node position,
a layout box, a schema column, a show-when condition), and a list or mapping
holding only numbers is one line too. Everything else is one item a line. Keys
keep the order their writer gives them; each writer orders its records' keys
as its specification lists them.

The release form a release ships (``_workbench_form.render_form``) is not a
source file and keeps its own serialisation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

__all__ = ["JsonPath", "OneLine", "at", "render_json", "render_json_bytes"]

#: Where a value sits in a document: mapping keys and list indices from the root.
JsonPath = tuple[str | int, ...]
#: Whether the record at a path is written on one line.
OneLine = Callable[[JsonPath], bool]

#: Matches any one key or index in a pattern given to :func:`at`.
ANY = "*"


def at(*patterns: Sequence[str | int]) -> OneLine:
    """A :data:`OneLine` naming the records at *patterns*: paths of keys and indices,
    :data:`ANY` (``"*"``) standing for any one of them."""

    def matches(path: JsonPath) -> bool:
        return any(
            len(pattern) == len(path)
            and all(part == ANY or part == step for part, step in zip(pattern, path, strict=True))
            for pattern in patterns
        )

    return matches


def _never(_path: JsonPath) -> bool:
    return False


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _numbers_only(value: dict[str, Any] | list[Any]) -> bool:
    items = value.values() if isinstance(value, dict) else value
    return all(_number(item) for item in items)


def _scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _one_line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _render(value: Any, path: JsonPath, indent: int, one_line: OneLine) -> str:
    if not isinstance(value, dict | list):
        return _scalar(value)
    if not value:
        return "{}" if isinstance(value, dict) else "[]"
    if one_line(path) or _numbers_only(value):
        return _one_line(value)
    pad = "  " * (indent + 1)
    if isinstance(value, dict):
        items = ",\n".join(
            f"{pad}{_scalar(key)}: {_render(item, (*path, key), indent + 1, one_line)}"
            for key, item in value.items()
        )
        return "{\n" + items + "\n" + "  " * indent + "}"
    items = ",\n".join(
        f"{pad}{_render(item, (*path, index), indent + 1, one_line)}"
        for index, item in enumerate(value)
    )
    return "[\n" + items + "\n" + "  " * indent + "]"


def render_json(value: Any, *, one_line: OneLine = _never) -> str:
    """*value* as Haute writes a JSON file: the records *one_line* names, and lists and
    mappings of numbers alone, on one line each; the rest one item a line, two-space
    indented; nothing escaped; a final newline."""
    return _render(value, (), 0, one_line) + "\n"


def render_json_bytes(value: Any, *, one_line: OneLine = _never) -> bytes:
    """:func:`render_json` as the UTF-8 bytes written to disk, LF line endings."""
    return render_json(value, one_line=one_line).encode("utf-8")
