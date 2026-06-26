"""Pipeline: my_pipeline"""

import polars as pl
import haute

pipeline = haute.Pipeline("my_pipeline", description='')


@pipeline.api_input(config="config/quote_input/quotes.json", contract="opaque")
def quotes() -> pl.LazyFrame:
    """quotes node"""
    from pathlib import Path
    import orjson
    from haute._api_input_schema import validate_v2_schema
    from haute._json_shred import load_v2_api_source
    _data_path = Path(__file__).parent / "data/quotes/nest_example.json"
    _config_path = Path(__file__).parent / "config/quote_input/quotes.json"
    _v2_config = orjson.loads(_config_path.read_bytes())
    if not isinstance(_v2_config.get('tables'), list):
        raise RuntimeError(
            "API Input has no v2 schema (tables[]). Open the node "
            "and click 'Infer Tables' to populate the schema mapping, "
            "then click 'Cache as Parquet'."
        )
    validate_v2_schema(_v2_config)
    return load_v2_api_source(str(_data_path), _v2_config)


@pipeline.output(config="config/quote_response/Quote_Response_9.json", contract={'inputs': ['driver_id', 'policy_id', 'type', 'vehicle_id'], 'outputs': []})
def Quote_Response_9(quotes: pl.LazyFrame, quotes_2: pl.LazyFrame, quotes_3: pl.LazyFrame, quotes_4: pl.LazyFrame) -> pl.LazyFrame:
    """"""
    return quotes


# Wire nodes together - edges define data flow
pipeline.connect("quotes", "Quote_Response_9", source_port="quotes")
pipeline.connect("quotes", "Quote_Response_9", source_port="drivers")
pipeline.connect("quotes", "Quote_Response_9", source_port="vehicles")
pipeline.connect("quotes", "Quote_Response_9", source_port="licenses")
