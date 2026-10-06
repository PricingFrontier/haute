"""Solve a synthetic ratebook and apply a versioned factor-table artifact.

A ratebook solve takes its rating factors from a Banding node: the Banding
node bands each policy's raw area into a region, and the optimiser names it
as its rating factor source (`banding_source`). The optimiser has no output,
so it is a terminal branch; Apply Optimisation reads the same banded frame
and feeds the response.
"""

import haute

pipeline = haute.Pipeline(
    "ratebook_optimisation_apply",
    description="Synthetic ratebook solve and versioned apply fixture.",
)


@pipeline.data_input(config="config/scored.json")
def scored(): ...


@pipeline.data_input(config="config/policies.json")
def policies(): ...


@pipeline.banding(config="config/banding/rating_factors.json")
def rating_factors(policies): ...


@pipeline.optimiser(config="config/optimiser.json")
def optimise(scored, rating_factors): ...


@pipeline.optimiser_apply(config="config/apply.json")
def applied(rating_factors): ...


@pipeline.output(config="config/output.json")
def response(applied): ...
