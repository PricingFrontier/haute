"""Import a reusable enrichment submodel behind an explicit boundary port."""

from pathlib import Path

import polars as pl

import haute
from haute.graph_utils import resolve_data_input_from_config

pipeline = haute.Pipeline(
    "reusable_submodel",
    description="Synthetic file-backed submodel with one explicit input boundary.",
)


@pipeline.data_input(config="config/data.json")
def quotes() -> pl.LazyFrame:
    return resolve_data_input_from_config("config/data.json", base_dir=Path(__file__).parent)


@pipeline.output(config="config/output.json")
def response(enriched: pl.LazyFrame) -> pl.LazyFrame:
    return enriched


pipeline.submodel(
    "modules/reusable_enrichment.py",
    definition_id="definition_reusable_enrichment",
    instance_id="instance_reusable_enrichment",
    alias="enriched",
)
pipeline.connect("quotes", "enriched", target_port="quotes")
pipeline.connect("enriched", "response", source_port="enriched")
