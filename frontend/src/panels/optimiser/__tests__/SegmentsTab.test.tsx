import { useState } from "react"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type {
  OptimiserSegmentIndexResponse,
  OptimiserSegmentKey,
  OptimiserSegmentRow,
  OptimiserSegmentsResponse,
  OptimiserSolveResult,
} from "../../../api/types"
import SegmentsTab, { type SegmentResults } from "../SegmentsTab"
import { makeOnlineSolveResult } from "./fixtures"

const mockSegments = vi.fn()
const mockIndex = vi.fn()

vi.mock("../../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/client")>()
  return {
    ...actual,
    getOptimiserSegments: (...args: unknown[]) => mockSegments(...args),
    getOptimiserSegmentIndex: (...args: unknown[]) => mockIndex(...args),
  }
})

const { ApiError } = await import("../../../api/client")

afterEach(cleanup)
beforeEach(() => {
  mockSegments.mockReset()
  mockIndex.mockReset()
})

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (error: unknown) => void }

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function key(name: string, overrides: Partial<OptimiserSegmentKey> = {}): OptimiserSegmentKey {
  return {
    key: name,
    source: "analysis",
    binning: "categorical",
    available: true,
    unavailable_reason: null,
    ...overrides,
  }
}

const KEYS: OptimiserSegmentKey[] = [
  key("region"),
  key("age", { binning: "numeric" }),
  key("postcode", {
    available: false,
    unavailable_reason: "about 9,000 distinct values; at most 1,800 (estimated) can be broken down.",
  }),
]

function figures(mean: number, up = 0.5, down = 0.25, edge = 0.1) {
  return { mean_scenario_value: mean, share_up: up, share_down: down, share_at_edge: edge }
}

function level(label: string, quotes: number, mean: number, overrides: Partial<OptimiserSegmentRow> = {}): OptimiserSegmentRow {
  return {
    label,
    kind: "value",
    lower: null,
    upper: null,
    merged_levels: null,
    quotes,
    weight_total: quotes,
    unweighted: figures(mean),
    weighted: figures(mean),
    deployed_factor_differs: null,
    ...overrides,
  }
}

function breakdown(overrides: Partial<OptimiserSegmentsResponse> = {}): OptimiserSegmentsResponse {
  const rows = overrides.rows ?? [
    level("North", 60, 1.04),
    level("South", 30, 0.95),
    level("Missing", 10, 1.0, { kind: "missing" }),
  ]
  return {
    key: "region",
    source: "analysis",
    binning: "categorical",
    weight: "quotes",
    weight_label: "Quotes",
    point_index: null,
    frontier_generation: 0,
    n_quotes: rows.reduce((total, row) => total + row.quotes, 0),
    n_levels: 2,
    mean_scenario_value: 1.0,
    weighted_mean_scenario_value: 1.0,
    rows,
    diagnostics_errors: [],
    ...overrides,
  }
}

function ranked(overrides: Partial<OptimiserSegmentIndexResponse> = {}): OptimiserSegmentIndexResponse {
  return {
    point_index: null,
    frontier_generation: 0,
    statistic: {
      label: "Adjustment spread",
      description: "How differently the optimiser adjusted the key's levels.",
    },
    keys: [
      { ...KEYS[1], spread: 0.08 },
      { ...KEYS[0], spread: 0.02 },
      { ...KEYS[2], spread: null },
    ],
    ...overrides,
  }
}

/** The preview owns the shared selection and the loaded results; this stands in for it. */
function Harness({
  solvedResult = makeOnlineSolveResult({ segment_keys: KEYS }),
  pointIndex = null,
  initialSelected = null,
}: {
  solvedResult?: OptimiserSolveResult
  pointIndex?: number | null
  initialSelected?: string | null
}) {
  const [selected, setSelected] = useState<string | null>(initialSelected)
  const [search, setSearch] = useState("")
  const [results, setResults] = useState<SegmentResults>({ breakdowns: {}, indexes: {} })
  return (
    <>
      <output data-testid="shared-selection">{selected ?? ""}</output>
      <SegmentsTab
        jobId="job_1"
        frontierGeneration={0}
        pointIndex={pointIndex}
        solvedResult={solvedResult}
        selection={{ selected, onSelect: setSelected, search, onSearch: setSearch }}
        results={results}
        onBreakdown={(cacheKey, response) =>
          setResults((current) => ({ ...current, breakdowns: { ...current.breakdowns, [cacheKey]: response } }))}
        onIndex={(cacheKey, response) =>
          setResults((current) => ({ ...current, indexes: { ...current.indexes, [cacheKey]: response } }))}
      />
    </>
  )
}

