# Optimiser

You've generated candidate prices with the Scenario Expander. Now you want to find the best price for each quote  - or the best set of rating factors  - subject to portfolio-level constraints like premium floors or claims caps.

!!! warning "Terminal node"
    This node saves results but does not pass data to downstream nodes. Results are saved as artifacts that can be loaded by [Optimiser Apply](optimiser-apply.md) in your production pipeline.

**Online mode** optimises per-record using a Lagrangian solver  - a mathematical method that balances your objective against constraint penalties. You provide a grid of candidate prices (from a [Scenario Expander](scenario-expander.md)) and the optimiser selects the best price per quote while respecting portfolio-level constraints.

**Ratebook mode** optimises factor tables using coordinate descent  - an iterative method that adjusts one factor at a time while holding the others fixed. Instead of per-quote prices, it finds the best set of rating factors that satisfy your constraints.

| Config | Description |
|---|---|
| `mode` | **Required.** `"online"` or `"ratebook"` |
| `data_input` | The exact input name of the connected edge carrying the scored, scenario-expanded data. **Required** when more than one input is connected; a sole connected input is used as-is. |
| `banding_source` | The exact input name of the connected [Banding](banding.md) edge that supplies rating-factor levels. **Required** in `"ratebook"` mode. |
| `quote_id` | **Required.** Column identifying each quote |
| `scenario_index` | **Required.** Column with the scenario step index (created by [Scenario Expander](scenario-expander.md)) |
| `scenario_value` | **Required.** Column with the scenario value (created by [Scenario Expander](scenario-expander.md)) |
| `objective` | **Required.** Column to maximise (e.g. `"predicted_income"`) |
| `constraints` | **Required.** Named sum constraints with absolute (`min`/`max`) bounds |
| `max_iter` | Maximum solver iterations |
| `tolerance` | How close to optimal the solution needs to be before stopping. Smaller values give more precise results but take longer. Typical values: 0.001 to 0.01. |
| `chunk_size` | Optional row slice size for chunked Parquet-to-grid ingestion. Use only when scored rows are already grouped by quote and ordered by scenario index. |
| `record_history` | Whether to save iteration-by-iteration convergence history |
| `mlflow_experiment` | MLflow experiment name for logging results |
| `model_name` | Model registry name for saving artifacts |

A typical constraint configuration:

```json
{
  "objective": "predicted_income",
  "constraints": {
    "premium": { "min": 1000000 },
    "claims": { "max": 650000 }
  }
}
```

This tells the optimiser: maximise the objective column, but keep premium at or above 1,000,000 and claims at or below 650,000.

??? info "Ratebook-specific options"
    | Config | Description |
    |---|---|
    | `factor_columns` | **Required.** Factor columns to optimise |
    | `candidate_min` | **Required.** Minimum candidate factor value |
    | `candidate_max` | **Required.** Maximum candidate factor value |
    | `candidate_steps` | **Required.** Number of candidate values per factor |
    | `max_cd_iterations` | Maximum coordinate descent iterations |
    | `cd_tolerance` | Coordinate descent convergence tolerance |
    | `structure_mode` | `"explicit"` (you define the factor structure) or `"auto"` (inferred from the data) |

??? info "Efficient frontier"
    The efficient frontier shows the best achievable tradeoff between your objective and your constraints. Enable it to see how the optimum changes as absolute portfolio total bounds are tightened or relaxed.

    Frontier is available in both online and ratebook modes. Ratebook frontiers can be significantly more expensive because each frontier point may require another factor-table optimisation.

    Prefer `frontier_ranges` for new configs. Each range is keyed by constraint name and uses absolute portfolio totals, not multipliers:

    ```json
    {
      "frontier_ranges": {
        "premium": { "min": 900000, "max": 1200000 },
        "claims": { "min": 450000, "max": 650000 }
      }
    }
    ```

    | Config | Description |
    |---|---|
    | `frontier_enabled` | Whether to compute an efficient frontier after the individual-point solve |
    | `frontier_ranges` | Preferred absolute `min`/`max` portfolio totals for each constraint |
    | `frontier_steps` | Number of points per constraint dimension on the frontier |

**See also:**

- [Scenario Expander](scenario-expander.md)  - generate candidate prices for the optimiser
- [Optimiser Apply](optimiser-apply.md)  - apply saved results to fresh data at deployment
