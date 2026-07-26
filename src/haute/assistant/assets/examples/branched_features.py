"""A branched feature pipeline with an explicit merge into one output.

The two feature branches start from the same live input.  Explicit connection
statements make the topology unambiguous even when a future edit changes a
function parameter name.
"""

import polars as pl

import haute

pipeline = haute.Pipeline(
    "branched_features",
    description="Parallel feature branches merged before the response.",
)


@pipeline.api_input(config="config/quote_input/quote.json")
def quote() -> pl.LazyFrame | dict[str, pl.LazyFrame]:
    """Represent the live quote request source."""

    from pathlib import Path

    from haute.graph_utils import resolve_api_input_from_config

    return resolve_api_input_from_config(
        "config/quote_input/quote.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.polars
def vehicle_features(quote: pl.LazyFrame) -> pl.LazyFrame:
    """Derive vehicle features from the live quote."""

    return quote.with_columns(vehicle_age=pl.col("vehicle_year"))


@pipeline.polars
def customer_features(quote: pl.LazyFrame) -> pl.LazyFrame:
    """Derive customer features from the live quote."""

    return quote.with_columns(customer_tenure=pl.col("years_with_insurer"))


@pipeline.polars
def combined(vehicle_features: pl.LazyFrame, customer_features: pl.LazyFrame) -> pl.LazyFrame:
    """Join the two feature branches on their shared quote id."""

    return vehicle_features.join(customer_features, on="quote_id", suffix="_customer")


@pipeline.output(config="config/quote_response/response.json")
def response(combined: pl.LazyFrame) -> pl.LazyFrame:
    """Return the combined features for the response."""

    return combined


pipeline.connect("quote", "vehicle_features", source_port="quote")
pipeline.connect("quote", "customer_features", source_port="quote")
pipeline.connect("vehicle_features", "combined")
pipeline.connect("customer_features", "combined")
pipeline.connect("combined", "response")
