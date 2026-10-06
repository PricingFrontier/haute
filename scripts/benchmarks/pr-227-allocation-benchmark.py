"""Fresh-process allocation measurements for the existing consumer boundaries.

Fixtures are prepared by the parent, native RSS is sampled externally every
5ms, and the child records phase RSS separately. No model fit is measured:
training compares the old/new Pool-construction order with real CatBoost Pools.
Grid measurements call the installed price-contour adapter. Profile measurements
retain exact statistics. These are workload measurements, not universal bounds.
"""

from __future__ import annotations

# ruff: noqa: E402 — fixed thread-count environment must precede Polars import.
import argparse
import gc
import io
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

os.environ["POLARS_MAX_THREADS"] = "2"
os.environ["OMP_NUM_THREADS"] = "2"

import polars as pl
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.memory_smoke import run_smoke


def fixture(path: Path, rows: int, kind: str) -> None:
    with pq.ParquetWriter(path, make_batch(0, 1, kind).to_arrow().schema) as writer:
        for start in range(0, rows, 25_000):
            writer.write_table(make_batch(start, min(25_000, rows - start), kind).to_arrow())


def make_batch(start: int, count: int, kind: str) -> pl.DataFrame:
    index = pl.int_range(start, start + count, eager=True)
    if kind == "train":
        return pl.DataFrame(
            {
                **{f"f{i}": ((index * (i + 1) % 100_003) / 7).cast(pl.Float64) for i in range(32)},
                "target": (index % 977).cast(pl.Float64),
                "weight": pl.Series([1.0] * count),
            }
        )
    if kind == "grid":
        return pl.DataFrame(
            {
                "quote_id": (index // 20).cast(pl.String),
                "scenario_index": (index % 20).cast(pl.Int32),
                "scenario_value": (0.8 + (index % 20) * 0.02).cast(pl.Float32),
                "expected_income": (10 + index % 20).cast(pl.Float32),
                **{f"c{i}": ((index % (101 + i)) / 100).cast(pl.Float32) for i in range(4)},
            }
        )
    return pl.DataFrame(
        {
            **{f"f{i}": ((index * (i + 1)) % 100_003).cast(pl.Float64) for i in range(16)},
            "label": (index % 500_003).cast(pl.String),
        }
    )


def worker(workspace: Path, case: str, rows: int) -> None:
    from haute._execution_context import ExecutionContext, ExecutionProfile, current_rss_bytes

    phases = []

    def phase(name: str) -> None:
        phases.append(
            {
                "phase": name,
                "rss_bytes": current_rss_bytes(),
                "seconds": time.perf_counter() - started,
            }
        )

    if case.startswith("train"):
        from catboost import Pool  # warm imports before the baseline

        from haute.modelling._algorithms import _build_pool, _malloc_trim

        del Pool
    elif case == "grid":
        from price_contour import build_grid_from_parquet_chunked
    else:
        import haute._frame_profile as frame_profile

        # Keep this historical allocation baseline on its original single-aggregation control.
        frame_profile._PROFILE_DISTINCT_PARTITION_ROWS = max(
            rows, frame_profile._PROFILE_DISTINCT_PARTITION_ROWS
        )
        _build_frame_stats = frame_profile._build_frame_stats

    gc.collect()
    baseline = current_rss_bytes()
    started = time.perf_counter()
    phase("baseline")
    if case.startswith("train"):
        features = [f"f{i}" for i in range(32)]
        train = pl.read_parquet(workspace / "train.parquet")
        phase("training_frame")
        validation = None
        if case == "train-old":
            validation = pl.read_parquet(workspace / "validation.parquet")
            phase("validation_before_train_pool")
        y, w = train["target"].to_numpy(), train["weight"].to_numpy()
        data = train.select(features)
        del train
        train_pool = _build_pool(data, features, y=y, w=w)
        del data, y, w
        gc.collect()
        _malloc_trim()
        phase("train_pool_raw_released")
        if validation is None:
            validation = pl.read_parquet(workspace / "validation.parquet")
            phase("validation_after_train_pool")
        y, w = validation["target"].to_numpy(), validation["weight"].to_numpy()
        data = validation.select(features)
        del validation
        eval_pool = _build_pool(data, features, y=y, w=w)
        del data, y, w
        gc.collect()
        _malloc_trim()
        phase("both_pools_raw_released")
        assert train_pool.num_row() == rows and eval_pool.num_row() == rows // 4
        assert train_pool.num_col() == 32 and eval_pool.num_col() == 32
        result = {"train_rows": rows, "validation_rows": rows // 4, "features": 32}
    elif case == "grid":
        grid = build_grid_from_parquet_chunked(
            str(workspace / f"grid-{rows}.parquet"), [f"c{i}" for i in range(4)], 100_000
        )
        phase("resident_grid")
        assert grid.n_quotes == rows // 20 and grid.n_steps == 20
        result = {
            "rows": rows,
            "quotes": grid.n_quotes,
            "steps": grid.n_steps,
            "chunk_rows": 100_000,
        }
    else:
        frame = pl.scan_parquet(workspace / f"profile-{rows}.parquet")
        context = ExecutionContext(
            operation="allocation_probe", profile=ExecutionProfile.EXPLORE_ANALYSIS
        )
        stats = _build_frame_stats(frame, frame.collect_schema(), execution_context=context)
        phase("exact_profile_complete")
        assert stats.row_count == rows
        result = {"rows": rows, "columns": len(stats.columns)}
    elapsed = time.perf_counter() - started
    (workspace / "worker.json").write_text(
        json.dumps(
            {
                "case": case,
                "rows": rows,
                "elapsed_seconds": elapsed,
                "baseline_rss_bytes": baseline,
                "phases": phases,
                "result": result,
                "verified_scalar_contract": True,
            }
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--rows", type=int)
    args = parser.parse_args()
    if args.worker:
        worker(args.workspace, args.case, args.rows)
        return
    script = str(Path(__file__).resolve())
    bootstrap = (
        f"import runpy,sys;sys.path[:0]={[p for p in sys.path if p]!r};"
        f"runpy.run_path({script!r},run_name='__main__')"
    )
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    results = []
    with tempfile.TemporaryDirectory(prefix="pr227-allocation-") as temp:
        workspace = Path(temp)
        fixture(workspace / "train.parquet", 1_000_000, "train")
        fixture(workspace / "validation.parquet", 250_000, "train")
        for kind in ("grid", "profile"):
            for rows in (500_000, 2_000_000):
                fixture(workspace / f"{kind}-{rows}.parquet", rows, kind)
        cases = [
            ("train-old", 1_000_000),
            ("train-new", 1_000_000),
            ("grid", 500_000),
            ("grid", 2_000_000),
            ("profile", 500_000),
            ("profile", 2_000_000),
        ]
        for case, rows in cases:
            for repetition in (1, 2):
                output = io.BytesIO()
                smoke = run_smoke(
                    command=[
                        interpreter,
                        "-c",
                        bootstrap,
                        "--worker",
                        "--workspace",
                        str(workspace),
                        "--case",
                        case,
                        "--rows",
                        str(rows),
                    ],
                    enable_tracemalloc=False,
                    poll_interval_seconds=0.005,
                    child_output=output,
                )
                if smoke["exit_code"]:
                    raise RuntimeError(output.getvalue().decode(errors="replace"))
                result = json.loads((workspace / "worker.json").read_text(encoding="utf-8"))
                result.update(
                    peak_rss_bytes=smoke["child_peak_rss_bytes"],
                    rss_samples=smoke["child_rss_sample_count"],
                    repetition=repetition,
                )
                results.append(result)
                print(json.dumps(result), flush=True)
                Path(__file__).with_suffix(".json").write_text(
                    json.dumps(
                        {
                            "python": platform.python_version(),
                            "polars": pl.__version__,
                            "platform": platform.platform(),
                            "threads": 2,
                            "results": results,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )


if __name__ == "__main__":
    main()
