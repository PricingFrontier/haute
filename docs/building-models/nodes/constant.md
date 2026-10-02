# Constant

You have values that don't change per quote  - expense loadings, tax rates, minimum premiums. The Constant node stores them in one place so every part of your pipeline can reference them.

!!! info "When to use"
    Use this to store values that don't change per quote  - expense loadings, tax rates, minimum premiums, effective dates. These values are available to every downstream node.

A Constant has no inputs. Its output is a single-row table with one column per value. The panel has two tabs: **CONFIG** and **COLUMNS**.

## The CONFIG tab

**VALUES** lists the constants, one row each:

| Field | What it does |
|---|---|
| Name (placeholder "name") | The column the value becomes. A row without a name is left out of the output. |
| Value (placeholder "value") | The value. A value that reads as a number becomes a decimal number (`250` becomes `250.0`); anything else stays text. |

- **Add value** adds a row, named `constant_2`, `constant_3` and so on, with the value `0`.
- The bin at the end of a row (its tooltip reads "Remove") deletes it.

A new node starts with one value, `constant_1` = `1.0`. With no named values at all, the node outputs a single column called `constant`.

## The COLUMNS tab

The **COLUMNS** tab chooses which of the constants the node passes on (see [Working with any node](index.md#working-with-any-node)).

## Connecting to other nodes

The Constant node produces a single-row table. To give every row of your main data access to every constant, cross-join the two:

- **With an [Edge Join](edge-join.md)**: drag the Constant's output onto the connection that carries your data, then set **JOIN TYPE** to **Cross**.
- **In a [Transform](transform.md) node**: connect both, start from your data and add a **Join another input** step that joins the Constant's input with **cross: every combination of rows**. Then add steps that use the constants, such as an **Add column** step that multiplies `base_premium` by `expense_loading`.

## Example

1. Add a Constant node called `params`. Change the first row to `expense_loading` = `1.15`.
2. Click **Add value** twice and fill in `tax_rate` = `0.12` and `min_premium` = `250`.
3. Connect `params` and your data, `quotes`, to a Transform node. **Start from** `quotes`, add **Join another input** with **cross: every combination of rows** and `params`, then add **Add column** with **Column name** `loaded_premium` and the formula `base_premium * expense_loading`.

The Constant's preview is one row:

```
| expense_loading | tax_rate | min_premium |
|-----------------|----------|-------------|
| 1.15            | 0.12     | 250.0       |
```

and the Transform node's **Generated code** panel shows:

```python
df = quotes
df = df.join(params, how="cross", suffix="_right")
df = df.with_columns(
    (pl.col("base_premium") * pl.col("expense_loading")).alias("loaded_premium")
)
```

!!! note "Dates"
    Values are stored as text and turned into numbers where possible. For dates, type them as text (e.g. `2025-01-01`) and convert them in a downstream Transform node if needed, for example with a **Change types** step to **Date (date)**.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/constant/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.constant(config="config/constant/<node name>.json")`.

    | Setting in the editor | Stored as |
    |---|---|
    | **VALUES** | `values`: a list of `{"name": ..., "value": ...}` pairs, with each value stored as text |
    | **COLUMNS** tab | `selected_columns` |

    ```json
    {
      "values": [
        { "name": "expense_loading", "value": "1.15" },
        { "name": "tax_rate", "value": "0.12" },
        { "name": "min_premium", "value": "250" }
      ]
    }
    ```

**See also:**

- [Edge Join](edge-join.md)  - to join the constants onto your data without a Transform node
- [Transform](transform.md)  - for calculations that use the constants
