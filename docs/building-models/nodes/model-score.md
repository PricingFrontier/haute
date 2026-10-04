# Model Scoring

You've trained a model and logged it to MLflow (and perhaps your promotion process has registered it), or saved it to a file in your project. The Model Scoring node loads it, checks your data against the features the model was trained on, and produces predictions  - all without writing scoring code. The model is cached, and reloaded when its file changes.

!!! note "What is MLflow?"
    MLflow is an open-source platform for tracking model experiments and storing trained models  - think of it as version control for models. If your team has set up MLflow (or uses Databricks, which includes it), your trained models are stored in a model registry where they can be loaded by version.

!!! info "When to use"
    - Your models are managed in MLflow with versioning and a model registry.
    - Your model is a file in the project, saved with Model Training's **Save model to file** or handed over by a colleague.
    - You want the model's features checked and the model cached for you.

This node takes a single input. It outputs the input rows with the prediction added as a new column (two columns for a classification model).

## The CONFIG tab

The **INPUT** strip at the top names the connected input; its **×** removes the connection.

**MODEL SOURCE** chooses how the model is found:

- **Experiment Run**: one specific training run, picked by experiment. Useful during development, before a model is registered.
- **Registered Model** (the default): a named, versioned model in the registry. This is the most common setup.
- **Model file**: a model file in your project folder.