function browserKeys(): string[] {
  const list = screen.getByRole("group", { name: /Keys/ })
  return within(list).getAllByRole("button").map((button) => button.getAttribute("aria-label") ?? "")
}

async function flush() {
  await act(async () => {
    await Promise.resolve()
  })
}

describe("SegmentsTab: keys and the index", () => {
  it("says to add analysis columns when the result has no key, and asks for nothing", () => {
    render(<Harness solvedResult={makeOnlineSolveResult({ segment_keys: [] })} />)

    expect(screen.getByText("Add analysis columns in the optimiser config")).toBeTruthy()
    expect(mockIndex).not.toHaveBeenCalled()
    expect(mockSegments).not.toHaveBeenCalled()
  })

  it("lists the keys unranked while the index loads, then ranks them by the named statistic", async () => {
    const index = deferred<OptimiserSegmentIndexResponse>()
    mockIndex.mockReturnValue(index.promise)
    mockSegments.mockResolvedValue(breakdown())
    render(<Harness />)

    expect(browserKeys()).toEqual(["region", "age", "postcode"])
    expect(screen.getByText(/Ranking the keys/)).toBeTruthy()
    expect(screen.queryAllByTestId("feature-browser-bar")).toHaveLength(0)
    expect(mockIndex).toHaveBeenCalledWith({ job_id: "job_1", point_index: null }, expect.anything())

    await act(async () => index.resolve(ranked()))

    expect(browserKeys()).toEqual(["age", "region", "postcode"])
    expect(screen.getByText("Adjustment spread")).toBeTruthy()
    expect(screen.getByRole("group", { name: "Keys ranked by adjustment spread" })).toBeTruthy()
  })

  it("keeps the keys unranked with the message and Retry when the index fails", async () => {
    mockIndex.mockRejectedValueOnce(new ApiError("HTTP 500", 500, "The ranking failed."))
    mockSegments.mockResolvedValue(breakdown())
    render(<Harness />)
    await flush()

    expect(browserKeys()).toEqual(["region", "age", "postcode"])
    expect(screen.getByText(/The ranking failed\./)).toBeTruthy()
    mockIndex.mockResolvedValueOnce(ranked())
    fireEvent.click(screen.getByRole("button", { name: "Retry ranking" }))
    await flush()

    expect(mockIndex).toHaveBeenCalledTimes(2)
    expect(browserKeys()).toEqual(["age", "region", "postcode"])
  })
})

