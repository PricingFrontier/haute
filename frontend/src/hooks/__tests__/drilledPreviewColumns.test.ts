import { describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { makeEdge, makeIdentifiedNode, makeNode, makePreviewResponse } from "../../test-utils/factories"
import { buildSubmodelViewGraph } from "../../utils/submodelViewGraph"
import { applyPreviewResultColumnsToNodes } from "../usePipelineAPI"

// The pricing submodel, drilled into: its `policy` port feeds `bands` straight
// from the Input card, and its `items` port feeds `prepare`, which feeds
// `item_bands`. A second occurrence, pricing_copy, shares the definition.
const POLICY = [{ name: "policy_id", dtype: "String" }, { name: "age", dtype: "Int64" }]
const ITEMS = [{ name: "item_id", dtype: "String" }, { name: "value", dtype: "Float64" }]
const PREPARED = [...ITEMS, { name: "value_k", dtype: "Float64" }]
const DRILLED = { instanceId: "pricing", definitionId: "definition_pricing" }

const definition = (nodes: Node[]): SubmodelDefinition => ({
  definitionId: "definition_pricing",
  file: "modules/pricing.py",
  graph: { nodes, edges: [] },
  inputPorts: [
    { name: "policy", targets: [{ nodeId: "bands", handleId: null }] },
    { name: "items", targets: [{ nodeId: "prepare", handleId: null }] },
  ],
  outputPorts: [{ name: "rated", source: { nodeId: "item_bands", handleId: null } }],
})

const occurrence = (id: string) => makeNode(id, "submodel", {
  data: {
    label: id,
    nodeType: "submodel",
    config: { definition_id: "definition_pricing", alias: id, ...(id === "pricing" ? {} : { instance_of: "pricing" }) },
  },
})

function drilledView(): Node[] {
  const children = [makeIdentifiedNode("bands", "banding"), makeIdentifiedNode("prepare"), makeIdentifiedNode("item_bands", "banding")]
  const parentEdges: PipelineEdge[] = [
    { id: "feed-policy", source: "workbench", sourceHandle: "policy_frame", target: "pricing", targetHandle: "in__policy" },
    { id: "feed-items", source: "items_source", target: "pricing", targetHandle: "in__items" },
    { id: "feed-copy", source: "other_source", target: "pricing_copy", targetHandle: "in__policy" },
  ]
  return buildSubmodelViewGraph({
    submodelName: "pricing",
    instanceId: "pricing",
    definition: definition(children),
    childNodes: children,
    childEdges: [makeEdge("prepare", "item_bands")],
    parentNodes: [makeNode("workbench"), makeNode("items_source"), makeNode("other_source"), occurrence("pricing"), occurrence("pricing_copy")],
    parentEdges,
  }).nodes
}

const inputCard = (nodes: Node[]) => nodes.find((node) => (
  node.type === "submodelPort" && (node.data as SubmodelPortData).portDirection === "input"
))!
const byId = (nodes: Node[], id: string) => nodes.find((node) => node.id === id)!

describe("a drilled submodel view's preview columns", () => {
  it("records each child's columns from its runtime id and each Input card port's from the parent feeding it", () => {
    const result = makePreviewResponse({
      node_id: "submodel_runtime/pricing/item_bands",
      columns: [...PREPARED, { name: "value_band", dtype: "String" }],
      node_columns: {
        workbench: [{ name: "flat", dtype: "String" }],
        items_source: ITEMS,
        other_source: [{ name: "copy_only", dtype: "String" }],
        "submodel_runtime/pricing/prepare": PREPARED,
        "submodel_runtime/pricing/item_bands": [...PREPARED, { name: "value_band", dtype: "String" }],
        // A root node sharing a child's local id is not that child.
        prepare: [{ name: "root_prepare", dtype: "String" }],
      },
      node_frame_columns: { workbench: { policy_frame: POLICY } },
    })

    const nodes = applyPreviewResultColumnsToNodes(drilledView(), "item_bands", result, "live", 3, DRILLED)

    expect(byId(nodes, "prepare").data).toMatchObject({ _columns: PREPARED, _columnsSource: "live", _columnsStructuralVersion: 3 })
    expect(byId(nodes, "item_bands").data._columns).toEqual([...PREPARED, { name: "value_band", dtype: "String" }])
    expect(byId(nodes, "bands").data._columns).toBeUndefined()
    // The policy port takes its parent's frame; the copy's feed is not this occurrence's.
    expect(inputCard(nodes).data).toMatchObject({
      _frameColumns: { policy: POLICY, items: ITEMS },
      _columnsSource: "live",
    })
    expect(inputCard(nodes).data._columns).toBeUndefined()
  })

  it("keeps a port the preview did not reach while the source is the same, and drops it when it changed", () => {
    const both = makePreviewResponse({
      node_columns: { items_source: ITEMS },
      node_frame_columns: { workbench: { policy_frame: POLICY } },
    })
    const itemsOnly = makePreviewResponse({ node_columns: { items_source: PREPARED } })
    const previewed = applyPreviewResultColumnsToNodes(drilledView(), "bands", both, "live", 1, DRILLED)

    const sameSource = applyPreviewResultColumnsToNodes(previewed, "prepare", itemsOnly, "live", 1, DRILLED)
    expect(inputCard(sameSource).data._frameColumns).toEqual({ policy: POLICY, items: PREPARED })

    const otherSource = applyPreviewResultColumnsToNodes(previewed, "prepare", itemsOnly, "batch", 1, DRILLED)
    expect(inputCard(otherSource).data._frameColumns).toEqual({ items: PREPARED })
  })

  it("leaves the Input card alone when the preview reached none of its ports", () => {
    const nodes = drilledView()
    const result = makePreviewResponse({ node_columns: { "submodel_runtime/pricing/prepare": PREPARED } })

    const applied = applyPreviewResultColumnsToNodes(nodes, "prepare", result, "live", 1, DRILLED)

    expect(inputCard(applied)).toBe(inputCard(nodes))
  })
})
