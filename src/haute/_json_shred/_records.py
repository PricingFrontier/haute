"""Streaming record iteration for JSON, JSONL, and XML structured inputs.

Every reader enforces the shared bounded record limit, and byte-range tiling
with its parallelism policy lives beside the readers it partitions."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from itertools import islice
from pathlib import Path
from typing import Any, BinaryIO, Literal, NoReturn, cast
from xml.parsers import expat

import orjson

from haute._api_input_schema import (
    ApiInputSchemaError,
)
from haute._env import int_env
from haute._execution_context import (
    ExecutionContext,
    current_execution_context,
)
from haute._logging import get_logger
from haute._native_memory_limit import current_native_memory_backend

logger = get_logger(component="json_shred")


_SHRED_EXECUTION_CHECKPOINT_ROWS = 1_024


_STRUCTURED_INPUT_MAX_RECORD_BYTES_DEFAULT = 64 * 1024 * 1024


_STRUCTURED_INPUT_PARSE_CHUNK_BYTES = 64 * 1024


# Amortize filesystem calls during sequential JSONL scans without retaining
# whole ranges. Logical record limits are enforced independently of read-ahead.
_JSONL_READ_BUFFER_BYTES = 64 * 1024  # pragma: no mutate - performance tuning knob


@dataclass(slots=True)
class _ShredExecutionProgress:
    """Bound cancellation/RSS-check distance in Python shred materialisation."""

    execution_context: ExecutionContext | None  # pragma: no mutate
    work_since_checkpoint: int = 0

    @classmethod
    def current(cls) -> _ShredExecutionProgress:
        return cls(current_execution_context())

    def checkpoint(self, label: str) -> None:
        if self.execution_context is not None:
            self.execution_context.checkpoint(label=label)
        self.work_since_checkpoint = 0

    def advance(self, label: str) -> None:
        if self.execution_context is None:
            return
        self.work_since_checkpoint += 1
        if self.work_since_checkpoint >= _SHRED_EXECUTION_CHECKPOINT_ROWS:
            self.checkpoint(label)


# ---------------------------------------------------------------------------
# Skip accounting (W2 item 2.7) — zero silent record loss
# ---------------------------------------------------------------------------


@dataclass
class ShredSkipStats:
    """Counts of inputs the shred dropped because their shape didn't fit.

    Two units, never conflated:

    - ``skipped_records`` — top-level inputs that aren't JSON objects (a
      JSONL line holding a number/string/array, a non-object element of a
      root array). They produce no rows in ANY table.
    - ``skipped_rows_by_table`` — array elements at an emitting table's
      depth whose shape mismatched that table (a scalar/null in an
      object-table array, an object in a scalar-table array). Each one is
      a row that table silently lost before W2.

    A table build and the standalone shred log them when any are non-zero.
    """

    skipped_records: int = 0
    skipped_rows_by_table: dict[str, int] = field(default_factory=dict)

    def count_record_skip(self) -> None:
        self.skipped_records += 1

    def count_row_skip(self, label: str) -> None:
        self.skipped_rows_by_table[label] = self.skipped_rows_by_table.get(label, 0) + 1

    @property
    def total(self) -> int:
        return self.skipped_records + sum(self.skipped_rows_by_table.values())


# ---------------------------------------------------------------------------
# Record iteration
# ---------------------------------------------------------------------------


def _xml_local_name(name: str) -> str:
    """Strip an XML namespace while retaining the source element name."""
    return name.rsplit("}", 1)[-1].split(":", 1)[-1]


def _xml_element_value(element: ET.Element) -> Any:
    """Convert an XML element into the object/list/scalar shape used by shredding."""
    result: dict[str, Any] = {}

    for raw_name, value in element.attrib.items():
        name = _xml_local_name(raw_name)
        if name in result:
            raise ApiInputSchemaError(f"duplicate XML attribute name {name!r}")
        result[name] = value

    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if not result:
            return text
        if text:
            if "value" in result:
                raise ApiInputSchemaError(
                    "XML element has both a 'value' attribute and text content"
                )
            result["value"] = text
        return result

    if (element.text or "").strip() or any((child.tail or "").strip() for child in children):
        raise ApiInputSchemaError(
            f"mixed text and child elements are not supported in XML element "
            f"{_xml_local_name(element.tag)!r}"
        )

    grouped: dict[str, list[Any]] = {}
    for child in children:
        name = _xml_local_name(child.tag)
        grouped.setdefault(name, []).append(_xml_element_value(child))

    for name, values in grouped.items():
        if name in result:
            raise ApiInputSchemaError(f"XML attribute and child element share the name {name!r}")
        result[name] = values[0] if len(values) == 1 else values
    return result


def _structured_input_record_limit() -> int:
    return int_env(
        "HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES",
        _STRUCTURED_INPUT_MAX_RECORD_BYTES_DEFAULT,
    )


def _record_limit_error(kind: str, limit: int) -> ApiInputSchemaError:
    return ApiInputSchemaError(
        f"{kind} exceeds HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES={limit}; "
        "split the source into smaller logical records or raise the limit "
        "within the execution memory budget"
    )


def _scan_xml_declaration_chunk(carry: bytes, chunk: bytes) -> bytes:
    """Reject unsafe declarations in the exact bytes about to reach the parser."""
    tokens = (b"<!DOCTYPE", b"<!ENTITY")
    overlap = max(len(token) for token in tokens) - 1
    # Removing NULs also recognises the ASCII declaration tokens in UTF-16/32
    # XML encodings. The XML parser still remains authoritative for encoding.
    upper = (carry + chunk).upper().replace(b"\x00", b"")
    if any(token in upper for token in tokens):
        raise ApiInputSchemaError("XML DTD and entity declarations are not supported")
    return upper[-overlap:]


def _validate_xml_record_value_size(value: dict[str, Any], limit: int) -> None:
    if len(orjson.dumps(value)) > limit:
        raise _record_limit_error("XML record", limit)


class _XmlDirectChildByteTracker:
    """Enforce a conservative encoded-byte bound without retaining source bytes."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._depth = 0
        self._record_start: int | None = None  # pragma: no mutate
        self._closing_tag_allowance = 0
        self._parser = expat.ParserCreate()
        self._parser.StartElementHandler = self._start_element
        self._parser.EndElementHandler = self._end_element

    def _start_element(self, name: str, _attributes: dict[str, str]) -> None:
        self._depth += 1
        if self._depth == 2:
            self._record_start = self._parser.CurrentByteIndex
            # XML supports UTF-8/16/32. Reserving four bytes per closing-tag
            # code point makes the limit fail closed without buffering the tag.
            self._closing_tag_allowance = 4 * (len(name) + 3)

    def _end_element(self, _name: str) -> None:
        if self._depth == 2:
            self._check_open_record()
            self._record_start = None
            self._closing_tag_allowance = 0
        self._depth -= 1

    def _check_open_record(self) -> None:
        if self._record_start is None:
            return
        encoded_bytes = self._parser.CurrentByteIndex - self._record_start
        if encoded_bytes + self._closing_tag_allowance > self._limit:
            raise _record_limit_error("XML record", self._limit)

    def feed(self, chunk: bytes) -> None:
        self._parser.Parse(chunk, False)
        self._check_open_record()

    def close(self) -> None:
        self._parser.Parse(b"", True)


