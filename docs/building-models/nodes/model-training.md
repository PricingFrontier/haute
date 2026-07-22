# Model Training

You want to train a machine learning model from your pipeline data. The Model Training node supports CatBoost (a gradient-boosted tree algorithm) and GLM (generalised linear model, via RustyStats). Results are logged to MLflow and can be picked up downstream by a [Model Score](model-score.md) node.

!!! note "What is MLflow?"
    [MLflow](https://mlflow.org) is an open-source platform for tracking experiments and storing models. If you're new to MLflow, the key concepts are: an **experiment** groups related training runs, a **run** is a single training attempt with its metrics and parameters, and the **model registry** stores production-ready models by name and version.

This node accepts a single input and produces no downstream data  - it's a terminal node. It saves a trained model to MLflow (and optionally to disk) but does not pass data to downstream nodes.

| Config | Description |
|---|---|
| `name` | **Required.** Model name |
| `target` | **Required.** Target column (the value you're predicting) |
| `weight` | Weight column for weighted training (e.g. exposure) |
| `exclude` | Columns to exclude from the model inputs (e.g. identifiers, dates, or target-related columns). All columns except the target, weight, and excluded columns are used as model features. If your data contains ID columns, dates, or columns derived from the target, add them here to prevent data leakage. |
| `algorithm` | **Required.** `"catboost"` or `"glm"` |
| `task` | **Required.** `"regression"` or `"classification"` |
| `params` | Algorithm settings (see below) |
| `split` | Train/validation split configuration (see below) |
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

## Split configuration

Controls how data is divided for training and validation.

```json
{
  "strategy": "random",
  "validation_size": 0.2,
  "seed": 42
}
```

| Field | Description |
|---|---|
| `strategy` | **Required.** `"random"`, `"temporal"` (split by date), or `"group"` (split by group column) |
| `validation_size` | **Required.** Fraction held out for validation (0 to 1) |
| `holdout_size` | Additional holdout fraction. Defaults to 0. |
| `seed` | Random seed for reproducibility |
| `date_column` | Column to split on. Required for `"temporal"`. |
| `cutoff_date` | ISO date string for the split point (e.g. `"2024-01-01"`). Required for `"temporal"`. |
| `group_column` | Column to group by (e.g. `policy_id`). Required for `"group"`. |

??? info "CatBoost parameters"
    Passed via the `params` field. Common options:

    ```json
    {
      "iterations": 500,
      "depth": 6,
      "learning_rate": 0.1,
      "loss_function": "RMSE",
      "early_stopping_rounds": 50
    }
    ```

    | Param | Description |
    |---|---|
    | `iterations` | Number of boosting rounds |
    | `depth` | Tree depth |
    | `learning_rate` | Step size shrinkage  - smaller values are slower but often more accurate |
    | `loss_function` | CatBoost loss function name (e.g. `"RMSE"`, `"Poisson"`, `"Tweedie:variance_power=1.5"`) |
    | `early_stopping_rounds` | Stop if the validation metric doesn't improve for this many rounds |
    | `monotone_constraints` | Monotonicity constraints per feature  - force a feature to only increase or decrease the prediction |
    | `feature_weights` | Per-feature importance weights |

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

**See also:**

- [Model Score](model-score.md)  - to score data with your trained model
