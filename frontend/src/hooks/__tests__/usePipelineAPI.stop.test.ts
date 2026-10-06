/**
 * The preview frame's Stop: it stops the running preview — and the input
 * preparation the preview waits on — and shows the node's last stored result.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import usePipelineAPI from "../usePipelineAPI"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useToastStore from "../../stores/useToastStore"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useUIStore from "../../stores/useUIStore"

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(),
  previewInputs: vi.fn(async () => ({ input_node_ids: [] as string[] })),
  previewNode: vi.fn(),
  previewRecoveryNode: vi.fn(),
  savePipeline: vi.fn(),
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

const inputNode = {
  id: "source",
  type: "dataInput",
  position: { x: 0, y: 0 },
  data: {
    label: "Source",
    nodeType: "dataInput",
    config: { inputType: "file", format: "csv", mode: "scan", path: "quotes.csv" },
  },
} as Node

vi.mock("../../utils/buildGraph", () => ({
  resolveGraphFromRefs: vi.fn(() => ({ nodes: [inputNode], edges: [], preamble: "" })),
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

/** A preview request that only ends when it is aborted. */
function hangUntilAborted({ signal }: { signal?: AbortSignal }) {
  return new Promise<never>((_resolve, reject) => {
    signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))
  })
}

const okPreview = {
  node_id: "A",
  status: "ok" as const,
  row_count: 3,
  column_count: 1,
  columns: [{ name: "x", dtype: "i64" }],
  preview: [{ x: 1 }, { x: 2 }, { x: 3 }],
}

async function renderPipelineAPI(nodes: Node[]) {
  mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
  const params = makeParams()
  params.graphRef.current = { nodes, edges: [] }
  const hook = renderHook(() => usePipelineAPI(params))
  await waitFor(() => expect(hook.result.current.loading).toBe(false))
  return hook
}

