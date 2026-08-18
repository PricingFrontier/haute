import { useState } from "react"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { ExplorePivotResult } from "../../api/types"
import ExploreChartsConfig from "../../panels/editors/ExploreChartsConfig"
import { GraphProvider } from "../../panels/GraphContext"
import type { SimpleNode } from "../../panels/editors"

const mockRunExplorePivot = vi.fn()
const mockCancelExplorePivot = vi.fn()

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  runExplorePivot: (...args: unknown[]) => mockRunExplorePivot(...args),
  cancelExplorePivot: (...args: unknown[]) => mockCancelExplorePivot(...args),
}))
import type { OnUpdateConfig } from "../../panels/editors/_shared"
import {
  createExploreChart,
  exploreChartSeriesKey,
  parseExploreCharts,
  type ExploreChartConfig,
} from "../../panels/explore/chartConfig"
import {
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "../../panels/explore/pivotConfig"
import useNodeResultsStore, {
  explorePivotResultKey,
  resetNodeResultsDerivedCaches,
} from "../../stores/useNodeResultsStore"
import useUIStore from "../../stores/useUIStore"

afterEach(cleanup)

function pivot(
  id: string,
  overrides: Partial<ExplorePivotConfig> = {},
): ExplorePivotConfig {
  const values = overrides.values ?? [
    {
      id: `${id}-paid`,
      field: "paid",
      aggregation: "sum",
      reference: "paid_sum",
      display_name: "Paid",
    },
    {
      id: `${id}-count`,
      field: "claim_id",
      aggregation: "count",
      reference: "claim_id_count",
      display_name: "Claims",
    },
  ]
  const formulas = overrides.formulas ?? []
  return {
    version: 1,
    id,
    name: `Pivot ${id}`,
    enabled: true,
    filters: [],
    columns: [],
    rows: [{ id: `${id}-row`, field: "region" }],
    values,
    formulas,
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
    value_order: overrides.value_order ?? [...values, ...formulas].map(({ id }) => id),
  }
}

function chart(
  sourcePivot: ExplorePivotConfig | null = null,
  overrides: Partial<ExploreChartConfig> = {},
): ExploreChartConfig {
  const draft = createExploreChart([])
  return {
    ...draft,
    ...(sourcePivot
      ? {
          pivot_id: sourcePivot.id,
          value_encodings: sourcePivot.values.map((value, index) => ({
            id: `encoding_${index + 1}`,
            value_id: value.id,
            mark: "column" as const,
            axis: "primary" as const,
            stack_group: null,
            stack_normalize: false,
            color: null,
            data_labels: false,
            markers: false,
          })),
        }
      : {}),
    ...overrides,
  }
}

function result(sourcePivot: ExplorePivotConfig): ExplorePivotResult {
  return {
    version: 1,
    node_id: "explore_1",
    pivot_id: sourcePivot.id,
    source: "pricing",
    dataframe_cache_key: "dataframe-current",
    calculation_key: "backend-calculation",
    row_fields: ["region"],
    column_fields: [],
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
    cells: sourcePivot.values.map((value, index) => ({
      row_index: 0,
      column_index: 0,
      value_id: value.id,
      value: index + 1,
    })),
    warnings: [],
    generated_at: 1,
    execution_metrics: null,
  }
}

function seedFreshPivot(sourcePivot: ExplorePivotConfig) {
  const key = explorePivotResultKey("explore_1", sourcePivot.id)
  useNodeResultsStore.getState().startExplorePivotJob(
    key,
    "pivot-job",
    "explore_1",
    sourcePivot.id,
    "Explore Claims",
    sourcePivot.name,
    pivotCalculationIdentity(sourcePivot),
    "pricing",
    0,
  )
  useNodeResultsStore.getState().completeExplorePivotJob(key, result(sourcePivot))
  useNodeResultsStore.setState({
    exploreResults: {
      explore_1: {
        result: {
          status: "ok",
          node_id: "explore_1",
          upstream_node_id: "source_1",
          source: "pricing",
          dataframe_cache_key: "dataframe-current",
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
        },
        jobId: "explore-job",
        configHash: "hash",
        source: "pricing",
        structuralVersion: 0,
        nodeLabel: "Explore Claims",
      },
    },
  })
}

function ChartConfigHarness({
  initialConfig = {},
  currentConfigHash = "hash",
  onCommittedUpdate,
  onShowPivots,
}: {
  initialConfig?: Record<string, unknown>
  currentConfigHash?: string | null
  onCommittedUpdate?: () => void
  onShowPivots?: () => void
}) {
  const [config, setConfig] = useState(initialConfig)
  const onUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
    setConfig((current) =>
      typeof keyOrUpdates === "string"
        ? { ...current, [keyOrUpdates]: value }
        : { ...current, ...keyOrUpdates },
    )
    onCommittedUpdate?.()
    return { ok: true }
  }

  const exploreNode: SimpleNode = {
    id: "explore_1",
    type: "explore",
    data: {
      label: "Explore Claims",
      description: "",
      nodeType: "explore",
      config,
    },
  }
  return (
    <GraphProvider allNodes={[exploreNode]} edges={[]} submodels={{}} preamble="">
      <ExploreChartsConfig
        config={config}
        onUpdate={onUpdate}
        nodeId="explore_1"
        currentConfigHash={currentConfigHash}
        onShowPivots={onShowPivots}
      />
      <output data-testid="persisted-config">{JSON.stringify(config)}</output>
    </GraphProvider>
  )
}

