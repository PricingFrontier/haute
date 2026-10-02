# Node Types

Every step in a Haute pipeline is a node. You connect nodes on the canvas to define how data flows from source to output. Each node type is described on its own page.

!!! tip "First pipeline?"
    If you're building your first pipeline, a common path is: [Quote Input](quote-input.md) or [Data Input](data-input.md) → [Polars](polars.md) (clean your data) → [Banding](banding.md) and [Rating Step](rating-step.md) (build your rating structure) → [Quote Response](output.md). You don't need every node type to get started.

!!! info "How these pages are written"
    Each page walks through the node's panel in the editor: what each tab, field and button is for. How the same settings are stored in the pipeline file is in a closed **In the pipeline file** section at the end of each page, for when you read the generated code or review a change.

---

## Quick reference

| I want to... | Use this node |
|---|---|
| Bring in quote data for live pricing | [Quote Input](quote-input.md) |
| Load a parquet or CSV file, a database, lakehouse or Databricks table | [Data Input](data-input.md) |
| Store fixed parameters (tax rate, loadings) | [Constant](constant.md) |
| Join, filter, or calculate new columns | [Polars](polars.md) |
| Join another dataframe into an existing connection | [Edge Join](edge-join.md) |
| Convert ages or values into bands | [Banding](banding.md) |
| Look up rating factors from a table | [Rating Step](rating-step.md) |
| Score data with a trained model | [Model Scoring](model-score.md) or [Load File](external-file.md) |
| Train a new model | [Model Training](model-training.md) |
| Optimise prices subject to constraints | [Expander](scenario-expander.md) + [Optimisation](optimiser.md) |
| Apply saved optimisation results | [Apply Optimisation](optimiser-apply.md) |
| Switch between live and batch data | [Source Switch](source-switch.md) |
| Choose which columns to return from the API | [Quote Response](output.md) |
| Profile a dataset, build pivot tables and charts | [Explore](explore.md) |
| Save results to a file or table | [Data Output](data-output.md) |
| Group nodes into a reusable block | [Submodel](submodel.md) |
| Reuse a node's logic with different inputs | [Instances](instances.md) |

---

## Working with any node

