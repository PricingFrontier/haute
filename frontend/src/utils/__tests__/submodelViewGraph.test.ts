import { describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { makeEdge, makeIdentifiedNode, makeNode } from "../../test-utils/factories"
import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import { buildSubmodelViewGraph, submodelPortIdentity } from "../submodelViewGraph"

const makeDefinition = (nodes: Node[]): SubmodelDefinition => ({
  definitionId: "definition_pricing", file: "modules/pricing.py", graph: { nodes, edges: [] },
  inputPorts: [{ name: "policy", targets: [{ nodeId: "prepare", handleId: null }] }],
  outputPorts: [{ name: "premium", source: { nodeId: "score", handleId: "out" } }],
})
const instance = () => makeNode("pricing", "submodel", { data: { label: "pricing", nodeType: "submodel", config: { definition_id: "definition_pricing", alias: "pricing" } } })
const boundary = (nodes: Node[], direction: "input" | "output") => nodes.find((node) => node.type === "submodelPort" && (node.data as SubmodelPortData).portDirection === direction)!

interface PortCardCase {
  direction: "input" | "output"
  ports: string[]
  identity: {
    function_name: string
    config_reference: string | null
    default_input_name: string | null
    source_handle_input_names: Record<string, string>
  }
}

describe("submodelPortIdentity", () => {
  it("names a card as the server names it", () => {
    const { cases } = loadUiContractFixture<{ cases: PortCardCase[] }>("submodel_port_identities")
    for (const { direction, ports, identity } of cases) {
      expect(submodelPortIdentity(direction, ports)).toEqual({
        _functionName: identity.function_name,
        _defaultInputName: identity.default_input_name,
        _sourceHandleInputNames: identity.source_handle_input_names,
      })
      expect(identity.config_reference).toBeNull()
    }
  })
})

describe("buildSubmodelViewGraph", () => {
  it("projects canonical ports through declared endpoints", () => {
    const children = [makeIdentifiedNode("prepare"), makeIdentifiedNode("score")]
    const parentEdges: PipelineEdge[] = [{ id: "feed", source: "api", sourceHandle: "quotes", target: "pricing", targetHandle: "in__policy" }, { id: "consume", source: "pricing", sourceHandle: "out__premium", target: "output" }]
    const graph = buildSubmodelViewGraph({ submodelName: "pricing", instanceId: "pricing", definition: makeDefinition(children), childNodes: children, childEdges: [makeEdge("prepare", "score")], parentNodes: [makeNode("api"), instance(), makeNode("output")], parentEdges })
    const input = boundary(graph.nodes, "input").data as SubmodelPortData
    const output = boundary(graph.nodes, "output").data as SubmodelPortData
    expect(input).toMatchObject({ instanceId: "pricing", definitionId: "definition_pricing", ports: [{ id: "policy", label: "policy", parentEdges: [parentEdges[0]] }] })
    expect(output).toMatchObject({ instanceId: "pricing", definitionId: "definition_pricing", externalNodeIds: ["output"] })
    expect(graph.edges).toEqual(expect.arrayContaining([expect.objectContaining({ sourceHandle: "policy", target: "prepare", data: { _inputName: "policy", submodelBoundary: { direction: "input", name: "policy", parentEdges: [parentEdges[0]] } } }), expect.objectContaining({ source: "score", sourceHandle: "out", data: { _inputName: "score", submodelBoundary: { direction: "output", name: "premium", parentConsumerEdges: [parentEdges[1]] } } })]))
    // The cards are named from their ports, as the server names them, so the view asks it nothing.
    expect(input).toMatchObject({ _functionName: "INPUT", _defaultInputName: null, _sourceHandleInputNames: { policy: "policy" } })
    expect(output).toMatchObject({ _functionName: "OUTPUT", _defaultInputName: null, _sourceHandleInputNames: {} })
  })
  it("retains input bindings from every occurrence in the shared frame projection", () => {
    const children = [makeIdentifiedNode("prepare"), makeIdentifiedNode("score")]
    const secondary = makeNode("pricing_copy", "submodel", {
      data: {
        label: "pricing_copy",
        nodeType: "submodel",
        config: {
          definition_id: "definition_pricing",
          alias: "pricing_copy",
          instance_of: "pricing",
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
  it("starts the Input card with the columns its parent feeds last recorded, never an invalidated stash", () => {
    const policy = [{ name: "policy_id", dtype: "String" }]
    const children = [makeIdentifiedNode("prepare"), makeIdentifiedNode("score")]
    const feed: PipelineEdge = { id: "feed", source: "api", sourceHandle: "quotes", target: "pricing", targetHandle: "in__policy" }
    const build = (api: Node) => buildSubmodelViewGraph({ submodelName: "pricing", instanceId: "pricing", definition: makeDefinition(children), childNodes: children, childEdges: [], parentNodes: [api, instance()], parentEdges: [feed] })
    const recorded = makeNode("api", "apiInput", { data: { label: "api", nodeType: "apiInput", _frameColumns: { quotes: policy }, _columns: [{ name: "other_frame", dtype: "String" }], _columnsSource: "live" } })

    expect(boundary(build(recorded).nodes, "input").data).toMatchObject({ _frameColumns: { policy }, _columnsSource: "live" })
    // A source switch strips the tag along with the stash it vouched for.
    const invalidated = makeNode("api", "apiInput", { data: { label: "api", nodeType: "apiInput", _frameColumns: { quotes: policy } } })
    expect(boundary(build(invalidated).nodes, "input").data._frameColumns).toBeUndefined()
  })
  it("rejects undeclared parent handles", () => {
    const children = [makeIdentifiedNode("prepare"), makeIdentifiedNode("score")]
    expect(() => buildSubmodelViewGraph({ submodelName: "pricing", instanceId: "pricing", definition: makeDefinition(children), childNodes: children, childEdges: [], parentNodes: [instance()], parentEdges: [{ id: "bad", source: "api", target: "pricing", targetHandle: "in__unknown" }] })).toThrow(/undeclared input handle/)
  })
})
