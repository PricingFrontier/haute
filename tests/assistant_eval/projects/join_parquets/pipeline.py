"""Two held-out parquet sources for exact edge-join role testing."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import resolve_data_input_from_config

pipeline = haute.Pipeline("join_parquets")


@pipeline.data_input(config="config/data_input/nb_batch.json")
def nb_batch() -> pl.LazyFrame:
    return resolve_data_input_from_config(
        "config/data_input/nb_batch.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.data_input(config="config/data_input/competitor_insight.json")
def competitor_insight() -> pl.LazyFrame:
    return resolve_data_input_from_config(
        "config/data_input/competitor_insight.json",
        base_dir=Path(__file__).parent,
    )
