import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ExecutionMetrics, NodeDataPointResponse } from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import useNodeDataStore from "../../stores/useNodeDataStore"
import { refreshNodeDataCache } from "../../hooks/useNodeDataCache"
import useNodeResultsStore, { resetNodeResultsDerivedCaches } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useToastStore from "../../stores/useToastStore"
import useUIStore from "../../stores/useUIStore"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"
import type { PreviewData } from "../DataPreview"
import type { SimpleEdge, SimpleNode } from "../editors"
import ExplorePreview from "../ExplorePreview"
import type { ExploreDataView } from "../explore/exploreDataView"
import { PREVIEW_PANEL_DIMENSIONS } from "../previewPanelLayout"

const mockGetNodeDataPoint = vi.fn()
const mockRunNodeData = vi.fn()
const mockGetNodeDataStatus = vi.fn()
const mockCancelNodeData = vi.fn()
const mockClearNodeData = vi.fn()
const mockGetNodeDataProfile = vi.fn()

vi.mock("../../api/client", () => ({
  // Imported by useSettingsStore; never called from this panel.
  getMlflowDestinations: vi.fn(() => Promise.resolve({
    mlflow_installed: true,
    mlflow_importable: true,
    auto: "local",
    destinations: [],
    detail: "",
  })),
  getNodeDataPoint: (...args: unknown[]) => mockGetNodeDataPoint(...args),
  runNodeData: (...args: unknown[]) => mockRunNodeData(...args),
  getNodeDataStatus: (...args: unknown[]) => mockGetNodeDataStatus(...args),
  cancelNodeData: (...args: unknown[]) => mockCancelNodeData(...args),
  clearNodeData: (...args: unknown[]) => mockClearNodeData(...args),
  getNodeDataProfile: (...args: unknown[]) => mockGetNodeDataProfile(...args),
  clearInputCache: vi.fn(),
  // The embedded preview's status bar reads the snapshot store's size.
  fetchCacheUsage: vi.fn(() => Promise.resolve({ schema_version: 1, total_bytes: 0, automatic_bytes: 0, automatic_budget_bytes: 1 })),
}))

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// A lazily imported pane waits on a real dynamic import, which a cold chunk
// in a fully parallel suite run can take longer over than the 1 s default.
const LAZY_PANE_TIMEOUT_MS = 10_000
const SLOT_KEY = "source_1||pricing"
const DATA_VERSION = "gen-1"

function makeNode(id: string, label: string, nodeType: string): SimpleNode {
  return {
    id,
    type: nodeType,
    data: { label, description: "", nodeType, config: {} },
  }
}

function makeReport(overrides: Partial<ExploreDataView> = {}): ExploreDataView {
  return {
    producer_node_id: "source_1",
    source: "pricing",
    data_version: DATA_VERSION,
    row_count: 1234,
    column_count: 12,
    generated_at: 1710000000,
    columns: [],
    overview_summary: {
      data_quality: { issue_count: 0, issues: [], duplicate_row_count: 0, duplicate_ratio: 0 },
      categorical_summary: [],
    },
    ...overrides,
  }
}

function point(state: NodeDataPointResponse["state"], dataVersion: string | null): NodeDataPointResponse {
  return {
    consumer_node_id: "explore_1",
    point: { producer_node_id: "source_1", port_label: null },
    slot_key: SLOT_KEY,
    kind: "node_output",
    state,
    demand: "all",
    data_version: dataVersion,
    row_count: state === "current" ? 1234 : null,
    size_bytes: state === "current" ? 4096 : null,
    retention: state === "current" ? "pinned" : null,
    generation:
      state === "current" && dataVersion
        ? {
            generation_id: dataVersion,
            columns: "all",
            row_count: 1234,
            column_count: 12,
            size_bytes: 4096,
            retention: "pinned",
            fresh: true,
            created_at: 1,
          }
        : null,
    job: null,
    reads_directly: false,
    build_endpoint: null,
    clear_endpoint: null,
  }
}

/**
 * Put the shared store in the state it reaches once this node's point is cached
 * and profiled, and answer both routes with the same data.
 */
