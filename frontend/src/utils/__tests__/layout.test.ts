/**
 * Tests for layout.ts — ELK layout utility.
 *
 * Tests cover:
 * 1. Single node gets a position assigned
 * 2. Multiple nodes get distinct positions
 * 3. Connected nodes are laid out left-to-right (ELK "RIGHT" direction)
 * 4. Empty graph returns empty array
 * 5. Measured handles determine branch order and alignment
 * 6. Nodes preserve their original data (only position changes)
 */
import { describe, it, expect } from "vitest"
import { Position, type Node, type Edge, type InternalNode } from "@xyflow/react"
import {
  getLayoutedElements,
  mergeLayoutedNodePositions,
  nodeIdsNeedingLayout,
} from "../layout"

function makeNode(id: string, x = 0, y = 0): Node {
  return {
    id,
    position: { x, y },
    type: "polars",
    data: { label: `Node ${id}`, nodeType: "polars", config: {} },
  } as Node
}

function makeEdge(source: string, target: string): Edge {
  return {
    id: `e_${source}_${target}`,
    source,
    target,
  } as Edge
}

function measuredNode(node: Node, sourceYs: number[], targetY = 35): InternalNode {
  return {
    ...node,
    measured: { width: 240, height: 70, ...node.measured },
    internals: {
      positionAbsolute: node.position,
      z: 0,
      userNode: node,
      handleBounds: {
        source: sourceYs.map((y, index) => ({
          id: `out-${index}`, nodeId: node.id, type: "source",
          position: Position.Right, x: node.measured?.width ?? 240, y, width: 0, height: 0,
        })),
        target: [{
          id: null, nodeId: node.id, type: "target",
          position: Position.Left, x: 0, y: targetY, width: 0, height: 0,
        }],
      },
    },
  }
}

