"""How Haute reads the bytes of an input's file (``haute._input_files``)."""

from __future__ import annotations

import math
from pathlib import Path

import orjson
import pytest

from haute._input_files import (
    UTF8_BOM,
    file_position,
    load_json_file,
    loads_keeping_first,
    text_encoding_refusal,
)
from haute.errors import InputFileError

# --------------------------------------------------------------------------- repeated keys


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        (b'{"region": "north", "region": "south"}', {"region": "north"}),
        (b'[{"a": {"b": 1, "b": 2}}, {"a": 3}]', [{"a": {"b": 1}}, {"a": 3}]),
        (b'{"na\\u006de": "first", "name": "second"}', {"name": "first"}),
        # A colon inside a string is no member: nothing repeats.
        (b'{"time": "10:30", "url": "a:b:c"}', {"time": "10:30", "url": "a:b:c"}),
        (b'{"time": "10:30", "id": 1, "id": 2}', {"time": "10:30", "id": 1}),
        # An escaped colon is written back as one, so the native check cannot judge it.
        (b'{"t": "10\\u003a30", "id": 1, "id": 2}', {"t": "10:30", "id": 1}),
    ],
    ids=["flat", "nested", "escaped", "colons-in-strings", "colons-and-a-repeat", "escaped-colon"],
)
def test_a_repeated_key_keeps_its_first_value(raw: bytes, value: object) -> None:
    """IOL-02: as Polars reads a Data Input's JSON, never orjson's last value."""
    assert loads_keeping_first(raw) == value


def test_the_slower_parse_of_a_repeated_key_reads_numbers_as_orjson_does() -> None:
    raw = b'{"a": 1, "a": 2, "big": 123456789012345678901234567890, "max": 18446744073709551615}'

    value = loads_keeping_first(raw)

    assert value == {"a": 1, "big": orjson.loads(raw)["big"], "max": 18446744073709551615}
    assert isinstance(value["big"], float)


def test_text_that_is_not_json_is_refused_as_orjson_refuses_it() -> None:
    with pytest.raises(orjson.JSONDecodeError, match="trailing comma"):
        loads_keeping_first(b'{"a": 1,}')


# --------------------------------------------------------------------------- positions


