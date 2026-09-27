# Polars

This is the general-purpose node for shaping your data. Joining two datasets, calculating a new column, filtering rows  - if there isn't a specialised node for it, you do it here, as a list of steps or as Polars code. This is the node you'll use most often.

!!! tip "Spreadsheet equivalent"
    Think of this as the formula bar in a spreadsheet, but for entire columns at once. Instead of writing a formula in one cell and dragging it down, you write one expression and it applies to every row.

!!! info "When to use"
    - Joining two datasets together (e.g. quotes with external enrichment data).
    - Creating derived columns (age from date of birth, vehicle age from year of manufacture).
    - Filtering or reshaping data in ways the specialised nodes don't cover.

| Config | Description |
|---|---|
| `steps` | The node's steps, built in the step builder (see below). New nodes start with steps. |
| `code` | Polars code. While the node has steps, this is the code they generate; after **Switch to code**, it is the code you edit. |
| `selected_columns` | Subset of columns to keep in the output (see [note below](#selected_columns)) |

With one input connected, the step builder starts from that input, so a node with no further steps passes its input through. A node with no starting input and no code fails at this node when the pipeline runs.

## Building the node from steps

A new Polars node opens in the step builder:

1. **Start from**  - choose the input the node starts from. With one input connected, Haute chooses it for you.
2. **Add step**  - add steps in order. The menu groups them as Rows (**Filter rows**, **Sort rows**, **Remove duplicates**, **Limit rows**), Columns (**Add column**, **Keep columns**, **Drop columns**, **Rename columns**, **Change types**, **Fill missing values**), Combine (**Join another input**, **Append inputs**, **Group and aggregate**, **Pivot to columns**, **Unpivot to rows**), Values (**Define variable**) and Code (**Free code**). You can search the menu by a step's name or by its Polars method: typing `join` finds **Join another input**.
3. **Generated code**  - a read-only panel under the steps shows the Polars code they produce.

**Switch to code** replaces the steps with their generated code, which you can then edit freely. It asks you to confirm and cannot be undone: the steps are removed and the node stays as code.

## Mixing steps with free code

In the step builder, choose **Add step → Code → Free code** to write Python
between low-code steps. The editor starts with the current frame in `df` and
Polars available as `pl`. Assign your result back to `df`:

```python
df = df.with_columns(
    (pl.col("premium") * 1.05).alias("loaded_premium")
)
```

You can add more low-code steps afterwards, or move and delete the free-code
step like any other. Do not add `return df`: the next step needs to run.
Variables from earlier **Define variable** steps are available in your code.
If your code creates columns, type their names into subsequent step fields.
Use `df` for the current frame so upstream renames keep working; direct input
names written in free code need to be updated manually when those inputs change.

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

After **Switch to code**, each input table is available by the name of the node it came from. For example, if you connect a node called `policies`, you reference it as `policies` in your code. `df` is not an input  - it's the variable your code must assign its result to (reading `df` before assigning it is an error). Haute passes whatever `df` holds to the next node, so do not end your code with `return df`; the editor shows that line, dimmed, under the code box.

```python
df = policies.join(claims, on="policy_id", how="left")
df = df.with_columns(
    (pl.col("claim_amount") / pl.col("premium")).alias("loss_ratio")
)
```

## A custom join in code

Use the upstream node names as your source names. In the example above the
canvas has inputs named `policies` and `claims`. Haute reads the code to find
the columns it uses: the join key and the columns used to calculate
`loss_ratio`.

The same node built from steps starts from `policies`, adds a **Join another
input** step (`claims`, left join on `policy_id`) and an **Add column** step for
`loss_ratio`.

Keep the source names and join keys in sync with the canvas. Haute can project
the columns the code uses and the join keys, but custom code with a column contract
it cannot prove is handled conservatively as an execution boundary. That is
safe, but it can scan more columns or require an admitted materialisation. See
[Execution Strategy](../execution-strategy.md) for the boundary and diagnostic
details.

## Common patterns

The examples below assume one upstream node called `policies`; start from the
input's name and assign the result to `df`.

**Calculate a derived column:**

```python
df = policies.with_columns(
    (pl.col("premium") * pl.col("expense_loading")).alias("loaded_premium")
)
```

**Filter rows:**

```python
df = policies.filter(pl.col("cover_type") == "comprehensive")
```

**Conditional logic** (like IF in a spreadsheet):

```python
df = policies.with_columns(
    pl.when(pl.col("driver_age") < 25)
      .then(pl.lit("young"))
      .otherwise(pl.lit("standard"))
      .alias("driver_category")
)
```

## `selected_columns`

If set, only these columns are kept in the output  - like hiding columns in Excel. If not set, all columns pass through unchanged.

This is useful when a node produces many intermediate columns but the next node only needs a few. Set it on the node's **Columns** tab: **Output Columns** lists every column the node produces, with a checkbox each. Untick the columns you don't need; the filter box finds a column by name, **All** keeps every column again, and **None** clears the ticks so you can pick a few. The list appears once the node has been previewed or run.

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

- [Polars (Getting Started)](../../getting-started/polars.md)  - deeper dive into the data engine
- [Execution Strategy](../execution-strategy.md)  - projection and boundary diagnostics
