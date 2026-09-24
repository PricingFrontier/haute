"""Fresh-process evidence for the frontier auto-range memory bound.

The optimiser specification states that chunk-by-chunk auto-range memory
follows the chunk size rather than the expanded scenario frame: at a fixed
chunk size, four times the scenarios raises the job's peak memory by at most
half, plus 64 MiB. This measures that bound on the representative fixture --
high quote cardinality, a scenario expander and real CatBoost scoring between
the base and the optimiser -- at two scenario counts in fresh interpreters.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from scripts.memory_smoke import run_smoke

pytestmark = pytest.mark.perf

_PROBE = Path(__file__).with_name("_auto_range_memory_probe.py")
_QUOTES = 200_000
_SMALL_STEPS = 5
_LARGE_STEPS = 4 * _SMALL_STEPS
_CHUNK_ROWS = 500_000
_GROWTH_FACTOR = 1.5
_GROWTH_SLACK_BYTES = 64 * 1024 * 1024


def _write_fixture(root: Path) -> None:
    """Write the base parquet and a CatBoost conversion model into *root*."""
    from catboost import CatBoostRegressor, Pool

    rng = np.random.default_rng(7)
    pl.DataFrame(
        {
            "quote_id": pl.int_range(0, _QUOTES, eager=True).cast(pl.String),
            "premium": rng.uniform(200.0, 2_000.0, _QUOTES),
            "base_premium": rng.uniform(300.0, 1_500.0, _QUOTES),
            "claims_cost": rng.uniform(100.0, 1_200.0, _QUOTES),
            "age": rng.integers(18, 90, _QUOTES, dtype=np.int32),
            "vehicle_value": rng.uniform(2_000.0, 80_000.0, _QUOTES),
            "region": rng.choice(["north", "south", "east", "west"], _QUOTES),
            "payload": rng.uniform(0.0, 1.0, _QUOTES),
        }
    ).with_columns(
        pl.format("Q{}", pl.col("quote_id").str.zfill(10)).alias("quote_id")
    ).write_parquet(root / "base.parquet", row_group_size=50_000)

    train_rows = 20_000
    price_ratio = rng.uniform(0.5, 2.5, train_rows)
    age = rng.integers(18, 90, train_rows)
    vehicle_value = rng.uniform(2_000.0, 80_000.0, train_rows)
    target = 1.0 / (1.0 + np.exp(3.0 * (price_ratio - 1.2) + 0.01 * (age - 40)))
    model = CatBoostRegressor(iterations=60, depth=4, verbose=False, random_seed=7)
    model.fit(
        Pool(
            np.column_stack([age, vehicle_value, price_ratio]),
            target,
            feature_names=["age", "vehicle_value", "price_ratio"],
        )
    )
    model.save_model(str(root / "model.cbm"))


def _run_probe(tmp_path: Path, fixture: Path, steps: int) -> dict[str, Any]:
    """Run one auto-range job in a fresh interpreter and read its peak RSS."""
    result_path = tmp_path / f"auto-range-{steps}.json"
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
            "--fixture",
            str(fixture),
            "--steps",
            str(steps),
            "--chunk-rows",
            str(_CHUNK_ROWS),
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
    return {**result, "incremental_peak_rss_bytes": peak - baseline}


def test_chunked_auto_range_memory_does_not_grow_with_scenario_count(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    _write_fixture(fixture)

    small = _run_probe(tmp_path, fixture, _SMALL_STEPS)
    large = _run_probe(tmp_path, fixture, _LARGE_STEPS)

    # The same expanded rows per chunk, so the large run makes four times the chunks.
    assert small["chunk_rows"] == large["chunk_rows"] == _CHUNK_ROWS
    assert large["source_chunk_rows"] * _LARGE_STEPS == _CHUNK_ROWS
    for result in (small, large):
        assert set(result["ranges"]) == {"volume", "margin"}
        for bounds in result["ranges"].values():
            assert bounds["min"] <= bounds["max"]

    mib = 1024 * 1024
    small_peak = small["incremental_peak_rss_bytes"]
    large_peak = large["incremental_peak_rss_bytes"]
    allowed = _GROWTH_FACTOR * small_peak + _GROWTH_SLACK_BYTES
    print(
        f"auto-range peak increment: {_SMALL_STEPS} scenarios {small_peak / mib:.1f} MiB "
        f"in {small['elapsed_seconds']:.2f}s; {_LARGE_STEPS} scenarios "
        f"{large_peak / mib:.1f} MiB in {large['elapsed_seconds']:.2f}s"
    )
    assert large_peak <= allowed, (
        f"{_LARGE_STEPS} scenarios peaked {large_peak / mib:.1f} MiB above the job's baseline, "
        f"above the {allowed / mib:.1f} MiB the chunk-bound allows "
        f"({_SMALL_STEPS} scenarios peaked {small_peak / mib:.1f} MiB)"
    )
