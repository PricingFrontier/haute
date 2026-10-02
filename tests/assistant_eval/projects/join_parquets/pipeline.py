"""Pipeline: join_parquets"""

import haute

pipeline = haute.Pipeline("join_parquets")


@pipeline.data_input(config="config/data_input/nb_batch.json")
def nb_batch(): ...


@pipeline.data_input(config="config/data_input/competitor_insight.json")
def competitor_insight(): ...
