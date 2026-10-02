# Explore

You want to understand a dataset before you model or rate it  - how many rows, which columns are missing values, what the categories look like, how a measure breaks down by segment. The Explore node profiles the full data at any step of your pipeline without becoming part of it.

!!! info "When to use"
    - Checking a data source for missing values, constant columns, duplicates or outliers.
    - Seeing the distinct values of a categorical column before you band or rate it.
    - Building pivot tables and charts of any intermediate step.

This node takes a single input and has no output: it is a terminal, analysis-only branch. Connect it to the step you want to inspect.

The node's panel splits its settings into panes, **TRANSFORM**, **OVERVIEW**, **PIVOTS** and **CHARTS**, described below in that order. The results appear in the node's own preview under the canvas (see [The preview](#the-preview)). The node has no **COLUMNS** tab.

## The TRANSFORM pane

See [Transform](transform.md#building-the-node-from-steps).

## The OVERVIEW pane

**CARDS** lists the overview cards the preview can show. Click a card to switch it on or off; every card starts off.

| Card | What it shows |
|---|---|
| **Dataset Snapshot** | Rows, source, upstream node, and cached time. |
| **Schema** | Field-level types, nulls, distinct counts, min, and max. |
| **Numeric Summary** | Numeric fields only, with distribution, spread, missingness, zeros, and negatives. |
| **Categorical Summary** | Non-numeric fields, distinct counts, and bounded value-count expansion. |
| **Data Quality** | Missing, constant, negative, and mostly-zero signals. |

## The PIVOTS pane

**PIVOTS** lists the node's pivot tables. **Add Pivot** adds one; each card has a switch that shows or hides the pivot in the preview, **Configure** to edit it, and a delete button. A pivot that a chart uses cannot be deleted until you reassign or remove the chart (the card says **Used by** and the chart's name). With no pivots the pane says **No pivots yet. Add one to start defining a pivot layout.**

**Configure** opens the pivot, top to bottom (**Back to pivots** returns to the list):

| Control | What it does |
|---|---|
| **Pivot name** | The pivot's name. It must be unique and cannot be blank. |
| **Search pivot fields** | Narrows the list of the data's columns. Each column shows its type and **Add to:** buttons, **Filters**, **Columns**, **Rows** and **Values**, which place it in that area. |
| **FORMULAS** | The node's shared calculated fields, which any of its pivots can add to its values. **Add formula** opens the editor: a **Formula name** and a **Polars expression** that returns one aggregate per group, such as `pl.col("claims").sum() / pl.col("exposure").sum()`. While the editor is open, each column's **Add to:** buttons become **Formula**, which inserts `pl.col("…")` at the cursor. **Save formula** keeps it; **Delete formula** removes it from every pivot, after asking. **Add to: Values** adds a formula to this pivot. |
| **FILTERS**, **COLUMNS**, **ROWS**, **VALUES** | The pivot's areas. Drag a field between areas or reorder it (the arrow keys move it too), and remove it with its **×**. A filter field has a member picker to choose which values to keep (**All members** by default); its members are available once the data is cached. Each value has an aggregation: **Sum** (the default for a numeric field), **Count** (the default otherwise), **Average**, **Min**, **Max**, **Median** or **Distinct count**. A text field offers **Count**, **Distinct count**, **Min** and **Max**. |
| **SORTING** | **Sort by** the row labels (the default) or a row or value field, and the **Order**: **A → Z** / **Z → A**, or **Low → High** / **High → Low** for a numeric value. |
| **FORMATTING** | For each numeric column, row, value or formula: a **Format** (**General**, **Number**, **Percentage**, **Currency (£ GBP)**, **Currency (US$ USD)** or **Currency (€ EUR)**), **Decimal places** (**Automatic** or a fixed number) and **Thousands separator (,)**. |
| **CONDITIONAL FORMATTING** | **Add rule** colours a value with a scale. Each rule has a **Value field**, a **Colour scale** (**Low red → High green** or **Low green → High red**) and **Split scale by** (**None - entire Value**, or a row or column field). |
| **Show row grand totals**, **Show column grand totals** | Add the grand totals. |

## The CHARTS pane

**CHARTS** lists the node's charts, each built from one of its pivots. **Add Chart** adds one; each card has a switch that shows or hides it in the preview, **Configure** and a delete button. The charts pane handles only how a chart looks: its data comes from the source pivot.

**Configure** opens the chart (**Back to charts** returns to the list):

| Control | What it does |
|---|---|
| **Chart name** | The chart's name. It must be unique and cannot be blank. |
| **Source pivot** | The pivot the chart draws. Changing it resets the chart's mappings and series overrides, after asking. The source pivot needs at least one value or calculated field. |
| **Chart type** | A preset: **Clustered columns**, **Stacked columns**, **100% stacked columns** or **Combo**. |
| **Orientation** | **Vertical columns** or **Horizontal bars**. |
| **Primary axis** | The axis title, number format (**General (automatic)** or a specific format) and minimum and maximum (**Automatic** by default). |
| **Secondary axis** | Tick it to add a second value axis, with the same settings. |
| **Legend** | Tick it to show the legend, and choose its **Legend position**. |
| Series settings | For each value, and for any series you override (**Series overrides**): the mark (**Column**, **Line** or **Area**), the axis (**Primary** or **Secondary**), the stacking (**None**, **Stacked** or **100% stacked**) and stack group, the colour, markers and data labels. |
| **Category label rotation** | Rotates the category axis labels. |

## The preview

The node's preview has five panes, **PREVIEW**, **OVERVIEW**, **PIVOTS**, **CHARTS** and **RELATIONSHIPS**. **PREVIEW** shows the explored frame's rows. The other panes read the node's full data once it has been cached: click **Refresh** on the preview to cache it and profile it. A progress bar shows the work, **Refresh** becomes **Stop** while it runs, and **Retry profile** appears if profiling fails.

- The preview's **OVERVIEW** shows the cards switched on in the panel's **OVERVIEW** pane (**No cards enabled** until you switch one on).
- The preview's **PIVOTS** shows each pivot switched on in the panel's **PIVOTS** pane. A pivot calculates automatically once the data is cached, and updates when you change it.
- The preview's **CHARTS** shows each chart switched on in the panel's **CHARTS** pane, calculating its source pivot as needed.
- **RELATIONSHIPS** is described below.

The overview tables carry icons to copy or download them, and each chart has **Download image**.

### What the profile shows

For every column: its type, null count, NaN count (for float columns), distinct count, and  - depending on the type  - the minimum and maximum, quartiles, mean and standard deviation, zero and negative counts, text lengths, the time span, or the most frequent values (up to 50).

The data-quality summary lists columns with missing values, numeric columns with NaN values, constant columns, numeric columns with negative values, mostly-zero columns, high-cardinality columns and duplicate rows, each with a severity.

!!! note "Not part of scoring"
    An Explore node never changes the data that flows to the rest of the pipeline, and it is removed from a deployed pricing API. Editing only its cards, pivots or charts does not re-run anything upstream.

!!! note "Full data, computed once"
    The profile covers the whole dataset at that step, not a preview sample. It is computed once per version of the data and reused when you reopen the node; it is recomputed only after the data it reads has changed.

### Relationships

The **RELATIONSHIPS** pane answers two questions over the whole dataset:

- **Which features move a target?** Choose a numeric (or true/false) **Target**, an optional **Weight** and up to 50 **Features**. Haute ranks the features by how much of the target's variance their levels explain; expand a feature to see each level's rows, weight and target mean.
- **Do these columns identify a row?** Choose up to 8 **Key columns**. Haute reports whether they are unique, and if not, how many duplicate rows and rows with a missing key value there are.

The pane reads the node's cached data, so cache the data first if it asks you to. These choices are not saved with the node.

## Example

Checking claim frequency by area:

1. Connect an Explore node to the step you want to inspect.
2. In the panel's **OVERVIEW** pane, switch on **Data Quality** and **Categorical Summary**.
3. In the panel's **PIVOTS** pane, click **Add Pivot**, then **Configure**. Add `area` to **Rows**, and `claim_count` and `exposure` to **Values** (both **Sum**). Under **FORMULAS**, add a formula named `frequency` with the expression `pl.col("claim_count").sum() / pl.col("exposure").sum()` and add it to **Values**.
4. Click **Refresh** on the preview. The preview's **OVERVIEW** shows the two cards, and its **PIVOTS** shows the table with a frequency for each area.

??? note "In the pipeline file"
    The node's settings are stored as arguments of its decorator in the pipeline's `.py` file, `@pipeline.explore(...)`; it has no JSON sidecar. When the node has **TRANSFORM** steps, their generated code is the body of the node's function, which receives the input as `df`.

    | Setting in the editor | Stored as |
    |---|---|
    | **TRANSFORM** steps | `steps`, a decorator argument; their generated code in the function body |
    | **OVERVIEW** cards | `overview`: `dataset_snapshot`, `data_quality`, `numeric_summary`, `categorical_summary` and `schema`, each `true` when switched on (an off card is left out) |
    | **PIVOTS** | `pivots`: each pivot with its filter, column, row and value placements, sorting, formatting and totals |
    | **FORMULAS** | `pivot_formulas`: the node's shared library of calculated fields |
    | **CHARTS** | `charts`: each chart with its source pivot and formatting |

    Empty settings are left out, so a new node's decorator is bare.

    The column metadata any node may carry, `selected_columns`, `column_renames` and
    `categorical_levels`, has no editor control on an Explore node; when present it is
    stored as decorator arguments too.

**See also:**

- [Transform](transform.md)  - shaping the data you explore
