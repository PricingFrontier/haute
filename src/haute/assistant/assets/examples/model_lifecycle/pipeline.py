"""Train a tiny synthetic model on one branch and score with it on another.

Model Training has no output, so training is a terminal branch: nothing is
wired downstream of it. The response is fed by the Model Score node, which
names the training run whose artifact it scores with.
"""

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
def response(scored): ...
