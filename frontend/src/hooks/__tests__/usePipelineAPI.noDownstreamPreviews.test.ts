/**
 * Previewing a node must not preview anything downstream of it.
 *
 * A preview used to cascade: when the previewed node's columns differed from
 * what the graph held, every node in its downstream closure was previewed too,
 * to refresh their `_columns`. Nothing read those columns. Every consumer
 * reads either the selected node's own columns or an *upstream* source's
 * (`NodePanel.edgeSourceColumns`), and the preview route already returns
 * ancestor schema metadata without materialising ancestors
 * (`include_schema_metadata=True`), so a descendant's columns were only ever
 * read after a preview of that descendant recomputed them anyway.
 *
 * The cascade therefore materialised frames to produce metadata that was
 * always superseded — and captured them into the shared node-data cache for
 * nodes the user was often about to edit. These tests pin its absence:
 *
 *   1. A node with downstream children previews only itself.
 *   2. That holds when the response genuinely changes the node's columns —
 *      the condition that used to trigger the cascade.
 *   3. It does not resume further down a chain.
 *
 * Upstream fan-out is a separate mechanism and still exists: see the refresh
 * tests in `usePipelineAPI.test.ts`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import type { MutableRefObject } from "react"
import usePipelineAPI from "../usePipelineAPI"

// A preview crosses a mocked request and several effects, which a whole
// parallel suite run makes slower without making it wrong. The deadline only
// has to fail a preview that never settles.
const PREVIEW_TIMEOUT_MS = 10_000

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
    status: number
    detail?: string

    constructor(msg: string, status?: number, detail?: string) {
      super(msg)
      this.name = "ApiError"
      this.status = status ?? Number(msg.match(/HTTP (\d+)/)?.[1] ?? 0)
      this.detail = detail
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

/** A `previewNode` mock whose per-node promises the test resolves on demand. */
function makeControllablePreview() {
  const callOrder: string[] = []
  const deferreds = new Map<string, { resolve: (v: unknown) => void; reject: (e: unknown) => void }>()
  mockPreview.mockImplementation(({ nodeId }: { nodeId: string }) => {
    callOrder.push(nodeId)
    return new Promise((resolve, reject) => {
      deferreds.set(nodeId, { resolve: resolve as (v: unknown) => void, reject })
    })
  })
  return { callOrder, deferreds }
}

function okResult(nodeId: string, columnName: string) {
  return {
    node_id: nodeId,
    status: "ok",
    row_count: 1,
    column_count: 1,
    columns: [{ name: columnName, dtype: "f64" }],
    preview: [{ [columnName]: 1 }],
  }
}

/**
 * Settle the preview and give any cascade that still existed the chance to
 * start: `previewBusy` clears in the request's own `.finally`, which runs
 * after every `.then` that could have queued downstream work.
 */
async function settlePreview(
  result: { current: { previewBusy: boolean } },
  deferreds: Map<string, { resolve: (v: unknown) => void }>,
  nodeId: string,
  columnName: string,
) {
  act(() => {
    deferreds.get(nodeId)!.resolve(okResult(nodeId, columnName))
  })
  await waitFor(() => expect(result.current.previewBusy).toBe(false), {
    timeout: PREVIEW_TIMEOUT_MS,
  })
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    // A macrotask too, so a cascade reintroduced behind a setTimeout or
    // requestIdleCallback could not slip past these assertions.
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

describe("usePipelineAPI — a preview never runs downstream nodes", () => {
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

  it("previews only the requested node when it has downstream children", async () => {
    mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
    const { callOrder, deferreds } = makeControllablePreview()

    const params = makeParams()
    params.graphRef.current = {
      nodes: [makeNode("A"), makeNode("B"), makeNode("C")],
      edges: [makeEdge("A", "B"), makeEdge("A", "C")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(params.graphRef.current.nodes[0]) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: PREVIEW_TIMEOUT_MS })

    await settlePreview(result, deferreds, "A", "a_col")

    expect(callOrder).toEqual(["A"])
  })

  it("previews only the requested node when the response changes its columns", async () => {
    mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
    const { callOrder, deferreds } = makeControllablePreview()

    // The node already carries columns, and the response returns different
    // ones. This is precisely the condition the removed cascade fired on.
    const A = makeNode("A", "polars", {
      data: { _columns: [{ name: "stale_col", dtype: "i64" }] },
    })
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, makeNode("B")],
      edges: [makeEdge("A", "B")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: PREVIEW_TIMEOUT_MS })

    await settlePreview(result, deferreds, "A", "fresh_col")

    expect(callOrder).toEqual(["A"])
  })

  it("does not resume further down a chain", async () => {
    mockLoad.mockResolvedValue(makeLoadedPipeline({ nodes: [], edges: [] }))
    const { callOrder, deferreds } = makeControllablePreview()

    const params = makeParams()
    params.graphRef.current = {
      nodes: [makeNode("A"), makeNode("B"), makeNode("C")],
      edges: [makeEdge("A", "B"), makeEdge("B", "C")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(params.graphRef.current.nodes[0]) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: PREVIEW_TIMEOUT_MS })

    await settlePreview(result, deferreds, "A", "a_col")

    expect(callOrder).not.toContain("B")
    expect(callOrder).not.toContain("C")
  })
})