@dataclass(frozen=True, slots=True)
class _XmlRecordShape:
    repeated_object_children: bool


def _read_xml_events(
    parser: ET.XMLPullParser,
) -> Iterator[tuple[str, ET.Element[str]]]:
    """Narrow typeshed's event union to this parser's start/end contract."""
    return cast("Iterator[tuple[str, ET.Element[str]]]", parser.read_events())


def _require_xml_root(root: ET.Element[str] | None) -> ET.Element[str]:  # pragma: no mutate
    """Reject an impossible pull-parser event order with explicit evidence."""
    if root is None:
        raise RuntimeError("XML parser emitted a direct child before the document root")
    return root


def _inspect_xml_record_shape(data_path: Path, limit: int) -> _XmlRecordShape:
    """Validate XML and classify its top-level record shape with bounded retention."""
    parser = ET.XMLPullParser(events=("start", "end"))
    byte_tracker = _XmlDirectChildByteTracker(limit)
    chunk_size = min(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES, limit + 1)
    root: ET.Element | None = None  # pragma: no mutate
    depth = 0
    direct_child_count = 0
    direct_child_name: str | None = None  # pragma: no mutate
    all_direct_children_are_objects = True
    root_has_attributes = False
    declaration_carry = b""
    try:
        with data_path.open("rb") as source:
            while chunk := source.read(chunk_size):
                declaration_carry = _scan_xml_declaration_chunk(declaration_carry, chunk)
                byte_tracker.feed(chunk)
                parser.feed(chunk)
                for event, element in _read_xml_events(parser):
                    if event == "start":
                        depth += 1
                        if depth == 1:
                            root = element
                            root_has_attributes = bool(element.attrib)
                        continue

                    if depth == 2:
                        root_element = _require_xml_root(root)
                        if (element.tail or "").strip():
                            root_name = _xml_local_name(root_element.tag)
                            raise ApiInputSchemaError(
                                f"mixed text and child elements are not supported in XML element "
                                f"{root_name!r}"
                            )
                        value = _xml_element_value(element)
                        if isinstance(value, dict):
                            _validate_xml_record_value_size(value, limit)
                        child_name = _xml_local_name(element.tag)
                        if direct_child_name is None:
                            direct_child_name = child_name
                        elif child_name != direct_child_name:
                            all_direct_children_are_objects = False
                        if not isinstance(value, dict):
                            all_direct_children_are_objects = False
                        direct_child_count += 1
                        root_element.remove(element)
                        element.clear()
                    elif depth == 1 and (element.text or "").strip() and direct_child_count:
                        raise ApiInputSchemaError(
                            f"mixed text and child elements are not supported in XML element "
                            f"{_xml_local_name(element.tag)!r}"
                        )
                    depth -= 1
            byte_tracker.close()
            parser.close()
    except (ET.ParseError, expat.ExpatError) as exc:
        raise ApiInputSchemaError(f"Invalid XML in data file: {exc}") from exc

    return _XmlRecordShape(
        repeated_object_children=(
            direct_child_count > 0
            and not root_has_attributes
            and all_direct_children_are_objects
            and direct_child_name is not None
        )
    )