- **Adding and connecting nodes.** Drag a node from the **Nodes** palette on the left onto the canvas, then drag a connection from one node to the next. A connection carries the upstream node's data, under the upstream node's name; a connection from a Quote Input table carries it under the table's label, and one from a submodel output under the output's port name.
- **The node panel.** Click a node to open its panel on the right. Most nodes have tabs along its top:
    - **CONFIG** holds the node's own settings, described on its page. On a Polars node this tab is called **POLARS**, because the node's settings are its steps.
    - **POLARS**, on Data Input, Load File, Expander, Rating Step and Model Scoring nodes, adds optional steps that run on the node's result, built the same way as a Polars node's (see [Building the node from steps](polars.md#building-the-node-from-steps)).
    - **COLUMNS** lists the node's **Output Columns**: untick a column to stop the node passing it on. **Filter columns...** finds a column by name, and **All** and **None** tick or clear every box. The list appears once the node has been previewed. Quote Input, Quote Response, Model Training, Optimisation, Explore and Submodel nodes have no Columns tab.

    Model Training, Optimisation and Explore nodes split their settings into panes instead, described on their pages.
- **The preview.** Under the canvas, the preview shows the selected node's output. With **Calculation** set to **Automatic** in Pipeline settings (the toolbar's **Pipeline** button), clicking a node calculates its preview; set to **Manual**, a node shows its last result until you click **Refresh** (Ctrl+Enter). **Preview rows** in Pipeline settings sets how many rows a preview shows (0 means no limit), and **Search columns...** narrows the columns shown. Click a cell to trace how its value was calculated (see [Price tracing](../../getting-started/polars.md#price-tracing)).
- **Renaming, copying and deleting.** Right-click a node for **Rename**, **Duplicate** and **Delete**. A node's name is also the name downstream nodes use for its data.

---

## Example: your first pricing pipeline

Below is a simple motor pricing pipeline that takes in quote data, enriches it, applies rating factors, and returns a premium. Each box is a node on the canvas, and the arrows show the direction data flows.

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Quote Input   │────▶│   Polars      │────▶│   Banding     │
│ (load quotes) │     │ (vehicle_age) │     │ (driver_age)  │
└──────────────┘     └──────────────┘     └──────────────┘
                                                  │
                                                  ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│Quote Response │◀────│   Polars      │◀────│ Rating Step   │
│ (return cols) │     │ (premium)     │     │ (factors)     │
└──────────────┘     └──────────────┘     └──────────────┘
```

**Step by step:**

1. **[Quote Input](quote-input.md)**  - Loads sample quotes containing `driver_age`, `area`, `vehicle_value`, and `year_of_manufacture`. This is the entry point of the pipeline.

2. **[Polars](polars.md)**  - Calculates `vehicle_age` from `year_of_manufacture` (e.g. current year minus manufacture year). This adds a new column to the table.

3. **[Banding](banding.md)**  - Bands `driver_age` into groups: 18–25, 26–65, and 65+. The output is a new column (`driver_age_band`) that the rating step can look up against.

4. **[Rating Step](rating-step.md)**  - Looks up `area_factor` and `age_factor` from rating tables using `area` and `driver_age_band`, then multiplies them into a `combined_factor`.

5. **[Polars](polars.md)**  - Calculates the final premium: `final_premium = base_rate * combined_factor`. This is a single expression that produces the price.

6. **[Quote Response](output.md)**  - Selects the columns to return from the API: `quote_id`, `final_premium`, `area_factor`, and `age_factor`.

!!! tip "Try it yourself"
    You can recreate this pipeline on the canvas in a few minutes. Start with a Quote Input node, then chain each step by dragging a connection from one node's output to the next node's input.

---

## Inputs

Nodes that bring data into your pipeline. They have no upstream connections.

- **[Quote Input](quote-input.md)**  - entry point for live pricing; reads a preview file during development
- **[Data Input](data-input.md)**  - reads files (parquet, CSV and more), lakehouse, database or Databricks tables, or inline records
- **[Constant](constant.md)**  - stores fixed values like expense loadings or tax rates

## Transforms

- **[Polars](polars.md)**  - general-purpose node for joins, filters, and calculations
- **[Edge Join](edge-join.md)**  - compact join node created from canvas connections
- **[Banding](banding.md)**  - bands numeric or date values by breakpoints, or categorical values by mapping
- **[Rating Step](rating-step.md)**  - looks up rating factors from tables and combines them
- **[Expander](scenario-expander.md)**  - generates a range of candidate values for each row (used with Optimisation)
- **[Source Switch](source-switch.md)**  - toggles between live and batch data sources

## Models

- **[Model Training](model-training.md)**  - trains a CatBoost, XGBoost, LightGBM, EBM or GLM model
- **[Model Scoring](model-score.md)**  - scores data with an MLflow-managed model
- **[Load File](external-file.md)**  - loads and scores a standalone model file

## Optimisation

- **[Optimisation](optimiser.md)**  - find the best price per quote (or the best factor table) subject to your constraints
- **[Apply Optimisation](optimiser-apply.md)**  - apply the saved results to new data at deployment time

## Pipeline outputs

- **[Quote Response](output.md)**  - chooses which columns to return in the API response
- **[Data Output](data-output.md)**  - saves results to a file, lakehouse or database table

## Analysis

- **[Explore](explore.md)**  - profiles the data at any step and builds pivot tables and charts, without feeding the pipeline

## Organisation

- **[Submodel](submodel.md)**  - groups nodes into a collapsible, reusable block
- **[Instances](instances.md)**  - reuse a node's logic with different inputs

---

## Key terms

A quick glossary for terms you'll see throughout these docs.

| Term | What it means |
|---|---|
| **Pipeline** | A chain of connected steps that transforms data into a price. |
| **Node** | A single step in the pipeline. It might be a data source, a calculation, a model score, or an output. Each node type has its own page in this section. |
| **Canvas** | The visual editor workspace where you drag, drop, and connect nodes to build your pipeline. |
| **DataFrame / df** | A table of data (rows and columns). In code, `df` is shorthand for this. |
| **Polars** | The data engine Haute uses under the hood. When you see `pl.col("x")` in code, it means "the column called x." Think of it as a formula language for tables. |
| **Parquet** | A file format for tabular data, like CSV but faster and smaller. You don't need to understand the internals  - just know it's a data file. |
| **MLflow** | An open-source platform Haute uses to track model training experiments and store trained models. Think of it as version control for models. |
| **JSON** | A text-based data format. Haute uses it for configuration files and preview data. You won't usually write it by hand  - the UI generates it. |
| **Terminal node** | A node that produces a result (a trained model, an optimisation output) but doesn't pass data to the next node in the pipeline. |
