"""Pipeline: my_pipeline"""

import polars as pl
import haute

from utility.features import clean_columns, to_date, years_between

pipeline = haute.Pipeline("my_pipeline", description='')


@pipeline.data_source(config="config/data_source/batch_quotes.json", contract="opaque")
def batch_quotes() -> pl.LazyFrame:
    """batch_quotes node"""
    from pathlib import Path
    from haute.graph_utils import read_data_source
    df = read_data_source({"sourceType": 'flat_file', "path": str(Path(__file__).parent / "data/quotes/nb_batch.parquet"), 'contract': 'opaque'})
    df = df.limit(1000000)
    return df


@pipeline.data_source(config="config/data_source/competitor_insights.json", contract="opaque")
def competitor_insights() -> pl.LazyFrame:
    """competitor_insights node"""
    from pathlib import Path
    from haute.graph_utils import read_data_source
    df = read_data_source({"sourceType": 'flat_file', "path": str(Path(__file__).parent / "data/competitor_premiums/competitor_insight.parquet"), 'contract': 'opaque'})
    return df


@pipeline.data_source(config="config/data_source/policy_data.json", contract="opaque")
def policy_data() -> pl.LazyFrame:
    """policy_data node"""
    from pathlib import Path
    from haute.graph_utils import read_data_source
    df = read_data_source({"sourceType": 'flat_file', "path": str(Path(__file__).parent / "data/claims/britsure_policies.parquet"), 'selected_columns': ['policy_id', 'quote_id'], 'contract': 'opaque'})
    return df


@pipeline.data_source(config="config/data_source/quoted_premiums.json", contract="opaque")
def quoted_premiums() -> pl.LazyFrame:
    """quoted_premiums node"""
    from pathlib import Path
    from haute.graph_utils import read_data_source
    df = read_data_source({"sourceType": 'flat_file', "path": str(Path(__file__).parent / "data/competitor_premiums/britsure_premiums.parquet"), 'contract': 'opaque'})
    return df


@pipeline.api_input(config="config/quote_input/quotes.json", contract="opaque")
def quotes() -> pl.LazyFrame:
    """quotes node"""
    from pathlib import Path
    import orjson
    from haute._api_input_schema import validate_v2_schema
    from haute._json_flatten import _json_cache_dir
    from haute._json_shred import (
        is_per_port_cache_valid,
        load_per_port_cache,
    )
    _data_path = Path(__file__).parent / "data/quotes/sample_quote.json"
    _config_path = Path(__file__).parent / "config/quote_input/quotes.json"
    _v2_config = orjson.loads(_config_path.read_bytes())
    _tables = _v2_config.get('tables')
    if not isinstance(_tables, list):
        raise RuntimeError(
            "API Input has no v2 schema (tables[]). Open the node "
            "and click 'Infer Tables' to populate the schema mapping, "
            "then click 'Cache as Parquet'."
        )
    validate_v2_schema(_v2_config)
    _emit_true_tables = [t for t in _tables if t.get('emit')]
    if not _emit_true_tables:
        raise RuntimeError(
            "API Input has no emitting tables. Open the node, tick "
            "the 'emit' toggle on at least one table, then click "
            "'Cache as Parquet' before previewing."
        )
    _emit_labels = [
        t['label']
        for t in _emit_true_tables
        if any(c.get('selected') for c in (t.get('columns') or []))
    ]
    if not _emit_labels:
        _emit_labels_for_err = [t['label'] for t in _emit_true_tables]
        raise RuntimeError(
            "API Input has emit-true tables but none has any selected "
            "columns. Open the node and tick at least one column on "
            f"the emitting table(s): {_emit_labels_for_err}. Then click "
            "'Cache as Parquet' before previewing."
        )
    _cache_dir = _json_cache_dir(str(_data_path), 'working')
    if not is_per_port_cache_valid(_cache_dir, _v2_config, data_path=str(_data_path)):
        _cache_dir = _json_cache_dir(str(_data_path), 'committed')
        if not is_per_port_cache_valid(_cache_dir, _v2_config, data_path=str(_data_path)):
            raise RuntimeError(
                "API Input data hasn't been cached for the current schema, "
                "or the cache is stale. Click 'Cache as Parquet' on the "
                "API Input node to (re)build."
            )
    _bundle = load_per_port_cache(_cache_dir, _v2_config)
    if len(_emit_labels) == 1:
        return _bundle[_emit_labels[0]]
    return {label: _bundle[label] for label in _emit_labels if label in _bundle}


@pipeline.polars(contract="opaque")
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
                years_between(to_date(lic), cover_start).alias(
                    f"additional_driver_{i}_licence_years"
                )
            )
    
    # Youngest driver across proposer + additional drivers
    df = df.with_columns(
        pl.min_horizontal("proposer_age", *ad_age_cols).alias("youngest_driver_age"),
    )
    return df


