# Submodel

As your pipeline grows, the canvas gets crowded. Submodels let you collapse a group of nodes into a single block  - like grouping sheets in a workbook. Double-click to step inside; click the breadcrumb to come back out.

!!! tip "Spreadsheet equivalent"
    Like grouping several tabs into a named section  - you see one clean label in the main view, and can expand it when you need the detail.

!!! info "When to use"
    Use this when your canvas is getting crowded and you want to group related nodes into a single collapsible block. Also useful for reusing the same logic across multiple pipelines.

This node accepts multiple inputs and produces multiple outputs, defined by its ports.

| Config | Description |
|---|---|
| `definitionId` | **Required.** The stable identity of the shared definition, stored in its `modules/<name>.py` file. Haute sets it when you create the submodel. |
| `alias` | **Required.** This occurrence's name on the canvas; it is also the node's name downstream. |
| `instanceOf` | Set automatically on an instance: the name of the occurrence that owns the definition. The owner has none. |

The definition's input and output ports are part of the definition file, not the node's config.

## Creating a submodel

In the visual editor, select two or more nodes, then click **Submodel** in the toolbar (or press Ctrl+G) and name the submodel. Haute creates the definition file automatically.

## Ports

Input ports define what data flows into the submodel from the parent pipeline. Output ports define what flows back out. When you create a submodel from selected nodes, ports are created automatically based on the existing connections.

You can add ports afterwards on the occurrence that owns the definition. A collapsed submodel has a single `inputs` socket: dropping a new connection on it adds an input port. Inside the submodel, wiring a node to the Output boundary adds an output port. To remove an input, select the Input boundary inside the submodel and remove it from its list of inputs; this removes it, with its connections, from every occurrence.

An instance opens as a read-only view of the shared definition, and it can only use the ports the definition already has. Edit the definition through the occurrence that owns it.

## Dissolving a submodel

To ungroup a submodel and expand its nodes back into the parent pipeline, right-click the submodel node and choose **Dissolve Submodel**. The occurrence that owns the definition cannot be dissolved while instances still reference it: delete or dissolve the instances first.

## Reuse across pipelines

Each submodel's definition lives in its own `modules/<name>.py` file. To use the same logic again, create another instance of the submodel on the canvas (right-click it and choose **Create Instance**, or select it and click **Instance** in the toolbar): every instance shares the one definition, so editing the submodel updates them all. See [Instances](instances.md).

!!! warning "Shared definitions"
    Because reused submodels share a single definition file, changes made in one pipeline will affect every pipeline that references it.

**See also:**

- [Pipeline structure](../../building-models/index.md)  - how nodes connect in a pipeline
