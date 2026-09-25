# Optimiser Apply

You've run the Optimiser and saved the results. Now you want to apply those results  - the per-quote optimisation parameters (online mode) or the factor tables (ratebook mode)  - to fresh data at deployment time.

!!! info "When to use"
    Use this to apply saved optimisation results to fresh data  - typically in your production pipeline. The Optimiser itself is run during development; Optimiser Apply loads the saved results at deployment time.

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
| `version_column` | Column name for version tracking. Defaults to `"__optimiser_version__"`. |

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

In **online mode**, the node adds the optimal price for each quote as a new column. In **ratebook mode**, it applies the optimised factor tables  - each quote receives the adjusted factors. The output includes all columns from the input plus the optimisation results.

In ratebook mode each factor table adds a `<table>_optimised_factor` column, and their product is the combined `optimised_factor`. A level the tables have never seen rates `1.0`.

### The combined factor collar

The optimiser scored every quote at a scenario value inside the range of its scenario grid: when a quote's factor product fell outside that range, the solve priced it at the nearest end. The saved ratebook records that range as `combined_factor_bounds`, for example `{"min": 0.9, "max": 1.1}`, and the node clips `optimised_factor` to it. So a quote whose factors multiply to `1.2` deploys at `1.1`, the value the optimiser evaluated for it. The individual `<table>_optimised_factor` columns are not clipped.

If you take the factor tables into another rating engine (the **Download factor tables (CSV)** button on the optimiser's Publish section), apply the same collar there: the CSV and the Publish section both state it. A ratebook artifact without `combined_factor_bounds` is rejected when it is applied.

## Source types

| Source type | Use case | Required config |
|---|---|---|
| `file` | Local development  - loads from a path on disk | `artifact_path` |
| `registered` | Production  - loads from the MLflow model registry | `registered_model`, `version` |
| `run` | Loads from a specific training experiment run | `experiment_id`, `run_id` |

**See also:**

- [Optimiser](optimiser.md)  - run optimisation during development
- [Scenario Expander](scenario-expander.md)  - generate scenario combinations for optimisation
