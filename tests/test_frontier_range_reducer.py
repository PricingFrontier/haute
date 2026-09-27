"""The exact Float64 auto-range reducer: carry-over on quote-grouped batches, bucketed fallback.

The reference for every total is the solver's view of the frame: constraints
projected to Float32, per-quote min and max, then an exact (``math.fsum``)
Float64 sum. Each test states which path its fixture takes and asserts it
through the reducer's recorded fallback reason.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from haute._execution_context import ExecutionMemoryLimitExceededError
from haute._native_memory_limit import (
    NativeLimitState,
    native_headroom_bytes,
    native_memory_backend_scope,
)
from haute._ram_estimate import string_view_bytes_per_row
from haute.routes import _optimiser_service as svc
from haute.routes._optimiser_service import (
    AutoRangeReducerBudgetError,
    FrontierAutoRangeContext,
    _estimate_scenario_frontier_ranges,
    _reduce_frontier_range_batches,
    _reducer_budget,
    _reducer_min_budget_bytes,
    _ReducerBudget,
    _ScenarioFrontierRangeAccumulator,
)

MIB = 1024 * 1024
QID = "quote_id"


class _StoppedError(Exception):
    pass


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: None


def _frame(quotes: list[str], **columns: list[float]) -> pl.DataFrame:
    return pl.DataFrame({QID: quotes, **columns})


def _projected(df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    return df.select(
        pl.col(QID).cast(pl.String),
        *[pl.col(c).cast(pl.Float32) for c in cols],
    )


def _reference(df: pl.DataFrame, cols: list[str]) -> tuple[dict[str, dict[str, float]], float]:
    """Float32 projection, per-quote extrema, exact Float64 sums; and S = sum of |extrema|."""
    per_quote = (
        _projected(df, cols)
        .group_by(QID)
        .agg(
            *[pl.col(c).min().cast(pl.Float64).alias(f"{c}_min") for c in cols],
            *[pl.col(c).max().cast(pl.Float64).alias(f"{c}_max") for c in cols],
        )
    )
    ranges = {
        c: {
            "min": math.fsum(per_quote[f"{c}_min"].to_list()),
            "max": math.fsum(per_quote[f"{c}_max"].to_list()),
        }
        for c in cols
    }
    scale = max(
        math.fsum(abs(v) for v in per_quote[f"{c}_{k}"].to_list())
        for c in cols
        for k in ("min", "max")
    )
    return ranges, scale


def _assert_close(
    actual: dict[str, dict[str, float]],
    expected: dict[str, dict[str, float]],
    *,
    quotes: int,
    scale: float,
) -> None:
    tolerance = 4 * quotes * 2.0**-53 * scale + 1e-12
    assert actual.keys() == expected.keys()
    for name, values in expected.items():
        for key in ("min", "max"):
            assert abs(actual[name][key] - values[key]) <= tolerance, (name, key)


def _cut(df: pl.DataFrame, sizes: list[int]) -> list[pl.DataFrame]:
    batches, offset = [], 0
    for size in sizes:
        batches.append(df.slice(offset, size))
        offset += size
    assert offset == df.height
    return batches


def _accumulator(
    tmp_path: Path,
    cols: list[str],
    *,
    budget_bytes: int = 512 * MIB,
    batch_row_cap: int = 1_000,
) -> _ScenarioFrontierRangeAccumulator:
    return _ScenarioFrontierRangeAccumulator(
        quote_id_col=QID,
        constraint_cols=cols,
        parts_root=tmp_path,
        budget=_ReducerBudget(budget_bytes=budget_bytes, setting="TEST_SETTING"),
        batch_row_cap=batch_row_cap,
    )


def _reduce(
    tmp_path: Path,
    batches: list[pl.DataFrame],
    cols: list[str],
    **kwargs: Any,
) -> tuple[dict[str, dict[str, float]], _ScenarioFrontierRangeAccumulator]:
    kwargs.setdefault("batch_row_cap", max(batch.height for batch in batches))
    accumulator = _accumulator(tmp_path, cols, **kwargs)
    for index, batch in enumerate(batches):
        accumulator.add_batch(_projected(batch, cols), batch_index=index)
    return accumulator.finish(), accumulator


@pytest.fixture
def no_min_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let tiny budgets reach their branches: the whole minimum is one seam."""
    monkeypatch.setattr(svc, "_reducer_min_budget_bytes", lambda *args: 0)


