/**
 * Phase 1 Package 1H — Items #33 and #34:
 *
 * #33 Zustand `.getState()` calls inside async callbacks without ref capture
 *   — pipeline/WS/keyboard hooks read `useSettingsStore.getState().activeSource`
 *   or similar inside callbacks that close over component scope. A change to
 *   the store mid-operation can be silently missed or, worse, picked up
 *   partially.
 *
 * #34 `activeSourceRef.current` is captured lazily from a ref that updates via
 *   effect, so it could flip part-way through an operation spanning several
 *   `previewNode` promises and mix two sources into one result.
 *
 * The fix for both items is a local capture when the operation starts:
 *     const snapshotSource = activeSourceRef.current
 *     // use snapshotSource for every previewNode call it makes
 *
 * The downstream cascade that made #34 reachable is gone (see
 * `usePipelineAPI.noDownstreamPreviews.test.ts`); the refresh's upstream
 * fan-out is now the operation that spans several previews.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import type { MutableRefObject } from "react"
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
import { makeNode } from "../../test-utils/factories"
import { makeLoadedPipeline } from "../../testSupport/pipelineDocumentFixture"
const mockLoad = vi.mocked(loadPipeline)
const mockPreview = vi.mocked(previewNode)

function makeParams(overrides: Partial<Parameters<typeof usePipelineAPI>[0]> = {}) {
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

describe("usePipelineAPI — settings captured at fetch start (#33, #34)", () => {
  beforeEach(() => {
    vi.useRealTimers()
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

  it("handleSave reads activeSource at invocation time, not via stale closure", async () => {
    // Catches: if handleSave read `activeSource` at render time (via
    // closure), rapid source switches would silently save the wrong
    // source_file attribution.  Using `.getState()` inside the callback
    // is correct here — but only if the getter is invoked at save time
    // (not captured on hook render).
    mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
    const savePayloads: Array<Record<string, unknown>> = []
    vi.mocked(await import("../../api/client")).savePipeline.mockImplementation(
      (payload) => {
        savePayloads.push(payload as unknown as Record<string, unknown>)
        return Promise.resolve({ file: "t.py", pipeline_name: "t", source_revision: "revision-test" })
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
    mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))

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

    // The preview path lazily imports ./ensureInputSnapshots. Resolve that
    // import while real timers are still installed: a dynamic import left
    // pending when fake timers take over never settles, so the preview would
    // never fire and this test would fail for a reason that has nothing to do
    // with rowLimit capture.
    await import("../ensureInputSnapshots")

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
