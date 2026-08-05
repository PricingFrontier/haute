"""Solve a synthetic ratebook and apply a versioned factor-table artifact."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import (
    apply_optimiser_apply_from_config,
    resolve_data_input_from_config,
)

pipeline = haute.Pipeline(
    "ratebook_optimisation_apply",
    description="Synthetic ratebook solve and versioned apply fixture.",
)


@pipeline.data_input(config="config/scored.json")
def scored() -> pl.LazyFrame:
    return resolve_data_input_from_config(
        "config/scored.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.data_input(config="config/factors.json")
def factors() -> pl.LazyFrame:
    return resolve_data_input_from_config(
        "config/factors.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.optimiser(config="config/optimiser.json")
def optimise(scored: pl.LazyFrame, factors: pl.LazyFrame) -> pl.LazyFrame:
    return scored


@pipeline.optimiser_apply(config="config/apply.json")
def applied(factors: pl.LazyFrame) -> pl.LazyFrame:
    return apply_optimiser_apply_from_config(
        factors,
        config="config/apply.json",
        base_dir=Path(__file__).parent,
        source_names=["factors"],
        source_ids=["factors"],
    )


@pipeline.output(config="config/output.json")
def response(applied: pl.LazyFrame) -> pl.LazyFrame:
    return applied
