"""The shared path-grammar core — the single lynchpin (PATH_GRAMMAR.md §8).

A haute path is a mapping from a path string to a position in a tree-structured
document (PATH_GRAMMAR.md §0). The mapping is a **parameter of the transport
shape**; this module pins it for **array-outer JSON** — the one transport built
in this PR — where the document root is an array of records reached only by the
array selector ``[:]``.

This module is deliberately the *one* place the grammar lives, expressed as a
**small, named, doc-quotable** suite so PATH_GRAMMAR.md §8 can mirror it
verbatim. It carries three things, matching the spec's three lynchpin
constructs:

* the **acceptance** grammar — the regex pieces plus :func:`parse_path`, which
  accepts the full-width set (§2.2) and rejects everything in §3;
* the **canonicality** predicate — :func:`is_canonical`, true iff a path uses
  only the canonical spellings (§2.1);
* the **canonical writer** — :func:`make_output_path`, emitting the one
  canonical spelling.

The **transport shape** is the seam for siblings (object-outer JSON, JSONL, …
— §5): today its only value is array-outer, captured by the root constructs
``_ROOT_ARRAY`` / ``_ROOT`` and the ``$[:]`` canonical prefix. A sibling slots
in by varying those, not by rewriting the parser. This is left as a clear
*seam*, not a plugin system — over-engineering it is out of scope.

Consumers (``_api_input_schema.py`` INPUT, ``_output_assembler.py`` OUTPUT)
inject their own side's error class so a rejected path raises the type that
side's routes already discriminate on, while the grammar itself stays neutral.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol


class _PathError(Protocol):
    """The error a caller injects — a ``HauteError``-shaped class.

    Called as ``error(message, **context)``; the parser passes ``output_path``
    (or whichever context key the side uses) so the raised instance carries the
    offending path. Keeping it a parameter lets the neutral core raise each
    side's own discriminated error type (OUTPUT's ``OutputMappingSchemaError``,
    INPUT's ``ApiInputSchemaError``) without depending on either.
    """

    def __call__(self, message: str, **context: Any) -> Exception: ...


# ---------------------------------------------------------------------------
# The lynchpin — the grammar IS these named constructs (PATH_GRAMMAR.md §8)
# ---------------------------------------------------------------------------
#
# Acceptance grammar (full-width, §2.2): the identifier charset, the canonical
# dotted-name object selector, and the full-width bracket-name object selector
# (normalised to the bare name). The array selector ``[:]`` and the root are
# matched literally below. Everything else is rejected (§3).

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"  # identifier charset — object key
_DOT_NAME = re.compile(rf"\.({_NAME})")  # object comprehension (canonical)
_BRACKET_NAME = re.compile(r"\[(['\"])([^'\"]+)\1\]")  # bracket object name (full-width)

# Transport shape = array-outer JSON. The root container is ``$``; the sole data
# entry into it is the array selector, so ``$[:]`` is the canonical root prefix.
_ARRAY = "[:]"  # array comprehension — the only array selector
_ROOT = "$"  # the root container (not itself a data path)
_ROOT_ARRAY = "$[:]"  # the canonical (array-outer) data root


class _Seg(NamedTuple):
    """One output-path segment: a JSON key, and whether it iterates an array."""

    name: str
    is_array: bool


@dataclass(frozen=True)
class _ParsedPath:
    """A parsed output path (the ``[:]``-only conventional-JSONPath subset).

    ``segments`` are the keys after the root, each flagged where a ``[:]``
    selector iterates its value as an array. ``root_array`` records whether the
    document root itself is an array (``$[:]`` — the json document shape).
    """

    raw: str
    segments: tuple[_Seg, ...]
    root_array: bool


def parse_path(raw: str, error: _PathError) -> _ParsedPath:
    """Parse a path, rejecting every selector outside the accepted subset (§2/§3).

    Accepts the root ``$``/``$[:]``, dot name selectors (``.name``), bracketed
    name selectors (``['name']`` / ``["name"]``), and the whole-array selector
    ``[:]``. Rejects (PATH_GRAMMAR §3) index (``[0]``), range (``[0:5]``),
    filter (``[?(...)]``), descendant (``..``), and non-array wildcard (``.*``,
    ``[*]``) selectors — the dropped ``.:`` dot form included. Raises the
    injected ``error`` (a ``HauteError`` subclass) on anything else, with the
    offending path under ``output_path``.
    """
    if not raw.startswith(_ROOT):
        raise error("output path must start with '$'", output_path=raw)

    i = 1
    root_array = False
    if raw[i : i + len(_ARRAY)] == _ARRAY:
        root_array = True
        i += len(_ARRAY)

    segments: list[_Seg] = []
    while i < len(raw):
        ch = raw[i]
        if ch == ".":
            m = _DOT_NAME.match(raw, i)
            if m is None:
                raise error(
                    "unsupported output-path selector "
                    "(only '.name', \"['name']\" and whole-array '[:]' are accepted)",
                    output_path=raw,
                )
            name = m.group(1)
            i = m.end()
        elif ch == "[":
            m = _BRACKET_NAME.match(raw, i)
            if m is None:
                raise error(
                    "unsupported array selector "
                    "(index/range/filter/wildcard are rejected; use '[:]' for the whole array)",
                    output_path=raw,
                )
            name = m.group(2)
            i = m.end()
        else:
            raise error("malformed output path", output_path=raw)

        is_array = raw[i : i + len(_ARRAY)] == _ARRAY
        if is_array:
            i += len(_ARRAY)
        segments.append(_Seg(name, is_array))

    if not segments:
        raise error(
            "output path must name a leaf field, not the bare root array",
            output_path=raw,
        )
    return _ParsedPath(raw, tuple(segments), root_array)


def make_output_path(segments: tuple[_Seg, ...] | list[_Seg]) -> str:
    """The canonical writer — emit the one canonical spelling (§2.1).

    Renders ``$[:]`` root + ``.name`` per segment + ``[:]`` after each array
    segment. No bracket forms, no bare ``$`` data root: the output is canonical
    by construction (``is_canonical(make_output_path(segs))`` is always true).
    """
    out = _ROOT_ARRAY
    for seg in segments:
        out += f".{seg.name}"
        if seg.is_array:
            out += _ARRAY
    return out


def is_canonical(path: str) -> bool:
    """The canonicality predicate — true iff *path* uses only canonical spellings.

    Canonical (§2.1): ``$[:]`` root, ``.name`` object hops, ``[:]`` array hops;
    NO bracket forms, NO bare-``$`` data root. Equivalent to round-tripping
    through the canonical writer: a valid path is canonical iff re-emitting its
    parsed segments reproduces it verbatim, *and* it carries the canonical
    array-outer root. An unparseable (rejected) path is not canonical.
    """

    def _reject(message: str, **context: Any) -> Exception:
        return ValueError(message)

    try:
        parsed = parse_path(path, _reject)
    except ValueError:
        return False
    return parsed.root_array and make_output_path(parsed.segments) == path