function seedCachedExplore({
  report = makeReport(),
  executionMetrics = null as ExecutionMetrics | null,
  dataVersion = report.data_version,
}: {
  config?: Record<string, unknown>
  report?: ExploreDataView
  executionMetrics?: ExecutionMetrics | null
  dataVersion?: string
} = {}) {
  const currentPoint = point("current", dataVersion)
  const profile = {
    row_count: report.row_count,
    column_count: report.column_count,
    columns: report.columns,
    overview_summary: report.overview_summary,
    data_version: report.data_version,
    generated_at: report.generated_at,
  }
  useNodeDataStore.setState({
    // The preview reads its own answer, not this mapping; it is recorded here
    // as the store records it, for the panes that do read it.
    consumerSlots: { explore_1: { slotKey: SLOT_KEY, identity: "explore-identity" } },
    slots: {
      [SLOT_KEY]: {
        slotKey: SLOT_KEY,
        producerNodeId: "source_1",
        portLabel: null,
        source: "pricing",
        kind: "node_output",
        reportedState: "current",
        reportedDemand: "all",
        dataVersion,
        rowCount: report.row_count,
        sizeBytes: 4096,
        retention: "pinned",
        generation: currentPoint.generation ?? null,
        readsDirectly: false,
        buildEndpoint: null,
        clearEndpoint: null,
        job: null,
        delegatedBuild: null,
      },
    },
    profiles: { [SLOT_KEY]: { dataVersion: report.data_version, profile, executionMetrics } },
  })
  mockGetNodeDataPoint.mockResolvedValue(currentPoint)
  mockGetNodeDataProfile.mockResolvedValue({
    status: "completed",
    message: "Profile is ready",
    result: profile,
    point: currentPoint,
  })
  return { point: currentPoint, profile }
}

function makePreview(overrides: Partial<PreviewData> = {}): PreviewData {
  return {
    nodeId: "explore_1",
    nodeLabel: "Explore Claims",
    status: "ok",
    row_count: 3,
    column_count: 2,
    columns: [
      { name: "premium", dtype: "i64" },
      { name: "premium_plus_one", dtype: "i64" },
    ],
    preview: [
      { premium: 10, premium_plus_one: 11 },
      { premium: 20, premium_plus_one: 21 },
    ],
    preview_row_count: 2,
    preview_row_limit: 2,
    preview_truncated: true,
    error: null,
    ...overrides,
  }
}

const sourceNode = makeNode("source_1", "Claims Source", "dataInput")
const exploreNode = makeNode("explore_1", "Explore Claims", "explore")
const edges: SimpleEdge[] = [{ id: "e1", source: "source_1", target: "explore_1" }]

function exploreNodeWithConfig(config: Record<string, unknown>): SimpleNode {
  return {
    ...exploreNode,
    data: { ...exploreNode.data, config },
  }
}

