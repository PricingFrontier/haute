"""Group discrete synthetic property categories with an explicit default."""

import haute

pipeline = haute.Pipeline(
    "discrete_banding",
    description="Synthetic categorical banding with an explicit unmatched-category policy.",
)


@pipeline.data_input(config="config/data.json")
def quotes(): ...


@pipeline.banding(config="config/banding/property_group.json")
def banded(quotes): ...


@pipeline.output(config="config/output.json")
def response(banded): ...