def _iter_repeated_xml_records(data_path: Path, limit: int) -> Iterator[dict[str, Any]]:
    """Yield and release homogeneous direct-child XML records."""
    parser = ET.XMLPullParser(events=("start", "end"))
    byte_tracker = _XmlDirectChildByteTracker(limit)
    chunk_size = min(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES, limit + 1)
    root: ET.Element | None = None  # pragma: no mutate
    depth = 0
    declaration_carry = b""
    try:
        with data_path.open("rb") as source:
            while chunk := source.read(chunk_size):
                declaration_carry = _scan_xml_declaration_chunk(declaration_carry, chunk)
                byte_tracker.feed(chunk)
                parser.feed(chunk)
                for event, element in _read_xml_events(parser):
                    if event == "start":
                        depth += 1
                        if depth == 1:
                            root = element
                        continue

                    if depth == 2:
                        root_element = _require_xml_root(root)
                        value = _xml_element_value(element)
                        if not isinstance(value, dict):
                            raise RuntimeError(
                                "XML record shape changed between validation and emission"
                            )
                        _validate_xml_record_value_size(value, limit)
                        root_element.remove(element)
                        element.clear()
                        yield value
                    depth -= 1
            byte_tracker.close()
            parser.close()
    except (ET.ParseError, expat.ExpatError) as exc:
        raise ApiInputSchemaError(f"Invalid XML in data file: {exc}") from exc


def _parse_bounded_xml_root(data_path: Path, limit: int) -> ET.Element:
    """Parse one-root-record XML while enforcing its encoded-byte bound."""
    parser = ET.XMLPullParser(events=("start", "end"))
    total = 0
    root: ET.Element | None = None  # pragma: no mutate
    declaration_carry = b""
    try:
        with data_path.open("rb") as source:
            while chunk := source.read(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES):
                declaration_carry = _scan_xml_declaration_chunk(declaration_carry, chunk)
                total += len(chunk)
                if total > limit:
                    raise _record_limit_error("XML record", limit)
                parser.feed(chunk)
                for event, element in _read_xml_events(parser):
                    if event == "start" and root is None:
                        root = element
            parser.close()
    except ET.ParseError as exc:
        raise ApiInputSchemaError(f"Invalid XML in data file: {exc}") from exc
    if root is None:
        raise ApiInputSchemaError("Invalid XML in data file: no document element")
    return root


