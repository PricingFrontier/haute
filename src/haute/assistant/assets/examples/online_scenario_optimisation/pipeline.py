"""Expand quote scenarios, score each alternative, and optimise the portfolio online."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import (
    expand_scenarios_from_config,
    resolve_data_input_from_config,
)

pipeline = haute.Pipeline(
    "online_scenario_optimisation",
    description="Synthetic online price-scenario optimisation fixture.",
)


@pipeline.data_input(config="config/quotes.json")
def quotes() -> pl.LazyFrame:
    return resolve_data_input_from_config(
        "config/quotes.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.scenario_expander(config="config/scenarios.json")
def scenarios(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return expand_scenarios_from_config(
        quotes,
        "config/scenarios.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.polars
def scored(scenarios: pl.LazyFrame) -> pl.LazyFrame:
    return scenarios.with_columns(
        (pl.col("base_income") * pl.col("scenario_value"))
        .cast(pl.Float32)
        .alias("expected_income"),
        (pl.col("base_volume") * (2.0 - pl.col("scenario_value"))).cast(pl.Float32).alias("volume"),
    )


@pipeline.optimiser(config="config/optimiser.json")
def optimise(scored: pl.LazyFrame) -> pl.LazyFrame:
    return scored


@pipeline.output(config="config/output.json")
def response(optimise: pl.LazyFrame) -> pl.LazyFrame:
    return optimise
