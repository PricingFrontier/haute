"""Fresh-process evidence for the frontier auto-range memory bound.

Auto-range runs the solve setup's pipeline stage and reduces the resolved frame
in batches of the pipeline's streaming chunk size. The memory bound: four times
the scenarios raises the job's peak by at most half, plus 64 MiB. Measured on
the representative fixture -- high quote cardinality, a scenario expander and
real CatBoost scoring between the base and the optimiser, the scored frame
never cached -- at two scenario counts in fresh interpreters, with the Polars
thread pool the optimiser's workers run with.
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
# Sized to the fixture as 500,000 rows is to a real 10M-quote pipeline: the
# scored frame spans many chunks. At 500,000 rows every streaming stage could
# hold this fixture's whole 4M-row frame, and the bound would measure nothing.
_CHUNK_ROWS = 50_000
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
    from haute.routes._optimiser_worker import resolve_optimiser_polars_threads

    # The pool size is fixed when Polars loads, so it is set before the probe runs.
    bootstrap = (
        f"import os;os.environ['POLARS_MAX_THREADS']='{resolve_optimiser_polars_threads()}';"
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


@pytest.fixture(scope="module")
def probes(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], dict[str, Any]]:
    """One auto-range job per scenario count, each in a fresh interpreter."""
    root = tmp_path_factory.mktemp("auto_range_memory")
    fixture = root / "fixture"
    fixture.mkdir()
    _write_fixture(fixture)
    return _run_probe(root, fixture, _SMALL_STEPS), _run_probe(root, fixture, _LARGE_STEPS)


def test_auto_range_reduces_in_pipeline_batches_at_every_scenario_count(
    probes: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    small, large = probes

    # Both runs reduce in batches of the pipeline's streaming chunk size.
    assert small["chunk_rows"] == large["chunk_rows"] == _CHUNK_ROWS
    for result in (small, large):
        assert set(result["ranges"]) == {"volume", "margin"}
        for bounds in result["ranges"].values():
            assert bounds["min"] <= bounds["max"]


def test_auto_range_memory_does_not_grow_with_scenario_count(
    probes: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    small, large = probes

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
