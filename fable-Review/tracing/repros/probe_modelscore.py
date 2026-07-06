# Model score enrichment: feature list inference lists ALL input columns
from haute._trace_enrichment import enrich_model_score

# Config with no feature_columns and no contract -> inference path
config = {"output_column": "prediction", "sourceType": "run", "run_id": "abc", "task": "regression"}
input_row = {"quote_id": "Q123", "policy_ref": "P9", "driver_age": 40, "vehicle_value": 20000, "prediction": None}
output_row = {"prediction": 0.87}

# Force explain to fail cleanly (no real model) so we see the inference
detail = enrich_model_score(config, input_row, output_row)
print("feature_columns:", detail.get("feature_columns"))
print("feature_values:", detail.get("feature_values"))
print("-> Note quote_id/policy_ref (technical cols) reported as model features:",
      "quote_id" in detail.get("feature_columns", []))
