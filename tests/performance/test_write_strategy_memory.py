"""Performance artifact for chunked write memory scaling.

This artifact proves that Haute's native sink strategy has peak memory that grows
with input row count, while the sliced strategy remains bounded.

The passthrough control (``passthrough_native``), a native filter that keeps every
row, proves the growth belongs to the native sink
itself rather than to filtering. A change in these relationships represents a
change in the justification for CACHE-S20 (input-sliced writes for row-local nodes).
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from scripts.memory_smoke import run_smoke

pytestmark = [pytest.mark.perf, pytest.mark.usefixtures("_widen_sandbox_root")]

_PROBE = Path(__file__).with_name("_write_strategy_memory_probe.py")
_ROW_GROUP_SIZE = 25_000
_SMALL_ROWS = 1_500_000
_LARGE_ROWS = 6_000_000

EXPECTED_STRATEGIES: dict[str, str] = {
    "passthrough_native": "native",
    "filter": "native",
    "filter_with_derived_columns": "native",
    "unnest": "sliced",
}


def _create_source_parquet(path: Path, rows: int) -> None:
    """Generate a 40-column Parquet fixture with 20 floats, 10 ints, and 10 strings.

    Built lazily and sunk row group by row group: materialising 6M rows of 40
    columns here would hold about 3 GB in the test process before a single
    measurement started, and the parent's footprint is not what this artifact
    measures.
    """
    index = pl.int_range(0, rows).alias("index")
    columns = [
        *(
            ((index * (i + 1) % 100_003).cast(pl.Float64) / 7.0).alias(f"f{i:02d}")
            for i in range(20)
        ),
        *((index % (977 + i * 13)).cast(pl.Int64).alias(f"i{i:02d}") for i in range(10)),
        *(
            (pl.lit("row-") + (index % (10_000 + i * 500)).cast(pl.Utf8)).alias(f"s{i:02d}")
            for i in range(10)
        ),
    ]
    plan = pl.select(index).lazy().select(columns)
    sink = plan.sink_parquet(path, row_group_size=_ROW_GROUP_SIZE, lazy=True, engine="streaming")
    sink.collect(engine="streaming")


def _run_write_strategy_probe(
    tmp_path: Path,
    case: str,
    source_path: Path,
    rows: int,
) -> dict[str, Any]:
    """Execute the probe in a fresh interpreter via run_smoke and read its metrics."""
    result_path = tmp_path / f"probe-{case}-{rows}.json"
    child_output = io.BytesIO()
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    inherited_paths = [path for path in sys.path if path]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path[:0]={inherited_paths!r};"
        f"runpy.run_path({str(_PROBE)!r},run_name='__main__')"
    )
    smoke = run_smoke(
        command=[
            interpreter,
            "-c",
            bootstrap,
            "--case",
            case,
            "--source",
            str(source_path),
            "--output",
            str(result_path),
        ],
        enable_tracemalloc=False,
        poll_interval_seconds=0.005,
        child_output=child_output,
    )
    assert smoke["exit_code"] == 0, child_output.getvalue().decode(errors="replace")
    result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    baseline = result["rss_before_bytes"]
    peak = smoke["child_peak_rss_bytes"]
    assert isinstance(baseline, int)
    assert isinstance(peak, int)
    assert baseline > 0
    assert peak >= baseline
    sample_count = smoke["child_rss_sample_count"]
    assert isinstance(sample_count, int)
    assert sample_count >= 2
    return {
        **result,
        "child_peak_rss_bytes": peak,
        "incremental_peak_rss_bytes": peak - baseline,
        "rss_sample_count": smoke["child_rss_sample_count"],
        "wall_seconds": smoke["elapsed_seconds"],
    }


def test_write_strategy_memory(tmp_path: Path) -> None:
    """Verify write strategy memory growth scaling between 1.5M and 6M rows."""
    source_small = tmp_path / "source_small.parquet"
    source_large = tmp_path / "source_large.parquet"

    _create_source_parquet(source_small, _SMALL_ROWS)
    _create_source_parquet(source_large, _LARGE_ROWS)

    cases = ("passthrough_native", "filter", "filter_with_derived_columns", "unnest")
    ratios: dict[str, float] = {}

    for case in cases:
        r_small = _run_write_strategy_probe(tmp_path, case, source_small, _SMALL_ROWS)
        r_large = _run_write_strategy_probe(tmp_path, case, source_large, _LARGE_ROWS)

        expected_strategy = EXPECTED_STRATEGIES[case]
        assert r_small["strategy"] == expected_strategy, (
            f"Case {case} at {_SMALL_ROWS} reported strategy "
            f"{r_small['strategy']!r}, expected {expected_strategy!r}"
        )
        assert r_large["strategy"] == expected_strategy, (
            f"Case {case} at {_LARGE_ROWS} reported strategy "
            f"{r_large['strategy']!r}, expected {expected_strategy!r}"
        )

        inc_small = r_small["incremental_peak_rss_bytes"]
        inc_large = r_large["incremental_peak_rss_bytes"]
        ratio = inc_large / inc_small if inc_small > 0 else float("inf")

        ratios[case] = ratio

        # A case that wrote nothing has a flat ratio and would slip past the
        # sliced bound while measuring nothing at all.
        for result, rows in ((r_small, _SMALL_ROWS), (r_large, _LARGE_ROWS)):
            if case in ("passthrough_native", "unnest"):
                assert result["row_count"] == rows, (
                    f"Case {case} at {rows} wrote {result['row_count']} rows, expected every row"
                )
            else:
                assert 0 < result["row_count"] < rows, (
                    f"Case {case} at {rows} wrote {result['row_count']} rows, expected a "
                    f"filtered subset of {rows}"
                )

        if expected_strategy == "native":
            # Every native case must scale with input: over a 4x row step, at
            # least 1.6x. Measured over two runs of the 40-column fixture:
            # passthrough 1.97 to 2.00, filter 2.02 to 2.25, filter with derived
            # columns 2.06 to 2.35.
            assert ratio >= 1.6, (
                f"Native case {case!r} incremental peak ratio {ratio:.2f} is below 1.6 "
                f"({inc_small / (1024 * 1024):.1f} MB -> {inc_large / (1024 * 1024):.1f} MB)"
            )
        elif expected_strategy == "sliced":
            # The sliced case stays bounded whatever the input: measured 1.30 to
            # 1.32 over the same 4x row step, against every native case's 1.97 or
            # more, so this ceiling separates the two behaviours with room for
            # host variance.
            assert ratio <= 1.5, (
                f"Sliced case {case!r} incremental peak ratio {ratio:.2f} exceeds 1.5 "
                f"({inc_small / (1024 * 1024):.1f} MB -> {inc_large / (1024 * 1024):.1f} MB)"
            )

    # The absolute bounds above are sanity rails; this is the relationship the
    # artifact exists to prove. Without it, a host where every case happened to
    # measure about 1.45x would pass while native and sliced behaved alike.
    #
    # Every native case clears it, the passthrough control included: that case
    # exists to show the growth belongs to the sink rather than to filtering, so
    # it must separate from the sliced control too. Worst measured separation is
    # 1.97 against 1.32, which is 1.49.
    sliced_ratio = ratios["unnest"]
    for case, expected_strategy in EXPECTED_STRATEGIES.items():
        if expected_strategy != "native":
            continue
        assert ratios[case] >= 1.25 * sliced_ratio, (
            f"Native case {case!r} grew {ratios[case]:.2f}x against the sliced control's "
            f"{sliced_ratio:.2f}x: the two strategies are not behaving differently, so this "
            f"artifact is no longer measuring what it claims"
        )
