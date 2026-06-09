# Node Types

Every step in a Haute pipeline is a node. You connect nodes on the canvas to define how data flows from source to output. Each node type is described on its own page.

!!! tip "First pipeline?"
    If you're building your first pipeline, a common path is: [Quote Input](quote-input.md) or [Data Source](data-source.md) → [Polars](polars.md) (clean your data) → [Banding](banding.md) and [Rating Step](rating-step.md) (build your rating structure) → [Output](output.md). You don't need every node type to get started.

!!! info "About the config examples"
    The JSON examples on these pages show the underlying configuration. In the Haute UI, you configure most of these through forms, dropdowns, and editable tables  - you don't need to write JSON by hand.

---

## Quick reference

| I want to... | Use this node |
|---|---|
| Bring in quote data for live pricing | [Quote Input](quote-input.md) |
| Load a CSV, parquet file, or Databricks table | [Data Source](data-source.md) |
| Store fixed parameters (tax rate, loadings) | [Constant](constant.md) |
| Join, filter, or calculate new columns | [Polars](polars.md) |
| Join another dataframe into an existing connection | [Edge Join](edge-join.md) |
| Convert ages or values into bands | [Banding](banding.md) |
| Look up rating factors from a table | [Rating Step](rating-step.md) |
| Score data with a trained model | [Model Score](model-score.md) or [External File](external-file.md) |
| Train a new model | [Model Training](model-training.md) |
| Optimise prices subject to constraints | [Scenario Expander](scenario-expander.md) + [Optimiser](optimiser.md) |
| Apply saved optimisation results | [Optimiser Apply](optimiser-apply.md) |
| Switch between live and batch data | [Source Switch](source-switch.md) |
| Choose which columns to return from the API | [Output](output.md) |
| Save results to a file | [Data Sink](data-sink.md) |
| Group nodes into a reusable block | [Submodel](submodel.md) |
| Reuse a node's logic with different inputs | [Instances](instances.md) |

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
│    Output     │◀────│   Polars      │◀────│ Rating Step   │
│ (return cols) │     │ (premium)     │     │ (factors)     │
└──────────────┘     └──────────────┘     └──────────────┘
```

**Step by step:**

1. **[Quote Input](quote-input.md)**  - Loads sample quotes containing `driver_age`, `area`, `vehicle_value`, and `year_of_manufacture`. This is the entry point of the pipeline.

2. **[Polars](polars.md)**  - Calculates `vehicle_age` from `year_of_manufacture` (e.g. current year minus manufacture year). This adds a new column to the table.

3. **[Banding](banding.md)**  - Bands `driver_age` into groups: 18–25, 26–65, and 65+. The output is a new column (`driver_age_band`) that the rating step can look up against.

4. **[Rating Step](rating-step.md)**  - Looks up `area_factor` and `age_factor` from rating tables using `area` and `driver_age_band`, then multiplies them into a `combined_factor`.

5. **[Polars](polars.md)**  - Calculates the final premium: `final_premium = base_rate * combined_factor`. This is a single expression that produces the price.

6. **[Output](output.md)**  - Selects the columns to return from the API: `quote_id`, `final_premium`, `area_factor`, and `age_factor`.

!!! tip "Try it yourself"
    You can recreate this pipeline on the canvas in a few minutes. Start with a Quote Input node, then chain each step by dragging a connection from one node's output to the next node's input.

---

## Inputs

Nodes that bring data into your pipeline. They have no upstream connections.

- **[Quote Input](quote-input.md)**  - entry point for live pricing; reads a preview file during development
- **[Data Source](data-source.md)**  - loads flat files (parquet, CSV) or Databricks tables
- **[Constant](constant.md)**  - stores fixed values like expense loadings or tax rates

## Transforms

- **[Polars](polars.md)**  - general-purpose node for joins, filters, and calculations
- **[Edge Join](edge-join.md)**  - compact join node created from canvas connections
- **[Banding](banding.md)**  - converts continuous or categorical values into bands
- **[Rating Step](rating-step.md)**  - looks up rating factors from tables and combines them
- **[Scenario Expander](scenario-expander.md)**  - generates a range of candidate values for each row (used with the Optimiser)
- **[Source Switch](source-switch.md)**  - toggles between live and batch data sources

## Models

- **[Model Training](model-training.md)**  - trains a CatBoost or GLM model
- **[Model Score](model-score.md)**  - scores data with an MLflow-managed model
- **[External File](external-file.md)**  - loads and scores a standalone model file

## Optimisation

- **[Optimiser](optimiser.md)**  - find the best price per quote (or the best factor table) subject to your constraints
- **[Optimiser Apply](optimiser-apply.md)**  - apply the saved results to new data at deployment time

## Pipeline outputs

- **[Output](output.md)**  - chooses which columns to return in the API response
- **[Data Sink](data-sink.md)**  - saves results to a file

## Organisation

- **[Submodel](submodel.md)**  - groups nodes into a collapsible, reusable block
- **[Instances](instances.md)**  - reuse a node's logic with different inputs

---

## Key terms

A quick glossary for terms you'll see throughout these docs. If you're coming from Excel, Earnix, or Radar, the analogies should feel familiar.

| Term | What it means |
|---|---|
| **Pipeline** | A chain of connected steps that transforms data into a price  - like a multi-tab workbook where each tab feeds the next. |
| **Node** | A single step in the pipeline. It might be a data source, a calculation, a model score, or an output. Each node type has its own page in this section. |
| **Canvas** | The visual editor workspace where you drag, drop, and connect nodes to build your pipeline. |
| **DataFrame / df** | A table of data (rows and columns), like a spreadsheet tab. In code, `df` is shorthand for this. |
| **Polars** | The data engine Haute uses under the hood. When you see `pl.col("x")` in code, it means "the column called x." Think of it as a formula language for tables. |
| **Parquet** | A file format for tabular data, like CSV but faster and smaller. You don't need to understand the internals  - just know it's a data file. |
| **MLflow** | An open-source platform Haute uses to track model training experiments and store trained models. Think of it as version control for models. |
| **JSON** | A text-based data format. Haute uses it for configuration files and preview data. You won't usually write it by hand  - the UI generates it. |
| **Terminal node** | A node that produces a result (a trained model, an optimisation output) but doesn't pass data to the next node in the pipeline. |
