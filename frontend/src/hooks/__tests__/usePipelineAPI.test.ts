import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import usePipelineAPI from "../usePipelineAPI"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(),
  previewNode: vi.fn(),
  savePipeline: vi.fn(),
  ApiError: class ApiError extends Error {},
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
  })),
}))

import { loadPipeline, previewNode, savePipeline } from "../../api/client"
import { makeNode } from "../../test-utils/factories"
const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)
const mockSave = vi.mocked(savePipeline)

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

describe("usePipelineAPI", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live"] })
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
    })
    useNodeResultsStore.setState({ previews: {}, graphVersion: 0, columnCache: {} })
    mockLoad.mockReset()
    mockPreview.mockReset()

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
    // handleSave reads graphRef for the save payload, but markSaved()
    // captures from useGraphStore — keep the two in sync so isDirty()
    // reports false after save.
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
    // After save, useGraphStore.lastSavedSnapshot captures the current
    // state so isDirty() returns false — the new derived-dirty contract.
    expect(useGraphStore.getState().isDirty()).toBe(false)
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

  // ── B10: nodeIdCounter from max ID suffix, not nodes.length ──────

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
      nodes: [makeNode("legacy_node")],
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
