"""Mutation witnesses for bounded structured-input byte state machines."""

from __future__ import annotations

import io
from collections.abc import Callable
from types import SimpleNamespace

import pytest

import haute._json_shred as shred
from haute._api_input_schema import ApiInputSchemaError


class _RecordingBinarySource:
    """Binary source that exposes the caller's bounded-read contract."""

    def __init__(self, payload: bytes) -> None:
        self._source = io.BytesIO(payload)
        self.read_sizes: list[int] = []

    def __enter__(self) -> _RecordingBinarySource:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._source.read(size)


@pytest.mark.parametrize("token", [b"<!DOCTYPE", b"<!ENTITY", b"<!doctype", b"<!entity"])
def test_xml_declaration_scanner_rejects_every_split_point(token: bytes) -> None:
    for split in range(1, len(token)):
        carry = shred._scan_xml_declaration_chunk(b"", b"<root>" + token[:split])
        with pytest.raises(ApiInputSchemaError, match="DTD and entity"):
            shred._scan_xml_declaration_chunk(carry, token[split:] + b"</root>")


def test_xml_declaration_scanner_tracks_exact_overlap_and_utf_nuls() -> None:
    safe = b"abcdefghijklmnopq"
    assert shred._scan_xml_declaration_chunk(b"", safe) == safe[-8:].upper()
    with pytest.raises(ApiInputSchemaError, match="DTD and entity"):
        shred._scan_xml_declaration_chunk(b"", b"<\x00!\x00D\x00O\x00C\x00T\x00Y\x00P\x00E")


@pytest.mark.parametrize(
    "token", [b"<\x00!\x00D\x00O\x00C\x00T\x00Y\x00P\x00E", b"<\x00!\x00E\x00N\x00T\x00I\x00T\x00Y"]
)
def test_xml_declaration_scanner_rejects_utf_nul_tokens_at_every_split(token: bytes) -> None:
    for split in range(1, len(token)):
        carry = shred._scan_xml_declaration_chunk(b"", token[:split])
        with pytest.raises(ApiInputSchemaError, match="DTD and entity"):
            shred._scan_xml_declaration_chunk(carry, token[split:])


def test_xml_child_tracker_enforces_exact_encoded_boundary_and_resets() -> None:
    tracker = shred._XmlDirectChildByteTracker(limit=39)
    parser = SimpleNamespace(CurrentByteIndex=10)
    tracker._parser = parser

    tracker._start_element("root", {})
    assert tracker._depth == 1
    assert tracker._record_start is None

    parser.CurrentByteIndex = 20
    tracker._start_element("item", {})
    assert tracker._depth == 2
    assert tracker._record_start == 20
    assert tracker._closing_tag_allowance == 28

    parser.CurrentByteIndex = 31
    tracker._check_open_record()  # 11 observed bytes + 28 reserved bytes == the limit.
    tracker._end_element("item")
    assert tracker._depth == 1
    assert tracker._record_start is None
    assert tracker._closing_tag_allowance == 0

    parser.CurrentByteIndex = 40
    tracker._end_element("root")
    assert tracker._depth == 0


def test_xml_child_tracker_rejects_one_byte_over_the_exact_boundary() -> None:
    tracker = shred._XmlDirectChildByteTracker(limit=38)
    parser = SimpleNamespace(CurrentByteIndex=10)
    tracker._parser = parser
    tracker._start_element("root", {})
    parser.CurrentByteIndex = 20
    tracker._start_element("item", {})
    parser.CurrentByteIndex = 31
    with pytest.raises(ApiInputSchemaError, match="XML record exceeds"):
        tracker._check_open_record()


