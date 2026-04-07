# Optimiser

You've generated candidate prices with the Scenario Expander. Now you want to find the best price for each quote  - or the best set of rating factors  - subject to portfolio-level constraints like volume retention or loss ratio targets.

!!! warning "Terminal node"
    This node saves results but does not pass data to downstream nodes. Results are saved as artifacts that can be loaded by [Optimiser Apply](optimiser-apply.md) in your production pipeline.

**Online mode** optimises per-record using a Lagrangian solver  - a mathematical method that balances your objective against constraint penalties. You provide a grid of candidate prices (from a [Scenario Expander](scenario-expander.md)) and the optimiser selects the best price per quote while respecting portfolio-level constraints.

**Ratebook mode** optimises factor tables using coordinate descent  - an iterative method that adjusts one factor at a time while holding the others fixed. Instead of per-quote prices, it finds the best set of rating factors that satisfy your constraints.

| Config | Description |
|---|---|
| `mode` | **Required.** `"online"` or `"ratebook"` |
| `quote_id` | **Required.** Column identifying each quote |
| `scenario_index` | **Required.** Column with the scenario step index (created by [Scenario Expander](scenario-expander.md)) |
| `scenario_value` | **Required.** Column with the scenario value (created by [Scenario Expander](scenario-expander.md)) |
| `objective` | **Required.** Column to maximise (e.g. `"predicted_income"`) |
| `constraints` | **Required.** Named constraints with min/max bounds |
| `max_iter` | Maximum solver iterations |
| `tolerance` | How close to optimal the solution needs to be before stopping. Smaller values give more precise results but take longer. Typical values: 0.001 to 0.01. |
| `chunk_size` | Number of quotes to optimise at once. Smaller values use less memory. Leave blank to process all quotes at once. |
| `record_history` | Whether to save iteration-by-iteration convergence history |
| `mlflow_experiment` | MLflow experiment name for logging results |
| `model_name` | Model registry name for saving artifacts |

A typical constraint configuration:

```json
{
  "objective": "predicted_income",
  "constraints": {
    "volume":     { "min": 0.90 },
    "loss_ratio": { "max": 0.65 }
  }
}
```

This tells the optimiser: maximise the objective column, but keep volume at or above 90% of baseline and loss ratio at or below 65%.

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
    The efficient frontier shows the best achievable tradeoff between your objective (e.g. profit) and your constraints (e.g. volume retention). Enable it to see how much profit you give up for each additional percentage point of volume.

    | Config | Description |
    |---|---|
    | `frontier_enabled` | Whether to compute the efficient frontier |
    | `frontier_points_per_dim` | Number of points per dimension on the frontier |
    | `frontier_threshold_ranges` | Constraint ranges to sweep for the frontier |

**See also:**

- [Scenario Expander](scenario-expander.md)  - generate candidate prices for the optimiser
- [Optimiser Apply](optimiser-apply.md)  - apply saved results to fresh data at deployment
