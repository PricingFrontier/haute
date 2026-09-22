"""Fresh-process budget evidence for sliced write strategies.

The sliced cases run through ``write_parts`` with a 256 MiB execution-context
incremental RSS budget. Each must remain within that budget at 1.5M and 6M rows
and finish within eight times its same-size native control (with a 30-second
floor). Native cases are unbudgeted diagnostics: their RSS is recorded but no
growth relationship is required from them.
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
_BOUNDED_INCREMENTAL_LIMIT_BYTES = 256 * 1024 * 1024
_BOUNDED_NATIVE_CONTROLS = {
    "unnest": "passthrough_native",
    "filter_with_recipe": "filter",
}

EXPECTED_STRATEGIES: dict[str, str] = {
    "passthrough_native": "native",
    "filter": "native",
    "filter_with_derived_columns": "native",
    "unnest": "sliced",
    "filter_with_recipe": "input_sliced",
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
    """Certify the budgeted sliced paths over the 40-column fixture."""
    source_small = tmp_path / "source_small.parquet"
    source_large = tmp_path / "source_large.parquet"

    _create_source_parquet(source_small, _SMALL_ROWS)
    _create_source_parquet(source_large, _LARGE_ROWS)

    cases = (
        "passthrough_native",
        "filter",
        "filter_with_derived_columns",
        "unnest",
        "filter_with_recipe",
    )
    results: dict[str, dict[int, dict[str, Any]]] = {}

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

        results[case] = {_SMALL_ROWS: r_small, _LARGE_ROWS: r_large}

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

    for case, native_case in _BOUNDED_NATIVE_CONTROLS.items():
        for rows in (_SMALL_ROWS, _LARGE_ROWS):
            bounded = results[case][rows]
            native = results[native_case][rows]
            incremental = bounded["incremental_peak_rss_bytes"]
            assert incremental <= _BOUNDED_INCREMENTAL_LIMIT_BYTES, (
                f"Budgeted case {case!r} at {rows} rows used "
                f"{incremental / (1024 * 1024):.1f} MiB incremental RSS, above the 256 MiB budget"
            )
            allowed_seconds = max(30.0, 8 * native["elapsed_seconds"])
            assert bounded["elapsed_seconds"] <= allowed_seconds, (
                f"Budgeted case {case!r} at {rows} rows took {bounded['elapsed_seconds']:.1f}s, "
                f"above its {native_case!r} control allowance of {allowed_seconds:.1f}s"
            )

    for case in cases:
        summary = results[case]
        print(
            f"{case}: "
            f"small={summary[_SMALL_ROWS]['incremental_peak_rss_bytes'] / (1024 * 1024):.1f}MiB/"
            f"{summary[_SMALL_ROWS]['elapsed_seconds']:.2f}s, "
            f"large={summary[_LARGE_ROWS]['incremental_peak_rss_bytes'] / (1024 * 1024):.1f}MiB/"
            f"{summary[_LARGE_ROWS]['elapsed_seconds']:.2f}s"
        )
