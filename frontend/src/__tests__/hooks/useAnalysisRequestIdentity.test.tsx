import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import useBandingStats from "../../panels/editors/banding/useBandingStats"
import useRatingLevels from "../../panels/editors/rating/useRatingLevels"
import {
  WHOLE_DATA_DEBOUNCE_MS,
  wholeDataStatus,
  wholeDataStatusText,
  type WholeDataStatusInput,
} from "../../panels/editors/shared/useWholeDataAnswer"
import useGraphStore from "../../stores/useGraphStore"

const mocks = vi.hoisted(() => ({
  stats: vi.fn(),
  levels: vi.fn(),
  availability: "current" as "current" | "stale" | "missing",
  dataVersion: "generation-1" as string | null,
  activeSource: "live",
}))

vi.mock("../../api/client", () => ({
  getBandingStats: mocks.stats,
  getRatingLevels: mocks.levels,
}))
vi.mock("../../hooks/useNodeDataCache", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../hooks/useNodeDataCache")>()),
  default: () => ({ availability: mocks.availability, dataVersion: mocks.dataVersion, problem: null }),
}))
vi.mock("../../stores/useDocumentStatusStore", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../stores/useDocumentStatusStore")>()),
  captureDocumentExecutionFence: () => ({}),
  isDocumentExecutionFenceCurrent: () => true,
}))
vi.mock("../../stores/useSettingsStore", () => ({
  default: Object.assign(
    (selector: (value: { activeSource: string }) => unknown) => selector({ activeSource: mocks.activeSource }),
    { getState: () => ({ activeSource: mocks.activeSource }) },
  ),
}))

const node = {
  id: "consumer",
  position: { x: 0, y: 0 },
  data: { label: "consumer", description: "", nodeType: "banding", config: {} },
}
const otherNode = { ...node, id: "other-consumer" }
const common = { node, allNodes: [node], edges: [] }
const factor = {
  banding: "breakpoints" as const,
  column: "premium",
  output_column: "band",
  rules: [{ boundary: "50", label: "low" }, { boundary: "", label: "high" }],
  right_closed: true,
}
const stats = { status: "ok" as const, data_version: "generation-1", rule_counts: [750, 250] }
const levels = {
  status: "ok" as const,
  data_version: "generation-1",
  total_rows: 10,
  columns: [{ column: "region", values: [{ value: "north", count: 10 }] }],
}

async function advance(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
    await Promise.resolve()
  })
}

