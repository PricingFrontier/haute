# Load File

You have a model file on disk  - a pickle, joblib, or CatBoost `.cbm` file  - and you want to score your data with it. The Load File node loads the file as `obj`, and you apply it in the node's **TRANSFORM** tab: add a **Free code** step, or click **Switch to code** to write the node as code.

!!! info "When to use"
    - Your model is a standalone file not tracked in MLflow (e.g. a `.pkl` from a colleague), and its class is one Haute can load (see the warning below).
    - You need to load a JSON lookup file and apply it with custom logic.
    - If your models are managed in MLflow with versioning, use [Model Scoring](model-score.md) instead.

A Load File node takes the data to score as its first input and outputs that data with whatever your steps or code add. Your steps and code see the first input as `df`; any further inputs are available by their names, and **Join another input** and **Append inputs** steps can use them. With no steps and no code, the node passes its first input through unchanged. The panel has three tabs: **CONFIG**, **TRANSFORM** and **COLUMNS**.

## The CONFIG tab

The **INPUT** chips at the top name the node's connections; the × on a chip removes that connection. Below them:

| Field | What it does |
|---|---|
| **FILE TYPE** | How to read the file: **PICKLE** (the default), **JSON**, **JOBLIB** or **CATBOOST**. |
| **MODEL TYPE** | Shown for **CATBOOST** only: **Classifier** (the default) or **Regressor**, the kind of CatBoost model the file holds. |
| **FILE PATH** | The file, chosen in the file browser: a `.pkl`, `.json`, `.joblib` or `.cbm` file inside your project folder. Once a file is chosen, **change** opens the browser again. |

!!! warning "Pickle and joblib files load only known classes"
    For safety, Haute loads pickle and joblib files through an exact list of allowed classes: NumPy arrays, pandas and Polars DataFrames and Series, plain Python values such as dictionaries and lists, CatBoost models, scikit-learn's `RandomForestRegressor`, `LinearRegression` and `DecisionTreeRegressor`, and InterpretML's `ExplainableBoostingRegressor` and `ExplainableBoostingClassifier`. Any other class fails with "Blocked unpickling of ...". That includes other scikit-learn estimators (classifiers among them) and pickled XGBoost or LightGBM models.

## The TRANSFORM tab

See [Transform](transform.md#building-the-node-from-steps).

## The COLUMNS tab

The **COLUMNS** tab chooses which columns the node passes on (see [Working with any node](index.md#working-with-any-node)).

## Example

To score your data with a model saved as `models/frequency.pkl`:

1. Connect your data to a Load File node. On the **CONFIG** tab, leave **FILE TYPE** on **PICKLE** and choose `models/frequency.pkl` under **FILE PATH**.
2. On the **TRANSFORM** tab, click **Add step** and choose **Free code**.
3. Type this in the step's code box:

```python
feature_columns = ["driver_age", "vehicle_age", "area"]  # columns your model was trained on
predictions = obj.predict(df.select(feature_columns).collect().to_pandas())
df = df.with_columns(pl.Series("prediction", predictions))
```

**Reading the code:**

| Expression | What it does |
|---|---|
| `obj` | The loaded file (your model, lookup table, etc.) |
| `df` | The input data as a table (dataframe) |
| `obj.predict(...)` | Asks the model to produce predictions |
| `feature_columns = [...]` | A list of column names your model was trained on  - replace with your own |
| `df.select(feature_columns)` | Picks those columns from the table |
| `.collect()` | Reads the data, so the model gets actual rows (`df` is a lazy table until then) |
| `.to_pandas()` | Converts the data to the format most models expect  - you'll see this in most scoring code |
| `pl.Series("prediction", predictions)` | Wraps the results as a new column called "prediction" |
| `df.with_columns(...)` | Adds the new column to the table |

The preview then shows your data with a new `prediction` column.

!!! warning "Assign the result to `df`"
    Your code must assign its result to `df`, which Haute passes to the next node. Do not add `return df`: a **Free code** step refuses it ("Assign the result to df instead of using return."), and after **Switch to code** Haute adds that line itself and shows it, dimmed, under the code box.

### JSON lookup example

If your file is a JSON dictionary (e.g. area factors), choose **JSON** as the **FILE TYPE** and use it as a lookup table in a **Free code** step. Areas missing from the dictionary get a factor of 1.0:

```python
# obj is a dict loaded from a JSON file, e.g. {"London": 1.25, "Rural": 0.85}
df = df.with_columns(
    pl.col("area").replace_strict(obj, default=1.0).alias("area_factor")
)
```

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/load_file/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.external_file(config="config/load_file/<node name>.json")`. Steps on the **TRANSFORM** tab are stored in the sidecar as `steps`, and the code they generate is the body of the node's function, which takes the node's inputs and the loaded object as `obj`; after **Switch to code**, the body is your code.

    | Setting in the editor | Stored as |
    |---|---|
    | **FILE TYPE** | `fileType`: `"pickle"`, `"json"`, `"joblib"` or `"catboost"` |
    | **MODEL TYPE** | `modelClass`: `"classifier"` or `"regressor"` (CatBoost only; absent reads as `"classifier"`) |
    | **FILE PATH** | `path`: the file, relative to your project folder |
    | **TRANSFORM** tab | `steps`, or the function body after **Switch to code** |
    | **COLUMNS** tab | `selected_columns` |

**See also:**

- [Model Scoring](model-score.md)  - for MLflow-managed models
- [Transform](transform.md)  - for steps and code
- [Filesystem Portability](../filesystem-portability.md)  - if the file travels between operating systems, WSL, or network mounts
