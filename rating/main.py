"""Pipeline: my_pipeline"""

import polars as pl
import haute

pipeline = haute.Pipeline("my_pipeline", description='')


@pipeline.api_input()
def quotes() -> pl.LazyFrame | dict[str, pl.LazyFrame]:
    """quotes node"""
    from pathlib import Path
    from haute.graph_utils import resolve_api_input_from_config
    return resolve_api_input_from_config(
        "config/quote_input/quotes.json", base_dir=Path(__file__).resolve().parent
    )


@pipeline.output(config="config/quote_response/Quote_Response_9.json", contract={'inputs': ['driver_id', 'policy_id', 'type', 'vehicle_id'], 'outputs': []})
def Quote_Response_9(quotes: pl.LazyFrame, quotes_2: pl.LazyFrame, quotes_3: pl.LazyFrame, quotes_4: pl.LazyFrame) -> pl.LazyFrame:
    """"""
    return quotes


# Wire nodes together - edges define data flow
pipeline.connect("quotes", "Quote_Response_9", source_port="quotes")
pipeline.connect("quotes", "Quote_Response_9", source_port="drivers")
pipeline.connect("quotes", "Quote_Response_9", source_port="vehicles")
pipeline.connect("quotes", "Quote_Response_9", source_port="licenses")
