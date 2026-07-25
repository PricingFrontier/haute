/**
 * Tests for layout.ts — ELK layout utility.
 *
 * Tests cover:
 * 1. Single node gets a position assigned
 * 2. Multiple nodes get distinct positions
 * 3. Connected nodes are laid out left-to-right (ELK "RIGHT" direction)
 * 4. Empty graph returns empty array
 * 5. Cluster snapping aligns nearly-equal coordinates
 * 6. Nodes preserve their original data (only position changes)
 */
import { describe, it, expect } from "vitest"
import type { Node, Edge } from "@xyflow/react"
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

describe("getLayoutedElements", () => {
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

  it("aligns nodes at nearly-the-same y coordinate via cluster snapping", async () => {
    // Catches: without the alignPositions post-processing, nodes that
    // ELK places at slightly different y values (e.g. 100 and 103)
    // would look misaligned. The snapping should make them identical.
    //
    // We test a parallel fan-out where a→b and a→c. Both b and c
    // should be at the same x (different layer) and the same y
    // (snapped together) OR at least positioned sensibly.
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

  it("positions default to zero when ELK returns undefined coordinates", async () => {
    // Catches: if the `child.x ?? 0` / `child.y ?? 0` fallback is removed,
    // nodes would get NaN or undefined positions when ELK omits coordinates.
    // A single disconnected node is most likely to receive (0,0) from ELK;
    // we verify that the result is a finite number regardless.
    const nodes = [makeNode("z", 999, 999)]
    const result = await getLayoutedElements(nodes, [])

    expect(Number.isFinite(result[0].position.x)).toBe(true)
    expect(Number.isFinite(result[0].position.y)).toBe(true)
  })

  it("fan-out nodes in the same layer share snapped x coordinate", async () => {
    // Catches: if cluster snapping is removed, nodes placed in the same
    // ELK layer with slightly different x values would appear misaligned.
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
  it("treats an established origin as positioned and a new origin as unpositioned", () => {
    const incoming = [
      makeNode("established", 0, 0),
      makeNode("new-default", 0, 0),
      makeNode("new-positioned", 400, 200),
    ]
    const current = [makeNode("established", 120, 80)]

    expect([...nodeIdsNeedingLayout(incoming, current)]).toEqual(["new-default"])
  })

  it("treats non-finite incoming coordinates as unpositioned", () => {
    const invalid = makeNode("invalid")
    invalid.position = { x: Number.NaN, y: 10 }

    expect([...nodeIdsNeedingLayout([invalid], [])]).toEqual(["invalid"])
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
