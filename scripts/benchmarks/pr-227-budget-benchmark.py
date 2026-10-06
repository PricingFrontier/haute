"""Fresh-process measurements for PR-227's budget-aware writer paths.

Run with ``uv run python scripts/benchmarks/pr-227-budget-benchmark.py``. Fixtures
and verification live in the parent; each write runs in a fresh interpreter
with two Polars threads and external RSS sampled every five milliseconds.
Logical source occurrences count query-plan references, not physical I/O.
"""

from __future__ import annotations

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
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.memory_smoke import run_smoke  # noqa: E402

ROWS = 25_000
JOIN_CASES = (
    ("join-100k-100k", 100_000, 100_000, False),
    ("join-400k-400k", 400_000, 400_000, False),
    ("join-1600k-100k", 1_600_000, 100_000, False),
    ("join-skew-100k-100k-10matches", 100_000, 100_000, True),
)
WIDE_CASES = (250_000, 1_000_000)
MIB = 1024 * 1024


def join_fixture(path: Path, size: int, key_space: int, side: str) -> None:
    rng = np.random.default_rng(227 + size + (0 if side == "left" else 1))
    keys = np.arange(size, dtype=np.int64) % key_space
    rng.shuffle(keys)
    pl.DataFrame({"key": keys, **{f"{side}{i}": rng.random(size) for i in range(4)}}).write_parquet(
        path, row_group_size=ROWS
    )


def wide_fixture(path: Path, rows: int) -> int:
    """Write wide data in row groups without retaining the million-row strings."""
    writer: pq.ParquetWriter | None = None
    decoded_bytes = 0
    try:
        for offset in range(0, rows, ROWS):
            count = min(ROWS, rows - offset)
            columns: dict[str, object] = {"i0": np.arange(offset, offset + count, dtype=np.int64)}
            columns.update({f"f{i}": np.full(count, float(i)) for i in range(20)})
            columns.update({f"i{i}": np.full(count, i, dtype=np.int64) for i in range(1, 10)})
            columns.update({f"s{i}": [chr(65 + i) * (64 + i * 48)] * count for i in range(10)})
            frame = pl.DataFrame(columns)
            decoded_bytes += sum(frame.get_column(name).estimated_size() for name in frame.columns)
            table = frame.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(path, table.schema, compression="zstd")
            writer.write_table(table, row_group_size=ROWS)
    finally:
        if writer is not None:
            writer.close()
    return decoded_bytes


def fingerprint(lf: pl.LazyFrame) -> dict[str, object]:
    result = lf.select(
        pl.len().alias("rows"), pl.struct(pl.all()).hash(seed=227).sum().alias("row_hash_sum")
    ).collect(engine="streaming")
    return {
        "schema": {key: str(value) for key, value in lf.collect_schema().items()},
        **result.row(0, named=True),
    }


def worker(workspace: Path, kind: str, case: str, run_id: str) -> None:
    import haute._chunked_writes as writes
    from haute._execution_context import ExecutionContext, ExecutionProfile, current_rss_bytes

    source_queries: dict[str, int] = {}
    original_collect, original_sink = writes.execution_collect, writes.bounded_hashed_sink
    temporary_bytes = 0

    def count(lf: pl.LazyFrame) -> None:
        plan = lf.explain(optimized=False).replace("\\", "/")
        for source in workspace.glob("*.parquet"):
            count_for_source = plan.count(source.as_posix())
            if count_for_source:
                source_queries[source.name] = source_queries.get(source.name, 0) + count_for_source

    def measured_collect(lf: pl.LazyFrame, *args: object, **kwargs: object) -> pl.DataFrame:
        count(lf)
        return original_collect(lf, *args, **kwargs)

    def measured_sink(lf: pl.LazyFrame, path: Path, *args: object, **kwargs: object) -> str:
        nonlocal temporary_bytes
        count(lf)
        result = original_sink(lf, path, *args, **kwargs)
        if ".chunk-inputs" in path.parts:
            temporary_bytes += path.stat().st_size
        return result

    writes.execution_collect = measured_collect
    writes.bounded_hashed_sink = measured_sink
    baseline = current_rss_bytes()
    limit = 512 * MIB if kind == "join" else 256 * MIB
    context = ExecutionContext(
        operation=f"pr227-budget-{kind}",
        profile=ExecutionProfile.LAZY_SINK,
        memory_baseline_bytes=baseline,
        memory_limit_bytes=limit,
        memory_sampler=current_rss_bytes,
    )
    target = workspace / f"out-{kind}-{case}-{run_id}"
    target.mkdir()
    started = time.perf_counter()
    try:
        if kind == "join":
            left, right = (
                pl.scan_parquet(workspace / f"{case}-left.parquet"),
                pl.scan_parquet(workspace / f"{case}-right.parquet"),
            )
            skew = case.startswith("join-skew")
            recipe = writes.JoinRecipe(
                left, right, {"on": "key", "how": "left", "validate": "m:m" if skew else "m:1"}
            )
            result = writes.write_parts(
                target, recipe.native(), join=recipe, chunk_rows=ROWS, execution_context=context
            )
        else:
            scan = pl.scan_parquet(workspace / f"wide-{case}.parquet")
            if run_id == "plain":
                result = writes.write_parts(
                    target, scan, chunk_rows=500_000, execution_context=context
                )
            else:
                recipe = writes.WriteRecipe(
                    input=scan, fn=lambda lf: lf.filter(pl.col("i0") % 3 != 0)
                )
                result = writes.write_parts(
                    target,
                    recipe.native(),
                    recipe=recipe,
                    chunk_rows=500_000,
                    execution_context=context,
                )
        report: dict[str, object] = {
            "status": "ok",
            "kind": kind,
            "case": case,
            "strategy": result.strategy,
            "native_reason": result.native_reason,
            "chunk_rows": result.chunk_rows,
            "chunks": result.chunks,
            "write_seconds": time.perf_counter() - started,
            "baseline_rss_bytes": baseline,
            "logical_source_occurrences": source_queries,
            "temporary_bytes_written": temporary_bytes,
            "output_bytes": sum(p.stat().st_size for p in writes.part_paths(target)),
        }
    except Exception as exc:  # measurements must preserve an honest failed run
        report = {
            "status": "failed",
            "kind": kind,
            "case": case,
            "failure_type": type(exc).__name__,
            "failure": str(exc),
            "baseline_rss_bytes": baseline,
            "write_seconds": time.perf_counter() - started,
            "logical_source_occurrences": source_queries,
        }
    (workspace / "worker.json").write_text(json.dumps(report), encoding="utf-8")


