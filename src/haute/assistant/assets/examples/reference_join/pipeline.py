"""Attach a synthetic region label using an explicit left reference join."""

import polars as pl

import haute

pipeline = haute.Pipeline("reference_join", description="Synthetic reference-join fixture.")


@pipeline.data_input(config="config/quotes.json")
def quotes(): ...


@pipeline.data_input(config="config/regions.json")
def regions(): ...


@pipeline.edge_join(
    how="left",
    left_on=["region"],
    right_on=["region"],
    validate="m:1",
)
def joined(quotes, regions): ...


@pipeline.polars
def ordered(joined: pl.LazyFrame) -> pl.LazyFrame:
    """Stabilize the join result before applying positional golden assertions."""

    return joined.sort("quote_id")


@pipeline.output(config="config/output.json")
def response(ordered): ...


pipeline.connect("quotes", "joined", target_port="base")
pipeline.connect("regions", "joined", target_port="join")
pipeline.connect("joined", "ordered")
