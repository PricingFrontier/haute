/**
 * Unit tests for computePeekTraceLighting — what lights INSIDE an open Peek when
 * a traced data-path crosses the wrapper boundary (#3, Facet 1).
 */
import { describe, it, expect } from "vitest"
import { computePeekTraceLighting } from "../peekTraceLighting"
import { makeNode, makeEdge } from "../../test-utils/factories"

const WRAPPER = "submodel__W"

// A small internal graph with a fan-in:  X→c1→c2→c3→(out Y) ; Z→c1b→c2.
// Boundary ports: input port_in__c1__X (X), input port_in__c1b__Z (Z),
//                 output port_out__c3 (consumed by Y).
function fixture() {
  const peekNodes = [
    makeNode("c1"),
    makeNode("c1b"),
    makeNode("c2"),
    makeNode("c3"),
    makeNode("port_in__c1__X"),
    makeNode("port_in__c1b__Z"),
    makeNode("port_out__c3"),
  ]
  const peekEdges = [
    makeEdge("c1", "c2", { id: "e_c1_c2" }),
    makeEdge("c1b", "c2", { id: "e_c1b_c2" }),
    makeEdge("c2", "c3", { id: "e_c2_c3" }),
    makeEdge("port_in__c1__X", "c1", { id: "e_pin_c1" }),
    makeEdge("port_in__c1b__Z", "c1b", { id: "e_pin_c1b" }),
    makeEdge("c3", "port_out__c3", { id: "e_pout_c3" }),
  ]
  const parentEdges = [
    makeEdge("X", WRAPPER, { id: "pe_X", targetHandle: "in__c1" }),
    makeEdge("Z", WRAPPER, { id: "pe_Z", targetHandle: "in__c1b" }),
    makeEdge(WRAPPER, "Y", { id: "pe_Y", sourceHandle: "out__c3" }),
  ]
  return { peekNodes, peekEdges, parentEdges }
}

function call(over: Partial<Parameters<typeof computePeekTraceLighting>[0]> = {}) {
  const { peekNodes, peekEdges, parentEdges } = fixture()
  return computePeekTraceLighting({
    peekNodes,
    peekEdges,
    parentEdges,
    wrapperNodeId: WRAPPER,
    hoveredNodeId: null,
    peekHoverId: null,
    ...over,
  })
}