describe("usePipelineAPI - Stop", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live"] })
    useGraphStore.setState({ nodes: [], edges: [], preamble: "", lastSavedSnapshot: null, undoStack: [], redoStack: [] })
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    useToastStore.setState({ toasts: [] })
    for (const mock of [mockLoad, mockPreview, mockBuild, mockStatus, mockJob, mockCancelJob]) mock.mockReset()
    mockPreviewInputs.mockReset()
    mockPreviewInputs.mockResolvedValue({ input_node_ids: [] })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("aborts the running preview and shows the node's last stored result again", async () => {
    const node = makeNode("A")
    const { result } = await renderPipelineAPI([node])
    mockPreview.mockResolvedValueOnce(okPreview)
    act(() => result.current.refreshPreview(node))
    await waitFor(() => expect(result.current.previewData?.row_count).toBe(3))

    let signal: AbortSignal | undefined
    mockPreview.mockImplementationOnce((request) => {
      signal = request.signal
      return hangUntilAborted(request)
    })
    act(() => result.current.refreshPreview(node))
    await waitFor(() => expect(signal).toBeDefined())
    expect(result.current.previewBusy).toBe(true)

    act(() => result.current.stopPreview())

    await waitFor(() => expect(result.current.previewBusy).toBe(false))
    expect(signal?.aborted).toBe(true)
    expect(result.current.previewData?.status).toBe("ok")
    expect(result.current.previewData?.row_count).toBe(3)
  })

  it("leaves the panel empty after a stop when nothing is stored for the node", async () => {
    const node = makeNode("A")
    const { result } = await renderPipelineAPI([node])
    mockPreview.mockImplementationOnce(hangUntilAborted)
    act(() => result.current.refreshPreview(node))
    await waitFor(() => expect(mockPreview).toHaveBeenCalled())

    act(() => result.current.stopPreview())

    await waitFor(() => expect(result.current.previewBusy).toBe(false))
    expect(result.current.previewData).toBeNull()
  })

  it("keeps running when the snapshot build refuses to stop, and Stop cancels it again", async () => {
    const node = makeNode("A")
    const { result } = await renderPipelineAPI([node])
    mockPreviewInputs.mockResolvedValue({ input_node_ids: ["source"] })
    mockStatus.mockResolvedValue({ schema_version: 1, identity_digest: "d", state: "missing", freshness: "unknown", generation: null } as never)
    mockBuild.mockResolvedValue({ schema_version: 1, job_id: "build-1", identity_digest: "d", status: "running", joined: false, build_class: "bounded" } as never)
    mockJob.mockImplementation(async () => ({ status: "running", progress: { phase: "reading", rows: 0, batches: 0, bytes: 0, elapsed_seconds: 0 } }) as never)
    mockCancelJob
      .mockRejectedValueOnce(new Error("cancel route unreachable"))
      .mockResolvedValueOnce({ status: "cancelled" } as never)

    act(() => result.current.refreshPreview(node))
    await waitFor(() => expect(mockBuild).toHaveBeenCalled())
    await waitFor(() => expect(mockJob).toHaveBeenCalled())

    act(() => result.current.stopPreview())

    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((toast) => toast.text.includes("Stopping failed"))).toBe(true),
    )
    // The build may still be running, so the preview is still running.
    expect(result.current.previewBusy).toBe(true)
    expect(mockPreview).not.toHaveBeenCalled()

    await act(async () => {
      result.current.stopPreview()
    })

    await waitFor(() => expect(result.current.previewBusy).toBe(false))
    expect(mockCancelJob).toHaveBeenCalledTimes(2)
    expect(mockCancelJob).toHaveBeenLastCalledWith("build-1")
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("stops waiting for a snapshot build joined from elsewhere without cancelling it", async () => {
    const node = makeNode("A")
    const { result } = await renderPipelineAPI([node])
    mockPreviewInputs.mockResolvedValue({ input_node_ids: ["source"] })
    mockStatus.mockResolvedValue({ schema_version: 1, identity_digest: "d", state: "missing", freshness: "unknown", generation: null } as never)
    mockBuild.mockResolvedValue({ schema_version: 1, job_id: "theirs", identity_digest: "d", status: "running", joined: true, build_class: "bounded" } as never)
    mockJob.mockImplementation(async () => ({ status: "running", progress: { phase: "reading", rows: 0, batches: 0, bytes: 0, elapsed_seconds: 0 } }) as never)

    act(() => result.current.refreshPreview(node))
    await waitFor(() => expect(mockJob).toHaveBeenCalled())

    act(() => result.current.stopPreview())

    await waitFor(() => expect(result.current.previewBusy).toBe(false))
    expect(mockCancelJob).not.toHaveBeenCalled()
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("runs a clean preview when Refresh follows a stop", async () => {
    const node = makeNode("A")
    const { result } = await renderPipelineAPI([node])
    mockPreview.mockImplementationOnce(hangUntilAborted)
    act(() => result.current.refreshPreview(node))
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))
    act(() => result.current.stopPreview())
    await waitFor(() => expect(result.current.previewBusy).toBe(false))

    mockPreview.mockResolvedValueOnce(okPreview)
    act(() => result.current.refreshPreview(node))

    await waitFor(() => expect(result.current.previewData?.row_count).toBe(3))
    expect(result.current.previewBusy).toBe(false)
  })

  it("does not run a stopped preview again when its data changed under it", async () => {
    useUIStore.getState().setCalculationMode("automatic")
    const node = makeNode("A")
    const { result } = await renderPipelineAPI([node])
    mockPreview.mockResolvedValueOnce(okPreview)
    act(() => result.current.refreshPreview(node))
    await waitFor(() => expect(result.current.previewData?.row_count).toBe(3))

    mockPreview.mockImplementationOnce(hangUntilAborted)
    act(() => result.current.refreshPreview(node))
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2))
    // Data changed while the second run was in flight.
    act(() => useNodeDataStore.getState().bumpEpoch())

    act(() => result.current.stopPreview())
    await waitFor(() => expect(result.current.previewBusy).toBe(false))
    await new Promise((resolve) => setTimeout(resolve, 300))

    expect(mockPreview).toHaveBeenCalledTimes(2)
    expect(result.current.previewData?.row_count).toBe(3)
  })

  it("keeps a frame preview running when its snapshot build refuses to stop, and retries on Stop", async () => {
    const node = makeNode("A")
    const { result } = await renderPipelineAPI([node])
    mockPreviewInputs.mockResolvedValue({ input_node_ids: ["source"] })
    mockStatus.mockResolvedValue({ schema_version: 1, identity_digest: "d", state: "missing", freshness: "unknown", generation: null } as never)
    mockBuild.mockResolvedValue({ schema_version: 1, job_id: "build-1", identity_digest: "d", status: "running", joined: false, forced: false, build_class: "bounded" } as never)
    mockJob.mockImplementation(async () => ({ status: "running", progress: { phase: "reading", rows: 0, batches: 0, bytes: 0, elapsed_seconds: 0 } }) as never)
    mockCancelJob
      .mockRejectedValueOnce(new Error("cancel route unreachable"))
      .mockResolvedValueOnce({ status: "cancelled" } as never)

    act(() => result.current.previewNodeFrame("A", "quotes"))
    await waitFor(() => expect(mockJob).toHaveBeenCalled())

    act(() => result.current.stopPreview())
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((toast) => toast.text.includes("Stopping failed"))).toBe(true),
    )
    expect(result.current.previewBusy).toBe(true)

    await act(async () => {
      result.current.stopPreview()
    })
    await waitFor(() => expect(result.current.previewBusy).toBe(false))
    expect(mockCancelJob).toHaveBeenCalledTimes(2)
    expect(mockPreview).not.toHaveBeenCalled()
  })
})
