import { describe, expect, it } from "vitest"
import { NODE_TYPES } from "../nodeTypes"
import {
  appEdge,
  appNode,
  deselectNodes,
  freshNodeLabel,
  nodeLabel,
  nodeLabels,
  selectOnlyNode,
} from "../flowElements"

describe("flowElements", () => {
  it("creates app nodes from node metadata defaults", () => {
    const node = appNode({
      id: "edgeJoin_7",
      type: NODE_TYPES.EDGE_JOIN,
      position: { x: 12, y: 34 },
      config: {},
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
        },
      },
    })
  })

  it("uses the node metadata name when generating labels", () => {
    expect(nodeLabel(NODE_TYPES.POLARS, "polars_2")).toBe("Transform 2")
    expect(nodeLabel(NODE_TYPES.EDGE_JOIN, "edgeJoin_10")).toBe("Edge Join 10")
    expect(() => nodeLabel("unknown" as never, "mystery")).toThrow("Unknown node type")
  })

  it("mints each edge's id from its identity and normalizes absent handles", () => {
    expect(appEdge({ source: "quote", target: "price", sourceHandle: "policy" })).toEqual({
      id: "e-quote-price-policy",
      source: "quote",
      target: "price",
      sourceHandle: "policy",
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

describe("a new node's name (READ-13)", () => {
  it("is its type in snake_case, as its decorator spells it", () => {
    expect(freshNodeLabel(NODE_TYPES.WORKBENCH_OUTPUT, [])).toBe("workbench_output")
    expect(freshNodeLabel(NODE_TYPES.POLARS, ["quotes"])).toBe("polars")
  })

  it("takes the next free number only when the name is taken, ignoring case", () => {
    expect(freshNodeLabel(NODE_TYPES.POLARS, ["Polars"])).toBe("polars_2")
    expect(freshNodeLabel(NODE_TYPES.POLARS, ["polars", "polars_2", "polars_4"])).toBe("polars_3")
  })

  it("reads the labels of the nodes already on the canvas", () => {
    const nodes = [appNode({ id: "a", type: NODE_TYPES.EDGE_JOIN, position: { x: 0, y: 0 }, label: "edge_join" })]
    expect(freshNodeLabel(NODE_TYPES.EDGE_JOIN, nodeLabels(nodes))).toBe("edge_join_2")
  })
})
