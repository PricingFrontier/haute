"""Pipeline: heldout_pricing"""

import haute
import polars as pl

pipeline = haute.Pipeline("heldout_pricing")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    df = pl.scan_csv("data/quotes.csv")
    return df
