import { describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { makeEdge, makeNode } from "../../test-utils/factories"
import { buildSubmodelViewGraph } from "../submodelViewGraph"

const makeDefinition = (nodes: Node[]): SubmodelDefinition => ({
  definitionId: "definition_pricing", file: "modules/pricing.py", graph: { nodes, edges: [] },
  inputPorts: [{ name: "policy", targets: [{ nodeId: "prepare", handleId: null }] }],
  outputPorts: [{ name: "premium", source: { nodeId: "score", handleId: "out" } }],
})
const instance = () => makeNode("pricing", "submodel", { data: { label: "pricing", nodeType: "submodel", config: { definitionId: "definition_pricing", alias: "pricing" } } })
const boundary = (nodes: Node[], direction: "input" | "output") => nodes.find((node) => node.type === "submodelPort" && (node.data as SubmodelPortData).portDirection === direction)!

describe("buildSubmodelViewGraph", () => {
  it("projects canonical ports through declared endpoints", () => {
    const children = [makeNode("prepare"), makeNode("score")]
    const parentEdges: PipelineEdge[] = [{ id: "feed", source: "api", sourceHandle: "quotes", target: "pricing", targetHandle: "in__policy" }, { id: "consume", source: "pricing", sourceHandle: "out__premium", target: "output" }]
    const graph = buildSubmodelViewGraph({ submodelName: "pricing", instanceId: "pricing", definition: makeDefinition(children), childNodes: children, childEdges: [makeEdge("prepare", "score")], parentNodes: [makeNode("api"), instance(), makeNode("output")], parentEdges })
    const input = boundary(graph.nodes, "input").data as SubmodelPortData
    const output = boundary(graph.nodes, "output").data as SubmodelPortData
    expect(input).toMatchObject({ instanceId: "pricing", definitionId: "definition_pricing", ports: [{ id: "policy", label: "policy", parentEdges: [parentEdges[0]] }] })
    expect(output).toMatchObject({ instanceId: "pricing", definitionId: "definition_pricing", externalNodeIds: ["output"] })
    expect(graph.edges).toEqual(expect.arrayContaining([expect.objectContaining({ sourceHandle: "policy", target: "prepare", data: { submodelBoundary: { direction: "input", name: "policy", parentEdges: [parentEdges[0]] } } }), expect.objectContaining({ source: "score", sourceHandle: "out", data: { submodelBoundary: { direction: "output", name: "premium", parentConsumerEdges: [parentEdges[1]] } } })]))
  })
  it("retains input bindings from every occurrence in the shared frame projection", () => {
    const children = [makeNode("prepare"), makeNode("score")]
    const secondary = makeNode("pricing_copy", "submodel", {
      data: {
        label: "pricing_copy",
        nodeType: "submodel",
        config: {
          definitionId: "definition_pricing",
          alias: "pricing_copy",
          instanceOf: "pricing",
        },
      },
    })
    const parentEdges: PipelineEdge[] = [
      {
        id: "feed-owner",
        source: "api-owner",
        target: "pricing",
        targetHandle: "in__policy",
      },
      {
        id: "feed-copy",
        source: "api-copy",
        target: "pricing_copy",
        targetHandle: "in__policy",
      },
    ]

    const graph = buildSubmodelViewGraph({
      submodelName: "pricing",
      instanceId: "pricing",
      definition: makeDefinition(children),
      childNodes: children,
      childEdges: [],
      parentNodes: [makeNode("api-owner"), makeNode("api-copy"), instance(), secondary],
      parentEdges,
    })

    const input = boundary(graph.nodes, "input").data as SubmodelPortData
    expect(input.ports[0].parentEdges).toEqual(parentEdges)
    expect(input._parentBindingScope).toBe("definition")
    // Parent edge order is persisted state, so the projection records it and a
    // binding restored by history returns to its own position.
    expect(input._parentEdgeOrder).toEqual(["feed-owner", "feed-copy"])
    expect((boundary(graph.nodes, "output").data as SubmodelPortData)._parentEdgeOrder)
      .toBeUndefined()
    expect(input.externalNodeIds).toEqual(["api-owner"])
    expect(graph.edges.find((edge) => edge.sourceHandle === "policy")?.data)
      .toMatchObject({ submodelBoundary: { parentEdges: [parentEdges[0]] } })
  })
  it("rejects undeclared parent handles", () => {
    const children = [makeNode("prepare"), makeNode("score")]
    expect(() => buildSubmodelViewGraph({ submodelName: "pricing", instanceId: "pricing", definition: makeDefinition(children), childNodes: children, childEdges: [], parentNodes: [instance()], parentEdges: [{ id: "bad", source: "api", target: "pricing", targetHandle: "in__unknown" }] })).toThrow(/undeclared input handle/)
  })
})
