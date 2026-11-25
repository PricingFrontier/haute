"""What a frame holds in memory, counted with every string view (PL2-17).

Polars 2's ``estimated_size`` leaves out the 16-byte view of every String and
Binary value, so the RAM estimator's width probes, the preview cache budget and
the freed-memory accounting all read a column of short codes at under half its
size. ``haute._frame_bytes`` is the one place those figures come from.
"""

from __future__ import annotations

import re

import polars as pl
import pyarrow as pa
import pytest

from haute._frame_bytes import VIEW_BYTES, frame_bytes, series_bytes, series_bytes_per_row
from haute._ram_estimate import _detailed_parquet_metadata, _estimate_base_bytes_per_row
from tests._source_files import REPO_ROOT, source_files


def _arrow_view_bytes(values: list, view_type: pa.DataType) -> int:
    array = pa.array(values, type=view_type)
    return sum(buffer.size for buffer in array.buffers() if buffer is not None)


@pytest.mark.parametrize(
    "values",
    [
        ["a", "abcdefghijkl", "q-000001"],
        ["abcdefghijklm", "x" * 40, "quote-0000000001"],
        ["short", "x" * 13, "", "y" * 12, "z" * 100],
    ],
    ids=["inlined", "in-buffers", "both"],
)
def test_a_string_column_counts_its_views_as_arrow_holds_them(values: list[str]) -> None:
    assert series_bytes(pl.Series(values)) == _arrow_view_bytes(values, pa.string_view())
    encoded = [value.encode() for value in values]
    assert series_bytes(pl.Series(encoded)) == _arrow_view_bytes(encoded, pa.binary_view())


def test_a_string_column_with_nulls_counts_a_view_for_each_null() -> None:
    values = ["a", None, "x" * 20, None]

    # Arrow pads the validity bitmap to a whole byte; Polars counts its bits.
    assert abs(series_bytes(pl.Series(values)) - _arrow_view_bytes(values, pa.string_view())) <= 1


def test_short_codes_are_charged_their_views_where_estimated_size_is_not() -> None:
    codes = pl.Series([f"c{i % 50:05d}" for i in range(1_000)])

    assert codes.estimated_size() / codes.len() == 6
    assert series_bytes_per_row(codes) == VIEW_BYTES


def _arrow_held_bytes(series: pl.Series) -> int:
    """Arrow's own count of a single-chunk column's buffers, a shared one once."""
    (chunk,) = series.to_frame().to_arrow(compat_level=pl.CompatLevel.newest()).column(0).chunks
    return chunk.get_total_buffer_size()


def _masked(series: pl.Series) -> pl.Series:
    """*series* with its last row nulled by a mask, its children kept underneath."""
    keep = pl.Series([True] * (series.len() - 1) + [False])
    return pl.select(pl.when(keep).then(series).otherwise(None)).to_series()


_LISTS = pl.Series("s", [["x" * 20] * 100, ["y" * 5] * 100])
_ARRAYS = pl.Series("a", [["ab", "c" * 30], ["ef", "gh"]], dtype=pl.Array(pl.String, 2))
_STRUCTS = pl.DataFrame({"s": ["ab", "c" * 40], "t": ["e", None], "x": [1.0, 2.0]}).to_struct("st")
_LIST_OF_STRUCTS = pl.Series("ls", [[{"s": "ab"}], [{"s": "c" * 20}, {"s": None}]])


@pytest.mark.parametrize(
    "series",
    [
        _LISTS,
        _ARRAYS,
        _STRUCTS,
        _LIST_OF_STRUCTS,
        pl.Series([[1, 2], [3]]),
        _masked(_LISTS),
        _masked(_ARRAYS),
        _masked(_STRUCTS),
        _masked(_LIST_OF_STRUCTS),
    ],
    ids=[
        "list",
        "array",
        "struct",
        "list-of-struct",
        "list-of-int",
        "masked-list",
        "masked-array",
        "masked-struct",
        "masked-list-of-struct",
    ],
)
def test_a_nested_column_counts_every_buffer_it_holds(series: pl.Series) -> None:
    assert series_bytes(series) == _arrow_held_bytes(series)


def test_a_masked_list_still_counts_the_children_it_keeps() -> None:
    """A null over a list keeps its strings underneath; exploding it would not see them."""
    masked = _masked(_LISTS)

    assert masked.null_count() == 1
    assert series_bytes(masked) >= series_bytes(_LISTS)
    assert series_bytes(masked) > masked.estimated_size() + VIEW_BYTES * 100


def test_a_chunked_nested_column_counts_each_chunk_and_a_shared_buffer_once() -> None:
    other = pl.Series("s", [["z" * 30] * 50])
    chunked = pl.concat([_LISTS, other], rechunk=False)
    repeated = pl.concat([_LISTS, _LISTS], rechunk=False)

    assert chunked.n_chunks() == repeated.n_chunks() == 2
    assert series_bytes(chunked) == series_bytes(_LISTS) + series_bytes(other)
    assert series_bytes(repeated) == series_bytes(_LISTS)


def test_a_frame_is_the_sum_of_its_columns() -> None:
    frame = pl.DataFrame({"code": ["a", "b"], "amount": [1.0, 2.0]})

    assert frame_bytes(frame) == sum(series_bytes(column) for column in frame.get_columns())
    assert series_bytes_per_row(pl.Series([], dtype=pl.String)) == VIEW_BYTES
    with pytest.raises(TypeError, match="DataFrame"):
        frame_bytes(pl.Series([1]))  # type: ignore[arg-type]


def test_the_training_width_probe_charges_a_short_code_its_view(tmp_path) -> None:
    path = tmp_path / "codes.parquet"
    pl.DataFrame({"code": [f"c{i % 50:05d}" for i in range(5_000)]}).write_parquet(path)
    meta = _detailed_parquet_metadata(str(path))

    width = _estimate_base_bytes_per_row(
        1, target_columns=("code",), target_width_columns=("code",), sources=(meta,)
    )

    assert width >= VIEW_BYTES


@pytest.mark.meta
def test_no_source_outside_the_module_reads_estimated_size() -> None:
    """A direct ``estimated_size`` drops every string view from a memory figure."""
    module = REPO_ROOT / "src" / "haute" / "_frame_bytes.py"
    pattern = re.compile(r"\.estimated_size\(")
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in source_files(REPO_ROOT / "src" / "haute")
        if path != module
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_chunks_sharing_an_allocation_count_all_of_it_in_either_order() -> None:
    """A short prefix of a chunk shares its buffer's start: the whole buffer is still held."""
    pairs = pl.Series("x", [[1.0, 2.0]] * 20, dtype=pl.Array(pl.Float64, 2))
    held = 20 * 2 * 8

    assert series_bytes(pairs) == held
    assert series_bytes(pl.concat([pairs, pairs.head(2)], rechunk=False)) == held
    assert series_bytes(pl.concat([pairs.head(2), pairs], rechunk=False)) == held
    assert series_bytes(pl.concat([pairs.tail(3), pairs], rechunk=False)) == held
