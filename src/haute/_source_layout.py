"""A document printer that lays Python source out the way ``ruff format`` does.

Codegen prints every generated module through it, and the Polars step layout
(:mod:`haute._polars_steps_layout`) prints each rendered statement through it,
so a saved pipeline file is a fixed point of ``ruff format`` at its defaults:
88 columns and double quotes.

The algorithm is Wadler's prettier printer, the one ruff's own printer
follows. A :class:`Group` prints flat when it fits on the line up to its next
possible break, and broken otherwise; a broken :class:`Line` is a newline at
the current indent. Two sequence shapes cover what Haute emits.
:func:`bracketed` lays out a list or dict literal: flat, or one entry per line
with a trailing comma. :func:`arguments` lays out call and signature arguments:
flat, then on one indented line of their own, then one per line with a trailing
comma; a lone parameter of a broken signature takes a trailing comma too, and a
lone call argument never does. A trailing comma in a broken sequence is a magic trailing comma to ruff,
which keeps that sequence broken.

This is not a general formatter: it prints documents its callers build from
values and names they control, never parsed source.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeAlias

from haute.errors import HauteError

__all__ = [
    "INDENT",
    "LINE_WIDTH",
    "SOFT",
    "SPACE",
    "Doc",
    "Group",
    "IfBreak",
    "Indent",
    "Line",
    "arguments",
    "bracketed",
    "literal",
    "print_doc",
    "quote_string",
    "width",
]

#: ruff format's default line length.
LINE_WIDTH = 88
INDENT = "    "


@dataclass(frozen=True, slots=True)
class Group:
    """Printed flat when it fits on the line up to the next possible break."""

    contents: Doc


@dataclass(frozen=True, slots=True)
class Indent:
    contents: Doc


@dataclass(frozen=True, slots=True)
class Line:
    """A line break in a broken group; flat, a space (or nothing when soft)."""

    soft: bool


@dataclass(frozen=True, slots=True)
class IfBreak:
    """Text printed only when the enclosing group breaks (a trailing comma)."""

    text: str


Doc: TypeAlias = "str | Group | Indent | Line | IfBreak | list[Doc]"
SOFT = Line(soft=True)
SPACE = Line(soft=False)


def width(text: str) -> int:
    """Display columns, as ruff measures them: wide characters take two, marks none."""
    if text.isascii():
        return len(text)
    return sum(
        0
        if unicodedata.combining(char)
        else 2
        if unicodedata.east_asian_width(char) in ("W", "F")
        else 1
        for char in text
    )


def _fits(contents: Doc, rest: list[tuple[int, bool, Doc]], available: int) -> bool:
    """Whether *contents*, printed flat, and the rest up to its next break fit."""
    pending: list[tuple[bool, Doc]] = [(False, contents)]
    rest_index = len(rest)
    while available >= 0:
        if not pending:
            if rest_index == 0:
                return True
            rest_index -= 1
            _indent, broken, item = rest[rest_index]
            pending.append((broken, item))
            continue
        broken, item = pending.pop()
        if isinstance(item, str):
            available -= width(item)
        elif isinstance(item, list):
            pending.extend((broken, part) for part in reversed(item))
        elif isinstance(item, (Group, Indent)):
            pending.append((broken, item.contents))
        elif isinstance(item, Line):
            if broken:
                return True
            available -= 0 if item.soft else 1
        elif broken:
            available -= width(item.text)
    return False


def print_doc(doc: Doc, *, indent: int = 0) -> str:
    """Print *doc* as it lays out when it starts *indent* levels in.

    The first line carries no indentation of its own (the caller places it);
    every line break indents to the document's nesting plus *indent* levels.
    """
    out: list[str] = []
    column = len(INDENT) * indent
    stack: list[tuple[int, bool, Doc]] = [(indent, True, doc)]
    while stack:
        level, broken, item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            column += width(item)
        elif isinstance(item, list):
            stack.extend((level, broken, part) for part in reversed(item))
        elif isinstance(item, Indent):
            stack.append((level + 1, broken, item.contents))
        elif isinstance(item, Group):
            breaks = broken and not _fits(item.contents, stack, LINE_WIDTH - column)
            stack.append((level, breaks, item.contents))
        elif isinstance(item, Line):
            if broken:
                out.append("\n" + INDENT * level)
                column = len(INDENT) * level
            elif not item.soft:
                out.append(" ")
                column += 1
        elif broken:
            out.append(item.text)
            column += width(item.text)
    return "".join(out)


def _comma_separated(entries: Sequence[Doc]) -> list[Doc]:
    body: list[Doc] = []
    for index, entry in enumerate(entries):
        if index:
            body.extend([",", SPACE])
        body.append(entry)
    return body


def bracketed(open_: str, entries: Sequence[Doc], close: str) -> Doc:
    """A list or dict display: flat, or one entry per line.

    A broken display of two or more entries ends with a trailing comma, which
    ruff reads as a magic trailing comma and keeps expanded.
    """
    if not entries:
        return open_ + close
    body = _comma_separated(entries)
    if len(entries) > 1:
        body.append(IfBreak(","))
    return Group([open_, Indent([SOFT, body]), SOFT, close])


def arguments(
    open_: str, entries: Sequence[Doc], close: str, *, signature: bool = False
) -> Doc:
    """Call or signature arguments, laid out as ruff lays them out.

    Flat when they fit; otherwise on one indented line of their own when that
    fits; otherwise one per line with a trailing comma, which ruff reads as a
    magic trailing comma and keeps expanded. A lone argument takes no trailing
    comma, except a function's lone parameter, which ruff gives one whenever
    the signature breaks.
    """
    if not entries:
        return open_ + close
    if len(entries) == 1:
        tail: list[Doc] = [IfBreak(",")] if signature else []
        return Group([open_, Indent([SOFT, entries[0], *tail]), SOFT, close])
    body = _comma_separated(entries)
    body.append(IfBreak(","))
    return Group([open_, Indent([SOFT, Group(body)]), SOFT, close])


def quote_string(value: str) -> str:
    """A string literal as ruff writes it: double quotes unless single ones need fewer escapes."""
    quote = "'" if value.count('"') > value.count("'") else '"'
    body = "".join(
        f"\\{char}" if char == quote else char if char in "'\"" else repr(char)[1:-1]
        for char in value
    )
    return f"{quote}{body}{quote}"


def literal(value: object) -> Doc:
    """The Python literal for a JSON-like *value*: what a decorator keyword can carry.

    Strings, finite numbers, booleans, ``None``, lists and string-keyed dicts of
    those print as ruff writes them. Anything else has no literal form a parser
    could read back, so it raises rather than falling back to ``repr``.
    """
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HauteError(
                "A generated decorator value must be a finite number.",
                value=repr(value),
            )
        return repr(value)
    if isinstance(value, str):
        return quote_string(value)
    if isinstance(value, list):
        return bracketed("[", [literal(item) for item in value], "]")
    if isinstance(value, dict):
        entries: list[Doc] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise HauteError(
                    "A generated decorator dict must have string keys.",
                    key=repr(key),
                )
            entries.append([quote_string(key), ": ", literal(item)])
        return bracketed("{", entries, "}")
    raise HauteError(
        "A generated decorator value has no Python literal form.",
        value_type=type(value).__name__,
    )