def _iter_xml_records(data_path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from a single XML document.

    An attribute-free container whose children all share one element name is
    treated like a JSON root array. Otherwise the document root itself is one
    record so root attributes are never discarded.
    """
    limit = _structured_input_record_limit()
    shape = _inspect_xml_record_shape(data_path, limit)
    if shape.repeated_object_children:
        yield from _iter_repeated_xml_records(data_path, limit)
        return

    root = _parse_bounded_xml_root(data_path, limit)
    value = _xml_element_value(root)
    if isinstance(value, dict):
        yield value
    else:
        yield {_xml_local_name(root.tag): value}


RecordFormat = Literal["json", "xml"]


def record_format(data_path: Path) -> RecordFormat:
    """The format *data_path*'s records are read from, by its suffix.

    XML has no array syntax: it repeats an element to make a list, so one element
    where a schema declares an array is a one-element list. JSON (and JSONL) says
    which values are arrays.
    """
    return "xml" if data_path.suffix.lower() == ".xml" else "json"


def _iter_records(
    data_path: Path,
    *,  # pragma: no mutate
    stats: ShredSkipStats | None = None,  # pragma: no mutate
) -> Iterator[dict[str, Any]]:
    """Yield top-level records from a JSON or JSONL file.

    JSONL: one record per non-empty line.
    JSON: if the file's root is an array, yields each element; if the
    root is an object, yields that single object.

    A top-level input that parses as valid JSON but isn't an object (a
    JSONL line holding ``5`` / ``"x"`` / ``[...]``, a non-object element of
    a root array, a scalar root) is not a record and is skipped — *stats*,
    when provided, counts each one so the build can surface the loss
    (W2 item 2.7). Blank JSONL lines are formatting, not records, and are
    never counted. Malformed JSON still raises.
    """

    record_limit = _structured_input_record_limit()
    if record_format(data_path) == "xml":
        yield from _iter_xml_records(data_path)
        return
    suffix = data_path.suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        with data_path.open("rb", buffering=_JSONL_READ_BUFFER_BYTES) as f:
            while raw_line := f.readline(record_limit + 1):
                if len(raw_line) > record_limit:
                    raise _record_limit_error("JSONL record", record_limit)
                record = _jsonl_record(raw_line, stats)
                if record is not None:
                    yield record
        return
    with data_path.open("rb") as f:
        first, pos = _read_first_non_whitespace(f)
        if not first:
            return
        if first != b"[":
            # Root-object/scalar semantics remain exactly the existing JSON
            # semantics, with one explicit hard logical-record bound.
            document = bytearray(first)
            while chunk := f.read(min(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES, record_limit + 1)):
                document.extend(chunk)
                if len(document) > record_limit:
                    raise _record_limit_error("JSON root record", record_limit)
            obj = orjson.loads(document)
            if isinstance(obj, dict):
                yield obj
            elif stats is not None:
                stats.count_record_skip()
            return
        # A root array can be arbitrarily large: it is read one element at a
        # time, still consuming and validating the complete document.
        yield from _iter_root_array_records(f, pos, limit=record_limit, stats=stats)


def _jsonl_record(raw_line: bytes, stats: ShredSkipStats | None) -> dict[str, Any] | None:
    """One JSONL line's record, or ``None`` for a blank line or a non-object value.

    A blank line is formatting and counts nothing; *stats*, when given, counts a
    line whose value is not an object.
    """
    stripped = raw_line.strip()
    if not stripped:
        return None
    value = orjson.loads(stripped)
    if isinstance(value, dict):
        return value
    if stats is not None:
        stats.count_record_skip()
    return None


def _read_first_non_whitespace(f: BinaryIO) -> tuple[bytes, int]:
    """A JSON file's first byte that is not whitespace (empty when there is none),
    and the number of bytes read up to and including it."""
    pos = 0
    while byte := f.read(1):
        pos += 1
        if byte not in _JSON_WHITESPACE:
            return byte, pos
    return b"", pos


def _json_decode_error(message: str, pos: int) -> orjson.JSONDecodeError:
    return orjson.JSONDecodeError(message, "", pos)


def _validate_json_trailing_whitespace(source: Any, pos: int) -> int:
    """Consume JSON trailing whitespace without allocating it as one byte string."""
    while chunk := source.read(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES):
        for offset, byte in enumerate(chunk):
            if byte not in b" \t\r\n":
                # ``pos`` is the count of bytes already consumed, which is
                # also the zero-based index of the first byte in ``chunk``.
                raise _json_decode_error("unexpected trailing data", pos + offset)
        pos += len(chunk)
    return pos


def _iter_sampled_json_array_records(
    data_path: Path,
    sample_size: int,
) -> Iterator[dict[str, Any]]:
    """Yield up to ``sample_size`` object records from a root JSON array.

    This is intentionally used only for inference sampling. Full builds and
    unsampled inference still parse the whole file so malformed data is caught
    before any cache is materialised.
    """
    with data_path.open("rb") as f:
        first, pos = _read_first_non_whitespace(f)
        if not first:
            return
        if first != b"[":
            yield from islice(_iter_records(data_path), sample_size)
            return
        records = _iter_root_array_records(
            f, pos, limit=_structured_input_record_limit(), stats=None
        )
        yield from islice(records, sample_size)


_JSON_WHITESPACE = b" \t\r\n"
_QUOTE, _BACKSLASH, _CLOSE_ARRAY, _CLOSE_OBJECT = b'"\\]}'
_OPENERS = b"{["

# Inside a string only a quote or a backslash matters. Outside one, a comma or a
# closing bracket at an element's top level ends it, and inside a container only
# brackets and quotes do. Each run pattern skips, in one native call, every byte
# that cannot end or nest the element, whole strings included; a string that a
# chunk boundary splits stops a run at its opening quote.
_STRING_STOP = re.compile(rb'["\\]')
_TOP_LEVEL_RUN = re.compile(rb'(?:[^"{}\[\],]++|"(?:[^"\\]++|\\.)*+")*+', re.DOTALL)
_NESTED_RUN = re.compile(rb'(?:[^"{}\[\]]++|"(?:[^"\\]++|\\.)*+")*+', re.DOTALL)
_NON_WHITESPACE = re.compile(rb"[^ \t\r\n]")

# Where a root-array scan is: between elements, in one, or in one of its strings.
_SEEK, _ELEMENT, _STRING = range(3)

# The levels of containers inside an element that one bracket-counting match follows.
_BRACKETED_ELEMENT_LEVELS = 8


@cache
def _bracketed_element() -> re.Pattern[bytes]:
    """A guess at an element and its delimiter in one native match: whitespace, an
    object or array closed in this chunk, its brackets paired by kind with at most
    ``_BRACKETED_ELEMENT_LEVELS`` levels inside and strings ignored, whitespace,
    then ``,`` or ``]``.

    A bracket inside a string can mislead the count, so a guess is an element only
    when orjson parses it: a guess that ends early is a strict prefix of a JSON
    value, one that ends late holds the delimiter after it, and neither parses.
    Compiled on first use, since the pattern is long.
    """
    content = rb"[^{}\[\]]*+"
    for _ in range(_BRACKETED_ELEMENT_LEVELS):
        container = rb"\{" + content + rb"\}|\[" + content + rb"\]"
        content = rb"[^{}\[\]]*+(?:(?:" + container + rb")[^{}\[\]]*+)*+"
    element = rb"\{" + content + rb"\}|\[" + content + rb"\]"
    return re.compile(rb"[ \t\r\n]*+(" + element + rb")[ \t\r\n]*+([,\]])")


def _iter_root_array_values(f: BinaryIO, pos: int, limit: int) -> Iterator[Any]:
    """Yield each element of a root JSON array, parsed, *f* just past the ``[``.

    *pos* is the number of bytes already read. The file is read in fixed chunks.
    An element is first guessed by counting its brackets in one native match and
    kept when orjson parses the guess (:func:`_bracketed_element`). Otherwise it is
    found exactly: each chunk is searched with compiled patterns for the bytes that
    open or close a string, an object or an array or end an element, carrying the
    string, escape and depth state across chunks, and the element is sliced out of
    the chunks it spans, its trailing whitespace stripped, and parsed. An element
    longer than *limit* bytes is refused before more than one chunk past the limit
    is held. The closing bracket and everything after it are read and checked too;
    decode errors carry the number of bytes read when they were found.
    """
    chunk_size = _STRUCTURED_INPUT_PARSE_CHUNK_BYTES
    bracketed_element = _bracketed_element()
    chunk = b""
    view = memoryview(chunk)
    base = pos  # the file offset of chunk[0]
    at = 0  # the next byte of chunk to scan
    state = _SEEK
    start = 0  # where the element begins in chunk
    held = bytearray()  # the element's bytes from chunks before this one
    depth = 0
    expect_value = False
    while True:
        if at >= len(chunk):
            if state != _SEEK:
                if len(held) + len(chunk) - start > limit:
                    raise _record_limit_error("JSON array element", limit)
                held += chunk[start:]
                start = 0
            # An escape that ends a chunk skips the first byte of the next one.
            carry = at - len(chunk)
            base += len(chunk)
            chunk = f.read(chunk_size)
            if not chunk:
                raise _json_decode_error("unexpected end of data", base)
            view = memoryview(chunk)
            at = carry
            continue
        if state == _STRING:
            stop = _STRING_STOP.search(chunk, at)
            if stop is None:
                at = len(chunk)
            elif chunk[stop.start()] == _BACKSLASH:
                at = stop.end() + 1
            else:
                at = stop.end()
                state = _ELEMENT
            continue
        if state == _SEEK:
            guess = bracketed_element.match(chunk, at)
            if guess is not None and guess.start(2) - guess.start(1) <= limit:
                try:
                    value = orjson.loads(view[guess.start(1) : guess.end(1)])
                except orjson.JSONDecodeError:
                    pass  # misled by a string, or malformed: found exactly below
                else:
                    yield value
                    at = guess.end()
                    if chunk[at - 1] == _CLOSE_ARRAY:
                        _validate_after_array(f, chunk, at, base)
                        return
                    expect_value = True
                    continue
            found = _NON_WHITESPACE.search(chunk, at)
            if found is None:
                at = len(chunk)
                continue
            at = found.start()
            byte = chunk[at]
            if byte == _CLOSE_ARRAY:
                if expect_value:
                    raise _json_decode_error("trailing comma in array", base + at + 1)
                _validate_after_array(f, chunk, at + 1, base)
                return
            start = at
            held.clear()
            depth = 1 if byte in _OPENERS else 0
            state = _STRING if byte == _QUOTE else _ELEMENT
            at += 1
            continue
        run = (_NESTED_RUN if depth else _TOP_LEVEL_RUN).match(chunk, at)
        assert run is not None  # both patterns match the empty run
        at = run.end()
        if at >= len(chunk):
            continue
        byte = chunk[at]
        if byte == _QUOTE:
            state = _STRING
            at += 1
        elif byte in _OPENERS:
            depth += 1
            at += 1
        elif depth:
            depth -= 1
            at += 1
        elif len(held) + at - start > limit:
            raise _record_limit_error("JSON array element", limit)
        elif byte == _CLOSE_OBJECT:
            raise _json_decode_error("unexpected '}'", base + at + 1)
        else:
            # A top-level comma or closing bracket ends the element.
            element = bytes(held) + chunk[start:at] if held else chunk[start:at]
            yield orjson.loads(element.rstrip())
            at += 1
            if byte == _CLOSE_ARRAY:
                _validate_after_array(f, chunk, at, base)
                return
            expect_value = True
            state = _SEEK


def _validate_after_array(f: BinaryIO, chunk: bytes, at: int, base: int) -> None:
    """Refuse anything but whitespace after a root array's closing bracket.

    *chunk*, at file offset *base*, holds the bracket, and *at* is just past it.
    """
    trailing = _NON_WHITESPACE.search(chunk, at)
    if trailing is not None:
        raise _json_decode_error("unexpected trailing data", base + trailing.start())
    _validate_json_trailing_whitespace(f, base + len(chunk))


def _iter_root_array_records(
    f: BinaryIO,
    pos: int,
    *,  # pragma: no mutate
    limit: int,
    stats: ShredSkipStats | None,
) -> Iterator[dict[str, Any]]:
    """Each object element of a root JSON array, *f* just past its ``[``.

    *stats*, when given, counts each element that is not an object.
    """
    for value in _iter_root_array_values(f, pos, limit):
        if isinstance(value, dict):
            yield value
        elif stats is not None:
            stats.count_record_skip()


def _iter_records_for_inference(
    data_path: Path,
    *,  # pragma: no mutate
    sample_size: int | None,  # pragma: no mutate
) -> Iterator[dict[str, Any]]:
    if sample_size is None or sample_size <= 0:
        yield from _iter_records(data_path)
        return
    if data_path.suffix.lower() in (".jsonl", ".ndjson", ".xml"):  # pragma: no mutate
        yield from islice(_iter_records(data_path), sample_size)
        return
    yield from _iter_sampled_json_array_records(data_path, sample_size)


# Below this, process startup (plus build-only part-file round-trips) costs more
# than the serial walk saves.
_PARALLEL_MIN_BYTES = 64 * 1024 * 1024  # pragma: no mutate - performance tuning knob


# Target bytes of source JSON per chunk — the memory knob (see above).
_PARALLEL_CHUNK_BYTES = 64 * 1024 * 1024  # pragma: no mutate - performance tuning knob


# Workers beyond this show little gain and multiply peak memory.
_PARALLEL_MAX_WORKERS = 8  # pragma: no mutate - performance tuning knob


def _parallel_worker_count(chunk_count: int) -> int:
    """Workers to run for *chunk_count* chunks — never more than there is work."""
    cpu = os.cpu_count() or 1
    return max(1, min(_PARALLEL_MAX_WORKERS, cpu - 1, chunk_count))


def _jsonl_byte_ranges(data_path: Path, chunk_bytes: int) -> list[tuple[int, int]]:
    """Split a newline-delimited file into ``[start, end)`` byte ranges.

    Each boundary is advanced to just past the next newline so no range splits
    a record; consecutive ranges therefore tile the file exactly, with no gap
    and no overlap. Returned in file order — the order rows must keep.
    """
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    size = data_path.stat().st_size
    if size == 0:
        return []
    if size <= chunk_bytes:
        return [(0, size)]

    bounds = [0]
    record_limit = _structured_input_record_limit()
    with data_path.open("rb") as f:
        target = chunk_bytes
        while target < size:
            f.seek(target)
            partial_line = f.readline(record_limit + 1)
            if len(partial_line) > record_limit:
                raise _record_limit_error("JSONL record", record_limit)
            pos = f.tell()
            if pos >= size:
                break
            # Targets are strictly increasing and the discard only moves
            # forward, so every retained position lies beyond the last bound.
            bounds.append(pos)
            target = pos + chunk_bytes
    bounds.append(size)
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


def _iter_range_lines(data_path: Path, start: int, end: int) -> Iterator[bytes]:
    """Yield bounded raw JSONL lines beginning before the range's end."""
    record_limit = _structured_input_record_limit()
    with data_path.open("rb", buffering=_JSONL_READ_BUFFER_BYTES) as f:
        f.seek(start)
        remaining = end - start
        while remaining > 0:
            raw_line = f.readline(record_limit + 1)
            if not raw_line:
                break
            if len(raw_line) > record_limit:
                raise _record_limit_error("JSONL record", record_limit)
            remaining -= len(raw_line)
            yield raw_line


def _iter_range_records(
    data_path: Path,
    start: int,
    end: int,
    stats: ShredSkipStats | None = None,  # pragma: no mutate - type declaration
) -> Iterator[dict[str, Any]]:
    """Yield records from ``[start, end)`` of a newline-delimited file.

    Mirrors the JSONL arm of :func:`_iter_records` exactly — blank lines are
    formatting and never counted, and a non-object line is recorded through
    optional *stats* — but reads bytes so a range can be seeked to directly.
    ``orjson`` validates UTF-8 itself, so decoding stays inside the JSON parse.
    """
    for raw_line in _iter_range_lines(data_path, start, end):
        record = _jsonl_record(raw_line, stats)
        if record is not None:
            yield record


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _ChunkFailure:
    """Pickle-safe evidence needed to reconstruct a worker exception."""

    type_name: str
    module: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    doc: str | None = None  # pragma: no mutate
    pos: int | None = None  # pragma: no mutate
    errno: int | None = None  # pragma: no mutate
    strerror: str | None = None  # pragma: no mutate
    filename: Any = None
    winerror: int | None = None  # pragma: no mutate
    filename2: Any = None


def _failure_from_exception(exc: Exception) -> _ChunkFailure:
    """Capture only the stable, pickle-safe evidence for a worker failure."""
    if isinstance(exc, ApiInputSchemaError):
        return _ChunkFailure(
            type_name=type(exc).__name__,
            module=type(exc).__module__,
            message=exc.message,
            context=dict(exc.context),
        )
    if isinstance(exc, orjson.JSONDecodeError):
        return _ChunkFailure(
            type_name=type(exc).__name__,
            module=type(exc).__module__,
            message=exc.msg,
            doc=exc.doc,
            pos=exc.pos,
        )
    if isinstance(exc, OSError):
        return _ChunkFailure(
            type_name=type(exc).__name__,
            module=type(exc).__module__,
            message=str(exc),
            errno=exc.errno,
            strerror=exc.strerror,
            filename=exc.filename,
            winerror=getattr(exc, "winerror", None),
            filename2=exc.filename2,
        )
    return _ChunkFailure(
        type_name=type(exc).__name__,
        module=type(exc).__module__,
        message=str(exc),
    )


def _raise_worker_failure(failure: _ChunkFailure) -> NoReturn:
    """Reconstruct one ordinary worker failure in the parent process."""
    if failure.type_name == "ApiInputSchemaError":
        raise ApiInputSchemaError(failure.message, **failure.context)
    if failure.type_name == "JSONDecodeError":
        raise orjson.JSONDecodeError(failure.message, failure.doc or "", failure.pos or 0)

    os_error_types: dict[str, type[OSError]] = {
        "OSError": OSError,
        "BlockingIOError": BlockingIOError,
        "ChildProcessError": ChildProcessError,
        "ConnectionError": ConnectionError,
        "BrokenPipeError": BrokenPipeError,
        "ConnectionAbortedError": ConnectionAbortedError,
        "ConnectionRefusedError": ConnectionRefusedError,
        "ConnectionResetError": ConnectionResetError,
        "FileExistsError": FileExistsError,
        "FileNotFoundError": FileNotFoundError,
        "InterruptedError": InterruptedError,
        "IsADirectoryError": IsADirectoryError,
        "NotADirectoryError": NotADirectoryError,
        "PermissionError": PermissionError,
        "ProcessLookupError": ProcessLookupError,
        "TimeoutError": TimeoutError,
    }
    os_error_type = os_error_types.get(failure.type_name)
    if failure.module == "builtins" and os_error_type is not None:
        if failure.errno is None:
            raise os_error_type(failure.message)
        args: list[Any] = [failure.errno, failure.strerror]
        if failure.filename is not None:
            args.append(failure.filename)
            if failure.winerror is not None or failure.filename2 is not None:
                args.append(failure.winerror)
                if failure.filename2 is not None:
                    args.append(failure.filename2)
        raise os_error_type(*args)

    builtin_error_types: dict[str, type[Exception]] = {
        "MemoryError": MemoryError,
        "RuntimeError": RuntimeError,
        "ValueError": ValueError,
    }
    builtin_error_type = builtin_error_types.get(failure.type_name)
    if failure.module == "builtins" and builtin_error_type is not None:
        raise builtin_error_type(failure.message)

    qualified_type = f"{failure.module}.{failure.type_name}"
    raise RuntimeError(
        f"parallel json shred worker failed with {qualified_type}: {failure.message}"
    )


def _should_shred_in_parallel(data_path: Path) -> bool:
    """True when splitting *data_path* is both possible and worth it."""
    if data_path.suffix.lower() not in (".jsonl", ".ndjson"):
        return False
    if current_execution_context() is not None and current_native_memory_backend() not in {
        "cgroup",
        "windows_job",
    }:
        return False
    try:
        return data_path.stat().st_size >= _PARALLEL_MIN_BYTES
    except OSError:
        return False
