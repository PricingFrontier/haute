# Submodel

As your pipeline grows, the canvas gets crowded. Submodels let you collapse a group of nodes into a single block. Double-click to step inside; click the breadcrumb to come back out.

!!! info "When to use"
    Use this when your canvas is getting crowded and you want to group related nodes into a single collapsible block. Also useful for reusing the same logic across multiple pipelines.

This node takes multiple inputs and produces multiple outputs, defined by its ports.

## Creating a submodel

Select two or more nodes on the canvas, then click **Submodel** in the toolbar (or press Ctrl+G). In the **Create Submodel** dialog, type a **Submodel name** and click **Create**. Haute creates the ports from the existing connections. The graph changes immediately; **Save** writes the definition file.

## Reusing one node

Select one Transform, Load File, Data Input, Constant, Banding, Rating Step, Model Score, Edge Join, Scenario Expander, Model Training, Explore, Optimiser or Apply Optimisation node in the parent pipeline and click **Instance**. Haute wraps it in a one-node definition and places an unbound instance beside it, in one Undo step. Nothing is written until **Save**.

The owner keeps the node's name, position, connections and output name. The inner node is renamed `<name>_node`, suffixed if needed, and its config file follows that name. Public inputs keep the original incoming frame names; a name that conflicts with the output takes a suffix while the code continues reading its old name. The definition and module take the original name, suffixed when occupied, and the new instance normally becomes `<name>_2`.

Connect a different frame to the instance's public input to preview it through the shared definition. Edit through the owner to change every occurrence. After deleting or dissolving the instances, dissolving the owner returns a plain node under its inner name. See [Instances](instances.md) for the supported types and a complete example.

## The panel

A submodel's panel has no tabs. It shows:

- **Submodel** and the number of nodes inside it.
- **FILE**: the definition's file, `modules/<name>.py`.
- **INPUTS**: every public input, with a selector of this occurrence's incoming connections or **not connected**. Choosing a connection moves it to that input. If another connection already feeds the input, that connection is removed. The edit takes one Undo.
- **OUTPUTS**: the definition's public output ports.
- **MISSING COLUMNS**, on an instance after preview: the columns its inputs lack compared with the owner's, listed by public input. Only inputs connected on both occurrences are compared. If an input's schema cannot be resolved, its error appears instead. The section is absent when there are no warnings.

Double-click the node on the canvas to step inside. The input selectors remain editable on an instance; its internal definition is read-only.

## Previewing outputs

Click an owner or instance on the canvas to preview its first declared output. The preview uses that occurrence's inputs and shows the output's name. When it has several outputs, choose another from the preview's frame selector. Outputs from different frames of the same internal node show their respective rows.

**Refresh** re-runs the output currently shown. Each output keeps its own cached preview and shows its own errors. Click a cell to trace that output and frame. A submodel with no public outputs has no preview. The Input and Output boundary nodes inside a submodel do not offer Refresh.

`haute run` reports the rows and columns of each expanded internal node. Missing-column warnings appear in the instance's panel; the CLI does not print them or add a separate result row for the occurrence.

## Ports

Input ports define what data flows into the submodel from the parent pipeline. An owner's outputs keep their public port names. An instance with one declared output names its frame after the instance, such as `rating_ie`. With several declared outputs, each frame is named `<instance>_<port>`, such as `rating_ie_premium`. Unconnected outputs count too. The card shows these executable names.

Renaming an instance, adding a second output or removing all but one updates its consumers' input names, including Quote Response mappings, in the same undoable edit. Code keeps its logical input names. A rename that would give a consumer duplicate names is refused.

You can add ports afterwards on the occurrence that owns the definition. A collapsed submodel has a single `inputs` socket: dropping a new connection on it adds an input port. Inside the submodel, wiring a node to the Output boundary adds an output port. To remove an input, select the Input boundary inside the submodel: its panel lists the **INPUTS**, and each input's **×** removes that public input, with its internal routes and every occurrence's connection.

An instance opens as a read-only view of the shared definition, and it can only use the ports the definition already has. Edit the definition through the occurrence that owns it.

Drop a connection on an instance's `inputs` socket to bind the public input with the same name as the incoming frame. If that input is already connected, the drop is refused. Otherwise, when no name matches, Haute binds the sole unbound input or shows a picker of the unbound inputs at the drop. When every input is connected, the drop is refused. An instance never creates a new public input.

## Dissolving a submodel

To ungroup a submodel and expand its nodes back into the parent pipeline, select the submodel node on its own and click **Dissolve** in the toolbar (the **Submodel** button reads **Dissolve** while a single submodel is selected), or right-click the submodel node and choose **Dissolve Submodel**. The occurrence that owns the definition cannot be dissolved while instances still reference it: delete or dissolve the instances first.

Dissolving an instance names its children `<instance>__<child>` and preserves the input names their code reads. Dissolving the owner keeps its children's names. A name collision must be resolved before saving.

## Reuse across pipelines

Each submodel's definition lives in its own `modules/<name>.py` file. To use the same logic again, create another instance of the submodel on the canvas (right-click it and choose **Create Instance**, or select it and click **Instance** in the toolbar): every instance shares the one definition, so editing the submodel updates them all. See [Instances](instances.md).

!!! warning "Shared definitions"
    Because reused submodels share a single definition file, changes made in one pipeline will affect every pipeline that references it.

## Example

Grouping the scoring steps:

1. Select the Model Scoring nodes and the Transform node that combines their predictions.
2. Click **Submodel** in the toolbar, type `model_scoring` as the **Submodel name**, and click **Create**.
3. The nodes collapse into one `model_scoring` block, wired to the rest of the pipeline through its ports. Its panel shows **FILE** `modules/model_scoring.py` and its **INPUTS** and **OUTPUTS**.

??? note "In the pipeline file"
    A submodel has no JSON sidecar. Its definition is a Python module, `modules/<name>.py` beside the pipeline: the submodel's nodes, and a `haute.Submodel(...)` header with its `definition_id` and its `input_ports` and `output_ports`. The pipeline's `.py` file imports the module and registers each occurrence of its definition:

    ```python
    from modules import model_scoring

    pipeline.submodel(model_scoring.submodel, "model_scoring")
    ```

    The first argument is the imported definition, the second the occurrence's name on the canvas. An instance adds `instance_of="<name of the owning occurrence>"`. Because it's an ordinary import, your editor's go-to-definition and type checker follow it, and `python main.py` runs the submodel. Haute writes the import and the registrations when it saves; a submodel registered by its file path, `pipeline.submodel("modules/model_scoring.py", ...)`, is no longer read.

    | Setting in the editor | Stored as |
    |---|---|
    | **Submodel name** | the definition module's name (`modules/<name>.py`, imported as `from modules import <name>`), and the occurrence's first name |
    | Renaming the node | `alias`: the occurrence's name, used in connections and in its instance output names; the second argument of `pipeline.submodel(...)` |
    | **INPUTS**, **OUTPUTS** | the `input_ports` and `output_ports` of the definition file's `haute.Submodel(...)` header |
    | **Create Instance** | `instanceOf`: the owning occurrence's name, written as `instance_of="…"` in the instance's `pipeline.submodel(...)` |

    `definitionId`, the definition's stable identity, has no editor control: Haute sets it when
    you create the submodel and writes it as `definition_id` in the definition file.

**See also:**

- [Pipeline structure](../../building-models/index.md)  - how nodes connect in a pipeline
- [Instances](instances.md)  - reuse a node's logic with different inputs
