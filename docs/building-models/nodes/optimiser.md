# Optimisation

You've generated candidate prices with the Expander. Now you want to find the best price for each quote  - or the best set of rating factors  - subject to portfolio-level constraints like premium floors or claims caps.

!!! warning "Terminal node"
    This node does not pass data to downstream nodes, and it saves nothing automatically. Once a solve has finished, publish its result from the **Export** pane: **Save to file** writes it to `result_export_path`, and **Log to MLflow** logs it to `mlflow_experiment`. On a frontier, the point you select is what gets published. After a save, **Use in Apply node** points a chosen [Apply Optimisation](optimiser-apply.md) node at the saved file, which is how your production pipeline applies the result.

**Online mode** optimises per-record using a Lagrangian solver  - a mathematical method that balances your objective against constraint penalties. You provide a grid of candidate prices (from an [Expander](scenario-expander.md)) and the optimiser selects the best price per quote while respecting portfolio-level constraints.

**Ratebook mode** optimises factor tables using coordinate descent  - an iterative method that adjusts one factor at a time while holding the others fixed. Instead of per-quote prices, it finds the best set of rating factors that satisfy your constraints.

| Config | Description |
|---|---|
| `mode` | **Required.** `"online"` or `"ratebook"` |
| `data_input` | The exact input name of the connected edge carrying the scored, scenario-expanded data. **Required** when more than one input is connected; a sole connected input is used as-is. |
| `banding_source` | The editor's **Rating Factor Source**: the exact input name of the connected [Banding](banding.md) edge that supplies rating-factor levels. **Required** in `"ratebook"` mode. |
| `quote_id` | **Required.** Column identifying each quote |
| `scenario_index` | **Required.** Column with the scenario step index (created by [Expander](scenario-expander.md)) |
| `scenario_value` | **Required.** Column with the scenario value (created by [Expander](scenario-expander.md)) |
| `objective` | **Required.** Column to maximise (e.g. `"predicted_income"`) |
| `constraints` | Named sum constraints with absolute (`min`/`max`) bounds. May be empty: with no constraints, the optimiser maximises the objective alone. |
| `max_iter` | Maximum solver iterations. Defaults to 50. |
| `tolerance` | How close to optimal the solution needs to be before stopping. Smaller values give more precise results but take longer. Defaults to `1e-6`. |
| `chunk_size` | Optional row slice size for chunked Parquet-to-grid ingestion. Use only when scored rows are already grouped by quote and ordered by scenario index. |
| `mlflow_experiment` | MLflow experiment that **Log to MLflow** logs results to |
| `mlflow_destination` | `"databricks"` or `"server"` to log to that MLflow destination; leave it out to use the project's local MLflow folder |
| `result_export_path` | Where **Save to file** writes the result, relative to the project folder. Leave it unset to use the suggested `output/optimiser_<node>_<id>.json`. |
| `analysis_input` | The exact input name of the connected edge to take analysis columns from. Any connected input; leave it unset to use `data_input`. |
| `analysis_columns` | Up to 12 columns (for example a region or channel) kept per quote only to break the result down by segment; the **Factors** pane calls them **Validation** factors. They are never given to the solver. Each must hold one value per quote, and the chosen input must contain the `quote_id` column. |

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
    The candidate values for each factor come from the scenario values in the scored grid (see [Expander](scenario-expander.md)); there are no separate candidate settings.

    | Config | Description |
    |---|---|
    | `factor_columns` | **Required.** The rating factors to optimise, chosen by ticking them in the **Factors** pane from the levels the `banding_source` input supplies |
    | `max_cd_iterations` | Maximum coordinate descent iterations. Defaults to 10. |
    | `cd_tolerance` | Coordinate descent convergence tolerance. Defaults to `1e-3`. |

??? info "Efficient frontier"
    The efficient frontier shows the best achievable tradeoff between your objective and your constraints. Switch a constraint to **Sweep** to see how the optimum changes as its absolute portfolio total bound is tightened or relaxed; constraints you do not sweep stay at their bound at every frontier point. With nothing swept, the optimiser solves a single point.

    Each swept constraint needs a range: fill in its **from** and **to** totals, or click **Auto range** to run the pipeline and fill a viable range from the data. The **Constraints** pane shows what the solve will produce, a single point or a frontier with its number of solves. A frontier runs `frontier_steps` solves for each swept constraint, multiplied together, and one of more than 10,000 solves is refused: use fewer points or sweep fewer constraints.

    Frontier is available in both online and ratebook modes. Ratebook frontiers can be significantly more expensive because each frontier point may require another factor-table optimisation.

    Each swept constraint has an entry in `frontier_ranges`, keyed by constraint name, in absolute portfolio totals rather than multipliers:

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
    | `frontier_ranges` | Absolute `min`/`max` portfolio totals for each swept constraint; a frontier is computed when any constraint has one |
    | `frontier_steps` | Number of points per swept constraint on the frontier. Defaults to 15. |

## In the editor

The node's settings are split across five panes:

- **Data**: the mode, the input carrying the scored scenarios, the rating factor source (ratebook mode), the column mappings and the objective.
- **Factors**: tick the rating factors to optimise (ratebook mode) and the **Validation** factors to break the result down by.
- **Constraints**: the constraints, their bounds, and which ones to sweep for a frontier.
- **Solve**: the solver settings, and **Optimise** to run the solve.
- **Export**: publish the result with **Save to file** or **Log to MLflow**; see the note at the top of this page.

A finished solve opens in a results panel with these views:

- **Frontier** (when a frontier was computed): one point per solve at a different constraint target. Select a point to inspect it; the selected point is what the Export pane publishes.
- **Summary**: the expected objective and constraint totals, whether the solver converged, and how many quotes moved up or down.
- **Rates** (ratebook): the rate chosen for each factor level.
- **Adjustments**: how many quotes (or how much weight) sit at each scenario value of the grid, where 1.0 is the base price.
- **Segments**: the mean chosen scenario value for each level of a validation or rating factor, with the shares adjusted up, down and at the edge of the range.
- **Quotes**: the scenario value chosen for each quote, with its expected objective and constraint values.
- **Convergence**: how the objective, the constraint totals and the solver's multipliers settled over the iterations.

**See also:**

- [Expander](scenario-expander.md)  - generate candidate prices for the optimiser
- [Apply Optimisation](optimiser-apply.md)  - apply saved results to fresh data at deployment
