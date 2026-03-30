"""Submodel: submodel"""

import polars as pl
import haute


submodel = haute.Submodel("submodel")


@submodel.polars
def join_scoring(df: pl.LazyFrame) -> pl.LazyFrame:
    """Join competitor scoring onto policies"""
    df = policies.join(competitor_scoring, on="quote_id", how="left")
    return df


@submodel.data_source(config="config/data_source/policy_data.json")
def policy_data() -> pl.LazyFrame:
    """policy_data node"""
    return pl.scan_parquet("data/claims/britsure_policies.parquet")


@submodel.polars
def join_policy_data(join_scoring: pl.LazyFrame, policy_data: pl.LazyFrame) -> pl.LazyFrame:
    """Join policy data"""
    df = join_scoring
    df = join_scoring.join(policy_data, on="quote_id", how="left")
    return df


@submodel.data_source(config="config/data_source/quoted_premiums.json")
def quoted_premiums() -> pl.LazyFrame:
    """quoted_premiums node"""
    return pl.scan_parquet("data/competitor_premiums/britsure_premiums.parquet")


@submodel.polars
def join_premiums(join_policy_data: pl.LazyFrame, quoted_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """Join quoted premiums and derive sale_flag"""
    df = join_policy_data
    df = join_policy_data.join(quoted_premiums, on="quote_id", how="left").with_columns(
        sale_flag=pl.when(pl.col("policy_id").is_null()).then(pl.lit(0)).otherwise(pl.lit(1)),
        burn_cost=pl.col("premium") * 0.7,
    )
    return df



# Wire nodes together - edges define data flow
submodel.connect("join_scoring", "join_policy_data")
submodel.connect("policy_data", "join_policy_data")
submodel.connect("join_policy_data", "join_premiums")
submodel.connect("quoted_premiums", "join_premiums")
