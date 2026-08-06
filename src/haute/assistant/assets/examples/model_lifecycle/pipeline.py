"""Train a tiny synthetic model and document the corresponding scoring contract."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import resolve_data_input_from_config

pipeline = haute.Pipeline(
    "model_lifecycle",
    description="Synthetic model training and model-scoring lifecycle fixture.",
)


@pipeline.data_input(config="config/training_data.json")
def training_rows() -> pl.LazyFrame:
    return resolve_data_input_from_config(
        "config/training_data.json",
        base_dir=Path(__file__).parent,
    )


@pipeline.modelling(config="config/model.json")
def train(training_rows: pl.LazyFrame) -> pl.LazyFrame:
    return training_rows


@pipeline.model_score(config="config/model_score.json")
def scored(training_rows: pl.LazyFrame) -> pl.LazyFrame:
    return training_rows


@pipeline.output(config="config/output.json")
def response(train: pl.LazyFrame) -> pl.LazyFrame:
    return train
