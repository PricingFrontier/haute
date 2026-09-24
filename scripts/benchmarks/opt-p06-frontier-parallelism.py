"""OPT-P06: does the library's parallel frontier sweep pay for itself?

Compares ``solver.frontier(..., parallel=False)`` (what Haute calls today)
with ``parallel=True`` over representative frontiers. Every measurement runs in
a fresh subprocess so peak memory and the library's thread-pool start-up are
measured cleanly. The decision rule is the roadmap's: implement only if the
median wall-clock improves by at least 20% without raising peak memory or
changing point order or numerical results; otherwise record no change.

Usage: ``uv run python scripts/benchmarks/opt-p06-frontier-parallelism.py``
writes ``opt-p06-frontier-parallelism.json`` beside this script.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPEATS = 5
IMPROVEMENT_THRESHOLD = 0.20
# Numerical agreement: the sweep's own convergence tolerance is 1e-5 relative.
RELATIVE_TOLERANCE = 1e-4

WORKLOADS: list[dict[str, Any]] = [
    {"name": "small-1d", "quotes": 5_000, "scenarios": 11, "constraints": 1, "points": 20},
    {"name": "large-1d", "quotes": 200_000, "scenarios": 21, "constraints": 1, "points": 20},
    {"name": "small-2d", "quotes": 5_000, "scenarios": 11, "constraints": 2, "points": 8},
    {"name": "large-2d", "quotes": 100_000, "scenarios": 21, "constraints": 2, "points": 8},
]


def _write_workload(workload: dict[str, Any], directory: Path) -> Path:
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(20260924)
    quotes, scenarios = workload["quotes"], workload["scenarios"]
    quote_ids = np.repeat(np.arange(quotes), scenarios)
    step = np.tile(np.arange(scenarios), quotes)
    multiplier = 0.8 + 0.4 * step / (scenarios - 1)
    base_premium = np.repeat(rng.uniform(200, 2000, quotes), scenarios)
    elasticity = np.repeat(rng.uniform(2.0, 8.0, quotes), scenarios)
    conversion = 1.0 / (1.0 + np.exp(elasticity * (multiplier - 1.0)))
    loyalty = np.repeat(rng.uniform(0.5, 1.0, quotes), scenarios)
    frame = pl.DataFrame(
        {
            "quote_id": pl.Series(quote_ids.astype(str)).cast(pl.Categorical),
            "scenario_index": pl.Series(step, dtype=pl.Int32),
            "scenario_value": pl.Series(multiplier, dtype=pl.Float32),
            "expected_income": pl.Series(
                conversion * base_premium * (multiplier - 0.7), dtype=pl.Float32
            ),
            "volume": pl.Series(conversion, dtype=pl.Float32),
            "retention": pl.Series(conversion * loyalty, dtype=pl.Float32),
        }
    )
    path = directory / f"{workload['name']}.parquet"
    frame.write_parquet(path)
    return path


def _measure(path: str, workload: dict[str, Any], parallel: bool) -> dict[str, Any]:
    """Child process: build the grid, then time one frontier sweep and its peak RSS."""
    import polars as pl
    from price_contour import OnlineOptimiser, build_grid_from_parquet

    sys.path.insert(0, str(HERE.parents[1] / "src"))
    from haute._process_memory import current_process_rss_bytes

    constraint_names = ["volume", "retention"][: workload["constraints"]]
    grid = build_grid_from_parquet(path, constraint_names)
    frame = pl.read_parquet(path, columns=constraint_names)
    total = {name: float(frame[name].sum()) / workload["scenarios"] for name in constraint_names}
    constraints = {name: {"min": 0.9 * total[name]} for name in constraint_names}
    ranges = {name: (0.7 * total[name], 1.0 * total[name]) for name in constraint_names}
    solver = OnlineOptimiser(objective="expected_income", constraints=constraints)
    initial = solver.solve(grid).lambdas

    baseline = current_process_rss_bytes() or 0
    peak = [baseline]
    done = threading.Event()

    def sample() -> None:
        while not done.is_set():
            peak[0] = max(peak[0], current_process_rss_bytes() or 0)
            time.sleep(0.005)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.perf_counter()
    result = solver.frontier(
        grid,
        threshold_ranges=ranges,
        n_points_per_dim=workload["points"],
        initial_lambdas=initial,
        parallel=parallel,
    )
    elapsed = time.perf_counter() - started
    done.set()
    sampler.join()
    points = result.points
    return {
        "seconds": elapsed,
        "peak_rss_growth_bytes": max(0, peak[0] - baseline),
        "columns": points.columns,
        "rows": points.to_dicts(),
    }


def _run_child(path: Path, workload: dict[str, Any], parallel: bool) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, __file__, "--child", str(path), json.dumps(workload), str(parallel)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _numeric_agreement(
    serial: list[dict[str, Any]], parallel: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compare the result fields; iteration counts differ with the warm start by design."""
    if len(serial) != len(parallel):
        return {"same_point_count": False}
    worst = 0.0
    worst_field = None
    order_matches = True
    convergence_matches = True
    iteration_differences = 0
    for left, right in zip(serial, parallel, strict=True):
        for key, value in left.items():
            other = right.get(key)
            if key.startswith("threshold") and value != other:
                order_matches = False
            if key == "converged" and value != other:
                convergence_matches = False
            if key == "iterations":
                iteration_differences += value != other
                continue
            if (
                isinstance(value, int | float)
                and isinstance(other, int | float)
                and not isinstance(value, bool)
            ):
                scale = max(1.0, abs(value), abs(other))
                difference = abs(value - other) / scale
                if difference > worst:
                    worst, worst_field = difference, key
    return {
        "same_point_count": True,
        "same_point_order": order_matches,
        "same_convergence": convergence_matches,
        "max_relative_difference": worst,
        "max_difference_field": worst_field,
        "points_with_different_iteration_counts": iteration_differences,
        "within_tolerance": worst <= RELATIVE_TOLERANCE and convergence_matches,
    }


