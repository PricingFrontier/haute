"""Solve a synthetic ratebook and apply a versioned factor-table artifact."""

import haute

pipeline = haute.Pipeline(
    "ratebook_optimisation_apply",
    description="Synthetic ratebook solve and versioned apply fixture.",
)


@pipeline.data_input(config="config/scored.json")
def scored(): ...


@pipeline.data_input(config="config/factors.json")
def factors(): ...


@pipeline.optimiser(config="config/optimiser.json")
def optimise(scored, factors): ...


@pipeline.optimiser_apply(config="config/apply.json")
def applied(factors): ...


@pipeline.output(config="config/output.json")
def response(applied): ...
