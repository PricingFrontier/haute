"""How Haute reads the bytes of an input's file (``haute._input_files``)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from haute._input_files import (
    UTF8_BOM,
    file_position,
    load_json_file,
    text_encoding_refusal,
)
from haute.errors import InputFileError

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
    path.write_bytes(UTF8_BOM + '{"name": "x", "name": "Crème", "rate": NaN}'.encode())

    loaded = load_json_file(path)

    # Python's JSON reader, as before: a repeated key's last value, NaN loaded.
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


def test_haute_reads_a_repeated_key_by_one_rule_and_a_data_input_by_polars(
    tmp_path: Path,
) -> None:
    """IOL-02: the Quote Input's shredder, its served request, the dry run's test quotes and
    Load File keep a repeated key's last value, as orjson, Python and JavaScript read it,
    and none refuses it; a Data Input reads by Polars' rules, which the specification
    states (the first value of a top-level key repeated with one type)."""
    import asyncio

    from haute._json_shred._records import _iter_records
    from haute._polars_io_registry import read_polars_input
    from haute.deploy._request_limits import read_limited_json_body
    from haute.deploy._validators import load_test_quote_file

    record = b'{"quote_id": "q1", "region": "north", "region": "south"}'
    as_json = tmp_path / "quotes.json"
    as_json.write_bytes(b"[" + record + b"]")
    as_jsonl = tmp_path / "quotes.jsonl"
    as_jsonl.write_bytes(record + b"\n")
    test_quotes = tmp_path / "test_quotes.json"
    test_quotes.write_bytes(b'[{"input": ' + record + b"}]")

    haute = {
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
        "test quotes": [row["region"] for row in load_test_quote_file(test_quotes)],
        "Load File": [row["region"] for row in load_json_file(as_json)],
    }
    polars = {
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
    }

    assert haute == dict.fromkeys(haute, ["south"])
    assert polars == dict.fromkeys(polars, ["north"])


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


@pytest.mark.parametrize("compressed", [False, True], ids=["raw", "gzip"])
def test_a_fault_is_placed_without_holding_what_comes_before_it(
    tmp_path: Path, compressed: bool
) -> None:
    """A fault 12 MiB into a file, on a line as long, is placed holding a bounded chunk (and,
    for a gzip file, the decompressor's own bounded buffers), never what comes before it."""
    import gzip
    import tracemalloc

    body = b'{"id": 1}\n' + b"x" * (12 * 1024 * 1024)
    path = tmp_path / "quotes.jsonl"
    path.write_bytes(gzip.compress(body, mtime=0) if compressed else body)

    tracemalloc.start()
    try:
        position = file_position(path, len(body), decompressed=compressed)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert position == (2, 12 * 1024 * 1024 + 1)
    assert peak < 6 * 1024 * 1024


def test_a_raw_file_that_starts_like_a_compressed_stream_is_placed_in_its_own_bytes(
    tmp_path: Path,
) -> None:
    """Load File and the shredder read a file's raw bytes, so their faults are placed in
    them, never in a decompression of a file that only starts like a gzip stream."""
    from haute._json_shred._records import _iter_records

    path = tmp_path / "bad.json"
    path.write_bytes(b"\x1f\x8b")

    with pytest.raises(InputFileError) as loaded:
        load_json_file(path)
    with pytest.raises(InputFileError) as shredded:
        list(_iter_records(path))

    assert (
        loaded.value.message
        == shredded.value.message
        == ("bad.json is not valid UTF-8: byte 0x8B at line 1, column 2. Save it as UTF-8.")
    )


def _compressed(kind: str, data: bytes) -> bytes:
    import gzip
    import zlib

    import pyarrow as pa

    if kind == "gzip":
        return gzip.compress(data, mtime=0)
    if kind == "zlib":
        return zlib.compress(data)
    sink = pa.BufferOutputStream()
    with pa.CompressedOutputStream(sink, "zstd") as stream:
        stream.write(data)
    return sink.getvalue().to_pybytes()


@pytest.mark.parametrize("kind", ["gzip", "zlib", "zstd"])
def test_a_compressed_csv_that_cannot_be_decompressed_is_refused_in_words(
    tmp_path: Path, kind: str
) -> None:
    """A small file truncated by its last byte: each decompressor's own failure, said for
    the file by a read that reaches it (a probe of a large file's first bytes need not)."""
    path = tmp_path / "quotes.csv"
    path.write_bytes(_compressed(kind, b"row_id,value\n1,a\n2,b\n")[:-1])

    from haute._input_files import not_utf8_file_error

    said = (
        f"quotes.csv is a {kind} file that cannot be decompressed: it may be truncated or damaged."
    )
    # Read in full (the header check's decode) and only its first bytes (the encoding check).
    with pytest.raises(InputFileError) as decoded:
        not_utf8_file_error(path)
    with pytest.raises(InputFileError) as probed:
        text_encoding_refusal(path)

    assert decoded.value.message == probed.value.message == said


def test_a_readers_own_refusal_inside_a_compressed_stream_passes_as_it_is(
    tmp_path: Path,
) -> None:
    """Only the decompressor's read is guarded: a header that is not UTF-8 inside a gzip
    file is refused as not UTF-8, never as a damaged stream."""
    from haute._input_files import not_utf8_file_error

    path = tmp_path / "quotes.csv"
    path.write_bytes(_compressed("gzip", "id,r\xe9gion\n1,N\n".encode("latin-1")))

    refused = not_utf8_file_error(path)

    assert refused is not None
    assert refused.message == (
        "quotes.csv is not valid UTF-8: byte 0xE9 at line 1, column 5. Save it as UTF-8."
    )
