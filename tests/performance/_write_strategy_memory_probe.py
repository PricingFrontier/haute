"""Fresh-process peak-RSS probe for chunked write strategies.

Runs under ``scripts.memory_smoke.run_smoke`` in a fresh interpreter so each
measurement starts from a clean baseline and clean thread pool. The query plan
is sunk through Haute's chunked writer (``write_parts(..., fast_checkpoint=True)``)
into a temporary directory, reporting its strategy, row count, part count, and
pre-execution RSS baseline.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from haute._chunked_writes import WriteRecipe, write_parts
from scripts.memory_smoke import StdlibMemorySampler

CASES: tuple[str, ...] = (
    "passthrough_native",
    "filter",
    "filter_with_derived_columns",
    "unnest",
    "filter_with_recipe",
)


def build_frame_and_recipe(case: str, source_path: Path) -> tuple[pl.LazyFrame, WriteRecipe | None]:
    """Build the LazyFrame and optional WriteRecipe for the requested benchmark case."""
    scan = pl.scan_parquet(source_path)
    if case == "passthrough_native":
        # Full-width passthrough forced down the native strategy while retaining every
        # row: Polars will not push a slice through a filter, so sliceable() returns
        # False. This serves as the control attributing memory growth to the sink.
        return scan.filter(pl.lit(True)), None
    if case == "filter":
        # Row-local predicate filtering a subset of rows.
        return scan.filter(pl.col("i00") > 100), None
    if case == "filter_with_derived_columns":
        # Row-local predicate plus derived columns.
        return scan.filter(pl.col("i00") > 100).with_columns(
            d1=pl.col("f00") * 2.0,
            d2=pl.col("f01") + pl.col("f02"),
            d3=pl.col("i00") + 1,
        ), None
    if case == "unnest":
        # Struct constructed and unnested; sliceable() recognizes unnest as slice-
        # transparent, so this takes the sliced strategy and serves as the bounded control.
        return scan.with_columns(
            struct_col=pl.struct(
                pl.col("f00").alias("unnested_1"),
                pl.col("f01").alias("unnested_2"),
            )
        ).unnest("struct_col"), None
    if case == "filter_with_recipe":
        # Filter with chunk-local WriteRecipe; exercises input-sliced writes.
        frame = scan.filter(pl.col("i00") > 100)
        recipe = WriteRecipe(
            input=scan,
            fn=lambda lf: lf.filter(pl.col("i00") > 100),
        )
        return frame, recipe
    raise ValueError(f"unknown case {case!r}")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True, help="Benchmark case to execute.")
    parser.add_argument("--source", type=Path, required=True, help="Source parquet fixture path.")
    parser.add_argument("--output", type=Path, required=True, help="Path to write JSON results.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    sampler = StdlibMemorySampler()
    gc.collect()
    rss_before = sampler.process_rss_bytes(os.getpid())

    frame, recipe = build_frame_and_recipe(args.case, args.source)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as tmp_dir:
        parts_dir = Path(tmp_dir) / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        report = write_parts(parts_dir, frame, recipe=recipe, fast_checkpoint=True)
        elapsed_seconds = time.perf_counter() - started
        sunk_rows = int(pl.scan_parquet(parts_dir / "*.parquet").select(pl.len()).collect().item())
        rss_after = sampler.process_rss_bytes(os.getpid())

    payload = {
        "schema_version": 1,
        "case": args.case,
        "row_count": sunk_rows,
        "strategy": report.strategy,
        "part_count": report.chunks,
        "chunk_rows": report.chunk_rows,
        "native_reason": report.native_reason,
        "elapsed_seconds": elapsed_seconds,
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
    }
    # ``--output`` is supplied from pytest's tmp_path by the parent harness.
    args.output.write_text(  # write-sandbox: deliberate
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    # Keep the process resident briefly so the parent sampler observes peak working set.
    time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
