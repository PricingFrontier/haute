"""Pipeline: claims_join"""

import haute
import polars as pl

pipeline = haute.Pipeline(
    "claims_join", description="Quotes with their claims joined on, and a loss ratio."
)


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes(): ...


@pipeline.data_input(config="config/data_input/claims.json")
def claims(): ...


@pipeline.edge_join(how="left", on=["policy_id"], validate="m:1")
def quote_claims(quotes, claims): ...


@pipeline.polars(config="config/polars/loss_ratio.json")
def loss_ratio(quote_claims: pl.LazyFrame) -> pl.LazyFrame:
    df = quote_claims
    # Divide each quote's incurred claims by its premium
    df = df.with_columns(loss_ratio=pl.col('total_incurred') / pl.col('premium'))
    return df


# Wire nodes together - edges define data flow
pipeline.connect("quotes", "quote_claims", target_port="base")
pipeline.connect("claims", "quote_claims", target_port="join")
pipeline.connect("quote_claims", "loss_ratio")
