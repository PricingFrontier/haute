import { afterEach, describe, it, expect } from "vitest"
import type { Node } from "@xyflow/react"
import { documentGraphPayload } from "../../utils/buildGraph"
import type { PipelineEdge } from "../../types/node"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode, makeSimpleEdge } from "../../test-utils/factories"

const loadDocument = (nodes: Node[], edges: PipelineEdge[] = [], submodels: Record<string, unknown> = {}) =>
  useGraphStore.getState().loadGraphSnapshot({ nodes, edges, preamble: "", submodels })

// ─── Tests ───────────────────────────────────────────────────────

describe("the request payload of the graph store's document", () => {
  afterEach(() => {
    useGraphStore.getState().resetForTests()
  })

  it("builds correct graph shape from nodes and edges, keeping each node's own type", () => {
    const nodes = [
      makeNode("n1", "dataInput", { type: "custom" }),
      makeNode("n2", "polars", { type: "custom" }),
    ]
    const edges = [makeSimpleEdge("e1", "n1", "n2") as PipelineEdge]
    loadDocument(nodes, edges)

    const result = documentGraphPayload()

    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toStrictEqual(edges)
    expect(result.edges).not.toBe(useGraphStore.getState().edges)

    // Each node keeps its id, its explicit type over data.nodeType, and its data.
    expect(result.nodes.map((node) => [node.id, node.type])).toEqual([["n1", "custom"], ["n2", "custom"]])
    expect(result.nodes[0].data).toEqual(nodes[0].data)
    expect(result.nodes[1].data).toEqual(nodes[1].data)
  })

  it("handles an empty document", () => {
    const result = documentGraphPayload()

    expect(result.nodes).toEqual([])
    expect(result.edges).toEqual([])
  })

  it("clones submodels into the outbound graph", () => {
    const submodels = {
      "sub1": { nodes: [], edges: [] },
      "sub2": { nodes: [], edges: [] },
    }
    loadDocument([], [], submodels)

    const result = documentGraphPayload()

    expect(result.submodels).toStrictEqual(submodels)
    expect(result.submodels).not.toBe(useGraphStore.getState().submodels)
  })

  it("preserves all data fields on mapped nodes", () => {
    loadDocument([
      makeNode("n1", "dataInput", {
        type: "custom",
        data: {
          label: "My Source",
          description: "Loads data",
          nodeType: "dataInput",
          config: { path: "/data/input.csv" },
          extraField: "should be preserved",
        },
      }),
    ])

    const mapped = documentGraphPayload().nodes[0]

    expect(mapped.data.label).toBe("My Source")
    expect(mapped.data.description).toBe("Loads data")
    expect(mapped.data.config).toEqual({ path: "/data/input.csv" })
    expect(mapped.data.extraField).toBe("should be preserved")
  })
})
