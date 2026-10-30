# Instances

You have a node that does exactly what you need  - but you want to apply the same logic to a different input. Instead of duplicating the node (and maintaining two copies), you create an **instance** that reuses the original's configuration with different inputs.

!!! info "When to use"
    - You have the same cleaning or transformation logic applied to multiple datasets (e.g. normalising column names on both policies and claims).
    - You want to score the same model against different data splits (e.g. training vs validation).
    - You want to keep logic in one place so a change to the original automatically updates every instance.

An instance shares a definition with its **owner**. Connect different data to its public inputs to run the same logic on different rows. Editing the definition through the owner updates every instance.

## Creating an instance

In the parent pipeline, select one node and click **Instance** in the toolbar. For Transform, Data Input, Constant, Banding, Rating Step, Model Score, Edge Join, Scenario Expander, Explore, Optimiser and Apply Optimisation, Haute wraps the node in a one-node submodel and places an unconnected instance beside it. The action takes one Undo and writes files only when you click **Save**.

The owner keeps the original node's name, position, connections and output name. Its inner node becomes `<name>_node`, with a numeric suffix if needed. Any config file follows that inner name. The definition and module use the original name, suffixed when another definition or module already uses it. The instance normally starts as `<name>_2`.

Connect the instance to the data you want to process. Each public input keeps the name the original node read. If an input would have the same name as the output, its public name takes a suffix; the code inside still reads its original name. Sources without inputs need no connections, and sinks have no public output.

For an existing submodel, **Instance** adds another occurrence of its definition. You can also right-click a submodel and choose **Create Instance**. Plain nodes use the toolbar.

Load File, Data Output and Model Training currently keep node-level instances, with the panel described below. Previously created node-level instances continue to work.

## The panel

Submodel instances use the occurrence panel described in [Submodel](submodel.md): **INPUTS** lists every public input and lets you re-target an incoming connection. After preview, **MISSING COLUMNS** reports, by input, the columns the instance lacks compared with the owner's inputs, or a schema error when that comparison is unavailable. Their output frames are named after the instance, with `_<port>` appended when the definition has several outputs. The node-instance panel below applies to ordinary nodes.

An instance's panel has no tabs, because its settings belong to the original:

- **INSTANCE OF** names the original node. The panel reminds you: **This node uses the same logic as the original. To edit the code or config, select the original node. Changes will automatically apply to all instances.**
- **INPUT MAPPING** (**Map each original input to a connected upstream node.**) lists each of the original's inputs with a selector of this instance's connected inputs (**- unmapped -** when none is chosen).
- **MISSING COLUMNS (N)** lists columns the original receives that are not available at this instance's position.

Haute matches the original's inputs to the instance's inputs by exact name first, then by an unambiguous partial name match, then by position. You only need to choose in **INPUT MAPPING** when that matching is ambiguous (several upstream inputs fit one original input): the panel then warns **Name matching is ambiguous for …**, and saving and running are blocked until you choose each input. You can also use the selectors to override the automatic match.

## Example

Suppose a Transform named `clean_policies` reads `policies` and you want to apply its logic to `claims_data`:

1. Select `clean_policies` and click **Instance**.
2. Connect `claims_data` to the new `clean_policies_2` occurrence's `policies` input.
3. Click the instance to preview `claims_data` through the shared code.
4. Double-click the owner and edit `clean_policies_node`; the instance's preview uses the changed logic.
5. Save. Haute writes `modules/clean_policies.py` and the public input binding in the pipeline file.

Renaming the instance changes its output name for consumers. The owner's output stays `clean_policies`. To dissolve the owner, first delete or dissolve its instances; its plain node returns under the inner name `clean_policies_node`.

## Which node types support instances?

Quote Input, Workbench Input, Quote Response and Workbench Output cannot have instances, because they are document-wide singletons; for the same reason, neither can a submodel that contains one of them. A Source Switch cannot have instances either, because it routes by its own input names: add another Source Switch instead. Submodels have instances too; see [Submodel](submodel.md).

??? note "In the pipeline file"
    For a wrapped node, the pipeline imports the definition and registers both occurrences:

    ```python
    from modules import clean_policies

    pipeline.submodel(clean_policies.submodel, "clean_policies")
    pipeline.submodel(clean_policies.submodel, "clean_policies_2", instance_of="clean_policies")
    pipeline.connect("claims_data", "clean_policies_2", target_port="policies")
    ```

    Node-level instances retained for Load File, Data Output and Model Training have no JSON sidecar. Their decorator names the original, and their function takes their own inputs:

    ```python
    @pipeline.instance(of="load_rates", inputMapping={"policies": "claims_data"})
    def load_claim_rates(claims_data): ...
    ```

    | Setting in the editor | Stored as |
    |---|---|
    | **INSTANCE OF** | `of` in the decorator (`instanceOf` in the node's config): the name of the original node |
    | **INPUT MAPPING** | `inputMapping`: a map of the original's input names to this instance's input names. Absent until you choose an input in **INPUT MAPPING**; the automatic match is used meanwhile. |

    A submodel instance is stored differently; see [Submodel](submodel.md).

**See also:**

- [Transform](transform.md)  - the most common node type to create instances of
- [Model Scoring](model-score.md)  - reuse scoring configuration with different data
