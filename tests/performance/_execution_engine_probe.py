"""Fresh-process Parquet collection probe used by performance certification."""

from __future__ import annotations

import argparse
import gc
import json
import os
import time
from pathlib import Path

import polars as pl

from scripts.memory_smoke import StdlibMemorySampler


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--mode", choices=("projected", "full"), required=True)
    parser.add_argument("--columns", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    sampler = StdlibMemorySampler()
    gc.collect()
    rss_before = sampler.process_rss_bytes(os.getpid())

    source = pl.scan_parquet(args.parquet)
    plan = source.select(args.columns) if args.mode == "projected" else source
    optimized_plan = plan.explain(optimized=True)
    started = time.perf_counter()
    frame = plan.collect(engine="streaming")
    elapsed_seconds = time.perf_counter() - started

    semantic = frame.select(
        pl.len().alias("rows"),
        *[pl.col(column).sum().alias(column) for column in args.columns],
    ).to_dicts()[0]
    rss_after = sampler.process_rss_bytes(os.getpid())
    payload = {
        "schema_version": 1,
        "mode": args.mode,
        "rows": frame.height,
        "width": frame.width,
        "estimated_size_bytes": frame.estimated_size(),
        "elapsed_seconds": elapsed_seconds,
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "optimized_plan": optimized_plan,
        "semantic_summary": semantic,
    }
    # ``--output`` is supplied from pytest's tmp_path by the parent harness.
    args.output.write_text(  # write-sandbox: deliberate
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    # Keep the collected frame resident long enough for the independent parent
    # sampler to observe its working set on coarse/loaded CI schedulers.
    time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
