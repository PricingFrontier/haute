"""How Haute reads the bytes of an input's file, and what it says of one it cannot read.

The rules every reader of an input's own file holds to (the JSON shredder behind a Quote
Input, Load File, the CSV readers of a Data Input and a Quote Input, a deployment's
served request), so one file reads alike whichever node reads it (specs/io-layer,
"Reading an input's bytes"):

- A leading UTF-8 byte-order mark is read through, never taken for content: feeds and
  Windows tools write one, and they are not the analyst's to control.
- A JSON object that repeats a key keeps its last value, as orjson, Python and JavaScript
  read it. Nothing is refused for it. (A Data Input's JSON is read by Polars, by its
  own rules.)
- A file whose bytes cannot be read is an :class:`~haute.errors.InputFileError` naming
  the file from the project root and the line and column of the fault.
"""

from __future__ import annotations

import codecs
import gzip
import io
import json
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, cast

import orjson

from haute.errors import InputFileError

UTF8_BOM = b"\xef\xbb\xbf"

# The bytes read at a time when a fault's line and column are counted.
_POSITION_CHUNK_BYTES = 1024 * 1024


def bom_length(head: bytes) -> int:
    """The length of the UTF-8 byte-order mark *head* starts with: 3, or 0 without one."""
    return len(UTF8_BOM) if head.startswith(UTF8_BOM) else 0


def without_bom(raw: bytes) -> bytes:
    """*raw* without the UTF-8 byte-order mark it may start with."""
    return raw[bom_length(raw) :]


# ---------------------------------------------------------------------------
# The bytes a reader takes
# ---------------------------------------------------------------------------

# The starts of the compressed streams Polars' CSV and JSON Lines readers decompress, as
# Polars recognises them: gzip, zlib (each of its three usual headers) and zstd.
_GZIP = b"\x1f\x8b"
_ZLIB = (b"\x78\x01", b"\x78\x9c", b"\x78\xda")
_ZSTD = b"\x28\xb5\x2f\xfd"


class _InflatingReader(io.RawIOBase):
    """A zlib stream's bytes, inflated a bounded piece at a time, so a small stream that
    inflates to a great deal is never held whole."""

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._inflate = zlib.decompressobj()
        self._pending = b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while not self._pending:
            source = self._inflate.unconsumed_tail or self._raw.read(_POSITION_CHUNK_BYTES)
            if not source:
                self._pending = self._inflate.flush()
                if not self._pending:
                    return 0
                break
            self._pending = self._inflate.decompress(source, _POSITION_CHUNK_BYTES)
        taken = min(len(buffer), len(self._pending))
        buffer[:taken] = self._pending[:taken]
        self._pending = self._pending[taken:]
        return taken


@contextmanager
def open_input_bytes(path: str | Path) -> Iterator[BinaryIO]:
    """The bytes of the file *path* as Polars' text readers take them: decompressed when
    the file starts as a gzip, zlib or zstd stream, which Polars decompresses whatever the
    file's name, else as they are. Every judgement of a text file's bytes here reads them
    so, and a fault's line and column count in them."""
    with Path(path).open("rb") as raw:
        head = raw.read(len(_ZSTD))
        raw.seek(0)
        if head.startswith(_GZIP):
            with gzip.GzipFile(fileobj=raw) as inflated:
                yield cast(BinaryIO, inflated)
        elif head[:2] in _ZLIB:
            yield cast(BinaryIO, io.BufferedReader(_InflatingReader(raw)))
        elif head.startswith(_ZSTD):
            import pyarrow as pa

            with pa.input_stream(raw, compression="zstd") as inflated:
                yield cast(BinaryIO, inflated)
        else:
            yield raw


# ---------------------------------------------------------------------------
# Where a fault is
# ---------------------------------------------------------------------------


def _column(line_text: bytes, *, first_line: bool) -> int:
    """The column, from one and in characters, just past *line_text*: the text of a line
    up to a byte. A byte-order mark leading the file is no character an editor shows."""
    if first_line:
        line_text = without_bom(line_text)
    return len(line_text.decode("utf-8", errors="replace")) + 1


