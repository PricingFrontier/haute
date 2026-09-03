import { describe, expect, it } from "vitest"
import { structuralFingerprint } from "../structuralFingerprint"

const baseNodes = [
  { id: "b", type: "pipelineNode", data: { nodeType: "polars", label: "B" } },
  { id: "a", type: "pipelineNode", data: { nodeType: "dataInput", label: "A" } },
]
const baseEdges = [
  { id: "e1", source: "a", sourceHandle: null, target: "b", targetHandle: null },
]
const definition = {
  definitionId: "pricing",
  file: "modules/pricing.py",
  graph: { nodes: [], edges: [] },
  inputPorts: [{ portId: "input_1", label: "Incoming", targets: [{ nodeId: "a", handleId: null }] }],
  outputPorts: [{ portId: "output_1", label: "Priced", source: { nodeId: "a", handleId: null } }],
}

describe("structuralFingerprint", () => {
  it("is stable across node order", () => {
    expect(structuralFingerprint({ nodes: baseNodes, edges: baseEdges })).toBe(
      structuralFingerprint({ nodes: [...baseNodes].reverse(), edges: baseEdges }),
    )
  })

  it("ignores position, selection, dragging, dimensions and unrelated data", () => {
    const decorated = baseNodes.map((node) => ({
      ...node,
      position: { x: 10, y: 20 },
      selected: true,
      dragging: true,
      measured: { width: 5, height: 6 },
      width: 5,
      height: 6,
      data: { ...node.data, label: "renamed", config: { path: "x.parquet" } },
    }))

    expect(structuralFingerprint({ nodes: decorated, edges: baseEdges })).toBe(
      structuralFingerprint({ nodes: baseNodes, edges: baseEdges }),
    )
  })

  it("changes when a node leaves, changes kind, or is rewired", () => {
    const base = structuralFingerprint({ nodes: baseNodes, edges: baseEdges })

    expect(structuralFingerprint({ nodes: [baseNodes[0]!], edges: baseEdges })).not.toBe(base)
    expect(structuralFingerprint({
      nodes: [baseNodes[0]!, { ...baseNodes[1]!, data: { nodeType: "polars", label: "A" } }],
      edges: baseEdges,
    })).not.toBe(base)
    expect(structuralFingerprint({
      nodes: baseNodes,
      edges: [{ ...baseEdges[0]!, targetHandle: "in_2" }],
    })).not.toBe(base)
  })

  it("tracks each definition's public interface", () => {
    const base = structuralFingerprint({ nodes: [], edges: [], submodels: { pricing: definition } })

    expect(structuralFingerprint({
      nodes: [],
      edges: [],
      submodels: {
        pricing: {
          ...definition,
          outputPorts: [{ portId: "output_1", label: "Renamed", source: { nodeId: "a", handleId: null } }],
        },
      },
    })).not.toBe(base)
    expect(structuralFingerprint({
      nodes: [],
      edges: [],
      submodels: {
        pricing: { ...definition, graph: { nodes: [{ id: "z" }], edges: [] } },
      },
    })).toBe(base)
  })
})