def run_worker(
    workspace: Path, kind: str, case: str, run_id: str
) -> tuple[dict[str, object], dict[str, object]]:
    script = str(Path(__file__).resolve())
    bootstrap = (
        f"import runpy,sys;sys.path[:0]={[p for p in sys.path if p]!r};"
        f"runpy.run_path({script!r},run_name='__main__')"
    )
    log = io.BytesIO()
    smoke = run_smoke(
        command=[
            str(getattr(sys, "_base_executable", sys.executable)),
            "-c",
            bootstrap,
            "--worker",
            "--workspace",
            str(workspace),
            "--kind",
            kind,
            "--case",
            case,
            "--run-id",
            run_id,
        ],
        enable_tracemalloc=False,
        poll_interval_seconds=0.005,
        child_output=log,
    )
    report = json.loads((workspace / "worker.json").read_text(encoding="utf-8"))
    report.update(
        peak_rss_bytes=smoke["child_peak_rss_bytes"],
        rss_samples=smoke["child_rss_sample_count"],
        incremental_peak_rss_bytes=smoke["child_peak_rss_bytes"] - report["baseline_rss_bytes"],
        worker_exit_code=smoke["exit_code"],
        worker_log=log.getvalue().decode(errors="replace"),
    )
    return report, smoke


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--kind", choices=("join", "wide"))
    parser.add_argument("--case")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.worker:
        worker(args.workspace, args.kind, args.case, args.run_id)
        return
    baseline_path = Path(__file__).with_name("pr-227-implementation-benchmark.json")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_by_case = {
        (item["case"], item["strategy"]): item
        for item in baseline["results"]
        if item["strategy"] == "native"
    }
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="pr227-budget-") as temp:
        workspace = Path(temp)
        for name, left_rows, right_rows, skew in JOIN_CASES:
            key_space = 10_000 if skew else right_rows
            join_fixture(workspace / f"{name}-left.parquet", left_rows, key_space, "left")
            join_fixture(workspace / f"{name}-right.parquet", right_rows, key_space, "right")
            expected = fingerprint(
                pl.scan_parquet(workspace / f"{name}-left.parquet").join(
                    pl.scan_parquet(workspace / f"{name}-right.parquet"), on="key", how="left"
                )
            )
            for repetition in (1, 2):
                result, _ = run_worker(workspace, "join", name, f"r{repetition}")
                result["repetition"] = repetition
                prior = baseline_by_case.get((f"{name}-r{repetition}", "native"))
                if prior is not None:
                    result["baseline_native"] = {
                        "write_seconds": prior["write_seconds"],
                        "peak_rss_bytes": prior["peak_rss_bytes"],
                        "strategy": prior["strategy"],
                    }
                if result["status"] == "ok":
                    actual = fingerprint(
                        pl.scan_parquet(workspace / f"out-join-{name}-r{repetition}" / "*.parquet")
                    )
                    result.update(verified=actual == expected, expected=expected, actual=actual)
                results.append(result)
                print(json.dumps(result), flush=True)
        for rows in WIDE_CASES:
            case = str(rows)
            decoded_bytes = wide_fixture(workspace / f"wide-{case}.parquet", rows)
            for variant in ("plain", "filter"):
                result, _ = run_worker(workspace, "wide", case, variant)
                result.update(variant=variant, generated_decoded_bytes=decoded_bytes)
                if result["status"] == "ok":
                    source = pl.scan_parquet(workspace / f"wide-{case}.parquet")
                    expected = fingerprint(
                        source if variant == "plain" else source.filter(pl.col("i0") % 3 != 0)
                    )
                    actual = fingerprint(
                        pl.scan_parquet(workspace / f"out-wide-{case}-{variant}" / "*.parquet")
                    )
                    result.update(verified=actual == expected, expected=expected, actual=actual)
                results.append(result)
                print(json.dumps(result), flush=True)
    report = {
        "python": platform.python_version(),
        "polars": pl.__version__,
        "platform": platform.platform(),
        "threads": 2,
        "rss_poll_interval_ms": 5,
        "baseline_reference": str(baseline_path),
        "baseline_exists": baseline_path.exists(),
        "baseline_result_count": len(baseline["results"]),
        "results": results,
    }
    Path(__file__).with_suffix(".json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