def file_position(path: str | Path, offset: int) -> tuple[int, int]:
    """The line and column of byte *offset* in the file *path*, both from one, the column
    in characters, so they point an editor at the byte. The file is read a chunk at a
    time, so a fault far into a large file is placed without holding the file."""
    lines = 0
    line_start = 0
    read = 0
    with open_input_bytes(path) as source:
        while read < offset:
            chunk = source.read(min(_POSITION_CHUNK_BYTES, offset - read))
            if not chunk:
                break
            newlines = chunk.count(b"\n")
            if newlines:
                lines += newlines
                line_start = read + chunk.rfind(b"\n") + 1
            read += len(chunk)
    with open_input_bytes(path) as source:
        source.read(line_start)
        current_line = source.read(max(0, min(offset, read) - line_start))
    return lines + 1, _column(current_line, first_line=line_start == 0)


def display_name(path: str | Path) -> str:
    """The file as a message names it (``haute._failure_answers.display_file``)."""
    from haute._failure_answers import display_file

    return display_file(str(path))


# ---------------------------------------------------------------------------
# A file that cannot be read
# ---------------------------------------------------------------------------


def not_utf8_error(path: str | Path, offset: int, byte: int) -> InputFileError:
    """The file *path*, whose byte *byte* at *offset* is not UTF-8, refused."""
    name = display_name(path)
    line, column = file_position(path, offset)
    return InputFileError(
        f"{name} is not valid UTF-8: byte 0x{byte:02X} at line {line}, column {column}. "
        "Save it as UTF-8.",
        file=name,
        line=line,
        column=column,
    )


def not_utf8_file_error(path: str | Path) -> InputFileError | None:
    """The refusal of the file *path* at its first byte that is not UTF-8, else ``None``.

    The file is decoded a chunk at a time, so a fault far into a large file is found
    without holding the file.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    read = 0
    with open_input_bytes(path) as source:
        while chunk := source.read(_POSITION_CHUNK_BYTES):
            pending = len(decoder.getstate()[0])
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                # The error's bytes are the ones held from the last chunk, then this one.
                return not_utf8_error(path, read - pending + exc.start, exc.object[exc.start])
            read += len(chunk)
        pending = len(decoder.getstate()[0])
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            return not_utf8_error(path, read - pending + exc.start, exc.object[exc.start])
    return None


def json_syntax_error(path: str | Path, offset: int, words: str) -> InputFileError:
    """The file *path*, whose JSON text is not JSON at byte *offset*, refused in *words*."""
    name = display_name(path)
    line, column = file_position(path, offset)
    return InputFileError(
        f"{name} is not valid JSON: {words} at line {line}, column {column}.",
        file=name,
        line=line,
        column=column,
    )


#: What a request file holding no quotes holds instead of nothing.
NO_QUOTES = "A JSON file that holds no quotes is []."


def empty_json_error(path: str | Path, *, instead: str = "") -> InputFileError:
    """The JSON file *path*, which holds nothing, refused, saying what to write *instead*
    when the reader has a value for nothing (a request file's :data:`NO_QUOTES`)."""
    name = display_name(path)
    return InputFileError(f"{name} is empty, so it is not JSON. {instead}".rstrip(), file=name)


_ORJSON_NOT_UTF8 = "str is not valid UTF-8"


def decode_error_offset(
    raw: bytes | bytearray | memoryview, exc: orjson.JSONDecodeError
) -> tuple[int, str]:
    """Where in *raw* orjson's *exc* found its fault, in bytes, and its words.

    orjson counts a fault's position in characters of the decoded text, and says
    nothing of where text that is not UTF-8 stops being so: that is found by decoding
    *raw* strictly.
    """
    if exc.msg.startswith(_ORJSON_NOT_UTF8):
        try:
            bytes(raw).decode("utf-8")
        except UnicodeDecodeError as bad:
            return bad.start, _ORJSON_NOT_UTF8
    return len(exc.doc[: exc.pos].encode("utf-8", errors="surrogatepass")), exc.msg


def file_json_error(path: str | Path, offset: int, words: str) -> InputFileError:
    """A fault orjson found at byte *offset* of *path*, as the file's refusal: bytes that
    are not UTF-8 named by their byte, anything else as JSON that is not JSON."""
    if words == _ORJSON_NOT_UTF8:
        with Path(path).open("rb") as source:
            # The shredder reads a request file's bytes as they are: offsets count in them.
            source.seek(offset)
            byte = source.read(1)
        return not_utf8_error(path, offset, byte[0] if byte else 0)
    return json_syntax_error(path, offset, words)


# ---------------------------------------------------------------------------
# A text file saved in another encoding than UTF-8
# ---------------------------------------------------------------------------

# The bytes a text file's encoding is judged by: its byte-order mark and its first line.
_ENCODING_PROBE_BYTES = 4096

# The byte-order marks of the encodings that are not UTF-8, longest first: UTF-32's
# little-endian mark begins with UTF-16's.
_OTHER_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\xfe\xff", "UTF-32 (big-endian)"),
    (b"\xff\xfe\x00\x00", "UTF-32 (little-endian)"),
    (b"\xfe\xff", "UTF-16 (big-endian)"),
    (b"\xff\xfe", "UTF-16 (little-endian)"),
)


