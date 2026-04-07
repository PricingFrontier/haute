# Scenario Expander

You want to test a range of candidate prices for each quote  - say, 50 price points between 200 and 800  - so the optimiser can pick the best one. The Scenario Expander generates those candidates by cross-joining each row with a range of values.

!!! tip "Spreadsheet equivalent"
    Similar to a data table or sensitivity analysis in Excel, but integrated into the pipeline so the [Optimiser](optimiser.md) can act on the results.

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

In the example above, `column_name` is set to `"scenario_value"` and `step_column` is set to `"scenario_index"`. Both endpoints are inclusive.

!!! warning "Row multiplication"
    The output has `rows × steps` records. 1,000 rows with 50 steps produces 50,000 rows. With large datasets, use the Optimiser's `chunk_size` to process in batches rather than expanding the full dataset at once.

**See also:**

- [Optimiser](optimiser.md)  - find the best price subject to constraints
- [Optimiser Apply](optimiser-apply.md)  - apply saved results at deployment
