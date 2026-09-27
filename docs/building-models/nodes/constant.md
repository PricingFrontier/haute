# Constant

You have values that don't change per quote  - expense loadings, tax rates, minimum premiums. The Constant node stores them in one place so every part of your pipeline can reference them.

!!! info "When to use"
    Use this to store values that don't change per quote  - expense loadings, tax rates, minimum premiums, effective dates. These values are available to every downstream node.

| Config | Description |
|---|---|
| `values` | **Required.** List of `{name, value}` pairs |

Each entry becomes a column in the output. Values are coerced to numbers where possible, otherwise kept as strings.

```json
[
  { "name": "expense_loading", "value": "1.15" },
  { "name": "tax_rate",        "value": "0.12" },
  { "name": "min_premium",     "value": "250" }
]
```

## Connecting to other nodes

The Constant node produces a single-row table. To give every row of your main data access to every constant, cross-join the two:

- **With an [Edge Join](edge-join.md)**: drag the Constant's output onto the connection that carries your data, then set the join type to **Cross**.
- **In a [Polars](polars.md) node**: connect both, start from your data and add a **Join another input** step that joins the Constant's input with join type **cross**. Then add steps that use the constants, such as an **Add column** step that multiplies `base_premium` by `expense_loading`.

The same Polars node written as code (after **Switch to code**), with inputs `quotes` (your data) and `params` (your constants):

```python
df = quotes.join(params, how="cross")
df = df.with_columns(
    (pl.col("base_premium") * pl.col("expense_loading")).alias("loaded_premium")
)
```

!!! note "Dates"
    Values are stored as strings and coerced to numbers where possible. For dates, store them as strings (e.g. `"2025-01-01"`) and convert them in a downstream Polars node if needed.
