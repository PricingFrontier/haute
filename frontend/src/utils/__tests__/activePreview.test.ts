import { describe, expect, it } from "vitest"
import type { PreviewData } from "../../panels/DataPreview"
import { previewForActiveNode } from "../activePreview"

function makePreview(nodeId: string): PreviewData {
  return {
    nodeId,
    nodeLabel: nodeId,
    status: "ok",
    row_count: 1,
    column_count: 1,
    columns: [{ name: "value", dtype: "f64" }],
    preview: [{ value: 1 }],
    error: null,
  }
}

describe("previewForActiveNode", () => {
  it("returns matching preview data for the active node", () => {
    const preview = makePreview("active")

    expect(previewForActiveNode(preview, "active")).toBe(preview)
  })

  it("hides stale preview data from the previously selected node", () => {
    const optimiserPreview = makePreview("online_optimiser")

    expect(previewForActiveNode(optimiserPreview, "batch_quotes")).toBeNull()
  })

  it("returns null when there is no active node or preview", () => {
    expect(previewForActiveNode(makePreview("node"), null)).toBeNull()
    expect(previewForActiveNode(null, "node")).toBeNull()
  })
})
