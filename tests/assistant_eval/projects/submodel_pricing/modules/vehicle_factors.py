"""Submodel: vehicle_factors"""

import haute
import polars as pl

submodel = haute.Submodel(
    "vehicle_factors",
    description="Vehicle age and value factors shared by the pricing pipelines.",
    definition_id="definition_vehicle_factors",
    input_ports=[
        {"name": "vehicles", "targets": [{"nodeId": "vehicle_age", "handleId": None}]}
    ],
    output_ports=[
        {"name": "factored", "source": {"nodeId": "vehicle_factor", "handleId": None}}
    ],
    pipeline_dir="..",
)


@submodel.polars
def vehicle_age(vehicles: pl.LazyFrame) -> pl.LazyFrame:
    df = vehicles.with_columns(vehicle_age=2025 - pl.col("vehicle_year"))
    return df


@submodel.polars
def vehicle_factor(vehicle_age: pl.LazyFrame) -> pl.LazyFrame:
    df = vehicle_age.with_columns(
        vehicle_factor=pl.when(pl.col("vehicle_age") > 10).then(1.2).otherwise(1.0)
    )
    return df


# Wire nodes together - edges define data flow
submodel.connect("vehicle_age", "vehicle_factor")
