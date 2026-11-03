"""OPT-W02: the solver session's memory over one ``build_and_solve``, online and ratebook.

A child process writes a synthetic solver input as the setup worker writes it
(quote-contiguous, a String quote id, a Float32 objective and Float32
constraints) and, for ratebook mode, the per-quote banding artifact (an
8-level String ``region``, a 5-level Int32 ``tier`` and further 10-level Int32
factors). The default shape is 11 scenario steps, one constraint, a 10-byte
quote id and two factors; ``--steps``, ``--constraints``, ``--qid-bytes`` and
``--factors`` vary it. The first constraint, ``volume``, is swept; any others
are unswept ``max`` bounds the solve never reaches, so with more than one the
frontier is orchestrated in Python, one ``solve()`` per point.
Each case then runs in a fresh process started directly from this one, which
does what a solver session's child does:

- imports the session's preload module and starts the Polars thread pools (the
  ``RLIMIT_AS`` backend starts them before it installs a cap, so they are part
  of the charge the cap is installed over);
- runs ``build_and_solve`` itself, unchanged, with the solve's inline frontier
  over ``volume`` (15 points, the default): the grid build, the solve (and
  in ratebook mode the factor contexts), the adjustments report, the frontier,
  the summary and the as-solved apply artifact.

Each case records the process's RSS (``rss_start_bytes``) and address space
(``vm_start_bytes``) when the command starts, and each stage's own peak RSS:
``VmHWM`` is reset to the current RSS (``/proc/self/clear_refs``) at every
stage boundary ``build_and_solve`` reports, and read at the next. The command's
peak is the highest stage peak; its growth (``growth_bytes``) is that peak less
the RSS it started from, which is what the session's cap bounds (the cap is the
charge at install plus the grant). ``VmPeak`` cannot be reset, so the address
space is recorded for the whole command only (``vm_growth_bytes``); an
``RLIMIT_AS`` host allows the grant plus a fixed thread allowance on top of it.

Usage: ``uv run python scripts/benchmarks/opt-w02-solve-memory.py
[--quotes 1000000 5000000 10000000] [--repeats 2] [--steps 11] [--constraints 1]
[--qid-bytes 10] [--factors 2]`` writes ``opt-w02-solve-memory.json`` (or
``--output``). Sizes above 1M quotes run once. Set ``TMPDIR`` to a disk with
room for the fixture (about 2 GB at 10M quotes); the server's artifact roots
live under it too.
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
REGIONS = ("North", "South", "East", "West", "Midlands", "Wales", "Scotland", "Île-de-France")
MODES = ("online", "ratebook")


def _constraint_names(constraints: int) -> list[str]:
    return ["volume", *(f"exposure_{k}" for k in range(1, constraints))]


def _factor_names(factors: int) -> list[str]:
    return ["region", "tier", *(f"factor_{k}" for k in range(3, factors + 1))][:factors]


def _config(mode: str, fixture: dict[str, Any]) -> dict[str, Any]:
    low, high = fixture["volume_range"]
    constraints: dict[str, Any] = {"volume": {"min": low}}
    for name, total in fixture["unswept_baselines"].items():
        constraints[name] = {"max": 2.0 * total}
    config: dict[str, Any] = {
        "mode": mode,
        "objective": "expected_income",
        # A swept constraint is solved at its range's start (anchor_swept_constraints).
        "constraints": constraints,
        "frontier_ranges": {"volume": {"min": low, "max": high}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }
    if mode == "ratebook":
        config["factor_columns"] = [[name] for name in _factor_names(fixture["factors"])]
    return config


def _status_bytes(field: str) -> int:
    with open("/proc/self/status", encoding="ascii") as status:
        for line in status:
            if line.startswith(f"{field}:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError(f"/proc/self/status has no {field} line")


def _rss_bytes() -> int:
    with open("/proc/self/statm", encoding="ascii") as statm:
        return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def _reset_peak_rss() -> None:
    """Reset ``VmHWM`` to the current RSS, so the next reading is one stage's own peak."""
    with open("/proc/self/clear_refs", "w", encoding="ascii") as clear_refs:
        clear_refs.write("5")


