import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ExploreCacheReport } from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore, { hashConfig, resetNodeResultsDerivedCaches } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
import type { PreviewData } from "../DataPreview"
import type { SimpleEdge, SimpleNode } from "../editors"
import ExplorePreview from "../ExplorePreview"
import { buildExploreCacheIdentity } from "../explore/cacheIdentity"
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

function toGraphNode(node: SimpleNode): Node {
  return {
    id: node.id,
    type: node.type ?? node.data.nodeType,
    data: node.data,
    position: { x: 0, y: 0 },
  } as Node
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
    columns: [],
    overview_summary: {
      data_quality: { issue_count: 0, issues: [] },
      categorical_summary: [],
    },
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

function exploreNodeWithConfig(config: Record<string, unknown>): SimpleNode {
  return {
    ...exploreNode,
    data: { ...exploreNode.data, config },
  }
}

function makeExploreDataCacheHash({
  node,
  allNodes,
  graphEdges = edges,
  submodels = {},
  preamble = "import polars as pl",
  source = "pricing",
}: {
  node: SimpleNode
  allNodes: SimpleNode[]
  graphEdges?: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  source?: string
}): string {
  return hashConfig({
    graph: buildExploreCacheIdentity({ node, allNodes, edges: graphEdges, submodels, preamble }),
    source,
  })
}

function seedCachedExplore({
  config = {},
  source = "pricing",
  structuralVersion = 0,
  report = makeReport({ source }),
  node = exploreNodeWithConfig(config),
  allNodes,
  graphEdges = edges,
  submodels = {},
  preamble = "import polars as pl",
}: {
  config?: Record<string, unknown>
  source?: string
  structuralVersion?: number
  report?: ExploreCacheReport
  node?: SimpleNode
  allNodes?: SimpleNode[]
  graphEdges?: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
} = {}) {
  const graphNodes = allNodes ?? [sourceNode, node]
  useNodeResultsStore.setState({
    exploreResults: {
      explore_1: {
        result: report,
        terminalStatus: {
          status: "completed",
          progress: 1,
          message: "Cached",
          result: report,
          terminal_reason: "completed",
        },
        jobId: "explore-job-cached",
        configHash: makeExploreDataCacheHash({
          node,
          allNodes: graphNodes,
          graphEdges,
          submodels,
          preamble,
          source,
        }),
        source,
        structuralVersion,
        nodeLabel: "Explore Claims",
      },
    },
  })
}

function resetStores() {
  resetNodeResultsDerivedCaches()
  useGraphStore.setState({ structuralVersion: 0 })
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

  it("renders the dataset snapshot card on Overview tab when toggle is on and report present", () => {
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

    expect(screen.getByTestId("explore-dataset-snapshot-card")).toBeInTheDocument()
    expect(screen.getByText("9,876")).toBeInTheDocument()
    expect(screen.getByText("source_1")).toBeInTheDocument()
  })

  it("renders schema and quality cards on Overview tab when toggles are on and report present", () => {
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

    expect(screen.getByTestId("explore-schema-table-card")).toBeInTheDocument()
    expect(screen.getByTestId("explore-data-quality-card")).toBeInTheDocument()
    expect(screen.getByText("Schema")).toBeInTheDocument()
    expect(screen.getByText("Data Quality")).toBeInTheDocument()
  })

  it("reuses cached report when only overview toggles change", () => {
    const dataConfig = { code: "df = df.select(pl.all())" }
    const report = makeReport({ row_count: 9876, column_count: 7 })
    seedCachedExplore({ config: dataConfig, report })
    useGraphStore.setState({ structuralVersion: 1 })
    const nodeWithOverviewToggle: SimpleNode = {
      ...exploreNode,
      data: {
        ...exploreNode.data,
        config: { ...dataConfig, overview: { dataset_snapshot: true } },
      },
    }

    render(
      <ExplorePreview
        node={nodeWithOverviewToggle}
        allNodes={[sourceNode, nodeWithOverviewToggle]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))

    expect(screen.getByText(/pricing\s*\|\s*cached/i)).toBeInTheDocument()
    expect(screen.getByTestId("explore-dataset-snapshot-card")).toBeInTheDocument()
    expect(screen.getByText("9,876")).toBeInTheDocument()
  })

  it("keeps cached report visible after an overview-only config update through the graph store", () => {
    const dataConfig = { code: "df = df.select(pl.all())" }
    const nodeBeforeToggle: SimpleNode = {
      ...exploreNode,
      data: { ...exploreNode.data, config: dataConfig },
    }
    const nodeAfterToggle: SimpleNode = {
      ...exploreNode,
      data: {
        ...exploreNode.data,
        config: { ...dataConfig, overview: { dataset_snapshot: true } },
      },
    }
    act(() => {
      useGraphStore.getState().setNodesRaw([toGraphNode(sourceNode), toGraphNode(nodeBeforeToggle)])
      useGraphStore.getState().setEdgesRaw(edges as unknown as Edge[])
    })
    const cachedStructuralVersion = useGraphStore.getState().structuralVersion
    const report = makeReport({ row_count: 9876, column_count: 7 })
    seedCachedExplore({ config: dataConfig, structuralVersion: cachedStructuralVersion, report })

    const view = render(
      <ExplorePreview
        node={nodeBeforeToggle}
        allNodes={[sourceNode, nodeBeforeToggle]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )

    act(() => {
      useGraphStore.getState().setNodesRaw([toGraphNode(sourceNode), toGraphNode(nodeAfterToggle)])
    })
    expect(useGraphStore.getState().structuralVersion).toBe(cachedStructuralVersion)

    view.rerender(
      <ExplorePreview
        node={nodeAfterToggle}
        allNodes={[sourceNode, nodeAfterToggle]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))

    expect(screen.getByText(/pricing\s*\|\s*cached/i)).toBeInTheDocument()
    expect(screen.getByTestId("explore-dataset-snapshot-card")).toBeInTheDocument()
    expect(screen.getByText("9,876")).toBeInTheDocument()
  })

  it("keeps an active Explore job cancellable when only overview toggles change", () => {
    const dataConfig = { code: "df = df.select(pl.all())" }
    const nodeBeforeToggle = exploreNodeWithConfig(dataConfig)
    const nodeAfterToggle = exploreNodeWithConfig({
      ...dataConfig,
      overview: { schema: true, data_quality: true },
    })
    useNodeResultsStore.setState({
      exploreJobs: {
        explore_1: {
          jobId: "explore-job-running",
          nodeId: "explore_1",
          nodeLabel: "Explore Claims",
          progress: {
            status: "running",
            progress: 0.42,
            message: "Caching",
            result: null,
          },
          error: null,
          configHash: makeExploreDataCacheHash({
            node: nodeBeforeToggle,
            allNodes: [sourceNode, nodeBeforeToggle],
          }),
          source: "pricing",
          structuralVersion: 0,
        },
      },
    })
    useGraphStore.setState({ structuralVersion: 1 })

    render(
      <ExplorePreview
        node={nodeAfterToggle}
        allNodes={[sourceNode, nodeAfterToggle]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )

    expect(screen.getByText(/pricing\s*\|\s*caching/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /process & cache full data/i })).not.toBeInTheDocument()
  })

  it("hides cached report and status when Explore code changes", () => {
    const previousConfig = { code: "df = df.select(pl.all())" }
    const nextConfig = {
      code: "df = df.filter(pl.col('premium') > 0)",
      overview: { dataset_snapshot: true },
    }
    seedCachedExplore({
      config: previousConfig,
      report: makeReport({ row_count: 9999, column_count: 4 }),
    })
    const changedNode: SimpleNode = {
      ...exploreNode,
      data: { ...exploreNode.data, config: nextConfig },
    }

    render(
      <ExplorePreview
        node={changedNode}
        allNodes={[sourceNode, changedNode]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))

    expect(screen.getByText(/pricing\s*\|\s*ready/i)).toBeInTheDocument()
    expect(screen.getByText(/No cached data yet/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /process & cache full data/i })).toBeInTheDocument()
  })

  it("hides cached report and status when the active source changes", () => {
    const config = { overview: { dataset_snapshot: true } }
    seedCachedExplore({
      config,
      source: "pricing",
      report: makeReport({ source: "pricing", row_count: 9999 }),
    })
    useSettingsStore.setState({ activeSource: "renewal" })
    const nodeWithToggle: SimpleNode = {
      ...exploreNode,
      data: { ...exploreNode.data, config },
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

    expect(screen.getByText(/renewal\s*\|\s*ready/i)).toBeInTheDocument()
    expect(screen.getByText(/No cached data yet/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
  })

  it("hides cached report and status when upstream graph state changes", () => {
    const config = { overview: { dataset_snapshot: true } }
    const cachedSourceNode: SimpleNode = {
      ...sourceNode,
      data: {
        ...sourceNode.data,
        config: { path: "claims_2025.parquet" },
      },
    }
    const changedSourceNode: SimpleNode = {
      ...sourceNode,
      data: {
        ...sourceNode.data,
        config: { path: "claims_2026.parquet" },
      },
    }
    const nodeWithToggle: SimpleNode = {
      ...exploreNode,
      data: { ...exploreNode.data, config },
    }
    seedCachedExplore({
      node: nodeWithToggle,
      allNodes: [cachedSourceNode, nodeWithToggle],
      report: makeReport({ row_count: 9999 }),
    })

    render(
      <ExplorePreview
        node={nodeWithToggle}
        allNodes={[changedSourceNode, nodeWithToggle]}
        edges={edges}
        submodels={{}}
        preamble="import polars as pl"
        previewData={null}
      />,
    )

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))

    expect(screen.getByText(/pricing\s*\|\s*ready/i)).toBeInTheDocument()
    expect(screen.getByText(/No cached data yet/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
  })

  it("renders the no-data empty state on Overview tab when toggle is on but no report", () => {
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

    expect(screen.getByText(/No cached data yet/i)).toBeInTheDocument()
    expect(screen.queryByTestId("explore-dataset-snapshot-card")).not.toBeInTheDocument()
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
