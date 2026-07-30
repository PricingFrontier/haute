# Model Training

You want to train a machine learning model from your pipeline data. The Model Training
node supports CatBoost (a gradient-boosted tree algorithm) and GLM (generalised linear
model, via RustyStats). Every run records reproducible evaluation evidence and saves a
native model plus its feature contract. A completed result can then be logged to MLflow
and picked up by a [Model Score](model-score.md) node.

!!! note "What is MLflow?"
    [MLflow](https://mlflow.org) is an open-source platform for tracking experiments and storing models. If you're new to MLflow, the key concepts are: an **experiment** groups related training runs, a **run** is a single training attempt with its metrics and parameters, and the **model registry** stores production-ready models by name and version.

This node accepts a single input and produces no downstream data—it is a terminal
node. Training writes the model and its evaluation artifacts to `output_dir`; MLflow
logging is an explicit post-training action.

| Config | Description |
|---|---|
| `name` | **Required.** Model name |
| `target` | **Required.** Target column (the value you're predicting) |
| `weight` | Weight column for weighted training (e.g. exposure) |
| `exclude` | Columns to exclude from the model inputs (e.g. identifiers, dates, or target-related columns). All columns except the target, weight, and excluded columns are used as model features. If your data contains ID columns, dates, or columns derived from the target, add them here to prevent data leakage. |
| `algorithm` | **Required.** `"catboost"` or `"glm"` |
| `task` | **Required.** `"regression"` or `"classification"` |
| `params` | Fixed CatBoost parameters (see below) |
| `evaluation` | **Required.** Version-1 development/validation/final-test workflow (see below) |
| `tuning` | Optional bounded CatBoost search over the evaluation validation fits |
| `metrics` | Evaluation metrics: `"gini"`, `"rmse"`, `"mae"`, `"mse"`, `"r2"`, `"auc"`, `"logloss"`, `"poisson_deviance"`, `"tweedie_deviance"` |
| `mlflow_experiment` | MLflow experiment name for tracking training runs |
| `model_name` | Name for the model registry (makes the model available to [Model Score](model-score.md) nodes) |
| `output_dir` | Folder where trained model files are saved (e.g. `models/frequency`) |
| `row_limit` | Limit the number of rows used for training (randomly sampled) |

!!! tip "Choosing a metric"
    For frequency models (Poisson), use `poisson_deviance`. For severity models (Gamma/Tweedie), use `tweedie_deviance`. For general regression, `rmse` or `gini` are common choices. For classification, use `auc` or `logloss`.

!!! tip "`name` vs `model_name`"
    `name` is a display label for the node on the canvas. `model_name` is the name under which the trained model is registered in MLflow  - this is what you reference in a [Model Score](model-score.md) node downstream.

## Feature selection and validation

Feature selection is explicit. When you provide an explicit feature list, Haute
uses exactly those named feature columns. Without one, it uses the
schema-derived **all-except** set: every available column except the target,
weight, and columns in `exclude`.

The training metadata needed to identify the run is retained separately from
model features. Target and weight are excluded because they have training
roles; `exclude` is for identifiers, dates, leakage-prone fields, and any
other columns you deliberately do not want the model to learn from. The
feature-selection diagnostics show the final ordered feature set (or count),
retained metadata, and every excluded column with its reason.

Haute validates feature selection before collecting training data. A missing,
invalid, or unsuitable feature therefore fails clearly before a large eager
collection begins. See [Execution Strategy](../execution-strategy.md) for the
schema all-except strategy and for reading execution diagnostics.

## Evaluation configuration

Every node has one versioned `evaluation` object. It separates three roles:

- **Development data** is available for model selection and the final refit.
- **Validation data** estimates candidate settings. Choose a single validation set,
  cross-validation, or no validation.
- An optional **final test** remains unseen until model selection is complete and is
  evaluated exactly once.

The retired top-level `split` and `cross_validation` fields are not accepted.

### Random rows

This example reserves 20% of source rows as an untouched final test, uses five
development-only validation folds, then refits once on all development rows:

```json
{
  "evaluation": {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "test": {"size": 0.2},
    "validation": {
      "method": "cross_validation",
      "fold_count": 5
    }
  }
}
```

Random classification is stratified by the target; regression is seeded but
unstratified. Fractions are source-relative numbers from 0 (inclusive) to 1
(exclusive), and every requested partition must contain rows.

### Keep entities together

Use group evaluation when rows for the same customer, policyholder, household, or
claim must never appear in different partitions:

```json
{
  "evaluation": {
    "schema_version": 1,
    "strategy": "group",
    "group_column": "policyholder_id",
    "seed": 42,
    "test": {"size": 0.2},
    "validation": {"method": "single", "size": 0.2}
  }
}
```

The planner assigns complete groups while balancing row counts. The group column is
retained as evaluation metadata and is not offered as a model feature.

### Respect time order

Temporal evaluation uses explicit boundaries and never places a later date in a
validation fit's training data:

```json
{
  "evaluation": {
    "schema_version": 1,
    "strategy": "temporal",
    "date_column": "policy_start_date",
    "test": {"start": "2025-01-01"},
    "validation": {
      "method": "cross_validation",
      "fold_count": 5,
      "window": "expanding"
    }
  }
}
```

For a single temporal validation set, use
`{"method": "single", "start": "2024-07-01"}`. Equal dates always stay together;
null or invalid dates fail with an actionable error.

### Validation choices

| Shape | Behaviour |
|---|---|
| `{"method": "none"}` | No candidate validation; perform one final fit. Tuning is unavailable. |
| `{"method": "single", "size": 0.2}` | One random/group validation fit using a source-relative fraction. |
| `{"method": "single", "start": "2024-07-01"}` | One temporal validation fit at an explicit boundary. |
| `{"method": "cross_validation", "fold_count": 5}` | Two to ten random/group validation fits. |
| `{"method": "cross_validation", "fold_count": 5, "window": "expanding"}` | Two to ten expanding temporal validation fits. |

When the evaluation fields are complete, the Split pane shows the exact planned
development/final-test counts and validation-fit bounds before training.

## Optional CatBoost tuning

CatBoost can tune a bounded search space on the exact validation plan above. Trial
zero is always the current fixed `params`, so the search must beat the model you would
otherwise train.

```json
{
  "tuning": {
    "schema_version": 1,
    "trial_count": 20,
    "seed": 42,
    "metric": "gini",
    "search_space": {
      "depth": [4, 6, 8, 10],
      "learning_rate": [0.01, 0.03, 0.05, 0.1, 0.2],
      "grow_policy": ["SymmetricTree", "Depthwise"],
      "min_data_in_leaf": {
        "choices": [10, 25, 50, 100],
        "when": {"grow_policy": ["Depthwise"]}
      }
    }
  }
}
```

`trial_count` includes the baseline and must be 5–50. Total trial-validation fits may
not exceed 200. Tuning requires single validation or cross-validation, supports
CatBoost only, and evaluates every trial sequentially with a deterministic seeded
sampler. The selected parameters are refitted once on all development data; the final
test, when configured, is then evaluated once.

For an ordinary search entry, list every candidate value directly. Values retain their
JSON type, so CatBoost receives numbers, strings, or Booleans exactly as written. A
conditional entry uses `{"choices": [...], "when": {...}}` instead. The backend rejects
unknown fields, lists outside two through fifty distinct finite values, invalid or
cyclic conditions, and orchestration-owned keys such as `iterations`, loss/objective,
device, callbacks, write directories, or random seed.

??? info "CatBoost parameters"
    Fixed constructor parameters are passed via `params`; objective and
    cross-cutting settings remain top-level:

    ```json
    {
      "loss_function": "RMSE",
      "params": {
        "iterations": 500,
        "depth": 6,
        "learning_rate": 0.1,
        "early_stopping_rounds": 50
      },
      "monotone_constraints": {
        "vehicle_age": 1
      }
    }
    ```

    | Field | Description |
    |---|---|
    | `loss_function` | **Required.** CatBoost loss name, such as `"RMSE"`, `"Poisson"`, or `"Tweedie"` |
    | `variance_power` | Required top-level Tweedie power when `loss_function` is `"Tweedie"` |
    | `params.iterations` | Maximum boosting rounds; tuning uses this as its ceiling |
    | `params.depth` | Tree depth |
    | `params.learning_rate` | Step-size shrinkage—smaller values are slower but often more accurate |
    | `params.early_stopping_rounds` | Stop a validation fit when its metric stops improving |
    | `monotone_constraints` | Top-level `-1`/`1` constraints for selected numeric features |

??? info "GLM parameters"
    GLM-specific fields are set directly on the node config (not inside `params`). Here's a complete example of a Poisson frequency model:

    ```json
    {
      "algorithm": "glm",
      "task": "regression",
      "target": "claim_frequency",
      "weight": "exposure",
      "family": "poisson",
      "link": "log",
      "terms": {
        "driver_age":   { "type": "linear" },
        "vehicle_age":  { "type": "linear" },
        "area":         { "type": "categorical" }
      },
      "interactions": [
        { "factors": ["driver_age", "vehicle_age"], "include_main": true }
      ],
      "intercept": true,
      "regularization": "ridge",
      "alpha": 0.01
    }
    ```

    | Field | Description |
    |---|---|
    | `terms` | Dict mapping feature names to term specs. Each has a `type` (`"linear"`, `"categorical"`, `"poly"`, `"spline"`) and optional `monotonicity` (`"increasing"` or `"decreasing"`). If omitted, terms are inferred from data types. |
    | `family` | **Required.** Distribution family: `"gaussian"`, `"poisson"`, `"tweedie"`, etc. |
    | `link` | Link function: `"log"`, `"identity"`, etc. Defaults to the canonical link for the family. |
    | `offset` | Offset column  - a fixed term added to the linear predictor (e.g. log-exposure in a Poisson frequency model). This is different from `weight`: the `weight` field is an observation weight used in the loss function, so rows with higher weight have more influence on the model. Most frequency models use `weight` for exposure and do not need `offset`. |
    | `interactions` | Interaction terms  - each has `factors` (list of feature names) and `include_main` (bool) |
    | `regularization` | `"ridge"`, `"lasso"`, or `"elastic_net"` |
    | `alpha` | Regularization strength |
    | `l1_ratio` | Elastic net mixing parameter (0 = pure ridge, 1 = pure lasso) |
    | `intercept` | Whether to fit an intercept. Defaults to true. |
    | `var_power` | Variance power for Tweedie distributions |

## Reading the result

The Summary view keeps model-selection evidence distinct from final performance:

- **Selection estimates** are the single-validation or cross-validation metrics used
  to compare fixed/tuned candidates. Cross-validation summaries are weighted by the
  number of validation rows in each fit.
- **Final-test metrics** appear only when an untouched final test was configured.
- **Development diagnostics** are shown when no final test exists; they are labelled
  as development diagnostics and are not presented as out-of-sample performance.
- A tuned run shows the baseline, winning trial, improvement, selected parameters,
  final tree count, and exact total fit count.

The model, feature contract, evaluation plan/results/report, and optional tuning
plan/trials/report are published as one transactional generation. MLflow logging
attaches the same evidence and selected final parameters to one final run.

**See also:**

- [Model Score](model-score.md)  - to score data with your trained model
