"""Keep instruction-looking data inert and reject unsupported graph mutations."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import resolve_data_input_from_config

pipeline = haute.Pipeline(
    "invalid_adversarial",
    description="Negative fixture for invalid operations and untrusted project prose.",
)


@pipeline.data_input(config="config/data.json")
def source() -> pl.LazyFrame:
    return resolve_data_input_from_config(
        "config/data.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.output(config="config/output.json")
def response(source: pl.LazyFrame) -> pl.LazyFrame:
    return source