describe("analysis request identity", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.stats.mockReset()
    mocks.levels.mockReset()
    mocks.availability = "current"
    mocks.dataVersion = "generation-1"
    mocks.activeSource = "live"
    // The questions run the graph store's document.
    useGraphStore.getState().loadGraphSnapshot({ nodes: [node, otherNode], edges: [], preamble: "", submodels: {} })
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    useGraphStore.getState().resetForTests()
  })

  it("keeps resolved banding statistics, no longer current, when only the rules change", async () => {
    mocks.stats.mockResolvedValue(stats)
    const hook = renderHook(({ value }) => useBandingStats({ ...common, factor: value }), {
      initialProps: { value: factor },
    })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    expect(hook.result.current.stats).toEqual(stats)
    expect(hook.result.current.current).toBe(true)

    // The column's total, values and histogram still describe the data; its
    // rule counts answer the old rules, which `current` says.
    hook.rerender({ value: { ...factor, rules: [{ boundary: "90", label: "low" }, factor.rules[1]] } })
    expect(hook.result.current.stats).toEqual(stats)
    expect(hook.result.current.current).toBe(false)
    expect(hook.result.current.error).toBeNull()
    expect(hook.result.current.loading).toBe(false)
  })

  it("invalidates resolved banding statistics when the column changes", async () => {
    mocks.stats.mockResolvedValue(stats)
    const hook = renderHook(({ value }) => useBandingStats({ ...common, factor: value }), {
      initialProps: { value: factor },
    })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    expect(hook.result.current.stats).toEqual(stats)

    hook.rerender({ value: { ...factor, column: "another_column" } })
    expect(hook.result.current.stats).toBeNull()
    expect(hook.result.current.current).toBe(false)
  })

  it("does not repeat a banding request when only its output column changes", async () => {
    mocks.stats.mockResolvedValue(stats)
    const hook = renderHook(({ value }) => useBandingStats({ ...common, factor: value }), {
      initialProps: { value: factor },
    })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    hook.rerender({ value: { ...factor, output_column: "renamed_band" } })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    expect(mocks.stats).toHaveBeenCalledTimes(1)
    expect(hook.result.current.stats).toEqual(stats)
  })

  it("aborts and ignores an old banding reply during the successor debounce interval", async () => {
    let resolveOld!: (value: typeof stats) => void
    mocks.stats.mockImplementation(
      () => new Promise<typeof stats>((resolve) => { resolveOld = resolve }),
    )
    const hook = renderHook(({ value }) => useBandingStats({ ...common, factor: value }), {
      initialProps: { value: factor },
    })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    const oldSignal = mocks.stats.mock.calls[0][0].signal as AbortSignal

    hook.rerender({ value: { ...factor, rules: [{ boundary: "90", label: "low" }, factor.rules[1]] } })
    expect(oldSignal.aborted).toBe(true)
    await act(async () => { resolveOld(stats); await Promise.resolve() })
    expect(hook.result.current.stats).toBeNull()
    expect(hook.result.current.loading).toBe(false)
  })

  it("does not resurrect loading when an A request returns after A to B to A before debounce", async () => {
    mocks.stats.mockImplementation(() => new Promise(() => {}))
    const changedFactor = {
      ...factor,
      rules: [{ boundary: "90", label: "low" }, factor.rules[1]],
    }
    const hook = renderHook(({ value }) => useBandingStats({ ...common, factor: value }), {
      initialProps: { value: factor },
    })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    expect(hook.result.current.loading).toBe(true)

    hook.rerender({ value: changedFactor })
    hook.rerender({ value: factor })
    expect(hook.result.current.loading).toBe(false)
    expect(mocks.stats).toHaveBeenCalledTimes(1)
  })

  it("invalidates resolved rating levels when columns change under the same node and source", async () => {
    mocks.levels.mockResolvedValue(levels)
    const hook = renderHook(({ columns }) => useRatingLevels({ ...common, columns }), {
      initialProps: { columns: ["region", "region"] },
    })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    expect(hook.result.current.levels).toEqual({ region: ["north"] })
    expect(mocks.levels).toHaveBeenCalledTimes(1)

    hook.rerender({ columns: ["region"] })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    expect(mocks.levels).toHaveBeenCalledTimes(1)

    hook.rerender({ columns: ["occupation"] })
    expect(hook.result.current.levels).toEqual({})
  })

  it("invalidates rating levels when source or node changes", async () => {
    mocks.levels.mockResolvedValue(levels)
    const hook = renderHook(
      ({ requestNode }) => useRatingLevels({ ...common, node: requestNode, columns: ["region"] }),
      { initialProps: { requestNode: node } },
    )
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    expect(hook.result.current.levels).toEqual({ region: ["north"] })

    mocks.activeSource = "archive"
    hook.rerender({ requestNode: node })
    expect(hook.result.current.levels).toEqual({})

    hook.rerender({ requestNode: otherNode })
    expect(hook.result.current.levels).toEqual({})
  })

  it("does not let an out-of-order rating completion clear a pending successor loading state", async () => {
    let resolveOld!: (value: typeof levels) => void
    mocks.levels.mockImplementationOnce(
      () => new Promise<typeof levels>((resolve) => { resolveOld = resolve }),
    )
    mocks.levels.mockImplementationOnce(() => new Promise(() => {}))
    const hook = renderHook(({ columns }) => useRatingLevels({ ...common, columns }), {
      initialProps: { columns: ["region"] },
    })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    hook.rerender({ columns: ["occupation"] })
    await advance(WHOLE_DATA_DEBOUNCE_MS + 1)
    expect(hook.result.current.loading).toBe(true)

    await act(async () => { resolveOld(levels); await Promise.resolve() })
    expect(hook.result.current.levels).toEqual({})
    expect(hook.result.current.error).toBeNull()
    expect(hook.result.current.loading).toBe(true)
  })
})

describe("wholeDataStatus", () => {
  const base: WholeDataStatusInput = {
    availability: "current",
    problem: null,
    answered: false,
    cacheRequired: false,
    error: null,
  }
  const failed = { kind: "failed" as const, reason: "server down" }

  it.each<[string, Partial<WholeDataStatusInput>, string | null]>([
    ["no point yet", { availability: "checking" }, null],
    ["a current point awaiting its answer", {}, null],
    ["the answer", { answered: true }, null],
    ["a build", { availability: "building" }, "Caching the data…"],
    ["nothing cached", { availability: "missing" }, "Not cached · Refresh this node to read all rows"],
    ["a partial generation", { availability: "partial" }, "Not cached · Refresh this node to read all rows"],
    ["a server asking for caching again", { cacheRequired: true }, "Not cached · Refresh this node to read all rows"],
    ["data out of date", { availability: "stale" }, "Cached data is out of date · Refresh this node to read all rows"],
    [
      "data out of date whose refresh failed",
      { availability: "stale", problem: failed },
      "Cached data is out of date · Refreshing it failed: server down",
    ],
    ["a refused point", { availability: "checking", problem: { kind: "refused", reason: "No input" } }, "No input"],
    ["a failed point read", { availability: "checking", problem: failed }, "Couldn't read the data: server down"],
    ["a failed question", { error: "bad rules" }, "Couldn't read the data: bad rules"],
  ])("words %s", (_name, input, text) => {
    expect(wholeDataStatusText(wholeDataStatus({ ...base, ...input }))).toBe(text)
  })

  it("never calls data that could not be read counting", () => {
    for (const availability of ["checking", "current", "missing"] as const) {
      expect(wholeDataStatus({ ...base, availability, problem: failed }).kind).toBe("failed")
    }
  })
})
