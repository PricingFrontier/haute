"""Expand quote scenarios, score each alternative, and optimise the portfolio online."""

import polars as pl

import haute

pipeline = haute.Pipeline(
    "online_scenario_optimisation",
    description="Synthetic online price-scenario optimisation fixture.",
)


@pipeline.data_input(config="config/quotes.json")
def quotes(): ...


@pipeline.scenario_expander(config="config/scenarios.json")
def scenarios(quotes): ...


@pipeline.polars
def scored(scenarios: pl.LazyFrame) -> pl.LazyFrame:
    return scenarios.with_columns(
        (pl.col("base_income") * pl.col("scenario_value"))
        .cast(pl.Float32)
        .alias("expected_income"),
        (pl.col("base_volume") * (2.0 - pl.col("scenario_value"))).cast(pl.Float32).alias("volume"),
    )


@pipeline.optimiser(config="config/optimiser.json")
def optimise(scored): ...


@pipeline.output(config="config/output.json")
def response(optimise): ...
