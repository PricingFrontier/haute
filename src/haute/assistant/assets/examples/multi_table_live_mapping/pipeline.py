"""Join two named tables from one live request and map the combined response."""

import polars as pl

import haute

pipeline = haute.Pipeline(
    "multi_table_live_mapping",
    description="Synthetic multi-table request ports joined into one mapped response.",
)


@pipeline.api_input(config="config/request.json")
def request(): ...


@pipeline.polars
def quote_rows(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes


@pipeline.polars
def driver_rows(drivers: pl.LazyFrame) -> pl.LazyFrame:
    return drivers


@pipeline.edge_join(
    how="left",
    on=["driver_id"],
    validate="1:1",
)
def joined(quote_rows, driver_rows): ...


@pipeline.output(config="config/output.json")
def response(joined): ...


pipeline.connect("request", "quote_rows", source_port="quotes")
pipeline.connect("request", "driver_rows", source_port="drivers")
pipeline.connect("quote_rows", "joined", target_port="base")
pipeline.connect("driver_rows", "joined", target_port="join")
pipeline.connect("joined", "response")
