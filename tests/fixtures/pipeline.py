"""Fixture pipeline for tests — self-contained, no external model dependencies.

This pipeline mirrors the structure of a real pricing pipeline (apiInput,
dataSource, liveSwitch, polars, externalFile, output, dataSink) but uses
only simple Polars expressions and a JSON lookup file so tests don't depend
on CatBoost or the user's main.py.
"""

import polars as pl

import haute

pipeline = haute.Pipeline("test_pipeline", description="Test fixture pipeline")


@pipeline.api_input(config="config/quote_input/quotes.json")
def quotes() -> pl.LazyFrame:
    """API input source."""
    return pl.read_json("tests/fixtures/data/api_input.json").lazy()


@pipeline.data_source(config="config/data_source/batch_quotes.json")
def batch_quotes() -> pl.LazyFrame:
    """Batch data source."""
    return pl.scan_parquet("tests/fixtures/data/policies.parquet")


@pipeline.live_switch(config="config/source_switch/policies.json")
def policies(quotes: pl.LazyFrame, batch_quotes: pl.LazyFrame) -> pl.LazyFrame:
    """Live/batch switch."""
    return quotes


@pipeline.external_file(
    config="config/load_file/area_lookup.json",
    contract={"inputs": ["Area"], "outputs": ["area_factor"]},
)
def area_lookup(policies: pl.LazyFrame) -> pl.LazyFrame:
    """External file node — loads a JSON lookup table.

    The executor injects ``obj`` (the loaded JSON) via extra_ns.
    The parser strips the import + load_external_object call from the
    code body — only the lines that use ``obj`` are kept.
    """
    from haute.graph_utils import load_external_object

    obj = load_external_object("tests/fixtures/data/area_factors.json", "json")
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
def output(calculate_premium: pl.LazyFrame) -> pl.LazyFrame:
    """Output node."""
    return calculate_premium


@pipeline.data_sink(config="config/data_sink/results_write.json")
def results_write(calculate_premium: pl.LazyFrame) -> pl.LazyFrame:
    """Sink node."""
    return calculate_premium


pipeline.connect("quotes", "policies")
pipeline.connect("batch_quotes", "policies")
pipeline.connect("policies", "area_lookup")
pipeline.connect("area_lookup", "calculate_premium")
pipeline.connect("calculate_premium", "output")
pipeline.connect("calculate_premium", "results_write")
