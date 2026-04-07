# Instances

You have a node that does exactly what you need  - but you want to apply the same logic to a different input. Instead of duplicating the node (and maintaining two copies), you create an **instance** that reuses the original's configuration with different inputs.

!!! tip "Spreadsheet equivalent"
    Like copying a formula that references one tab and pasting it so it references a different tab  - the logic is identical, only the data source changes.

!!! info "When to use"
    - You have the same cleaning or transformation logic applied to multiple datasets (e.g. normalising column names on both policies and claims).
    - You want to score the same model against different data splits (e.g. training vs validation).
    - You want to keep logic in one place so a change to the original automatically updates every instance.

## How it works

An instance points at an existing node (the **original**) and inherits its code or configuration. The only thing that changes is which inputs are connected.

| Config | Description |
|---|---|
| `instanceOf` | **Required.** Name of the node to reuse logic from |
| `inputMapping` | **Required.** Maps the original node's input names to this instance's inputs |

When you update the original node's code, every instance updates automatically.

## Example

Suppose you have a Polars node called `clean_policies` that normalises column names:

```python
df = df.with_columns(
    pl.col("Date_Of_Birth").alias("date_of_birth"),
    pl.col("Post_Code").alias("postcode"),
)
return df
```

You want to apply the same cleaning to a different dataset called `claims_data`. Instead of duplicating the node, create an instance:

```json
{
  "instanceOf": "clean_policies",
  "inputMapping": { "policies": "claims_data" }
}
```

The `inputMapping` says: wherever the original node reads from `policies`, this instance reads from `claims_data` instead. The code stays the same.

## Which node types support instances?

Instances are available on [Polars](polars.md) and [Model Score](model-score.md) nodes. In both cases, the configuration is the same  - `instanceOf` and `inputMapping`.

## Creating an instance in the UI

On the canvas, right-click a node and choose **Create Instance**. Haute creates a new node pre-configured with `instanceOf` pointing at the original. Connect the new node's inputs to the data you want to process.

**See also:**

- [Polars](polars.md)  - the most common node type to create instances of
- [Model Score](model-score.md)  - reuse scoring configuration with different data
