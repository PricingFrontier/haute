"""Derive one transparent value and retain a deterministic row-trace fixture."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import resolve_data_input_from_config

pipeline = haute.Pipeline(
    "trace_audit",
    description="Synthetic transform with explicit row-trace and dry-run evidence.",
)


@pipeline.data_input(config="config/data.json")
def source() -> pl.LazyFrame:
    return resolve_data_input_from_config("config/data.json", base_dir=Path(__file__).parent)


@pipeline.polars
def derived(source: pl.LazyFrame) -> pl.LazyFrame:
    return source.with_columns((pl.col("fixture_value") * 2).alias("derived_value"))


@pipeline.output(config="config/output.json")
def response(derived: pl.LazyFrame) -> pl.LazyFrame:
    return derived