describe("SegmentsTab: a key's breakdown", () => {
  it("searches, selects and shares the selection", async () => {
    mockIndex.mockResolvedValue(ranked())
    mockSegments.mockResolvedValue(breakdown())
    render(<Harness />)
    await flush()

    fireEvent.change(screen.getByRole("textbox", { name: "Search keys" }), { target: { value: "reg" } })
    expect(browserKeys()).toEqual(["region"])
    fireEvent.click(screen.getByRole("button", { name: "region" }))
    await flush()

    expect(screen.getByTestId("shared-selection").textContent).toBe("region")
    expect(mockSegments).toHaveBeenCalledWith(
      { job_id: "job_1", point_index: null, key: "region", weight: "quotes" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(screen.getByRole("group", { name: "Mean chosen scenario value for region" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "region" }).getAttribute("aria-pressed")).toBe("true")
  })

  it("starts from a key the Rates tab selected", async () => {
    mockIndex.mockResolvedValue(ranked())
    mockSegments.mockResolvedValue(breakdown())
    render(<Harness initialSelected="region" />)
    await flush()

    expect(mockSegments.mock.calls[0][0]).toMatchObject({ key: "region" })
  })

  it("fills the detail line for a focused level", async () => {
    mockIndex.mockResolvedValue(ranked())
    mockSegments.mockResolvedValue(breakdown())
    render(<Harness initialSelected="region" />)
    await flush()

    const detail = screen.getByText("Hover or focus a level to inspect its adjustments.").closest("[role=status]")!
    expect(detail).toBeTruthy()
    const rows = screen.getAllByTestId("relativity-row")
    expect(rows.map((row) => row.getAttribute("data-key"))).toEqual(["value:North", "value:South", "missing:Missing"])
    fireEvent.focus(rows[0])

    expect(detail.textContent).toContain("North")
    expect(detail.textContent).toContain("Quotes: 60 (60.0%)")
    expect(detail.textContent).toContain("Mean: 1.04 (+4.0% vs 1.0)")
    expect(detail.textContent).toContain("Adjusted up: 50.0%")
    expect(detail.textContent).toContain("At range edge: 10.0%")
  })

  it("lists every level in the values table, with a dash for an unavailable weighted figure", async () => {
    mockIndex.mockResolvedValue(ranked())
    mockSegments.mockImplementation(async (payload: { weight: string }) =>
      payload.weight === "quotes"
        ? breakdown()
        : breakdown({
          weight: "optimal_loss_ratio",
          weight_label: "loss_ratio at the chosen scenario",
          rows: [
            level("North", 60, 1.04, { weighted: figures(1.06), weight_total: 12.5 }),
            level("South", 30, 0.95, { weighted: null, weight_total: 0 }),
          ],
          diagnostics_errors: [
            {
              diagnostic: "segment_weight",
              error_type: "ZeroLevelWeight",
              message: "loss_ratio at the chosen scenario is zero for every quote in 'South'.",
            },
          ],
        }))
    render(<Harness initialSelected="region" />)
    await flush()

    fireEvent.click(screen.getByRole("button", { name: "loss_ratio at the chosen scenario" }))
    await flush()

    expect(mockSegments).toHaveBeenLastCalledWith(
      expect.objectContaining({ key: "region", weight: "optimal_loss_ratio" }),
      expect.anything(),
    )
    expect(screen.getByText(/is zero for every quote in 'South'/)).toBeTruthy()
    const table = screen.getByRole("table", { name: "region segment values" })
    const cells = within(table).getAllByRole("row").map((row) =>
      within(row).queryAllByRole("cell").map((cell) => cell.textContent))
    expect(cells[1]).toEqual(["60", "1.04", "1.06", "50.0%", "25.0%", "10.0%"])
    expect(cells[2]).toEqual(["30", "0.95", "—", "—", "—", "—"])
    // The South bar is unavailable: no bar, a dash.
    const south = screen.getAllByTestId("relativity-row")[1]
    expect(south.querySelector("[data-relativity-bar]")).toBeNull()
    expect(south.textContent).toContain("—")
  })

  it("aborts the request in flight when the key changes", async () => {
    mockIndex.mockResolvedValue(ranked())
    const first = deferred<OptimiserSegmentsResponse>()
    mockSegments.mockReturnValueOnce(first.promise).mockResolvedValue(breakdown({ key: "age", binning: "numeric" }))
    render(<Harness initialSelected="region" />)
    await flush()
    const firstSignal = (mockSegments.mock.calls[0][1] as { signal: AbortSignal }).signal
    expect(firstSignal.aborted).toBe(false)

    fireEvent.click(screen.getByRole("button", { name: "age" }))
    await flush()

    expect(firstSignal.aborted).toBe(true)
    expect(mockSegments.mock.calls[1][0]).toMatchObject({ key: "age" })
    // The aborted reply is dropped even if it arrives.
    await act(async () => first.resolve(breakdown({ rows: [level("Stale", 5, 1.2)] })))
    expect(screen.queryByText("Stale")).toBeNull()
  })

  it("shows why an unavailable key cannot be broken down, and asks for nothing", async () => {
    mockIndex.mockResolvedValue(ranked())
    mockSegments.mockResolvedValue(breakdown())
    render(<Harness initialSelected="postcode" />)
    await flush()

    expect(screen.getByText(/postcode cannot be broken down: about 9,000 distinct values/)).toBeTruthy()
    expect(mockSegments).not.toHaveBeenCalled()
  })

  it("loads the selected point's breakdown and keeps loaded results for the review", async () => {
    mockIndex.mockResolvedValue(ranked({ point_index: 2 }))
    mockSegments.mockResolvedValue(breakdown({ point_index: 2 }))
    render(<Harness pointIndex={2} initialSelected="region" />)
    await flush()

    expect(mockIndex).toHaveBeenCalledWith({ job_id: "job_1", point_index: 2 }, expect.anything())
    expect(mockSegments.mock.calls[0][0]).toMatchObject({ point_index: 2, key: "region" })
    fireEvent.click(screen.getByRole("button", { name: "age" }))
    await flush()
    fireEvent.click(screen.getByRole("button", { name: "region" }))
    await flush()

    // Region was already loaded for this target: no second request for it.
    expect(mockSegments.mock.calls.map((call) => (call[0] as { key: string }).key)).toEqual(["region", "age"])
    expect(mockIndex).toHaveBeenCalledTimes(1)
  })

  it("offers Retry after a failure, and none for a point that is gone", async () => {
    mockIndex.mockResolvedValue(ranked())
    mockSegments.mockRejectedValueOnce(new ApiError("HTTP 500", 500, "Boom."))
    render(<Harness initialSelected="region" />)
    await flush()

    expect(screen.getByText(/Boom\./)).toBeTruthy()
    mockSegments.mockRejectedValueOnce(new ApiError("HTTP 410", 410, "The point is no longer available."))
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    await flush()

    expect(screen.getByText(/The point is no longer available\./)).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull()
  })
})
