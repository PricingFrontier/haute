# Model Scoring

You've trained a model and logged it to MLflow (and perhaps your promotion process has registered it). The Model Scoring node loads it, checks your data against the features the model was trained on, and produces predictions  - all without writing scoring code. The model is cached, and reloaded when its file changes.

!!! note "What is MLflow?"
    MLflow is an open-source platform for tracking model experiments and storing trained models  - think of it as version control for models. If your team has set up MLflow (or uses Databricks, which includes it), your trained models are stored in a model registry where they can be loaded by version.

!!! info "When to use"
    - Your models are managed in MLflow with versioning and a model registry.
    - You want the model's features checked and the model cached for you.
    - If your model is a standalone file not in MLflow, use [Load File](external-file.md) instead.

This node takes a single input. It outputs the input rows with the prediction added as a new column (two columns for a classification model).

## The CONFIG tab

The **INPUT** strip at the top names the connected input; its **×** removes the connection.

Below it, the MLflow destination buttons choose where the node browses for and loads the model: **Databricks**, **MLflow server** or **Local folder** (the default, the project's local MLflow folder). A remote that is not configured is greyed out, and clicking it opens MLflow settings instead. Switching destination clears the chosen model and says **Selection cleared - run and model identifiers are not portable across destinations.**

**MODEL SOURCE** chooses how the model is found:

- **Registered Model** (the default): a named, versioned model in the registry. This is the most common setup.
- **Experiment Run**: one specific training run, picked by experiment. Useful during development, before a model is registered.

With **Registered Model**:

| Field | What it does |
|---|---|
| **MODEL NAME** | The registered model, from the destination's registry (**Select a model...** until you choose). With an empty registry the pane says **No registered models yet - haute logs training runs; your promotion process registers them.** |
| **VERSION** | **latest** (the default), an alias such as **@champion → v3**, or a version, listed as `v3` with its status and description. |

With **Experiment Run**:

| Field | What it does |
|---|---|
| **EXPERIMENT** | The MLflow experiment to browse (**Select an experiment...** until you choose). |
| **RUN** | A finished run in that experiment that holds a model. An experiment without one shows **No finished runs with a model artifact in this experiment yet.** |
| **RUN ID** | The chosen run's ID. You can also paste one here. |
| **ARTIFACT PATH** | The model's path inside the run (for example `model.cbm`). Picking a run fills it with the run's first artifact. |

Then, for either source:

| Field | What it does |
|---|---|
| **TASK** | **Regression** (the default) or **Classification**. |
| **OUTPUT COLUMN** | The name of the prediction column. Defaults to `prediction`. |

!!! tip "Following an alias"
    If your promotion process moves an alias such as `champion` to each newly approved version, pick the alias in the **VERSION** list (it shows as `@champion → v3`). The node then always scores the version the alias targets. Deploying records which version the alias pointed to at deploy time.

### Task type

Use **Regression** when your model predicts a number (frequency, severity, premium). Use **Classification** when your model predicts a category or probability (e.g. likelihood of claim, fraud detection).

A classification model also adds an `<output column>_proba` column (for example `prediction_proba`) beside the predicted class: the probability of the positive class, which is usually what you use downstream. It appears when the model can produce probabilities. The panel reminds you of the extra column when **Classification** is chosen.

A run logged by Haute's Model Training node records its task. When you pick such a run or registered version, **TASK** shows it read-only with **Task recorded by the training run.** A model logged elsewhere may not record one, so you choose the task yourself. If the node's task and the recorded one disagree, the panel warns and offers a button to use the recorded task. Either way, scoring a model as the wrong task fails with an error naming the task it was trained for.

### Model files

Model Scoring loads the native model a Model Training run logged: CatBoost (`.cbm`),
XGBoost (`.ubj`), LightGBM (`.lgbm`), EBM (`.ebm`) or GLM (`.rsglm`), or an MLflow pyfunc
model. XGBoost and LightGBM files describe their own inputs and offset. An EBM file is
the bare estimator, so it loads only with the feature contract Model Training logged
beside it, and only under the `interpret-core` version that contract records; a
contract for a different loss or version is refused rather than scored. Categorical
values a tree or EBM model never saw fail instead of scoring as missing.

Scoring checks the input against the model's features before it runs: a missing feature,
a missing offset column the model was trained with, features in a different order from
training, or a numeric column where the model expects a categorical one fails with an error
listing what is wrong, rather than predicting from mismatched data.

## The POLARS tab

See [Polars](polars.md#building-the-node-from-steps).

## The COLUMNS tab

The **COLUMNS** tab chooses which output columns the node passes on; see [Working with any node](index.md#working-with-any-node).

## Example

Scoring a registered frequency model:

1. Connect the data to score to a Model Scoring node.
2. Leave the destination on **Local folder** (or choose where your registry lives) and **MODEL SOURCE** on **Registered Model**.
3. Choose `frequency_model` in **MODEL NAME** and leave **VERSION** on **latest**.
4. Set **OUTPUT COLUMN** to `predicted_frequency`. **TASK** shows **Regression**, recorded by the training run.

The preview shows your input rows with a new `predicted_frequency` column.

## Instances

Instances let you reuse the same scoring configuration with different inputs  - for example, scoring the same model against both training and validation data. See [Instances](instances.md) for full details.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/model_scoring/<node name>.json`, which the pipeline's `.py` file names in the node's decorator: `@pipeline.model_score(config="config/model_scoring/<node name>.json")`. When the node has **POLARS** steps, their generated code is the body of the node's function, which receives the scored frame as `df`.

    | Setting in the editor | Stored as |
    |---|---|
    | MLflow destination | `mlflow_destination`: `"databricks"` or `"server"`; absent for the local folder |
    | **MODEL SOURCE** | `sourceType`: `"registered"` or `"run"` |
    | **MODEL NAME** | `registered_model` |
    | **VERSION** | `version` (a version number or `"latest"`), or `alias` for an alias such as `"champion"`; never both |
    | **EXPERIMENT** | `experiment_id` (and `experiment_name`, the name the panel shows) |
    | **RUN**, **RUN ID** | `run_id` (and `run_name`, the name the panel shows) |
    | **ARTIFACT PATH** | `artifact_path` |
    | **TASK** | `task`: `"regression"` or `"classification"` |
    | **OUTPUT COLUMN** | `output_column` (defaults to `"prediction"`) |
    | **POLARS** tab | `steps` in the sidecar; their generated code in the function body |
    | **COLUMNS** tab | `selected_columns` |

    These keys have no editor control:

    | Key | What it does |
    |---|---|
    | `feature_contract_path` | An in-project feature-contract file to bundle with the model when deploying. |
    | `categorical_levels` | Declared category levels for categorical columns, checked against the model's feature contract when deploying. |

    The example above is stored as:

    ```json
    {
      "sourceType": "registered",
      "registered_model": "frequency_model",
      "version": "latest",
      "task": "regression",
      "output_column": "predicted_frequency"
    }
    ```

**See also:**

- [Load File](external-file.md)  - for standalone model files not managed in MLflow
- [Model Training](model-training.md)  - to train models that can be scored here