Below it, for **Experiment Run** and **Registered Model**, the destination buttons choose where the node browses for and loads the model: **Databricks**, **MLflow server** or **Local folder** (the default, the project's local MLflow folder). A remote that is not configured is greyed out, and clicking it opens MLflow settings instead. Switching destination clears the chosen model and says **Selection cleared - run and model identifiers are not portable across destinations.** A **Model file** has no destination buttons, because the file is read from the project, not from MLflow.

With **Experiment Run**:

| Field | What it does |
|---|---|
| **EXPERIMENT** | The MLflow experiment to browse (**Select an experiment...** until you choose). |
| **RUN** | A finished run in that experiment that holds a model. An experiment without one shows **No finished runs with a model artifact in this experiment yet.** |
| **RUN ID** | The chosen run's ID. You can also paste one here. |
| **ARTIFACT PATH** | The model's path inside the run (for example `model.cbm`). Picking a run fills it with the run's first artifact. |

With **Registered Model**:

| Field | What it does |
|---|---|
| **MODEL NAME** | The registered model, from the destination's registry (**Select a model...** until you choose). With an empty registry the pane says **No registered models yet - haute logs training runs; your promotion process registers them.** |
| **VERSION** | **latest** (the default), an alias such as **@champion → v3**, or a version, listed as `v3` with its status and description. |

With **Model file**:

| Field | What it does |
|---|---|
| **MODEL FILE** | The model file, chosen in the file browser, which lists only model files: `.cbm`, `.rsglm`, `.ubj`, `.lgbm` and `.ebm`. Once a file is chosen, **change** opens the browser again. |

Under the file the panel shows what it found: the kind of model, its features, its offset (or **none**) and the feature contract it scores with. A file the node cannot score shows the reason here instead, the same message the preview would give.

Then, for every source:

| Field | What it does |
|---|---|
| **TASK** | **Regression** (the default) or **Classification**. |
| **OUTPUT COLUMN** | The name of the prediction column. Defaults to `prediction`. |

!!! tip "Following an alias"
    If your promotion process moves an alias such as `champion` to each newly approved version, pick the alias in the **VERSION** list (it shows as `@champion → v3`). The node then always scores the version the alias targets. Deploying records which version the alias pointed to at deploy time.

### Task type

Use **Regression** when your model predicts a number (frequency, severity, premium). Use **Classification** when your model predicts a category or probability (e.g. likelihood of claim, fraud detection).

A classification model also adds an `<output column>_proba` column (for example `prediction_proba`) beside the predicted class: the probability of the positive class, which is usually what you use downstream. It appears when the model can produce probabilities. The panel reminds you of the extra column when **Classification** is chosen.

A run logged by Haute's Model Training node records its task. When you pick such a run or registered version, **TASK** shows it read-only with **Task recorded by the training run.** A model file saved with its feature contract records its task too, shown with **Task recorded with the model.** A model logged elsewhere may not record one, so you choose the task yourself. If the node's task and the recorded one disagree, the panel warns and offers a button to use the recorded task. Either way, scoring a model as the wrong task fails with an error naming the task it was trained for.

### Model files

Model Scoring loads the native model a Model Training run logged or **Save model to
file** wrote: CatBoost (`.cbm`), XGBoost (`.ubj`), LightGBM (`.lgbm`), t-boost
(`.tboost`), EBM (`.ebm`) or GLM (`.rsglm`). From MLflow it also loads an MLflow pyfunc model.

A model file scores with the feature contract saved beside it: `<model name>.feature_contract.json`,
or else `feature_contract.json` in the same folder. **Save model to file** writes the
first. The contract names the model's features, categories and offset, so keep it next to
the model when you copy the model elsewhere in the project. XGBoost, LightGBM and t-boost files describe their own inputs and offset. An EBM file is
the bare estimator, so it loads only with the feature contract Model Training logged
beside it, and only under the `interpret-core` version that contract records; a
contract for a different loss or version is refused rather than scored. Categorical
values a tree or EBM model never saw fail instead of scoring as missing.

A CatBoost model file does not record its own offset. Models trained by Haute record it
in the model; for any other CatBoost model the feature contract must say whether the
model was trained with an offset (its `offset_column` and `offset_link`, `log` for an
exposure or `identity` for an additive term) or without one. A CatBoost model whose offset
nothing declares is refused, whether it comes from a file, a run or the registry, because
scoring it without its offset would mis-price every row. A contract that disagrees with
what the model records is refused too. Run and registered models trained by an earlier
Haute are scored with the contract the run logged beside the model.

Scoring checks the input against the model's features before it runs: a missing feature,
a missing offset column the model was trained with, features in a different order from
training, or a numeric column where the model expects a categorical one fails with an error
listing what is wrong, rather than predicting from mismatched data.

## The TRANSFORM tab

See [Transform](transform.md#building-the-node-from-steps).

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
    The node's settings are stored in a JSON sidecar, `config/model_scoring/<node name>.json`, which the pipeline's `.py` file names in the node's decorator: `@pipeline.model_score(config="config/model_scoring/<node name>.json")`. When the node has **TRANSFORM** steps, their generated code is the body of the node's function, which receives the scored frame as `df`.

    | Setting in the editor | Stored as |
    |---|---|
    | MLflow destination | `mlflow_destination`: `"databricks"` or `"server"`; absent for the local folder |
    | **MODEL SOURCE** | `sourceType`: `"registered"`, `"run"` or `"file"` |
    | **MODEL FILE** | `model_path`: the model file, relative to the project folder |
    | **MODEL NAME** | `registered_model` |
    | **VERSION** | `version` (a version number or `"latest"`), or `alias` for an alias such as `"champion"`; never both |
    | **EXPERIMENT** | `experiment_id` (and `experiment_name`, the name the panel shows) |
    | **RUN**, **RUN ID** | `run_id` (and `run_name`, the name the panel shows) |
    | **ARTIFACT PATH** | `artifact_path` |
    | **TASK** | `task`: `"regression"` or `"classification"` |
    | **OUTPUT COLUMN** | `output_column` (defaults to `"prediction"`) |
    | **TRANSFORM** tab | `steps` in the sidecar; their generated code in the function body |
    | **COLUMNS** tab | `selected_columns` |

    These keys have no editor control:

    | Key | What it does |
    |---|---|
    | `feature_contract_path` | An in-project feature-contract file the model scores with, in place of the one saved beside a model file, and bundled with the model when deploying. |
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

- [Load File](external-file.md)  - for pickle, joblib and JSON objects you apply with your own code
- [Model Training](model-training.md)  - to train models that can be scored here
