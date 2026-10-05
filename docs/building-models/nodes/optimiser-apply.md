# Apply Optimisation

You've run the [Optimisation](optimiser.md) node and saved the results. Now you want to apply those results  - the per-quote optimisation parameters (online mode) or the factor tables (ratebook mode)  - to fresh data at deployment time.

!!! info "When to use"
    Use this to apply saved optimisation results to fresh data  - typically in your production pipeline. The Optimisation node itself is run during development; Apply Optimisation loads the saved results at deployment time.

An online result applies to the node's single input. A ratebook result may have several connected inputs and applies to the one you choose as its **RATEBOOK INPUT**. What the node outputs depends on the mode; see [What it does to the data](#what-it-does-to-the-data).

## The CONFIG tab

The **INPUT** strip at the top names the connected inputs; each **×** removes that connection. The fields below it appear in this order.

With **Registered** or **Experiment Run** chosen as the **ARTIFACT SOURCE**, the MLflow destination buttons (**Databricks**, **MLflow server** or **Local folder**) come first and choose where to look. Switching destination clears the selection and says **Selection cleared - run and model identifiers are not portable across destinations.**

Once the chosen result is known to be a ratebook, **RATEBOOK INPUT** comes next: the connected input the factor tables are applied to (**Select input...** until you choose). It is required for a ratebook result, even with one input connected. A choice that is no longer connected shows **Missing input**.

**ARTIFACT SOURCE** chooses where the saved result comes from:

| Source | Use case |
|---|---|
| **File Path** (the default) | Local development  - loads a result saved with the Optimisation node's **Save to file** |
| **Registered** | Production  - loads from the MLflow model registry |
| **Experiment Run** | Loads a result logged to a specific MLflow run |

With **File Path**:

| Field | What it does |
|---|---|
| **ARTIFACT PATH** | The saved optimiser result, a JSON file (placeholder `artifacts/optimiser_v1.json`). The Optimisation node's **Use in Apply node** fills it in for you. |

With **Registered** or **Experiment Run**:

| Field | What it does |
|---|---|
| **MODEL NAME** | Registered: the registered model (**Select a model...** until you choose). |
| **VERSION** | Registered: **latest** (the default), an alias such as **@champion → v3** (the node applies whichever version the alias targets), or a version. |
| **EXPERIMENT** | Experiment Run: the MLflow experiment to browse. |
| **RUN** | Experiment Run: the run, listed with its mode (for example `[ratebook]`) and total objective. |
| **RUN ID** | Experiment Run: the chosen run's ID. You can also paste one here. |

Then, for every source:

| Field | What it does |
|---|---|
| **VERSION COLUMN** | The column added to the output for monitoring and version tracking. Defaults to `__optimiser_version__`. |
| **OPTIMISED VALUE COLUMN** | The column holding the selected optimiser value: the chosen scenario value (online) or the combined factor (ratebook). A new node sets it to `optimised_value`; clear it to keep the names `optimal_scenario_value` (online) and `optimised_factor` (ratebook). |

At the foot of the tab, once a **File Path** result loads, a **LOADED ARTIFACT** summary shows its **Mode**, **Version**, **Created** date and **Objective**, with its **LAMBDAS** (online) or its **FACTOR TABLES** and their level counts (ratebook). A file that cannot be read shows **Could not load artifact file**.

## The COLUMNS tab

The **COLUMNS** tab chooses which output columns the node passes on; see [Working with any node](index.md#working-with-any-node).

## What it does to the data

In **online mode**, the node chooses each quote's scenario and outputs one row per quote. It does not keep the input columns; the output has a fixed set of columns:

- `quote_id`
- `optimal_step`: the chosen scenario index
- the chosen scenario value (for example a price multiplier), named by **OPTIMISED VALUE COLUMN** (`optimised_value` on a new node, or `optimal_scenario_value` when the setting is empty)
- `optimal_objective`
- `optimal_<constraint>` for each constraint
- the **VERSION COLUMN**, when the saved result records a version

Join it back to your other data downstream if you need their columns.

In **ratebook mode**, the node applies the optimised factor tables: the output keeps every input column and adds the factors. Each factor table adds a `<table>_optimised_factor` column, and their product is the combined factor, named by **OPTIMISED VALUE COLUMN** (`optimised_value` on a new node, or `optimised_factor` when the setting is empty). A level the tables have never seen rates `1.0`. The **VERSION COLUMN** is added when the saved result records a version.

### The combined factor collar

The optimiser scored every quote at a scenario value inside the range of its scenario grid: when a quote's factor product fell outside that range, the solve priced it at the nearest end. The saved ratebook records that range as `combined_factor_bounds`, for example `{"min": 0.9, "max": 1.1}`, and the node clips the combined factor column to it. So a quote whose factors multiply to `1.2` deploys at `1.1`, the value the optimiser evaluated for it. The individual `<table>_optimised_factor` columns are not clipped.

If you take the factor tables into another rating engine (the **Download factor tables (CSV)** button in the **PUBLISH** section of the Optimisation node's **EXPORT** pane), apply the same collar there: the CSV and the **PUBLISH** section both state it. A ratebook result without `combined_factor_bounds` is rejected when it is applied.

## Example

Applying the latest registered optimisation result in production:

1. Connect the scored data to an Apply Optimisation node.
2. Under **ARTIFACT SOURCE**, choose **Registered**, then choose `motor_pricing_optimiser` in **MODEL NAME** and leave **VERSION** on **latest**.
3. If the result is a ratebook, choose the input to rate in **RATEBOOK INPUT**.

The node loads the latest version of `motor_pricing_optimiser` from the registry and applies it to the incoming data.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/apply_optimisation/<node name>.json`, which the pipeline's `.py` file names in the node's decorator: `@pipeline.optimiser_apply(config="config/apply_optimisation/<node name>.json")`.

    | Setting in the editor | Stored as |
    |---|---|
    | **ARTIFACT SOURCE** | `sourceType`: `"file"`, `"registered"` or `"run"` |
    | **ARTIFACT PATH** | `artifact_path`: required when `sourceType` is `"file"` |
    | MLflow destination | `mlflow_destination`: `"databricks"` or `"server"`; absent for the local folder |
    | **MODEL NAME** | `registered_model`: required when `sourceType` is `"registered"` |
    | **VERSION** | `version` (a version or `"latest"`), or `alias` for an alias such as `"champion"`; one of the two when `sourceType` is `"registered"` |
    | **EXPERIMENT** | `experiment_id` (and `experiment_name`, the name the panel shows); required when `sourceType` is `"run"` |
    | **RUN**, **RUN ID** | `run_id` (and `run_name`, the name the panel shows); required when `sourceType` is `"run"` |
    | **RATEBOOK INPUT** | `ratebook_input`: the exact input name of the connected edge. **Required** for ratebook results, even with one connected input; ignored for online results. |
    | **VERSION COLUMN** | `version_column` (defaults to `"__optimiser_version__"`) |
    | **OPTIMISED VALUE COLUMN** | `optimised_value_column` |
    | **COLUMNS** tab | `selected_columns` |

    `optimiser_mode` (`"online"` or `"ratebook"`) has no editor control: the editor copies it from the chosen result so the pipeline file can wire up the ratebook input without reading the result.

    The example above is stored as:

    ```json
    {
      "sourceType": "registered",
      "registered_model": "motor_pricing_optimiser",
      "version": "latest"
    }
    ```

**See also:**

- [Optimisation](optimiser.md)  - run optimisation during development
- [Expander](scenario-expander.md)  - generate scenario combinations for optimisation
