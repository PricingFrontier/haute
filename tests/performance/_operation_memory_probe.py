"""Fresh-process peak-RSS probe for one global Polars operation.

The certification test builds the fixture parquets once and then runs this
script under ``scripts.memory_smoke.run_smoke`` for every operation, so each
measurement starts from a clean interpreter and a clean Polars thread pool.
The plan is sunk through Haute's own ``bounded_sink`` — the same streaming
entry point execution uses — so a materialising operator shows up as peak RSS
above the streaming floor rather than as a difference in collection strategy.

The process runs with spilling off, as every Haute process does. ``--spill-budget-mb``
turns it on at that budget instead, for the spilling measurement, and records the
most the process held in its spill folder.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import threading
import time
from pathlib import Path

import polars as pl

from scripts.memory_smoke import StdlibMemorySampler

#: The streaming controls. ``scan`` is the full-width passthrough sink and the
#: ceiling every streaming operator must stay under. ``scan_head`` is the floor a
#: reducing boundary operator's does-not-stream witness is matched against, where
#: a full-width sink would be an unfairly high control. ``scan_narrow`` is the
#: two-column floor for the narrow-width witness.
CONTROLS = ("scan", "scan_head", "scan_narrow", "scan_gaps")

#: Measured as a variant of a registered operation -- to witness an operator's
#: materialisation, or to size a fan-out join -- and never certified as a
#: registry name themselves.
WITNESS_PROBES = ("over_narrow", "join_asof_big_right", "join_fanout")

#: ``v1_gaps`` is a straight line of slope ``GAP_SLOPE`` in ``key`` with runs of
#: ``GAP_RUN`` nulls punched out every ``GAP_PERIOD`` rows, after a null-free
#: margin of ``GAP_MARGIN`` at each end. ``GAP_PERIOD`` is a divisor of the
#: 25,000-row row-group size and the runs straddle the row-group boundaries, so
#: interpolation has to carry state across row groups.
GAP_SLOPE = 0.25
GAP_RUN = 50
GAP_PERIOD = 12_500
GAP_MARGIN = 1_000

OPERATIONS = (
    "scan",
    "scan_head",
    "scan_narrow",
    "scan_gaps",
    "filter",
    "shift",
    "unpivot",
    "rolling",
    "group_by_dynamic",
    "merge_sorted",
    "group_by",
    "sort",
    "unique",
    "join",
    "join_fanout",
    "join_asof",
    "explode",
    "over",
    "top_k",
    "bottom_k",
    "reverse",
    "interpolate",
    "over_narrow",
    "join_asof_big_right",
    "shift_expr",
    "diff_expr",
    "pct_change_expr",
    "bin_quantiles_expr",
    "bin_ranks_expr",
    "approx_quantile_expr",
)

#: Probes that certify an expression-level registry entry whose name is also
#: taken by a frame probe (``shift``) or is expression-only, mapped to that
#: registry name. Each adds one neighbouring-row column of ``v1`` to the
#: full-width fact, the way an analyst writes a lag or difference feature.
EXPRESSION_PROBES = {
    "shift_expr": "shift",
    "diff_expr": "diff",
    "pct_change_expr": "pct_change",
    "bin_quantiles_expr": "bin_quantiles",
    "bin_ranks_expr": "bin_ranks",
    "approx_quantile_expr": "approx_quantile",
}

#: Operations whose plan is two columns wide, and whose streaming ceiling is
#: therefore a narrow control rather than the full-width passthrough.
NARROW_OPERATIONS = ("interpolate",)

#: Narrow operations that read the nullable ``v1_gaps`` column. Their control is
#: ``scan_gaps`` rather than ``scan_narrow``: a control has to match the
#: operation's input columns *and* their nullability, or the ratio charges the
#: operator for a read cost the control never paid.
GAP_COLUMN_OPERATIONS = ("interpolate",)

#: ``v1_gaps`` is a straight line of slope ``GAP_SLOPE`` in ``key`` with runs of
#: ``GAP_RUN`` nulls punched out every ``GAP_PERIOD`` rows, after a null-free
#: margin of ``GAP_MARGIN`` at each end. ``GAP_PERIOD`` is a divisor of the
#: 25,000-row row-group size and the runs straddle the row-group boundaries, so
#: interpolation has to carry state across row groups.
GAP_SLOPE = 0.25
GAP_RUN = 50
GAP_PERIOD = 12_500
GAP_MARGIN = 1_000


def build_plan(operation: str, fact_path: Path, dim_path: Path, multi_path: Path) -> pl.LazyFrame:
    """Return the lazy plan exercising ``operation`` over the fixture frames."""
    fact = pl.scan_parquet(fact_path)
    dim = pl.scan_parquet(dim_path)
    multi = pl.scan_parquet(multi_path)
    if operation == "scan":
        return fact
    if operation == "scan_head":
        return fact.head(1000)
    if operation == "scan_narrow":
        return fact.select("key", "v1")
    if operation == "scan_gaps":
        # The like-for-like control for a plan that reads the nullable gap
        # column: same columns, same nullability, no operator.
        return fact.select("key", "v1_gaps")
    if operation == "filter":
        return fact.filter(pl.col("v3") > 100)
    if operation == "shift":
        return fact.shift(1)
    if operation == "unpivot":
        return fact.unpivot(index=["key"], on=["v1", "v2", "v3"])
    if operation == "rolling":
        return fact.rolling(index_column="ts", period="1h").agg(pl.col("v1").sum().alias("v1_sum"))
    if operation == "group_by_dynamic":
        return fact.group_by_dynamic("ts", every="1h").agg(pl.col("v1").sum().alias("v1_sum"))
    if operation == "merge_sorted":
        # Both fixtures are written in ascending ``ts`` order, so neither side
        # needs a sort of its own and the measurement is merge_sorted's alone.
        return fact.select("ts", "key").merge_sorted(dim.select("ts", "key"), key="ts")
    if operation == "group_by":
        return fact.group_by("key").agg(pl.col("v1").sum().alias("v1_sum"))
    if operation == "sort":
        return fact.sort("v1")
    if operation == "unique":
        return fact.unique(subset=["key"])
    if operation == "join":
        # The same declared uniqueness contract the planner is handed, so the
        # measurement and the estimate describe one plan and not two.
        return fact.join(dim, on="key", how="inner", validate="m:1")
    if operation == "join_fanout":
        # Three dim rows per key: the output is three times the largest operand,
        # which is the case an input-sized estimate has to bound.
        return fact.join(multi, on="key", how="inner")
    if operation == "join_asof":
        # No leading sort: a chained boundary would take the maximum operator
        # factor and this case would certify ``sort`` instead of ``join_asof``.
        return fact.join_asof(dim, on="ts")
    if operation == "join_asof_big_right":
        # An asof join buffers its right (lookup) port and streams its left, so
        # a wide left with a small right sits near the floor and proves nothing.
        # Swapping the ports puts the large frame in the buffered position.
        return dim.join_asof(fact, on="ts")
    if operation == "explode":
        return fact.explode("tags")
    if operation == "over":
        return fact.with_columns(pl.col("v1").sum().over("key").alias("v1_over"))
    if operation == "over_narrow":
        return fact.select("key", "v1").with_columns(pl.col("v1").sum().over("key"))
    if operation == "top_k":
        return fact.top_k(1000, by="v1")
    if operation == "bottom_k":
        return fact.bottom_k(1000, by="v1")
    if operation == "interpolate":
        return fact.select("key", "v1_gaps").interpolate()
    if operation == "reverse":
        return fact.reverse()
    if operation == "shift_expr":
        return fact.with_columns(pl.col("v1").shift(1).alias("v1_shift"))
    if operation == "diff_expr":
        return fact.with_columns(pl.col("v1").diff().alias("v1_diff"))
    if operation == "pct_change_expr":
        return fact.with_columns(pl.col("v1").pct_change().alias("v1_pct_change"))
    if operation == "bin_quantiles_expr":
        return fact.with_columns(pl.col("v1").bin_quantiles(10, labels=False).alias("v1_decile"))
    if operation == "bin_ranks_expr":
        return fact.with_columns(pl.col("v1").bin_ranks(10, labels=False).alias("v1_rank_band"))
    if operation == "approx_quantile_expr":
        return fact.with_columns(pl.col("v1").approx_quantile(0.5).alias("v1_median"))
    raise ValueError(f"unknown operation {operation!r}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", choices=OPERATIONS, required=True)
    parser.add_argument("--fact", type=Path, required=True)
    parser.add_argument("--dim", type=Path, required=True)
    parser.add_argument("--multi", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--keep-sink",
        action="store_true",
        help="Leave the sunk parquet in place so the parent can verify it.",
    )
    parser.add_argument(
        "--spill-budget-mb",
        type=int,
        help="Spill once Polars' own allocations pass this many megabytes.",
    )
    return parser.parse_args()


def _turn_spilling_on(budget_mb: int) -> None:
    """Spill past *budget_mb*, with room on disk to finish rather than abort."""
    pl.Config.load(
        json.dumps(
            {
                "environment": {
                    "POLARS_OOC_MEMORY_BUDGET_MB": str(budget_mb),
                    "POLARS_OOC_DISK_BUDGET_MB": "65536",
                }
            }
        )
    )


def _folder_bytes(folder: Path) -> tuple[int, int]:
    total = files = 0
    for root, _dirs, names in os.walk(folder):
        for name in names:
            try:
                total += (Path(root) / name).stat().st_size
            except FileNotFoundError:
                continue
            files += 1
    return total, files


class _SpillSampler:
    """The most this process holds in its spill folder, sampled while it runs."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.peak_bytes = 0
        self.peak_files = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, name="spill-sampler", daemon=True)

    def _poll(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(0.02)

    def _sample(self) -> None:
        size, files = _folder_bytes(self.folder)
        self.peak_bytes = max(self.peak_bytes, size)
        self.peak_files = max(self.peak_files, files)

    def __enter__(self) -> _SpillSampler:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()


def main() -> int:
    from haute._polars_utils import (
        DEFAULT_STREAMING_CHUNK_SIZE,
        bounded_sink,
        set_streaming_chunk_size,
    )

    args = _parse_args()
    if args.spill_budget_mb is not None:
        _turn_spilling_on(args.spill_budget_mb)
    # Polars spills into a folder named after the process, inside the spill root.
    spill_folder = Path(os.environ.get("POLARS_OOC_SPILL_DIR", ".")) / str(os.getpid())
    sampler = StdlibMemorySampler()
    gc.collect()
    rss_before = sampler.process_rss_bytes(os.getpid())

    plan = build_plan(args.operation, args.fact, args.dim, args.multi)
    explain_streaming = plan.explain(engine="streaming")
    sink_path = args.output.with_suffix(".sink.parquet")
    set_streaming_chunk_size(DEFAULT_STREAMING_CHUNK_SIZE)
    started = time.perf_counter()
    with _SpillSampler(spill_folder) as spilled:
        bounded_sink(plan, sink_path)
    elapsed_seconds = time.perf_counter() - started
    rows_out = pl.scan_parquet(sink_path).select(pl.len()).collect().item()
    rss_after = sampler.process_rss_bytes(os.getpid())

    payload = {
        "schema_version": 1,
        "operation": args.operation,
        "polars_version": pl.__version__,
        "polars_threads": pl.thread_pool_size(),
        "streaming_chunk_size": DEFAULT_STREAMING_CHUNK_SIZE,
        "rows_out": rows_out,
        "sink_path": str(sink_path),
        "elapsed_seconds": elapsed_seconds,
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "explain_streaming": explain_streaming,
        "spill_budget_mb": args.spill_budget_mb,
        "spill_peak_bytes": spilled.peak_bytes,
        "spill_peak_files": spilled.peak_files,
    }
    # ``--output`` is supplied from pytest's tmp_path by the parent harness.
    args.output.write_text(  # write-sandbox: deliberate
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    if not args.keep_sink:
        sink_path.unlink()
    # Keep the process resident long enough for the parent sampler to observe
    # the operator's working set on coarse/loaded schedulers.
    time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
