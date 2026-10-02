"""Pipeline: submodel_pricing"""

import haute
import polars as pl

pipeline = haute.Pipeline(
    "submodel_pricing",
    description="Policies priced with a shared vehicle factors submodel.",
)


@pipeline.data_input(config="config/data_input/policies.json")
def policies(): ...


@pipeline.polars(config="config/polars/premium.json")
def premium(factored: pl.LazyFrame) -> pl.LazyFrame:
    df = factored
    # Apply the vehicle factor to the base rate
    df = df.with_columns(premium=(pl.col('base_rate') * pl.col('vehicle_factor')).round(2))
    return df


pipeline.submodel("modules/vehicle_factors.py", "vehicle_factors")

# Wire nodes together - edges define data flow
pipeline.connect("policies", "vehicle_factors", target_port="vehicles")
pipeline.connect("vehicle_factors", "premium", source_port="factored")