def test_xml_child_tracker_keeps_direct_child_boundary_through_nested_elements() -> None:
    # The arithmetic-sensitive name has a length that distinguishes common
    # +/|/^/* allowance mutations while remaining a valid XML name.
    name = "a-b.c_d:eF"
    allowance = 4 * (len(name) + 3)
    tracker = shred._XmlDirectChildByteTracker(limit=allowance + 14)
    parser = SimpleNamespace(CurrentByteIndex=100)
    tracker._parser = parser

    tracker._start_element("root", {})
    parser.CurrentByteIndex = 120
    tracker._start_element(name, {})
    record_start = tracker._record_start
    parser.CurrentByteIndex = 126
    tracker._start_element("nested", {})
    assert tracker._depth == 3
    assert tracker._record_start == record_start == 120
    assert tracker._closing_tag_allowance == allowance

    parser.CurrentByteIndex = 134
    tracker._end_element("nested")
    assert tracker._depth == 2
    assert tracker._record_start == 120
    tracker._check_open_record()  # 14 observed + the reserved close tag is exact.
    tracker._end_element(name)
    assert tracker._record_start is None

    too_large = shred._XmlDirectChildByteTracker(limit=allowance + 13)
    too_large._parser = SimpleNamespace(CurrentByteIndex=100)
    too_large._start_element("root", {})
    too_large._parser.CurrentByteIndex = 120
    too_large._start_element(name, {})
    too_large._parser.CurrentByteIndex = 134
    with pytest.raises(ApiInputSchemaError, match="XML record exceeds"):
        too_large._check_open_record()


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (b"<root><row><a>1</a></row><row><a>2</a></row></root>", True),
        (b"<root><row><a>1</a></row></root>", True),
        (b"<root id='x'><row><a>1</a></row></root>", False),
        (b"<root><row><a>1</a></row><other><a>2</a></other></root>", False),
        (b"<root><row>scalar</row><row>other</row></root>", False),
        (b"<root/>", False),
    ],
)
def test_xml_shape_classifier_distinguishes_every_record_condition(
    tmp_path, document: bytes, expected: bool
) -> None:
    path = tmp_path / "records.xml"
    path.write_bytes(document)
    shape = shred._inspect_xml_record_shape(path, len(document) + 128)
    assert shape.repeated_object_children is expected


def test_xml_shape_classifier_reads_one_byte_past_the_odd_record_limit() -> None:
    source = _RecordingBinarySource(b"<r/>")
    path = SimpleNamespace(open=lambda _mode: source)

    assert not shred._inspect_xml_record_shape(path, 7).repeated_object_children
    assert source.read_sizes == [8, 8]


@pytest.mark.parametrize(
    "document",
    [
        b"<root>text<row><a>1</a></row></root>",
        b"<root><row><a>1</a></row>tail</root>",
    ],
)
def test_xml_shape_classifier_rejects_root_mixed_text(tmp_path, document: bytes) -> None:
    path = tmp_path / "mixed.xml"
    path.write_bytes(document)
    with pytest.raises(ApiInputSchemaError, match="mixed text"):
        shred._inspect_xml_record_shape(path, len(document) + 128)


@pytest.mark.parametrize("document", [b"<root>", b"<root><row></root>"])
def test_xml_shape_classifier_wraps_malformed_xml(tmp_path, document: bytes) -> None:
    path = tmp_path / "malformed.xml"
    path.write_bytes(document)
    with pytest.raises(ApiInputSchemaError, match="Invalid XML"):
        shred._inspect_xml_record_shape(path, 128)


def test_repeated_xml_iterator_yields_each_object_and_rejects_scalar(tmp_path) -> None:
    valid = tmp_path / "valid.xml"
    valid.write_bytes(b"<root><row><a>1</a></row><row><a>2</a></row></root>")
    assert list(shred._iter_repeated_xml_records(valid, 1_024)) == [
        {"a": "1"},
        {"a": "2"},
    ]

    scalar = tmp_path / "scalar.xml"
    scalar.write_bytes(b"<root><row>value</row></root>")
    with pytest.raises(RuntimeError, match="shape changed"):
        list(shred._iter_repeated_xml_records(scalar, 1_024))


