import { describe, expect, it } from "vitest"
import { optimiserPanesFor, resolveOptimiserPane } from "../optimiserPanes"

describe("optimiserPanesFor", () => {
  it("offers Factors only in ratebook mode", () => {
    expect(optimiserPanesFor("ratebook").map((pane) => pane.label)).toEqual([
      "Data", "Factors", "Constraints", "Solve", "Export",
    ])
    expect(optimiserPanesFor("online").map((pane) => pane.label)).toEqual([
      "Data", "Constraints", "Solve", "Export",
    ])
  })
})

describe("resolveOptimiserPane", () => {
  it("keeps a remembered pane the mode has", () => {
    expect(resolveOptimiserPane("online", "solve")).toBe("solve")
    expect(resolveOptimiserPane("ratebook", "factors")).toBe("factors")
  })

  it("opens Data when nothing is remembered or the mode lacks the pane", () => {
    expect(resolveOptimiserPane("ratebook", undefined)).toBe("data")
    expect(resolveOptimiserPane("online", "factors")).toBe("data")
  })
})
