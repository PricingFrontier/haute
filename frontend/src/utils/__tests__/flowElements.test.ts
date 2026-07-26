import { describe, expect, it } from "vitest"
import { NODE_TYPES } from "../nodeTypes"
import {
  appEdge,
  appNode,
  deselectNodes,
  edgeId,
  nodeLabel,
  selectOnlyNode,
} from "../flowElements"

describe("flowElements", () => {
  it("creates app nodes from node metadata defaults", () => {
    const node = appNode({
      id: "edgeJoin_7",
      type: NODE_TYPES.EDGE_JOIN,
      position: { x: 12, y: 34 },
      config: { baseInput: "base", joinInput: "lookup" },
    })

    expect(node).toMatchObject({
      id: "edgeJoin_7",
      type: NODE_TYPES.EDGE_JOIN,
      position: { x: 12, y: 34 },
      origin: [0.5, 0.5],
      data: {
        label: "Edge Join 7",
        description: "",
        nodeType: NODE_TYPES.EDGE_JOIN,
        config: {
          how: "left",
          suffix: "_right",
          baseInput: "base",
          joinInput: "lookup",
        },
      },
    })
  })

  it("uses the node metadata name when generating labels", () => {
    expect(nodeLabel(NODE_TYPES.POLARS, "polars_2")).toBe("Polars 2")
    expect(nodeLabel(NODE_TYPES.EDGE_JOIN, "edgeJoin_10")).toBe("Edge Join 10")
    expect(() => nodeLabel("unknown" as never, "mystery")).toThrow("Unknown node type")
  })

  it("creates deterministic edge ids and normalized edge handle fields", () => {
    expect(edgeId("source", "target", "in", "out")).toBe("e_source_target_in_out")
    expect(appEdge({ source: "source", target: "target" })).toEqual({
      id: "e_source_target_default_default",
      source: "source",
      target: "target",
      sourceHandle: null,
      targetHandle: null,
    })
  })

  it("deselects nodes without mutating the original node objects", () => {
    const nodes = [
      { id: "a", selected: true },
      { id: "b", selected: false },
    ]

    const result = deselectNodes(nodes)

    expect(result).toEqual([
      { id: "a", selected: false },
      { id: "b", selected: false },
    ])
    expect(nodes[0].selected).toBe(true)
  })

  it("selects exactly one node by id without mutating existing node objects", () => {
    const nodes = [
      { id: "a", selected: true },
      { id: "b", selected: false },
    ]

    const result = selectOnlyNode(nodes, "b")

    expect(result).toEqual([
      { id: "a", selected: false },
      { id: "b", selected: true },
    ])
    expect(nodes[0].selected).toBe(true)
  })
})
