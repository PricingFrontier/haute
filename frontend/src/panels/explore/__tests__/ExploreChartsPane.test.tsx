import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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
import ExploreChartsPane from "../ExploreChartsPane"
import {
  createExploreChart,
  seedValueEncodings,
  type ExploreChartConfig,
} from "../chartConfig"
import type { PivotChartData } from "../chartData"
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

vi.mock("../ComboChart", () => ({
  default: ({
    chart,
    data,
  }: {
    chart: ExploreChartConfig
    data: PivotChartData
  }) => (
    <div data-testid="combo-chart">
      {chart.name}: {data.categories.length} categories, {data.series.length} series
    </div>
  ),
}))

function pivot(
  id: string,
  overrides: Partial<ExplorePivotConfig> = {},
): ExplorePivotConfig {
  return {
    version: 1,
    id,
    name: `Pivot ${id}`,
    enabled: true,
    filters: [],
    columns: [],
    rows: [{ id: `${id}_row`, field: "region" }],
    values: [
      {
        id: `${id}_paid`,
        field: "paid",
        aggregation: "sum",
        display_name: "Paid",
      },
    ],
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
  }
}

function chart(
  id: string,
  sourcePivot: ExplorePivotConfig | null,
  overrides: Partial<ExploreChartConfig> = {},
): ExploreChartConfig {
  return {
    ...createExploreChart([]),
    id,
    name: `Chart ${id}`,
    pivot_id: sourcePivot?.id ?? null,
    value_encodings: sourcePivot ? seedValueEncodings(sourcePivot) : [],
    ...overrides,
  }
}

function node(
  pivots: ExplorePivotConfig[],
  charts: ExploreChartConfig[],
): SimpleNode {
  return {
    id: "explore_1",
    type: "explore",
    data: {
      label: "Explore Claims",
      description: "",
      nodeType: "explore",
      config: { pivots, charts },
    },
  }
}

