"""Import a reusable enrichment submodel behind an explicit boundary port.

The occurrence is named `enrichment`, apart from every node inside the
definition: submodels run in one namespace with the pipeline, so an
occurrence named like an inner node would collide with it. The response
reads the occurrence through its public output port, `enriched`.
"""

import haute

pipeline = haute.Pipeline(
    "reusable_submodel",
    description="Synthetic file-backed submodel with one explicit input boundary.",
)


@pipeline.data_input(config="config/data.json")
def quotes(): ...


@pipeline.output(config="config/output.json")
def response(enriched): ...


pipeline.submodel("modules/reusable_enrichment.py", "enrichment")
pipeline.connect("quotes", "enrichment", target_port="quotes")
pipeline.connect("enrichment", "response", source_port="enriched")
