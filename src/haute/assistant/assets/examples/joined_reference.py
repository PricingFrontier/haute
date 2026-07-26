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


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes() -> pl.LazyFrame:
    """Read the quote rows."""

    from pathlib import Path

    from haute.graph_utils import resolve_data_input_from_config

    return resolve_data_input_from_config(
        "config/data_input/quotes.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.data_input(config="config/data_input/regions.json")
def regions() -> pl.LazyFrame:
    """Read one factor row per rating region."""

    from pathlib import Path

    from haute.graph_utils import resolve_data_input_from_config

    return resolve_data_input_from_config(
        "config/data_input/regions.json",
        base_dir=Path(__file__).parent,
    )


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


@pipeline.output(config="config/quote_response/joined_priced.json")
def priced(quote_with_region: pl.LazyFrame) -> pl.LazyFrame:
    """Expose the enriched quote rows."""

    return quote_with_region


pipeline.connect("quotes", "quote_with_region")
pipeline.connect("regions", "quote_with_region")
pipeline.connect("quote_with_region", "priced")