def test_a_byte_is_placed_by_its_line_and_its_column_in_characters(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_bytes('{"a": 1,\n "é": x}'.encode())

    # The `x` is byte 16: the é is two bytes and one character.
    assert file_position(path, 16) == (2, 7)


def test_a_leading_byte_order_mark_is_no_column(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_bytes(UTF8_BOM + b'{"a": x}')

    assert file_position(path, len(UTF8_BOM) + 6) == (1, 7)


def test_a_fault_far_into_a_large_file_is_placed_across_its_chunks(tmp_path: Path) -> None:
    path = tmp_path / "data.jsonl"
    line = b'{"id": 1, "pad": "' + b"x" * 1000 + b'"}\n'
    path.write_bytes(line * 3000 + b'{"id": ')

    assert file_position(path, len(line) * 3000 + 7) == (3001, 8)


# --------------------------------------------------------------------------- encodings


@pytest.mark.parametrize(
    ("encoding", "found"),
    [
        ("utf-16", "UTF-16 (little-endian)"),
        ("utf-16-le", "UTF-16 (little-endian)"),
        ("utf-16-be", "UTF-16 (big-endian)"),
        ("utf-32", "UTF-32 (little-endian)"),
        ("utf-32-be", "UTF-32 (big-endian)"),
    ],
)
def test_a_file_in_another_encoding_is_refused_naming_it(
    tmp_path: Path, encoding: str, found: str
) -> None:
    """IOL-04: with its byte-order mark or without one, by the null bytes of its header."""
    path = tmp_path / "quotes.csv"
    path.write_bytes("row_id,value\n1,a\n".encode(encoding))

    refused = text_encoding_refusal(path)

    assert refused is not None
    assert refused.message == f"quotes.csv is saved as {found}, not UTF-8. Save it as UTF-8."
    assert refused.file == "quotes.csv"


@pytest.mark.parametrize("mark", [b"", UTF8_BOM], ids=["plain", "byte-order-mark"])
def test_utf8_text_is_not_refused(tmp_path: Path, mark: bytes) -> None:
    path = tmp_path / "quotes.csv"
    path.write_bytes(mark + "row_id,région\n1,a\n".encode())

    assert text_encoding_refusal(path) is None


def test_null_bytes_in_the_header_of_no_known_encoding_are_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "quotes.csv"
    path.write_bytes(b"row_id,va\x00\x00lue\n1,a\n")

    refused = text_encoding_refusal(path)

    assert refused is not None
    assert refused.message == (
        "quotes.csv is not UTF-8 text: its first line holds null bytes. Save it as UTF-8."
    )


# --------------------------------------------------------------------------- Load File's JSON


def test_a_json_file_that_is_not_utf8_is_refused_naming_its_byte(tmp_path: Path) -> None:
    """IOL-05: the byte is never replaced, so no value changes without a word."""
    path = tmp_path / "lookup.json"
    path.write_bytes('{"name": "Crème"}'.encode("latin-1"))

    with pytest.raises(InputFileError) as refused:
        load_json_file(path)

    assert refused.value.message == (
        "lookup.json is not valid UTF-8: byte 0xE8 at line 1, column 13. Save it as UTF-8."
    )
    assert (refused.value.line, refused.value.column) == (1, 13)


def test_a_json_file_reads_as_written_through_its_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "lookup.json"
    path.write_bytes(UTF8_BOM + '{"name": "Crème", "name": "x", "rate": NaN}'.encode())

    loaded = load_json_file(path)

    assert loaded["name"] == "Crème"
    assert math.isnan(loaded["rate"])


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "lookup.json is empty, so it is not JSON."),
        (
            '{"a": 1,\n "b": }',
            "lookup.json is not valid JSON: Expecting value at line 2, column 7.",
        ),
    ],
    ids=["empty", "not-json"],
)
def test_a_json_file_that_is_not_json_is_refused_with_its_place(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "lookup.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(InputFileError) as refused:
        load_json_file(path)

    assert refused.value.message == message


# --------------------------------------------------------------------------- one rule, every reader


class _Request:
    """A served request: its headers and its body, in one chunk."""

    def __init__(self, body: bytes) -> None:
        self.headers: dict[str, str] = {}
        self._body = body

    async def stream(self):  # type: ignore[no-untyped-def]
        yield self._body


def test_every_reader_of_a_json_input_keeps_a_repeated_keys_first_value(tmp_path: Path) -> None:
    """IOL-02: a Data Input (through Polars), the Quote Input's shredder, its served
    request and Load File read one record alike, and none refuses it."""
    import asyncio

    from haute._json_shred._records import _iter_records
    from haute._polars_io_registry import read_polars_input
    from haute.deploy._request_limits import read_limited_json_body

    record = b'{"quote_id": "q1", "region": "north", "region": "south"}'
    as_json = tmp_path / "quotes.json"
    as_json.write_bytes(b"[" + record + b"]")
    as_jsonl = tmp_path / "quotes.jsonl"
    as_jsonl.write_bytes(record + b"\n")

    regions = {
        "Data Input, JSON": read_polars_input(
            {"format": "json", "mode": "read", "path": str(as_json)}
        )
        .collect()["region"]
        .to_list(),
        "Data Input, JSON Lines": read_polars_input(
            {"format": "ndjson", "mode": "scan", "path": str(as_jsonl)}
        )
        .collect()["region"]
        .to_list(),
        "Quote Input, JSON": [row["region"] for row in _iter_records(as_json)],
        "Quote Input, JSON Lines": [row["region"] for row in _iter_records(as_jsonl)],
        "served request": [
            row["region"]
            for row in asyncio.run(
                read_limited_json_body(
                    _Request(b"[" + record + b"]"), operation="deploy_quote", limit_bytes=1 << 20
                )
            )
        ],
        "Load File": [row["region"] for row in load_json_file(as_json)],
    }

    assert regions == dict.fromkeys(regions, ["north"])


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("quotes.json", b'[{"id": 1}, {"id": 2}]'),
        ("quotes.json", b'{"id": 1}'),
        ("quotes.jsonl", b'{"id": 1}\n{"id": 2}\n'),
        ("quotes.xml", b"<quotes><quote><id>1</id></quote><quote><id>2</id></quote></quotes>"),
    ],
    ids=["json-array", "json-object", "jsonl", "xml"],
)
def test_a_request_file_with_a_byte_order_mark_reads_as_one_without(
    tmp_path: Path, name: str, body: bytes
) -> None:
    """IOL-02: feeds and Windows tools write the mark; it is not the analyst's to remove."""
    from haute._json_shred._records import _iter_records

    plain = tmp_path / "plain" / name
    marked = tmp_path / "marked" / name
    plain.parent.mkdir()
    marked.parent.mkdir()
    plain.write_bytes(body)
    marked.write_bytes(UTF8_BOM + body)

    assert list(_iter_records(marked)) == list(_iter_records(plain))


def test_an_empty_json_lines_file_holds_no_quotes(tmp_path: Path) -> None:
    """IOL-02: JSON Lines has no ``[]``; an empty ``.json`` file is refused instead."""
    from haute._json_shred._records import _iter_records

    (tmp_path / "quotes.jsonl").write_bytes(b"")
    (tmp_path / "quotes.json").write_bytes(b"")

    assert list(_iter_records(tmp_path / "quotes.jsonl")) == []
    with pytest.raises(InputFileError, match=r"^quotes\.json is empty, so it is not JSON\. "):
        list(_iter_records(tmp_path / "quotes.json"))


@pytest.mark.parametrize(
    ("body", "offset"),
    [
        (b"id,r\xe9gion\n1,N\n", 4),
        # A two-byte character split across the decoder's chunks, then a bad byte.
        (b"x" * (1024 * 1024 - 1) + "é".encode() + b"\xff", 1024 * 1024 + 1),
        # A sequence the file's end leaves unfinished.
        (b"id,value\n1,\xc3", 11),
    ],
    ids=["latin-1-header", "across-chunks", "unfinished-at-the-end"],
)
def test_a_file_that_is_not_utf8_is_refused_at_its_first_bad_byte(
    tmp_path: Path, body: bytes, offset: int
) -> None:
    from haute._input_files import not_utf8_file_error

    path = tmp_path / "quotes.csv"
    path.write_bytes(body)

    refused = not_utf8_file_error(path)

    assert refused is not None
    line, column = file_position(path, offset)
    assert refused.message == (
        f"quotes.csv is not valid UTF-8: byte 0x{body[offset]:02X} at line {line}, "
        f"column {column}. Save it as UTF-8."
    )


def test_a_utf8_file_is_not_refused_as_not_utf8(tmp_path: Path) -> None:
    from haute._input_files import not_utf8_file_error

    path = tmp_path / "quotes.csv"
    path.write_bytes("id,région\n1,N\n".encode())

    assert not_utf8_file_error(path) is None
