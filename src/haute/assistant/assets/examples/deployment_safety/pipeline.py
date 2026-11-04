"""Expose a minimal live scorer with reproducible deployment configuration."""

import haute

pipeline = haute.Pipeline(
    "deployment_safety",
    description="Synthetic deployment-preflight and fail-closed safety fixture.",
)


@pipeline.api_input(config="config/request.json")
def quote_input(): ...


@pipeline.output(config="config/output.json")
def quote_response(quote): ...


pipeline.connect("quote_input", "quote_response", source_port="quote")
