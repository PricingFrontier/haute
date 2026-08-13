import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type {
  ExploreCacheReport,
  ExplorePivotResult,
  ExplorePivotRunResponse,
  ExplorePivotStatusResponse,
} from "../../../api/types"
import useGraphStore from "../../../stores/useGraphStore"
import useNodeResultsStore, {
  explorePivotResultKey,
  resetNodeResultsDerivedCaches,
} from "../../../stores/useNodeResultsStore"
import useSettingsStore from "../../../stores/useSettingsStore"
import type { SimpleNode } from "../../editors"
import ExplorePivotsPane from "../ExplorePivotsPane"
import {
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "../pivotConfig"

const mockRunExplorePivot = vi.fn()
const mockCancelExplorePivot = vi.fn()

vi.mock("../../../api/client", () => ({
  runExplorePivot: (...args: unknown[]) => mockRunExplorePivot(...args),
  cancelExplorePivot: (...args: unknown[]) => mockCancelExplorePivot(...args),
}))

function makePivot(
  id: string,
  overrides: Partial<ExplorePivotConfig> = {},
): ExplorePivotConfig {
  return {
    version: 1,
    id,
    name: `Pivot ${id}`,
    enabled: true,
    filters: [],
    rows: [{ id: `${id}-row`, field: "region" }],
    columns: [],
    values: [
      {
        id: `${id}-value`,
        field: "paid",
        aggregation: "sum",
        display_name: "Paid",
      },
    ],
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
  }
}

function makeNode(pivots: ExplorePivotConfig[]): SimpleNode {
  return {
    id: "explore_1",
    type: "explore",
    data: {
      label: "Explore Claims",
      description: "",
      nodeType: "explore",
      config: { pivots },
    },
  }
}

function makeReport(
  dataframeCacheKey = "explore_dataset:current",
): ExploreCacheReport {
  return {
    status: "ok",
    node_id: "explore_1",
    upstream_node_id: "source_1",
    source: "pricing",
    dataframe_cache_key: dataframeCacheKey,
    row_count: 1,
    column_count: 2,
    generated_at: 1,
    columns: [],
    overview_summary: {
      data_quality: {
        issue_count: 0,
        issues: [],
        duplicate_row_count: 0,
        duplicate_ratio: 0,
      },
      categorical_summary: [],
    },
  }
}

function makeResult(
  pivot: ExplorePivotConfig,
  dataframeCacheKey = "explore_dataset:current",
): ExplorePivotResult {
  return {
    version: 1,
    node_id: "explore_1",
    pivot_id: pivot.id,
    source: "pricing",
    dataframe_cache_key: dataframeCacheKey,
    calculation_key: "calculation-key",
    row_fields: ["region"],
    column_fields: [],
    values: pivot.values.map(({ id, field, aggregation }) => ({
      id,
      field,
      aggregation,
    })),
    row_paths: [
      {
        members: [{ kind: "string", value: "North" }],
        is_grand_total: false,
      },
    ],
    column_paths: [{ members: [], is_grand_total: false }],
    cells: [
      {
        row_index: 0,
        column_index: 0,
        value_id: pivot.values[0].id,
        value: 42,
      },
    ],
    warnings: [],
    generated_at: 2,
    execution_metrics: null,
  }
}

function renderPane(
  pivots: ExplorePivotConfig[],
  report: ExploreCacheReport | null = makeReport(),
) {
  const node = makeNode(pivots)
  return render(
    <ExplorePivotsPane
      node={node}
      allNodes={[node]}
      edges={[]}
      submodels={{}}
      preamble=""
      report={report}
    />,
  )
}

function startStoredJob(
  pivot: ExplorePivotConfig,
  jobId: string,
  calculationIdentity = pivotCalculationIdentity(pivot),
  requestedDataframeCacheKey: string | null = "explore_dataset:current",
) {
  const key = explorePivotResultKey("explore_1", pivot.id)
  useNodeResultsStore.getState().startExplorePivotJob(
    key,
    jobId,
    "explore_1",
    pivot.id,
    "Explore Claims",
    pivot.name,
    calculationIdentity,
    "pricing",
    0,
    requestedDataframeCacheKey,
  )
  return key
}

function completeStoredResult(
  pivot: ExplorePivotConfig,
  dataframeCacheKey = "explore_dataset:current",
  calculationIdentity = pivotCalculationIdentity(pivot),
) {
  const key = startStoredJob(pivot, `completed-${pivot.id}`, calculationIdentity)
  act(() => {
    useNodeResultsStore
      .getState()
      .completeExplorePivotJob(key, makeResult(pivot, dataframeCacheKey))
  })
  return key
}

describe("ExplorePivotsPane", () => {
  beforeEach(() => {
    mockRunExplorePivot.mockReset()
    mockCancelExplorePivot.mockReset()
    resetNodeResultsDerivedCaches()
    useGraphStore.setState({ structuralVersion: 0 })
    useSettingsStore.setState({
      activeSource: "pricing",
      streamingChunkSize: 250_000,
    })
    useNodeResultsStore.setState({ pivotResults: {}, pivotJobs: {} })
  })

  afterEach(cleanup)

  it("renders malformed config instead of mounting pivot cards", () => {
    const duplicate = makePivot("duplicate")

    renderPane([duplicate, duplicate])

    expect(screen.getByRole("alert")).toHaveTextContent(/duplicate pivot id/i)
    expect(screen.queryByRole("region")).not.toBeInTheDocument()
  })

  it.each([
    ["no cards", [], "Add a pivot from the Pivots settings pane."],
    [
      "all cards hidden",
      [makePivot("hidden", { enabled: false })],
      "No pivots are currently shown.",
    ],
  ] as const)("renders the %s empty state", (_state, pivots, message) => {
    renderPane([...pivots])

    expect(screen.getByText(message)).toBeVisible()
    expect(screen.queryByRole("region")).not.toBeInTheDocument()
  })

  it("treats an Explore node without config as having no pivot cards", () => {
    const node = makeNode([])
    delete node.data.config

    render(
      <ExplorePivotsPane
        node={node}
        allNodes={[node]}
        edges={[]}
        report={makeReport()}
      />,
    )

    expect(
      screen.getByText("Add a pivot from the Pivots settings pane."),
    ).toBeVisible()
  })

  it("does not schedule or render a refresh action for a pivot without Values", () => {
    const pivot = makePivot("unconfigured", { values: [] })

    renderPane([pivot])

    expect(screen.queryByRole("button", { name: "Update" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
    expect(
      screen.getByText("Add at least one Value in this pivot's configuration."),
    ).toBeVisible()
    expect(mockRunExplorePivot).not.toHaveBeenCalled()
  })

  it("waits for cached Explore data without a report or retained result", () => {
    const pivot = makePivot("average-claims", {
      rows: [{ id: "cover-type", field: "cover_type" }],
      values: [
        {
          id: "total-claims",
          field: "total_claims",
          aggregation: "average",
          display_name: "Average total claims",
        },
      ],
    })

    renderPane([pivot], null)

    expect(
      screen.getByText(
        "Cache the full Explore data above to calculate this pivot automatically.",
      ),
    ).toBeVisible()
    expect(mockRunExplorePivot).not.toHaveBeenCalled()
  })

  it("automatically calculates when the current cache report arrives", async () => {
    const pivot = makePivot("report-arrived")
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: "report-arrived-job",
      cached: false,
      message: "Calculating cached data",
      result: null,
      failure: null,
    })
    const view = renderPane([pivot], null)
    expect(mockRunExplorePivot).not.toHaveBeenCalled()

    const node = makeNode([pivot])
    view.rerender(
      <ExplorePivotsPane
        node={node}
        allNodes={[node]}
        edges={[]}
        submodels={{}}
        preamble=""
        report={makeReport()}
      />,
    )

    await waitFor(() => expect(mockRunExplorePivot).toHaveBeenCalledTimes(1))
    expect(screen.getByText("Calculating cached data")).toBeVisible()
  })

  it("keeps a retained result visible and waits when the report is absent", () => {
    const pivot = makePivot("stale-absent")
    completeStoredResult(pivot)

    renderPane([pivot], null)

    expect(screen.getByText("Waiting for current cached Explore data.")).toBeVisible()
    expect(
      screen.getByRole("table", { name: `${pivot.name} results` }),
    ).toBeVisible()
    expect(mockRunExplorePivot).not.toHaveBeenCalled()
  })

  it("automatically refreshes a retained result when the report cache changes", async () => {
    const pivot = makePivot("stale-report")
    completeStoredResult(pivot)
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: "refresh-report-job",
      cached: false,
      message: "Refreshing Pivot",
      result: null,
      failure: null,
    })

    renderPane([pivot], makeReport("explore_dataset:replacement"))

    await waitFor(() => expect(mockRunExplorePivot).toHaveBeenCalledTimes(1))
    expect(screen.getByText("Refreshing Pivot")).toBeVisible()
    expect(
      screen.getByRole("table", { name: `${pivot.name} results` }),
    ).toBeVisible()
  })

  it("shows submitting state until the automatic request resolves", async () => {
    const pivot = makePivot("submitting")
    let resolveRun!: (response: ExplorePivotRunResponse) => void
    mockRunExplorePivot.mockReturnValueOnce(
      new Promise<ExplorePivotRunResponse>((resolve) => {
        resolveRun = resolve
      }),
    )

    renderPane([pivot])

    expect(await screen.findByText("Starting calculation")).toBeVisible()
    expect(
      screen.queryByRole("button", { name: "Update" }),
    ).not.toBeInTheDocument()

    await act(async () => {
      resolveRun({
        status: "started",
        job_id: "submitting-job",
        cached: false,
        message: "Grouping claims",
        result: null,
        failure: null,
      })
    })

    expect(await screen.findByRole("button", { name: "Cancel" })).toBeVisible()
    expect(screen.getByRole("status")).toHaveTextContent("Grouping claims")
  })

  it("keeps a retained result visible while its refresh job is running", () => {
    const pivot = makePivot("refreshing")
    const key = completeStoredResult(pivot)
    act(() => {
      startStoredJob(pivot, "refresh-job")
      useNodeResultsStore.getState().updateExplorePivotProgress(key, {
        status: "running",
        progress: 0.4,
        message: "Grouping refreshed claims",
        result: null,
        failure: null,
        terminal_reason: null,
        execution_metrics: null,
      })
    })

    renderPane([pivot])

    expect(screen.getByRole("button", { name: "Cancel" })).toBeVisible()
    expect(screen.getByRole("status")).toHaveTextContent(
      "Grouping refreshed claims",
    )
    expect(screen.getByText("Current result")).toBeVisible()
    expect(
      screen.getByRole("table", { name: `${pivot.name} results` }),
    ).toBeVisible()
  })

  it("automatically starts each stale enabled Pivot exactly once", async () => {
    const first = makePivot("first")
    const second = makePivot("second")
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: "pivot-job-first",
      cached: false,
      message: "Pivot started",
      result: null,
      failure: null,
    })
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: "pivot-job-second",
      cached: false,
      message: "Pivot started",
      result: null,
      failure: null,
    })

    renderPane([first, second])

    const firstKey = explorePivotResultKey("explore_1", first.id)
    const secondKey = explorePivotResultKey("explore_1", second.id)
    await waitFor(() => {
      expect(mockRunExplorePivot).toHaveBeenCalledTimes(2)
      expect(useNodeResultsStore.getState().pivotJobs[firstKey]?.jobId).toBe(
        "pivot-job-first",
      )
      expect(useNodeResultsStore.getState().pivotJobs[secondKey]?.jobId).toBe(
        "pivot-job-second",
      )
    })
    await Promise.resolve()
    expect(mockRunExplorePivot).toHaveBeenCalledTimes(2)
  })

  it("stores an immediate cached result through the shared lifecycle", async () => {
    const pivot = makePivot("cached")
    const result = makeResult(pivot)
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "completed",
      job_id: null,
      cached: true,
      message: "Cache hit",
      result,
      failure: null,
    })

    renderPane([pivot])

    expect(await screen.findByText("Current result")).toBeVisible()
    const key = explorePivotResultKey("explore_1", pivot.id)
    expect(useNodeResultsStore.getState().pivotResults[key]?.result).toEqual(result)
    expect(useNodeResultsStore.getState().pivotJobs[key]).toBeUndefined()
  })

  it("persists cache-required remediation after the pane remounts", async () => {
    const pivot = makePivot("needs-cache")
    const failure = {
      reason_code: "cache_required",
      message: "Cache the Explore dataset first.",
      remediation: "Use Process & Cache Full Data.",
      dimensions: { node_id: "explore_1" },
    }
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "cache_required",
      job_id: null,
      cached: false,
      message: failure.message,
      result: null,
      failure,
    })
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "completed",
      job_id: null,
      cached: true,
      message: "Cache hit after retry",
      result: makeResult(pivot),
      failure: null,
    })

    const view = renderPane([pivot])

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Use Process & Cache Full Data.",
    )
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible()
    const key = explorePivotResultKey("explore_1", pivot.id)
    expect(useNodeResultsStore.getState().pivotResults[key]).toMatchObject({
      error: failure.message,
      terminalStatus: {
        status: "contract_error",
        failure,
      },
    })

    view.unmount()
    renderPane([pivot])
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Use Process & Cache Full Data.",
    )
    expect(mockRunExplorePivot).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("Current result")).toBeVisible()
    expect(mockRunExplorePivot).toHaveBeenCalledTimes(2)
  })

  it("automatically retries a failed calculation for a new cache identity", async () => {
    const pivot = makePivot("cache-changed-after-failure")
    const failure = {
      reason_code: "cache_required",
      message: "Cache the Explore dataset first.",
      remediation: "Use Process & Cache Full Data.",
      dimensions: { node_id: "explore_1" },
    }
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "cache_required",
      job_id: null,
      cached: false,
      message: failure.message,
      result: null,
      failure,
    })
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "completed",
      job_id: null,
      cached: true,
      message: "New cache calculated",
      result: makeResult(pivot, "explore_dataset:replacement"),
      failure: null,
    })

    const view = renderPane([pivot])
    expect(await screen.findByRole("alert")).toHaveTextContent(failure.message)
    expect(mockRunExplorePivot).toHaveBeenCalledTimes(1)

    const node = makeNode([pivot])
    view.rerender(
      <ExplorePivotsPane
        node={node}
        allNodes={[node]}
        edges={[]}
        submodels={{}}
        preamble=""
        report={makeReport("explore_dataset:replacement")}
      />,
    )

    expect(await screen.findByText("Current result")).toBeVisible()
    expect(mockRunExplorePivot).toHaveBeenCalledTimes(2)
  })

  it("keeps a successful sibling visible when another pivot has failed", () => {
    const failed = makePivot("failed")
    const successful = makePivot("successful")
    const failedKey = startStoredJob(failed, "failed-job")
    completeStoredResult(successful)
    act(() => {
      useNodeResultsStore
        .getState()
        .failExplorePivotJob(failedKey, "Failed to group claims")
    })

    renderPane([failed, successful])

    expect(
      within(screen.getByRole("region", { name: failed.name })).getByRole(
        "alert",
      ),
    ).toHaveTextContent("Failed to group claims")
    expect(
      within(screen.getByRole("region", { name: successful.name })).getByRole(
        "table",
        { name: `${successful.name} results` },
      ),
    ).toBeVisible()
  })

  it("retains a failed refresh and retries when the current report is unavailable", async () => {
    const pivot = makePivot("retained-refresh-failure")
    const key = completeStoredResult(pivot)
    const failure = {
      reason_code: "contract_error",
      message: "Refresh failed",
      remediation: "Correct the pivot and retry.",
      dimensions: { node_id: "explore_1" },
    }
    act(() => {
      startStoredJob(
        pivot,
        "failed-refresh-job",
        pivotCalculationIdentity(pivot),
        "explore_dataset:replacement",
      )
      useNodeResultsStore.getState().failExplorePivotJob(key, failure.message, {
        status: "contract_error",
        progress: 1,
        message: failure.message,
        result: null,
        failure,
        terminal_reason: "contract_error",
        execution_metrics: null,
      })
    })
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: "retry-without-report-job",
      cached: false,
      message: "Retrying Pivot",
      result: null,
      failure: null,
    })

    const view = renderPane(
      [pivot],
      makeReport("explore_dataset:replacement"),
    )

    expect(
      screen.getByText("Current result retained after the refresh failed."),
    ).toBeVisible()
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Correct the pivot and retry.",
    )

    const node = makeNode([pivot])
    view.rerender(
      <ExplorePivotsPane
        node={node}
        allNodes={[node]}
        edges={[]}
        submodels={{}}
        preamble=""
        report={null}
      />,
    )
    expect(screen.getByText("Waiting for current cached Explore data.")).toBeVisible()

    fireEvent.click(screen.getByRole("button", { name: "Retry" }))

    await waitFor(() => expect(mockRunExplorePivot).toHaveBeenCalledTimes(1))
    await waitFor(() => {
      expect(
        useNodeResultsStore.getState().pivotJobs[key]?.requestedDataframeCacheKey,
      ).toBeNull()
    })
  })

  it("reuses presentation edits and automatically recalculates calculation edits", async () => {
    const original = makePivot("identity")
    const key = startStoredJob(original, "completed-job")
    act(() => {
      useNodeResultsStore
        .getState()
        .completeExplorePivotJob(key, makeResult(original))
    })

    const renamed = {
      ...original,
      name: "Renamed pivot",
      values: [{ ...original.values[0], display_name: "Renamed Paid" }],
    }
    const view = renderPane([renamed])
    expect(screen.getByText("Current result")).toBeVisible()
    expect(mockRunExplorePivot).not.toHaveBeenCalled()

    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: "changed-calculation-job",
      cached: false,
      message: "Updating changed Pivot",
      result: null,
      failure: null,
    })

    view.rerender(
      <ExplorePivotsPane
        node={makeNode([
          { ...renamed, rows: [{ ...renamed.rows[0], field: "territory" }] },
        ])}
        allNodes={[
          makeNode([
            { ...renamed, rows: [{ ...renamed.rows[0], field: "territory" }] },
          ]),
        ]}
        edges={[]}
        submodels={{}}
        preamble=""
        report={makeReport()}
      />,
    )
    await waitFor(() => expect(mockRunExplorePivot).toHaveBeenCalledTimes(1))
    expect(screen.getByText("Updating changed Pivot")).toBeVisible()
  })

  it("cancels only the selected pivot job", async () => {
    const first = makePivot("first")
    const second = makePivot("second")
    const firstKey = startStoredJob(first, "job-first")
    const secondKey = startStoredJob(second, "job-second")
    const cancelled: ExplorePivotStatusResponse = {
      status: "cancelled",
      progress: 1,
      message: "Cancelled",
      result: null,
      failure: null,
      terminal_reason: "cancelled",
      execution_metrics: null,
    }
    mockCancelExplorePivot.mockResolvedValueOnce(cancelled)

    renderPane([first, second])
    fireEvent.click(
      within(screen.getByRole("region", { name: first.name })).getByRole(
        "button",
        { name: "Cancel" },
      ),
    )

    await waitFor(() => {
      expect(useNodeResultsStore.getState().pivotJobs[firstKey]).toBeUndefined()
    })
    expect(mockCancelExplorePivot).toHaveBeenCalledWith("job-first")
    expect(useNodeResultsStore.getState().pivotJobs[secondKey]).toBeDefined()
  })

  it("keeps polling and shows a card-local notice when cancellation fails", async () => {
    const pivot = makePivot("cancel-failed")
    const key = startStoredJob(pivot, "job-cancel-failed")
    mockCancelExplorePivot.mockRejectedValueOnce(
      new Error("Cancellation service unavailable"),
    )

    renderPane([pivot])
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Cancellation service unavailable",
    )
    expect(useNodeResultsStore.getState().pivotJobs[key]).toBeDefined()
    expect(screen.getByRole("button", { name: "Cancel" })).toBeVisible()
  })
})
