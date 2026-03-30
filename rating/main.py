"""Pipeline: my_pipeline"""

import polars as pl
import haute

from utility.features import (
    clean_columns,
    to_date,
    years_between,
)

pipeline = haute.Pipeline("my_pipeline", description='')


@pipeline.data_source(config="config/data_source/batch_quotes.json")
def batch_quotes() -> pl.LazyFrame:
    """batch_quotes node"""
    df = pl.scan_parquet("data/quotes/nb_batch.parquet")
    df = df.limit(100000)
    return df


@pipeline.data_source(config="config/data_source/competitor_insights.json")
def competitor_insights() -> pl.LazyFrame:
    """competitor_insights node"""
    return pl.scan_parquet("data/competitor_premiums/competitor_insight.parquet")


@pipeline.data_source(config="config/data_source/policy_data.json")
def policy_data() -> pl.LazyFrame:
    """policy_data node"""
    return pl.scan_parquet("data/claims/britsure_policies.parquet")


@pipeline.data_source(config="config/data_source/quoted_premiums.json")
def quoted_premiums() -> pl.LazyFrame:
    """quoted_premiums node"""
    return pl.scan_parquet("data/competitor_premiums/britsure_premiums.parquet")


@pipeline.api_input(config="config/quote_input/quotes.json")
def quotes() -> pl.LazyFrame:
    """quotes node"""
    from haute._json_flatten import read_json_flat
    return read_json_flat("data/quotes/sample_quote.json", config_path="config/quote_input/quotes.json")


@pipeline.polars
def processing(quotes: pl.LazyFrame) -> pl.LazyFrame:
    """processing node"""
    df = quotes
    df = clean_columns(quotes)
    cover_start = to_date("cover_start_date")
    
    # Core derived features
    df = df.with_columns(
        years_between(to_date("proposer_date_of_birth"), cover_start).alias("proposer_age"),
        (cover_start.dt.year() - pl.col("year_of_manufacture")).alias("vehicle_age"),
        pl.col("postcode").str.split(" ").list.first().alias("postcode_area"),
    )
    
    # Additional driver ages + licence years
    cols = df.collect_schema().names()
    ad_age_cols = []
    for i in range(1, 5):
        dob = f"additional_drivers_{i}_date_of_birth"
        lic = f"additional_drivers_{i}_licence_licence_date"
        if dob in cols:
            name = f"additional_driver_{i}_age"
            df = df.with_columns(years_between(to_date(dob), cover_start).alias(name))
            ad_age_cols.append(name)
        if lic in cols:
            df = df.with_columns(
                years_between(to_date(lic), cover_start).alias(f"additional_driver_{i}_licence_years")
            )
    
    # Youngest driver across proposer + additional drivers
    df = df.with_columns(
        pl.min_horizontal("proposer_age", *ad_age_cols).alias("youngest_driver_age"),
    )
    return df


@pipeline.live_switch(config="config/source_switch/policies.json")
def policies(batch_quotes: pl.LazyFrame, processing: pl.LazyFrame) -> pl.LazyFrame:
    """policies node"""
    return processing


@pipeline.banding(config="config/banding/Banding_7.json")
def Banding_7(policies: pl.LazyFrame) -> pl.LazyFrame:
    """Banding 7 node"""
    return policies


@pipeline.polars
def competitor_join(policies: pl.LazyFrame, competitor_insights: pl.LazyFrame) -> pl.LazyFrame:
    """competitor_join node"""
    df = policies
    df = policies.join(competitor_insights, on="quote_id", how="inner")
    return df


@pipeline.modelling(config="config/model_training/avg_top_5.json")
def avg_top_5(competitor_join: pl.LazyFrame) -> pl.LazyFrame:
    """avg_top_5 node"""
    return competitor_join


@pipeline.model_score(config="config/model_scoring/competitor_scoring.json")
def competitor_scoring(policies: pl.LazyFrame) -> pl.LazyFrame:
    """competitor_scoring node"""
    from pathlib import Path
    from haute.graph_utils import score_from_config
    base = str(Path(__file__).parent)
    return score_from_config(policies, config="config/model_scoring/competitor_scoring.json", base_dir=base)


@pipeline.polars
def join_scoring(policies: pl.LazyFrame, competitor_scoring: pl.LazyFrame) -> pl.LazyFrame:
    """Join competitor scoring onto policies"""
    df = policies
    df = policies.join(competitor_scoring, on="quote_id", how="left")
    return df


@pipeline.polars
def join_policy_data(join_scoring: pl.LazyFrame, policy_data: pl.LazyFrame) -> pl.LazyFrame:
    """Join policy data"""
    df = join_scoring
    df = join_scoring.join(policy_data, on="quote_id", how="left")
    return df


