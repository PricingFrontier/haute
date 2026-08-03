import { describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { makeEdge, makeNode } from "../../test-utils/factories"
import { buildSubmodelViewGraph } from "../submodelViewGraph"

const makeDefinition = (nodes: Node[]): SubmodelDefinition => ({
  definitionId: "definition_pricing", file: "modules/pricing.py", graph: { nodes, edges: [] },
  inputPorts: [{ portId: "policy", label: "Policy data", targets: [{ nodeId: "prepare", handleId: null }] }],
  outputPorts: [{ portId: "premium", label: "Written premium", source: { nodeId: "score", handleId: "out" } }],
})
const instance = () => makeNode("instance_primary", "submodel", { data: { label: "Pricing", nodeType: "submodel", config: { definitionId: "definition_pricing", alias: "pricing" } } })
const boundary = (nodes: Node[], direction: "input" | "output") => nodes.find((node) => node.type === "submodelPort" && (node.data as SubmodelPortData).portDirection === direction)!

describe("buildSubmodelViewGraph", () => {
  it("projects canonical ports through declared endpoints", () => {
    const children = [makeNode("prepare"), makeNode("score")]
    const parentEdges: PipelineEdge[] = [{ id: "feed", source: "api", sourceHandle: "quotes", target: "instance_primary", targetHandle: "in__policy" }, { id: "consume", source: "instance_primary", sourceHandle: "out__premium", target: "output" }]
    const graph = buildSubmodelViewGraph({ submodelName: "pricing", instanceId: "instance_primary", definition: makeDefinition(children), childNodes: children, childEdges: [makeEdge("prepare", "score")], parentNodes: [makeNode("api"), instance(), makeNode("output")], parentEdges })
    const input = boundary(graph.nodes, "input").data as SubmodelPortData
    const output = boundary(graph.nodes, "output").data as SubmodelPortData
    expect(input).toMatchObject({ instanceId: "instance_primary", definitionId: "definition_pricing", ports: [{ id: "policy", label: "Policy data", parentEdges: [parentEdges[0]] }] })
    expect(output).toMatchObject({ instanceId: "instance_primary", definitionId: "definition_pricing", externalNodeIds: ["output"] })
    expect(graph.edges).toEqual(expect.arrayContaining([expect.objectContaining({ sourceHandle: "policy", target: "prepare", data: { submodelBoundary: { direction: "input", portId: "policy", parentEdges: [parentEdges[0]] } } }), expect.objectContaining({ source: "score", sourceHandle: "out", data: { submodelBoundary: { direction: "output", portId: "premium", parentConsumerEdges: [parentEdges[1]] } } })]))
  })
  it("rejects undeclared parent handles", () => {
    const children = [makeNode("prepare"), makeNode("score")]
    expect(() => buildSubmodelViewGraph({ submodelName: "pricing", instanceId: "instance_primary", definition: makeDefinition(children), childNodes: children, childEdges: [], parentNodes: [instance()], parentEdges: [{ id: "bad", source: "api", target: "instance_primary", targetHandle: "in__unknown" }] })).toThrow(/undeclared input handle/)
  })
})