"""Apply a synthetic engineering factor table with no commercial assumptions."""

import polars as pl

import haute

pipeline = haute.Pipeline("rating_step", description="Synthetic mechanical rating fixture.")


@pipeline.data_input(config="config/data.json")
def banded() -> pl.LazyFrame:
    from pathlib import Path

    from haute.graph_utils import resolve_data_input_from_config

    return resolve_data_input_from_config("config/data.json", base_dir=Path(__file__).parent)


@pipeline.rating_step(config="config/rating_step/rated.json")
def rated(banded: pl.LazyFrame) -> pl.LazyFrame:
    return banded


@pipeline.output(config="config/output.json")
def response(rated: pl.LazyFrame) -> pl.LazyFrame:
    return rated
