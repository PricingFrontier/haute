"""Route schema-compatible live and batch sources through one source switch."""

import haute

pipeline = haute.Pipeline(
    "live_batch_parity",
    description="Synthetic live/batch source parity behind an explicit scenario switch.",
)


@pipeline.api_input(config="config/request.json")
def live_request(): ...


@pipeline.data_input(config="config/batch.json")
def batch_rows(): ...


@pipeline.live_switch(config="config/source_switch/selected.json")
def selected(live_request, batch_rows): ...


@pipeline.output(config="config/output.json")
def response(selected): ...


pipeline.connect("live_request", "selected", source_port="live_request")
