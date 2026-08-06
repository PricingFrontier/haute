"""A synthetic batch source flowing directly to a response output."""

import polars as pl

import haute

pipeline = haute.Pipeline("minimal_batch", description="Synthetic mechanical batch fixture.")


@pipeline.data_input(config="config/data.json")
def quotes() -> pl.LazyFrame:
    from pathlib import Path

    from haute.graph_utils import resolve_data_input_from_config

    return resolve_data_input_from_config("config/data.json", base_dir=Path(__file__).parent)


@pipeline.output(config="config/output.json")
def response(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes
