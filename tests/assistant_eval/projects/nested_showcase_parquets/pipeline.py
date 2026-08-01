"""Empty batch pipeline for nested Parquet showcase discovery."""

import haute

pipeline = haute.Pipeline(
    "nested_showcase_parquets",
    description="Build a coherent batch pipeline from nested local Parquet data.",
)
