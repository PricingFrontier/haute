"""Pipeline: reference"""

from pathlib import Path as _HautePath

import polars as pl
import haute

pipeline = haute.Pipeline("reference", description='Runnable reference pipeline over synthetic quotes.')

_HAUTE_CONFIG_BASE = _HautePath(__file__).resolve().parent


@pipeline.data_input(config="config/data_input/quotes.json", contract="opaque")
def quotes() -> pl.LazyFrame:
    """Read the synthetic quote rows."""
    from haute._project import get_project_root
    from haute.graph_utils import resolve_data_input_from_config
    project_root = get_project_root(_HAUTE_CONFIG_BASE)
    df = resolve_data_input_from_config(
        "config/data_input/quotes.json", base_dir=_HAUTE_CONFIG_BASE, project_root=project_root
    )
    return df


@pipeline.polars(contract="opaque")
def features(quotes: pl.LazyFrame) -> pl.LazyFrame:
    """Derive the rating features."""
    df: pl.LazyFrame
    df = quotes.with_columns(
        vehicle_age=2026 - pl.col("vehicle_year"),
        driver_band=pl.col("driver_age").cut([25, 40, 65]).cast(pl.String),
    )
    return df


@pipeline.output(config="config/quote_response/priced.json", contract={"inputs": ['driver_band', 'quote_id', 'sum_insured', 'vehicle_age'], "outputs": []})
def priced(features: pl.LazyFrame) -> pl.LazyFrame:
    """Return the priced quote rows."""
    from haute.graph_utils import assemble_output_from_config
    return assemble_output_from_config(
        features,
        config="config/quote_response/priced.json",
        base_dir=_HAUTE_CONFIG_BASE,
        source_names=['features'],
    )



# Wire nodes together - edges define data flow
pipeline.connect("quotes", "features")
pipeline.connect("features", "priced")
