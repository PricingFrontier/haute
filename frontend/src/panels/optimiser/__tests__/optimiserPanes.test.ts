import { describe, expect, it } from "vitest"
import { OPTIMISER_PANES, resolveOptimiserPane } from "../optimiserPanes"

describe("OPTIMISER_PANES", () => {
  it("lists the panes in tab order", () => {
    expect(OPTIMISER_PANES.map((pane) => pane.label)).toEqual([
      "Data", "Factors", "Constraints", "Solve", "Export",
    ])
  })
})

describe("resolveOptimiserPane", () => {
  it("keeps a remembered pane", () => {
    expect(resolveOptimiserPane("solve")).toBe("solve")
    expect(resolveOptimiserPane("factors")).toBe("factors")
  })

  it("opens Data when nothing is remembered", () => {
    expect(resolveOptimiserPane(undefined)).toBe("data")
  })
})
