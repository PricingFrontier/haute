import { describe, expect, it } from "vitest"
import { otherScopedEditedNodes } from "../scopedSaveGuards"

describe("scoped save guards", () => {
  it("lists every other edited node, sorted, excluding the save target", () => {
    expect(otherScopedEditedNodes(new Set(["b@1", "a@1", "target@1"]), "target@1")).toEqual([
      "a@1",
      "b@1",
    ])
    expect(otherScopedEditedNodes(new Set(["target@1"]), "target@1")).toEqual([])
    expect(otherScopedEditedNodes(new Set(), "target@1")).toEqual([])
  })
})
