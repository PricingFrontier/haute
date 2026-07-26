import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import type { Mock } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import usePipelineAPI, {
  DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT,
  PREVIEW_INITIAL_COLUMN_LIMIT,
} from "../usePipelineAPI"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import type { PipelineEdge } from "../../types/node"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(),
  previewNode: vi.fn(),
  savePipeline: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail?: string

    constructor(message: string, status: number, detail?: string) {
      super(message)
      this.status = status
      this.detail = detail
    }
  },
  ApiTimeoutError: class ApiTimeoutError extends Error {
    timeoutMs: number
    url: string

    constructor(url: string, timeoutMs: number) {
      super(`Request timed out after ${timeoutMs / 1000} seconds.`)
      this.name = "ApiTimeoutError"
      this.timeoutMs = timeoutMs
      this.url = url
    }
  },
}))

vi.mock("../../utils/buildGraph", () => ({
  resolveGraphFromRefs: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

vi.mock("../../utils/makePreviewData", () => ({
  makePreviewData: vi.fn((nodeId: string, label: string, opts: Record<string, unknown>) => ({
    nodeId,
    nodeLabel: label,
    status: opts.status || "ok",
    row_count: opts.row_count ?? 0,
    column_count: opts.column_count ?? 0,
    columns: opts.columns ?? [],
    preview: opts.preview ?? [],
    error: opts.error ?? null,
    timing_ms: opts.timing_ms ?? 0,
    memory_bytes: opts.memory_bytes ?? 0,
    timings: opts.timings ?? [],
    memory: opts.memory ?? [],
    schema_warnings: opts.schema_warnings ?? [],
    execution_metrics: opts.execution_metrics ?? null,
  })),
}))

import { ApiError, ApiTimeoutError, loadPipeline, previewNode, savePipeline } from "../../api/client"
import { resolveGraphFromRefs } from "../../utils/buildGraph"
import { makeEdge, makeNode } from "../../test-utils/factories"
const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)
const mockSave = vi.mocked(savePipeline)
const mockResolveGraphFromRefs = vi.mocked(resolveGraphFromRefs)

function makeParams(overrides: Partial<Parameters<typeof usePipelineAPI>[0]> = {}) {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    submodelsRef: { current: {} },
    setNodes: vi.fn(),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    pipelineNameRef: { current: "test" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    nodeIdCounter: { current: 0 },
    ...overrides,
  }
}

type NodeUpdater = Node[] | ((nds: Node[]) => Node[])
type NodeSetterMock = Mock<(updater: NodeUpdater) => void>

function applyNodeUpdater(current: Node[], updater: NodeUpdater): Node[] {
  return typeof updater === "function" ? updater(current) : updater
}

function nodeSetterMock(setter: unknown): NodeSetterMock {
  return setter as NodeSetterMock
}

function wireGraphStoreNodeSetters(params: ReturnType<typeof makeParams>) {
  params.setNodes = vi.fn((updater: NodeUpdater) => {
    const nodes = applyNodeUpdater(params.graphRef.current.nodes, updater)
    params.graphRef.current = { ...params.graphRef.current, nodes }
    useGraphStore.getState().setNodes(nodes)
  })
  params.setNodesRaw = vi.fn((updater: NodeUpdater) => {
    const nodes = applyNodeUpdater(params.graphRef.current.nodes, updater)
    params.graphRef.current = { ...params.graphRef.current, nodes }
    useGraphStore.getState().setNodesRaw(nodes)
  })
}

function edgeJoinSaveGraph(config: Record<string, unknown>): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: [
      {
        id: "quotes",
        type: "pipelineNode",
        position: { x: 0, y: 0 },
        data: { label: "Quotes", nodeType: "polars", config: {} },
      },
      {
        id: "lookup",
        type: "pipelineNode",
        position: { x: 0, y: 120 },
        data: { label: "Lookup", nodeType: "polars", config: {} },
      },
      {
        id: "edge_join_1",
        type: "pipelineNode",
        position: { x: 240, y: 60 },
        data: { label: "Edge Join", nodeType: "edgeJoin", config },
      },
    ],
    edges: [
      { id: "e_quotes_join", source: "quotes", target: "edge_join_1", targetHandle: "base" },
      { id: "e_lookup_join", source: "lookup", target: "edge_join_1", targetHandle: "join" },
    ],
  }
}

function makeSubmodelPortNode(id = "port_in__source"): Node {
  return {
    id,
    type: NODE_TYPES.SUBMODEL_PORT,
    position: { x: 0, y: 0 },
    data: { label: "Source Port", portDirection: "input", portName: "Source Port" },
  } as unknown as Node
}

