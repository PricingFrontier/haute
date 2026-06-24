import { describe, it, expect } from "vitest"
import type { Node } from "@xyflow/react"
import { groupIntoWrapperBlockedReason } from "../groupIntoWrapper"

const node = (id: string, nodeType = "polars"): Node =>
  ({ id, position: { x: 0, y: 0 }, data: { nodeType } }) as unknown as Node

describe("groupIntoWrapperBlockedReason", () => {
  it("allows grouping 2+ plain nodes at the top level", () => {
    expect(
      groupIntoWrapperBlockedReason({
        nodes: [node("a"), node("b")],
        selectedIds: ["a", "b"],
        isInsideWrapper: false,
      }),
    ).toBeNull()
  })

  it("blocks while inside a wrapper (no nesting)", () => {
    expect(
      groupIntoWrapperBlockedReason({
        nodes: [node("a"), node("b")],
        selectedIds: ["a", "b"],
        isInsideWrapper: true,
      }),
    ).toMatch(/nested/)
  })

  it("blocks with fewer than 2 selected", () => {
    expect(
      groupIntoWrapperBlockedReason({
        nodes: [node("a")],
        selectedIds: ["a"],
        isInsideWrapper: false,
      }),
    ).toMatch(/2 nodes/)
  })

  it("blocks when the selection includes a wrapper", () => {
    expect(
      groupIntoWrapperBlockedReason({
        nodes: [node("a"), node("w", "submodel")],
        selectedIds: ["a", "w"],
        isInsideWrapper: false,
      }),
    ).toMatch(/another wrapper/)
  })
})
