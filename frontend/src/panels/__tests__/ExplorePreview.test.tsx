import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ExploreCacheReport } from "../../api/types"
import useNodeResultsStore, { resetNodeResultsDerivedCaches } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
import type { PreviewData } from "../DataPreview"
import type { SimpleEdge, SimpleNode } from "../editors"
import ExplorePreview from "../ExplorePreview"
import { DEFAULT_PREVIEW_PANEL_DIMENSIONS } from "../previewPanelLayout"

const mockRunExplore = vi.fn()
const mockGetExploreStatus = vi.fn()
const mockCancelExplore = vi.fn()

vi.mock("../../api/client", () => ({
  checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
  runExplore: (...args: unknown[]) => mockRunExplore(...args),
  getExploreStatus: (...args: unknown[]) => mockGetExploreStatus(...args),
  cancelExplore: (...args: unknown[]) => mockCancelExplore(...args),
}))

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function makeNode(id: string, label: string, nodeType: string): SimpleNode {
  return {
    id,
    type: nodeType,
    data: { label, description: "", nodeType, config: {} },
  }
}

function makeReport(overrides: Partial<ExploreCacheReport> = {}): ExploreCacheReport {
  return {
    status: "ok",
    node_id: "explore_1",
    upstream_node_id: "source_1",
    source: "pricing",
    dataframe_cache_key: "explore_dataset:abc123",
    row_count: 1234,
    column_count: 12,
    generated_at: 1710000000,
    ...overrides,
  }
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

const sourceNode = makeNode("source_1", "Claims Source", "dataSource")
const exploreNode = makeNode("explore_1", "Explore Claims", "explore")
const edges: SimpleEdge[] = [{ id: "e1", source: "source_1", target: "explore_1" }]

function resetStores() {
  resetNodeResultsDerivedCaches()
  useNodeResultsStore.setState({
    previews: {},
    pinnedPreviewNodeId: null,
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
    exploreResults: {},
    exploreJobs: {},
  })
  useSettingsStore.setState({
    activeSource: "pricing",
    streamingChunkSize: 250000,
  })
  useUIStore.setState({ explorePreviewPanes: {} })
}

function renderExplore(previewData?: PreviewData | null) {
  return render(
    <ExplorePreview
      node={exploreNode}
      allNodes={[sourceNode, exploreNode]}
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
    mockRunExplore.mockReset()
    mockGetExploreStatus.mockReset()
    mockCancelExplore.mockReset()
    resetStores()
  })

  afterEach(() => {
    cleanup()
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it("starts a cache materialisation and stores the result for the node", async () => {
    const report = makeReport()
    mockRunExplore.mockResolvedValueOnce({
      status: "completed",
      job_id: null,
      cached: true,
      message: "Explore cache hit",
      result: report,
    })

    renderExplore()
    fireEvent.click(screen.getByRole("button", { name: /process & cache full data/i }))

    await waitFor(() => expect(mockRunExplore).toHaveBeenCalledTimes(1))
    expect(mockRunExplore).toHaveBeenCalledWith({
      graph: {
        nodes: expect.arrayContaining([
          expect.objectContaining({ id: "source_1" }),
          expect.objectContaining({ id: "explore_1" }),
        ]),
        edges,
        submodels: {},
        preamble: "import polars as pl",
      },
      node_id: "explore_1",
      source: "pricing",
      streamingChunkSize: 250000,
    })

    expect(await screen.findByText(/pricing\s*\|\s*cached/i)).toBeInTheDocument()
    expect(useNodeResultsStore.getState().exploreResults.explore_1?.result).toEqual(report)
  })

  it("renders preview rows for the Explore dataframe", () => {
    renderExplore(makePreview())

    const nodeTitle = screen.getByText("Explore Claims")
    const previewTab = screen.getByRole("tab", { name: "Preview" })
    const processButton = screen.getByRole("button", { name: "Process & cache full data" })

    expect(screen.getByTestId("explore-preview-frame")).toBeInTheDocument()
    expect(screen.getByTestId("explore-preview-frame")).toHaveStyle({
      height: `${DEFAULT_PREVIEW_PANEL_DIMENSIONS.initialHeight}px`,
    })
    expect(screen.getByTestId("explore-preview-frame-header")).toHaveClass("h-9")
    expect(screen.getByTestId("preview-panel-node-icon").querySelector(".lucide-search")).toBeTruthy()
    expect(screen.getByLabelText("Collapse preview panel")).toBeInTheDocument()
    expect(processButton).toHaveClass("h-6", "whitespace-nowrap")
    expect(nodeTitle.compareDocumentPosition(previewTab) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("data-preview-embedded")).toBeInTheDocument()
    expect(screen.getByText("premium_plus_one")).toBeInTheDocument()
    expect(screen.getByText("11")).toBeInTheDocument()
    expect(screen.getByText(/Showing 2 of 3 rows/)).toBeInTheDocument()
  })

  it("switches lower Explore panes without rendering preview rows outside Preview", () => {
    renderExplore(makePreview())

    const preview = screen.getByRole("tab", { name: "Preview" })
    const overview = screen.getByRole("tab", { name: "Overview" })
    const relationships = screen.getByRole("tab", { name: "Relationships" })
    const charts = screen.getByRole("tab", { name: "Charts" })

    expect(preview).toHaveAttribute("aria-selected", "true")
    expect(overview).toHaveAttribute("aria-selected", "false")
    expect(relationships).toHaveAttribute("aria-selected", "false")
    expect(charts).toHaveAttribute("aria-selected", "false")

    fireEvent.click(charts)

    expect(preview).toHaveAttribute("aria-selected", "false")
    expect(charts).toHaveAttribute("aria-selected", "true")
    expect(screen.queryByTestId("data-preview-embedded")).not.toBeInTheDocument()
    expect(screen.getByTestId("explore-preview-charts-pane")).toBeEmptyDOMElement()
    expect(useUIStore.getState().explorePreviewPanes.explore_1).toBe("charts")
  })

  it("registers a started Explore job for background polling", async () => {
    mockRunExplore.mockResolvedValueOnce({
      status: "started",
      job_id: "explore-job-1",
      cached: false,
      message: "Started",
      result: null,
    })

    renderExplore()

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /process & cache full data/i }))
    })

    expect(screen.getByText(/pricing\s*\|\s*caching/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument()
    expect(useNodeResultsStore.getState().exploreJobs.explore_1).toMatchObject({
      jobId: "explore-job-1",
      nodeLabel: "Explore Claims",
      source: "pricing",
    })
    expect(mockGetExploreStatus).not.toHaveBeenCalled()
  })

  it("cancels the active Explore job through the API", async () => {
    mockRunExplore.mockResolvedValueOnce({
      status: "started",
      job_id: "explore-job-2",
      cached: false,
      message: "Started",
      result: null,
    })
    mockCancelExplore.mockResolvedValueOnce({
      status: "cancelled",
      progress: 1,
      message: "Cancelled",
      result: null,
    })

    renderExplore()

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /process & cache full data/i }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /cancel/i }))
    })

    expect(mockCancelExplore).toHaveBeenCalledWith("explore-job-2")
    expect(await screen.findByText(/pricing\s*\|\s*cancelled/i)).toBeInTheDocument()
    expect(useNodeResultsStore.getState().exploreJobs.explore_1).toBeUndefined()
  })
})
