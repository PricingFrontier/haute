/**
 * W0 baseline — preview responses must reach a terminal state when the
 * graph's structuralVersion changes while the request is in flight.
 *
 * Repro (deterministic on Windows, e2e "real optimiser flow"): clicking the
 * browser_apply node fires fetchPreview → fetchPreviewImmediate captures
 * structuralVersion=V and issues the API call. The same click mounts
 * OptimiserApplyEditor, whose artifact-load effect resolves mid-flight and
 * mirrors `optimiser_mode` into node config (`onUpdate` → setNodes), bumping
 * structuralVersion to V+1. The response then arrives, `requestStillCurrent()`
 * is false, and the entire success envelope is silently dropped — previewData
 * is stranded on `{status:"loading"}` ("Running..." / "Executing pipeline...")
 * forever: previewBusy clears, no error surfaces, no retry is issued.
 *
 * Terminal-state contract pinned here:
 *   - superseded by a NEWER request (seq mismatch) → drop; the newer request
 *     owns the panel (covered by usePipelineAPI.abortStale.test.ts);
 *   - structuralVersion changed but seq still current → the panel must still
 *     terminalize (data or error). Graph mutation (column application +
 *     downstream cascade) stays version-gated so stale columns are never
 *     written into a restructured graph;
 *   - node deleted mid-flight → handleDeleteNode already cleared the panel;
 *     the late response must not resurrect it or re-create cache entries.
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
    status: number
    detail?: string

    constructor(message: string, status: number, detail?: string) {
      super(message)
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

import { loadPipeline, previewNode } from "../../api/client"
import { makeNode } from "../../test-utils/factories"
const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)

type PreviewEnvelope = Awaited<ReturnType<typeof previewNode>>

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

const okEnvelope: PreviewEnvelope = {
  node_id: "browser_apply",
  status: "ok",
  columns: [
    { name: "optimal_scenario_value", dtype: "f64" },
    { name: "__optimiser_version__", dtype: "str" },
  ],
  preview: [
    { optimal_scenario_value: 1.25, __optimiser_version__: "v1" },
    { optimal_scenario_value: 2.5, __optimiser_version__: "v1" },
  ],
  row_count: 2,
  column_count: 2,
  node_statuses: { browser_apply: "ok" },
}

describe("usePipelineAPI — preview lifecycle terminal states (W0)", () => {
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

  it("renders a preview response that arrives after a mid-flight structuralVersion bump", async () => {
    // Catches the W0 hang: the success envelope was dropped by
    // `requestStillCurrent()` because OptimiserApplyEditor's artifact-load
    // effect bumped structuralVersion while the request was in flight,
    // stranding the panel on "Executing pipeline..." with no further
    // requests and no error.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    let resolvePreview!: (value: PreviewEnvelope) => void
    mockPreview.mockImplementation(() => new Promise((resolve) => {
      resolvePreview = resolve
    }))

    const applyNode = makeNode("browser_apply", "optimiserApply")
    const params = makeParams()
    params.graphRef.current = { nodes: [applyNode], edges: [] }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(applyNode, { debounceMs: 0 }) })
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))
    expect(result.current.previewData?.status).toBe("loading")

    // Mid-flight: the apply editor mirrors artifact metadata into node
    // config (onUpdate → setNodes), which bumps structuralVersion.
    act(() => {
      useGraphStore.setState((state) => ({
        structuralVersion: state.structuralVersion + 1,
      }))
    })

    await act(async () => {
      resolvePreview(okEnvelope)
      await Promise.resolve()
      await Promise.resolve()
    })

    // Terminal state: the response renders instead of stranding "loading".
    expect(result.current.previewData?.status).toBe("ok")
    expect(result.current.previewData?.nodeId).toBe("browser_apply")
    expect(result.current.previewData?.row_count).toBe(2)
    expect(result.current.previewData?.preview).toEqual(okEnvelope.preview)
    expect(result.current.previewBusy).toBe(false)
    // No silent retry: exactly the one request the click issued.
    expect(mockPreview).toHaveBeenCalledTimes(1)

    // Graph mutation stays version-gated: no column application or
    // downstream cascade from a response computed against the old graph.
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(result.current.nodeStatuses).toEqual({})

    // The result is cached under the fetch-time version, so the next
    // preview sees a context mismatch and refetches in the background.
    const cached = useNodeResultsStore.getState().getPreview("browser_apply")
    expect(cached?.data.status).toBe("ok")
    expect(cached?.structuralVersion).toBe(0)
  })

  it("surfaces a preview failure that arrives after a mid-flight structuralVersion bump", async () => {
    // Same interleave as above but the backend fails: the panel must show
    // the error, never an eternal "loading" with no error surfaced.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    let rejectPreview!: (reason: unknown) => void
    mockPreview.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectPreview = reject
    }))

    const applyNode = makeNode("browser_apply", "optimiserApply")
    const params = makeParams()
    params.graphRef.current = { nodes: [applyNode], edges: [] }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(applyNode, { debounceMs: 0 }) })
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))

    act(() => {
      useGraphStore.setState((state) => ({
        structuralVersion: state.structuralVersion + 1,
      }))
    })

    await act(async () => {
      rejectPreview(new Error("optimiser artifact not found"))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(result.current.previewData?.status).toBe("error")
    expect(result.current.previewData?.error).toContain("optimiser artifact not found")
    expect(result.current.previewBusy).toBe(false)
  })

  it("does not resurrect the panel or cache for a node deleted while its preview was in flight", async () => {
    // handleDeleteNode clears previewData to null and removes the node;
    // the late response must keep that terminal state (no panel for a
    // node that no longer exists, no orphaned cache entry).
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    let resolvePreview!: (value: PreviewEnvelope) => void
    mockPreview.mockImplementation(() => new Promise((resolve) => {
      resolvePreview = resolve
    }))

    const applyNode = makeNode("browser_apply", "optimiserApply")
    const params = makeParams()
    params.graphRef.current = { nodes: [applyNode], edges: [] }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(applyNode, { debounceMs: 0 }) })
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))

    // Delete the node mid-flight: panel cleared, node removed from the
    // live graph, structuralVersion bumped.
    params.graphRef.current = { nodes: [], edges: [] }
    act(() => {
      result.current.setPreviewData(null)
      useGraphStore.setState((state) => ({
        structuralVersion: state.structuralVersion + 1,
      }))
    })

    await act(async () => {
      resolvePreview(okEnvelope)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(result.current.previewData).toBeNull()
    expect(useNodeResultsStore.getState().getPreview("browser_apply")).toBeNull()
    expect(result.current.previewBusy).toBe(false)
  })
})