@pytest.fixture
def scans(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every partial file the fallback reads back."""
    calls: list[Any] = []
    original = pl.scan_parquet

    def spy(source: Any, *args: Any, **kwargs: Any) -> pl.LazyFrame:
        calls.append(source)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(pl, "scan_parquet", spy)
    return calls


# --- key width -------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        ["a", "abcdefghijkl", "q-000001"],  # every value inlined in its view
        ["abcdefghijklm", "x" * 40, "quote-0000000001"],  # every value in a data buffer
        ["short", "x" * 13, "", "y" * 12, "z" * 100],  # both
    ],
)
def test_string_view_width_matches_arrow_buffers(values: list[str]) -> None:
    arrow = pa.array(values, type=pa.string_view())
    arrow_bytes = sum(buffer.size for buffer in arrow.buffers() if buffer is not None)

    assert string_view_bytes_per_row(pl.Series(values)) * len(values) == arrow_bytes


# --- budget ----------------------------------------------------------------


class _Headroom:
    def __init__(self, headroom: int | None) -> None:
        self.headroom = headroom

    def remaining_memory_bytes(self) -> int | None:
        return self.headroom


def test_reducer_budget_is_the_cap_or_a_quarter_of_the_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAUTE_OPTIMISER_REDUCER_BUDGET_MB", raising=False)
    assert _reducer_budget(None) == _ReducerBudget(512 * MIB, "HAUTE_OPTIMISER_REDUCER_BUDGET_MB")
    assert _reducer_budget(_Headroom(None)).budget_bytes == 512 * MIB  # type: ignore[arg-type]
    assert _reducer_budget(_Headroom(1024 * MIB)) == _ReducerBudget(  # type: ignore[arg-type]
        256 * MIB, "HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB"
    )

    monkeypatch.setenv("HAUTE_OPTIMISER_REDUCER_BUDGET_MB", "64")
    assert _reducer_budget(_Headroom(1024 * MIB)) == _ReducerBudget(  # type: ignore[arg-type]
        64 * MIB, "HAUTE_OPTIMISER_REDUCER_BUDGET_MB"
    )


class _CappedLease:
    """A native lease whose cap stands ``headroom`` bytes above its current charge."""

    backend = "windows_job"

    def __init__(self, headroom: int) -> None:
        self.headroom = headroom

    def limit_state(self) -> NativeLimitState:
        return NativeLimitState("windows_job", baseline_bytes=0, ceiling_bytes=10 * 1024 * MIB)

    def current_charge_bytes(self) -> int:
        return 10 * 1024 * MIB - self.headroom


def test_native_headroom_is_the_capped_worker_calls_ceiling_minus_its_charge() -> None:
    assert native_headroom_bytes() is None
    with native_memory_backend_scope("windows_job", _CappedLease(300 * MIB)):  # type: ignore[arg-type]
        assert native_headroom_bytes() == 300 * MIB
    with native_memory_backend_scope("windows_job", _CappedLease(-5)):  # type: ignore[arg-type]
        assert native_headroom_bytes() == 0
    # An uncapped call exposes no lease, whatever the caller passes.
    with native_memory_backend_scope(None, _CappedLease(300 * MIB)):  # type: ignore[arg-type]
        assert native_headroom_bytes() is None
    assert native_headroom_bytes() is None


def test_reducer_budget_takes_the_smaller_of_the_rss_and_native_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAUTE_OPTIMISER_REDUCER_BUDGET_MB", raising=False)
    # Ample RSS headroom, but the worker is near its private-byte cap.
    with native_memory_backend_scope("windows_job", _CappedLease(1024 * MIB)):  # type: ignore[arg-type]
        assert _reducer_budget(_Headroom(8192 * MIB)).budget_bytes == 256 * MIB  # type: ignore[arg-type]
        # And the other way round: the RSS headroom is the smaller.
        assert _reducer_budget(_Headroom(512 * MIB)).budget_bytes == 128 * MIB  # type: ignore[arg-type]
        # With no execution context the native headroom still bounds the budget.
        assert _reducer_budget(None).budget_bytes == 256 * MIB


def test_reducer_minimum_is_the_measured_floor_or_the_structural_term() -> None:
    # This pipeline's shape and a 64-constraint, 900k-row shape sit on the floor.
    assert _reducer_min_budget_bytes(1, 16.0, 2_000_000) == 384 * MIB
    assert _reducer_min_budget_bytes(64, 16.0, 900_000) == 384 * MIB
    # 10M-row batches at 64 constraints: 8 * (footer + 2 decoded row groups).
    b_raw = 16 + 16 * 64 + 16
    expected = 8 * (64 * 130 * 440 + 2 * math.ceil(10_000_000 / 64) * b_raw)
    assert _reducer_min_budget_bytes(64, 16.0, 10_000_000) == expected > 384 * MIB


# --- carry path --------------------------------------------------------------


def test_carry_path_combines_every_split_quote_exactly(tmp_path: Path, scans: list[Any]) -> None:
    """Quotes of 11 rows in 10-row batches split at every boundary; one spans three batches."""
    quotes: list[str] = []
    values: list[float] = []
    for q in range(20):
        rows = 25 if q == 7 else 11
        quotes.extend([f"q{q:03d}"] * rows)
        values.extend(float((q * 37 + r * 11) % 101 - 50) for r in range(rows))
    df = _frame(quotes, volume=values)
    batches = _cut(df, [10] * (df.height // 10) + ([df.height % 10] if df.height % 10 else []))
    assert any(batch.get_column(QID).n_unique() == 1 for batch in batches)  # q007 spans three

    ranges, accumulator = _reduce(tmp_path, batches, ["volume"])

    expected, _ = _reference(df, ["volume"])
    assert ranges == expected  # integer-valued: exact
    assert accumulator.carry_path is True
    assert accumulator.fallback_reason is None
    assert scans == []


def test_split_quote_min_is_exact_without_subtraction(tmp_path: Path) -> None:
    """Quote a ends batch 1 at 1e20 and starts batch 2 at 1: its min is 1, not lost to 1e20."""
    batches = [
        _frame(["b", "a"], volume=[3.0, 1e20]),
        _frame(["a", "c"], volume=[1.0, 3.0]),
    ]

    ranges, accumulator = _reduce(tmp_path, batches, ["volume"])

    assert accumulator.fallback_reason is None
    assert ranges["volume"]["min"] == 7.0
    assert ranges["volume"]["max"] == float(np.float32(1e20)) + 6.0


# --- fallbacks -----------------------------------------------------------------


def test_contiguity_violation_within_a_batch_falls_back_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _RecordingLogger()
    monkeypatch.setattr(svc, "logger", recorder)
    df = _frame(
        ["w", "x", "y", "x", "z", "v", "v"],
        volume=[5.0, 1.0, 2.0, 7.0, 4.0, 9.0, -3.0],
        margin=[0.5, -1.0, 2.5, 3.0, 0.0, 4.0, 1.0],
    )

    ranges, accumulator = _reduce(tmp_path, _cut(df, [4, 3]), ["volume", "margin"])

    expected, scale = _reference(df, ["volume", "margin"])
    _assert_close(ranges, expected, quotes=5, scale=scale)
    assert accumulator.fallback_reason == "batch_not_contiguous"
    assert [fields["reason"] for event, fields in recorder.events] == ["batch_not_contiguous"]
    assert recorder.events[0][0] == "auto_range_reducer_fallback"


def test_quote_reappearing_across_batches_falls_back_exactly(tmp_path: Path) -> None:
    """Every batch is contiguous; x ends in batch 1, is absent from batch 2, returns in 3."""
    df = _frame(
        ["x", "y", "z", "w", "x", "v"],
        volume=[1.0, 2.0, 3.0, 4.0, -8.0, 6.0],
    )

    ranges, accumulator = _reduce(tmp_path, _cut(df, [2, 2, 2]), ["volume"])

    expected, _ = _reference(df, ["volume"])
    assert ranges == expected
    assert accumulator.fallback_reason == "quote_reappeared"


def test_colliding_hashes_cost_speed_not_accuracy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With every quote hashing alike, the finish sees a repeat and the fallback stays exact."""
    original_hash = pl.Expr.hash
    monkeypatch.setattr(
        pl.Expr,
        "hash",
        lambda self, *a, **k: original_hash(self, *a, **k) * pl.lit(0, dtype=pl.UInt64),
    )
    df = _frame(["a", "a", "b", "c", "c", "d"], volume=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    ranges, accumulator = _reduce(tmp_path, _cut(df, [3, 3]), ["volume"])

    expected, _ = _reference(df, ["volume"])
    assert ranges == expected
    assert accumulator.fallback_reason == "quote_reappeared"


@pytest.mark.usefixtures("no_min_budget")
def test_quote_in_more_batches_than_a_pass_holds_is_combined(tmp_path: Path) -> None:
    """z is finished in each of 200 batches; a pass holds ~170 partial rows at 64 KiB."""
    budget = 64 * 1024
    batch_count = 200
    capacity = budget // (2 * 4 * (16 + 16 + 16))
    assert capacity < batch_count
    batches = [
        _frame([f"a{i:04d}", "z", f"b{i:04d}"], volume=[float(i), float(i % 17 - 8), -float(i)])
        for i in range(batch_count)
    ]

    ranges, accumulator = _reduce(tmp_path, batches, ["volume"], budget_bytes=budget)

    expected, _ = _reference(pl.concat(batches), ["volume"])
    assert ranges == expected
    assert accumulator.fallback_reason == "quote_reappeared"


@pytest.mark.usefixtures("no_min_budget")
def test_full_hash_buffer_falls_back_exactly(tmp_path: Path) -> None:
    """10,000 one-row quotes and no repeats against a 3,640-hash buffer at 64 KiB."""
    budget = 64 * 1024
    assert budget // 18 == 3_640
    df = _frame(
        [f"quote-{i:06d}" for i in range(10_000)],
        volume=[float((i * 7919) % 1000) / 8 for i in range(10_000)],
    )

    ranges, accumulator = _reduce(tmp_path, _cut(df, [2_000] * 5), ["volume"], budget_bytes=budget)

    expected, scale = _reference(df, ["volume"])
    _assert_close(ranges, expected, quotes=10_000, scale=scale)
    assert accumulator.fallback_reason == "hash_buffer_full"


# --- the fallback pass's final flush --------------------------------------------


def _three_file_accumulator(
    tmp_path: Path,
) -> tuple[_ScenarioFrontierRangeAccumulator, pl.DataFrame]:
    df = _frame(
        [f"q{i:03d}" for i in range(30)] + ["q000", "q015", "q029"],
        volume=[float(i % 7) for i in range(30)] + [-5.0, 20.0, -9.0],
    )
    accumulator = _accumulator(tmp_path, ["volume"])
    for index, batch in enumerate(_cut(df, [11, 11, 11])):
        accumulator.add_batch(_projected(batch, ["volume"]), batch_index=index)
    assert len(accumulator.part_files) == 3
    return accumulator, df


def _piece_sizes(accumulator: _ScenarioFrontierRangeAccumulator) -> list[int]:
    return [
        pl.read_parquet(path).drop(svc._REDUCER_BUCKET_COLUMN).estimated_size()
        for path in accumulator.part_files
    ]


def _count_merges(
    monkeypatch: pytest.MonkeyPatch,
    accumulator: _ScenarioFrontierRangeAccumulator,
) -> list[int]:
    merges: list[int] = []
    original = accumulator._merge_pass_pieces

    def recording(state: Any, pieces: list[pl.DataFrame]) -> pl.DataFrame:
        merges.append(len(pieces))
        return original(state, pieces)

    monkeypatch.setattr(accumulator, "_merge_pass_pieces", recording)
    return merges


def _state_totals(state: pl.DataFrame | None) -> tuple[float, float]:
    assert state is not None
    return (
        math.fsum(state["__haute_frontier_min_0"].to_list()),
        math.fsum(state["__haute_frontier_max_0"].to_list()),
    )


def test_a_pass_below_the_piece_threshold_is_flushed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accumulator, df = _three_file_accumulator(tmp_path)
    merges = _count_merges(monkeypatch, accumulator)

    state = accumulator._fallback_pass_state(
        0,
        svc._REDUCER_BUCKET_COUNT - 1,
        piece_threshold=10 * sum(_piece_sizes(accumulator)),
        check_cancelled=None,
        execution_context=None,
    )

    expected, _ = _reference(df, ["volume"])
    assert merges == [3]  # only the final flush
    assert state is not None and state.height == 30
    assert _state_totals(state) == (expected["volume"]["min"], expected["volume"]["max"])


def test_pieces_after_the_last_threshold_merge_are_flushed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accumulator, df = _three_file_accumulator(tmp_path)
    first, second, _third = _piece_sizes(accumulator)
    merges = _count_merges(monkeypatch, accumulator)

    state = accumulator._fallback_pass_state(
        0,
        svc._REDUCER_BUCKET_COUNT - 1,
        piece_threshold=first + second,
        check_cancelled=None,
        execution_context=None,
    )

    expected, _ = _reference(df, ["volume"])
    assert merges == [2, 1]  # the threshold after the second piece, then the flush
    assert _state_totals(state) == (expected["volume"]["min"], expected["volume"]["max"])


# --- partial file layout -----------------------------------------------------------


def test_partial_files_are_bucket_sorted_with_at_most_64_row_groups(tmp_path: Path) -> None:
    df = _frame(
        [f"quote-{i:07d}" for i in range(5_000)],
        volume=[float(i) for i in range(5_000)],
    )
    accumulator = _accumulator(tmp_path, ["volume"], batch_row_cap=5_000)
    accumulator.add_batch(_projected(df, ["volume"]), batch_index=0)

    (path,) = accumulator.part_files
    metadata = pq.ParquetFile(path).metadata
    rows_per_group = math.ceil(5_000 / 64)
    assert metadata.num_row_groups <= 64
    assert all(
        metadata.row_group(i).num_rows <= rows_per_group for i in range(metadata.num_row_groups)
    )
    assert metadata.row_group(0).column(0).compression == "LZ4"
    buckets = pl.read_parquet(path).get_column(svc._REDUCER_BUCKET_COLUMN)
    assert buckets.dtype == pl.UInt16
    assert buckets.is_sorted()


# --- the minimum budget ----------------------------------------------------------------


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []
    original = pl.DataFrame.write_parquet

    def spy(self: pl.DataFrame, file: Any, *args: Any, **kwargs: Any) -> None:
        calls.append(file)
        original(self, file, *args, **kwargs)

    monkeypatch.setattr(pl.DataFrame, "write_parquet", spy)
    return calls


def _shuffled_and_grouped() -> tuple[list[pl.DataFrame], list[pl.DataFrame]]:
    quotes = [f"q{q:03d}" for q in range(50) for _ in range(4)]
    df = _frame(quotes, volume=[float(i % 13) for i in range(len(quotes))])
    shuffled = df.sample(fraction=1.0, shuffle=True, seed=3)
    return (
        _cut(_projected(shuffled, ["volume"]), [50] * 4),
        _cut(_projected(df, ["volume"]), [50] * 4),
    )


def test_below_the_minimum_budget_a_violation_fails_before_any_fallback_io(
    monkeypatch: pytest.MonkeyPatch,
    scans: list[Any],
    writes: list[Any],
) -> None:
    monkeypatch.setenv("HAUTE_OPTIMISER_REDUCER_BUDGET_MB", "64")
    shuffled, grouped = _shuffled_and_grouped()
    consumed: list[int] = []

    def counted(batches: list[pl.DataFrame]) -> Any:
        for batch in batches:
            consumed.append(batch.height)
            yield batch

    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        _reduce_frontier_range_batches(
            counted(shuffled),
            quote_id_col=QID,
            constraint_cols=["volume"],
            batch_row_cap=50,
        )

    error = exc_info.value
    assert isinstance(error, AutoRangeReducerBudgetError)
    assert error.reason == "reducer_budget_below_minimum"
    payload = error.to_payload()
    assert payload["reducer_budget_bytes"] == 64 * MIB
    assert payload["reducer_min_budget_bytes"] == 384 * MIB
    assert payload["violation"] == "batch_not_contiguous"
    assert "HAUTE_OPTIMISER_REDUCER_BUDGET_MB" in str(error)
    assert consumed == [50]  # failed at the first batch
    assert scans == [] and writes == []

    ranges = _reduce_frontier_range_batches(
        iter(grouped),
        quote_id_col=QID,
        constraint_cols=["volume"],
        batch_row_cap=50,
    )

    expected, _ = _reference(pl.concat(grouped), ["volume"])
    assert ranges == expected
    assert scans == [] and writes == []


def test_a_fallback_that_becomes_unavailable_fails_the_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wider key later in the run lifts the minimum above the budget; partials are dropped."""
    monkeypatch.setattr(
        svc,
        "_reducer_min_budget_bytes",
        lambda c, key_width, cap: 0 if key_width <= 16 else 1 << 60,
    )
    accumulator = _accumulator(tmp_path, ["volume"])
    accumulator.add_batch(
        _projected(_frame(["a", "b"], volume=[1.0, 2.0]), ["volume"]), batch_index=0
    )
    assert accumulator.fallback_available is True and len(accumulator.part_files) == 1

    wide = "w" * 40
    accumulator.add_batch(
        _projected(_frame([wide, "c"], volume=[1.0, 2.0]), ["volume"]), batch_index=1
    )
    assert accumulator.fallback_available is False
    assert accumulator.part_files == [] and list(tmp_path.iterdir()) == []

    with pytest.raises(AutoRangeReducerBudgetError, match="batch_not_contiguous"):
        accumulator.add_batch(
            _projected(_frame(["d", "e", "d"], volume=[1.0, 2.0, 3.0]), ["volume"]),
            batch_index=2,
        )


def test_a_fallback_in_use_that_becomes_unavailable_fails_at_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        svc,
        "_reducer_min_budget_bytes",
        lambda c, key_width, cap: 0 if key_width <= 16 else 1 << 60,
    )
    accumulator = _accumulator(tmp_path, ["volume"])
    accumulator.add_batch(
        _projected(_frame(["a", "b", "a"], volume=[1.0, 2.0, 3.0]), ["volume"]), batch_index=0
    )
    assert accumulator.fallback_reason == "batch_not_contiguous"

    with pytest.raises(AutoRangeReducerBudgetError):
        accumulator.add_batch(
            _projected(_frame(["w" * 40], volume=[1.0]), ["volume"]),
            batch_index=1,
        )


# --- the estimator ------------------------------------------------------------------------


def test_estimator_asks_for_the_frames_order(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[bool] = []
    original = svc.bounded_collect_batches

    def spy(lf: Any, **kwargs: Any) -> Any:
        requested.append(kwargs["maintain_order"])
        return original(lf, **kwargs)

    monkeypatch.setattr(svc, "bounded_collect_batches", spy)
    df = _frame(["a", "a", "b"], volume=[1.0, 2.0, 3.0])

    _estimate_scenario_frontier_ranges(
        FrontierAutoRangeContext(chunk_size=2),
        scored_lf=df.lazy(),
        quote_id_col=QID,
        constraint_cols=["volume"],
    )

    assert requested == [True]


def test_estimator_projects_to_float32_and_sums_in_float64(tmp_path: Path) -> None:
    """Float64 sources are projected to the solver's Float32 before the extrema are taken."""
    rng = np.random.default_rng(11)
    quotes = np.repeat([f"quote-{q:05d}" for q in range(400)], 11)
    df = _frame(
        quotes.tolist(),
        volume=(rng.normal(size=quotes.size) * 1e3).tolist(),
        margin=(rng.lognormal(size=quotes.size) * 0.1).tolist(),
    )

    ranges = _estimate_scenario_frontier_ranges(
        FrontierAutoRangeContext(chunk_size=97),
        scored_lf=df.lazy(),
        quote_id_col=QID,
        constraint_cols=["volume", "margin"],
    )

    expected, scale = _reference(df, ["volume", "margin"])
    _assert_close(ranges, expected, quotes=400, scale=scale)


def test_fallback_checks_cancellation_between_files(tmp_path: Path) -> None:
    df = _frame(["x", "y", "z", "w", "x", "v"], volume=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    accumulator = _accumulator(tmp_path, ["volume"])
    for index, batch in enumerate(_cut(df, [2, 2, 2])):
        accumulator.add_batch(_projected(batch, ["volume"]), batch_index=index)
    calls = 0

    def check_cancelled() -> None:
        # 1: finish, 2: the pass, 3: before the first file, 4: before the second.
        nonlocal calls
        calls += 1
        if calls == 4:
            raise _StoppedError

    with pytest.raises(_StoppedError):
        accumulator.finish(check_cancelled=check_cancelled)
    assert accumulator.fallback_reason == "quote_reappeared"


# --- property -----------------------------------------------------------------------------------


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(seed=st.integers(0, 2**32 - 1), grouped=st.booleans())
def test_totals_match_the_reference_on_random_frames(
    tmp_path_factory: pytest.TempPathFactory,
    seed: int,
    grouped: bool,
) -> None:
    rng = np.random.default_rng(seed)
    quote_count = int(rng.integers(1, 60))
    rows_per_quote = rng.integers(1, 13, size=quote_count)
    quotes = np.repeat(np.array([f"q{q}" for q in range(quote_count)]), rows_per_quote)
    size = quotes.size
    magnitude = 10.0 ** rng.uniform(-3, 20, size=(2, size))
    values = magnitude * rng.choice([-1.0, 1.0], size=(2, size))
    df = _frame(quotes.tolist(), volume=values[0].tolist(), margin=values[1].tolist())
    if not grouped:
        df = df.sample(fraction=1.0, shuffle=True, seed=seed % 2**31)
    cuts = (
        sorted(set(rng.integers(1, size, size=int(rng.integers(0, 6))).tolist()))
        if size > 1
        else []
    )
    bounds = [0, *cuts, size]
    sizes = [b - a for a, b in zip(bounds, bounds[1:], strict=False)]

    ranges, accumulator = _reduce(
        tmp_path_factory.mktemp("parts"), _cut(df, sizes), ["volume", "margin"]
    )

    expected, scale = _reference(df, ["volume", "margin"])
    _assert_close(ranges, expected, quotes=quote_count, scale=scale)
    if grouped:
        assert accumulator.fallback_reason is None