def main() -> None:
    import price_contour

    report: dict[str, Any] = {
        "package": "OPT-P06",
        "question": "Does solver.frontier(parallel=True) improve median wall-clock by >=20% "
        "without raising peak memory or changing point order or results?",
        "library": {"price_contour": getattr(price_contour, "__version__", "unknown")},
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "rayon_num_threads": os.environ.get("RAYON_NUM_THREADS"),
        },
        "repeats": REPEATS,
        "workloads": [],
    }
    with tempfile.TemporaryDirectory() as tmp:
        for workload in WORKLOADS:
            path = _write_workload(workload, Path(tmp))
            runs: dict[str, list[dict[str, Any]]] = {"serial": [], "parallel": []}
            for _ in range(REPEATS):
                for mode, parallel in (("serial", False), ("parallel", True)):
                    runs[mode].append(_run_child(path, workload, parallel))
            summary: dict[str, Any] = {"workload": workload}
            for mode in ("serial", "parallel"):
                summary[mode] = {
                    "median_seconds": statistics.median(r["seconds"] for r in runs[mode]),
                    "median_peak_rss_growth_bytes": statistics.median(
                        r["peak_rss_growth_bytes"] for r in runs[mode]
                    ),
                    "points": len(runs[mode][0]["rows"]),
                }
            serial_median = summary["serial"]["median_seconds"]
            parallel_median = summary["parallel"]["median_seconds"]
            summary["improvement"] = (serial_median - parallel_median) / serial_median
            # Haute checks cancellation before and after the one library call, so a
            # cancel is observed only when the sweep returns: its latency is the sweep.
            summary["cancellation_latency_seconds"] = {
                "serial": serial_median,
                "parallel": parallel_median,
            }
            summary["peak_rss_ratio"] = summary["parallel"]["median_peak_rss_growth_bytes"] / max(
                1, summary["serial"]["median_peak_rss_growth_bytes"]
            )
            summary["agreement"] = _numeric_agreement(
                runs["serial"][0]["rows"], runs["parallel"][0]["rows"]
            )
            report["workloads"].append(summary)
            serial_kib = summary["serial"]["median_peak_rss_growth_bytes"] // 1024
            parallel_kib = summary["parallel"]["median_peak_rss_growth_bytes"] // 1024
            print(
                f"{workload['name']}: serial {serial_median:.3f}s, "
                f"parallel {parallel_median:.3f}s, "
                f"improvement {summary['improvement']:.0%}, "
                f"peak RSS growth {serial_kib} KiB -> {parallel_kib} KiB, "
                f"agreement {summary['agreement']}",
                flush=True,
            )
    qualifies = [
        w["improvement"] >= IMPROVEMENT_THRESHOLD
        and w["peak_rss_ratio"] <= 1.0
        and w["agreement"].get("same_point_order", False)
        and w["agreement"].get("within_tolerance", False)
        for w in report["workloads"]
    ]
    report["decision"] = "implement" if all(qualifies) else "no-change"
    (HERE / "opt-p06-frontier-parallelism.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print("decision:", report["decision"])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        outcome = _measure(sys.argv[2], json.loads(sys.argv[3]), sys.argv[4] == "True")
        print(json.dumps(outcome, default=str))
    else:
        main()
