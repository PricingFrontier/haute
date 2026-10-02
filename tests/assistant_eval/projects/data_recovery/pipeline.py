"""Pipeline: data_recovery"""

import haute

pipeline = haute.Pipeline(
    "data_recovery",
    description="Motor quotes with their region loadings and vehicle factors to hand.",
)


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes(): ...
