# Explore

You want to understand a dataset before you model or rate it  - how many rows, which columns are missing values, what the categories look like, how a measure breaks down by segment. The Explore node profiles the full data at any step of your pipeline without becoming part of it.

!!! tip "Spreadsheet equivalent"
    Like a pivot table and a column summary on a scratch tab  - you look at the data from every angle, and nothing on that tab feeds the final price.

!!! info "When to use"
    - Checking a data source for missing values, constant columns, duplicates or outliers.
    - Seeing the distinct values of a categorical column before you band or rate it.
    - Building pivot tables and charts of any intermediate step.

This node accepts a single input and has no output: it is a terminal, analysis-only branch. Connect it to the step you want to inspect.

| Config | Description |
|---|---|
| `overview` | Which overview cards to show: `dataset_snapshot`, `data_quality`, `numeric_summary`, `categorical_summary` and `schema`, each `true` or `false` |
| `pivots` | Saved pivot tables, each with its filter, column, row and value placements |
| `charts` | Saved charts, each built from a pivot on the same node |
| `pivot_formulas` | The node's shared library of calculated fields that its pivots can use as values |
| `steps` | Optional steps that shape the frame being explored, built in the node's **Polars Code** pane  - they start from the input, `df`. See [Polars](polars.md) for the step builder. |
| `code` | Polars code that shapes the frame. While the node has steps, this is the code they generate; after **Switch to code** (one-way), it is the code you edit, and the input is available as `df`. |

In the UI you build all of these from the node's panes; you don't need to write them by hand.

## What the profile shows

For every column: its type, null count, NaN count (for float columns), distinct count, and  - depending on the type  - the minimum and maximum, quartiles, mean and standard deviation, zero and negative counts, text lengths, the time span, or the most frequent values (up to 50).

The data-quality summary lists columns with missing values, numeric columns with NaN values, constant columns, numeric columns with negative values, mostly-zero columns, high-cardinality columns and duplicate rows, each with a severity.

!!! note "Not part of scoring"
    An Explore node never changes the data that flows to the rest of the pipeline, and it is removed from a deployed pricing API. Editing only its cards, pivots or charts does not re-run anything upstream.

!!! note "Full data, computed once"
    The profile covers the whole dataset at that step, not a preview sample. It is computed once per version of the data and reused when you reopen the node; it is recomputed only after the data it reads has changed.

## Relationships

The node's preview has a **Relationships** pane beside Preview, Overview, Pivots and Charts. It answers two questions over the whole dataset:

- **Which features move a target?** Choose a numeric (or true/false) **Target**, an optional **Weight** and up to 50 **Features**. Haute ranks the features by how much of the target's variance their levels explain; expand a feature to see each level's rows, weight and target mean.
- **Do these columns identify a row?** Choose up to 8 **Key columns**. Haute reports whether they are unique, and if not, how many duplicate rows and rows with a missing key value there are.

The pane reads the node's cached data, so cache the data first if it asks you to. These choices are not saved with the node.

**See also:**

- [Polars](polars.md)  - shaping the data you explore
