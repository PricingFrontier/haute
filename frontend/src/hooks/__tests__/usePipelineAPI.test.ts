import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import usePipelineAPI, { PREVIEW_INITIAL_COLUMN_LIMIT } from "../usePipelineAPI"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
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

import { ApiError, loadPipeline, previewNode, savePipeline } from "../../api/client"
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
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
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

  it("loads successful backend responses with nullable metadata", async () => {
    mockLoad.mockResolvedValue({
      nodes: [],
      edges: [],
      pipeline_name: null,
      pipeline_description: null,
      preamble: null,
      source_file: null,
      submodels: null,
      warning: null,
    })

    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))

    await waitFor(() => expect(result.current.loading).toBe(false))

    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "error" && t.text.includes("Failed to load pipeline"))).toBe(false)
    expect(params.setNodesRaw).toHaveBeenCalledWith([])
    expect(params.setEdgesRaw).toHaveBeenCalledWith([])
    expect(params.setPreamble).not.toHaveBeenCalled()
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

  it("keeps nodeStatuses when selectedNode is recreated with the same id", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    mockPreview.mockResolvedValue({
      node_id: "n1",
      status: "ok",
      columns: [{ name: "a", dtype: "f64" }],
      preview: [{ a: 1 }],
      row_count: 1,
      column_count: 1,
      node_statuses: { n1: "ok", upstream: "running" },
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
    await waitFor(() => expect(result.current.nodeStatuses).toEqual({ n1: "ok", upstream: "running" }))

    rerender({ selectedNode: makeNode("n1") })

    expect(result.current.nodeStatuses).toEqual({ n1: "ok", upstream: "running" })
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