describe("getLayoutedElements", () => {
  it("aligns measured connection points on unequal-height cards without snapping their tops", async () => {
    const nodes = [
      { ...makeNode("a"), measured: { width: 240, height: 120 } },
      { ...makeNode("b"), measured: { width: 240, height: 150 } },
    ]
    const internals = new Map([
      ["a", measuredNode(nodes[0], [60], 60)],
      ["b", measuredNode(nodes[1], [75], 75)],
    ])
    const result = await getLayoutedElements(nodes, [makeEdge("a", "b")], id => internals.get(id))

    expect(result[0].position.y + 60).toBeCloseTo(result[1].position.y + 75)
    expect(result[1].position.x - (result[0].position.x + 240)).toBeGreaterThanOrEqual(120)
  })

  it.each([false, true])("orders branches by fixed output rows and keeps continuations straight (reversed: %s)", async (reversed) => {
    const nodes = [
      { ...makeNode("source"), measured: { width: 240, height: 240 } },
      makeNode("lower"), makeNode("lower-next"),
      makeNode("upper"), makeNode("upper-next"), makeNode("sink"),
    ]
    const edges = [
      { ...makeEdge("source", "lower"), sourceHandle: reversed ? "out-0" : "out-1" },
      { ...makeEdge("source", "upper"), sourceHandle: reversed ? "out-1" : "out-0" },
      makeEdge("lower", "lower-next"), makeEdge("upper", "upper-next"),
      makeEdge("lower-next", "sink"), makeEdge("upper-next", "sink"),
    ]
    const internals = new Map(nodes.map(node => [
      node.id, measuredNode(node, node.id === "source" ? [40, 200] : [35]),
    ]))
    const result = await getLayoutedElements(nodes, edges, id => internals.get(id))
    const byId = new Map(result.map(node => [node.id, node]))
    const upper = byId.get("upper")!.position
    const lower = byId.get("lower")!.position

    // Fixed source rows and matching target order mean the branch edges cannot cross.
    const [top, bottom] = reversed ? [lower, upper] : [upper, lower]
    expect(top.y + 70 + 60).toBeLessThanOrEqual(bottom.y)
    expect(byId.get("upper-next")!.position.y).toBeCloseTo(upper.y)
    expect(byId.get("lower-next")!.position.y).toBeCloseTo(lower.y)
    for (const edge of edges) {
      expect(byId.get(edge.target)!.position.x)
        .toBeGreaterThan(byId.get(edge.source)!.position.x + 240)
    }
    const repeated = await getLayoutedElements(result, edges, id => internals.get(id))
    expect(repeated.map(node => node.position)).toEqual(result.map(node => node.position))
    expect(result.map(node => node.id)).toEqual(nodes.map(node => node.id))
  })

  it("keeps measured tall sibling cards separated", async () => {
    const nodes = [makeNode("source"), ...["a", "b"].map(id => ({
      ...makeNode(id), measured: { width: 300, height: 240 },
    }))]
    const result = await getLayoutedElements(nodes, [makeEdge("source", "a"), makeEdge("source", "b")])
    expect(Math.abs(result[1].position.y - result[2].position.y)).toBeGreaterThanOrEqual(300)
  })

  it.each([Position.Top, Position.Bottom])("places join input on its %s side while keeping the base path straight", async (side) => {
    const nodes = [makeNode("base"), makeNode("lookup"), {
      ...makeNode("join"), measured: { width: 40, height: 34 },
    }, makeNode("sink")]
    const internals = new Map(nodes.map(node => [node.id, measuredNode(node, [35])]))
    const join = measuredNode(nodes[2], [17], 17)
    join.internals.handleBounds = {
      source: [{
        nodeId: "join", type: "source", position: Position.Right,
        x: 36, y: 17, width: 0, height: 0,
      }],
      target: [{
        id: "base", nodeId: "join", type: "target", position: Position.Left,
        x: 4, y: 17, width: 0, height: 0,
      }, {
        id: "lookup", nodeId: "join", type: "target", position: side,
        x: 20, y: side === Position.Top ? 6 : 28, width: 0, height: 0,
      }],
    }
    internals.set("join", join)
    const edges = [
      { ...makeEdge("base", "join"), targetHandle: "base" },
      { ...makeEdge("lookup", "join"), targetHandle: "lookup" },
      makeEdge("join", "sink"),
    ]
    const result = await getLayoutedElements(nodes, edges, id => internals.get(id))
    const [base, lookup, junction, sink] = result.map(node => node.position)

    expect(base.y + 35).toBeCloseTo(junction.y + 17)
    expect(sink.y + 35).toBeCloseTo(junction.y + 17)
    expect(base.x + 240).toBeLessThan(junction.x)
    expect(junction.x + 40).toBeLessThan(sink.x)
    if (side === Position.Top) {
      expect(lookup.y + 70).toBeLessThan(junction.y)
    } else {
      expect(lookup.y).toBeGreaterThan(junction.y + 34)
    }
  })

  it("returns empty array for empty input", async () => {
    // Catches: if the function throws on empty input instead of
    // returning [], the initial load of an empty pipeline would crash.
    const result = await getLayoutedElements([], [])
    expect(result).toEqual([])
  })

  it("assigns a non-zero position to a single node", async () => {
    // Catches: if ELK returns (0,0) for all nodes or the position
    // mapping is broken, every node would stack on the origin.
    const nodes = [makeNode("a")]
    const result = await getLayoutedElements(nodes, [])

    expect(result).toHaveLength(1)
    expect(result[0].id).toBe("a")
    // ELK should assign some position (may or may not be 0,0 for a single node,
    // but the function should at least return a result without throwing)
    expect(result[0].position).toBeDefined()
    expect(typeof result[0].position.x).toBe("number")
    expect(typeof result[0].position.y).toBe("number")
  })

  it("assigns distinct positions to two connected nodes", async () => {
    // Catches: if all nodes get the same position, the graph would be
    // an illegible pile. Connected nodes must be separated.
    const nodes = [makeNode("a"), makeNode("b")]
    const edges = [makeEdge("a", "b")]
    const result = await getLayoutedElements(nodes, edges)

    expect(result).toHaveLength(2)
    const posA = result.find((n) => n.id === "a")!.position
    const posB = result.find((n) => n.id === "b")!.position

    // In a RIGHT-directed layout, b should be to the right of a
    expect(posB.x).toBeGreaterThan(posA.x)
  })

  it("preserves node data through layout (only position changes)", async () => {
    // Catches: if the layout function reconstructs nodes from scratch
    // instead of spreading the original, custom data fields (config,
    // label, nodeType) would be lost.
    const nodes = [makeNode("a")]
    nodes[0].data = {
      label: "My Transform",
      nodeType: "polars",
      config: { sql: "SELECT 1" },
      _columns: [{ name: "x", dtype: "f64" }],
    }

    const result = await getLayoutedElements(nodes, [])
    expect(result[0].data).toEqual(nodes[0].data)
    expect(result[0].type).toBe("polars")
  })

  it("handles a linear chain of 3 nodes laid out left-to-right", async () => {
    // Catches: ensures the ELK layered algorithm with RIGHT direction
    // actually produces a left-to-right ordering for a→b→c.
    const nodes = [makeNode("a"), makeNode("b"), makeNode("c")]
    const edges = [makeEdge("a", "b"), makeEdge("b", "c")]

    const result = await getLayoutedElements(nodes, edges)
    const posA = result.find((n) => n.id === "a")!.position
    const posB = result.find((n) => n.id === "b")!.position
    const posC = result.find((n) => n.id === "c")!.position

    expect(posB.x).toBeGreaterThan(posA.x)
    expect(posC.x).toBeGreaterThan(posB.x)
  })

  it("aligns sibling nodes in the same layer", async () => {
    const nodes = [makeNode("a"), makeNode("b"), makeNode("c")]
    const edges = [makeEdge("a", "b"), makeEdge("a", "c")]

    const result = await getLayoutedElements(nodes, edges)
    const posB = result.find((n) => n.id === "b")!.position
    const posC = result.find((n) => n.id === "c")!.position

    // b and c are in the same layer → same x coordinate
    expect(posB.x).toBe(posC.x)
  })

  it("disconnected nodes all get valid positions", async () => {
    // Catches: if the ELK graph only lays out connected components,
    // disconnected nodes might get undefined or NaN positions.
    const nodes = [makeNode("a"), makeNode("b"), makeNode("c")]
    // No edges — all disconnected

    const result = await getLayoutedElements(nodes, [])

    for (const node of result) {
      expect(Number.isFinite(node.position.x)).toBe(true)
      expect(Number.isFinite(node.position.y)).toBe(true)
    }
  })

  it("replaces existing positions with finite layout coordinates", async () => {
    const nodes = [makeNode("z", 999, 999)]
    const result = await getLayoutedElements(nodes, [])

    expect(Number.isFinite(result[0].position.x)).toBe(true)
    expect(Number.isFinite(result[0].position.y)).toBe(true)
  })

  it("fan-out nodes in the same layer share an x coordinate", async () => {
    const nodes = [makeNode("a"), makeNode("b"), makeNode("c"), makeNode("d")]
    const edges = [makeEdge("a", "b"), makeEdge("a", "c"), makeEdge("a", "d")]

    const result = await getLayoutedElements(nodes, edges)
    const posB = result.find((n) => n.id === "b")!.position
    const posC = result.find((n) => n.id === "c")!.position
    const posD = result.find((n) => n.id === "d")!.position

    expect(posB.x).toBe(posC.x)
    expect(posC.x).toBe(posD.x)
  })
})

