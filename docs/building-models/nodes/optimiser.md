# Optimisation

You've generated candidate prices with the Expander. Now you want to find the best price for each quote  - or the best set of rating factors  - subject to portfolio-level constraints like premium floors or claims caps.

!!! warning "Terminal node"
    This node does not pass data to downstream nodes, and it saves nothing automatically. Once a solve has finished, publish its result from the **EXPORT** pane: **Save to file** writes it to a JSON file, and **Log to MLflow** logs it to an MLflow experiment. On a frontier, the point you select is what gets published. After a save, **Use in Apply node** points a chosen [Apply Optimisation](optimiser-apply.md) node at the saved file, which is how your production pipeline applies the result.

**Online mode** optimises per-record using a Lagrangian solver  - a mathematical method that balances your objective against constraint penalties. You provide a grid of candidate prices (from an [Expander](scenario-expander.md)) and the optimiser selects the best price per quote while respecting portfolio-level constraints.

**Ratebook mode** optimises factor tables using coordinate descent  - an iterative method that adjusts one factor at a time while holding the others fixed. Instead of per-quote prices, it finds the best set of rating factors that satisfy your constraints. The candidate values for each factor come from the scenario values in the scored grid (see [Expander](scenario-expander.md)); there are no separate candidate settings.

Connect the scored, scenario-expanded data to the node. In ratebook mode, also connect the [Banding](banding.md) node that defines the rating factors; you can connect another input to take validation factors from. The node has no output.

The node's settings are split across five panes, **DATA**, **FACTORS**, **CONSTRAINTS**, **SOLVE** and **EXPORT**, described below in that order. A pane whose settings stop the solve shows a warning mark on its tab, and the **SOLVE** tab shows a running indicator during a solve. The node has no **COLUMNS** tab.

## The DATA pane

| Field | What it does |
|---|---|
| **MODE** | **Online** (the default) optimises a price per quote; **Ratebook** optimises factor tables. |
| **OBJECTIVES & CONSTRAINTS** | The connected input carrying the scored, scenario-expanded data. With one input connected it is used as it is; with several, choose one (**Select the Objectives & Constraints input to enable solving.** until you do). A choice that is no longer connected shows **The configured Objectives & Constraints input is not connected.** |
| **Column to maximise** | Under **OBJECTIVE**: the column the optimiser maximises, for example `predicted_income`. The solve is blocked until it is chosen. |
| **Row ID** | Under **COLUMN MAPPINGS**: the column identifying each quote. Defaults to `quote_id`. |
| **Scenario Index** | The column with the scenario step index, created by the [Expander](scenario-expander.md). Defaults to `scenario_index`. |
| **Scenario Value** | The column with the scenario value, created by the Expander. Defaults to `scenario_value`. |

If a mapped column is not in the input, the **SOLVE** pane names it (for example **Row ID uses "quote_id", which the input does not have.**).

## The FACTORS pane

In **Ratebook** mode the pane opens with **RATING FACTOR SOURCE**: the connected [Banding](banding.md) input that supplies the rating-factor levels. Choosing it ticks every factor it defines for optimisation. With no Banding node connected the pane says **No Banding nodes found. Add a Banding node to define rating factors.**; a Banding output with no valid levels is named, asking you to add labelled rules before selecting it.

**FACTORS** then lists the columns you can use, with **Search factors** to narrow the list:

| Column | What it does |
|---|---|
| **Ratebook** | Tick a factor to optimise its table. Only the **RATING FACTOR SOURCE**'s factors can be ticked, and only in **Ratebook** mode. Ratebook mode needs at least one. |
| **Validation** | Tick up to 12 columns (for example a region or channel) to break the result down by segment after the solve. The solver never sees them. Each must hold one value per quote. |

**Validation input** chooses the connected input the validation factors come from. It defaults to the **OBJECTIVES & CONSTRAINTS** input; another input must contain the **Row ID** column. A ticked validation factor that is not a column of the chosen input shows a warning with **Remove**.

## The CONSTRAINTS pane

**CONSTRAINTS** lists the constraints, each a bound on the total of one column across the portfolio. **Add** adds one on the first free column, starting at **at least** 0. With no constraints the pane says **No constraints: the optimiser maximises the objective alone. Add one to bound a column's total.**

Each constraint has:

| Field | What it does |
|---|---|
| Column | The column whose portfolio total is bounded. The list leaves out the objective and columns that already have a constraint. The **×** removes the constraint. |
| **at least** / **at most** | Whether the total must stay at or above the bound, or at or below it. |
| **Fixed** / **Sweep** | **Fixed** (the default) holds the bound at the value you type. **Sweep** turns the constraint into a frontier axis (see below). |
| Value | The bound, as an absolute portfolio total. Clearing it keeps the stored value, so a bound is never relaxed to 0 by accident. |
| **from** / **to** | For a swept constraint: the range of totals to sweep (**Required** until set). The minimum must be below the maximum. |
| **Auto range** | For a swept constraint: runs the pipeline and fills a viable range from the data. Click again (**Restart auto range**) to start over. |

