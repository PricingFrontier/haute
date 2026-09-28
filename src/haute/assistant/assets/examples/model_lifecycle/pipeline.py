"""Train a tiny synthetic model and document the corresponding scoring contract."""

import haute

pipeline = haute.Pipeline(
    "model_lifecycle",
    description="Synthetic model training and model-scoring lifecycle fixture.",
)


@pipeline.data_input(config="config/training_data.json")
def training_rows(): ...


@pipeline.modelling(config="config/model.json")
def train(training_rows): ...


@pipeline.model_score(config="config/model_score.json")
def scored(training_rows): ...


@pipeline.output(config="config/output.json")
def response(train): ...
