# Polars

This is the general-purpose node where you write code to shape your data. Joining two datasets, calculating a new column, filtering rows  - if there isn't a specialised node for it, you do it here. This is the node you'll use most often.

!!! tip "Spreadsheet equivalent"
    Think of this as the formula bar in a spreadsheet, but for entire columns at once. Instead of writing a formula in one cell and dragging it down, you write one expression and it applies to every row.

!!! info "When to use"
    - Joining two datasets together (e.g. quotes with external enrichment data).
    - Creating derived columns (age from date of birth, vehicle age from year of manufacture).
    - Filtering or reshaping data in ways the specialised nodes don't cover.

| Config | Description |
|---|---|
| `code` | **Required.** Polars transformation code |
| `selected_columns` | Subset of columns to keep in the output (see [note below](#selected_columns)) |

## Reading Polars code

If you're coming from Excel or a drag-and-drop pricing tool, the code on this page may look unfamiliar. Here's a quick cheat-sheet  - every concept below maps to something you already know.

| Polars syntax | What it means | Excel equivalent |
|---|---|---|
| `df` | A **dataframe**  - a table of data. `df` is the node's **output**: assign your result to it. Each input is available by the name of the upstream node it came from. | A spreadsheet tab |
| `pl.col("column_name")` | Refers to a column by name. | Clicking a column header |
| `.alias("new_name")` | Gives the result a new column name. | Naming a cell or column |
| `.with_columns(...)` | Adds or replaces columns in the table. | Adding a new formula column |
| `.filter(...)` | Keeps only rows that match a condition. | Filtering rows in Excel |
| `pl.when(...).then(...).otherwise(...)` | An IF statement. | `=IF(condition, then, else)` |
| `pl.lit("value")` | A fixed/literal value. | Typing a constant into a formula |
| `return df` | Passes the result to the next node. Always the last line. |  - |

Each input table is available by the name of the node it came from. For example, if you connect a node called `policies`, you reference it as `policies` in your code. `df` is not an input  - it's the variable your code must assign its result to (reading `df` before assigning it is an error). The last line should be `return df`, which passes the resulting table to the next node. A polars node must have code: with none, running the pipeline fails at this node.

```python
df = policies.join(claims, on="policy_id", how="left")
df = df.with_columns(
    (pl.col("claim_amount") / pl.col("premium")).alias("loss_ratio")
)
return df
```

## A custom join with explicit contracts

Use the upstream node names as your source names. In this example the canvas
has inputs named `policies` and `claims`; the node declares that it needs the
two join keys and the columns used to calculate `loss_ratio`:

```python
# Inputs: policies(policy_id, premium), claims(policy_id, claim_amount)
df = policies.join(claims, on="policy_id", how="left")
return df.with_columns(
    (pl.col("claim_amount") / pl.col("premium")).alias("loss_ratio")
)
```

Keep the source names and join keys in sync with the canvas. Haute can project
the declared columns and the join keys, but custom code with a column contract
it cannot prove is handled conservatively as an execution boundary. That is
safe, but it can scan more columns or require an admitted materialisation. See
[Execution Strategy](../execution-strategy.md) for the boundary and diagnostic
details.

!!! tip "Column sidebar"
    The code editor has an **Available Columns** panel below it. Click the **+** next to any column name to insert it at your cursor. If you're new to Polars, start with [Preparing Your Data](../preparing-your-data.md) for a guided walkthrough.

## Common patterns

The examples below assume one upstream node called `policies`; start from the
input's name and assign the result to `df`.

**Calculate a derived column:**

```python
df = policies.with_columns(
    (pl.col("premium") * pl.col("expense_loading")).alias("loaded_premium")
)
return df
```

**Filter rows:**

```python
df = policies.filter(pl.col("cover_type") == "comprehensive")
return df
```

**Conditional logic** (like IF in a spreadsheet):

```python
df = policies.with_columns(
    pl.when(pl.col("driver_age") < 25)
      .then(pl.lit("young"))
      .otherwise(pl.lit("standard"))
      .alias("driver_category")
)
return df
```

## `selected_columns`

If set, only these columns are kept in the output  - like hiding columns in Excel. If not set, all columns pass through unchanged.

This is useful when a node produces many intermediate columns but the next node only needs a few:

```json
{
  "code": "...",
  "selected_columns": ["policy_id", "premium", "loss_ratio"]
}
```

## Reusing code with instances

If you have the same logic applied to different inputs, you don't need to duplicate the node. Create an **instance** that reuses the original's code with different inputs. Change the original and every instance updates.

See [Instances](instances.md) for full details. A quick example:

```json
{
  "instanceOf": "clean_policies",
  "inputMapping": { "policies": "claims_data" }
}
```

This creates a node that runs the same code as `clean_policies`, but reads from `claims_data` instead of `policies`.

**See also:**

- [Preparing Your Data](../preparing-your-data.md)  - guided walkthrough for newcomers
- [Polars (Getting Started)](../../getting-started/polars.md)  - deeper dive into the data engine
- [Execution Strategy](../execution-strategy.md)  - projection and boundary diagnostics
