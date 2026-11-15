import { afterEach, describe, expect, it } from "vitest"

import type { SimpleEdge, SimpleNode } from "../../panels/editors/_shared"
import { buildGraph } from "../buildGraph"
import { nodeRequestTarget, setDrilledRequestScope } from "../nodeRequestTarget"

function node(id: string, nodeType: string): SimpleNode {
  return { id, data: { label: id, description: "", nodeType, config: {} } }
}

// The drilled view of occurrence "br rating" (a space, so its runtime id is encoded).
const viewNodes = [node("bands", "banding"), node("tables", "ratingStep")]
const viewEdges: SimpleEdge[] = [{ id: "e1", source: "bands", target: "tables" }]
const rootGraph = buildGraph([node("workbench_input", "workbenchInput"), node("br rating", "submodel")], [], {})

describe("nodeRequestTarget", () => {
  afterEach(() => setDrilledRequestScope(null))

  it("sends the graph the caller holds outside a submodel", () => {
    const target = nodeRequestTarget(viewNodes, viewEdges, {}, "bands")

    expect(target.graph).toEqual(buildGraph(viewNodes, viewEdges, {}))
    expect(target.nodeId).toBe("bands")
    expect(target.nodeNamed("tables")?.id).toBe("tables")
  })

  it("sends the whole pipeline and the node's runtime id inside an open submodel", () => {
    setDrilledRequestScope(() => ({
      graph: rootGraph,
      occurrence: { instanceId: "br rating", definitionId: "br_rating" },
    }))

    const target = nodeRequestTarget(viewNodes, viewEdges, {}, "bands")

    expect(target.graph).toBe(rootGraph)
    expect(target.nodeId).toBe("submodel_runtime/br%20rating/bands")
    // An answer names a node on screen by its runtime id, and one outside the
    // submodel by its own.
    expect(target.nodeNamed("submodel_runtime/br%20rating/tables")?.id).toBe("tables")
    expect(target.nodeNamed("workbench_input")?.id).toBe("workbench_input")
    expect(target.nodeNamed("submodel_runtime/other/tables")).toBeNull()
  })

  it("sends the graph the caller holds when the registered scope has no submodel open", () => {
    setDrilledRequestScope(() => null)

    const target = nodeRequestTarget(viewNodes, viewEdges, {}, "bands")

    expect(target.nodeId).toBe("bands")
    expect(target.graph).toEqual(buildGraph(viewNodes, viewEdges, {}))
  })
})
