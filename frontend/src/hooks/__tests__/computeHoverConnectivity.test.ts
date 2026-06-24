/**
 * Unit tests for computeHoverConnectivity — the hover-trace traversal that lights
 * the full data PATH through the hovered node: all upstream ancestors + all
 * downstream descendants, transitively (and through wrappers, which are ordinary
 * nodes to this walk). Side-branches off the path must stay dim.
 */
import { describe, it, expect } from "vitest"
import { buildEdgeAdjacency, computeHoverConnectivity } from "../useTracing"
import { makeEdge } from "../../test-utils/factories"

describe("computeHoverConnectivity", () => {
  it("lights the whole linear chain through the hovered node", () => {
    // A → B → C → D ; hovering B lights upstream A and downstream C, D.
    const adj = buildEdgeAdjacency([makeEdge("A", "B"), makeEdge("B", "C"), makeEdge("C", "D")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("B", adj)
    expect([...nodeIds].sort()).toEqual(["A", "B", "C", "D"])
    expect([...edgeIds].sort()).toEqual(["e_A_B", "e_B_C", "e_C_D"])
  })

  it("returns just the seed for a node with no edges", () => {
    const adj = buildEdgeAdjacency([makeEdge("a", "b")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("lonely", adj)
    expect([...nodeIds]).toEqual(["lonely"])
    expect(edgeIds.size).toBe(0)
  })

  it("excludes a sibling branch that only shares a DESCENDANT (the other input to a join)", () => {
    // A → J ← X ; J → K. Hovering A lights A, J, K (downstream) but NOT X — X is a
    // co-parent of J, not on A's up/downstream path. The X→J edge stays dim.
    const adj = buildEdgeAdjacency([makeEdge("A", "J"), makeEdge("X", "J"), makeEdge("J", "K")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("A", adj)
    expect([...nodeIds].sort()).toEqual(["A", "J", "K"])
    expect(nodeIds.has("X")).toBe(false)
    expect(edgeIds.has("e_X_J")).toBe(false)
    expect([...edgeIds].sort()).toEqual(["e_A_J", "e_J_K"])
  })

  it("excludes a sibling branch that only shares an ANCESTOR (a fan-out)", () => {
    // A → B, A → C. Hovering B lights B + upstream A, but NOT the sibling C.
    const adj = buildEdgeAdjacency([makeEdge("A", "B"), makeEdge("A", "C")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("B", adj)
    expect([...nodeIds].sort()).toEqual(["A", "B"])
    expect(nodeIds.has("C")).toBe(false)
    expect([...edgeIds]).toEqual(["e_A_B"])
  })

  it("flows through both branches of a diamond from the top", () => {
    // A → B → D, A → C → D. Hovering A lights everything downstream.
    const adj = buildEdgeAdjacency([
      makeEdge("A", "B"),
      makeEdge("A", "C"),
      makeEdge("B", "D"),
      makeEdge("C", "D"),
    ])
    const { nodeIds } = computeHoverConnectivity("A", adj)
    expect([...nodeIds].sort()).toEqual(["A", "B", "C", "D"])
  })

  it("flows transitively through a wrapper (an ordinary node to this walk)", () => {
    // A → W → B → C. Wrappers aren't special here — directed walk reaches C.
    const adj = buildEdgeAdjacency([makeEdge("A", "W"), makeEdge("W", "B"), makeEdge("B", "C")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("A", adj)
    expect([...nodeIds].sort()).toEqual(["A", "B", "C", "W"])
    expect([...edgeIds].sort()).toEqual(["e_A_W", "e_B_C", "e_W_B"])
  })
})
