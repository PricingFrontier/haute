"""Group discrete synthetic property categories with an explicit default."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import apply_banding_from_config, resolve_data_input_from_config

pipeline = haute.Pipeline(
    "discrete_banding",
    description="Synthetic categorical banding with an explicit unmatched-category policy.",
)


@pipeline.data_input(config="config/data.json")
def quotes() -> pl.LazyFrame:
    return resolve_data_input_from_config("config/data.json", base_dir=Path(__file__).parent)


@pipeline.banding(config="config/banding/property_group.json")
def banded(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return apply_banding_from_config(
        quotes,
        "config/banding/property_group.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.output(config="config/output.json")
def response(banded: pl.LazyFrame) -> pl.LazyFrame:
    return banded
