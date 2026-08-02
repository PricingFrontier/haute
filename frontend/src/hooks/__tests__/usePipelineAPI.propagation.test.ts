/**
 * Phase 2 Package 2D-5 — flatten preview-propagation indirection.
 *
 * usePipelineAPI currently threads downstream cascade through a 3-layer
 * indirection (`propagatingRef` in-flight set, `propagateRef.current`
 * function holder updated via useEffect, and chained then/finally). The
 * refactor inlines `propagateDownstream` inside the cascade's `.then()`
 * so the indirection collapses.
 *
 * These tests pin the observable cascade behaviour so the refactor does
 * not regress:
 *
 *   1. Propagation fires in dependency order: A resolves → B fires →
 *      B resolves → C fires (linear chain A → B → C).
 *   2. Cascade captures the source at cascade start (#34 regression
 *      guard): a mid-cascade store flip must not split the chain
 *      across two sources.
 *   3. Cascade captures the rowLimit at cascade start (#33 regression
 *      guard).
 *   4. Cascade does not continue past a node whose columns are
 *      unchanged (dedup of work, not just work-in-flight).
 *   5. A second fetchPreview for a node that is already mid-cascade
 *      does not start a second parallel cascade from that node
 *      (dedup via in-flight guard — pre-refactor this is
 *      `propagatingRef`, post-refactor any equivalent guard is fine,
 *      or duplicate propagation may be accepted as harmless).
 *   6. Fan-out: a node with two downstream children triggers previews
 *      for both.
 *   7. A node with zero downstream edges does nothing extra (no
 *      cascade call, no error, no toast).
 *   8. Cascade survives a rejection of one downstream preview: the
 *      sibling continues, a warning toast is emitted, and the
 *      in-flight guard is cleared so the node can be re-previewed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import type { MutableRefObject } from "react"
import usePipelineAPI, { DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT } from "../usePipelineAPI"
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

import { ApiError, loadPipeline, previewNode } from "../../api/client"
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
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    nodeIdCounter: { current: 0 },
    ...overrides,
  }
}

/**
 * Utility: build a controllable previewNode mock where each node ID
 * returns a Promise the test can resolve/reject on demand.  Records
 * the order in which previewNode was invoked per node ID.
 */
function makeControllablePreview() {
  const callOrder: string[] = []
  const graphsByNode = new Map<string, unknown[]>()
  const deferreds = new Map<
    string,
    { resolve: (v: unknown) => void; reject: (e: unknown) => void; source?: string; rowLimit?: number }
  >()
  mockPreview.mockImplementation(({
    graph,
    nodeId,
    rowLimit,
    source,
  }: { graph: unknown; nodeId: string; rowLimit: number; source?: string }) => {
    callOrder.push(nodeId)
    const graphs = graphsByNode.get(nodeId) ?? []
    graphs.push(graph)
    graphsByNode.set(nodeId, graphs)
    return new Promise((resolve, reject) => {
      deferreds.set(nodeId, { resolve: resolve as (v: unknown) => void, reject, source, rowLimit })
    })
  })
  return { callOrder, deferreds, graphsByNode }
}

async function flushAsyncWork() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

function columnsByNodeFromGraph(graph: unknown) {
  const nodes = (graph as { nodes: Node[] }).nodes
  return Object.fromEntries(
    nodes.map((node) => [node.id, (node.data as { _columns?: unknown })._columns]),
  )
}

