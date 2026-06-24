/**
 * Unit tests for computeHoverConnectivity — the hover-trace traversal that makes
 * wrappers TRANSPARENT (the glow flows THROUGH a collapsed wrapper to the far
 * side) while keeping ordinary nodes at one hop. With no wrapper on the path it
 * must reduce to exactly the old 1-hop behaviour.
 */
import { describe, it, expect } from "vitest"
import { buildEdgeAdjacency, computeHoverConnectivity } from "../useTracing"
import { makeEdge } from "../../test-utils/factories"

const NO_WRAPPERS = new Set<string>()

describe("computeHoverConnectivity", () => {
  it("reduces to 1-hop when no wrapper is on the path", () => {
    // n0 - n1 - n2 ; hovering n1 lights only its direct neighbours, not n0..n2 chain.
    const adj = buildEdgeAdjacency([makeEdge("n0", "n1"), makeEdge("n1", "n2"), makeEdge("n2", "n3")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("n1", adj, NO_WRAPPERS)
    expect([...nodeIds].sort()).toEqual(["n0", "n1", "n2"])
    expect([...edgeIds].sort()).toEqual(["e_n0_n1", "e_n1_n2"])
    // n3 is two hops away (through normal node n2) — must NOT light.
    expect(nodeIds.has("n3")).toBe(false)
  })

  it("returns just the seed for a node with no edges", () => {
    const adj = buildEdgeAdjacency([makeEdge("a", "b")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("lonely", adj, NO_WRAPPERS)
    expect([...nodeIds]).toEqual(["lonely"])
    expect(edgeIds.size).toBe(0)
  })

  it("flows THROUGH a wrapper to the far side when hovering an external node", () => {
    // A → W → B, W is a wrapper. Hovering A must reach B across the wrapper.
    const adj = buildEdgeAdjacency([makeEdge("A", "W"), makeEdge("W", "B")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("A", adj, new Set(["W"]))
    expect([...nodeIds].sort()).toEqual(["A", "B", "W"])
    expect([...edgeIds].sort()).toEqual(["e_A_W", "e_W_B"])
  })

  it("flows through a wrapper hovered from the far side too (undirected)", () => {
    const adj = buildEdgeAdjacency([makeEdge("A", "W"), makeEdge("W", "B")])
    const { nodeIds } = computeHoverConnectivity("B", adj, new Set(["W"]))
    expect([...nodeIds].sort()).toEqual(["A", "B", "W"])
  })

  it("hovering the wrapper itself lights all its external neighbours", () => {
    const adj = buildEdgeAdjacency([makeEdge("A", "W"), makeEdge("W", "B"), makeEdge("W", "C")])
    const { nodeIds } = computeHoverConnectivity("W", adj, new Set(["W"]))
    expect([...nodeIds].sort()).toEqual(["A", "B", "C", "W"])
  })

  it("does NOT expand past a normal node beyond the wrapper", () => {
    // A → W → B → C. Hover A: W expands to B; B is normal so the path stops — C excluded.
    const adj = buildEdgeAdjacency([makeEdge("A", "W"), makeEdge("W", "B"), makeEdge("B", "C")])
    const { nodeIds } = computeHoverConnectivity("A", adj, new Set(["W"]))
    expect([...nodeIds].sort()).toEqual(["A", "B", "W"])
    expect(nodeIds.has("C")).toBe(false)
  })

  it("flows through chained wrappers transitively", () => {
    // A → W1 → W2 → B, both wrappers. Hover A reaches B across both.
    const adj = buildEdgeAdjacency([makeEdge("A", "W1"), makeEdge("W1", "W2"), makeEdge("W2", "B")])
    const { nodeIds, edgeIds } = computeHoverConnectivity("A", adj, new Set(["W1", "W2"]))
    expect([...nodeIds].sort()).toEqual(["A", "B", "W1", "W2"])
    expect([...edgeIds].sort()).toEqual(["e_A_W1", "e_W1_W2", "e_W2_B"])
  })
})
