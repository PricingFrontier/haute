"""Pipeline: motor_pricing"""

import haute
import polars as pl

pipeline = haute.Pipeline(
    "motor_pricing",
    description="Motor quotes priced from a quote request or a batch, with bands and a rating.",
)


@pipeline.api_input(config="config/quote_input/quote_input.json")
def quote_input(): ...


@pipeline.data_input(config="config/data_input/batch_quotes.json")
def batch_quotes(): ...


@pipeline.live_switch(config="config/source_switch/policies.json")
def policies(quotes, batch_quotes): ...


@pipeline.polars
def rating_features(policies: pl.LazyFrame) -> pl.LazyFrame:
    df = policies
    # Derive the driver's age at inception and the vehicle value in thousands
    df = df.with_columns(
        driver_age=((pl.col('inception_date') - pl.col('date_of_birth')).dt.total_days() // 365).cast(pl.Int64),
        vehicle_value_k=(pl.col('vehicle_value') / 1000).round(1),
    )
    return df


@pipeline.model_score(
    config="config/model_scoring/claim_frequency.json",
    contract={
        "inputs": ["driver_age", "vehicle_group", "vehicle_value"],
        "outputs": ["expected_frequency"],
    },
)
def claim_frequency(rating_features): ...


@pipeline.banding(config="config/banding/rating_bands.json")
def rating_bands(claim_frequency): ...


@pipeline.rating_step(config="config/rating_step/base_premium.json")
def base_premium(df: pl.LazyFrame) -> pl.LazyFrame:
    # Round the premium to pence
    df = df.with_columns(pl.col('premium').round(2))
    return df


@pipeline.output(config="config/quote_response/quote_response.json")
def quote_response(base_premium): ...


@pipeline.explore(config="config/explore/premium_explore.json")
def premium_explore(base_premium): ...


@pipeline.data_output(config="config/data_output/priced_batch.json")
def priced_batch(base_premium): ...


pipeline.connect("quote_input", "policies", source_port="quotes")
pipeline.connect("rating_bands", "base_premium")