Above the list, **Result:** says what the solve will produce: **single point**, or a frontier over the swept constraints with its number of solves.

**Efficient frontier.** The efficient frontier shows the best achievable trade-off between your objective and your constraints. Switch a constraint to **Sweep** to see how the optimum changes as its absolute portfolio total bound is tightened or relaxed; constraints you do not sweep stay at their bound at every frontier point. With nothing swept, the optimiser solves a single point. While a constraint is swept, the **FRONTIER** section shows **Points per swept constraint** (15 to start, at least 2). A frontier runs that many solves for each swept constraint, multiplied together, and one of more than 10,000 solves is refused: use fewer points or sweep fewer constraints. Frontier is available in both online and ratebook modes. Ratebook frontiers can be significantly more expensive because each frontier point may require another factor-table optimisation.

## The SOLVE pane

Once the input can be counted, the pane shows its size: **Quotes**, **Scenarios / quote** and **Total rows**. It warns when each quote has one scenario (**Each quote has one scenario, so the optimiser has nothing to choose between. Check the Scenario Index mapping.**) or when quotes have different numbers of scenarios.

- **Optimise** runs the solve (Ctrl+Enter from anywhere in the node's panel does the same). If anything blocks the solve, the button is disabled and a **Complete before optimising** list names each problem with a **Go to …** link to the pane that fixes it.
- While the solve runs, the pane shows its progress and a **Stop** button.
- **Config changed since last solve** appears when you change a setting after a solve, with **Re-run** to solve again.
- A finished solve reports **Converged** (or **Did not converge**) with the iterations, the number of quotes and the number of steps. A solve that did not converge also shows **Solver did not converge** with advice, usually to increase the maximum iterations or relax the tolerance. A failed solve shows **Optimisation failed** and the reason.

**SOLVER SETTINGS**

| Field | What it does |
|---|---|
| **Max iterations** | Maximum solver iterations. Defaults to 50. |
| **Tolerance** | How close to optimal the solution needs to be before stopping. Smaller values give more precise results but take longer. Defaults to `1e-6`. |
| **CD iterations** | Ratebook mode: maximum coordinate descent iterations. Defaults to 10. |
| **CD tolerance** | Ratebook mode: coordinate descent convergence tolerance. Defaults to `1e-3`. |
| **Chunk size** | How many rows of scored scenarios the solver reads at a time while it builds its grid. The box shows 500000 until you change it; until you do, Haute sizes the slices from its memory budget. A smaller value reads fewer rows at once. |

## The EXPORT pane

**PUBLISH** acts on the node's last finished solve. Before there is one it says **Nothing to publish yet. Run the optimiser from the Solve pane; its result can then be saved for an Apply Optimisation node or logged to MLflow.**

| Field | What it does |
|---|---|
| **Result to publish** | **Solved result**, or on a frontier one of the **Frontier point N (objective …)** entries. It follows the point selected in the results panel, and changing it here selects that point there too. |
| **File path** | Where **Save to file** writes the result, relative to the project folder. Leave it blank to use the suggested `output/optimiser_<node name>_<node id>.json` shown in the box. |
| **Version label** | The version recorded in the saved file, which an Apply Optimisation node can add to its output. Blank (**Automatic**) uses the node name and the time of the save. |

- **Save to file** writes the result as a JSON file. If the file already exists, the pane asks before replacing it (**Replace existing file** or **Cancel**). After a save, the pane shows where it went, with a list of the pipeline's Apply Optimisation nodes (**Choose an Apply Optimisation node...**) and **Use in Apply node**, which points that node at the saved file.
- **Log to MLflow** logs the result to the experiment set under **MLFLOW LOGGING**, and shows the run with **Open run** (or **Open in Databricks**). If the chosen destination is unavailable the pane says why with **Configure MLflow**; a connection or credentials failure offers **Test connection in MLflow settings**.
- **Download factor tables (CSV)** (ratebook results) downloads the factor tables. For a frontier point, click **Load factor tables for CSV** first.
- When the configuration has changed since the solve, the pane warns **The configuration has changed since this result was solved. Publishing records it as outdated.**, and the buttons read **Save outdated result** and **Log outdated result**.

A ratebook result also states its **Combined factor collar**: the optimiser scored only this range of factor products, so the Apply Optimisation node clips each quote's product of factors to it, and you should apply it in any other rating engine too. The CSV repeats it on every row (see [The combined factor collar](optimiser-apply.md#the-combined-factor-collar)). A ratebook result without a collar cannot be published and asks you to re-run the solve.

**MLFLOW LOGGING**

| Field | What it does |
|---|---|
| MLflow destination | **Databricks**, **MLflow server** or **Local folder** (the default, the project's local MLflow folder). A remote that is not configured is greyed out, and clicking it opens MLflow settings instead. |
| **Experiment path** | The MLflow experiment the result is logged into. On Databricks it is a workspace folder path; on an MLflow server or local folder it is a plain name. Leave it blank to use the default shown in the box: the node's name, or `/Shared/haute/<node name>` on Databricks. |

## Reading the result

A finished solve opens in a results panel under the canvas with these views:

- **Frontier** (when a frontier was computed): one point per solve at a different constraint target: the highest expected objective found at that level. Select a point to inspect it; the selected point is what the **EXPORT** pane publishes. With more than one swept constraint, **X axis:** chooses the constraint to plot against and **Holding … at** chooses the values the others are held at. The arrows in the panel header step to the previous or next point.
- **Summary**: the expected objective and constraint totals, whether the solver converged, and how many quotes moved up or down.
- **Rates** (ratebook): the rate chosen for each factor level. A quote's combined factor is the product of its levels' rates, collared to the scenario range the solve scored.
- **Adjustments**: how many quotes (or how much weight) sit at each scenario value of the grid, where 1.0 is the base price.
- **Segments**: the mean chosen scenario value for each level of a validation or rating factor, with the shares adjusted up, down and at the edge of the range.
- **Quotes**: the scenario value chosen for each quote, with its expected objective and constraint values.
- **Convergence**: how the objective, the constraint totals and the solver's multipliers settled over the iterations.

When the node's settings have changed since the solve, the results panel says so and offers **Re-run**.

## Example

Maximise income while keeping premium at or above 1,000,000 and claims at or below 650,000:

1. Connect the scored, scenario-expanded data to an Optimisation node.
2. In the **DATA** pane, leave **MODE** on **Online** and set **Column to maximise** to `predicted_income`.
3. In the **CONSTRAINTS** pane, click **Add**, choose `premium`, leave **at least** and type `1000000`. Click **Add** again, choose `claims`, pick **at most** and type `650000`.
4. In the **SOLVE** pane, click **Optimise**.

To see the trade-off instead, switch `claims` to **Sweep**, fill in **from** and **to** (or click **Auto range**), and optimise again: the result opens on the **Frontier** view.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/optimisation/<node name>.json`, which the pipeline's `.py` file names in the node's decorator: `@pipeline.optimiser(config="config/optimisation/<node name>.json")`.

    | Setting in the editor | Stored as |
    |---|---|
    | **MODE** | `mode`: `"online"` or `"ratebook"` |
    | **OBJECTIVES & CONSTRAINTS** | `data_input`: the exact input name of the connected edge. **Required** when more than one input is connected; a sole connected input is used as it is. |
    | **Column to maximise** | `objective` |
    | **Row ID** | `quote_id` |
    | **Scenario Index** | `scenario_index` |
    | **Scenario Value** | `scenario_value` |
    | **RATING FACTOR SOURCE** | `banding_source`: the exact input name of the connected Banding edge. **Required** in ratebook mode. |
    | **Ratebook** tick boxes | `factor_columns`: a list of factor groups, each a one-item list such as `[["driver_age_band"]]` |
    | **Validation input** | `analysis_input`: the exact input name of the connected edge; absent uses `data_input` |
    | **Validation** tick boxes | `analysis_columns`: up to 12 column names |
    | Constraints | `constraints`: a map of column to `{"min": …}` (**at least**) or `{"max": …}` (**at most**). May be empty. |
    | **Sweep**, **from**, **to** | `frontier_ranges`: a map of swept constraint to its absolute `min` and `max` totals; a frontier is computed when any constraint has one |
    | **Points per swept constraint** | `frontier_steps` (defaults to 15) |
    | **Max iterations** | `max_iter` (defaults to 50) |
    | **Tolerance** | `tolerance` (defaults to `1e-6`) |
    | **CD iterations** | `max_cd_iterations` (defaults to 10) |
    | **CD tolerance** | `cd_tolerance` (defaults to `1e-3`) |
    | **Chunk size** | `chunk_size`; absent lets Haute size the slices |
    | **File path** | `result_export_path`; absent uses the suggested path |
    | MLflow destination | `mlflow_destination`: `"databricks"` or `"server"`; absent for the local folder |
    | **Experiment path** | `mlflow_experiment` |

    The **Version label** is not stored. The example above is stored as:

    ```json
    {
      "mode": "online",
      "objective": "predicted_income",
      "constraints": {
        "premium": { "min": 1000000 },
        "claims": { "max": 650000 }
      }
    }
    ```

    and sweeping both constraints adds, in absolute portfolio totals rather than multipliers:

    ```json
    {
      "frontier_ranges": {
        "premium": { "min": 900000, "max": 1200000 },
        "claims": { "min": 450000, "max": 650000 }
      }
    }
    ```

**See also:**

- [Expander](scenario-expander.md)  - generate candidate prices for the optimiser
- [Apply Optimisation](optimiser-apply.md)  - apply saved results to fresh data at deployment
