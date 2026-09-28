# Expander

You want to test a range of candidate prices for each quote  - say, 50 price points between 200 and 800  - so the optimiser can pick the best one. The Expander generates those candidates by cross-joining each row with a range of values.

An Expander takes a single input and outputs every input row repeated once per step, with a step index column and, optionally, a column of values spread across a range. The panel has three tabs: **CONFIG**, **POLARS** and **COLUMNS**.

## The CONFIG tab

The **INPUT** chip at the top names the node's connection; its × removes the connection. Below it:

| Field | What it does |
|---|---|
| **ROW KEY** | The column identifying each input row (e.g. `quote_id`), chosen from the input's columns ("unique column per input row"). Until the input has been previewed it is a text box. The expansion itself does not read it. |
| **INDEX COLUMN** | The name of the new 0-based step index column ("0-based step index column"). Left blank, it is `scenario_index`. |
| **STEPS** | The number of rows generated per input row, and of values across the range ("rows generated per input row"). A new node starts with 21; it is at least 1. While a **VALUE COLUMN** is set, this field moves into **VALUE RANGE**. |
| **VALUE COLUMN** | Optional. The name of the new column holding the generated values. Without it, only the index column is added. |
| **VALUE RANGE** | Shown once a **VALUE COLUMN** is set; the range is used only then. **Min** and **Max** are the ends of the range, both included; left blank, Min is `0.8` and Max is `1.2`. **Steps** is as above, and **Step Size** shows the gap between neighbouring values, (Max − Min) / (Steps − 1). |

A **Min** or **Max** that is not a number is outlined in red and not saved; fix it or clear it. When Min is not below Max, the range shows "Warning: min value should be less than max value".

## The POLARS tab

See [Polars](polars.md#building-the-node-from-steps).

## The COLUMNS tab

The **COLUMNS** tab chooses which columns the node passes on (see [Working with any node](index.md#working-with-any-node)).

## Example

1. Connect your quotes to an Expander.
2. Choose `quote_id` as the **ROW KEY** and leave **INDEX COLUMN** blank.
3. Type `scenario_value` as the **VALUE COLUMN**.
4. Under **VALUE RANGE**, set **Min** to `200`, **Max** to `400` and **Steps** to `3`. **Step Size** shows `100`.

**Before and after:**

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

The index column keeps its default name, `scenario_index`. Both ends of the range are included.

!!! warning "Row multiplication"
    The output has `rows × steps` records. 1,000 rows with 50 steps produces 50,000 rows. With large datasets, use the Optimisation node's **Chunk size** to process in batches rather than expanding the full dataset at once.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/expander/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.scenario_expander(config="config/expander/<node name>.json")`. Steps on the **POLARS** tab are stored in the sidecar as `steps`, and the code they generate is the body of the node's function, which takes the expanded data as `df`; after **Switch to code**, the body is your code.

    | Setting in the editor | Stored as |
    |---|---|
    | **ROW KEY** | `quote_id` |
    | **INDEX COLUMN** | `step_column` (absent or empty means `"scenario_index"`) |
    | **STEPS** | `stepCount`: a whole number of at least 1. It is required: a node without it fails with "Scenario expander requires stepCount (the number of grid values)." |
    | **VALUE COLUMN** | `column_name` |
    | **Min** | `min_value` (absent means `0.8`) |
    | **Max** | `max_value` (absent means `1.2`) |
    | **POLARS** tab | `steps`, or the function body after **Switch to code** |
    | **COLUMNS** tab | `selected_columns` |

**See also:**

- [Optimisation](optimiser.md)  - find the best price subject to constraints
- [Apply Optimisation](optimiser-apply.md)  - apply saved results at deployment
