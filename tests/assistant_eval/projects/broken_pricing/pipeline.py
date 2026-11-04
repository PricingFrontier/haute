"""Pipeline: broken_pricing"""

import haute
import polars as pl

pipeline = haute.Pipeline(
    "broken_pricing", description="Quotes with vehicle features and bands."
)


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes(): ...


@pipeline.polars
def rating_features(quotes: pl.LazyFrame) -> pl.LazyFrame:
    df = quotes
    # Derive the vehicle's age from its year of manufacture
    df = df.with_columns(vehicle_age=2025 - pl.col('vehicle_year'))
    return df


@pipeline.banding(config="config/banding/vehicle_bands.json")
def vehicle_bands(rating_features): ...
