"""Submodel: fttgggg"""

import polars as pl
import haute


submodel = haute.Submodel("fttgggg")


@submodel.polars
def join_scoring(policies: pl.LazyFrame, competitor_scoring: pl.LazyFrame) -> pl.LazyFrame:
    """Join competitor scoring onto policies"""
    df = policies
    df = policies.join(competitor_scoring, on="quote_id", how="left")
    return df


@submodel.data_source(config="config/data_source/policy_data.json")
def policy_data() -> pl.LazyFrame:
    """policy_data node"""
    return pl.scan_parquet("")


@submodel.polars
def join_policy_data(join_scoring: pl.LazyFrame, policy_data: pl.LazyFrame) -> pl.LazyFrame:
    """Join policy data"""
    df = join_scoring
    df = join_scoring.join(policy_data, on="quote_id", how="left")
    return df


@submodel.data_source(config="config/data_source/quoted_premiums.json")
def quoted_premiums() -> pl.LazyFrame:
    """quoted_premiums node"""
    return pl.scan_parquet("")



# Wire nodes together - edges define data flow
submodel.connect("join_scoring", "join_policy_data")
submodel.connect("policy_data", "join_policy_data")
