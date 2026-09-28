"""Fixture pipeline for tests — self-contained, no external model dependencies.

This pipeline mirrors the structure of a real pricing pipeline (apiInput,
dataInput, liveSwitch, polars, externalFile, output, dataOutput) but uses
only simple Polars expressions and a JSON lookup file so tests don't depend
on CatBoost or the user's main.py.
"""

import polars as pl

import haute

pipeline = haute.Pipeline("test_pipeline", description="Test fixture pipeline")


@pipeline.api_input(config="config/quote_input/quotes.json")
def quotes():
    """API input source."""


@pipeline.data_input(config="config/data_input/batch_quotes.json")
def batch_quotes():
    """Batch data source."""


@pipeline.live_switch(config="config/source_switch/policies.json")
def policies(quotes, batch_quotes):
    """Live/batch switch."""


@pipeline.external_file(
    config="config/load_file/area_lookup.json",
    contract={"inputs": ["Area"], "outputs": ["area_factor"]},
)
def area_lookup(policies: pl.LazyFrame, *, obj) -> pl.LazyFrame:
    """External file node — loads a JSON lookup table.

    The decorator loads the configured file and passes it as ``obj``; the
    body is the node's code, which reads its input by name.
    """
    df = policies.with_columns(
        area_factor=pl.col("Area").replace_strict(obj, default=1.0),
    )
    return df


@pipeline.polars(
    contract={"inputs": ["VehPower", "area_factor", "Exposure"], "outputs": ["premium"]},
)
def calculate_premium(area_lookup: pl.LazyFrame) -> pl.LazyFrame:
    """Simple premium calculation."""
    df = area_lookup.with_columns(
        premium=(pl.col("VehPower") * pl.col("area_factor") * pl.col("Exposure")),
    )
    return df


@pipeline.output(config="config/quote_response/output.json")
def output(calculate_premium):
    """Output node."""


@pipeline.data_output(config="config/data_output/results_write.json")
def results_write(calculate_premium):
    """Sink node."""


pipeline.connect("quotes", "policies", source_port="quotes")
pipeline.connect("batch_quotes", "policies")
pipeline.connect("policies", "area_lookup")
pipeline.connect("area_lookup", "calculate_premium")
pipeline.connect("calculate_premium", "output")
pipeline.connect("calculate_premium", "results_write")
