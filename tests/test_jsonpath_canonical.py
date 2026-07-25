"""The shared path-grammar core — canonical writer, predicate, and data paths.

Direct unit coverage of :mod:`haute._jsonpath` (PATH_GRAMMAR.md): the OUTPUT
acceptance grammar (:func:`parse_path`) is exercised heavily through the
assembler tests, but the canonical writer (:func:`make_output_path`), the
canonicality predicate (:func:`is_canonical`), and the INPUT-mode data-path
parser (:func:`parse_data_path` — ``allow_root`` and the ``$value`` reserved
leaf) need their own witnesses. These pin the grammar lynchpin so subtle branch
mutations in the single-sourced core are caught.
"""

from __future__ import annotations

from typing import Any

import pytest

from haute._jsonpath import (
    _Seg,
    is_canonical,
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


# ─── is_canonical — the canonicality predicate (§2.1) ─────────────────


@pytest.mark.parametrize(
    "path",
    [
        "$[:].a",
        "$[:].a.b",
        "$[:].drivers[:].name",
        "$[:].a[:].b.c[:].d",
    ],
)
def test_is_canonical_true_for_canonical_spellings(path: str) -> None:
    assert is_canonical(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "$[:]['a']",  # bracket name — re-emits as $[:].a, so not canonical
        '$[:]["drivers"][:].name',  # bracket name in an array path
        "$.a",  # object-outer root — root_array is false
        "$['a']",  # bracket object root, still no canonical array root
    ],
)
def test_is_canonical_false_for_noncanonical_but_parseable(path: str) -> None:
    # Parses (an accepted selector) but is not the canonical spelling.
    assert is_canonical(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "$[:].a[0]",  # index selector — rejected, hence not canonical
        "$[:].a[*]",  # array wildcard — rejected
        "not_a_path",  # no root
        "$[:]",  # names no leaf — parse_path rejects
    ],
)
def test_is_canonical_false_for_unparseable(path: str) -> None:
    # An unparseable (rejected) path is never canonical — the except path.
    assert is_canonical(path) is False


def test_is_canonical_round_trips_with_make_output_path() -> None:
    # is_canonical(make_output_path(segs)) is true for any path that names a
    # leaf — the empty-segment root "$[:]" is excluded because it names no leaf
    # and is_canonical rejects it via parse_path (it is not an output path).
    for segs in (
        (_Seg("a", False),),
        (_Seg("drivers", True), _Seg("name", False)),
        (_Seg("a", True), _Seg("b", False), _Seg("c", True)),
    ):
        assert is_canonical(make_output_path(segs)) is True


# ─── parse_data_path — INPUT mode: allow_root and the $value sentinel ──


def test_parse_data_path_plain_array_outer() -> None:
    p = parse_data_path("$[:].a.b", _reject)
    assert p.root_array is True
    assert p.segments == (_Seg("a", False), _Seg("b", False))


def test_parse_data_path_allow_root_accepts_bare_root_array() -> None:
    # With allow_root the bare ``$[:]`` / ``$`` is the root table level: zero
    # segments, root_array true. parse_path alone rejects a segment-less path.
    for raw in ("$[:]", "$"):
        p = parse_data_path(raw, _reject, allow_root=True)
        assert p.segments == ()
        assert p.root_array is True


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
    assert p.root_array is True


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
        "$['a']",  # bracket object root, not the array root
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


@pytest.mark.parametrize("path", ["$[:]['a.b']", '$[:]["a.b"]'])
def test_bracket_selector_cannot_smuggle_dotted_key(path: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        parse_path(path, _reject)


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
# parse_path dispatches a leading char to the dot-name branch ('.'), the
# bracket branch ('['), or the malformed fall-through. The exact message a
# given malformed path triggers is the user-facing grammar contract
# (PATH_GRAMMAR.md). Pinning the message — not merely "it raises" — catches the
# comparison-operator mutants that reroute a malformed char to the wrong branch
# (e.g. '==' -> '<=' / '>=' on the char dispatch, '==' -> '>=' on the root- and
# array-selector slice checks). Both branches still raise, so a bare
# `pytest.raises` survives the mutation; the message does not.


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        # Leading char neither '.' nor '[' nor a root array → malformed. A '>='
        # mutant on the root-array slice check (L~109) would instead treat it as
        # the root array and report the leaf message.
        ("$zzz", "malformed output path"),
        # A char strictly below '.' at a selector position (no dot, no bracket)
        # → malformed. A '<=' mutant on the dot dispatch would misroute it to the
        # dot branch ("unsupported output-path selector"); a '<=' mutant on the
        # bracket dispatch would misroute it to the bracket branch ("unsupported
        # array selector").
        ("$[:]-x", "malformed output path"),
        # A char strictly above '[' at a selector position → malformed. A '>='
        # mutant on the bracket dispatch would misroute it to the bracket branch.
        ("$[:]]x", "malformed output path"),
        # A dot followed by a non-identifier → the dot branch's own rejection.
        ("$[:].5", "unsupported output-path selector"),
        # A filter selector after a segment → the bracket branch's rejection. A
        # '>=' mutant on the array-selector slice check (L~139) would treat
        # '[?(' as an array hop and fall through to malformed instead.
        ("$[:].a[?(x)]", "unsupported array selector"),
    ],
)
def test_parse_path_rejection_messages_are_branch_specific(bad: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_path(bad, _reject)
