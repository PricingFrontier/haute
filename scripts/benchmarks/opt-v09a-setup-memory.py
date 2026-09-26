"""OPT-V09A: peak memory of solve setup with and without the quote-analysis extraction.

A child process writes a synthetic scored data input (quote-contiguous, 10
scenario steps per quote, a String quote id, Float32 objective and one
constraint, an 8-level String ``region`` and an Int32 ``tier``), a separate
one-row-per-quote side-input frame of the same two columns, and the two solver
inputs the setup worker writes (without and with the analysis columns). Each
case then runs in a fresh process started directly from this one:

- ``server``: the server process's grid build from the worker's solver input
  (``server/plain``: no analysis columns, which is also the side-input path;
  ``server/data``: the data-input path's file, which carries them).
- ``worker``: the setup worker's steps: validate and project the data input,
  write the solver input (``worker/plain``), then reduce the analysis table
  from it (``worker/data``) or from the side-input frame (``worker/side``).
- ``thread``: the thread compatibility mode, which runs the worker's write,
  the grid build and then the extraction in one process, the grid held
  resident while the table is reduced.

Each case records the process's RSS after its imports (``rss_start_bytes``)
and, for each step, the RSS before it and the step's own peak: ``VmHWM`` is
reset to the current RSS (``/proc/self/clear_refs``) before the step and read
after it. The whole process's peak is the highest of those. ``VmHWM``, not
``ru_maxrss``: Linux carries ``ru_maxrss`` over ``fork`` and ``exec``, so a
child can report its parent's peak instead of its own; each case records the
``ru_maxrss`` it inherited for comparison.

Usage: ``uv run python scripts/benchmarks/opt-v09a-setup-memory.py
[--quotes 1000000 5000000] [--repeats 3]`` writes
``opt-v09a-setup-memory.json`` beside this script. Sizes above 1M quotes run
once.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STEPS = 10
REGIONS = ("North", "South", "East", "West", "Midlands", "Wales", "Scotland", "Île-de-France")
ANALYSIS_COLUMNS = ("region", "tier")
CONFIG: dict[str, Any] = {
    "objective": "expected_income",
    "constraints": {"volume": {"min": 0.9}},
    "quote_id": "quote_id",
    "scenario_index": "scenario_index",
    "scenario_value": "scenario_value",
}
CASES = (
    "server/plain",
    "server/data",
    "worker/plain",
    "worker/data",
    "worker/side",
    "thread/plain",
    "thread/data",
    "thread/side",
)


def _rss_bytes() -> int:
    with open("/proc/self/statm", encoding="ascii") as statm:
        return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def _peak_rss_bytes() -> int:
    """This process's own high-water RSS (``VmHWM``)."""
    with open("/proc/self/status", encoding="ascii") as status:
        for line in status:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("/proc/self/status has no VmHWM line")


def _reset_peak_rss() -> None:
    """Reset ``VmHWM`` to the current RSS, so the next reading is one step's own peak."""
    with open("/proc/self/clear_refs", "w", encoding="ascii") as clear_refs:
        clear_refs.write("5")


def _project(directory: Path, analysis_columns: tuple[str, ...]) -> Any:
    """The setup worker's validation (an eager pass) and projection of the data input."""
    import polars as pl

    from haute.routes._optimiser_input import validate_and_project

    _, projected = validate_and_project(
        pl.scan_parquet(directory / "source.parquet"),
        dict(CONFIG),
        analysis_columns=analysis_columns,
        execution_context=None,
    )
    return projected


def _write_solver_input(projected: Any, output: Path) -> None:
    """The setup worker's write of the projected solver input."""
    from haute.routes._optimiser_input import write_solver_input

    write_solver_input(
        projected, str(output), "optimiser", execution_context=None, allow_borrow=False
    )


