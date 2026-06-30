"""Mutation-killing witness tests for the streaming root-JSON-array sampler in
:mod:`haute._json_shred`.

Targets the inference-only streaming reader:

* ``_iter_sampled_json_array_records(data_path, sample_size)``  (~lines 383-442)
* ``_read_root_array_value(first, read_byte, current_pos)``     (~lines 445-493)

reachable via the public ``infer_v2_schema_from_data(...)`` entry point.

Each test is written to FAIL under a specific Cosmic Ray survivor mutation and
PASS on the real implementation. Survivor line numbers (Cosmic Ray start_pos_row)
are noted inline.

Note on ``Eq -> Is`` survivors on single-byte reads (lines 428, 453, 465, 467,
471, the ``Is`` arm of 415, the ``Is`` arms of 439/440/486): ``f.read(1)``
returns CPython's cached single-byte ``bytes`` singleton, so ``b is b'x'`` is
identity-true exactly when ``b == b'x'``. Those ``Is`` mutations are therefore
behaviourally equivalent (killing them would rely on a non-portable read-buffer
warm-up artifact). They are reported EQUIVALENT in the structured output.
"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from haute._json_shred import (
    _iter_sampled_json_array_records,
    _read_root_array_value,
    infer_v2_schema_from_data,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _write(tmp_path: Path, content: bytes, name: str = "data.json") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _sample(tmp_path: Path, content: bytes, n: int, name: str = "data.json"):
    return list(_iter_sampled_json_array_records(_write(tmp_path, content, name), n))


def _read_value(content: bytes) -> tuple[bytes, bytes]:
    """Drive ``_read_root_array_value`` over an in-memory byte buffer."""
    idx = [0]

    def read_byte() -> bytes:
        if idx[0] >= len(content):
            return b""
        ch = content[idx[0] : idx[0] + 1]
        idx[0] += 1
        return ch

    first = read_byte()
    return _read_root_array_value(first, read_byte, lambda: idx[0])


# --------------------------------------------------------------------------- #
# _iter_sampled_json_array_records — top-level loop
# --------------------------------------------------------------------------- #
def test_line394_395_402_403_error_position_tracking(tmp_path: Path) -> None:
    """Lines 394 (pos=0), 402 (`if b:`), 403 (pos+=1): the reported error
    position is exactly the number of consumed bytes.

    `[{"a":1}` is a truncated array: the reader consumes all 8 bytes then hits
    EOF and raises "unexpected end of data" with pos == 8. Any NumberReplacer on
    the pos seed (394) or the increment (403), or AddNot on the `if b:` guard
    (402, which gates the increment), shifts this position away from 8.
    """
    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1}', 5)
    assert ei.value.msg == "unexpected end of data"
    assert ei.value.pos == 8


def test_pos_tracking_inside_value_reader(tmp_path: Path) -> None:
    """Lines 459/current_pos path + pos seed (394) / increment (403): a value
    truncated mid-object surfaces pos == 7 through ``current_pos()``.

    `[{"a":1` consumes 7 bytes before EOF inside ``_read_root_array_value``.
    """
    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1', 5)
    assert ei.value.msg == "unexpected end of data"
    assert ei.value.pos == 7


def test_pos_tracking_trailing_comma_and_data(tmp_path: Path) -> None:
    """Reinforces pos tracking (394/402/403) at two further error sites whose
    positions are sensitive to every consumed byte.
    """
    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1},]', 5)
    assert ei.value.msg == "trailing comma in array"
    assert ei.value.pos == 10

    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1}]X', 5)
    assert ei.value.msg == "unexpected trailing data"
    assert ei.value.pos == 9


def test_line395_expect_value_starts_false_empty_array(tmp_path: Path) -> None:
    """Line 395 `expect_value = False` (FalseWithTrue).

    For an EMPTY array `[]`, the first non-ws after `[` is `]`. With the real
    seed (False) there is no pending value, so the array closes cleanly and
    yields []. If the seed were True, line 429 fires and raises
    "trailing comma in array".
    """
    assert _sample(tmp_path, b"[]", 5) == []


@pytest.mark.timeout(10)
def test_line409_whitespace_skip_not_inverted(tmp_path: Path) -> None:
    """Line 409 `if not b or b not in b" \\t\\r\\n":` (AddNot).

    AddNot inverts the whole stop condition, so ``_read_non_ws`` would loop on
    NON-whitespace bytes and only "stop" on whitespace/EOF — and at EOF the
    inverted condition is ALSO false, so the mutant spins forever reading empty
    bytes (infinite loop). The ``@pytest.mark.timeout`` turns that hang into a
    deterministic failure; the real reader returns the object instantly.
    """
    assert _sample(tmp_path, b'[{"a":1}]', 5) == [{"a": 1}]
    # And leading whitespace IS skipped (real behaviour) — the array still parses.
    assert _sample(tmp_path, b'   [{"a":9}]', 5) == [{"a": 9}]


def test_line415_nonarray_root_object_is_delegated(tmp_path: Path) -> None:
    """Line 415 `if first != b"[":` — NotEq -> Lt arm.

    A root OBJECT `{...}` (first byte `{` == 0x7b) must be delegated to the
    whole-file parser, which yields the single object. Under NotEq->Lt
    (`first < b"["`), `b"{" < b"[" ` is False, so the mutant would try to STREAM
    the bare object as if it were a root array and mangle/raise instead of
    yielding the intact dict.
    """
    assert _sample(tmp_path, b'{"a":1,"b":2}', 5) == [{"a": 1, "b": 2}]


def test_line415_nonarray_root_scalar_is_delegated(tmp_path: Path) -> None:
    """Line 415 `if first != b"[":` — NotEq -> Gt arm.

    A root NUMBER (first byte `1` == 0x31, which is < 0x5b) must be delegated;
    the whole-file parser sees a non-dict root and yields nothing -> []. Under
    NotEq->Gt (`first > b"["`), `b"1" > b"[" ` is False, so the mutant would try
    to stream `123` as a root array and raise instead of returning [].
    """
    assert _sample(tmp_path, b"123", 5) == []


def test_line415_array_root_streams_not_delegates(tmp_path: Path) -> None:
    """Line 415 — positive direction: a `[`-root with a malformed TAIL and a
    small sample STREAMS (stopping early, never seeing the tail) rather than
    delegating to the whole-file parser (which would raise on the tail).

    Guards the NotEq comparison from collapsing into a form that delegates on
    `[` (e.g. an always-true mutant).
    """
    out = _sample(tmp_path, b'[{"a":1},{"a":2},GARBAGE]', 2)
    assert out == [{"a": 1}, {"a": 2}]


def test_line424_438_sample_truncation_stops_early(tmp_path: Path) -> None:
    """Line 438 `yielded += 1` (NumberReplacer -> += 0).

    With `+= 0`, yielded never reaches sample_size, so the loop keeps consuming
    objects past the cap and runs into the malformed tail -> raises. The real
    reader stops after exactly `sample_size` objects and never reads GARBAGE.
    """
    out = _sample(tmp_path, b'[{"a":1},{"a":2},{"a":3},GARBAGE]', 2)
    assert out == [{"a": 1}, {"a": 2}]


def test_line438_increment_is_exactly_one(tmp_path: Path) -> None:
    """Line 438 `yielded += 1` (NumberReplacer -> += 2).

    With `+= 2`, yielded jumps to 2 after a single object and the loop stops one
    object early. The real reader yields exactly `sample_size` objects.
    """
    out = _sample(tmp_path, b'[{"a":1},{"a":2},{"a":3}]', 2)
    assert out == [{"a": 1}, {"a": 2}]
    assert len(out) == 2


def test_line428_close_bracket_eq_lt_empty_array(tmp_path: Path) -> None:
    """Line 428 `if first == b"]":` — Eq -> Lt arm.

    Empty array `[]`: the first value-slot byte is `]`. Real `]==]` closes and
    returns []. Under Eq->Lt (`first < b"]"`), `]<]` is False, so the mutant
    feeds `]` to the value reader, which hits EOF and raises.
    """
    assert _sample(tmp_path, b"[]", 5) == []


def test_line428_close_bracket_eq_lte_scalar_first(tmp_path: Path) -> None:
    """Line 428 `if first == b"]":` — Eq -> LtE and Eq -> Gt arms.

    `[123,{"a":1}]`: the first value byte is `1` (0x31). Real `1==]` is False, so
    `123` is read as a (skipped) scalar and the object is yielded -> [{"a":1}].
    Under Eq->LtE (`first <= b"]"`), `1 <= ]` is True, so the mutant treats the
    very first byte as the array close and then chokes on the trailing
    `23,{"a":1}]` -> raises. (Eq->Gt is killed by the object-first array below.)
    """
    assert _sample(tmp_path, b'[123,{"a":1}]', 5) == [{"a": 1}]


def test_line428_close_bracket_eq_gt_object_first(tmp_path: Path) -> None:
    """Line 428 `if first == b"]":` — Eq -> Gt arm.

    `[{"a":1}]`: first value byte is `{` (0x7b). Real `{==]` is False -> the
    object is streamed and yielded. Under Eq->Gt (`first > b"]"`), `{ > ]` is
    True, so the mutant treats the object's opening brace as the array close and
    then raises on the trailing object text.
    """
    assert _sample(tmp_path, b'[{"a":1}]', 5) == [{"a": 1}]


def test_line439_expect_value_from_comma_delimiter(tmp_path: Path) -> None:
    """Line 439 `expect_value = delimiter == b","` (Eq -> Lt / Eq -> Gt).

    A genuine trailing comma `[{"a":1},]` must raise "trailing comma in array":
    after the object the delimiter is `,`, so `delimiter == b","` is True and the
    subsequent `]` is flagged. Under Eq->Lt (`,<,`=False) or Eq->Gt (`,>,`=False)
    expect_value would be False and the trailing comma would be silently
    accepted (returning [{"a":1}]) instead of raising.
    """
    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1},]', 5)
    assert ei.value.msg == "trailing comma in array"


def test_line440_delimiter_close_eq_lt_gt(tmp_path: Path) -> None:
    """Line 440 `if delimiter == b"]":` — Eq -> Lt and Eq -> Gt arms.

    `[{"a":1}]`: after the object the delimiter is `]`. Real `]==]` returns
    cleanly -> [{"a":1}]. Under Eq->Lt (`]<]`=False) or Eq->Gt (`]>]`=False) the
    reader fails to terminate, loops, hits EOF and raises.
    """
    assert _sample(tmp_path, b'[{"a":1}]', 5) == [{"a": 1}]


def test_line440_delimiter_close_eq_lte_continues_on_comma(tmp_path: Path) -> None:
    """Line 440 `if delimiter == b"]":` — Eq -> LtE arm.

    `[{"a":1},{"a":2}]`: after the FIRST object the delimiter is `,` (0x2c).
    Real `,==]` is False, so the loop continues and yields BOTH objects. Under
    Eq->LtE (`, <= ]` is True) the mutant would return after the first object,
    yielding only [{"a":1}].
    """
    out = _sample(tmp_path, b'[{"a":1},{"a":2}]', 5)
    assert out == [{"a": 1}, {"a": 2}]


# --------------------------------------------------------------------------- #
# _read_root_array_value — depth / string / escape machine
# --------------------------------------------------------------------------- #
def test_line452_depth_seed_object_element(tmp_path: Path) -> None:
    """Line 452 `depth = 1 if first in {b"{", b"["} else 0` — the `1` seed
    (NumberReplacer -> 0).

    An object element `{"a":1},` opens with `{` so depth must seed to 1. With a
    seed of 0 the matching `}` underflows the close-bracket logic
    (`depth > 0` False -> "unexpected '}'"). The real reader returns the full
    object and the `,` delimiter.
    """
    assert _read_value(b'{"a":1},') == (b'{"a":1}', b",")


def test_line452_depth_seed_scalar_element(tmp_path: Path) -> None:
    """Line 452 `depth = ... else 0` — the `0` seed (NumberReplacer -> 1/2).

    A scalar element `42]` opens with `4`, which is NOT a bracket, so depth must
    seed to 0; the trailing `]` is recognised as the top-level close and
    returned. A non-zero seed would consume that `]` as a nested close, run off
    the end and raise.
    """
    assert _read_value(b"42]") == (b"42", b"]")


def test_line453_in_string_seed_string_element(tmp_path: Path) -> None:
    """Line 453 `in_string = first == b'"'` (FalseWithTrue-adjacent / Eq seed).

    A string element `"a,b]c",` opens with `"`, so in_string must seed True and
    the interior `,` and `]` must be ignored as content. If in_string seeded
    False the first interior `,` (at depth 0) would terminate the value early,
    truncating it to `"a`.
    """
    assert _read_value(b'"a,b]c",') == (b'"a,b]c"', b",")


def test_line454_escaped_seed_false(tmp_path: Path) -> None:
    """Line 454 `escaped = False` (FalseWithTrue).

    With `escaped` seeded True, the FIRST byte inside a string would be treated
    as an escaped char, so a leading backslash would not arm escaping and the
    following quote would terminate the string prematurely. Use a string element
    whose first interior byte is a backslash escaping a quote: `"\\"x"`,. The
    real seed (False) reads the whole `"\\"x"` token; a True seed would close the
    string at the escaped quote and mis-split the value.
    """
    # bytes: " \ " x "  ,  -> the \" is an escaped quote, value is the 5-byte token
    assert _read_value(b'"\\"x",') == (b'"\\"x"', b",")


def test_line465_backslash_arms_escape(tmp_path: Path) -> None:
    """Line 465 `elif b == b"\\\\":` (escape arming).

    Inside a string, a backslash must arm `escaped` so the NEXT quote is content,
    not a terminator. `"a\\"b",` carries an escaped quote mid-string; if the
    backslash branch were broken the string would close at the escaped quote and
    the value would mis-split. Real value is the full `"a\\"b"` token.
    """
    assert _read_value(b'"a\\"b",') == (b'"a\\"b"', b",")


def test_line467_closing_quote_ends_string(tmp_path: Path) -> None:
    """Line 467 `elif b == b'"':` (string close) and line 471 (string open).

    The closing quote must end the string so the trailing `,` becomes the
    delimiter rather than string content. `"xy",` -> value `"xy"`, delim `,`.
    Combined with a re-opened string to also exercise line 471: `{"k":"v"},`.
    """
    assert _read_value(b'"xy",') == (b'"xy"', b",")


def test_line471_quote_opens_string_inside_object(tmp_path: Path) -> None:
    """Line 471 `if b == b'"':` (string OPEN at depth>0).

    Inside an object value, a `"` must OPEN a string so that interior `,` `]` `}`
    are treated as content. `{"k":"a,b]}"},` packs all three structural bytes
    inside the string; if the quote failed to open the string the first interior
    `,` would be read as a structural delimiter and corrupt depth tracking.
    Real reader returns the whole object.
    """
    assert _read_value(b'{"k":"a,b]}"},') == (b'{"k":"a,b]}"}', b",")


def test_line482_depth_gt_zero_object_close(tmp_path: Path) -> None:
    """Line 482 `if depth > 0:` — Gt -> NumberReplacer on the `0` (`depth > 1`)
    and the AddNot/Gt direction.

    A nested object `{"a":{"b":2}},` relies on `depth > 0` being True for BOTH
    inner closes (`}` at depth 2 then depth 1). Mutating the threshold up to 1
    makes the depth-1 close fall through to the top-level-close branch and raise
    "unexpected '}'". The real reader returns the whole nested object.
    """
    assert _read_value(b'{"a":{"b":2}},') == (b'{"a":{"b":2}}', b",")


def test_line486_top_level_close_bracket(tmp_path: Path) -> None:
    """Line 486 `if b == b"]":` (AddNot, Eq -> Lt, Eq -> Gt).

    A scalar element terminated by the top-level `]` (`42]`) reaches line 486 at
    depth 0. Real `]==]` returns the value and the `]` delimiter. AddNot
    (`not (b==b"]")`) or Eq->Lt (`]<]`=False) or Eq->Gt (`]>]`=False) would all
    fall through to the `raise "unexpected '}'"` instead of returning.
    """
    assert _read_value(b"42]") == (b"42", b"]")


def test_line490_depth_zero_comma_terminates(tmp_path: Path) -> None:
    """Line 490 `if depth == 0 and b in {b",", b"]"}:` (the comma path).

    A scalar element terminated by a top-level comma (`42,`) must return at the
    comma. This exercises the depth==0 structural-delimiter branch directly:
    real returns (`42`, `,`). (A nested comma must NOT terminate — covered by the
    object/nested tests above, where interior commas are consumed as content.)
    """
    assert _read_value(b"42,") == (b"42", b",")
    # Interior comma at depth>0 is NOT a terminator (guards the `depth == 0` guard):
    assert _read_value(b'{"a":1,"b":2},') == (b'{"a":1,"b":2}', b",")


# --------------------------------------------------------------------------- #
# end-to-end through the public entry point
# --------------------------------------------------------------------------- #
def test_infer_schema_streams_sampled_root_array(tmp_path: Path) -> None:
    """Smoke test that the public ``infer_v2_schema_from_data`` reaches the
    streaming sampler for a `.json` root array and that sampling caps the scan
    (type widening stops at the sampled prefix).
    """
    p = _write(tmp_path, b'[{"a":1},{"a":2},{"a":3.5}]', "doc.json")
    # sample_size=2 sees only ints -> column type int
    schema = infer_v2_schema_from_data(p, sample_size=2)
    cols = schema["tables"][0]["columns"]
    (col,) = cols
    assert col["path"] == "$[:].a"
    assert col["type"] == "int"
    # full scan widens to float
    schema_full = infer_v2_schema_from_data(p, sample_size=10)
    assert schema_full["tables"][0]["columns"][0]["type"] == "float"
