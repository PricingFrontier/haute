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
| `code` | Optional Polars code that shapes the frame being explored  - the input is available as `df`. See [Polars](polars.md) for code syntax. |

In the UI you build all of these from the node's tabs; you don't need to write them by hand.

## What the profile shows

For every column: its type, null count, NaN count (for float columns), distinct count, and  - depending on the type  - the minimum and maximum, quartiles, mean and standard deviation, zero and negative counts, text lengths, the time span, or the most frequent values (up to 50).

The data-quality summary lists columns with missing values, numeric columns with NaN values, constant columns, numeric columns with negative values, mostly-zero columns and duplicate rows, each with a severity.

!!! note "Not part of scoring"
    An Explore node never changes the data that flows to the rest of the pipeline, and it is removed from a deployed pricing API. Editing only its cards, pivots or charts does not re-run anything upstream.

!!! note "Full data, computed once"
    The profile covers the whole dataset at that step, not a preview sample. It is computed once per version of the data and reused when you reopen the node; it is recomputed only after the data it reads has changed.

**See also:**

- [Polars](polars.md)  - shaping the data you explore
- [Preparing Your Data](../preparing-your-data.md)  - a walkthrough of cleaning data before modelling
