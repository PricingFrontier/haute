# Polars

This is the general-purpose node for shaping your data. Joining two datasets, calculating a new column, filtering rows - if there isn't a specialised node for it, you do it here, as a list of steps or as Polars code. This is the node you'll use most often.

!!! info "When to use"
    - Joining two datasets together (e.g. quotes with external enrichment data).
    - Creating derived columns (age from date of birth, vehicle age from year of manufacture).
    - Filtering or reshaping data in ways the specialised nodes don't cover.

A Polars node takes any number of inputs and has one output. Each connection brings in the upstream node's data under the upstream node's name (a [Quote Input](quote-input.md) table arrives under its label). The panel has two tabs: **POLARS**, where you build the node, and **COLUMNS**.

## Building the node from steps

The **POLARS** tab is the step builder. You build the node as a list of steps named in plain English, and Haute turns them into Polars code. The same builder is on the **POLARS** tab of the [Data Input](data-input.md), [Load File](external-file.md), [Rating Step](rating-step.md), [Expander](scenario-expander.md) and [Model Scoring](model-score.md) nodes, and in the **POLARS CODE** pane of an [Explore](explore.md) node; [Steps on other nodes](#steps-on-other-nodes) says what differs there.

From top to bottom, the tab shows the node's inputs, the **STEPS** (a **Start from** card, then one card per step), the **Add step** button and the **Generated code** panel.

### Inputs

**INPUT** (**INPUTS** when there are several) shows one chip per connection, named as the steps and code see it. The × on a chip removes that connection from the canvas. A chip shown in amber with a warning sign comes from a Quote Input table that is no longer emitted; hover it for the reason.

### Start from

The first card, **Start from**, chooses the input the node starts from. With one input connected, Haute chooses it for you. With several, pick one from the list, which reads **Choose an input** until you do; an input that has since been disconnected shows as "*name* (not connected)". Once the start is chosen the node can be previewed: with no further steps it passes that input through unchanged.

With no input connected, the tab shows "Connect an input to start building steps, or switch to code." and a **Switch to code** button instead.

### Add step

**Add step**, under the last card, opens the step chooser in place:

- The search box ("Search, e.g. join or group_by") filters the steps by name, by what they do, or by their Polars method: typing `join` finds **Join another input**, and `with_columns` finds **Add column**. **Enter** adds the first match.
- The arrow keys move between the steps, and the foot of the chooser describes the step you point at, with its Polars method.
- **Esc**, or the × beside the search box, closes the chooser without adding a step.

The steps are grouped as follows:

| Group | Steps |
|---|---|
| Rows | **Filter rows**, **Sort rows**, **Remove duplicates**, **Limit rows** |
| Columns | **Add column**, **Keep columns**, **Drop columns**, **Rename columns**, **Change types**, **Fill missing values** |
| Combine | **Join another input**, **Append inputs**, **Group and aggregate**, **Pivot to columns**, **Unpivot to rows** |
| Values | **Define variable** |
| Code | **Free code** |

Choosing a step adds its card at the end of the list, open, with the cursor in its first field.

### Step cards

Each step is a numbered card. Its header shows the number, the step's icon and its name. A closed card also shows a one-line summary of the step and the change it makes to the columns: `+loss_ratio` for a column it adds, `−` before columns it removes, or `→ 3 columns` after a grouping or pivot. Click the header to open the card and edit the step, and click it again (or press **Esc**) to close it.

The buttons on the right of each card:

- The up and down arrows move the step one place. **Alt+Up** and **Alt+Down** on the header do the same, and you can also drag a card by its header onto another card. Steps run in list order, top to bottom; the **Start from** card always stays first.
- The bin deletes the step.

Notes under a closed card tell you what needs attention:

- "Not in the data at this step:" followed by column names, when the step names a column the data does not have at that point, for example one an earlier step dropped or renamed.
- What an unfinished step still needs, such as "Needs a formula." or "Needs a name for the new column." These are neutral reminders while you build. They turn into errors only after the pipeline has run and failed on this node, and then the failing step's card shows the error.

A card that Haute cannot read (for example after the pipeline file was edited by hand) shows as **Invalid step**, with "This step cannot be edited here. Delete it to repair the step list."

While you work in the step builder, **Delete** and **Backspace** outside a text box, and the canvas's copy, paste, select-all and group shortcuts, stay in the step builder instead of acting on the canvas.

### What each step does

Each card reads as a sentence or a short form. The column boxes suggest the columns the data has at that step.

| Step | What it does | What you fill in |
|---|---|---|
| **Filter rows** | Keeps rows that match conditions (`filter`) | "Keep rows where", then one condition per row: a column, a test (**equals**, **is greater than**, **is one of**, **is missing**, **contains text**, **matches pattern (regex)** and more) and a value. **Add condition** adds a row; with several, choose whether **all** or **any** of them must be true. |
| **Add column** | Computes a new or replaced column (`with_columns`) | **Column name**, then **Computed as**: **Value**, **Formula**, **Function**, **If-then**, **Window** or **Join text**. A formula is typed as text, e.g. `(premium + commission) * tax / 12`, and the box completes column names, earlier variables and function names. |
| **Keep columns** | Keeps only the listed columns (`select`) | The columns to keep; **More options** adds **And every other column of type**. |
| **Drop columns** | Removes the listed columns (`drop`) | The columns to drop; **More options** adds **And every other column of type**. |
| **Rename columns** | Gives columns new names (`rename`) | "Rename *column* to *new name*"; **Add rename** adds another. |
| **Change types** | Casts columns to another data type (`cast`) | "Change *column* to *type*", for example **Int64 (whole number)**, **Float64 (decimal number)**, **String (text)** or **Date (date)**. |
| **Sort rows** | Orders rows by one or more columns (`sort`) | "Sort by *column*", "then by" further columns (**Add sort column**), each ascending or descending, and **Missing values last**. |
| **Remove duplicates** | Keeps one row per key (`unique`) | **Rows are duplicates when they match on** (empty means every column), and which row to keep: **the first row**, **the last row**, **any one row** or **no row (drop every duplicate)**. |
| **Group and aggregate** | Summarises rows per group (`group_by().agg()`) | **Group by** (empty summarises the whole table) and **Aggregations**: a name, a function (sum, mean, count of values, row count and more) and a column each. **More options** adds a row filter per aggregation (**Only rows where…**). |
| **Join another input** | Combines columns from another input (`join`) | The join kind (for example **left: every row here, with matches added**) and the input to join, then **Match on** pairs ("a column here = a column there"). **More options** holds **Check key cardinality**, **Output row order** and **Suffix for clashing column names**. |
| **Append inputs** | Stacks rows from other inputs (`pl.concat`) | **Append rows from**: tick the inputs. **Columns** either **must match exactly** or **may differ (fill missing with null)**. |
| **Fill missing values** | Replaces nulls with a value or strategy (`fill_null`) | **Fill missing values in** (empty means every column), then **With** a value or a strategy such as **the previous row's value (forward)** or **the column's mean (mean)**. |
| **Limit rows** | Keeps the first N rows (`head`) | "Keep the first *100* rows". |
| **Define variable** | Names a value for later steps | "*name* = *value*"; later steps and Free code can use the name. |
| **Pivot to columns** | Makes one column per value of a category (`pivot`) | **One row per** (index columns), "Spread the values of", "Each cell is the *function* of *column*", and the **New columns**, one per value. |
| **Unpivot to rows** | Stacks several columns into name/value rows (`unpivot`) | **Stack these columns**, **Keep as index**, and the two new column names ("Into … and …"). |
| **Free code** | Runs Python statements against the current table | A code box: see [Free code](#free-code). |

A **Join another input** card on a node with a single input says "Connect the table to join on the canvas.": connect the other table to the node first.

### Free code

When no step does what you need, choose **Add step → Code → Free code** to write Python between the other steps. The code starts with the current table in `df` and Polars available as `pl`; assign your result back to `df`:

```python
df = df.with_columns(
    (pl.col("premium") * 1.05).alias("loaded_premium")
)
```

- You can add more steps afterwards, and move or delete the free-code step like any other.
- Do not use `return`: the next step needs to run. The card says "Assign the result to df instead of using return." if you do.
- Variables from earlier **Define variable** steps are available in your code.
- If your code creates columns, type their names into the fields of later steps: the builder cannot see inside your code.
- Use `df` for the current table so upstream renames keep working. Input names written in free code need updating by hand when those inputs change.
- Code that is not valid Python is reported on the card, for example "Invalid Python on line 2: …".

The code box suggests column names as you type, and you can drag its lower edge to make it taller.

### Generated code

Under **Add step**, the **Generated code** panel shows the Polars code your steps produce, line-numbered and read-only. Click its title to fold it away or open it again.

- Point at a card and its lines are tinted in the code; point at a line to tint its step, and click it to open that step's card.
- While a step is unfinished, the panel keeps the last code that worked, dimmed and marked **out of date**, with a note such as "Step 2 isn't finished: it needs a formula."
- After a run that failed on this node, the panel names the failing step in red with **Go to error**, which opens that step's card, and highlights the line that failed.

### Switch to code

**Switch to code**, at the foot of the **Generated code** panel, replaces the steps with their generated code, which you can then edit freely (see [Writing the node as code](#writing-the-node-as-code)). It asks you to confirm - "Switch this node to code? The steps are removed and the generated code becomes editable. This cannot be undone." - and it is one-way: the node stays as code and there is no way back to steps.

The button is disabled while the steps are being turned into code, and while a step cannot be; its tooltip then says which, for example "Fix Step 2 first".

### Steps on other nodes

On the **POLARS** tab of a Data Input, Load File, Rating Step, Expander or Model Scoring node (and in Explore's **POLARS CODE** pane), the steps start from a table the node already holds in `df` - the loaded data, the rated data, and so on:

- There is no **Start from** card, and the cards are numbered from 1.
- **Join another input** and **Append inputs** are offered only on a Load File node, whose other inputs the steps can name. On the other nodes the steps see only `df`.
- The loaded object of a Load File node, `obj`, is available only in a **Free code** step.

Each node's page says what `df` holds there.

## Writing the node as code

After **Switch to code**, the **POLARS** tab holds a code box, **POLARS CODE**, instead of the steps. The hint beside the label reads "use input names, assign to df" ("assign to df" when nothing is connected):

- Each input is available by the name of the node it came from. For example, if you connect a node called `policies`, you reference it as `policies` in your code.
- `df` is not an input - it's the variable your code must assign its result to (reading `df` before assigning it is an error). Haute passes whatever `df` holds to the next node, so do not end your code with `return df`: Haute adds that line itself, and shows it dimmed under the code box.
- The code box suggests column names inside quotes, and after a failed run it highlights the line that failed.

```python
df = policies.join(claims, on="policy_id", how="left")
df = df.with_columns(
    (pl.col("claim_amount") / pl.col("premium")).alias("loss_ratio")
)
```

### Reading Polars code

If you're coming from a drag-and-drop pricing tool, the code on this page may look unfamiliar. Here's a quick cheat-sheet.

| Polars syntax | What it means |
|---|---|
| `df` | A **dataframe**  - a table of data. `df` is the node's **output**: assign your result to it. Each input is available by the name of the upstream node it came from. |
| `pl.col("column_name")` | Refers to a column by name. |
| `.alias("new_name")` | Gives the result a new column name. |
| `.with_columns(...)` | Adds or replaces columns in the table. |
| `.filter(...)` | Keeps only rows that match a condition. |
| `pl.when(...).then(...).otherwise(...)` | An IF statement. |
| `pl.lit("value")` | A fixed/literal value. |

### A custom join in code

In the code above, the canvas has inputs named `policies` and `claims`. Haute reads the code to find the columns it uses: the join key and the columns used to calculate `loss_ratio`.

Keep the source names and join keys in sync with the canvas. Haute can project the columns the code uses and the join keys, but custom code with a column contract it cannot prove is handled conservatively as an execution boundary. That is safe, but it can scan more columns or require an admitted materialisation. See [Execution Strategy](../execution-strategy.md) for the boundary and diagnostic details.

### Common patterns

The examples below assume one upstream node called `policies`; start from the input's name and assign the result to `df`.

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

**Conditional logic:**

```python
df = policies.with_columns(
    pl.when(pl.col("driver_age") < 25)
      .then(pl.lit("young"))
      .otherwise(pl.lit("standard"))
      .alias("driver_category")
)
```

## The COLUMNS tab

The **COLUMNS** tab chooses which of the node's output columns it passes on, as on any node (see [Working with any node](index.md#working-with-any-node)).

## Reusing the node with instances

If you have the same logic applied to different inputs, you don't need to duplicate the node. An **instance** reuses the original node's steps or code with different inputs, and a change to the original updates every instance. See [Instances](instances.md).

## Example

A node that adds each policy's loss ratio, from two inputs called `policies` and `claims`:

1. Connect `policies` and `claims` to a new Polars node. Its **POLARS** tab lists both under **INPUTS**.
2. In **Start from**, choose `policies`.
3. Click **Add step**, type `join` and press **Enter** to add **Join another input**. Choose **left: every row here, with matches added**, join `claims`, and under **Match on** pick `policy_id` = `policy_id`.
4. Click **Add step** and choose **Add column**. Type `loss_ratio` as the **Column name**, leave **Computed as** on **Formula**, and type `claim_amount / premium`.

The closed cards read as a summary of the node, and the **Generated code** panel shows:

```python
df = policies
df = df.join(
    claims,
    left_on=["policy_id"],
    right_on=["policy_id"],
    how="left",
    suffix="_right",
)
df = df.with_columns(
    (pl.col("claim_amount") / pl.col("premium")).alias("loss_ratio")
)
```

!!! warning "A node that is not finished stops the run"
    You can save a node whose steps are not finished; saving warns which step is incomplete. Running the pipeline then stops at that node with "This node's steps are incomplete. Complete or remove them before running." A node with no starting input and no code stops with "This transform has no code yet. Add code that defines what it returns."

!!! note "Hand edits to the pipeline file"
    The steps and the code in the `.py` file must agree. If the node's function body is edited by hand so that it no longer matches its steps, Haute keeps the code and drops the steps when it loads the pipeline, and the **POLARS** tab says "Steps were discarded because the function body no longer matches the rendered steps."

??? note "In the pipeline file"
    A Polars node is a function in the pipeline's `.py` file, decorated with `@pipeline.polars`. Its parameters are the node's inputs, and its body is the node's code followed by the `return df` Haute adds. While the node is built from steps, the steps are stored in a JSON sidecar, `config/polars/<node name>.json`, which the decorator names with `config=`, and the body is the code they generate. After **Switch to code** the node has no sidecar and the body is your code.

    | Setting in the editor | Stored as |
    |---|---|
    | The steps on the **POLARS** tab | `steps` in the sidecar: a list with one object per card, each with an `id` and a `kind` (`"source"` for **Start from**, then `"filter"`, `"with_column"`, `"join"`, `"free_code"` and so on) and the card's fields |
    | The code, after **Switch to code** | the function body (`code` in the node's configuration) |
    | **COLUMNS** tab | `selected_columns`, a decorator argument: the columns passed on (absent passes every column) |

    An instance of the node is written as `@pipeline.instance(of="<original>")`, with an `inputMapping` argument when its inputs need matching by hand; see [Instances](instances.md).

**See also:**

- [Polars (Getting Started)](../../getting-started/polars.md)  - deeper dive into the data engine
- [Execution Strategy](../execution-strategy.md)  - projection and boundary diagnostics
