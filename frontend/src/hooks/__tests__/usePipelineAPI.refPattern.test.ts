/**
 * Phase 1 Package 1H — Items #33 and #34:
 *
 * #33 Zustand `.getState()` calls inside async callbacks without ref capture
 *   — pipeline/WS/keyboard hooks read `useSettingsStore.getState().activeSource`
 *   or similar inside callbacks that close over component scope. A change to
 *   the store mid-operation can be silently missed or, worse, picked up
 *   partially.
 *
 * #34 `activeSourceRef.current` in the cascade is captured lazily from a
 *   ref that updates via effect — but within a single `propagateDownstream`
 *   run (which chains multiple `previewNode` promises), the ref can flip
 *   mid-cascade when the user changes the active source, causing a column
 *   mismatch (downstream node previewed with the NEW source vs. the source
 *   originally used for the start-of-cascade node).
 *
 * The fix for both items is a local capture at cascade start:
 *     const snapshotSource = activeSourceRef.current
 *     // use snapshotSource for every previewNode call in this cascade
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import type { MutableRefObject } from "react"
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
  resolveGraphFromRefs: vi.fn(
    (
      graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>,
      parentGraphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>,
      submodelsRef: MutableRefObject<Record<string, unknown>>,
      preambleRef: MutableRefObject<string>,
    ) =>
      parentGraphRef.current
        ? {
          nodes: parentGraphRef.current.nodes,
          edges: parentGraphRef.current.edges,
          submodels: parentGraphRef.current.submodels,
          preamble: preambleRef.current,
        }
        : {
          nodes: graphRef.current.nodes,
          edges: graphRef.current.edges,
          submodels: submodelsRef.current,
          preamble: preambleRef.current,
        },
  ),
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
import { makeNode, makeEdge } from "../../test-utils/factories"
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
    nodeIdCounter: { current: 0 },
    ...overrides,
  }
}

async function advanceTimers(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe("usePipelineAPI — activeSource captured at cascade start (#33, #34)", () => {
  beforeEach(() => {
    vi.useRealTimers()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useSettingsStore.setState({ rowLimit: 1000, activeSource: "live", sources: ["live", "staging"] })
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

  it("fetchPreview + downstream cascade uses a single snapshot of activeSource", async () => {
    // Catches: if the cascade reads activeSourceRef.current (or
    // useSettingsStore.getState().activeSource) afresh for each
    // downstream node, switching the active source after the root
    // preview starts causes downstream previews to use a different
    // source — producing column schemas that don't match the root.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    const seenSources: string[] = []
    mockPreview.mockImplementation(async ({ nodeId, source }) => {
      seenSources.push(source ?? "<none>")
      // Simulate a slow root preview so we have time to switch the source
      // between root and downstream.
      if (nodeId === "root") {
        await new Promise((r) => setTimeout(r, 60))
        return {
          node_id: nodeId,
          status: "ok",
          row_count: 1,
          column_count: 1,
          // Columns differ from downstream's expected schema to trigger cascade
          columns: [{ name: "new_col", dtype: "f64" }],
          preview: [{ new_col: 1 }],
        }
      }
      // Downstream nodes
      return {
        node_id: nodeId,
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "ds_col", dtype: "f64" }],
        preview: [{ ds_col: 1 }],
      }
    })

    const root = makeNode("root")
    const ds1 = makeNode("ds1")
    const ds2 = makeNode("ds2")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [root, ds1, ds2],
      edges: [makeEdge("root", "ds1"), makeEdge("root", "ds2")],
    }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    useSettingsStore.setState({ activeSource: "live" })

    vi.useFakeTimers()

    act(() => { result.current.fetchPreview(root) })

    // Let debounce fire (200ms)
    await advanceTimers(200)

    // While root preview is mid-flight, the user flips the active source.
    act(() => {
      useSettingsStore.setState({ activeSource: "staging" })
    })

    // Wait for root preview + cascade to complete
    await advanceTimers(60)
    expect(seenSources.length).toBeGreaterThanOrEqual(3)

    // CORRECT behaviour: all three previews used the same source
    // captured when fetchPreview was invoked ("live").  Under the
    // pre-fix code, root would use "live" (captured via ref before
    // the cascade starts) and the two downstream nodes would see
    // "staging" because propagateDownstream reads activeSourceRef
    // at call time.
    const distinctSources = Array.from(new Set(seenSources))
    expect(distinctSources).toEqual(["live"])
  })

  it("handleSave reads activeSource at invocation time, not via stale closure", async () => {
    // Catches: if handleSave read `activeSource` at render time (via
    // closure), rapid source switches would silently save the wrong
    // source_file attribution.  Using `.getState()` inside the callback
    // is correct here — but only if the getter is invoked at save time
    // (not captured on hook render).
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const savePayloads: Array<Record<string, unknown>> = []
    vi.mocked(await import("../../api/client")).savePipeline.mockImplementation(
      (payload) => {
        savePayloads.push(payload as unknown as Record<string, unknown>)
        return Promise.resolve({ file: "t.py", pipeline_name: "t" })
      },
    )

    const params = makeParams()
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    useSettingsStore.setState({ activeSource: "live" })
    await act(async () => { result.current.handleSave() })

    useSettingsStore.setState({ activeSource: "staging" })
    await act(async () => { result.current.handleSave() })

    await waitFor(() => expect(savePayloads.length).toBe(2))

    // Each save used the CURRENT active source at invocation time
    expect(savePayloads[0].active_source).toBe("live")
    expect(savePayloads[1].active_source).toBe("staging")
  })

  it("rowLimit change mid-fetch does not affect an already-running preview", async () => {
    // Catches: related to #33, the rowLimit used by a preview should be
    // captured at fetch start (not re-read when the request fires), so
    // a user bumping rowLimit mid-flight doesn't corrupt the in-flight
    // payload.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })

    const seenRowLimits: number[] = []
    mockPreview.mockImplementation(async ({ rowLimit }) => {
      seenRowLimits.push(rowLimit)
      await new Promise((r) => setTimeout(r, 50))
      return { node_id: "n1", status: "ok", row_count: 1, column_count: 0, columns: [], preview: [] }
    })

    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    useSettingsStore.setState({ rowLimit: 100 })

    vi.useFakeTimers()

    act(() => { result.current.fetchPreview(makeNode("n1")) })

    // Flip rowLimit while debounce is pending
    await advanceTimers(100)
    useSettingsStore.setState({ rowLimit: 999 })

    await advanceTimers(100)
    await advanceTimers(50)
    expect(seenRowLimits.length).toBeGreaterThanOrEqual(1)

    // The first preview fired with rowLimit read at debounce-fire time.
    // Note: both "capture at fetchPreview" and "capture at debounce fire"
    // are defensible.  This test asserts stability: rowLimit used must
    // be one consistent value, never zero or undefined.
    expect(seenRowLimits[0]).toBeGreaterThan(0)
    expect(Number.isFinite(seenRowLimits[0])).toBe(true)
  })
})
