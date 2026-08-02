import { describe, expect, it } from "vitest"
import type { Edge, Node } from "@xyflow/react"
import type {
  PipelineEdge,
  SubmodelPortData,
} from "../../types/node"
import { makeEdge, makeNode } from "../../test-utils/factories"
import { buildSubmodelViewGraph } from "../submodelViewGraph"

function boundaryNodes(nodes: Node[], direction: "input" | "output"): Node[] {
  return nodes.filter(
    (node) =>
      node.type === "submodelPort" &&
      (node.data as unknown as SubmodelPortData).portDirection === direction,
  )
}

function boundaryData(node: Node): SubmodelPortData {
  return node.data as unknown as SubmodelPortData
}

function parentEdge(
  id: string,
  source: string,
  target: string,
  overrides: Partial<PipelineEdge> = {},
): PipelineEdge {
  return { id, source, target, ...overrides } as PipelineEdge
}

describe("buildSubmodelViewGraph", () => {
  it("builds one composite Input whose named frames can feed individual children", () => {
    const childNodes = [
      makeNode("add_drivers", "polars", { data: { label: "add_drivers" } }),
      makeNode("quote", "polars", { data: { label: "quote" } }),
      makeNode("claims", "edgeJoin", { data: { label: "claims" } }),
    ]
    const parentNodes = [
      makeNode("quote_input", "apiInput", { data: { label: "QUOTE IN" } }),
    ]
    const parentEdges = [
      parentEdge("in-quote-drivers", "quote_input", "submodel__pricing", {
        sourceHandle: "quote_info",
        targetHandle: "in__add_drivers",
      }),
      parentEdge("in-quote-quote", "quote_input", "submodel__pricing", {
        sourceHandle: "quote_info",
        targetHandle: "in__quote",
      }),
      parentEdge("in-claims", "quote_input", "submodel__pricing", {
        sourceHandle: "proposer_claims",
        targetHandle: "in__claims",
        targetPort: "join",
      }),
    ]

    const projected = buildSubmodelViewGraph({
      submodelName: "pricing",
      childNodes,
      childEdges: [],
      parentNodes,
      parentEdges,
    })

    const inputs = boundaryNodes(projected.nodes, "input")
    expect(inputs).toHaveLength(1)
    expect(boundaryNodes(projected.nodes, "output")).toHaveLength(1)

    const inputData = boundaryData(inputs[0])
    expect(inputData.label).toBe("INPUT")
    expect(inputData.ports.map((port) => port.label)).toEqual([
      "quote_info",
      "proposer_claims",
    ])
    expect(new Set(inputData.ports.map((port) => port.id)).size).toBe(2)
    expect(inputData.externalNodeIds).toEqual(["quote_input"])

    const inputEdges = projected.edges.filter((edge) => edge.source === inputs[0].id)
    expect(inputEdges).toHaveLength(3)
    expect(inputEdges.map((edge) => edge.sourceHandle)).toEqual([
      inputData.ports[0].id,
      inputData.ports[0].id,
      inputData.ports[1].id,
    ])
    expect(inputEdges.map((edge) => edge.target)).toEqual([
      "add_drivers",
      "quote",
      "claims",
    ])
    expect(inputEdges[2].targetHandle).toBe("join")
  })

  it("shows a newly connected parent frame as unassigned without choosing a child", () => {
    const childNodes = [makeNode("child")]
    const parentNodes = [
      makeNode("quote_input", "apiInput", { data: { label: "QUOTE IN" } }),
      makeNode("submodel__pricing", "submodel", { data: { label: "pricing" } }),
    ]
    const unassigned = parentEdge(
      "new-frame",
      "quote_input",
      "submodel__pricing",
      { sourceHandle: "additional_drivers", targetHandle: null },
    )

    const projected = buildSubmodelViewGraph({
      submodelName: "pricing",
      childNodes,
      childEdges: [],
      parentNodes,
      parentEdges: [unassigned],
    })

    const input = boundaryNodes(projected.nodes, "input")[0]
    const inputData = boundaryData(input)
    expect(inputData.ports).toHaveLength(1)
    expect(inputData.ports[0].label).toBe("additional_drivers")
    expect(inputData.ports[0].parentEdges).toEqual([unassigned])
    expect(projected.edges.filter((edge) => edge.source === input.id)).toEqual([])
  })

  it("keeps equal frame labels from different parent sources independently connectable", () => {
    const childNodes = [makeNode("left"), makeNode("right")]
    const parentNodes = [
      makeNode("api_a", "apiInput", { data: { label: "API A" } }),
      makeNode("api_b", "apiInput", { data: { label: "API B" } }),
    ]
    const parentEdges = [
      parentEdge("from-a", "api_a", "submodel__pricing", {
        sourceHandle: "quote",
        targetHandle: "in__left",
      }),
      parentEdge("from-b", "api_b", "submodel__pricing", {
        sourceHandle: "quote",
        targetHandle: "in__right",
      }),
    ]

    const projected = buildSubmodelViewGraph({
      submodelName: "pricing",
      childNodes,
      childEdges: [],
      parentNodes,
      parentEdges,
    })
    const input = boundaryNodes(projected.nodes, "input")[0]
    const ports = boundaryData(input).ports

    expect(ports.map((port) => port.label)).toEqual(["quote", "quote"])
    expect(ports[0].id).not.toBe(ports[1].id)
    expect(
      projected.edges
        .filter((edge) => edge.source === input.id)
        .map((edge) => edge.sourceHandle),
    ).toEqual([ports[0].id, ports[1].id])
  })

  it("uses the ordinary parent label for an id-less incoming frame", () => {
    const childNodes = [makeNode("child")]
    const parentNodes = [
      makeNode("data_source", "dataInput", { data: { label: "Policy snapshot" } }),
    ]
    const parentEdges = [
      parentEdge("ordinary-in", "data_source", "submodel__pricing", {
        targetHandle: "in__child",
      }),
    ]

    const projected = buildSubmodelViewGraph({
      submodelName: "pricing",
      childNodes,
      childEdges: [],
      parentNodes,
      parentEdges,
    })

    const input = boundaryNodes(projected.nodes, "input")[0]
    expect(boundaryData(input).ports.map((port) => port.label)).toEqual([
      "Policy snapshot",
    ])
  })

  it("projects declared exports onto one shared Output even without consumers", () => {
    const childNodes = [
      makeNode("claims_id", "polars", { data: { label: "claims" } }),
      makeNode("quote_id", "polars", { data: { label: "quote" } }),
    ]
    const parentEdges = [
      parentEdge("claims-to-a", "submodel__pricing", "consumer_a", {
        sourceHandle: "out__claims_id",
        sourcePort: "claims_frame",
      }),
      parentEdge("claims-to-b", "submodel__pricing", "consumer_b", {
        sourceHandle: "out__claims_id",
        sourcePort: "claims_frame",
      }),
    ]
    const parentNodes = [
      makeNode("submodel__pricing", "submodel", {
        data: {
          label: "pricing",
          config: {
            outputPorts: ["claims_id", "quote_id"],
            outputPortLabels: { claims_id: "claims", quote_id: "quote" },
          },
        },
      }),
    ]

    const projected = buildSubmodelViewGraph({
      submodelName: "pricing",
      childNodes,
      childEdges: [makeEdge("claims_id", "quote_id")],
      parentNodes,
      parentEdges,
    })

    const output = boundaryNodes(projected.nodes, "output")[0]
    expect(boundaryData(output).ports).toEqual([])
    expect(boundaryData(output).externalNodeIds).toEqual([
      "consumer_a", "consumer_b",
    ])

    const outputEdges = projected.edges.filter((edge) => edge.target === output.id)
    expect(outputEdges).toHaveLength(2)
    expect(outputEdges.map((edge) => edge.source)).toEqual([
      "claims_id",
      "quote_id",
    ])
    expect(outputEdges.map((edge) => edge.targetHandle)).toEqual([null, null])
    expect(outputEdges[0].sourceHandle).toBe("claims_frame")
    expect(outputEdges[0].data).toMatchObject({
      submodelBoundary: {
        direction: "output",
        parentConsumerEdges: parentEdges,
      },
    })
    expect(outputEdges[1].data).toMatchObject({
      submodelBoundary: {
        direction: "output",
        parentConsumerEdges: [],
      },
    })

    expect(projected.edges.some(
      (edge) => edge.source === "claims_id" && edge.target === "quote_id",
    )).toBe(true)
  })

  it("retains both empty boundary cards while omitting malformed or stale boundary edges", () => {
    const childNodes = [makeNode("child")]
    const parentEdges: PipelineEdge[] = [
      parentEdge("stale-input-child", "source", "submodel__pricing", {
        targetHandle: "in__missing",
      }),
      parentEdge("missing-output-handle", "submodel__pricing", "target"),
      parentEdge("stale-output-child", "submodel__pricing", "target", {
        sourceHandle: "out__missing",
      }),
    ]
    const childEdges: Edge[] = []

    const projected = buildSubmodelViewGraph({
      submodelName: "pricing",
      childNodes,
      childEdges,
      parentNodes: [makeNode("source")],
      parentEdges,
    })

    const input = boundaryNodes(projected.nodes, "input")[0]
    const output = boundaryNodes(projected.nodes, "output")[0]
    expect(boundaryData(input).ports).toEqual([])
    expect(boundaryData(output).ports).toEqual([])
    expect(boundaryData(input).externalNodeIds).toEqual([])
    expect(boundaryData(output).externalNodeIds).toEqual([])
    expect(projected.edges).toEqual([])
    expect(projected.nodes).toHaveLength(childNodes.length + 2)
    expect(childNodes).toHaveLength(1)
    expect(childEdges).toHaveLength(0)
  })
})
