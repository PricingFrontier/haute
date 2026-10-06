"""OPT-V09B: peak memory of the bounded choice queries over the chosen scenarios.

Writes a synthetic as-solved apply artifact (one row per quote, the columns
price-contour writes: a String quote id, Int32 step, Float32 scenario value,
objective and one constraint) and a quote-analysis side table (an 8-level
String ``region`` and an Int32 ``tier``) under the owned artifact roots, then
runs each reducer through ``ChoiceQueryService.choice_query`` in a fresh
process. It records the process's RSS before the query, its peak
(``VmHWM``) after, and the query's own admission estimate.

Usage: ``uv run python scripts/benchmarks/opt-v09b-choice-query-memory.py
[--quotes 1000000 5000000] [--repeats 3]`` writes
``opt-v09b-choice-query-memory.json`` beside this script.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REDUCERS = ("histogram", "group_by", "index", "top_k")
REGIONS = ("North", "South", "East", "West", "Midlands", "Wales", "Scotland", "Ulster")


def _write_fixture(quotes: int, directory: Path) -> dict[str, Any]:
    import numpy as np
    import polars as pl

    from haute.routes._optimiser_artifacts import (
        _new_quote_analysis_directory,
        _persist_apply_result_artifact,
        _quote_analysis_handle,
    )

    rng = np.random.default_rng(7)
    ids = pl.Series("quote_id", [f"Q{quote:09d}" for quote in range(quotes)])
    steps = rng.integers(0, 10, quotes).astype(np.int32)
    grid = np.linspace(0.8, 1.25, 10).astype(np.float32)
    apply = pl.DataFrame(
        {
            "quote_id": ids,
            "optimal_step": steps,
            "optimal_scenario_value": grid[steps],
            "optimal_objective": rng.uniform(10, 1000, quotes).astype(np.float32),
            "optimal_volume": rng.uniform(0.1, 1.0, quotes).astype(np.float32),
        }
    )
    apply_handle = _persist_apply_result_artifact(_Frame(apply))
    del apply
    analysis_dir = _new_quote_analysis_directory()
    analysis_handle = _quote_analysis_handle(
        analysis_dir,
        row_count=quotes,
        columns=["region", "tier"],
        analysis_source="data_input",
        missing_quote_count=0,
        column_stats={},
    )
    pl.DataFrame(
        {
            "quote_id": ids,
            "region": pl.Series(np.array(REGIONS)[rng.integers(0, 8, quotes)]),
            "tier": rng.integers(1, 6, quotes).astype(np.int32),
            "__haute_analysis_row_present": pl.repeat(True, quotes, eager=True),
        }
    ).write_parquet(analysis_handle["path"])
    return {
        "apply_result": apply_handle,
        "quote_analysis": analysis_handle,
        "scenario_grid": [
            {"optimal_step": step, "scenario_value": float(value)}
            for step, value in enumerate(grid)
        ],
        "directory": str(directory),
    }


class _Frame:
    def __init__(self, frame: Any) -> None:
        self.dataframe = frame


def _reducer(name: str, quotes: int) -> Any:
    from haute.routes._optimiser_outcomes import RowIndex, ScenarioHistogram, SegmentGroupBy, TopK

    return {
        "histogram": lambda: ScenarioHistogram(),
        "group_by": lambda: SegmentGroupBy(("region", "tier"), limit=100),
        "index": lambda: RowIndex(offset=quotes // 2, limit=1000),
        "top_k": lambda: TopK("optimal_objective", k=1000),
    }[name]()


def _rss_bytes() -> int:
    with open("/proc/self/statm", encoding="ascii") as statm:
        return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def _peak_rss_bytes() -> int:
    """This process's own high-water RSS (``VmHWM``).

    Not ``ru_maxrss``: Linux carries it over ``fork`` and ``exec``, so a child
    would report the parent's peak from writing the fixture.
    """
    with open("/proc/self/status", encoding="ascii") as status:
        for line in status:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("/proc/self/status has no VmHWM line")


def _measure(fixture_path: str, reducer_name: str, with_analysis: bool) -> dict[str, Any]:
    """Run one query in this (fresh) process and report its memory."""
    from haute import _ram_estimate
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_frontier import OptimiserFrontierService
    from haute.routes._optimiser_outcomes import ChoiceQueryService, ChoiceTarget

    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    quotes = fixture["quotes"]
    handles = {"apply_result": fixture["apply_result"]}
    if with_analysis:
        handles["quote_analysis"] = fixture["quote_analysis"]
    store = JobStore()
    job_id = store.create_job({"status": "running"})
    store.transition_terminal(
        job_id,
        to="completed",
        fields={
            "config": {
                "mode": "online",
                "quote_id": "quote_id",
                "constraints": {"volume": {"min": 0.5}},
            },
            "scenario_grid": fixture["scenario_grid"],
            "artifact_handles": handles,
            "frontier_generation": 0,
            "result": {"mode": "online"},
        },
    )
    estimates: list[int] = []
    real_estimate = _ram_estimate.estimate_choice_query_peak_bytes

    def recording(**kwargs: Any) -> int:
        estimates.append(real_estimate(**kwargs))
        return estimates[-1]

    import haute.routes._optimiser_outcomes as outcomes

    outcomes.estimate_choice_query_peak_bytes = recording  # type: ignore[assignment]
    service = ChoiceQueryService(store, OptimiserFrontierService(store))
    before = _rss_bytes()
    started = time.perf_counter()
    result = service.choice_query(job_id, ChoiceTarget(None), _reducer(reducer_name, quotes))
    elapsed = time.perf_counter() - started
    peak = _peak_rss_bytes()
    return {
        "rss_before_bytes": before,
        "peak_rss_bytes": peak,
        "peak_over_start_bytes": peak - before,
        "estimate_bytes": estimates[0],
        "elapsed_seconds": round(elapsed, 3),
        "result_rows": result.rows.height,
        "total": result.total,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotes", type=int, nargs="+", default=[1_000_000, 5_000_000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", default=str(HERE / "opt-v09b-choice-query-memory.json"))
    args = parser.parse_args()
    import polars as pl

    runs: list[dict[str, Any]] = []
    for quotes in args.quotes:
        work = Path(tempfile.mkdtemp(prefix="opt_v09b_bench_"))
        fixture = _write_fixture(quotes, work)
        fixture["quotes"] = quotes
        fixture_path = work / "fixture.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        try:
            for with_analysis in (False, True):
                for reducer in REDUCERS:
                    if reducer == "group_by" and not with_analysis:
                        continue
                    repeats = args.repeats if quotes <= 1_000_000 else 1
                    for repeat in range(repeats):
                        completed = subprocess.run(
                            [
                                sys.executable,
                                __file__,
                                "--child",
                                str(fixture_path),
                                reducer,
                                str(int(with_analysis)),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        measured = json.loads(completed.stdout.strip().splitlines()[-1])
                        runs.append(
                            {
                                "quotes": quotes,
                                "reducer": reducer,
                                "with_analysis": with_analysis,
                                "repeat": repeat,
                                **measured,
                            }
                        )
                        print(json.dumps(runs[-1]), flush=True)
        finally:
            shutil.rmtree(fixture["apply_result"]["directory"], ignore_errors=True)
            shutil.rmtree(fixture["quote_analysis"]["directory"], ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)
    Path(args.output).write_text(
        json.dumps(
            {
                "measured_on": time.strftime("%Y-%m-%d"),
                "polars": pl.__version__,
                "cpus": os.cpu_count(),
                "platform": platform.platform(),
                "runs": runs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        print(json.dumps(_measure(sys.argv[2], sys.argv[3], sys.argv[4] == "1")))
    else:
        main()
