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
| `inputMapping` | Maps the original node's input names to this instance's inputs. Optional: see below |

When you update the original node's code, every instance updates automatically.

Haute matches the original's inputs to the instance's inputs by exact name first, then by an unambiguous partial name match, then by position. You only need an `inputMapping` when that matching is ambiguous (several upstream inputs fit one original input); the instance's panel then warns you, and saving and running are blocked until you choose each input in its **Input Mapping** selectors. You can also use those selectors to override the automatic match.

## Example

Suppose you have a Polars node called `clean_policies` that normalises column names. It starts from its input `policies` and has one **Rename columns** step (`Date_Of_Birth` to `date_of_birth`, `Post_Code` to `postcode`). Its generated code reads:

```python
df = policies
df = df.rename({'Date_Of_Birth': 'date_of_birth', 'Post_Code': 'postcode'})
```

You want to apply the same cleaning to a different dataset called `claims_data`. Instead of duplicating the node, create an instance:

```json
{
  "instanceOf": "clean_policies",
  "inputMapping": { "policies": "claims_data" }
}
```

The `inputMapping` says: wherever the original node reads from `policies`, this instance reads from `claims_data` instead. The code stays the same. With a single input, Haute makes this match by position on its own, so here the mapping only makes it explicit.

## Which node types support instances?

Every node type can have instances except Quote Input, Quote Response and Source Switch, because a pipeline allows only one of each; for the same reason, neither can a submodel that contains one of them. The configuration is always the same  - `instanceOf` and `inputMapping`. Submodels have instances too; see [Submodel](submodel.md).

## Creating an instance in the UI

Select exactly one node on the canvas and click **Instance** in the toolbar. Haute creates a new node beside it, pre-configured with `instanceOf` pointing at the original. Connect the new node's inputs to the data you want to process. (For a submodel, you can also right-click it and choose **Create Instance**.)

**See also:**

- [Polars](polars.md)  - the most common node type to create instances of
- [Model Scoring](model-score.md)  - reuse scoring configuration with different data
