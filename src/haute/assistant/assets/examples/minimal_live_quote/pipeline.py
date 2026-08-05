"""A synthetic live quote request flowing directly to a response output."""

import polars as pl

import haute

pipeline = haute.Pipeline("minimal_live_quote", description="Synthetic mechanical live fixture.")


@pipeline.api_input(config="config/request.json")
def quote() -> pl.LazyFrame | dict[str, pl.LazyFrame]:
    from pathlib import Path

    from haute.graph_utils import resolve_api_input_from_config

    return resolve_api_input_from_config("config/request.json", base_dir=Path(__file__).parent)


@pipeline.output(config="config/output.json")
def response(quote: pl.LazyFrame) -> pl.LazyFrame:
    return quote


pipeline.connect("quote", "response", source_port="quote")
