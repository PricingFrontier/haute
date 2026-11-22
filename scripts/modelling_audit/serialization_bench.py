import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from t_boost import TBoostRegressor

from haute.modelling._staged_runtime import open_stage_bundle

parser = argparse.ArgumentParser(
    description="Compare native model encodings without changing a checkpoint"
)
parser.add_argument("--checkpoint", type=Path, required=True, help="An existing .hstage artifact")
parser.add_argument(
    "--data", type=Path, required=True, help="Parquet containing the model's input features"
)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(parents=True, exist_ok=True)
with open_stage_bundle(args.checkpoint) as stage:
    reference = stage.metadata["staged"]["reference_fit"]
    model = stage.session(reference, raw=True).model("full")
text = model.to_json()
binary = model.to_bytes()
frame = pl.read_parquet(args.data).head(1000)
X = frame.select(list(model.feature_names_in_))
expected = model.predict(X)
results: dict[str, Any] = {}
for label, payload in [("json", text), ("binary", binary)]:
    times = []
    for _ in range(5):
        start = time.perf_counter()
        restored = (
            TBoostRegressor.from_json(text)
            if label == "json"
            else TBoostRegressor.from_bytes(binary)
        )
        times.append(time.perf_counter() - start)
        np.testing.assert_array_equal(restored.predict(X), expected)
    results[label] = {
        "bytes": len(payload),
        "median_seconds": statistics.median(times),
        "runs": times,
    }
results["identical_predictions_rows"] = 1000
(root / "serialization-bench.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