describe("usePipelineAPI", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live"] })
    useGraphStore.setState({
      nodes: [],
      edges: [],
      submodels: {},
      preamble: "",
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
    })
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    mockLoad.mockReset()
    mockPreview.mockReset()
    mockResolveGraphFromRefs.mockReset()
    mockResolveGraphFromRefs.mockImplementation((graphRef, parentGraphRef, submodelsRef, preambleRef) => {
      if (parentGraphRef.current) {
        return {
          nodes: parentGraphRef.current.nodes,
          edges: parentGraphRef.current.edges,
          submodels: parentGraphRef.current.submodels,
          preamble: preambleRef.current,
        }
      }
      return {
        nodes: graphRef.current.nodes,
        edges: graphRef.current.edges,
        submodels: submodelsRef.current,
        preamble: preambleRef.current,
      }
    })

    mockSave.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
    vi.restoreAllMocks()
  })

  it("loads pipeline on mount and sets loading to false", async () => {
    mockLoad.mockResolvedValue({
      nodes: [makeNode("n1")],
      edges: [],
      preamble: "import polars as pl",
      pipeline_name: "pricing",
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(params.setNodesRaw).toHaveBeenCalled()
    expect(params.setEdgesRaw).toHaveBeenCalled()
    expect(params.setPreamble).toHaveBeenCalledWith("import polars as pl")
  })

  it("uses the cold-start retry policy for the initial pipeline load", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    const params = makeParams()
    renderHook(() => usePipelineAPI(params))

    await waitFor(() => expect(mockLoad).toHaveBeenCalled())
    expect(mockLoad).toHaveBeenCalledWith({
      signal: expect.any(AbortSignal),
      retry: { maxRetries: 6, baseDelayMs: 250 },
    })
  })

  it("aborts the initial pipeline load on unmount", async () => {
    mockLoad.mockImplementation(() => new Promise(() => {}))

    const params = makeParams()
    const { unmount } = renderHook(() => usePipelineAPI(params))

    await waitFor(() => expect(mockLoad).toHaveBeenCalled())
    const options = mockLoad.mock.calls[0][0] as { signal: AbortSignal }
    expect(options.signal.aborted).toBe(false)

    unmount()

    expect(options.signal.aborted).toBe(true)
  })

  it("surfaces initial load AbortErrors that were not caused by unmount cleanup", async () => {
    mockLoad.mockRejectedValue(new DOMException("request timed out", "AbortError"))

    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "error" && t.text.includes("request timed out"))).toBe(true)
    })
  })

  it.each([
    { label: "null", submodels: null },
    { label: "omitted", submodels: undefined },
  ])("normalizes $label HTTP submodels without retaining stale state", async ({ submodels }) => {
    mockLoad.mockResolvedValue({
      nodes: [],
      edges: [],
      pipeline_name: null,
      pipeline_description: null,
      preamble: null,
      source_file: null,
      submodels,
      warning: null,
    })

    const staleSubmodels = {
      stale: { nodes: [makeNode("stale-child")], edges: [] },
    }
    useGraphStore.getState().setSubmodelsRaw(staleSubmodels)
    const setSubmodelsRaw = vi.fn((submodels: Record<string, unknown>) => {
      useGraphStore.getState().setSubmodelsRaw(submodels)
    })
    const params = makeParams({
      submodelsRef: { current: staleSubmodels },
      setSubmodelsRaw,
    })
    const { result } = renderHook(() => usePipelineAPI(params))

    await waitFor(() => expect(result.current.loading).toBe(false))

    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "error" && t.text.includes("Failed to load pipeline"))).toBe(false)
    expect(params.setNodesRaw).toHaveBeenCalledWith([])
    expect(params.setEdgesRaw).toHaveBeenCalledWith([])
    expect(params.setPreamble).not.toHaveBeenCalled()
    expect(params.submodelsRef.current).toEqual({})
    expect(setSubmodelsRaw).toHaveBeenCalledWith({})
    expect(useGraphStore.getState().submodels).toEqual({})
  })

  it("shows toast on load failure", async () => {
    mockLoad.mockRejectedValue(new Error("Server down"))
    const params = makeParams()
    renderHook(() => usePipelineAPI(params))
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "error" && t.text.includes("Server down"))).toBe(true)
    })
  })

  it("handleSave calls savePipeline and shows success toast", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockSave.mockResolvedValue({ file: "pricing.py", pipeline_name: "pricing" })
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    // handleSave reads graphRef for the save payload and marks that
    // submitted snapshot as saved after the backend accepts it. Keep
    // graphRef and useGraphStore in sync so isDirty() reports false.
    useGraphStore.setState({ nodes: [makeNode("n1")], edges: [], preamble: "" })
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => {
      result.current.handleSave()
    })
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "success" && t.text.includes("pricing.py"))).toBe(true)
    })
    // After save, the submitted graph snapshot is the saved baseline.
    expect(useGraphStore.getState().isDirty()).toBe(false)
  })

  it("handleSave includes the current submodel mirror in the payload", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockSave.mockResolvedValue({ file: "pricing.py", pipeline_name: "pricing" })
    const params = makeParams()
    const submodels = {
      pricing: {
        nodes: [makeNode("child")],
        edges: [],
      },
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    params.submodelsRef.current = submodels

    await act(async () => {
      await result.current.handleSave()
    })

    expect(mockSave).toHaveBeenCalledWith(expect.objectContaining({
      graph: expect.objectContaining({ submodels }),
    }))
  })

  it("preserves authored submodel boundary ports in the save payload", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockSave.mockResolvedValue({ file: "pricing.py", pipeline_name: "pricing" })
    const boundaryEdge: PipelineEdge = {
      id: "e_boundary",
      source: "submodel__pricing",
      target: "submodel__scoring",
      sourceHandle: "out__priced",
      targetHandle: "in__score",
      sourcePort: "quotes",
      targetPort: "base",
    }
    const params = makeParams()
    params.graphRef.current = { nodes: [], edges: [boundaryEdge] }
    useGraphStore.setState({ nodes: [], edges: [boundaryEdge], preamble: "" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => {
      await result.current.handleSave()
    })

    expect(mockSave).toHaveBeenCalledWith(expect.objectContaining({
      graph: expect.objectContaining({
        edges: [
          expect.objectContaining({
            sourcePort: "quotes",
            targetPort: "base",
          }),
        ],
      }),
    }))
  })

  it("keeps later edits dirty when they happen while a save is in flight", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    let resolveSave!: (value: { file: string; pipeline_name: string }) => void
    const savePromise = new Promise<{ file: string; pipeline_name: string }>((resolve) => {
      resolveSave = resolve
    })
    mockSave.mockReturnValue(savePromise)

    const savedNodes = [makeNode("n1", "polars", { data: { label: "Before save" } })]
    const editedNodes = [makeNode("n1", "polars", { data: { label: "Edited while saving" } })]
    const params = makeParams()
    params.graphRef.current = { nodes: savedNodes, edges: [] }
    useGraphStore.setState({ nodes: savedNodes, edges: [], preamble: "" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      result.current.handleSave()
    })

    expect(mockSave).toHaveBeenCalledWith(expect.objectContaining({
      graph: expect.objectContaining({ nodes: savedNodes }),
    }))

    act(() => {
      params.graphRef.current = { nodes: editedNodes, edges: [] }
      useGraphStore.getState().setNodes(editedNodes)
    })
    expect(useGraphStore.getState().isDirty()).toBe(true)

    await act(async () => {
      resolveSave({ file: "pricing.py", pipeline_name: "pricing" })
      await savePromise
    })

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "success" && t.text.includes("pricing.py"))).toBe(true)
    })
    expect(useGraphStore.getState().isDirty()).toBe(true)
  })

  it("keeps in-place graph mutations dirty when they happen while a save is in flight", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    let resolveSave!: (value: { file: string; pipeline_name: string }) => void
    const savePromise = new Promise<{ file: string; pipeline_name: string }>((resolve) => {
      resolveSave = resolve
    })
    mockSave.mockReturnValue(savePromise)

    const config = { code: "df = input_df" }
    const savedNode = makeNode("n1", "polars", { data: { label: "Transform", config } })
    const savedEdge = makeEdge("n1", "n2", { id: "e1", sourceHandle: "before" })
    const params = makeParams()
    params.graphRef.current = { nodes: [savedNode], edges: [savedEdge] }
    useGraphStore.setState({ nodes: [savedNode], edges: [savedEdge], preamble: "" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      result.current.handleSave()
    })

    config.code = "df = input_df.with_columns(pl.lit(1).alias('later'))"
    savedEdge.sourceHandle = "after"
    act(() => {
      useGraphStore.getState().setNodes([savedNode])
      useGraphStore.getState().setEdges([savedEdge])
    })
    expect(useGraphStore.getState().isDirty()).toBe(true)

    await act(async () => {
      resolveSave({ file: "pricing.py", pipeline_name: "pricing" })
      await savePromise
    })

    expect(useGraphStore.getState().isDirty()).toBe(true)

    act(() => {
      config.code = "df = input_df"
      savedEdge.sourceHandle = "before"
      useGraphStore.getState().setNodes([savedNode])
      useGraphStore.getState().setEdges([savedEdge])
    })
    expect(useGraphStore.getState().isDirty()).toBe(false)
  })

  it("does not let an older save response replace a newer saved baseline", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    let resolveFirst!: (value: { file: string; pipeline_name: string }) => void
    let resolveSecond!: (value: { file: string; pipeline_name: string }) => void
    const firstSave = new Promise<{ file: string; pipeline_name: string }>((resolve) => {
      resolveFirst = resolve
    })
    const secondSave = new Promise<{ file: string; pipeline_name: string }>((resolve) => {
      resolveSecond = resolve
    })
    mockSave
      .mockReturnValueOnce(firstSave)
      .mockReturnValueOnce(secondSave)

    const firstNodes = [makeNode("n1", "polars", { data: { label: "First" } })]
    const secondNodes = [makeNode("n1", "polars", { data: { label: "Second" } })]
    const params = makeParams()
    params.graphRef.current = { nodes: firstNodes, edges: [] }
    useGraphStore.setState({ nodes: firstNodes, edges: [], preamble: "" })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      result.current.handleSave()
    })

    act(() => {
      params.graphRef.current = { nodes: secondNodes, edges: [] }
      useGraphStore.getState().setNodes(secondNodes)
    })

    await act(async () => {
      result.current.handleSave()
    })

    await act(async () => {
      resolveSecond({ file: "pricing.py", pipeline_name: "pricing" })
      await secondSave
    })
    expect(useGraphStore.getState().isDirty()).toBe(false)

    await act(async () => {
      resolveFirst({ file: "pricing.py", pipeline_name: "pricing" })
      await firstSave
    })
    expect(useGraphStore.getState().isDirty()).toBe(false)

    act(() => {
      useGraphStore.getState().setNodes(firstNodes)
    })
    expect(useGraphStore.getState().isDirty()).toBe(true)
  })

  it("handleSave is blocked while drilled into a submodel", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockSave.mockResolvedValue({ file: "pricing.py", pipeline_name: "pricing" })
    const params = makeParams({
      parentGraphRef: {
        current: {
          nodes: [makeNode("parent")],
          edges: [],
          submodels: { pricing: {} },
        },
      },
    })
    params.graphRef.current = { nodes: [makeNode("child")], edges: [] }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      result.current.handleSave()
    })

    expect(mockSave).not.toHaveBeenCalled()
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) =>
      t.type === "error" &&
      t.text.includes("Return to the main pipeline before saving"),
    )).toBe(true)
  })

  it("handleSave shows error toast on failure", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockSave.mockRejectedValue(new Error("disk full"))
    const params = makeParams()
    params.graphRef.current = { nodes: [], edges: [] }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => {
      result.current.handleSave()
    })
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "error")).toBe(true)
    })
  })

  it("handleSave shows ApiError detail for backend validation failures", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockSave.mockRejectedValue(
      new ApiError(
        "HTTP 400",
        400,
        "edgeJoin non-cross joins require join keys via on or leftOn/rightOn.",
      ),
    )
    const params = makeParams()
    params.graphRef.current = { nodes: [], edges: [] }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      result.current.handleSave()
    })

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) =>
        t.type === "error" &&
        t.text.includes("edgeJoin non-cross joins require join keys"),
      )).toBe(true)
      expect(toasts.some((t) => t.text === "Failed to save pipeline: HTTP 400")).toBe(false)
    })
  })

  it.each([
    [
      "non-cross joins without keys",
      { baseInput: "quotes", joinInput: "lookup", how: "left" },
      "Non-cross joins need join keys.",
    ],
    [
      "cross joins with keys",
      { baseInput: "quotes", joinInput: "lookup", how: "cross", on: ["policy_id"] },
      "Cross joins must not configure join keys.",
    ],
  ])("handleSave blocks invalid edgeJoin config before posting for %s", async (_caseName, config, message) => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockSave.mockResolvedValue({ status: "saved", file: "test.py", pipeline_name: "test", warnings: [] })
    const graph = edgeJoinSaveGraph(config)
    const params = makeParams()
    params.graphRef.current = graph
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      result.current.handleSave()
    })

    expect(mockSave).not.toHaveBeenCalled()
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) =>
        t.type === "error" &&
        t.text.includes("Edge Join") &&
        t.text.includes(message),
      )).toBe(true)
    })
  })

  it("loads sources from backend", async () => {
    mockLoad.mockResolvedValue({
      nodes: [],
      edges: [],
      sources: ["live", "test_scenario"],
      active_source: "test_scenario",
    })
    const params = makeParams()
    renderHook(() => usePipelineAPI(params))
    await waitFor(() => {
      expect(useSettingsStore.getState().sources).toEqual(["live", "test_scenario"])
      expect(useSettingsStore.getState().activeSource).toBe("test_scenario")
    })
  })

  it("setPreviewData can be set externally", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => {
      result.current.setPreviewData(null)
    })
    expect(result.current.previewData).toBeNull()
  })

  it("initial nodeStatuses is empty", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.nodeStatuses).toEqual({})
  })

  it("fetchPreview sets loading preview then calls API", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [{ name: "a", dtype: "f64" }],
      preview: [{ a: 1 }],
      row_count: 1,
      column_count: 1,
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    const node = makeNode("n1")
    act(() => {
      result.current.fetchPreview(node)
    })
    // Should show loading state immediately
    expect(result.current.previewData?.status).toBe("loading")
  })

  it.each([
    NODE_TYPES.SUBMODEL,
    NODE_TYPES.SUBMODEL_PORT,
  ])("fetchPreview skips backend preview for non-executable placeholder node type %s", async (nodeType) => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "should-not-run",
      status: "ok",
      columns: [],
      preview: [],
      row_count: 0,
      column_count: 0,
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(makeNode("submodel__model_stuff", nodeType), { debounceMs: 0 })
    })

    expect(result.current.previewData).toBeNull()
    expect(result.current.previewBusy).toBe(false)
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("fetchPreview skips backend preview for submodel port nodes typed by React Flow", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "should-not-run",
      status: "ok",
      columns: [],
      preview: [],
      row_count: 0,
      column_count: 0,
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(makeSubmodelPortNode(), { debounceMs: 0 })
    })

    expect(result.current.previewData).toBeNull()
    expect(result.current.previewBusy).toBe(false)
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it.each([
    NODE_TYPES.SUBMODEL,
    NODE_TYPES.SUBMODEL_PORT,
  ])("refreshPreview skips backend preview for non-executable placeholder node type %s", async (nodeType) => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(makeNode("submodel__model_stuff", nodeType))
    })

    expect(result.current.previewData).toBeNull()
    expect(result.current.previewBusy).toBe(false)
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("refreshPreview skips backend preview for submodel port nodes typed by React Flow", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(makeSubmodelPortNode())
    })

    expect(result.current.previewData).toBeNull()
    expect(result.current.previewBusy).toBe(false)
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it("fetchPreview propagation skips downstream submodel placeholders", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "upstream",
      status: "ok",
      columns: [{ name: "premium", dtype: "f64" }],
      preview: [{ premium: 100 }],
      row_count: 1,
      column_count: 1,
    })
    const upstream = makeNode("upstream", NODE_TYPES.POLARS)
    const submodel = makeNode("submodel__model_stuff", NODE_TYPES.SUBMODEL)
    const params = makeParams()
    params.graphRef.current = {
      nodes: [upstream, submodel],
      edges: [makeEdge("upstream", "submodel__model_stuff")],
    }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(upstream, { debounceMs: 0 })
    })

    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await act(async () => { await Promise.resolve() })

    expect(mockPreview).toHaveBeenCalledOnce()
    expect(mockPreview.mock.calls[0][0].nodeId).toBe("upstream")
  })

  it("fetchPreview propagation skips downstream submodel ports typed by React Flow", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "upstream",
      status: "ok",
      columns: [{ name: "premium", dtype: "f64" }],
      preview: [{ premium: 100 }],
      row_count: 1,
      column_count: 1,
    })
    const upstream = makeNode("upstream", NODE_TYPES.POLARS)
    const port = makeSubmodelPortNode()
    const params = makeParams()
    params.graphRef.current = {
      nodes: [upstream, port],
      edges: [makeEdge("upstream", port.id)],
    }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(upstream, { debounceMs: 0 })
    })

    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))
    await act(async () => { await Promise.resolve() })

    expect(mockPreview).toHaveBeenCalledOnce()
    expect(mockPreview.mock.calls[0][0].nodeId).toBe("upstream")
  })

  it("refreshPreview skips stale upstream submodel placeholders", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "target",
      status: "ok",
      columns: [{ name: "premium", dtype: "f64" }],
      preview: [{ premium: 100 }],
      row_count: 1,
      column_count: 1,
    })
    const submodel = makeNode("submodel__model_stuff", NODE_TYPES.SUBMODEL)
    const target = makeNode("target", NODE_TYPES.POLARS)
    const params = makeParams()
    params.graphRef.current = {
      nodes: [submodel, target],
      edges: [makeEdge("submodel__model_stuff", "target")],
    }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(target)
    })

    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))

    expect(mockPreview).toHaveBeenCalledOnce()
    expect(mockPreview.mock.calls[0][0].nodeId).toBe("target")
  })

  it("refreshPreview skips stale upstream submodel ports typed by React Flow", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "target",
      status: "ok",
      columns: [{ name: "premium", dtype: "f64" }],
      preview: [{ premium: 100 }],
      row_count: 1,
      column_count: 1,
    })
    const port = makeSubmodelPortNode()
    const target = makeNode("target", NODE_TYPES.POLARS)
    const params = makeParams()
    params.graphRef.current = {
      nodes: [port, target],
      edges: [makeEdge(port.id, "target")],
    }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(target)
    })

    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))

    expect(mockPreview).toHaveBeenCalledOnce()
    expect(mockPreview.mock.calls[0][0].nodeId).toBe("target")
  })

  it("fetchPreview requests known preview columns for nodes with cached schema", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [
        { name: "age", dtype: "i64" },
        { name: "premium", dtype: "f64" },
      ],
      preview_columns: ["age", "premium"],
      preview: [{ age: 25, premium: 100.5 }],
      row_count: 1,
      column_count: 2,
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    const node = makeNode("n1", "polars", {
      data: {
        _columns: [
          { name: "age", dtype: "i64" },
          { name: "premium", dtype: "f64" },
        ],
      },
    })

    act(() => { result.current.fetchPreview(node, { debounceMs: 0 }) })

    await waitFor(() => expect(mockPreview).toHaveBeenCalled())
    expect(mockPreview.mock.calls.at(-1)?.[0].requestedPreviewColumns).toEqual(["age", "premium"])
  })

  it("fetchPreview caps requested preview columns for wide cached schemas", async () => {
    const columns = Array.from({ length: PREVIEW_INITIAL_COLUMN_LIMIT + 5 }, (_, i) => ({
      name: `col_${i}`,
      dtype: "i64",
    }))
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "wide",
      status: "ok",
      columns,
      preview_columns: columns.slice(0, PREVIEW_INITIAL_COLUMN_LIMIT).map((column) => column.name),
      preview: [{ col_0: 1 }],
      row_count: 1,
      column_count: columns.length,
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(
        makeNode("wide", "polars", {
          data: { _columns: columns },
        }),
        { debounceMs: 0 },
      )
    })

    await waitFor(() => expect(mockPreview).toHaveBeenCalled())
    const requested = mockPreview.mock.calls.at(-1)?.[0].requestedPreviewColumns
    expect(requested).toHaveLength(PREVIEW_INITIAL_COLUMN_LIMIT)
    expect(requested?.[0]).toBe("col_0")
    expect(requested?.at(-1)).toBe(`col_${PREVIEW_INITIAL_COLUMN_LIMIT - 1}`)
  })

  it("fetchPreview preserves full preview fetch when schema is not known yet", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [{ name: "age", dtype: "i64" }],
      preview: [{ age: 25 }],
      row_count: 1,
      column_count: 1,
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 }) })

    await waitFor(() => expect(mockPreview).toHaveBeenCalled())
    expect(mockPreview.mock.calls.at(-1)?.[0].requestedPreviewColumns).toBeUndefined()
  })

  it("fetchPreview populates nodeStatuses from response", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [{ name: "a", dtype: "f64" }],
      preview: [{ a: 1 }],
      row_count: 1,
      column_count: 1,
      node_statuses: { n1: "ok", n0: "ok" },
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    const node = makeNode("n1")
    act(() => {
      result.current.fetchPreview(node)
    })
    // Wait for the async preview to resolve
    await waitFor(() => expect(result.current.nodeStatuses).toEqual({ n1: "ok", n0: "ok" }))
  })

  it("fetchPreview writes preview schema through the raw node setter", async () => {
    const node = makeNode("n1", "polars", {
      data: { label: "Node n1", nodeType: "polars", config: {} },
    })
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [{ name: "premium", dtype: "Float64" }],
      available_columns: [{ name: "premium", dtype: "Float64" }],
      schema_warnings: [],
      preview: [{ premium: 10 }],
      row_count: 1,
      column_count: 1,
    })
    const params = makeParams()
    params.graphRef.current = { nodes: [node], edges: [] }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))
    nodeSetterMock(params.setNodes).mockClear()
    nodeSetterMock(params.setNodesRaw).mockClear()

    act(() => {
      result.current.fetchPreview(node, { debounceMs: 0 })
    })

    await waitFor(() => expect(params.setNodesRaw).toHaveBeenCalledTimes(1))
    expect(params.setNodes).not.toHaveBeenCalled()
    const updater = nodeSetterMock(params.setNodesRaw).mock.calls[0][0] as (nodes: Node[]) => Node[]
    const [updated] = updater([node])
    expect(updated.data._columns).toEqual([{ name: "premium", dtype: "Float64" }])
  })

  it("fetchPreview aborts downstream propagation previews when the request is cancelled", async () => {
    const root = makeNode("root", "polars", {
      data: { label: "Root", nodeType: "polars", config: {} },
    })
    const child = makeNode("child", "polars", {
      data: { label: "Child", nodeType: "polars", config: {} },
    })
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    let childSignal: AbortSignal | undefined
    mockPreview.mockImplementation(({ nodeId, signal }) => {
      if (nodeId === "root") {
        return Promise.resolve({
          node_id: "root",
          status: "ok",
          columns: [{ name: "root_col", dtype: "f64" }],
          preview: [{ root_col: 1 }],
          row_count: 1,
          column_count: 1,
        })
      }
      if (nodeId === "child") {
        childSignal = signal
        return new Promise(() => {})
      }
      throw new Error(`Unexpected preview ${nodeId}`)
    })

    const params = makeParams()
    params.graphRef.current = {
      nodes: [root, child],
      edges: [makeEdge("root", "child")],
    }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(root, { debounceMs: 0 })
    })

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2))
    expect(childSignal).toBeInstanceOf(AbortSignal)
    expect(childSignal?.aborted).toBe(false)

    act(() => {
      result.current.cancelPreview()
    })

    expect(childSignal?.aborted).toBe(true)
  })

  it("fetchPreview carries execution metrics into visible preview data and cache", async () => {
    const executionMetrics = makeExecutionMetricsFixture()
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [{ name: "a", dtype: "f64" }],
      preview: [{ a: 1 }],
      row_count: 1,
      column_count: 1,
      execution_metrics: executionMetrics,
    })
    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 })
    })

    await waitFor(() => {
      expect(result.current.previewData?.execution_metrics).toBe(executionMetrics)
    })
    expect(useNodeResultsStore.getState().getPreview("n1")?.data.execution_metrics).toBe(executionMetrics)
  })

  it("shows client-side preview timeouts in the panel and toast", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockRejectedValue(new ApiTimeoutError("/api/pipeline/preview", 120_000))
    const params = makeParams()
    const node = makeNode("n1", "polars", {
      data: { label: "Rating step", nodeType: "polars", config: {} },
    })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(node, { debounceMs: 0 })
    })

    await waitFor(() => {
      expect(result.current.previewData?.status).toBe("error")
      expect(result.current.previewData?.error).toBe("Request timed out after 120 seconds.")
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) =>
        t.type === "error" &&
        t.text.includes("Preview timed out for \"Rating step\"") &&
        t.text.includes("Request timed out after 120 seconds."),
      )).toBe(true)
    })
  })

  it("applies preview schema through the raw node setter without history or dirty churn", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [{ name: "premium", dtype: "f64" }],
      available_columns: [
        { name: "premium", dtype: "f64" },
        { name: "region", dtype: "str" },
      ],
      schema_warnings: [{ column: "region", status: "missing" }],
      preview: [{ premium: 120.5 }],
      row_count: 1,
      column_count: 1,
    })
    const node = makeNode("n1", "polars", {
      data: { label: "Rating step", nodeType: "polars", config: { expression: "df" } },
    })
    const params = makeParams()
    wireGraphStoreNodeSetters(params)

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      params.graphRef.current = { nodes: [node], edges: [] }
      useGraphStore.getState().setNodesRaw([node])
      useGraphStore.getState().markSaved()
      nodeSetterMock(params.setNodes).mockClear()
      nodeSetterMock(params.setNodesRaw).mockClear()
    })
    const {
      persistedFingerprint,
      savedPersistedFingerprint,
      structuralVersion,
      panelContextVersion,
    } = useGraphStore.getState()

    act(() => {
      result.current.fetchPreview(node, { debounceMs: 0 })
    })

    await waitFor(() => expect(result.current.previewData?.status).toBe("ok"))

    const state = useGraphStore.getState()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setNodesRaw).toHaveBeenCalledTimes(1)
    expect(state.nodes[0].data._columns).toEqual([{ name: "premium", dtype: "f64" }])
    expect(state.nodes[0].data._availableColumns).toEqual([
      { name: "premium", dtype: "f64" },
      { name: "region", dtype: "str" },
    ])
    expect(state.nodes[0].data._schemaWarnings).toEqual([{ column: "region", status: "missing" }])
    expect(state.undoStack).toHaveLength(0)
    expect(state.redoStack).toHaveLength(0)
    expect(state.persistedFingerprint).toBe(persistedFingerprint)
    expect(state.savedPersistedFingerprint).toBe(savedPersistedFingerprint)
    expect(state.dirty).toBe(false)
    expect(state.isDirty()).toBe(false)
    expect(state.structuralVersion).toBe(structuralVersion)
    expect(state.panelContextVersion).toBeGreaterThan(panelContextVersion)
  })

  it("keeps nodeStatuses when selectedNode is recreated with the same id", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [{ name: "a", dtype: "f64" }],
      preview: [{ a: 1 }],
      row_count: 1,
      column_count: 1,
      node_statuses: { n1: "ok", upstream: "error" },
    })
    const baseParams = makeParams()
    const { result, rerender } = renderHook(
      ({ selectedNode }: { selectedNode: Node | null }) =>
        usePipelineAPI({ ...baseParams, selectedNode }),
      { initialProps: { selectedNode: makeNode("n1") } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 })
    })
    await waitFor(() => expect(result.current.nodeStatuses).toEqual({ n1: "ok", upstream: "error" }))

    rerender({ selectedNode: makeNode("n1") })

    expect(result.current.nodeStatuses).toEqual({ n1: "ok", upstream: "error" })
  })

  // ── B10: nodeIdCounter from max ID suffix, not nodes.length ──────

  it("refreshPreview ignores in-flight upstream results after graph structure changes", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    const upstream = makeNode("upstream")
    const target = makeNode("target")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [upstream, target],
      edges: [{ id: "upstream-target", source: "upstream", target: "target" }],
    }

    let resolveUpstream!: (value: Awaited<ReturnType<typeof previewNode>>) => void
    mockPreview.mockImplementation(({ nodeId }) => {
      if (nodeId === "upstream") {
        return new Promise((resolve) => {
          resolveUpstream = resolve
        })
      }
      return Promise.resolve({
        node_id: nodeId,
        status: "ok",
        columns: [{ name: "target_col", dtype: "f64" }],
        preview: [{ target_col: 1 }],
        row_count: 1,
        column_count: 1,
      })
    })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(target)
    })

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))
    expect(mockPreview.mock.calls[0][0].nodeId).toBe("upstream")

    act(() => {
      useGraphStore.setState((state) => ({
        structuralVersion: state.structuralVersion + 1,
      }))
    })

    await act(async () => {
      resolveUpstream({
        node_id: "upstream",
        status: "ok",
        columns: [{ name: "late_col", dtype: "f64" }],
        preview: [{ late_col: 1 }],
        row_count: 1,
        column_count: 1,
      })
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(mockPreview).toHaveBeenCalledTimes(1)
  })

  it("refreshPreview applies upstream schema through the raw node setter", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const upstream = makeNode("upstream")
    const target = makeNode("target")
    const edge = makeEdge("upstream", "target")
    mockPreview.mockImplementation(({ nodeId }) => Promise.resolve(
      nodeId === "upstream"
        ? {
            node_id: "upstream",
            status: "ok",
            columns: [{ name: "upstream_col", dtype: "i64" }],
            preview: [{ upstream_col: 1 }],
            row_count: 1,
            column_count: 1,
          }
        : {
            node_id: "target",
            status: "ok",
            preview: [],
            row_count: 0,
            column_count: 0,
          },
    ))
    const params = makeParams()
    wireGraphStoreNodeSetters(params)

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      params.graphRef.current = { nodes: [upstream, target], edges: [edge] }
      useGraphStore.getState().setNodesRaw([upstream, target])
      useGraphStore.getState().setEdgesRaw([edge])
      useGraphStore.getState().markSaved()
      nodeSetterMock(params.setNodes).mockClear()
      nodeSetterMock(params.setNodesRaw).mockClear()
    })
    const { persistedFingerprint, structuralVersion } = useGraphStore.getState()

    act(() => {
      result.current.refreshPreview(target)
    })

    await waitFor(() => {
      expect(mockPreview.mock.calls.map(([call]) => call.nodeId)).toEqual(["upstream", "target"])
    })

    const state = useGraphStore.getState()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setNodesRaw).toHaveBeenCalledTimes(1)
    expect(state.nodes[0].data._columns).toEqual([{ name: "upstream_col", dtype: "i64" }])
    expect(state.undoStack).toHaveLength(0)
    expect(state.persistedFingerprint).toBe(persistedFingerprint)
    expect(state.dirty).toBe(false)
    expect(state.isDirty()).toBe(false)
    expect(state.structuralVersion).toBe(structuralVersion)
  })

  it("refreshPreview suppresses stale upstream warnings after graph structure changes", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    const upstream = makeNode("upstream", "polars", {
      data: { label: "Upstream", nodeType: "polars", config: {} },
    })
    const target = makeNode("target")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [upstream, target],
      edges: [{ id: "upstream-target", source: "upstream", target: "target" }],
    }

    let rejectUpstream!: (reason: unknown) => void
    mockPreview.mockImplementation(({ nodeId }) => {
      if (nodeId === "upstream") {
        return new Promise((_resolve, reject) => {
          rejectUpstream = reject
        })
      }
      return Promise.resolve({
        node_id: nodeId,
        status: "ok",
        columns: [{ name: "target_col", dtype: "f64" }],
        preview: [{ target_col: 1 }],
        row_count: 1,
        column_count: 1,
      })
    })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(target)
    })

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))

    act(() => {
      useGraphStore.setState((state) => ({
        structuralVersion: state.structuralVersion + 1,
      }))
    })

    await act(async () => {
      rejectUpstream(new Error("stale upstream failure"))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(useToastStore.getState().toasts).toEqual([])
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(mockPreview).toHaveBeenCalledTimes(1)
  })

  it("refreshPreview aborts stale upstream preview requests when superseded", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    const upstream = makeNode("upstream")
    const target = makeNode("target")
    const replacement = makeNode("replacement", "polars", {
      data: {
        label: "Replacement",
        nodeType: "polars",
        config: {},
        _columns: [{ name: "known_col", dtype: "f64" }],
      },
    })
    const params = makeParams()
    params.graphRef.current = {
      nodes: [upstream, target, replacement],
      edges: [{ id: "upstream-target", source: "upstream", target: "target" }],
    }

    const abortSignals: AbortSignal[] = []
    mockPreview.mockImplementation(({ signal }) => {
      if (signal) abortSignals.push(signal)
      return new Promise(() => {})
    })

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(target)
    })

    await waitFor(() => expect(abortSignals).toHaveLength(1))
    expect(abortSignals[0].aborted).toBe(false)

    act(() => {
      result.current.refreshPreview(replacement)
    })

    expect(abortSignals[0].aborted).toBe(true)
  })

  it("refreshPreview does not start the target preview after unmount aborts stale upstream work", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    const upstream = makeNode("upstream")
    const target = makeNode("target")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [upstream, target],
      edges: [{ id: "upstream-target", source: "upstream", target: "target" }],
    }

    mockPreview.mockImplementation(({ nodeId, signal }) => {
      if (nodeId === "target") {
        return Promise.resolve({
          node_id: "target",
          status: "ok",
          columns: [{ name: "target_col", dtype: "f64" }],
          preview: [{ target_col: 1 }],
          row_count: 1,
          column_count: 1,
        })
      }
      return new Promise((_, reject) => {
        signal?.addEventListener(
          "abort",
          () => reject(Object.assign(new Error("Aborted"), { name: "AbortError" })),
          { once: true },
        )
      })
    })

    const { result, unmount } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(target)
    })
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))

    unmount()
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(mockPreview).toHaveBeenCalledTimes(1)
    expect(mockPreview).not.toHaveBeenCalledWith(expect.objectContaining({ nodeId: "target" }))
  })

  it("refreshPreview caps concurrent stale upstream previews", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const callOrder: string[] = []
    const activeUpstream = new Set<string>()
    const deferreds = new Map<string, { resolve: (value: unknown) => void }>()
    let maxConcurrentUpstream = 0

    mockPreview.mockImplementation(({ nodeId }: { nodeId: string }) => {
      callOrder.push(nodeId)
      if (nodeId !== "target") {
        activeUpstream.add(nodeId)
        maxConcurrentUpstream = Math.max(maxConcurrentUpstream, activeUpstream.size)
      }
      return new Promise<Awaited<ReturnType<typeof previewNode>>>((resolve) => {
        deferreds.set(nodeId, {
          resolve: (value: unknown) => {
            activeUpstream.delete(nodeId)
            resolve(value as Awaited<ReturnType<typeof previewNode>>)
          },
        })
      })
    })

    const target = makeNode("target")
    const upstreamIds = Array.from(
      { length: DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT * 2 + 1 },
      (_, index) => `upstream-${index + 1}`,
    )
    const upstreamNodes = upstreamIds.map((id) => makeNode(id))
    const params = makeParams()
    params.graphRef.current = {
      nodes: [target, ...upstreamNodes],
      edges: upstreamIds.map((id) => makeEdge(id, "target")),
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.refreshPreview(target)
    })

    await waitFor(() => {
      expect(callOrder).toHaveLength(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT)
    })
    expect(activeUpstream.size).toBe(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT)
    expect(maxConcurrentUpstream).toBeLessThanOrEqual(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT)

    let resolvedUpstream = 0
    while (resolvedUpstream < upstreamIds.length) {
      const runningIds = [...activeUpstream]
      resolvedUpstream += runningIds.length
      act(() => {
        for (const nodeId of runningIds) {
          deferreds.get(nodeId)!.resolve({
            node_id: nodeId,
            status: "ok",
            columns: [{ name: `${nodeId}_col`, dtype: "f64" }],
            preview: [{ [`${nodeId}_col`]: 1 }],
            row_count: 1,
            column_count: 1,
          })
        }
      })

      const remaining = upstreamIds.length - resolvedUpstream
      await waitFor(() => {
        expect(activeUpstream.size).toBe(
          Math.min(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT, remaining),
        )
      })
      expect(maxConcurrentUpstream).toBeLessThanOrEqual(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT)
    }

    await waitFor(() => expect(callOrder).toContain("target"))
    expect(callOrder.filter((id) => id !== "target").sort()).toEqual([...upstreamIds].sort())
  })

  it("refreshPreview clears an older debounced preview before starting target preview", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "target",
      status: "ok",
      columns: [{ name: "target_col", dtype: "f64" }],
      preview: [{ target_col: 1 }],
      row_count: 1,
      column_count: 1,
    })

    const target = makeNode("target", "polars", {
      data: {
        label: "Target",
        nodeType: "polars",
        config: {},
        _columns: [{ name: "existing_col", dtype: "f64" }],
      },
    })
    const oldNode = makeNode("old")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [oldNode, target],
      edges: [],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    vi.useFakeTimers()
    try {
      act(() => {
        result.current.fetchPreview(oldNode, { debounceMs: 5_000 })
        result.current.refreshPreview(target)
      })

      await act(async () => {
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(mockPreview).toHaveBeenCalledTimes(1)
      expect(mockPreview.mock.calls[0][0].nodeId).toBe("target")

      act(() => {
        vi.advanceTimersByTime(5_000)
      })

      await act(async () => {
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(mockPreview).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it("sets nodeIdCounter from max numeric suffix, not nodes.length", async () => {
    // Simulate nodes with gaps: node_0, node_5 → length=2, but max suffix=5
    mockLoad.mockResolvedValue({
      nodes: [makeNode("transform_0"), makeNode("transform_5")],
      edges: [],
    })
    const params = makeParams()
    renderHook(() => usePipelineAPI(params))
    await waitFor(() => {
      // Counter should be max suffix (5) + 1 = 6, not nodes.length (2)
      expect(params.nodeIdCounter.current).toBe(6)
    })
  })

  it("sets nodeIdCounter to 0 when no nodes have numeric suffixes", async () => {
    mockLoad.mockResolvedValue({
      nodes: [makeNode("plain_node")],
      edges: [],
    })
    const params = makeParams()
    renderHook(() => usePipelineAPI(params))
    await waitFor(() => {
      // No _\d+ suffix match → max = -1, counter = -1 + 1 = 0
      expect(params.nodeIdCounter.current).toBe(0)
    })
  })

  it("sets nodeIdCounter to 0 when pipeline has no nodes", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const params = makeParams()
    renderHook(() => usePipelineAPI(params))
    await waitFor(() => {
      expect(params.nodeIdCounter.current).toBe(0)
    })
  })
})
