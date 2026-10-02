# Submodel

As your pipeline grows, the canvas gets crowded. Submodels let you collapse a group of nodes into a single block. Double-click to step inside; click the breadcrumb to come back out.

!!! info "When to use"
    Use this when your canvas is getting crowded and you want to group related nodes into a single collapsible block. Also useful for reusing the same logic across multiple pipelines.

This node takes multiple inputs and produces multiple outputs, defined by its ports.

## Creating a submodel

Select two or more nodes on the canvas, then click **Submodel** in the toolbar (or press Ctrl+G). In the **Create Submodel** dialog, type a **Submodel name** and click **Create**. Haute creates the definition file automatically, and creates ports from the existing connections.

## The panel

A submodel's panel has no tabs. It shows:

- **Submodel** and the number of nodes inside it.
- **FILE**: the definition's file, `modules/<name>.py`.
- **INPUTS** and **OUTPUTS**: the submodel's ports.
- **Double-click to view internal nodes**: double-click the node on the canvas to step inside.

## Ports

Input ports define what data flows into the submodel from the parent pipeline. Output ports define what flows back out: a node connected to an output reads its data under the output's port name, whatever you name the submodel node. When you create a submodel from selected nodes, ports are created automatically based on the existing connections.

You can add ports afterwards on the occurrence that owns the definition. A collapsed submodel has a single `inputs` socket: dropping a new connection on it adds an input port. Inside the submodel, wiring a node to the Output boundary adds an output port. To remove an input, select the Input boundary inside the submodel: its panel lists the **INPUTS**, and each input's **×** removes that public input, with its internal routes and every occurrence's connection.

An instance opens as a read-only view of the shared definition, and it can only use the ports the definition already has. Edit the definition through the occurrence that owns it.

## Dissolving a submodel

To ungroup a submodel and expand its nodes back into the parent pipeline, select the submodel node on its own and click **Dissolve** in the toolbar (the **Submodel** button reads **Dissolve** while a single submodel is selected), or right-click the submodel node and choose **Dissolve Submodel**. The occurrence that owns the definition cannot be dissolved while instances still reference it: delete or dissolve the instances first.

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
    A submodel has no JSON sidecar. The pipeline's `.py` file registers each occurrence with a call such as `pipeline.submodel("modules/model_scoring.py", "model_scoring")`: the definition's file, then the occurrence's name on the canvas. An instance adds `instance_of="<name of the owning occurrence>"`. The definition file, `modules/<name>.py`, holds the submodel's nodes and a `haute.Submodel(...)` header with its `definition_id` and its `input_ports` and `output_ports`.

    | Setting in the editor | Stored as |
    |---|---|
    | **Submodel name** | the definition file's name, and the occurrence's first name |
    | Renaming the node | `alias`: the occurrence's name, which the pipeline file's connections use (a node downstream reads each output under its port name); the second argument of `pipeline.submodel(...)` |
    | **INPUTS**, **OUTPUTS** | the `input_ports` and `output_ports` of the definition file's `haute.Submodel(...)` header |
    | **Create Instance** | `instanceOf`: the owning occurrence's name, written as `instance_of="…"` in the instance's `pipeline.submodel(...)` |

    `definitionId`, the definition's stable identity, has no editor control: Haute sets it when
    you create the submodel and writes it as `definition_id` in the definition file.

**See also:**

- [Pipeline structure](../../building-models/index.md)  - how nodes connect in a pipeline
- [Instances](instances.md)  - reuse a node's logic with different inputs
