# Instances

You have a node that does exactly what you need  - but you want to apply the same logic to a different input. Instead of duplicating the node (and maintaining two copies), you create an **instance** that reuses the original's configuration with different inputs.

!!! info "When to use"
    - You have the same cleaning or transformation logic applied to multiple datasets (e.g. normalising column names on both policies and claims).
    - You want to score the same model against different data splits (e.g. training vs validation).
    - You want to keep logic in one place so a change to the original automatically updates every instance.

An instance points at an existing node (the **original**) and inherits its code or configuration. The only thing that changes is which inputs are connected: the instance outputs what the original's logic produces from its own inputs. When you update the original node, every instance updates automatically.

## Creating an instance

Select exactly one node on the canvas and click **Instance** in the toolbar. Haute creates a new node beside it, linked to the original. Connect the new node's inputs to the data you want to process. (For a submodel, you can also right-click it and choose **Create Instance**.)

## The panel

An instance's panel has no tabs, because its settings belong to the original:

- **INSTANCE OF** names the original node. The panel reminds you: **This node uses the same logic as the original. To edit the code or config, select the original node. Changes will automatically apply to all instances.**
- **INPUT MAPPING** (**Map each original input to a connected upstream node.**) lists each of the original's inputs with a selector of this instance's connected inputs (**- unmapped -** when none is chosen).
- **MISSING COLUMNS (N)** lists columns the original receives that are not available at this instance's position.

Haute matches the original's inputs to the instance's inputs by exact name first, then by an unambiguous partial name match, then by position. You only need to choose in **INPUT MAPPING** when that matching is ambiguous (several upstream inputs fit one original input): the panel then warns **Name matching is ambiguous for …**, and saving and running are blocked until you choose each input. You can also use the selectors to override the automatic match.

## Example

Suppose you have a Transform node called `clean_policies` that normalises column names. It starts from its input `policies` and has one **Rename columns** step (`Date_Of_Birth` to `date_of_birth`, `Post_Code` to `postcode`).

You want to apply the same cleaning to a different dataset called `claims_data`. Instead of duplicating the node:

1. Select `clean_policies` and click **Instance** in the toolbar.
2. Connect `claims_data` to the new node.
3. Its panel shows **INSTANCE OF** `clean_policies`, and under **INPUT MAPPING**, `policies` → `claims_data`.

Wherever the original reads from `policies`, the instance reads from `claims_data` instead; the code stays the same. With a single input, Haute makes this match by position on its own, so choosing it in **INPUT MAPPING** only makes it explicit.

## Which node types support instances?

Every node type can have instances except Quote Input and Quote Response, because a pipeline allows only one of each; for the same reason, neither can a submodel that contains one of them. A Source Switch cannot have instances either, because it routes by its own input names: add another Source Switch instead. Submodels have instances too; see [Submodel](submodel.md).

??? note "In the pipeline file"
    An instance has no JSON sidecar. The pipeline's `.py` file declares it with an instance decorator naming the original, and the instance's function takes the instance's own inputs:

    ```python
    @pipeline.instance(of="clean_policies", inputMapping={"policies": "claims_data"})
    def clean_claims(claims_data): ...
    ```

    | Setting in the editor | Stored as |
    |---|---|
    | **INSTANCE OF** | `of` in the decorator (`instanceOf` in the node's config): the name of the original node |
    | **INPUT MAPPING** | `inputMapping`: a map of the original's input names to this instance's input names. Absent until you choose an input in **INPUT MAPPING**; the automatic match is used meanwhile. |

    A submodel instance is stored differently; see [Submodel](submodel.md).

**See also:**

- [Transform](transform.md)  - the most common node type to create instances of
- [Model Scoring](model-score.md)  - reuse scoring configuration with different data
