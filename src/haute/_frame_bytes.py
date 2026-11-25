"""What a Polars frame or series holds in memory: the one source of those figures.

Polars 2 holds every String and Binary value in a 16-byte view; a value longer
than 12 bytes also keeps its payload in a data buffer, and a shorter one sits
inside its view. ``estimated_size`` counts every payload byte but no view, so a
column of short codes reads at under half what it holds. A nested column (a list,
array or struct) also keeps the child storage of a row a null masks, which
``estimated_size`` and an ``explode`` both leave out, so it is counted from the
Arrow buffers it holds.
Every memory figure Haute takes from a frame or series (admission widths,
cache budgets, buffered bytes, freed bytes) comes from here, and a scan in
``tests/test_frame_bytes.py`` refuses ``estimated_size`` anywhere else.
"""

from __future__ import annotations

import polars as pl
from polars import CompatLevel

from haute._polars_dtypes import ColumnKind, column_kind

VIEW_BYTES = 16
"""The view every String and Binary value has."""
VIEW_INLINE_BYTES = 12
"""The longest value a view holds itself; a longer one is in a data buffer."""

#: The kinds Polars holds in views: String and Binary.
_VIEW_KINDS: frozenset[ColumnKind] = frozenset({"text", "binary"})


def series_bytes(series: pl.Series) -> int:
    """The bytes *series* holds in memory.

    A String or Binary column is counted exactly: its views, the payloads too long
    to inline, and its validity. A nested column is every Arrow buffer it holds,
    each once, so a masked row's children and a slice's parent buffers count: what
    the column keeps alive, never less.
    """
    if not isinstance(series, pl.Series):
        raise TypeError("series must be a Polars Series")
    estimated = int(series.estimated_size())
    if estimated < 0:
        raise ValueError(f"Polars estimated_size returned {estimated} bytes for {series.name!r}")
    kind = column_kind(series.dtype)
    if kind in _VIEW_KINDS:
        lengths = series.bin.size() if kind == "binary" else series.str.len_bytes()
        inlined = int(lengths.filter(lengths <= VIEW_INLINE_BYTES).sum() or 0)
        return estimated - inlined + VIEW_BYTES * series.len()
    if kind == "nested":
        return _arrow_buffer_bytes(series)
    return estimated


def frame_bytes(frame: pl.DataFrame) -> int:
    """The bytes *frame* holds in memory, column by column as :func:`series_bytes`."""
    if not isinstance(frame, pl.DataFrame):
        raise TypeError("frame must be a Polars DataFrame")
    return sum(series_bytes(column) for column in frame.get_columns())


def series_bytes_per_row(series: pl.Series) -> float:
    """:func:`series_bytes` per row; an empty String or Binary column is one view."""
    if not isinstance(series, pl.Series):
        raise TypeError("series must be a Polars Series")
    if series.len() == 0:
        return float(VIEW_BYTES) if column_kind(series.dtype) in _VIEW_KINDS else 0.0
    return series_bytes(series) / series.len()


def _arrow_buffer_bytes(series: pl.Series) -> int:
    """The bytes of every Arrow buffer *series* holds, each counted once."""
    # A frame's chunks export without a copy, and ``Array.buffers()`` lists a nested
    # array's children's buffers too. Chunks can share one allocation, each seeing all
    # of it or a slice, so the bytes are the union of the address ranges they cover.
    column = series.to_frame().to_arrow(compat_level=CompatLevel.newest()).column(0)
    extents = sorted(
        (buffer.address, buffer.address + buffer.size)
        for chunk in column.chunks
        for buffer in chunk.buffers()
        if buffer is not None and buffer.size > 0
    )
    total = 0
    covered_to = 0
    for start, end in extents:
        if end <= covered_to:
            continue
        total += end - max(start, covered_to)
        covered_to = end
    return total