describe("ExploreChartsConfig", () => {
  beforeEach(() => {
    resetNodeResultsDerivedCaches()
    useNodeResultsStore.setState({
      pivotResults: {},
      pivotJobs: {},
      exploreResults: {},
    })
    useUIStore.setState({
      exploreConfiguredChartIds: {},
      exploreConfiguredPivotIds: {},
      explorePreviewPanes: {},
      explorePanes: {},
    })
    useNodeResultsStore.setState({ pivotStartClaims: {} })
    mockRunExplorePivot.mockReset()
    mockCancelExplorePivot.mockReset()
  })

  it("keeps chart Configure to formatting only, with no pivot field editing", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    // Pivot structure is edited in the Pivots editor; the chart view carries
    // no field well, field summary, or disclosure box.
    expect(screen.queryByText(/Fields are shared/i)).not.toBeInTheDocument()
    expect(
      screen.queryByRole("searchbox", { name: "Search pivot fields" }),
    ).not.toBeInTheDocument()
    expect(screen.queryByTestId("pivot-field-areas")).not.toBeInTheDocument()
  })

  it("automatically refreshes a stale hidden source from Configure alone, once per target", async () => {
    const sourcePivot = pivot("source", { enabled: false })
    const configured = chart(sourcePivot)
    seedFreshPivot(sourcePivot)
    // Keep the retained Explore report but drop the pivot result: the open
    // Configure view alone must schedule the refresh.
    useNodeResultsStore.setState({ pivotResults: {}, pivotJobs: {} })
    mockRunExplorePivot.mockImplementation(
      async ({ pivot: requested }: { pivot: ExplorePivotConfig }) => ({
        status: "completed",
        job_id: `auto-${mockRunExplorePivot.mock.calls.length}`,
        cached: true,
        message: "Completed",
        result: {
          ...result(requested),
          row_fields: requested.rows.map(({ field }) => field),
        },
        failure: null,
      }),
    )

    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    await waitFor(() => expect(mockRunExplorePivot).toHaveBeenCalledTimes(1))
    expect(mockRunExplorePivot.mock.calls[0][0].pivot.id).toBe(sourcePivot.id)
    await waitFor(() =>
      expect(
        useNodeResultsStore.getState().pivotResults[
          explorePivotResultKey("explore_1", sourcePivot.id)
        ]?.result,
      ).toBeTruthy(),
    )
    expect(mockRunExplorePivot).toHaveBeenCalledTimes(1)
  })

  it("never shows a status suffix in the source picker", () => {
    const readyPivot = pivot("source")
    const notCalculated = pivot("uncalc")
    const configured = chart(readyPivot)
    seedFreshPivot(readyPivot)
    render(
      <ChartConfigHarness
        initialConfig={{
          charts: [configured],
          pivots: [readyPivot, notCalculated],
        }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    const source = screen.getByRole("combobox", { name: "Source pivot" })
    // Accessible-name lookup is a full match: any status suffix would fail.
    expect(
      within(source).getByRole("option", { name: readyPivot.name }),
    ).toBeInTheDocument()
    expect(
      within(source).getByRole("option", { name: notCalculated.name }),
    ).toBeInTheDocument()
  })

  it("groups axis formatting into Primary and Secondary boxes with a use-secondary tickbox", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    configured.value_encodings = configured.value_encodings.map(
      (encoding, index) =>
        index === 1 ? { ...encoding, axis: "secondary" as const } : encoding,
    )
    configured.axes = {
      ...configured.axes,
      secondary: {
        title: "Avg",
        minimum: 0,
        maximum: 5,
        number_format: "currency_gbp",
        enabled: true,
      },
    }
    configured.legend = { visible: true, position: "left" }
    const onCommittedUpdate = vi.fn()
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    // Requested ordering: gallery → orientation → axes → Value boxes.
    const expectBefore = (first: Element, second: Element) => {
      expect(
        first.compareDocumentPosition(second) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
    }
    const gallery = screen.getByRole("group", { name: "Chart type" })
    const orientation = screen.getByRole("group", { name: "Orientation" })
    const primaryBox = screen.getByRole("group", { name: "Primary axis" })
    const secondaryBox = screen.getByRole("group", { name: "Secondary axis" })
    const legendBox = screen.getByRole("group", { name: "Legend" })
    const firstValueControl = screen.getByRole("combobox", {
      name: "Chart type for Paid",
    })
    expectBefore(gallery, orientation)
    expectBefore(orientation, primaryBox)
    expectBefore(primaryBox, secondaryBox)
    expectBefore(secondaryBox, legendBox)
    expectBefore(legendBox, firstValueControl)

    // The Legend box is gated by its Show legend tickbox; toggling preserves
    // the (non-default) position in both directions.
    expect(
      within(legendBox).getByRole("combobox", { name: "Legend position" }),
    ).toHaveValue("left")
    fireEvent.click(
      within(legendBox).getByRole("checkbox", { name: "Show legend" }),
    )
    expect(
      within(legendBox).queryByRole("combobox", { name: "Legend position" }),
    ).not.toBeInTheDocument()
    expect(
      JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
        .charts[0].legend,
    ).toEqual({ visible: false, position: "left" })
    fireEvent.click(
      within(legendBox).getByRole("checkbox", { name: "Show legend" }),
    )
    expect(
      JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
        .charts[0].legend,
    ).toEqual({ visible: true, position: "left" })
    expect(
      within(legendBox).getByRole("combobox", { name: "Legend position" }),
    ).toHaveValue("left")

    const tickbox = within(secondaryBox).getByRole("checkbox", {
      name: "Use secondary axis",
    })
    expect(tickbox).toBeChecked()
    expect(
      within(secondaryBox).getByRole("combobox", {
        name: "Secondary number format",
      }),
    ).toBeVisible()

    // Unticking is ONE committed edit that disables the axis, hides its
    // fields, moves the secondary series back to primary, and removes the
    // Secondary option per series — while retaining the axis formatting.
    const commitsBeforeToggle = onCommittedUpdate.mock.calls.length
    fireEvent.click(tickbox)
    expect(onCommittedUpdate).toHaveBeenCalledTimes(commitsBeforeToggle + 1)
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].axes.secondary).toEqual({
      title: "Avg",
      minimum: 0,
      maximum: 5,
      number_format: "currency_gbp",
      enabled: false,
    })
    expect(
      persisted.charts[0].value_encodings.every(
        (encoding: { axis: string }) => encoding.axis === "primary",
      ),
    ).toBe(true)
    expect(parseExploreCharts({ charts: persisted.charts }).ok).toBe(true)
    expect(
      within(
        screen.getByRole("group", { name: "Secondary axis" }),
      ).queryByRole("combobox", { name: "Secondary number format" }),
    ).not.toBeInTheDocument()
    const axisSelect = screen.getByRole("combobox", { name: "Axis for Paid" })
    expect(
      within(axisSelect).queryByRole("option", { name: "Secondary" }),
    ).not.toBeInTheDocument()

    // Re-ticking is one edit that re-enables the axis with formatting
    // intact and without touching series assignments.
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Use secondary axis" }),
    )
    expect(onCommittedUpdate).toHaveBeenCalledTimes(commitsBeforeToggle + 2)
    const reEnabled = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(reEnabled.charts[0].axes.secondary).toEqual({
      title: "Avg",
      minimum: 0,
      maximum: 5,
      number_format: "currency_gbp",
      enabled: true,
    })
    expect(
      reEnabled.charts[0].value_encodings.every(
        (encoding: { axis: string }) => encoding.axis === "primary",
      ),
    ).toBe(true)
  })

  it("moves a secondary override to primary when disabling and hides its Secondary option", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    configured.series_overrides = [
      {
        id: "override_1",
        series_key: exploreChartSeriesKey(`${sourcePivot.id}-paid`, []),
        mark: "line" as const,
        axis: "secondary" as const,
        stack_group: "s",
        stack_normalize: false,
        color: null,
        data_labels: false,
        markers: true,
      },
    ]
    seedFreshPivot(sourcePivot)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Use secondary axis" }),
    )
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].series_overrides[0]).toMatchObject({
      axis: "primary",
      stack_group: null,
      stack_normalize: false,
    })
    expect(parseExploreCharts({ charts: persisted.charts }).ok).toBe(true)

    fireEvent.click(
      screen.getByRole("button", { name: "Series overrides for Paid" }),
    )
    const overrideAxisSelect = screen.getByRole("combobox", {
      name: "Axis for Paid exact series",
    })
    expect(overrideAxisSelect).toHaveValue("primary")
    expect(
      within(overrideAxisSelect).queryByRole("option", { name: "Secondary" }),
    ).not.toBeInTheDocument()
  })

  it("hides series overrides when each Value yields a single series", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    seedFreshPivot(sourcePivot)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    // Without a Columns split, the Value boxes ARE the series config: no
    // override section is rendered at all.
    expect(
      screen.queryByRole("button", { name: /Series overrides for/ }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /^Override / }),
    ).not.toBeInTheDocument()
  })

  it("keeps a pre-existing override reachable on a single-series Value", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    configured.series_overrides = [
      {
        id: "override_1",
        series_key: exploreChartSeriesKey(`${sourcePivot.id}-paid`, []),
        mark: "column" as const,
        axis: "primary" as const,
        stack_group: null,
        stack_normalize: false,
        color: "#112233",
        data_labels: false,
        markers: false,
      },
    ]
    seedFreshPivot(sourcePivot)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    // The override keeps its disclosure reachable even without Columns; the
    // override-free single-series Value shows none.
    expect(
      screen.queryByRole("button", { name: "Series overrides for Claims" }),
    ).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Series overrides for Paid" }),
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Reset Paid to Value default" }),
    )
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].series_overrides).toEqual([])
  })

  it("stores the configured chart without touching the preview, and clears on Back", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    // Seed a non-default preview pane so the untouched assertions below prove
    // Configure/Back leave it alone rather than clearing it.
    useUIStore.setState({ explorePreviewPanes: { explore_1: "overview" } })
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )

    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )
    expect(
      useUIStore.getState().exploreConfiguredChartIds.explore_1,
    ).toBe(configured.id)
    // Editor-side navigation never changes the preview pane.
    expect(
      useUIStore.getState().explorePreviewPanes.explore_1,
    ).toBe("overview")

    fireEvent.click(screen.getByRole("button", { name: "Back to charts" }))
    expect(
      useUIStore.getState().exploreConfiguredChartIds.explore_1,
    ).toBeNull()
    expect(
      useUIStore.getState().explorePreviewPanes.explore_1,
    ).toBe("overview")
  })

  it("self-clears a stored configured id whose chart no longer exists", () => {
    useUIStore.setState({
      exploreConfiguredChartIds: { explore_1: "chart_ghost" },
    })
    render(<ChartConfigHarness initialConfig={{ charts: [], pivots: [] }} />)

    expect(screen.getByRole("button", { name: "Add Chart" })).toBeVisible()
    expect(
      useUIStore.getState().exploreConfiguredChartIds.explore_1,
    ).toBeNull()
  })

  it("adds complete drafts and keeps toggle separate from Configure and Back", () => {
    render(<ChartConfigHarness />)

    fireEvent.click(screen.getByRole("button", { name: "Add Chart" }))
    fireEvent.click(screen.getByRole("button", { name: "Add Chart" }))

    expect(screen.getByRole("group", { name: "Chart 1" })).toBeInTheDocument()
    expect(screen.getByRole("group", { name: "Chart 2" })).toBeInTheDocument()
    expect(screen.getAllByTestId("explore-toggle-card")).toHaveLength(2)
    expect(screen.getByRole("checkbox", { name: "Chart 1" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.getByRole("checkbox", { name: "Chart 2" })).toHaveAttribute(
      "aria-checked",
      "true",
    )

    fireEvent.click(screen.getByRole("button", { name: "Configure Chart 1" }))
    expect(
      screen.getByRole("heading", { name: "Configure Chart 1" }),
    ).toBeVisible()
    expect(screen.queryByRole("checkbox", { name: "Chart 1" })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Back to charts" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Chart 2" }))
    expect(screen.getByRole("checkbox", { name: "Chart 1" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.getByRole("checkbox", { name: "Chart 2" })).toHaveAttribute(
      "aria-checked",
      "false",
    )

    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0]).toMatchObject({
      version: 1,
      id: "chart_1",
      name: "Chart 1",
      enabled: true,
      pivot_id: null,
      kind: "combo",
      value_encodings: [],
      series_overrides: [],
      category: {
        source: "rows",
        include_grand_total: false,
        label_rotation: 0,
      },
    })
  })

  it("removes a chart only after explicit confirmation", () => {
    const first = chart(null, { id: "chart_1", name: "Chart 1" })
    const second = chart(null, { id: "chart_2", name: "Chart 2" })
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false)
    render(
      <ChartConfigHarness initialConfig={{ charts: [first, second] }} />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Delete Chart 1" }))
    expect(screen.getByRole("button", { name: "Configure Chart 1" })).toBeVisible()

    confirm.mockReturnValueOnce(true)
    fireEvent.click(screen.getByRole("button", { name: "Delete Chart 1" }))
    expect(screen.queryByRole("button", { name: "Configure Chart 1" })).toBeNull()
    expect(screen.getByRole("button", { name: "Configure Chart 2" })).toBeVisible()
    confirm.mockRestore()
  })

  it("guides an empty chart workflow to Pivots without mutating config", () => {
    const onShowPivots = vi.fn()
    const draft = chart()
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [draft], pivots: [] }}
        onShowPivots={onShowPivots}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: `Configure ${draft.name}` }))
    expect(screen.getByText(/requires a pivot/i)).toBeVisible()
    fireEvent.click(screen.getByRole("button", { name: /go to pivots/i }))

    expect(onShowPivots).toHaveBeenCalledTimes(1)
    expect(JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")).toEqual({
      charts: [draft],
      pivots: [],
    })
  })

  it("lists hidden pivots, seeds mappings, and exposes inherited fields", () => {
    const hidden = pivot("hidden", { enabled: false })
    const draft = chart()
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [draft], pivots: [hidden] }}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: `Configure ${draft.name}` }))
    const source = screen.getByRole("combobox", { name: "Source pivot" })
    expect(within(source).getByRole("option", { name: /Hidden/i })).toBeVisible()
    fireEvent.change(source, { target: { value: hidden.id } })

    expect(screen.queryByTestId("pivot-field-areas")).not.toBeInTheDocument()
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].pivot_id).toBe(hidden.id)
    // Source selection seeds the Combo default: columns + trailing line.
    expect(
      persisted.charts[0].value_encodings.map(
        ({ mark, markers }: { mark: string; markers: boolean }) => ({
          mark,
          markers,
        }),
      ),
    ).toEqual([
      { mark: "column", markers: false },
      { mark: "line", markers: true },
    ])
    expect(persisted.charts[0].series_overrides).toEqual([])
  })

  it("reconciles a pivot Value added after chart creation and persists on the next commit", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    const grown = pivot("source", {
      values: [
        ...sourcePivot.values,
        {
          id: "source-rate",
          field: "rate",
          aggregation: "average",
          reference: "rate_mean",
          display_name: "Rate",
        },
      ],
    })
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [grown] }}
      />,
    )

    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    expect(screen.queryByText(/Missing encoding/i)).not.toBeInTheDocument()
    expect(
      screen.getByText("New Value from the source Pivot — defaults applied."),
    ).toBeVisible()
    const seededMark = screen.getByRole("combobox", {
      name: "Chart type for Rate",
    })
    expect(seededMark).toHaveValue("column")
    expect(
      JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
        .charts[0].value_encodings,
    ).toHaveLength(2)

    fireEvent.change(seededMark, { target: { value: "line" } })
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].value_encodings).toHaveLength(3)
    expect(persisted.charts[0].value_encodings[2]).toMatchObject({
      id: "encoding_3",
      value_id: "source-rate",
      mark: "line",
    })
  })

  it("labels the inherit number format as General (automatic)", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )
    const format = screen.getByRole("combobox", {
      name: "Primary number format",
    })
    expect(
      within(format).getByRole("option", { name: "General (automatic)" }),
    ).toHaveValue("inherit")
  })

  it("confirms a populated source reset and commits it as one edit", () => {
    const first = pivot("first")
    const second = pivot("second", {
      values: [
        {
          id: "second-average",
          field: "paid",
          aggregation: "average",
          reference: "paid_mean",
          display_name: "Average paid",
        },
      ],
    })
    const configured = chart(first)
    const onCommittedUpdate = vi.fn()
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [first, second] }}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )

    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )
    const source = screen.getByRole("combobox", { name: "Source pivot" })
    fireEvent.change(source, { target: { value: second.id } })
    expect(source).toHaveValue(first.id)
    expect(onCommittedUpdate).not.toHaveBeenCalled()

    confirm.mockReturnValueOnce(true)
    fireEvent.change(source, { target: { value: second.id } })
    expect(onCommittedUpdate).toHaveBeenCalledTimes(1)
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0]).toMatchObject({
      pivot_id: second.id,
      series_overrides: [],
      value_encodings: [
        expect.objectContaining({ value_id: "second-average" }),
      ],
    })
    confirm.mockRestore()
  })

  it("composes a column + secondary line via per-field controls and commits axis, legend, and category settings", async () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    // Combo arrangements come from per-field chart type + axis, not presets.
    fireEvent.change(
      screen.getByRole("combobox", { name: "Chart type for Claims" }),
      { target: { value: "line" } },
    )
    fireEvent.change(screen.getByRole("combobox", { name: "Axis for Claims" }), {
      target: { value: "secondary" },
    })
    expect(
      screen.getByRole("combobox", { name: "Chart type for Claims" }),
    ).toHaveValue("line")
    expect(screen.getByRole("combobox", { name: "Axis for Claims" })).toHaveValue(
      "secondary",
    )
    // The composed state reads as the general Combo option in the gallery.
    expect(
      within(screen.getByRole("group", { name: "Chart type" })).getByRole(
        "button",
        { name: "Combo" },
      ),
    ).toHaveAttribute("aria-pressed", "true")

    fireEvent.click(screen.getByRole("checkbox", { name: "Data labels for Paid" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Legend position" }), {
      target: { value: "right" },
    })
    fireEvent.change(screen.getByRole("combobox", { name: "Category label rotation" }), {
      target: { value: "45" },
    })
    fireEvent.change(screen.getByRole("combobox", { name: "Primary number format" }), {
      target: { value: "currency_gbp" },
    })

    await waitFor(() => {
      const persisted = JSON.parse(
        screen.getByTestId("persisted-config").textContent ?? "{}",
      )
      expect(persisted.charts[0]).toMatchObject({
        category: expect.objectContaining({ label_rotation: 45 }),
        legend: expect.objectContaining({ position: "right" }),
        axes: {
          primary: expect.objectContaining({ number_format: "currency_gbp" }),
          secondary: expect.any(Object),
        },
      })
      expect(persisted.charts[0].value_encodings[0].data_labels).toBe(true)
    })
  })

  it("validates manual bounds and colours before committing complete values", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot, {
      axes: {
        primary: {
          title: "",
          minimum: null,
          maximum: 10,
          number_format: "inherit",
        },
        secondary: {
          title: "",
          minimum: null,
          maximum: null,
          number_format: "inherit",
          enabled: true,
        },
      },
    })
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    const minimum = screen.getByRole("spinbutton", { name: "Primary minimum" })
    fireEvent.change(minimum, { target: { value: "12" } })
    fireEvent.blur(minimum)
    expect(screen.getByRole("alert")).toHaveTextContent(/minimum must be less/i)
    let persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].axes.primary.minimum).toBeNull()

    fireEvent.change(minimum, { target: { value: "-5" } })
    fireEvent.blur(minimum)
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].axes.primary.minimum).toBe(-5)

    fireEvent.click(
      screen.getAllByRole("button", { name: /Colour #[0-9A-F]{6} for Paid/ })[0],
    )
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    const paletteColour: string = persisted.charts[0].value_encodings[0].color
    expect(paletteColour).toMatch(/^#[0-9A-F]{6}$/)

    // The native picker streams change events while dragging; only blur
    // commits, so a drag-in-progress persists nothing.
    const custom = screen.getByLabelText("Custom colour for Paid")
    fireEvent.change(custom, { target: { value: "#118822" } })
    fireEvent.change(custom, { target: { value: "#aabbcc" } })
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].value_encodings[0].color).toBe(paletteColour)
    fireEvent.blur(custom)
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].value_encodings[0].color).toBe("#AABBCC")

    fireEvent.click(
      screen.getByRole("button", { name: "Automatic colour for Paid" }),
    )
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].value_encodings[0].color).toBeNull()
  })

  it("shows the detected type, applies gallery presets, and preserves orientation", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot, { orientation: "horizontal" })
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    const gallery = screen.getByRole("group", { name: "Chart type" })
    // Combo sits leftmost as the default option, exactly one option is
    // pressed, and no separate Custom indicator exists.
    expect(
      within(gallery).getAllByRole("button")[0],
    ).toHaveAccessibleName("Combo")
    expect(
      within(gallery)
        .getAllByRole("button")
        .filter(
          (button) => button.getAttribute("aria-pressed") === "true",
        ),
    ).toHaveLength(1)
    expect(within(gallery).queryByText("Custom")).not.toBeInTheDocument()
    expect(
      within(gallery).getByRole("button", { name: "Clustered columns" }),
    ).toHaveAttribute("aria-pressed", "true")

    fireEvent.click(
      within(gallery).getByRole("button", { name: "100% stacked columns" }),
    )
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].orientation).toBe("horizontal")
    expect(
      persisted.charts[0].value_encodings.every(
        (encoding: { stack_group: string; stack_normalize: boolean }) =>
          encoding.stack_group === "stack_1" &&
          encoding.stack_normalize === true,
      ),
    ).toBe(true)
    expect(persisted.charts[0].axes.primary.number_format).toBe("percent")
    expect(
      within(gallery).getByRole("button", { name: "100% stacked columns" }),
    ).toHaveAttribute("aria-pressed", "true")
  })

  it("marks custom charts explicitly and toggles orientation", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    configured.value_encodings = configured.value_encodings.map(
      (encoding, index) =>
        index === 0 ? { ...encoding, mark: "area" as const } : encoding,
    )
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    const gallery = screen.getByRole("group", { name: "Chart type" })
    // An area-marked chart is outside the column layouts: Combo is pressed.
    expect(
      within(gallery).getByRole("button", { name: "Combo" }),
    ).toHaveAttribute("aria-pressed", "true")

    const orientation = screen.getByRole("group", { name: "Orientation" })
    expect(
      within(orientation).getByRole("button", { name: "Vertical columns" }),
    ).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(
      within(orientation).getByRole("button", { name: "Horizontal bars" }),
    )
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].orientation).toBe("horizontal")
  })

  it("drives stacking transitions and shows the group input only on multi-group charts", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    configured.value_encodings = configured.value_encodings.map(
      (encoding, index) =>
        index === 0
          ? { ...encoding, stack_group: "stack_1", stack_normalize: false }
          : encoding,
    )
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    expect(
      screen.queryByRole("textbox", { name: "Stack group for Paid" }),
    ).not.toBeInTheDocument()

    const stackState = () => {
      const persistedConfig = JSON.parse(
        screen.getByTestId("persisted-config").textContent ?? "{}",
      )
      expect(parseExploreCharts({ charts: persistedConfig.charts }).ok).toBe(
        true,
      )
      return {
        persisted: persistedConfig,
        stacks: persistedConfig.charts[0].value_encodings.map(
          (encoding: {
            stack_group: string | null
            stack_normalize: boolean
          }) => ({
            stack_group: encoding.stack_group,
            stack_normalize: encoding.stack_normalize,
          }),
        ),
      }
    }

    fireEvent.change(screen.getByRole("combobox", { name: "Stacking for Claims" }), {
      target: { value: "stacked" },
    })
    expect(stackState().stacks).toEqual([
      { stack_group: "stack_1", stack_normalize: false },
      { stack_group: "stack_1", stack_normalize: false },
    ])

    fireEvent.change(screen.getByRole("combobox", { name: "Stacking for Paid" }), {
      target: { value: "normalized" },
    })
    expect(stackState().stacks).toEqual([
      { stack_group: "stack_1", stack_normalize: true },
      { stack_group: "stack_1", stack_normalize: true },
    ])

    fireEvent.change(
      screen.getByRole("combobox", { name: "Stacking for Claims" }),
      { target: { value: "none" } },
    )
    expect(stackState().stacks).toEqual([
      { stack_group: "stack_1", stack_normalize: true },
      { stack_group: null, stack_normalize: false },
    ])

    fireEvent.change(screen.getByRole("combobox", { name: "Axis for Paid" }), {
      target: { value: "secondary" },
    })
    const final = stackState()
    expect(final.stacks).toEqual([
      { stack_group: null, stack_normalize: false },
      { stack_group: null, stack_normalize: false },
    ])
    expect(final.persisted.charts[0].value_encodings[0].axis).toBe("secondary")
  })

  it("renames stack groups atomically and rejects incompatible merges inline", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    configured.value_encodings = [
      {
        ...configured.value_encodings[0],
        stack_group: "stack_1",
        stack_normalize: false,
      },
      {
        ...configured.value_encodings[1],
        axis: "secondary" as const,
        stack_group: "other",
        stack_normalize: false,
      },
    ]
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    const groupInput = screen.getByRole("textbox", {
      name: "Stack group for Paid",
    })
    fireEvent.change(groupInput, { target: { value: "other" } })
    fireEvent.blur(groupInput)
    expect(screen.getByRole("alert")).toHaveTextContent(/merge only/i)
    let persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].value_encodings[0].stack_group).toBe("stack_1")

    fireEvent.change(groupInput, { target: { value: "actuarial" } })
    fireEvent.blur(groupInput)
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].value_encodings[0].stack_group).toBe("actuarial")
    expect(parseExploreCharts({ charts: persisted.charts }).ok).toBe(true)
  })

  it("uses Series vocabulary in every source-status message", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    const key = explorePivotResultKey("explore_1", sourcePivot.id)
    const openConfigure = () => {
      const view = render(
        <ChartConfigHarness
          initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
        />,
      )
      // The configured subview is per-node store state: the first mount opens
      // it via the card action, later mounts restore it automatically.
      const configureButton = screen.queryByRole("button", {
        name: `Configure ${configured.name}`,
      })
      if (configureButton) fireEvent.click(configureButton)
      expect(screen.queryByText(/concrete series/i)).not.toBeInTheDocument()
      return view
    }

    const notCalculated = openConfigure()
    expect(
      screen.getByText("Update the source Pivot to discover its series."),
    ).toBeVisible()
    notCalculated.unmount()

    useNodeResultsStore.getState().startExplorePivotJob(
      key,
      "pivot-loading",
      "explore_1",
      sourcePivot.id,
      "Explore Claims",
      sourcePivot.name,
      pivotCalculationIdentity(sourcePivot),
      "pricing",
      0,
    )
    const loading = openConfigure()
    expect(
      screen.getByText(
        "The source Pivot is updating. Series will refresh when it completes.",
      ),
    ).toBeVisible()
    loading.unmount()

    useNodeResultsStore.getState().failExplorePivotJob(key, "boom")
    const errored = openConfigure()
    expect(
      screen.getByText(
        "The source Pivot failed. Update it before configuring series overrides.",
      ),
    ).toBeVisible()
    errored.unmount()
  })

  it("merges compatible stack groups through a rename", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    configured.value_encodings = [
      {
        ...configured.value_encodings[0],
        stack_group: "stack_1",
        stack_normalize: false,
      },
      {
        ...configured.value_encodings[1],
        stack_group: "other",
        stack_normalize: false,
      },
    ]
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    const groupInput = screen.getByRole("textbox", {
      name: "Stack group for Paid",
    })
    fireEvent.change(groupInput, { target: { value: "other" } })
    fireEvent.blur(groupInput)
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(
      persisted.charts[0].value_encodings.map(
        (encoding: { stack_group: string | null }) => encoding.stack_group,
      ),
    ).toEqual(["other", "other"])
    expect(parseExploreCharts({ charts: persisted.charts }).ok).toBe(true)
  })

  it("names dormant formatting instead of internal ids", () => {
    const sourcePivot = pivot("source", {
      columns: [{ id: "source-year", field: "year" }],
    })
    const configured = chart(sourcePivot)
    configured.series_overrides = [
      {
        id: "override_1",
        series_key: JSON.stringify({
          version: 1,
          value_id: `${sourcePivot.id}-paid`,
          column_path: [{ kind: "integer", value: "2099" }],
        }),
        mark: "column" as const,
        axis: "primary" as const,
        stack_group: null,
        stack_normalize: false,
        color: null,
        data_labels: false,
        markers: false,
      },
    ]
    const pivotResult = result(sourcePivot)
    pivotResult.column_fields = ["year"]
    pivotResult.column_paths = [
      { members: [{ kind: "integer", value: "2024" }], is_grand_total: false },
    ]
    seedFreshPivot(sourcePivot)
    const key = explorePivotResultKey("explore_1", sourcePivot.id)
    useNodeResultsStore.setState((state) => ({
      pivotResults: {
        ...state.pivotResults,
        [key]: { ...state.pivotResults[key], result: pivotResult },
      },
    }))

    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    const dormant = screen.getByText(/kept for series not currently shown/i)
    expect(dormant).toHaveTextContent("2099 · Paid")
    expect(dormant).not.toHaveTextContent("override_1")
  })

  it("lists series and creates an exact override from a fresh result", () => {
    const sourcePivot = pivot("source", {
      columns: [{ id: "source-year", field: "year" }],
    })
    const configured = chart(sourcePivot)
    configured.value_encodings[0].id = "override_1"
    const pivotResult = result(sourcePivot)
    pivotResult.column_fields = ["year"]
    pivotResult.column_paths = [
      {
        members: [{ kind: "integer", value: "2024" }],
        is_grand_total: false,
      },
    ]
    seedFreshPivot(sourcePivot)
    const key = explorePivotResultKey("explore_1", sourcePivot.id)
    useNodeResultsStore.setState((state) => ({
      pivotResults: {
        ...state.pivotResults,
        [key]: { ...state.pivotResults[key], result: pivotResult },
      },
    }))

    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    // Series overrides nest under their Value box behind per-Value
    // disclosures, both collapsed initially.
    const paidDisclosure = screen.getByRole("button", {
      name: "Series overrides for Paid",
    })
    expect(paidDisclosure).toHaveAttribute("aria-expanded", "false")
    expect(
      screen.getByRole("button", { name: "Series overrides for Claims" }),
    ).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(paidDisclosure)
    expect(paidDisclosure).toHaveAttribute("aria-expanded", "true")
    // Expanding Paid exposes only Paid's series; Claims stays collapsed.
    expect(screen.getByText("2024 · Paid")).toBeVisible()
    expect(screen.queryByText("2024 · Claims")).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Override 2024 · Paid" }),
    )
    expect(screen.getByRole("group", { name: "Override 2024 · Paid" })).toBeVisible()
    let persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].series_overrides).toHaveLength(1)
    expect(persisted.charts[0].series_overrides[0].id).toBe("override_2")
    expect(persisted.charts[0].series_overrides[0].series_key).toBe(
      exploreChartSeriesKey(`${sourcePivot.id}-paid`, [
        { kind: "integer", value: "2024" },
      ]),
    )
    expect(persisted.charts[0].series_overrides[0]).not.toHaveProperty(
      "value_id",
    )

    fireEvent.click(
      screen.getByRole("button", {
        name: /Reset .* to Value default/,
      }),
    )
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].series_overrides).toEqual([])
    expect(
      screen.getByRole("button", { name: /Override .* Paid/ }),
    ).toBeVisible()
  })

  it("reports a retained result from a superseded identity as stale, never ready", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    seedFreshPivot(sourcePivot)

    // The retained Explore result was produced under configHash "hash"; the
    // node's current identity has moved on (an upstream edit or source
    // switch), so the pivot result must not be treated as current.
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
        currentConfigHash="hash-after-upstream-edit"
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    // The picker carries no status suffix even for a stale source; the state
    // is communicated by the message below.
    expect(
      screen.getByRole("option", { name: sourcePivot.name }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        "The source Pivot result is out of date. Update it to refresh its series.",
      ),
    ).toBeVisible()
    expect(screen.queryByText(/concrete series/i)).not.toBeInTheDocument()
  })

  it("surfaces malformed persisted charts without destructive controls", () => {
    render(
      <ExploreChartsConfig
        config={{
          charts: [
            chart(null, { id: "chart_1", name: "Chart A" }),
            chart(null, { id: "chart_1", name: "Chart B" }),
          ],
        }}
        onUpdate={vi.fn()}
        nodeId="explore_1"
        currentConfigHash={null}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(/duplicate chart id/i)
    expect(screen.queryByRole("button", { name: "Add Chart" })).toBeNull()
    expect(screen.queryByRole("checkbox")).toBeNull()
  })
})
