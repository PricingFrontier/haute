"""Pipeline: stepped_pricing"""

import haute
import polars as pl

pipeline = haute.Pipeline(
    "stepped_pricing",
    description="A quotes source feeding a Transform authored as steps.",
)


@pipeline.polars
def quotes() -> pl.LazyFrame:
    df = pl.scan_csv('data/quotes.csv')
    return df


@pipeline.polars(config="config/polars/risk_features.json")
def risk_features(quotes: pl.LazyFrame) -> pl.LazyFrame:
    df = quotes
    # Flag experienced drivers
    df = df.with_columns((pl.col('driver_age') >= 25).alias('experienced_driver'))
    return df


# Wire nodes together - edges define data flow
pipeline.connect("quotes", "risk_features")
