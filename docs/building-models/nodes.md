# Node Types

Every step in a Haute pipeline is a node. You connect nodes on the canvas to define how data flows from source to output. This page describes each node type, what it does, and how to configure it.

!!! tip "First pipeline?"
    If you're building your first pipeline, a common path is: [Quote Input](#quote-input) or [Data Source](#data-source) → [Polars](#polars) (clean your data) → [Banding](#banding) and [Rating Step](#rating-step) (build your rating structure) → [Output](#output). You don't need every node type to get started.

!!! info "About the config examples"
    The JSON examples on this page show the underlying configuration. In the Haute UI, you configure most of these through forms, dropdowns, and editable tables — you don't need to write JSON by hand.

---

## Quick reference

| I want to... | Use this node |
|---|---|
| Bring in quote data for live pricing | [Quote Input](#quote-input) |
| Load a CSV, parquet file, or Databricks table | [Data Source](#data-source) |
| Store fixed parameters (tax rate, loadings) | [Constant](#constant) |
| Join, filter, or calculate new columns | [Polars](#polars) |
| Convert ages or values into bands | [Banding](#banding) |
| Look up rating factors from a table | [Rating Step](#rating-step) |
| Score data with a trained model | [Model Score](#model-score) or [External File](#external-file) |
| Train a new model | [Model Training](#model-training) |
| Optimise prices subject to constraints | [Scenario Expander](#scenario-expander) + [Optimiser](#optimiser) |
| Apply saved optimisation results | [Optimiser Apply](#optimiser-apply) |
| Switch between live and batch data | [Source Switch](#source-switch) |
| Choose which columns to return from the API | [Output](#output) |
| Save results to a file | [Data Sink](#data-sink) |
| Group nodes into a reusable block | [Submodel](#submodel) |

---

## Inputs

These nodes bring data into your pipeline. They have no upstream connections.

### Quote Input

Your pipeline will receive live API requests in production. During development, you need realistic data to build and test against. The Quote Input node handles both — it's the entry point for live pricing, and it reads a preview file so you can work with sample data on your machine.

| Config | Description |
|---|---|
| `path` | **Required.** Path to a `.json` or `.jsonl` preview file, relative to your project folder (e.g. `data/quotes.json`) |
| `row_id_column` | Column that uniquely identifies each quote or record, e.g. `quote_id` or `policy_number` |

The preview file should match the shape of the requests your deployed pipeline will receive. Nested JSON fields are automatically flattened into dot-notation columns like `proposer.date_of_birth` — see [Preparing Your Data](preparing-your-data.md) for how to clean these up.

!!! warning "One per pipeline"
    You can only have one Quote Input node in a pipeline.

---

### Data Source

You have tabular data you want to bring into your pipeline — historical policies, external enrichment data, lookup tables. The Data Source node reads flat files (parquet or CSV) or Databricks tables.

!!! info "When to use"
    - Loading historical data for analysis or model training.
    - Bringing in reference data to join with your quotes (e.g. postcode lookups, external scores).
    - Use [Quote Input](#quote-input) instead when building the live API entry point.

| Config | Description |
|---|---|
| `sourceType` | **Required.** `"flat_file"` or `"databricks"` |
| `path` | File path (parquet or CSV). Required when sourceType is `"flat_file"`. |
| `table` | Databricks table name (`catalog.schema.table`). Required when sourceType is `"databricks"`. |
| `http_path` | Databricks SQL warehouse HTTP path (e.g. `/sql/1.0/warehouses/abc123`). Your Databricks administrator can provide this. |
| `query` | SQL query to filter or transform the data before it enters the pipeline. |
| `code` | Polars code applied after loading — the loaded data is available as `df`. Useful for filtering large datasets before they enter the graph. See [Polars](#polars) for code examples. |

---

### Constant

You have values that don't change per quote — expense loadings, tax rates, minimum premiums. The Constant node stores them in one place so every part of your pipeline can reference them.

!!! tip "Spreadsheet equivalent"
    Like a named range or a parameters sheet in Excel — one place to store values you reference throughout your workbook.

| Config | Description |
|---|---|
| `values` | **Required.** List of `{name, value}` pairs |

Each entry becomes a column in the output. Values are coerced to numbers where possible, otherwise kept as strings.

```json
[
  { "name": "expense_loading", "value": "1.15" },
  { "name": "tax_rate",        "value": "0.12" },
  { "name": "min_premium",     "value": "250" }
]
```

---

## Transforms

### Polars

This is the general-purpose node where you write code to shape your data. Joining two datasets, calculating a new column, filtering rows — if there isn't a specialised node for it, you do it here. This is the node you'll use most often.

!!! tip "Spreadsheet equivalent"
    Think of this as the formula bar in a spreadsheet, but for entire columns at once. Instead of writing a formula in one cell and dragging it down, you write one expression and it applies to every row.

!!! info "When to use"
    - Joining two datasets together (e.g. quotes with external enrichment data).
    - Creating derived columns (age from date of birth, vehicle age from year of manufacture).
    - Filtering or reshaping data in ways the specialised nodes don't cover.

| Config | Description |
|---|---|
| `code` | **Required.** Polars transformation code |
| `selected_columns` | Subset of columns to keep in the output |

Each input table is available by the name of the node it came from. For example, if you connect a node called `policies`, you reference it as `policies` in your code. If there's a single input, you can also use `df`. The last line should be `return df`, which passes the resulting table to the next node.

```python
df = policies.join(claims, on="policy_id", how="left")
df = df.with_columns(
    (pl.col("claim_amount") / pl.col("premium")).alias("loss_ratio")
)
return df
```

!!! tip "Column sidebar"
    The code editor has an **Available Columns** panel below it. Click the **+** next to any column name to insert it at your cursor. If you're new to Polars, start with [Preparing Your Data](preparing-your-data.md) for a guided walkthrough.

#### Common patterns

**Calculate a derived column:**

```python
df = df.with_columns(
    (pl.col("premium") * pl.col("expense_loading")).alias("loaded_premium")
)
return df
```

**Filter rows:**

```python
df = df.filter(pl.col("cover_type") == "comprehensive")
return df
```

**Conditional logic** (like IF in a spreadsheet):

```python
df = df.with_columns(
    pl.when(pl.col("driver_age") < 25)
      .then(pl.lit("young"))
      .otherwise(pl.lit("standard"))
      .alias("driver_category")
)
return df
```

#### Reusing code with instances

This is not a separate node type — it's a configuration option on Polars nodes. If you have the same logic applied to different inputs, you don't need to duplicate the node. Set `instanceOf` to point at the original, and the instance reuses its code with different inputs. Change the original and every instance updates.

| Config | Description |
|---|---|
| `instanceOf` | Name of the Polars node to reuse code from |
| `inputMapping` | Maps the original node's input names to this instance's inputs |

For example, if you have a node called `clean_policies` that normalises column names, you could create an instance that applies the same logic to a different dataset:

```json
{
  "instanceOf": "clean_policies",
  "inputMapping": { "policies": "claims_data" }
}
```

---

### Banding

You have a continuous value like driver age or sum insured, but your rating structure needs age bands or value brackets. The Banding node turns a column of raw values into a column of bands, using rules you define. It also works with categorical values — grouping many fuel types into "Standard" vs "Green", for example.

!!! tip "Spreadsheet equivalent"
    This replaces nested IF statements in Excel (e.g. `=IF(age<=25, "18-25", IF(age<=65, "26-65", "65+"))`) or banding definitions in tools like Earnix or Radar.

!!! info "When to use"
    - Converting continuous values (age, mileage, sum insured) into discrete bands for your rating tables.
    - Grouping categorical values into broader categories.
    - Preparing inputs for a [Rating Step](#rating-step) that expects banded values.

This node accepts a single input.

| Config | Description |
|---|---|
| `factors` | **Required.** List of banding factors |

Each factor has:

| Field | Description |
|---|---|
| `column` | **Required.** Input column to band |
| `outputColumn` | **Required.** Name of the new banded column |
| `banding` | **Required.** `"continuous"` or `"categorical"` |
| `rules` | **Required.** List of rules defining each band |
| `default` | Value assigned to rows that don't match any rule |

Rules are evaluated top to bottom. The first match wins.

**Continuous rules** define ranges using operator/value pairs. Each rule can use one or both conditions. Operators: `<`, `<=`, `>`, `>=`, `=`.

This example bands driver age into three groups:

```json
{
  "factors": [{
    "banding": "continuous",
    "column": "driver_age",
    "outputColumn": "age_band",
    "rules": [
      { "op1": ">=", "val1": "18", "op2": "<=", "val2": "25", "assignment": "18-25" },
      { "op1": ">",  "val1": "25", "op2": "<=", "val2": "65", "assignment": "26-65" },
      { "op1": ">",  "val1": "65", "op2": "",   "val2": "",   "assignment": "65+" }
    ],
    "default": "Unknown"
  }]
}
```

**Categorical rules** map exact values to groups:

```json
{
  "factors": [{
    "banding": "categorical",
    "column": "fuel_type",
    "outputColumn": "fuel_band",
    "rules": [
      { "value": "Petrol",   "assignment": "Standard" },
      { "value": "Diesel",   "assignment": "Standard" },
      { "value": "Electric", "assignment": "Green" }
    ],
    "default": "Other"
  }]
}
```

**Before and after:**

```
BEFORE                              AFTER
| driver_age | fuel_type |          | driver_age | fuel_type | age_band | fuel_band |
|------------|-----------|          |------------|-----------|----------|-----------|
| 22         | Petrol    |    →     | 22         | Petrol    | 18-25    | Standard  |
| 45         | Electric  |          | 45         | Electric  | 26-65    | Green     |
| 71         | Diesel    |          | 71         | Diesel    | 65+      | Standard  |
```

!!! warning "Watch for gaps"
    Rows that don't match any rule get the `default` value. Make sure your ranges don't have gaps unless you intentionally want unmatched rows to fall through to the default.

---

### Rating Step

You have a set of rating factors — area, age band, NCD level — and a table of relativities for each. The Rating Step looks up the right factor for each row and combines them into a single multiplier (or sum). This is how you build a traditional multiplicative or additive rating structure.

!!! tip "Spreadsheet equivalent"
    Like VLOOKUP or INDEX/MATCH in Excel, but it handles multi-dimensional lookups and combines the results automatically.

!!! info "When to use"
    - Building a traditional multiplicative or additive rating structure.
    - Recreating factor tables from a spreadsheet or another rating tool.
    - Looking up relativities based on one, two, or three dimensions.
    - Use [Banding](#banding) first if your tables expect banded inputs rather than raw values.

This node accepts a single input.

| Config | Description |
|---|---|
| `tables` | **Required.** List of rating tables |
| `operation` | **Required.** How to combine factors across tables: `"multiply"`, `"add"`, `"min"`, or `"max"` |
| `combinedColumn` | Name of the column containing the combined result. If omitted, individual factor columns are still created but no combined column is produced. |

Each table has:

| Field | Description |
|---|---|
| `name` | **Required.** Table name |
| `factors` | **Required.** Input columns to match on (up to 3 for multi-way lookups) |
| `outputColumn` | **Required.** Column name for this table's looked-up value |
| `defaultValue` | Value used when the input doesn't match any entry in the table (e.g. an area code you haven't mapped) |
| `entries` | **Required.** The rows of your factor table — each entry maps a combination of factor values to an output |

A one-way table maps a single column. A two-way table maps two columns. Here's a one-way area factor and a one-way age factor, multiplied together:

```json
{
  "tables": [
    {
      "name": "Area Factor",
      "factors": ["area"],
      "outputColumn": "area_factor",
      "defaultValue": "1.0",
      "entries": [
        { "area": "London",     "area_factor": "1.25" },
        { "area": "Manchester", "area_factor": "1.10" },
        { "area": "Rural",      "area_factor": "0.85" }
      ]
    },
    {
      "name": "Age Factor",
      "factors": ["age_band"],
      "outputColumn": "age_factor",
      "defaultValue": "1.0",
      "entries": [
        { "age_band": "18-25", "age_factor": "1.40" },
        { "age_band": "26-65", "age_factor": "1.00" },
        { "age_band": "65+",   "age_factor": "1.15" }
      ]
    }
  ],
  "operation": "multiply",
  "combinedColumn": "location_age_factor"
}
```

**Before and after:**

```
BEFORE                                AFTER
| area       | age_band |            | area   | age_band | area_factor | age_factor | location_age_factor |
|------------|----------|            |--------|----------|-------------|------------|---------------------|
| London     | 18-25    |      →     | London | 18-25    | 1.25        | 1.40       | 1.75                |
| Rural      | 26-65    |            | Rural  | 26-65    | 0.85        | 1.00       | 0.85                |
| Manchester | 65+      |            | Man... | 65+      | 1.10        | 1.15       | 1.265               |
```

!!! warning "String matching"
    Factor values are matched as strings. If your data has `"London"` but your table has `"london"`, it won't match. Use a [Polars](#polars) node upstream to normalise casing if needed.

---

### Source Switch

You want your pipeline to use live API data in production but a batch file during development. The Source Switch lets you wire up both paths and toggle between them.

| Config | Description |
|---|---|
| `input_scenario_map` | **Required.** Maps each input name to a source scenario |

```json
{
  "input_scenario_map": {
    "quotes": "live",
    "batch_data": "batch"
  }
}
```

In this example, when the active source is `"live"`, the node passes through data from the `quotes` input. When switched to `"batch"`, it passes through `batch_data` instead. At deployment, the live source is active automatically.

---

## Models

### External File

You have a model file on disk — a pickle, joblib, or CatBoost `.cbm` file — and you want to score your data with it. The External File node loads the file and gives you a code editor to apply it.

!!! info "When to use"
    - Your model is a standalone file not tracked in MLflow (e.g. a `.pkl` from a colleague or a vendor model).
    - You need to load a JSON lookup file and apply it with custom logic.
    - If your models are managed in MLflow with versioning, use [Model Score](#model-score) instead.

This node accepts a single input.

| Config | Description |
|---|---|
| `path` | **Required.** Path to the file (`.pkl`, `.json`, `.joblib`, `.cbm`) |
| `fileType` | **Required.** `"pickle"`, `"json"`, `"joblib"`, or `"catboost"` |
| `modelClass` | `"classifier"` or `"regressor"` (CatBoost only) |
| `code` | **Required.** Code that uses the loaded object (available as `obj`) and the input data (available as `df`) |

```python
predictions = obj.predict(df.select(feature_columns).to_pandas())
df = df.with_columns(pl.Series("prediction", predictions))
```

---

### Model Score

You've trained a model and registered it in MLflow. The Model Score node loads it, casts your data to the types the model expects, and produces predictions — all without writing scoring code. The model is cached and reloaded automatically when the file changes on disk.

!!! info "When to use"
    - Your models are managed in MLflow with versioning and a model registry.
    - You want automatic feature type casting and model caching.
    - If your model is a standalone file not in MLflow, use [External File](#external-file) instead.

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
| `code` | Post-scoring transformation code — useful for deriving columns from the prediction (e.g. `expected_claims = prediction * exposure`). |

!!! tip "Registered vs run"
    Use `"registered"` if your model has been published to the model registry — this is the most common setup. Use `"run"` to load a model from a specific training experiment, which is useful during development before a model is formally registered.

Model Score also supports instances (`instanceOf`, `inputMapping`) — see [Reusing code with instances](#reusing-code-with-instances) for how this works.

---

### Model Training

You want to train a machine learning model from your pipeline data. The Model Training node supports CatBoost (a gradient-boosted tree algorithm) and GLM (generalised linear model, via RustyStats). Results are logged to MLflow and can be picked up downstream by a [Model Score](#model-score) node.

This node accepts a single input and produces no downstream output — it's a terminal node that outputs a trained model, not data.

| Config | Description |
|---|---|
| `name` | **Required.** Model name |
| `target` | **Required.** Target column (the value you're predicting) |
| `weight` | Weight column for weighted training (e.g. exposure) |
| `exclude` | Columns to exclude from the model inputs (e.g. identifiers, dates, or target-related columns) |
| `algorithm` | **Required.** `"catboost"` or `"glm"` |
| `task` | **Required.** `"regression"` or `"classification"` |
| `params` | Algorithm settings (see below) |
| `split` | Train/validation split configuration (see below) |
| `metrics` | Evaluation metrics: `"gini"`, `"rmse"`, `"mae"`, `"mse"`, `"r2"`, `"auc"`, `"logloss"`, `"poisson_deviance"`, `"tweedie_deviance"` |
| `mlflow_experiment` | MLflow experiment name for tracking training runs |
| `model_name` | Name for the model registry (makes the model available to [Model Score](#model-score) nodes) |
| `output_dir` | Folder where trained model files are saved (e.g. `models/frequency`) |
| `row_limit` | Limit the number of rows used for training (randomly sampled) |

#### Split configuration

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
    | `learning_rate` | Step size shrinkage — smaller values are slower but often more accurate |
    | `loss_function` | CatBoost loss function name (e.g. `"RMSE"`, `"Poisson"`, `"Tweedie:variance_power=1.5"`) |
    | `early_stopping_rounds` | Stop if the validation metric doesn't improve for this many rounds |
    | `monotone_constraints` | Monotonicity constraints per feature — force a feature to only increase or decrease the prediction |
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
      "alpha": 0.01,
      "cv_folds": 5
    }
    ```

    | Field | Description |
    |---|---|
    | `terms` | Dict mapping feature names to term specs. Each has a `type` (`"linear"`, `"categorical"`, `"poly"`, `"spline"`) and optional `monotonicity` (`"increasing"` or `"decreasing"`). If omitted, terms are inferred from data types. |
    | `family` | **Required.** Distribution family: `"gaussian"`, `"poisson"`, `"tweedie"`, etc. |
    | `link` | Link function: `"log"`, `"identity"`, etc. Defaults to the canonical link for the family. |
    | `offset` | Offset column (e.g. log-exposure for a frequency model) |
    | `interactions` | Interaction terms — each has `factors` (list of feature names) and `include_main` (bool) |
    | `regularization` | `"ridge"`, `"lasso"`, or `"elastic_net"` |
    | `alpha` | Regularization strength |
    | `l1_ratio` | Elastic net mixing parameter (0 = pure ridge, 1 = pure lasso) |
    | `intercept` | Whether to fit an intercept. Defaults to true. |
    | `var_power` | Variance power for Tweedie distributions |
    | `cv_folds` | Number of cross-validation folds for regularization tuning |

---

## Optimisation

Price optimisation in Haute is a three-step process:

1. **[Scenario Expander](#scenario-expander)** — generate a range of candidate prices for each quote
2. **[Optimiser](#optimiser)** — find the best price per quote (or the best factor table) subject to your constraints
3. **[Optimiser Apply](#optimiser-apply)** — apply the saved results to new data at deployment time

A typical canvas looks like:

```
Quote Input → [pricing nodes] → Scenario Expander → Optimiser
Quote Input → [pricing nodes] → Optimiser Apply → Output
```

### Scenario Expander

You want to test a range of candidate prices for each quote — say, 50 price points between 200 and 800 — so the optimiser can pick the best one. The Scenario Expander generates those candidates by cross-joining each row with a range of values.

!!! tip "Spreadsheet equivalent"
    Similar to a data table or sensitivity analysis in Excel, but integrated into the pipeline so the [Optimiser](#optimiser) can act on the results.

This node accepts a single input.

| Config | Description |
|---|---|
| `quote_id` | **Required.** Column identifying each unique row (e.g. `quote_id`) |
| `column_name` | Name of the new column containing the generated values |
| `min_value` | **Required.** Start of the value range |
| `max_value` | **Required.** End of the value range |
| `steps` | **Required.** Number of values to generate across the range |
| `step_column` | **Required.** Name of the 0-based step index column |
| `code` | Polars code applied after expansion |

**Before and after** (with `min_value: 200`, `max_value: 400`, `steps: 3`):

```
BEFORE                        AFTER
| quote_id | base_premium |   | quote_id | base_premium | scenario_value | scenario_index |
|----------|--------------|   |----------|--------------|----------------|----------------|
| Q001     | 350          |   | Q001     | 350          | 200            | 0              |
| Q002     | 420          |   | Q001     | 350          | 300            | 1              |
                              | Q001     | 350          | 400            | 2              |
                        →     | Q002     | 420          | 200            | 0              |
                              | Q002     | 420          | 300            | 1              |
                              | Q002     | 420          | 400            | 2              |
```

!!! warning "Row multiplication"
    The output has `rows × steps` records. 1,000 rows with 50 steps produces 50,000 rows. With large datasets, use the Optimiser's `chunk_size` to process in batches rather than expanding the full dataset at once.

---

### Optimiser

You've generated candidate prices with the Scenario Expander. Now you want to find the best price for each quote — or the best set of rating factors — subject to portfolio-level constraints like volume retention or loss ratio targets.

This node produces no downstream output — it's a terminal node. Results are saved as artifacts that can be loaded by [Optimiser Apply](#optimiser-apply).

**Online mode** optimises per-record using a Lagrangian solver — a mathematical method that balances your objective against constraint penalties. You provide a grid of candidate prices (from a [Scenario Expander](#scenario-expander)) and the optimiser selects the best price per quote while respecting portfolio-level constraints.

**Ratebook mode** optimises factor tables using coordinate descent — an iterative method that adjusts one factor at a time while holding the others fixed. Instead of per-quote prices, it finds the best set of rating factors that satisfy your constraints.

| Config | Description |
|---|---|
| `mode` | **Required.** `"online"` or `"ratebook"` |
| `quote_id` | **Required.** Column identifying each quote |
| `scenario_index` | **Required.** Column with the scenario step index (created by [Scenario Expander](#scenario-expander)) |
| `scenario_value` | **Required.** Column with the scenario value (created by [Scenario Expander](#scenario-expander)) |
| `objective` | **Required.** Column to maximise (e.g. `"predicted_income"`) |
| `constraints` | **Required.** Named constraints with min/max bounds |
| `max_iter` | Maximum solver iterations |
| `tolerance` | How close to optimal the solution needs to be before stopping. Smaller values give more precise results but take longer. Typical values: 0.001 to 0.01. |
| `chunk_size` | Number of quotes to optimise at once. Smaller values use less memory. Leave blank to process all quotes at once. |
| `record_history` | Whether to save iteration-by-iteration convergence history |
| `mlflow_experiment` | MLflow experiment name for logging results |
| `model_name` | Model registry name for saving artifacts |

A typical constraint configuration:

```json
{
  "objective": "predicted_income",
  "constraints": {
    "volume":     { "min": 0.90 },
    "loss_ratio": { "max": 0.65 }
  }
}
```

This tells the optimiser: maximise the objective column, but keep volume at or above 90% of baseline and loss ratio at or below 65%.

??? info "Ratebook-specific options"
    | Config | Description |
    |---|---|
    | `factor_columns` | **Required.** Factor columns to optimise |
    | `candidate_min` | **Required.** Minimum candidate factor value |
    | `candidate_max` | **Required.** Maximum candidate factor value |
    | `candidate_steps` | **Required.** Number of candidate values per factor |
    | `max_cd_iterations` | Maximum coordinate descent iterations |
    | `cd_tolerance` | Coordinate descent convergence tolerance |
    | `structure_mode` | `"explicit"` (you define the factor structure) or `"auto"` (inferred from the data) |

??? info "Efficient frontier"
    The efficient frontier shows the best achievable tradeoff between your objective (e.g. profit) and your constraints (e.g. volume retention). Enable it to see how much profit you give up for each additional percentage point of volume.

    | Config | Description |
    |---|---|
    | `frontier_enabled` | Whether to compute the efficient frontier |
    | `frontier_points_per_dim` | Number of points per dimension on the frontier |
    | `frontier_threshold_ranges` | Constraint ranges to sweep for the frontier |

---

### Optimiser Apply

You've run the Optimiser and saved the results. Now you want to apply those results — the per-quote optimisation parameters (online mode) or the factor tables (ratebook mode) — to fresh data at deployment time.

| Config | Description |
|---|---|
| `sourceType` | **Required.** `"file"`, `"registered"`, or `"run"` |
| `artifact_path` | Path to the saved optimiser artifact. Required when sourceType is `"file"`. |
| `registered_model` | Model registry name. Required when sourceType is `"registered"`. |
| `version` | Version or `"latest"`. Required when sourceType is `"registered"`. |
| `experiment_id` | MLflow experiment ID. Required when sourceType is `"run"`. |
| `run_id` | MLflow run ID. Required when sourceType is `"run"`. |
| `version_column` | Column name for version tracking. Defaults to `"__optimiser_version__"`. |

---

## Pipeline outputs

### Output

You've calculated a price. Now you choose which columns to send back in the API response — the final premium, any breakdown fields, a reference ID. Everything not listed here is still calculated but stays internal.

| Config | Description |
|---|---|
| `fields` | **Required.** List of column names to include in the response |

See the [deployment guide](../deployment/index.md) for how the Output node maps to your live API response.

!!! warning "One per pipeline"
    You can only have one Output node in a pipeline.

---

### Data Sink

You want to save results to a file — scoring a full dataset and writing the output to parquet or CSV for downstream analysis.

This node accepts a single input.

| Config | Description |
|---|---|
| `path` | **Required.** Output file path (e.g. `outputs/scored_policies`) |
| `format` | **Required.** `"parquet"` or `"csv"` |

If you provide a filename without a directory, it's written to `outputs/`. The format extension is added automatically if missing.

---

## Organisation

### Submodel

As your pipeline grows, the canvas gets crowded. Submodels let you collapse a group of nodes into a single block — like grouping sheets in a workbook. Double-click to step inside; click the breadcrumb to come back out.

| Config | Description |
|---|---|
| `file` | **Required.** Path to the submodel definition file |
| `inputPorts` | Port names for inputs |
| `outputPorts` | Port names for outputs |

You can reuse a submodel across pipelines by referencing the same definition file. To ungroup, dissolve the submodel and its nodes expand back into the parent pipeline.

---

## See also

- **[Preparing Your Data](preparing-your-data.md)** — cleaning column names and building derived features
- **[Polars](../getting-started/polars.md)** — how the data engine works and why it matters
- **[Deployment](../deployment/index.md)** — deploying your pipeline as a live pricing API
