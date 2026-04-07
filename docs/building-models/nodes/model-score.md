# Model Score

You've trained a model and registered it in MLflow. The Model Score node loads it, casts your data to the types the model expects, and produces predictions  - all without writing scoring code. The model is cached and reloaded automatically when the file changes on disk.

!!! note "What is MLflow?"
    MLflow is an open-source platform for tracking model experiments and storing trained models  - think of it as version control for models. If your team has set up MLflow (or uses Databricks, which includes it), your trained models are stored in a model registry where they can be loaded by version.

!!! info "When to use"
    - Your models are managed in MLflow with versioning and a model registry.
    - You want automatic feature type casting and model caching.
    - If your model is a standalone file not in MLflow, use [External File](external-file.md) instead.

This node accepts a single input.

| Config | Description |
|---|---|
| `sourceType` | **Required.** `"registered"` (from model registry) or `"run"` (from a specific experiment run) |
| `registered_model` | Model name in the registry. Required when sourceType is `"registered"`. |
| `version` | Version number or `"latest"`. Required when sourceType is `"registered"`. |
| `experiment_id` | MLflow experiment ID. Required when sourceType is `"run"`. |
| `run_id` | MLflow run ID. Required when sourceType is `"run"`. |
| `artifact_path` | Path to the model artifact within the run. Required when sourceType is `"run"`. |
| `task` | **Required.** `"regression"` or `"classification"` |
| `output_column` | Name for the prediction column. Defaults to `"prediction"`. |
| `code` | Post-scoring transformation code  - useful for deriving columns from the prediction (e.g. `expected_claims = prediction * exposure`). |

### Example configuration

The most common setup  - loading a registered model for regression:

```json
{
  "sourceType": "registered",
  "registered_model": "frequency_model",
  "version": "latest",
  "task": "regression",
  "output_column": "predicted_frequency"
}
```

!!! tip "Registered vs run"
    Use `"registered"` if your model has been published to the model registry  - this is the most common setup. Use `"run"` to load a model from a specific training experiment, which is useful during development before a model is formally registered.

### Task type

Use `regression` when your model predicts a number (frequency, severity, premium). Use `classification` when your model predicts a category or probability (e.g. likelihood of claim, fraud detection).

### Post-scoring code

The `code` field lets you transform the predictions after scoring. The prediction is already in the `output_column` (e.g. `"predicted_frequency"`):

```python
# The prediction is already in the output_column (e.g. "predicted_frequency")
df = df.with_columns(
    (pl.col("predicted_frequency") * pl.col("exposure")).alias("expected_claims")
)
return df
```

### Instances

Instances let you reuse the same scoring configuration with different inputs  - for example, scoring the same model against both training and validation data. See [Instances](instances.md) for full details.

**See also:**

- [External File](external-file.md)  - for standalone model files not managed in MLflow
- [Model Training](model-training.md)  - to train models that can be scored here
