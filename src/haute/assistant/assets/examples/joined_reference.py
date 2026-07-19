"""A reference-table enrichment using Haute's dedicated edge-join node.

This is the idiomatic shape for attaching a small lookup table to the quote
flow: keep the lookup as its own source and describe the join keys on the
specialised node rather than hiding the operation in a generic transform.
"""

import polars as pl

import haute

pipeline = haute.Pipeline(
    "joined_reference",
    description="Quote enrichment from a regional reference table.",
)


@pipeline.polars
def quotes() -> pl.LazyFrame:
    """Read the quote rows."""

    return pl.scan_parquet("data/quotes.parquet")


@pipeline.polars
def regions() -> pl.LazyFrame:
    """Read one factor row per rating region."""

    return pl.scan_parquet("data/regions.parquet")


@pipeline.edge_join(
    base_input="quotes",
    join_input="regions",
    how="left",
    left_on=["region"],
    right_on=["region"],
)
def quote_with_region(quotes: pl.LazyFrame, regions: pl.LazyFrame) -> pl.LazyFrame:
    """Attach the regional factors to each quote."""

    return quotes.join(regions, on="region", how="left")


@pipeline.polars
def priced(quote_with_region: pl.LazyFrame) -> pl.LazyFrame:
    """Expose the enriched quote rows."""

    return quote_with_region


pipeline.connect("quotes", "quote_with_region")
pipeline.connect("regions", "quote_with_region")
pipeline.connect("quote_with_region", "priced")
