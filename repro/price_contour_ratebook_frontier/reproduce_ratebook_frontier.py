"""Standalone price-contour ratebook frontier repro.

Copy this folder into the price-contour repository and run from the copied
folder:

    python reproduce_ratebook_frontier.py --frontier-steps 15

The script depends only on polars and price_contour. It does not import Haute.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import polars as pl
from price_contour import RatebookOptimiser, build_grid_from_parquet_chunked


HERE = Path(__file__).resolve().parent
METADATA_PATH = HERE / "metadata.json"


def timed(name: str, fn, **extra: Any) -> Any:
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    print(json.dumps({"phase": name, "seconds": round(elapsed, 3), **extra}), flush=True)
    return result


def load_metadata() -> dict[str, Any]:
    return json.loads(METADATA_PATH.read_text(encoding="utf-8"))


def build_grid(metadata: dict[str, Any], chunk_size: int | None):
    config = metadata["config"]
    scored_path = HERE / metadata["scored_parquet"]
    return build_grid_from_parquet_chunked(
        str(scored_path),
        config["constraint_cols"],
        chunk_size or int(config["chunk_size"]),
        quote_id=config["quote_id"],
        scenario_index=config["scenario_index"],
        scenario_value=config["scenario_value"],
        objective=config["objective"],
    )


def align_factors(metadata: dict[str, Any], quote_ids: list[str]) -> pl.DataFrame:
    config = metadata["config"]
    factor_path = HERE / metadata["factors_parquet"]
    factor_columns = config["factor_columns"]
    factor_cols_flat = list(dict.fromkeys(col for group in factor_columns for col in group))

    factors_df = (
        pl.read_parquet(factor_path)
        .select([config["quote_id"], *factor_cols_flat])
        .with_columns(pl.col(config["quote_id"]).cast(pl.Utf8).alias("quote_id"))
        .unique(subset=["quote_id"])
    )
    quote_order = pl.DataFrame({"quote_id": quote_ids}).unique(maintain_order=True)
    aligned = quote_order.join(factors_df, on="quote_id", how="left").drop("quote_id")

    null_counts = aligned.select(
        [pl.col(col).null_count().alias(col) for col in factor_cols_flat],
    ).row(0, named=True)
    bad = {name: count for name, count in null_counts.items() if count}
    if bad:
        raise ValueError(f"Factor columns contain nulls after alignment: {bad}")
    return aligned


def make_solver(metadata: dict[str, Any]) -> RatebookOptimiser:
    config = metadata["config"]
    return RatebookOptimiser(
        objective=config["objective"],
        constraints=config["constraints"],
        factor_columns=config["factor_columns"],
        max_iter=int(config["max_iter"]),
        max_cd_iterations=int(config["max_cd_iterations"]),
        cd_tolerance=float(config["cd_tolerance"]),
        tolerance=float(config["tolerance"]),
    )


def run_frontier(
    solver: RatebookOptimiser,
    grid,
    factors_df: pl.DataFrame,
    metadata: dict[str, Any],
    *,
    frontier_steps: int,
    initial_lambdas: dict[str, float] | None,
):
    config = metadata["config"]
    ranges = {
        name: (float(bounds["min"]), float(bounds["max"]))
        for name, bounds in config["frontier_ranges"].items()
    }
    return solver.frontier(
        grid,
        factors_df,
        threshold_ranges=ranges,
        n_points_per_dim=frontier_steps,
        factor_columns=config["factor_columns"],
        initial_lambdas=initial_lambdas,
    )


def point_summary(points_df: pl.DataFrame) -> dict[str, Any]:
    rows = points_df.to_dicts()
    return {
        "points": len(rows),
        "iterations": [row.get("iterations") for row in rows],
        "converged": [row.get("converged") for row in rows],
        "threshold_conversion_prediction": [
            round(float(row.get("threshold_conversion_prediction", 0.0)), 3)
            for row in rows
        ],
        "total_conversion_prediction": [
            round(float(row.get("total_conversion_prediction", 0.0)), 3)
            for row in rows
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontier-steps", type=int, default=15)
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument(
        "--scale",
        action="store_true",
        help="Run frontier_steps 1, 2, 3, 5, 8, and the requested value.",
    )
    args = parser.parse_args()

    metadata = load_metadata()
    print(json.dumps({"metadata": metadata}, default=str), flush=True)

    grid = timed(
        "build_grid_from_parquet_chunked",
        lambda: build_grid(metadata, args.chunk_size),
        chunk_size=args.chunk_size or metadata["config"]["chunk_size"],
    )
    print(json.dumps({"grid": {"n_quotes": grid.n_quotes, "n_steps": grid.n_steps}}), flush=True)

    factors_df = timed(
        "align_factors",
        lambda: align_factors(metadata, grid.quote_ids),
    )
    print(json.dumps({"factors": {"shape": factors_df.shape}}), flush=True)

    solver = timed("ratebook_solver_init", lambda: make_solver(metadata))
    base = timed("ratebook_base_solve", lambda: solver.solve(grid, factors_df))
    print(
        json.dumps(
            {
                "base": {
                    "total_objective": base.total_objective,
                    "total_constraints": base.total_constraints,
                    "lambdas": base.lambdas,
                    "converged": base.converged,
                    "cd_iterations": getattr(base, "cd_iterations", None),
                }
            }
        ),
        flush=True,
    )

    step_values = [args.frontier_steps]
    if args.scale:
        step_values = sorted({1, 2, 3, 5, 8, args.frontier_steps})

    for steps in step_values:
        frontier = timed(
            "ratebook_frontier",
            lambda steps=steps: run_frontier(
                solver,
                grid,
                factors_df,
                metadata,
                frontier_steps=steps,
                initial_lambdas=base.lambdas,
            ),
            frontier_steps=steps,
        )
        print(json.dumps({"frontier": {"steps": steps, **point_summary(frontier.points)}}), flush=True)


if __name__ == "__main__":
    main()
