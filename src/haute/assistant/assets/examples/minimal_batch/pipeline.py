"""A synthetic batch source flowing directly to a response output."""

import haute

pipeline = haute.Pipeline("minimal_batch", description="Synthetic mechanical batch fixture.")


@pipeline.data_input(config="config/data.json")
def quotes(): ...


@pipeline.output(config="config/output.json")
def response(quotes): ...