def _side_input_frame(directory: Path) -> Any:
    """The side-input frame as setup resolves it from a connected node's output."""
    import polars as pl

    from haute._types import GraphEdge
    from haute.routes._optimiser_input import AnalysisPlan, resolve_analysis_frame

    plan = AnalysisPlan(
        columns=ANALYSIS_COLUMNS,
        path="side_input",
        source_node_id="side",
        edge=GraphEdge(id="side-optimiser", source="side", target="optimiser"),
    )
    return resolve_analysis_frame(
        {"side": pl.scan_parquet(directory / "side_input.parquet")},
        {**CONFIG, "analysis_input": "side"},
        plan,
    )


def _generate(quotes: int, directory: Path) -> dict[str, Any]:
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(3)
    per_quote = pl.DataFrame(
        {
            "q": np.arange(quotes, dtype=np.int64),
            "base_income": rng.uniform(100, 1000, quotes).astype(np.float32),
            "base_volume": rng.uniform(0.5, 1.5, quotes).astype(np.float32),
            "region": pl.Series(REGIONS).gather(np.arange(quotes) % len(REGIONS)),
            "tier": (np.arange(quotes) % 5).astype(np.int32),
        }
    ).with_columns(
        pl.concat_str(pl.lit("q"), pl.col("q").cast(pl.String).str.zfill(9)).alias("quote_id")
    )
    grid = np.linspace(0.8, 1.25, STEPS).astype(np.float32)
    long = pl.DataFrame(
        {
            "q": np.repeat(np.arange(quotes, dtype=np.int64), STEPS),
            "scenario_index": np.tile(np.arange(STEPS, dtype=np.int32), quotes),
            "scenario_value": np.tile(grid, quotes),
        }
    )
    # Quote-contiguous rows in step order, as the grid builder requires.
    long.lazy().join(per_quote.lazy(), on="q", how="left", maintain_order="left").select(
        "quote_id",
        "scenario_index",
        "scenario_value",
        (pl.col("base_income") * pl.col("scenario_value")).alias("expected_income"),
        (pl.col("base_volume") * (2.0 - pl.col("scenario_value"))).alias("volume"),
        "region",
        "tier",
    ).sink_parquet(directory / "source.parquet")
    per_quote.select("quote_id", *ANALYSIS_COLUMNS).write_parquet(directory / "side_input.parquet")
    del long, per_quote
    _write_solver_input(_project(directory, ()), directory / "solver_input_plain.parquet")
    _write_solver_input(
        _project(directory, ANALYSIS_COLUMNS), directory / "solver_input_data.parquet"
    )
    return {
        "quotes": quotes,
        "rows": quotes * STEPS,
        "file_bytes": {
            name: (directory / name).stat().st_size
            for name in (
                "source.parquet",
                "side_input.parquet",
                "solver_input_plain.parquet",
                "solver_input_data.parquet",
            )
        },
    }


