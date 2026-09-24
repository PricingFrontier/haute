from __future__ import annotations

# ruff: noqa: E402 — fixed thread-count environment must precede Polars import.
import argparse
import importlib.util
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

s = importlib.util.spec_from_file_location(
    "allocation", ROOT / "scripts/benchmarks/pr-227-allocation-benchmark.py"
)
allocation = importlib.util.module_from_spec(s)
s.loader.exec_module(allocation)


def fixture(path, rows):
    with pq.ParquetWriter(path, allocation.make_batch(0, 1, "profile").to_arrow().schema) as w:
        for start in range(0, rows, 25000):
            w.write_table(
                allocation.make_batch(start, min(25000, rows - start), "profile").to_arrow()
            )


def worker(ws, rows):
    from haute._execution_context import ExecutionContext, ExecutionProfile, current_rss_bytes
    from haute._frame_profile import _build_frame_stats

    scratch = ws / f"scratch-{rows}-{os.getpid()}"
    frame = pl.scan_parquet(ws / f"profile-{rows}.parquet")
    ctx = ExecutionContext(operation="allocation_probe", profile=ExecutionProfile.EXPLORE_ANALYSIS)
    baseline = current_rss_bytes()
    started = time.perf_counter()
    stats = _build_frame_stats(
        frame, frame.collect_schema(), execution_context=ctx, scratch_directory=scratch
    )
    elapsed = time.perf_counter() - started
    assert stats.row_count == rows and stats.overview_summary.data_quality.duplicate_row_count == 0
    for col in stats.columns:
        assert col.null_count == 0
        if col.name.startswith("f"):
            assert col.distinct_count == 100003
        if col.name == "label":
            assert col.distinct_count == min(rows, 500003)
    files = list(scratch.rglob("*")) if scratch.exists() else []
    temp_bytes = sum(p.stat().st_size for p in files if p.is_file())
    (ws / "worker.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "elapsed_seconds": elapsed,
                "stats": {"row_count": stats.row_count, "columns": len(stats.columns)},
                "temporary_bytes_written": temp_bytes,
                "scratch_files": len([p for p in files if p.is_file()]),
                "baseline_rss_bytes": baseline,
                "end_rss_bytes": current_rss_bytes(),
            }
        ),
        encoding="utf-8",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--workspace", type=Path)
    ap.add_argument("--rows", type=int)
    a = ap.parse_args()
    if a.worker:
        worker(a.workspace, a.rows)
        return
    results = []
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    script = str(Path(__file__).resolve())
    bootstrap = (
        f"import runpy,sys;sys.path[:0]={[p for p in sys.path if p]!r};"
        f"runpy.run_path({script!r},run_name='__main__')"
    )
    with tempfile.TemporaryDirectory(prefix="pr227-profile-") as td:
        ws = Path(td)
        for rows in (500000, 2000000):
            fixture(ws / f"profile-{rows}.parquet", rows)
            for rep in (1, 2):
                out = io.BytesIO()
                smoke = run_smoke(
                    command=[
                        interpreter,
                        "-c",
                        bootstrap,
                        "--worker",
                        "--workspace",
                        str(ws),
                        "--rows",
                        str(rows),
                    ],
                    enable_tracemalloc=False,
                    poll_interval_seconds=0.005,
                    child_output=out,
                )
                if smoke["exit_code"]:
                    raise RuntimeError(out.getvalue().decode(errors="replace"))
                r = json.loads((ws / "worker.json").read_text(encoding="utf-8"))
                r.update(
                    repetition=rep,
                    peak_rss_bytes=smoke["child_peak_rss_bytes"],
                    incremental_peak_rss_bytes=smoke["child_peak_rss_bytes"]
                    - r["baseline_rss_bytes"],
                    rss_samples=smoke["child_rss_sample_count"],
                    worker_exit_code=smoke["exit_code"],
                )
                results.append(r)
                print(json.dumps(r), flush=True)
    Path(__file__).with_suffix(".json").write_text(
        json.dumps(
            {
                "python": platform.python_version(),
                "polars": pl.__version__,
                "threads": 2,
                "rss_poll_interval_ms": 5,
                "results": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
