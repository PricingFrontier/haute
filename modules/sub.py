"""Submodel: sub"""

import polars as pl
import haute


submodel = haute.Submodel("sub")


@submodel.data_source(config="config/data_source/quoted_premiums.json", contract="opaque")
def quoted_premiums() -> pl.LazyFrame:
    """quoted_premiums node"""
    from pathlib import Path
    df = pl.scan_parquet(Path(__file__).parent / "data/competitor_premiums/britsure_premiums.parquet")
    return df


@submodel.polars(contract="opaque")
def join_policy_data(join_scoring: pl.LazyFrame, policy_data: pl.LazyFrame) -> pl.LazyFrame:
    """Join policy data"""
    df = join_scoring
    df = join_scoring.join(policy_data, on="quote_id", how="left")
    return df


@submodel.polars(contract="opaque")
def join_premiums(join_policy_data: pl.LazyFrame, quoted_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """Join quoted premiums and derive sale_flag"""
    df = join_policy_data
    df = join_policy_data.join(quoted_premiums, on="quote_id", how="left").with_columns(
        sale_flag=pl.when(pl.col("policy_id").is_null()).then(pl.lit(0)).otherwise(pl.lit(1)),
        burn_cost=pl.col("premium") * 0.7,
    )
    return df


@submodel.polars(selected_columns=['quote_id', 'sale_flag', 'competitor_premium', 'premium', 'difference_to_market', 'proposer_age', 'cover_type', 'burn_cost'], contract="opaque")
def competitor_features(join_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """competitor_features node"""
    df = join_premiums
    df = join_premiums.with_columns(
        difference_to_market=pl.col("premium") / pl.col("competitor_premium")
    )
    return df


@submodel.scenario_expander(config="config/expander/premium.json", contract="opaque")
def premium(join_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """premium node"""
    df = join_premiums
    df = df.with_columns(premium=pl.col("premium") * pl.col("premium_multiplier"))
    return df



# Wire nodes together - edges define data flow
submodel.connect("join_policy_data", "join_premiums")
submodel.connect("quoted_premiums", "join_premiums")
submodel.connect("join_premiums", "competitor_features")
submodel.connect("join_premiums", "premium")
