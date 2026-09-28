"""Expose a minimal live scorer with reproducible deployment configuration."""

import haute

pipeline = haute.Pipeline(
    "deployment_safety",
    description="Synthetic deployment-preflight and fail-closed safety fixture.",
)


@pipeline.api_input(config="config/request.json")
def quote(): ...


@pipeline.output(config="config/output.json")
def response(quote): ...


pipeline.connect("quote", "response", source_port="quote")
