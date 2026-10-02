/**
 * The preview frame's Refresh on a structured Quote Input re-reads its file and
 * caches every table again before previewing it, whether or not the file
 * changed. Every other preview keeps the freshness rules.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import type { MutableRefObject } from "react"
import usePipelineAPI from "../usePipelineAPI"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useToastStore from "../../stores/useToastStore"
import useUIStore from "../../stores/useUIStore"
import type { InputCacheJobStatusResponse } from "../../api/types"

// A step crosses the snapshot module's first dynamic import, a build and a
// preview, which a whole parallel suite run makes slower without making it wrong.
const STEP_TIMEOUT_MS = 10_000

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(),
  previewInputs: vi.fn(),
  previewNode: vi.fn(),
  previewRecoveryNode: vi.fn(),
  savePipeline: vi.fn(),
  getPreviewProgress: vi.fn(async () => null),
  buildInputCache: vi.fn(),
  getInputCacheStatus: vi.fn(),
  getInputCacheJob: vi.fn(),
  cancelInputCacheJob: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(msg: string) {
      super(msg)
      this.name = "ApiError"
    }
  },
}))

vi.mock("../../utils/buildGraph", () => ({
  resolveGraphFromRefs: vi.fn((graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>) => ({
    nodes: graphRef.current.nodes,
    edges: graphRef.current.edges,
    submodels: {},
    preamble: "",
  })),
}))

import {
  buildInputCache,
  cancelInputCacheJob,
  getInputCacheJob,
  getInputCacheStatus,
  loadPipeline,
  previewInputs,
  previewNode,
} from "../../api/client"
import { makeNode } from "../../test-utils/factories"
import { makeLoadedPipeline } from "../../testSupport/pipelineDocumentFixture"

const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)
const mockPreviewInputs = vi.mocked(previewInputs)
const mockBuild = vi.mocked(buildInputCache)
const mockStatus = vi.mocked(getInputCacheStatus)
const mockJob = vi.mocked(getInputCacheJob)
const mockCancelJob = vi.mocked(cancelInputCacheJob)

const emittingTable = {
  path: "$[:]",
  label: "quotes",
  emit: true,
  columns: [{ name: "id", path: "$[:].id", selected: true }],
}
const quotesConfig = { path: "data/quotes.jsonl", tables: [emittingTable] }

function quoteInput(id: string, config: Record<string, unknown>): Node {
  return makeNode(id, "apiInput", {
    data: { label: id, nodeType: "apiInput", config, _sourceHandleInputNames: { quotes: "quotes" } },
  })
}

function job(status: InputCacheJobStatusResponse["status"], message = ""): InputCacheJobStatusResponse {
  return {
    schema_version: 1,
    job_id: "reread-1",
    identity_digest: "group",
    status,
    terminal_reason: status === "running" ? null : status,
    message,
    refresh: true,
    build_class: "bounded",
    progress: { phase: status === "running" ? "building" : "completed", rows: 0, batches: 0, bytes: 0, elapsed_seconds: 0 },
    snapshot: null,
    error_code: null,
  }
}

const okPreview = {
  node_id: "quotes",
  status: "ok" as const,
  row_count: 2,
  column_count: 1,
  columns: [{ name: "id", dtype: "i64" }],
  preview: [{ id: 1 }, { id: 2 }],
}

function makeParams() {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    activeSubmodelIdentity: null,
    submodelsRef: { current: {} },
    setNodes: vi.fn(),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    pipelineNameRef: { current: "test" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    nodeIdCounter: { current: 0 },
  }
}

async function renderPipelineAPI(nodes: Node[]) {
  mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
  const params = makeParams()
  params.graphRef.current = { nodes, edges: [] }
  const hook = renderHook(() => usePipelineAPI(params))
  await waitFor(() => expect(hook.result.current.loading).toBe(false))
  return { ...hook, params }
}

function epoch(): number {
  return useNodeDataStore.getState().epoch
}

/** The builds this test saw that re-read their source. */
function forcedBuilds() {
  return mockBuild.mock.calls.filter(([request]) => request.refresh === true)
}

