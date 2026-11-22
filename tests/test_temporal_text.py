"""The exact text of a temporal value: Python's spelling, to the nanosecond."""

from __future__ import annotations

import random
from datetime import UTC, datetime, time, timedelta

import polars as pl
import pytest

from haute._temporal_text import (
    TemporalTextError,
    temporal_frame,
    temporal_physical,
    temporal_text,
    with_temporal_text,
)
from haute.errors import SchemaMismatchError

_SEED = 20261122
_COUNT = 1_500


def _written(series: pl.Series, *, separator: str = "T") -> list[str | None]:
    frame = series.to_frame("value")
    expression = temporal_text(pl.col("value"), series.dtype, separator=separator)
    return frame.select(expression.alias("text")).to_series().to_list()


#: Years 1 to 9999 in seconds from 1970, as far as a Python datetime reaches; a
#: nanosecond column reaches only 1677 to 2262.
_FIRST_SECOND, _LAST_SECOND = -62_135_596_800 + 86_400, 253_402_300_799 - 86_400
_NANOSECOND_SECONDS = 2**63 // 1_000_000_000 - 1
#: 1901 to 2037: past 2037 Polars stops applying daylight saving that Python's zoneinfo
#: carries on, so the two write one instant in different local times.
_ZONED_FIRST_SECOND, _ZONED_LAST_SECOND = -2_177_452_800, 2_145_916_800


