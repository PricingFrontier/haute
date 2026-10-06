"""A synthetic live quote request flowing directly to a response output."""

import haute

pipeline = haute.Pipeline("minimal_live_quote", description="Synthetic mechanical live fixture.")


@pipeline.api_input(config="config/request.json")
def quote(): ...


@pipeline.output(config="config/output.json")
def response(quote): ...


pipeline.connect("quote", "response", source_port="quote")
