# Load File

You have a model file on disk  - a pickle, joblib, or CatBoost `.cbm` file  - and you want to score your data with it. The Load File node loads the file as `obj`, and you apply it in the node's **Polars** tab: add a **Free code** step, or click **Switch to code** to write the node as code.

!!! info "When to use"
    - Your model is a standalone file not tracked in MLflow (e.g. a `.pkl` from a colleague), and its class is one Haute can load (see the warning below).
    - You need to load a JSON lookup file and apply it with custom logic.
    - If your models are managed in MLflow with versioning, use [Model Scoring](model-score.md) instead.

Your steps and code see the first input as `df`. Any further inputs are available by their names, and **Join another input** and **Append inputs** steps can use them.

| Config | Description |
|---|---|
| `path` | **Required.** Path to the file (`.pkl`, `.json`, `.joblib`, `.cbm`). The file must be inside your project folder. |
| `fileType` | **Required.** `"pickle"`, `"json"`, `"joblib"`, or `"catboost"` |
| `modelClass` | `"classifier"` or `"regressor"` (CatBoost only) |
| `steps` | The steps in the node's **Polars** tab. With no steps and no code, the node passes its input through unchanged. |
| `code` | Code that uses the loaded object (available as `obj`) and the input data (available as `df`). While the node has steps, this is the code they generate; after **Switch to code**, it is the code you edit. |

!!! warning "Pickle and joblib files load only known classes"
    For safety, Haute loads pickle and joblib files through an exact list of allowed classes: NumPy arrays, pandas and Polars DataFrames and Series, plain Python values such as dictionaries and lists, CatBoost models, scikit-learn's `RandomForestRegressor`, `LinearRegression` and `DecisionTreeRegressor`, and InterpretML's `ExplainableBoostingRegressor` and `ExplainableBoostingClassifier`. Any other class fails with "Blocked unpickling of ...". That includes other scikit-learn estimators (classifiers among them) and pickled XGBoost or LightGBM models.

To score the data, put this in a **Free code** step (or in the code after **Switch to code**):

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

!!! warning "Assign the result to `df`"
    Your code must assign its result to `df`, which Haute passes to the next node. Do not add `return df`: Haute adds it for you, and after **Switch to code** the editor shows that line, dimmed, under the code box.

### JSON lookup example

If your file is a JSON dictionary (e.g. area factors), you can use it as a lookup table. Areas missing from the dictionary get a factor of 1.0:

```python
# obj is a dict loaded from a JSON file, e.g. {"London": 1.25, "Rural": 0.85}
df = df.with_columns(
    pl.col("area").replace_strict(obj, default=1.0).alias("area_factor")
)
```

**See also:**

- [Model Scoring](model-score.md)  - for MLflow-managed models
- [Polars](polars.md)  - for code syntax
- [Filesystem Portability](../filesystem-portability.md)  - if the file travels between operating systems, WSL, or network mounts