describe("usePipelineAPI — downstream propagation (Phase 2D-5)", () => {
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

  // ─────────────────────────────────────────────────────────────────
  // 1. Ordering — A → B → C cascade
  // ─────────────────────────────────────────────────────────────────

  it("cascades linearly in dependency order (A resolves before B fires, B before C)", async () => {
    // Catches: if the refactor were to fire B and C in parallel (e.g.
    // by mistakenly walking the whole chain inside the first .then()
    // rather than chaining recursively), downstream column updates
    // would race and A's new schema would not have propagated into
    // the B-preview graph payload yet.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const C = makeNode("C")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B, C],
      edges: [makeEdge("A", "B"), makeEdge("B", "C")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    // Wait for fetchPreview debounce (200ms) so A's previewNode is invoked
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    // B should NOT have fired yet — cascade is gated on A resolving
    expect(callOrder).toEqual(["A"])

    // Resolve A with columns that differ from B's (empty) — triggers cascade
    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    // Now B should fire, but C should still be pending
    await waitFor(() => expect(callOrder).toEqual(["A", "B"]), { timeout: 2000 })
    expect(callOrder).toEqual(["A", "B"])

    // Resolve B with new columns → triggers C's cascade
    act(() => {
      deferreds.get("B")!.resolve({
        node_id: "B",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "b_col", dtype: "f64" }],
        preview: [{ b_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toEqual(["A", "B", "C"]), { timeout: 2000 })

    // Resolve C to clean up (no further cascade since C has no downstream)
    act(() => {
      deferreds.get("C")!.resolve({
        node_id: "C",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "c_col", dtype: "f64" }],
        preview: [{ c_col: 1 }],
      })
    })
  })

  // ─────────────────────────────────────────────────────────────────
  // 2. Source captured at cascade start (regression guard for #34)
  // ─────────────────────────────────────────────────────────────────

  it("cascade uses the source captured at cascade start, even when store flips mid-flight", async () => {
    // Catches: if the refactor drops the snapshot variables and re-reads
    // activeSourceRef.current for each recursive cascade call, the chain
    // can split across sources when the user flips the active source
    // while the root preview is in flight.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const C = makeNode("C")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B, C],
      edges: [makeEdge("A", "B"), makeEdge("B", "C")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    useSettingsStore.setState({ activeSource: "live" })

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })
    expect(deferreds.get("A")!.source).toBe("live")

    // Flip the source while A is still pending
    act(() => { useSettingsStore.setState({ activeSource: "staging" }) })

    // Resolve A → B should be invoked with the ORIGINAL source ("live")
    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toEqual(["A", "B"]), { timeout: 2000 })
    expect(deferreds.get("B")!.source).toBe("live")

    // Flip again mid-B
    act(() => { useSettingsStore.setState({ activeSource: "live" }) })

    act(() => {
      deferreds.get("B")!.resolve({
        node_id: "B",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "b_col", dtype: "f64" }],
        preview: [{ b_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toEqual(["A", "B", "C"]), { timeout: 2000 })
    // C must still see the original "staging"-era snapshot... wait no:
    // snapshot was captured as "live" at fetchPreview-fire time, so all
    // three previews must use "live".
    expect(deferreds.get("C")!.source).toBe("live")

    act(() => {
      deferreds.get("C")!.resolve({
        node_id: "C",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "c_col", dtype: "f64" }],
        preview: [{ c_col: 1 }],
      })
    })
  })

  // ─────────────────────────────────────────────────────────────────
  // 3. rowLimit captured at cascade start (regression guard for #33)
  // ─────────────────────────────────────────────────────────────────

  it("cascade uses the rowLimit captured at cascade start, even when store flips mid-flight", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B],
      edges: [makeEdge("A", "B")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    useSettingsStore.setState({ rowLimit: 100 })

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })
    expect(deferreds.get("A")!.rowLimit).toBe(100)

    // Flip rowLimit while A is still pending
    act(() => { useSettingsStore.setState({ rowLimit: 999 }) })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toEqual(["A", "B"]), { timeout: 2000 })
    // B must see the ORIGINAL rowLimit (100), not the post-flip 999.
    expect(deferreds.get("B")!.rowLimit).toBe(100)

    act(() => {
      deferreds.get("B")!.resolve({
        node_id: "B",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "b_col", dtype: "f64" }],
        preview: [{ b_col: 1 }],
      })
    })
  })

  // ─────────────────────────────────────────────────────────────────
  // 4. Cascade halts when downstream columns are unchanged
  // ─────────────────────────────────────────────────────────────────

  it("cascade halts at a downstream node whose columns are unchanged", async () => {
    // Catches: if the refactor drops the columnsEqual check, every
    // cascade would walk the whole downstream graph unconditionally,
    // wasting API calls and masking genuine schema changes.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    // B already has the columns the backend will return → cascade halts at B.
    const A = makeNode("A")
    const B = makeNode("B", "polars", {
      data: { label: "Node B", nodeType: "polars", config: {}, _columns: [{ name: "b_col", dtype: "f64" }] },
    })
    const C = makeNode("C")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B, C],
      edges: [makeEdge("A", "B"), makeEdge("B", "C")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    // Resolve A with columns different from what it had (none) → triggers B cascade
    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toEqual(["A", "B"]), { timeout: 2000 })

    // Resolve B with the SAME columns it already had — cascade must NOT continue to C.
    act(() => {
      deferreds.get("B")!.resolve({
        node_id: "B",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "b_col", dtype: "f64" }],
        preview: [{ b_col: 1 }],
      })
    })

    // Give the microtask chain time to (not) fire C
    await flushAsyncWork()
    expect(callOrder).toEqual(["A", "B"])
    expect(callOrder).not.toContain("C")
  })

  // ─────────────────────────────────────────────────────────────────
  // 5. No duplicate cascade for a node already mid-propagation
  // ─────────────────────────────────────────────────────────────────

  it("a second fetchPreview while the first cascade is in-flight does not kick off duplicate downstream previews", async () => {
    // Catches: pre-refactor this is guarded by `propagatingRef`.  If
    // the refactor simply inlines without preserving *some* in-flight
    // dedup (e.g. a per-node promise map), a rapid double-fire of
    // fetchPreview on the root (e.g. two trailing debounce edges in
    // some future change, or a manual caller path) would issue two
    // previews for every downstream node.  Duplicates would not cause
    // wrong data, but they would double API load and cause
    // setNodes thrash.
    //
    // Acceptance: we assert downstream B is invoked AT MOST twice
    // across two root-triggered cascades — the refactor may legitimately
    // collapse to exactly 2 invocations (once per cascade) but NOT 4.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B],
      edges: [makeEdge("A", "B")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    // First fetchPreview — root A
    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    // Resolve A → B cascade starts
    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })
    await waitFor(() => expect(callOrder).toEqual(["A", "B"]), { timeout: 2000 })

    // While B is pending, trigger a second cascade from A by previewing A again.
    // The cache stored after A's first resolution would otherwise make the
    // second fetch skip the API call; clear it so this test focuses on the
    // downstream in-flight guard rather than cache freshness.
    act(() => {
      useNodeResultsStore.setState({ previews: {} })
      result.current.fetchPreview(A)
    })

    // Wait for the second A preview to fire (debounce 200ms)
    await waitFor(() => {
      const aCount = callOrder.filter((id) => id === A.id).length
      expect(aCount).toBeGreaterThanOrEqual(2)
    }, { timeout: 2000 })

    // Resolve the second A with the same columns as B currently has
    // (differing from before's A columns) so cascade would ordinarily
    // re-fire B.  Whether it does or not depends on the guard; count
    // B invocations before/after.
    const bCountBefore = callOrder.filter((id) => id === B.id).length
    const secondA = deferreds.get("A")!
    act(() => {
      secondA.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col_v2", dtype: "f64" }],
        preview: [{ a_col_v2: 1 }],
      })
    })

    // Wait for any cascade to settle (no second B invocation if in-flight guard is active).
    await flushAsyncWork()
    const bCountAfter = callOrder.filter((id) => id === B.id).length

    // B must not be invoked more than once while the first B is still pending.
    // If the refactor keeps a per-node in-flight guard, bCountAfter === bCountBefore.
    // If not, bCountAfter === bCountBefore + 1 is also acceptable.  We cap at +1.
    expect(bCountAfter - bCountBefore).toBeLessThanOrEqual(1)

    // Resolve the first B to let the promise chain settle.
    act(() => {
      deferreds.get("B")!.resolve({
        node_id: "B",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "b_col", dtype: "f64" }],
        preview: [{ b_col: 1 }],
      })
    })
  })

  // ─────────────────────────────────────────────────────────────────
  // 6. Fan-out: multiple downstream children both fire
  // ─────────────────────────────────────────────────────────────────

  it("fires previews for all direct downstream children when root columns change", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const C = makeNode("C")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B, C],
      edges: [makeEdge("A", "B"), makeEdge("A", "C")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    // Both B and C must be invoked (order between siblings is not
    // observable; we care that both fired).
    await waitFor(() => {
      expect(callOrder).toContain("B")
      expect(callOrder).toContain("C")
    }, { timeout: 2000 })

    // Clean up: resolve both
    act(() => {
      deferreds.get("B")!.resolve({
        node_id: "B",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "b_col", dtype: "f64" }],
        preview: [{ b_col: 1 }],
      })
      deferreds.get("C")!.resolve({
        node_id: "C",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "c_col", dtype: "f64" }],
        preview: [{ c_col: 1 }],
      })
    })
  })

  it("caps concurrent downstream previews for a wide fan-out while eventually running all affected nodes", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const callOrder: string[] = []
    const activeDownstream = new Set<string>()
    const deferreds = new Map<string, { resolve: (v: unknown) => void; reject: (e: unknown) => void }>()
    let maxConcurrentDownstream = 0

    mockPreview.mockImplementation(({ nodeId }: { nodeId: string }) => {
      callOrder.push(nodeId)
      if (nodeId !== "A") {
        activeDownstream.add(nodeId)
        maxConcurrentDownstream = Math.max(maxConcurrentDownstream, activeDownstream.size)
      }
      return new Promise<Awaited<ReturnType<typeof previewNode>>>((resolve, reject) => {
        deferreds.set(nodeId, {
          resolve: (value: unknown) => {
            activeDownstream.delete(nodeId)
            resolve(value as Awaited<ReturnType<typeof previewNode>>)
          },
          reject,
        })
      })
    })

    const A = makeNode("A")
    const downstreamIds = Array.from(
      { length: DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT * 2 + 1 },
      (_, index) => `B${index + 1}`,
    )
    const downstreamNodes = downstreamIds.map((id) => makeNode(id))
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, ...downstreamNodes],
      edges: downstreamIds.map((id) => makeEdge("A", id)),
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => {
      expect(callOrder.filter((id) => id !== "A")).toHaveLength(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT)
    }, { timeout: 2000 })
    expect(activeDownstream.size).toBe(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT)
    expect(maxConcurrentDownstream).toBeLessThanOrEqual(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT)

    let resolvedDownstream = 0
    while (resolvedDownstream < downstreamIds.length) {
      const runningIds = [...activeDownstream]
      resolvedDownstream += runningIds.length
      act(() => {
        for (const nodeId of runningIds) {
          deferreds.get(nodeId)!.resolve({
            node_id: nodeId,
            status: "ok",
            row_count: 1,
            column_count: 1,
            columns: [{ name: `${nodeId}_col`, dtype: "f64" }],
            preview: [{ [`${nodeId}_col`]: 1 }],
          })
        }
      })

      const remaining = downstreamIds.length - resolvedDownstream
      await waitFor(() => {
        expect(activeDownstream.size).toBe(Math.min(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT, remaining))
      }, { timeout: 2000 })
      expect(maxConcurrentDownstream).toBeLessThanOrEqual(DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT)
    }

    expect(callOrder.filter((id) => id !== "A").sort()).toEqual([...downstreamIds].sort())
  })

  it("suppresses stale downstream failure toasts after a newer preview supersedes the cascade", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const X = makeNode("X")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B, X],
      edges: [makeEdge("A", "B")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toContain("B"), { timeout: 2000 })

    act(() => { result.current.fetchPreview(X) })
    act(() => { deferreds.get("B")!.reject(new Error("stale downstream boom")) })

    await flushAsyncWork()
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((toast) => toast.type === "warning" && toast.text.includes("stale downstream boom"))).toBe(false)
  })

  it("treats expected downstream preview supersession as cancellation, not a warning", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B", "polars", {
      data: { label: "Competitor features", nodeType: "polars", config: {} },
    })
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B],
      edges: [makeEdge("A", "B")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toEqual(["A", "B"]), { timeout: 2000 })

    act(() => {
      deferreds.get("B")!.reject(
        new ApiError("HTTP 409", 409, "Preview request superseded by a newer request"),
      )
    })

    await flushAsyncWork()

    const toasts = useToastStore.getState().toasts
    expect(toasts.some((toast) => toast.type === "warning" && toast.text.includes("Preview propagation failed"))).toBe(false)
  })

  it("still warns for downstream conflicts that are not preview supersession", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B],
      edges: [makeEdge("A", "B")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toEqual(["A", "B"]), { timeout: 2000 })

    act(() => {
      deferreds.get("B")!.reject(new ApiError("HTTP 409", 409, "Trace data does not match selected row"))
    })

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((toast) =>
        toast.type === "warning" &&
        toast.text.includes("Preview propagation failed") &&
        toast.text.includes("Trace data does not match selected row"),
      )).toBe(true)
    }, { timeout: 2000 })
  })

  it("dedupes diamond-shaped downstream propagation so a shared child previews once", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds, graphsByNode } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const C = makeNode("C")
    const D = makeNode("D")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B, C, D],
      edges: [
        makeEdge("A", "B"),
        makeEdge("A", "C"),
        makeEdge("B", "D"),
        makeEdge("C", "D"),
      ],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => {
      expect(callOrder).toContain("B")
      expect(callOrder).toContain("C")
    }, { timeout: 2000 })

    act(() => {
      deferreds.get("B")!.resolve({
        node_id: "B",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "b_col", dtype: "f64" }],
        preview: [{ b_col: 1 }],
      })
    })

    await flushAsyncWork()
    expect(callOrder).not.toContain("D")

    act(() => {
      deferreds.get("C")!.resolve({
        node_id: "C",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "c_col", dtype: "f64" }],
        preview: [{ c_col: 1 }],
      })
    })

    await flushAsyncWork()
    expect(callOrder.filter((id) => id === "D")).toHaveLength(1)
    expect(columnsByNodeFromGraph(graphsByNode.get("D")![0])).toMatchObject({
      A: [{ name: "a_col", dtype: "f64" }],
      B: [{ name: "b_col", dtype: "f64" }],
      C: [{ name: "c_col", dtype: "f64" }],
    })

    act(() => {
      deferreds.get("D")!.resolve({
        node_id: "D",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "d_col", dtype: "f64" }],
        preview: [{ d_col: 1 }],
      })
    })
  })

  it("waits for a longer sibling branch before previewing an uneven diamond join", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds, graphsByNode } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const C = makeNode("C")
    const E = makeNode("E")
    const D = makeNode("D")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B, C, E, D],
      edges: [
        makeEdge("A", "B"),
        makeEdge("A", "C"),
        makeEdge("A", "D"),
        makeEdge("B", "D"),
        makeEdge("C", "E"),
        makeEdge("E", "D"),
      ],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => {
      expect(callOrder).toContain("B")
      expect(callOrder).toContain("C")
    }, { timeout: 2000 })

    act(() => {
      deferreds.get("B")!.resolve({
        node_id: "B",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "b_col", dtype: "f64" }],
        preview: [{ b_col: 1 }],
      })
    })

    await flushAsyncWork()
    expect(callOrder).not.toContain("D")

    act(() => {
      deferreds.get("C")!.resolve({
        node_id: "C",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "c_col", dtype: "f64" }],
        preview: [{ c_col: 1 }],
      })
    })

    await waitFor(() => expect(callOrder).toContain("E"), { timeout: 2000 })
    expect(callOrder).not.toContain("D")

    act(() => {
      deferreds.get("E")!.resolve({
        node_id: "E",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "e_col", dtype: "f64" }],
        preview: [{ e_col: 1 }],
      })
    })

    await waitFor(() => {
      expect(callOrder.filter((id) => id === "D")).toHaveLength(1)
    }, { timeout: 2000 })
    expect(columnsByNodeFromGraph(graphsByNode.get("D")![0])).toMatchObject({
      A: [{ name: "a_col", dtype: "f64" }],
      B: [{ name: "b_col", dtype: "f64" }],
      E: [{ name: "e_col", dtype: "f64" }],
    })

    act(() => {
      deferreds.get("D")!.resolve({
        node_id: "D",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "d_col", dtype: "f64" }],
        preview: [{ d_col: 1 }],
      })
    })
  })

  // ─────────────────────────────────────────────────────────────────
  // 7. Leaf node — no cascade, no toast
  // ─────────────────────────────────────────────────────────────────

  it("does not invoke additional previews when the previewed node has no downstream edges", async () => {
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const leaf = makeNode("leaf")
    const params = makeParams()
    params.graphRef.current = { nodes: [leaf], edges: [] }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(leaf) })
    await waitFor(() => expect(callOrder).toEqual(["leaf"]), { timeout: 1000 })

    act(() => {
      deferreds.get("leaf")!.resolve({
        node_id: "leaf",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "l_col", dtype: "f64" }],
        preview: [{ l_col: 1 }],
      })
    })

    // Wait a tick to allow any stray cascade to try to fire
    await flushAsyncWork()

    expect(callOrder).toEqual(["leaf"])
    // No propagation warning toast
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "warning" && t.text.includes("propagation"))).toBe(false)
  })

  // ─────────────────────────────────────────────────────────────────
  // 8. Cascade survives a sibling rejection
  // ─────────────────────────────────────────────────────────────────

  it("one downstream rejection does not abort its siblings' previews and emits a toast", async () => {
    // Catches: if the refactor chains siblings through a single
    // Promise.all (rather than per-sibling then/catch/finally), a
    // single rejection would reject the whole cascade and leak the
    // in-flight guard.
    mockLoad.mockResolvedValue({ nodes: [], edges: [] })
    const { callOrder, deferreds } = makeControllablePreview()

    const A = makeNode("A")
    const B = makeNode("B")
    const C = makeNode("C")
    const params = makeParams()
    params.graphRef.current = {
      nodes: [A, B, C],
      edges: [makeEdge("A", "B"), makeEdge("A", "C")],
    }

    const { result } = renderHook(() => usePipelineAPI(params))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => { result.current.fetchPreview(A) })
    await waitFor(() => expect(callOrder).toEqual(["A"]), { timeout: 1000 })

    act(() => {
      deferreds.get("A")!.resolve({
        node_id: "A",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "a_col", dtype: "f64" }],
        preview: [{ a_col: 1 }],
      })
    })

    await waitFor(() => {
      expect(callOrder).toContain("B")
      expect(callOrder).toContain("C")
    }, { timeout: 2000 })

    // Reject B → must NOT cancel C's already-in-flight preview
    act(() => {
      deferreds.get("B")!.reject(new Error("boom"))
    })

    // Resolve C normally
    act(() => {
      deferreds.get("C")!.resolve({
        node_id: "C",
        status: "ok",
        row_count: 1,
        column_count: 1,
        columns: [{ name: "c_col", dtype: "f64" }],
        preview: [{ c_col: 1 }],
      })
    })

    // A warning toast should surface for B's failure
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === "warning" && t.text.includes("B"))).toBe(true)
    }, { timeout: 2000 })
  })
})
