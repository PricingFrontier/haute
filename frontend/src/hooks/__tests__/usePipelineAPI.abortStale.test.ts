/**
 * Phase 1 Package 1H — Item #31: aborted preview fetch must clear previewData
 * for the previous node before returning; user never sees prior node's data
 * on the new node's panel.
 *
 * Pre-fix: the catch block treats AbortError as a silent no-op, leaving the
 * previous `previewData` in place.  The bug surfaces when a user clicks node
 * A (fetch starts), then quickly clicks node B: node B shows a momentary
 * loading flash, but if the request is aborted before node B's request
 * completes, previewData is never cleared, so node B's panel still shows
 * node A's rows.
 */
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
  ApiError: class ApiError extends Error {
    constructor(msg: string) {
      super(msg)
      this.name = "ApiError"
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

import { loadPipeline, previewNode } from "../../api/client"
import { makeNode } from "../../test-utils/factories"
const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)

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
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    nodeIdCounter: { current: 0 },
    ...overrides,
  }
}

describe("usePipelineAPI — aborted preview clears stale data (#31)", () => {
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
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
    vi.restoreAllMocks()
  })

  it("switching node while a preview is aborted clears prior node's previewData", async () => {
    // Catches: silent AbortError handling leaves `previewData.nodeId` equal
    // to the *old* node even after the user has clicked a new node. The
    // panel then shows A's rows under B's title.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    // Node A resolves successfully with columns/preview
    // Node B aborts in-flight — pre-fix, previewData stays stuck on A.
    mockPreview.mockImplementation(async ({ nodeId, signal }) => {
      if (nodeId === "A") {
        return {
          node_id: "A",
          status: "ok",
          row_count: 3,
          column_count: 1,
          columns: [{ name: "col_a", dtype: "f64" }],
          preview: [{ col_a: 1 }, { col_a: 2 }, { col_a: 3 }],
        }
      }
      // B: never resolves; will be aborted by the next fetch
      return new Promise((_res, rej) => {
        signal?.addEventListener("abort", () => {
          const e = new DOMException("aborted", "AbortError")
          rej(e)
        })
      })
    })

    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    // 1. Preview A — wait for resolution (not just the loading placeholder)
    act(() => { result.current.fetchPreview(makeNode("A")) })
    await waitFor(() => {
      expect(result.current.previewData?.nodeId).toBe("A")
      expect(result.current.previewData?.status).toBe("ok")
      expect(result.current.previewData?.row_count).toBe(3)
    }, { timeout: 2000 })

    // 2. Switch to B — aborts A's request (A already resolved, but for B
    //    we expect the new loading state) and starts B's fetch.
    act(() => { result.current.fetchPreview(makeNode("B")) })

    // 3. Immediately switch to C — this aborts B's pending request.
    //    Pre-fix: AbortError in B's catch block is swallowed, but the
    //    loading placeholder for C should not let stale A data linger.
    act(() => { result.current.fetchPreview(makeNode("C")) })

    // After all switches, previewData must reference the LATEST node (C),
    // or a loading state for C — NEVER the stale nodeId "A".
    await waitFor(() => {
      expect(result.current.previewData?.nodeId).not.toBe("A")
    }, { timeout: 2000 })
    expect(result.current.previewData?.nodeId).not.toBe("A")
  })

  it("aborted fetch does NOT re-render the stale node's rows onto the new node's panel", async () => {
    // More precise test: an already-aborted response that races to resolve
    // must not set previewData because `previewAbort.current.signal.aborted`
    // is true by the time the .then() runs.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    // Simulate a slow request for A that will be aborted mid-flight.
    let aSignal: AbortSignal | undefined
    mockPreview.mockImplementation(async ({ nodeId, signal }) => {
      if (nodeId === "A") {
        aSignal = signal
        return new Promise((resolve) => {
          // Resolve A only after the user has already moved on.
          setTimeout(() => {
            resolve({
              node_id: "A",
              status: "ok",
              row_count: 99,
              column_count: 1,
              columns: [{ name: "stale", dtype: "f64" }],
              preview: Array.from({ length: 99 }, (_, i) => ({ stale: i })),
            })
          }, 10_000)
        })
      }
      // B's request just hangs — we care about A's late response not
      // clobbering B's loading placeholder.
      return new Promise(() => {})
    })

    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    vi.useFakeTimers()

    act(() => { result.current.fetchPreview(makeNode("A")) })
    // Wait for debounce to fire so A's request is in flight
    await act(async () => {
      vi.advanceTimersByTime(200)
      await Promise.resolve()
    })
    expect(aSignal).toBeDefined()

    // Switch to B: the hook aborts A immediately when selection changes.
    act(() => { result.current.fetchPreview(makeNode("B")) })
    expect(aSignal?.aborted).toBe(true)

    // Let A's late response race through the .then().
    await act(async () => {
      vi.advanceTimersByTime(10_000)
      await Promise.resolve()
    })

    // previewData must never be equal to A's stale rows
    expect(result.current.previewData?.nodeId).not.toBe("A")
    expect(result.current.previewData?.row_count).not.toBe(99)
  })
})