describe("usePipelineAPI - Refresh re-reads a structured Quote Input", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useUIStore.getState().setCalculationMode("automatic")
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live"] })
    useGraphStore.setState({ nodes: [], edges: [], preamble: "", lastSavedSnapshot: null, undoStack: [], redoStack: [] })
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    useNodeDataStore.getState().reset()
    useToastStore.setState({ toasts: [] })
    for (const mock of [mockLoad, mockPreview, mockPreviewInputs, mockBuild, mockStatus, mockJob, mockCancelJob]) {
      mock.mockReset()
    }
    // The preview reads the Quote Input, whose tables are ready and fresh.
    mockPreviewInputs.mockImplementation(async ({ nodeId }) => ({ input_node_ids: [nodeId] }))
    mockStatus.mockResolvedValue({ schema_version: 1, identity_digest: "group", state: "ready", freshness: "fresh", generation: null })
    mockBuild.mockResolvedValue({
      schema_version: 1,
      job_id: "reread-1",
      identity_digest: "group",
      status: "running",
      joined: false,
      forced: true,
      build_class: "bounded",
    })
    mockJob.mockResolvedValue(job("completed"))
    mockPreview.mockResolvedValue(okPreview)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("re-reads the file and caches every table before previewing, at the raised epoch", async () => {
    const quotes = quoteInput("quotes", quotesConfig)
    const { result } = await renderPipelineAPI([quotes])
    const before = epoch()

    act(() => result.current.refreshPreview(quotes, { rereadSource: true }))

    await waitFor(() => expect(result.current.previewData?.row_count).toBe(2), { timeout: STEP_TIMEOUT_MS })
    expect(mockBuild).toHaveBeenCalledTimes(1)
    expect(mockBuild).toHaveBeenCalledWith({ schema_version: 1, node_type: "apiInput", config: quotesConfig, refresh: true })
    expect(mockBuild.mock.invocationCallOrder[0]).toBeLessThan(mockPreview.mock.invocationCallOrder[0])
    expect(epoch()).toBeGreaterThan(before)
    // Stored at the epoch the re-read raised, so it is current and not fetched again.
    expect(useNodeResultsStore.getState().previews.quotes.nodeDataEpoch).toBe(epoch())
    await waitFor(() => expect(result.current.previewBusy).toBe(false))
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(mockPreview).toHaveBeenCalledTimes(1)
    // The re-read was asked for: no "Building input snapshot…" toast.
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("re-reads the original's file for an instance", async () => {
    const original = quoteInput("quotes", quotesConfig)
    const instance = quoteInput("quotes_copy", { instanceOf: "quotes" })
    const { result } = await renderPipelineAPI([original, instance])

    act(() => result.current.refreshPreview(instance, { rereadSource: true }))

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1), { timeout: STEP_TIMEOUT_MS })
    expect(forcedBuilds()).toEqual([[{ schema_version: 1, node_type: "apiInput", config: quotesConfig, refresh: true }]])
  })

  it.each([
    ["a re-preview that is not the frame's Refresh", quoteInput("quotes", quotesConfig), {}],
    ["a Data Input, whose forced re-read is Import", makeNode("quotes", "dataInput", {
      data: { label: "quotes", nodeType: "dataInput", config: { inputType: "file", format: "csv", mode: "scan", path: "quotes.csv" } },
    }), { rereadSource: true }],
    ["a Quote Input with no emitting table", quoteInput("quotes", { path: "data/quotes.jsonl", tables: [{ ...emittingTable, emit: false }] }), { rereadSource: true }],
    ["a Quote Input reading a flat file", quoteInput("quotes", { path: "data/quotes.csv", tables: [emittingTable] }), { rereadSource: true }],
  ])("keeps the freshness rules for %s", async (_case, target, options) => {
    const { result } = await renderPipelineAPI([target])

    act(() => result.current.refreshPreview(target, options))

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1), { timeout: STEP_TIMEOUT_MS })
    expect(forcedBuilds()).toEqual([])
  })

  it("lets Stop cancel the re-read and run no preview", async () => {
    const quotes = quoteInput("quotes", quotesConfig)
    const { result } = await renderPipelineAPI([quotes])
    mockJob.mockResolvedValue(job("running"))
    mockCancelJob.mockResolvedValue({ schema_version: 1, job_id: "reread-1", cancellation_requested: true, status: "cancelled" })
    const before = epoch()

    act(() => result.current.refreshPreview(quotes, { rereadSource: true }))
    await waitFor(() => expect(mockJob).toHaveBeenCalled(), { timeout: STEP_TIMEOUT_MS })
    expect(result.current.previewBusy).toBe(true)
    expect(result.current.previewData?.loading_message).toBe("Caching Quote Input tables as Parquet…")

    act(() => result.current.stopPreview())

    await waitFor(() => expect(result.current.previewBusy).toBe(false), { timeout: STEP_TIMEOUT_MS })
    expect(mockCancelJob).toHaveBeenCalledWith("reread-1")
    expect(mockPreview).not.toHaveBeenCalled()
    expect(result.current.previewData).toBeNull()
    // A stopped re-read may have published some tables.
    expect(epoch()).toBeGreaterThan(before)
  })

  it("shows a failed re-read as the node's preview error", async () => {
    const quotes = quoteInput("quotes", quotesConfig)
    const { result } = await renderPipelineAPI([quotes])
    mockJob.mockResolvedValue(job("error", "The source could not be read."))
    const before = epoch()

    act(() => result.current.refreshPreview(quotes, { rereadSource: true }))

    await waitFor(() => expect(result.current.previewData?.status).toBe("error"), { timeout: STEP_TIMEOUT_MS })
    expect(result.current.previewData?.error).toBe("The source could not be read.")
    await waitFor(() => expect(result.current.previewBusy).toBe(false))
    expect(mockPreview).not.toHaveBeenCalled()
    // A failed build can publish the tables it finished before failing.
    expect(epoch()).toBeGreaterThan(before)
  })

  it("previews the graph as it is once the re-read ends, without reading again", async () => {
    const quotes = quoteInput("quotes", quotesConfig)
    const { result, params } = await renderPipelineAPI([quotes])
    let finish!: (value: InputCacheJobStatusResponse) => void
    mockJob.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))

    act(() => result.current.refreshPreview(quotes, { rereadSource: true }))
    await waitFor(() => expect(mockJob).toHaveBeenCalled(), { timeout: STEP_TIMEOUT_MS })

    // Another node is edited while the file is read.
    const rating = makeNode("rating")
    act(() => {
      params.graphRef.current = { nodes: [quotes, rating], edges: [] }
      useGraphStore.setState((state) => ({ structuralVersion: state.structuralVersion + 1 }))
    })
    await act(async () => {
      finish(job("completed"))
    })

    await waitFor(() => expect(result.current.previewData?.row_count).toBe(2), { timeout: STEP_TIMEOUT_MS })
    expect(mockPreview).toHaveBeenCalledTimes(1)
    expect(mockPreview.mock.calls[0][0].graph.nodes.map((node: { id: string }) => node.id)).toEqual(["quotes", "rating"])
    expect(forcedBuilds()).toHaveLength(1)
    await waitFor(() => expect(result.current.previewBusy).toBe(false))
  })

  it("runs nothing more for a node that left the graph while its file was read", async () => {
    const quotes = quoteInput("quotes", quotesConfig)
    const { result, params } = await renderPipelineAPI([quotes])
    let finish!: (value: InputCacheJobStatusResponse) => void
    mockJob.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))

    act(() => result.current.refreshPreview(quotes, { rereadSource: true }))
    await waitFor(() => expect(mockJob).toHaveBeenCalled(), { timeout: STEP_TIMEOUT_MS })

    act(() => {
      params.graphRef.current = { nodes: [], edges: [] }
    })
    await act(async () => {
      finish(job("completed"))
    })

    await waitFor(() => expect(result.current.previewBusy).toBe(false), { timeout: STEP_TIMEOUT_MS })
    expect(mockPreviewInputs).not.toHaveBeenCalled()
    expect(mockPreview).not.toHaveBeenCalled()
  })
})
