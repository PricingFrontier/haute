/**
 * Gap tests for usePipelineAPI — covers:
 *
 * 1. Preview error handling (ApiError vs AbortError distinction)
 * 2. Concurrent saves — second save while first is in flight
 * 3. Preview caching integration (cache hit skips API call)
 * 4. fetchPreview abort — new preview aborts the previous in-flight request
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import usePipelineAPI from "../usePipelineAPI"
import useToastStore from "../../stores/useToastStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import type { BackendNodeStatus } from "../../types/node"

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(),
  previewNode: vi.fn(),
  savePipeline: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail?: string

    constructor(msg: string, status = 0, detail?: string) {
      super(msg)
      this.name = "ApiError"
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
  })),
}))

import { loadPipeline, previewNode, savePipeline, ApiError } from "../../api/client"
import { makeNode } from "../../test-utils/factories"
const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)
const mockSave = vi.mocked(savePipeline)

async function advanceTimers(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
    await Promise.resolve()
    await Promise.resolve()
  })
}

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

describe("usePipelineAPI — gap tests", () => {
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
      structuralVersion: 0,
      structuralFingerprint: "nodes:||edges:||preamble:\"\"",
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

  // ────────────────────────────────────────────────────────────────
  // 1. Preview error handling — ApiError vs AbortError
  // ────────────────────────────────────────────────────────────────

  describe("preview error handling", () => {
    it("shows error preview when previewNode rejects with ApiError", async () => {
      // Catches: if the catch block doesn't distinguish ApiError from
      // AbortError, genuine server errors would be silently swallowed,
      // leaving the user staring at a loading spinner forever.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })

      const apiErr = new (ApiError as unknown as new (msg: string) => Error)("Column 'x' not found")
      mockPreview.mockRejectedValue(apiErr)

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      const node = makeNode("n1")

      act(() => {
        result.current.fetchPreview(node)
      })

      // fetchPreview debounces by 200ms — use real timers and just wait
      await waitFor(() => {
        expect(result.current.previewData?.status).toBe("error")
        expect(result.current.previewData?.error).toBe("Column 'x' not found")
      }, { timeout: 2000 })
    })

    it("does NOT show error preview when request is aborted (AbortError)", async () => {
      // Catches: if AbortError is treated as a real error, every time the
      // user clicks a different node (which aborts the previous request),
      // they'd see a brief red error flash.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })

      const abortErr = new DOMException("The operation was aborted.", "AbortError")
      mockPreview.mockRejectedValue(abortErr)

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      const node = makeNode("n1")

      vi.useFakeTimers()

      act(() => {
        result.current.fetchPreview(node)
      })

      // Wait for debounce + rejection to settle
      await advanceTimers(200)
      expect(mockPreview).toHaveBeenCalledTimes(1)

      // AbortError should be ignored — previewData should still be "loading", NOT "error"
      expect(result.current.previewData?.status).not.toBe("error")
    })

    it("does NOT show error preview when the backend supersedes an obsolete preview", async () => {
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })

      mockPreview.mockRejectedValue(
        new ApiError("HTTP 409", 409, "Preview request superseded by a newer request"),
      )

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()

      act(() => {
        result.current.fetchPreview(makeNode("n1"))
      })

      await advanceTimers(200)
      expect(mockPreview).toHaveBeenCalledTimes(1)
      expect(result.current.previewData?.status).not.toBe("error")
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 2. Concurrent saves
  // ────────────────────────────────────────────────────────────────

  describe("concurrent saves", () => {
    it("both saves complete independently (no save dedup)", async () => {
      // Catches: if handleSave had a guard preventing concurrent saves,
      // rapid Ctrl+S presses might silently skip the second save. The
      // current implementation does NOT deduplicate — both calls go through.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })

      let saveCallCount = 0
      mockSave.mockImplementation(() => {
        saveCallCount++
        return Promise.resolve({ file: `pricing_${saveCallCount}.py`, pipeline_name: "pricing" })
      })

      const params = makeParams()
      params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }

      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      // Fire two saves without awaiting the first
      await act(async () => {
        result.current.handleSave()
        result.current.handleSave()
      })

      await waitFor(() => {
        expect(saveCallCount).toBe(2)
      })
    })

    it("second save failure does not corrupt lastSavedSnapshot from first success", async () => {
      // Catches: if the failure path of the second save somehow cleared
      // lastSavedSnapshot, dirty derivation would incorrectly mark the
      // graph as unsaved even though the first save persisted the data.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })

      let callIdx = 0
      mockSave.mockImplementation(() => {
        callIdx++
        if (callIdx === 1) return Promise.resolve({ file: "ok.py", pipeline_name: "p" })
        return Promise.reject(new Error("conflict"))
      })

      const params = makeParams()
      params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
      // markSaved captures from useGraphStore, keep the two in sync.
      useGraphStore.setState({ nodes: [makeNode("n1")], edges: [], preamble: "" })

      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      await act(async () => {
        result.current.handleSave()
        result.current.handleSave()
      })

      // Wait for both promises to settle
      await waitFor(() => {
        const toasts = useToastStore.getState().toasts
        // Should have both a success and an error toast
        expect(toasts.some((t) => t.type === "success")).toBe(true)
        expect(toasts.some((t) => t.type === "error")).toBe(true)
      })

      // After the first success, lastSavedSnapshot should be non-null.
      // The failing second save's catch block must NOT touch it.
      expect(useGraphStore.getState().lastSavedSnapshot).not.toBeNull()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 3. Preview caching integration
  // ────────────────────────────────────────────────────────────────

  describe("preview caching", () => {
    it("shows cached preview immediately without API call when cache is fresh", async () => {
      // Catches: if the cache-first logic in fetchPreviewImmediate is broken,
      // every node click would hit the API even when data is fresh, causing
      // unnecessary loading spinners and server load.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 2,
        columns: [{ name: "a", dtype: "f64" }],
        preview: [{ a: 1 }],
        error: null,
        timing_ms: 5,
        memory_bytes: 100,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      // Populate after the initial load: a whole-document load is an
      // intentional cache-identity boundary and advances structuralVersion.
      const structuralVersion = useGraphStore.getState().structuralVersion
      useNodeResultsStore.setState({
        previews: {
          n1: { data: cachedData, structuralVersion, source: "live", rowLimit: 1000 },
        },
        columnCache: {},
      })

      const node = makeNode("n1")

      vi.useFakeTimers()

      act(() => {
        result.current.fetchPreview(node)
      })

      // fetchPreview shows cached data immediately (before debounce fires)
      expect(result.current.previewData?.nodeId).toBe("n1")
      expect(result.current.previewData?.row_count).toBe(10)

      await advanceTimers(200)

      // API should NOT have been called (cache was fresh for the same structuralVersion)
      expect(mockPreview).not.toHaveBeenCalled()
    })

    it("honours a custom preview debounce before starting the API request", async () => {
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })
      mockPreview.mockResolvedValue({
        node_id: "n1",
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      })

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()
      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 800 })
      })

      expect(result.current.previewData?.nodeId).toBe("n1")
      expect(result.current.previewData?.status).toBe("loading")

      act(() => {
        vi.advanceTimersByTime(799)
      })
      expect(mockPreview).not.toHaveBeenCalled()

      await act(async () => {
        vi.advanceTimersByTime(1)
        await Promise.resolve()
      })
      expect(mockPreview).toHaveBeenCalledTimes(1)
    })

    it("uses graph structuralVersion to decide preview freshness", async () => {
      // Catches: if fetchPreview stops checking structuralVersion, a fresh
      // structural graph change would be missed and the cached preview would
      // be treated as current.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 2,
        columns: [{ name: "a", dtype: "f64" }],
        preview: [{ a: 1 }],
        error: null,
        timing_ms: 5,
        memory_bytes: 100,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      useNodeResultsStore.setState({
        previews: { n1: { data: cachedData, structuralVersion: 3, source: "live", rowLimit: 1000 } },
        columnCache: {},
      })
      useGraphStore.setState({ structuralVersion: 4 })
      mockPreview.mockResolvedValue({
        node_id: "n1",
        status: "ok",
        columns: [{ name: "b", dtype: "i64" }],
        preview: [{ b: 2 }],
        row_count: 20,
        column_count: 1,
      })

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      const node = makeNode("n1")

      act(() => {
        result.current.fetchPreview(node)
      })

      await waitFor(() => {
        expect(mockPreview).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it("fetches from API when cache exists but structuralVersion is stale", async () => {
      // Catches: if the structuralVersion check is removed, the cache would
      // always be considered fresh, showing stale data after the user
      // modifies a node's config.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })
      mockPreview.mockResolvedValue({
        node_id: "n1",
        status: "ok",
        columns: [{ name: "b", dtype: "i64" }],
        preview: [{ b: 2 }],
        row_count: 20,
        column_count: 1,
      })

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 2,
        columns: [],
        preview: [],
        error: null,
        timing_ms: 0,
        memory_bytes: 0,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      // Cache at version 0, but graph store is at version 5 (stale)
      useNodeResultsStore.setState({
        previews: { n1: { data: cachedData, structuralVersion: 0, source: "live", rowLimit: 1000 } },
        columnCache: {},
      })
      useGraphStore.setState({ structuralVersion: 5 })

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      const node = makeNode("n1")

      act(() => {
        result.current.fetchPreview(node)
      })

      // API should be called since cache is stale (after debounce)
      await waitFor(() => {
        expect(mockPreview).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it("refetches when cached preview source differs from active source", async () => {
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })
      mockPreview.mockResolvedValue({
        node_id: "n1",
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      })

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 1,
        columns: [],
        preview: [],
        error: null,
        timing_ms: 0,
        memory_bytes: 0,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      useNodeResultsStore.setState({
        previews: { n1: { data: cachedData, structuralVersion: 0, source: "backtest", rowLimit: 1000 } },
        columnCache: {},
      })

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 })
      })

      await waitFor(() => {
        expect(mockPreview).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it("refetches when cached preview row limit differs from current row limit", async () => {
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })
      mockPreview.mockResolvedValue({
        node_id: "n1",
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      })

      const cachedData = {
        nodeId: "n1",
        nodeLabel: "Node n1",
        status: "ok" as const,
        row_count: 10,
        column_count: 1,
        columns: [],
        preview: [],
        error: null,
        timing_ms: 0,
        memory_bytes: 0,
        timings: [],
        memory: [],
        schema_warnings: [],
      }

      useSettingsStore.setState({ rowLimit: 250 })
      useNodeResultsStore.setState({
        previews: { n1: { data: cachedData, structuralVersion: 0, source: "live", rowLimit: 1000 } },
        columnCache: {},
      })

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 })
      })

      await waitFor(() => {
        expect(mockPreview).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it("terminalizes in-flight preview results after graph execution context changes without applying graph effects", async () => {
      // W0 contract: a structuralVersion bump while the response is in
      // flight must not strand the panel on "loading" — the response still
      // renders as the panel's terminal state. Graph-coupled effects stay
      // version-gated: stale columns are never written into the changed
      // graph (no setNodes/cascade), and no cache entry is created for a
      // node that is absent from the live graph.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })
      let resolvePreview!: (value: {
        node_id: string
        status: BackendNodeStatus
        columns: { name: string; dtype: string }[]
        preview: { stale: number }[]
        row_count: number
        column_count: number
      }) => void
      mockPreview.mockReturnValue(new Promise((resolve) => {
        resolvePreview = resolve
      }))

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      act(() => {
        result.current.fetchPreview(makeNode("n1"))
      })
      await waitFor(() => expect(mockPreview).toHaveBeenCalled(), { timeout: 2000 })

      act(() => {
        useGraphStore.getState().setPreambleRaw("import polars as pl")
      })

      await act(async () => {
        resolvePreview({
          node_id: "n1",
          status: "ok",
          columns: [{ name: "stale", dtype: "i64" }],
          preview: [{ stale: 1 }],
          row_count: 1,
          column_count: 1,
        })
        await Promise.resolve()
      })

      expect(result.current.previewData?.status).toBe("ok")
      expect(result.current.previewData?.row_count).toBe(1)
      expect(result.current.previewBusy).toBe(false)
      expect(params.setNodes).not.toHaveBeenCalled()
      expect(useNodeResultsStore.getState().previews.n1).toBeUndefined()
    })
  })

  // ────────────────────────────────────────────────────────────────
  // 4. fetchPreview abort
  // ────────────────────────────────────────────────────────────────

  describe("fetchPreview abort on new request", () => {
    it("cancelPreview clears a pending debounced preview before the API request starts", async () => {
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })
      mockPreview.mockResolvedValue({
        node_id: "n1",
        status: "ok",
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
      })

      const { result } = renderHook(() => usePipelineAPI(makeParams()))
      await waitFor(() => expect(result.current.loading).toBe(false))

      vi.useFakeTimers()
      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 800 })
        result.current.cancelPreview()
        vi.advanceTimersByTime(800)
      })

      expect(mockPreview).not.toHaveBeenCalled()
    })

    it("aborts previous in-flight request when a new fetchPreview is called", async () => {
      // Catches: without abort, the response from a slow first request
      // could overwrite the fresher second request's data, showing the
      // wrong node's preview.
      mockLoad.mockResolvedValue({ nodes: [], edges: [] })

      const abortSignals: AbortSignal[] = []
      mockPreview.mockImplementation((args: { signal?: AbortSignal }) => {
        if (args.signal) abortSignals.push(args.signal)
        return new Promise(() => {}) // never resolves
      })

      const params = makeParams()
      const { result } = renderHook(() => usePipelineAPI(params))
      await waitFor(() => expect(result.current.loading).toBe(false))

      // First preview — triggers debounce
      act(() => {
        result.current.fetchPreview(makeNode("n1"), { debounceMs: 0 })
      })

      // Wait for debounce to fire the first fetchPreviewImmediate
      await waitFor(() => expect(abortSignals.length).toBe(1))

      // Second preview — should abort the first
      act(() => {
        result.current.fetchPreview(makeNode("n2"), { debounceMs: 0 })
      })

      // Wait for second debounce to fire
      expect(abortSignals[0].aborted).toBe(true)
      await waitFor(() => expect(abortSignals.length).toBeGreaterThanOrEqual(2))

      // The first signal should have been aborted
      expect(abortSignals.length).toBeGreaterThanOrEqual(1)
      expect(abortSignals[0].aborted).toBe(true)
    })
  })
})