def test_repeated_xml_iterator_reads_one_byte_past_the_odd_record_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoopByteTracker:
        def __init__(self, _limit: int) -> None:
            pass

        def feed(self, _chunk: bytes) -> None:
            pass

        def close(self) -> None:
            pass

    source = _RecordingBinarySource(b"<r><x><a>1</a></x></r>")
    path = SimpleNamespace(open=lambda _mode: source)
    monkeypatch.setattr(shred, "_XmlDirectChildByteTracker", NoopByteTracker)
    monkeypatch.setattr(shred, "_validate_xml_record_value_size", lambda *_args: None)

    assert list(shred._iter_repeated_xml_records(path, 7)) == [{"a": "1"}]
    assert source.read_sizes and set(source.read_sizes) == {8}


def test_repeated_xml_iterator_preserves_order_and_enforces_serialized_record_limit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoopByteTracker:
        def __init__(self, _limit: int) -> None:
            pass

        def feed(self, _chunk: bytes) -> None:
            pass

        def close(self) -> None:
            pass

    # Isolate the separate logical-record serialization contract from the
    # conservative on-wire XML byte guard (covered above).
    monkeypatch.setattr(shred, "_XmlDirectChildByteTracker", NoopByteTracker)
    path = tmp_path / "records.xml"
    path.write_bytes(b"<root><row><a>1</a></row><row><a>22</a></row></root>")
    assert list(shred._iter_repeated_xml_records(path, len(b'{"a":"22"}'))) == [
        {"a": "1"},
        {"a": "22"},
    ]
    with pytest.raises(ApiInputSchemaError, match="XML record exceeds"):
        list(shred._iter_repeated_xml_records(path, len(b'{"a":"22"}') - 1))


@pytest.mark.parametrize("payload", [b"<root>", b"<!DOCTYPE root>"])
def test_repeated_xml_iterator_rejects_malformed_and_declarations(tmp_path, payload: bytes) -> None:
    path = tmp_path / "bad.xml"
    path.write_bytes(payload)
    with pytest.raises(ApiInputSchemaError, match="Invalid XML|DTD and entity"):
        list(shred._iter_repeated_xml_records(path, 128))


def test_bounded_xml_root_accepts_exact_limit_and_rejects_next_byte(tmp_path) -> None:
    document = b"<root><value>1</value></root>"
    path = tmp_path / "one.xml"
    path.write_bytes(document)
    assert shred._parse_bounded_xml_root(path, len(document)).tag == "root"
    with pytest.raises(ApiInputSchemaError, match="XML record exceeds"):
        shred._parse_bounded_xml_root(path, len(document) - 1)

    empty = tmp_path / "empty.xml"
    empty.write_bytes(b"")
    with pytest.raises(ApiInputSchemaError, match="Invalid XML.*no element found"):
        shred._parse_bounded_xml_root(empty, 1)


def test_bounded_xml_root_crosses_small_chunks_and_handles_whitespace_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shred, "_STRUCTURED_INPUT_PARSE_CHUNK_BYTES", 3)
    document = b" \n<root><value>1</value></root>\t"
    path = tmp_path / "chunked.xml"
    path.write_bytes(document)
    assert shred._parse_bounded_xml_root(path, len(document)).tag == "root"
    with pytest.raises(ApiInputSchemaError, match="XML record exceeds"):
        shred._parse_bounded_xml_root(path, len(document) - 1)
    malformed = tmp_path / "malformed.xml"
    malformed.write_bytes(b"<root><x></root>")
    with pytest.raises(ApiInputSchemaError, match="Invalid XML"):
        shred._parse_bounded_xml_root(malformed, 128)


@pytest.mark.parametrize("chunk_bytes", [0, -1])
def test_jsonl_byte_ranges_require_a_strictly_positive_chunk(tmp_path, chunk_bytes: int) -> None:
    path = tmp_path / "records.jsonl"
    path.write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="must be positive"):
        shred._jsonl_byte_ranges(path, chunk_bytes)