function draftChart(id: string, name: string, enabled: boolean) {
  return {
    version: 1,
    id,
    name,
    enabled,
    pivot_id: null,
    kind: "combo",
    orientation: "vertical",
    category: { source: "rows", include_grand_total: false, label_rotation: 0 },
    value_encodings: [],
    series_overrides: [],
    axes: {
      primary: {
        title: "",
        minimum: null,
        maximum: null,
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
    legend: { visible: true, position: "bottom" },
  }
}

function resetStores() {
  resetNodeResultsDerivedCaches()
  useGraphStore.setState({ structuralVersion: 0, nodes: [], edges: [] })
  useNodeResultsStore.setState({
    previews: {},
    pinnedPreviewNodeId: null,
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
  })
  useNodeDataStore.getState().reset()
  useSettingsStore.setState({
    activeSource: "pricing",
    streamingChunkSize: 250000,
  })
  useUIStore.setState({ explorePreviewPanes: {}, explorePanes: {} })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
}

function renderExplore(previewData?: PreviewData | null, node: SimpleNode = exploreNode) {
  return render(
    <ExplorePreview
      node={node}
      allNodes={[sourceNode, node]}
      edges={edges}
      submodels={{}}
      preamble="import polars as pl"
      previewData={previewData}
    />,
  )
}

describe("ExplorePreview", () => {
  beforeEach(() => {
    vi.useRealTimers()
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver
    mockGetNodeDataPoint.mockReset()
    mockGetNodeDataPoint.mockResolvedValue(point("missing", null))
    mockRunNodeData.mockReset()
    mockRunNodeData.mockResolvedValue({
      status: "started",
      job_id: "node-data-1",
      cached: false,
      message: "Caching started",
      point: point("building", null),
    })
    mockGetNodeDataStatus.mockReset()
    mockGetNodeDataStatus.mockResolvedValue({ status: "running", progress: 0.2, message: "Caching data" })
    mockCancelNodeData.mockReset()
    mockClearNodeData.mockReset()
    mockGetNodeDataProfile.mockReset()
    mockGetNodeDataProfile.mockResolvedValue({
      status: "cache_required",
      message: "Cache the data first",
      point: point("missing", null),
    })
    resetStores()
  })

  afterEach(() => {
    cleanup()
    vi.clearAllTimers()
    vi.useRealTimers()
  })


  it("renders preview rows for the Explore dataframe", async () => {
    renderExplore(makePreview())

    const nodeTitle = screen.getByText("Explore Claims")
    const previewTab = screen.getByRole("tab", { name: "Preview" })
    await screen.findByTestId("data-cache-status")

    expect(screen.getByTestId("explore-preview-frame")).toBeInTheDocument()
    expect(screen.getByTestId("explore-preview-frame")).toHaveStyle({
      height: `${PREVIEW_PANEL_DIMENSIONS.initialHeight}px`,
    })
    expect(screen.getByTestId("explore-preview-frame-header")).toHaveClass("h-9")
    expect(screen.getByTestId("preview-panel-node-icon").querySelector(".lucide-search")).toBeTruthy()
    expect(screen.getByLabelText("Collapse preview panel")).toBeInTheDocument()
    expect(nodeTitle.compareDocumentPosition(previewTab) & globalThis.Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("data-preview-embedded")).toBeInTheDocument()
    expect(screen.getByText("premium_plus_one")).toBeInTheDocument()
    expect(screen.getByText("11")).toBeInTheDocument()
    expect(screen.getByText(/Showing 2 of 3 rows/)).toBeInTheDocument()
  })

  it("opens the Relationships pane without touching the editor pane", () => {
    useUIStore.setState({ explorePanes: { explore_1: "export" } })
    renderExplore(makePreview())

    fireEvent.click(screen.getByRole("tab", { name: "Relationships" }))

    expect(screen.getByRole("tab", { name: "Relationships" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("explore-preview-relationships-pane")).toBeInTheDocument()
    expect(screen.queryByTestId("data-preview-embedded")).not.toBeInTheDocument()
    expect(useUIStore.getState().explorePreviewPanes.explore_1).toBe("relationships")
    expect(useUIStore.getState().explorePanes.explore_1).toBe("export")
  })

  it("offers the implemented Explore panes and hides preview rows on Overview", () => {
    // Seed a non-default editor pane so the untouched assertions below prove
    // Preview/Overview clicks leave it alone rather than clearing it.
    useUIStore.setState({ explorePanes: { explore_1: "export" } })
    renderExplore(makePreview())

    const preview = screen.getByRole("tab", { name: "Preview" })
    const overview = screen.getByRole("tab", { name: "Overview" })

    expect(preview).toHaveAttribute("aria-selected", "true")
    expect(overview).toHaveAttribute("aria-selected", "false")
    expect(screen.getByRole("tab", { name: "Relationships" })).toHaveAttribute("aria-selected", "false")
    expect(screen.getByRole("tab", { name: "Pivots" })).toHaveAttribute("aria-selected", "false")
    expect(screen.getByRole("tab", { name: "Charts" })).toHaveAttribute("aria-selected", "false")

    fireEvent.click(overview)

    expect(preview).toHaveAttribute("aria-selected", "false")
    expect(overview).toHaveAttribute("aria-selected", "true")
    expect(screen.queryByTestId("data-preview-embedded")).not.toBeInTheDocument()
    expect(screen.getByTestId("explore-preview-overview-pane")).toBeInTheDocument()
    expect(useUIStore.getState().explorePreviewPanes.explore_1).toBe("overview")
    // Preview/Overview selections leave the editor pane untouched.
    expect(useUIStore.getState().explorePanes.explore_1).toBe("export")

    fireEvent.click(preview)

    expect(preview).toHaveAttribute("aria-selected", "true")
    expect(useUIStore.getState().explorePreviewPanes.explore_1).toBe("preview")
    expect(useUIStore.getState().explorePanes.explore_1).toBe("export")
  })

  it("falls back to Preview when an unsupported runtime pane was remembered", () => {
    useUIStore.setState({ explorePreviewPanes: { explore_1: "legacy" as never } })

    renderExplore(makePreview())

    expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("explore-preview-preview-pane")).toBeInTheDocument()
    expect(screen.getByTestId("data-preview-embedded")).toBeInTheDocument()
  })

  it("lazy-loads the Pivots pane and distinguishes no cards from all hidden", async () => {
    // Seed a different editor pane so the alignment assertion proves an
    // existing value is overwritten, not just set from empty.
    useUIStore.setState({ explorePanes: { explore_1: "code" } })
    const { rerender } = renderExplore(makePreview())

    fireEvent.click(screen.getByRole("tab", { name: "Pivots" }))
    expect(
      await screen.findByTestId("explore-pivots-pane", {}, { timeout: LAZY_PANE_TIMEOUT_MS }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Add a pivot from the Pivots settings pane/i)).toBeInTheDocument()
    expect(useUIStore.getState().explorePreviewPanes.explore_1).toBe("pivots")
    // Selecting Pivots here aligns the settings pane to the Pivots editor.
    expect(useUIStore.getState().explorePanes.explore_1).toBe("pivots")

    const hiddenNode = exploreNodeWithConfig({
      pivots: [
        {
          version: 1,
          id: "pivot_1",
          name: "Hidden pivot",
          enabled: false,
          filters: [],
          columns: [],
          rows: [],
          values: [],
          formulas: [],
          value_order: [],
          options: { row_grand_totals: true, column_grand_totals: true },
        },
      ],
    })
    rerender(
      <ExplorePreview
        node={hiddenNode}
        allNodes={[sourceNode, hiddenNode]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={makePreview()}
      />,
    )
    expect(await screen.findByText(/No pivots are currently shown/i)).toBeInTheDocument()
  })

  it("renders enabled draft chart cards in config order and remembers the Charts pane", async () => {
    const node = exploreNodeWithConfig({
      charts: [
        draftChart("chart_1", "Chart 1", true),
        draftChart("chart_2", "Chart 2", false),
        draftChart("chart_3", "Chart 3", true),
      ],
    })
    useUIStore.setState({ explorePanes: { explore_1: "code" } })
    renderExplore(makePreview(), node)

    fireEvent.click(screen.getByRole("tab", { name: "Charts" }))

    expect(
      await screen.findByTestId("explore-charts-pane", {}, { timeout: LAZY_PANE_TIMEOUT_MS }),
    ).toBeInTheDocument()
    const visibleCharts = screen.getAllByTestId("explore-chart-visualisation")
    expect(visibleCharts).toHaveLength(2)
    expect(visibleCharts[0]).toHaveAccessibleName("Chart 1")
    expect(visibleCharts[1]).toHaveAccessibleName("Chart 3")
    expect(screen.queryByLabelText("Chart 2")).not.toBeInTheDocument()
    expect(useUIStore.getState().explorePreviewPanes.explore_1).toBe("charts")
    // Selecting Charts here aligns the settings pane to the Charts editor.
    expect(useUIStore.getState().explorePanes.explore_1).toBe("charts")
  })

  it("distinguishes no chart cards from cards that are all hidden", async () => {
    const { rerender } = renderExplore(makePreview())

    fireEvent.click(screen.getByRole("tab", { name: "Charts" }))
    expect(await screen.findByText(/Add a chart from the Charts settings pane/i)).toBeInTheDocument()

    const hiddenNode = exploreNodeWithConfig({
      charts: [draftChart("chart_1", "Chart 1", false)],
    })
    rerender(
      <ExplorePreview
        node={hiddenNode}
        allNodes={[sourceNode, hiddenNode]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={makePreview()}
      />,
    )

    expect(await screen.findByText(/No charts are currently shown/i)).toBeInTheDocument()
  })

  it("surfaces malformed chart config in the visualisation pane", async () => {
    const node = exploreNodeWithConfig({
      charts: [
        draftChart("chart_1", "Chart A", true),
        draftChart("chart_1", "Chart B", false),
      ],
    })
    renderExplore(makePreview(), node)

    fireEvent.click(screen.getByRole("tab", { name: "Charts" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(/duplicate chart id/i)
  })

  it("renders the dataset snapshot card on Overview tab when toggle is on and report present", async () => {
    const report = makeReport({ row_count: 9876, column_count: 7, source: "pricing" })
    const nodeWithToggle: SimpleNode = {
      ...exploreNode,
      data: { ...exploreNode.data, config: { overview: { dataset_snapshot: true } } },
    }
    seedCachedExplore({ config: nodeWithToggle.data.config as Record<string, unknown>, report })

    render(
      <ExplorePreview
        node={nodeWithToggle}
        allNodes={[sourceNode, nodeWithToggle]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))

    expect(await screen.findByTestId("explore-dataset-snapshot-card")).toBeInTheDocument()
    expect(screen.getByText("9,876")).toBeInTheDocument()
    expect(screen.getByText("source_1")).toBeInTheDocument()
  })

  it("renders schema and quality cards on Overview tab when toggles are on and report present", async () => {
    const report = makeReport({
      row_count: 200,
      column_count: 1,
      columns: [
        {
          name: "a",
          dtype: "Int64",
          kind: "Numeric",
          null_count: 0,
          distinct_count: 5,
          unique_ratio: 0.025,
          is_high_cardinality: false,
          is_identifier_candidate: false,
          text_min_length: null,
          text_mean_length: null,
          text_max_length: null,
          temporal_span: null,
        },
      ],
    })
    const nodeWithToggle: SimpleNode = {
      ...exploreNode,
      data: { ...exploreNode.data, config: { overview: { schema: true, data_quality: true } } },
    }
    seedCachedExplore({ config: nodeWithToggle.data.config as Record<string, unknown>, report })

    render(
      <ExplorePreview
        node={nodeWithToggle}
        allNodes={[sourceNode, nodeWithToggle]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))

    expect(await screen.findByTestId("explore-schema-table-card")).toBeInTheDocument()
    expect(screen.getByTestId("explore-data-quality-card")).toBeInTheDocument()
    expect(screen.getByText("Schema")).toBeInTheDocument()
    expect(screen.getByText("Data Quality")).toBeInTheDocument()
  })

  it("renders the no-data empty state on Overview tab when toggle is on but no report", async () => {
    const nodeWithToggle: SimpleNode = {
      ...exploreNode,
      data: { ...exploreNode.data, config: { overview: { dataset_snapshot: true } } },
    }

    render(
      <ExplorePreview
        node={nodeWithToggle}
        allNodes={[sourceNode, nodeWithToggle]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))

    expect(await screen.findByText(/No cached data yet/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
  })

  it("renders the shared cache state of the data it reads", async () => {
    renderExplore()

    expect(await screen.findByTestId("data-cache-status")).toHaveTextContent("Not cached")
    expect(mockGetNodeDataPoint).toHaveBeenCalledWith(
      expect.objectContaining({ node_id: "explore_1", source: "pricing" }),
    )
    expect(screen.getByTestId("explore-preview-frame")).toHaveTextContent("pricing | Not cached")
  })

  it("asks the shared build for the data and shows its progress", async () => {
    renderExplore()
    // Refreshing the node is what caches its data; the pane itself offers no
    // way to start a build.
    // Wait for the point to arrive: until it has, the pane does not yet know
    // whether anything needs caching.
    expect(await screen.findByTestId("data-cache-status")).toHaveTextContent("Not cached")
    expect(screen.queryByTestId("data-cache-button")).toBeNull()
    // Let the pane's effects settle, as they have by the time a user reads the
    // panel and clicks; only then does Refresh know there is nothing cached.
    await act(async () => {})
    await act(async () => {
      refreshNodeDataCache("explore_1")
    })

    await waitFor(() =>
      expect(mockRunNodeData).toHaveBeenCalledWith(
        expect.objectContaining({ node_id: "explore_1", refresh: false }),
      ),
    )
    expect(await screen.findByTestId("data-cache-cancel")).toBeInTheDocument()
    expect(screen.getByRole("progressbar", { name: "Explore data progress" })).toBeInTheDocument()
  })

  it("reads the shared profile once the point is cached, and says so", async () => {
    seedCachedExplore({ report: makeReport({ row_count: 4321 }) })

    renderExplore(null, exploreNodeWithConfig({ overview: { dataset_snapshot: true } }))

    expect(await screen.findByTestId("data-cache-status")).toHaveTextContent("Cached")
    expect(screen.getByTestId("explore-preview-frame")).toHaveTextContent("pricing | Cached")
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))
    expect(await screen.findByText("4,321")).toBeInTheDocument()
  })

  it("re-asks for a cached profile after the shared node-data store resets", async () => {
    const { profile } = seedCachedExplore({ report: makeReport({ row_count: 4321 }) })
    useNodeDataStore.setState({ profiles: {} })
    renderExplore(null, exploreNodeWithConfig({ overview: { dataset_snapshot: true } }))
    await waitFor(() => expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(1))

    act(() => {
      useNodeDataStore.getState().reset()
    })

    await waitFor(() => expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(2))
    expect(useNodeDataStore.getState().profiles[SLOT_KEY]?.profile).toEqual(profile)
  })

  it("discards an old completed profile after reset while the successor request owns the slot", async () => {
    const { profile } = seedCachedExplore({ report: makeReport({ row_count: 4321 }) })
    useNodeDataStore.setState({ profiles: {} })
    let resolveOld!: (value: unknown) => void
    let resolveNew!: (value: unknown) => void
    mockGetNodeDataProfile
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNew = resolve }))
    const view = renderExplore(null, exploreNodeWithConfig({ overview: { dataset_snapshot: true } }))
    await waitFor(() => expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(1))
    view.rerender(
      <ExplorePreview node={exploreNodeWithConfig({ overview: { dataset_snapshot: true } })} allNodes={[sourceNode, exploreNode]} edges={edges} submodels={{}} preamble="import polars as pl" previewData={null} />,
    )
    expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(1)

    act(() => { useNodeDataStore.getState().reset() })
    await waitFor(() => expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(2))
    await act(async () => {
      resolveOld({ status: "completed", message: "old", result: profile, point: point("current", DATA_VERSION) })
      await Promise.resolve()
    })
    expect(useNodeDataStore.getState().profiles[SLOT_KEY]).toBeUndefined()
    await act(async () => {
      resolveNew({ status: "completed", message: "new", result: profile, point: point("current", DATA_VERSION) })
      await Promise.resolve()
    })
    expect(useNodeDataStore.getState().profiles[SLOT_KEY]?.profile).toEqual(profile)
  })

  it("does not publish an old profile job or failure after reset", async () => {
    seedCachedExplore({ report: makeReport({ row_count: 4321 }) })
    useNodeDataStore.setState({ profiles: {} })
    let resolveOld!: (value: unknown) => void
    let resolveNew!: (value: unknown) => void
    mockGetNodeDataProfile
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNew = resolve }))
    renderExplore(null, exploreNodeWithConfig({ overview: { dataset_snapshot: true } }))
    await waitFor(() => expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(1))
    act(() => { useNodeDataStore.getState().reset() })
    await waitFor(() => expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(2))
    await act(async () => {
      resolveOld({ status: "started", job_id: "old-job", message: "old", point: point("current", DATA_VERSION) })
      await Promise.resolve()
    })
    expect(useNodeDataStore.getState().profileJobs[SLOT_KEY]).toBeUndefined()
    await act(async () => {
      resolveNew({ status: "started", job_id: "new-job", message: "new", point: point("current", DATA_VERSION) })
      await Promise.resolve()
    })
    expect(useNodeDataStore.getState().profileJobs[SLOT_KEY]?.jobId).toBe("new-job")
  })

  it("does not record an old profile request error after reset", async () => {
    seedCachedExplore({ report: makeReport({ row_count: 4321 }) })
    useNodeDataStore.setState({ profiles: {} })
    let rejectOld!: (reason: Error) => void
    mockGetNodeDataProfile
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject }))
      .mockImplementationOnce(() => new Promise(() => {}))
    renderExplore(null, exploreNodeWithConfig({ overview: { dataset_snapshot: true } }))
    await waitFor(() => expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(1))
    act(() => { useNodeDataStore.getState().reset() })
    await waitFor(() => expect(mockGetNodeDataProfile).toHaveBeenCalledTimes(2))
    await act(async () => { rejectOld(new Error("old request failed")); await Promise.resolve() })
    expect(useNodeDataStore.getState().profileFailures[SLOT_KEY]).toBeUndefined()
  })

  it("asks for the profile of a cached point that has none yet, and renders it when the job finishes", async () => {
    const currentPoint = point("current", DATA_VERSION)
    mockGetNodeDataPoint.mockResolvedValue(currentPoint)
    mockGetNodeDataProfile.mockResolvedValue({
      status: "started",
      job_id: "profile-1",
      message: "Profiling data",
      point: currentPoint,
    })

    renderExplore(null, exploreNodeWithConfig({ overview: { dataset_snapshot: true } }))

    await waitFor(() =>
      expect(mockGetNodeDataProfile).toHaveBeenCalledWith(
        expect.objectContaining({ node_id: "explore_1" }),
      ),
    )
    await waitFor(() =>
      expect(useNodeDataStore.getState().profileJobs[SLOT_KEY]?.jobId).toBe("profile-1"),
    )

    act(() => {
      useNodeDataStore.getState().finishProfileJob(SLOT_KEY, {
        status: "completed",
        progress: 1,
        message: "Profile is ready",
        profile: {
          row_count: 77,
          column_count: 2,
          columns: [],
          overview_summary: {
            data_quality: { issue_count: 0, issues: [], duplicate_row_count: 0, duplicate_ratio: 0 },
            categorical_summary: [],
          },
          data_version: DATA_VERSION,
          generated_at: 1,
        },
      })
    })

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))
    expect(await screen.findByText("77")).toBeInTheDocument()
  })

  it("stops showing a profile once the point moves to another data version", async () => {
    seedCachedExplore({ report: makeReport({ row_count: 4321 }) })
    const nodeWithToggle = exploreNodeWithConfig({ overview: { dataset_snapshot: true } })
    renderExplore(null, nodeWithToggle)
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))
    expect(await screen.findByText("4,321")).toBeInTheDocument()

    await act(async () => {
      // A rebuild published a new generation; the profile describes the old one.
      mockGetNodeDataPoint.mockResolvedValue(point("current", "gen-2"))
      mockGetNodeDataProfile.mockResolvedValue({
        status: "started",
        job_id: "profile-2",
        message: "Profiling data",
        point: point("current", "gen-2"),
      })
      useNodeDataStore
        .getState()
        .observePoint(point("current", "gen-2"), "pricing", "explore-identity")
    })

    // The pane falls back to its "nothing cached yet for this data" state
    // rather than showing statistics of the generation that was replaced.
    await waitFor(() => expect(screen.queryByText("4,321")).not.toBeInTheDocument())
    expect(await screen.findByText("No cached data yet")).toBeInTheDocument()
  })

  it("cancels the running profile job, whichever consumer started it", async () => {
    const currentPoint = point("current", DATA_VERSION)
    mockGetNodeDataPoint.mockResolvedValue(currentPoint)
    mockGetNodeDataProfile.mockResolvedValue({
      status: "started",
      job_id: "profile-1",
      message: "Profiling data",
      point: currentPoint,
    })
    mockCancelNodeData.mockResolvedValue({ status: "cancelled", progress: 0.4, message: "Cancelled" })

    renderExplore()

    const cancel = await screen.findByTestId("explore-profile-cancel")
    await act(async () => {
      fireEvent.click(cancel)
    })

    expect(mockCancelNodeData).toHaveBeenCalledWith("profile-1")
  })

  it("offers a retry after a failed profile instead of empty panes", async () => {
    const currentPoint = point("current", DATA_VERSION)
    mockGetNodeDataPoint.mockResolvedValue(currentPoint)
    mockGetNodeDataProfile.mockResolvedValue({
      status: "started",
      job_id: "profile-1",
      message: "Profiling data",
      point: currentPoint,
    })

    renderExplore(null, exploreNodeWithConfig({ overview: { dataset_snapshot: true } }))
    await waitFor(() =>
      expect(useNodeDataStore.getState().profileJobs[SLOT_KEY]?.jobId).toBe("profile-1"),
    )

    act(() => {
      // The job ends without a profile: the panes have nothing to render, and
      // nothing asks again on its own.
      useNodeDataStore.getState().finishProfileJob(SLOT_KEY, {
        status: "memory_limited",
        progress: 0.5,
        message: "Profiling ran out of memory",
      })
    })

    expect(await screen.findByText(/Profiling failed: Profiling ran out of memory/)).toBeVisible()
    mockGetNodeDataProfile.mockClear()
    await act(async () => {
      fireEvent.click(screen.getByTestId("explore-profile-retry"))
    })

    expect(mockGetNodeDataProfile).toHaveBeenCalledWith(
      expect.objectContaining({ node_id: "explore_1" }),
    )
  })

  it("keeps profiling rebuilt data after an older version's profile fails", async () => {
    const currentPoint = point("current", DATA_VERSION)
    mockGetNodeDataPoint.mockResolvedValue(currentPoint)
    mockGetNodeDataProfile.mockResolvedValue({
      status: "started",
      job_id: "profile-1",
      message: "Profiling data",
      point: currentPoint,
    })

    renderExplore()
    await waitFor(() =>
      expect(useNodeDataStore.getState().profileJobs[SLOT_KEY]?.jobId).toBe("profile-1"),
    )

    const rebuilt = point("current", "gen-2")
    mockGetNodeDataPoint.mockResolvedValue(rebuilt)
    mockGetNodeDataProfile.mockResolvedValue({
      status: "started",
      job_id: "profile-2",
      message: "Profiling data",
      point: rebuilt,
    })
    await act(async () => {
      // A re-cache publishes gen-2 while the profile of gen-1 is still running.
      useNodeDataStore.getState().observePoint(rebuilt, "pricing", "explore-identity")
    })
    act(() => {
      // Then the gen-1 job is cancelled: its failure describes gen-1, not the
      // data the point holds now.
      useNodeDataStore.getState().finishProfileJob(SLOT_KEY, {
        status: "cancelled",
        progress: 0.4,
        message: "Cancelled",
      })
    })

    expect(useNodeDataStore.getState().profileFailures[SLOT_KEY]?.dataVersion).toBe(DATA_VERSION)
    // gen-2 is still asked for, and no stale failure is shown for it.
    await waitFor(() =>
      expect(useNodeDataStore.getState().profileJobs[SLOT_KEY]?.jobId).toBe("profile-2"),
    )
    expect(screen.getByTestId("explore-preview-frame")).not.toHaveTextContent("Profiling failed")
  })

  it("shows a rejected profile request and asks again only when told to", async () => {
    mockGetNodeDataPoint.mockResolvedValue(point("current", DATA_VERSION))
    mockGetNodeDataProfile.mockRejectedValue(new Error("profile route unreachable"))

    renderExplore(null, exploreNodeWithConfig({ overview: { dataset_snapshot: true } }))

    expect(
      await screen.findByText(/Profiling failed: profile route unreachable/),
    ).toBeVisible()
    const callsAfterFailure = mockGetNodeDataProfile.mock.calls.length
    // Nothing asks again on its own: the recorded failure suppresses the
    // automatic request instead of retrying in a loop.
    await act(async () => {
      await Promise.resolve()
    })
    expect(mockGetNodeDataProfile.mock.calls.length).toBe(callsAfterFailure)

    // A rejected retry leaves the same visible state, still retryable.
    await act(async () => {
      fireEvent.click(screen.getByTestId("explore-profile-retry"))
    })
    expect(mockGetNodeDataProfile.mock.calls.length).toBeGreaterThan(callsAfterFailure)
    expect(await screen.findByTestId("explore-profile-retry")).toBeInTheDocument()
    expect(screen.getByTestId("explore-preview-frame")).toHaveTextContent(
      /Profiling failed: profile route unreachable/,
    )

    // And a retry that succeeds clears it.
    mockGetNodeDataProfile.mockResolvedValue({
      status: "completed",
      message: "Profile is ready",
      result: {
        row_count: 12,
        column_count: 1,
        columns: [],
        overview_summary: {
          data_quality: { issue_count: 0, issues: [], duplicate_row_count: 0, duplicate_ratio: 0 },
          categorical_summary: [],
        },
        data_version: DATA_VERSION,
        generated_at: 1,
      },
      point: point("current", DATA_VERSION),
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("explore-profile-retry"))
    })

    expect(screen.queryByTestId("explore-profile-retry")).toBeNull()
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))
    expect(await screen.findByText("12")).toBeInTheDocument()
  })

  it("keeps the profile job actionable when cancelling it is rejected", async () => {
    const currentPoint = point("current", DATA_VERSION)
    mockGetNodeDataPoint.mockResolvedValue(currentPoint)
    mockGetNodeDataProfile.mockResolvedValue({
      status: "started",
      job_id: "profile-1",
      message: "Profiling data",
      point: currentPoint,
    })
    mockCancelNodeData.mockRejectedValue(new Error("cancel route unreachable"))

    renderExplore()
    const cancel = await screen.findByTestId("explore-profile-cancel")
    await act(async () => {
      fireEvent.click(cancel)
    })

    expect(
      useToastStore.getState().toasts.some((toast) =>
        toast.text.includes("Cancelling the data profile failed: cancel route unreachable"),
      ),
    ).toBe(true)
    // The job is untouched by a refused cancellation: still running, still
    // cancellable, never silently forgotten.
    expect(useNodeDataStore.getState().profileJobs[SLOT_KEY]?.jobId).toBe("profile-1")
    expect(screen.getByTestId("explore-profile-cancel")).toBeInTheDocument()
  })

  it("renders execution diagnostics from the profile job", async () => {
    seedCachedExplore({
      executionMetrics: makeExecutionMetricsFixture({ profile: "preview_eager" }),
    })

    renderExplore()

    expect(
      await screen.findByText("Preview reached 75% of its memory allowance."),
    ).toBeInTheDocument()
    expect(screen.getByText("Technical details")).toBeInTheDocument()
  })
})
