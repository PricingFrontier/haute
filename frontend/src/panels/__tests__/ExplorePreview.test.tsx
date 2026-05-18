import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ExploreCacheReport } from "../../api/types"
import useNodeResultsStore, { resetNodeResultsDerivedCaches } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import type { SimpleEdge, SimpleNode } from "../editors"
import ExplorePreview from "../ExplorePreview"

const mockRunExplore = vi.fn()
const mockGetExploreStatus = vi.fn()
const mockCancelExplore = vi.fn()

vi.mock("../../api/client", () => ({
  checkMlflow: vi.fn(() => Promise.resolve({ mlflow_installed: false })),
  runExplore: (...args: unknown[]) => mockRunExplore(...args),
  getExploreStatus: (...args: unknown[]) => mockGetExploreStatus(...args),
  cancelExplore: (...args: unknown[]) => mockCancelExplore(...args),
}))

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
}

function renderExplore() {
  return render(
    <ExplorePreview
      node={exploreNode}
      allNodes={[sourceNode, exploreNode]}
      edges={edges}
      submodels={{}}
      preamble="import polars as pl"
    />,
  )
}

describe("ExplorePreview", () => {
  beforeEach(() => {
    vi.useRealTimers()
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
    // v1: the lower-panel body stays empty (reserved for future EDA work);
    // the cached payload still lands in the store for downstream consumers.
    expect(screen.getByTestId("explore-preview-body")).toBeEmptyDOMElement()
    expect(useNodeResultsStore.getState().exploreResults.explore_1?.result).toEqual(report)
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
