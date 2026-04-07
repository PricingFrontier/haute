# Constant

You have values that don't change per quote  - expense loadings, tax rates, minimum premiums. The Constant node stores them in one place so every part of your pipeline can reference them.

!!! tip "Spreadsheet equivalent"
    Like a named range or a parameters sheet in Excel  - one place to store values you reference throughout your workbook.

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

The Constant node produces a single-row table. When you connect it to a Polars node alongside your main data, you cross-join it so every row gets access to every constant. Here's what that looks like:

```python
# In a Polars node with two inputs: 'quotes' (your data) and 'params' (your constants)
df = quotes.join(params, how="cross")
df = df.with_columns(
    (pl.col("base_premium") * pl.col("expense_loading")).alias("loaded_premium")
)
return df
```

!!! note "Dates"
    Values are stored as strings and coerced to numbers where possible. For dates, store them as strings (e.g. `"2025-01-01"`) and convert them in a downstream Polars node if needed.
