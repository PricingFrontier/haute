# Apply Optimisation

You've run the [Optimisation](optimiser.md) node and saved the results. Now you want to apply those results  - the per-quote optimisation parameters (online mode) or the factor tables (ratebook mode)  - to fresh data at deployment time.

!!! info "When to use"
    Use this to apply saved optimisation results to fresh data  - typically in your production pipeline. The Optimisation node itself is run during development; Apply Optimisation loads the saved results at deployment time.

An online artifact applies to the node's single input. A ratebook artifact may have several
connected inputs and applies to the one named by `ratebook_input`.

| Config | Description |
|---|---|
| `sourceType` | **Required.** `"file"`, `"registered"`, or `"run"` |
| `ratebook_input` | The exact input name of the connected edge a ratebook artifact is applied to. **Required** for ratebook artifacts, even with one connected input. Ignored for online artifacts. |
| `artifact_path` | Path to the saved optimiser artifact. Required when sourceType is `"file"`. |
| `registered_model` | Model registry name. Required when sourceType is `"registered"`. |
| `version` | Version or `"latest"`. Use either `version` or `alias` when sourceType is `"registered"`. |
| `alias` | A registered model alias such as `"champion"`; applies whichever version it targets. |
| `experiment_id` | MLflow experiment ID. Required when sourceType is `"run"`. |
| `run_id` | MLflow run ID. Required when sourceType is `"run"`. |
| `mlflow_destination` | `"databricks"` or `"server"` to load from that MLflow destination; leave it out to use the project's local MLflow folder |
| `version_column` | Column name for version tracking. Defaults to `"__optimiser_version__"`. |
| `optimised_value_column` | The editor's **Optimised Value Column**: the name of the column holding the selected value, the chosen scenario value (online) or the combined factor (ratebook). A new node sets it to `"optimised_value"`; leave it empty to keep the names `optimal_scenario_value` (online) and `optimised_factor` (ratebook). |

## Example

```json
{
  "sourceType": "registered",
  "registered_model": "motor_pricing_optimiser",
  "version": "latest"
}
```

This loads the latest version of the `motor_pricing_optimiser` model from the registry and applies it to incoming data.

## What it does to the data

In **online mode**, the node chooses each quote's scenario and outputs one row per quote. It does not keep the input columns; the output has a fixed set of columns:

- `quote_id`
- `optimal_step`: the chosen scenario index
- the chosen scenario value (for example a price multiplier), named by `optimised_value_column` (`optimised_value` on a new node, or `optimal_scenario_value` when the setting is empty)
- `optimal_objective`
- `optimal_<constraint>` for each constraint
- the `version_column`, when the saved result records a version

Join it back to your other data downstream if you need their columns.

In **ratebook mode**, the node applies the optimised factor tables: the output keeps every input column and adds the factors. Each factor table adds a `<table>_optimised_factor` column, and their product is the combined factor, named by `optimised_value_column` (`optimised_value` on a new node, or `optimised_factor` when the setting is empty). A level the tables have never seen rates `1.0`. The `version_column` is added when the saved result records a version.

### The combined factor collar

The optimiser scored every quote at a scenario value inside the range of its scenario grid: when a quote's factor product fell outside that range, the solve priced it at the nearest end. The saved ratebook records that range as `combined_factor_bounds`, for example `{"min": 0.9, "max": 1.1}`, and the node clips the combined factor column to it. So a quote whose factors multiply to `1.2` deploys at `1.1`, the value the optimiser evaluated for it. The individual `<table>_optimised_factor` columns are not clipped.

If you take the factor tables into another rating engine (the **Download factor tables (CSV)** button in the Publish section of the Optimisation node's **Export** pane), apply the same collar there: the CSV and the Publish section both state it. A ratebook artifact without `combined_factor_bounds` is rejected when it is applied.

## Source types

| Source type | Use case | Required config |
|---|---|---|
| `file` | Local development  - loads from a path on disk | `artifact_path` |
| `registered` | Production  - loads from the MLflow model registry | `registered_model`, and `version` or `alias` |
| `run` | Loads from a specific training experiment run | `experiment_id`, `run_id` |

**See also:**

- [Optimisation](optimiser.md)  - run optimisation during development
- [Expander](scenario-expander.md)  - generate scenario combinations for optimisation
