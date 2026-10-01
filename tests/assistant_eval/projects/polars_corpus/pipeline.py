"""Pipeline: polars_corpus"""

import haute

pipeline = haute.Pipeline("polars_corpus")


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes(): ...


@pipeline.data_input(config="config/data_input/rates.json")
def rates(): ...