def _microsecond_values(rng: random.Random, unit: str, zone: str | None) -> list[int]:
    """Physical values a Python datetime holds exactly, whole seconds and fractions alike."""
    per_second = {"ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
    step = per_second // min(per_second, 1_000_000)
    first, last = (
        (-_NANOSECOND_SECONDS, _NANOSECOND_SECONDS)
        if unit == "ns"
        else (_FIRST_SECOND, _LAST_SECOND)
    )
    if zone not in (None, "UTC"):
        first, last = max(first, _ZONED_FIRST_SECOND), min(last, _ZONED_LAST_SECOND)
    seconds = [rng.randint(first, last) for _ in range(_COUNT)]
    values = [second * per_second for second in seconds[: _COUNT // 3]]
    values += [second * per_second + rng.randrange(per_second) for second in seconds]
    return [value - value % step for value in values]


@pytest.mark.parametrize("unit", ["ms", "us", "ns"])
@pytest.mark.parametrize("zone", [None, "UTC", "Europe/London", "Asia/Kolkata"])
@pytest.mark.parametrize("separator", ["T", " "])
def test_a_datetime_python_holds_is_written_as_python_writes_it(
    unit: str, zone: str | None, separator: str
) -> None:
    rng = random.Random(f"{_SEED}{unit}{zone}")
    values = _microsecond_values(rng, unit, zone)
    series = pl.Series(values, dtype=pl.Int64).cast(pl.Datetime(unit, zone))  # type: ignore[arg-type]

    expected = [moment.isoformat(sep=separator) for moment in series.to_list()]

    assert _written(series, separator=separator) == expected


@pytest.mark.parametrize("zone", [None, "Europe/London"])
def test_a_datetime_below_a_microsecond_keeps_all_nine_digits(zone: str | None) -> None:
    base = 1_000_000_000 * 1_000_000_000
    series = pl.Series(
        [base, base + 100, base + 200, base + 1_000, base + 500_000_000, None], dtype=pl.Int64
    ).cast(pl.Datetime("ns", zone))
    # 01:46:40 UTC on 9 September 2001 is 02:46:40 in London's summer time.
    day = "2001-09-09T01:46:40" if zone is None else "2001-09-09T02:46:40"
    offset = "+01:00" if zone == "Europe/London" else ""

    assert _written(series) == [
        f"{day}{offset}",
        f"{day}.000000100{offset}",
        f"{day}.000000200{offset}",
        f"{day}.000001{offset}",
        f"{day}.500000{offset}",
        None,
    ]


def test_each_occurrence_of_a_repeated_local_time_keeps_its_offset() -> None:
    series = (
        pl.Series([datetime(2024, 10, 27, 0, 30), datetime(2024, 10, 27, 1, 30)])
        .dt.replace_time_zone("UTC")
        .dt.convert_time_zone("Europe/London")
        .dt.cast_time_unit("ns")
    )

    assert _written(series) == ["2024-10-27T01:30:00+01:00", "2024-10-27T01:30:00+00:00"]
    assert [moment.astimezone(UTC).hour for moment in series.to_list()] == [0, 1]


def test_a_time_is_written_to_the_nanosecond() -> None:
    rng = random.Random(_SEED)
    microseconds = [rng.randrange(86_400_000_000) for _ in range(_COUNT)]
    microseconds += [0, 1, 86_399_999_999, 1_500_000]
    held = pl.Series([value * 1_000 for value in microseconds], dtype=pl.Int64).cast(pl.Time)
    finer = pl.Series([100, 1_000_000_200, None], dtype=pl.Int64).cast(pl.Time)

    assert _written(held) == [moment.isoformat() for moment in held.to_list()]
    # Polars' own String cast writes "00:00:01" for 1.5 s: its whole fraction is lost.
    assert _written(pl.Series([time(0, 0, 1, 500_000)])) == ["00:00:01.500000"]
    assert _written(finer) == ["00:00:00.000000100", "00:00:01.000000200", None]


@pytest.mark.parametrize("unit", ["ms", "us", "ns"])
def test_a_duration_is_written_as_str_timedelta_writes_it(unit: str) -> None:
    rng = random.Random(f"{_SEED}{unit}")
    per_microsecond = {"ms": None, "us": 1, "ns": 1_000}[unit]
    if per_microsecond is None:
        physical = [rng.randint(-(10**14), 10**14) for _ in range(_COUNT)] + [0, 1, -1]
        microseconds = [value * 1_000 for value in physical]
    else:
        microseconds = [rng.randint(-(2**52), 2**52) for _ in range(_COUNT)] + [0, 1, -1]
        physical = [value * per_microsecond for value in microseconds]
    series = pl.Series(physical, dtype=pl.Int64).cast(pl.Duration(unit))  # type: ignore[arg-type]

    assert _written(series) == [str(timedelta(microseconds=value)) for value in microseconds]


def test_a_duration_below_a_microsecond_keeps_all_nine_digits() -> None:
    series = pl.Series([100, -100, 86_400_000_000_001, None], dtype=pl.Int64).cast(
        pl.Duration("ns")
    )

    assert _written(series) == [
        "0:00:00.000000100",
        "-1 day, 23:59:59.999999900",
        "1 day, 0:00:00.000000001",
        None,
    ]


def test_a_value_of_another_kind_is_refused_by_its_type() -> None:
    with pytest.raises(SchemaMismatchError, match="Int64 is not a datetime, time or duration"):
        temporal_text(pl.col("value"), pl.Int64())


#: 2024-03-01T09:00:00 as nanoseconds from 1970: a pandas-written timestamp's unit.
_NINE_AM = 1_709_283_600 * 1_000_000_000


def test_a_nested_temporal_value_is_written_with_all_its_digits() -> None:
    """A datetime, time or duration inside a struct, a list or an array is read as text too:
    Python would cut it to six digits wherever it sits."""
    frame = pl.DataFrame(
        {
            "event": [{"at": _NINE_AM + 100, "n": 1}, None],
            "waits": [[100, None], None],
            "clocks": [[300, 400], [500, 600]],
            "n": [1, 2],
        },
        schema={
            "event": pl.Struct({"at": pl.Datetime("ns"), "n": pl.Int64}),
            "waits": pl.List(pl.Duration("ns")),
            "clocks": pl.Array(pl.Time, 2),
            "n": pl.Int64,
        },
    )
    expected = [
        {
            "event": {"at": "2024-03-01T09:00:00.000000100", "n": 1},
            "waits": ["0:00:00.000000100", None],
            "clocks": ["00:00:00.000000300", "00:00:00.000000400"],
            "n": 1,
        },
        {
            "event": None,
            "waits": None,
            "clocks": ["00:00:00.000000500", "00:00:00.000000600"],
            "n": 2,
        },
    ]

    assert with_temporal_text(frame).to_dicts() == expected
    assert with_temporal_text(frame.lazy()).collect().to_dicts() == expected


def test_a_frame_is_read_back_from_its_exact_text() -> None:
    """``temporal_frame`` is ``with_temporal_text`` undone: every digit, at any depth, an
    aware column's instant included, and a field the rows leave out a null."""
    schema = pl.Schema(
        {
            "at": pl.Datetime("ns", "Europe/London"),
            "events": pl.List(pl.Struct({"clock": pl.Time, "wait": pl.Duration("ns")})),
            "n": pl.Int64,
        }
    )
    source = pl.DataFrame(
        {
            "at": [_NINE_AM + 100, None],
            "events": [[{"clock": 300, "wait": -400}], []],
            "n": [1, 2],
        },
        schema={
            "at": pl.Int64,
            "events": pl.List(pl.Struct({"clock": pl.Int64, "wait": pl.Int64})),
            "n": pl.Int64,
        },
    ).cast(dict(schema))  # type: ignore[arg-type]
    rows = with_temporal_text(source).to_dicts()
    assert rows[0]["at"] == "2024-03-01T09:00:00.000000100+00:00"

    read = temporal_frame([rows[0], {"n": 2, "events": []}], schema)

    assert read.schema == schema
    assert read.select(pl.all().to_physical()).equals(source.select(pl.all().to_physical()))


def test_text_finer_than_its_column_is_refused_when_a_frame_is_read() -> None:
    with pytest.raises(TemporalTextError, match="finer than a us datetime holds"):
        temporal_frame(
            [{"at": "2024-03-01T09:00:00.000000100"}], pl.Schema({"at": pl.Datetime("us")})
        )


@pytest.mark.parametrize(
    "text",
    [
        "2024-03-01T09:00:00,123456789",
        "2024-03-01T09:00:00.123456789+0000",
        "2024-03-01T09:00:00.123456789+00:00",
    ],
)
def test_every_accepted_iso_spelling_of_a_part_second_is_read_exactly(text: str) -> None:
    """A comma before the part-second and an offset without colons are ISO 8601's too:
    each is read to the nanosecond, never cut to whole seconds."""
    zoned = "+" in text
    dtype = pl.Datetime("ns", "UTC" if zoned else None)

    assert temporal_physical(text, dtype) % 1_000_000_000 == 123_456_789
    with pytest.raises(TemporalTextError, match="finer than a us datetime holds"):
        temporal_physical(text, pl.Datetime("us", "UTC" if zoned else None))


def test_text_of_another_shape_is_refused_rather_than_read_in_part() -> None:
    with pytest.raises(TemporalTextError, match="is not a datetime"):
        temporal_physical("2024-03-01T09:00:00.1.2", pl.Datetime("ns"))


def test_a_multidimensional_temporal_array_is_read_back_whole() -> None:
    frame = pl.DataFrame(
        {"a": pl.Series([[[1, 2], [3, 4]], [[5, 100], [7, 8]]], dtype=pl.Array(pl.Int64, (2, 2)))}
    ).with_columns(pl.col("a").cast(pl.Array(pl.Datetime("ns"), (2, 2))))

    back = temporal_frame(with_temporal_text(frame).to_dicts(), frame.schema)

    assert back.schema == frame.schema
    assert back["a"].cast(pl.Array(pl.Int64, (2, 2))).to_list() == [
        [[1, 2], [3, 4]],
        [[5, 100], [7, 8]],
    ]
