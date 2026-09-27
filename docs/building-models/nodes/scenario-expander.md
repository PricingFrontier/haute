# Expander

You want to test a range of candidate prices for each quote  - say, 50 price points between 200 and 800  - so the optimiser can pick the best one. The Expander generates those candidates by cross-joining each row with a range of values.

!!! tip "Spreadsheet equivalent"
    Similar to a data table or sensitivity analysis in Excel, but integrated into the pipeline so the [Optimisation](optimiser.md) node can act on the results.

This node accepts a single input. The editor's field names are shown in bold.

| Config | Description |
|---|---|
| `quote_id` | **Row Key**: the column identifying each input row (e.g. `quote_id`). The expansion itself does not read it. |
| `step_column` | **Index Column**: name of the 0-based step index column. Defaults to `"scenario_index"`. |
| `column_name` | **Value Column** (optional): name of the new column containing the generated values. Without it, only the index column is added. |
| `min_value` | **Min** under **Value Range**: start of the value range. Defaults to `0.8`. |
| `max_value` | **Max** under **Value Range**: end of the value range. Defaults to `1.2`. |
| `stepCount` | **Required.** **Steps**: number of rows generated per input row, and of values across the range. A new node starts with 21. |
| `steps` | Optional post-expansion steps built on the node's **Polars** tab (see below). |
| `code` | The Polars code the steps generate, or your own code after **Switch to code**. |

The editor shows **Value Range** (with **Min**, **Max**, **Steps** and the resulting **Step Size**) only once a **Value Column** is set; the range is used only then.

The node's **Polars** tab adds steps that run after the expansion, on the expanded frame `df`, with the same step builder as a [Polars](polars.md) node. Add a **Free code** step, or click **Switch to code** to replace the steps with their code and edit it directly (this is one-way). Code assigns its result back to `df`.

**Before and after** (with `min_value: 200`, `max_value: 400`, `stepCount: 3`):

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

In the example above, the **Value Column** (`column_name`) is set to `"scenario_value"`, and the index column keeps its default name, `"scenario_index"`. Both endpoints are inclusive.

!!! warning "Row multiplication"
    The output has `rows × steps` records. 1,000 rows with 50 steps produces 50,000 rows. With large datasets, use the Optimisation node's `chunk_size` to process in batches rather than expanding the full dataset at once.

**See also:**

- [Optimisation](optimiser.md)  - find the best price subject to constraints
- [Apply Optimisation](optimiser-apply.md)  - apply saved results at deployment
