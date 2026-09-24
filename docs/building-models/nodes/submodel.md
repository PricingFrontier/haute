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

The definition's input and output ports are part of the definition file, not the node's config.

## Creating a submodel

In the visual editor, select the nodes you want to group, right-click, and choose **Create Submodel**. Haute creates the definition file automatically.

## Ports

Input ports define what data flows into the submodel from the parent pipeline. Output ports define what flows back out. When you create a submodel from selected nodes, ports are created automatically based on the existing connections.

## Dissolving a submodel

To ungroup a submodel and expand its nodes back into the parent pipeline, right-click the submodel node and choose **Dissolve**.

## Reuse across pipelines

Each submodel's definition lives in its own `modules/<name>.py` file. To use the same logic again, create another instance of the submodel on the canvas: every instance shares the one definition, so editing the submodel updates them all. See [Instances](instances.md).

!!! warning "Shared definitions"
    Because reused submodels share a single definition file, changes made in one pipeline will affect every pipeline that references it.

**See also:**

- [Pipeline structure](../../building-models/index.md)  - how nodes connect in a pipeline
