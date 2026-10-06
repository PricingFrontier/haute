/**
 * A running preview's step progress: polled by its own request id and shown on
 * the loading panel, with the upstream previews a Refresh runs first labelled.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import usePipelineAPI from "../usePipelineAPI"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"

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
  getPreviewProgress: vi.fn(),
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
  getPreviewProgress,
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
const mockProgress = vi.mocked(getPreviewProgress)

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

async function renderPipelineAPI(nodes: Node[], edges: Edge[] = []) {
  mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
  const params = makeParams()
  params.graphRef.current = { nodes, edges }
  const hook = renderHook(() => usePipelineAPI(params))
  await waitFor(() => expect(hook.result.current.loading).toBe(false))
  return hook
}

describe("usePipelineAPI - step progress", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live"] })
    useGraphStore.setState({ nodes: [], edges: [], preamble: "", lastSavedSnapshot: null, undoStack: [], redoStack: [] })
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    for (const mock of [mockLoad, mockPreview, mockBuild, mockStatus, mockJob, mockCancelJob, mockProgress]) mock.mockReset()
    mockPreviewInputs.mockReset()
    mockPreviewInputs.mockResolvedValue({ input_node_ids: [] })
    mockProgress.mockResolvedValue(null)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("polls the running request's own progress and shows it until the response arrives", async () => {
    const node = makeNode("A")
    const { result } = await renderPipelineAPI([node])
    let finish!: (value: typeof okPreview) => void
    let sentRequestId: string | undefined
    mockPreview.mockImplementationOnce((request) => {
      sentRequestId = request.requestId
      return new Promise((resolve) => { finish = resolve })
    })
    mockProgress.mockImplementation(async (requestId: string) => ({
      request_id: requestId,
      phase: "running",
      done: 1,
      total: 2,
      label: "Caching join",
    }))

    act(() => result.current.refreshPreview(node))

    await waitFor(() => expect(result.current.previewData?.progress?.done).toBe(1))
    expect(sentRequestId).toMatch(/^[A-Za-z0-9-]{1,64}$/)
    expect(mockProgress.mock.calls.every(([requestId]) => requestId === sentRequestId)).toBe(true)

    await act(async () => {
      finish(okPreview)
    })
    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    expect(result.current.previewData?.progress).toBeUndefined()
    const asked = mockProgress.mock.calls.length
    await new Promise((resolve) => setTimeout(resolve, 600))
    expect(mockProgress.mock.calls.length).toBe(asked)
  })

  it("labels the upstream previews a Refresh runs before its target", async () => {
    const upstream = makeNode("U")
    const target = makeNode("A")
    const { result } = await renderPipelineAPI([upstream, target], [{ id: "e", source: "U", target: "A" }])
    mockPreview.mockImplementation((request) =>
      request.nodeId === "U" ? hangUntilAborted(request) : Promise.resolve(okPreview),
    )

    act(() => result.current.refreshPreview(target))

    await waitFor(() => expect(result.current.previewData?.loading_message).toBe("Previewing inputs (0 of 1)"))
    act(() => result.current.stopPreview())
  })
})