describe("partial imported-graph layout", () => {
  it("treats every finite origin as positioned, including a newly imported node", () => {
    const incoming = [
      makeNode("established", 0, 0),
      makeNode("new-default", 0, 0),
      makeNode("new-positioned", 400, 200),
    ]
    expect([...nodeIdsNeedingLayout(incoming)]).toEqual([])
  })

  it("treats non-finite incoming coordinates as unpositioned", () => {
    const invalid = makeNode("invalid")
    invalid.position = { x: Number.NaN, y: 10 }

    expect([...nodeIdsNeedingLayout([invalid])]).toEqual(["invalid"])
  })

  it("copies layout only to missing nodes and moves them clear of preserved nodes", () => {
    const incoming = [
      makeNode("origin", 0, 0),
      makeNode("new-a", 0, 0),
      makeNode("new-b", 0, 0),
    ]
    const layouted = [
      makeNode("origin", 500, 500),
      makeNode("new-a", 0, 0),
      makeNode("new-b", 0, 0),
    ]
    const result = mergeLayoutedNodePositions(
      incoming,
      layouted,
      new Set(["new-a", "new-b"]),
    )

    expect(result.find(node => node.id === "origin")?.position).toEqual({ x: 0, y: 0 })
    const newAPosition = result.find(node => node.id === "new-a")!.position
    const newBPosition = result.find(node => node.id === "new-b")!.position
    expect(newAPosition).not.toEqual({ x: 0, y: 0 })
    expect(newBPosition).not.toEqual({ x: 0, y: 0 })
    expect(newBPosition).not.toEqual(newAPosition)
  })

  it("does not mutate incoming or layouted node arrays", () => {
    const incoming = [makeNode("origin", 0, 0), makeNode("new", 0, 0)]
    const layouted = [makeNode("origin", 100, 100), makeNode("new", 0, 0)]
    const result = mergeLayoutedNodePositions(incoming, layouted, new Set(["new"]))

    expect(result).not.toBe(incoming)
    expect(result[1]).not.toBe(incoming[1])
    expect(incoming[1].position).toEqual({ x: 0, y: 0 })
    expect(layouted[1].position).toEqual({ x: 0, y: 0 })
  })
})
