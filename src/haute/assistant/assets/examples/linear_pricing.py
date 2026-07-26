"""A compact linear pricing pipeline: source, feature enrichment, and output.

This example shows the preferred implicit wiring convention.  Each transform
accepts the exact name of its upstream frame, so the parser can recover the
edge without a separate list of connection statements.
"""

import polars as pl

import haute

pipeline = haute.Pipeline(
    "linear_pricing",
    description="A linear source-to-output pricing flow.",
)


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes() -> pl.LazyFrame:
    """Read the quote rows used by the rating flow."""

    from pathlib import Path

    from haute.graph_utils import resolve_data_input_from_config

    return resolve_data_input_from_config(
        "config/data_input/quotes.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.polars
def enriched(quotes: pl.LazyFrame) -> pl.LazyFrame:
    """Add a transparent feature for downstream pricing steps."""

    return quotes.with_columns(
        vehicle_age=pl.col("vehicle_year").cast(pl.Int64),
        driver_band=pl.col("driver_age").cut([25, 40, 65]),
    )


@pipeline.output(config="config/quote_response/linear_priced.json")
def priced(enriched: pl.LazyFrame) -> pl.LazyFrame:
    """Expose the enriched quote rows as the pipeline output."""

    return enriched
