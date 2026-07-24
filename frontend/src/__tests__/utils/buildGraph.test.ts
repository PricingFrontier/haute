import { describe, it, expect } from "vitest"
import { buildGraph } from "../../utils/buildGraph"
import type { SimpleNode, SimpleEdge } from "../../panels/editors/_shared"
import { makeSimpleNode, makeSimpleEdge } from "../../test-utils/factories"

// ─── Tests ───────────────────────────────────────────────────────

describe("buildGraph", () => {
  it("builds correct graph shape from nodes and edges", () => {
    const nodes: SimpleNode[] = [
      makeSimpleNode("n1", "dataInput", { type: "custom" }),
      makeSimpleNode("n2", "polars", { type: "custom" }),
    ]
    const edges: SimpleEdge[] = [makeSimpleEdge("e1", "n1", "n2")]

    const result = buildGraph(nodes, edges)

    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toBe(edges)

    // Each node should have id, type, data, and a position
    expect(result.nodes[0]).toEqual({
      id: "n1",
      type: "custom",
      data: nodes[0].data,
      position: { x: 0, y: 0 },
    })
    expect(result.nodes[1]).toEqual({
      id: "n2",
      type: "custom",
      data: nodes[1].data,
      position: { x: 0, y: 0 },
    })
  })

  it("handles empty nodes array", () => {
    const result = buildGraph([], [])

    expect(result.nodes).toEqual([])
    expect(result.edges).toEqual([])
    expect(result.submodels).toBeUndefined()
  })

  it("maps node type from data.nodeType when type is undefined", () => {
    const nodes: SimpleNode[] = [
      makeSimpleNode("n1", "banding"),  // no type override -> n.type is undefined
    ]

    const result = buildGraph(nodes, [])

    // Should fall back to n.data.nodeType
    expect(result.nodes[0].type).toBe("banding")
  })

  it("prefers n.type over n.data.nodeType when both are present", () => {
    const nodes: SimpleNode[] = [
      makeSimpleNode("n1", "polars", { type: "custom" }),
    ]

    const result = buildGraph(nodes, [])

    expect(result.nodes[0].type).toBe("custom")
  })

  it("passes submodels through", () => {
    const submodels = {
      "sub1": { nodes: [], edges: [] },
      "sub2": { nodes: [], edges: [] },
    }

    const result = buildGraph([], [], submodels)

    expect(result.submodels).toBe(submodels)
  })

  it("handles undefined submodels", () => {
    const result = buildGraph([], [])

    expect(result.submodels).toBeUndefined()
  })

  it("passes preamble through", () => {
    const preamble = "def helper():\n    return 42\n"
    const result = buildGraph([], [], undefined, preamble)

    expect(result.preamble).toBe(preamble)
  })

  it("handles undefined preamble", () => {
    const result = buildGraph([], [])

    expect(result.preamble).toBeUndefined()
  })

  it("preserves all data fields on mapped nodes", () => {
    const nodes: SimpleNode[] = [
      {
        id: "n1",
        type: "custom",
        data: {
          label: "My Source",
          description: "Loads data",
          nodeType: "dataInput",
          config: { path: "/data/input.csv" },
          extraField: "should be preserved",
        },
      },
    ]

    const result = buildGraph(nodes, [])
    const mapped = result.nodes[0]

    expect(mapped.data.label).toBe("My Source")
    expect(mapped.data.description).toBe("Loads data")
    expect(mapped.data.config).toEqual({ path: "/data/input.csv" })
    expect(mapped.data.extraField).toBe("should be preserved")
  })

  it("always sets position to {x: 0, y: 0}", () => {
    const nodes: SimpleNode[] = [
      makeSimpleNode("n1", "polars", { type: "custom" }),
      makeSimpleNode("n2", "output", { type: "custom" }),
      makeSimpleNode("n3", "banding"),
    ]

    const result = buildGraph(nodes, [])

    for (const node of result.nodes) {
      expect(node.position).toEqual({ x: 0, y: 0 })
    }
  })
})
