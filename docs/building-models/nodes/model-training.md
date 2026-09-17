# Model Training

You want to train a machine learning model from your pipeline data. The Model Training
node supports CatBoost (a gradient-boosted tree algorithm) and GLM (generalised linear
model, via RustyStats). Every run records reproducible evaluation evidence and saves a
native model plus its feature contract. A completed result can then be logged to MLflow
as a candidate run and scored by a [Model Score](model-score.md) node.

!!! note "What is MLflow?"
    [MLflow](https://mlflow.org) is an open-source platform for tracking experiments and storing models. If you're new to MLflow, the key concepts are: an **experiment** groups related training runs, a **run** is a single training attempt with its metrics and parameters, and the **model registry** stores production-ready models by name and version.

This node accepts a single input and produces no downstream data—it is a terminal
node. Training in the editor keeps the model and its evaluation artifacts with that
training result; logging it to MLflow and saving it to a project file are explicit
post-training actions in the node's **Export** pane. An exported training script writes
them to `output_dir`.

| Config | Description |
|---|---|
| `name` | **Required.** Model name |
| `target` | **Required.** Target column (the value you're predicting) |
| `weight` | Weight column for weighted training (e.g. exposure) |
| `offset` | Offset column. Under a log link (a log-link GLM, or a CatBoost `Poisson` or `Tweedie` loss) it is a strictly positive exposure multiplier: 2× exposure gives 2× the expected count, and null, zero, or negative values are refused when training and when scoring. Under any other link it is added to the prediction. Different from `weight`, which weights the loss. |
| `exclude` | CatBoost only. Columns to exclude from the model inputs (e.g. identifiers, dates, or target-related columns). All columns except the target, weight, offset, and excluded columns are used as model features. If your data contains ID columns, dates, or columns derived from the target, add them here to prevent data leakage. A GLM's features are its terms and interaction factors instead. |
| `algorithm` | **Required.** `"catboost"` or `"glm"` |
| `task` | **Required.** `"regression"` or `"classification"` |
| `params` | Fixed CatBoost parameters (see below) |
| `evaluation` | **Required.** Version-1 development/validation/final-test workflow (see below) |
| `tuning` | Optional bounded CatBoost search over the evaluation validation fits |
| `metrics` | Evaluation metrics: `"gini"`, `"rmse"`, `"mae"`, `"mse"`, `"r2"`, `"auc"`, `"logloss"`, `"poisson_deviance"`, `"tweedie_deviance"` |
| `mlflow_experiment` | MLflow experiment the Export pane logs to (blank uses the default shown in the field) |
| `mlflow_destination` | `"databricks"` or `"server"` to log to that MLflow destination; leave it out to use the project's local MLflow folder |
| `output_dir` | Folder an exported training script saves trained model files to (e.g. `models/frequency`) |
| `model_export_path` | Filename or path the Export pane's **Save model to file** action writes the trained model to (e.g. `frequency` saves `models/frequency.cbm`); the feature contract is written beside it |
| `row_limit` | Limit the number of rows used for training (randomly sampled) |

!!! tip "Choosing a metric"
    For frequency models (Poisson), use `poisson_deviance`. For severity models (Gamma/Tweedie), use `tweedie_deviance`. For general regression, `rmse` or `gini` are common choices. For classification, use `auc` or `logloss`.

!!! note "Registering and promoting models"
    Haute logs candidate runs but never registers a trained model. Registering a run in the MLflow model registry, and promoting it (for example after comparing it with the current champion and moving an alias), is a separate process outside Haute. A [Model Score](model-score.md) node can then load the registered version or alias, or score a logged run directly.

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
      "target": "claim_count",
      "offset": "exposure",
      "family": "poisson",
      "terms": {
        "driver_age":   { "type": "bs", "df": 5 },
        "vehicle_age":  { "type": "linear", "monotonicity": "decreasing" },
        "area":         { "type": "categorical", "reference": "urban" }
      },
      "interactions": [
        { "factors": ["driver_age", "area"], "specs": {"driver_age": {"type": "linear"}}, "include_main": true }
      ],
      "intercept": true,
      "regularization": "ridge",
      "cv_folds": 5,
      "cv_selection": "min",
      "cv_seed": 42
    }
    ```

    | Field | Description |
    |---|---|
    | `family` | **Required.** `"gaussian"`, `"poisson"`, `"quasipoisson"`, `"binomial"`, `"quasibinomial"`, `"gamma"`, `"tweedie"`, or `"negbinomial"`. |
    | `link` | Leave unset for the family's canonical link (log for Poisson, Quasi-Poisson, Gamma, Tweedie, and Negative Binomial; logit for Binomial and Quasi-Binomial; identity for Gaussian). RustyStats supports only `"identity"`, `"log"`, and `"logit"`: Gaussian, Poisson, Quasi-Poisson, Gamma, Tweedie, and Negative Binomial accept log or identity, and Binomial and Quasi-Binomial accept logit, log, or identity. |
    | `terms` | **Required.** Dict mapping a name to a term spec. A native spec is keyed by the column it fits; an `"expression"` spec (`{"type": "expression", "expr": "age ** 2"}`) or a second encoding (`{"type": "frequency_encoding", "variable": "area"}`) is keyed by a name that is not a column. |
    | `interactions` | Each has `factors` (two or more columns) and `include_main`. See **Interactions** below. |
    | `regularization` | `"ridge"`, `"lasso"`, or `"elastic_net"`. Cannot be combined with automatically smoothed splines. |
    | `alpha` | A positive number fixes the penalty. Leave it unset (or 0) to choose the penalty by cross-validation. |
    | `cv_folds`, `cv_selection`, `cv_seed` | Required when the penalty is cross-validated: folds from 2 to 20, `"min"` (lowest deviance) or `"1se"` (the largest penalty within one standard error), and a non-negative seed so the same data selects the same penalty. |
    | `l1_ratio` | **Required** for elastic net: 0 fits ridge, 1 fits lasso. |
    | `max_iter`, `tol` | Optional solver settings: maximum iterations (1 to 10000) and convergence tolerance (between 0 and 1). Leave unset for RustyStats' defaults. |
    | `robust_standard_errors` | Optional `"HC0"`, `"HC1"`, `"HC2"`, or `"HC3"` heteroskedasticity-robust standard errors. Cannot be combined with regularization, monotonicity constraints, or automatically smoothed splines, whose standard errors are not valid. |
    | `intercept` | Whether to fit an intercept. Defaults to true. |
    | `var_power` | **Required** for Tweedie: from 1 (Poisson) to 2 (Gamma). **Estimate from data** profiles it on the node's training data. |
    | `theta` | **Required** for Negative Binomial: a positive dispersion. RustyStats refuses to fit without it; **Estimate from data** profiles the likelihood on the node's training data. |

    `exclude`, `feature_columns`, `monotone_constraints`, and `feature_weights` apply only to CatBoost and are refused for a GLM.

    **Terms.** Each column's dtype decides which fits it offers:

    | Column dtype | Fits | Default |
    |---|---|---|
    | Float, Decimal | Linear, B-spline (`bs`), natural spline (`ns`), monotone spline (`ms`); numeric expressions | Linear |
    | Integer | The float fits plus Categorical, Target encoding, and Frequency encoding | Linear |
    | Boolean | Categorical, Target encoding, Frequency encoding | Categorical |
    | String, Categorical, Enum | Categorical, Target encoding, Frequency encoding | Categorical |

    Dates, times, durations, lists, structs, and binary columns cannot be fitted; the **Features** pane hides them and says how many it hid. Target, weight, offset, fold, identifier, and evaluation-key columns cannot be terms, expression columns, or interaction factors.

    Spline fits offer **Auto / Fixed** degrees of freedom. Auto leaves `df` unset so RustyStats chooses the smoothing (the Summary lists each smooth term's effective degrees of freedom); Fixed stores `df`, an integer up to 20 and at least `degree + 1` for B-splines (degree 3 unless set) or at least 2 for natural and monotone splines. Advanced controls expose the basis size `k` (Auto only), interior `knots` (which replace `df`), and `boundary_knots`; set only one of `df`, `k`, and `knots`. Linear, B-spline, monotone spline, and expression terms accept `monotonicity` (`"increasing"` or `"decreasing"`).

    A categorical fit shares the intercept with its first level in sorted order. Set `reference` to choose that baseline level, or `levels` to fit indicators only for the listed levels; they replace each other, and the rest (and levels unseen at scoring) share the intercept. Training refuses a reference or listed level the data does not contain and lists the observed labels. Quote numeric labels exactly as the column holds them, for example `"2"` for an integer column (or `"2.0"` if it also has nulls).

    Target encoding offers **Auto / Fixed** prior weight (Auto leaves `prior_weight` unset; Fixed accepts a non-negative value) and, under Advanced, `n_permutations` from 1 to 100 (default 4).

    Expressions have one of the forms `x`, `x ** n`, `x + y`, `x - y`, `x * y`, or `x / y`, where `y` is a column or a number. Columns in an expression must be numeric and named with letters, digits, and underscores, starting with a letter or underscore; compute logs and other transforms in an upstream Polars node.

    Terms that cannot be fitted (a role column, a column no longer upstream, an unsupported dtype, or an entry with no fit type) are listed under **Unresolved terms** with the reason. Fix or remove them before training.

    **Interactions.** In a **Product** interaction each feature fits as:

    1. its override in `specs` (`linear`, `categorical`, `bs`, `ns`, or `target_encoding`), if set;
    2. otherwise its main-effect term, when it has exactly one that interactions honour;
    3. otherwise its dtype default.

    A monotone spline, a monotone linear or B-spline term, a categorical term with `levels` or `reference`, a frequency encoding, or several main-effect terms for one feature cannot be used inside an interaction, so the card asks for an explicit fit. Categorical fits apply only over a categorical main effect, and linear, spline, and target-encoding fits never do. A categorical feature may use **Target enc.** when every other feature is **Linear**; this always adds the target-encoded main effect, even with **Include main effects** off. Different cards may fit the same feature with different local splines.

    **Include main effects** adds a main effect for each feature that has none, using the fit its cards agree on; the **Features** pane tags that feature "Main effect from Interaction N". Cards that would add different main effects for one feature are refused together: add a main term for it, or give the cards the same fit. Features with a term keep it.

    To target-encode a combination such as brand and region, select **Target encoding** on the interaction card:

    ```json
    {
      "factors": ["brand", "region"],
      "encoding": "target_encoding",
      "include_main": false
    }
    ```

    Joint encodings accept integer, boolean, and categorical features, use the raw columns, and accept `prior_weight` and `n_permutations`. Product, target-encoded, and frequency-encoded interactions can coexist over the same factors; a duplicate within one mode is refused.

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
- A GLM shows its fit statistics, the penalty actually applied (with the folds, rule,
  and seed when it was cross-validated), and each automatic spline's effective degrees
  of freedom. Standard errors and p-values are valid only for an unpenalised,
  unconstrained fit without automatic splines; otherwise the **Coefficients** view
  shows dashes and says why. Relativities (exponentiated coefficients) exist only for
  log-link models, and a relativity too large to compute is reported as a diagnostic
  error naming the terms.

The model, feature contract, evaluation plan/results/report, and optional tuning
plan/trials/report are published together as that training result's own files, so a
later training run never changes what an earlier result exports. MLflow logging
attaches the same evidence and selected final parameters to one final run.

## Exporting a trained model

The **Export** pane acts on the node's last completed training result:

- **MLflow logging** chooses the destination and experiment path, then **Log run to
  MLflow** logs the run. Nothing is logged automatically. The destination is the
  project's local MLflow folder unless you choose Databricks or an MLflow server; a node
  keeps its choice even when other destinations are configured later. If a chosen remote
  cannot be reached or rejects its credentials, the pane shows why with a link to test
  the connection in MLflow settings, and nothing is logged anywhere else instead.
- **Model file** writes a copy of the trained model and its feature contract to a file
  in the project with **Save model to file**, the same way a Data Output writes a file.
  A bare filename saves in the project's `models/` folder, paths are relative to the
  project root, and the model's extension (`.cbm` for CatBoost, `.rsglm` for GLM) is added
  if you leave it off. The pane shows the destination before you save, and asks before
  replacing a file that already exists.

A logged run follows Haute's candidate-run contract so a separate promotion process can
find and compare it: tags such as `haute.node_id`, `haute.trained_at` and
`haute.evaluation_plan_sha256`, metrics named by evaluation set (`final_test_gini`,
`development_gini`, `selection_gini_mean`), and the model, feature contract and evaluation
evidence as artifacts.

Both actions stay disabled until the model has been trained and while a new training run
is in progress. If you change training settings after training, the pane warns that
exports still use the last trained model.

The pane remembers where the result was last logged and saved, including after you reload the
page while the server still holds the training result. Logging a result that is already logged
asks first and creates a new run; if a log fails because the response never arrived, **Retry**
repeats the same log rather than risking a duplicate run. After a server restart the pane says the
result is no longer available and asks you to train again.

**See also:**

- [Model Score](model-score.md)  - to score data with your trained model
