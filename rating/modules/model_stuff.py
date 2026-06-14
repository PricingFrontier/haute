"""Submodel: model_stuff"""

import polars as pl
import haute


submodel = haute.Submodel("model_stuff")


@submodel.polars(contract={'inputs': ['policy_id', 'premium', 'quote_id'], 'outputs': ['burn_cost', 'sale_flag']})
def sale_flag(join_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """Join quoted premiums and derive sale_flag"""
    df = join_premiums
    df = (
        join_premiums.with_columns(
            sale_flag=pl.when(pl.col("policy_id").is_null()).then(pl.lit(0)).otherwise(pl.lit(1)),
            burn_cost=pl.col("premium") * 0.7
        )
    )
    return df


@submodel.polars(selected_columns=['quote_id', 'sale_flag', 'competitor_premium', 'premium', 'difference_to_market', 'proposer_age', 'cover_type', 'burn_cost'], contract="opaque")
def competitor_features(sale_flag: pl.LazyFrame) -> pl.LazyFrame:
    """competitor_features node"""
    df = sale_flag
    df = sale_flag.with_columns(
        difference_to_market=pl.col("premium") / pl.col("competitor_premium")
    )
    return df


@submodel.scenario_expander(config="config/expander/premium.json", contract={'inputs': ['premium'], 'outputs': ['premium_multiplier', 'scenario_index']})
def premium(sale_flag: pl.LazyFrame) -> pl.LazyFrame:
    """premium node"""
    df = sale_flag
    df = df.with_columns(premium=pl.col("premium") * pl.col("premium_multiplier"))
    return df



# Wire nodes together - edges define data flow
submodel.connect("sale_flag", "competitor_features")
submodel.connect("sale_flag", "premium")
