/** Review-only regression probes. Copy to frontend/src/hooks/__tests__/ to run. */
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import useBandingStats from "../../panels/editors/banding/useBandingStats"
import useRatingLevels from "../../panels/editors/rating/useRatingLevels"

const mocks = vi.hoisted(() => ({ stats: vi.fn(), levels: vi.fn() }))
vi.mock("../../api/client", () => ({
  getBandingStats: mocks.stats,
  getRatingLevels: mocks.levels,
}))
vi.mock("../useNodeDataCache", () => ({
  default: () => ({ availability: "current", dataVersion: "same-input-generation" }),
}))
vi.mock("../../stores/useDocumentStatusStore", () => ({
  captureDocumentExecutionFence: () => ({}),
  isDocumentExecutionFenceCurrent: () => true,
}))
vi.mock("../../stores/useSettingsStore", () => ({
  default: (selector: (value: unknown) => unknown) => selector({ activeSource: "live" }),
}))

const node = {
  id: "consumer",
  data: { label: "consumer", nodeType: "banding", config: {} },
}
const common = { node, allNodes: [node], edges: [] }
const factor = {
  banding: "breakpoints" as const,
  column: "premium",
  outputColumn: "band",
  rules: [{ boundary: "50", label: "low" }, { boundary: "", label: "high" }],
  rightClosed: true,
}
const oldStats = {
  status: "ok",
  data_version: "same-input-generation",
  rule_counts: [750, 250],
}

describe("PR 227 analysis identity regressions", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.stats.mockReset()
    mocks.levels.mockReset()
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("does not attribute old rule counts to new boundaries on the same input", async () => {
    mocks.stats.mockResolvedValue(oldStats)
    const hook = renderHook(({ value }) => useBandingStats({ ...common, factor: value }), {
      initialProps: { value: factor },
    })
    await act(async () => { vi.advanceTimersByTime(251) })
    expect(hook.result.current.basis).toBe("all")
    hook.rerender({ value: { ...factor, rules: [{ boundary: "90", label: "low" }, factor.rules[1]] } })
    expect(hook.result.current.stats).toBeNull()
  })

  it("rejects an old response arriving during the next debounce window", async () => {
    let resolveOld!: (value: unknown) => void
    mocks.stats.mockImplementation(() => new Promise(resolve => { resolveOld = resolve }))
    const hook = renderHook(({ value }) => useBandingStats({ ...common, factor: value }), {
      initialProps: { value: factor },
    })
    await act(async () => { vi.advanceTimersByTime(251) })
    hook.rerender({ value: { ...factor, rules: [{ boundary: "90", label: "low" }, factor.rules[1]] } })
    await act(async () => { resolveOld(oldStats) })
    expect(hook.result.current.stats).toBeNull()
  })

  it("does not label old rating columns as the new whole-data answer", async () => {
    mocks.levels.mockResolvedValue({
      status: "ok", data_version: "same-input-generation", total_rows: 10,
      columns: [{ column: "region", values: [{ value: "north", count: 10 }] }],
    })
    const hook = renderHook(({ columns }) => useRatingLevels({ ...common, columns }), {
      initialProps: { columns: ["region"] },
    })
    await act(async () => { vi.advanceTimersByTime(251) })
    expect(hook.result.current.levels).toEqual({ region: ["north"] })
    hook.rerender({ columns: ["occupation"] })
    expect(hook.result.current.basis).not.toBe("all")
  })
})
