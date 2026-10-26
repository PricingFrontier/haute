import { describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import { applyPreviewResultColumnsToNodes } from "../usePipelineAPI"
import type { NodeResult } from "../../api/types"

describe("preview notes", () => {
  it("stores the previewed node's notes when the response carries its schema maps", () => {
    const note = "3 of the 10 rows scored had no 'exposure' and were scored as a full policy term."
    const nodes = [
      { id: "ms", position: { x: 0, y: 0 }, data: { label: "ms", nodeType: "modelScore" } },
      { id: "src", position: { x: 0, y: 0 }, data: { label: "src", nodeType: "dataSource" } },
    ] as Node[]
    const result = {
      status: "ok",
      columns: [{ name: "pred", dtype: "Float64" }],
      node_columns: { ms: [{ name: "pred", dtype: "Float64" }] },
      notes: [note],
    } as NodeResult

    const [scored, source] = applyPreviewResultColumnsToNodes(nodes, "ms", result, "live", 1)

    expect(scored.data._notes).toEqual([note])
    expect(source.data._notes).toBeUndefined()
  })
})
