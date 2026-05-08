"""Submodel: df"""

import polars as pl
import haute


submodel = haute.Submodel("df")


@submodel.banding(config="config/banding/age_veh_banding.json", contract={"inputs": ['channel', 'proposer_age', 'vehicle_age'], "outputs": ['channel_band', 'proposer_age_band', 'vehicle_age_band']})
def age_veh_banding(df: pl.LazyFrame) -> pl.LazyFrame:
    """Banding 7 node"""
    return df


@submodel.rating_step(config="config/rating_step/adjustments.json", contract="opaque")
def adjustments(age_veh_banding: pl.LazyFrame) -> pl.LazyFrame:
    """"""
    from pathlib import Path
    from haute.graph_utils import apply_rating_step_from_config
    base = Path(__file__).parent
    df = apply_rating_step_from_config(age_veh_banding, "config/rating_step/adjustments.json", base_dir=base)
    df = df.with_columns(test=pl.col('multiplied') * 2)
    return df



# Wire nodes together - edges define data flow
submodel.connect("age_veh_banding", "adjustments")
