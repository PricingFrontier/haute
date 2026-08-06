"""Ordinary held-out pricing project; no assistant-specific context artifact."""

import polars as pl

import haute

pipeline = haute.Pipeline("heldout_pricing")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    return pl.scan_csv("data/quotes.csv")