def _measure(case: str, directory: Path) -> dict[str, Any]:
    """Run one case's steps in this (fresh) process and report its memory."""
    # Before any import: what ru_maxrss already holds is what exec carried over.
    inherited_ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    from haute.routes import _optimiser_artifacts
    from haute.routes._optimiser_input import (
        build_quote_grid,
        grid_chunk_decision,
        price_contour,
    )
    from haute.routes._optimiser_outcomes import (
        require_one_row_per_solved_quote,
        write_quote_analysis,
    )

    price_contour()  # the native library's import counts toward the start, as in a server
    role, path = case.split("/")
    steps: list[dict[str, Any]] = []
    handles: list[dict[str, Any]] = []
    resident: list[Any] = []

    def step(name: str, action: Any) -> Any:
        """Run one step and record its own peak: ``VmHWM`` is reset to the RSS before it."""
        before = _rss_bytes()
        _reset_peak_rss()
        started = time.perf_counter()
        result = action()
        seconds = time.perf_counter() - started
        peak = _peak_rss_bytes()
        steps.append(
            {
                "step": name,
                "seconds": round(seconds, 2),
                "rss_before_bytes": before,
                "peak_rss_bytes": peak,
                "growth_bytes": peak - before,
            }
        )
        return result

    def grid_from(input_path: Path) -> Any:
        decision = grid_chunk_decision(CONFIG, str(input_path))
        return build_quote_grid(
            str(input_path), ["volume"], CONFIG, decision.chunk_size, execution_context=None
        )

    def extract(input_path: Path, directory_for_table: Path | None) -> dict[str, Any]:
        handle = write_quote_analysis(
            solver_input_path=str(input_path),
            analysis_frame=_side_input_frame(directory) if path == "side" else None,
            quote_id="quote_id",
            columns=ANALYSIS_COLUMNS,
            directory=directory_for_table,
            execution_context=None,
        )
        handles.append(handle)
        return handle

    start_peak = _peak_rss_bytes()
    start = _rss_bytes()
    if role == "server":
        source = directory / f"solver_input_{path}.parquet"
        resident.append(step("grid", lambda: grid_from(source)))
    else:
        written = directory / f"{role}_{path}_{os.getpid()}.parquet"
        columns = ANALYSIS_COLUMNS if path == "data" else ()
        projected = step("validate_and_project", lambda: _project(directory, columns))
        step("write_solver_input", lambda: _write_solver_input(projected, written))
        del projected
        if role == "thread":
            resident.append(step("grid", lambda: grid_from(written)))
        if path != "plain":
            table_dir = None
            if role == "worker":
                # The parent creates, under the artifact root, the directory the worker fills.
                table_dir = _optimiser_artifacts._new_quote_analysis_directory()
            handle = step("extract", lambda: extract(written, table_dir))
            if role == "thread":
                require_one_row_per_solved_quote(handle, resident[0].n_quotes)
        written.unlink()
    # The whole process's peak: the highest of the imports' and every step's.
    peak = max(start_peak, *(entry["peak_rss_bytes"] for entry in steps))
    measured = {
        "rss_start_bytes": start,
        "peak_rss_bytes": peak,
        "growth_bytes": peak - start,
        "inherited_ru_maxrss_bytes": inherited_ru_maxrss,
        "steps": steps,
        "n_quotes": resident[0].n_quotes if resident else None,
        "table_rows": handles[0]["row_count"] if handles else None,
    }
    for handle in handles:
        _optimiser_artifacts._cleanup_quote_analysis_artifact(handle)
    return measured


def _child(*arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, __file__, "--child", *arguments],
        check=True,
        stdout=subprocess.PIPE,  # the child's stderr passes through, so a failure shows why
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotes", type=int, nargs="+", default=[1_000_000, 5_000_000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--output", default=str(HERE / "opt-v09a-setup-memory.json"))
    args = parser.parse_args()
    import polars as pl
    import price_contour

    fixtures: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for quotes in args.quotes:
        work = Path(tempfile.mkdtemp(prefix="opt_v09a_bench_"))
        try:
            fixture = _child("generate", str(quotes), str(work))
            fixtures.append(fixture)
            print(json.dumps(fixture), flush=True)
            repeats = args.repeats if quotes <= 1_000_000 else 1
            # Cases interleaved by repeat, so a drift in the machine's state
            # spreads over every case rather than landing on one.
            for repeat in range(repeats):
                for case in args.cases:
                    measured = _child("measure", case, str(work))
                    runs.append({"quotes": quotes, "case": case, "repeat": repeat, **measured})
                    print(json.dumps(runs[-1]), flush=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    Path(args.output).write_text(
        json.dumps(
            {
                "measured_on": time.strftime("%Y-%m-%d"),
                "polars": pl.__version__,
                "price_contour": price_contour.__version__,
                "cpus": os.cpu_count(),
                "platform": platform.platform(),
                "fixtures": fixtures,
                "runs": runs,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        action, argument, target = sys.argv[2:5]
        if action == "generate":
            print(json.dumps(_generate(int(argument), Path(target))))
        else:
            print(json.dumps(_measure(argument, Path(target))))
    else:
        main()
