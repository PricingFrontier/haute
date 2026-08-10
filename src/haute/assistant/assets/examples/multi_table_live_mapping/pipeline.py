"""Join two named tables from one live request and map the combined response."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import resolve_api_input_from_config

pipeline = haute.Pipeline(
    "multi_table_live_mapping",
    description="Synthetic multi-table request ports joined into one mapped response.",
)


@pipeline.api_input(config="config/request.json")
def request() -> pl.LazyFrame | dict[str, pl.LazyFrame]:
    return resolve_api_input_from_config("config/request.json", base_dir=Path(__file__).parent)


@pipeline.polars
def quote_rows(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes


@pipeline.polars
def driver_rows(drivers: pl.LazyFrame) -> pl.LazyFrame:
    return drivers


@pipeline.edge_join(
    base_input="quote_rows",
    join_input="driver_rows",
    how="left",
    on=["driver_id"],
    validate="1:1",
)
def joined(quote_rows: pl.LazyFrame, driver_rows: pl.LazyFrame) -> pl.LazyFrame:
    result = pipeline._apply_edge_join("joined", quote_rows, driver_rows)
    if not isinstance(result, pl.LazyFrame):
        raise TypeError("multi-table live mapping must preserve lazy execution")
    return result


@pipeline.output(config="config/output.json")
def response(joined: pl.LazyFrame) -> pl.LazyFrame:
    return joined


pipeline.connect("request", "quote_rows", source_port="quotes")
pipeline.connect("request", "driver_rows", source_port="drivers")
pipeline.connect("quote_rows", "joined", target_port="base")
pipeline.connect("driver_rows", "joined", target_port="join")
pipeline.connect("joined", "response")