function report(dataframeCacheKey = "dataframe-current"): ExploreCacheReport {
  return {
    status: "ok",
    node_id: "explore_1",
    upstream_node_id: "source_1",
    source: "pricing",
    dataframe_cache_key: dataframeCacheKey,
    row_count: 1,
    column_count: 1,
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

function result(
  sourcePivot: ExplorePivotConfig,
  dataframeCacheKey = "dataframe-current",
): ExplorePivotResult {
  return {
    version: 1,
    node_id: "explore_1",
    pivot_id: sourcePivot.id,
    source: "pricing",
    dataframe_cache_key: dataframeCacheKey,
    calculation_key: "calculation-current",
    row_fields: sourcePivot.rows.map(({ field }) => field),
    column_fields: sourcePivot.columns.map(({ field }) => field),
    values: sourcePivot.values.map(({ id, field, aggregation }) => ({
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
    cells: sourcePivot.values.map(({ id }, index) => ({
      row_index: 0,
      column_index: 0,
      value_id: id,
      value: index + 42,
    })),
    warnings: [],
    generated_at: 1,
    execution_metrics: null,
  }
}

function seedResult(
  sourcePivot: ExplorePivotConfig,
  sourceResult = result(sourcePivot),
) {
  const key = explorePivotResultKey("explore_1", sourcePivot.id)
  useNodeResultsStore.getState().startExplorePivotJob(
    key,
    "pivot-completed",
    "explore_1",
    sourcePivot.id,
    "Explore Claims",
    sourcePivot.name,
    pivotCalculationIdentity(sourcePivot),
    "pricing",
    0,
  )
  act(() => {
    useNodeResultsStore.getState().completeExplorePivotJob(key, sourceResult)
  })
  return key
}

function renderPane(
  pivots: ExplorePivotConfig[],
  charts: ExploreChartConfig[],
  cacheReport: ExploreCacheReport | null = report(),
) {
  const exploreNode = node(pivots, charts)
  return render(
    <ExploreChartsPane
      node={exploreNode}
      allNodes={[exploreNode]}
      edges={[]}
      submodels={{}}
      preamble=""
      report={cacheReport}
    />,
  )
}

describe("ExploreChartsPane", () => {
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

  it("distinguishes no cards, all hidden, draft, and missing sources", () => {
    const sourcePivot = pivot("source")
    const empty = renderPane([], [])
    expect(screen.getByText(/add a chart from the Charts settings pane/i)).toBeVisible()

    empty.rerender(
      <ExploreChartsPane
        node={node([sourcePivot], [chart("hidden", sourcePivot, { enabled: false })])}
        allNodes={[]}
        edges={[]}
        report={report()}
      />,
    )
    expect(screen.getByText(/no charts are currently shown/i)).toBeVisible()

    empty.rerender(
      <ExploreChartsPane
        node={node(
          [sourcePivot],
          [
            chart("draft", null),
            chart("missing", null, { pivot_id: "removed_pivot" }),
          ],
        )}
        allNodes={[]}
        edges={[]}
        report={report()}
      />,
    )
    expect(
      within(screen.getByRole("region", { name: "Chart draft" })).getByText(
        /select a source pivot/i,
      ),
    ).toBeVisible()
    expect(
      within(screen.getByRole("region", { name: "Chart missing" })).getByRole(
        "alert",
      ),
    ).toHaveTextContent(/removed_pivot.*no longer exists/i)
  })

  it("renders two charts from one fresh hidden Pivot result without recalculating", () => {
    const sourcePivot = pivot("shared", { enabled: false })
    seedResult(sourcePivot)

    renderPane(
      [sourcePivot],
      [chart("one", sourcePivot), chart("two", sourcePivot)],
    )

    expect(screen.getAllByTestId("combo-chart")).toHaveLength(2)
    expect(screen.getByTestId("explore-chart-grid")).toHaveStyle({
      gridTemplateColumns:
        "repeat(auto-fit, minmax(min(100%, 28rem), 1fr))",
    })
    expect(mockRunExplorePivot).not.toHaveBeenCalled()
  })

  it("marks a retained result stale and delegates Update only to its source Pivot", async () => {
    const first = pivot("first")
    const second = pivot("second")
    seedResult(first, result(first, "dataframe-old"))
    seedResult(second)
    mockRunExplorePivot.mockResolvedValueOnce({
      status: "started",
      job_id: "pivot-refresh-first",
      cached: false,
      message: "Pivot started",
      result: null,
      failure: null,
    })

    renderPane(
      [first, second],
      [chart("first", first), chart("second", second)],
    )

    const firstCard = screen.getByRole("region", { name: "Chart first" })
    expect(within(firstCard).getByText(/out of date/i)).toBeVisible()
    expect(
      within(screen.getByRole("region", { name: "Chart second" })).getByTestId(
        "combo-chart",
      ),
    ).toBeVisible()

    fireEvent.click(within(firstCard).getByRole("button", { name: "Update" }))
    await waitFor(() => expect(mockRunExplorePivot).toHaveBeenCalledTimes(1))
    expect(mockRunExplorePivot.mock.calls[0][0]).toMatchObject({
      node_id: "explore_1",
      pivot: first,
    })
    expect(
      useNodeResultsStore.getState().pivotJobs[
        explorePivotResultKey("explore_1", first.id)
      ]?.jobId,
    ).toBe("pivot-refresh-first")
  })

  it("cancels a running source calculation from only the selected chart", async () => {
    const sourcePivot = pivot("running")
    const key = explorePivotResultKey("explore_1", sourcePivot.id)
    useNodeResultsStore.getState().startExplorePivotJob(
      key,
      "pivot-running",
      "explore_1",
      sourcePivot.id,
      "Explore Claims",
      sourcePivot.name,
      pivotCalculationIdentity(sourcePivot),
      "pricing",
      0,
    )
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

    renderPane([sourcePivot], [chart("running", sourcePivot)])
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))

    await waitFor(() =>
      expect(useNodeResultsStore.getState().pivotJobs[key]).toBeUndefined(),
    )
    expect(mockCancelExplorePivot).toHaveBeenCalledWith("pivot-running")
  })

  it("keeps an adapter error local while a successful sibling still renders", () => {
    const sourcePivot = pivot("shared")
    seedResult(sourcePivot)
    const broken = chart("broken", sourcePivot, { value_encodings: [] })
    const good = chart("good", sourcePivot)

    renderPane([sourcePivot], [broken, good])

    expect(
      within(screen.getByRole("region", { name: "Chart broken" })).getByRole(
        "alert",
      ),
    ).toHaveTextContent(/explicit encoding/i)
    expect(
      within(screen.getByRole("region", { name: "Chart good" })).getByTestId(
        "combo-chart",
      ),
    ).toBeVisible()
  })
})
