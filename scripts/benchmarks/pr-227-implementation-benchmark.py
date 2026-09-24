"""Reproducible write-only measurements; verification runs in the parent.

Run with uv run python scripts/benchmarks/pr-227-implementation-benchmark.py.
Fixtures and output live in a private temporary directory. Two Polars threads,
25,000-row groups/chunks, fresh real interpreters, 5ms external RSS sampling.
Scan counts are logical source occurrences in executed query plans, not an
assertion about physical I/O (footer pruning and OS caches can eliminate reads).
"""

from __future__ import annotations

# ruff: noqa: E402 — fixed thread-count environment must precede Polars import.
import argparse
import io
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

os.environ["POLARS_MAX_THREADS"] = "2"

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.memory_smoke import run_smoke

ROWS = 25_000
CASES = (
    ("join-100k-100k", 100_000, 100_000, False),
    ("join-400k-100k", 400_000, 100_000, False),
    ("join-1600k-100k", 1_600_000, 100_000, False),
    ("join-100k-400k", 100_000, 400_000, False),
    ("join-100k-1600k", 100_000, 1_600_000, False),
    ("join-400k-400k", 400_000, 400_000, False),
    ("join-skew", 100_000, 100_000, True),
)


def fixture(path: Path, size: int, key_space: int, side: str) -> None:
    rng = np.random.default_rng(227 + size)
    keys = np.arange(size, dtype=np.int64) % key_space
    rng.shuffle(keys)
    frame = pl.DataFrame({"key": keys, **{f"{side}{i}": rng.random(size) for i in range(4)}})
    frame.write_parquet(path, row_group_size=ROWS)


def fingerprint(lf: pl.LazyFrame) -> dict[str, object]:
    result = lf.select(
        pl.len().alias("rows"),
        pl.struct(pl.all()).hash(seed=227).sum().alias("row_hash_sum"),
    ).collect(engine="streaming")
    return {
        "schema": {key: str(value) for key, value in lf.collect_schema().items()},
        **result.row(0, named=True),
    }


def worker(workspace: Path, case: str, strategy: str) -> None:
    import haute._chunked_writes as writes
    from haute._execution_context import current_rss_bytes
    from haute._polars_utils import bounded_sink

    left_path, right_path = workspace / f"{case}-left.parquet", workspace / f"{case}-right.parquet"
    left, right = pl.scan_parquet(left_path), pl.scan_parquet(right_path)
    recipe = writes.JoinRecipe(left, right, {"on": "key", "how": "left"})
    output = workspace / f"{case}-{strategy}"
    scans = {"left": 0, "right": 0}
    temporary_bytes = 0
    collect, sink = writes.execution_collect, writes.bounded_hashed_sink

    def count(lf: pl.LazyFrame) -> None:
        plan = lf.explain(optimized=False)
        for side, path in (("left", left_path), ("right", right_path)):
            scans[side] += plan.replace("\\", "/").count(path.as_posix())

    def measured_collect(lf, *args, **kwargs):
        count(lf)
        return collect(lf, *args, **kwargs)

    def measured_sink(lf, path, *args, **kwargs):
        nonlocal temporary_bytes
        count(lf)
        result = sink(lf, path, *args, **kwargs)
        if ".chunk-inputs" in Path(path).parts:
            temporary_bytes += Path(path).stat().st_size
        return result

    writes.execution_collect, writes.bounded_hashed_sink = measured_collect, measured_sink
    baseline = current_rss_bytes()
    start = time.perf_counter()
    if strategy == "native":
        count(recipe.native())
        bounded_sink(recipe.native(), output, fast_checkpoint=False)
        files = [output]
    else:
        output.mkdir()
        writes.write_parts(
            output, recipe.native(), join=recipe, chunk_rows=ROWS, fast_checkpoint=False
        )
        files = writes.part_paths(output)
    seconds = time.perf_counter() - start
    report = {
        "case": case,
        "strategy": strategy,
        "write_seconds": seconds,
        "baseline_rss_bytes": baseline,
        "logical_source_occurrences": scans,
        "temporary_bytes_written": temporary_bytes,
        "output_bytes": sum(path.stat().st_size for path in files),
        "parts": len(files),
    }
    (workspace / "worker.json").write_text(json.dumps(report), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--strategy", choices=("native", "recipe"))
    args = parser.parse_args()
    if args.worker:
        worker(args.workspace, args.case, args.strategy)
        return
    results = []
    script = str(Path(__file__).resolve())
    bootstrap = (
        f"import runpy,sys;sys.path[:0]={[p for p in sys.path if p]!r};"
        f"runpy.run_path({script!r},run_name='__main__')"
    )
    interpreter = str(getattr(sys, "_base_executable", sys.executable))
    with tempfile.TemporaryDirectory(prefix="pr227-implementation-") as temp:
        workspace = Path(temp)
        for name, driving, lookup, skew in CASES:
            key_space = 10_000 if skew else lookup
            fixture(workspace / f"{name}-left.parquet", driving, key_space, "left")
            fixture(workspace / f"{name}-right.parquet", lookup, key_space, "right")
            expected = fingerprint(
                pl.scan_parquet(workspace / f"{name}-left.parquet").join(
                    pl.scan_parquet(workspace / f"{name}-right.parquet"), on="key", how="left"
                )
            )
            for strategy in ("native", "recipe"):
                for repetition in (1, 2):
                    # Each repetition has its own output name; no measured deletion.
                    run_name = f"{name}-r{repetition}"
                    for side in ("left", "right"):
                        destination = workspace / f"{run_name}-{side}.parquet"
                        if not destination.exists():
                            os.link(workspace / f"{name}-{side}.parquet", destination)
                    log = io.BytesIO()
                    smoke = run_smoke(
                        command=[
                            interpreter,
                            "-c",
                            bootstrap,
                            "--worker",
                            "--workspace",
                            str(workspace),
                            "--case",
                            run_name,
                            "--strategy",
                            strategy,
                        ],
                        enable_tracemalloc=False,
                        poll_interval_seconds=0.005,
                        child_output=log,
                    )
                    if smoke["exit_code"]:
                        raise RuntimeError(log.getvalue().decode(errors="replace"))
                    result = json.loads((workspace / "worker.json").read_text(encoding="utf-8"))
                    output = workspace / f"{run_name}-{strategy}"
                    verify_start = time.perf_counter()
                    actual = fingerprint(
                        pl.scan_parquet(output if output.is_file() else output / "*.parquet")
                    )
                    if actual != expected:
                        raise AssertionError((name, strategy, actual, expected))
                    result.update(
                        peak_rss_bytes=smoke["child_peak_rss_bytes"],
                        rss_samples=smoke["child_rss_sample_count"],
                        repetition=repetition,
                        input_decoded_fixed_width_bytes=(driving + lookup) * 40,
                        verification_seconds=time.perf_counter() - verify_start,
                        verified=True,
                    )
                    results.append(result)
                    print(json.dumps(result), flush=True)
    report = {
        "python": platform.python_version(),
        "polars": pl.__version__,
        "platform": platform.platform(),
        "threads": 2,
        "chunk_rows": ROWS,
        "peak_excludes_verification": True,
        "results": results,
    }
    Path(__file__).with_suffix(".json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