@pipeline.live_switch(config="config/source_switch/policies.json", contract={'inputs': [], 'outputs': []})
def policies(batch_quotes: pl.LazyFrame, processing: pl.LazyFrame) -> pl.LazyFrame:
    """policies node"""
    return processing


@pipeline.polars(contract="opaque")
def competitor_join(policies: pl.LazyFrame, competitor_insights: pl.LazyFrame) -> pl.LazyFrame:
    """competitor_join node"""
    df = policies
    df = policies.join(competitor_insights, on="quote_id", how="inner")
    return df


@pipeline.model_score(config="config/model_scoring/competitor_scoring.json", contract={'inputs': ['annual_mileage', 'city', 'compulsory_excess', 'cover_type', 'estimated_value', 'insurance_group', 'ncd_years', 'proposer_age', 'proposer_licence_held_years', 'voluntary_excess'], 'outputs': ['competitor_premium']})
def competitor_scoring(policies: pl.LazyFrame) -> pl.LazyFrame:
    """competitor_scoring node"""
    from pathlib import Path
    from haute.graph_utils import score_from_config
    base = str(Path(__file__).parent)
    df = score_from_config(policies, config="config/model_scoring/competitor_scoring.json", base_dir=base)
    return df


@pipeline.banding(config="config/banding/age_veh_banding.json", contract={'inputs': ['channel', 'proposer_age', 'vehicle_age'], 'outputs': ['channel_band', 'proposer_age_band', 'vehicle_age_band']})
def age_veh_banding(policies: pl.LazyFrame) -> pl.LazyFrame:
    """Banding 7 node"""
    return policies


@pipeline.explore(overview={'categorical_summary': True}, contract="opaque")
def Explore_7(policies: pl.LazyFrame) -> pl.LazyFrame:
    """"""
    return policies


@pipeline.modelling(config="config/model_training/avg_top_5.json", contract={'inputs': [], 'outputs': []})
def avg_top_5(competitor_join: pl.LazyFrame) -> pl.LazyFrame:
    """avg_top_5 node"""
    return competitor_join


@pipeline.edge_join(base_input="policies", join_input="competitor_scoring", how="left", on=['quote_id'], suffix="_right", contract="opaque")
def join_scoring(policies: pl.LazyFrame, competitor_scoring: pl.LazyFrame) -> pl.LazyFrame:
    """"""
    return pipeline._apply_edge_join("join_scoring", policies, competitor_scoring)


@pipeline.rating_step(config="config/rating_step/adjustments.json", contract="opaque")
def adjustments(age_veh_banding: pl.LazyFrame) -> pl.LazyFrame:
    """"""
    from pathlib import Path
    from haute.graph_utils import apply_rating_step_from_config
    base = Path(__file__).parent
    df = apply_rating_step_from_config(age_veh_banding, "config/rating_step/adjustments.json", base_dir=base)
    df = df.with_columns(test=pl.col('multiplied') * 2)
    return df


@pipeline.optimiser_apply(config="config/apply_optimisation/apply_ratebook.json", contract="opaque")
def apply_ratebook(age_veh_banding: pl.LazyFrame) -> pl.LazyFrame:
    """apply_ratebook node"""
    return age_veh_banding


@pipeline.polars(contract={'inputs': ['quote_id'], 'outputs': [], 'inputs_by_parent': {'join_scoring': ['competitor_premium', 'quote_id'], 'policy_data': ['policy_id', 'quote_id']}})
def join_policy_data(policy_data: pl.LazyFrame, join_scoring: pl.LazyFrame) -> pl.LazyFrame:
    """Join policy data"""
    df = policy_data
    df = join_scoring.join(policy_data, on="quote_id", how="left")
    return df


