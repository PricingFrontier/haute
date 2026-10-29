"""Mutation-killing witness tests for the streaming root-JSON-array readers in
:mod:`haute._json_shred._records`:

* ``_iter_sampled_json_array_records(data_path, sample_size)``, reached through the
  public ``infer_v2_schema_from_data(...)`` entry point;
* ``_iter_root_array_values(f, pos, limit)``, the chunked scanner that finds each
  element of a root array by its string, escape and depth state.

Each test pins one observable rule a mutation of these readers would break.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import orjson
import pytest

from haute._json_shred._inference import infer_v2_schema_from_data
from haute._json_shred._records import _iter_root_array_values, _iter_sampled_json_array_records


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _write(tmp_path: Path, content: bytes, name: str = "data.json") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _sample(tmp_path: Path, content: bytes, n: int, name: str = "data.json"):
    return list(_iter_sampled_json_array_records(_write(tmp_path, content, name), n))


def _elements(body: bytes) -> list[Any]:
    """The elements of the root array whose bytes after its ``[`` are *body*."""
    return list(_iter_root_array_values(io.BytesIO(body), 1, 1 << 20))


# --------------------------------------------------------------------------- #
# _iter_sampled_json_array_records — top-level loop
# --------------------------------------------------------------------------- #
def test_line394_395_402_403_error_position_tracking(tmp_path: Path) -> None:
    """A truncated array's error position is the number of bytes read: `[{"a":1}`
    reads all 8 bytes, then raises "unexpected end of data" at 8."""
    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1}', 5)
    assert ei.value.msg == "unexpected end of data"
    assert ei.value.pos == 8


def test_pos_tracking_inside_value_reader(tmp_path: Path) -> None:
    """An element cut off mid-object reports the bytes read: `[{"a":1` raises
    "unexpected end of data" at 7."""
    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1', 5)
    assert ei.value.msg == "unexpected end of data"
    assert ei.value.pos == 7


def test_pos_tracking_trailing_comma_and_data(tmp_path: Path) -> None:
    """The trailing-comma and trailing-data errors report the exact byte offsets."""
    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1},]', 5)
    assert ei.value.msg == "trailing comma in array"
    assert ei.value.pos == 10

    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1}]X', 5)
    assert ei.value.msg == "unexpected trailing data"
    assert ei.value.pos == 9


def test_line395_expect_value_starts_false_empty_array(tmp_path: Path) -> None:
    """An empty array closes cleanly: no element is pending before its first `]`."""
    assert _sample(tmp_path, b"[]", 5) == []


@pytest.mark.timeout(10)
def test_line409_whitespace_skip_not_inverted(tmp_path: Path) -> None:
    """Leading whitespace is skipped, and a reader that stopped on whitespace instead
    would never return (the timeout turns that into a failure)."""
    assert _sample(tmp_path, b'[{"a":1}]', 5) == [{"a": 1}]
    # And leading whitespace IS skipped (real behaviour) — the array still parses.
    assert _sample(tmp_path, b'   [{"a":9}]', 5) == [{"a": 9}]


def test_line415_nonarray_root_object_is_delegated(tmp_path: Path) -> None:
    """A root object is read by the whole-document reader, which yields it intact."""
    assert _sample(tmp_path, b'{"a":1,"b":2}', 5) == [{"a": 1, "b": 2}]


def test_line415_nonarray_root_scalar_is_delegated(tmp_path: Path) -> None:
    """A root scalar is read by the whole-document reader and yields no record."""
    assert _sample(tmp_path, b"123", 5) == []


def test_line415_array_root_streams_not_delegates(tmp_path: Path) -> None:
    """A root array streams: a small sample stops before a malformed tail the
    whole-document reader would refuse."""
    out = _sample(tmp_path, b'[{"a":1},{"a":2},GARBAGE]', 2)
    assert out == [{"a": 1}, {"a": 2}]


def test_line424_438_sample_truncation_stops_early(tmp_path: Path) -> None:
    """The sample stops after exactly `sample_size` objects, never reading the
    malformed tail after them."""
    out = _sample(tmp_path, b'[{"a":1},{"a":2},{"a":3},GARBAGE]', 2)
    assert out == [{"a": 1}, {"a": 2}]


def test_line438_increment_is_exactly_one(tmp_path: Path) -> None:
    """The sample counts one per object, yielding exactly `sample_size` of them."""
    out = _sample(tmp_path, b'[{"a":1},{"a":2},{"a":3}]', 2)
    assert out == [{"a": 1}, {"a": 2}]
    assert len(out) == 2


def test_line428_close_bracket_eq_lt_empty_array(tmp_path: Path) -> None:
    """A `]` in the first element's place closes the empty array."""
    assert _sample(tmp_path, b"[]", 5) == []


def test_line428_close_bracket_eq_lte_scalar_first(tmp_path: Path) -> None:
    """A scalar first element is read whole and skipped; the object after it is
    yielded."""
    assert _sample(tmp_path, b'[123,{"a":1}]', 5) == [{"a": 1}]


def test_line428_close_bracket_eq_gt_object_first(tmp_path: Path) -> None:
    """An object first element is streamed, not mistaken for the array's close."""
    assert _sample(tmp_path, b'[{"a":1}]', 5) == [{"a": 1}]


def test_line439_expect_value_from_comma_delimiter(tmp_path: Path) -> None:
    """A comma before the closing bracket is a trailing comma and is refused."""
    with pytest.raises(orjson.JSONDecodeError) as ei:
        _sample(tmp_path, b'[{"a":1},]', 5)
    assert ei.value.msg == "trailing comma in array"


def test_line440_delimiter_close_eq_lt_gt(tmp_path: Path) -> None:
    """An element ended by `]` ends the array cleanly."""
    assert _sample(tmp_path, b'[{"a":1}]', 5) == [{"a": 1}]


def test_line440_delimiter_close_eq_lte_continues_on_comma(tmp_path: Path) -> None:
    """An element ended by `,` is followed by another: both objects are yielded."""
    out = _sample(tmp_path, b'[{"a":1},{"a":2}]', 5)
    assert out == [{"a": 1}, {"a": 2}]


# --------------------------------------------------------------------------- #
# _iter_root_array_values — depth / string / escape machine
# --------------------------------------------------------------------------- #
def test_an_object_element_ends_at_its_own_closing_brace() -> None:
    assert _elements(b'{"a":1},2]') == [{"a": 1}, 2]


def test_a_scalar_element_ends_at_the_closing_bracket() -> None:
    assert _elements(b"42]") == [42]


def test_a_string_element_keeps_its_commas_and_brackets() -> None:
    assert _elements(b'"a,b]c",1]') == ["a,b]c", 1]


def test_an_escaped_quote_opening_a_string_does_not_close_it() -> None:
    # bytes: " \ " x "  -> the \" is an escaped quote inside the one string
    assert _elements(b'"\\"x",1]') == ['"x', 1]


def test_an_escaped_quote_inside_a_string_does_not_close_it() -> None:
    assert _elements(b'"a\\"b",1]') == ['a"b', 1]


def test_a_closing_quote_ends_a_string_element() -> None:
    assert _elements(b'"xy",1]') == ["xy", 1]


def test_a_string_inside_an_object_hides_its_brackets() -> None:
    assert _elements(b'{"k":"a,b]}"},1]') == [{"k": "a,b]}"}, 1]


def test_a_nested_object_closes_before_its_element() -> None:
    assert _elements(b'{"a":{"b":2}},1]') == [{"a": {"b": 2}}, 1]


def test_a_top_level_comma_ends_an_element_but_an_inner_one_does_not() -> None:
    assert _elements(b"42,1]") == [42, 1]
    assert _elements(b'{"a":1,"b":2},1]') == [{"a": 1, "b": 2}, 1]


def test_whitespace_before_a_comma_is_not_a_delimiter() -> None:
    assert _elements(b"42 ,1]") == [42, 1]


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


def test_sampled_non_array_root_falls_back_to_the_full_reader(tmp_path) -> None:
    """A non-array root defers to ``_iter_records`` under the same sample cap."""
    records = _sample(tmp_path, b'{"id": 1}', 5, name="object.json")

    assert records == [{"id": 1}]


def test_sampled_reader_yields_nothing_for_an_empty_file(tmp_path) -> None:
    assert _sample(tmp_path, b"", 5, name="empty.json") == []


def test_sampled_array_rejects_truncated_input_between_records(tmp_path) -> None:
    with pytest.raises(orjson.JSONDecodeError, match="unexpected end of data"):
        _sample(tmp_path, b'[{"id": 1},', 5, name="truncated.json")
