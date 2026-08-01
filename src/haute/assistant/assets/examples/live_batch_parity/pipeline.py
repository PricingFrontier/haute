"""Route schema-compatible live and batch sources through one source switch."""

from pathlib import Path

import polars as pl

import haute
from haute._model_scorer import _scenario_ctx
from haute.graph_utils import (
    resolve_api_input_from_config,
    resolve_data_input_from_config,
    select_live_switch_input,
)

pipeline = haute.Pipeline(
    "live_batch_parity",
    description="Synthetic live/batch source parity behind an explicit scenario switch.",
)

_SCENARIOS = {"live_request": "live", "batch_rows": "nb_batch"}


@pipeline.api_input(config="config/request.json")
def live_request() -> pl.LazyFrame | dict[str, pl.LazyFrame]:
    return resolve_api_input_from_config("config/request.json", base_dir=Path(__file__).parent)


@pipeline.data_input(config="config/batch.json")
def batch_rows() -> pl.LazyFrame:
    return resolve_data_input_from_config("config/batch.json", base_dir=Path(__file__).parent)


@pipeline.live_switch(config="config/source_switch/selected.json")
def selected(live_request: pl.LazyFrame, batch_rows: pl.LazyFrame) -> pl.LazyFrame:
    return select_live_switch_input(
        _SCENARIOS,
        _scenario_ctx.get(),
        {"live_request": live_request, "batch_rows": batch_rows},
        ["live_request", "batch_rows"],
        switch="selected",
    )


@pipeline.output(config="config/output.json")
def response(selected: pl.LazyFrame) -> pl.LazyFrame:
    return selected


pipeline.connect("live_request", "selected", source_port="live_request")
