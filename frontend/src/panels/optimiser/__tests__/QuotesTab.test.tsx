import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ApplyOptimiserResponse, OptimiserApplyQuery, OptimiserQuoteColumn } from "../../../api/types"
import useNodeResultsStore from "../../../stores/useNodeResultsStore"
import QuotesTab from "../QuotesTab"
import { makeOnlineSolveResult, makeRatebookSolveResult } from "./fixtures"

const mockApply = vi.fn()

vi.mock("../../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/client")>()
  return { ...actual, applyOptimiser: (...args: unknown[]) => mockApply(...args) }
})

const { ApiError } = await import("../../../api/client")

const DEFAULT_QUERY: OptimiserApplyQuery = {
  sort_by: null,
  descending: false,
  quote_id_prefix: null,
  filters: {
    scenario_value_min: null,
    scenario_value_max: null,
    at_range_edge: false,
    analysis_equals: {},
    deployed_factor_differs: false,
  },
  offset: 0,
  limit: 100,
}

const ONLINE_COLUMNS: OptimiserQuoteColumn[] = [
  { name: "quote_id", role: "id", sortable: false, filterable: false },
  { name: "optimal_scenario_value", role: "scenario", sortable: true, filterable: false },
  { name: "optimal_objective", role: "objective", sortable: true, filterable: false },
  { name: "optimal_volume", role: "constraint", sortable: true, filterable: false },
  { name: "region", role: "analysis", sortable: true, filterable: true },
]

const RATEBOOK_COLUMNS: OptimiserQuoteColumn[] = [
  ...ONLINE_COLUMNS.slice(0, 4),
  { name: "factor_product", role: "factor", sortable: true, filterable: false },
  { name: "deployed_factor_differs", role: "flag", sortable: false, filterable: false },
]

function onlineRow(quoteId: string, scenario: number, region: string | null = "North") {
  return {
    quote_id: quoteId,
    optimal_scenario_value: scenario,
    optimal_objective: 1234.5,
    optimal_volume: 0.75,
    region,
  }
}

function page(
  rows: Record<string, unknown>[],
  overrides: Partial<ApplyOptimiserResponse> = {},
): ApplyOptimiserResponse {
  return {
    status: "ok",
    total_objective: 1000,
    constraints: { volume: 0.9 },
    from_artifact: true,
    columns: ONLINE_COLUMNS,
    preview: rows,
    row_count: 1000,
    matched_row_count: rows.length,
    offset: 0,
    preview_row_count: rows.length,
    preview_row_limit: 100,
    frontier_generation: 0,
    error: null,
    ...overrides,
  }
}

const ROWS = [onlineRow("Q001", 1.1), onlineRow("Q002", 0.9, "South"), onlineRow("Q003", 1.0, null)]

