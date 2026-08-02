"""Attach a synthetic region label using an explicit left reference join."""

import polars as pl

import haute

pipeline = haute.Pipeline("reference_join", description="Synthetic reference-join fixture.")


@pipeline.data_input(config="config/quotes.json")
def quotes() -> pl.LazyFrame:
    from pathlib import Path

    from haute.graph_utils import resolve_data_input_from_config

    return resolve_data_input_from_config("config/quotes.json", base_dir=Path(__file__).parent)


@pipeline.data_input(config="config/regions.json")
def regions() -> pl.LazyFrame:
    from pathlib import Path

    from haute.graph_utils import resolve_data_input_from_config

    return resolve_data_input_from_config("config/regions.json", base_dir=Path(__file__).parent)


@pipeline.edge_join(
    base_input="quotes",
    join_input="regions",
    how="left",
    left_on=["region"],
    right_on=["region"],
    validate="m:1",
)
def joined(quotes: pl.LazyFrame, regions: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.join(regions, on="region", how="left")


@pipeline.polars
def ordered(joined: pl.LazyFrame) -> pl.LazyFrame:
    """Stabilize the join result before applying positional golden assertions."""

    return joined.sort("quote_id")


@pipeline.output(config="config/output.json")
def response(ordered: pl.LazyFrame) -> pl.LazyFrame:
    return ordered


pipeline.connect("quotes", "joined", target_port="base")
pipeline.connect("regions", "joined", target_port="join")
pipeline.connect("joined", "ordered")
