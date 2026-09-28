"""Pipeline: reference"""

import haute
import polars as pl

pipeline = haute.Pipeline(
    "reference", description="Runnable reference pipeline over synthetic quotes."
)


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes():
    """Read the synthetic quote rows."""


@pipeline.polars
def features(quotes: pl.LazyFrame) -> pl.LazyFrame:
    """Derive the rating features."""
    df = quotes.with_columns(
        vehicle_age=2026 - pl.col("vehicle_year"),
        driver_band=pl.col("driver_age").cut([25, 40, 65]).cast(pl.String),
    )
    return df


@pipeline.output(config="config/quote_response/priced.json")
def priced(features):
    """Return the priced quote rows."""


# Wire nodes together - edges define data flow
pipeline.connect("quotes", "features")
pipeline.connect("features", "priced")