@pytest.mark.parametrize(
    ("payload", "chunk_bytes", "expected"),
    [
        (b"", 1, []),
        (b"abc", 3, [(0, 3)]),
        (b"abc", 4, [(0, 3)]),
        (b"a\nbb\nccc\n", 1, [(0, 2), (2, 5), (5, 9)]),
        (b"a\nbb\nccc\n", 2, [(0, 5), (5, 9)]),
        (b"a\nbb\nccc\n", 4, [(0, 5), (5, 9)]),
        (b"abcdefghij", 3, [(0, 10)]),
    ],
)
def test_jsonl_byte_ranges_tile_whole_records_exactly(
    tmp_path, payload: bytes, chunk_bytes: int, expected: list[tuple[int, int]]
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_bytes(payload)
    ranges = shred._jsonl_byte_ranges(path, chunk_bytes)
    assert ranges == expected
    assert b"".join(payload[start:end] for start, end in ranges) == payload


@pytest.mark.parametrize(
    ("payload", "chunk_bytes", "expected"),
    [
        (b"a\n", 2, [(0, 2)]),  # size == chunk
        (b"a\nb", 2, [(0, 3)]),  # one byte past chunk, no final newline
        (b"a\nbb\nccc", 3, [(0, 5), (5, 8)]),
        (b"\n\nabc\n\n", 2, [(0, 6), (6, 7)]),
        (b"very-long-record-without-newline", 1, [(0, 32)]),
    ],
)
def test_jsonl_byte_ranges_respect_seek_boundaries_and_unterminated_lines(
    tmp_path, payload: bytes, chunk_bytes: int, expected: list[tuple[int, int]]
) -> None:
    path = tmp_path / "seek.jsonl"
    path.write_bytes(payload)
    ranges = shred._jsonl_byte_ranges(path, chunk_bytes)
    assert ranges == expected
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(payload)
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))


def test_record_iterator_enforces_exact_jsonl_limit_and_skip_accounting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = b'{"a":1}'
    monkeypatch.setattr(shred, "_structured_input_record_limit", lambda: len(record))

    exact = tmp_path / "exact.ndjson"
    exact.write_bytes(record)
    stats = shred.ShredSkipStats()
    assert list(shred._iter_records(exact, stats=stats)) == [{"a": 1}]
    assert stats.skipped_records == 0

    too_large = tmp_path / "large.jsonl"
    too_large.write_bytes(record + b" ")
    with pytest.raises(ApiInputSchemaError, match="JSONL record exceeds"):
        list(shred._iter_records(too_large))


def test_record_iterator_jsonl_requests_one_byte_past_record_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Source:
        def __init__(self) -> None:
            self.readline_sizes: list[int] = []
            self._lines = iter([b'{"a":1}', b""])

        def __enter__(self) -> Source:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def readline(self, size: int) -> bytes:
            self.readline_sizes.append(size)
            return next(self._lines)

    source = Source()
    path = SimpleNamespace(suffix=".jsonl", open=lambda _mode: source)
    monkeypatch.setattr(shred, "_structured_input_record_limit", lambda: 7)
    assert list(shred._iter_records(path)) == [{"a": 1}]
    assert source.readline_sizes == [8, 8]


def test_record_iterator_dispatches_case_insensitive_xml_suffix(tmp_path) -> None:
    path = tmp_path / "records.XML"
    path.write_bytes(b"<root><row><a>1</a></row><row><a>2</a></row></root>")
    assert list(shred._iter_records(path)) == [{"a": "1"}, {"a": "2"}]


def test_record_iterator_counts_each_scalar_but_not_blank_jsonl_line(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shred, "_structured_input_record_limit", lambda: 128)
    path = tmp_path / "mixed.jsonl"
    path.write_bytes(b'\n1\n[]\n{"a":2}\n')
    stats = shred.ShredSkipStats()
    assert list(shred._iter_records(path, stats=stats)) == [{"a": 2}]
    assert stats.skipped_records == 2


