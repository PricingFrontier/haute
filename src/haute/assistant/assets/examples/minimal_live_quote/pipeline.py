"""A synthetic live quote request flowing directly to a response output."""

import haute

pipeline = haute.Pipeline("minimal_live_quote", description="Synthetic mechanical live fixture.")


@pipeline.api_input(config="config/request.json")
def quote_input(): ...


@pipeline.output(config="config/output.json")
def quote_response(quote): ...


pipeline.connect("quote_input", "quote_response", source_port="quote")