def _generate(quotes: int, shape: dict[str, int], directory: Path) -> dict[str, Any]:
    import numpy as np
    import polars as pl

    from haute.routes._optimiser_artifacts import (
        _new_ratebook_factors_directory,
        _persist_ratebook_factors_lazy_artifact,
    )
    from haute.routes._optimiser_input import validate_and_project, write_solver_input

    steps = shape["steps"]
    constraint_names = _constraint_names(shape["constraints"])
    factor_names = _factor_names(shape["factors"])
    rng = np.random.default_rng(7)
    per_quote = pl.DataFrame(
        {
            "q": np.arange(quotes, dtype=np.int64),
            "base_income": rng.uniform(100, 1000, quotes).astype(np.float32),
            "base_volume": rng.uniform(0.5, 1.5, quotes).astype(np.float32),
            "elasticity": rng.uniform(2.0, 6.0, quotes).astype(np.float32),
            "region": pl.Series(REGIONS).gather(np.arange(quotes) % len(REGIONS)),
            "tier": (np.arange(quotes) % 5).astype(np.int32),
            **{
                name: ((np.arange(quotes) // (k + 1)) % 10).astype(np.int32)
                for k, name in enumerate(factor_names[2:])
            },
        }
    ).with_columns(
        pl.concat_str(
            pl.lit("q"), pl.col("q").cast(pl.String).str.zfill(shape["qid_bytes"] - 1)
        ).alias("quote_id")
    )
    grid = np.linspace(0.75, 1.25, steps).astype(np.float32)
    long = pl.DataFrame(
        {
            "q": np.repeat(np.arange(quotes, dtype=np.int64), steps),
            "scenario_index": np.tile(np.arange(steps, dtype=np.int32), quotes),
            "scenario_value": np.tile(grid, quotes),
        }
    )
    conversion = (-pl.col("elasticity") * (pl.col("scenario_value") - 1.0)).exp()
    # Quote-contiguous rows in step order, as the grid builder requires. The
    # income peaks inside the grid, so the volume constraint has work to do.
    long.lazy().join(per_quote.lazy(), on="q", how="left", maintain_order="left").select(
        "quote_id",
        "scenario_index",
        "scenario_value",
        (pl.col("base_income") * (pl.col("scenario_value") - 0.7) * conversion)
        .cast(pl.Float32)
        .alias("expected_income"),
        (pl.col("base_volume") * conversion).cast(pl.Float32).alias("volume"),
        *(
            (pl.col("base_income") * pl.col("scenario_value") * (k + 1))
            .cast(pl.Float32)
            .alias(name)
            for k, name in enumerate(constraint_names[1:])
        ),
    ).sink_parquet(directory / "source.parquet")
    del long
    projection_fixture = {
        "volume_range": [0.0, 0.0],
        "unswept_baselines": {name: 0.0 for name in constraint_names[1:]},
        "factors": shape["factors"],
    }
    _, projected = validate_and_project(
        pl.scan_parquet(directory / "source.parquet"),
        _config("online", projection_fixture),
        analysis_columns=(),
        execution_context=None,
    )
    write_solver_input(
        projected,
        str(directory / "solver_input.parquet"),
        "optimiser",
        execution_context=None,
        allow_borrow=False,
    )
    factors_handle = _persist_ratebook_factors_lazy_artifact(
        per_quote.lazy().select("quote_id", *factor_names),
        artifact_dir=_new_ratebook_factors_directory(),
    )
    del per_quote
    # The frontier sweeps volume from its value at the base price (the step
    # nearest 1.0, the lower on a tie, as price-contour's baseline) up 3%, so
    # every point needs a positive lambda.
    base_step = int(np.argmin(np.abs(grid - np.float32(1.0))))
    baselines = (
        pl.scan_parquet(directory / "solver_input.parquet")
        .filter(pl.col("scenario_index") == base_step)
        .select(pl.col(name).sum() for name in constraint_names)
        .collect()
        .row(0, named=True)
    )
    (directory / "source.parquet").unlink()
    return {
        "quotes": quotes,
        "rows": quotes * steps,
        **shape,
        "volume_range": [float(baselines["volume"]), float(baselines["volume"]) * 1.03],
        "unswept_baselines": {name: float(baselines[name]) for name in constraint_names[1:]},
        "ratebook_factors_handle": factors_handle,
        "solver_input_bytes": (directory / "solver_input.parquet").stat().st_size,
    }


def _measure(mode: str, directory: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Run one ``build_and_solve`` in this (fresh) process and report its memory."""
    import haute.routes._optimiser_session_worker as session_worker
    import haute.routes._optimiser_solver as optimiser_solver
    from haute._native_memory_limit import start_polars_thread_pools
    from haute.routes import _optimiser_artifacts
    from haute.routes._optimiser_input import price_contour

    price_contour()
    start_polars_thread_pools()
    stages: list[dict[str, Any]] = []
    current = {"stage": "start", "rss_before_bytes": _rss_bytes(), "started": time.perf_counter()}

    def close_stage() -> None:
        stages.append(
            {
                "stage": current["stage"],
                "seconds": round(time.perf_counter() - current["started"], 2),
                "rss_before_bytes": current["rss_before_bytes"],
                "peak_rss_bytes": _status_bytes("VmHWM"),
            }
        )

    def report_stage(label: str, fraction: float) -> None:
        if label == current["stage"]:
            return
        close_stage()
        current.update(stage=label, rss_before_bytes=_rss_bytes(), started=time.perf_counter())
        _reset_peak_rss()

    session_worker._report_stage = report_stage  # type: ignore[assignment]
    compute_frontier = optimiser_solver._compute_frontier

    def frontier_then_finish(*args: Any, **kwargs: Any) -> Any:
        result = compute_frontier(*args, **kwargs)
        # What follows the frontier: the summary and the as-solved apply artifact.
        report_stage("Finishing", 0.9)
        return result

    optimiser_solver._compute_frontier = frontier_then_finish  # type: ignore[assignment]
    apply_dir = _optimiser_artifacts._new_apply_artifact_directory()
    request = session_worker.SessionSolveRequest(
        session_id=f"bench-{os.getpid()}",
        node_id="optimiser",
        mode=mode,
        config=_config(mode, fixture),
        node_label="Optimiser",
        input_provenance={},
        setup_chunking={},
        input_path=str(directory / "solver_input.parquet"),
        constraint_cols=_constraint_names(fixture["constraints"]),
        ratebook_factors_handle=fixture["ratebook_factors_handle"] if mode == "ratebook" else None,
        quote_analysis_handle=None,
        factor_level_order={},
        apply_artifact_dir=str(apply_dir),
    )
    rss_start = _rss_bytes()
    vm_start = _status_bytes("VmSize")
    _reset_peak_rss()
    started = time.perf_counter()
    outcome = session_worker.build_and_solve(request)
    seconds = time.perf_counter() - started
    close_stage()
    vm_peak = _status_bytes("VmPeak")
    shutil.rmtree(apply_dir, ignore_errors=True)
    if outcome.failure is not None:
        raise RuntimeError(f"build_and_solve failed: {outcome.failure}")
    fields = outcome.completion_fields or {}
    result = fields.get("result") or {}
    frontier = result.get("frontier") or {}
    peak = max(entry["peak_rss_bytes"] for entry in stages)
    return {
        "seconds": round(seconds, 1),
        "rss_start_bytes": rss_start,
        "peak_rss_bytes": peak,
        "growth_bytes": peak - rss_start,
        "vm_start_bytes": vm_start,
        "vm_peak_bytes": vm_peak,
        "vm_growth_bytes": vm_peak - vm_start,
        "solve_memory_forecast_bytes": outcome.solve_memory_forecast_bytes,
        "converged": result.get("converged"),
        "frontier_points": frontier.get("n_points"),
        "frontier_error": result.get("frontier_error"),
        "stages": stages,
    }


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
    parser.add_argument("--quotes", type=int, nargs="+", default=[1_000_000, 5_000_000, 10_000_000])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--steps", type=int, default=11)
    parser.add_argument("--constraints", type=int, default=1)
    parser.add_argument("--qid-bytes", type=int, default=10)
    parser.add_argument("--factors", type=int, default=2)
    parser.add_argument("--output", default=str(HERE / "opt-w02-solve-memory.json"))
    args = parser.parse_args()
    import polars as pl
    import price_contour

    fixtures: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for quotes in args.quotes:
        work = Path(tempfile.mkdtemp(prefix="opt_w02_bench_"))
        try:
            shape = {
                "steps": args.steps,
                "constraints": args.constraints,
                "qid_bytes": args.qid_bytes,
                "factors": args.factors,
            }
            fixture = _child("generate", str(quotes), str(work), json.dumps(shape))
            fixtures.append(fixture)
            print(json.dumps(fixture), flush=True)
            fixture_path = work / "fixture.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            repeats = args.repeats if quotes <= 1_000_000 else 1
            for repeat in range(repeats):
                for mode in args.modes:
                    measured = _child("measure", mode, str(work))
                    runs.append({"quotes": quotes, "mode": mode, "repeat": repeat, **measured})
                    print(json.dumps(runs[-1]), flush=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            for fixture in fixtures:
                shutil.rmtree(fixture["ratebook_factors_handle"]["directory"], ignore_errors=True)
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
            print(json.dumps(_generate(int(argument), json.loads(sys.argv[5]), Path(target))))
        else:
            fixture = json.loads((Path(target) / "fixture.json").read_text(encoding="utf-8"))
            print(json.dumps(_measure(argument, Path(target), fixture)))
    else:
        main()
