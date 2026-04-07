# External File

You have a model file on disk  - a pickle, joblib, or CatBoost `.cbm` file  - and you want to score your data with it. The External File node loads the file and gives you a code editor to apply it.

!!! info "When to use"
    - Your model is a standalone file not tracked in MLflow (e.g. a `.pkl` from a colleague or a vendor model).
    - You need to load a JSON lookup file and apply it with custom logic.
    - If your models are managed in MLflow with versioning, use [Model Score](model-score.md) instead.

This node accepts a single input.

| Config | Description |
|---|---|
| `path` | **Required.** Path to the file (`.pkl`, `.json`, `.joblib`, `.cbm`) |
| `fileType` | **Required.** `"pickle"`, `"json"`, `"joblib"`, or `"catboost"` |
| `modelClass` | `"classifier"` or `"regressor"` (CatBoost only) |
| `code` | **Required.** Code that uses the loaded object (available as `obj`) and the input data (available as `df`) |

```python
feature_columns = ["driver_age", "vehicle_age", "area"]  # columns your model was trained on
predictions = obj.predict(df.select(feature_columns).to_pandas())
df = df.with_columns(pl.Series("prediction", predictions))
return df
```

**Reading the code:**

| Expression | What it does |
|---|---|
| `obj` | The loaded file (your model, lookup table, etc.) |
| `df` | The input data as a table (dataframe) |
| `obj.predict(...)` | Asks the model to produce predictions |
| `feature_columns = [...]` | A list of column names your model was trained on  - replace with your own |
| `df.select(feature_columns)` | Picks those columns from the table |
| `.to_pandas()` | Converts the data to the format most models expect  - you'll see this in most scoring code |
| `pl.Series("prediction", predictions)` | Wraps the results as a new column called "prediction" |
| `df.with_columns(...)` | Adds the new column to the table |

!!! warning "Always return the result"
    Your code must end with `return df` to pass the result to the next node.

### JSON lookup example

If your external file is a JSON dictionary (e.g. area factors), you can use it as a lookup table:

```python
# obj is a dict loaded from a JSON file, e.g. {"London": 1.25, "Rural": 0.85}
df = df.with_columns(
    pl.col("area").replace(obj).alias("area_factor")
)
return df
```

**See also:**

- [Model Score](model-score.md)  - for MLflow-managed models
- [Polars](polars.md)  - for code syntax