@pipeline.polars
def join_premiums(join_policy_data: pl.LazyFrame, quoted_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """Join quoted premiums and derive sale_flag"""
    df = join_policy_data
    df = join_policy_data.join(quoted_premiums, on="quote_id", how="left").with_columns(
        sale_flag=pl.when(pl.col("policy_id").is_null()).then(pl.lit(0)).otherwise(pl.lit(1)),
        burn_cost=pl.col("premium") * 0.7,
    )
    return df


@pipeline.polars(selected_columns=['quote_id', 'sale_flag', 'competitor_premium', 'premium', 'difference_to_market', 'proposer_age', 'cover_type', 'margin', 'burn_cost'])
def competitor_features(join_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """competitor_features node"""
    df = join_premiums
    df = join_premiums.with_columns(
        difference_to_market=pl.col("premium") / pl.col("competitor_premium")
    )
    return df


@pipeline.modelling(config="config/model_training/conversion.json")
def conversion(competitor_features: pl.LazyFrame) -> pl.LazyFrame:
    """conversion node"""
    return competitor_features


@pipeline.data_sink(config="config/data_sink/conversion_sink.json")
def conversion_sink(competitor_features: pl.LazyFrame) -> pl.LazyFrame:
    """Data Sink 9 node"""
    from haute._polars_utils import safe_sink
    safe_sink(competitor_features, "output/conversion_data.parquet")
    return competitor_features


@pipeline.scenario_expander(config="config/expander/premium.json")
def premium(join_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """premium node"""
    df = join_premiums
    df = df.with_columns(premium=pl.col("premium") * pl.col("premium_multiplier"))
    return df


@pipeline.model_score(config="config/model_scoring/conversion_scoring.json")
def conversion_scoring(competitor_features_scenarios: pl.LazyFrame) -> pl.LazyFrame:
    """conversion_scoring node"""
    from pathlib import Path
    from haute.graph_utils import score_from_config
    base = str(Path(__file__).parent)
    return score_from_config(competitor_features_scenarios, config="config/model_scoring/conversion_scoring.json", base_dir=base)


@pipeline.polars
def optimiser_input(conversion_scoring: pl.LazyFrame) -> pl.LazyFrame:
    """Polars 8 node"""
    df = conversion_scoring
    df = conversion_scoring.with_columns(
        margin=pl.col("premium") - pl.col("burn_cost"),
    ).with_columns(
        expected_margin=pl.col("margin") * pl.col("conversion_prediction"),
    )
    return df


@pipeline.optimiser_apply(config="config/apply_optimisation/apply_optimisation.json")
def apply_optimisation(optimiser_input: pl.LazyFrame) -> pl.LazyFrame:
    """apply_optimisation node"""
    return optimiser_input


@pipeline.optimiser(config="config/optimisation/online_optimiser.json")
def online_optimiser(optimiser_input: pl.LazyFrame) -> pl.LazyFrame:
    """online_optimiser node"""
    return optimiser_input


@pipeline.instance(of="competitor_features")
def competitor_features_scenarios(premium: pl.LazyFrame) -> pl.LazyFrame:
    """Instance of competitor_features"""
    return competitor_features(join_premiums=premium)



# Wire nodes together - edges define data flow
pipeline.connect("batch_quotes", "policies")
pipeline.connect("policies", "competitor_join")
pipeline.connect("competitor_insights", "competitor_join")
pipeline.connect("competitor_join", "avg_top_5")
pipeline.connect("policies", "competitor_scoring")
pipeline.connect("policies", "join_scoring")
pipeline.connect("competitor_scoring", "join_scoring")
pipeline.connect("join_scoring", "join_policy_data")
pipeline.connect("policy_data", "join_policy_data")
pipeline.connect("join_policy_data", "join_premiums")
pipeline.connect("quoted_premiums", "join_premiums")
pipeline.connect("join_premiums", "competitor_features")
pipeline.connect("competitor_features", "conversion_sink")
pipeline.connect("join_premiums", "premium")
pipeline.connect("competitor_features", "conversion")
pipeline.connect("premium", "competitor_features_scenarios")
pipeline.connect("competitor_features_scenarios", "conversion_scoring")
pipeline.connect("conversion_scoring", "optimiser_input")
pipeline.connect("optimiser_input", "online_optimiser")
pipeline.connect("quotes", "processing")
pipeline.connect("processing", "policies")
pipeline.connect("optimiser_input", "apply_optimisation")
pipeline.connect("policies", "Banding_7")
