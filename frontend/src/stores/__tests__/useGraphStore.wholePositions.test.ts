/**
 * Node positions are whole pixels (READ-11, specs/frontend-canvas): a drag ending at
 * fractional coordinates, a node placed by a layout and a node added are stored rounded, so a
 * save writes what the canvas compares and an edit that moves no node leaves every position.
 */
import { beforeEach, describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import useGraphStore, { wholePositions } from "../useGraphStore"

const node = (id: string, x: number, y: number, data: Record<string, unknown> = {}): Node =>
  ({ id, position: { x, y }, data, type: "default" }) as Node

describe("whole-pixel positions", () => {
  beforeEach(() => {
    useGraphStore.setState({ nodes: [], edges: [] })
  })

  it("rounds a drag that ends at fractional coordinates", () => {
    useGraphStore.getState().setNodes([node("a", 0, 0)])
    useGraphStore.getState().setNodesRaw((nodes) => nodes.map((n) => ({ ...n, position: { x: 120.4, y: -355.61225658228415 } })))
    expect(useGraphStore.getState().nodes[0].position).toEqual({ x: 120, y: -356 })
  })

  it("rounds a node placed by setNodes, half up as the save does", () => {
    useGraphStore.getState().setNodes([node("a", 10.5, 2.49)])
    expect(useGraphStore.getState().nodes[0].position).toEqual({ x: 11, y: 2 })
  })

  it("leaves whole positions, and the nodes holding them, as they are when only data changes", () => {
    useGraphStore.getState().setNodes([node("a", 40, 80), node("b", 300, 80)])
    const before = useGraphStore.getState().nodes
    useGraphStore.getState().setNodes((nodes) => nodes.map((n) => (n.id === "a" ? { ...n, data: { label: "edited" } } : n)))
    const after = useGraphStore.getState().nodes
    expect(after.map((n) => n.position)).toEqual(before.map((n) => n.position))
    expect(after[1]).toBe(before[1])
  })

  it("returns the same array when every position is whole", () => {
    const nodes = [node("a", 1, 2)]
    expect(wholePositions(nodes)).toBe(nodes)
  })
})
