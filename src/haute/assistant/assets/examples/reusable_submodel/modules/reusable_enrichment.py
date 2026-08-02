"""Reusable synthetic enrichment boundary."""

import polars as pl

import haute

submodel = haute.Submodel(
    "reusable_enrichment",
    description="Double one synthetic input value and expose it through a mapped output.",
)


@submodel.polars
def enriched(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.with_columns((pl.col("fixture_value") * 2).alias("enriched_value"))