function install(mode: "online" | "ratebook" = "online") {
  const store = useNodeResultsStore.getState()
  store.startSolveJob("opt_1", "job_1", "Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
  store.completeSolveJob("opt_1", mode === "online" ? makeOnlineSolveResult() : makeRatebookSolveResult())
}

function renderTab(mode: "online" | "ratebook" = "online") {
  install(mode)
  return render(<QuotesTab nodeId="opt_1" jobId="job_1" mode={mode} frontierGeneration={0} pointIndex={null} />)
}

/** The query each request sent, in order. */
function queries(): OptimiserApplyQuery[] {
  return mockApply.mock.calls.map(([payload]) => {
    const { job_id: _jobId, point_index: _pointIndex, ...query } = payload as Record<string, unknown>
    return query as unknown as OptimiserApplyQuery
  })
}

function header(name: string): HTMLElement {
  return screen.getAllByRole("columnheader").find((th) => th.textContent?.startsWith(name))!
}

beforeEach(() => {
  mockApply.mockReset()
  useNodeResultsStore.setState({ solveResults: {}, solveJobs: {}, optimiserApplyCache: [] })
})
afterEach(cleanup)

describe("QuotesTab", () => {
  it("formats the columns and marks each scenario value against 1.0 in words", async () => {
    mockApply.mockResolvedValue(page(ROWS))
    renderTab()

    const table = await screen.findByRole("table", { name: "Per-quote detail" })
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Quote ID", "Scenario value", "Objective", "volume", "region",
    ])
    expect(within(table).getByRole("img", { name: "Adjusted up" })).toHaveTextContent("▲")
    expect(within(table).getByRole("img", { name: "Adjusted down" })).toHaveTextContent("▼")
    expect(within(table).getByRole("img", { name: "Unadjusted" })).toHaveTextContent("=")
    const first = within(table).getAllByRole("row")[1]
    expect(within(first).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "Q001", "▲1.1", "1,234.5", "0.75", "North",
    ])
    // A missing analysis value is a dash, not an empty cell.
    expect(within(within(table).getAllByRole("row")[3]).getAllByRole("cell")[4]).toHaveTextContent("—")
    expect(queries()).toEqual([DEFAULT_QUERY])
    expect(mockApply.mock.calls[0][0]).toEqual({ job_id: "job_1", ...DEFAULT_QUERY })
  })

  it("cycles aria-sort ascending and descending, asking the server for each", async () => {
    mockApply.mockResolvedValue(page(ROWS))
    renderTab()
    await screen.findByRole("table", { name: "Per-quote detail" })
    expect(header("Scenario value")).not.toHaveAttribute("aria-sort")
    expect(within(header("Quote ID")).queryByRole("button")).toBeNull()

    fireEvent.click(within(header("Scenario value")).getByRole("button"))
    await waitFor(() => expect(header("Scenario value")).toHaveAttribute("aria-sort", "ascending"))
    fireEvent.click(within(header("Scenario value")).getByRole("button"))
    await waitFor(() => expect(header("Scenario value")).toHaveAttribute("aria-sort", "descending"))
    fireEvent.click(within(header("region")).getByRole("button"))
    await waitFor(() => expect(header("region")).toHaveAttribute("aria-sort", "ascending"))
    expect(header("Scenario value")).not.toHaveAttribute("aria-sort")

    expect(queries().slice(1)).toEqual([
      { ...DEFAULT_QUERY, sort_by: "optimal_scenario_value", descending: false },
      { ...DEFAULT_QUERY, sort_by: "optimal_scenario_value", descending: true },
      { ...DEFAULT_QUERY, sort_by: "region", descending: false },
    ])
  })

  it("sends each preset's request and shows it pressed", async () => {
    mockApply.mockResolvedValue(page(ROWS))
    renderTab()
    await screen.findByRole("table", { name: "Per-quote detail" })
    const presets = screen.getByRole("group", { name: "Presets" })
    expect(within(presets).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Highest adjustment", "Lowest adjustment", "At range edge",
    ])

    for (const name of ["Highest adjustment", "Lowest adjustment", "At range edge"]) {
      fireEvent.click(within(presets).getByRole("button", { name }))
      await waitFor(() => expect(within(presets).getByRole("button", { name })).toHaveAttribute("aria-pressed", "true"))
    }

    expect(queries().slice(1)).toEqual([
      { ...DEFAULT_QUERY, sort_by: "optimal_scenario_value", descending: true },
      { ...DEFAULT_QUERY, sort_by: "optimal_scenario_value", descending: false },
      { ...DEFAULT_QUERY, filters: { ...DEFAULT_QUERY.filters, at_range_edge: true } },
    ])
    expect(within(presets).getByRole("button", { name: "Highest adjustment" })).toHaveAttribute("aria-pressed", "false")
  })

  it("offers the deployed-factor preset for a ratebook page, with its factor columns", async () => {
    mockApply.mockResolvedValue(page(
      [{ ...onlineRow("Q001", 1.1), region: undefined, factor_product: 1.08, deployed_factor_differs: true }],
      { columns: RATEBOOK_COLUMNS },
    ))
    renderTab("ratebook")

    const table = await screen.findByRole("table", { name: "Per-quote detail" })
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Quote ID", "Scenario value", "Objective", "volume", "Factor product", "Deployed ≠ evaluated",
    ])
    expect(within(within(table).getAllByRole("row")[1]).getAllByRole("cell").slice(4).map((cell) => cell.textContent))
      .toEqual(["1.08", "Yes"])

    fireEvent.click(screen.getByRole("button", { name: "Deployed ≠ evaluated" }))
    await waitFor(() => expect(queries()).toHaveLength(2))
    expect(queries()[1]).toEqual({
      ...DEFAULT_QUERY,
      filters: { ...DEFAULT_QUERY.filters, deployed_factor_differs: true },
    })
  })

  it("filters by an analysis value from its cell and removes the filter from its chip", async () => {
    mockApply.mockResolvedValue(page(ROWS))
    renderTab()
    await screen.findByRole("table", { name: "Per-quote detail" })

    fireEvent.click(screen.getByRole("button", { name: "Show only quotes with region = South" }))
    await waitFor(() => expect(queries()).toHaveLength(2))
    expect(queries()[1]).toEqual({
      ...DEFAULT_QUERY,
      filters: { ...DEFAULT_QUERY.filters, analysis_equals: { region: "South" } },
    })

    const chip = await screen.findByRole("button", { name: "Remove filter region = South" })
    fireEvent.click(chip)
    // The unfiltered page is already cached: removing the filter asks for nothing.
    expect(await screen.findByRole("button", { name: "Show only quotes with region = North" })).toBeInTheDocument()
    expect(queries()).toHaveLength(2)
    expect(screen.queryByRole("group", { name: "Filters" })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Show only quotes with region = missing" }))
    await waitFor(() => expect(queries()).toHaveLength(3))
    expect(queries()[2].filters.analysis_equals).toEqual({ region: null })
  })

  it("searches quote IDs once typing pauses, from the first page", async () => {
    mockApply.mockResolvedValue(page(ROWS, { matched_row_count: 250, offset: 0 }))
    renderTab()
    await screen.findByRole("table", { name: "Per-quote detail" })
    fireEvent.click(screen.getByRole("button", { name: "Next" }))
    await waitFor(() => expect(queries()).toHaveLength(2))

    fireEvent.change(screen.getByLabelText("Search quote IDs"), { target: { value: "Q0" } })
    fireEvent.change(screen.getByLabelText("Search quote IDs"), { target: { value: "Q00" } })

    await waitFor(() => expect(queries()).toHaveLength(3))
    expect(queries()[2]).toEqual({ ...DEFAULT_QUERY, quote_id_prefix: "Q00", offset: 0 })
  })

  it("pages with Previous and Next and states the counts", async () => {
    mockApply.mockImplementation((payload: { offset: number }) => Promise.resolve(
      page(ROWS, { matched_row_count: 250, offset: payload.offset }),
    ))
    renderTab()

    expect(await screen.findByText("Showing 1–3 of 250 matching (of 1,000)")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(await screen.findByText("Showing 101–103 of 250 matching (of 1,000)")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(await screen.findByText("Showing 201–203 of 250 matching (of 1,000)")).toBeInTheDocument()
    // The last matching page: nothing after it.
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Previous" }))
    expect(await screen.findByText("Showing 101–103 of 250 matching (of 1,000)")).toBeInTheDocument()
    expect(queries().map((query) => query.offset)).toEqual([0, 100, 200])
  })

  it("stops at the depth limit and asks to narrow the filter", async () => {
    mockApply.mockResolvedValue(page(ROWS, { matched_row_count: 20_000, offset: 9_900 }))
    renderTab()

    expect(await screen.findByText("Showing 9,901–9,903 of 20,000 matching (of 1,000)")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()
    expect(screen.getByRole("note")).toHaveTextContent("narrow the filter or search the quote ID")
  })

  it("says when no quote matches", async () => {
    mockApply.mockResolvedValue(page([], { matched_row_count: 0 }))
    renderTab()

    expect(await screen.findByText("No quotes match (of 1,000)")).toBeInTheDocument()
    expect(screen.getByText("No quotes of the solved result match.")).toBeInTheDocument()
  })

  it("drops a response for a query the tab has moved past", async () => {
    let resolveFirst!: (response: ApplyOptimiserResponse) => void
    mockApply
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(page([onlineRow("HIGHEST", 1.2)]))
    renderTab()
    await waitFor(() => expect(queries()).toHaveLength(1))

    fireEvent.click(screen.getByRole("button", { name: "Highest adjustment" }))
    expect(await screen.findByText("HIGHEST")).toBeInTheDocument()
    await act(async () => { resolveFirst(page([onlineRow("LATE", 1.0)])) })

    expect(screen.queryByText("LATE")).toBeNull()
    expect(screen.getByText("HIGHEST")).toBeInTheDocument()
  })

  it("refuses a page the server answered for another frontier generation", async () => {
    // The server recomputed the frontier between the request and its reply.
    mockApply.mockResolvedValueOnce(page(ROWS, { frontier_generation: 1 }))
    renderTab()

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The server answered for frontier generation 1, but this result shows generation 0.",
    )
    expect(screen.queryByText("Q001")).toBeNull()
  })

  it("offers Retry after a failure, and none when the result is gone", async () => {
    mockApply
      .mockRejectedValueOnce(new ApiError("HTTP 507", 507, "The optimiser choice query needs more memory."))
      .mockResolvedValueOnce(page(ROWS))
    renderTab()

    expect(await screen.findByRole("alert")).toHaveTextContent("Per-quote detail could not be loaded")
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("Q001")).toBeInTheDocument()

    mockApply.mockRejectedValueOnce(new ApiError("HTTP 410", 410, "Optimiser apply artifact is no longer available. Re-run the solve to regenerate it."))
    fireEvent.click(screen.getByRole("button", { name: "Lowest adjustment" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Re-run the solve")
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull()
  })
})
