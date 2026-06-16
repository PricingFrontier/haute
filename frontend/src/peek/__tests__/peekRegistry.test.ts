/**
 * Tests for the explodability registry (node-explosion design §3.2).
 *
 * Submodel is the only v1 entry; edgeJoin (join-node marker) and banding
 * (fast-follow F1) must NOT be explodable in v1.
 */
import { describe, it, expect } from "vitest"
import type { Node } from "@xyflow/react"
import { isNodeExplodable, getPeekDescriptor, PEEK_REGISTRY } from "../peekRegistry"
import { NODE_TYPES } from "../../utils/nodeTypes"

function makeNode(nodeType: string, id = "n1"): Node {
  return {
    id,
    type: nodeType,
    position: { x: 0, y: 0 },
    data: { label: "X", nodeType, config: {} },
  }
}

describe("peekRegistry", () => {
  it("submodel nodes are explodable", () => {
    expect(isNodeExplodable(makeNode(NODE_TYPES.SUBMODEL, "submodel__x"))).toBe(true)
    expect(getPeekDescriptor(makeNode(NODE_TYPES.SUBMODEL, "submodel__x"))).toBeDefined()
  })

  it("edgeJoin (join-node marker) is never explodable", () => {
    expect(isNodeExplodable(makeNode(NODE_TYPES.EDGE_JOIN))).toBe(false)
    expect(getPeekDescriptor(makeNode(NODE_TYPES.EDGE_JOIN))).toBeUndefined()
  })

  it("banding is not explodable in v1 (fast-follow F1)", () => {
    expect(isNodeExplodable(makeNode(NODE_TYPES.BANDING))).toBe(false)
  })

  it("polars / other transform nodes are not explodable", () => {
    expect(isNodeExplodable(makeNode(NODE_TYPES.POLARS))).toBe(false)
    expect(isNodeExplodable(makeNode(NODE_TYPES.API_INPUT))).toBe(false)
  })

  it("a node with no nodeType is not explodable", () => {
    const node: Node = { id: "x", position: { x: 0, y: 0 }, data: {} }
    expect(isNodeExplodable(node)).toBe(false)
  })

  it("the registry only contains the submodel entry in v1", () => {
    expect(Object.keys(PEEK_REGISTRY)).toEqual([NODE_TYPES.SUBMODEL])
  })
})