@pipeline.polars(contract={'inputs': ['policy_id', 'premium', 'quote_id'], 'outputs': ['burn_cost', 'sale_flag'], 'inputs_by_parent': {'join_policy_data': ['competitor_premium', 'policy_id', 'quote_id'], 'quoted_premiums': ['premium', 'quote_id']}})
def join_premiums(join_policy_data: pl.LazyFrame, quoted_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """Join quoted premiums and derive sale_flag"""
    df = join_policy_data
    df = join_policy_data.join(quoted_premiums, on="quote_id", how="left").with_columns(
        sale_flag=pl.when(pl.col("policy_id").is_null()).then(pl.lit(0)).otherwise(pl.lit(1)),
        burn_cost=pl.col("premium") * 0.7,
    )
    return df


@pipeline.polars(selected_columns=['quote_id', 'sale_flag', 'competitor_premium', 'premium', 'difference_to_market', 'proposer_age', 'cover_type', 'burn_cost'], contract="opaque")
def competitor_features(join_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """competitor_features node"""
    df = join_premiums
    df = join_premiums.with_columns(
        difference_to_market=pl.col("premium") / pl.col("competitor_premium")
    )
    return df


@pipeline.scenario_expander(config="config/expander/premium.json", contract={'inputs': ['premium'], 'outputs': ['premium_multiplier', 'scenario_index']})
def premium(join_premiums: pl.LazyFrame) -> pl.LazyFrame:
    """premium node"""
    df = join_premiums
    df = df.with_columns(premium=pl.col("premium") * pl.col("premium_multiplier"))
    return df


@pipeline.data_sink(config="config/data_sink/conversion_sink.json", contract={'inputs': [], 'outputs': []})
def conversion_sink(competitor_features: pl.LazyFrame) -> pl.LazyFrame:
    """Data Sink 9 node"""
    from pathlib import Path
    from haute._polars_utils import bounded_sink
    bounded_sink(competitor_features, Path(__file__).parent / "output/conversion_data.parquet")
    return competitor_features


@pipeline.modelling(config="config/model_training/conversion.json", contract={'inputs': [], 'outputs': []})
def conversion(competitor_features: pl.LazyFrame) -> pl.LazyFrame:
    """conversion node"""
    return competitor_features


@pipeline.model_score(config="config/model_scoring/conversion_scoring.json", contract={'inputs': ['difference_to_market'], 'outputs': ['conversion_prediction']})
def conversion_scoring(competitor_features_scenarios: pl.LazyFrame) -> pl.LazyFrame:
    """conversion_scoring node"""
    from pathlib import Path
    from haute.graph_utils import score_from_config
    base = str(Path(__file__).parent)
    df = score_from_config(competitor_features_scenarios, config="config/model_scoring/conversion_scoring.json", base_dir=base)
    return df


@pipeline.polars(contract={'inputs': ['burn_cost', 'conversion_prediction', 'premium'], 'outputs': ['expected_margin', 'margin']})
def optimiser_input(conversion_scoring: pl.LazyFrame) -> pl.LazyFrame:
    """Polars 8 node"""
    df = conversion_scoring
    df = conversion_scoring.with_columns(
        margin=pl.col("premium") - pl.col("burn_cost"),
    ).with_columns(
        expected_margin=pl.col("margin") * pl.col("conversion_prediction"),
    )
    return df


@pipeline.optimiser(config="config/optimisation/ratebook_optimiser.json", contract={'inputs': [], 'outputs': []})
def ratebook_optimiser(optimiser_input: pl.LazyFrame, age_veh_banding: pl.LazyFrame) -> pl.LazyFrame:
    """ratebook_optimiser node"""
    return optimiser_input


@pipeline.optimiser(config="config/optimisation/online_optimiser.json", contract={'inputs': [], 'outputs': []})
def online_optimiser(optimiser_input: pl.LazyFrame) -> pl.LazyFrame:
    """online_optimiser node"""
    return optimiser_input


@pipeline.optimiser_apply(config="config/apply_optimisation/apply_online.json", contract="opaque")
def apply_online(optimiser_input: pl.LazyFrame) -> pl.LazyFrame:
    """apply_online node"""
    return optimiser_input


@pipeline.instance(of="competitor_features", contract={'inputs': ['competitor_premium', 'premium'], 'outputs': ['difference_to_market']})
def competitor_features_scenarios(premium: pl.LazyFrame) -> pl.LazyFrame:
    """Instance of competitor_features"""
    return competitor_features(join_premiums=premium)



# Wire nodes together - edges define data flow
pipeline.connect("batch_quotes", "policies")
pipeline.connect("policies", "competitor_join")
pipeline.connect("competitor_insights", "competitor_join")
pipeline.connect("competitor_join", "avg_top_5")
pipeline.connect("policies", "competitor_scoring")
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
pipeline.connect("optimiser_input", "ratebook_optimiser")
pipeline.connect("quotes", "processing")
pipeline.connect("processing", "policies")
pipeline.connect("policies", "age_veh_banding")
pipeline.connect("age_veh_banding", "adjustments")
pipeline.connect("age_veh_banding", "ratebook_optimiser")
pipeline.connect("age_veh_banding", "apply_ratebook")
pipeline.connect("optimiser_input", "online_optimiser")
pipeline.connect("optimiser_input", "apply_online")
pipeline.connect("policies", "Explore_7")
pipeline.connect("policies", "join_scoring", target_port="base")
pipeline.connect("competitor_scoring", "join_scoring", target_port="join")
pipeline.connect("join_scoring", "join_policy_data")
