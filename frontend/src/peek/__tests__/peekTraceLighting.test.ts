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
})