def _encoding_without_mark(first_line: bytes) -> str:
    """The encoding a first line holding null bytes is in, by where they fall (UTF-16 and
    UTF-32 spell an ASCII character with one or three null bytes beside it), or ``""``
    when they fall otherwise."""
    head = first_line[:4]
    if len(head) == 4 and head[1:] == b"\x00\x00\x00":
        return "UTF-32 (little-endian)"
    if len(head) == 4 and head[:3] == b"\x00\x00\x00":
        return "UTF-32 (big-endian)"
    if first_line[1:2] == b"\x00":
        return "UTF-16 (little-endian)"
    if first_line[:1] == b"\x00":
        return "UTF-16 (big-endian)"
    return ""


def text_encoding_refusal(path: str | Path) -> InputFileError | None:
    """The refusal of a text file *path* saved in another encoding than UTF-8, else ``None``.

    A UTF-16 or UTF-32 byte-order mark, or a null byte in the first line, which no UTF-8
    text holds, is a file in another encoding: Windows PowerShell 5.1 writes UTF-16 when
    output is redirected to a file. Read as UTF-8 it gives columns named with null
    characters, so it is refused naming the encoding found. Only the file's first bytes
    are read. A UTF-8 byte-order mark is UTF-8, and is read through.
    """
    with open_input_bytes(path) as source:
        head = source.read(_ENCODING_PROBE_BYTES)
    found = next((name for mark, name in _OTHER_BOMS if head.startswith(mark)), None)
    if found is None:
        first_line = without_bom(head).split(b"\n", 1)[0]
        if b"\x00" not in first_line:
            return None
        found = _encoding_without_mark(first_line)
    name = display_name(path)
    saved = (
        f"is saved as {found}, not UTF-8"
        if found
        else "is not UTF-8 text: its first line holds null bytes"
    )
    return InputFileError(f"{name} {saved}. Save it as UTF-8.", file=name)


def refuse_text_in_another_encoding(path: str | Path) -> None:
    """Raise :func:`text_encoding_refusal` of *path* when it has one."""
    refusal = text_encoding_refusal(path)
    if refusal is not None:
        raise refusal


# ---------------------------------------------------------------------------
# A JSON file loaded whole
# ---------------------------------------------------------------------------


def load_json_file(path: str | Path) -> Any:
    """The JSON value of the file *path*, as Load File loads it.

    The bytes are read as UTF-8, strictly: a byte that is not UTF-8 is refused naming the
    file and the byte's place, never replaced, so a value (or a key a lookup then never
    matches) is never changed without a word. A leading byte-order mark is read through;
    a file holding nothing is refused as empty. Python's own JSON reader parses the
    text, as before, so ``NaN``, integers of any size and a repeated key's last value
    are read as they were.
    """
    raw = Path(path).read_bytes()
    start = bom_length(raw)
    try:
        text = raw[start:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise not_utf8_error(path, start + exc.start, raw[start + exc.start]) from None
    if not text.strip():
        raise empty_json_error(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        offset = start + len(text[: exc.pos].encode("utf-8"))
        raise json_syntax_error(path, offset, exc.msg) from None


def refuse_json_lines_with_a_mark(path: str | Path) -> None:
    """Refuse a Data Input's JSON Lines file that starts with a byte-order mark, in words.

    Polars' JSON Lines reader cannot read through the mark, and a scan cannot skip it
    without copying the file, so the file is refused naming it and the mark, where Polars
    would say ``InternalError(TapeError) at character 0``. Haute's own readers read
    through the mark.
    """
    with open_input_bytes(path) as source:
        if bom_length(source.read(len(UTF8_BOM))) == 0:
            return
    name = display_name(path)
    raise InputFileError(
        f"{name} starts with a byte-order mark, which a Data Input cannot read a JSON Lines "
        "file through. Save it as UTF-8 without one.",
        file=name,
        line=1,
        column=1,
    )