describe("computePeekTraceLighting", () => {
  it("is inactive with no hover", () => {
    expect(call().active).toBe(false)
  })

  it("is inactive when hovering an unrelated external node", () => {
    expect(call({ hoveredNodeId: "Q" }).active).toBe(false)
  })

  it("hovering the wrapper card lights the whole peek graph", () => {
    const lit = call({ hoveredNodeId: WRAPPER })
    expect(lit.active).toBe(true)
    expect(lit.litNodeIds.size).toBe(7)
    expect(lit.litEdgeIds.size).toBe(6)
  })

  it("hovering an INPUT source lights its DOWNSTREAM cone only", () => {
    // X feeds c1; the X-cone is c1→c2→c3→out, NOT the sibling input c1b/Z.
    const lit = call({ hoveredNodeId: "X" })
    expect(lit.active).toBe(true)
    expect([...lit.litNodeIds].sort()).toEqual(
      ["c1", "c2", "c3", "port_in__c1__X", "port_out__c3"].sort(),
    )
    expect(lit.litNodeIds.has("c1b")).toBe(false)
    expect(lit.litNodeIds.has("port_in__c1b__Z")).toBe(false)
  })

  it("hovering an OUTPUT consumer lights its UPSTREAM cone", () => {
    // Y consumes out(c3); upstream of c3 is everything that feeds it (both inputs).
    const lit = call({ hoveredNodeId: "Y" })
    expect(lit.active).toBe(true)
    expect([...lit.litNodeIds].sort()).toEqual(
      ["c1", "c1b", "c2", "c3", "port_in__c1__X", "port_in__c1b__Z", "port_out__c3"].sort(),
    )
  })

  it("internal self-hover lights 1-hop neighbours and takes priority", () => {
    // Hover c2 inside the peek: light c1, c1b, c3 + the incident edges only.
    const lit = call({ peekHoverId: "c2", hoveredNodeId: WRAPPER })
    expect([...lit.litNodeIds].sort()).toEqual(["c1", "c1b", "c2", "c3"].sort())
    expect([...lit.litEdgeIds].sort()).toEqual(["e_c1_c2", "e_c1b_c2", "e_c2_c3"].sort())
    // The wrapper-hover "light everything" path is NOT taken (port nodes dark).
    expect(lit.litNodeIds.has("port_in__c1__X")).toBe(false)
  })

  it("falls back to parent hover when peekHoverId is not a peek node", () => {
    const lit = call({ peekHoverId: "ghost", hoveredNodeId: WRAPPER })
    expect(lit.litNodeIds.size).toBe(7) // wrapper-hover path taken
  })

  it("lights only the hovered source's input port when two sources feed the SAME child", () => {
    // submodelBoundary makes one input port PER LINK (port_in__<child>__<src>), so
    // S1->c1 and S2->c1 are two ports. Hovering S1 must light port_in__c1__S1 only
    // — a collapse-by-child bug would light both and pass the sibling-child fixture.
    const peekNodes = [
      makeNode("c1"),
      makeNode("c2"),
      makeNode("port_in__c1__S1"),
      makeNode("port_in__c1__S2"),
    ]
    const peekEdges = [
      makeEdge("port_in__c1__S1", "c1", { id: "e_s1" }),
      makeEdge("port_in__c1__S2", "c1", { id: "e_s2" }),
      makeEdge("c1", "c2", { id: "e_c1_c2" }),
    ]
    const parentEdges = [
      makeEdge("S1", WRAPPER, { id: "pe_s1", targetHandle: "in__c1" }),
      makeEdge("S2", WRAPPER, { id: "pe_s2", targetHandle: "in__c1" }),
    ]
    const lit = computePeekTraceLighting({
      peekNodes,
      peekEdges,
      parentEdges,
      wrapperNodeId: WRAPPER,
      hoveredNodeId: "S1",
      peekHoverId: null,
    })
    expect(lit.litNodeIds.has("port_in__c1__S1")).toBe(true)
    expect(lit.litNodeIds.has("port_in__c1__S2")).toBe(false)
    expect(lit.litNodeIds.has("c1")).toBe(true)
    expect(lit.litNodeIds.has("c2")).toBe(true)
  })

  it("unions BOTH cones when one external node both feeds an input and consumes an output", () => {
    // M feeds c1b (input) AND consumes out(c3). The backward (output) cone reaches
    // c1 + port_in__c1__X ONLY after the forward (input) cone already walked the
    // shared spine c2->c3 — so the per-walk `visited` reset is load-bearing. A
    // shared-visited regression would silently drop c1 / port_in__c1__X.
    const peekNodes = [
      makeNode("c1"),
      makeNode("c1b"),
      makeNode("c2"),
      makeNode("c3"),
      makeNode("port_in__c1__X"),
      makeNode("port_in__c1b__M"),
      makeNode("port_out__c3"),
    ]
    const peekEdges = [
      makeEdge("c1", "c2", { id: "e_c1_c2" }),
      makeEdge("c1b", "c2", { id: "e_c1b_c2" }),
      makeEdge("c2", "c3", { id: "e_c2_c3" }),
      makeEdge("port_in__c1__X", "c1", { id: "e_pin_c1" }),
      makeEdge("port_in__c1b__M", "c1b", { id: "e_pin_c1b" }),
      makeEdge("c3", "port_out__c3", { id: "e_pout_c3" }),
    ]
    const parentEdges = [
      makeEdge("X", WRAPPER, { id: "pe_X", targetHandle: "in__c1" }),
      makeEdge("M", WRAPPER, { id: "pe_M_in", targetHandle: "in__c1b" }),
      makeEdge(WRAPPER, "M", { id: "pe_M_out", sourceHandle: "out__c3" }),
    ]
    const lit = computePeekTraceLighting({
      peekNodes,
      peekEdges,
      parentEdges,
      wrapperNodeId: WRAPPER,
      hoveredNodeId: "M",
      peekHoverId: null,
    })
    expect([...lit.litNodeIds].sort()).toEqual(
      ["c1", "c1b", "c2", "c3", "port_in__c1__X", "port_in__c1b__M", "port_out__c3"].sort(),
    )
    // Reachable only via the backward cone after the forward cone took the spine:
    expect(lit.litNodeIds.has("c1")).toBe(true)
    expect(lit.litNodeIds.has("port_in__c1__X")).toBe(true)
  })

  it("a port id passed as peekHoverId is a 1-hop self-hover (split-contract guard)", () => {
    // The pure function does not special-case ports; SubmodelPeekBody nulls
    // peekHoverId for ports. Pin the function's behaviour so the split contract
    // can't drift unnoticed: self-hover takes priority over the parent (Y) cone.
    const lit = call({ peekHoverId: "port_in__c1__X", hoveredNodeId: "Y" })
    expect(lit.active).toBe(true)
    expect(lit.litNodeIds.has("port_in__c1__X")).toBe(true)
    expect(lit.litNodeIds.has("c1")).toBe(true) // 1-hop neighbour
    expect(lit.litNodeIds.has("c3")).toBe(false) // the Y output cone is NOT applied
  })
})
