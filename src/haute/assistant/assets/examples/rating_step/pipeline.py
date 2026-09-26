"""Apply a synthetic engineering factor table with no commercial assumptions."""

import haute

pipeline = haute.Pipeline("rating_step", description="Synthetic mechanical rating fixture.")


@pipeline.data_input(config="config/data.json")
def banded(): ...


@pipeline.rating_step(config="config/rating_step/rated.json")
def rated(banded): ...


@pipeline.output(config="config/output.json")
def response(rated): ...
