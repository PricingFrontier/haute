# Reusing a node

You have a node that does exactly what you need  - but you want to apply the same logic to a different input. Instead of duplicating the node (and maintaining two copies), you create an **instance** that reuses the original's configuration with different inputs.

!!! info "When to use"
    - You have the same cleaning or transformation logic applied to multiple datasets (e.g. normalising column names on both policies and claims).
    - You want to score the same model against different data splits (e.g. training vs validation).
    - You want to keep logic in one place so a change to the original automatically updates every instance.

An instance shares a definition with its **owner**. Connect different data to its public inputs to run the same logic on different rows. Editing the definition through the owner updates every instance.

## Creating an instance

In the parent pipeline, select one node and click **Instance** in the toolbar. For Transform, Load File, Data Input, Constant, Banding, Rating Step, Model Score, Edge Join, Scenario Expander, Model Training, Explore, Optimiser and Apply Optimisation, Haute wraps the node in a one-node submodel and places an unconnected instance beside it. The action takes one Undo and writes files only when you click **Save**.

The owner keeps the original node's name, position, connections and output name. Its inner node becomes `<name>_node`, with a numeric suffix if needed. Any config file follows that inner name. The definition and module use the original name, suffixed when another definition or module already uses it. The instance normally starts as `<name>_2`.

Connect the instance to the data you want to process. Each public input keeps the name the original node read. If an input would have the same name as the output, its public name takes a suffix; the code inside still reads its original name. Sources without inputs need no connections, and sinks have no public output.

For an existing submodel, **Instance** adds another occurrence of its definition. You can also right-click a submodel and choose **Create Instance**. Plain nodes use the toolbar.

A Data Output cannot be reused: two writers to the same destination are refused. Add a separate Data Output with its own destination instead.

## The panel

An instance uses the [Submodel](submodel.md) panel. Its **INPUTS** selectors are its input mapping: each public input can receive any incoming frame, whatever that frame is named. Choosing a connection moves it to that input, replacing its previous connection, in one Undo step. The instance shares its code and configuration with the owner; double-click the owner to edit them.

Dropping a connection on the instance binds the public input with the same name as the incoming frame. If that input is already connected, the drop is refused. With no matching name, Haute binds the sole unconnected input or lets you choose among the unconnected inputs. An instance never creates a new public input.

With one declared output, the instance's frame takes the instance's name. With several, each frame is named `<instance>_<port>`. Unconnected outputs count too. Renaming an instance updates the input names its consumers read.

After preview, **MISSING COLUMNS** lists, by public input, the columns the instance lacks compared with the owner's input. Only inputs connected on both occurrences are compared. If a schema cannot be resolved, its error appears instead. The section is absent when there are no warnings.

Click the instance to preview its first output using its own inputs. Choose another output from the preview's frame selector. **Refresh** runs the selected output again, and clicking a cell traces that output and frame. A definition with no public output has no preview.

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

**See also:**

- [Transform](transform.md)  - the most common node type to create instances of
- [Model Scoring](model-score.md)  - reuse scoring configuration with different data
