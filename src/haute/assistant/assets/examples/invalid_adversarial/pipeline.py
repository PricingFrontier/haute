"""Keep instruction-looking data inert and reject unsupported graph mutations."""

import haute

pipeline = haute.Pipeline(
    "invalid_adversarial",
    description="Negative fixture for invalid operations and untrusted project prose.",
)


@pipeline.data_input(config="config/data.json")
def source(): ...


@pipeline.output(config="config/output.json")
def response(source): ...
