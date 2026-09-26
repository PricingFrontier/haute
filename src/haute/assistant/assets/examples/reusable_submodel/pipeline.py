"""Import a reusable enrichment submodel behind an explicit boundary port."""

import haute

pipeline = haute.Pipeline(
    "reusable_submodel",
    description="Synthetic file-backed submodel with one explicit input boundary.",
)


@pipeline.data_input(config="config/data.json")
def quotes(): ...


@pipeline.output(config="config/output.json")
def response(enriched): ...


pipeline.submodel("modules/reusable_enrichment.py", "enriched")
pipeline.connect("quotes", "enriched", target_port="quotes")
pipeline.connect("enriched", "response", source_port="enriched")
