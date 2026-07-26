"""The shared path-grammar core — canonical writer and data paths.

Direct unit coverage of :mod:`haute._jsonpath` (PATH_GRAMMAR.md): the OUTPUT
acceptance grammar (:func:`parse_path`) is exercised heavily through the
assembler tests, but the canonical writer (:func:`make_output_path`) and the
INPUT-mode data-path parser (:func:`parse_data_path` — ``allow_root`` and the ``$value`` reserved
leaf) need their own witnesses. These pin the grammar lynchpin so subtle branch
mutations in the single-sourced core are caught.
"""

from __future__ import annotations

from typing import Any

import pytest

from haute._jsonpath import (
    _Seg,
    is_identifier_name,
    make_output_path,
    parse_data_path,
    parse_path,
)


def _reject(message: str, **context: Any) -> Exception:
    """A neutral error factory for the injected-error parameter."""
    return ValueError(f"{message} {context}")


# ─── make_output_path — the canonical writer (§2.1) ───────────────────


def test_make_output_path_root_only() -> None:
    # Zero segments emit the bare canonical root array, nothing more.
    assert make_output_path(()) == "$[:]"
    assert make_output_path([]) == "$[:]"


def test_make_output_path_object_hops_get_no_array_suffix() -> None:
    # A non-array segment renders ``.name`` with NO trailing ``[:]``.
    assert make_output_path([_Seg("a", False)]) == "$[:].a"
    assert make_output_path([_Seg("a", False), _Seg("b", False)]) == "$[:].a.b"


def test_make_output_path_array_hops_get_array_suffix() -> None:
    # An array segment renders ``.name[:]`` — the ``is_array`` branch fires.
    assert make_output_path([_Seg("drivers", True)]) == "$[:].drivers[:]"
    assert make_output_path([_Seg("drivers", True), _Seg("name", False)]) == "$[:].drivers[:].name"


def test_make_output_path_mixed_array_and_object() -> None:
    # Each array segment — and only the array segments — gets the ``[:]`` suffix.
    segs = [_Seg("a", True), _Seg("b", False), _Seg("c", True), _Seg("d", False)]
    assert make_output_path(segs) == "$[:].a[:].b.c[:].d"


# ─── parse_data_path — INPUT mode: allow_root and the $value sentinel ──


def test_parse_data_path_plain_array_outer() -> None:
    p = parse_data_path("$[:].a.b", _reject)
    assert p.segments == (_Seg("a", False), _Seg("b", False))


def test_parse_data_path_allow_root_accepts_bare_root_array() -> None:
    p = parse_data_path("$[:]", _reject, allow_root=True)
    assert p.segments == ()


def test_parse_data_path_default_allow_root_is_false() -> None:
    # The DEFAULT allow_root is False — exercised WITHOUT passing the kwarg, so a
    # flipped default (False -> True) is caught: a bare root naming no leaf must
    # reject by default, yet be accepted when allow_root is explicitly True. The
    # two answers differ, so the default genuinely matters.
    with pytest.raises(ValueError):
        parse_data_path("$[:]", _reject)
    accepted = parse_data_path("$[:]", _reject, allow_root=True)
    assert accepted.segments == ()


def test_parse_data_path_reserved_leaf_as_trailing_hop() -> None:
    # ``$value`` is peeled before the identifier-pure parse, then re-appended as
    # a final NON-array object segment.
    p = parse_data_path("$[:].a.$value", _reject, reserved_leaf="$value")
    assert p.segments == (_Seg("a", False), _Seg("$value", False))
    assert p.segments[-1].is_array is False


def test_parse_data_path_reserved_leaf_directly_on_root() -> None:
    # ``$[:].$value`` — the sentinel sits on the root array, naming a leaf even
    # without allow_root.
    p = parse_data_path("$[:].$value", _reject, reserved_leaf="$value", allow_root=False)
    assert p.segments == (_Seg("$value", False),)


def test_parse_data_path_reserved_leaf_only_peeled_when_configured() -> None:
    # Without reserved_leaf set, a ``.$value`` tail is NOT special and the
    # non-identifier ``$`` fails the dotted-name parse.
    with pytest.raises(ValueError):
        parse_data_path("$[:].a.$value", _reject)


@pytest.mark.parametrize(
    "bad",
    [
        "$.a",  # object-outer root — a different transport
        "$.a[:].b",  # non-array root with a deeper array
    ],
)
def test_parse_data_path_rejects_non_array_root(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_data_path(bad, _reject)


def test_parse_path_rejects_non_root_start() -> None:
    # The very first gate: a path not starting with '$' is rejected outright.
    with pytest.raises(ValueError):
        parse_path("drivers[:].name", _reject)


@pytest.mark.parametrize("name", ["a", "_id", "field2", "A_B"])
def test_identifier_name_accepts_exact_path_grammar_names(name: str) -> None:
    assert is_identifier_name(name) is True


@pytest.mark.parametrize("name", ["", "2field", "a-b", "a.b", "#id", "white space"])
def test_identifier_name_rejects_unaddressable_names(name: str) -> None:
    assert is_identifier_name(name) is False


# ─── _ParsedPath is an immutable value object (frozen dataclass) ───────


def test_parsed_path_is_frozen() -> None:
    # The parsed path is a frozen value object — reassigning a field raises. This
    # pins the frozen=True contract (a mutable parse result would let a consumer
    # silently corrupt a cached/shared path).
    p = parse_path("$[:].a", _reject)
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        p.raw = "tampered"  # type: ignore[misc]


# ─── Rejection-message routing — each malformed shape hits its own branch ──
#
# parse_path dispatches a leading char to the dot-name branch ('.') or the
# malformed fall-through. The exact message a
# given malformed path triggers is the user-facing grammar contract
# (PATH_GRAMMAR.md). Pinning the message — not merely "it raises" — catches the
# comparison-operator mutants that reroute a malformed char to the wrong branch.
# Both branches still raise, so a bare
# `pytest.raises` survives the mutation; the message does not.


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        # The canonical root prefix is mandatory.
        ("$zzz", "output path must start"),
        # A char other than '.' at a selector position is malformed.
        ("$[:]-x", "malformed output path"),
        ("$[:]]x", "malformed output path"),
        # A dot followed by a non-identifier → the dot branch's own rejection.
        ("$[:].5", "unsupported output-path selector"),
        ("$[:].a[?(x)]", "malformed output path"),
    ],
)
def test_parse_path_rejection_messages_are_branch_specific(bad: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_path(bad, _reject)
