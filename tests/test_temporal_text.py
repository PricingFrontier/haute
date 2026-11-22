"""The exact text of a temporal value: Python's spelling, to the nanosecond."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta

import polars as pl
import pytest

from haute._temporal_text import (
    TemporalTextError,
    exact_row,
    exact_rows,
    exact_text,
    temporal_literal,
    temporal_physical,
    temporal_text,
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


# ---------------------------------------------------------------------------
# Rows read through exact text, and text read back as a value
# ---------------------------------------------------------------------------


def _physical(series: pl.Series) -> list[int | None]:
    return series.to_physical().to_list()


def _read_back(series: pl.Series) -> list[int]:
    return [temporal_physical(text, series.dtype) for text in _written(series) if text is not None]


@pytest.mark.parametrize("unit", ["ms", "us", "ns"])
@pytest.mark.parametrize("zone", [None, "UTC", "Europe/London", "Asia/Kolkata"])
def test_a_datetimes_text_reads_back_as_its_own_value(unit: str, zone: str | None) -> None:
    rng = random.Random(f"{_SEED}back{unit}{zone}")
    per_second = {"ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
    first, last = (
        (-_NANOSECOND_SECONDS, _NANOSECOND_SECONDS)
        if unit == "ns"
        else (_FIRST_SECOND, _LAST_SECOND)
    )
    if zone not in (None, "UTC"):
        first, last = max(first, _ZONED_FIRST_SECOND), min(last, _ZONED_LAST_SECOND)
    values = [
        rng.randint(first, last) * per_second + rng.randrange(per_second) for _ in range(_COUNT)
    ]
    series = pl.Series(values, dtype=pl.Int64).cast(pl.Datetime(unit, zone))  # type: ignore[arg-type]

    assert _read_back(series) == values
    frame = series.to_frame("v").with_columns(
        same=pl.col("v") == temporal_literal(_written(series)[0] or "", series.dtype)
    )
    assert frame["same"].to_list() == [value == values[0] for value in values]


def test_each_occurrence_of_a_repeated_local_time_reads_back_as_its_own_instant() -> None:
    series = (
        pl.Series([datetime(2024, 10, 27, 0, 30), datetime(2024, 10, 27, 1, 30)])
        .dt.replace_time_zone("UTC")
        .dt.convert_time_zone("Europe/London")
        .dt.cast_time_unit("ns")
    )

    assert _read_back(series) == _physical(series)


def test_a_times_and_a_durations_text_reads_back_as_its_own_value() -> None:
    rng = random.Random(f"{_SEED}clock")
    clocks = [rng.randrange(86_400 * 1_000_000_000) for _ in range(_COUNT)] + [
        0,
        100,
        1_500_000_000,
    ]
    times = pl.Series(clocks, dtype=pl.Int64).cast(pl.Time)
    spans = [rng.randint(-(2**62), 2**62) for _ in range(_COUNT)] + [0, 100, -100, -1]
    for unit, step in (("ns", 1), ("us", 1_000), ("ms", 1_000_000)):
        physical = [span // step for span in spans]
        durations = pl.Series(physical, dtype=pl.Int64).cast(pl.Duration(unit))  # type: ignore[arg-type]
        assert _read_back(durations) == physical, unit

    assert _read_back(times) == clocks


@pytest.mark.parametrize(
    ("text", "dtype", "expected"),
    [
        # What datetime.fromisoformat accepts reads as it would, its fraction read whole.
        ("2024-03-01 09:00:00", pl.Datetime("us"), datetime(2024, 3, 1, 9)),
        ("2024-03-01T09:00", pl.Datetime("us"), datetime(2024, 3, 1, 9)),
        ("2024-03-01T09:00:00,5", pl.Datetime("ms"), datetime(2024, 3, 1, 9, 0, 0, 500_000)),
        ("2024-03-01T09:00:00Z", pl.Datetime("us", "UTC"), datetime(2024, 3, 1, 9, tzinfo=UTC)),
        ("2024-03-01", pl.Datetime("us"), datetime(2024, 3, 1)),
        ("09:30", pl.Time(), time(9, 30)),
    ],
)
def test_a_value_is_read_from_any_spelling_isoformat_reads(
    text: str, dtype: pl.DataType, expected: datetime | time
) -> None:
    assert pl.select(temporal_literal(text, dtype)).item() == expected


def test_a_fraction_past_six_digits_is_read_whole() -> None:
    dtype = pl.Datetime("ns")
    base = temporal_physical("2024-03-01T09:00:00", dtype)

    assert temporal_physical("2024-03-01T09:00:00.000000100", dtype) == base + 100
    assert temporal_physical("2024-03-01T09:00:00.0000001", dtype) == base + 100
    assert temporal_physical("09:00:00.000000100", pl.Time()) == 9 * 3_600 * 10**9 + 100


@pytest.mark.parametrize(
    "text",
    [
        # Python reads a fraction as a fraction of a second wherever the clock stops.
        "2024-03-01T09:30.5",
        "2024-03-01T0930.5",
        "2024-03-01 09:30:00,5",
        "20240301T093000.5",
        "2024-03-01T09:30:00.5+01:00",
        # An offset's own fraction is the offset's, not the clock's.
        "2024-03-01T09:00:00+01:00:00.5",
        "2024-03-01T09:00:00.25+01:00:00.5",
    ],
)
def test_a_spelling_python_reads_is_read_as_python_reads_it(text: str) -> None:
    from datetime import datetime

    dtype = pl.Datetime("us", "UTC") if "+" in text else pl.Datetime("us")
    moment = datetime.fromisoformat(text)

    assert pl.select(temporal_literal(text, dtype)).item() == moment


def test_digits_past_six_of_a_fraction_after_minutes_are_kept_and_held_to_the_unit() -> None:
    nanoseconds = pl.Datetime("ns")
    base = temporal_physical("2024-03-01T09:30:00", nanoseconds)

    assert temporal_physical("2024-03-01T09:30.0000001", nanoseconds) == base + 100
    assert temporal_physical("09:30.0000002", pl.Time()) == (9 * 60 + 30) * 60 * 10**9 + 200
    with pytest.raises(TemporalTextError) as refused:
        temporal_physical("2024-03-01T09:30.0000001", pl.Datetime("us"))
    assert refused.value.reason == "finer"


@pytest.mark.parametrize(
    ("text", "dtype", "reason"),
    [
        ("2024-03-01T09:00:00.000000100", pl.Datetime("us"), "finer"),
        ("2024-03-01T09:00:00.0001", pl.Datetime("ms"), "finer"),
        ("0:00:00.000000100", pl.Duration("us"), "finer"),
        ("2024-03-01T09:00:00", pl.Datetime("us", "UTC"), "zone"),
        ("2024-03-01T09:00:00+01:00", pl.Datetime("us"), "zone"),
        ("09:00:00+01:00", pl.Time(), "zone"),
        ("2024-03-01T09:00:00.0000000001", pl.Datetime("ns"), "malformed"),
        ("yesterday", pl.Datetime("us"), "malformed"),
        ("1 hour", pl.Duration("us"), "malformed"),
        ("9999-01-01T00:00:00", pl.Datetime("ns"), "range"),
    ],
)
def test_text_its_column_cannot_hold_is_refused_saying_why(
    text: str, dtype: pl.DataType, reason: str
) -> None:
    with pytest.raises(TemporalTextError) as refused:
        temporal_physical(text, dtype)

    assert refused.value.reason == reason
    assert text in str(refused.value)


def test_exact_text_writes_every_temporal_value_and_leaves_the_rest() -> None:
    moment = pl.Series([10**18 + 100, None], dtype=pl.Int64).cast(pl.Datetime("ns"))
    clock = pl.Series([1_000_000_200, None], dtype=pl.Int64).cast(pl.Time)
    frame = pl.DataFrame(
        {
            "moment": moment,
            "clock": clock,
            "span": pl.Series([100, None], dtype=pl.Int64).cast(pl.Duration("ns")),
            "amount": [1.5, None],
            "day": [date(2024, 3, 1), None],
            "event": pl.DataFrame({"at": moment, "n": [1, 2]}).to_struct(),
            "events": [[10**18 + 100, 10**18 + 200], None],
            "pair": pl.Series([[100, 200], None], dtype=pl.Array(pl.Int64, 2)),
        }
    ).with_columns(
        pl.col("events").cast(pl.List(pl.Datetime("ns"))),
        pl.col("pair").cast(pl.Array(pl.Duration("ns"), 2)),
        pl.when(pl.col("amount").is_null()).then(None).otherwise(pl.col("event")).alias("event"),
    )
    at = "2001-09-09T01:46:40.000000100"

    rows = exact_rows(frame)

    assert rows == [
        {
            "moment": at,
            "clock": "00:00:01.000000200",
            "span": "0:00:00.000000100",
            "amount": 1.5,
            "day": date(2024, 3, 1),
            "event": {"at": at, "n": 1},
            "events": [at, "2001-09-09T01:46:40.000000200"],
            "pair": ["0:00:00.000000100", "0:00:00.000000200"],
        },
        dict.fromkeys(frame.columns),
    ]
    assert exact_row(frame, 0) == rows[0]
    assert exact_text(frame.lazy()).collect().to_dicts() == rows
    plain = frame.select("amount", "day")
    assert exact_text(plain) is plain


def test_a_frame_of_multidimensional_arrays_reads_back_whole() -> None:
    from haute._temporal_text import frame_from_exact_rows

    frame = pl.DataFrame(
        {
            "moments": pl.Series(
                [[[100, 200], [300, 400]], None], dtype=pl.Array(pl.Int64, (2, 2))
            ).cast(pl.Array(pl.Datetime("ns"), (2, 2)), strict=False),
            "grid": pl.Series(
                [[[1, 2], [3, 4]], [[5, 6], [7, 8]]], dtype=pl.Array(pl.Int64, (2, 2))
            ),
        }
    )

    back = frame_from_exact_rows(exact_rows(frame), frame.schema)

    assert back.schema == frame.schema
    assert back.equals(frame)
