"""Expose a minimal live scorer with reproducible deployment configuration."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import resolve_api_input_from_config

pipeline = haute.Pipeline(
    "deployment_safety",
    description="Synthetic deployment-preflight and fail-closed safety fixture.",
)


@pipeline.api_input(config="config/request.json")
def quote() -> pl.LazyFrame | dict[str, pl.LazyFrame]:
    return resolve_api_input_from_config(
        "config/request.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.output(config="config/output.json")
def response(quote: pl.LazyFrame) -> pl.LazyFrame:
    return quote


pipeline.connect("quote", "response", source_port="quote")
