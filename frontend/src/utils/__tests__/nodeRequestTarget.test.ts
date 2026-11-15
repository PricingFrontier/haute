import { afterEach, describe, expect, it } from "vitest"

import type { SimpleEdge, SimpleNode } from "../../panels/editors/_shared"
import { buildGraph } from "../buildGraph"
import {
  nodeRequestTarget,
  pipelineRequestGraph,
  requestNodeNamer,
  setDrilledRequestScope,
} from "../nodeRequestTarget"

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

  it("reads an Input card's port at the parent node feeding it", () => {
    setDrilledRequestScope(() => ({
      graph: rootGraph,
      occurrence: { instanceId: "br rating", definitionId: "br_rating" },
    }))
    const parentEdges = (port: string, source: string, sourceHandle: string | null) => [
      { id: `${port}-other`, source, sourceHandle, target: "another_occurrence", targetHandle: port },
      { id: port, source, sourceHandle, target: "br rating", targetHandle: port },
    ]
    const card = (ports: Array<{ id: string; parentEdges: unknown[] }>): SimpleNode => ({
      id: "input_card",
      data: {
        label: "Input", description: "", nodeType: "submodelPort", config: {},
        portDirection: "input", instanceId: "br rating", definitionId: "br_rating",
        ports: ports.map((port) => ({ ...port, label: port.id })),
      } as unknown as SimpleNode["data"],
    })
    const twoPorts = [card([
      { id: "quotes", parentEdges: parentEdges("quotes", "workbench_input", "quote") },
      { id: "claims", parentEdges: parentEdges("claims", "claims_join", null) },
    ]), ...viewNodes]

    const quotes = nodeRequestTarget(twoPorts, viewEdges, {}, "input_card", "quotes")
    expect(quotes.graph).toBe(rootGraph)
    expect([quotes.nodeId, quotes.portLabel]).toEqual(["workbench_input", "quote"])
    const claims = nodeRequestTarget(twoPorts, viewEdges, {}, "input_card", "claims")
    expect([claims.nodeId, claims.portLabel]).toEqual(["claims_join", undefined])
    // With no port named, only a card of one port says which frame.
    expect(nodeRequestTarget(twoPorts, viewEdges, {}, "input_card").nodeId).toBe("submodel_runtime/br%20rating/input_card")
    const onePort = [card([{ id: "quotes", parentEdges: parentEdges("quotes", "workbench_input", "quote") }])]
    expect(nodeRequestTarget(onePort, [], {}, "input_card").nodeId).toBe("workbench_input")
    // Any other node keeps the port the caller reads.
    expect(nodeRequestTarget(viewNodes, viewEdges, {}, "bands", "out").portLabel).toBe("out")
  })

  it("sends the whole pipeline for a request about all of it, and names nodes without building a graph", () => {
    const named = requestNodeNamer(viewNodes)
    expect(pipelineRequestGraph(viewNodes, viewEdges, {})).toEqual(buildGraph(viewNodes, viewEdges, {}))
    expect(named("tables")?.id).toBe("tables")

    setDrilledRequestScope(() => ({
      graph: rootGraph,
      occurrence: { instanceId: "br rating", definitionId: "br_rating" },
    }))
    expect(pipelineRequestGraph(viewNodes, viewEdges, {})).toBe(rootGraph)
    const drilledNamed = requestNodeNamer(viewNodes)
    expect(drilledNamed("submodel_runtime/br%20rating/tables")?.id).toBe("tables")
    expect(drilledNamed("workbench_input")?.id).toBe("workbench_input")
  })

  it("sends the graph the caller holds when the registered scope has no submodel open", () => {
    setDrilledRequestScope(() => null)

    const target = nodeRequestTarget(viewNodes, viewEdges, {}, "bands")

    expect(target.nodeId).toBe("bands")
    expect(target.graph).toEqual(buildGraph(viewNodes, viewEdges, {}))
  })
})
