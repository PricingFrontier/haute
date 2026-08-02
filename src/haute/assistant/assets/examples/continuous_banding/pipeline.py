"""Band synthetic driver ages with explicit mechanical boundary rules."""

import polars as pl

import haute

pipeline = haute.Pipeline("continuous_banding", description="Synthetic continuous-banding fixture.")


@pipeline.data_input(config="config/data.json")
def quotes() -> pl.LazyFrame:
    from pathlib import Path

    from haute.graph_utils import resolve_data_input_from_config

    return resolve_data_input_from_config("config/data.json", base_dir=Path(__file__).parent)


@pipeline.banding(config="config/banding/banded.json")
def banded(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes


@pipeline.output(config="config/output.json")
def response(banded: pl.LazyFrame) -> pl.LazyFrame:
    return banded
