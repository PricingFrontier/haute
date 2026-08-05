"""Empty batch pipeline for the trace-derived parquet showcase prompt."""

import haute

pipeline = haute.Pipeline(
    "showcase_parquets", description="Build a coherent batch pipeline from local parquet data."
)