def test_record_iterator_root_object_scalar_and_array_paths(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shred, "_structured_input_record_limit", lambda: 128)

    obj_path = tmp_path / "object.json"
    obj_path.write_bytes(b'{"a":1}')
    assert list(shred._iter_records(obj_path)) == [{"a": 1}]

    scalar_path = tmp_path / "scalar.json"
    scalar_path.write_bytes(b"1")
    scalar_stats = shred.ShredSkipStats()
    assert list(shred._iter_records(scalar_path, stats=scalar_stats)) == []
    assert scalar_stats.skipped_records == 1

    array_path = tmp_path / "array.json"
    array_path.write_bytes(b'[1,{"a":2},[],{"a":3}] \r\n')
    array_stats = shred.ShredSkipStats()
    assert list(shred._iter_records(array_path, stats=array_stats)) == [
        {"a": 2},
        {"a": 3},
    ]
    assert array_stats.skipped_records == 2


def test_record_iterator_root_document_reads_one_byte_past_the_odd_record_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _RecordingBinarySource(b"{}")
    path = SimpleNamespace(suffix=".json", open=lambda _mode: source)
    monkeypatch.setattr(shred, "_structured_input_record_limit", lambda: 7)

    assert list(shred._iter_records(path)) == [{}]
    assert source.read_sizes == [1, 8, 8]


def test_trailing_json_error_reports_the_exact_absolute_byte_offset() -> None:
    source = io.BytesIO(b" \tx")

    with pytest.raises(shred.orjson.JSONDecodeError) as raised:
        shred._validate_json_trailing_whitespace(source, 6)

    assert raised.value.pos == 8


def _byte_reader(*values: bytes) -> tuple[Callable[[], bytes], list[bytes]]:
    remaining = iter(values)
    observed: list[bytes] = []

    def read() -> bytes:
        value = next(remaining, b"")
        observed.append(value)
        return value

    return read, observed


def test_root_array_string_accepts_the_exact_byte_limit() -> None:
    read, observed = _byte_reader(b'"', b",")

    assert shred._read_root_array_value(b'"', read, lambda: len(observed), max_bytes=2) == (
        b'""',
        b",",
    )
    assert observed == [b'"', b","]


@pytest.mark.parametrize("opening", [b'"', b"["])
def test_root_array_nested_token_crosses_the_limit_only_on_the_next_byte(
    opening: bytes,
) -> None:
    read, observed = _byte_reader(opening, b"x")

    with pytest.raises(ApiInputSchemaError, match="JSON array element exceeds"):
        shred._read_root_array_value(b"1", read, lambda: len(observed), max_bytes=2)

    assert observed == [opening, b"x"]


def test_root_array_scalar_accepts_the_exact_byte_limit() -> None:
    read, observed = _byte_reader(b"2", b",")

    assert shred._read_root_array_value(b"1", read, lambda: len(observed), max_bytes=2) == (
        b"12",
        b",",
    )
    assert observed == [b"2", b","]


def _root_column_config(type_token: str) -> dict[str, object]:
    return {
        "path": "x.json",
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[:].value",
                        "type": type_token,
                        "selected": True,
                    }
                ],
            }
        ],
    }


def test_normal_float_column_is_not_scalar_leaf_coerced_while_shredding() -> None:
    buffers = shred.shred_to_buffers([{"value": 1}], _root_column_config("float"))

    assert buffers["root"] == [{"value": 1}]
    assert type(buffers["root"][0]["value"]) is int


def test_normal_string_column_token_uses_value_equality() -> None:
    noninterned = "".join(["s", "t", "r"])
    expected = "str"
    assert noninterned == expected and noninterned is not expected

    buffers = shred.shred_to_buffers([{"value": 1}], _root_column_config(noninterned))

    assert buffers["root"] == [{"value": "1"}]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"[{}", "unexpected end of data"),
        (b"[{},]", "trailing comma"),
        (b"[{}]x", "unexpected trailing data"),
    ],
)
def test_record_iterator_rejects_incomplete_or_trailing_array_syntax(
    tmp_path, monkeypatch: pytest.MonkeyPatch, payload: bytes, message: str
) -> None:
    monkeypatch.setattr(shred, "_structured_input_record_limit", lambda: 128)
    path = tmp_path / "invalid.json"
    path.write_bytes(payload)
    with pytest.raises(shred.orjson.JSONDecodeError, match=message):
        list(shred._iter_records(path))
