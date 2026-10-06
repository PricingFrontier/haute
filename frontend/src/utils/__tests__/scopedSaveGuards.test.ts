import { describe, expect, it } from "vitest"
import { otherScopedEditedNodes, scopedSaveResponseFenceError } from "../scopedSaveGuards"

describe("scoped save guards", () => {
  it("lists every other edited node, sorted, excluding the save target", () => {
    expect(otherScopedEditedNodes(new Set(["b@1", "a@1", "target@1"]), "target@1")).toEqual([
      "a@1",
      "b@1",
    ])
    expect(otherScopedEditedNodes(new Set(["target@1"]), "target@1")).toEqual([])
    expect(otherScopedEditedNodes(new Set(), "target@1")).toEqual([])
  })

  it("discards responses when the document identity moved during the flight", () => {
    const base = {
      requestSourceFile: "main.py",
      requestRevision: "rev-1",
      currentSourceFile: "main.py",
      currentRevision: "rev-1" as string | null,
    }
    expect(scopedSaveResponseFenceError(base)).toBeNull()
    expect(
      scopedSaveResponseFenceError({ ...base, currentRevision: "rev-2" }),
    ).toMatch("The document changed while saving")
    expect(
      scopedSaveResponseFenceError({ ...base, currentSourceFile: "other.py" }),
    ).toMatch("The document changed while saving")
    expect(scopedSaveResponseFenceError({ ...base, currentRevision: null })).toMatch(
      "The document changed while saving",
    )
  })
})
