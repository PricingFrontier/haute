"""Derive one transparent value and retain a deterministic row-trace fixture."""

import polars as pl

import haute

pipeline = haute.Pipeline(
    "trace_audit",
    description="Synthetic transform with explicit row-trace and dry-run evidence.",
)


@pipeline.data_input(config="config/data.json")
def source(): ...


@pipeline.polars
def derived(source: pl.LazyFrame) -> pl.LazyFrame:
    return source.with_columns((pl.col("fixture_value") * 2).alias("derived_value"))


@pipeline.output(config="config/output.json")
def response(derived): ...
