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

function renderPane(pivots: ExplorePivotConfig[], report = makeReport()) {
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
  )
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

  it("does not calculate automatically and starts only the selected card", async () => {
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

    renderPane([first, second])
    expect(mockRunExplorePivot).not.toHaveBeenCalled()

    const firstCard = screen.getByRole("region", { name: first.name })
    fireEvent.click(within(firstCard).getByRole("button", { name: "Update" }))

    const firstKey = explorePivotResultKey("explore_1", first.id)
    const secondKey = explorePivotResultKey("explore_1", second.id)
    await waitFor(() => {
      expect(useNodeResultsStore.getState().pivotJobs[firstKey]?.jobId).toBe(
        "pivot-job-first",
      )
    })
    expect(useNodeResultsStore.getState().pivotJobs[secondKey]).toBeUndefined()
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
    fireEvent.click(screen.getByRole("button", { name: "Update" }))

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

    const view = renderPane([pivot])
    fireEvent.click(screen.getByRole("button", { name: "Update" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Use Process & Cache Full Data.",
    )
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
  })

  it("keeps presentation-only edits fresh while calculation edits are stale", () => {
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
    expect(
      screen.getByText("Result is out of date. Update to recalculate it."),
    ).toBeVisible()
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
})
