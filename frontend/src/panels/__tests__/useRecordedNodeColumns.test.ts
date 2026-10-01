import { describe, expect, it } from "vitest"
import { renderHook } from "@testing-library/react"

import type { HauteNodeData } from "../../types/node"
import { useRecordedNodeColumns } from "../useRecordedNodeColumns"

const SCORED = [
  { name: "policy_id", dtype: "String" },
  { name: "prediction", dtype: "Float64" },
]

/** A Model Score node whose last preview under "live" recorded its scored frame. */
function scored(overrides: Partial<HauteNodeData> = {}): HauteNodeData {
  return {
    label: "Score",
    nodeType: "modelScore",
    config: { output_column: "prediction", code: "" },
    _availableColumns: SCORED,
    _columnsSource: "live",
    ...overrides,
  }
}

/** The same node after an edit: the config changed and the stash was cleared. */
function edited(config: Record<string, unknown>): HauteNodeData {
  return { label: "Score", nodeType: "modelScore", config }
}

function render(data: HauteNodeData, activeSource = "live") {
  return renderHook(
    ({ nodeData, source }: { nodeData: HauteNodeData; source: string }) => useRecordedNodeColumns(nodeData, source),
    { initialProps: { nodeData: data, source: activeSource } },
  )
}

describe("useRecordedNodeColumns", () => {
  it("returns the columns the node's last preview recorded under the active source", () => {
    expect(render(scored()).result.current).toEqual(SCORED)
  })

  it("ignores columns recorded under another source", () => {
    expect(render(scored({ _columnsSource: "batch" })).result.current).toBeUndefined()
    expect(render(scored({ _columnsSource: undefined })).result.current).toBeUndefined()
  })

  it("is undefined before any preview has recorded the node's columns", () => {
    expect(render(scored({ _availableColumns: undefined })).result.current).toBeUndefined()
  })

  it("keeps the recorded columns while only the code or steps change", () => {
    const hook = render(scored())

    hook.rerender({ nodeData: edited({ output_column: "prediction", code: "df = df.with_columns(pl.col(\"pr" }), source: "live" })
    expect(hook.result.current).toEqual(SCORED)

    hook.rerender({
      nodeData: edited({ output_column: "prediction", code: "", steps: [], _steps_error: "Step 1: needs a formula." }),
      source: "live",
    })
    expect(hook.result.current).toEqual(SCORED)
  })

  it("drops the kept columns when any other setting changes", () => {
    const hook = render(scored())

    hook.rerender({ nodeData: edited({ output_column: "pred_freq", code: "" }), source: "live" })
    expect(hook.result.current).toBeUndefined()

    // Changing it back does not bring back columns no preview has recorded since.
    hook.rerender({ nodeData: edited({ output_column: "prediction", code: "" }), source: "live" })
    expect(hook.result.current).toBeUndefined()
  })

  it("drops the kept columns when the active source switches", () => {
    const hook = render(scored())

    // The switch lands before the stale stash is invalidated, and after.
    hook.rerender({ nodeData: scored(), source: "batch" })
    expect(hook.result.current).toBeUndefined()
    hook.rerender({ nodeData: edited({ output_column: "prediction", code: "" }), source: "batch" })
    expect(hook.result.current).toBeUndefined()

    // Switching back does not bring them back either.
    hook.rerender({ nodeData: edited({ output_column: "prediction", code: "" }), source: "live" })
    expect(hook.result.current).toBeUndefined()
  })

  it("replaces the kept columns when a refreshed preview records different ones", () => {
    const renamed = [
      { name: "policy_id", dtype: "String" },
      { name: "premium", dtype: "Float64" },
    ]
    const hook = render(scored())
    const code = "df = df.rename({\"prediction\": \"premium\"})"

    hook.rerender({ nodeData: scored({ config: { output_column: "prediction", code }, _availableColumns: renamed }), source: "live" })
    expect(hook.result.current).toEqual(renamed)
    hook.rerender({ nodeData: edited({ output_column: "prediction", code: `${code}\n` }), source: "live" })
    expect(hook.result.current).toEqual(renamed)
  })

  it("keeps one identity while the recorded content is unchanged", () => {
    const hook = render(scored())
    const first = hook.result.current

    hook.rerender({ nodeData: scored({ _availableColumns: SCORED.map((column) => ({ ...column })) }), source: "live" })
    expect(hook.result.current).toBe(first)
    hook.rerender({ nodeData: edited({ output_column: "prediction", code: "df = df" }), source: "live" })
    expect(hook.result.current).toBe(first)
  })
})
